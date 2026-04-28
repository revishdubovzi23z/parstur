import sys
from HdRezkaApi import HdRezkaSearch
import re

def test_search():
    searcher = HdRezkaSearch("https://rezka.ag")
    results = searcher.fast_search("Мертвец")
    
    print(f"Results for 'Мертвец':")
    for res in results:
        res_name = res['title']
        res_url = res['url']
        
        year_match = re.search(r'\((\d{4})\)', res_name)
        res_year = int(year_match.group(1)) if year_match else None
        
        print(f"- {res_name} (Year: {res_year}) -> {res_url}")

if __name__ == "__main__":
    test_search()
