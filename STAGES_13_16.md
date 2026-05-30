# Этапы 13–16: Cloud Sync, Прокси, Lampa, Оценки

> Обновлено с учётом уточнений: Lampa.mx (не LAMP), автоматический sync, VLESS+SOCKS5.

---

## Этап 13 — ☁️ Cloud Sync (Turso / libSQL)

### Почему Turso, а не Supabase?

| | Turso | Supabase | Cloudflare R2 |
|---|---|---|---|
| **Тип** | libSQL (SQLite-совместимый) | PostgreSQL | Object storage (файл) |
| **Совместимость** | ✅ Прямая (наш код почти не меняется) | ❌ Нужна полная миграция схемы | ⚠️ Только backup-файл, не live-sync |
| **Бесплатно** | 5 ГБ, 500 баз | 500 МБ, 2 проекта | 10 ГБ |
| **Sync** | ✅ Встроенный `push/pull` через libSQL | ❌ Не поддерживает SQLite | ❌ Ручная загрузка/скачивание |
| **Latency** | Работает с локальным файлом | Только network | Только network |

**Вывод**: Turso — идеальный выбор для нашего SQLite-приложения. Код почти не меняется (заменяем `sqlite3.connect` на `turso.connect`).

### Как работает синхронизация

```
VPS (работает постоянно):
  app_data.db (локальный SQLite) ──auto push──► Turso Cloud (облако)
       ▲                                               │
  пишет при каждом изменении                          │
                                                      │
Ноутбук (запускаешь иногда):                          │
  при старте ──auto pull──◄─────────────────────────────┘
  app_data.db (локальная копия) ─► работает локально
```

**Полностью автоматически** — никакого ручного push/pull. Только при первом запуске на новой машине надо настроить токен в `.env`.

### Настройка (один раз)

```bash
# 1. Создать аккаунт на turso.tech (бесплатно)
# 2. Установить Turso CLI
curl -sSfL https://get.tur.so/install.sh | bash
# 3. Создать базу
turso db create antigravity-tracker
# 4. Получить URL и токен
turso db show antigravity-tracker --url
turso db tokens create antigravity-tracker
# 5. Добавить в .env
CLOUD_PROVIDER=turso
CLOUD_TURSO_URL=libsql://antigravity-tracker-xxx.turso.io
CLOUD_TURSO_TOKEN=eyJ...
```

### Предлагаемые изменения

---

#### [NEW] `cloud_sync.py`
```python
import turso  # pip install pyturso

class CloudSync:
    def push(self) -> bool:
        """Синхронизировать локальные изменения в облако."""

    def pull(self) -> bool:
        """Скачать изменения из облака."""

    def get_status(self) -> dict:
        """Вернуть статус: last_push, last_pull, provider."""
```

#### [MODIFY] `settings.py`
```python
class _CloudSettings(BaseSettings):
    cloud_provider: Literal["none", "turso"] = "none"
    cloud_turso_url: str | None = None
    cloud_turso_token: str | None = None
    cloud_sync_on_startup: bool = True   # pull при старте
    cloud_sync_after_job: bool = True    # push после каждого job'а
    cloud_sync_interval_minutes: int = 15  # фоновый push каждые N минут
```

#### [MODIFY] `main.py` (lifespan)
- `startup`: если `cloud_sync_on_startup` → `cloud_sync.pull()`
- После завершения каждого job'а → `cloud_sync.push()` (хук в `task_queue`)
- Фоновая задача: push каждые `cloud_sync_interval_minutes`

#### [NEW] `routes/cloud.py`
```
GET  /api/cloud/status    — провайдер, last_push, last_pull, статус
POST /api/cloud/push      — ручной принудительный push
POST /api/cloud/pull      — ручной pull (с confirmation в UI)
```

#### [NEW] `migrations/0007_cloud_sync_log.sql`
```sql
CREATE TABLE IF NOT EXISTS cloud_sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    direction   TEXT NOT NULL,  -- 'push' | 'pull'
    provider    TEXT NOT NULL,
    status      TEXT NOT NULL,  -- 'ok' | 'error'
    bytes       INTEGER,
    error_msg   TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
PRAGMA user_version = 7;
```

#### [MODIFY] Фронт — настройки
- Раздел «☁️ Облако» с индикатором статуса
- `last push: 2 мин назад ✅` / `ошибка: нет соединения ❌`
- Кнопки «Sync сейчас» и «Pull (перезаписать локальное)»

---

## Этап 14 — 🔒 Proxy Support (VLESS + SOCKS5/HTTP)

### Концепция

Каждый сервис может иметь свой прокси:

```
HDRezka  → vless://...@your-vps:443?...  (парсим ссылку → запускаем через xray)
KinoPub  → без прокси (прямой)
TMDB     → socks5://127.0.0.1:1080
Rutor    → http://proxy.example.com:8080
```

### Насчёт VPS — что уже может быть установлено

На VPS часто уже стоят `xray-core` or `sing-box` (3x-ui, x-ui, marzban, remunage и т.д.). Наш код **автоматически обнаруживает** их:

```python
XRAY_CANDIDATES = ["xray", "/usr/local/bin/xray", "sing-box", "/usr/bin/sing-box"]
# Пробуем каждый, используем первый рабочий
```

Если ничего не найдено — при первом использовании VLESS приложение предупредит в логах и UI: «xray-core не найден. Установите: apt install xray».

**SOCKS5/HTTP прокси не требуют никаких бинарников** — работают напрямую через `httpx`.

### Порядок работы для VLESS

```
1. Пользователь вставляет:  vless://uuid@host:port?security=tls&...
2. Приложение парсит URL → конфиг xray
3. Запускает xray локально на свободном порту (напр. 10800)
4. httpx в клиентах подключается через socks5://127.0.0.1:10800
5. При остановке приложения → xray тоже останавливается
```

### Предлагаемые изменения

---

#### [NEW] `proxy_manager.py`
```python
class ProxyManager:
    async def get_httpx_proxy(self, service: str) -> str | None:
        """Вернуть URL прокси для httpx (или None)."""

    def parse_vless_url(self, url: str) -> dict:
        """Разобрать vless://... в dict конфигурации."""

    async def ensure_xray_running(self, vless_url: str) -> int:
        """Запустить xray sidecar если нужно, вернуть локальный порт."""

    def detect_xray_binary(self) -> str | None:
        """Найти xray/sing-box в системе."""

    async def test_proxy(self, service: str) -> dict:
        """Проверить связь через прокси, вернуть latency."""

    async def stop_all(self) -> None:
        """Остановить все xray sidecar процессы."""
```

#### [MODIFY] `settings.py`
```python
class _ProxySettings(BaseSettings):
    proxy_rezka: str | None = None       # vless://... | socks5://... | http://...
    proxy_kinopub: str | None = None
    proxy_rutor: str | None = None
    proxy_tmdb: str | None = None
    proxy_kinopoisk: str | None = None
    proxy_poiskkino: str | None = None
    xray_binary: str = "xray"           # или путь вручную
    xray_port_base: int = 10800         # 10800, 10801, ... для каждого уникального proxy
```

#### [MODIFY] `base_client.py`
```python
async def _get_client(self):
    proxy_url = await proxy_manager.get_httpx_proxy(self.service_name)
    return httpx.AsyncClient(proxy=proxy_url, ...)
```

#### [MODIFY] Все `*_client.py`
Добавить `service_name = "rezka"` / `"kinopub"` / `"tmdb"` и т.д.

#### [NEW] `routes/proxy.py`
```
GET  /api/proxy/status           — список сервисов + их текущий прокси + статус
POST /api/proxy/test/{service}   — проверить прокси (latency, ip-check)
POST /api/proxy/reload           — перечитать настройки из .env
```

#### [MODIFY] Фронт — настройки
- Раздел «🔒 Прокси» с полем на каждый сервис
- Подсказка: «Вставь vless://, socks5://, http:// или оставь пустым»
- Кнопка «🔍 Тест» рядом с каждым полем → показывает пинг и IP-адрес через прокси
- Статус-индикатор: 🟢 / 🔴 / ⚪ (не настроен)

---

## Этап 15 — 📺 Lampa.mx Catalog Plugin

### Как это работает (правильное понимание)

Lampa — TV-интерфейс, похожий на красивый каталог с карточками. По умолчанию использует TMDB для данных. **Наша задача** — создать каталог-плагин, который:

1. Добавляет в главное меню Lampa раздел **«📚 Мои коллекции»**
2. Внутри — твои папки из Antigravity: «Хочу посмотреть», «Смотрю», «Просмотренное» и т.д.
3. Внутри каждой папки — **фильмы и сериалы в формате TMDB** (по `tmdb_id`/`imdb_id` из нашей БД)
4. Lampa сам строит красивые карточки со своими шаблонами
5. **Что смотреть** — решает уже другой плагин (Collaps, Rezka-плагин и т.д.). Наш плагин только отдаёт список.

```
TV с Lampa:

  [Главное меню]                         [Наш раздел — список папок]
  ├── Топ TMDB                           ┌──────────────────────────────────────
  ├── Кино                               │ 📚 Мои коллекции
  ├── Сериалы                            │
  └── 📚 Мои коллекции  ──────────────►  │  [🎬 Хочу посмотреть]  [👁 Смотрю]
                                         │   обложка = случайный   обложка =
                                         │   бэкдроп из папки      случайный
                                         │   (Дюна 2024)            бэкдроп
                                         │
                                         │  [✅ Просмотрено]  [🌟 Избранное]
                                         └──────────────────────────────────────

  [Открыл «Хочу посмотреть»]
  ┌──────────────────────────────────────────────────────────────
  │  Карточки — постер + описание + трейлер БЕРУТСЯ С TMDB
  │  (мы передаём только tmdb_id, остальное Lampa тянет сама)
  │
  │  [Дюна 2024]   [Оппенгеймер 2023]   [Ла-Ла Ленд 2016]
  │   ████████       ████████████         ███████████████
  │   постер с       постер с TMDB        постер с TMDB
  │   TMDB           8.9 ⭐               8.0 ⭐
  │   8.3 ⭐
  │
  └──────────────────────────────────────────────────────────────
  ← мы отдаём: tmdb_id (ID в TMDB) + media_type (movie/tv)
    Lampa по tmdb_id сама берёт: постер, бэкдроп, описание, трейлер
```

### Ключевой момент: что мы отдаём и что делает Lampa

**Мы отдаём минимум** — только идентификаторы и базовые данные. **Lampa делает остальное** сама через TMDB.

#### Формат карточки фильма (в `results[]`)

```json
{
  "id": 157336,
  "media_type": "movie",
  "antigravity_id": 1234
}
```

Только `tmdb_id` как `id` + `media_type` — и Lampa по ним сама загружает с TMDB:
- 🖼️ Постер (`poster_path`)
- 🌄 Бэкдроп (`backdrop_path`)
- 📝 Описание (`overview`)
- 🎬 Трейлер
- ⭐ Рейтинг TMDB
- Жанры, актёры, год

> **Fallback**: если `tmdb_id` нет — отдаём наши данные: `poster_url` напрямую, `vote_average = imdb_rating`, `overview = description`.

#### Формат списка коллекций (`/api/lampa/collections`)

Каждая коллекция (папка) получает **обложку = бэкдроп случайного фильма из этой папки**:

```json
{
  "collections": [
    {
      "id": 3,
      "name": "Хочу посмотреть",
      "count": 42,
      "cover_tmdb_id": 157336,
      "cover_media_type": "movie",
      "cover_poster_url": "https://image.tmdb.org/t/p/w780/..."
    }
  ]
}
```

Как формируется `cover_*`:
1. Берём случайный фильм из коллекции у которого есть `tmdb_id`
2. Отдаём его `tmdb_id` как `cover_tmdb_id`
3. Plugin.js в Lampa по нему запрашивает у TMDB бэкдроп и ставит его как обложку папки
4. Fallback: если ни у кого нет `tmdb_id` — берём `poster_url` любого фильма

### Предлагаемые изменения

---

#### [NEW] `routes/lampa.py`
Backend API для Lampa-плагина. **CORS открыт**, без сессионной авторизации (только опциональный API-ключ):

```
GET /api/lampa/plugin.js              — отдаёт JS-плагин (Content-Type: application/javascript)
GET /api/lampa/collections            — список папок [{id, name, count}, ...]
GET /api/lampa/collection/{id}        — фильмы папки в TMDB-формате (пагинация ?page=1)
GET /api/lampa/search?q=...           — поиск по нашей БД, ответ в TMDB-формате
GET /api/lampa/item/{id}              — карточка одного фильма по our internal id
```

#### [NEW] `lampa_plugin.js` (хранить в `static/` или прямо в `routes/lampa.py` как строку)

JS-файл регистрирует **Catalog Source** в Lampa:

```javascript
(function () {
    'use strict';

    // URL бэкенда — пользователь настраивает в plugin-настройках Lampa
    var BASE = Lampa.Storage.get('antigravity_base', '');
    var KEY  = Lampa.Storage.get('antigravity_key',  '');

    function headers() {
        return KEY ? { 'X-API-Key': KEY } : {};
    }

    // 1. Регистрируем «источник каталога» в Lampa
    Lampa.Listener.follow('catalog', function (e) {
        // Добавляем нашу кнопку в меню
        e.object.push({
            title: '📚 Мои коллекции',
            source: 'antigravity',
            onMore: function (params, oncomplite, onerror) {
                // Lampa нажала «ещё» — загружаем коллекции
                fetch(BASE + '/api/lampa/collections', { headers: headers() })
                    .then(r => r.json())
                    .then(data => {
                        // data = [{id, name, count}, ...]
                        // Превращаем папки в «жанры» Lampa
                        oncomplite(data.collections.map(c => ({
                            id: c.id,
                            title: c.name,
                            url: BASE + '/api/lampa/collection/' + c.id
                        })));
                    })
                    .catch(onerror);
            }
        });
    });

    // 2. Обработка запроса конкретной коллекции
    Lampa.Listener.follow('source', function (e) {
        if (e.object.source !== 'antigravity') return;
        var url = e.object.url + '?page=' + (e.object.page || 1);
        fetch(url, { headers: headers() })
            .then(r => r.json())
            .then(data => e.object.oncomplite(data))  // уже в TMDB-формате
            .catch(e.object.onerror);
    });

    // 3. Настройки плагина (URL бэкенда + ключ)
    Lampa.SettingsApi.addParam({
        component: 'antigravity',
        param: { name: 'antigravity_base', type: 'input',
                 default: '', placeholder: 'http://your-vps:8000' },
        field: { name: 'Antigravity: URL сервера' }
    });

    Lampa.Manifest.plugins = Lampa.Manifest.plugins || [];
    Lampa.Manifest.plugins.push({
        name: 'Antigravity Tracker',
        version: '1.0.0',
        description: 'Ваши коллекции из Antigravity Tracker'
    });
})();
```

#### [MODIFY] `settings.py`
```python
class _LampaSettings(BaseSettings):
    lampa_enabled: bool = True
    lampa_api_key: str | None = None  # None = без авторизации (только для домашнего VPS)
```

#### [MODIFY] `main.py`
- Подключить `routes/lampa.py`
- Добавить `add_middleware(CORSMiddleware, allow_origins=["*"])` для `/api/lampa/*`

#### Нюансы реализации

| Ситуация | Решение |
|---|---|
| У фильма есть `tmdb_id` | Отдаём `{id: tmdb_id, media_type}` → Lampa сама тянет постер/бэкдроп/трейлер/описание с TMDB |
| У фильма нет `tmdb_id` | Отдаём `{id: antigravity_id, poster_path: poster_url, overview: description, vote_average: imdb_rating}` |
| Сериал vs фильм | `media_type: "tv"` для сериальных категорий, `"movie"` для остальных |
| Обложка папки | Случайный `tmdb_id` из коллекции → плагин запрашивает бэкдроп у TMDB и ставит как обложку |
| Папка без `tmdb_id` | `poster_url` любого фильма как обложка |
| Пагинация | `?page=N`, 20 элементов на страницу, возвращаем `total_pages` + `total_results` |
| CORS | Открытый CORS для `/api/lampa/*` чтобы Lampa могла запрашивать с TV |

#### [MODIFY] Фронт — настройки
- Раздел «📺 Lampa»
- URL плагина: `https://твой-впс:8000/api/lampa/plugin.js` с кнопкой «Копировать»
- QR-код (через `qrcode` Python-библиотеку) для удобного ввода с телевизора
- Поля: включить/выключить, API-ключ (если хочешь защиту)

---

## Этап 16 — ⭐ Оценки + Просмотрено + Экспорт

### Концепция

1. **Оценка** — звёздочки 1–10 на карточке, сохраняется в `items.user_rating`
2. **Просмотрено** — кнопка на карточке → `is_watched = 1` + `watched_at`
3. **Список «Просмотренное»** — отдельная встроенная коллекция (создаётся автоматически), туда попадает фильм после нажатия «Просмотрено»
4. **Экспорт** — возможность выгрузить список просмотренного в форматы для IMDb, TMDB, Кинопоиска

### Формат экспорта

```
IMDb:  CSV (Const, Your Rating, Date Rated, Title, URL, Title Type, IMDb Rating, Runtime, Year, Genres, Num Votes, Release Date, Directors)
TMDB:  Нет официального импорта CSV, но есть через Trakt → TMDB
Кинопоиск: JSON или CSV (kp_id, title, year, user_rating, watched_date)
Trakt: JSON (movies/shows watchlist + history)
```

### Предлагаемые изменения

---

#### [NEW] `migrations/0009_user_watched.sql`
```sql
-- Убедиться что поля есть (is_watched уже есть из migration 0006)
ALTER TABLE items ADD COLUMN watched_at TEXT;
-- Встроенная системная коллекция «Просмотренное»
INSERT OR IGNORE INTO collections (name, sort_order, is_system)
VALUES ('Просмотренное', 9999, 1);
PRAGMA user_version = 9;
```

Также нужно добавить `is_system INTEGER DEFAULT 0` в `collections`:
```sql
ALTER TABLE collections ADD COLUMN is_system INTEGER DEFAULT 0;
```

#### [MODIFY] `routes/items.py`
```python
class RateRequest(BaseModel):
    rating: int | None  # 1–10 или None

class WatchedRequest(BaseModel):
    watched: bool

@router.post("/api/item/{id}/rate")
async def rate_item(id: int, data: RateRequest): ...

@router.post("/api/item/{id}/watched")
async def mark_watched(id: int, data: WatchedRequest): ...
```

#### [NEW] `routes/export.py`
```
GET /api/export/watched/imdb     → CSV в формате IMDb
GET /api/export/watched/tmdb     → JSON для импорта через Trakt
GET /api/export/watched/kp       → CSV для Кинопоиска
GET /api/export/watched/trakt    → Trakt JSON history format
GET /api/export/ratings/imdb     → CSV рейтингов для IMDb
```

#### [MODIFY] `db/items.py`
```python
def mark_watched(self, item_id: int, watched: bool) -> None:
    """Установить is_watched + watched_at + добавить в коллекцию Просмотренное."""

def get_watched_export(self) -> list[dict]:
    """Вернуть все просмотренные фильмы с imdb_id, kp_id, watched_at."""
```

#### [MODIFY] Фронт — ItemCard
- 🌟 Звёздочки 1–10 с hover-анимацией (клик — сохраняет оценку)
- ✅ Кнопка «Просмотрено» → анимация → карточка исчезает из списка
- Если `is_watched`: показывать `watched_at` и кнопку «↩ Отметить как непросмотренное»

#### [MODIFY] Фронт — страница настроек / отдельный экран
- «📤 Экспорт» с кнопками:
  - «Скачать IMDb CSV» (рейтинги)
  - «Скачать список просмотренного (IMDb)»
  - «Скачать для Кинопоиска»
  - «Скачать Trakt JSON»

---

## Порядок реализации

| # | Этап | Сложность | Зависимости | Старт |
|---|------|-----------|-------------|-------|
| **16** | ⭐ Оценки + Просмотрено + Экспорт | Низкая | Нет | 🔴 **Первый** |
| **15** | 📺 Lampa.mx Plugin | Средняя | Нет | 🟠 Второй |
| **13** | ☁️ Cloud Sync | Средняя | Нет | 🟡 Третий |
| **14** | 🔒 Proxy | Высокая | Нет | 🟢 Четвёртый |

> [!TIP]
> Начнём с **Этапа 16** — максимальная ценность при минимальном риске. Backend наполовину готов (поле `is_watched` уже есть), нужно только фронт и экспорт.

---

## Требования к окружению

### Этап 13 (Turso)
```bash
pip install pyturso
# + аккаунт на turso.tech (бесплатно)
```

### Этап 14 (Proxy)
- **SOCKS5/HTTP**: ничего не нужно устанавливать дополнительно
- **VLESS**: нужен `xray-core` или `sing-box` (проверяем что уже есть на VPS):
  ```bash
  which xray sing-box  # если что-то вернёт — уже установлено
  ```
  Если не установлено: `bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install`

### Этапы 15, 16
Только Python-зависимости, ничего нового устанавливать не нужно.
