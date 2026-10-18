import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from db import get_conn

app = FastAPI()
DHAKA = ZoneInfo("Asia/Dhaka")


@app.get("/api/articles")
def articles(category: str | None = None, severity: str | None = None,
             region: str | None = None, sort: str = "date", days: int = 2):
    # "today + yesterday" in Dhaka time
    now = datetime.now(DHAKA)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start.astimezone(timezone.utc).isoformat()

    sql = "SELECT * FROM articles WHERE category IS NOT NULL AND published_at >= ?"
    params = [start_utc]
    if category:
        sql += " AND category = ?"
        params.append(category)
    if severity:
        sql += " AND severity = ?"
        params.append(severity)
    if region:
        sql += " AND region = ?"
        params.append(region)

    if sort == "severity":
        sql += """ ORDER BY CASE severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                   WHEN 'Medium' THEN 3 ELSE 4 END, published_at DESC"""
    else:
        sql += " ORDER BY published_at DESC"

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        d["bullets"] = json.loads(d["bullets"] or "[]")
        d.pop("raw_text", None)
        out.append(d)
    return out


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def home():
    return FileResponse("static/index.html")