# План интеграции kino.pub (по аналогии с HDRezka)

- Контекст: `parstur` уже умеет «по фильму подтянуть HDRezka URL + сезоны + потоки + субтитры» через `runtime/rezka.py` + `routes/streams.py` + фронтовый `PlayerModal.vue`. Хотим то же самое с источником **kino.pub**.
- Дата плана: 2026-05-14.
- Это **план**, а не PR. Кода я не писал. Когда подтвердишь scope и доступы — превращу в 3-4 маленьких PR по образцу существующих стадий.

---

## 0. Что такое kino.pub с точки зрения интеграции

В отличие от HDRezka (скрейпинг HTML + iframe), у kino.pub есть **официальный JSON API** — `https://api.service-kp.com/v1/...`. Документация на `https://kinoapi.com`. Это сильно меняет архитектуру:

- Не нужно session-rezka-like «эмулировать вход» — есть OAuth2 Device Flow (RFC 8628).
- API отдаёт **готовые URL'ы потоков** (HLS, MP4) разных качеств + список субтитров с прямыми ссылками.
- Можно искать по названию/году, можно найти по `imdb_id`/`kinopoisk_id` (что у нас уже есть в `items`).

Это значит, что архитектурно интеграция чище и **меньше кода**, чем rezka. Главный «тяжёлый» вопрос — авторизация и хранение токенов.

### Что НЕ работает «из коробки»

1. **Нужен client_id/client_secret.** По доке (`https://kinoapi.com/authentication.html`): «Для получения client_id и client_secret пишите на support@kino.pub». То есть это белый список — нужно либо запросить пару у поддержки, либо использовать пару от какого-то open-source клиента (есть Roku-клиент `proton/Kinopub`, kodi-клиент и т.п., которые гитхабятся). Юридически чисто — запросить свою пару.
2. **Нужна подписка kino.pub.** Бесплатно потоки не отдаются. Подписку покупает пользователь (мы), интеграция использует его access_token.
3. **IP-locked стримы.** kino.pub привязывает stream URL к IP, с которого был сделан запрос `/items/{id}`. Если ходить на API с сервера parstur, а смотреть на телефоне в другой сети — может ругаться «not allowed». Решается тем, что **API-запрос на получение URL делает клиент** (плеер), а не сервер. То есть наш бэкенд хранит токен и отдаёт его клиенту по защищённому каналу — либо отдаёт уже резолвнутые URL за один прокинутый запрос с заголовком `X-Forwarded-For` (если включить у kino.pub — не уверен, надо тестить).

---

## 1. UX: что увидит пользователь

Поверх существующего `ItemCardModal.vue`:

1. **Бейдж «kino.pub»** рядом с уже существующим «Rezka» в карточке фильма. Состояния:
   - не привязан → серый, по клику открыть `kino_search` (поиск по названию на kino.pub).
   - привязан → синий с кликом «Открыть на kino.pub» (как нынешний `rezka_url`-бейдж).
   - привязан + нет потоков → жёлтый «Подписка истекла?» / «Нет на kino.pub».

2. **Кнопка «Смотреть → kino.pub»** в PlayerModal: новая вкладка/таб «Источник» рядом с «Rezka» и `Online (Kinobox)`.

3. **Выбор качества** (`360p / 480p / 720p / 1080p / 4K`) и **озвучки** (`AVO / DVO / MVO / sub`), плюс **выбор сабтитров** — отдельный select.

4. **Кнопка «Открыть в плеере»** с тремя пунктами (как у нынешнего m3u-flow):
   - «Встроенный плеер» (HTML5 video в браузере, как сейчас).
   - «Скопировать m3u» (для KMPlayer/VLC/Infuse).
   - **«Открыть в Android-плеере»** — `intent://...` URI. На Android это деплинк, который VLC/Kodi/MX Player подхватывают и сразу запускают видео с переданным URL потока + saturlur. Для iOS — `vlc://` или `infuse://`.

5. **«Привязать вручную»** — поле «вставить kino.pub URL»/«ID на kino.pub», на случай если автопоиск нашёл не то.

---

## 2. Архитектура бэкенда

### 2.1. Новые файлы

```
kinopub_client.py            # HTTP-клиент к https://api.service-kp.com/v1/*
                             # (наследник BaseMovieClient, с rate-limit/cache)
runtime/kinopub.py           # OAuth2 device-flow, хранение и refresh токенов,
                             # привязка kp_id <-> item_id, ленивый relogin
routes/kinopub.py            # /api/kinopub/* эндпоинты
sync_kinopub.py              # фоновой матчер «по каждому item — найти на kino.pub»
                             # (аналог rezka_sync.py, но проще — у API есть поиск)
tests/test_kinopub_client.py
tests/test_kinopub_relogin.py
```

### 2.2. Изменения в схеме БД (миграция `0006_kinopub.sql`)

```sql
ALTER TABLE items ADD COLUMN kinopub_id    INTEGER;     -- /v1/items/{id}
ALTER TABLE items ADD COLUMN kinopub_type  TEXT;        -- movie/serial/...
ALTER TABLE items ADD COLUMN checked_kinopub INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN kinopub_url   TEXT;        -- https://kino.pub/item/...
CREATE INDEX IF NOT EXISTS idx_items_kinopub_id ON items(kinopub_id);

CREATE TABLE IF NOT EXISTS kinopub_auth (
  id              INTEGER PRIMARY KEY CHECK (id = 1),   -- one-row table
  access_token    TEXT NOT NULL,
  refresh_token   TEXT NOT NULL,
  expires_at      REAL NOT NULL,
  client_id       TEXT NOT NULL,
  -- client_secret НЕ храним в БД (env), но фиксируем хеш, чтобы
  -- при ротации env-переменной знать, что токены устарели.
  client_secret_sha256 TEXT NOT NULL,
  updated_at      REAL NOT NULL
);
```

Решения по дизайну:
- **`kinopub_auth` — одна строка.** Это «глобальный» аккаунт kino.pub, как и нынешние REZKA-кредлы. Если завтра захотим multi-tenant — превратим в `(user_id, ...)`. Сейчас pet-проект — overkill не нужен.
- **`client_secret` не в БД.** Хранится в settings (`KINOPUB_CLIENT_SECRET` env). Хеш сохраняем, чтобы вычислять «refresh-токены протухли при смене секрета».
- **Поле `checked_kinopub`** — по образцу `checked_rezka` / `checked_uz`: «прошёл ли по этому item матчинг». Используется `sync_kinopub` для возобновления через checkpoint.

### 2.3. Новые settings (settings.py mixin)

```python
class KinopubSettings(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    api_base_url: str = "https://api.service-kp.com"
    device_verification_uri: str = "https://kino.pub/device"
    # refresh заранее за N секунд до expiry (Rezka-like)
    refresh_skew_seconds: int = 300
```

`.env.example`:
```
KINOPUB_ENABLED=false
KINOPUB_CLIENT_ID=
KINOPUB_CLIENT_SECRET=
```

### 2.4. OAuth2 Device Flow (бэкенд)

`runtime/kinopub.py` экспортирует:

```python
def start_device_flow() -> DeviceFlow:
    """POST /oauth2/device?grant_type=device_code
       Возвращает user_code, verification_uri, expires_in, interval."""

def poll_device_flow(code: str) -> bool:
    """POST /oauth2/device?grant_type=device_token
       Возвращает True когда юзер подтвердил, иначе False."""

def get_access_token() -> str | None:
    """Читает текущий токен из kinopub_auth, рефрешит при необходимости."""

def is_authenticated() -> bool:
    """True если есть валидные токены."""
```

Логика рефреша:
- Каждый вызов `_kinopub_request(...)` проверяет `expires_at - now < refresh_skew_seconds` → если так, делает `grant_type=refresh_token`.
- 401 от API → один retry с force-refresh, потом помечаем `kinopub_auth` как протухший и шлём WS-broadcast `{type: "kinopub_session", state: "expired"}` (как уже сделано для rezka — `_rezka.rezka_session_state`).

Background loop в `main.py` (по образцу `_rezka_session_retry_loop`): если токен «expired», раз в 5 минут пытаться refresh.

### 2.5. Эндпоинты

| Метод | Путь | Назначение |
|-------|------|-----------|
| `GET` | `/api/kinopub/status` | `{enabled, authenticated, expires_in, account_email?}` |
| `POST` | `/api/kinopub/device/start` | Стартует Device Flow, возвращает `user_code` + `verification_uri` для отображения в UI |
| `POST` | `/api/kinopub/device/poll` | Polls (фронт дёргает раз в 5с пока не вернёт `200 ready`) |
| `POST` | `/api/kinopub/logout` | Стирает токены |
| `GET` | `/api/kinopub/search?title=…&year=…` | Поиск (для ручной привязки) |
| `POST` | `/api/kinopub/bind/{item_id}` | `{kinopub_id}` → запишет `items.kinopub_id` + `kinopub_url`, лог в audit_log |
| `POST` | `/api/kinopub/unbind/{item_id}` | Сброс kinopub_* полей |
| `GET` | `/api/kinopub/stream_info/{item_id}` | Возвращает `{qualities: [{name, url, format, audio, subtitles: [...]}], seasons?: [...]}` (мапит ответ `/v1/items/{id}` в нашу схему) |
| `GET` | `/api/kinopub/m3u/{item_id}` | Генерит m3u (по образцу `/api/rezka_m3u/...`) |
| `POST` | `/api/start_sync_kinopub` | Запуск фонового матчинга через `task_queue` |

Все, кроме `/status` и `/device/start`, **требуют авторизации parstur** (т.е. защищены тем же middleware). Auth-status и device/start не требуют kino.pub-токена, но требуют parstur-сессию.

### 2.6. Mapping ответа kino.pub → наш формат `stream_info`

Что вернёт `GET /v1/items/{id}`:
```json
{
  "item": {
    "id": 12345,
    "title": "Inception",
    "year": 2010,
    "type": "movie",
    "videos": [
      {
        "id": 1,
        "files": [
          {"file": "https://cdn.kino.pub/...720.mp4", "quality": "720p", "codec": "h264"},
          {"file": "https://cdn.kino.pub/...1080.mp4", "quality": "1080p"},
          {"file": "https://cdn.kino.pub/...2160.mp4", "quality": "2160p"}
        ],
        "audios": [{"lang": "ru", "type": "AVO"}, {"lang": "en"}],
        "subtitles": [
          {"lang": "ru", "url": "https://cdn.kino.pub/...ru.vtt", "embed": false},
          {"lang": "en", "url": "https://cdn.kino.pub/...en.srt", "embed": false}
        ]
      }
    ],
    "seasons": [ ... ]  // для типа serial
  }
}
```

Наш мап-слой нормализует это в shape, который уже понимает `PlayerModal.vue` — тогда фронт-изменений будет минимум.

### 2.7. Фоновой матчинг (`sync_kinopub.py`)

По образцу `rezka_sync.py`:

1. Берём из БД все `items`, где `checked_kinopub = 0` и `(kp_id OR imdb_id)`.
2. Идём пачками по 50, для каждого пробуем найти по `imdb_id` (если есть) через `GET /v1/items?imdb={imdb_id}`; fallback — `GET /v1/items?q={title}&year={year}`.
3. Скоринг как в `tmdb_client.search_movie`: +60 за совпадение года, +40 за совпадение типа (movie/serial vs наш `category`).
4. Записываем `kinopub_id`, `kinopub_url`, `kinopub_type`, `checked_kinopub=1`.
5. Checkpoint каждые 100 записей (`save_checkpoint("kinopub", last_id)`), читаемый при resume — точно так же, как rezka.
6. Прогресс шлётся через `report_progress("kinopub", ...)` — UI его подхватит автоматически.

Запуск из UI: кнопка `start_sync_kinopub` в `SyncPanel.vue` рядом с `start_rezka`. Останавливается тем же stop-флагом, что и rezka (см. пункт 1.1 предыдущего аудита — заодно его и починим, потому что этот код станет тестовым).

---

## 3. Фронт

### 3.1. Стор

`frontend/src/stores/kinopub.ts`:
```ts
state:  { status: 'unknown' | 'disabled' | 'authenticated' | 'unauthenticated',
          deviceCode: string, userCode: string, verifyUri: string, expiresAt: number }
actions:
  - init()                  // GET /api/kinopub/status
  - startDeviceFlow()       // POST /api/kinopub/device/start
  - pollDeviceFlow()        // POST /api/kinopub/device/poll
  - logout()
  - search(title, year)
  - bind(itemId, kinopubId)
  - getStreamInfo(itemId)
```

### 3.2. UI

- **`AdminPanel.vue`** → новая секция «kino.pub» с device-flow модалкой: «Перейди на `https://kino.pub/device` и введи код `XXXX-YY`».
- **`ItemCardModal.vue`** → бейдж + кнопка «Найти на kino.pub» / «Открыть на kino.pub».
- **`PlayerModal.vue`** → вкладка «kino.pub» рядом с rezka, дропдаун качества/озвучки, список субтитров с `<track>`-ами в `<video>`.
- **`SyncPanel.vue`** → кнопка start/stop sync_kinopub.

### 3.3. Android-плеер deep-link

В `PlayerModal.vue` добавить дропдаун «Открыть в...»:

| Плеер | Схема URI |
|-------|-----------|
| VLC (Android) | `vlc://https%3A//cdn.kino.pub/...mp4` |
| MX Player | `intent:https://cdn.kino.pub/...mp4#Intent;package=com.mxtech.videoplayer.ad;type=video/mp4;S.title=Inception;end` |
| Kodi (Yatse) | `yatse://play?url=...` |
| Just Player | `intent:.../#Intent;package=com.brouken.player;...` |
| iOS Infuse | `infuse://x-callback-url/play?url=...` |
| iOS VLC | `vlc-x-callback://x-callback-url/stream?url=...` |

Субтитры передаём через `S.subs` (MX) или `S.subtitles` (Just Player). Не у всех плееров одинаковая схема — сделаем словарь шаблонов в `frontend/src/composables/useExternalPlayer.ts`.

Это всё **обычный `window.location.href = 'vlc://...'`** на нажатие кнопки. Никаких нативных приложений писать не нужно — Android-плееры регистрируют свои схемы в системе.

### 3.4. Браузерный плеер с субтитрами

В текущем `PlayerModal.vue` уже есть `<video>` через HLS.js — мы просто добавим `<track src="..." srclang="ru" kind="subtitles" default>` для каждого `subtitles[]` из ответа kino.pub. CORS — kino.pub отдаёт `.vtt`/`.srt` с правильными заголовками; если нет, проксируем через `/api/kinopub/subtitle_proxy?url=...` с allow-list (как `_SUBTITLE_HOST_ALLOWLIST` сейчас).

---

## 4. Безопасность

1. **Сабтитры через прокси с allow-list** — `cdn.service-kp.com`, `cdn.kino.pub` (нужно сверить точные хосты эмпирически).
2. **Stream URLs НЕ кэшировать в БД** — они короткоживущие и IP-locked. Кэшировать только `kinopub_id` и метаданные.
3. **`KINOPUB_CLIENT_SECRET` в env, не в БД**. В `.env.example` — пустые placeholders.
4. **Audit log** на привязку/отвязку kinopub_id и на bind/unbind токенов (без самих токенов в логе!).
5. **Rate-limit** на `/api/kinopub/device/start` — 5/час (защита от спама device-кодами).

---

## 5. Что нужно от тебя для старта работ

Подтверди, пожалуйста, прежде чем я начну писать код:

### A. Доступ к kino.pub API

Я могу:
- **(A1)** Использовать какую-то готовую `client_id/client_secret` пару от open-source клиента (есть в публичных репах — `proton/Kinopub` Roku-плагин и т.п.). Юридически серая зона, но работает «из коробки».
- **(A2)** Подождать, пока ты сам напишешь support@kino.pub и получишь свою пару. Чище, но потребует пару дней на ожидание.
- **(A3)** Использовать неофициальную «обходную» авторизацию через токен из браузера kino.pub (как делают некоторые мобильные клиенты-обёртки). Хрупко, ломается при апдейтах kino.pub.

### B. Объём работы

- **(B1)** Минимальный: только bind kinopub_id + кнопка-ссылка + просмотр в браузере. Без device-flow на сервере — токен вписываешь в env вручную. ≈ 1 PR.
- **(B2)** Средний: + device-flow в UI + fрбоновой матчер `sync_kinopub`. ≈ 3 PR.
- **(B3)** Полный: всё из плана выше + deep-links на Android-плееры + субтитры. ≈ 4-5 PR.

### C. «Своё приложение Android»

Я понял эту часть так: ты хочешь не писать нативное Android-приложение, а использовать **существующие** плееры (VLC/MX/Just Player) через intent-URI. Если же ты хотел реально нативное приложение — это совсем отдельный проект (Kotlin + Jetpack Compose + Media3), к parstur отношения почти не имеющий. Уточни.

---

## 6. Что НЕ войдёт в этот план

- Поддержка ТВ-каналов kino.pub (`/v1/tv-channels`). Не вижу пользы для трекера, можно докинуть отдельно.
- Голосование за видео (`POST /v1/items/{id}/vote`).
- Комментарии.
- Подгрузка постеров kino.pub в `items.poster_url` — у нас уже TMDB/Kinopoisk-постеры, добавлять третий источник без необходимости.
- Watchlist-синхронизация в обе стороны (kino.pub-избранное ↔ parstur-коллекции). Идея интересная, но это отдельный feature и отдельный риск (двунаправленная синхронизация всегда болит).

---

## 7. Альтернатива «без бэкенда»

Если интересует **минимум** усилий: можно сделать просто **frontend-only** кнопку «Открыть на kino.pub», которая делает поиск по `title + year` на самом kino.pub (через их UI-поиск) и открывает результат в новой вкладке. Без API, без OAuth, без stream-URL. Это ~2 часа работы, и оно полезно само по себе. Но смотреть в нашем встроенном плеере / отдавать через intent-URI на Android — для этого без API не обойтись.
