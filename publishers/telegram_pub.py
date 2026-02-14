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

    async def _send_with_retry(self, endpoint: str, payload: dict) -> bool:
        """Send a Telegram API request with retry on 429 and 5xx."""
        sess = await self._ensure_session()
        if sess is None:
            return False

        for attempt in range(1, self.max_retries + 1):
            try:
                async with sess.post(endpoint, data=payload) as resp:
                    if resp.status == 200:
                        return True

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
                    return False

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
        return False

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        try:
            url = add_utm(article.url, source="telegram", medium="social", campaign="rss")
            emoji = determine_importance_emoji(article.summary)
            tags_str = " ".join(article.tags)

            # Clean summary: no prefix, 4 paragraphs max
            pretty = prettify_summary(
                article.summary, self.summary_max, prefix="", max_paragraphs=4
            )

            # --- Build message ---
            # Line 1: emoji + title (bold, hero element)
            parts = [f"{emoji} <b>{htmlmod.escape(article.title)}</b>"]

            # Blank line + clean summary
            parts.append("")
            parts.append(htmlmod.escape(pretty))

            # Blank line + link CTA
            parts.append("")
            parts.append(f"<a href='{htmlmod.escape(url)}'>Lire sur Bergfrid \u2192</a>")

            # Meta: author + category + date (compact, on one line)
            meta_bits = []
            if article.author:
                meta_bits.append(article.author)
            if article.category:
                meta_bits.append(article.category)
            if article.published_at:
                meta_bits.append(article.published_at.strftime("%d %b %Y"))
            if meta_bits:
                meta_line = " \u00b7 ".join(meta_bits)
                parts.append(f"<i>{htmlmod.escape(meta_line)}</i>")

            # Tags (last line, no wrapping)
            if tags_str:
                parts.append(htmlmod.escape(tags_str))

            text = "\n".join(parts).strip()

            endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }

            ok = await self._send_with_retry(endpoint, payload)
            if ok:
                log.info("Telegram: publie '%s'.", article.title[:60])
            else:
                log.error("Telegram: echec publication '%s'.", article.title[:60])
            return ok

        except Exception as e:
            log.exception("Erreur inattendue publication Telegram: %s", e)
            return False
