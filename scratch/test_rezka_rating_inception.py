from HdRezkaApi import HdRezkaSearch, HdRezkaApi

def test_rating_inception():
    s = HdRezkaSearch("https://rezka.ag")
    results = s.fast_search("Начало") # Inception
    for res in results:
        if 'nachalo-2010' in res['url']:
            url = res['url']
            break
    else:
        url = results[0]['url']
        
    print(f"Testing URL: {url}")
    rezka = HdRezkaApi(url)
    r = rezka.rating
    print(f"Rating Object: {r}")
    print(f"Type: {type(r)}")
    
    # Посмотрим атрибуты объекта рейтинга
    print("Attributes:")
    import inspect
    for name, obj in inspect.getmembers(r):
        if not name.startswith('__'):
            print(f" - {name}: {obj}")

if __name__ == '__main__':
    test_rating_inception()
