EXCHANGE_CONFIG: dict[str, dict] = {
    "NASDAQ":   {"currency": "USD", "provider": "polygon",     "td_exchange": None,        "mic": "XNAS"},
    "XETR":     {"currency": "EUR", "provider": "twelve_data", "td_exchange": "XETR",      "mic": "XETR"},
    "GETTEX":   {"currency": "EUR", "provider": "twelve_data", "td_exchange": "MUN",       "mic": "XMUN"},
    "EURONEXT": {"currency": "EUR", "provider": "twelve_data", "td_exchange": "EURONEXT",  "mic": "XPAR"},
}

SETTING_DEFAULTS: dict[str, str] = {
    "sma_period": "20",
    "rsi_period": "14",
    "buy_proximity_pct": "2.0",
    "refresh_frequency": "end-of-day",
    "exchange": "NASDAQ",
    "stop_loss_pct": "-5.0",
    "trailing_stop_pct": "-15.0",
    "rsi_sell_threshold": "35",
    "atr_threshold_high": "5.0",
    "atr_threshold_low": "2.0",
    "regime_filter_enabled": "false",
    "history_days": "730",
    "api_throttle_seconds": "12",
    "ai_analysis_enabled": "true",
    "ai_analysis_cache_hours": "24",
    "vv_source_id": "US_SR_577",
    "vv_sync_enabled": "false",
    "telegram_notifications_enabled": "false",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "live_updates_enabled": "false",
    "live_updates_interval_minutes": "5",
    "news_filter_enabled": "false",
}

HISTORY_DAYS_DEFAULT = 90
