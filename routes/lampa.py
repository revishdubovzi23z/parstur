import math
import random

from fastapi import APIRouter, HTTPException, Request, Response

from db import db
from settings import settings

router = APIRouter()


def check_lampa_auth(request: Request):
    """Verify that Lampa integration is enabled and optional API key matches."""
    if not settings.lampa_enabled:
        raise HTTPException(status_code=403, detail="Lampa integration is disabled")
    if settings.lampa_api_key:
        hdr_key = request.headers.get("X-API-Key")
        query_key = request.query_params.get("key")
        if hdr_key != settings.lampa_api_key and query_key != settings.lampa_api_key:
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing API Key")


def get_media_type(category_id: int | None) -> str:
    """Map our category ID to TMDB media type."""
    if category_id in (4, 10, 16):
        return "tv"
    return "movie"


def item_to_tmdb(item: dict, base_url: str = "") -> dict:
    """Format our database item dict to TMDB-compatible catalog card."""
    media_type = get_media_type(item.get("category_id"))
    title = item.get("title") or ""
    orig_title = item.get("original_title") or ""

    # Clean Russian/English multi-title splits
    clean_title = title.split(" / ")[0].split("/")[0].strip()

    poster = item.get("poster_url") or ""
    if poster:
        if "image.tmdb.org" in poster:
            parts = poster.split("/t/p/")
            if len(parts) > 1:
                subparts = parts[1].split("/")
                if len(subparts) > 1:
                    poster = "/" + "/".join(subparts[1:])
        elif base_url:
            import urllib.parse

            poster = f"{base_url}/api/lampa/poster?url={urllib.parse.quote(poster)}"

    vote_avg = 0.0
    if item.get("user_rating"):
        vote_avg = float(item["user_rating"])
    elif item.get("imdb_rating"):
        vote_avg = float(item["imdb_rating"])
    elif item.get("kp_rating"):
        try:
            vote_avg = float(item["kp_rating"])
        except ValueError:
            pass

    year = item.get("year")
    date_str = f"{year}-01-01" if year else ""

    tmdb_id = item.get("tmdb_id")
    tmdb_id_valid = False
    try:
        if tmdb_id:
            display_id = int(tmdb_id)
            tmdb_id_valid = True
        else:
            display_id = int(item["id"])
    except (ValueError, TypeError):
        display_id = int(item["id"])

    internal_id = int(item["id"])

    return {
        "id": display_id,
        "media_type": media_type,
        "title": clean_title,
        "name": clean_title,
        "original_title": orig_title or clean_title,
        "original_name": orig_title or clean_title,
        "poster_path": poster,
        "backdrop_path": poster,  # fallback to poster
        "overview": item.get("description") or "",
        "vote_average": vote_avg,
        "release_date": date_str,
        "first_air_date": date_str,
        "antigravity_id": internal_id,
        "_antigravity_id": internal_id,
        "_tmdb_id_valid": tmdb_id_valid,
        "is_watched": bool(item.get("is_watched")),
        "user_rating": item.get("user_rating"),
    }


@router.get("/api/lampa/plugin.js")
def get_lampa_plugin(request: Request, key: str | None = None):
    """Dynamically serve the Lampa.mx JavaScript plugin, pre-baked with connection parameters."""
    if not settings.lampa_enabled:
        raise HTTPException(status_code=403, detail="Lampa integration is disabled")

    api_key = key or settings.lampa_api_key or ""
    base_url = str(request.base_url).rstrip("/")

    # JavaScript plugin content
    js_content = f"""(function () {{
    'use strict';

    var BASE = '{base_url}';
    var KEY  = '{api_key}';

    if (!BASE) BASE = Lampa.Storage.get('antigravity_base', '');
    if (!KEY)  KEY  = Lampa.Storage.get('antigravity_key',  '');

    function headers() {{
        return KEY ? {{ 'X-API-Key': KEY }} : {{}};
    }}

    function buildUrl(path) {{
        var url = BASE + path;
        if (KEY) {{
            url += (url.indexOf('?') >= 0 ? '&' : '?') + 'key=' + encodeURIComponent(KEY);
        }}
        return url;
    }}

    // 1. Регистрируем «Мои коллекции» в главном каталоге
    Lampa.Listener.follow('catalog', function (e) {{
        e.object.push({{
            title: '📚 Мои коллекции',
            source: 'antigravity',
            onMore: function (params, oncomplite, onerror) {{
                fetch(buildUrl('/api/lampa/collections'), {{ headers: headers() }})
                    .then(function (r) {{ return r.json(); }})
                    .then(function (data) {{
                        oncomplite((data.collections || []).map(function (c) {{
                            return {{
                                id: c.id,
                                title: c.name,
                                url: buildUrl('/api/lampa/collection/' + c.id)
                            }};
                        }}));
                    }})
                    .catch(onerror);
            }}
        }});
    }});

    // 2. Обработка запроса конкретной коллекции
    Lampa.Listener.follow('source', function (e) {{
        if (e.object.source !== 'antigravity') return;
        var page = e.object.page || 1;
        var url = e.object.url;
        url += (url.indexOf('?') >= 0 ? '&' : '?') + 'page=' + page;

        fetch(url, {{ headers: headers() }})
            .then(function (r) {{ return r.json(); }})
            .then(function (data) {{
                e.object.oncomplite(data);
            }})
            .catch(e.object.onerror);
    }});

    // 3. Добавление раздела поиска
    Lampa.Listener.follow('search', function (e) {{
        e.object.push({{
            title: '📚 Мои коллекции',
            source: 'antigravity',
            onMore: function (params, oncomplite, onerror) {{
                var query = encodeURIComponent(params.query || '');
                fetch(buildUrl('/api/lampa/search?q=' + query), {{ headers: headers() }})
                    .then(function (r) {{ return r.json(); }})
                    .then(oncomplite)
                    .catch(onerror);
            }}
        }});
    }});

    // 4. Добавляем в основное левое меню Lampa через DOM-инъекцию (как в проверенном TMDB плагине)
    function addMenuButton() {{
        if ($('.menu .menu__list li[data-action="antigravity_collections"]').length) return;

        var $button = $(
            '<li class="menu__item selector" data-action="antigravity_collections">' +
            '<div class="menu__ico">' +
            '<svg height="24" viewBox="0 0 24 24" width="24" xmlns="http://www.w3.org/2000/svg"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z" fill="currentColor"/></svg>' +
            '</div>' +
            '<div class="menu__text">Мои коллекции</div>' +
            '</li>'
        );

        $button.on('hover:enter', function () {{
            Lampa.Activity.push({{
                url: '',
                title: '📚 Мои коллекции',
                component: 'antigravity_collections',
                page: 1
            }});
        }});

        var $list = $('.menu .menu__list').eq(0);
        if ($list.length) $list.append($button);
    }}

    // Вспомогательный адаптер для папок-коллекций
    function adaptFolderToCard(folder) {{
        var name = folder.name;
        var count = folder.count != null ? folder.count : '';
        return {{
            source: 'antigravity',
            type: 'movie',
            id: 'antigravity_folder_' + folder.id,
            title: name,
            original_title: name,
            name: name,
            original_name: name,
            overview: count ? (count + ' шт.') : '',
            release_date: '',
            first_air_date: '',
            poster_path: folder.cover_poster_url || '',
            backdrop_path: folder.cover_poster_url || '',
            vote_average: 0,
            vote_count: 0,
            adult: false,
            genre_ids: [],
            popularity: 0,
            media_type: 'movie',
            _list_id: folder.id,
            _list_name: name
        }};
    }}

    // 5. Регистрируем кастомный компонент antigravity_collections (первый уровень — список папок)
    function foldersComponent(object) {{
        var comp = new Lampa.InteractionCategory(object);

        comp.create = function () {{
            var self = this;
            this.activity.loader(true);

            fetch(buildUrl('/api/lampa/collections'), {{ headers: headers() }})
                .then(function (r) {{ return r.json(); }})
                .then(function (data) {{
                    self.activity.loader(false);
                    var collections = (data && data.collections) || [];
                    if (collections.length) {{
                        var cards = collections.map(adaptFolderToCard);
                        self.build({{
                            results: cards,
                            total_pages: 1,
                            page: 1
                        }});
                    }} else {{
                        self.empty();
                    }}
                }})
                .catch(function (err) {{
                    self.activity.loader(false);
                    self.empty('Не удалось загрузить коллекции');
                }});
        }};

        comp.nextPageReuest = function (obj, resolve, reject) {{
            resolve.call(comp, {{ results: [], total_pages: 1, page: 1 }});
        }};
        comp.nextPageRequest = comp.nextPageReuest;

        comp.cardRender = function (obj, element, card) {{
            card.onMenu = false;
            card.onEnter = function () {{
                Lampa.Activity.push({{
                    url: '',
                    title: element._list_name || element.title,
                    component: 'antigravity_collection_content',
                    list_id: element._list_id,
                    page: 1
                }});
            }};
        }};

        return comp;
    }}

    // Кастомный компонент для содержимого папки (второй уровень)
    function folderContentComponent(object) {{
        var comp = new Lampa.InteractionCategory(object);

        function loadPage(page, resolve, reject) {{
            if (!object.list_id) {{
                reject.call(comp);
                return;
            }}

            var url = buildUrl('/api/lampa/collection/' + object.list_id);
            url += (url.indexOf('?') >= 0 ? '&' : '?') + 'page=' + page;

            fetch(url, {{ headers: headers() }})
                .then(function (r) {{ return r.json(); }})
                .then(function (data) {{
                    var items = ((data && data.results) || [])
                        .filter(function (c) {{ return c && c.id != null; }});

                    resolve.call(comp, {{
                        results: items,
                        page: page,
                        total_pages: (data && data.total_pages) || 1
                    }});
                }})
                .catch(function (err) {{
                    reject.call(comp);
                }});
        }}

        comp.create = function () {{
            loadPage(object.page || 1, this.build.bind(this), this.empty.bind(this));
        }};

        comp.nextPageReuest = function (obj, resolve, reject) {{
            loadPage(obj.page, resolve, reject);
        }};
        comp.nextPageRequest = comp.nextPageReuest;

        comp.cardRender = function (obj, element, card) {{
            card.onMenu = false;
            card.onEnter = function () {{
                // Критично: используем TMDB id только если он реально существует.
                // Если _tmdb_id_valid === false, element.id — это наш внутренний ID,
                // и открытие через source:'tmdb' откроет СОВСЕМ ДРУГОЙ фильм с тем же номером.
                if (element._tmdb_id_valid) {{
                    Lampa.Activity.push({{
                        url: '',
                        component: 'full',
                        id: element.id,
                        method: element.media_type === 'tv' ? 'tv' : 'movie',
                        source: 'tmdb',
                        card: element
                    }});
                }} else {{
                    // Нет TMDB ID — показываем предупреждение вместо открытия неверного фильма
                    Lampa.Noty.show('Для "' + (element.title || element.name) + '" нет ID TMDB — синхронизируйте библиотеку');
                }}
            }};
        }};

        return comp;
    }}

    // Инициализация плагина
    function bootstrap() {{
        Lampa.Component.add('antigravity_collections', foldersComponent);
        Lampa.Component.add('antigravity_collection_content', folderContentComponent);

        if (window.appready) {{
            addMenuButton();
        }} else {{
            Lampa.Listener.follow('app', function (e) {{
                if (e.type === 'ready') addMenuButton();
            }});
        }}
    }}

    bootstrap();

    // 6. Настройки плагина
    Lampa.SettingsApi.addParam({{
        component: 'antigravity',
        param: {{
            name: 'antigravity_base',
            type: 'input',
            default: BASE,
            placeholder: 'http://your-vps:8000'
        }},
        field: {{
            name: 'Antigravity: URL сервера'
        }}
    }});

    Lampa.SettingsApi.addParam({{
        component: 'antigravity',
        param: {{
            name: 'antigravity_key',
            type: 'input',
            default: KEY,
            placeholder: 'API Key'
        }},
        field: {{
            name: 'Antigravity: API Ключ'
        }}
    }});

    Lampa.Manifest.plugins = Lampa.Manifest.plugins || [];
    Lampa.Manifest.plugins.push({{
        name: 'Antigravity Tracker',
        version: '1.1.0',
        description: 'Ваши коллекции из Antigravity Tracker'
    }});
}})();
"""
    return Response(content=js_content, media_type="application/javascript")


@router.get("/api/lampa/poster")
async def proxy_lampa_poster(url: str):
    """Proxy external images (like Rezka) to bypass CORS and referer locks on TVs."""
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    media_type=resp.headers.get("content-type", "image/jpeg"),
                )
    except Exception:
        pass
    return Response(content=b"", status_code=404)


@router.get("/api/lampa/collections")
def get_lampa_collections(request: Request):
    """Get list of collections with count and a random movie's cover info."""
    check_lampa_auth(request)

    collections = db.get_collections(include_system=False)
    res_cols = []

    with db._conn() as conn:
        for col in collections:
            # Fetch all items in this collection
            items = conn.execute(
                """
                SELECT i.id, i.tmdb_id, i.category_id, i.poster_url
                FROM items i
                JOIN collection_items ci ON i.id = ci.item_id
                WHERE ci.collection_id = ?
                """,
                (col["id"],),
            ).fetchall()

            items = [dict(it) for it in items]

            cover_tmdb_id = None
            cover_media_type = "movie"
            cover_poster_url = ""

            if items:
                # Select a random item that actually has a poster_url
                valid_items = [it for it in items if it.get("poster_url")]
                chosen = random.choice(valid_items) if valid_items else random.choice(items)

                cover_media_type = get_media_type(chosen.get("category_id"))
                if chosen.get("tmdb_id"):
                    try:
                        cover_tmdb_id = int(chosen["tmdb_id"])
                    except (ValueError, TypeError):
                        pass
                poster_url = chosen.get("poster_url") or ""
                if poster_url:
                    if "image.tmdb.org" in poster_url:
                        parts = poster_url.split("/t/p/")
                        if len(parts) > 1:
                            subparts = parts[1].split("/")
                            if len(subparts) > 1:
                                cover_poster_url = "/" + "/".join(subparts[1:])
                    else:
                        base_url = str(request.base_url).rstrip("/")
                        import urllib.parse

                        cover_poster_url = (
                            f"{base_url}/api/lampa/poster?url={urllib.parse.quote(poster_url)}"
                        )

            res_cols.append(
                {
                    "id": col["id"],
                    "name": col["name"],
                    "count": len(items),
                    "cover_tmdb_id": cover_tmdb_id,
                    "cover_media_type": cover_media_type,
                    "cover_poster_url": cover_poster_url,
                }
            )

    return {"collections": res_cols}


@router.get("/api/lampa/collection/{collection_id}")
def get_lampa_collection_items(collection_id: int, request: Request, page: int = 1):
    """Retrieve items from a specific collection, converted to TMDB format."""
    check_lampa_auth(request)

    limit = 20
    offset = (page - 1) * limit

    with db._conn() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*)
            FROM items i
            JOIN collection_items ci ON i.id = ci.item_id
            WHERE ci.collection_id = ?
            """,
            (collection_id,),
        ).fetchone()[0]

        rows = conn.execute(
            """
            SELECT i.*
            FROM items i
            JOIN collection_items ci ON i.id = ci.item_id
            WHERE ci.collection_id = ?
            ORDER BY ci.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (collection_id, limit, offset),
        ).fetchall()

        base_url = str(request.base_url).rstrip("/")
        results = [item_to_tmdb(dict(row), base_url) for row in rows]

    total_pages = math.ceil(total / limit) if total > 0 else 1

    return {
        "page": page,
        "results": results,
        "total_pages": total_pages,
        "total_results": total,
    }


@router.get("/api/lampa/search")
def search_lampa(q: str, request: Request, page: int = 1):
    """Search our collections database for items and return results in TMDB format."""
    check_lampa_auth(request)

    limit = 20
    offset = (page - 1) * limit

    where_clause = "title LIKE ? OR original_title LIKE ? OR description LIKE ?"
    param = f"%{q}%"
    params = (param, param, param)

    items = db.get_items(where_clause=where_clause, params=params)
    total = len(items)

    paginated_items = items[offset : offset + limit]
    base_url = str(request.base_url).rstrip("/")
    results = [item_to_tmdb(it, base_url) for it in paginated_items]

    total_pages = math.ceil(total / limit) if total > 0 else 1

    return {
        "page": page,
        "results": results,
        "total_pages": total_pages,
        "total_results": total,
    }


@router.get("/api/lampa/item/{item_id}")
def get_lampa_item(item_id: int, request: Request):
    """Get single movie/show by internal id in TMDB format."""
    check_lampa_auth(request)

    item = db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    base_url = str(request.base_url).rstrip("/")
    return item_to_tmdb(item, base_url)
