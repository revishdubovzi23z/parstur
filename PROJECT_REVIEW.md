# Antigravity Tracker v2.1 — Полное ревью проекта

Этот документ — результат глубокого анализа всей кодовой базы. Каждая рекомендация объяснена: что не так, почему это важно, как предлагается исправить, и что будет если не исправить.

---

## РАЗДЕЛ 1: АРХИТЕКТУРА

### 1.1 Отсутствие единого слоя работы с БД

**Как сейчас:** Каждый .py файл открывает SQLite самостоятельно. В проекте как минимум 5 разных вариантов подключения:

- `main.py` → своя `get_db()` с WAL и `py_lower`
- `rezka_sync.py` → своя `get_db()` с WAL, но без `py_lower`
- `sync_job.py` → локальный класс `TrackerAppCore` (затеняет импорт из `app_core.py`)
- `reprocess_database.py` → прямое `sqlite3.connect()` без WAL
- `fix_posters.py` → прямое `sqlite3.connect()` без WAL
- `user_sync.py` → прямое `sqlite3.connect()` без WAL

**Почему это проблема:**

1. **WAL-режим** (Write-Ahead Logging) позволяет читать БД пока идёт запись. Без него — при запуске reprocess (который пишет тысячи строк) фронтенд зависнет на каждом `SELECT`, потому что SQLite блокирует всю БД на запись. Сейчас это частично работает, потому что reprocess_database.py не использует WAL — но это значит что при его работе весь фронтенд тормозит.

2. **`py_lower`** — кастомная SQL-функция для регистронезависимого поиска с кириллицей. Если один файл её регистрирует, а другой нет — поиск работает в одном контексте и ломается в другом. Это настоящий баг: `reprocess_database.py` ищет дубли по названию без `py_lower`, что может пропустить дубли с разным регистром.

3. **DRY-нарушение:** Один и тот же SQL-запрос написан 3-4 раза в разных файлах с мелкими отличиями. Изменение схемы (добавление колонки) требует правок в 5-7 местах, и легко забыть одно.

4. **Нет транзакционной согласованности:** `sync_job.py` делает `conn.commit()` после каждого айтема. Если процесс упал посередине — половина айтемов записана, половина нет. Нет отката, нет консистентности.

**Что предлагаю:**

Создать `db.py` — единую точку входа:

```python
# db.py
import sqlite3
from contextlib import contextmanager

DB_PATH = "app_data.db"

def py_lower(x):
    if x is None: return None
    return unicodedata.normalize("NFC", str(x)).lower().replace("x", "х")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.create_function("py_lower", 1, py_lower)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

class ItemsTable:
    def __init__(self, conn):
        self.conn = conn
    
    def get_by_id(self, item_id):
        return self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    
    def update_fields(self, item_id, **fields):
        if not fields: return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [item_id]
        self.conn.execute(f"UPDATE items SET {sets} WHERE id = ?", vals)
    
    def mark_checked(self, item_id, source):
        self.conn.execute(f"UPDATE items SET checked_{source} = 1 WHERE id = ?", (item_id,))
    
    def needs_enrichment(self, sources=None):
        """Вернуть айтемы где не все данные заполнены"""
        ...
```

Все скрипты: `from db import get_db, ItemsTable`. Изменение схемы — правка в одном месте. Переход на PostgreSQL (если проект вырастет) — замена одного файла.

**Что будет если не делать:** С каждым новым файлом копипаст растёт. Когда-нибудь забудут WAL на новом скрипте, и фронтенд будет зависать при каждом запуске процесса. Добавление колонки превратится в квест по 7 файлам.

---

### 1.2 Класс-заглушка TrackerAppCore

**Как сейчас:** В `app_core.py` есть класс `TrackerAppCore` с 15+ методами. Из них:
- 2 метода работают (нормализация названий)
- 13 методов — заглушки (`pass`, `return []`, `return 0`)

Реальная логика размазана по `main.py` (800+ строк SQL), `sync_job.py` (свой локальный `TrackerAppCore` с частичной реализацией), `reprocess_database.py`, и т.д.

`sync_job.py` создаёт свой `TrackerAppCore` внутри функции, который затеняет (shadowing) класс из `app_core.py`. Это означает что если кто-то импортирует `TrackerAppCore` из `app_core` — он получит неработающие заглушки.

**Почему это проблема:**

Это не просто «некрасиво». Это архитектурная ловушка. Новый разработчик (или ты сам через полгода) видишь `TrackerAppCore` в `app_core.py`, думаешь «ага, вот основной класс», импортируешь — и получаешь пустые методы. Потом тратишь час на отладку, пока не обнаруживаешь что реальный класс в `sync_job.py`.

**Что предлагаю:**

Два пути — выбрать один:

**Путь А (простой):** Удалить stub-класс из `app_core.py`. Оставить только утилитарные функции (`normalize_title`, `clean_title_for_search`, константы категорий). Каждый скрипт — самостоятельный, без иллюзии ООП.

**Путь Б (правильный):** Перенести реальную логику в `TrackerAppCore`. Методы `get_last_sync_date()`, `insert_item()`, `update_item()` — из `sync_job.py` в класс. Тогда `sync_job.py` станет: `app = TrackerAppCore(db)` + 50 строк оркестрации.

Путь Б требует рефакторинга, но даёт тестируемость и читаемость. Путь А — быстрый, просто убирает путаницу.

---

### 1.3 Нет системы миграций БД

**Как сейчас:** Схема БД меняется через `ALTER TABLE ADD COLUMN` в `_init_db()` (app_core.py) и `_check_schema()` (user_sync.py). Каждый файл проверяет «есть ли колонка X?» и добавляет если нет.

**Почему это проблема:**

1. Нет версионирования. Невозможно узнать «какая версия схемы у пользователя?»
2. Нет отката. Если добавили колонку и она оказалась неправильной — нельзя отменить.
3. Конкуренция. Два файла могут одновременно пытаться ALTER TABLE.
4. Новые инсталляции неясны. `_init_db()` создаёт базовую схему, но все последующие ALTER'ы размазаны по файлам. Новый пользователь запускает sync_job → БД создаётся без `checked_rezka` → потом rezka_sync пытается UPDATE несуществующую колонку → ошибка.

**Что предлагаю:**

Простая система миграций:

```python
# migrations.py
MIGRATIONS = {
    1: """CREATE TABLE items (...)""",  # базовая схема
    2: """ALTER TABLE items ADD COLUMN checked_rezka INTEGER DEFAULT 0""",
    3: """ALTER TABLE items ADD COLUMN rezka_url TEXT""",
    4: """CREATE TABLE IF NOT EXISTS item_search_names (...)""",
    5: """ALTER TABLE items ADD COLUMN original_title TEXT""",
    # ...
}

def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)""")
    current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    for version in sorted(MIGRATIONS):
        if version > current:
            conn.executescript(MIGRATIONS[version])
            conn.execute("INSERT INTO schema_version VALUES (?)", (version,))
            conn.commit()
```

Вызывается один раз при старте сервера. Все ALTER'ы в одном месте, пронумерованы, с историей.

---

## РАЗДЕЛ 2: НАДЁЖНОСТЬ

### 2.1 Процессы в subprocess — хрупкость

**Как сейчас:** `main.py` запускает скрипты через `subprocess.Popen([sys.executable, "script.py", ...])`. Это отдельные OS-процессы. Общение с ними — только через:
- Файлы `stop_{key}.flag` (сигнал остановки)
- Файлы `progress_{key}.json` (прогресс)
- Файлы `*_log.txt` (логи)
- `process.returncode` (результат)

**Почему это проблема:**

1. **Нет потока данных.** Процесс не может вернуть «найдено 3 новых айтема» — только exit code 0/1. Все данные пишутся в БД напрямую из subprocess, а main.py об этом не знает.

2. **Хрупкая остановка.** Если процесс завис (не проверяет `should_stop()`), его можно только terminate/kill. Данные могут быть в середине транзакции. SQLite с WAL переживёт это, но без WAL — возможна corruption.

3. **Невозможна композиция.** Pipeline — это последовательность subprocess. Нельзя сказать «если step 2 нашёл 0 новых айтемов — пропустить step 3».

4. **Ресурсы.** Каждый subprocess — новый Python-интерпретатор (~30MB RAM). 6 шагов pipeline — 180MB только на процессы.

**Что предлагаю:**

Короткосрочно: оставить как есть, это работает. Но добавить:
- `conn.execute("BEGIN IMMEDIATE")` перед критичными операциями (защита от corruption при kill)
- Писать промежуточные результаты в `app_state` таблицу («сколько новых айтемов»)

Долгосрочно: рассмотреть переход на `multiprocessing` вместо `subprocess`. Преимущества: shared memory, Queue для коммуникации, возможность вернуть результат. Но это крупный рефакторинг.

---

### 2.2 Отсутствие валидации входных данных API

**Как сейчас:** FastAPI-эндпоинты принимают параметры без строгой валидации. Например:
- `min_year` может быть отрицательным или 9999
- `search` может быть 10MB строкой
- `limit` может быть 1000000
- Нет ограничения на количество одновременных запросов

**Почему это проблема:**

Даже с авторизацией (которую мы добавили), легитимный пользователь может случайно или намеренно:
- Попросить `limit=1000000` → SQLite загрузит миллион строк в RAM
- Отправить `search` с регуляркой → LIKE `%...%` с паттерном типа `%%%%%%%%%%%%%%%%%` → 100% CPU
- Вызвать 1000 API-запросов одновременно → DDoS собственного сервера

**Что предлагаю:**

```python
from fastapi import Query

@app.get("/api/feed")
def get_feed(
    limit: int = Query(default=20, ge=1, le=100),
    page: int = Query(default=1, ge=1, le=10000),
    search: str = Query(default=None, max_length=200),
    min_year: int = Query(default=None, ge=1888, le=2030),
    max_year: int = Query(default=None, ge=1888, le=2030),
    ...
):
```

FastAPI автоматически валидирует и вернёт 422 с описанием ошибки. ~20 строк на все эндпоинты.

---

## РАЗДЕЛ 3: ПРОИЗВОДИТЕЛЬНОСТЬ

### 3.1 Polling вместо push — 30 лишних запросов в минуту

**Как сейчас:** Фронтенд каждые 2 секунды запрашивает `/api/process_status`. Этот эндпоинт:
1. Читает `process_status` dict (быстро)
2. Для каждого ключа читает `progress_{key}.json` с диска (10 файловых чтений!)
3. Обновляет статусы завершённых процессов (SQL к job_history)

В idle-режиме (ничего не запущено) — это 30 запросов/мин × 10 файловых чтений = 300 файловых операций в минуту. Ничего не делая.

**Почему это проблема:**

На Windows файловые операции медленнее чем на Linux (нет такого агрессивного кеширования). На слабом железе (где обычно и работает трекер) — это заметный I/O. Плюс — каждый `/api/process_status` инициирует HTTP-соединение с заголовками, парсинг JSON, и т.д.

**Что предлагаю:**

**Быстрый вариант (30 мин):** Кешировать progress в памяти. Скрипты пишут в `progress_{key}.json` (оставляем для resume). Но API-эндпоинт читает из dict в памяти, обновляемый раз в 5 секунд фоновым таском. Файловых чтений = 0 при polling.

**Правильный вариант (3ч):** WebSocket. Один коннект, сервер пушит:
- `{"type": "status", "key": "rezka", "value": "running"}`
- `{"type": "progress", "key": "rezka", "current": 45, "total": 100}`
- `{"type": "log", "key": "rezka", "line": "Найдено:..."}`

Фронтенд получает логи в реальном времени. Прогресс обновляется мгновенно. Нулевой overhead в idle.

---

### 3.2 LIKE-поиск — медленный и неточный

**Как сейчас:** Поиск по названию:
```sql
WHERE title LIKE '%dune%' 
   OR title_norm LIKE '%dune%'
   OR EXISTS (SELECT 1 FROM item_search_names WHERE name_norm LIKE '%dune%')
```

`LIKE '%...%'` — полный scan таблицы. Не использует индекс. На 100K+ записях — секунды вместо миллисекунд.

Но даже хуже: поиск не ранжирует. «Dune Part Two» и «Dune Warrior» — одинаковый вес. Нет поддержания опечаток. «Дюна» латиницей не найдёт кириллицей.

**Что предлагаю:**

SQLite FTS5 — встроенный полнотекстовый поиск:

```sql
CREATE VIRTUAL TABLE items_fts USING fts5(
    title, original_title, title_norm,
    content=items, content_rowid=id,
    tokenize="unicode61 categories UnicodeL* L*"
);

-- Поиск с ранжированием:
SELECT *, rank FROM items_fts 
WHERE items_fts MATCH 'dune part' 
ORDER BY rank LIMIT 20;
```

**Преимущества:**
- `MATCH` использует FTS-индекс → O(log n) вместо O(n)
- Автоматическое ранжирование (bm25) → лучшая карточка первая
- Префиксный поиск: `dun*` найдёт «Dune»
- `unicode61` токенизатор понимает кириллицу
- Поддержка «ИЛИ»: `dune OR матрица`

**Обновление:** Триггеры на items:
```sql
CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, original_title, title_norm) 
    VALUES (new.id, new.title, new.original_title, new.title_norm);
END;
-- Аналогично для UPDATE и DELETE
```

**Сложность:** ~50 строк SQL + один CREATE TABLE при миграции. FTS5 встроен в Python SQLite, ничего не нужно ставить.

---

### 3.3 Постеры — самый большой источник трафика

**Как сейчас:** Каждый айтем в feed грузит постер напрямую с `image.tmdb.org/t/p/original/...`. Это:
- `original` размер — ~50-200KB на постер
- 20 постеров на страницу = 1-4MB
- Внешний HTTP-запрос к CDN за каждым
- Если TMDB недоступен — пустые карточки
- PWA офлайн — все постеры пропадают

**Что предлагаю (два уровня):**

**Уровень 1 — Миниатюры (20 мин):** Заменить `original` на `w342` в URL:
```
image.tmdb.org/t/p/original/abc.jpg  →  image.tmdb.org/t/p/w342/abc.jpg
```
`w342` = 342px шириной, ~10-15KB. Качество для карточки в сетке — более чем достаточно. При клике на карточку — загружать `w500`. Экономия 70-80% трафика.

Простая замена: `poster_url.replace("/original/", "/w342/")` в шаблоне.

**Уровень 2 — Локальный прокси (1ч):** Endpoint `/api/poster/{item_id}`:
```python
@app.get("/api/poster/{item_id}")
async def get_poster(item_id: int):
    cache_path = f"posters/{item_id}.jpg"
    if os.path.exists(cache_path):
        return FileResponse(cache_path)
    
    conn = get_db()
    row = conn.execute("SELECT poster_url FROM items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    
    if not row or not row["poster_url"]:
        return FileResponse("static/no_poster.jpg")
    
    # Скачиваем и кешируем
    import requests
    resp = requests.get(row["poster_url"].replace("/original/", "/w342/"), timeout=10)
    os.makedirs("posters", exist_ok=True)
    with open(cache_path, "wb") as f:
        f.write(resp.content)
    return FileResponse(cache_path)
```

**Выгода уровня 2:**
- Постер скачивается один раз, потом отдаётся с диска (~1ms)
- Нет зависимости от доступности TMDB
- Офлайн-режим PWA реально работает (кешируем `/api/poster/*` в SW)
- Можно добавить `webp` конвертацию для ещё меньшего размера

---

### 3.4 Счётчики категорий — 15 SQL-запросов на каждый polling

**Как сейчас:** `/api/categories` делает:
- 7-8 COUNT(*) запросов (все видео, нет постера, нет рейтинга, и т.д.)
- Если `hide_rated` — ещё `get_watched_item_ids()` с 3 подзапросами
- Это вызывается при каждом polling + при каждом применении фильтра

При polling каждые 2 сек: 8 COUNT(*) × 30 раз/мин = 240 SQL-запросов/мин.

**Что предлагаю:**

Кешировать counts в памяти на 30 секунд:

```python
from time import time

_category_cache = {"data": None, "ts": 0}
CATEGORY_CACHE_TTL = 30  # секунд

@app.get("/api/categories")
def get_categories(...):
    now = time()
    if _category_cache["data"] and now - _category_cache["ts"] < CATEGORY_CACHE_TTL:
        # Возвращаем кешированные данные, только подменяем hide_rated если нужно
        ...
    
    # Считаем и кешируем
    data = _compute_categories(...)
    _category_cache["data"] = data
    _category_cache["ts"] = now
    return data
```

Инвалидация: при завершении любого процесса (process_status → «completed») — сбросить кеш.

**Выгода:** 240 SQL/мин → ~8 SQL/мин (только при реальных изменениях).

---

## РАЗДЕЛ 4: UX И ФРОНТЕНД

### 4.1 Кнопка «обновить карточку» — делает только половину работы

**Как сейчас:** Иконка обновления на карточке вызывает `reprocess_database.py --force --id X`. Этот скрипт:
1. Скрейпит Rutor → находит kp_id, imdb_id
2. Запрашивает TMDB → обновляет постер, описание, название

**Что он НЕ делает:**
- Не запрашивает PoiskKino → нет рейтингов KP/IMDb
- Не запрашивает Kinopoisk API → нет рейтингов
- Не ищет на Rezka → нет ссылки и не проверяются ID

То есть пользователь нажимает «обновить» → постер и описание появляются, но рейтинги — пустые. Нужно вручную запускать PoiskKino и Rezka. Это не очевидно.

**Что предлагаю:**

Использовать `single_item_update.py` (который мы починили в #1). Этот скрипт делает полный цикл: Rutor → TMDB → PoiskKino → Rezka. Одна кнопка — все данные.

На фронтенде: заменить `/api/reprocess_item/{id}` → `/api/update_item/{id}`. Бэкенд-эндпоинт уже существует (`main.py:647`).

---

### 4.2 Дашборд — сухие цифры вместо действий

**Как сейчас:** Статистика показывает «312 без KP ID», «89 без Rezka». Пользователь видит цифру, но не знает что с этим делать.

**Что предлагаю:**

Превратить каждую цифру в действие:

```
┌─────────────────────────────────────────────┐
│ ⚠ 312 айтемов без KP ID                      │
│   → Запустить 2.1 PoiskKino    [ЗАПУСТИТЬ]  │
│                                              │
│ ⚠ 89 без Rezka                               │
│   → Запустить 2.3 Rezka       [ЗАПУСТИТЬ]   │
│                                              │
│ ⚠ 23 возможных дубликата                     │
│   → Запустить Cleanup         [ЗАПУСТИТЬ]   │
│                                              │
│ ✅ Все постеры заполнены                      │
└─────────────────────────────────────────────┘
```

Каждая кнопка сразу запускает нужный процесс. Новичку понятно что нажимать. Опытному — не нужно искать кнопку в сайдбаре.

---

### 4.3 Нет тёмной темы

**Как сейчас:** Светлая тема с `bg-slate-50`, `bg-white`. Для вечернего/ночного использования — жжёт глаза.

**Что предлагаю:**

Tailwind имеет встроенную поддержку `dark:` модификатора. Переключатель в шапке:

```html
<html :class="{ dark: darkMode }">
```

```css
/* Примеры замен */
body { background: #f8fafc; }          → body { @apply bg-slate-50 dark:bg-slate-900; }
.card { background: white; }            → .card { @apply bg-white dark:bg-slate-800; }
h1 { color: #1e293b; }                  → h1 { @apply text-slate-900 dark:text-slate-100; }
```

Tailwind CDN поддерживает `dark:` из коробки через:
```html
<script>tailwind.config = { darkMode: 'class' }</script>
```

~50 минут работы на добавление `dark:` классов к основным элементам. Хранить в `localStorage`.

---

### 4.4 Нет клавиатурных шорткатов

**Как сейчас:** Всё мышкой. Для приложения где 80% времени — скролл и поиск — это замедляет.

**Что предлагаю:**

```javascript
document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !e.target.matches('input')) {
        e.preventDefault();
        this.$refs.searchInput.focus();
    }
    if (e.key === 'Escape') {
        this.showSidebar = false;
        this.showIdEditor = false;
    }
    if (e.key === 'ArrowRight' && !e.target.matches('input')) {
        if (this.page < this.totalPages) this.page++;
    }
    if (e.key === 'ArrowLeft' && !e.target.matches('input')) {
        if (this.page > 1) this.page--;
    }
});
```

`/` → фокус на поиск. `Esc` → закрыть всё. `←/→` → навигация по страницам. ~40 строк.

---

### 4.5 Процесс-кнопки в сайдбаре — неясная иерархия

**Как сейчас:** 7 кнопок в столбик: «1.1 PARSING VIDEO», «1.2 PARSING GAMES», «2.0 DATABASE UPDATE», «2.1 POISKKINO», «2.2 LEGACY API», «2.3 REZKA.AG», «3. RATING SYNC». Плюс «CLEANUP» и «FULL CYCLE».

Непонятно: что зависит от чего? Можно ли запускать 2.1 без 2.0? Что будет если запустить 2.3 до 1.1?

**Что предлагаю:**

Группировать с визуальными шагами и пояснениями:

```
┌─ ШАГ 1: Сбор данных ──────────────────────┐
│  1.1 Видео (Rutor + TMDB)     [ЗАПУСТИТЬ]  │
│  1.2 Игры/Софт (Rutor)       [ЗАПУСТИТЬ]  │
│  ⚠ Шаг 1 должен быть выполнен первым       │
└────────────────────────────────────────────┘
┌─ ШАГ 2: Обогащение метаданных ─────────────┐
│  2.0 Обновление БД            [ЗАПУСТИТЬ]  │
│  2.1 PoiskKino (рейтинги)     [ЗАПУСТИТЬ]  │
│  2.2 Legacy API (рейтинги)    [ЗАПУСТИТЬ]  │
│  2.3 Rezka (ссылки, ID)       [ЗАПУСТИТЬ]  │
└────────────────────────────────────────────┘
┌─ ШАГ 3: Обслуживание ─────────────────────┐
│  3.0 CSV-оценки               [ЗАПУСТИТЬ]  │
│  Cleanup дублей               [ЗАПУСТИТЬ]  │
└────────────────────────────────────────────┘
```

---

## РАЗДЕЛ 5: КОД И КАЧЕСТВО

### 5.1 Голые except (ИСПРАВЛЕНО, но добавлю контекст)

Мы заменили 19 голых `except:` на `except Exception:`. Но этого недостаточно. Большинство `except Exception:` сейчас делают `pass` — то есть молча проглатывают ошибку.

**Почему это всё ещё проблема:** Если PoiskKino API начал возвращать 403 (ключ истёк) — скрипт «тихо» обработает 1000 айтемов, ни одного не найдёт, и завершится «успешно» с `checked_poiskkino=1` на всех. Пользователь не узнает что ключ протух.

**Что предлагаю:**

Минимум — считать ошибки и выводить в конце:
```python
errors = {"rate_limit": 0, "network": 0, "parse": 0}

except Exception as e:
    if "429" in str(e) or "rate" in str(e).lower():
        errors["rate_limit"] += 1
    elif "Connection" in str(e):
        errors["network"] += 1
    else:
        errors["parse"] += 1

# В конце:
print(f"Завершено. Ошибки: {errors}")
if errors["rate_limit"] > 10:
    print("⚠️ API-лимит исчерпан! Результаты неполные.")
```

---

### 5.2 Python logging вместо stdout-перехвата

**Как сейчас:** Класс `Logger` в `fix_posters.py` и `user_sync.py`:
```python
class Logger:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")
    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
    def flush(self): ...
sys.stdout = Logger("fix_tech_log.txt")
```

**Почему это опасно:**

1. Перехватывает `sys.stdout` глобально. Если в другом потоке (например, FastAPI) кто-то делает `print()` — это попадёт в лог файл процесса.
2. Файл остаётся открытым пока жив `sys.stdout`. На Windows — файл залочен.
3. `print()` внутри библиотек (requests, urllib3) — тоже перехватывается, засоряя лог мусором.
4. Если два процесса одновременно переопределили `sys.stdout` — непредсказуемо.

**Что предлагаю:**

```python
import logging

def setup_logger(name, log_file):
    logger = logging.getLogger(name)
    if logger.handlers:  # уже настроен
        return logger
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger

# Использование:
log = setup_logger("fix_tech", "fix_tech_log.txt")
log.info("Обработка: %s (%s)", title, year)
```

Не ломает stdout. Можно добавить уровни. Параллельные скрипты не конфликтуют. Лог можно ротировать. ~30 строк на модуль + замена `print()` на `log.info()` в 5 файлах.

---

### 5.3 Дублирование парсинга в rutor_parser.py

**Как сейчас:** `get_category_releases()` (строки 62-111) и `search_releases()` (строки 137-181) содержат идентичный код парсинга HTML-таблицы — ~50 строк копипасты.

**Что предлагаю:**

```python
def _parse_release_rows(self, rows, category_id):
    """Общий парсер для HTML-таблиц Rutor"""
    releases = []
    for row in rows:
        # ... единая логика парсинга
        releases.append({...})
    return releases

def get_category_releases(self, cat_id, page=0):
    # ... получение HTML
    rows = table.find_all("tr")[1:]
    return self._parse_release_rows(rows, cat_id)

def search_releases(self, query, cat_id=None):
    # ... получение HTML
    rows = table.find_all("tr")[1:]
    return self._parse_release_rows(rows, cat_id)
```

---

## РАЗДЕЛ 6: БЕЗОПАСНОСТЬ (дополнение к уже исправленному)

### 6.1 Rate limiting на API

Мы добавили авторизацию. Но нет ограничения частоты запросов. Один пользователь (даже легитимный) может:
- Дёрнуть `/api/feed` 100 раз в секунду (слайдер рейтинга дёргается)
- Запустить pipeline 10 раз одновременно (обойдя `check_any_running` через race condition)
- Скачать экспорт 100 раз (DDoS дискового I/O)

**Что предлагаю:**

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.get("/api/feed")
@limiter.limit("30/minute")
def get_feed(...): ...

@app.post("/api/start_full_pipeline")
@limiter.limit("2/minute")
def start_full_pipeline(): ...
```

`pip install slowapi`. 10 строк. Защищает от случайного и намеренного злоупотребления.

---

### 6.2 Race condition в check_any_running()

**Как сейчас:** `check_any_running()` проверяет что ни один процесс не запущен, потом ставит статус «running». Между проверкой и установкой — промежуток. Если два запроса придут одновременно — оба пройдут проверку.

**Что предлагаю:**

```python
import threading

_queue_lock = threading.Lock()

def check_any_running():
    if not _queue_lock.acquire(blocking=False):
        raise HTTPException(400, "Другой процесс уже запущен")
    # Lock будет отпущен когда процесс завершится
```

Или проще — TaskQueue уже сериализует задачи. Убрать `check_any_running()` и полагаться на очередь, но с `maxsize=1`.

---

## РАЗДЕЛ 7: АВТОМАТИЗАЦИЯ

### 7.1 Встроенный cron-планировщик

**Как сейчас:** Всё вручную. Забыл обновить — данные устарели. Уехал на неделю — пропустил 100 новых релизов.

**Что предлагаю:**

```json
// config.json
"scheduler": {
    "enabled": true,
    "timezone": "Europe/Moscow",
    "jobs": [
        {"cron": "0 6 * * *", "task": "full_pipeline", "label": "Каждое утро в 6:00"},
        {"cron": "0 18 * * *", "task": "sync_video", "label": "Вечерний парсинг"}
    ]
}
```

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

for job in config.get("scheduler", {}).get("jobs", []):
    scheduler.add_job(
        run_pipeline_task,  # или другой таск
        "cron", 
        **parse_cron(job["cron"]),
        id=job["task"],
        replace_existing=True
    )

scheduler.start()
```

**Выгода:** Данные всегда свежие. Фронтенд показывает «Следующий запуск: 6:00 завтра». `pip install apscheduler`, ~80 строк.

---

### 7.2 Автоматическая ротация логов

**Как сейчас:** Лог-файлы растут бесконечно. `sync_video_log.txt` после месяца ежедневных запусков — 50MB.

**Что предлагаю:** При старте скрипта — обрезать лог до последних 100KB:
```python
def init_log(path):
    if os.path.exists(path) and os.path.getsize(path) > 200_000:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-500:]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
```

5 строк. Логи никогда не превысят ~200KB.

---

## ИТОГОВАЯ ТАБЛИЦА ПРИОРИТЕТОВ

| # | Рекомендация | Время | Влияние на UX | Влияние на код |
|---|---|---|---|---|
| 3.3a | Миниатюры w342 | 20мин | Скорость загрузки +70% | 1 строка |
| 4.1 | Полный single-update | 30мин | 1 кнопка вместо 3 | 2 строки |
| 3.4 | Кешированный counts | 30мин | Меньше зависаний | 30 строк |
| 3.3b | Прокси постеров | 1ч | Офлайн + скорость | 60 строк |
| 3.2 | FTS5 поиск | 1ч | Качество поиска x10 | 50 строк |
| 2.2 | Валидация API | 30мин | Защита от краша | 20 строк |
| 5.2 | Python logging | 1ч | Надёжность параллельности | 100 строк |
| 6.1 | Rate limiting | 30мин | Защита от злоупотреблений | 10 строк |
| 4.2 | Умный дашборд | 1ч | Понятность для новичков | 80 строк |
| 4.3 | Тёмная тема | 40мин | Вечерний комфорт | 50 строк |
| 3.1 | WebSocket | 3ч | Реалтайм, нагрузка -30x | 150 строк |
| 7.1 | Cron-планировщик | 2ч | Автоматизация | 80 строк |
| 1.1 | Единый DB-слой | 1день | Поддерживаемость | 300 строк |
| 5.3 | Рефакторинг rutor_parser | 1ч | DRY | 50 строк |
| 1.3 | Система миграций | 2ч | Надёжность схемы | 80 строк |
| 6.2 | Race condition fix | 30мин | Корректность | 10 строк |
| 7.2 | Ротация логов | 10мин | Диск не переполняется | 5 строк |
| 5.1 | Подсчёт ошибок | 30мин | Диагностика | 20 строк |
| 4.4 | Клавиатурные шорткаты | 30мин | Скорость навигации | 40 строк |
| 4.5 | Визуальная иерархия шагов | 1ч | Понятность процесса | 60 строк |
| 1.2 | Stub-класс → реальные методы | 3ч | Чистота архитектуры | 200 строк |
| 2.1 | Переход на multiprocessing | 1день | Композиция процессов | 500 строк |
