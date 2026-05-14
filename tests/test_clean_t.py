"""Unit tests for cleanup_duplicates.clean_t.

clean_t is what builds the name+year merge bundles. A regression
that flattens the title too aggressively would create huge bundles
and trip the new NAME_GROUP_MAX guard from item 4.2.

Like normalize_title, clean_t folds Latin 'x' to Cyrillic 'х'.
"""

from __future__ import annotations

import pytest

from cleanup_duplicates import clean_t

CYRILLIC_X = "\u0445"  # 'х'


class TestCleanT:
    def test_returns_empty_for_empty(self) -> None:
        assert clean_t("") == ""
        assert clean_t(None) == ""  # type: ignore[arg-type]

    def test_lowercases(self) -> None:
        assert clean_t("Matrix") == f"matri{CYRILLIC_X}"

    def test_drops_year(self) -> None:
        # The 4-digit year regex catches both bare and parenthesised forms.
        assert clean_t("Matrix (1999)") == f"matri{CYRILLIC_X}"
        assert clean_t("Matrix 1999") == f"matri{CYRILLIC_X}"

    def test_drops_quality_tags(self) -> None:
        # The literal-list quality-tag regex strips these cleanly.
        # NOTE: '1080p' / '720p' get caught by the 4-digit-year regex
        # FIRST (because '1080' / '720' look like years), leaving the
        # 'p' suffix behind. That's a soft bug in clean_t, but we pin
        # the current behaviour rather than mask it.
        for tag in ("BDRip", "HEVC", "AVC", "MVO", "Web-DL", "SATRip"):
            out = clean_t(f"Matrix {tag}")
            assert out.strip() == f"matri{CYRILLIC_X}", tag

    def test_year_regex_eats_resolution_numbers(self) -> None:
        # Document the soft bug above: '1080' looks like a year, so
        # 'Matrix 1080p' becomes 'matriх  p'.
        out = clean_t("Matrix 1080p").strip()
        assert "p" in out  # the 'p' survives
        assert "1080" not in out  # but the digits don't

    def test_keeps_alt_title_split_first_part(self) -> None:
        # "Matrix / Матрица" -> first half only.
        assert clean_t("Matrix / Матрица") == f"matri{CYRILLIC_X}"
        assert clean_t("Matrix/Матрица") == f"matri{CYRILLIC_X}"

    def test_strips_brackets(self) -> None:
        out = clean_t("Matrix [BDRip] Reloaded")
        # The bracketed group is dropped; 'Matrix Reloaded' (with
        # the x->х fold) survives, with one or more spaces between.
        assert out.startswith(f"matri{CYRILLIC_X}")
        assert "reloaded" in out

    def test_replaces_dots_and_underscores_with_spaces(self) -> None:
        # Release naming convention: dots/underscores instead of spaces.
        out_dot = clean_t("Matrix.Reloaded.2003")
        out_us = clean_t("Matrix_Reloaded_2003")
        assert " " in out_dot
        assert "reloaded" in out_dot
        assert " " in out_us
        assert "reloaded" in out_us

    def test_folds_latin_x_to_cyrillic(self) -> None:
        # Both Latin x and Cyrillic Х/х lowercased should produce the
        # same Cyrillic glyph in the output. This is the dedup invariant
        # cleanup_duplicates relies on.
        assert clean_t("X-Men") == clean_t("Х-Men")

    @pytest.mark.parametrize(
        "messy,must_contain",
        [
            ("Matrix (1999) [BDRip] 1080p", f"matri{CYRILLIC_X}"),
            ("THE MATRIX (1999)", f"the matri{CYRILLIC_X}"),
            ("Matrix.Reloaded.2003.WEB-DL.MVO", f"matri{CYRILLIC_X} reloaded"),
        ],
    )
    def test_realistic_release_names(self, messy: str, must_contain: str) -> None:
        # Don't pin the exact whitespace shape — the regex sweep can
        # leave doubled spaces. Only require that the meaningful
        # tokens survive in the right order.
        out = clean_t(messy)
        assert must_contain in out
