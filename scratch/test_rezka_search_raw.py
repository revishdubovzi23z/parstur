import sys
from HdRezkaApi import HdRezkaSearch
import json

def test_search():
    searcher = HdRezkaSearch("https://rezka.ag")
    results = searcher.fast_search("Мертвец")
    
    # Try with English title too
    results_en = searcher.fast_search("Dead Man")
    
    print("Results for 'Мертвец':")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    
    print("\nResults for 'Dead Man':")
    print(json.dumps(results_en, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    test_search()
