import asyncio
import logging
from typing import Dict, Any

from core.models import Article
from core.utils import determine_importance_emoji, truncate_text, add_utm

log = logging.getLogger("bergfrid.publisher.twitter")


class TwitterPublisher:
    name = "twitter"

    def __init__(self, api_key: str, api_secret: str,
                 access_token: str, access_secret: str,
                 tweet_max: int = 280,
                 max_retries: int = 3, retry_base_delay: float = 5):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.access_secret = access_secret
        self.tweet_max = tweet_max
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import tweepy
        except ImportError:
            log.error("tweepy non installe. pip install tweepy")
            return None
        self._client = tweepy.Client(
            consumer_key=self.api_key,
            consumer_secret=self.api_secret,
            access_token=self.access_token,
            access_token_secret=self.access_secret,
        )
        return self._client

    def _build_tweet(self, article: Article) -> str:
        url = add_utm(article.url, source="twitter", medium="social", campaign="rss")
        emoji = determine_importance_emoji(article.summary)

        # URL takes ~23 chars (t.co shortening) + 1 newline
        url_budget = 24
        available = self.tweet_max - url_budget - 1  # 1 for \n before url

        if article.social_summary:
            # Use social_summary as main text
            text = f"{emoji} {article.social_summary}"
            text = truncate_text(text, available)
            return f"{text}\n{url}"
        else:
            # Fallback: title only
            text = f"{emoji} {article.title}"
            text = truncate_text(text, available)
            return f"{text}\n{url}"

    def _post_tweet(self, text: str) -> bool:
        """Synchronous tweet post (called via to_thread)."""
        client = self._ensure_client()
        if client is None:
            return False

        import tweepy

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = client.create_tweet(text=text)
                if resp and resp.data:
                    return True
                log.warning("Twitter: reponse inattendue: %s", resp)
                return False
            except tweepy.TooManyRequests as e:
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                log.warning(
                    "Twitter rate limit (429). Retry dans %.1fs (tentative %d/%d).",
                    delay, attempt, self.max_retries,
                )
                if attempt < self.max_retries:
                    import time
                    time.sleep(delay)
                continue
            except tweepy.TwitterServerError as e:
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                log.warning(
                    "Twitter erreur serveur %s. Retry dans %.1fs (tentative %d/%d).",
                    e, delay, attempt, self.max_retries,
                )
                if attempt < self.max_retries:
                    import time
                    time.sleep(delay)
                continue
            except tweepy.TweepyException as e:
                log.error("Twitter erreur API (tentative %d/%d): %s", attempt, self.max_retries, e)
                return False

        log.error("Twitter: echec apres %d tentatives.", self.max_retries)
        return False

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        try:
            text = self._build_tweet(article)
            ok = await asyncio.to_thread(self._post_tweet, text)
            if ok:
                log.info("Twitter: publie '%s'.", article.title[:60])
            else:
                log.error("Twitter: echec publication '%s'.", article.title[:60])
            return ok
        except Exception as e:
            log.exception("Erreur inattendue publication Twitter: %s", e)
            return False
