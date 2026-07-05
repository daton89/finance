from sqlalchemy import (
    Column, Integer, Text, Float, Boolean, Date, DateTime,
    ForeignKey, UniqueConstraint, CheckConstraint, func,
)
from sqlalchemy.orm import relationship

from finance_core.base import Base

Real = Float


class WatchlistStock(Base):
    __tablename__ = "watchlist_stocks"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    ticker          = Column(Text, nullable=False, unique=True)
    company_name    = Column(Text)
    added_at        = Column(DateTime, nullable=False, server_default=func.now())
    is_active       = Column(Boolean, nullable=False, default=True)
    notes           = Column(Text)
    exchange_ticker = Column(Text, nullable=True)
    isin            = Column(Text, nullable=True)
    tv_symbol       = Column(Text, nullable=True)

    holdings        = relationship("Holding", back_populates="stock")


class ExternalRating(Base):
    __tablename__ = "external_ratings"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    ticker         = Column(Text, nullable=False)
    source         = Column(Text, nullable=False, default="tradingview")
    recommendation = Column(Text, nullable=False)
    score          = Column(Real)
    fetched_at     = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("recommendation IN ('BUY','HOLD','SELL')", name="ck_external_rating_rec"),
    )


class PriceBar(Base):
    __tablename__ = "price_bars"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    ticker     = Column(Text, nullable=False)
    bar_date   = Column(Date, nullable=False)
    exchange   = Column(Text, nullable=False, default="NASDAQ")
    open       = Column(Real, nullable=False)
    high       = Column(Real, nullable=False)
    low        = Column(Real, nullable=False)
    close      = Column(Real, nullable=False)
    volume     = Column(Integer, nullable=False)
    fetched_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("ticker", "bar_date", "exchange", name="uq_price_bar"),
    )


class IndicatorValue(Base):
    __tablename__ = "indicator_values"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    ticker        = Column(Text, nullable=False)
    calc_date     = Column(Date, nullable=False)
    sma_period    = Column(Integer, nullable=False)
    sma_value     = Column(Real)
    sma50_value   = Column(Real)
    sma200_value  = Column(Real)
    rsi_period    = Column(Integer, nullable=False)
    rsi_value     = Column(Real)
    pct_from_sma  = Column(Real)
    ema_value     = Column(Real)
    atr_value     = Column(Real)
    adx_value     = Column(Real)
    calculated_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("ticker", "calc_date", "sma_period", "rsi_period",
                         name="uq_indicator"),
    )


class Holding(Base):
    __tablename__ = "holdings"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ticker       = Column(Text, ForeignKey("watchlist_stocks.ticker"), nullable=False)
    entry_price  = Column(Real, nullable=False)
    entry_date   = Column(Date, nullable=False)
    shares       = Column(Real, nullable=False)
    notes        = Column(Text)
    is_open      = Column(Boolean, nullable=False, default=True)
    sell_price   = Column(Real)
    sell_date    = Column(Date)
    realised_pnl = Column(Real)
    peak_price   = Column(Real, nullable=True)
    created_at   = Column(DateTime, nullable=False, server_default=func.now())

    stock        = relationship("WatchlistStock", back_populates="holdings")
    signals      = relationship("Signal", back_populates="holding")


class Signal(Base):
    __tablename__ = "signals"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ticker       = Column(Text, nullable=False)
    holding_id   = Column(Integer, ForeignKey("holdings.id"))
    signal_type  = Column(Text, nullable=False)
    conditions   = Column(Text, nullable=False)
    triggered_at = Column(DateTime, nullable=False, server_default=func.now())
    status       = Column(Text, nullable=False, default="active")
    dismissed_at = Column(DateTime)
    resolved_at  = Column(DateTime)
    is_read      = Column(Boolean, nullable=False, default=False)

    holding      = relationship("Holding", back_populates="signals")

    __table_args__ = (
        CheckConstraint("signal_type IN ('NEWS_CATALYST','BUY_ZONE','ACCUMULATE','WATCH','OVERBOUGHT','SELL_ALERT','EARLY_TREND_BREAK','STOP_LOSS','TRAILING_STOP')", name="ck_signal_type"),
        CheckConstraint("status IN ('active','dismissed','resolved')", name="ck_signal_status"),
    )


class MarketRegime(Base):
    __tablename__ = "market_regimes"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    ticker          = Column(Text, nullable=False)
    calc_date       = Column(Date, nullable=False)
    regime          = Column(Text, nullable=False)
    sma20_value     = Column(Real)
    sma50_value     = Column(Real)
    sma200_value    = Column(Real)
    adx_value       = Column(Real)
    rsi_value       = Column(Real)
    direction_score = Column(Real)
    confidence      = Column(Real)
    trend_phase     = Column(Text)
    calculated_at   = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("ticker", "calc_date", name="uq_market_regime"),
    )


class StrategyDiscoveryRun(Base):
    __tablename__ = "strategy_discovery_runs"

    id                   = Column(Integer, primary_key=True, autoincrement=True)
    status               = Column(Text, nullable=False, default="pending")
    model_used           = Column(Text)
    ticker_count         = Column(Integer)
    strategies_generated = Column(Integer, default=0)
    context_summary      = Column(Text)
    error_message        = Column(Text)
    created_at           = Column(DateTime, nullable=False, server_default=func.now())
    completed_at         = Column(DateTime)

    strategies = relationship("Strategy", back_populates="discovery_run",
                              foreign_keys="Strategy.discovery_run_id")


class Strategy(Base):
    __tablename__ = "strategies"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    name          = Column(Text, nullable=False, unique=True)
    description   = Column(Text)
    strategy_type = Column(Text, nullable=False)
    entry_rules   = Column(Text, nullable=False)
    exit_rules    = Column(Text, nullable=False)
    parameters    = Column(Text)
    discovery_run_id = Column(Integer, ForeignKey("strategy_discovery_runs.id"), nullable=True)
    ai_rationale  = Column(Text)
    created_at    = Column(DateTime, nullable=False, server_default=func.now())
    updated_at    = Column(DateTime, nullable=False, server_default=func.now())

    discovery_run = relationship("StrategyDiscoveryRun", back_populates="strategies",
                                 foreign_keys=[discovery_run_id])

    __table_args__ = (
        CheckConstraint("strategy_type IN ('BUILTIN','CUSTOM','AI_DISCOVERED')", name="ck_strategy_type"),
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id      = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    ticker           = Column(Text)
    start_date       = Column(Date, nullable=False)
    end_date         = Column(Date, nullable=False)
    initial_capital  = Column(Real, nullable=False, default=10000.0)
    status           = Column(Text, nullable=False, default="pending")
    total_trades     = Column(Integer)
    winning_trades   = Column(Integer)
    losing_trades    = Column(Integer)
    win_rate         = Column(Real)
    total_pnl        = Column(Real)
    total_return_pct = Column(Real)
    max_drawdown_pct = Column(Real)
    sharpe_ratio     = Column(Real)
    avg_gain_pct     = Column(Real)
    avg_loss_pct     = Column(Real)
    equity_curve     = Column(Text)
    created_at       = Column(DateTime, nullable=False, server_default=func.now())
    completed_at     = Column(DateTime)
    error_message    = Column(Text)

    strategy = relationship("Strategy")
    trades   = relationship("BacktestTrade", back_populates="backtest_run", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("status IN ('pending','running','completed','failed')", name="ck_bt_status"),
    )


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    run_id       = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False)
    ticker       = Column(Text, nullable=False)
    side         = Column(Text, nullable=False)
    entry_date   = Column(Date, nullable=False)
    entry_price  = Column(Real, nullable=False)
    exit_date    = Column(Date)
    exit_price   = Column(Real)
    shares       = Column(Real, nullable=False)
    pnl          = Column(Real)
    pnl_pct      = Column(Real)
    entry_reason = Column(Text)
    exit_reason  = Column(Text)
    holding_days = Column(Integer)

    backtest_run = relationship("BacktestRun", back_populates="trades")


class StockAnalysis(Base):
    __tablename__ = "stock_analyses"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    ticker                = Column(Text, nullable=False)
    analysis_date         = Column(Date, nullable=False)
    technical_analysis    = Column(Text)
    news_analysis         = Column(Text)
    geopolitical_analysis = Column(Text)
    regime_analysis       = Column(Text)
    overall_score         = Column(Real)
    outlook               = Column(Text)
    key_factors           = Column(Text)
    summary               = Column(Text)
    model_used            = Column(Text)
    tokens_used           = Column(Integer)
    duration_seconds      = Column(Real)
    status                = Column(Text, nullable=False, default="pending")
    error_message         = Column(Text)
    created_at            = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("ticker", "analysis_date", name="uq_stock_analysis"),
        CheckConstraint("status IN ('pending','running','completed','failed')", name="ck_analysis_status"),
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key        = Column(Text, primary_key=True)
    value      = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, server_default=func.now())


class StockGroup(Base):
    __tablename__ = "stock_groups"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    name          = Column(Text, nullable=False, unique=True)
    description   = Column(Text)
    vv_source_id  = Column(Text)
    color         = Column(Text, default="#6366f1")
    created_at    = Column(DateTime, nullable=False, server_default=func.now())

    memberships   = relationship("StockGroupMembership", back_populates="group",
                                 cascade="all, delete-orphan")


class StockGroupMembership(Base):
    __tablename__ = "stock_group_memberships"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    group_id   = Column(Integer, ForeignKey("stock_groups.id"), nullable=False)
    ticker     = Column(Text, nullable=False)
    added_at   = Column(DateTime, nullable=False, server_default=func.now())

    group      = relationship("StockGroup", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("group_id", "ticker", name="uq_group_ticker"),
    )


class ScalableTransaction(Base):
    __tablename__ = "scalable_transactions"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    reference        = Column(Text, nullable=False, unique=True)
    ticker           = Column(Text, nullable=False)
    isin             = Column(Text, nullable=True)
    description      = Column(Text)
    transaction_type = Column(Text, nullable=False)
    shares           = Column(Real, nullable=False)
    price            = Column(Real, nullable=False)
    amount           = Column(Real, nullable=False)
    fee              = Column(Real, nullable=False, default=0.0)
    tax              = Column(Real, nullable=False, default=0.0)
    currency         = Column(Text, nullable=False, default="EUR")
    transaction_date = Column(Date, nullable=False)
    holding_id       = Column(Integer, ForeignKey("holdings.id"), nullable=True)
    imported_at      = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("transaction_type IN ('Buy','Sell')", name="ck_scalable_tx_type"),
    )
