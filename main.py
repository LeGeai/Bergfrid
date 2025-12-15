import os
import re
import json
import html
import asyncio
import logging
from typing import Any, Dict, Optional, List
from urllib.parse import urljoin

import discord
from discord.ext import commands, tasks

import feedparser

try:
    import aiohttp
except ImportError:
    aiohttp = None  # Telegram async nécessite aiohttp


# =========================
# CONFIGURATION ESSENTIELLE
# =========================

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# --- CONFIG DISCORD ---
DISCORD_OFFICIAL_CHANNEL_ID = int(os.getenv("DISCORD_NEWS_CHANNEL_ID", "1330916602425770088"))

# --- CONFIG RSS / FICHIERS ---
BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
STATE_FILE = "bergfrid_state.json"
DISCORD_CHANNELS_FILE = "discord_channels.json"

BASE_DOMAIN = "https://bergfrid.com"

# --- Fréquences / limites ---
RSS_POLL_MINUTES = float(os.getenv("RSS_POLL_MINUTES", "2.0"))
DISCORD_SEND_DELAY_SECONDS = float(os.getenv("DISCORD_SEND_DELAY_SECONDS", "0.2"))  # anti rate-limit
MAX_BACKLOG_POSTS_PER_TICK = int(os.getenv("MAX_BACKLOG_POSTS_PER_TICK", "20"))      # garde-fou

# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bergfrid-bot")


# =========================
# DISCORD SETUP
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Session HTTP partagée (Telegram)
_http_session: Optional["aiohttp.ClientSession"] = None


# =========================
# PERSISTENCE (STATE)
# =========================

def _atomic_write_json(file_path: str, data: Dict[str, Any]) -> None:
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def load_state() -> Dict[str, Any]:
    """
    State schema:
    {
      "last_id": "....",        # GUID ou lien stable
      "etag": "...",            # HTTP cache info pour feedparser (si dispo)
      "modified": [..]          # feedparser modified (si dispo)
    }
    """
    if not os.path.exists(STATE_FILE):
        return {"last_id": None, "etag": None, "modified": None}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"last_id": None, "etag": None, "modified": None}
        data.setdefault("last_id", None)
        data.setdefault("etag", None)
        data.setdefault("modified", None)
        return data
    except Exception as e:
        log.error("Erreur lecture state: %s", e)
        return {"last_id": None, "etag": None, "modified": None}


def save_state(state: Dict[str, Any]) -> None:
    try:
        _atomic_write_json(STATE_FILE, state)
    except Exception as e:
        log.error("Erreur écriture state: %s", e)


# =========================
# DISCORD CHANNELS (MULTI-SERVEURS)
# =========================

def load_discord_channels() -> Dict[str, int]:
    if not os.path.exists(DISCORD_CHANNELS_FILE):
        return {}
    try:
        with open(DISCORD_CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # Normalise vers int
        out: Dict[str, int] = {}
        for k, v in data.items():
            try:
                out[str(k)] = int(v)
            except Exception:
                continue
        return out
    except Exception:
        return {}


def save_discord_channels(channels_dict: Dict[str, int]) -> None:
    try:
        _atomic_write_json(DISCORD_CHANNELS_FILE, channels_dict)
    except Exception as e:
        log.error("Erreur écriture discord_channels: %s", e)


# =========================
# CONTENU
# =========================

def determine_importance_and_emoji(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ["critique", "urgent", "alerte", "attaque", "explosion", "guerre"]):
        return "🔥"
    return "📰"


def truncate_text(text: str, limit: int) -> str:
    if text is None:
        return ""
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def entry_stable_id(entry: Any) -> str:
    """
    Priorité:
    - entry.id (souvent GUID)
    - entry.guid
    - entry.link
    """
    for attr in ("id", "guid", "link"):
        v = getattr(entry, attr, None)
        if v:
            return str(v)
    # fallback
    return str(getattr(entry, "title", "unknown"))


def extract_html(entry: Any) -> str:
    # content:encoded souvent exposé via entry.content[0].value
    content = getattr(entry, "content", None)
    if content and isinstance(content, list) and len(content) > 0:
        v = getattr(content[0], "value", None)
        if v:
            return str(v)

    # fallback
    return str(getattr(entry, "description", "") or getattr(entry, "summary", "") or "")


def strip_html_to_text(raw_html: str) -> str:
    raw_html = raw_html or ""

    # Si BeautifulSoup dispo, c'est le mieux
    try:
        from bs4 import BeautifulSoup  # type: ignore
        text = BeautifulSoup(raw_html, "html.parser").get_text("\n")
        text = html.unescape(text)
        # compact: évite 15 lignes vides
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text
    except Exception:
        # fallback simple sans dépendance
        txt = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.I)
        txt = re.sub(r"</p\s*>", "\n\n", txt, flags=re.I)
        txt = re.sub(r"<[^>]+>", "", txt)
        txt = html.unescape(txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
        return txt


def extract_tags(entry: Any) -> str:
    tags = []
    entry_tags = getattr(entry, "tags", None)
    if entry_tags:
        for t in entry_tags:
            term = getattr(t, "term", None)
            if term:
                # petit nettoyage pour hashtags
                term_clean = re.sub(r"\s+", "", str(term))
                tags.append(f"#{term_clean}")
    return " ".join(tags)


# =========================
# PUBLICATION DISCORD
# =========================

async def _resolve_discord_channel(channel_id: int) -> Optional[discord.abc.Messageable]:
    ch = bot.get_channel(channel_id)
    if ch is not None:
        return ch
    try:
        return await bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    except discord.HTTPException as e:
        log.warning("Discord fetch_channel HTTPException channel=%s: %s", channel_id, e)
        return None


async def publish_discord(title: str, summary: str, url: str, tags_str: str, importance_emoji: str) -> None:
    truncated_summary = truncate_text(summary, 3500)  # Embed desc max 4096, marge

    embed = discord.Embed(
        title=truncate_text(title, 256),
        url=url,
        description=truncated_summary,
        color=0x000000,
    )
    message_content = f"{importance_emoji} **NOUVEL ARTICLE** {tags_str}".strip()

    target_channel_ids: List[int] = [DISCORD_OFFICIAL_CHANNEL_ID]
    channels_map = load_discord_channels()
    target_channel_ids.extend(list(channels_map.values()))

    for channel_id in sorted(set(target_channel_ids)):
        channel = await _resolve_discord_channel(channel_id)
        if not channel:
            continue
        try:
            await channel.send(content=message_content, embed=embed)
        except discord.Forbidden:
            log.warning("Discord forbidden channel=%s", channel_id)
        except discord.HTTPException as e:
            log.warning("Discord HTTPException channel=%s: %s", channel_id, e)

        await asyncio.sleep(DISCORD_SEND_DELAY_SECONDS)


# =========================
# PUBLICATION TELEGRAM (ASYNC)
# =========================

async def ensure_http_session() -> Optional["aiohttp.ClientSession"]:
    global _http_session
    if aiohttp is None:
        log.error("aiohttp non installé: Telegram async indisponible.")
        return None
    if _http_session is None or _http_session.closed:
        timeout = aiohttp.ClientTimeout(total=20)
        _http_session = aiohttp.ClientSession(timeout=timeout)
    return _http_session


async def publish_telegram(title: str, summary: str, url: str, tags_str: str, importance_emoji: str) -> None:
    sess = await ensure_http_session()
    if sess is None:
        return

    truncated_summary = truncate_text(summary, 3500)

    telegram_text = (
        f"{importance_emoji} <b>{html.escape(title)}</b>\n\n"
        f"{html.escape(truncated_summary)}\n\n"
        f"👉 <a href='{html.escape(url)}'>Lire l'article</a>\n\n"
        f"<i>{html.escape(tags_str)}</i>"
    ).strip()

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": telegram_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with sess.post(endpoint, data=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning("Telegram error status=%s body=%s", resp.status, body[:1000])
    except Exception as e:
        log.warning("Telegram exception: %s", e)


# =========================
# RSS WATCHER
# =========================

def parse_rss_with_cache(state: Dict[str, Any]) -> Any:
    etag = state.get("etag")
    modified = state.get("modified")

    # feedparser accepte etag/modified si fournis
    feed = feedparser.parse(BERGFRID_RSS_URL, etag=etag, modified=modified)

    # Mise à jour cache si dispo
    if getattr(feed, "etag", None):
        state["etag"] = feed.etag
    if getattr(feed, "modified", None):
        state["modified"] = feed.modified

    return feed


@tasks.loop(minutes=RSS_POLL_MINUTES)
async def bergfrid_watcher() -> None:
    state = load_state()
    last_seen = state.get("last_id")

    try:
        feed = parse_rss_with_cache(state)
        save_state(state)  # cache etag/modified

        # 304 Not Modified: feed.bozo peut être False et entries vide, selon serveurs
        entries = getattr(feed, "entries", None) or []
        if not entries:
            return

        # Cold start: on se synchronise sans publier
        if not last_seen:
            newest = entry_stable_id(entries[0])
            state["last_id"] = newest
            save_state(state)
            log.warning("Cold start: sync on last_id=%s", newest)
            return

        # Collecte de la backlog jusqu'à last_seen
        backlog: List[Any] = []
        for e in entries:
            eid = entry_stable_id(e)
            if eid == last_seen:
                break
            backlog.append(e)

        if not backlog:
            return

        # Garde-fou si le bot est resté offline longtemps
        if len(backlog) > MAX_BACKLOG_POSTS_PER_TICK:
            log.warning(
                "Backlog=%s > max=%s. Troncature aux plus récents.",
                len(backlog),
                MAX_BACKLOG_POSTS_PER_TICK,
            )
            backlog = backlog[:MAX_BACKLOG_POSTS_PER_TICK]

        # Publier du plus ancien au plus récent
        for e in reversed(backlog):
            title = str(getattr(e, "title", "Sans titre"))
            raw_link = str(getattr(e, "link", "") or "")
            url = urljoin(BASE_DOMAIN, raw_link)

            raw_html = extract_html(e)
            summary = strip_html_to_text(raw_html)

            tags_str = extract_tags(e)
            importance_emoji = determine_importance_and_emoji(summary)

            log.info("Publication: %s", title)

            await publish_discord(title, summary, url, tags_str, importance_emoji)
            await publish_telegram(title, summary, url, tags_str, importance_emoji)

        # Update last_id sur l’item le plus récent du flux
        state["last_id"] = entry_stable_id(entries[0])
        save_state(state)

    except Exception as e:
        log.warning("Erreur RSS watcher: %s", e)


# =========================
# EVENTS & COMMANDES DISCORD
# =========================

@bot.event
async def on_ready() -> None:
    log.info("Connecté: %s", bot.user)
    if not bergfrid_watcher.is_running():
        bergfrid_watcher.start()
        log.info("Tâche RSS démarrée: %s min", RSS_POLL_MINUTES)

    # Init session HTTP Telegram
    if aiohttp is not None:
        await ensure_http_session()


@bot.event
async def on_close() -> None:
    # Rarement appelé selon l’implémentation, mais on tente
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()


@bot.command(name="setnews")
@commands.has_permissions(manage_channels=True)
async def set_news_channel(ctx: commands.Context, channel: discord.TextChannel = None) -> None:
    channel = ctx.channel if channel is None else channel
    channels_map = load_discord_channels()
    guild_id_str = str(ctx.guild.id)
    channels_map[guild_id_str] = int(channel.id)
    save_discord_channels(channels_map)
    await ctx.send(f"✅ Ce serveur publiera les nouvelles dans {channel.mention}.")


@bot.command(name="unsetnews")
@commands.has_permissions(manage_channels=True)
async def unset_news_channel(ctx: commands.Context) -> None:
    channels_map = load_discord_channels()
    guild_id_str = str(ctx.guild.id)
    if guild_id_str in channels_map:
        del channels_map[guild_id_str]
        save_discord_channels(channels_map)
        await ctx.send("❌ Canal de nouvelles retiré pour ce serveur.")
    else:
        await ctx.send("ℹ️ Aucun canal de nouvelles n'était configuré pour ce serveur.")


@bot.command(name="rsssync")
@commands.has_permissions(manage_channels=True)
async def rss_sync(ctx: commands.Context) -> None:
    """
    Force une synchronisation sur le dernier item RSS sans publier.
    Utile si tu as un backlog énorme ou si tu veux repartir propre.
    """
    state = load_state()
    feed = feedparser.parse(BERGFRID_RSS_URL)
    entries = getattr(feed, "entries", None) or []
    if not entries:
        await ctx.send("⚠️ Flux RSS vide ou inaccessible.")
        return
    state["last_id"] = entry_stable_id(entries[0])
    save_state(state)
    await ctx.send(f"✅ Synchronisé sur last_id={state['last_id']} (aucune publication).")


# =========================
# BOOT
# =========================

async def _graceful_shutdown():
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()


if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        # Dans certains cas, on peut avoir une loop déjà fermée, donc on protège.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # pas idéal, mais on évite un crash
                pass
            else:
                loop.run_until_complete(_graceful_shutdown())
        except Exception:
            pass
