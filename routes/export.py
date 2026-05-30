import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Response

from db import db

router = APIRouter()


@router.get("/api/export/watched/imdb")
def export_watched_imdb():
    """Export watched movies/shows in IMDb watchlist format.

    Headers: Const,Your Rating,Date Rated,Title,URL,Title Type,IMDb Rating,Runtime,Year,Genres,Num Votes,Release Date,Directors
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Const",
            "Your Rating",
            "Date Rated",
            "Title",
            "URL",
            "Title Type",
            "IMDb Rating",
            "Runtime",
            "Year",
            "Genres",
            "Num Votes",
            "Release Date",
            "Directors",
        ]
    )

    with db._conn() as c:
        rows = c.execute(
            """
            SELECT imdb_id, user_rating, watched_at, title, year, kp_rating, imdb_rating, category_id, description
            FROM items
            WHERE is_watched = 1 AND imdb_id IS NOT NULL AND imdb_id <> ''
            """
        ).fetchall()

        for r in rows:
            title_type = (
                "tvSeries" if r["category_id"] in (2, 7) else "movie"
            )  # Example Rutor categories mapping to series
            date_rated = r["watched_at"] or datetime.now().strftime("%Y-%m-%d")
            # format date_rated as YYYY-MM-DD
            if " " in date_rated:
                date_rated = date_rated.split(" ")[0]

            writer.writerow(
                [
                    r["imdb_id"],
                    r["user_rating"] or "",
                    date_rated,
                    r["title"],
                    f"https://www.imdb.com/title/{r['imdb_id']}/",
                    title_type,
                    r["imdb_rating"] or "",
                    "",  # Runtime
                    r["year"] or "",
                    "",  # Genres
                    "",  # Num Votes
                    "",  # Release Date
                    "",  # Directors
                ]
            )

    response = Response(content=output.getvalue(), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=watched_imdb.csv"
    return response


@router.get("/api/export/ratings/imdb")
def export_ratings_imdb():
    """Export rated movies/shows in IMDb ratings format."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Const",
            "Your Rating",
            "Date Rated",
            "Title",
            "URL",
            "Title Type",
            "IMDb Rating",
            "Runtime",
            "Year",
            "Genres",
            "Num Votes",
            "Release Date",
            "Directors",
        ]
    )

    with db._conn() as c:
        rows = c.execute(
            """
            SELECT imdb_id, user_rating, watched_at, title, year, kp_rating, imdb_rating, category_id
            FROM items
            WHERE user_rating IS NOT NULL AND imdb_id IS NOT NULL AND imdb_id <> ''
            """
        ).fetchall()

        for r in rows:
            title_type = "tvSeries" if r["category_id"] in (2, 7) else "movie"
            date_rated = r["watched_at"] or datetime.now().strftime("%Y-%m-%d")
            if " " in date_rated:
                date_rated = date_rated.split(" ")[0]

            writer.writerow(
                [
                    r["imdb_id"],
                    r["user_rating"],
                    date_rated,
                    r["title"],
                    f"https://www.imdb.com/title/{r['imdb_id']}/",
                    title_type,
                    r["imdb_rating"] or "",
                    "",
                    r["year"] or "",
                    "",
                    "",
                    "",
                    "",
                ]
            )

    response = Response(content=output.getvalue(), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=ratings_imdb.csv"
    return response


@router.get("/api/export/watched/kp")
def export_watched_kp():
    """Export watched movies/shows in Kinopoisk format.

    CSV (kp_id, title, year, user_rating, watched_date)
    """
    output = io.StringIO()
    # Kinopoisk export usually expects standard CSV, or utf-16 with tabs, let's use standard CSV UTF-8
    writer = csv.writer(output)
    writer.writerow(["kp_id", "title", "year", "user_rating", "watched_date"])

    with db._conn() as c:
        rows = c.execute(
            """
            SELECT kp_id, title, year, user_rating, watched_at
            FROM items
            WHERE is_watched = 1 AND kp_id IS NOT NULL AND kp_id <> ''
            """
        ).fetchall()

        for r in rows:
            watched_date = r["watched_at"] or datetime.now().strftime("%Y-%m-%d %H:%M")
            writer.writerow(
                [r["kp_id"], r["title"], r["year"] or "", r["user_rating"] or "", watched_date]
            )

    response = Response(content=output.getvalue(), media_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = "attachment; filename=watched_kp.csv"
    return response


@router.get("/api/export/watched/trakt")
def export_watched_trakt():
    """Export watched movies/shows in Trakt JSON history format."""
    movies = []
    shows = []

    with db._conn() as c:
        rows = c.execute(
            """
            SELECT title, year, imdb_id, kp_id, tmdb_id, watched_at, category_id
            FROM items
            WHERE is_watched = 1
            """
        ).fetchall()

        for r in rows:
            watched_at = r["watched_at"] or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
            if " " in watched_at:
                watched_at = watched_at.replace(" ", "T") + ".000Z"

            ids = {}
            if r["imdb_id"]:
                ids["imdb"] = r["imdb_id"]
            if r["tmdb_id"]:
                try:
                    ids["tmdb"] = int(r["tmdb_id"])
                except ValueError:
                    pass

            # Map based on category_id
            is_show = r["category_id"] in (2, 7)  # tv categories

            item = {"title": r["title"], "year": r["year"], "ids": ids, "watched_at": watched_at}

            if is_show:
                shows.append(item)
            else:
                movies.append(item)

    data = {"movies": movies, "shows": shows}

    response = Response(
        content=json.dumps(data, indent=2, ensure_ascii=False), media_type="application/json"
    )
    response.headers["Content-Disposition"] = "attachment; filename=watched_trakt.json"
    return response


@router.get("/api/export/watched/tmdb")
def export_watched_tmdb():
    """Alias for Trakt JSON format (often used as intermediary for TMDB imports)."""
    return export_watched_trakt()
