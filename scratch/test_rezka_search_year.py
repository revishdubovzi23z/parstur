import sys
from HdRezkaApi import HdRezkaSearch
import json

def test_search():
    searcher = HdRezkaSearch("https://rezka.ag")
    
    query = "Мертвец 2024"
    print(f"Results for '{query}':")
    results = searcher.fast_search(query)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    
    query_en = "Dead Man 2024"
    print(f"\nResults for '{query_en}':")
    results_en = searcher.fast_search(query_en)
    print(json.dumps(results_en, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    test_search()
