import re
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db import db

router = APIRouter()

@router.get("/api/online_sources/{item_id}")
def get_online_sources(item_id: int):
    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT kp_id, imdb_id, title FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    kp_id = row["kp_id"]
    imdb_id = row["imdb_id"]
    if not kp_id and not imdb_id:
        return {"sources": []}

    page_url = f"https://fbdomen.cfd/film/{kp_id}/" if kp_id else ""

    all_players = {}

    def _merge_players(data):
        if not isinstance(data, dict):
            return
        for p in data.get("data", []):
            if not p.get("iframeUrl") or not p.get("type"):
                continue
            key = p["type"].lower()
            if key not in all_players:
                all_players[key] = {
                    "type": p["type"],
                    "iframeUrl": p["iframeUrl"],
                    "translations": p.get("translations") or [],
                }

    def _fetch_kinobox_api(params):
        try:
            from curl_cffi import requests as _cf

            r = _cf.get(
                "https://api.kinobox.tv/api/players",
                params=params,
                impersonate="chrome",
                timeout=10,
            )
            if r.status_code == 200:
                _merge_players(r.json())
                return True
        except Exception:
            pass
        return False

    def _fetch_fbphdplay(params):
        try:
            import requests as _req

            r = _req.get(
                "https://fbphdplay.top/api/players",
                params=params,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                timeout=10,
            )
            if r.status_code == 200:
                _merge_players(r.json())
        except Exception:
            pass

    params_kp = {"kinopoisk": kp_id} if kp_id else {}
    params_imdb = {"imdb": imdb_id} if imdb_id else {}

    if kp_id:
        _fetch_kinobox_api(params_kp)
        _fetch_fbphdplay(params_kp)

    if imdb_id and not all_players:
        _fetch_kinobox_api(params_imdb)
        _fetch_fbphdplay(params_imdb)

    if not all_players and imdb_id:
        _fetch_fbphdplay({**params_kp, **params_imdb})

    sources = list(all_players.values())
    return {"sources": sources, "pageUrl": page_url}


@router.get("/api/stream_info/{item_id}")
def get_stream_info(item_id: int):
    from HdRezkaApi.types import TVSeries

    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT rezka_url FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row or not row["rezka_url"]:
        return {"error": "no rezka_url"}

    rezka, _ = main._get_rezka_obj(item_id, row["rezka_url"])
    if not rezka:
        return {"error": "failed to load page"}

    try:
        is_series = rezka.type == TVSeries
        result = {
            "type": "series" if is_series else "movie",
            "name": rezka.name,
            "translators": rezka.translators,
        }

        if is_series:
            series_data = {}
            for tid, info in rezka.seriesInfo.items():
                series_data[str(tid)] = {
                    "name": info.get("translator_name", ""),
                    "premium": info.get("premium", False),
                    "seasons": {str(k): v for k, v in info.get("seasons", {}).items()},
                    "episodes": {
                        str(s): {str(e): name for e, name in eps.items()}
                        for s, eps in info.get("episodes", {}).items()
                    },
                }
            result["series_info"] = series_data

        return result
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/stream/{item_id}")
def get_stream(
    item_id: int,
    season: str | None = None,
    episode: str | None = None,
    translator: str | None = None,
):
    import main
    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT rezka_url FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row or not row["rezka_url"]:
        return {"error": "no rezka_url"}

    rezka, _ = main._get_rezka_obj(item_id, row["rezka_url"])
    if not rezka:
        return {"error": "failed to load page"}

    try:
        kwargs = {}
        if translator:
            kwargs["translation"] = translator

        if season and episode:
            stream = rezka.getStream(season, episode, **kwargs)
        else:
            stream = rezka.getStream(**kwargs)

        videos = {}
        for quality, urls in stream.videos.items():
            videos[quality] = urls[0] if urls else None

        subtitles = {}
        if stream.subtitles and stream.subtitles.subtitles:
            for lang, info in stream.subtitles.subtitles.items():
                subtitles[lang] = {
                    "title": info.get("title", lang),
                    "link": info.get("link", ""),
                }

        return {
            "videos": videos,
            "subtitles": subtitles,
            "translator_id": stream.translator_id,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/stream_m3u/{item_id}")
def get_stream_m3u(
    item_id: int,
    season: str | None = None,
    episode: str | None = None,
    translator: str | None = None,
    quality: str | None = None,
):
    from fastapi.responses import Response as R

    import main

    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT rezka_url, title FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row or not row["rezka_url"]:
        return R(content="error: no rezka_url", media_type="text/plain", status_code=404)

    rezka, _ = main._get_rezka_obj(item_id, row["rezka_url"])
    if not rezka:
        return R(
            content="error: failed to load page",
            media_type="text/plain",
            status_code=500,
        )

    try:
        kwargs = {}
        if translator:
            kwargs["translation"] = translator

        if season and episode:
            stream = rezka.getStream(season, episode, **kwargs)
        else:
            stream = rezka.getStream(**kwargs)

        if not quality or quality not in stream.videos:
            quality = max(
                stream.videos.keys(),
                key=lambda q: {
                    "4K": 7,
                    "2K": 6,
                    "1080p Ultra": 5,
                    "1080p": 4,
                    "720p": 3,
                    "480p": 2,
                    "360p": 1,
                }.get(q, 0),
            )

        url = stream.videos[quality][0] if stream.videos[quality] else None
        if not url:
            return R(content="error: no stream url", media_type="text/plain", status_code=500)

        title = row["title"]
        if season and episode:
            title += f" - S{season}E{episode}"

        safe_title = re.sub(r'[<>:"/\\|?*]', "", title).encode("ascii", "replace").decode("ascii")
        m3u = f"#EXTM3U\n#EXTINF:-1,{title}\n{url}\n"
        return R(
            content=m3u.encode("utf-8"),
            media_type="audio/mpegurl; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.m3u"'},
        )
    except Exception as e:
        return R(content=f"error: {e}", media_type="text/plain", status_code=500)


@router.post("/api/mark_season_seen/{item_id}")
def mark_season_seen(item_id: int):
    row = (
        db.get_connection()
        .cursor()
        .execute("SELECT latest_season, latest_episode FROM items WHERE id = ?", (item_id,))
        .fetchone()
    )
    if not row:
        return {"error": "not found"}
    key = f"rezka_seen_{item_id}"
    value = f"s{row['latest_season']}e{row['latest_episode']}"
    conn = db.get_connection()
    conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()
    return {"status": "success"}


@router.get("/api/trailer/{item_id}")
def get_trailer(item_id: int):
    from tmdb_client import TMDBClient

    with db._conn() as c:
        row = c.execute(
            "SELECT id, imdb_id, tmdb_id, title, original_title, year FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "item not found"}, status_code=404)

    client = TMDBClient()
    if not client.api_key:
        return JSONResponse({"error": "TMDB_API_KEY not configured"}, status_code=503)

    tmdb_id = row["tmdb_id"]
    media_type = "movie"
    if not tmdb_id and row["imdb_id"]:
        meta = client.find_by_imdb_id(row["imdb_id"], return_meta=True)
        if meta and meta.get("tmdb_id"):
            tmdb_id = str(meta["tmdb_id"])
            media_type = meta.get("media_type") or "movie"
            with db._conn() as c:
                c.execute(
                    "UPDATE items SET tmdb_id = ? WHERE id = ?",
                    (tmdb_id, item_id),
                )
    if not tmdb_id:
        return JSONResponse(
            {"error": "no TMDB id for this item; set imdb_id first"},
            status_code=404,
        )

    videos = client.get_videos(media_type, tmdb_id) or []

    # Prefer YouTube + Trailer + Official.
    def _score(v: dict) -> int:
        s = 0
        if (v.get("site") or "").lower() == "youtube":
            s += 100
        if (v.get("type") or "").lower() == "trailer":
            s += 50
        if v.get("official"):
            s += 25
        return s

    videos.sort(key=_score, reverse=True)
    # Return up to 5 YouTube candidates so the frontend can fall back
    # to the next one if a video has embedding disabled (YouTube error
    # 101 / 150 / 153). TMDB doesn't expose an embed-allowed flag, so
    # serial fallback is the only reliable workaround.
    youtube_candidates = [
        {
            "youtube_key": v["key"],
            "name": v.get("name") or "",
            "type": v.get("type") or "",
            "official": bool(v.get("official")),
        }
        for v in videos
        if (v.get("site") or "").lower() == "youtube" and v.get("key")
    ][:5]
    if not youtube_candidates:
        return JSONResponse({"error": "no trailer available"}, status_code=404)
    primary = youtube_candidates[0]
    return {
        # Backwards-compatible flat fields (first / best candidate).
        "youtube_key": primary["youtube_key"],
        "name": primary["name"],
        "type": primary["type"],
        "official": primary["official"],
        # New: ranked list for client-side embed fallback.
        "candidates": youtube_candidates,
        "tmdb_id": tmdb_id,
        "media_type": media_type,
    }


@router.get("/api/stream_url/{item_id}")
def get_stream_url(
    item_id: int,
    season: str | None = None,
    episode: str | None = None,
    translator: str | None = None,
    quality: str | None = None,
):
    import main
    with db._conn() as c:
        row = c.execute("SELECT rezka_url, title FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row or not row["rezka_url"]:
        return JSONResponse({"error": "no rezka_url"}, status_code=404)

    rezka, _ = main._get_rezka_obj(item_id, row["rezka_url"])
    if not rezka:
        return JSONResponse({"error": "failed to load page"}, status_code=502)
    try:
        kwargs: dict = {}
        if translator:
            kwargs["translation"] = translator
        if season and episode:
            stream = rezka.getStream(season, episode, **kwargs)
        else:
            stream = rezka.getStream(**kwargs)

        if not quality or quality not in stream.videos:
            quality = max(
                stream.videos.keys(),
                key=lambda q: {
                    "4K": 7,
                    "2K": 6,
                    "1080p Ultra": 5,
                    "1080p": 4,
                    "720p": 3,
                    "480p": 2,
                    "360p": 1,
                }.get(q, 0),
            )
        url = stream.videos[quality][0] if stream.videos[quality] else None
        if not url:
            return JSONResponse({"error": "no stream url"}, status_code=502)
        is_hls = ".m3u8" in url.lower()
        return {
            "url": url,
            "quality": quality,
            "title": row["title"],
            "is_hls": is_hls,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@router.get("/api/subtitle_proxy")
def subtitle_proxy(url: str):
    from fastapi.responses import Response

    import main

    if not url or not url.startswith(("http://", "https://")):
        return JSONResponse({"error": "invalid url"}, status_code=400)
    host = (urlparse(url).hostname or "").lower()
    if not any(host == h.lstrip(".") or host.endswith(h) for h in main._SUBTITLE_HOST_ALLOWLIST):
        return JSONResponse({"error": f"host {host} not allowed"}, status_code=403)
    try:
        # Subtitle files live on CDNs (no auth required) — use a plain
        # requests.get rather than `_rezka_request`, which would no-op
        # when the user hasn't configured Rezka credentials.
        import requests as _req

        resp = _req.get(url, timeout=10)
        if resp.status_code != 200:
            return JSONResponse(
                {"error": f"upstream HTTP {resp.status_code}"},
                status_code=502,
            )
        # Pick a Content-Type that the browser will accept for <track>.
        ct = resp.headers.get("Content-Type") or ""
        if "vtt" in ct.lower() or url.lower().endswith(".vtt"):
            ct = "text/vtt; charset=utf-8"
        else:
            # Most rezka subtitles are SRT — convert lazily so the
            # browser <track> element accepts them as captions.
            ct = "text/plain; charset=utf-8"
        body = resp.content
        # If the file is SRT (common on rezka), do a quick conversion
        # to WebVTT — browsers only render captions in VTT format.
        if b"WEBVTT" not in body[:64] and (url.lower().endswith(".srt") or b"-->" in body[:512]):
            try:
                txt = body.decode("utf-8-sig", errors="replace")
                txt = txt.replace("\r\n", "\n")
                # SRT timestamps use comma; VTT uses dot.
                import re as _re

                txt = _re.sub(
                    r"(\d{2}:\d{2}:\d{2}),(\d{3})",
                    r"\1.\2",
                    txt,
                )
                body = ("WEBVTT\n\n" + txt).encode("utf-8")
                ct = "text/vtt; charset=utf-8"
            except Exception:
                pass
        return Response(
            content=body,
            media_type=ct,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=3600",
            },
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


