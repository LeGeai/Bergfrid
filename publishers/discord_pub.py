import asyncio
import logging
from typing import Dict, Any, Optional, List

import discord

from core.models import Article
from core.config import DISCORD_EMBED_COLOR, load_discord_channels_map
from core.utils import determine_importance_emoji, prettify_summary, truncate_text, add_utm

log = logging.getLogger("bergfrid.publisher.discord")


class DiscordPublisher:
    name = "discord"

    def __init__(self, bot: discord.Client, official_channel_id: int,
                 send_delay: float = 0.2, summary_max: int = 2200):
        self.bot = bot
        self.official_channel_id = official_channel_id
        self.send_delay = send_delay
        self.summary_max = summary_max

    def _get_target_channel_ids(self) -> List[int]:
        """Deduplicated list of target channel IDs (official + per-server)."""
        ids = [self.official_channel_id]
        ids.extend(load_discord_channels_map().values())
        return list(dict.fromkeys(ids))

    async def _resolve_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(channel_id)
        except discord.NotFound:
            log.warning("Canal Discord %d introuvable.", channel_id)
            return None
        except discord.Forbidden:
            log.warning("Acces refuse au canal Discord %d.", channel_id)
            return None
        except Exception as e:
            log.error("Erreur resolution canal %d: %s", channel_id, e)
            return None

    async def publish(self, article: Article, cfg: Dict[str, Any]) -> bool:
        try:
            url = add_utm(article.url, source="discord", medium="social", campaign="rss")
            emoji = determine_importance_emoji(article.summary)

            # Clean summary: no prefix, 4 paragraphs max
            desc = prettify_summary(
                article.summary, self.summary_max, prefix="", max_paragraphs=4
            )

            embed = discord.Embed(
                title=truncate_text(f"{emoji} {article.title}", 256),
                url=url,
                description=desc,
                color=DISCORD_EMBED_COLOR,
            )

            # Footer: category + date (compact, elegant)
            footer_parts = []
            if article.category:
                footer_parts.append(article.category)
            if article.author:
                footer_parts.append(article.author)
            if footer_parts:
                embed.set_footer(text=" \u00b7 ".join(footer_parts))

            if article.published_at:
                embed.timestamp = article.published_at

            target_ids = self._get_target_channel_ids()
            sent_count = 0
            fail_count = 0

            for cid in target_ids:
                ch = await self._resolve_channel(cid)
                if not ch:
                    fail_count += 1
                    continue
                try:
                    await ch.send(embed=embed)
                    sent_count += 1
                except discord.Forbidden:
                    log.warning("Permission refusee pour envoyer dans le canal %d.", cid)
                    fail_count += 1
                except discord.HTTPException as e:
                    log.error("Erreur HTTP Discord pour canal %d: %s", cid, e)
                    fail_count += 1
                await asyncio.sleep(self.send_delay)

            if sent_count > 0:
                log.info("Discord: publie '%s' dans %d canal/canaux (%d echec(s)).",
                         article.title[:60], sent_count, fail_count)
                return True
            else:
                log.error("Discord: aucun canal n'a recu '%s'. %d echec(s).",
                          article.title[:60], fail_count)
                return False

        except Exception as e:
            log.exception("Erreur inattendue publication Discord: %s", e)
            return False
