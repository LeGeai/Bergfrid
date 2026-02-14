import asyncio
import logging
from typing import Dict, Any

from core.models import Article
from core.utils import determine_importance_emoji, truncate_text, add_utm

log = logging.getLogger("bergfrid.publisher.mastodon")


class MastodonPublisher:
    name = "mastodon"

    def __init__(self, instance_url: str, access_token: str,
                 post_max: int = 500,
                 max_retries: int = 3, retry_base_delay: float = 5):
        self.instance_url = instance_url.rstrip("/")
        self.access_token = access_token
        self.post_max = post_max
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from mastodon import Mastodon
        except ImportError:
            log.error("Mastodon.py non installe. pip install Mastodon.py")
            return None
        self._client = Mastodon(
            access_token=self.access_token,
            api_base_url=self.instance_url,
        )
        return self._client

    def _build_post(self, article: Article) -> str:
        url = add_utm(article.url, source="mastodon", medium="social", campaign="rss")
        emoji = determine_importance_emoji(article.summary)

        # Reserve space for URL and hashtags
        hashtag_line = " ".join(article.tags[:5]) if article.tags else ""
        fixed_len = len(url) + 1  # \n before url
        if hashtag_line:
            fixed_len += len(hashtag_line) + 2  # \n\n before hashtags
        available = self.post_max - fixed_len

        if article.social_summary:
            text = f"{emoji} {article.social_summary}"
        else:
            text = f"{emoji} {article.title}"
        text = truncate_text(text, available)

        if hashtag_line:
            return f"{text}\n\n{hashtag_line}\n{url}"
        return f"{text}\n{url}"

    def _post_status(self, text: str) -> bool:
        """Synchronous post (called via to_thread)."""
        client = self._ensure_client()
        if client is None:
            return False

        from mastodon import MastodonError, MastodonRatelimitError, MastodonServerError

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = client.status_post(text, visibility="public")
                if resp and resp.get("id"):
                    return True
                log.warning("Mastodon: reponse inattendue: %s", resp)
                return False
            except MastodonRatelimitError:
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                log.warning(
                    "Mastodon rate limit. Retry dans %.1fs (tentative %d/%d).",
                    delay, attempt, self.max_retries,
                )
                if attempt < self.max_retries:
                    import time
                    time.sleep(delay)
                continue
            except MastodonServerError as e:
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                log.warning(
                    "Mastodon erreur serveur %s. Retry dans %.1fs (tentative %d/%d).",
                    e, delay, attempt, self.max_retries,
                )
                if attempt < self.max_retries:
                    import time
                    time.sleep(delay)
                continue
            except MastodonError as e:
                log.error("Mastodon erreur API (tentative %d/%d): %s", attempt, self.max_retries, e)
                return False

        log.error("Mastodon: echec apres %d tentatives.", self.max_retries)
        return False

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        try:
            text = self._build_post(article)
            ok = await asyncio.to_thread(self._post_status, text)
            if ok:
                log.info("Mastodon: publie '%s'.", article.title[:60])
            else:
                log.error("Mastodon: echec publication '%s'.", article.title[:60])
            return ok
        except Exception as e:
            log.exception("Erreur inattendue publication Mastodon: %s", e)
            return False
