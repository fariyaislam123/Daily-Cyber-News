from db import init_db
from collector import collect_rss, collect_cirt
from bd_news import collect_bd
from enrich import enrich_all

init_db()
print("RSS new:", collect_rss())
try:
    print("CIRT new:", collect_cirt())
except Exception as e:
    print("CIRT failed:", e)
print("BD news new:", collect_bd())
enrich_all()