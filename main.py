import os
import asyncio
import logging
import json

import discord
from discord.ext import commands, tasks

from core.state import StateStore
from core.rss import parse_rss_with_cache, feed_to_backlog, entry_to_article


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bergfrid-bot")


# ENV
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DISCORD_OFFICIAL_CHANNEL_ID = int(os.getenv("DISCORD_NEWS_CHANNEL_ID", "1330916602425770088"))

# Files
STATE_FILE = os.getenv("STATE_FILE", "bergfrid_state.json")
DISCORD_CHANNELS_FILE = os.getenv("DISCORD_CHANNELS_FILE", "discord_channels.json")
TARGETS_FILE = os.getenv("PUBLISH_TARGETS_FILE", "config/publish_targets.json")

# RSS
BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BASE_DOMAIN = "https://bergfrid.com"
RSS_POLL_MINUTES = float(os.getenv("RSS_POLL_MINUTES", "2.0"))
MAX_BACKLOG_POSTS_PER_TICK = int(os.getenv("MAX_BACKLOG_POSTS_PER_TICK", "20"))

# Rendering
DISCORD_SUMMARY_MAX = int(os.getenv("DISCORD_SUMMARY_MAX", "2200"))
TELEGRAM_SUMMARY_MAX = int(os.getenv("TELEGRAM_SUMMARY_MAX", "900"))

# Rate
DISCORD_SEND_DELAY_SECONDS = float(os.getenv("DISCORD_SEND_DELAY_SECONDS", "0.2"))

# Delay between articles (anti-spam)
ARTICLE_PUBLISH_DELAY_SECONDS = float(os.getenv("ARTICLE_PUBLISH_DELAY_SECONDS", "30"))

# Sent ring
SENT_RING_MAX = int(os.getenv("SENT_RING_MAX", "250"))


def load_targets():
    try:
        with open(TARGETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("publish_targets must be dict")
        data.setdefault("enabled", ["discord", "telegram"])
        data.setdefault("discord", {})
        data.setdefault("telegram", {})
        return data
    except Exception:
        return {"enabled": ["discord", "telegram"], "discord": {}, "telegram": {}}


# Discord bot init
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

state_store = StateStore(STATE_FILE, sent_ring_max=SENT_RING_MAX)

# Publishers (imports here to avoid circular import surprises)
from publishers.discord_pub import DiscordPublisher
from publishers.telegram_pub import TelegramPublisher

discord_pub = DiscordPublisher(
    bot=bot,
    official_channel_id=DISCORD_OFFICIAL_CHANNEL_ID,
    channels_file=DISCORD_CHANNELS_FILE,
    send_delay=DISCORD_SEND_DELAY_SECONDS,
    summary_max=DISCORD_SUMMARY_MAX,
)
telegram_pub = TelegramPublisher(
    token=TELEGRAM_TOKEN,
    chat_id=TELEGRAM_CHAT_ID,
    summary_max=TELEGRAM_SUMMARY_MAX,
)

publishers = {"discord": discord_pub, "telegram": telegram_pub}


@tasks.loop(minutes=RSS_POLL_MINUTES)
async def bergfrid_watcher():
    targets = load_targets()
    enabled = set(targets.get("enabled", ["discord", "telegram"]))

    state = state_store.load()
    last_seen = state.get("last_id")

    # Fetch RSS with cache
    feed = parse_rss_with_cache(BERGFRID_RSS_URL, BASE_DOMAIN, state)
    state_store.save(state)  # persist etag/modified even if no publish

    entries = getattr(feed, "entries", None) or []
    if not entries:
        return

    # Cold start: sync sans publier (ne publie pas tout l’historique)
    if not last_seen:
        newest_entry = entries[0]
        article = entry_to_article(newest_entry, BASE_DOMAIN)
        state["last_id"] = article.id
        state_store.save(state)
        log.warning("Cold start: sync on last_id=%s (aucune publication)", state["last_id"])
        return

    backlog = feed_to_backlog(feed, last_seen)
    if not backlog:
        return

    if len(backlog) > MAX_BACKLOG_POSTS_PER_TICK:
        log.warning("Backlog=%s > max=%s. Troncature.", len(backlog), MAX_BACKLOG_POSTS_PER_TICK)
        backlog = backlog[:MAX_BACKLOG_POSTS_PER_TICK]

    # Publication du plus ancien au plus récent
    for entry in reversed(backlog):
        article = entry_to_article(entry, BASE_DOMAIN)
        eid = article.id

        published_any = False

        # Discord
        discord_ok = True
        if "discord" in enabled and not StateStore.sent_has(state, "discord", eid):
            log.info("Discord publish: %s", article.title)
            discord_ok = await discord_pub.publish(article, targets.get("discord", {}))
            if discord_ok:
                published_any = True
                state_store.sent_add(state, "discord", eid)
                state_store.save(state)

        # Telegram
        telegram_ok = True
        if "telegram" in enabled and not StateStore.sent_has(state, "telegram", eid):
            log.info("Telegram publish: %s", article.title)
            telegram_ok = await telegram_pub.publish(article, targets.get("telegram", {}))
            if telegram_ok:
                published_any = True
                state_store.sent_add(state, "telegram", eid)
                state_store.save(state)

        # Commit last_id seulement si les publishers activés sont OK
        active_ok = True
        if "discord" in enabled and not discord_ok:
            active_ok = False
        if "telegram" in enabled and not telegram_ok:
            active_ok = False

        if active_ok:
            state["last_id"] = eid
            state_store.save(state)
        else:
            log.warning("Publication partielle pour id=%s. Stop pour retry au prochain tick.", eid)
            return

        # Anti-spam: délai minimum entre chaque article effectivement publié
        if published_any:
            await asyncio.sleep(ARTICLE_PUBLISH_DELAY_SECONDS)


@bot.event
async def on_ready():
    log.info("Connecté: %s", bot.user)
    if not bergfrid_watcher.is_running():
        bergfrid_watcher.start()
        log.info("Tâche RSS démarrée: %s min", RSS_POLL_MINUTES)


@bot.command(name="setnews")
@commands.has_permissions(manage_channels=True)
async def set_news_channel(ctx: commands.Context, channel: discord.TextChannel = None):
    channel = ctx.channel if channel is None else channel

    try:
        if os.path.exists(DISCORD_CHANNELS_FILE):
            with open(DISCORD_CHANNELS_FILE, "r", encoding="utf-8") as f:
                channels_map = json.load(f)
            if not isinstance(channels_map, dict):
                channels_map = {}
        else:
            channels_map = {}
    except Exception:
        channels_map = {}

    channels_map[str(ctx.guild.id)] = int(channel.id)

    tmp = f"{DISCORD_CHANNELS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(channels_map, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DISCORD_CHANNELS_FILE)

    await ctx.send(f"✅ Ce serveur publiera les nouvelles dans {channel.mention}.")


@bot.command(name="unsetnews")
@commands.has_permissions(manage_channels=True)
async def unset_news_channel(ctx: commands.Context):
    try:
        with open(DISCORD_CHANNELS_FILE, "r", encoding="utf-8") as f:
            channels_map = json.load(f)
        if not isinstance(channels_map, dict):
            channels_map = {}
    except Exception:
        channels_map = {}

    gid = str(ctx.guild.id)
    if gid in channels_map:
        del channels_map[gid]
        tmp = f"{DISCORD_CHANNELS_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(channels_map, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DISCORD_CHANNELS_FILE)
        await ctx.send("❌ Canal de nouvelles retiré pour ce serveur.")
    else:
        await ctx.send("ℹ️ Aucun canal n'était configuré.")


@bot.command(name="rsssync")
@commands.has_permissions(manage_channels=True)
async def rss_sync(ctx: commands.Context):
    state = state_store.load()
    feed = parse_rss_with_cache(BERGFRID_RSS_URL, BASE_DOMAIN, state)
    state_store.save(state)

    entries = getattr(feed, "entries", None) or []
    if not entries:
        await ctx.send("⚠️ Flux RSS vide ou inaccessible.")
        return

    newest_entry = entries[0]
    article = entry_to_article(newest_entry, BASE_DOMAIN)
    state["last_id"] = article.id
    state_store.save(state)

    await ctx.send(f"✅ Synchronisé sur last_id={state['last_id']} (aucune publication).")


async def _shutdown():
    await telegram_pub.close()


if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                loop.run_until_complete(_shutdown())
        except Exception:
            pass
