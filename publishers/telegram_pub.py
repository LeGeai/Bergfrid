import asyncio
import json
import html as htmlmod
import logging
from typing import Dict, Any, Optional

from core.models import Article
from core.utils import determine_importance_emoji, prettify_summary, add_utm

log = logging.getLogger("bergfrid.publisher.telegram")


class TelegramPublisher:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, summary_max: int = 900,
                 max_retries: int = 3, retry_base_delay: float = 5):
        self.token = token
        self.chat_id = chat_id
        self.summary_max = summary_max
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._session = None

    async def _ensure_session(self):
        try:
            import aiohttp
        except ImportError:
            log.error("aiohttp non installe, impossible d'envoyer sur Telegram.")
            return None
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _send_with_retry(self, endpoint: str, payload: dict) -> Optional[int]:
        """Send a Telegram API request with retry on 429 and 5xx.

        Returns the message_id on success, None on failure.
        """
        sess = await self._ensure_session()
        if sess is None:
            return None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with sess.post(endpoint, data=payload) as resp:
                    if resp.status == 200:
                        try:
                            data = json.loads(await resp.text())
                            return data.get("result", {}).get("message_id")
                        except Exception:
                            return -1  # success but could not parse id

                    body = await resp.text()

                    if resp.status == 429:
                        try:
                            data = json.loads(body)
                            retry_after = data.get("parameters", {}).get("retry_after", self.retry_base_delay)
                        except Exception:
                            retry_after = self.retry_base_delay * attempt
                        log.warning(
                            "Telegram rate limit (429). Retry dans %ss (tentative %d/%d).",
                            retry_after, attempt, self.max_retries,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    if resp.status >= 500:
                        delay = self.retry_base_delay * (2 ** (attempt - 1))
                        log.warning(
                            "Telegram erreur serveur %d. Retry dans %.1fs (tentative %d/%d).",
                            resp.status, delay, attempt, self.max_retries,
                        )
                        await asyncio.sleep(delay)
                        continue

                    # 4xx (sauf 429) = erreur client, pas de retry
                    log.error("Telegram erreur client %d: %s", resp.status, body[:600])
                    return None

            except asyncio.TimeoutError:
                log.warning("Telegram timeout (tentative %d/%d).", attempt, self.max_retries)
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_base_delay * attempt)
                continue
            except Exception as e:
                log.error("Telegram exception (tentative %d/%d): %s", attempt, self.max_retries, e)
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_base_delay * attempt)
                continue

        log.error("Telegram: echec apres %d tentatives.", self.max_retries)
        return None

    async def set_reaction(self, message_id: int, emoji: str) -> None:
        """Set a reaction on a Telegram message."""
        if not message_id or message_id < 0:
            return
        sess = await self._ensure_session()
        if sess is None:
            return
        endpoint = f"https://api.telegram.org/bot{self.token}/setMessageReaction"
        payload = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "reaction": json.dumps([{"type": "emoji", "emoji": emoji}]),
        }
        try:
            async with sess.post(endpoint, data=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("Telegram setReaction erreur %d: %s", resp.status, body[:300])
        except Exception as e:
            log.warning("Telegram setReaction exception: %s", e)

    def _build_caption(self, article: Article, url: str, use_photo: bool) -> str:
        """Build message text / photo caption."""
        emoji = determine_importance_emoji(article.summary)

        # Photo captions limited to 1024 chars
        max_summary = 700 if use_photo else self.summary_max
        pretty = prettify_summary(
            article.summary, max_summary, prefix="", max_paragraphs=4
        )

        parts = [f"{emoji} <b>{htmlmod.escape(article.title)}</b>"]
        parts.append("")
        parts.append(htmlmod.escape(pretty))

        # Meta line: category + date
        meta_bits = []
        if article.category:
            meta_bits.append(article.category)
        if article.published_at:
            meta_bits.append(article.published_at.strftime("%d %b %Y"))
        if meta_bits:
            parts.append("")
            parts.append(f"<i>{htmlmod.escape(' \u00b7 '.join(meta_bits))}</i>")

        # Hashtags
        if article.tags:
            parts.append("")
            parts.append(" ".join(article.tags[:6]))

        text = "\n".join(parts).strip()

        # Telegram caption limit = 1024
        if use_photo and len(text) > 1024:
            text = text[:1021] + "..."

        return text

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        try:
            url = add_utm(article.url, source="telegram", medium="social", campaign="rss")
            use_photo = bool(article.image_url)
            text = self._build_caption(article, url, use_photo)

            # Inline button "Lire l'article"
            reply_markup = json.dumps({
                "inline_keyboard": [[
                    {"text": "\U0001f4d6 Lire l'article", "url": url}
                ]]
            })

            if use_photo:
                endpoint = f"https://api.telegram.org/bot{self.token}/sendPhoto"
                payload = {
                    "chat_id": self.chat_id,
                    "photo": article.image_url,
                    "caption": text,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup,
                }
            else:
                endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
                payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                    "reply_markup": reply_markup,
                }

            msg_id = await self._send_with_retry(endpoint, payload)
            if msg_id is not None:
                log.info("Telegram: publie '%s'.", article.title[:60])
                await self.set_reaction(msg_id, "\U0001f44d")
                return True
            else:
                log.error("Telegram: echec publication '%s'.", article.title[:60])
                return False

        except Exception as e:
            log.exception("Erreur inattendue publication Telegram: %s", e)
            return False
