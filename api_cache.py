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
        _session = CachedSession(
            _cache_dir,
            backend=_cache_cfg.get("backend", "sqlite"),
            expire_after=_expire_hours * 3600,
            allowable_methods=_cache_cfg.get("allowable_methods", ["GET"]),
            match_headers=_cache_cfg.get("match_headers"),
        )
    return _session
