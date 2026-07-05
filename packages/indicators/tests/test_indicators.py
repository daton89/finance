from finance_indicators.indicators import (
    classify_regime,
)


def test_classify_regime_still_importable_and_works():
    label, score = classify_regime(sma50=110, sma200=100, adx=30, rsi=65, sma20=115, price=118)
    assert label in {"STRONG_BULL", "BULL", "WEAK_BULL"}
    assert score > 0


def test_removed_functions_are_gone():
    import finance_indicators.indicators as indicators

    assert not hasattr(indicators, "calc_sma")
    assert not hasattr(indicators, "calc_rsi")
    assert not hasattr(indicators, "calc_ema")
    assert not hasattr(indicators, "calc_atr")
    assert not hasattr(indicators, "calc_adx")
    assert not hasattr(indicators, "calc_pct_from_sma")
    assert not hasattr(indicators, "calc_all_indicators")
