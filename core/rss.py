import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import feedparser

from core.models import Article
from core.utils import strip_html_to_text, extract_tags_from_terms

log = logging.getLogger("bergfrid.rss")


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
    return "Redaction"


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


def _image_url(entry: Any, base_domain: str) -> str:
    """Extract article image URL from media:content, media:thumbnail, or enclosure."""
    mc = getattr(entry, "media_content", None) or []
    if mc and isinstance(mc, list):
        for m in mc:
            url = m.get("url", "")
            if url:
                return urljoin(base_domain, url)
    mt = getattr(entry, "media_thumbnail", None) or []
    if mt and isinstance(mt, list):
        for m in mt:
            url = m.get("url", "")
            if url:
                return urljoin(base_domain, url)
    enc = getattr(entry, "enclosures", None) or []
    if enc and isinstance(enc, list):
        for e in enc:
            url = e.get("href", "") or e.get("url", "")
            if url and "image" in e.get("type", ""):
                return urljoin(base_domain, url)
    return ""


def _published_dt(entry: Any) -> Optional[datetime]:
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if st:
        try:
            return datetime(*st[:6], tzinfo=timezone.utc)
        except Exception:
            log.debug("Impossible de parser la date de publication.")
            return None
    return None


def _parse_rss_sync(url: str, etag: Optional[str], modified: Optional[str]) -> Any:
    """Synchronous RSS parse (called via asyncio.to_thread)."""
    return feedparser.parse(url, etag=etag, modified=modified)


async def parse_rss_with_cache(url: str, base_domain: str, state: Dict[str, Any],
                                timeout: float = 30) -> Any:
    """Async RSS fetch with timeout. Runs feedparser in a thread pool."""
    try:
        feed = await asyncio.wait_for(
            asyncio.to_thread(_parse_rss_sync, url, state.get("etag"), state.get("modified")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.error("Timeout RSS apres %.0fs pour %s", timeout, url)
        return feedparser.parse("")
    except Exception as e:
        log.error("Erreur fetch RSS: %s", e)
        return feedparser.parse("")

    if getattr(feed, "etag", None):
        state["etag"] = feed.etag
    if getattr(feed, "modified", None):
        state["modified"] = feed.modified

    status = getattr(feed, "status", None)
    entries = getattr(feed, "entries", None) or []
    log.debug("RSS fetch status=%s, entries=%d", status, len(entries))

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

    # social_summary: custom field > description (short) > empty
    social_raw = getattr(entry, "social_summary", None) or ""
    if not social_raw:
        desc = getattr(entry, "description", None) or ""
        if desc:
            social_raw = desc
    social_summary = strip_html_to_text(social_raw).strip() if social_raw else ""

    return Article(
        id=eid,
        title=title,
        url=url,
        summary=summary,
        tags=tags,
        author=_author(entry),
        category=_category(entry),
        published_at=_published_dt(entry),
        social_summary=social_summary,
        image_url=_image_url(entry, base_domain),
        source="Bergfrid",
    )
