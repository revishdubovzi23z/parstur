"""Misc administrative helpers: reset-tokens and restart trigger.

These were previously module-level globals on `main.py` (`_reset_tokens`,
`_trigger_restart`). Keeping the same names so the routes can reach them
through `runtime.admin` without further renames.
"""

from __future__ import annotations

import logging
import subprocess

from settings import settings

logger = logging.getLogger("parsclode.runtime.admin")

# Short-lived one-time tokens for `/api/reset_database` confirmation.
# Each value is a `datetime` expiry; entries are popped on use.
_reset_tokens: dict = {}


def _trigger_restart() -> bool:
    """Spawn the configured restart command in a new session.

    Returns True if a command was started, False if no restart
    command is configured. Errors from `Popen` are logged but not
    raised — the caller (admin endpoints) reports the outcome to
    the user.
    """
    if settings.restart_command:
        try:
            subprocess.Popen(
                settings.restart_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to trigger restart: {e}")
            return False
    return False
