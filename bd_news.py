import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import httpx
import trafilatura
from bs4 import BeautifulSoup

from collector import save, entry_date
from db import get_conn, init_db

DHAKA = ZoneInfo("Asia/Dhaka")

# Add or edit sources here. "sections" is optional: paste a tech section URL
# if the script doesn't find it automatically.
SOURCES = [
    {"name": "The Daily Star",        "home": "https://www.thedailystar.net",  "sections": ["https://www.thedailystar.net/tech-startup"]},
    {"name": "The Business Standard", "home": "https://www.tbsnews.net",       "sections": []},
    {"name": "Prothom Alo English",   "home": "https://en.prothomalo.com",     "sections": []},
    {"name": "Daily Sun",             "home": "https://www.daily-sun.com",     "sections": []},
]

MAX_AGE_DAYS = 7          # ignore older articles
MAX_NEW_PER_SOURCE = 15   # new articles to save per source per run
COMMON_FEED_PATHS = ["/rss.xml", "/feed", "/feed/", "/rss", "/rss/", "/rss/feed"]
TECH_TOKENS = {"tech", "technology", "science", "cyber", "digital", "ai", "startup", "ict"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Only articles matching these keywords are kept (edit freely).
KEYWORDS_CI = re.compile(
    r"cyber\w*|hack\w*|ransomware|malware|phishing|spyware|data breach|data leak|"
    r"data protection|artificial intelligence|machine learning|chatbot|chatgpt|openai|"
    r"\btech\b|technolog\w*|software|startup|\bICT\b|fintech|btrc|telecom|"
    r"internet|digital|semiconductor|smartphone|\b5G\b|online fraud|e-commerce|"
    r"encryption|vulnerabilit\w*|data center|cloud computing|robot\w*",
    re.I,
)
KEYWORD_AI = re.compile(r"\bAI\b")  # case-sensitive so "said" doesn't match


def relevant(text):
    return bool(KEYWORDS_CI.search(text) or KEYWORD_AI.search(text))


def get(url):
    return httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)


def host(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def clean(html):
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


# ---------- RSS discovery ----------
def discover_feeds(home):
    """Returns (working feed URLs, homepage soup or None)."""
    candidates, soup = [], None
    try:
        r = get(home)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for link in soup.find_all("link", rel="alternate"):
            t = (link.get("type") or "").lower()
            if "rss" in t or "atom" in t:
                candidates.append(urljoin(home, link.get("href", "")))
    except Exception as e:
        msg = str(e)[:100]
        if "403" in msg:
            msg = "403 Forbidden (the site blocks scripts)"
        print("  homepage fetch failed:", msg)
    candidates += [urljoin(home, p) for p in COMMON_FEED_PATHS]

    good = []
    for url in dict.fromkeys(candidates):
        try:
            parsed = feedparser.parse(get(url).content)
            if parsed.entries:
                good.append(url)
        except Exception:
            pass
    # /feed and /feed/ are usually the same feed, so keep only one of them
    unique = []
    for u in good:
        if u.rstrip("/") not in [x.rstrip("/") for x in unique]:
            unique.append(u)
    return unique, soup


def collect_feed(conn, name, url):
    parsed = feedparser.parse(get(url).content)
    new = kept = 0
    for e in parsed.entries:
        title, link = e.get("title", ""), e.get("link", "")
        summary = e.get("summary", "")
        if not title or not link:
            continue
        if not relevant(title + " " + clean(summary)):
            continue
        kept += 1
        new += save(conn, title, link, name, "Bangladesh", entry_date(e), summary)
    print(f"  feed {url}: {len(parsed.entries)} entries, {kept} relevant, {new} new")
    return new


# ---------- Section page scraping ----------
def tech_sections(home, soup, configured):
    found = list(configured)
    if soup:
        for a in soup.select("a[href]"):
            url = urljoin(home, a["href"]).split("#")[0]
            if host(url) != host(home):
                continue
            parts = [p for p in urlparse(url).path.split("/") if p]
            if len(parts) != 1:
                continue
            tokens = set(re.split(r"[-_]", parts[0].lower()))
            if tokens & TECH_TOKENS:
                found.append(url)
    return list(dict.fromkeys(found))[:4]


def article_links(page_url):
    r = get(page_url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = {}
    for a in soup.select("a[href]"):
        url = urljoin(page_url, a["href"]).split("#")[0]
        if host(url) != host(page_url):
            continue
        text = a.get_text(" ", strip=True)
        parts = [p for p in urlparse(url).path.split("/") if p]
        if len(text) >= 30 and len(parts) >= 2:  # looks like a headline link
            out.setdefault(url, text)
    return list(out.items())


def read_article(url):
    r = get(url)
    r.raise_for_status()
    text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
    meta = trafilatura.extract_metadata(r.text)
    published = datetime.now(timezone.utc)
    if meta and meta.date:
        try:
            d = datetime.strptime(meta.date[:10], "%Y-%m-%d").replace(hour=12, tzinfo=DHAKA)
            published = min(d.astimezone(timezone.utc), published)
        except ValueError:
            pass
    return text[:12000], published


def mark_skipped(conn, url):
    conn.execute("INSERT OR IGNORE INTO skipped (url) VALUES (?)", (url,))
    conn.commit()


def collect_section(conn, name, page, budget):
    new = 0
    stats = {"old": 0, "short": 0, "off-topic": 0, "failed": 0}
    try:
        links = article_links(page)
    except Exception as e:
        print(f"  section {page}: failed ({str(e)[:80]})")
        return 0
    print(f"  section {page}: {len(links)} article links")
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    for url, title in links:
        if new >= budget:
            break
        if conn.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
            continue
        if conn.execute("SELECT 1 FROM skipped WHERE url=?", (url,)).fetchone():
            continue
        try:
            text, published = read_article(url)
        except Exception as e:
            stats["failed"] += 1
            print("    failed:", url[-60:], str(e)[:60])
            continue
        time.sleep(1)  # be gentle with the site
        if published < cutoff:
            stats["old"] += 1
            mark_skipped(conn, url)
            continue
        if len(text) < 200:
            stats["short"] += 1
            mark_skipped(conn, url)
            continue
        if not relevant(title + " " + text[:1500]):
            stats["off-topic"] += 1
            mark_skipped(conn, url)
            continue
        new += save(conn, title, url, name, "Bangladesh", published.isoformat(), text)
        conn.commit()
        print("    +", title[:70])
    print("    skipped:", ", ".join(f"{v} {k}" for k, v in stats.items() if v) or "none")
    return new


# ---------- Run everything ----------
def collect_source(conn, src):
    name, home = src["name"], src["home"]
    print(f"\n{name}")
    new = 0
    feeds, soup = discover_feeds(home)
    print("  feeds found:", feeds or "none")
    for f in feeds:
        try:
            new += collect_feed(conn, name, f)
        except Exception as e:
            print("  feed failed:", str(e)[:80])
    sections = tech_sections(home, soup, src.get("sections", []))
    print("  tech sections:", sections or "none")
    for page in sections:
        new += collect_section(conn, name, page, MAX_NEW_PER_SOURCE)
    conn.commit()
    return new


def collect_bd():
    total = 0
    with get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS skipped (url TEXT PRIMARY KEY)")
        for src in SOURCES:
            try:
                total += collect_source(conn, src)
            except Exception as e:
                print("  source failed:", str(e)[:100])
    return total


if __name__ == "__main__":
    init_db()
    print("\nNew Bangladesh articles:", collect_bd())