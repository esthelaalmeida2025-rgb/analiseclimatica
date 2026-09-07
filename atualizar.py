"""
Atualiza o Painel Climático INMET · SP e MG.

Uso:
  python build/atualizar.py                 # baixa o que falta + ano corrente, reprocessa e gera docs/index.html
  python build/atualizar.py --zips pasta/   # usa zips locais (ex.: 2020.zip ... 2026.zip) em vez de baixar
  python build/atualizar.py --forcar 2024   # reprocessa um ano específico mesmo que já exista em dados/

Regras:
- Anos de ANO_INICIAL até o ano atual.
- Um ano só é baixado se não existir dados/mensal_ANO.json, ou se for o ano corrente
  (o INMET repõe o zip do ano em curso conforme os meses fecham), ou até março para o ano anterior,
  ou se o arquivo existente ainda for do esquema antigo (sem MG — ver SCHEMA/migração em main()).
- As estações dos estados configurados em UFS (arquivos INMET_SE_SP_* e INMET_SE_MG_*) são
  processadas a partir do mesmo zip anual nacional do INMET; cada estação sai marcada com 'uf'.
"""
import argparse, glob, io, json, os, re, sys, time, zipfile, datetime as dt
import urllib.request
import pandas as pd, numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
# Aceita duas organizações: build/ + dados/ (padrão) ou tudo na raiz do repositório.
RAIZ = os.path.dirname(AQUI) if os.path.basename(AQUI) == 'build' else AQUI
BUILD = AQUI
DADOS = os.path.join(RAIZ, 'dados') if os.path.basename(AQUI) == 'build' else RAIZ
DOCS = os.path.join(RAIZ, 'docs')
ANO_INICIAL = 2020
URL = 'https://portal.inmet.gov.br/uploads/dadoshistoricos/{ano}.zip'
# Estados incluídos no painel e caixa de sanidade (lat_min,lat_max,lon_min,lon_max) para descartar
# estações claramente mal rotuladas no metadado do INMET. Region prefix: ambos são 'SE' (Sudeste).
UFS = {
    'SP': {'prefixo': 'SE_SP', 'bounds': (-26.0, -19.0, -54.0, -43.0)},
    'MG': {'prefixo': 'SE_MG', 'bounds': (-23.0, -14.0, -52.0, -39.5)},
}
SCHEMA = 2  # bump quando o formato de mensal_ANO.json mudar de forma incompatível (força reprocesso)
COLS = ['data', 'hora', 'prec', 'pres', 'pmax', 'pmin', 'rad', 'temp', 'orv', 'tmax', 'tmin',
        'omax', 'omin', 'umax', 'umin', 'umid', 'vdir', 'raj', 'vel']
FIELDS = ['prec', 'dias_chuva', 'pmax_dia', 'tmed', 'tmax_med', 'tmin_med', 'tmax_abs', 'tmin_abs',
          'umid', 'umin', 'rad', 'vel', 'raj', 'dias_geada', 'dias_calor', 'cob_prec', 'cob_temp']


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def num(s):
    return pd.to_numeric(s.astype(str).str.replace(',', '.', regex=False).str.strip(), errors='coerce')


def baixar(ano, destino):
    url = URL.format(ano=ano)
    log('baixando', url)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (painel-climatico-sp)'})
    with urllib.request.urlopen(req, timeout=600) as r, open(destino, 'wb') as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    log('ok', os.path.getsize(destino) // 1_000_000, 'MB')


def ler_csv(raw, uf, bounds):
    txt = raw.decode('latin1')
    linhas = txt.split('\n')
    meta = {}
    for h in linhas[:8]:
        if ';' in h:
            k, v = h.split(';', 1)
            meta[k.strip(':').strip()] = v.strip()
    try:
        lat = float(meta['LATITUDE'].replace(',', '.')); lon = float(meta['LONGITUDE'].replace(',', '.'))
    except Exception:
        return None
    lat_min, lat_max, lon_min, lon_max = bounds
    if lat < lat_min or lat > lat_max or lon > lon_max or lon < lon_min:   # fora do estado (ex.: estação mal rotulada)
        return None
    alt = meta.get('ALTITUDE', '').replace(',', '.')
    st = {'code': meta['CODIGO (WMO)'], 'name': meta['ESTACAO'].title(), 'lat': round(lat, 4), 'lon': round(lon, 4),
          'alt': float(alt) if alt else None, 'uf': uf}
    df = pd.read_csv(io.StringIO('\n'.join(linhas[8:])), sep=';', dtype=str)
    df = df.iloc[:, :19]; df.columns = COLS
    for c in COLS[2:]:
        df[c] = num(df[c])
    df['data'] = pd.to_datetime(df['data'].str.replace('/', '-'), errors='coerce')
    df = df.dropna(subset=['data'])
    df.loc[(df.prec < 0) | (df.prec > 200), 'prec'] = np.nan
    for c in ['temp', 'tmax', 'tmin']:
        df.loc[(df[c] < -10) | (df[c] > 50), c] = np.nan
    df.loc[(df.umid < 1) | (df.umid > 100), 'umid'] = np.nan
    df.loc[(df.rad < 0) | (df.rad > 5000), 'rad'] = np.nan
    df.loc[(df.vel < 0) | (df.vel > 40), 'vel'] = np.nan
    df.loc[(df.raj < 0) | (df.raj > 60), 'raj'] = np.nan
    g = df.groupby('data')
    d = pd.DataFrame({
        'prec': g['prec'].sum(min_count=1), 'nprec': g['prec'].count(),
        'tmed': g['temp'].mean(), 'tmax': g['tmax'].max(), 'tmin': g['tmin'].min(),
        'umid': g['umid'].mean(), 'umin': g['umin'].min(),
        'rad': g['rad'].sum(min_count=1) / 1000.0, 'nrad': g['rad'].count(),
        'vel': g['vel'].mean(), 'raj': g['raj'].max(), 'ntemp': g['temp'].count(),
    }).reset_index()
    d['code'] = st['code']
    return st, d


def processar_ano(ano, caminho_zip):
    """Lê o zip nacional do INMET e devolve {'stations': {...}, 'daily': DataFrame} dos estados em UFS."""
    stations, dias = {}, []
    with zipfile.ZipFile(caminho_zip) as z:
        todos = z.namelist()
        for uf, cfg in UFS.items():
            nomes = [n for n in todos if f"INMET_{cfg['prefixo']}_" in n.upper() and n.upper().endswith('.CSV')]
            log(ano, ':', len(nomes), f'estações de {uf}')
            for n in nomes:
                r = ler_csv(z.read(n), uf, cfg['bounds'])
                if r is None:
                    continue
                st, d = r
                stations.setdefault(st['code'], st)
                dias.append(d)
    if not dias:
        return None
    daily = pd.concat(dias).sort_values(['code', 'data']).drop_duplicates(['code', 'data'])
    daily.loc[daily.nprec < 18, 'prec'] = np.nan
    daily.loc[daily.ntemp < 18, ['tmed', 'tmax', 'tmin', 'umid', 'umin', 'vel', 'raj']] = np.nan
    daily.loc[daily.nrad < 8, 'rad'] = np.nan
    daily['ym'] = daily.data.dt.strftime('%Y-%m')
    for col, cond in [('chuva', daily.prec >= 1.0), ('geada', daily.tmin <= 3.0), ('calor', daily.tmax >= 32.0)]:
        daily[col] = cond.astype(float)
    daily.loc[daily.prec.isna(), 'chuva'] = np.nan
    daily.loc[daily.tmin.isna(), 'geada'] = np.nan
    daily.loc[daily.tmax.isna(), 'calor'] = np.nan
    gm = daily.groupby(['code', 'ym'])
    m = pd.DataFrame({
        'prec': gm['prec'].sum(min_count=1), 'ndias_prec': gm['prec'].count(), 'pmax_dia': gm['prec'].max(),
        'dias_chuva': gm['chuva'].sum(min_count=1), 'tmed': gm['tmed'].mean(), 'tmax_med': gm['tmax'].mean(),
        'tmin_med': gm['tmin'].mean(), 'tmax_abs': gm['tmax'].max(), 'tmin_abs': gm['tmin'].min(),
        'umid': gm['umid'].mean(), 'umin': gm['umin'].min(), 'rad': gm['rad'].mean(), 'vel': gm['vel'].mean(),
        'raj': gm['raj'].max(), 'dias_geada': gm['geada'].sum(min_count=1), 'dias_calor': gm['calor'].sum(min_count=1),
        'ndias_temp': gm['tmed'].count(),
    }).reset_index()
    m['dias_mes'] = pd.to_datetime(m.ym + '-01').dt.days_in_month
    m['cob_prec'] = (m.ndias_prec / m.dias_mes * 100).round(0)
    m['cob_temp'] = (m.ndias_temp / m.dias_mes * 100).round(0)
    m.loc[m.cob_prec < 80, ['prec', 'dias_chuva', 'pmax_dia']] = np.nan
    m.loc[m.cob_temp < 70, ['tmed', 'tmax_med', 'tmin_med', 'tmax_abs', 'tmin_abs', 'umid', 'umin', 'vel', 'raj',
                            'dias_geada', 'dias_calor']] = np.nan

    def r(x, n=1):
        return None if pd.isna(x) else round(float(x), n)

    base = pd.Timestamp('2020-01-01')
    out = {'ano': ano, 'gerado': dt.date.today().isoformat(), 'schema': SCHEMA, 'stations': {}}
    for code, st in stations.items():
        rec = dict(st); rec['m'] = {}; rec['d'] = []
        for row in m[m.code == code].itertuples():
            rec['m'][row.ym] = [r(row.prec), r(row.dias_chuva, 0), r(row.pmax_dia), r(row.tmed), r(row.tmax_med),
                                r(row.tmin_med), r(row.tmax_abs), r(row.tmin_abs), r(row.umid, 0), r(row.umin, 0),
                                r(row.rad), r(row.vel), r(row.raj), r(row.dias_geada, 0), r(row.dias_calor, 0),
                                r(row.cob_prec, 0), r(row.cob_temp, 0)]
        for row in daily[daily.code == code].itertuples():
            rec['d'].append([int((row.data - base).days), r(row.prec), r(row.tmax), r(row.tmin), r(row.tmed)])
        out['stations'][code] = rec
    return out


def montar_html(anos_ok, anos_faltantes):
    est = {}
    for ano in anos_ok:
        j = json.load(open(os.path.join(DADOS, f'mensal_{ano}.json'), encoding='utf-8'))
        for code, rec in j['stations'].items():
            e = est.setdefault(code, {'code': code, 'name': rec['name'], 'lat': rec['lat'], 'lon': rec['lon'],
                                      'alt': rec['alt'], 'uf': rec.get('uf', 'SP'), 'm': {}, 'd': [], '_names': {}})
            e['_names'][rec['name']] = e['_names'].get(rec['name'], 0) + 1
            e['m'].update(rec['m']); e['d'].extend(rec['d'])
    stations = []
    for e in est.values():
        e['name'] = max(e['_names'], key=e['_names'].get); del e['_names']
        e['d'].sort(key=lambda x: x[0]); stations.append(e)
    stations.sort(key=lambda s: s['name'])
    months = sorted({ym for s in stations for ym in s['m']})
    data = {'meta': {'months': months, 'fields': FIELDS, 'daily_fields': ['dia', 'prec', 'tmax', 'tmin', 'tmed'],
                     'base_date': '2020-01-01', 'atualizado': dt.date.today().strftime('%d/%m/%Y'),
                     'anos_faltantes': anos_faltantes}, 'stations': stations}
    t = open(os.path.join(BUILD, 'painel_template.html'), encoding='utf-8').read()
    libs = ''
    for f in ['chart.umd.js', 'html2canvas.min.js', 'jspdf.umd.min.js']:
        cam = os.path.join(BUILD, 'libs', f)
        if not os.path.exists(cam):
            cam = os.path.join(BUILD, f)
        libs += '<script>' + open(cam, encoding='utf-8').read().replace('</script>', '<\\/script>') + '</script>\n'
    t = t.replace('__LIBS__', libs)
    t = t.replace('__DATA__', json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    t = t.replace('__SP__', open(os.path.join(BUILD, 'sp.json'), encoding='utf-8').read())
    t = t.replace('__MG__', open(os.path.join(BUILD, 'mg.json'), encoding='utf-8').read())
    t = t.replace('__APT__', open(os.path.join(BUILD, 'aptidao.json'), encoding='utf-8').read())
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8').write(t)
    # CSV mensal consolidado (SP + MG)
    with open(os.path.join(DOCS, 'inmet_sp_mg_mensal.csv'), 'w', encoding='utf-8-sig') as f:
        f.write(';'.join(['uf', 'codigo', 'estacao', 'lat', 'lon', 'alt', 'ano_mes'] + FIELDS) + '\n')
        for s in stations:
            for ym in sorted(s['m']):
                vals = ['' if v is None else str(v).replace('.', ',') for v in s['m'][ym]]
                f.write(';'.join([s.get('uf', 'SP'), s['code'], s['name'], str(s['lat']), str(s['lon']), str(s['alt']), ym] + vals) + '\n')
    n_sp = sum(1 for s in stations if s.get('uf', 'SP') == 'SP'); n_mg = sum(1 for s in stations if s.get('uf') == 'MG')
    log('painel gerado:', os.path.join(DOCS, 'index.html'), f'| {n_sp} estações SP, {n_mg} estações MG |', months[0], 'a', months[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--zips', help='pasta com zips locais ANO.zip (não baixa)')
    ap.add_argument('--forcar', nargs='*', type=int, default=[], help='anos a reprocessar')
    a = ap.parse_args()
    os.makedirs(DADOS, exist_ok=True)
    hoje = dt.date.today(); anos = list(range(ANO_INICIAL, hoje.year + 1))
    ok, faltantes = [], []
    for ano in anos:
        alvo = os.path.join(DADOS, f'mensal_{ano}.json')
        desatualizado = False
        if os.path.exists(alvo):
            try:
                desatualizado = json.load(open(alvo, encoding='utf-8')).get('schema') != SCHEMA
            except Exception:
                desatualizado = True
        precisa = (not os.path.exists(alvo)) or desatualizado or ano in a.forcar or ano == hoje.year or (ano == hoje.year - 1 and hoje.month <= 3)
        if desatualizado:
            log(f'{ano}: mensal_{ano}.json em esquema antigo (sem MG) — reprocessando')
        if precisa:
            try:
                if a.zips:
                    zp = os.path.join(a.zips, f'{ano}.zip')
                    if not os.path.exists(zp):
                        raise FileNotFoundError(zp)
                else:
                    zp = os.path.join('/tmp', f'inmet_{ano}.zip'); baixar(ano, zp)
                res = processar_ano(ano, zp)
                if res is None:
                    raise RuntimeError('zip sem estações de SP')
                json.dump(res, open(alvo, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
                if not a.zips:
                    os.remove(zp)
            except Exception as e:
                log(f'AVISO: {ano} não processado ({e}); mantendo o que já existe')
        if os.path.exists(alvo):
            ok.append(ano)
        else:
            faltantes.append(ano)
    if not ok:
        sys.exit('nenhum ano disponível')
    montar_html(ok, faltantes)


if __name__ == '__main__':
    main()
