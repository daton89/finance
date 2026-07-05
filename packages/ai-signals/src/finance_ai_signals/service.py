"""
Orchestrates AI stock analysis — calls the Stock Signal Worker and persists results.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx
from finance_core.models import StockAnalysis
from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_ai_signals.client import StockSignalClient

logger = logging.getLogger(__name__)

_SIGNAL_TO_OUTLOOK = {
    "BUY": "bullish",
    "HOLD": "neutral",
    "WAIT": "bearish",
}


class AISignalService:
    """Orchestrates AI analysis: calls Worker, persists StockAnalysis record."""

    def __init__(self, client: StockSignalClient):
        self.client = client

    async def analyze_ticker(
        self,
        ticker: str,
        db: Session,
        settings: dict,
        model: str = "openai/gpt-oss-120b:free",
        horizon: str = "week",
    ) -> StockAnalysis:
        """
        Run AI analysis for a ticker, persist the result, and return it.

        If the Worker call fails, creates a failed StockAnalysis record with
        the error message so the signal engine can still read a result.
        """
        today = date.today()

        existing = db.execute(
            select(StockAnalysis)
            .where(StockAnalysis.ticker == ticker)
            .where(StockAnalysis.analysis_date == today)
            .where(StockAnalysis.status == "completed")
        ).scalar_one_or_none()

        if existing is not None:
            logger.debug(f"[{ticker}] Already analyzed today — skipping.")
            return existing

        try:
            result = await self.client.analyze(ticker, model=model, horizon=horizon)
        except httpx.HTTPStatusError as exc:
            detail = _extract_error_detail(exc)
            logger.warning(f"[{ticker}] Worker error: {detail}")
            msg = f"Worker HTTP {exc.response.status_code}: {detail}"
            return _failed_analysis(ticker, today, db, msg)
        except httpx.RequestError as exc:
            logger.warning(f"[{ticker}] Worker unreachable: {exc}")
            return _failed_analysis(ticker, today, db, f"Worker unreachable: {exc}")

        analysis = StockAnalysis(
            ticker=ticker,
            analysis_date=today,
            news_analysis=result.headline,
            overall_score=float(result.confidence),
            outlook=_SIGNAL_TO_OUTLOOK.get(result.signal, "neutral"),
            key_factors=result.catalyst,
            summary=result.reasoning,
            model_used=model,
            status="completed",
        )
        db.add(analysis)
        db.commit()
        logger.info(
            f"[{ticker}] AI analysis stored (signal={result.signal}, score={result.confidence})"
        )
        return analysis


def _extract_error_detail(exc: httpx.HTTPStatusError) -> str:
    try:
        body = exc.response.json()
        return body.get("error", str(body))
    except Exception:
        return exc.response.text[:200]


def _failed_analysis(ticker: str, analysis_date: date, db: Session, error: str) -> StockAnalysis:
    record = StockAnalysis(
        ticker=ticker,
        analysis_date=analysis_date,
        status="failed",
        error_message=error,
    )
    db.add(record)
    db.commit()
    return record
