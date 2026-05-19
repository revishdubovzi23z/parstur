# Дорожная карта работ по проекту `par2`

План текущих и будущих работ. Закрытые пункты (Этапы 0–10, ~220
позиций) вычищены — их история живёт в `git log`. Здесь только
**активное и будущее**.

## Принципы

1. **Один этап = один или несколько маленьких PR.**
2. **Каждый PR должен оставлять `main` зелёным (CI passing).**
3. **Никаких обратно-несовместимых правок без grace-period.**
4. **Удаление кода предпочтительнее добавления.**
5. **Перед большими этапами** — отдельный PR с подготовительным
   рефакторингом, чтобы основной diff был механическим.

---

## Этап 10 — Vite + Vue 3 + TypeScript фронт. [почти готово]

Стадии 10.1–10.7z завершены: фронт целиком собирается через Vite,
легаси `index.html` снят с регистра, SPA отдаётся из `/`. Что ещё
осталось:

- [ ] **Smoke-тест на чистом окружении**: загрузить корень в
  браузере, проверить логин, feed, фильтры, sync-панель, логи,
  item-modal, player, rules. Желательно после `docker-compose up`
  на свежей машине.
- [ ] **CSP без `'unsafe-eval'`**: Vue runtime больше не
  компилируется в браузере — можно убрать `'unsafe-eval'` и
  ужесточить policy.

---

## Этап 11 — HTTPS sidecar в `docker-compose.yml`

Сейчас compose поднимает только HTTP. Для размещения наружу нужен
TLS-terminator.

Один PR:

- [x] `docker-compose.yml`: добавить сервис `caddy:` (
  `caddy:2-alpine`), volume на `./Caddyfile`.
- [x] `Caddyfile`: `localhost { reverse_proxy par2:8000 }`
  с автоматическим self-signed для dev.
- [x] Обновить README инструкцию: «по умолчанию HTTPS на
  https://localhost; принять самоподписанный».
- [x] Опционально: ENV `EXTERNAL_HOST` для прод-сетапа с
  настоящим Let's Encrypt.

---

## Этап 12 — Фичи

### 12.1. Logout-кнопка в UI

- [x] Эндпойнт `/api/logout` уже есть (см. PR #24, persistent sessions).
- [x] Добавить кнопку в header / user-menu.
- [x] Очищать `sessionStorage` и WS.

### 12.2. Webhook при завершении job'а

- [ ] Конфиг: `webhook.url` + опциональный `webhook.secret`.
- [ ] При завершении job'а — `POST` с JSON
  (`{job, status, started_at, finished_at, counts}`).
- [ ] HMAC-sha256 в заголовке.
- [ ] Документировать в README.

### 12.3. Prometheus metrics

- [ ] `prometheus_client` в зависимости.
- [ ] Метрики: `job_duration_seconds{job=...}`,
  `db_queries_total`, `ws_clients`, `feed_items_total`,
  `rezka_session_state`, `queue_size`.
- [ ] Эндпойнт `/metrics` (за auth-middleware либо отдельный
  bind на 9090).

### 12.4. RSS-feed

- [ ] `GET /rss/collection/{id}` и `/rss/category/{id}` →
  Atom 1.0 с последними N релизами.
- [ ] Кеш на 10 минут.

### 12.5. Bulk-операции с коллекциями

- [ ] `POST /api/collections/{id}/toggle_bulk { item_ids: [...] }`
  — один SELECT + один INSERT/DELETE.
- [ ] Фронт: при выборе N карточек и кнопке «в коллекцию» —
  одним запросом.

### 12.6. Telegram-уведомления

- [ ] `python-telegram-bot` в зависимости.
- [ ] Конфиг: `telegram.token`, `telegram.chat_id`.
- [ ] При завершении sync'а проверяем releases с
  magnet-ссылкой.
- [ ] Off-by-default.

### 12.7. Recommendations / Discover

- [ ] `GET /api/recommendations` — берёт топ-N по user_rating
  и через TMDB `/movie/{id}/recommendations` дотягивает соседей,
  отдаёт фронту.
- [ ] `GET /api/discover?genre=...&year_from=...` — обёртка
  над TMDB Discover.
- [ ] Фронт: новые вкладки в сайдбаре.

### 12.8. Episode tracking для сериалов

- [ ] Таблица `episode_progress(item_id, season, episode, watched_at)`.
- [ ] Эндпойнты `POST /api/items/{id}/episodes/{season}/{episode}/watched`.
- [ ] Фронт: «Продолжить смотреть» на главной.
- [ ] Учитывать в `is_watched` агрегате.

### 12.9. Subtitles auto-fetch

- [ ] `opensubtitles_client.py` — по imdb_id или hash файла.
- [ ] `GET /api/subtitles?item_id=...&lang=ru` →
  скачивает, кладёт в `/data/subtitles/`, отдаёт URL.
- [ ] Конфиг: `opensubtitles.api_key`.

### 12.10. Jellyfin / Plex integration

- [ ] `POST /api/webhook/jellyfin` — принимает
  PlaybackStop / PlaybackProgress; обновляет
  `is_watched`/`watched_at`/прогресс.
- [ ] Документация: настройка плагина «Webhook» в Jellyfin.

### 12.11. Trakt sync

- [ ] OAuth-flow (`/api/trakt/auth/start`, `/api/trakt/auth/callback`).
- [ ] Хранение токенов в `trakt_credentials` таблице
  (миграция).
- [ ] Фоновая задача «trakt_sync»: watchlist → коллекция,
  ratings → user_rating, history → watched.
- [ ] CLI `python trakt_sync.py` + интеграция в task_queue.

### 12.12. Multi-user / роли

- [ ] Миграция `users(id, name, password_hash, role, created_at)`.
- [ ] Внешний ключ `user_id` на `collections`, `user_ratings`,
  `audit_log`, `sessions`.
- [ ] Auth — пользователь+пароль, не общий `AUTH_USER`/`AUTH_PASS`.
- [ ] Роли: `admin` (всё), `user` (свои коллекции, общая feed).
- [ ] Миграция существующих данных — все привязать к единственному `admin`.

---

## Принципы для агента в каждом PR

В порядке убывания приоритета:

1. **Сначала тест, потом код**, если правка меняет поведение.
   `pytest -q` должен быть зелёным перед коммитом.
2. **`pre-commit run --all-files`** перед `git push`.
3. **Описание PR** должно содержать:
   - какой пункт ROADMAP закрывает (например, «Этап 12.2»);
   - что протестировано локально;
   - чего НЕ сделано (если что-то опущено).
4. **Никаких force-push в `main`**. Только PR.
5. **Не трогать схему БД без миграции под `user_version`** (см.
   `migrations/README.md`).
6. **Не делать одной правкой больше двух пунктов** этой карты.
   Если хочется — два отдельных PR.

## Контрольные точки

- **После Этапа 10**: фронт на Vite, можно убирать `unsafe-eval`/`unsafe-inline` из CSP.
- **После Этапа 11**: HTTPS из коробки, можно выставлять наружу.
- **После Этапа 12**: проект — полноценный домашний медиа-центр.
