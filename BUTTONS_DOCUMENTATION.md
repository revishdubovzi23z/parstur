# Antigravity Tracker v2.1 — Полная документация логики кнопок и процессов

---

## БАЗА ДАННЫХ (app_data.db)

### Таблица `items`
| Колонка | Тип | Назначение |
|---|---|---|
| id | INTEGER PK AUTO | Уникальный ID |
| category_id | INTEGER | Категория Rutor (1=Заруб.фильмы, 4=Заруб.сериалы, 5=Русские фильмы, 16=Русские сериалы, 7=Мультфильмы, и т.д.) |
| title | TEXT | Отображаемое название |
| original_title | TEXT | Оригинальное название |
| year | INTEGER | Год выпуска |
| kp_rating | REAL DEFAULT 0 | Рейтинг Кинопоиск |
| imdb_rating | REAL DEFAULT 0 | Рейтинг IMDb |
| description | TEXT | Описание сюжета |
| poster_url | TEXT | URL постера |
| is_ignored | BOOLEAN DEFAULT 0 | 1=игнорируется пользователем |
| ignored_at | TEXT | Время игнора |
| is_metadata_fixed | BOOLEAN DEFAULT 0 | 1=все метаданные заполнены |
| is_reprocessed | INTEGER DEFAULT 0 | 1=обработан reprocess_database.py |
| checked_tech | INTEGER DEFAULT 0 | 1=проверен через Kinopoisk Legacy API |
| checked_poiskkino | INTEGER DEFAULT 0 | 1=проверен через PoiskKino API |
| checked_rezka | INTEGER DEFAULT 0 | 1=проверен через Rezka |
| kp_id | TEXT | ID Кинопоиска |
| imdb_id | TEXT | ID IMDb (tt1234567) |
| kinorium_id | TEXT | ID Кинориума |
| title_norm | TEXT | Нормализованное название для сопоставления |
| rezka_url | TEXT | URL на HdRezka.ag |
| UNIQUE(title, year, category_id) | | Защита от дублей |

### Таблица `releases`
| Колонка | Тип |
|---|---|
| id | INTEGER PK AUTO |
| item_id | INTEGER FK → items.id |
| rutor_id | TEXT |
| torrent_title | TEXT |
| quality | TEXT |
| size | TEXT |
| link | TEXT |
| magnet | TEXT |
| date_added | TEXT |

### Таблица `user_ratings`
| Колонка | Тип |
|---|---|
| id | INTEGER PK AUTO |
| item_title | TEXT |
| original_title | TEXT |
| item_year | INTEGER |
| rating | INTEGER |
| service | TEXT (kp/imdb/merged) |
| imdb_id | TEXT |
| kp_id | TEXT |
| title_norm | TEXT |
| original_title_norm | TEXT |

### Таблица `collections`
- id INTEGER PK AUTO, name TEXT UNIQUE, sort_order INTEGER

### Таблица `collection_items`
- collection_id INTEGER FK, item_id INTEGER FK, added_at TEXT

### Таблица `item_search_names`
- item_id INTEGER, name_norm TEXT — несколько нормализованных вариантов названий для поиска

### Таблица `job_history`
- id, job_type, start_time, end_time, duration, items_processed, total_items, status

### Таблица `app_state`
- key TEXT PK, value TEXT — хранит `last_visit`

---

## ВИРТУАЛЬНЫЕ КАТЕГОРИИ

| ID | Название | SQL-логика |
|---|---|---|
| -1 | «Все видео» | `category_id IN (1, 4, 5, 16, 7)` |
| -100 | «НЕТ ПОСТЕРА» | Видео-категории + `(poster_url IS NULL OR poster_url = '')` |
| -101 | «НЕТ РЕЙТИНГА» | Видео + `(kp_rating=0 OR NULL OR imdb_rating=0 OR NULL)` |
| -102 | «НЕТ KP ID» | Видео + `(kp_id IS NULL OR kp_id = '')` |
| -103 | «НЕТ IMDb ID» | Видео + `(imdb_id IS NULL OR imdb_id = '')` |
| -104 | «НЕТ ID ВООБЩЕ» | Видео + нет kp_id И нет imdb_id |
| 0 | «Любая категория» | Без фильтра по категории |
| -2 | «ИГНОРИРОВАНО» | `is_ignored = 1` |

---

## `get_watched_item_ids()` — 3 стратегии сопоставления

Находит item_id, которые пользователь оценил. Используется при фильтре «Скрыть просмотренное».

**Стратегия 1 — По IMDb ID:**
- Берёт все `imdb_id` из `user_ratings`
- Запрашивает `SELECT id FROM items WHERE imdb_id IN (...)`

**Стратегия 2 — По KP ID:**
- Берёт все `kp_id` из `user_ratings`
- Запрашивает `SELECT id FROM items WHERE kp_id IN (...)`

**Стратегия 3 — По нормализованному названию:**
- Берёт `title_norm` и `original_title_norm` из `user_ratings`
- Порциями по 900 (лимит SQLite)
- Запрашивает `SELECT item_id FROM item_search_names WHERE name_norm IN (...)`
- Таблица `item_search_names` содержит несколько вариантов названий каждого айтема

Все три стратегии аддитивны — совпадение по любой из них = просмотрено.

---

## `/api/feed` — Полный состав фильтров

**Параметры:** category_id, collection_id, search, min_kp/max_kp, min_imdb/max_imdb, min_year/max_year, min_date/max_date, hide_ignored, hide_rated, hide_collected, page, limit

**Конструкция SQL:**
1. **База:** `WHERE 1=1`
2. **Коллекция:** `items.id IN (SELECT item_id FROM collection_items WHERE collection_id = ?)`
3. **Категория:** маппинг виртуальных категорий (см. выше) или `category_id = ?`
4. **hide_ignored:** `is_ignored = 0`
5. **Поиск:** `title LIKE ? OR title_norm LIKE ? OR EXISTS (SELECT 1 FROM item_search_names sn WHERE sn.item_id = items.id AND sn.name_norm LIKE ?)`
6. **Дата:** `items.id IN (SELECT item_id FROM releases WHERE date_added >= ? / <= ?)`
7. **hide_rated:** Вызывает `get_watched_item_ids()`, затем `items.id NOT IN (...)`
8. **hide_collected:** `items.id NOT IN (SELECT item_id FROM collection_items)`
9. **KP/IMDb рейтинг:** `kp_rating >= ?` / `kp_rating <= ?`
10. **Год:** `year >= ?` / `year <= ?`

**Сортировка:**
- Игнор: `ignored_at DESC, latest_release DESC`
- Коллекция: `ci.added_at DESC, latest_release DESC`
- По умолчанию: `latest_release DESC NULLS LAST`

---

## ДОКУМЕНТАЦИЯ КНОПОК

---

### 1. FULL PIPELINE (Полный цикл)

- **Расположение:** Сайдбар, верх кнопок, градиентная кнопка «FULL CYCLE (1.1 → Cleanup)»
- **API:** `POST /api/start_full_pipeline`
- **Status Key:** `full_pipeline`
- **Логика:** Запускает 6 шагов **последовательно**:

| Шаг | Скрипт | Аргументы | Status Key | Лог-файл |
|---|---|---|---|---|
| 1 | sync_job.py | ["video", "0", "0"] | sync_video | sync_video_log.txt |
| 2 | reprocess_database.py | [] | reprocess | reprocess_log.txt |
| 3 | fix_posters.py | ["poiskkino", "fix_poiskkino_log.txt"] | poiskkino | fix_poiskkino_log.txt |
| 4 | fix_posters.py | ["tech", "fix_tech_log.txt"] | fix | fix_tech_log.txt |
| 5 | rezka_sync.py | [] | rezka | sync_rezka_log.txt |
| 6 | cleanup_duplicates.py | [] | cleanup | cleanup_log.txt |

- Если любой шаг завершается «stopped» или «error» — конвейер прерывается
- Фронтенд автоматически переключается на лог-вкладку текущего шага
- **Завершение:** `process_status["full_pipeline"]` = «completed» / «stopped» / «error»

---

### 2. 1.1 PARSING VIDEO (Парсинг видео)

- **Расположение:** Сайдбар, кнопка «1.1 PARSING VIDEO»
- **API:** `POST /api/start_sync_video?min_year=X&max_year=Y&min_date=Z`
- **Status Key:** `sync_video`
- **Лог-файл:** `sync_video_log.txt`
- **Прогресс:** `progress_sync_video.json`
- **Скрипт:** `sync_job.py` с `mode="video"`

**Пошаговая логика:**

1. Загружает чекпоинт (для resume после остановки)
2. Определяет начальную дату: `manual_min_date` (из UI) или `get_last_sync_date()` (MAX date_added из releases, или сегодня - 30 дней)
3. Итерирует категории с `use_tmdb=True`: **1, 4, 5, 16, 7**
4. Для каждой категории:
   - Скачивает страницы с rutor.info (до 20 страниц)
   - Парсит релизы через `RutorParser.get_category_releases()` — извлекает title, year, rutor_id, magnet, link, quality, date
   - Фильтрует по дате (только новее target) и году (MIN_YEAR/MAX_YEAR)
   - Останавливает пагинацию когда мало новых (< stop_threshold=5)
   - Задержка 0.3с между запросами
5. Дедуплицирует по `(normalize_title(parsed_title), year)`
6. Для каждого уникального айтема:
   - Проверяет наличие в БД: `SELECT id FROM items WHERE year=? AND category_id=?` + `normalize_title()`
   - **Если новый:**
     - Скрейпит страницу Rutor для извлечения KP ID (`kinopoisk.ru/rating/(\d+).gif` или `/film/(\d+)`) и IMDb ID (`imdb.com/title/(tt\d+)`)
     - Если ID не найдены — «глубокий поиск» на Rutor (поиск по названию, до 3 совпадений)
     - Запрашивает **TMDB API** (сначала по imdb_id через `find_by_imdb_id`, потом по названию через `search_movie`) для: poster_url, description, title, imdb_id
     - Вставляет в `items` с `is_metadata_fixed = 0`, `is_reprocessed = 0`
   - **Если существует:** Добавляет новые релизы в `releases`
   - Заполняет `item_search_names` нормализованными вариантами названия
7. Сохраняет чекпоинт после каждой категории

**Источники данных:**
- Rutor.info (скрейпинг)
- TMDB API (постер, описание, название, imdb_id)
- Локальная БД

**Запись в БД:**
- `items`: INSERT OR IGNORE (title, year, category_id, poster_url, description, imdb_id, kp_id, kp_rating, imdb_rating, is_metadata_fixed, title_norm)
- `releases`: INSERT (item_id, rutor_id, torrent_title, quality, date_added, magnet, link)
- `item_search_names`: INSERT (item_id, name_norm)

**Метка окончания:** Нет checked_* флага. Отслеживается через `process_status["sync_video"]` = «completed»

---

### 3. 1.2 PARSING GAMES / SOFTWARE

- **Расположение:** Сайдбар, кнопка «1.2 PARSING GAMES / SOFTWARE»
- **API:** `POST /api/start_sync_other?min_year=X&max_year=Y&min_date=Z`
- **Status Key:** `sync_other`
- **Лог-файл:** `sync_other_log.txt`

**Логика:** Тот же `sync_job.py` но `mode="other"`:
- Категории с `use_tmdb=False`: **6 (ТВ), 10 (Аниме), 15 (Юмор), 8 (Игры), 12 (Научнопоп)** — обновлено через config
- НЕ запрашивает TMDB
- Всё равно пытается извлечь KP/IMDb ID со страниц Rutor
- Та же дедупликация и логика хранения

**Источники:** Только Rutor.info (без TMDB)
**Метка окончания:** `process_status["sync_other"]` = «completed»

---

### 4. 2.0 DATABASE UPDATE (Обновление БД)

- **Расположение:** Сайдбар, кнопка «2.0 DATABASE UPDATE» + чекбокс «Recheck all items»
- **API:** `POST /api/start_reprocess?force=true|false`
- **Status Key:** `reprocess`
- **Лог-файл:** `reprocess_log.txt`
- **Скрипт:** `reprocess_database.py`

**Пошаговая логика:**

1. Если передан `specific_id` — обрабатывает только этот айтем
2. Иначе строит WHERE:
   - Без force: `category_id IN (1,4,5,16,7) AND is_reprocessed = 0 AND (is_metadata_fixed = 0 OR kp_id/imdb_id нет) AND (постер/описание/imdb_id/kp_id/imdb_rating нет ИЛИ название содержит мусор)`
   - С force: `category_id IN (1,4,5,16,7) AND is_reprocessed = 0`
3. Обрабатывает батчами по 100
4. Для каждого айтема:
   - **Шаг 1 — Скрейп Rutor:** Если kp_id/imdb_id отсутствуют, посещает каждую страницу релиза на Rutor для извлечения ID
   - **Шаг 2 — TMDB API:** Если отсутствуют постер/описание/imdb_rating или название грязное:
     - Сначала `tmdb.find_by_imdb_id(imdb_id)`
     - Затем `tmdb.search_movie(original_title или search_title, year)`
     - Обновляет poster, description, imdb_id, title (формат «Русское / Оригинальное (Год)»), original_title
   - **Определяет is_metadata_fixed:** 1 если ВСЕ: постер, описание, kp_id, imdb_id, kp_rating > 0, нет мусора в названии
   - **Обновляет items:** `SET title, poster_url, description, imdb_id, kp_id, kp_rating, imdb_rating, is_metadata_fixed, is_reprocessed = 1, original_title`
   - **При IntegrityError (дубль):** Переносит релизы в существующий айтем, ставит `is_reprocessed=1`, удаляет дубль

**Источники:** Rutor (для ID), TMDB API (постер, описание, рейтинги, чистое название)

**Запись в БД:**
- `items`: UPDATE (title, poster_url, description, imdb_id, kp_id, kp_rating, imdb_rating, is_metadata_fixed, **is_reprocessed = 1**, original_title)
- `releases`: UPDATE item_id (при слиянии дублей)
- `items`: DELETE (дубли)

**Метки окончания:**
- `is_reprocessed = 1` — обработан
- `is_metadata_fixed = 1` — все поля заполнены

---

### 5. 2.1 POISKKINO (Прямой ID)

- **Расположение:** Сайдбар, кнопка «2.1 POISKKINO (DIRECT ID)»
- **API:** `POST /api/start_fix_poisk`
- **Status Key:** `poiskkino`
- **Лог-файл:** `fix_poiskkino_log.txt`
- **Скрипт:** `fix_posters.py` с `api_type="poiskkino"`

**Логика:**

1. Сбрасывает TMDB-рейтинги-дубли (где kp_rating == imdb_rating > 0) → ставит 0, `is_metadata_fixed = 0`
2. Выбирает айтемы:
   - Видео-категории
   - Нет постера ИЛИ kp_rating=0 ИЛИ imdb_rating=0
   - `is_ignored = 0`, `is_metadata_fixed = 0`
   - **`checked_poiskkino = 0`**
   - LIMIT 300 (батч)
3. Для каждого:
   - Приоритет: **Прямой поиск по KP ID** — если `kp_id` есть, вызывает `PoiskKinoClient.get_by_id(kp_id)` → `/v1.4/movie/{kp_id}`
   - Фолбэк: **Поиск по названию** — `client.search_movie(title, year)` → `/v1.4/movie/search?query=...`
   - Обновляет только **пустые** поля: kp_rating, imdb_rating, poster_url, description, imdb_id
   - Проверяет полноту → `is_metadata_fixed = 1`
   - **Всегда ставит `checked_poiskkino = 1`** (даже если ничего не найдено)

**Источник:** PoiskKino API (`api.poiskkino.dev/v1.4`)

**Запись в БД:**
- `items`: UPDATE (kp_rating, imdb_rating, poster_url, description, imdb_id, is_metadata_fixed, **checked_poiskkino**)

**Метка окончания:** `checked_poiskkino = 1`

---

### 6. 2.2 LEGACY API (Тех)

- **Расположение:** Сайдбар, кнопка «2.2 LEGACY API (TECH)»
- **API:** `POST /api/start_fix`
- **Status Key:** `fix`
- **Лог-файл:** `fix_tech_log.txt`
- **Скрипт:** `fix_posters.py` с `api_type="tech"`

**Логика:** Идентична PoiskKino, но использует `KinopoiskClient`:
1. Тот же сброс TMDB-дублей
2. Выбирает айтемы с **`checked_tech = 0`**
3. Прямой поиск: `/v2.2/films/{kp_id}`
4. Фолбэк: `/v2.2/films?keyword=...`
5. Обновляет пустые поля
6. **Ставит `checked_tech = 1`**

**Источник:** Kinopoisk Unofficial API (`kinopoiskapiunofficial.tech`)

**Запись в БД:**
- `items`: UPDATE (kp_rating, imdb_rating, poster_url, description, imdb_id, is_metadata_fixed, **checked_tech**)

**Метка окончания:** `checked_tech = 1`

---

### 7. 2.3 REZKA.AG (ID и ссылки)

- **Расположение:** Сайдбар, кнопка «2.3 REZKA.AG (ID & LINKS)»
- **API:** `POST /api/start_sync_rezka`
- **Status Key:** `rezka`
- **Лог-файл:** `sync_rezka_log.txt`
- **Скрипт:** `rezka_sync.py`

**Логика:**

1. Выбирает айтемы:
   - Видео-категории
   - Нет kp_id ИЛИ imdb_id ИЛИ kp_rating=0 ИЛИ imdb_rating=0 ИЛИ rezka_url
   - `is_ignored = 0`
   - **`checked_rezka = 0`**
2. Для каждого вызывает `search_rezka_for_item()`:

   **Система скоринга:**
   - Разделяет название по "/" на части, очищает
   - Ищет через `HdRezkaSearch.fast_search(title + year)` и `fast_search(title)` (без года если с годом уже точное совпадение)
   - Для каждого результата считает **score**:
     - **Точное совпадение нормализованного названия**: +130
     - **Подстрока** (len>4, одно содержит другое): +50
     - **Год совпадает**: +60; разница 1: +50; ≤3: -40; >3: -150
   - Результаты со score < 70 без точного совпадения пропускаются
   - Для кандидатов загружает страницу HdRezka (`HdRezkaApi(url)`):
     - Извлекает kp_id из ссылок kinopoisk.ru
     - Извлекает imdb_id из ссылок imdb.com (включая base64-кодированные `/help/` ссылки)
     - Извлекает год со страницы
     - **Верификация по ID:**
       - kp_id из БД совпадает с kp_id на странице → подтверждено
       - Конфликт ID → отклонено
     - **Правила валидации:**
       - id_match = True → валидно
       - Есть ID в БД, но нет на странице + score ≥ 90 → доверяем названию
       - Нет ID нигде + score ≥ 90 → валидно
       - Нет ID в БД, но есть на странице + score ≥ 110 → валидно
   - Извлекает: kp_rating, imdb_rating (из блоков рейтинга), poster_url (из og:image)

3. Обновляет айтемы:
   - `rezka_url` (всегда если найдено)
   - kp_rating, imdb_rating — только если 0/NULL
   - kp_id, imdb_id — через COALESCE (только если NULL)
   - poster_url — только если NULL/''
   - **Ставит `checked_rezka = 1`** даже если ничего не найдено

**Источник:** HdRezka.ag (через библиотеки HdRezkaApi + HdRezkaSearch)

**Запись в БД:**
- `items`: UPDATE (rezka_url, kp_rating, imdb_rating, kp_id, imdb_id, poster_url, **checked_rezka**)

**Метка окончания:** `checked_rezka = 1`

---

### 8. 3.0 RATING SYNC (CSV)

- **Расположение:** Сайдбар, кнопка «3. RATING SYNC (CSV)»
- **API:** `POST /api/sync_user`
- **Status Key:** `user`
- **Лог-файл:** `user_sync_log.txt`
- **Скрипт:** `user_sync.py`

**Логика:**

1. Читает 2 CSV файла (пути из config.json → `user_sync`):
   - `IMDBOCENKI.csv` — колонки: Const, Title, Original Title, Year, Your Rating
   - `kinopoiskocenki.csv` — колонки варьируются (пробует несколько кодировок: utf-8-sig, utf-16, cp1251)
2. Мержит по ключу `(normalize_title(title), year)`:
   - IMDb загружается первым
   - KP ищет совпадение по названию+год; если найдено — добавляет kp_id
   - Если нет — создаёт новую запись
3. Проверяет/дополняет схему БД (ALTER TABLE ADD COLUMN если нужно)
4. Создаёт таблицу `item_search_names` если не существует
5. Для каждой записи: UPSERT в `user_ratings` (по imdb_id, kp_id или title_norm+year)

**Источники:** Локальные CSV файлы
**Запись в БД:** `user_ratings`: INSERT/UPDATE

**Метка окончания:** Нет флага на items — процесс пишет только в `user_ratings`

---

### 9. CLEANUP DUPLICATES

- **Расположение:** Сайдбар внизу, кнопка «CLEANUP DUPLICATES»
- **API:** `POST /api/start_cleanup`
- **Status Key:** `cleanup`
- **Лог-файл:** `cleanup_log.txt`
- **Скрипт:** `cleanup_duplicates.py`

**Логика:**

1. Загружает ВСЕ айтемы из БД
2. Группирует по 4 критериям:
   - По `(clean_t(title), category_id)` — нормализованное название + категория
   - По `kp_id`
   - По `imdb_id`
   - По `rezka_url`
3. **Слияние:**
   - Сортирует по «качеству» (постер=+10, описание=+5, kp_rating>0=+5, imdb_rating>0=+5, год>0=+3, kp_id=+2). Лучший = мастер.
   - Для совпадений по названию: дополнительно `SequenceMatcher` ≥ 0.6 + год (одинаковый или разница ≤1)
   - Для совпадений по ID (KP/IMDb/Rezka): без проверки похожести (100% совпадение по ID)
   - Для каждого дубликата: переносит releases, collection_items, search_names на мастер, удаляет дубль
4. **Приоритет слияния:** KP ID → IMDb ID → Rezka URL → Название+Год

**Запись в БД:**
- `releases`: UPDATE item_id
- `collection_items`: перенос
- `item_search_names`: перенос
- `items`: DELETE дубли

**Метка окончания:** Дубли просто удаляются

---

### 10. Кнопка IGNORE (на карточке)

- **Расположение:** Верхний правый угол постера, «✕» (при наведении)
- **API:** `POST /api/ignore/{item_id}`
- **Логика:** Переключает `is_ignored` между 0 и 1. Если 1 — ставит `ignored_at = datetime.now()`. Если 0 — `ignored_at = NULL`
- **Фронтенд:** Убирает карточку, показывает toast с «Undo» (вызывает ignoreItem снова)

---

### 11. Кнопка RESET (на карточке)

- **Расположение:** Верхний левый угол постера, иконка корзины (при наведении). Открывает оверлей с чекбоксами.
- **API:** `POST /api/reset_item/{item_id}` с `{fields: [...]}`
- **Доступные поля сброса:**

| Поле | SQL-действие |
|---|---|
| poster | `poster_url = NULL` |
| description | `description = NULL` |
| kp_id | `kp_id = NULL` |
| imdb_id | `imdb_id = NULL` |
| rezka_url | `rezka_url = NULL` |
| ratings | `kp_rating = 0, imdb_rating = 0` |
| is_reprocessed | `is_reprocessed = 0` |
| is_metadata_fixed | `is_metadata_fixed = 0` |
| checked_poiskkino | `checked_poiskkino = 0` |
| checked_tech | `checked_tech = 0` |
| checked_rezka | `checked_rezka = 0` |

**Каскадные сбросы:**
- kp_id, imdb_id, poster, ratings → также сбрасывают is_reprocessed, is_metadata_fixed, checked_rezka
- kp_id, ratings → также checked_poiskkino, checked_tech
- rezka_url → также checked_rezka

**Выбранные по умолчанию:** poster, description, kp_id, imdb_id, rezka_url, ratings

---

### 12. Кнопка EDIT IDs (на карточке)

- **Расположение:** Постер, иконка ID-карты (при наведении). Открывает модал.
- **API:** `POST /api/set_ids/{item_id}` с `{kp_id: "...", imdb_id: "..."}`
- **Логика:**
  - kp_id → ставит `kp_id = ?`, **каскад**: `checked_poiskkino = 0`, `checked_tech = 0`, `checked_rezka = 0`
  - imdb_id → ставит `imdb_id = ?`, **каскад**: `checked_rezka = 0`
  - Всегда: `is_metadata_fixed = 0`, `is_reprocessed = 0`
  - Отправляет только изменившиеся поля

---

### 13. Кнопка REFRESH (на карточке)

- **Расположение:** Постер, иконка обновления (при наведении)
- **API:** `POST /api/reprocess_item/{item_id}`
- **Status Key:** `single_update`
- **Лог-файл:** `single_update_log.txt`
- **Вызывает:** `reprocess_database.py --force --id {item_id}` — обрабатывает только одну карточку, форсированно (даже если is_reprocessed=1)

> Примечание: Также существует `POST /api/update_item/{item_id}` → `single_item_update.py`, но на фронтенде **не привязан к кнопке**. Этот скрипт: Rutor → TMDB → PoiskKino → Rezka.

---

### 14. Поиск

- **Расположение:** Шапка, текстовый ввод (десктоп); в сайдбаре (мобильный)
- **Логика:** По Enter → `fetchFeed()` с параметром `search`
- **SQL:** `title LIKE %search% OR title_norm LIKE %search% OR EXISTS (SELECT 1 FROM item_search_names WHERE name_norm LIKE %search%)`
- Нормализация `py_lower`: NFC, lowercase, замена латинской 'x' на кириллическую

---

### 15. Фильтр категории

- **Расположение:** Сайдбар, выпадающий список
- **Логика:** `v-model="selectedCategory" @change="applyFilter"` → маппинг виртуальных категорий

---

### 16. Управление коллекциями

| Действие | API | Логика |
|---|---|---|
| Создать | `POST /api/collections {name}` | INSERT INTO collections |
| Удалить | `DELETE /api/collections/{id}` | DELETE + каскад collection_items |
| Переставить | `POST /api/collections/save_order {order: [id1,id2,...]}` | UPDATE sort_order |
| Добавить/убрать айтем | `POST /api/collections/{coll_id}/toggle {item_id}` | INSERT или DELETE из collection_items |
| Выбрать для просмотра | Клик по имени в сайдбаре | `fetchFeed()` с collection_id |

---

### 17. Скрыть просмотренные (Hide Watched)

- **Расположение:** Сайдбар, тогл
- **Логика:** `hide_rated=true` → вызывает `get_watched_item_ids()` (3 стратегии: imdb_id, kp_id, name_norm) → `items.id NOT IN (...)`

---

### 18. Скрыть в папках (Hide Collected)

- **Расположение:** Сайдбар, тогл
- **Логика:** `hide_collected=true` → `items.id NOT IN (SELECT item_id FROM collection_items)`

---

### 19. Только новые (Only New)

- **Расположение:** Сайдбар, тогл
- **Логика:** `min_date = lastVisit.split(' ')[0]` → фильтр по дате релиза
- `lastVisit` хранится в `app_state`, обновляется при уходе со страницы (`POST /api/mark_visited`)
- Бейдж «NEW»: `new Date(item.latest_release) > new Date(this.lastVisit)`

---

### 20. Слайдеры года / KP / IMDb / Даты

| Фильтр | SQL |
|---|---|
| min/max year | `year >= ?` / `year <= ?` |
| min/max kp | `kp_rating >= ?` / `kp_rating <= ?` (при min=0 добавляется `kp_rating > 0`) |
| min/max imdb | `imdb_rating >= ?` / `imdb_rating <= ?` (аналогично) |
| date from/to | `items.id IN (SELECT item_id FROM releases WHERE date_added >= ? / <= ?)` |

---

### 21. Дашборд (Статистика)

- **Расположение:** Сайдбар, кнопка «STATISTICS»
- **API:** `GET /api/stats` + `GET /api/job_history?limit=10`
- **Показывает:** 4 карточки статистики (всего видео, нет постеров, нет рейтингов, нет Rezka) + таблица истории задач

---

### 22. Экспорт

- **Расположение:** Сайдбар, кнопки «JSON» и «CSV»
- **API:** `GET /api/export?fmt=json|csv&category_id=...`
- **Логика:** Тот же фильтр категорий что и feed, но ВСЕ айтемы (без пагинации)

---

### 23. Просмотр логов

- **Расположение:** Шапка, тогл через кнопку «LOGS» в сайдбаре
- **9 вкладок:**

| Вкладка | Ключ | Файл | Status Key |
|---|---|---|---|
| Обновление | reprocess | reprocess_log.txt | reprocess |
| Видео | video | sync_video_log.txt | sync_video |
| Остальное | other | sync_other_log.txt | sync_other |
| Поиск | fix | fix_tech_log.txt | fix |
| PoiskKino | fix_poiskkino | fix_poiskkino_log.txt | poiskkino |
| Rezka | rezka | sync_rezka_log.txt | rezka |
| CSV | user | user_sync_log.txt | user |
| Чистка | cleanup | cleanup_log.txt | cleanup |
| Карточка | single_update | single_update_log.txt | single_update |

- **Автопереключение:** Если процесс запущен и вкладка не выбрана вручную — переключает на вкладку процесса
- **Обновление:** Каждые 2 сек при открытом логе и работающем процессе
- **Кнопки:** Обновить, Очистить, Скачать

---

### 24. Кнопка STOP

- **Расположение:** На каждой работающей кнопке процесса (маленький «✕»), в лог-панели (красная «STOP»)
- **API:** `POST /api/stop/{key}`
- **Логика:**
  - Для full_pipeline: `pipeline_stop_requested = True`, пишет stop-флаг для активного шага, ждёт graceful_timeout (5с), затем terminate
  - Для отдельных процессов: Пишет файл `stop_{key}.flag`, ждёт timeout, затем terminate
  - Скрипты проверяют `should_stop()` периодически
  - Перед выходом сохраняют чекпоинт для resume

---

### 25. Ссылки на карточке (footer)

| Ссылка | Условие | URL |
|---|---|---|
| **RUTOR** | `releases.length > 0` | Первый релиз: `releases[0].link` |
| **KP** | `kp_id` существует | `https://www.kinopoisk.ru/film/{kp_id}/` |
| **REZKA** | `rezka_url` существует | Ссылка на Rezka |
| **KINORIUM** | `rezka_url` НЕ существует | `https://ru.kinorium.com/search/?q={title}` |
| **IMDB** | `imdb_id` существует | `https://www.imdb.com/title/{imdb_id}/` |

- **Индикатор Rezka:** Оранжевая точка на ссылке REZKA/KINORIUM если `checked_rezka === 0` (ещё не проверен)

---

### 26. Логин модал

- **Расположение:** Полноэкранный оверлей при первом заходе (если auth включён)
- **API:** `POST /api/login {username, password}`
- **Логика:** Сравнивает с AUTH_USER/AUTH_PASS из .env, возвращает токен (hex). Токен в sessionStorage, отправляется как `Authorization: Bearer {token}`.
- Без AUTH_USER/AUTH_PASS в .env — авторизация отключена

---

## СИСТЕМА СТАТУСОВ ПРОЦЕССОВ

| Status Key | Процесс |
|---|---|
| sync_video | Парсинг видео |
| sync_other | Парсинг игр/софта |
| reprocess | Обновление БД |
| poiskkino | PoiskKino |
| fix | Legacy API |
| rezka | Rezka |
| user | CSV синхронизация |
| cleanup | Чистка дублей |
| full_pipeline | Полный цикл |
| single_update | Обновление карточки |

**Значения:** idle → queued → running → completed/stopped/error
**Опрос:** Фронтенд каждые 2 сек запрашивает `GET /api/process_status`
**Остановка:** Файл `stop_{key}.flag`, чекпоинт в `checkpoint_{key}.json`
**Прогресс:** Файл `progress_{key}.json` с `{current: N, total: M}`
**История:** Таблица `job_history` — тип, время, длительность, обработано, статус

---

## СВОДНАЯ ТАБЛИЦА: ИСТОЧНИКИ → ДАННЫЕ → МЕТКИ

| Процесс | Источник | Что ищет | Что пишет в items | Метка окончания |
|---|---|---|---|---|
| sync_video | Rutor + TMDB | Релизы, KP/IMDb ID, постер, описание | title, poster_url, description, imdb_id, kp_id | Нет checked_* |
| sync_other | Rutor | Релизы, KP/IMDb ID (если есть) | title, kp_id, imdb_id | Нет checked_* |
| reprocess | Rutor + TMDB | KP/IMDb ID (deep), постер, описание, чистое название | title, poster_url, description, imdb_id, kp_id, kp/imdb_rating | is_reprocessed = 1, is_metadata_fixed = 1 |
| poiskkino | PoiskKino API | kp/imdb_rating, постер, описание, imdb_id | kp/imdb_rating, poster_url, description, imdb_id | checked_poiskkino = 1 |
| fix/tech | Kinopoisk API | kp/imdb_rating, постер, описание, imdb_id | kp/imdb_rating, poster_url, description, imdb_id | checked_tech = 1 |
| rezka | HdRezka.ag | rezka_url, kp/imdb_id, kp/imdb_rating, постер | rezka_url, kp_id, imdb_id, kp/imdb_rating, poster_url | checked_rezka = 1 |
| csv_sync | Локальные CSV | Пользовательские оценки | → user_ratings (не items) | Нет |
| cleanup | Локальная БД | Дубликаты | Перенос releases, удаление дублей | Дубли удалены |

---

## КАСКАДНОСТЬ СБРОСА ФЛАГОВ

При ручных действиях сбрасываются проверки, чтобы данные могли быть повторно обогащены:

| Действие | checked_poiskkino | checked_tech | checked_rezka | is_reprocessed | is_metadata_fixed |
|---|---|---|---|---|---|
| Установка kp_id | 0 | 0 | 0 | 0 | 0 |
| Установка imdb_id | — | — | 0 | 0 | 0 |
| Сброс kp_id | 0 | 0 | 0 | 0 | 0 |
| Сброс imdb_id | — | — | 0 | 0 | 0 |
| Сброс poster | — | — | 0 | 0 | 0 |
| Сброс ratings | 0 | 0 | 0 | 0 | 0 |
| Сброс rezka_url | — | — | 0 | — | — |
