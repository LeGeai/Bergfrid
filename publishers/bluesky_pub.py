import asyncio
import logging
import re
from typing import Dict, Any

from core.models import Article
from core.utils import determine_importance_emoji, truncate_text, add_utm

log = logging.getLogger("bergfrid.publisher.bluesky")


class BlueskyPublisher:
    name = "bluesky"

    def __init__(self, handle: str, app_password: str,
                 post_max: int = 300,
                 max_retries: int = 3, retry_base_delay: float = 5):
        self.handle = handle
        self.app_password = app_password
        self.post_max = post_max
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from atproto import Client
        except ImportError:
            log.error("atproto non installe. pip install atproto")
            return None
        self._client = Client()
        try:
            self._client.login(self.handle, self.app_password)
        except Exception as e:
            log.error("Bluesky: echec login pour %s: %s", self.handle, e)
            self._client = None
            return None
        return self._client

    def _build_post_text(self, article: Article) -> str:
        url = add_utm(article.url, source="bluesky", medium="social", campaign="rss")
        emoji = determine_importance_emoji(article.summary)

        url_budget = len(url) + 1  # +1 for \n before url
        available = self.post_max - url_budget

        if article.social_summary:
            text = f"{emoji} {article.social_summary}"
            text = truncate_text(text, available)
            return f"{text}\n{url}"
        else:
            text = f"{emoji} {article.title}"
            text = truncate_text(text, available)
            return f"{text}\n{url}"

    @staticmethod
    def _detect_facets(text: str):
        """Detect URLs in text and build Bluesky facets for clickable links."""
        try:
            from atproto import models
        except ImportError:
            return None

        facets = []
        url_pattern = re.compile(r'https?://\S+')
        text_bytes = text.encode('utf-8')

        for match in url_pattern.finditer(text):
            url = match.group(0)
            # Calculate byte positions for facet
            start_byte = len(text[:match.start()].encode('utf-8'))
            end_byte = start_byte + len(url.encode('utf-8'))
            facets.append(
                models.AppBskyRichtextFacet.Main(
                    index=models.AppBskyRichtextFacet.ByteSlice(
                        byte_start=start_byte,
                        byte_end=end_byte,
                    ),
                    features=[
                        models.AppBskyRichtextFacet.Link(uri=url),
                    ],
                )
            )
        return facets if facets else None

    def _post_skeet(self, text: str) -> bool:
        """Synchronous post (called via to_thread)."""
        client = self._ensure_client()
        if client is None:
            return False

        for attempt in range(1, self.max_retries + 1):
            try:
                facets = self._detect_facets(text)
                resp = client.send_post(text=text, facets=facets)
                if resp and resp.uri:
                    return True
                log.warning("Bluesky: reponse inattendue: %s", resp)
                return False
            except Exception as e:
                err_str = str(e).lower()
                if "rate" in err_str or "limit" in err_str or "429" in err_str:
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "Bluesky rate limit. Retry dans %.1fs (tentative %d/%d).",
                        delay, attempt, self.max_retries,
                    )
                    if attempt < self.max_retries:
                        import time
                        time.sleep(delay)
                    continue
                elif "500" in err_str or "502" in err_str or "503" in err_str:
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "Bluesky erreur serveur. Retry dans %.1fs (tentative %d/%d).",
                        delay, attempt, self.max_retries,
                    )
                    if attempt < self.max_retries:
                        import time
                        time.sleep(delay)
                    continue
                else:
                    log.error("Bluesky erreur API (tentative %d/%d): %s", attempt, self.max_retries, e)
                    return False

        log.error("Bluesky: echec apres %d tentatives.", self.max_retries)
        return False

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        try:
            text = self._build_post_text(article)
            ok = await asyncio.to_thread(self._post_skeet, text)
            if ok:
                log.info("Bluesky: publie '%s'.", article.title[:60])
            else:
                log.error("Bluesky: echec publication '%s'.", article.title[:60])
            return ok
        except Exception as e:
            log.exception("Erreur inattendue publication Bluesky: %s", e)
            return False
