import os

from requests_cache import CachedSession

from script_utils import load_config

_config = load_config()
_cache_cfg = _config.get("cache", {})

_expire_hours = _cache_cfg.get("expire_hours", 168)
_db_path = _cache_cfg.get("db_path", "api_cache.db")
_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), _db_path)

_session = None


def get_cached_session():
    global _session
    if _session is None:
        backend = _cache_cfg.get("backend", "sqlite")
        kwargs = dict(
            backend=backend,
            expire_after=_expire_hours * 3600,
            allowable_methods=_cache_cfg.get("allowable_methods", ["GET"]),
            match_headers=_cache_cfg.get("match_headers"),
        )
        # 4.6: tune the SQLite backend so the cache db keeps up with
        # tight inner loops (each `_request` calls `session.get` and
        # CachedSession does a write on every miss). Defaults run with
        # `synchronous=FULL` and rollback journal — fine for a few
        # requests/sec, painful for tens of thousands. We force WAL +
        # synchronous=NORMAL via `fast_save=True`, which translates
        # roughly to 'durable enough, much less fsync churn'.
        if backend == "sqlite":
            kwargs["fast_save"] = _cache_cfg.get("fast_save", True)
            kwargs["wal"] = _cache_cfg.get("wal", True)
        _session = CachedSession(_cache_dir, **kwargs)
    return _session
