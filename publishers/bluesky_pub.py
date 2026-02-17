import asyncio
import logging
import urllib.request
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
        """Build text body (without URL — URL goes in the embed card)."""
        emoji = determine_importance_emoji(article.summary)

        if article.social_summary:
            text = f"{emoji} {article.social_summary}"
        else:
            text = f"{emoji} {article.title}"

        # Hashtags
        if article.tags:
            hashtag_line = " ".join(article.tags[:5])
            budget = self.post_max - len(hashtag_line) - 2  # 2 for \n\n
            text = truncate_text(text, budget)
            text = f"{text}\n\n{hashtag_line}"
        else:
            text = truncate_text(text, self.post_max)

        return text

    def _upload_thumb(self, image_url: str):
        """Download image and upload as blob for embed thumbnail."""
        client = self._ensure_client()
        if not client:
            return None
        try:
            req = urllib.request.Request(
                image_url, headers={"User-Agent": "Bergfrid-Bot/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            blob_resp = client.upload_blob(data)
            return blob_resp.blob
        except Exception as e:
            log.warning("Bluesky: echec upload image: %s", e)
            return None

    def _build_embed(self, article: Article):
        """Build an external embed (link card) with optional thumbnail."""
        try:
            from atproto import models
        except ImportError:
            return None

        url = add_utm(article.url, source="bluesky", medium="social", campaign="rss")

        # Description: social_summary ou debut du summary
        description = article.social_summary or truncate_text(article.summary, 300)

        thumb = None
        if article.image_url:
            thumb = self._upload_thumb(article.image_url)

        return models.AppBskyEmbedExternal.Main(
            external=models.AppBskyEmbedExternal.External(
                uri=url,
                title=article.title,
                description=description,
                thumb=thumb,
            )
        )

    def _re_login(self) -> bool:
        """Force a fresh login (session expired)."""
        self._client = None
        return self._ensure_client() is not None

    def _post_skeet(self, text: str, embed):
        """Synchronous post (called via to_thread). Returns response or None."""
        client = self._ensure_client()
        if client is None:
            return None

        _relogged = False
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = client.send_post(text=text, embed=embed)
                if resp and resp.uri:
                    return resp
                log.warning("Bluesky: reponse inattendue: %s", resp)
                return None
            except Exception as e:
                err_str = str(e).lower()
                # atproto SDK: status_code sur e.response.status_code
                _resp = getattr(e, "response", None)
                status_code = getattr(_resp, "status_code", None)
                # Message d'erreur XRPC (ex: "Record/text must not be longer than 300 graphemes")
                _content = getattr(_resp, "content", None)
                xrpc_msg = getattr(_content, "message", None) or ""

                log.warning(
                    "Bluesky erreur (tentative %d/%d, status=%s): %s",
                    attempt, self.max_retries, status_code,
                    xrpc_msg or e,
                )

                # 400 Bad Request = contenu invalide, ne pas retry
                if status_code == 400 or (
                    "status_code=400" in err_str
                ):
                    log.error(
                        "Bluesky 400 Bad Request: %s (texte=%d chars, embed=%s)",
                        xrpc_msg or e, len(text),
                        type(embed).__name__ if embed else None,
                    )
                    return None

                # Rate limit (429) -> retry avec backoff
                if status_code == 429 or "ratelimit" in err_str.replace(" ", ""):
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "Bluesky rate limit. Retry dans %.1fs (tentative %d/%d).",
                        delay, attempt, self.max_retries,
                    )
                    if attempt < self.max_retries:
                        import time
                        time.sleep(delay)
                    continue

                # Auth / session expired -> re-login once
                if not _relogged and (
                    status_code == 401
                    or "expired" in err_str
                    or "unauthorized" in err_str
                    or ("auth" in err_str and "token" in err_str)
                ):
                    log.warning("Bluesky: session expiree, re-login...")
                    if self._re_login():
                        _relogged = True
                        client = self._client
                        continue
                    else:
                        log.error("Bluesky: echec re-login.")
                        return False

                # Erreur serveur (5xx) -> retry avec backoff
                if status_code and 500 <= status_code < 600:
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "Bluesky erreur serveur %d. Retry dans %.1fs (tentative %d/%d).",
                        status_code, delay, attempt, self.max_retries,
                    )
                    if attempt < self.max_retries:
                        import time
                        time.sleep(delay)
                    continue

                # Autre erreur inconnue -> ne pas retry
                log.error("Bluesky erreur inattendue: %s", e)
                return None

        log.error("Bluesky: echec apres %d tentatives.", self.max_retries)
        return None

    def _like_post(self, uri: str, cid: str) -> None:
        """Like own post to encourage interaction."""
        client = self._ensure_client()
        if not client:
            return
        try:
            client.like(uri=uri, cid=cid)
        except Exception as e:
            log.warning("Bluesky: echec like post: %s", e)

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        try:
            text = self._build_post_text(article)
            embed = self._build_embed(article)
            resp = await asyncio.to_thread(self._post_skeet, text, embed)
            if resp:
                log.info("Bluesky: publie '%s'.", article.title[:60])
                await asyncio.to_thread(self._like_post, resp.uri, resp.cid)
                return True
            else:
                log.error("Bluesky: echec publication '%s'.", article.title[:60])
                return False
        except Exception as e:
            log.exception("Erreur inattendue publication Bluesky: %s", e)
            return False
