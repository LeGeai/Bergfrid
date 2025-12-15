import os
import re
import json
import html
import asyncio
import logging
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

import discord
from discord.ext import commands, tasks

import feedparser

try:
    import aiohttp
except ImportError:
    aiohttp = None  # Telegram async nécessite aiohttp


# =========================
# CONFIGURATION
# =========================

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

DISCORD_OFFICIAL_CHANNEL_ID = int(os.getenv("DISCORD_NEWS_CHANNEL_ID", "1330916602425770088"))

BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
STATE_FILE = os.getenv("STATE_FILE", "bergfrid_state.json")
DISCORD_CHANNELS_FILE = os.getenv("DISCORD_CHANNELS_FILE", "discord_channels.json")

BASE_DOMAIN = "https://bergfrid.com"

RSS_POLL_MINUTES = float(os.getenv("RSS_POLL_MINUTES", "2.0"))
DISCORD_SEND_DELAY_SECONDS = float(os.getenv("DISCORD_SEND_DELAY_SECONDS", "0.2"))  # anti rate-limit
MAX_BACKLOG_POSTS_PER_TICK = int(os.getenv("MAX_BACKLOG_POSTS_PER_TICK", "30"))     # garde-fou

# Résumé "média": plus court = meilleur taux de lecture
DISCORD_SUMMARY_LIMIT = int(os.getenv("DISCORD_SUMMARY_LIMIT", "1200"))
TELEGRAM_SUMMARY_LIMIT = int(os.getenv("TELEGRAM_SUMMARY_LIMIT", "900"))

# Taille des journaux "déjà envoyé" (anti-doublons en cas de partial failure)
SENT_RING_SIZE = int(os.getenv("SENT_RING_SIZE", "100"))


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
intents.message_content = True  # nécessaire pour les commandes préfixées !
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

_http_session: Optional["aiohttp.ClientSession"] = None


# =========================
# PERSISTENCE (STATE)
# =========================

def _atomic_write_json(file_path: str, data: Dict[str, Any]) -> None:
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def _default_state() -> Dict[str, Any]:
    # last_id = dernier item complètement livré (Discord + Telegram)
    return {
        "last_id": None,
        "etag": None,
        "modified": None,
        "sent": {
            "discord": [],
            "telegram": [],
        },
    }


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return _default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_state()
        # Normalisation
        data.setdefault("last_id", None)
        data.setdefault("etag", None)
        data.setdefault("modified", None)
        data.setdefault("sent", {"discord": [], "telegram": []})
        data["sent"].setdefault("discord", [])
        data["sent"].setdefault("telegram", [])
        return data
    except Exception as e:
        log.error("Erreur lecture state: %s", e)
        return _default_state()


def save_state(state: Dict[str, Any]) -> None:
    try:
        # ring buffer
        for k in ("discord", "telegram"):
            lst = state.get("sent", {}).get(k, [])
            if isinstance(lst, list) and len(lst) > SENT_RING_SIZE:
                state["sent"][k] = lst[-SENT_RING_SIZE:]
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
# CONTENU / FORMAT
# =========================

def determine_importance_emoji(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ["critique", "urgent", "alerte", "attaque", "explosion", "guerre", "terror"]):
        return "🔥"
    return "📰"


def truncate_text(text: str, limit: int) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def entry_stable_id(entry: Any) -> str:
    for attr in ("id", "guid", "link"):
        v = getattr(entry, attr, None)
        if v:
            return str(v)
    return str(getattr(entry, "title", "unknown"))


def extract_html(entry: Any) -> str:
    content = getattr(entry, "content", None)
    if content and isinstance(content, list) and len(content) > 0:
        v = getattr(content[0], "value", None)
        if v:
            return str(v)
    return str(getattr(entry, "description", "") or getattr(entry, "summary", "") or "")


def strip_html_to_text(raw_html: str) -> str:
    raw_html = raw_html or ""
    # Si BeautifulSoup dispo, c'est le mieux
    try:
        from bs4 import BeautifulSoup  # type: ignore
        text = BeautifulSoup(raw_html, "html.parser").get_text("\n")
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text
    except Exception:
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
                term_clean = re.sub(r"\s+", "", str(term))
                tags.append(f"#{term_clean}")
    return " ".join(tags)


def add_utm(url: str, source: str) -> str:
    """Ajoute des UTM sans casser l'URL."""
    try:
        parts = urlparse(url)
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        q.setdefault("utm_source", source)
        q.setdefault("utm_medium", "social")
        q.setdefault("utm_campaign", "rss")
        new_query = urlencode(q, doseq=True)
        return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, new_query, parts.fragment))
    except Exception:
        return url


def entry_timestamp(entry: Any) -> Optional[discord.utils.utcnow]:
    # discord.Embed(timestamp=...) attend un datetime aware; discord.utils.utcnow() donne now.
    # On convertit published_parsed (struct_time) si dispo.
    import datetime
    try:
        t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if t:
            dt = datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
            return dt
    except Exception:
        return None
    return None


# =========================
# DISCORD PUBLISH
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


async def publish_discord(title: str, summary: str, url: str, tags_str: str, importance_emoji: str, ts=None) -> bool:
    try:
        truncated_summary = truncate_text(summary, DISCORD_SUMMARY_LIMIT)

        embed = discord.Embed(
            title=truncate_text(title, 256),
            url=url,
            description=truncated_summary,
            color=0x000000,
            timestamp=ts,
        )
        embed.set_footer(text="Bergfrid")

        if tags_str:
            embed.add_field(name="Tags", value=truncate_text(tags_str, 1024), inline=False)

        message_content = f"{importance_emoji} **Bergfrid** {tags_str}".strip()

        target_channel_ids: List[int] = [DISCORD_OFFICIAL_CHANNEL_ID]
        channels_map = load_discord_channels()
        target_channel_ids.extend(list(channels_map.values()))

        ok_any = False
        for channel_id in sorted(set(target_channel_ids)):
            channel = await _resolve_discord_channel(channel_id)
            if not channel:
                continue
            try:
                await channel.send(content=message_content, embed=embed)
                ok_any = True
            except discord.Forbidden:
                log.warning("Discord forbidden channel=%s", channel_id)
            except discord.HTTPException as e:
                log.warning("Discord HTTPException channel=%s: %s", channel_id, e)

            await asyncio.sleep(DISCORD_SEND_DELAY_SECONDS)

        return ok_any
    except Exception as e:
        log.warning("publish_discord exception: %s", e)
        return False


# =========================
# TELEGRAM PUBLISH (ASYNC)
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


def _telegram_format(title: str, summary: str, url: str, tags_str: str, emoji: str) -> str:
    # Format "média": court, lisible, cliquable.
    summary_short = truncate_text(summary, TELEGRAM_SUMMARY_LIMIT)
    parts = [
        f"{emoji} <b>{html.escape(title)}</b>",
        "",
        html.escape(summary_short),
        "",
        f"👉 <a href='{html.escape(url)}'>Lire l'article</a>",
    ]
    if tags_str:
        parts += ["", f"<i>{html.escape(tags_str)}</i>"]
    return "\n".join(parts).strip()


async def publish_telegram(title: str, summary: str, url: str, tags_str: str, importance_emoji: str) -> bool:
    sess = await ensure_http_session()
    if sess is None:
        return False

    telegram_text = _telegram_format(title, summary, url, tags_str, importance_emoji)

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
                return False
            return True
    except Exception as e:
        log.warning("Telegram exception: %s", e)
        return False


# =========================
# RSS WATCHER
# =========================

def parse_rss_with_cache(state: Dict[str, Any]) -> Any:
    etag = state.get("etag")
    modified = state.get("modified")

    feed = feedparser.parse(BERGFRID_RSS_URL, etag=etag, modified=modified)

    if getattr(feed, "etag", None):
        state["etag"] = feed.etag
    if getattr(feed, "modified", None):
        state["modified"] = feed.modified

    return feed


def _already_sent(state: Dict[str, Any], platform: str, eid: str) -> bool:
    try:
        return eid in state.get("sent", {}).get(platform, [])
    except Exception:
        return False


def _mark_sent(state: Dict[str, Any], platform: str, eid: str) -> None:
    state.setdefault("sent", {}).setdefault(platform, [])
    lst = state["sent"][platform]
    if eid not in lst:
        lst.append(eid)
        if len(lst) > SENT_RING_SIZE:
            state["sent"][platform] = lst[-SENT_RING_SIZE:]


@tasks.loop(minutes=RSS_POLL_MINUTES)
async def bergfrid_watcher() -> None:
    state = load_state()
    last_seen = state.get("last_id")

    try:
        feed = parse_rss_with_cache(state)
        save_state(state)  # cache etag/modified

        entries = getattr(feed, "entries", None) or []
        if not entries:
            return

        # Cold start: sync sur le head sans publier
        if not last_seen:
            newest = entry_stable_id(entries[0])
            state["last_id"] = newest
            save_state(state)
            log.warning("Cold start: sync on last_id=%s", newest)
            return

        # Backlog jusqu'à last_seen
        backlog: List[Any] = []
        for e in entries:
            eid = entry_stable_id(e)
            if eid == last_seen:
                break
            backlog.append(e)

        if not backlog:
            return

        if len(backlog) > MAX_BACKLOG_POSTS_PER_TICK:
            log.warning("Backlog=%s > max=%s (troncature).", len(backlog), MAX_BACKLOG_POSTS_PER_TICK)
            backlog = backlog[:MAX_BACKLOG_POSTS_PER_TICK]

        # Publier du plus ancien au plus récent, et avancer last_id item par item uniquement si OK
        for e in reversed(backlog):
            eid = entry_stable_id(e)
            title = str(getattr(e, "title", "Sans titre"))
            raw_link = str(getattr(e, "link", "") or "")
            base_url = urljoin(BASE_DOMAIN, raw_link)

            # UTM par plateforme
            url_discord = add_utm(base_url, "discord")
            url_telegram = add_utm(base_url, "telegram")

            raw_html = extract_html(e)
            summary = strip_html_to_text(raw_html)
            tags_str = extract_tags(e)
            emoji = determine_importance_emoji(summary)
            ts = entry_timestamp(e)

            log.info("Tentative publication id=%s title=%s", eid, title)

            # Discord
            discord_ok = True
            if not _already_sent(state, "discord", eid):
                discord_ok = await publish_discord(title, summary, url_discord, tags_str, emoji, ts=ts)
                if discord_ok:
                    _mark_sent(state, "discord", eid)
                    save_state(state)

            # Telegram
            telegram_ok = True
            if not _already_sent(state, "telegram", eid):
                telegram_ok = await publish_telegram(title, summary, url_telegram, tags_str, emoji)
                if telegram_ok:
                    _mark_sent(state, "telegram", eid)
                    save_state(state)

            # Avancer le curseur uniquement quand les 2 sont OK (ou déjà envoyés)
            if discord_ok and telegram_ok:
                state["last_id"] = eid
                save_state(state)
            else:
                # Stop: on retry au tick suivant, sans sauter l'item.
                log.warning("Publication partielle id=%s discord_ok=%s telegram_ok=%s -> retry next tick",
                            eid, discord_ok, telegram_ok)
                break

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

    if aiohttp is not None:
        await ensure_http_session()


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
    """Force une synchronisation sur le dernier item RSS sans publier."""
    state = load_state()
    feed = feedparser.parse(BERGFRID_RSS_URL)
    entries = getattr(feed, "entries", None) or []
    if not entries:
        await ctx.send("⚠️ Flux RSS vide ou inaccessible.")
        return
    state["last_id"] = entry_stable_id(entries[0])
    # on vide les sent ring pour repartir net
    state["sent"] = {"discord": [], "telegram": []}
    save_state(state)
    await ctx.send(f"✅ Synchronisé sur last_id={state['last_id']} (aucune publication).")


# =========================
# SHUTDOWN
# =========================

async def _graceful_shutdown():
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()


if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                pass
            else:
                loop.run_until_complete(_graceful_shutdown())
        except Exception:
            pass
