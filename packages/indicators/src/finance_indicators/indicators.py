"""
Technical indicator calculations.
All functions operate on plain Python lists and have no database dependency.
"""

from __future__ import annotations


def calc_sma_slope(sma_values: list[float | None], lookback: int = 5) -> float | None:
    """
    Rate of change of SMA over `lookback` bars.
    Returns percentage change: (current - lookback_ago) / lookback_ago * 100.
    Positive = uptrend, negative = downtrend.
    """
    valid = [v for v in sma_values[-lookback:] if v is not None]
    if len(valid) < 2:
        return None
    if valid[0] == 0:
        return None
    return (valid[-1] - valid[0]) / valid[0] * 100.0


def classify_regime(
    sma50: float | None,
    sma200: float | None,
    adx: float | None,
    rsi: float | None = None,
    sma20: float | None = None,
    price: float | None = None,
) -> tuple[str, float]:
    """
    Multi-factor regime classification using a weighted scoring system.

    Factors (weights sum to 1.0):
      - SMA50 vs SMA200 crossover      (0.35) — primary trend direction
      - SMA20 vs SMA50 alignment        (0.20) — short-term trend alignment
      - Price vs SMA200                 (0.15) — long-term price position
      - Price vs SMA50                  (0.10) — medium-term price position
      - RSI momentum zone               (0.20) — momentum confirmation

    ADX dampening: low ADX (<20) compresses the score toward zero,
    reflecting range-bound / non-trending conditions.

    Returns: (regime_label, direction_score)
      - regime_label: STRONG_BULL | BULL | WEAK_BULL | SIDEWAYS |
                      WEAK_BEAR | BEAR | STRONG_BEAR | UNKNOWN
      - direction_score: float in [-1.0, 1.0], negative = bearish
    """
    if sma50 is None or sma200 is None:
        return "UNKNOWN", 0.0

    score = 0.0

    score += 0.35 if sma50 > sma200 else -0.35

    if sma20 is not None:
        score += 0.20 if sma20 > sma50 else -0.20

    if price is not None:
        score += 0.15 if price > sma200 else -0.15
        score += 0.10 if price > sma50 else -0.10

    if rsi is not None:
        if rsi >= 60:
            score += 0.20
        elif rsi >= 50:
            score += 0.10
        elif rsi >= 40:
            score -= 0.10
        else:
            score -= 0.20

    if adx is not None:
        if adx < 15:
            score *= 0.25
        elif adx < 20:
            score *= 0.55

    score = max(-1.0, min(1.0, round(score, 3)))

    if adx is not None and adx < 15:
        label = "SIDEWAYS"
    elif score >= 0.60:
        label = "STRONG_BULL"
    elif score >= 0.30:
        label = "BULL"
    elif score >= 0.08:
        label = "WEAK_BULL"
    elif score >= -0.08:
        label = "SIDEWAYS"
    elif score >= -0.30:
        label = "WEAK_BEAR"
    elif score >= -0.60:
        label = "BEAR"
    else:
        label = "STRONG_BEAR"

    return label, score


def compute_trend_phase(
    regime: str,
    rsi: float | None,
    adx: float | None,
    direction_score: float = 0.0,
) -> str:
    """
    Infer the current trend phase based on regime label, RSI, and ADX.

    Phases:
      early_bull    — bullish regime just forming, momentum building
      mature_bull   — established uptrend with strong ADX
      topping       — bull regime but RSI overbought (potential reversal)
      ranging       — no clear trend direction
      early_bear    — bearish regime just forming, momentum turning down
      mature_bear   — established downtrend with strong ADX
      bottoming     — bear regime but RSI oversold (potential reversal)
    """
    is_bull = regime in ("STRONG_BULL", "BULL", "WEAK_BULL")
    is_bear = regime in ("STRONG_BEAR", "BEAR", "WEAK_BEAR")
    strong_trend = adx is not None and adx > 28
    rsi_overbought = rsi is not None and rsi >= 70
    rsi_oversold = rsi is not None and rsi <= 30

    if is_bull:
        if rsi_overbought:
            return "topping"
        if strong_trend:
            return "mature_bull"
        return "early_bull"
    if is_bear:
        if rsi_oversold:
            return "bottoming"
        if strong_trend:
            return "mature_bear"
        return "early_bear"
    return "ranging"


def detect_bearish_divergence(closes: list[float], rsis: list[float], window: int) -> bool:
    """
    Detect bearish price/RSI divergence over a rolling `window` of bars.

    A divergence is flagged when:
      - max(close) in the second half > max(close) in the first half  (price HH)
      - max(RSI)   in the second half < max(RSI)   in the first half  (RSI LH)

    The window is split into two equal halves; for odd windows the middle bar
    is included in both halves.

    Returns False if insufficient data.
    """
    if len(closes) < window or len(rsis) < window:
        return False

    recent_closes = closes[-window:]
    recent_rsis = rsis[-window:]

    mid = window // 2

    first_closes = recent_closes[: mid + (window % 2)]
    second_closes = recent_closes[mid:]
    first_rsis = recent_rsis[: mid + (window % 2)]
    second_rsis = recent_rsis[mid:]

    price_hh = max(second_closes) > max(first_closes)
    rsi_lh = max(second_rsis) < max(first_rsis)

    return price_hh and rsi_lh


def _build_rsi_series(closes: list[float], period: int) -> list[float]:
    """
    Build the full RSI time series for a list of closes.
    Used internally for divergence detection.
    """
    if len(closes) < period + 1:
        return []

    rsi_values: list[float] = []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [max(d, 0.0) for d in deltas[:period]]
    losses = [abs(min(d, 0.0)) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_from_avgs(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + ag / al))

    rsi_values.append(_rsi_from_avgs(avg_gain, avg_loss))

    for delta in deltas[period:]:
        gain = max(delta, 0.0)
        loss = abs(min(delta, 0.0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsi_values.append(_rsi_from_avgs(avg_gain, avg_loss))

    return rsi_values
