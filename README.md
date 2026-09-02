Painel Climático INMET · São Paulo

Painel HTML que consolida os dados horários das estações automáticas do INMET no estado de SP
(chuva, temperatura, umidade, radiação, vento, geada, calor) e se atualiza sozinho a partir de
https://portal.inmet.gov.br/dadoshistoricos.

## Como publicar (uma vez só)
1. Crie um repositório no GitHub (pode ser privado) e envie estes arquivos.
2. Em **Settings → Pages**, escolha *Deploy from a branch*, branch `main`, pasta `/docs`. Salve.
3. Em **Actions**, abra "Atualizar painel INMET-SP" e clique em **Run workflow** para a primeira carga
   (baixa 2020 até o ano atual — leva uns 10–15 min).
4. O painel fica em `https://SEU-USUARIO.github.io/NOME-DO-REPO/` e o CSV consolidado em `.../inmet_sp_mensal.csv`.

Depois disso o robô roda toda segunda-feira: baixa só o zip do ano corrente (e anos que faltem),
reprocessa, regrava `docs/index.html` e publica. Nada a fazer.

## Rodar no computador
```
pip install pandas numpy
python build/atualizar.py            # baixa do INMET
python build/atualizar.py --zips C:\pasta\com\zips   # usa zips já baixados
```

## Estrutura
- `build/atualizar.py` — baixa, filtra SP, agrega (diário → mensal) e monta o HTML
- `build/painel_template.html` — o painel (layout, gráficos, mapa, PDF)
- `dados/mensal_ANO.json` — resumo processado por ano (evita rebaixar anos fechados)
- `docs/index.html` — painel publicado · `docs/inmet_sp_mensal.csv` — base mensal
