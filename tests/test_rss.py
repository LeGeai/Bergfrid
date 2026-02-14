import time
import pytest
from types import SimpleNamespace
from core.rss import _entry_id, _entry_html, _author, _category, _published_dt, feed_to_backlog, entry_to_article


def _make_entry(**kwargs):
    """Create a mock feedparser entry."""
    defaults = {
        "id": "https://bergfrid.com/article-1",
        "title": "Test Article",
        "link": "https://bergfrid.com/article-1",
        "description": "This is a test description.",
        "summary": "This is a test summary.",
        "author": "Jean Dupont",
        "category": "Geopolitique",
        "content": None,
        "tags": None,
        "published_parsed": time.struct_time((2024, 6, 15, 12, 0, 0, 5, 167, 0)),
        "updated_parsed": None,
        "guid": None,
        "dc_creator": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── _entry_id ─────────────────────────────────────────────────

class TestEntryId:
    def test_uses_id(self):
        e = _make_entry(id="my-id")
        assert _entry_id(e) == "my-id"

    def test_falls_back_to_guid(self):
        e = _make_entry(id=None, guid="my-guid")
        assert _entry_id(e) == "my-guid"

    def test_falls_back_to_link(self):
        e = _make_entry(id=None, guid=None, link="https://example.com")
        assert _entry_id(e) == "https://example.com"

    def test_falls_back_to_title(self):
        e = _make_entry(id=None, guid=None, link=None, title="My Title")
        assert _entry_id(e) == "My Title"


# ── _entry_html ───────────────────────────────────────────────

class TestEntryHtml:
    def test_uses_content_first(self):
        content_obj = SimpleNamespace(value="<p>Content HTML</p>")
        e = _make_entry(content=[content_obj], description="Fallback")
        assert _entry_html(e) == "<p>Content HTML</p>"

    def test_falls_back_to_description(self):
        e = _make_entry(content=None, description="Description text")
        assert _entry_html(e) == "Description text"

    def test_falls_back_to_summary(self):
        e = _make_entry(content=None, description="", summary="Summary text")
        assert _entry_html(e) == "Summary text"


# ── _author ───────────────────────────────────────────────────

class TestAuthor:
    def test_uses_author(self):
        e = _make_entry(author="Alice")
        assert _author(e) == "Alice"

    def test_falls_back_to_dc_creator(self):
        e = _make_entry(author=None, dc_creator="Bob")
        assert _author(e) == "Bob"

    def test_default_redaction(self):
        e = _make_entry(author=None, dc_creator=None)
        assert _author(e) == "Redaction"


# ── _category ─────────────────────────────────────────────────

class TestCategory:
    def test_uses_category(self):
        e = _make_entry(category="Defense")
        assert _category(e) == "Defense"

    def test_falls_back_to_first_tag(self):
        tag = SimpleNamespace(term="Strategie")
        e = _make_entry(category=None, tags=[tag])
        assert _category(e) == "Strategie"

    def test_empty_when_nothing(self):
        e = _make_entry(category=None, tags=None)
        assert _category(e) == ""


# ── _published_dt ─────────────────────────────────────────────

class TestPublishedDt:
    def test_parses_struct_time(self):
        e = _make_entry(published_parsed=time.struct_time((2024, 3, 15, 10, 30, 0, 4, 75, 0)))
        dt = _published_dt(e)
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15

    def test_none_when_missing(self):
        e = _make_entry(published_parsed=None, updated_parsed=None)
        assert _published_dt(e) is None


# ── feed_to_backlog ───────────────────────────────────────────

class TestFeedToBacklog:
    def test_returns_entries_before_last_seen(self):
        entries = [_make_entry(id=f"id-{i}") for i in range(5)]
        feed = SimpleNamespace(entries=entries)
        backlog = feed_to_backlog(feed, "id-2")
        assert len(backlog) == 2  # id-0, id-1

    def test_returns_all_when_last_seen_not_found(self):
        entries = [_make_entry(id=f"id-{i}") for i in range(3)]
        feed = SimpleNamespace(entries=entries)
        backlog = feed_to_backlog(feed, "nonexistent")
        assert len(backlog) == 3

    def test_returns_empty_when_last_seen_is_first(self):
        entries = [_make_entry(id="latest"), _make_entry(id="old")]
        feed = SimpleNamespace(entries=entries)
        backlog = feed_to_backlog(feed, "latest")
        assert len(backlog) == 0

    def test_empty_feed(self):
        feed = SimpleNamespace(entries=[])
        assert feed_to_backlog(feed, "any") == []


# ── entry_to_article ──────────────────────────────────────────

class TestEntryToArticle:
    def test_basic_conversion(self):
        e = _make_entry()
        article = entry_to_article(e, "https://bergfrid.com")
        assert article.id == "https://bergfrid.com/article-1"
        assert article.title == "Test Article"
        assert article.author == "Jean Dupont"
        assert article.source == "Bergfrid"

    def test_url_resolved(self):
        e = _make_entry(link="/relative-path")
        article = entry_to_article(e, "https://bergfrid.com")
        assert article.url == "https://bergfrid.com/relative-path"

    def test_tags_extracted(self):
        tags = [SimpleNamespace(term="geopolitique"), SimpleNamespace(term="defense")]
        e = _make_entry(tags=tags)
        article = entry_to_article(e, "https://bergfrid.com")
        assert "#geopolitique" in article.tags
        assert "#defense" in article.tags
