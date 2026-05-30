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

    # Poster handling (hybrid):
    #  - TMDB posters: keep a relative poster_path (native TMDB path) AND expose a
    #    full CDN url in "img" so the card can load it directly.
    #  - Non-TMDB posters: proxy through our server (CORS/referer) and expose the
    #    proxy url in "img"; leave poster_path empty so Lampa does not prepend the
    #    TMDB base to an absolute url.
    raw_poster = item.get("poster_url") or ""
    poster = raw_poster
    img = ""
    if raw_poster:
        if "image.tmdb.org" in raw_poster:
            img = raw_poster
            parts = raw_poster.split("/t/p/")
            if len(parts) > 1:
                subparts = parts[1].split("/")
                if len(subparts) > 1:
                    poster = "/" + "/".join(subparts[1:])
        elif base_url:
            import urllib.parse

            img = f"{base_url}/api/lampa/poster?url={urllib.parse.quote(raw_poster)}"
            poster = ""
        else:
            img = raw_poster
            poster = ""

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
        "img": img,
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

    # Plugin source. Written as a plain template (no f-string) to avoid brace
    # escaping; connection params are injected via str.replace below.
    js_template = """(function () {
    'use strict';

    var BASE = '__BASE__';
    var KEY  = '__KEY__';

    if (!BASE) BASE = Lampa.Storage.get('antigravity_base', '');
    if (!KEY)  KEY  = Lampa.Storage.get('antigravity_key', '');

    function reqHeaders() {
        return KEY ? { 'X-API-Key': KEY } : {};
    }

    function buildUrl(path) {
        var url = (BASE || '') + path;
        if (KEY) url += (url.indexOf('?') >= 0 ? '&' : '?') + 'key=' + encodeURIComponent(KEY);
        return url;
    }

    function api(path, onok, onerr) {
        var full = /^https?:/.test(path) ? path : buildUrl(path);
        fetch(full, { headers: reqHeaders() })
            .then(function (r) { return r.json(); })
            .then(function (json) { onok(json); })
            .catch(function (e) { if (onerr) onerr(e); });
    }

    // Custom poster card based on the native 'card' template. We set the image
    // src directly from element.img (a full URL), which works for both native
    // TMDB CDN urls and our proxy urls, and gives the native poster grid (3/row).
    function PosterCard(data) {
        var self = this;

        this.build = function () {
            var node = Lampa.Template.js('card');
            this.card = (node && node.querySelector) ? node : (node && node[0]) ? node[0] : node;

            this.img = this.card.querySelector('.card__img');

            var titleEl = this.card.querySelector('.card__title');
            if (titleEl) titleEl.innerText = data.title || data.name || '';

            var typeEl = this.card.querySelector('.card__type');
            if (typeEl) {
                if (data.is_folder) {
                    typeEl.style.display = '';
                    typeEl.innerText = (data.items_count != null ? ('' + data.items_count) : '');
                } else if (data.media_type === 'tv') {
                    typeEl.style.display = '';
                    typeEl.innerText = 'TV';
                }
            }

            var voteEl = this.card.querySelector('.card__vote');
            if (voteEl) {
                if (!data.is_folder && data.vote_average) {
                    voteEl.innerText = parseFloat(data.vote_average).toFixed(1);
                } else {
                    voteEl.style.display = 'none';
                }
            }

            this.card.addEventListener('visible', this.visible.bind(this));
        };

        this.visible = function () {
            var im = this.img;
            if (im) {
                im.onload = function () { if (self.card) self.card.classList.add('card--loaded'); };
                im.onerror = function () { im.src = './img/img_broken.svg'; };
                im.src = data.img || './img/img_broken.svg';
            }
            if (this.onVisible) this.onVisible(this.card, data);
        };

        this.create = function () {
            this.build();

            this.card.addEventListener('hover:focus', function () { if (self.onFocus) self.onFocus(self.card, data); });
            this.card.addEventListener('hover:hover', function () { if (self.onHover) self.onHover(self.card, data); });
            this.card.addEventListener('hover:long',  function () { if (self.onLong)  self.onLong(self.card, data); });
            this.card.addEventListener('hover:enter', function () { if (self.onEnter) self.onEnter(self.card, data); });
        };

        this.render = function (js) { return js ? this.card : $(this.card); };

        this.destroy = function () {
            if (this.img) { this.img.onload = null; this.img.onerror = null; this.img.src = ''; }
            if (this.card && this.card.remove) this.card.remove();
            this.img = null;
            this.card = null;
        };
    }

    // Level 1: list of collections (folders).
    function collectionsComponent(object) {
        var comp = new Lampa.InteractionCategory(object);

        comp.create = function () {
            var that = this;
            api('/api/lampa/collections', function (data) {
                var cols = (data && data.collections) || [];
                var results = cols.map(function (c) {
                    return {
                        id: c.id,
                        title: c.name,
                        name: c.name,
                        items_count: c.count,
                        img: c.cover_img || '',
                        is_folder: true
                    };
                });
                that.build({
                    results: results,
                    total_pages: 1,
                    cardClass: function (elem) { return new PosterCard(elem); }
                });
            }, function () { that.empty(); });
        };

        comp.nextPageReuest = function (object, resolve, reject) {
            resolve({ results: [], total_pages: 1 });
        };
        comp.nextPageRequest = comp.nextPageReuest;

        comp.cardRender = function (object, element, card) {
            card.onMenu = false;
            card.onEnter = function () {
                Lampa.Activity.push({
                    url: '',
                    title: element.title,
                    component: 'antigravity_collection_content',
                    list_id: element.id,
                    page: 1
                });
            };
        };

        return comp;
    }

    // Level 2: items inside a collection.
    function collectionContentComponent(object) {
        var comp = new Lampa.InteractionCategory(object);

        function load(page, resolve, reject) {
            api('/api/lampa/collection/' + object.list_id + '?page=' + page, function (data) {
                data = data || {};
                data.results = data.results || [];
                data.cardClass = function (elem) { return new PosterCard(elem); };
                resolve(data);
            }, function () { reject(); });
        }

        comp.create = function () {
            load(1, this.build.bind(this), this.empty.bind(this));
        };

        comp.nextPageReuest = function (object, resolve, reject) {
            load(object.page, function (data) { resolve(data); }, function () { reject(); });
        };
        comp.nextPageRequest = comp.nextPageReuest;

        comp.cardRender = function (object, element, card) {
            card.onMenu = false;
            card.onEnter = function () {
                if (element._tmdb_id_valid) {
                    Lampa.Activity.push({
                        url: '',
                        component: 'full',
                        id: element.id,
                        method: element.media_type,
                        card: element,
                        source: 'tmdb'
                    });
                } else {
                    Lampa.Noty.show('\"' + (element.title || '') + '\" - net TMDB ID');
                }
            };
        };

        return comp;
    }

    function addMenuButton() {
        if (document.querySelector('.menu .menu__list [data-action=\"antigravity_collections\"]')) return;

        var html =
            '<li class=\"menu__item selector\" data-action=\"antigravity_collections\">' +
                '<div class=\"menu__ico\">' +
                    '<svg width=\"24\" height=\"24\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">' +
                        '<path d=\"M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linejoin=\"round\"/>' +
                    '</svg>' +
                '</div>' +
                '<div class=\"menu__text\">📚 Мои коллекции</div>' +
            '</li>';

        var button = $(html);

        button.on('hover:enter', function () {
            Lampa.Activity.push({
                url: '',
                title: 'Мои коллекции',
                component: 'antigravity_collections',
                page: 1
            });
        });

        $('.menu .menu__list').eq(0).append(button);
    }

    function addSettings() {
        if (!Lampa.SettingsApi) return;

        Lampa.SettingsApi.addParam({
            component: 'antigravity',
            param: { name: 'antigravity_base', type: 'input', default: BASE },
            field: { name: 'Адрес сервера', description: 'URL вашего Antigravity Tracker' },
            onChange: function (value) { Lampa.Storage.set('antigravity_base', value); }
        });

        Lampa.SettingsApi.addParam({
            component: 'antigravity',
            param: { name: 'antigravity_key', type: 'input', default: KEY },
            field: { name: 'API ключ', description: 'Ключ доступа, если задан на сервере' },
            onChange: function (value) { Lampa.Storage.set('antigravity_key', value); }
        });
    }

    function startPlugin() {
        if (window.antigravity_plugin_ready) return;
        window.antigravity_plugin_ready = true;

        Lampa.Component.add('antigravity_collections', collectionsComponent);
        Lampa.Component.add('antigravity_collection_content', collectionContentComponent);

        addSettings();

        if (window.appready) {
            addMenuButton();
        } else {
            Lampa.Listener.follow('app', function (e) {
                if (e.type === 'ready') addMenuButton();
            });
        }
    }

    if (window.Lampa) {
        startPlugin();
    } else {
        var waiter = setInterval(function () {
            if (window.Lampa) {
                clearInterval(waiter);
                startPlugin();
            }
        }, 200);
    }
})();"""

    js_content = js_template.replace("__BASE__", base_url).replace("__KEY__", api_key)
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
            cover_img = ""

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
                        cover_img = poster_url
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
                        cover_img = cover_poster_url

            res_cols.append(
                {
                    "id": col["id"],
                    "name": col["name"],
                    "count": len(items),
                    "cover_tmdb_id": cover_tmdb_id,
                    "cover_media_type": cover_media_type,
                    "cover_poster_url": cover_poster_url,
                    "cover_img": cover_img,
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
