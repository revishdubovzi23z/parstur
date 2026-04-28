from HdRezkaApi import HdRezkaApi
import re

def test_full_metadata():
    url = "https://rezka.ag/films/action/770-nachalo-2010.html"
    rezka = HdRezkaApi(url)
    soup = rezka.soup
    
    print(f"Movie: {rezka.name}")
    
    # Ищем блок рейтингов
    rates = soup.find('div', class_='b-post__info_rates')
    if rates:
        print("\n--- Ratings Block Found ---")
        # Проверяем все ссылки внутри блока
        for a in rates.find_all('a'):
            href = a.get('href', '')
            text = a.text
            print(f"Link found: {text} -> {href}")
            
            # Извлекаем KP ID
            if 'kinopoisk.ru' in href:
                kp_id_match = re.search(r'film/(\d+)', href)
                if kp_id_match:
                    print(f"  >>> EXTRACTED KP ID: {kp_id_match.group(1)}")
            
            # Извлекаем IMDb ID
            if 'imdb.com' in href:
                imdb_id_match = re.search(r'title/(tt\d+)', href)
                if imdb_id_match:
                    print(f"  >>> EXTRACTED IMDb ID: {imdb_id_match.group(1)}")
        
        # Проверяем текстовые значения рейтингов (теги <b> внутри <span>)
        for s in rates.find_all('span'):
            val_tag = s.find('b')
            if val_tag:
                print(f"Text rating: {s.text.strip()} -> Value: {val_tag.text}")

if __name__ == '__main__':
    test_full_metadata()
