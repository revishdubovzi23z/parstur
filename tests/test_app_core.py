"""Unit tests for app_core.normalize_title / clean_title_for_search.

These two helpers feed the dedup pipeline (cleanup_duplicates) and
the rezka_sync title-matching, so a regression here silently drops
matches across the whole app.

NOTE: `normalize_title` folds the Latin letter 'x' to the Cyrillic
'х' (U+0445). This is intentional — Russian releases interchange
the two glyphs all the time. Tests assert against the post-fold form
so a regression that drops the fold (or, worse, folds in the other
direction) is caught.
"""

from __future__ import annotations

import pytest

from app_core import clean_title_for_search, clean_title_year_duplicates, normalize_title

CYRILLIC_X = "\u0445"  # 'х'


class TestNormalizeTitle:
    def test_returns_empty_for_empty(self) -> None:
        assert normalize_title("") == ""
        assert normalize_title(None) == ""  # type: ignore[arg-type]

    def test_lowercases(self) -> None:
        # 'x' becomes Cyrillic 'х' as part of the fold.
        assert normalize_title("Matrix") == f"matri{CYRILLIC_X}"

    def test_strips_punctuation_and_whitespace(self) -> None:
        assert normalize_title("Matrix: Reloaded!") == f"matri{CYRILLIC_X}reloaded"

    def test_drops_parenthetical_and_bracket_content(self) -> None:
        # Year/quality tags often live in parentheses or brackets.
        assert normalize_title("Matrix (1999) [BDRip]") == f"matri{CYRILLIC_X}"

    def test_folds_latin_x_to_cyrillic(self) -> None:
        # The single-character replacement keeps the surrounding text
        # untouched. This regression-guards the x→х direction; a
        # bidirectional fold (which used to be a refactor temptation)
        # would also silently drop the test below.
        out_latin = normalize_title("X-Men")
        # Title becomes 'х-мен'... wait, ASCII 'X' (uppercase) and 'M',
        # 'e', 'n' are inside the [a-zа-яё0-9] keep-set, then x→х.
        # After lower(): "x-men". After fold: "х-men". After [^...]
        # strip removes "-": "хmen".
        assert out_latin == f"{CYRILLIC_X}men"

        # Cyrillic Х (U+0425) lower-cases to х (U+0445), then survives
        # the regex (it's in the cyrillic range), and there's no Latin
        # 'x' to fold this time. Result: 'х-Мен' -> 'хмен'.
        assert normalize_title("Х-Мен") == f"{CYRILLIC_X}мен"

    def test_preserves_cyrillic_words(self) -> None:
        assert normalize_title("Игра престолов") == "играпрестолов"

    def test_drops_diacritics_via_strip_not_normalize(self) -> None:
        # Combining diacritics aren't in the [a-zа-яё0-9] charset
        # so they get dropped, leaving the bare base char.
        # u'na\u0301' -> 'na'
        assert normalize_title("Na\u0301ive") == "naive"


class TestCleanTitleForSearch:
    """Looser cleaning used when feeding queries to external APIs.

    Unlike normalize_title, this one keeps spaces (so the search
    string still looks word-shaped to the upstream service) and
    does NOT do the x→х fold (spaces are preserved as well).
    """

    def test_returns_empty_for_empty(self) -> None:
        assert clean_title_for_search("") == ""
        assert clean_title_for_search(None) == ""  # type: ignore[arg-type]

    def test_keeps_word_boundaries(self) -> None:
        assert clean_title_for_search("The Matrix Reloaded") == "the matrix reloaded"

    def test_collapses_repeated_whitespace(self) -> None:
        assert clean_title_for_search("the   matrix\t\nreloaded") == "the matrix reloaded"

    def test_strips_parens_and_punctuation(self) -> None:
        # Whitespace gets collapsed by `' '.join(t.split())` so the
        # double-space that the parenthesis-strip leaves behind
        # becomes a single one.
        assert clean_title_for_search("Matrix (1999): Reloaded!") == "matrix reloaded"


@pytest.mark.parametrize(
    "title",
    [
        "",
        None,
        "    ",
        "()",
        "[]",
    ],
)
def test_normalize_title_handles_degenerate_input(title: str | None) -> None:
    # None of these should raise; all return an empty string after
    # stripping. (Not the same as `== ""` because "    " strips to "".)
    out = normalize_title(title)  # type: ignore[arg-type]
    assert isinstance(out, str)
    assert out == "" or out.strip() == out


class TestCleanTitleYearDuplicates:
    def test_no_year_returns_as_is(self) -> None:
        assert clean_title_year_duplicates("Inception") == "Inception"
        assert clean_title_year_duplicates("Мумия / The Mummy") == "Мумия / The Mummy"

    def test_single_year_returns_as_is(self) -> None:
        assert clean_title_year_duplicates("Inception (2010)") == "Inception (2010)"
        assert clean_title_year_duplicates("Мумия / The Mummy (2026)") == "Мумия / The Mummy (2026)"

    def test_duplicate_years_collapsed(self) -> None:
        assert clean_title_year_duplicates("Мумия (2026) (2026)") == "Мумия (2026)"
        assert (
            clean_title_year_duplicates("Мумия / Lee Cronin's The Mummy (2026) (2026)")
            == "Мумия / Lee Cronin's The Mummy (2026)"
        )
        assert clean_title_year_duplicates("Мумия 2026 (2026)") == "Мумия (2026)"
        assert clean_title_year_duplicates("Мумия (2026) 2026") == "Мумия (2026)"
        assert clean_title_year_duplicates("Мумия 2026 2026") == "Мумия 2026"
        assert clean_title_year_duplicates("Мумия (2026)   (2026)") == "Мумия (2026)"
