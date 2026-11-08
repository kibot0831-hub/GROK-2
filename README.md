# IPDA GRAB Bot v5

Senales GRAB en BingX con:

- Liquidity pools, FVG, Displacement, Order Block
- Relative Strength vs BTC + Funding
- Volume quality + Volatility regime
- HA-EMA (filtro suave)
- **Journal automatico** (TP1/SL/TIMEOUT + R-multiple)
- PAPER: partial TP + trailing
- Filtro volumen 24h minimo

## Railway

1. Deploy from GitHub
2. Volumen en `/data`
3. Pegar `railway_vars.txt` (completar TELEGRAM_*)
4. worker via Procfile

## Local

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... MODE=SIGNAL
python bot.py
```

## Journal

Cada senal se guarda en `/data/journal.json`.
Se resuelve con precio vivo: TP1, TP2, SL o TIMEOUT (24h).
El heartbeat muestra winrate y avg R cuando hay datos.

## Modos

| MODE   | Que hace                          |
|--------|-----------------------------------|
| SIGNAL | Solo Telegram + journal           |
| PAPER  | + posiciones simuladas            |
| LIVE   | + ordenes BingX                   |

No garantiza beneficios.
