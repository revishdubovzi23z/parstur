import requests
from bs4 import BeautifulSoup
import PTN
import re
import sys
import codecs
import time

if sys.stdout.encoding != 'utf-8':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

class RutorParser:
    def __init__(self):
        self.mirror = "http://rutor.info" 
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        }

    def clean_display_title(self, full_title):
        # Очищаем заголовок от технического мусора
        t = full_title
        # Пытаемся сохранить год
        year_match = re.search(r'\((\d{4})\)', t)
        year_str = f" ({year_match.group(1)})" if year_match else ""
        
        # Отсекаем всё, что начинается с технических тегов качества
        t = re.split(r'SATRip|Web-DL|WEBRip|WEB-Rip|BDRip|BDRemux|HDTV|Rip|1080p|720p|4K|8K|HDR|SDR|UHD|HEVC|AVC|MVO|DUB|VO|от\s+|\|', t, flags=re.IGNORECASE)[0]
        
        # Убираем лишние скобки
        t = re.sub(r'\(.*?\)|\[.*?\]', '', t).strip()
        # Возвращаем чистое имя + год
        clean = f"{t}{year_str}".strip().replace('  ', ' ')
        # Унифицируем букву x
        return clean.replace('x', 'х').replace('X', 'Х')

    def get_category_releases(self, category_id=1, page=0, max_retries=3):
        url = f"{self.mirror}/browse/{page}/{category_id}/0/0"
        
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, headers=self.headers, timeout=20)
                response.raise_for_status()
                break
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(5 * attempt)
                else:
                    return []

        # Используем content для автоопределения кодировки (UTF-8 или 1251)
        soup = BeautifulSoup(response.content, "html.parser")
        rows = soup.find_all("tr", class_=re.compile("gira|tum"))
        
        releases = []
        for row in rows:
            tds = row.find_all("td")
            if len(tds) < 4: continue
                
            title_tag = tds[1].find("a", href=re.compile(r"/torrent/"))
            if not title_tag: continue
            
            full_title = title_tag.text.strip()
            link = self.mirror + title_tag["href"]
            
            magnet_tag = tds[1].find("a", href=re.compile(r"magnet:?"))
            magnet = magnet_tag["href"] if magnet_tag else ""
            
            rutor_id_match = re.search(r'/torrent/(\d+)', title_tag["href"])
            rutor_id = rutor_id_match.group(1) if rutor_id_match else title_tag["href"].strip('/').split('/')[-1]

            date_str = tds[0].text.strip()
            
            # Красивое название для базы
            display_title = self.clean_display_title(full_title)
            
            # Парсим год для метаданных
            parsed = PTN.parse(full_title)
            year = parsed.get("year")
            if not year:
                year_match = re.search(r'\((\d{4})\)', full_title)
                if year_match:
                    year = int(year_match.group(1))

            releases.append({
                "rutor_id": rutor_id,
                "full_title": full_title,
                "parsed_title": display_title,
                "year": year,
                "magnet": magnet,
                "link": link,
                "date_str": date_str,
                "quality": parsed.get("resolution", "Unknown")
            })
            
        return releases

    def search_releases(self, query, category_id=0, page=0, max_retries=3):
        import urllib.parse
        encoded_query = urllib.parse.quote(query)
        url = f"{self.mirror}/search/{page}/{category_id}/0/0/{encoded_query}"
        
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, headers=self.headers, timeout=20)
                response.raise_for_status()
                break
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(5 * attempt)
                else:
                    return []

        soup = BeautifulSoup(response.content, "html.parser")
        rows = soup.find_all("tr", class_=re.compile("gira|tum"))
        
        releases = []
        for row in rows:
            tds = row.find_all("td")
            if len(tds) < 4: continue
                
            title_tag = tds[1].find("a", href=re.compile(r"/torrent/"))
            if not title_tag: continue
            
            full_title = title_tag.text.strip()
            link = self.mirror + title_tag["href"]
            
            magnet_tag = tds[1].find("a", href=re.compile(r"magnet:?"))
            magnet = magnet_tag["href"] if magnet_tag else ""
            
            rutor_id_match = re.search(r'/torrent/(\d+)', title_tag["href"])
            rutor_id = rutor_id_match.group(1) if rutor_id_match else title_tag["href"].strip('/').split('/')[-1]

            date_str = tds[0].text.strip()
            display_title = self.clean_display_title(full_title)
            
            parsed = PTN.parse(full_title)
            year = parsed.get("year")
            if not year:
                year_match = re.search(r'\((\d{4})\)', full_title)
                if year_match:
                    year = int(year_match.group(1))

            releases.append({
                "rutor_id": rutor_id,
                "full_title": full_title,
                "parsed_title": display_title,
                "year": year,
                "magnet": magnet,
                "link": link,
                "date_str": date_str,
                "quality": parsed.get("resolution", "Unknown")
            })
            
        return releases
