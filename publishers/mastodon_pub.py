import asyncio
import logging
import urllib.request
from typing import Dict, Any, Optional

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

    def _upload_image(self, image_url: str) -> Optional[dict]:
        """Download article image and upload to Mastodon as media attachment."""
        client = self._ensure_client()
        if not client:
            return None
        try:
            req = urllib.request.Request(
                image_url, headers={"User-Agent": "Bergfrid-Bot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")

            # Mastodon.py media_post accepts file-like or bytes via file_name
            import tempfile
            import os
            ext = ".jpg" if "jpeg" in content_type else ".png"
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            try:
                media = client.media_post(tmp_path, mime_type=content_type)
                return media
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            log.warning("Mastodon: echec upload image: %s", e)
            return None

    def _post_status(self, text: str, media_ids: Optional[list] = None) -> bool:
        """Synchronous post (called via to_thread)."""
        client = self._ensure_client()
        if client is None:
            return False

        from mastodon import MastodonError, MastodonRatelimitError, MastodonServerError

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = client.status_post(
                    text, visibility="public", media_ids=media_ids
                )
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

            media_ids = None
            if article.image_url:
                media = await asyncio.to_thread(self._upload_image, article.image_url)
                if media:
                    media_ids = [media["id"]]

            ok = await asyncio.to_thread(self._post_status, text, media_ids)
            if ok:
                log.info("Mastodon: publie '%s'.", article.title[:60])
            else:
                log.error("Mastodon: echec publication '%s'.", article.title[:60])
            return ok
        except Exception as e:
            log.exception("Erreur inattendue publication Mastodon: %s", e)
            return False
