"""IPDA GRAB Bot — config desde variables de entorno (Railway)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _b(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Modo: SIGNAL = solo Telegram | PAPER = simula fills | LIVE = órdenes BingX
    mode: str = "SIGNAL"

    # Universo / cadencia
    quote: str = "USDT"
    max_symbols: int = 150
    scan_sec: int = 120
    workers: int = 12
    tf_main: str = "15m"
    tf_confirm: str = "5m"

    # Origen — papel: solo GRAB
    allow_grab: bool = True
    allow_cont: bool = False
    allow_b_grab: bool = False

    # Score / calidad
    min_score: int = 85
    short_min_score: int = 90
    min_xsec_pct: float = 75.0
    require_near_level: bool = True
    near_atr_max: float = 0.6

    # Absorción (papel empeora E → off)
    absorb_boost: bool = False
    require_absorption: bool = False

    # Confirmación 5m
    confirm_required: bool = True
    confirm_mode: str = "reject"  # reject | close_back

    # BTC / dirección
    require_btc_align: bool = True
    allow_long: bool = True
    allow_short: bool = True

    # Sesión UTC
    session_start: int = 0
    session_end: int = 24

    # Riesgo
    risk_pct: float = 0.5
    max_trades_day: int = 3
    max_loss_streak: int = 2
    max_day_loss_pct: float = 2.0
    min_rr: float = 1.5
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    sl_atr_buf: float = 0.35

    # Lookbacks liquidez (velas 15m ~ días)
    lookback_a: int = 20 * 24 * 4   # ~20d en 15m
    lookback_b: int = 40 * 24 * 4

    # Telegram / API
    telegram_token: str = ""
    telegram_chat_id: str = ""
    bingx_api_key: str = ""
    bingx_api_secret: str = ""
    state_path: str = "/data/state.json"
    paper_path: str = "/data/paper.json"
    extra_symbols: str = ""  # CSV: OIL-USDT,XBR-USDT,...
    use_regime: bool = True
    regime_ema: int = 50
    crisis_atr_pct: float = 3.5

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("MODE", "SIGNAL").strip().upper()
        if mode not in {"SIGNAL", "PAPER", "LIVE"}:
            mode = "SIGNAL"
        return cls(
            mode=mode,
            quote=os.getenv("QUOTE", "USDT").strip().upper(),
            max_symbols=_i("MAX_SYMBOLS", 150),
            scan_sec=max(30, _i("SCAN_SEC", 120)),
            workers=max(2, min(32, _i("WORKERS", 12))),
            tf_main=os.getenv("TF_MAIN", "15m"),
            tf_confirm=os.getenv("TF_CONFIRM", "5m"),
            allow_grab=_b("ALLOW_GRAB", "true"),
            allow_cont=_b("ALLOW_CONT", "false"),
            allow_b_grab=_b("ALLOW_B_GRAB", "false"),
            min_score=_i("MIN_SCORE", 85),
            short_min_score=_i("SHORT_MIN_SCORE", 90),
            min_xsec_pct=_f("MIN_XSEC_PCT", 75.0),
            require_near_level=_b("REQUIRE_NEAR_LEVEL", "true"),
            near_atr_max=_f("NEAR_ATR_MAX", 0.6),
            absorb_boost=_b("ABSORB_BOOST", "false"),
            require_absorption=_b("REQUIRE_ABSORPTION", "false"),
            confirm_required=_b("CONFIRM_REQUIRED", "true"),
            confirm_mode=os.getenv("CONFIRM_MODE", "reject").strip().lower(),
            require_btc_align=_b("REQUIRE_BTC_ALIGN", "true"),
            allow_long=_b("ALLOW_LONG", "true"),
            allow_short=_b("ALLOW_SHORT", "true"),
            session_start=_i("SESSION_START", 0),
            session_end=_i("SESSION_END", 24),
            risk_pct=_f("RISK_PCT", 0.5),
            max_trades_day=_i("MAX_TRADES_DAY", 3),
            max_loss_streak=_i("MAX_LOSS_STREAK", 2),
            max_day_loss_pct=_f("MAX_DAY_LOSS_PCT", 2.0),
            min_rr=_f("MIN_RR", 1.5),
            tp1_r=_f("TP1_R", 1.0),
            tp2_r=_f("TP2_R", 2.0),
            sl_atr_buf=_f("SL_ATR_BUF", 0.35),
            telegram_token=os.getenv("TELEGRAM_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            bingx_api_key=os.getenv("BINGX_API_KEY", "").strip(),
            bingx_api_secret=os.getenv("BINGX_API_SECRET", "").strip(),
            state_path=os.getenv("STATE_PATH", "/data/state.json"),
            paper_path=os.getenv("PAPER_PATH", "/data/paper.json"),
            extra_symbols=os.getenv("EXTRA_SYMBOLS", "").strip(),
            use_regime=_b("USE_REGIME", "true"),
            regime_ema=_i("REGIME_EMA", 50),
            crisis_atr_pct=_f("CRISIS_ATR_PCT", 3.5),
        )


settings = Settings.from_env()
