from db.collections import DbCollectionsMixin

# Re-exports of intentionally private helpers from `db.core`. These are
# imported by tests (e.g. `tests/test_db_helpers.py`) and by callers that
# expect to access them as `db._placeholders(...)` rather than reaching
# into `db.core` directly. Listing them in `__all__` (below) tells ruff
# the leading-underscore names are deliberately part of the public
# `db` surface, so the F401 "imported but unused" warning is silenced.
from db.core import (
    _LARGE_ID_LIST_THRESHOLD,
    DbCore,
    _compile_filter_pattern,
    _materialize_id_list,
    _placeholders,
    _sqlite_regexp,
    logger,
)
from db.history import DbHistoryMixin
from db.items import DbItemsMixin
from db.kinopub_auth import DbKinopubAuthMixin
from db.sessions import DbSessionsMixin
from db.settings_db import DbSettingsMixin


class Database(
    DbItemsMixin,
    DbCollectionsMixin,
    DbHistoryMixin,
    DbSettingsMixin,
    DbSessionsMixin,
    DbKinopubAuthMixin,
    DbCore,
):
    pass


__all__ = [
    "_LARGE_ID_LIST_THRESHOLD",
    "Database",
    "DbCollectionsMixin",
    "DbCore",
    "DbHistoryMixin",
    "DbItemsMixin",
    "DbKinopubAuthMixin",
    "DbSessionsMixin",
    "DbSettingsMixin",
    "_compile_filter_pattern",
    "_materialize_id_list",
    "_placeholders",
    "_sqlite_regexp",
    "logger",
]


from settings import settings

db = Database(path=settings.resolved_db_path)
