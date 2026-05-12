import logging
import re

from db.core import FILTER_RULE_ACTIONS, FILTER_RULE_FIELDS

logger = logging.getLogger('parsclode.db')


class DbSettingsMixin:
        def list_filter_rules(self, *, only_enabled: bool = False, conn=None) -> list[dict]:
            with self._conn(conn) as c:
                sql = "SELECT * FROM filter_rules"
                if only_enabled:
                    sql += " WHERE enabled = 1"
                sql += " ORDER BY id"
                return [dict(r) for r in c.execute(sql).fetchall()]

        def create_filter_rule(
            self,
            *,
            name: str,
            field: str,
            pattern: str,
            action: str,
            enabled: bool = True,
            conn=None,
        ) -> int:
            if field not in FILTER_RULE_FIELDS:
                raise ValueError(f"unsupported field: {field!r}")
            if action not in FILTER_RULE_ACTIONS:
                raise ValueError(f"unsupported action: {action!r}")
            # Validate the pattern up-front so the rule editor surfaces a
            # meaningful error instead of silently storing garbage.
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid regex: {e}") from e
            with self._conn(conn) as c:
                cur = c.execute(
                    "INSERT INTO filter_rules (name, field, pattern, action, enabled) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name.strip(), field, pattern, action, 1 if enabled else 0),
                )
                return int(cur.lastrowid)

        def update_filter_rule(
            self,
            rule_id: int,
            *,
            name: str | None = None,
            field: str | None = None,
            pattern: str | None = None,
            action: str | None = None,
            enabled: bool | None = None,
            conn=None,
        ) -> bool:
            sets: list[str] = []
            params: list = []
            if name is not None:
                sets.append("name = ?")
                params.append(name.strip())
            if field is not None:
                if field not in FILTER_RULE_FIELDS:
                    raise ValueError(f"unsupported field: {field!r}")
                sets.append("field = ?")
                params.append(field)
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as e:
                    raise ValueError(f"invalid regex: {e}") from e
                sets.append("pattern = ?")
                params.append(pattern)
            if action is not None:
                if action not in FILTER_RULE_ACTIONS:
                    raise ValueError(f"unsupported action: {action!r}")
                sets.append("action = ?")
                params.append(action)
            if enabled is not None:
                sets.append("enabled = ?")
                params.append(1 if enabled else 0)
            if not sets:
                return False
            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(rule_id)
            with self._conn(conn) as c:
                cur = c.execute(
                    f"UPDATE filter_rules SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
                return cur.rowcount > 0

        def delete_filter_rule(self, rule_id: int, conn=None) -> bool:
            with self._conn(conn) as c:
                cur = c.execute("DELETE FROM filter_rules WHERE id = ?", (rule_id,))
                return cur.rowcount > 0

