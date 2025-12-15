import asyncio
from typing import Dict, Any, Optional, List

import discord

from core.models import Article
from core.utils import determine_importance_emoji, prettify_summary, truncate_text, add_utm


class DiscordPublisher:
    name = "discord"

    def __init__(self, bot: discord.Client, official_channel_id: int, channels_file: str,
                 send_delay: float = 0.2, summary_max: int = 2200):
        self.bot = bot
        self.official_channel_id = official_channel_id
        self.channels_file = channels_file
        self.send_delay = send_delay
        self.summary_max = summary_max

    def _load_channels(self) -> Dict[str, int]:
        import os, json
        if not os.path.exists(self.channels_file):
            return {}
        try:
            with open(self.channels_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            out = {}
            for k, v in data.items():
                try:
                    out[str(k)] = int(v)
                except Exception:
                    continue
            return out
        except Exception:
            return {}

    async def _resolve_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception:
            return None

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        try:
            url = add_utm(article.url, source="discord", medium="social", campaign="rss")
            tags_str = " ".join(article.tags)
            emoji = determine_importance_emoji(article.summary)

            desc = prettify_summary(article.summary, self.summary_max, prefix="› ")

            embed = discord.Embed(
                title=truncate_text(article.title, 256),
                url=url,
                description=desc,
                color=0x0B0F14,
            )

            meta_bits = []
            if article.author:
                meta_bits.append(article.author)
            if article.category:
                meta_bits.append(article.category)
            if article.published_at:
                meta_bits.append(article.published_at.strftime("%d %b %Y"))
            meta_line = " • ".join(meta_bits).strip()
            if meta_line:
                embed.add_field(name="Meta", value=truncate_text(meta_line, 1024), inline=False)

            embed.add_field(name="Lire", value=f"[Ouvrir sur Bergfrid]({url})", inline=False)

            if tags_str:
                embed.add_field(name="Tags", value=truncate_text(tags_str, 1024), inline=False)

            embed.set_footer(text="Bergfrid")
            if article.published_at:
                embed.timestamp = article.published_at

            message_content = f"{emoji} **Bergfrid** {tags_str}".strip()

            target_ids: List[int] = [self.official_channel_id]
            target_ids.extend(list(self._load_channels().values()))

            for cid in sorted(set(target_ids)):
                ch = await self._resolve_channel(cid)
                if not ch:
                    continue
                try:
                    await ch.send(content=message_content, embed=embed)
                except Exception:
                    pass
                await asyncio.sleep(self.send_delay)

            return True
        except Exception:
            return False
