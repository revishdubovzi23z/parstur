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


def item_to_tmdb(item: dict) -> dict:
    """Format our database item dict to TMDB-compatible catalog card."""
    media_type = get_media_type(item.get("category_id"))
    title = item.get("title") or ""
    orig_title = item.get("original_title") or ""

    # Clean Russian/English multi-title splits
    clean_title = title.split(" / ")[0].split("/")[0].strip()

    poster = item.get("poster_url") or ""

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
    try:
        display_id = int(tmdb_id) if tmdb_id else int(item["id"])
    except (ValueError, TypeError):
        display_id = int(item["id"])

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
        "antigravity_id": item["id"],
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

    // 4. Настройки плагина
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
        version: '1.0.0',
        description: 'Ваши коллекции из Antigravity Tracker'
    }});
}})();
"""
    return Response(content=js_content, media_type="application/javascript")


@router.get("/api/lampa/collections")
def get_lampa_collections(request: Request):
    """Get list of collections with count and a random movie's cover info."""
    check_lampa_auth(request)

    collections = db.get_collections(include_system=True)
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
                # Select a random item
                valid_items = [it for it in items if it.get("poster_url") or it.get("tmdb_id")]
                chosen = random.choice(valid_items) if valid_items else random.choice(items)

                cover_media_type = get_media_type(chosen.get("category_id"))
                if chosen.get("tmdb_id"):
                    try:
                        cover_tmdb_id = int(chosen["tmdb_id"])
                    except (ValueError, TypeError):
                        pass
                cover_poster_url = chosen.get("poster_url") or ""

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

        results = [item_to_tmdb(dict(row)) for row in rows]

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
    results = [item_to_tmdb(it) for it in paginated_items]

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

    return item_to_tmdb(item)
