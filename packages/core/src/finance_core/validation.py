def validate_ohlcv(bar: dict) -> bool:
    try:
        high = float(bar.get("high", 0))
        low = float(bar.get("low", 0))
        open_ = float(bar.get("open", 0))
        close = float(bar.get("close", 0))
        volume = int(bar.get("volume", 0))
        if high < low:
            return False
        if high < 0 or low < 0 or open_ < 0 or close < 0:
            return False
        if volume < 0:
            return False
        return True
    except (ValueError, TypeError, KeyError):
        return False
