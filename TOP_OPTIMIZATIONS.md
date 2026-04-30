# Antigravity Tracker — Топовые рекомендации по оптимизации

Мой взгляд на то, что реально сделает проект качественнее, быстрее и удобнее. Не баг-фиксы, а архитектурные и UX-улучшения.

---

## ТИР 1: GAME-CHANGERS

### 1. ✅ WebSocket вместо polling — ВЫПОЛНЕНО

**Реализация:**
- Бэкенд: `ConnectionManager` + `@app.websocket("/ws")` endpoint
- Broadcast 3 типов событий: `status` (старт/завершение), `progress` (каждые 1 сек), `log` (реалтайм stdout из subprocess)
- Фронтенд: `connectWebSocket()` с автореконнектом каждые 3 сек
- При WS подключении polling отключается; при обрыве — fallback polling каждые 5 сек
- Зависимость: `websockets>=12.0` добавлена в requirements.txt

**Было:** Фронтенд каждые 2 секунды дёргает `/api/process_status` — это 30 запросов в минуту, даже когда ничего не происходит. При открытой вкладке — бесконечный шум.

**Стало:** Один постоянный WS-коннект, сервер пушит события:
- процесс стартанул / завершился
- прогресс обновился (каждую 1 сек, не каждый айтем)
- лог-строка прилетела в реальном времени (вместо опроса файла)

**Выгода:** Нагрузка на сервер падает с 30 req/min до ~0 в idle. Лог отображается в реальном времени без задержки. Прогресс-бар обновляется мгновенно.

---

### 2. ✅ FTS5 полнотекстовый поиск — ВЫПОЛНЕНО

**Реализация:**
- `app_core.py/_init_db()`: виртуальная таблица `items_fts` с `unicode61` токенизатором + 3 триггера (INSERT/UPDATE/DELETE)
- `main.py/get_feed()`: при поиске использует `MATCH` (слова через OR), fallback на LIKE если FTS недоступна
- `main.py/startup_event()`: автозаполнение FTS-индекса при старте если пуст
- `POST /api/rebuild_fts`: endpoint для ручной переиндексации

**Было:** Поиск через `LIKE %query%` — не ранжирует, медленный на больших БД, не работает с опечатками.

**Стало:** FTS5 MATCH-поиск с `unicode61` (кириллица), слова через OR (dune part → dune OR part), префиксный поиск (dun*), автоматическое ранжирование bm25. В 10-100x быстрее LIKE.

---

### 3. Cron-планировщик (автопайплайн)

**Сейчас:** Всё запускается вручную кнопками. Забыл обновить — устаревшие данные.

**Сделать:** Встроенный планировщик в `main.py`:
```python
# config.json
"scheduler": {
    "enabled": true,
    "jobs": [
        {"cron": "0 6 * * *", "task": "full_pipeline"},
        {"cron": "0 18 * * *", "task": "sync_video"},
        {"cron": "0 0 * * 0", "task": "cleanup"}
    ]
}
```
Использовать `apscheduler` (уже есть похожие библиотеки). Запускает задачи по расписанию, как cron. Фронтенд показывает «следующий запуск через 4ч 23мин».

**Выгода:** Данные всегда свежие. Не нужно помнить про обновление. Запись в job_history автоматически.

**Сложность:** Низкая. `pip install apscheduler`, ~80 строк кода.

---

### 4. Прокси постеров

**Сейчас:** Постеры грузятся напрямую с `image.tmdb.org`. Если TMDB недоступен, кеширует CDN, или медленный — карточки без картинок. PWA офлайн — все постеры пропадают.

**Сделать:** Endpoint `/api/poster/{item_id}`:
- Отдаёт из локального кеша (папка `posters/`)
- Если нет — скачивает с TMDB, сохраняет на диск, отдаёт
- Если TMDB недоступен — отдаёт заглушку
- Service Worker кеширует `/api/poster/*` через cache-first

**Выгода:**
- Постеры работают офлайн
- Нет зависимости от доступности TMDB
- Экономия запросов к TMDB (кеш навсегда)
- Страница грузится быстрее (один домен, нет DNS/CORS задержек)

**Сложность:** Низкая. ~60 строк бэкенд + 5 строк sw.js.

---

## ТИР 2: ЗНАЧИТЕЛЬНЫЕ УЛУЧШЕНИЯ

### 5. ✅ Единый DB-слой (наконец-то) — ВЫПОЛНЕНО

**Реализация:**
- Создан `db.py` с классом `Database` — единственная точка работы с SQLite
- Все 9 скриптов рефакторингнуты: `from db import Database`, нигде нет прямого `sqlite3`
- Схема БД объединена в `init_schema()` с исправлением расхождений (releases.rutor_id/magnet, collection_items.added_at, user_ratings все колонки)
- `check_and_migrate_schema()` — миграции для существующих БД
- Паттерн `_conn(conn=None)`: без conn — автосоздание/autocommit/autoclose; с conn — caller контролирует commit (для батчей)
- Унифицировано заполнение метаданных через `fill_item_metadata()` (умные CASE/COALESCE для рейтингов, постеров, ID)
- `get_connection()` — единая фабрика (WAL, timeout=30s, py_lower)
- Синглтон `db = Database()` для импорта в main.py

**Было:** 7 файлов, каждый пишет SQL по-своему. Нет общих методов. Изменение схемы = правка в 5 местах.

**Сделать:** Один модуль `db.py`:
```python
class Database:
    def __init__(self, path="app_data.db"): ...
    def get_items(self, **filters) -> list[dict]: ...
    def get_item(self, item_id: int) -> dict | None: ...
    def update_item(self, item_id: int, **fields) -> None: ...
    def insert_item(self, data: dict) -> int: ...
    def get_releases(self, item_id: int) -> list[dict]: ...
    def mark_checked(self, item_id: int, source: str) -> None: ...
    def get_feed(self, **filters) -> dict: ...
```
Все скрипты импортируют `from db import Database` и не пишут SQL напрямую.

**Выгода:** Изменение схемы в одном месте. Переход на другую БД (PostgreSQL) — одна правка. Тестируемость. Чистый код.

**Сложность:** Высокая (рефакторинг 7 файлов). Но поэтапно — начать с `get_db()` и `mark_checked()`, потом постепенно переносить.

---

### 6. ✅ Нормальный single-item update — ВЫПОЛНЕНО

**Реализация:**
- Фронтенд: кнопка обновления карточки теперь вызывает `/api/update_item/{id}` вместо `/api/reprocess_item/{id}`
- Удалён старый `/api/reprocess_item` endpoint (вызывал `reprocess_database.py --force --id X` — только Rutor + TMDB)
- `/api/update_item/{id}` запускает `single_item_update.py` — полный цикл: Rutor → TMDB → PoiskKino → Rezka
- `single_item_update.py` рефакторингнут под `Database` класс

**Было:** Кнопка обновления карточки вызывает `reprocess_database.py --force --id X` — это целый скрипт с Rutor + TMDB. Но НЕ запускает PoiskKino, Kinopoisk и Rezka. То есть рейтинги и rezka_url не обновляются.

**Сделать:** Полный цикл для одной карточки:
```
1. Rutor → kp_id, imdb_id
2. TMDB → постер, описание, imdb_id
3. PoiskKino → kp_rating, imdb_rating
4. Rezka → rezka_url, ID, рейтинги
```
Функция `update_single_item()` уже существует в `single_item_update.py` (и мы починили импорт Rezka). Нужно просто привязать её к кнопке на фронтенде вместо `reprocess_database.py`.

**Выгода:** Одна кнопка — полное обновление карточки. Сейчас это 3 разных кнопки вручную.

**Сложность:** Низкая. Фронтенд: заменить `reprocess_item` → `update_item`. Бэкенд: уже есть `/api/update_item/{id}`.

---

### 7. ✅ Python logging вместо stdout-хака — ВЫПОЛНЕНО

**Реализация:**
- Создан `logger.py` с `setup_logger()` (чистый file logging) и `setup_tee_logger()` (stdout + файл через `TeeWriter`)
- Удалены `class Logger` из `fix_posters.py` и `user_sync.py` — больше нет перехвата `sys.stdout`
- Удалён `io.TextIOWrapper` хак из `cleanup_duplicates.py` (кодировка теперь через logging)
- Все 6 batch-скриптов используют `setup_tee_logger()` при старте: sync, reprocess, rezka, fix, single_update, cleanup
- Параллельные скрипты не конфликтуют (каждый logger со своим именем и файлом)

**Было:** Класс `Logger` в `fix_posters.py` и `user_sync.py` перехватывает `sys.stdout` и пишет в файл. Это ломает другие скрипты, если запущены параллельно.

**Сделать:** Единственный `logger.py`:
```python
import logging

def setup_logger(name: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger(name)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
```
Каждый скрипт: `log = setup_logger("rezka", "sync_rezka_log.txt")`, затем `log.info("...")`.

**Выгода:** Не ломает stdout. Параллельные скрипты не конфликтуют. Можно добавить уровни (DEBUG/INFO/WARNING). Можно отправлять в WebSocket (п.1).

**Сложность:** Низкая. ~30 строк + замена в 5 файлах.

---

### 8. ✅ Умный batch-режим для Rezka — ВЫПОЛНЕНО

**Реализация:**
- `rezka_sync.py` полностью переписан на async: `asyncio` + `aiohttp` с `Semaphore(concurrency)`
- Двухфазная архитектура:
  - **Phase 1a:** Все поиски "с годом" собираются, дедуплицируются и отправляются параллельно
  - **Phase 1b:** Условные поиски "без года" — только если в Phase 1a не найден точный матч по названию
  - **Phase 1c:** Fallback-поиски для айтемов с нулевыми результатами
  - **Phase 1d:** Скоринг всех кандидатов
  - **Phase 2:** Загрузка страниц только для жизнеспособных кандидатов (score >= 70 или exact name match), с авторетраем при ID-конфликтах (до 5 раундов)
  - **Phase 3:** Единый `conn.commit()` вместо коммита на каждый айтем
- Общие хелперы вынесены в чистые функции: `_parse_title`, `_score_candidates`, `_extract_ids_from_soup`, `_verify_candidate`, `_extract_ratings_and_poster`
- Синхронная `search_rezka_for_item()` сохранена для `single_item_update.py`, переписана на `requests` напрямую + общие хелперы (без зависимости от `HdRezkaApi`/`HdRezkaSearch`)
- Конкурентность настраивается через `config.json`: `rezka.concurrency` (по умолчанию 3)
- Зависимость `aiohttp>=3.9.0` добавлена в `requirements.txt`

**Было:** Каждый айтем — 0.5с задержка + 2-3 HTTP запроса (search + page load). 1000 айтемов = ~8 минут.

**Стало:** 3 параллельных HTTP-запроса, поиски дедуплицированы, страницы грузятся только для ~30-50% айтемов. 1000 айтемов за 2-3 минуты.

---

## ТИР 3: ПОЛИРОВКА

### 9. ✅ Базовый класс для API-клиентов — ВЫПОЛНЕНО

**Реализация:**
- Создан `base_client.py` с `BaseMovieClient`:
  - `_check_limited()` — проверка лимитов и наличия API-ключа
  - `_handle_rate_limit(response)` — обработка 402/403/429 с автоповтором при 429
  - `_request(url, params, timeout)` — единый метод запроса: задержка + CachedSession + rate limit + raise_for_status
  - Абстрактные методы: `_get_api_key()`, `_build_headers()`, `get_by_id()`, `search_movie()`
- `KinopoiskClient(BaseMovieClient)` — только `base_url`, `headers`, `_parse_result()`, rate limit codes `[402, 429]`
- `PoiskKinoClient(BaseMovieClient)` — только `base_url`, `headers`, `_parse_result()`, rate limit codes `[401, 403, 429]`
- Все потребители (`single_item_update.py`, `sync_job.py`, `fix_posters.py`) работают без изменений

**Было:** `kinopoisk_client.py` и `poiskkino_client.py` — почти копипаст: одинаковые `__init__`, rate limit обработка, retry-логика, `time.sleep(0.3)`, `session.get()`, `raise_for_status()`.

**Стало:** Общая логика в `BaseMovieClient`, подклассы — только URL, заголовки и парсинг ответов.

---

### 10. Режим «тёмная тема»

Уже есть Tailwind — добавить `dark:` классы. Переключатель в шапке. Один класс на `<html>`, всё остальное Tailwind делает. ~30 минут работы, огромная разница в UX для вечернего использования.

---

### 11. Клавиатурные шорткаты

```
/       → фокус на поиск
Esc     → закрыть сайдбар / модал
← →     → предыдущая/следующая страница
Space   → пауза/резюме лога
```
Vue: `@keydown` на `document`. ~40 строк.

---

### 12. Кешированный счётчик категорий

**Сейчас:** Каждый вызов `/api/categories` делает 7 COUNT(*) запросов. С фильтрами — ещё `get_watched_item_ids()` с 3 подзапросами. При polling каждые 2 сек = 15+ SQL-запросов в секунду.

**Сделать:** Кешировать counts на 30 секунд в памяти (dict с timestamp). Инвалидировать при завершении любого процесса.

**Выгода:** Нагрузка на БД падает в 15 раз при polling.

---

### 13. Рекомендации на дашборде

Вместо 4 сухих цифр — умные подсказки:
```
⚠ 312 айтемов без KP ID → запусти 2.1 PoiskKino
⚠ 89 без Rezka → запусти 2.3 Rezka  
⚠ 23 дубликата → запусти Cleanup
✅ Все постеры заполнены!
```
Каждая подсказка — кнопка, сразу запускающая нужный процесс. Новичку понятно что нажимать.

---

### 14. Миниатюры вместо полных постеров

**Сейчас:** Feed грузит 20 постеров по ~50KB каждый = 1MB на страницу. На мобильном — боль.

**Сделать:** TMDB отдаёт размеры: `w92`, `w154`, `w185`, `w342`, `w500`, `original`. Для карточки в сетке достаточно `w342` (342px, ~15KB). Для модала — `w500`. Экономия 70% трафика.

---

## ПРИОРИТЕТ РЕАЛИЗАЦИИ

| # | Что | Время | Влияние |
|---|------|-------|---------|
| 4 | Прокси постеров | 1ч | Офлайн, скорость, надёжность |
| 2 | ✅ FTS5 поиск | Выполнено | Качество поиска x10 |
| 12 | Кешированный counts | 30мин | Нагрузка -15x |
| 14 | Миниатюры w342 | 20мин | Трафик -70% |
| 6 | ✅ Single-item update | Выполнено | Одна кнопка = полный цикл обновления |
| 1 | ✅ WebSocket | Выполнено | Реалтайм, нагрузка -30x |
| 3 | Cron-планировщик | 2ч | Автоматизация |
| 7 | ✅ Python logging | Выполнено | Чистота, параллельность без конфликтов |
| 9 | ✅ Базовый API-класс | Выполнено | DRY, поддерживаемость |
| 13 | Умные подсказки | 1ч | UX для новичков |
| 10 | Тёмная тема | 30мин | Вечерний комфорт |
| 11 | Шорткаты | 30мин | Скорость навигации |
| 5 | ✅ Единый DB-слой | Выполнено | Архитектура, схема в одном месте |
| 8 | ✅ Async Rezka | Выполнено | Скорость x3 |
