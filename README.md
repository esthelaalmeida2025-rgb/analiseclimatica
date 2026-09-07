Painel Climático INMET · São Paulo e Minas Gerais

Painel HTML que consolida os dados horários das estações automáticas do INMET nos estados de SP e MG
(chuva, temperatura, umidade, radiação, vento, geada, calor) e se atualiza sozinho a partir de
https://portal.inmet.gov.br/dadoshistoricos. Uma aba no topo alterna entre os dois estados; todas as
análises (visão geral, comparativo mensal, série, mapa espacial, ranking, PDF) valem para o estado ativo.

## Como publicar (uma vez só)
1. Crie um repositório no GitHub (pode ser privado) e envie estes arquivos, com `atualizar.yml` dentro
   da pasta `.github/workflows/` (não na raiz — é onde o GitHub Actions procura os workflows).
2. Em **Settings → Pages**, escolha *Deploy from a branch*, branch `main`, pasta `/docs`. Salve.
3. Em **Actions**, abra "Atualizar painel INMET-SP e MG" e clique em **Run workflow** para a primeira carga
   (baixa 2020 até o ano atual — leva uns 10–15 min).
4. O painel fica em `https://SEU-USUARIO.github.io/NOME-DO-REPO/` e o CSV consolidado em `.../inmet_sp_mg_mensal.csv`.

Depois disso o robô roda toda segunda-feira: baixa só o zip do ano corrente (e anos que faltem),
reprocessa, regrava `docs/index.html` e publica. Nada a fazer.

## Rodar no computador
```
pip install pandas numpy
python atualizar.py            # baixa do INMET (SP + MG)
python atualizar.py --zips C:\pasta\com\zips   # usa zips já baixados
python atualizar.py --forcar 2020 2021 2022 2024 2025 2026   # força reprocesso de anos específicos
```

## Estrutura
- `atualizar.py` — baixa, filtra SP e MG, agrega (diário → mensal) e monta o HTML
- `painel_template.html` — o painel (layout, gráficos, mapa, PDF, seletor de estado)
- `sp.json` / `mg.json` — contorno dos estados usado no mapa espacial (IDW)
- `mensal_ANO.json` — resumo processado por ano (evita rebaixar anos fechados); arquivos gerados antes
  da inclusão de MG são reprocessados automaticamente uma vez (controle de versão de esquema)
- `docs/index.html` — painel publicado · `docs/inmet_sp_mg_mensal.csv` — base mensal (SP + MG)
