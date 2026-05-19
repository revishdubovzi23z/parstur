import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import Database
from runtime.kinopub import _authenticated_client
from sync_kinopub import (
    CATEGORY_TYPE_HINT,
    _candidate_titles,
    _clean_query_for_search,
    best_candidate,
    score_candidate,
)

db = Database()
conn = db.get_connection()
item_row = conn.execute("SELECT * FROM items WHERE id = 5426").fetchone()
conn.close()

item = dict(item_row)
print(f"Par2 Item: {item}")

client = _authenticated_client()
queries = _candidate_titles(item) or [str(item.get("title") or "").strip()]
queries = [q for q in queries if q]
print(f"Candidate search queries: {queries}")

type_hint = CATEGORY_TYPE_HINT.get(int(item.get("category_id") or 0))
print(f"Type hint: {type_hint}")

# For series, we don't pass the year to the search API
api_year = None
print(f"API Search Year: {api_year}")

raw = []
seen_ids = set()
for query in queries:
    clean_q = _clean_query_for_search(query)
    if not clean_q:
        continue
    results = client.search(
        clean_q,
        type_=None,
        year=api_year,
        limit=25,
    )
    print(f"Search query '{clean_q}' with year {api_year} found {len(results)} items")
    for entry in results:
        cand_id = entry.get("id")
        if cand_id in seen_ids:
            continue
        seen_ids.add(cand_id)
        raw.append(entry)

print(f"\nEvaluating {len(raw)} total unique candidates:")
for cand in raw:
    score = score_candidate(item=item, candidate=cand, type_hint=type_hint)
    print(
        f"  Candidate #{cand.get('id')} title={cand.get('title')!r} year={cand.get('year')} score={score} (KP: {cand.get('kinopoisk')}, IMDB: {cand.get('imdb')})"
    )

pick = best_candidate(item=item, raw_results=raw, type_hint=type_hint)
if pick:
    print(f"\nSUCCESS MATCH: {pick}")
else:
    print("\nFAIL MATCH: No candidate matched accept threshold.")
