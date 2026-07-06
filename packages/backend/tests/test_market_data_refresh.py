from fastapi.testclient import TestClient

import main
from finance_market_data.refresh import RefreshResult
from main import app

client = TestClient(app)


def test_refresh_endpoint_runs_and_guards_against_rapid_retrigger(monkeypatch):
    async def fake_refresh(db):
        return RefreshResult(refreshed=["AAPL"], skipped=[])

    monkeypatch.setattr(main, "refresh_market_data", fake_refresh)

    resp1 = client.post("/api/market-data/refresh")
    assert resp1.status_code == 200
    assert resp1.json() == {"refreshed": ["AAPL"], "skipped": []}

    resp2 = client.post("/api/market-data/refresh")
    assert resp2.status_code == 429
