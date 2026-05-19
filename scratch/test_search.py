import logging
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime.kinopub import _authenticated_client

logging.basicConfig(level=logging.INFO)

try:
    client = _authenticated_client()
    print("Authenticated successfully!")

    # 1. Search with field="title" (default)
    print("\n--- Search: field=title (via API wrapper default) ---")
    res_title_ru = client.search("Ночная смена", limit=10)
    print(f"Query 'Ночная смена' returned {len(res_title_ru)} items:")
    for item in res_title_ru[:5]:
        print(
            f"  ID: {item.get('id')}, Title: {item.get('title')}, KP: {item.get('kinopoisk')}, IMDB: {item.get('imdb')}"
        )

    res_title_en = client.search("Last Straw", limit=10)
    print(f"\nQuery 'Last Straw' returned {len(res_title_en)} items:")
    for item in res_title_en[:5]:
        print(
            f"  ID: {item.get('id')}, Title: {item.get('title')}, KP: {item.get('kinopoisk')}, IMDB: {item.get('imdb')}"
        )

    # 2. Search without field="title"
    print("\n--- Search: NO field parameter ---")

    # Let's bypass field title manually
    params = {"q": "Last Straw", "perpage": 10}
    res_no_field = client._request("GET", "/v1/items/search", params=params).get("items", []) or []
    print(f"Query 'Last Straw' (no field) returned {len(res_no_field)} items:")
    for item in res_no_field[:5]:
        print(
            f"  ID: {item.get('id')}, Title: {item.get('title')}, KP: {item.get('kinopoisk')}, IMDB: {item.get('imdb')}"
        )

    params_ru = {"q": "Ночная смена", "perpage": 10}
    res_no_field_ru = (
        client._request("GET", "/v1/items/search", params=params_ru).get("items", []) or []
    )
    print(f"\nQuery 'Ночная смена' (no field) returned {len(res_no_field_ru)} items:")
    for item in res_no_field_ru[:5]:
        print(
            f"  ID: {item.get('id')}, Title: {item.get('title')}, KP: {item.get('kinopoisk')}, IMDB: {item.get('imdb')}"
        )

except Exception as e:
    print(f"Error: {e}")
