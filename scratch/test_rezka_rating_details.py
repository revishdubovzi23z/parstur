from HdRezkaApi import HdRezkaApi

def test_rating():
    url = "https://rezka.ag/films/fantasy/1126-garri-potter-i-filosofskiy-kamen-2001.html"
    rezka = HdRezkaApi(url)
    print(f"Movie: {rezka.name}")
    print(f"Property 'rating': {rezka.rating}")
    print(f"Type of rating: {type(rezka.rating)}")
    
    # Попробуем посмотреть, что внутри, если это объект
    if hasattr(rezka.rating, '__dict__'):
        print(f"Rating dict: {rezka.rating.__dict__}")
    
    # Проверим суп напрямую на всякий случай
    rates = rezka.soup.find('div', class_='b-post__info_rates')
    if rates:
        print(f"HTML Rates block text: {rates.text.strip()}")

if __name__ == '__main__':
    test_rating()
