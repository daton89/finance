from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from finance_core.base import Base, engine, SessionLocal
from finance_core.models import AppSetting
from finance_market_data.refresh import refresh_market_data
from finance_portfolio import parse_scalable_csv

app = FastAPI(title="Finance Backend")

Base.metadata.create_all(bind=engine)

_HTML_TOP = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Finance - CSV Import</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f5f7;color:#1d1d1f;padding:2rem}
.card{max-width:640px;margin:2rem auto;background:#fff;border-radius:16px;padding:2rem;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h1{font-size:1.5rem;font-weight:600;margin-bottom:.25rem}
.sub{color:#6e6e73;font-size:.9rem;margin-bottom:1.5rem}
input[type=file]{width:100%;padding:.5rem;border:1px dashed #c7c7cc;border-radius:8px;background:#fafafa;cursor:pointer}
button{margin-top:1rem;padding:.6rem 1.5rem;background:#0071e3;color:#fff;border:none;border-radius:8px;font-size:.9rem;font-weight:500;cursor:pointer}
button:hover{background:#0077ed}
.result{margin-top:1.5rem;padding:1rem;border-radius:8px}
.ok{background:#e8f5e9;border:1px solid #a5d6a7}
.err{background:#fbe9e7;border:1px solid #ef9a9a}
.result h2{font-size:1rem;margin-bottom:.5rem}
.result ul{list-style:none;font-size:.9rem}
.result li{margin-bottom:.25rem}
</style>
</head>
<body>
<div class="card">
<h1>Scalable Capital CSV Import</h1>
<p class="sub">Upload your Scalable Capital transaction report</p>
<form action="/import" method="post" enctype="multipart/form-data">
<input type="file" name="file" accept=".csv,.tsv" required style="margin-bottom:.5rem">
<button type="submit">Upload &amp; Import</button>
</form>
"""

_HTML_BOTTOM = """\
</div>
</body>
</html>"""


def _page(result=None, error=None) -> str:
    if error:
        body = '<div class="result err"><h2>Error</h2><p>{}</p></div>'.format(error)
    elif result:
        parts = ['<div class="result ok"><h2>Import complete</h2><ul>']
        parts.append(f"<li>Transactions imported: {result.transactions_imported}</li>")
        parts.append(f"<li>Holdings created: {result.holdings_created}, closed: {result.holdings_closed}</li>")
        if result.tickers_added:
            parts.append(f"<li>New tickers: {', '.join(result.tickers_added)}</li>")
        if result.skipped:
            parts.append(f"<li>Skipped rows: {len(result.skipped)}</li>")
        parts.append("</ul></div>")
        body = "".join(parts)
    else:
        body = ""
    return _HTML_TOP + body + _HTML_BOTTOM


@app.get("/", response_class=HTMLResponse)
async def index():
    return _page()


@app.post("/import", response_class=HTMLResponse)
async def import_csv(file: UploadFile | None = None):
    if file is None or not file.filename:
        return _page(error="No file selected")
    contents = await file.read()
    db = SessionLocal()
    try:
        result = await parse_scalable_csv(contents, db)
        return _page(result=result)
    except Exception as e:
        return _page(error=str(e))
    finally:
        db.close()


_REFRESH_SETTING_KEY = "market_data_last_refreshed_at"
_REFRESH_MIN_INTERVAL_SECONDS = 60


@app.post("/api/market-data/refresh")
async def api_refresh_market_data():
    db = SessionLocal()
    try:
        setting = db.get(AppSetting, _REFRESH_SETTING_KEY)
        now = datetime.utcnow()

        if setting is not None:
            last = datetime.fromisoformat(setting.value)
            if (now - last).total_seconds() < _REFRESH_MIN_INTERVAL_SECONDS:
                raise HTTPException(
                    status_code=429,
                    detail=f"Refresh already ran within the last {_REFRESH_MIN_INTERVAL_SECONDS}s",
                )

        result = await refresh_market_data(db)

        if setting is None:
            db.add(AppSetting(key=_REFRESH_SETTING_KEY, value=now.isoformat()))
        else:
            setting.value = now.isoformat()
        db.commit()

        return {"refreshed": result.refreshed, "skipped": result.skipped}
    finally:
        db.close()
