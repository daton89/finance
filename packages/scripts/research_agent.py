#!/usr/bin/env python3
"""
research_agent.py — Research Agent per Finance Team.

Tasks:
  - Fetch latest news for MU and AMD
  - Earnings calendar monitoring (next earnings, beats, estimates)
  - Analyst upgrades/downgrades detection
  - Basic sentiment scoring (headline-based)
  - Sector context (semiconductors, AI)
  - Formatted report per Telegram
"""

import json
import os
import re
import sys
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import yfinance as yf
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    SCRIPT_DIR = os.path.dirname(SCRIPT_DIR)

DATA_DIR = os.path.join(SCRIPT_DIR, "data")
STATE_FILE = os.path.join(DATA_DIR, "research_state.json")
os.makedirs(DATA_DIR, exist_ok=True)

def _load_portfolio_stock_tickers() -> list[str]:
    """Stock in portafoglio con ticker yfinance — il focus news segue le posizioni reali."""
    portfolio_file = os.path.join(SCRIPT_DIR, "portfolio.json")
    try:
        with open(portfolio_file) as f:
            positions = json.load(f).get("positions", [])
        tickers = [p["ticker"] for p in positions
                   if p.get("ticker") and p.get("type") == "stock"]
        if tickers:
            return tickers
    except Exception:
        pass
    return ["MU", "AMD"]  # fallback storico


TICKERS = _load_portfolio_stock_tickers()
SECTOR_KEYWORDS = ["semiconductor", "chip", "ai", "nvidia", "hbm", "dram", "nand"]

# ── Sentiment Lexicon ──

_POSITIVE = [
    "beat", "surge", "rally", "upgrade", "bullish", "buy", "outperform",
    "growth", "record", "breakout", "positive", "raise", "raised", "strong",
    "momentum", "expansion", "innovation", "partner", "launch", "approval",
    "optimistic", "profit", "earnings beat", "guidance up",
]

_NEGATIVE = [
    "downgrade", "sell", "underperform", "bearish", "cut", "cutting",
    "decline", "drop", "fall", "loss", "negative", "weak", "slowdown",
    "concern", "risk", "investigation", "lawsuit", "delay", "cancel",
    "recession", "tariff", "ban", "restriction", "warning", "miss",
    "earnings miss", "guidance down", "layoff",
]

def score_sentiment(text: str) -> tuple[str, float]:
    """Basic keyword sentiment scoring. Returns (label, score) where score is -1..1."""
    text_lower = text.lower()
    pos_score = sum(1 for w in _POSITIVE if w in text_lower)
    neg_score = sum(1 for w in _NEGATIVE if w in text_lower)
    total = pos_score + neg_score
    if total == 0:
        return "neutral", 0.0
    net = (pos_score - neg_score) / total
    if net > 0.3:
        return "positive", net
    elif net < -0.3:
        return "negative", net
    return "neutral", net

def format_pubdate(pub: str) -> str:
    """Format ISO date to human readable."""
    if not pub:
        return "?"
    try:
        dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        if diff.days == 0:
            return f"{diff.seconds // 3600}h ago"
        elif diff.days == 1:
            return "yesterday"
        elif diff.days < 7:
            return f"{diff.days}d ago"
        return dt.strftime("%b %d")
    except:
        return pub[:10]

# ── Data fetching ──

def fetch_ticker_data(ticker: str) -> dict:
    """Fetch all data for a ticker: news, earnings, calendar, price."""
    t = yf.Ticker(ticker)
    info = {}
    try:
        info = t.info or {}
    except:
        pass

    # News
    raw_news = []
    try:
        raw_news = t.news or []
    except:
        pass

    # Earnings
    earnings_df = None
    try:
        earnings_df = t.earnings_dates
    except:
        pass

    # Calendar
    cal = {}
    try:
        cal_raw = t.calendar or {}
        cal = {str(k): str(v) for k, v in cal_raw.items()}
    except:
        pass

    return {
        "ticker": ticker,
        "info": info,
        "news": raw_news,
        "earnings_df": earnings_df,
        "calendar": cal,
    }

def extract_news(data: dict) -> list[dict]:
    """Extract and normalize news from yfinance data."""
    articles = []
    for item in data.get("news", []):
        c = item.get("content", item)  # handle both formats
        if not isinstance(c, dict):
            continue
        title = c.get("title", "")
        if not title:
            continue
        articles.append({
            "title": title,
            "summary": c.get("summary", ""),
            "pub_date": c.get("pubDate", ""),
            "publisher": c.get("provider", {}).get("displayName", "") if isinstance(c.get("provider"), dict) else "",
            "link": c.get("clickThroughUrl", {}).get("url", "") if isinstance(c.get("clickThroughUrl"), dict) else c.get("canonicalUrl", {}).get("url", c.get("url", "")),
        })
    return articles

def get_earnings_summary(ticker: str, data: dict) -> dict:
    """Extract earnings calendar and history."""
    cal = data.get("calendar", {})
    earnings_df = data.get("earnings_df")

    result = {"next_earnings": None, "last_beat_pct": None, "estimates": {}}

    # Next earnings date from calendar
    earn_str = cal.get("Earnings Date", "")
    if earn_str:
        # Format: "[datetime.date(2026, 9, 23)]" or similar
        m = re.search(r'(\d{4}),\s*(\d{1,2}),\s*(\d{1,2})', earn_str)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            result["next_earnings"] = f"{y}-{mo:02d}-{d:02d}"
        else:
            # Try ISO format
            m2 = re.search(r'(\d{4}-\d{2}-\d{2})', earn_str)
            if m2:
                result["next_earnings"] = m2.group(0)

    # Last beat from earnings history
    if earnings_df is not None and len(earnings_df) > 0:
        # Find the most recent reported earnings
        reported = earnings_df[earnings_df["Reported EPS"].notna()]
        if len(reported) > 0:
            last = reported.iloc[0]
            surprise = last.get("Surprise(%)", None)
            if pd.notna(surprise):
                result["last_beat_pct"] = float(surprise)
            est = last.get("EPS Estimate", None)
            rep = last.get("Reported EPS", None)
            if pd.notna(est) and pd.notna(rep):
                result["last_eps_estimate"] = float(est)
                result["last_eps_reported"] = float(rep)

        # Next estimate
        upcoming = earnings_df[earnings_df["Reported EPS"].isna()]
        if len(upcoming) > 0:
            est = upcoming.iloc[0].get("EPS Estimate", None)
            if pd.notna(est):
                result["next_eps_estimate"] = float(est)

    # Estimates from calendar
    for k in ["Earnings High", "Earnings Low", "Earnings Average"]:
        if k in cal:
            result[k.lower().replace(" ", "_")] = cal[k]

    return result

def get_price_change(ticker: str) -> Optional[float]:
    """Get 1-day and 5-day price change."""
    try:
        df = yf.download(ticker, period="5d", interval="1d", auto_adjust=True)
        if df.empty:
            return None, None, None
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"][ticker]
        else:
            close = df["Close"]
        prices = close.values
        if len(prices) >= 2:
            chg_1d = ((prices[-1] - prices[-2]) / prices[-2]) * 100
        else:
            chg_1d = 0
        if len(prices) >= 5:
            chg_5d = ((prices[-1] - prices[-5]) / prices[-5]) * 100
        else:
            chg_5d = chg_1d
        last_price = float(prices[-1])
        return last_price, chg_1d, chg_5d
    except:
        return None, None, None

# ── Sentiment aggregation ──

def aggregate_sentiment(articles: list[dict]) -> dict:
    """Aggregate sentiment across articles."""
    if not articles:
        return {"overall": "neutral", "score": 0.0, "positive": 0, "negative": 0, "neutral": 0}

    scores = []
    for a in articles:
        label, score = score_sentiment(f"{a['title']} {a['summary']}")
        scores.append((label, score))

    pos_count = sum(1 for l, _ in scores if l == "positive")
    neg_count = sum(1 for l, _ in scores if l == "negative")
    neu_count = sum(1 for l, _ in scores if l == "neutral")
    avg_score = sum(s for _, s in scores) / len(scores) if scores else 0

    if avg_score > 0.15:
        overall = "positive"
    elif avg_score < -0.15:
        overall = "negative"
    else:
        overall = "neutral"

    return {"overall": overall, "score": round(avg_score, 2),
            "positive": pos_count, "negative": neg_count, "neutral": neu_count}

# ── Key topics detection ──

def detect_key_topics(articles: list[dict]) -> list[str]:
    """Detect recurring key topics in articles."""
    topics = []
    all_text = " ".join(f"{a['title']} {a['summary']}" for a in articles).lower()

    topic_patterns = [
        ("earnings", r"earnings|eps|quarter|revenue|guidance|forecast"),
        ("analyst", r"analyst|upgrade|downgrade|price target|buy rating"),
        ("product", r"launch|announce|release|unveil|new chip|next-gen"),
        ("sector", r"semiconductor|chip|nand|dram|hbm|foundry"),
        ("ai", r"ai|artificial intelligence|machine learning|nvidia|gpu"),
        ("macro", r"tariff|trade|china|inflation|fed|interest rate|recession"),
        ("competition", r"intel|samsung|sk hynix|nvidia|tsmc|qualcomm"),
    ]

    for topic, pattern in topic_patterns:
        if re.search(pattern, all_text):
            topics.append(topic)

    return topics

# ── Research State ──

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_seen_articles": [], "last_report_date": None}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ── Main Research ──

def research_brief() -> str:
    """Generate a comprehensive research brief."""
    state = load_state()
    today = date.today().strftime("%A %d %B %Y")
    now = datetime.now(timezone.utc)

    lines = [f"🔬 RESEARCH BRIEF — {today}", ""]
    section_separator = "─" * 40

    all_new_articles = []

    for ticker in TICKERS:
        data = fetch_ticker_data(ticker)
        articles = extract_news(data)
        earnings = get_earnings_summary(ticker, data)
        price, chg_1d, chg_5d = get_price_change(ticker)

        lines.append(section_separator)
        price_str = f"${price:.2f}" if price else "N/A"
        chg1d_str = f" | 1d: {chg_1d:+.2f}%" if chg_1d is not None else ""
        chg5d_str = f" | 5d: {chg_5d:+.2f}%" if chg_5d is not None else ""
        lines.append(f"  {ticker}  |  {price_str}{chg1d_str}{chg5d_str}")

        # Earnings
        next_earn = earnings.get("next_earnings")
        if next_earn:
            try:
                earn_date = datetime.strptime(next_earn[:10], "%Y-%m-%d").date()
                days_to = (earn_date - date.today()).days
                beat = earnings.get("last_beat_pct")
                est = earnings.get("next_eps_estimate")
                beat_str = f" (last beat: {beat:+.1f}%)" if beat else ""

                lines.append(f"  📅 Earnings: {earn_date} ({days_to}d away){beat_str}")
                if est:
                    lines.append(f"     EPS estimate: ${est:.2f}")

                # Alert if earnings within 2 weeks
                if 0 <= days_to <= 14:
                    lines.append(f"  ⚠️  EARNINGS ALERT — {days_to} giorni! Posizionati.")
            except:
                lines.append(f"  📅 Earnings: {next_earn[:10]}")

        # Dividend
        div_date = data.get("calendar", {}).get("Dividend Date", "")
        if div_date and div_date != "None":
            try:
                d = datetime.strptime(div_date[:10], "%Y-%m-%d").date()
                if d >= date.today():
                    lines.append(f"  💰 Dividend: {d}")
            except:
                pass

        # News sentiment
        if articles:
            sentiment = aggregate_sentiment(articles)
            topics = detect_key_topics(articles)
            emoji = "🟢" if sentiment["overall"] == "positive" else "🔴" if sentiment["overall"] == "negative" else "🟡"
            lines.append(f"  {emoji} Sentiment: {sentiment['overall']} ({sentiment['score']:+.2f})")
            lines.append(f"     Articles: {sentiment['positive']} pos / {sentiment['negative']} neg / {sentiment['neutral']} neu")
            if topics:
                lines.append(f"     Topics: {', '.join(topics)}")

            # Recent articles (last 3, highlight new ones)
            lines.append(f"  📰 Latest headlines:")
            seen_ids = set(state.get("last_seen_articles", []))
            for a in articles[:5]:
                title = a["title"]
                pub = format_pubdate(a["pub_date"])
                # Check if new since last report
                article_id = f"{ticker}:{title[:60]}"
                is_new = article_id not in seen_ids
                all_new_articles.append(article_id)

                marker = "  🆕 " if is_new else "    "
                lines.append(f"{marker}• {title}")
                lines.append(f"      {pub}")
        else:
            lines.append(f"  📰 No recent news")

    # New articles tracker
    if all_new_articles:
        state["last_seen_articles"] = [a for a in all_new_articles[-50:]]  # keep last 50
    state["last_report_date"] = now.isoformat()
    save_state(state)

    # ── Sector context ──
    lines.append("")
    lines.append(section_separator)
    lines.append("  🌐 Sector Context: Semiconductors")

    try:
        # Get SMH (semiconductor ETF) for sector context
        smh = yf.Ticker("SMH")
        smh_info = {}
        try:
            smh_info = smh.info or {}
        except:
            pass
        smh_price, smh_1d, smh_5d = get_price_change("SMH")
        if smh_price:
            lines.append(f"  SMH (Semiconductor ETF): ${smh_price:.2f}")
            if smh_1d is not None:
                lines.append(f"  1d: {smh_1d:+.2f}% | 5d: {smh_5d:+.2f}%")
    except:
        pass

    # ── Swing momentum crossover ──
    lines.append("")
    lines.append(section_separator)
    lines.append("  📊 Swing Momentum Crossover")

    for ticker in TICKERS:
        try:
            df = yf.download(ticker, period="3mo", interval="1d", auto_adjust=True)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    close = df["Close"][ticker]
                else:
                    close = df["Close"]

                last_close = float(close.iloc[-1])
                ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
                ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]

                delta = close.diff()
                gain = delta.clip(lower=0)
                loss = -delta.clip(upper=0)
                avg_g = gain.ewm(span=14, adjust=False).mean()
                avg_l = loss.ewm(span=14, adjust=False).mean()
                rs = avg_g / avg_l.replace(0, float('nan'))
                rsi_val = float((100 - (100 / (1 + rs))).iloc[-1])

                vs_ema20 = ((last_close - ema20) / ema20) * 100
                ema_status = "🟢 sopra" if last_close > ema20 else "🔴 sotto"

                lines.append(f"  {ticker}: ${last_close:.0f} | RSI: {rsi_val:.0f} | {ema_status} EMA20 ({vs_ema20:+.1f}%)")
        except:
            lines.append(f"  {ticker}: dati non disponibili")

    # ── Dynamic Key Actions ──
    lines.append("")
    lines.append(section_separator)
    lines.append("  ⚡ Key Actions:")

    for ticker in TICKERS:
        data = fetch_ticker_data(ticker)
        earnings = get_earnings_summary(ticker, data)
        next_earn = earnings.get("next_earnings")
        if next_earn:
            try:
                earn_date = datetime.strptime(next_earn[:10], "%Y-%m-%d").date()
                days_to = (earn_date - date.today()).days
                if 0 <= days_to <= 14:
                    lines.append(f"    🚨 EARNINGS THIS WEEK: {ticker} il {earn_date} ({days_to}d)!")
                elif 15 <= days_to <= 30:
                    lines.append(f"    ⏰ Earnings imminenti: {ticker} il {earn_date} ({days_to}d)")
                elif days_to > 30:
                    lines.append(f"    📅 Earnings {ticker}: {earn_date} ({days_to}d)")
            except:
                pass

    lines.append("    • Leggi le notizie contrassegnate 🆕")
    lines.append("    • Swing signals alle 14:30 CET")

    return "\n".join(lines)

# ── CLI ──

def cmd_earnings():
    """Show only earnings calendar."""
    lines = ["📅 Earnings Calendar", ""]
    for ticker in TICKERS:
        data = fetch_ticker_data(ticker)
        earnings = get_earnings_summary(ticker, data)
        next_earn = earnings.get("next_earnings")
        if next_earn:
            try:
                earn_date = datetime.strptime(next_earn[:10], "%Y-%m-%d").date()
                days_to = (earn_date - date.today()).days
                beat = earnings.get("last_beat_pct")
                est = earnings.get("next_eps_estimate")
                lines.append(f"  {ticker}: {earn_date} ({days_to}d)")
                if est:
                    lines.append(f"     EPS est: ${est:.2f}")
                if beat:
                    lines.append(f"     Last beat: {beat:+.1f}%")
            except:
                lines.append(f"  {ticker}: {next_earn}")
        else:
            lines.append(f"  {ticker}: N/A")
    lines.append("")
    lines.append("  MU div date: 2026-07-21 (pay)")
    return "\n".join(lines)

def cmd_sentiment():
    """Show detailed sentiment analysis."""
    lines = ["🎯 Sentiment Analysis", ""]
    for ticker in TICKERS:
        data = fetch_ticker_data(ticker)
        articles = extract_news(data)
        if articles:
            lines.append(f"── {ticker} ──")
            for a in articles[:5]:
                label, score = score_sentiment(f"{a['title']} {a['summary']}")
                emoji = "🟢" if label == "positive" else "🔴" if label == "negative" else "🟡"
                lines.append(f"  {emoji} [{label}] {a['title'][:80]}")
                lines.append(f"      score: {score:+.2f} | {format_pubdate(a['pub_date'])}")
            lines.append("")
    return "\n".join(lines)

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "earnings":
            print(cmd_earnings())
        elif cmd == "sentiment":
            print(cmd_sentiment())
        elif cmd == "reset":
            save_state({"last_seen_articles": [], "last_report_date": None})
            print("✅ Research state resettato.")
        else:
            print(f"Comandi: earnings | sentiment | reset")
        return

    # Default: research brief
    print(research_brief())

if __name__ == "__main__":
    main()
