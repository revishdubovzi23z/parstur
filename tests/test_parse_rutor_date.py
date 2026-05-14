"""Unit tests for sync_job.parse_rutor_date.

Per item 3.3 the function is now allowed to return None. Previously
it would silently fall back to datetime.now() and contaminate the
sync cursor for unparseable releases. These tests pin both the
happy paths (recognised formats) and the explicit-None contract for
junk input so a regression can't quietly bring back the old
behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from sync_job import parse_rutor_date


class TestKnownFormats:
    def test_full_date(self) -> None:
        # "1 Янв 2024 12:30" -> 2024-01-01 (the time part is
        # discarded for full dates by the current implementation).
        out = parse_rutor_date("1 Янв 2024")
        assert out is not None
        assert out.startswith("2024-01-01")

    def test_two_digit_year_treated_as_2000s(self) -> None:
        out = parse_rutor_date("15 Мар 23")
        assert out is not None
        assert out.startswith("2023-03-15")

    def test_today_uses_current_date(self) -> None:
        out = parse_rutor_date("Сегодня 14:25")
        assert out is not None
        # Should pick today's calendar date.
        today = datetime.now().date().isoformat()
        assert out.startswith(today)
        # And carry the requested H:M.
        assert "14:25" in out

    def test_yesterday_uses_yesterday(self) -> None:
        out = parse_rutor_date("Вчера 09:00")
        assert out is not None
        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
        assert out.startswith(yesterday)
        assert "09:00" in out

    @pytest.mark.parametrize(
        "month_ru,expected",
        [
            ("Янв", 1),
            ("Фев", 2),
            ("Мар", 3),
            ("Апр", 4),
            ("Май", 5),
            ("Июн", 6),
            ("Июл", 7),
            ("Авг", 8),
            ("Сен", 9),
            ("Окт", 10),
            ("Ноя", 11),
            ("Дек", 12),
        ],
    )
    def test_every_month_abbreviation_resolves(self, month_ru: str, expected: int) -> None:
        out = parse_rutor_date(f"5 {month_ru} 2024")
        assert out is not None
        # Month component appears as zero-padded two-digit field.
        assert f"-{expected:02d}-05" in out


class TestUnparseableInput:
    """Junk input must return None so the caller can decide what to
    do (item 3.3) instead of getting a datetime.now() poisoned cursor.
    """

    @pytest.mark.parametrize(
        "junk",
        [
            "",
            None,
            "   ",
            "totally",  # single token; no parts >= 3
            "Сегодня noclock",
            "Вчера",  # missing time
            "32 Дек 2024",  # invalid day
            "no day Янв 2024",
        ],
    )
    def test_returns_none(self, junk: str | None) -> None:
        assert parse_rutor_date(junk) is None

    def test_unknown_month_falls_back_to_january(self) -> None:
        # KNOWN, documented behavior: an unrecognised month name is
        # silently treated as January (`MONTHS.get(parts[1], 1)`).
        # This isn't strictly correct — a future fix could return
        # None instead — but for now we pin the behaviour so the
        # current callers (which depend on it) don't break.
        out = parse_rutor_date("5 Foo 2024")
        assert out is not None
        assert out.startswith("2024-01-05")
