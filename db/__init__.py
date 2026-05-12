from db.collections import DbCollectionsMixin
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
from db.settings_db import DbSettingsMixin


class Database(DbItemsMixin, DbCollectionsMixin, DbHistoryMixin, DbSettingsMixin, DbCore):
    pass

from settings import settings

db = Database(path=settings.resolved_db_path)
