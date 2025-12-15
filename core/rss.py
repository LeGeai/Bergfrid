from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import feedparser

from core.models import Article
from core.utils import strip_html_to_text, extract_tags_from_terms


def _entry_id(entry: Any) -> str:
    for attr in ("id", "guid", "link"):
        v = getattr(entry, attr, None)
        if v:
            return str(v)
    return str(getattr(entry, "title", "unknown"))


def _entry_html(entry: Any) -> str:
    content = getattr(entry, "content", None)
    if content and isinstance(content, list) and len(content) > 0:
        v = getattr(content[0], "value", None)
        if v:
            return str(v)
    return str(getattr(entry, "description", "") or getattr(entry, "summary", "") or "")


def _author(entry: Any) -> str:
    a = getattr(entry, "author", None)
    if a:
        return str(a).strip()
    dc = getattr(entry, "dc_creator", None)
    if dc:
        return str(dc).strip()
    return "Rédaction"


def _category(entry: Any) -> str:
    c = getattr(entry, "category", None)
    if c:
        return str(c).strip()
    tags = getattr(entry, "tags", None) or []
    if tags:
        term = getattr(tags[0], "term", None)
        if term:
            return str(term).strip()
    return ""


def _published_dt(entry: Any) -> Optional[datetime]:
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if st:
        try:
            return datetime(*st[:6], tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def parse_rss_with_cache(url: str, base_domain: str, state: Dict[str, Any]) -> Any:
    feed = feedparser.parse(url, etag=state.get("etag"), modified=state.get("modified"))
    if getattr(feed, "etag", None):
        state["etag"] = feed.etag
    if getattr(feed, "modified", None):
        state["modified"] = feed.modified
    return feed


def feed_to_backlog(feed: Any, last_seen: str) -> List[Any]:
    entries = getattr(feed, "entries", None) or []
    backlog = []
    for e in entries:
        eid = _entry_id(e)
        if eid == last_seen:
            break
        backlog.append(e)
    return backlog


def entry_to_article(entry: Any, base_domain: str) -> Article:
    eid = _entry_id(entry)
    title = str(getattr(entry, "title", "Sans titre"))
    raw_link = str(getattr(entry, "link", "") or "")
    url = urljoin(base_domain, raw_link)

    raw_html = _entry_html(entry)
    summary = strip_html_to_text(raw_html)

    raw_terms = []
    for t in (getattr(entry, "tags", None) or []):
        term = getattr(t, "term", None)
        if term:
            raw_terms.append(str(term))

    tags = extract_tags_from_terms(raw_terms)

    return Article(
        id=eid,
        title=title,
        url=url,
        summary=summary,
        tags=tags,
        author=_author(entry),
        category=_category(entry),
        published_at=_published_dt(entry),
        source="Bergfrid",
    )
