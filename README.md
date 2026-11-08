# IPDA GRAB Bot v5 (BingX + Telegram)

Bot de senales **GRAB** en perpetuos BingX con capas de edge:

- Liquidity Pools (Equal Highs/Lows)
- Displacement + Fair Value Gap
- Order Block / Breaker
- Relative Strength vs BTC
- Funding rate
- Volume quality + climax + delta
- Volatility regime filter
- **HA-EMA (filtro suave)** - EMA sobre Heikin-Ashi, solo bonus de score
- Position management (PAPER/LIVE): TP1 50% + BE + trailing

## Deploy Railway

1. Sube este repo a GitHub
2. New Project -> Deploy from GitHub
3. Anade volumen en `/data`
4. Variables: pega `railway_vars.txt` y completa TELEGRAM_*
5. Arranque: worker (Procfile)

## Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... MODE=SIGNAL
python bot.py
```

## Modos

| MODE   | Comportamiento                                      |
|--------|-----------------------------------------------------|
| SIGNAL | Solo Telegram                                       |
| PAPER  | Senal + gestion local (partial TP + trailing)       |
| LIVE   | Ordenes reales BingX + misma gestion                |

## Variables importantes

```
MODE=SIGNAL
MIN_SCORE=85
SHORT_MIN_SCORE=90
NEAR_ATR_MAX=0.55
USE_HA_EMA=true
REQUIRE_HA_EMA_ALIGN=false   # false = filtro suave (recomendado)
USE_FUNDING=true
AVOID_LOW_VOL=true
AVOID_EXTREME_VOL=true
EXTRA_SYMBOLS=OIL-USDT,XBR-USDT,BRENT-USDT
```

## Criterio LIVE

Tras >=40 senales GRAB score>=85 con esperanza TP1 > 0 en journal, valorar LIVE con RISK_PCT<=0.5.

No garantiza beneficios. Cripto es de alto riesgo.
