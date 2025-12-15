import re
import html
from typing import List
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse


def truncate_text(text: str, limit: int) -> str:
    text = text or ""
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def determine_importance_emoji(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ["critique", "urgent", "alerte", "attaque", "explosion", "guerre"]):
        return "🔥"
    return "📰"


def strip_html_to_text(raw_html: str) -> str:
    raw_html = raw_html or ""
    try:
        from bs4 import BeautifulSoup  # type: ignore
        text = BeautifulSoup(raw_html, "html.parser").get_text("\n")
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text
    except Exception:
        txt = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.I)
        txt = re.sub(r"</p\s*>", "\n\n", txt, flags=re.I)
        txt = re.sub(r"<[^>]+>", "", txt)
        txt = html.unescape(txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
        return txt


def prettify_summary(text: str, max_chars: int, prefix: str = "› ") -> str:
    text = (text or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if len(paras) > 7:
        paras = paras[:7]
    pretty = "\n\n".join(prefix + p for p in paras)
    return truncate_text(pretty, max_chars)


def extract_tags_from_terms(terms: List[str]) -> List[str]:
    tags_out: List[str] = []
    for term in terms:
        term = (term or "").strip()
        if not term:
            continue
        parts = re.split(r"[;,/|]\s*|\s+#", term.replace("#", " #").strip())
        for p in parts:
            p = re.sub(r"\s+", "", p.strip())
            if not p:
                continue
            if not p.startswith("#"):
                p = "#" + p
            tags_out.append(p)

    seen = set()
    clean = []
    for x in tags_out:
        key = x.lower()
        if key not in seen:
            seen.add(key)
            clean.append(x)
    return clean


def add_utm(url: str, source: str, medium: str = "social", campaign: str = "rss") -> str:
    try:
        u = urlparse(url)
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        q.setdefault("utm_source", source)
        q.setdefault("utm_medium", medium)
        q.setdefault("utm_campaign", campaign)
        new_query = urlencode(q, doseq=True)
        return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
    except Exception:
        return url
