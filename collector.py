import time
import feedparser
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from db import get_conn, init_db

DHAKA = ZoneInfo("Asia/Dhaka")

# ---------- Global RSS feeds ----------
FEEDS = [
    {"name": "The Hacker News",   "url": "https://feeds.feedburner.com/TheHackersNews", "region": "Global"},
    {"name": "BleepingComputer",  "url": "https://www.bleepingcomputer.com/feed/",      "region": "Global"},
    {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/",           "region": "Global"},
    {"name": "SecurityWeek",      "url": "https://feeds.feedburner.com/securityweek",   "region": "Global"},
]

# ---------- BGD e-GOV CIRT settings ----------
CIRT_URL = "https://www.cirt.gov.bd/advisories"
CIRT_PAGES = 2           # listing pages to read per run (6 advisories per page)
CIRT_MAX_PER_RUN = 15    # max new advisories to open per run
HEADERS = {"User-Agent": "cyber-news-dashboard/0.1"}


def entry_date(entry):
    """Turn a feed entry's date into a UTC ISO string."""
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def save(conn, title, url, source, region, published_at, raw_text):
    """Insert one article. Returns 1 if new, 0 if the URL already existed."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (title, url, source, region, published_at, raw_text)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (title, url, source, region, published_at, raw_text),
    )
    return cur.rowcount


# ---------- Global RSS ----------
def collect_rss():
    new = 0
    with get_conn() as conn:
        for f in FEEDS:
            parsed = feedparser.parse(f["url"])
            print(f"{f['name']}: {len(parsed.entries)} entries")
            for e in parsed.entries:
                new += save(
                    conn,
                    e.get("title", ""),
                    e.get("link", ""),
                    f["name"],
                    f["region"],
                    entry_date(e),
                    e.get("summary", ""),
                )
    return new


# ---------- BGD e-GOV CIRT ----------
def parse_cirt_date(text):
    """'Published on 01-Oct-2026 12:00:00' -> UTC ISO string.
    Assumes the site shows Dhaka time."""
    text = text.replace("Published on", "").strip()
    dt = datetime.strptime(text, "%d-%b-%Y %H:%M:%S")
    return dt.replace(tzinfo=DHAKA).astimezone(timezone.utc).isoformat()


def cirt_links():
    """Stage 1: collect advisory links from the listing pages."""
    links, seen = [], set()
    for page in range(CIRT_PAGES):
        resp = httpx.get(CIRT_URL, params={"page": page}, timeout=30,
                         follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select("a.post-title[href]"):
            url = urljoin(CIRT_URL, a["href"])
            if url not in seen:
                seen.add(url)
                links.append(url)
        time.sleep(1)  # be gentle with a government server
    return links


def cirt_fetch_one(url):
    """Stage 2: open one advisory and read title, date, text."""
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    h1 = soup.select_one("section.description h1")
    date_tag = soup.select_one("p.published-date")
    body = soup.select_one("div.holder")

    title = h1.get_text(strip=True) if h1 else ""
    published = (parse_cirt_date(date_tag.get_text(strip=True))
                 if date_tag else datetime.now(timezone.utc).isoformat())
    text = body.get_text(" ", strip=True)[:6000] if body else ""
    return title, published, text


def collect_cirt():
    links = cirt_links()
    print(f"BGD e-GOV CIRT: {len(links)} advisory links found")

    new = 0
    with get_conn() as conn:
        for url in links:
            if new >= CIRT_MAX_PER_RUN:
                break
            exists = conn.execute(
                "SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone()
            if exists:
                continue  # already saved, don't fetch again
            try:
                title, published, text = cirt_fetch_one(url)
                if not title:
                    print("  skipped (no title):", url)
                    continue
                new += save(conn, title, url, "BGD e-GOV CIRT",
                            "Bangladesh", published, text)
                print("  +", title[:70])
            except Exception as e:
                print("  failed:", url, e)
            time.sleep(1)  # be gentle with a government server
    return new


if __name__ == "__main__":
    init_db()
    print("New RSS articles:", collect_rss())
    try:
        print("New CIRT advisories:", collect_cirt())
    except Exception as e:
        print("CIRT scraper failed:", e)