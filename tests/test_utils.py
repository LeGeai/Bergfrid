import pytest
from core.utils import (
    truncate_text,
    determine_importance_emoji,
    strip_html_to_text,
    prettify_summary,
    extract_tags_from_terms,
    add_utm,
)


# ── truncate_text ──────────────────────────────────────────────

class TestTruncateText:
    def test_short_text_unchanged(self):
        assert truncate_text("hello", 10) == "hello"

    def test_exact_limit(self):
        assert truncate_text("hello", 5) == "hello"

    def test_truncates_with_ellipsis(self):
        result = truncate_text("hello world", 8)
        assert result == "hello..."
        assert len(result) == 8

    def test_empty_string(self):
        assert truncate_text("", 5) == ""

    def test_none_input(self):
        assert truncate_text(None, 5) == ""

    def test_very_small_limit(self):
        result = truncate_text("hello", 3)
        assert result == "..."


# ── determine_importance_emoji ─────────────────────────────────

class TestDetermineImportanceEmoji:
    def test_critical_keyword(self):
        assert determine_importance_emoji("Situation critique en Ukraine") == "\U0001f525"

    def test_guerre_keyword(self):
        assert determine_importance_emoji("La guerre continue") == "\U0001f525"

    def test_normal_text(self):
        assert determine_importance_emoji("Analyse economique du trimestre") == "\U0001f4f0"

    def test_case_insensitive(self):
        assert determine_importance_emoji("ALERTE MAXIMALE") == "\U0001f525"

    def test_empty_text(self):
        assert determine_importance_emoji("") == "\U0001f4f0"

    def test_none_text(self):
        assert determine_importance_emoji(None) == "\U0001f4f0"


# ── strip_html_to_text ────────────────────────────────────────

class TestStripHtmlToText:
    def test_simple_html(self):
        result = strip_html_to_text("<p>Hello <b>world</b></p>")
        assert "Hello" in result
        assert "world" in result
        assert "<" not in result

    def test_br_tags(self):
        result = strip_html_to_text("line1<br/>line2")
        assert "line1" in result
        assert "line2" in result

    def test_empty_string(self):
        assert strip_html_to_text("") == ""

    def test_none_input(self):
        assert strip_html_to_text(None) == ""

    def test_html_entities(self):
        result = strip_html_to_text("&amp; &lt; &gt;")
        assert "&" in result

    def test_collapses_multiple_newlines(self):
        result = strip_html_to_text("<p>a</p><p>b</p><p>c</p><p>d</p>")
        assert "\n\n\n" not in result


# ── prettify_summary ──────────────────────────────────────────

class TestPrettifySummary:
    def test_no_prefix_by_default(self):
        result = prettify_summary("paragraph one", 1000)
        assert result == "paragraph one"

    def test_custom_prefix(self):
        result = prettify_summary("paragraph one", 1000, prefix="\u203a ")
        assert result.startswith("\u203a ")

    def test_limits_paragraphs_default(self):
        text = "\n".join(f"paragraph {i}" for i in range(20))
        result = prettify_summary(text, 5000)
        # default max_paragraphs=5
        assert result.count("paragraph") == 5

    def test_limits_paragraphs_custom(self):
        text = "\n".join(f"paragraph {i}" for i in range(20))
        result = prettify_summary(text, 5000, max_paragraphs=3)
        assert result.count("paragraph") == 3

    def test_truncates_to_max(self):
        text = "a" * 500
        result = prettify_summary(text, 100)
        assert len(result) <= 100

    def test_empty_input(self):
        assert prettify_summary("", 100) == ""

    def test_none_input(self):
        assert prettify_summary(None, 100) == ""


# ── extract_tags_from_terms ───────────────────────────────────

class TestExtractTagsFromTerms:
    def test_simple_terms(self):
        result = extract_tags_from_terms(["geopolitique", "defense"])
        assert result == ["#geopolitique", "#defense"]

    def test_already_hashed(self):
        result = extract_tags_from_terms(["#geopolitique"])
        assert result == ["#geopolitique"]

    def test_semicolon_delimited(self):
        result = extract_tags_from_terms(["geo;defense;strategie"])
        assert "#geo" in result
        assert "#defense" in result
        assert "#strategie" in result

    def test_deduplicates_case_insensitive(self):
        result = extract_tags_from_terms(["France", "france", "FRANCE"])
        assert len(result) == 1

    def test_empty_terms(self):
        assert extract_tags_from_terms([]) == []

    def test_none_in_terms(self):
        result = extract_tags_from_terms([None, "", "  "])
        assert result == []


# ── add_utm ───────────────────────────────────────────────────

class TestAddUtm:
    def test_adds_utm_params(self):
        result = add_utm("https://bergfrid.com/article", "discord")
        assert "utm_source=discord" in result
        assert "utm_medium=social" in result
        assert "utm_campaign=rss" in result

    def test_preserves_existing_params(self):
        result = add_utm("https://bergfrid.com/article?foo=bar", "telegram")
        assert "foo=bar" in result
        assert "utm_source=telegram" in result

    def test_does_not_overwrite_existing_utm(self):
        result = add_utm("https://bergfrid.com/article?utm_source=existing", "discord")
        assert "utm_source=existing" in result

    def test_empty_url_still_adds_params(self):
        result = add_utm("", "discord")
        assert "utm_source=discord" in result
