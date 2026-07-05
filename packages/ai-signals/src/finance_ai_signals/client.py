"""
HTTP client for the Stock Signal Cloudflare Worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

Signal = Literal["BUY", "HOLD", "WAIT"]
Horizon = Literal["week", "months", "years"]


@dataclass
class WorkerAnalysis:
    ticker: str
    name: str
    price: float
    change: float
    signal: Signal
    confidence: int
    reasoning: str
    target_price: float | None
    pe: str
    headline: str
    catalyst: str
    horizon: str
    analyzed_at: int


class StockSignalClient:
    """Thin HTTP adapter for the Stock Signal Worker API."""

    def __init__(self, worker_url: str, app_token: str, timeout: float = 30.0):
        self.worker_url = worker_url.rstrip("/")
        self.app_token = app_token
        self.timeout = timeout

    async def analyze(
        self,
        ticker: str,
        model: str = "openai/gpt-oss-120b:free",
        horizon: Horizon = "week",
    ) -> WorkerAnalysis:
        """
        Call /api/analyze on the Stock Signal Worker.
        Raises httpx.HTTPStatusError on non-2xx.
        """
        params = {"ticker": ticker, "model": model, "horizon": horizon}
        headers = {"X-App-Token": self.app_token}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.worker_url}/api/analyze",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        return WorkerAnalysis(
            ticker=data["ticker"],
            name=data.get("name", ticker),
            price=data["price"],
            change=data["change"],
            signal=data["signal"],
            confidence=data["confidence"],
            reasoning=data.get("reasoning", ""),
            target_price=data.get("targetPrice"),
            pe=str(data.get("pe", "")),
            headline=data.get("headline", ""),
            catalyst=data.get("catalyst", ""),
            horizon=data.get("horizon", "week"),
            analyzed_at=data.get("analyzedAt", 0),
        )

    async def discover(
        self,
        model: str = "openai/gpt-oss-120b:free",
        strategy: str = "movers",
        sector: str | None = None,
    ) -> list[dict]:
        """Call /api/discover on the Stock Signal Worker."""
        params = {"model": model, "strategy": strategy}
        if sector is not None:
            params["sector"] = sector

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{self.worker_url}/api/discover",
                params=params,
                headers={"X-App-Token": self.app_token},
            )
            resp.raise_for_status()
            return resp.json()
