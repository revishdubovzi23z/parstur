import sys
from HdRezkaApi import HdRezkaSearch
import json

def test_search():
    searcher = HdRezkaSearch("https://rezka.ag")
    query = "Человек, который смеётся 2026"
    print(f"Results for '{query}':")
    results = searcher.fast_search(query)
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    test_search()
