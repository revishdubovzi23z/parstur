import datetime
import sqlite3

# Категории точно как на Rutor
RUTOR_CATEGORIES = {
    0: "Любая категория",
    -1: "Все видео", # Кастомная категория: 1, 4, 5, 16, 7
    1: "Зарубежные фильмы",
    5: "Наши фильмы",
    12: "Научно-популярные фильмы",
    4: "Зарубежные сериалы",
    16: "Наши сериалы",
    6: "Телевизор",
    7: "Мультипликация",
    10: "Аниме",
    8: "Игры",
    13: "Спорт и Здоровье",
    15: "Юмор",
    3: "Другое"
}

VIDEO_CATEGORY_IDS = (1, 4, 5, 16, 7)

class TrackerAppCore:
    def __init__(self, db_path="app_data.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Инициализация базы данных: таблицы для релизов, фильмов/игр/музыки, и игнор-листа."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Таблица категорий
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY,
                    name TEXT
                )
            ''')
            
            # Обновляем категории в базе из нашего словаря
            for cat_id, cat_name in RUTOR_CATEGORIES.items():
                if cat_id <= 0: continue # Пропускаем "Любая" и "Все видео" в таблице, они обрабатываются логикой
                cursor.execute("INSERT OR REPLACE INTO categories (id, name) VALUES (?, ?)", (cat_id, cat_name))

            # Таблица для уникальных сущностей (Фильм, Музыкальный альбом, Игра)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER,
                    title TEXT,
                    year INTEGER,
                    kp_rating REAL,
                    imdb_rating REAL,
                    description TEXT,
                    poster_url TEXT,
                    is_ignored BOOLEAN DEFAULT 0,
                    is_metadata_fixed BOOLEAN DEFAULT 0,
                    checked_tech INTEGER DEFAULT 0,
                    checked_uz INTEGER DEFAULT 0,
                    checked_poiskkino INTEGER DEFAULT 0,
                    UNIQUE(title, year, category_id)
                )
            ''')
            # Таблица для конкретных раздач (торрентов)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    torrent_title TEXT,
                    quality TEXT,
                    size TEXT,
                    link TEXT,
                    date_added TEXT,
                    FOREIGN KEY(item_id) REFERENCES items(id)
                )
            ''')
            # Таблица с личными оценками пользователя
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_title TEXT,
                    item_year INTEGER,
                    rating INTEGER,
                    service TEXT, -- 'kp' или 'imdb'
                    external_id TEXT,
                    UNIQUE(item_title, item_year)
                )
            ''')

            # Таблица для коллекций (закладок)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    sort_order INTEGER DEFAULT 0
                )
            ''')
            # Связь предметов с коллекциями
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS collection_items (
                    collection_id INTEGER,
                    item_id INTEGER,
                    PRIMARY KEY (collection_id, item_id),
                    FOREIGN KEY(collection_id) REFERENCES collections(id) ON DELETE CASCADE,
                    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
                )
            ''')
            
            # Добавим несколько базовых коллекций, если их нет
            default_collections = [
                "говноозвучки", "на телефон просмотр", "детские", 
                "в первую очередь", "Проходняк сериал завершенные", 
                "Топ сериалы с завершённые", "проходняк фильмы", 
                "Топ сериал с продолжением", "Проходняк сериал с продолжением", 
                "тв шоу", "топ фильмы", "docum"
            ]
            for name in default_collections:
                cursor.execute("INSERT OR IGNORE INTO collections (name) VALUES (?)", (name,))

            conn.commit()

    # ==========================================
    # 1. СБОР И ДЕДУБЛИКАЦИЯ
    # ==========================================
    def fetch_new_releases(self, date_from: datetime.date, date_to: datetime.date, category_id: int):
        """
        Идет на сайт (rutor/rutracker) и собирает раздачи за указанный период в выбранной категории.
        """
        print(f"Сбор данных для категории: {RUTOR_CATEGORIES.get(category_id)} с {date_from} по {date_to}")
        # Здесь будет логика парсера (которую мы тестировали ранее)
        pass

    def deduplicate_and_save(self, raw_releases, category_id):
        """
        Убирает дубликаты. Логика зависит от категории:
        - Для фильмов: ищем по "Название + Год"
        - Для музыки: ищем по "Исполнитель + Альбом + Год"
        - Для игр: ищем по "Название + Год"
        Сохраняет уникальную сущность в таблицу `items`, а сами торренты привязывает к ней в `releases`.
        """
        pass

    # ==========================================
    # 2. ФИЛЬТРАЦИЯ И ВЫДАЧА (ГЛАВНАЯ ЛОГИКА)
    # ==========================================
    def get_feed(self, 
                 category_id: int, 
                 min_kp: float = 0.0, 
                 min_imdb: float = 0.0, 
                 min_year: int = None,
                 max_year: int = None,
                 hide_ignored: bool = True,
                 hide_already_rated_by_me: bool = True):
        """
        Выдает ленту релизов с учетом всех твоих настроек.
        """
        query = "SELECT * FROM items WHERE category_id = ?"
        params = [category_id]

        if hide_ignored:
            query += " AND is_ignored = 0"
        
        if min_kp > 0:
            query += " AND kp_rating >= ?"
            params.append(min_kp)
            
        if min_imdb > 0:
            query += " AND imdb_rating >= ?"
            params.append(min_imdb)
            
        if min_year:
            query += " AND year >= ?"
            params.append(min_year)

        if max_year:
            query += " AND year <= ?"
            params.append(max_year)

        # Выполняем SQL запрос и возвращаем отфильтрованный список
        # ...
        return [] # Возвращает список карточек

    # ==========================================
    # 3. ИГНОР И ОЦЕНКИ
    # ==========================================
    def ignore_item(self, item_id: int):
        """
        Пользователь нажал 'Игнор' на карточке.
        Больше эта сущность не будет появляться в свежих релизах, даже если выйдет новое качество (4K).
        Работает для фильмов, музыки, сериалов и т.д.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE items SET is_ignored = 1 WHERE id = ?", (item_id,))
            conn.commit()
        print(f"Сущность {item_id} добавлена в игнор-лист.")

    def sync_my_accounts(self, kp_csv_path: str = None, imdb_csv_path: str = None):
        """
        Синхронизирует оценки с Кинопоиска и IMDb.
        Загружает файлы, чтобы приложение знало, какие фильмы ты уже оценил и скрывало их.
        """
        pass

    # ==========================================
    # 4. РАБОТА С КАРТОЧКОЙ
    # ==========================================
    def get_item_details(self, item_id: int):
        """
        Возвращает детальную информацию о карточке (описание, постер, оценки)
        и список всех доступных торрентов (разное качество) для перехода на сайт.
        """
        pass

if __name__ == "__main__":
    # Пример использования ядра:
    app = TrackerAppCore()
    print("Ядро приложения инициализировано. База данных создана.")
