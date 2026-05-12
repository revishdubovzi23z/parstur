# Дорожная карта работ по проекту `par2`

Этот файл — план для **AI-агента** (или человека), который будет
поэтапно дочищать техдолг и наращивать фичи. Все пункты упорядочены
**по зависимостям**: каждый следующий этап опирается на сделанное в
предыдущих, и **ничто из сделанного не обесценивается** работой по
дальнейшим этапам.

Источник дефектов — `REVIEW.md` в корне (полная ревизия от `602e49b`).
Источник истории — `par2_progress.md`.

## Принципы

1. **Один этап = один или несколько маленьких PR.** Никаких
   «большой PR на 5000 строк».
2. **Каждый PR должен оставлять `main` зелёным (CI passing).**
   Поэтому Этап 0 — это починка CI. Всё остальное должно проходить
   через зелёные тесты.
3. **Никаких обратно-несовместимых правок без grace-period.**
   Если убираем `AUTH_PASS` plain, сначала логируем deprecation
   1 релиз, потом удаляем.
4. **Удаление кода предпочтительнее добавления.** Если фича не нужна
   — снести, а не «доделать когда-нибудь».
5. **Перед каждым «большим» этапом** (миграция логирования, split
   `main.py`, фронт на Vite) — отдельный PR с подготовительным
   рефакторингом, чтобы основной diff был механическим.

---

## Этап 0 — Починка CI

**Зачем сначала:** без зелёного CI каждый следующий PR улетает в
красное, и агент не сможет отделить «свою» регрессию от существующей.

**Состояние сейчас:** `ci.example.yml` лежит в корне, не в
`.github/workflows/`. Если переименовать «как есть» — CI красный
из-за §1.1–1.3 в `REVIEW.md`.

### 0.1. Привести репозиторий в состояние, в котором CI будет зелёным.

Один PR со следующими правками:

- [x] `requirements.in`: добавить `httpx>=0.27`.
- [x] `requirements.txt`: добавить `httpx==0.27.x` (последняя
  совместимая со starlette/fastapi).
- [x] Прогнать `ruff check --fix .`. Это починит:
  - `main.py:1:1` — порядок импортов.
  - `main.py:1894:28` — неиспользуемый `HdRezkaApi.HdRezkaApi`
    (`from HdRezkaApi.types import TVSeries` оставить).
  - `rezka_sync.py:937:27` — убрать `f` префикс из print без `{}`.
- [x] Прогнать `ruff format .`. Заденет `main.py` (импортный
  блок).
- [x] Локально убедиться, что `pytest -q` зелёный (144 теста)
  и `ruff check .` + `ruff format --check .` тоже.

**Acceptance criteria:**
- `pytest -q` → 144 passed, 0 failed.
- `ruff check .` → `All checks passed!`.
- `ruff format --check .` → `40 files already formatted`.

### 0.2. Активировать CI workflow.

Отдельным PR (после слияния 0.1), чтобы видеть, что зелёный
прогон поднимается с нуля:

- [x] `git mv ci.example.yml .github/workflows/ci.yml`.
- [x] Убедиться, что три джобы (`lint`, `tests`, `types`) запускаются.
- [x] Если у репозитория нет `workflow` scope на токене — добавить
  репо-секрет/scope через настройки.

**Acceptance criteria:** на новом PR (любом тестовом) три зелёные
галочки CI.

### 0.3. Зафиксировать pre-commit pinning.

Один PR:
- [x] `requirements-dev.in`: поставить `pytest>=8,<10`,
  `pre-commit>=3.7,<5`, фиксировать ruff в lock-step с
  `.pre-commit-config.yaml` (там сейчас `v0.6.9`).
- [x] Прогнать `pip-compile requirements.in -o requirements.txt`
  и `pip-compile requirements-dev.in -o requirements-dev.txt`,
  закоммитить **результат**. Шапка `requirements.txt` уже
  предписывает этот ритуал.

**Acceptance criteria:** `pip install -r requirements.txt -r
requirements-dev.txt` на чистой 3.10/3.12 ставится без
конфликтов.

---

## Этап 1 — Удаление мусора и низкорискованной грязи

**Зачем здесь:** этот этап чисто механический. Чем меньше его делать
вместе с большим рефактором — тем чище diff и меньше шанс что-то
случайно сломать. Все правки локальные, ничего архитектурного.

### 1.1. Удалить мёртвые/случайные файлы и строки.

Один PR:

- [x] Удалить `tests/d` (пустой 1-байтный файл).
- [x] Удалить `# Force update commit` из `main.py:2`.
- [x] Удалить второй (недостижимый) блок в `/api/reset_database`
  после первого `return` (`main.py:2705-2716`).
- [x] Удалить функцию `require_auth(request)` (`main.py:155-162`)
  — нигде не используется как `Depends(...)`.
  Альтернатива: оставить и применить точечно
  (`Depends(require_auth)`) на критичных эндпойнтах
  (`/api/database_import`, `/api/reset_database`,
  `/api/self_update`). Выбрать один из двух путей.
- [x] Удалить `if __name__ == "__main__":` блок из `app_core.py`
  (бесполезный, печатает строку и закрывает БД).
- [ ] Вынести три копии `log_files = {...}` в `main.py` (в
  `get_sync_log` / `download_log` / `clear_log`) в одну
  модульную константу `_LOG_FILES`.

### 1.2. Привести `.dockerignore` в порядок.

Один PR:
- [x] Удалить из `.dockerignore` ссылки на `SECURITY.md`,
  `TODO.md`, `TOP_OPTIMIZATIONS.md` — этих файлов нет.
- [x] Удалить строчку `!README.md` (README в репо тоже нет; вернём
  в Этапе 2).

### 1.3. Унифицировать имя продукта.

Один PR:
- [x] Решить, как проект называется: `antigravity-tracker` (pyproject)
  / `Antigravity Tracker` (index.html `<title>`). Привести три файла
  к единому имени.
- [x] Добавить переменную `APP_NAME` в `settings.py`, чтобы дальше
  использовать одну.

---

## Этап 2 — Документация

**Зачем здесь:** README нужен **до** любых рефакторингов, чтобы
будущий агент / новый человек смог собрать проект, не открывая
`Dockerfile` и `.env.example`. Дальнейшие этапы будут на него
ссылаться.

### 2.1. Написать `README.md`.

Один PR. Структура:

- [x] **Что это.** Описано (Media Manager, Rutor, Rezka, etc.).
- [x] **Быстрый старт.** Описано (Docker Compose).
- [x] **Локальная разработка без Docker.** Описано (pip, venv).
- [x] **Архитектура и ссылки.** Добавлены ссылки на миграции и техдолг.
- [x] **Фоновые задачи.** Описаны основные процессы.
- [x] **API и тесты.** Упомянуты в соответствующих разделах.
- [x] **Бэкапы.** Упомянут скрипт бэкапа.

### 2.2. Добавить `AGENTS.md` (или `CLAUDE.md`).

Один PR. Файл специально для AI-агентов, описывающий:
- [ ] Где живёт код по доменам.
- [ ] Какие команды считаются «git-добро» (`ruff format .`,
  `pytest -q`, `pre-commit run --all-files`).
- [ ] Что считается «нельзя трогать без обсуждения» (схема БД
  без миграции, удаление `app_data.db`, force-push в main).
- [ ] Какой стиль логов и сообщений ожидается (см. Этап 5).
- [ ] Ссылка на `ROADMAP.md` (этот файл).

---

## Этап 3 — Доделать миграцию на `settings.py`

**Зачем здесь:** перед тем, как чинить Docker-пути (Этап 4),
нужно, чтобы в коде был один способ читать конфигурацию. Иначе
Docker-фикс получится либо пол-делом (только `Database`), либо
повторно зашьём `os.getenv` в местах, которые потом всё равно
придётся переписывать.

`settings.py` уже написан и покрыт тестами (`tests/test_settings.py`),
но ни один production-модуль его не импортирует.

### 3.1. Подключить `settings.py` к auth-блоку в `main.py`.

Один PR:
- [ ] Заменить `AUTH_USER = os.getenv("AUTH_USER", "")` и
  компанию на `settings.auth_user` и т.д.
- [ ] Добавить тест, что приложение читает auth-настройки из
  env (через `monkeypatch.setenv` + `reload_settings()`).
- [ ] Убедиться, что `_auth_enabled` ведёт себя так же, как
  сейчас (булева логика).

### 3.2. Подключить `settings.py` к Rezka-блоку.

Один PR:
- [ ] `rezka_sync.py`, `rezka_collections_sync.py` — читать
  `REZKA_EMAIL`/`REZKA_PASSWORD`/`REZKA_CONCURRENCY` через
  `settings.*`.
- [ ] `main.py:_init_rezka_session` тоже.

### 3.3. Подключить `settings.py` к API-ключам.

Один PR:
- [ ] `tmdb_client.py` → `settings.tmdb_api_key`.
- [ ] `kinopoisk_client.py` → `settings.kinopoisk_api_key`.
- [ ] `poiskkino_client.py` → `settings.poiskkino_api_key`.

### 3.4. Подключить `settings.py` к sync-блоку.

Один PR:
- [ ] `sync_job.py` — `MIN_YEAR`/`MAX_YEAR`/`STATUS_KEY`
  через `settings.*`.

### 3.5. Подключить `settings.py` к storage-блоку.

**Этот PR — фундамент Этапа 4.**

- [ ] Глобальный `db = Database()` в конце `db.py` →
  `db = Database(path=settings.db_path)`.
- [ ] `api_cache.py` → `settings.api_cache_path`.
- [ ] `RUTOR_MIRROR` в `rutor_parser.py` → `settings.rutor_mirror`.
- [ ] `app_data.db`-хардкоды в `/api/database_export`,
  `/api/database_import`, `/api/reset_database` (в `main.py`)
  → `settings.db_path`.

### 3.6. Удалить старый `os.getenv` слой.

Один PR (после 3.1–3.5):
- [ ] grep по `os.getenv(` в production-коде, убедиться, что
  всё через `settings.*`.
- [ ] Удалить лишние `load_dotenv()` вызовы (settings подхватывает
  `.env` сам).

---

## Этап 4 — Docker-персистентность

**Зачем здесь:** теперь, когда `Database` и `api_cache` читают пути
из `settings`, можно безопасно фиксировать compose-сетап.

### 4.1. Проверить, что compose реально пишет в `/data`.

Один PR (или комментарий к 3.5 если ничего больше не нужно):
- [ ] `docker compose up --build` → выполнить пару действий
  (логин, добавление в коллекцию) → `docker compose down` →
  `docker compose up` → проверить, что состояние осталось.
- [ ] Описать процедуру в `README.md`.
- [ ] Добавить smoke-тест в CI: `docker build .` хотя бы
  собирается. Полный compose-up в GH Actions — overkill.

### 4.2. Хардкод `systemctl restart parsclode` → абстракция.

Один PR:
- [ ] В `config.json` добавить блок:
  ```json
  "restart": { "command": ["systemctl", "restart", "parsclode"] }
  ```
  С дефолтом `None` (не перезапускаем сами).
- [ ] `/api/self_update`, `/api/database_import`,
  `/api/reset_database` читают список из конфига; если он
  пустой — возвращают «restart manually».
- [ ] Логировать `subprocess.run(..., check=False)` returncode.

---

## Этап 5 — Маленькие баг-фиксы и security-pass

**Зачем здесь:** низкорискованные точечные правки. Делаем **сейчас**,
чтобы потом не тащить их через большие архитектурные миграции.

### 5.1. Починить `/api/database_import`.

Один PR:
- [ ] Фронт: переписать `onDbImportFile` на `this.apiFetch(...)`
  (`index.html:1722-1741`), чтобы шёл Bearer-токен.
- [ ] Бэк: проверить magic `b"SQLite format 3\x00"` в первых
  16 байтах. Иначе 400 «invalid SQLite file».
- [ ] Бэк: перед `os.replace` сделать бэкап текущего
  `app_data.db` в `backups/pre-import-<timestamp>.db`.
- [ ] Тест: `test_database_import_rejects_garbage`,
  `test_database_import_accepts_valid_sqlite`.

### 5.2. Защитить `/api/reset_database` confirm-токеном.

Один PR:
- [ ] Эндпойнт ждёт `?confirm=<random>` где random выдаётся
  отдельным `GET /api/reset_database/token` (одноразовый,
  TTL 60 секунд).
- [ ] Фронт: модалка с подтверждением вызывает GET, потом POST
  с токеном.

### 5.3. N+1 в `batch_item_collections`.

Один PR:
- [ ] Добавить `db.get_item_collections_batch(ids: list[int])`,
  один SELECT с `WHERE item_id IN (...)` и группировкой в
  Python-словарь.
- [ ] Заменить цикл в `/api/batch_item_collections`.
- [ ] Тест.

### 5.4. `datetime.utcnow()` → `datetime.now(timezone.utc)`.

Один PR:
- [ ] Найти все вызовы (только `main.py:2190`), заменить.

### 5.5. TMDB API-ключ через Bearer.

Один PR:
- [ ] Перейти на v4 token TMDB (Authorization header).
- [ ] Удалить `api_key=` из URL.
- [ ] Обновить `.env.example` (`TMDB_API_TOKEN` вместо/в
  дополнение к `TMDB_API_KEY` — grace period).

### 5.6. GC для `_session_tokens`.

Один PR:
- [ ] Фоновая корутина `_session_gc_loop` (по образцу
  `_wal_checkpoint_loop`), раз в 10 минут чистит истёкшие.
- [ ] Метрика `_session_tokens_count` (хотя бы print, дальше
  попадёт в Prometheus в Этапе 12).

### 5.7. Rate-limit на `/api/login`.

Один PR:
- [ ] Добавить `slowapi` в `requirements.in`/.txt.
- [ ] `@limiter.limit("5/minute")` на login по IP.
- [ ] Тест с моком `Request.client.host`.

### 5.8. Retry для `_init_rezka_session`.

Один PR:
- [ ] Если первый старт не получился — фоновая задача пытается
  раз в 5 минут (с экспоненциальным backoff до 1 часа).
- [ ] WS-broadcast события `rezka_session_state`
  (`connecting`/`up`/`down`).
- [ ] Фронт показывает плашку «Rezka недоступна».

### 5.9. `subtitle_proxy` allow-list.

Один PR:
- [ ] Заменить `endswith / in` на explicit-suffix-list
  (`.rezka.ag`, `.voidboost.net`, `.videocdn.tv`,
  `hdrezka.app`, `rezka.cdnstream.tv`).
- [ ] Тест на `voidboost.evil.example.com` → 403.

### 5.10. WebSocket-токен в query: prod-инструкции.

Один PR:
- [ ] Добавить в `README.md` секцию «За HTTPS-прокси —
  отключить логирование `?token=` в nginx/Caddy».
- [ ] Альтернатива: пересмотреть схему (сначала `POST /ws/ticket`
  с Bearer → одноразовый ticket → `?ticket=...`). Tickets
  TTL 30 секунд, одноразовые. Отдельный PR, **не блокирует**
  остальное.

---

## Этап 6 — Миграция логов на `logging`

**Зачем здесь:** этот этап трогает **каждый** скрипт-сценарий и
готовит почву для разделения `main.py` (Этап 7) и `db.py` (Этап 8).
Если сделать split **до** логов, придётся править вышедшие модули
ещё раз → лишний шум в diff.

В `par2_progress.md` это пункт 5.4 (deferred).

### 6.1. Подготовить unified logging config.

Один PR:
- [ ] Создать `logging_config.py`:
  - `setup_logging(component, log_file)` возвращает `logging.Logger`.
  - Formatter единого вида: `<UTC ISO> <LEVEL> <component> | <message>`.
  - `RotatingFileHandler` с лимитом 50 МБ и 3 backup'ами.
  - `StreamHandler(sys.stdout)` дублируется для интерактивного
    запуска (как сейчас делает `setup_tee_logger`).
- [ ] Тесты: лог пишется и в файл, и в stdout.

### 6.2. Перевести `setup_tee_logger` на `logging`.

Один PR (после 6.1):
- [ ] `logger.py:setup_tee_logger` → тонкая обёртка над
  `logging_config.setup_logging`.
- [ ] Старый `TeeWriter` снести (или оставить для одного
  переходного релиза с deprecation warning).
- [ ] Все вызывающие модули продолжают звать `setup_tee_logger` —
  ничего не ломается.

### 6.3. Перевести `sync_job.py` на `logger.info` etc.

Один PR:
- [ ] Заменить `print(...)` на `logger.info(...)` /
  `logger.warning(...)` / `logger.error(...)`.
- [ ] Привести стиль сообщений к английскому, без эмодзи
  (можно русский, но единообразно — решить и зафиксировать
  в `AGENTS.md`, Этап 2.2).

### 6.4. То же для `rezka_sync.py`.

Один PR. Аналогично.

### 6.5. То же для `reprocess_database.py`.

Один PR.

### 6.6. То же для `fix_posters.py`, `cleanup_duplicates.py`,
`single_item_update.py`, `user_sync.py`.

Один PR (можно одним, скрипты небольшие).

### 6.7. `main.py` — `print` → `logger`.

Один PR. На фоне очереди задач и WS — таких мест мало (≈20).

### 6.8. `db.py` — `print` → `logger`.

Один PR.

### 6.9. Удалить `setup_tee_logger`.

Один PR (после того как никто не зовёт):
- [ ] grep подтверждает 0 вызовов.
- [ ] Удалить `TeeWriter`, `setup_tee_logger`, `logger.py`
  целиком или оставить как `logging_config` re-export.

### 6.10. `/api/log_level` эндпойнт.

Один PR (бонус):
- [ ] `POST /api/log_level {level: "DEBUG"}` — меняет уровень
  всех логгеров рантайм.

---

## Этап 7 — Разрезать `main.py` (2723 строки)

**Зачем здесь:** к этому моменту:
- логи единообразны (можно безопасно двигать функции),
- `settings.py` — единый источник конфига,
- мусор удалён.

Делаем **исключительно механический** mv-only рефактор. Никаких
правок кроме `from X import Y` и регистрации роутеров.

### 7.1. Подготовить `routes/` каркас.

Один PR:
- [ ] Создать `routes/__init__.py`.
- [ ] Вынести объявление `app = FastAPI(...)` в `main.py`
  (оставить).
- [ ] Создать пустые `routes/auth.py`, `routes/feed.py`,
  `routes/collections.py`, `routes/process.py`,
  `routes/streams.py`, `routes/admin.py`, `routes/items.py`
  — каждый со своим `APIRouter`.
- [ ] В `main.py` сделать `app.include_router(...)` для каждого.

### 7.2. Перенести auth-эндпойнты.

Один PR (только mv):
- [ ] `/api/login`, `/api/logout`, `/api/auth_status`,
  `/api/check_auth` → `routes/auth.py`.
- [ ] Auth-middleware остаётся в `main.py` (он на app-уровне).

### 7.3. Перенести feed/items.

Один PR (mv):
- [ ] `/api/feed`, `/api/categories`, `/api/item/*`,
  `/api/items/*`, `/api/stats` → `routes/feed.py` +
  `routes/items.py`.

### 7.4. Перенести collections.

Один PR (mv):
- [ ] `/api/collections/*`, `/api/collection_items/*`,
  `/api/batch_item_collections`, `/api/item_collections/*`
  → `routes/collections.py`.

### 7.5. Перенести process-control.

Один PR (mv):
- [ ] `/api/start_*`, `/api/stop_*`, `/api/process_status`,
  `/api/progress/*`, `/api/sync_log`, `/api/download_log`,
  `/api/clear_log` → `routes/process.py`.

### 7.6. Перенести streams/subtitles.

Один PR (mv):
- [ ] `/api/stream_info`, `/api/online_sources`,
  `/api/subtitle_proxy`, `/api/trailer` → `routes/streams.py`.

### 7.7. Перенести admin (database_*, reset, self_update).

Один PR (mv):
- [ ] `/api/database_export`, `/api/database_import`,
  `/api/reset_database`, `/api/self_update`,
  `/api/log_level` (если уже есть из 6.10) → `routes/admin.py`.

### 7.8. WebSocket и оставшееся.

Один PR (mv):
- [ ] `/ws`, `/health`, `/sw.js`, `/`, `/manifest.json`,
  `/icon.png` остаются в `main.py` (это инфраструктура, а не
  бизнес-логика).

**После этапа:** `main.py` ≈ 300–400 строк, остальное — в `routes/`.

---

## Этап 8 — Разрезать `db.py` (2016 строк)

Аналогично Этапу 7, **mv-only**. К этому моменту весь production-код
уже через `settings.py`, так что миграция не вызовет проблем с
импортами.

### 8.1. Подготовить `db/` каркас.

Один PR:
- [ ] Переименовать `db.py` → `db/__init__.py`. Внутри —
  только `class Database` core (init, `_conn`,
  `get_connection`, миграции).
- [ ] Глобальный `db = Database()` (плюс `settings.db_path`
  из этапа 3.5).

### 8.2. Вынести items / feed / search.

Один PR (mv):
- [ ] `db/items.py`: `get_item`, `get_items`, `get_items_count`,
  `find_existing_item`, `add_item`, `update_item_field`, etc.
- [ ] `db/feed.py`: `get_feed`, `get_categories_with_counts`,
  `get_stats`, FTS-логика.

### 8.3. Вынести collections.

Один PR (mv):
- [ ] `db/collections.py`: `get_collections`,
  `add_collection`, `toggle_collection_item`,
  `delete_collection`, etc.

### 8.4. Вынести audit / filter_rules / user_ratings / releases.

Один PR (mv):
- [ ] `db/audit.py`, `db/filter_rules.py`, `db/ratings.py`,
  `db/releases.py`.

### 8.5. Вынести миграции и backup.

Один PR (mv):
- [ ] `db/migrations.py`: `_apply_migrations`,
  `check_and_migrate_schema`.
- [ ] `db/backup.py`: `backup_to`, `wal_checkpoint`.

---

## Этап 9 — Консолидировать пути миграции схемы

**Зачем здесь:** к этому моменту `db.py` разрезан, и три параллельных
пути миграции (`init_schema` со встроенными ALTER, `check_and_migrate_schema`,
`_apply_migrations`) можно безопасно сводить.

### 9.1. Вынести все ALTER из `init_schema` в миграции.

Один PR:
- [ ] Каждое `ALTER TABLE ... ADD COLUMN tmdb_id` и т.п. в
  `init_schema` оборачиваем в `migrations/NNNN_*.sql`.
- [ ] Из `init_schema` оставляем только `CREATE TABLE IF NOT
  EXISTS` для baseline-схемы.
- [ ] Тест: на старом snapshot БД (без новой колонки) после
  `init_schema()` колонка добавлена через миграцию.

### 9.2. Снести `check_and_migrate_schema`.

Один PR:
- [ ] Все ALTER из неё → миграции с большими номерами.
- [ ] Удалить функцию.
- [ ] grep на её вызовы → переадресовать на `_apply_migrations`.

---

## Этап 10 — Фронт на Vite + Vue 3 + TypeScript

**Зачем здесь:** к этому моменту бэк зачищен, и можно сосредоточиться
на фронте без параллельных правок API. До этого этапа любая фронт-
правка ломала бы рефактор Этапов 7/8.

В `par2_progress.md` это пункты 4.7 / 4.8 / 5.9 (deferred).

### 10.1. Подготовить `frontend/` workspace.

Один PR:
- [ ] `frontend/package.json`, `vite.config.ts`,
  `tsconfig.json`. Vue 3.4, TypeScript 5, Tailwind через
  PostCSS.
- [ ] CI: новая джоба `frontend-build` на `npm ci` + `npm run build`.
- [ ] FastAPI: маршрут `/` отдаёт `frontend/dist/index.html`
  (через `StaticFiles`) если `dist/` существует, иначе fallback
  на legacy `index.html`.
- [ ] `mv index.html legacy_index.html` — оставить как
  reference на один релиз.

### 10.2. Минимальный SFC — точка входа.

Один PR:
- [ ] `frontend/src/main.ts` поднимает Vue + базовый layout.
- [ ] Один пустой компонент `App.vue`, который рендерит «Hello».
- [ ] Подключить shared store (Pinia).
- [ ] CSP: разрешить `'self'`, убрать `cdn.tailwindcss.com`.

### 10.3. Перенести auth-модалку.

Один PR:
- [ ] `LoginModal.vue` + `useAuth` store.
- [ ] `apiFetch` → `useApi` composable.

### 10.4. Перенести feed-список.

Один PR. Самый большой кусок legacy `index.html`.

### 10.5. Перенести коллекции и фильтры.

Один PR.

### 10.6. Перенести модалки админки (database/reset/self_update).

Один PR. К этому моменту весь функционал на Vue 3 SFC.

### 10.7. Снести `legacy_index.html` и Tailwind Play.

Один PR:
- [ ] grep на отсутствие ссылок на legacy.
- [ ] Обновить SW (`sw.js`) под новый bundle path.
- [ ] CSP: убрать `'unsafe-eval'` (Vue runtime больше не
  нужен), убрать `'unsafe-inline'` если получится.

---

## Этап 11 — HTTPS sidecar в compose

**Зачем здесь:** после миграции на Vite фронт стабилен, можно класть
поверх обратный прокси с TLS.

Один PR:
- [ ] `docker-compose.yml`: добавить сервис `caddy:` (
  `caddy:2-alpine`), volume на `./Caddyfile`.
- [ ] `Caddyfile`: `localhost { reverse_proxy par2:8000 }`
  с автоматическим self-signed для dev.
- [ ] Обновить README инструкцию: «по умолчанию HTTPS на
  https://localhost; принять самоподписанный».
- [ ] Опционально: ENV `EXTERNAL_HOST` для прод-сетапа с
  Let's Encrypt.

---

## Этап 12 — Фичи

Здесь порядок свободнее. Указан грубый приоритет «польза/трудозатраты».

### 12.1. Кнопка Logout в UI (xs, 1 час).

- [ ] Эндпойнт `/api/logout` уже есть.
- [ ] Добавить кнопку в header / user-menu.
- [ ] Очищать `sessionStorage` и WS.

### 12.2. Webhook на завершение sync-задачи (s, полдня).

- [ ] Конфиг: `webhook.url` + опциональный `webhook.secret`.
- [ ] При завершении job'а — `POST` с JSON
  (`{job, status, started_at, finished_at, counts}`).
- [ ] HMAC-sha256 в заголовке.
- [ ] Документировать в README.

### 12.3. `/metrics` эндпойнт (Prometheus) (s, полдня).

- [ ] `prometheus_client` в зависимости.
- [ ] Метрики: `job_duration_seconds{job=...}`,
  `job_runs_total{job=...,status=...}`,
  `session_tokens_count`, `db_user_version`,
  `rezka_session_state`, `queue_size`.
- [ ] Эндпойнт `/metrics` (за auth-middleware либо отдельный
  bearer-secret для Prometheus).

### 12.4. RSS-фид новых релизов (s, день).

- [ ] `GET /rss/collection/{id}` и `/rss/category/{id}` →
  Atom 1.0 с последними N релизами.
- [ ] Кеш на 10 минут.

### 12.5. Bulk-эндпойнты (s, день).

- [ ] `POST /api/collections/{id}/toggle_bulk { item_ids: [...] }`
  — один SELECT + один INSERT/DELETE.
- [ ] Фронт: при выборе N карточек и кнопке «в коллекцию» —
  один запрос вместо N.

### 12.6. Telegram-уведомления о новых релизах в коллекциях (m, 2 дня).

- [ ] `python-telegram-bot` в зависимости.
- [ ] Конфиг: `telegram.token`, `telegram.chat_id`.
- [ ] При завершении sync'а проверяем releases с
  `date_added > last_notified` для item'ов в коллекциях типа
  «Хочу посмотреть»; отправляем сообщение с постером и
  magnet-ссылкой.
- [ ] Off-by-default.

### 12.7. TMDB recommendations / discover (m, 2 дня).

- [ ] `GET /api/recommendations` — берёт топ-N по user_rating
  юзера, дергает `/movie/{id}/recommendations`, агрегирует,
  отдаёт фронту.
- [ ] `GET /api/discover?genre=...&year_from=...` — обёртка
  над TMDB Discover.
- [ ] Фронт: новые вкладки в сайдбаре.

### 12.8. Прогресс серий (m, 2–3 дня).

- [ ] Таблица `episode_progress(item_id, season, episode, watched_at)`.
- [ ] Эндпойнты `POST /api/items/{id}/episodes/{season}/{episode}/watched`.
- [ ] Фронт: «Продолжить смотреть» на главной.
- [ ] Учитывать в `is_watched` агрегате.

### 12.9. OpenSubtitles интеграция (m, 2 дня).

- [ ] `opensubtitles_client.py` — по imdb_id или hash файла.
- [ ] `GET /api/subtitles?item_id=...&lang=ru` →
  скачивает, кладёт в `/data/subtitles/`, отдаёт URL.
- [ ] Конфиг: `opensubtitles.api_key`.

### 12.10. Jellyfin webhook (m, 2 дня).

- [ ] `POST /api/webhook/jellyfin` — принимает
  `PlaybackStop` события, ищет матч по imdb_id, проставляет
  `is_watched`/`watched_at`/прогресс.
- [ ] Документация: настройка плагина «Webhook» в Jellyfin.

### 12.11. Trakt.tv sync (l, 4–5 дней).

- [ ] OAuth-flow (`/api/trakt/auth/start`, `/api/trakt/auth/callback`).
- [ ] Хранение токенов в `trakt_credentials` таблице
  (миграция).
- [ ] Фоновая задача «trakt_sync»: watchlist → коллекция,
  ratings → user_rating, history → watched.
- [ ] CLI `python trakt_sync.py` + интеграция в task_queue.

### 12.12. Multi-user (xl, 1–2 недели).

Эта фича — самая большая, делать только когда всё остальное
устаканится.

- [ ] Миграция `users(id, name, password_hash, role,
  created_at)`.
- [ ] Внешний ключ `user_id` на `collections`,
  `user_ratings`, `audit_log`, `_session_tokens` (в БД).
- [ ] Auth — пользователь+пароль, не общий
  `AUTH_USER`/`AUTH_PASS`.
- [ ] Роли: `admin` (всё), `user` (свои коллекции, общая
  feed).
- [ ] Миграция существующих данных — все привязать к
  единственному `admin`.

---

## Принципы для агента в каждом PR

В порядке убывания приоритета:

1. **Сначала тест, потом код**, если правка меняет поведение.
   `pytest -q` должен быть зелёным перед коммитом.
2. **`pre-commit run --all-files`** перед `git push`.
3. **Описание PR** должно содержать:
   - какой пункт ROADMAP/REVIEW закрывает (например, «Этап 5.3»);
   - что протестировано локально;
   - чего НЕ сделано (если что-то опущено).
4. **Никаких force-push в `main`**. Только PR.
5. **Не трогать схему БД без миграции под `user_version`** (см.
   `migrations/README.md`).
6. **Не делать одной правкой больше двух пунктов** этой карты.
   Если хочется — два отдельных PR.
7. **`par2_progress.md` обновлять в том же PR**, где закрывается
   пункт оттуда.
8. **`REVIEW.md` обновлять** в конце этапа: вычеркнуть из «реальных
   багов» то, что закрыто; перенести в «история» или удалить.

## Контрольные точки

- **После Этапа 0**: CI зелёный, любой будущий PR валидируется.
- **После Этапа 3**: один источник правды по конфигурации.
- **После Этапа 4**: Docker-сетап реально персистентен.
- **После Этапа 6**: единообразные логи, можно строить мониторинг.
- **После Этапа 8**: `main.py` < 500 строк, `db.py` разрезан по
  доменам, любой агент может добавлять эндпойнты без слома
  всего файла.
- **После Этапа 10**: фронт на Vite, можно убирать
  `unsafe-eval`/`unsafe-inline` из CSP.
- **После Этапа 11**: HTTPS из коробки, можно выставлять наружу.
- **После Этапа 12**: проект — полноценный домашний медиа-центр.
