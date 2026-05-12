# Отчёт по ревизии проекта `parsnew` (par2)

Дата: 2026-05-12. Ветка: `main` на коммите `602e49b`.

Контекст: проект — личный медиа-каталог/синхронизатор (rutor / HDRezka / Kinopoisk
/ TMDB) на FastAPI + SQLite + Vue 3 (CDN) + Tailwind Play.
~11k строк Python, один `index.html` на ~2.7k строк / 194 KB, 144 теста,
3 миграции, Dockerfile + compose, pre-commit, CI-шаблон (`ci.example.yml`).

В целом проект сделан **аккуратно и продуманно**: видно, что он уже проходил
большую ревизию (в `par2_progress.md` отмечены десятки исправлений и отложенные
архитектурные пункты). Поэтому ниже — только то, что **реально** не так на
текущем `main`, без выдумывания «потенциальных проблем».

## 1. Что прямо ломает CI / тесты прямо сейчас

Эти пункты — самые приоритетные, потому что они влияют на тесты и линт
прямо сейчас (после `git mv ci.example.yml .github/workflows/ci.yml`).

### 1.1. `httpx` отсутствует в `requirements.txt`, тесты падают на сборе

```
RuntimeError: The starlette.testclient module requires the httpx package
```

`tests/test_api_collections_io.py`, `tests/test_health.py`,
`tests/test_rebind_audit.py` импортируют `fastapi.testclient.TestClient`,
а он требует `httpx`. В `requirements.txt` и `requirements.in` его нет, а
CI-джоба `tests` ставит только то, что в `requirements.txt` + `pytest` +
`pytest-asyncio`. Локально после `pip install httpx` все 144 теста
проходят за ~1.2 с.

Фикс: добавить `httpx>=0.27` в `requirements.in`/`requirements.txt`.

### 1.2. `ruff check .` падает на трёх ошибках

```
main.py:1:1:        I001  Import block is un-sorted or un-formatted
main.py:1894:28:    F401  `HdRezkaApi.HdRezkaApi` imported but unused
rezka_sync.py:937:27: F541  f-string without any placeholders
```

Все три — мусор:
- `main.py:18-25` — порядок импортов из `fastapi` сбит
  (`UploadFile` вынесен в конец). Достаточно `ruff check --fix .`.
- `main.py:1894` — `from HdRezkaApi import HdRezkaApi` в `get_stream_info`,
  при этом `HdRezkaApi` дальше не используется (используется
  `HdRezkaApi.types.TVSeries`).
- `rezka_sync.py:937` — `print(f"...")` без `{...}` в строке.

### 1.3. `ruff format --check .` хочет переформатировать `main.py`

Тот же кусок импортов. Локально лечится `ruff format .`.

### 1.4. Версия `pytest` в `requirements-dev.in` (`>=8.0`) расходится с CI

CI ставит `pytest` без верхней границы → подтянет 9.x. Сам по себе это не
ломает тесты, но `--strict-config` (pyproject.toml) включён, и при будущих
breaking-changes в `pytest` плагины могут не подняться. Закрепить нижнюю
границу нормально, но `pip-compile` так и не запускался — оба `.in` и
`.txt` ведутся вручную (см. шапку `requirements.txt`).

## 2. Реальные баги в коде

### 2.1. `DB_PATH` / `API_CACHE_PATH` / `APP_DATA_DIR` не используются

В `docker-compose.yml` они переопределены (`/data/app_data.db` и т.д.),
в `settings.py` объявлены, но **ни `db.py`, ни `api_cache.py` их не
читают**. Конструктор глобального инстанса `db = Database()` в конце
`db.py` берёт `path="app_data.db"` относительно CWD; `api_cache.py`
берёт путь из `config.json` и кладёт его рядом с собой.

Последствие: в docker-сетапе SQLite будет писаться **в слой
контейнера** (`/app/app_data.db`), а смонтированный том `/data`
останется пустым. При пересоздании контейнера данные пропадут — а это
ровно сценарий, против которого писался compose.

Фикс: пробросить путь в `Database()` через `settings.db_path` (или хотя
бы `os.getenv("DB_PATH", "app_data.db")`) и то же самое в `api_cache`.

См. <ref_snippet file="/home/ubuntu/repos/parsnew/db.py" lines="2014-2016" />
и <ref_snippet file="/home/ubuntu/repos/parsnew/docker-compose.yml" lines="29-34" />.

### 2.2. Мёртвый код после `return` в `/api/reset_database`

`main.py:2691-2716` — после первого `return {"status": "success", ...}`
идёт неотдоступный второй блок с тем же `os.remove(db_path)` +
`subprocess.Popen([...])`. Никогда не выполняется. Видно по
<ref_snippet file="/home/ubuntu/repos/parsnew/main.py" lines="2690-2716" />.

Фикс: удалить второй блок целиком.

### 2.3. `/api/database_import` зовётся в обход `apiFetch`

`index.html:1729` — `await fetch('/api/database_import', { method: 'POST', body: formData })`.
Это единственный вызов `/api/...` во всём фронте, который **не** идёт
через `this.apiFetch`. Когда `AUTH_USER` + `AUTH_PASS_HASH` включены,
этот запрос уходит без `Authorization: Bearer ...`, ловит 401 и в UI
показывается «Ошибка: неизвестно» (потому что 401-ответ — HTML, не JSON,
и `res.json()` бросает).

Фикс: переписать через `this.apiFetch(...)` (он сам подложит токен).

### 2.4. `/api/database_import` не проверяет, что файл — это SQLite

Единственная защита — `len(content) < 100`. Можно загрузить любой
файл > 100 байт, он перезапишет `app_data.db`, и приложение упадёт при
следующем `_conn`. Тот, кто это умеет загрузить, уже залогинен (auth-мидлвар
требует токен), но **тихая порча БД от опечатки** — это уже UX-баг.

Фикс: проверить magic `SQLite format 3\x00` в первых 16 байтах + бэкап
текущей БД в `backups/` перед записью.

### 2.5. `systemctl restart parsclode` зашит в код

`/api/self_update`, `/api/database_import`, `/api/reset_database` все
вызывают `subprocess.Popen(["systemctl", "restart", "parsclode"], ...)`
с подавленным stderr. Имя сервиса жёстко зашито (`parsclode` — видимо,
исторический typo от `parscloud`), плюс:
- В Docker этого systemd нет вообще — Popen стартует, тихо падает,
  пользователю отвечают «restarting» и UI ждёт перезагрузку, которой не
  будет.
- Если кто-то не root и не имеет sudoers на systemctl — то же самое.

Фикс: либо вынести имя/способ перезапуска в `config.json`
(systemctl/supervisor/docker-restart/none), либо просто проверять
`returncode` Popen и честно говорить «перезапустите вручную».

### 2.6. `_session_tokens` копит протухшие токены навсегда

Сессии живут в `_session_tokens: dict[str, float]`. Истёкший токен
удаляется только если по нему придёт следующий запрос (`_check_token`
делает `pop(token, None)` на cache-miss). Все остальные истёкшие токены
накапливаются: на 1k-чешуйку POSTov /api/login без logout процесс
оставит у себя 1k мёртвых ключей. Не критично — на личном инстансе
вряд ли вырастет до проблемы, — но это утечка.

Фикс: фоновая корутина чистит истёкшие раз в N минут (паттерн уже
есть — `_wal_checkpoint_loop`).

### 2.7. Логин без rate-limit

`POST /api/login` — никакой защиты от перебора. PBKDF2 на 600 000
итераций даёт ~50–100 мс на проверку, что для **одиночного** клиента
адекватно, но любой, кто видит порт 8000 (а раз доступ только из
домашней сети — это уже OK), может бомбить логин со скоростью
10–20 попыток/сек со скрипта.

На личном LAN — допустимый компромисс. Если планируется когда-то
выставить наружу (см. идеи в §6), без `slowapi`/`fastapi-limiter` или
ручного per-IP counter'а лучше не оставлять.

### 2.8. `_init_rezka_session` срабатывает один раз на старте без ретраев

В `lifespan` startup-фазе:
```python
await asyncio.get_running_loop().run_in_executor(None, _init_rezka_session)
```
Если в этот момент `rezka.ag` отвечает 5xx или капчей, `rezka_session`
остаётся `None` до **полного рестарта** процесса. Все `/api/online_sources`,
`/api/stream_info`, `/api/collections/*/toggle (sync to Rezka)` ниже
по коду молча no-op-ят (`if rezka_session:`), фронт об этом не узнаёт.

Фикс: либо ленивая инициализация при первом обращении с фолбэком, либо
повтор через N минут.

### 2.9. `datetime.utcnow()` — в Python 3.12 deprecated

`main.py:2190`:
```python
stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
```
Заменить на `datetime.now(timezone.utc).strftime(...)`. Не критично,
но Dockerfile собирается на `python:3.12-slim`.

### 2.10. `TMDB_API_KEY` едет в URL параметром

`tmdb_client.py` шлёт `?api_key=...` во все запросы. Это ключ попадает
в:
- ключи кеша `requests-cache` (на диске, в `api_cache.db`);
- любые прокси-логи между приложением и TMDB.

TMDB поддерживает Bearer-вариант (v4 token, header `Authorization: Bearer ...`).
Заменить — мелкая правка, повышает гигиену.

### 2.11. `batch_item_collections`: N+1 запросов

```python
@app.post("/api/batch_item_collections")
def batch_item_collections(data: BatchCollectionsRequest):
    result = {}
    for item_id in data.ids:
        result[str(item_id)] = db.get_item_collections(item_id)
    return result
```
На каждом item открывается отдельное соединение. Один `SELECT
collection_id, item_id FROM collection_items WHERE item_id IN (...)`
сделает то же самое за один обход.

### 2.12. `find_existing_item` без category_id всё ещё может склеить разные категории

Item 1.7 в `par2_progress.md` помечен сделанным: для `kp_id`/`imdb_id`/
`rezka_url` поиск теперь сужается по `category_id`, **когда** category
передан. Но если вызывающая сторона не передала `category_id`,
поведение прежнее — может попасть в любую категорию.
Все вызовы в `sync_job.py` передают, в `single_item_update.py` — нет.
Стоит сделать `category_id` обязательным (или хотя бы добавить
warning, когда не передан).

### 2.13. Три почти одинаковых dict-а `log_files = {...}`

`/api/sync_log`, `/api/download_log`, `/api/clear_log` (main.py:2095,
2126, 2147) держат тройник из одного и того же словаря. Вынести в
модульную константу и расшарить.

## 3. Безопасность

### 3.1. `AUTH_PASS` (plain) — fallback есть, в `.env.example` он
второй строкой. Уже помечен deprecated, но активен. Стоит выпилить
после следующего major-апдейта (или просто перестать читать `AUTH_PASS`
после, скажем, 2026-06).

### 3.2. CSP — `'unsafe-inline'` + `'unsafe-eval'`

Из-за Tailwind Play + Vue runtime templates. Это известное ограничение
текущей фронт-схемы (CDN), снимется только вместе с миграцией на
Vite/Vue SFC (`par2_progress.md` 4.7 / 4.8 / 5.9 — все deferred).
До тех пор сам сайт уязвим к XSS на любых полях, которые рендерятся
без эскейпа.

### 3.3. WebSocket: токен в query

`/ws?token=...` — нормальная практика для WS-аутентификации в браузере
(заголовки на upgrade-запросе нельзя выставить), но query попадает в
nginx/uvicorn access-log в открытом виде. Если логи когда-то будут
расшарены — токен утечёт. Минимум — отключить логирование `?token=` в
прод-инсталляции.

### 3.4. `subtitle_proxy` allow-list `_SUBTITLE_HOST_ALLOWLIST`

Список такой:
```python
("rezka.ag", "hdrezka", "rezka.cdnstream", "voidboost", "videocdn")
```
Проверка — `host.endswith(h) or h in host`. То есть `voidboost`
матчится в любую часть домена → `voidboost.evil.example.com` тоже
пройдёт. Лучше `endswith` явно и со списком полных суффиксов вроде
`.rezka.ag`, `.voidboost.net`, `.videocdn.tv`.

## 4. Архитектура, размер файлов, инфраструктура

### 4.1. Размер ключевых файлов

| Файл         | Строк | LOC сверх среднего |
|--------------|------:|--------------------|
| `main.py`    | 2723  | 88 эндпойнтов + менеджер очереди + WS + auth + lifespan в одном файле |
| `db.py`      | 2016  | DAO для всех таблиц, миграции, FTS, audit_log, backup |
| `index.html` | 2711  | Vue 3 SPA в одном файле, ~75% — это `<script>` |

Это уже стало неудобно для review. В `par2_progress.md` это отмечено
как **deferred** (4.9 / 5.9). Просто фиксирую, что пункты не сделаны.

Минимально-инвазивный шаг (без миграции на Vite): вынести из `main.py`
группы эндпойнтов в `routes/feed.py`, `routes/collections.py`,
`routes/streams.py`, `routes/auth.py`, `routes/process_control.py` и
оставить в `main.py` только `app = FastAPI(...)` + `include_router`.

### 4.2. `settings.py` написан, но не подключён

Pydantic-Settings класс `Settings` лежит в `settings.py`, покрыт
тестами, но **ни один production-модуль его не импортирует** (grep по
`from settings` выдаёт только `tests/`). Везде остался прямой
`os.getenv(...)`. Это явно «незавершённый rollout» (5.5 в
`par2_progress.md`).

Стоит либо доделать миграцию (`db.py`, `api_cache.py`, `main.py`,
`rezka_sync.py`, …), либо снести `settings.py` пока он не разъехался с
реальностью.

### 4.3. Три перекрывающихся пути миграции схемы

В `db.py` живут одновременно:
1. `init_schema()` — `CREATE TABLE IF NOT EXISTS ...` + локальные
   `ALTER TABLE ... ADD COLUMN tmdb_id` (в `try/except OperationalError`).
2. `check_and_migrate_schema()` — добавляет колонки через `PRAGMA
   table_info` + явные `ALTER TABLE`.
3. `_apply_migrations()` — runner `migrations/NNNN_*.sql` под
   `PRAGMA user_version`.

Они идемпотентны и сейчас не конфликтуют, но любые будущие изменения
схемы придётся синхронизировать в трёх местах, иначе пути разойдутся.

Целевое состояние: всё, что не базлайн, — только через
`migrations/NNNN_*.sql`. `init_schema()` оставить, как и обещано в
`migrations/0001_baseline.sql`, **только для голой базы**.

### 4.4. CI, который не запускается

`ci.example.yml` лежит в корне с инструкцией:
```
git mv ci.example.yml .github/workflows/ci.yml
```
Это неудобство, но понятное (OAuth app без `workflow` scope). Стоит
явно отметить в README, когда тот будет (см. §6.1).

### 4.5. `.dockerignore` ссылается на удалённые файлы

```
SECURITY.md
TODO.md
TOP_OPTIMIZATIONS.md
```
Все три удалены коммитами `12c75a4`, `602e49b` (последний коммит), …,
плюс `!README.md` — а README в репозитории **нет** (тоже удалили).
Шум, ничего не ломает.

### 4.6. Корневая директория как «public/»

`icon.png`, `index.html`, `manifest.json`, `sw.js` лежат рядом с
питон-модулями. `main.py` отдаёт их хардкодом через
`FileResponse("manifest.json")`. На Docker-сетапе это работает,
потому что `WORKDIR /app` и `COPY . .` всё захватывает, но
семантически фронт-артефакты лучше перенести в `static/`.

## 5. Мелочи

- `tests/d` — пустой 1-байтный файл, попавший случайно (`git log -- tests/d` →
  коммит `4b6e3e8 Create d`). Удалить.
- `par2_progress.md` (44 KB) — рабочий чек-лист, лежит в `main`.
  Ссылается на `par2_code_review.md`, которого в репозитории нет.
  Технически — не код, но шумит в diff'ах и `.dockerignore` уже
  исключает его (`par2_progress.md`).
- `app_core.py:62-64` — `if __name__ == "__main__":` создаёт
  `TrackerAppCore()` и печатает строку. Используется как способ
  «запустить миграцию руками». Это уже делает FastAPI lifespan.
  Кандидат на удаление либо отдельный `init_db.py`.
- `main.py:2` — комментарий `# Force update commit`. Мусор из
  force-push'а, удалить.
- `require_auth(request)` (`main.py:155-162`) объявлен, но нигде не
  использован как FastAPI dependency. Auth идёт через middleware. Либо
  использовать `Depends(require_auth)` точечно на критических ручках
  (`/api/database_import`, `/api/reset_database`, `/api/self_update`),
  либо удалить.
- 17 `except Exception: pass` / `except Exception:` без логирования в
  `main.py`. Часть из них (`os.remove progress_*.json` и т.д.) — это
  best-effort cleanup, и ок. Но в местах вроде:
  ```python
  except Exception as e:
      print(f"Ошибка записи истории: {e}")
  ```
  стоит использовать `logger.exception(...)` (когда 5.4 будет
  доделан).
- Жёсткие пути `sync_video_log.txt`, `progress_<key>.json`,
  `checkpoint_<key>.json`, `stop_<key>.flag` создаются в CWD. При
  запуске не из корня репо они полетят в неожиданные места. В Docker
  это решено `WORKDIR /app`, но локально на Windows-юзеров уже
  ловилось.
- `manifest.json` называется «Торрент-Радар», `index.html` `<title>` —
  «Antigravity Tracker», `pyproject.toml` — `name = "par2"`. Три
  разных имени продукта. Не баг, но косметика.
- `requirements.txt` НЕ закоммичен в результате `pip-compile`: шапка
  файла честно говорит «hand-curated». Лок-файл сейчас сделан
  смешанно — часть `==`, часть `>=`. На воспроизводимость билда это
  влияет; нужно один раз прогнать `pip-compile` и закоммитить
  результат.
- `rezka_session` — глобальная переменная, не потокобезопасная (читается
  из обработчиков, инициализируется в startup, обновляется при
  re-login). На фоне FastAPI с одним event-loop это пока ОК, но
  любое `run_in_executor`, которое попадает в реиниту, гонится с
  чтением — пока спасает GIL.

## 6. Что хорошо

Это не «всё плохо», поэтому стоит подсветить и сильные стороны:

- **Тесты есть и они быстрые**: 144 теста, < 2 секунд. Покрытие
  ключевых утилит (`normalize_title`, `clean_t`, `parse_rutor_date`,
  `find_existing_item`, `save_checkpoint`, `filter_rules`,
  `audit_log`, миграции). Это лучше, чем у большинства
  «личных» проектов.
- **Миграции под `PRAGMA user_version`** + чёткий
  `migrations/README.md` — правильное решение для проекта такого
  размера (Alembic был бы оверкилл).
- **PBKDF2 + sliding TTL + constant-time compare** для auth — сделано
  по уму.
- **`/health` endpoint** делает реальный `SELECT 1` против БД и
  возвращает `user_version`. Готов под k8s / docker healthcheck.
- **Атомарный `save_checkpoint`** через `tempfile.mkstemp` + `fsync`
  + `os.replace`. Это серьёзный паттерн, который часто игнорируют.
- **Backup-API через SQLite Online Backup** (`Database.backup_to`),
  и CLI `backup_db.py` с ротацией.
- **WAL-чекпоинт по таймеру** + boot-truncate. Реальная польза при
  длинных sync-сессиях.
- **FTS5 для поиска** (`items_fts`) с fallback на LIKE, плюс
  partial-индексы под `kp_id`/`imdb_id`/`rezka_url`. Хороший набор.
- **Service Worker** с network-first для HTML и cache-bust по хешу
  `(mtime, size)` ключевых файлов — добротно.
- **`par2_progress.md`** — честный лог технического долга:
  что сделано, что отложено и почему. Многие проекты этого не имеют.
- **`.env.example`** объясняет каждую переменную, советует завести
  отдельный rezka-аккаунт, даёт one-liner для генерации
  `AUTH_PASS_HASH`. Уровень документации env-файла — намного выше
  среднего.

## 7. Идеи фич

То, чего сейчас нет, и что органически достроилось бы на
существующей архитектуре. Без «революции», только то, что реально
полезно медиа-каталогу:

1. **README.md**. Это самый важный пункт. В репозитории нет
   единой точки входа. На текущем `main` нужно открывать
   `pyproject.toml` / `Dockerfile` / `.env.example` чтобы понять,
   что это вообще такое. Короткий README с:
   - что проект делает (в 3 строки);
   - как запустить (Docker compose + `.env`);
   - как залогиниться;
   - какие фоновые задачи бывают и зачем они;
   - ссылка на `migrations/README.md` и `par2_progress.md`.
2. **Кнопка logout** в UI. Эндпойнт `/api/logout` уже есть с 2.1
   (`par2_progress.md`), но в `index.html` нет кнопки. Tiny patch.
3. **Уведомления о новых релизах** в коллекциях. Пользователь добавил
   фильм в «Посмотреть позже» → пришёл новый качественный рип →
   push/Telegram-бот. Все строительные блоки есть: `releases` таблица
   с `date_added`, `collection_items`, бэкграунд-таска. Не хватает
   только канала доставки (Telegram bot — самый простой).
4. **Trakt.tv / Letterboxd sync**. Двунаправленная синхронизация
   просмотров. У вас уже есть `user_sync.py` для CSV-импорта из
   IMDb/Кинопоиска — добавить Trakt OAuth и периодическую
   синхронизацию: ватчлист → коллекция «Хочу посмотреть»,
   просмотренное с оценкой → user_rating.
5. **Jellyfin / Plex webhook**. Аналог пункта 4, только локальный
   — когда юзер посмотрел кино в Jellyfin, оно автоматически
   переезжает из «Хочу посмотреть» в «Просмотрено». Jellyfin отдаёт
   webhook, FastAPI принимает.
6. **Recommendations**. У вас уже есть `user_rating` по каждому
   фильму. TMDB отдаёт `/movie/{id}/recommendations` и `/similar`
   бесплатно. Можно собирать «вам должно понравиться» из
   высокооценённых пользователем фильмов.
7. **Discover endpoints**. TMDB Discover API позволяет фильтр
   «лучшее за десятилетие N в жанре X со средней оценкой > 7.5».
   Сейчас фильтр идёт только по локальной БД, которая знает только
   то, что попало через rutor. Discover открывает «потолок»
   рекомендаций — это много чего может дать пользователю.
8. **Поддержка субтитров побольше**. `/api/subtitle_proxy` уже умеет
   проксировать SRT/VTT с rezka и cdn'ов. Логичное продолжение —
   OpenSubtitles API (или подписной opensubtitles.com): по hash файла
   или imdb_id скачать русские субтитры и положить рядом со
   стримом.
9. **Прогресс просмотра серий**. Сейчас есть только
   `mark_season_seen`. Полноценный «смотрю 3й сезон, 5й эпизод» с
   continue-watching на главной — органичное развитие.
10. **Multi-user**. Сейчас приложение однопользовательское
    (`AUTH_USER` — один). Если развести `users` таблицу + per-user
    `user_ratings`, `collections`, `last_visit` — получается домашний
    Plex без видео-файлов. На уровне БД это не очень больно (внешний
    FK).
11. **Prometheus / metrics endpoint**. `/metrics` отдаёт `queue.size`,
    время последних sync-задач, кол-во ошибок rezka login, etc.
    `_session_tokens` count тоже сюда. Health у вас есть, метрик нет.
12. **RSS-фид новых релизов**. По коллекциям и категориям.
    Подписаться можно из FreshRSS/Miniflux. Делается в один
    `@app.get('/rss/...')` поверх существующего `get_feed`.
13. **HTTPS sidecar** в `docker-compose.yml`. Сейчас Dockerfile
    отдаёт plain HTTP на 8000. Добавить Caddy с
    `localhost:443 -> par2:8000`, и `AUTH_USER`/`AUTH_PASS` начинают
    реально что-то значить (см. 2.7 + 3.3).
14. **Webhook на завершение sync-задачи**. По окончании
    `sync_video`/`reprocess` дёргать настраиваемый URL с JSON
    (counts, длительность, статус). Полезно для интеграции с домашним
    автоматизатором (Home Assistant, n8n).
15. **`/api/bulk` для редактирования**. Сейчас, чтобы переметить 20
    фильмов в коллекцию, фронт делает 20 POST'ов. Один
    `POST /api/collections/{id}/toggle_bulk` с `ids[]` снимет нагрузку.

---

## TL;DR

Состояние проекта — **рабочее, аккуратное, с заметным техдолгом**. Это
не «всё разваливается», это «есть конкретный список того, что нужно
дочинить и где задержалась незавершённая миграция».

Если упорядочить по приоритету «что точно стоит сделать сейчас»:

1. **Починить CI**: добавить `httpx` в `requirements*.txt`, прогнать
   `ruff check --fix .` + `ruff format .` (§1.1–1.3).
2. **Починить Docker-персистентность**: пробросить `DB_PATH` /
   `API_CACHE_PATH` в `db.py` / `api_cache.py` (§2.1) — иначе compose
   обманывает пользователя.
3. **Поправить `/api/database_import` на фронте** (§2.3) и валидацию
   SQLite-magic на бэке (§2.4).
4. **Удалить мёртвый код** после `return` в `reset_database` (§2.2),
   `tests/d`, комментарий «Force update commit», неиспользуемый
   `require_auth`, дубль `log_files = {...}`×3 (§5).
5. **Добавить README.md** (§6.1).
6. **Доделать миграцию на `settings.py`** или удалить его (§4.2).
7. Дальше — большие пункты из `par2_progress.md` (Vite-сборка
   фронта, разрезание `main.py`/`db.py`, переход с `print` на
   `logging`).
