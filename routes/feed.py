from fastapi import APIRouter

from db import db
from script_utils import load_config

router = APIRouter()


@router.get("/api/feed")
def get_feed(
    category_id: int = -1,
    collection_id: int = None,
    search: str = None,
    min_kp: float = 0.0,
    max_kp: float = 10.0,
    min_imdb: float = 0.0,
    max_imdb: float = 10.0,
    min_year: int = None,
    max_year: int = None,
    min_date: str = None,
    max_date: str = None,
    hide_ignored: bool = True,
    hide_rated: bool = False,
    hide_collected: bool = False,
    sort_by: str = "date_desc",
    page: int = 1,
    limit: int = None,
):
    if limit is None:
        limit = load_config().get("feed", {}).get("default_limit", 20)
    return db.get_feed(
        category_id=category_id,
        collection_id=collection_id,
        search=search,
        min_kp=min_kp,
        max_kp=max_kp,
        min_imdb=min_imdb,
        max_imdb=max_imdb,
        min_year=min_year,
        max_year=max_year,
        min_date=min_date,
        max_date=max_date,
        hide_ignored=hide_ignored,
        hide_rated=hide_rated,
        hide_collected=hide_collected,
        sort_by=sort_by,
        page=page,
        limit=limit,
    )


@router.get("/api/categories")
def get_categories(hide_rated: bool = False, hide_collected: bool = False):
    return db.get_categories_with_counts(hide_rated, hide_collected)


@router.get("/api/stats")
def get_stats():
    return db.get_stats()


@router.get("/api/job_history")
def get_job_history(limit: int = 20):
    return db.get_job_history(limit)
