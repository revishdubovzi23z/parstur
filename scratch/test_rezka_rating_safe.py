from HdRezkaApi import HdRezkaSearch, HdRezkaApi

def test_rating_safe():
    s = HdRezkaSearch("https://rezka.ag")
    results = s.fast_search("Гарри Поттер")
    if not results:
        print("Nothing found")
        return
    
    url = results[0]['url']
    print(f"Testing URL: {url}")
    rezka = HdRezkaApi(url)
    
    print(f"Property 'rating': {rezka.rating}")
    # Проверим тип и структуру
    r = rezka.rating
    print(f"Class of rating: {r.__class__}")
    
    # Пытаемся достать конкретные значения
    if hasattr(r, 'kp'): print(f"KP: {r.kp}")
    if hasattr(r, 'imdb'): print(f"IMDb: {r.imdb}")
    if hasattr(r, 'rezka'): print(f"Rezka: {r.rezka}")
    
    # Если это просто словарь или объект с полями
    try:
        print(f"Values: {r.value}")
        print(f"Votes: {r.votes}")
    except: pass

if __name__ == '__main__':
    test_rating_safe()
