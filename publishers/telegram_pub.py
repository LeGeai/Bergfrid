import html as htmlmod
from typing import Dict, Any, Optional

from core.models import Article
from core.utils import determine_importance_emoji, prettify_summary, add_utm


class TelegramPublisher:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, summary_max: int = 900):
        self.token = token
        self.chat_id = chat_id
        self.summary_max = summary_max
        self._session = None

    async def _ensure_session(self):
        try:
            import aiohttp
        except ImportError:
            return None
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        sess = await self._ensure_session()
        if sess is None:
            return False

        try:
            url = add_utm(article.url, source="telegram", medium="social", campaign="rss")
            emoji = determine_importance_emoji(article.summary)
            tags_str = " ".join(article.tags)

            pretty = prettify_summary(article.summary, self.summary_max, prefix="› ")

            meta_bits = []
            if article.author:
                meta_bits.append(article.author)
            if article.category:
                meta_bits.append(article.category)
            if article.published_at:
                meta_bits.append(article.published_at.strftime("%d %b %Y"))
            meta_line = " • ".join(meta_bits).strip()

            parts = []
            parts.append(f"{emoji} <b>{htmlmod.escape(article.title)}</b>")
            if meta_line:
                parts.append(f"<i>{htmlmod.escape(meta_line)}</i>")
            parts.append("")
            parts.append(htmlmod.escape(pretty))
            parts.append("")
            parts.append(f"👉 <a href='{htmlmod.escape(url)}'>Lire l'article</a>")

            if tags_str:
                parts.append("")
                parts.append(f"<i>{htmlmod.escape(tags_str)}</i>")

            parts.append(f"<i>Source: Bergfrid</i>")

            text = "\n".join(parts).strip()

            endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }

            async with sess.post(endpoint, data=payload) as resp:
                if resp.status != 200:
                    return False
            return True
        except Exception:
            return False
