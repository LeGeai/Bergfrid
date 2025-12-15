import os
import re
import json
import html
import asyncio
import logging
from typing import Any, Dict, Optional, List, Tuple
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

import feedparser

try:
    import aiohttp
except ImportError:
    aiohttp = None


# =========================
# CONFIG
# =========================

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

DISCORD_OFFICIAL_CHANNEL_ID = int(os.getenv("DISCORD_NEWS_CHANNEL_ID", "1330916602425770088"))

BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BASE_DOMAIN = "https://bergfrid.com"

STATE_FILE = os.getenv("STATE_FILE", "bergfrid_state.json")
DISCORD_CHANNELS_FILE = os.getenv("DISCORD_CHANNELS_FILE", "discord_channels.json")

RSS_POLL_MINUTES = float(os.getenv("RSS_POLL_MINUTES", "2.0"))
DISCORD_SEND_DELAY_SECONDS = float(os.getenv("DISCORD_SEND_DELAY_SECONDS", "0.2"))
MAX_BACKLOG_POSTS_PER_TICK = int(os.getenv("MAX_BACKLOG_POSTS_PER_TICK", "20"))

# Rendu
DISCORD_SUMMARY_MAX = int(os.getenv("DISCORD_SUMMARY_MAX", "2200"))
TELEGRAM_SUMMARY_MAX = int(os.getenv("TELEGRAM_SUMMARY_MAX", "900"))

# Mémoire anti doublons
SENT_RING_MAX = int(os.getenv("SENT_RING_MAX", "250"))


# =========================
# LOGS
# =========================

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bergfrid-bot")


# =========================
# DISCORD SETUP
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

_http_session: Optional["aiohttp.ClientSession"] = None


# =========================
# STATE / PERSISTENCE
# =========================

def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_state() -> Dict[str, Any]:
    """
    Schema:
    {
      "last_id": "...",
      "etag": "...",
      "modified": ...,
      "sent": {
         "discord": ["id1","id2",...],
         "telegram": ["id1","id2",...]
      }
    }
    """
    if not os.path.exists(STATE_FILE):
        return {
            "last_id": None,
            "etag": None,
            "modified": None,
            "sent": {"discord": [], "telegram": []},
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("state not dict")

        data.setdefault("last_id", None)
        data.setdefault("etag", None)
        data.setdefault("modified", None)
        data.setdefault("sent", {"discord": [], "telegram": []})
        data["sent"].setdefault("discord", [])
        data["sent"].setdefault("telegram", [])
        return data
    except Exception as e:
        log.error("Erreur lecture state: %s", e)
        return {
            "last_id": None,
            "etag": None,
            "modified": None,
            "sent": {"discord": [], "telegram": []},
        }


def save_state(state: Dict[str, Any]) -> None:
    try:
        # Nettoyage ring buffers
        for k in ("discord", "telegram"):
            lst = state.get("sent", {}).get(k, [])
            if isinstance(lst, list) and len(lst) > SENT_RING_MAX:
                state["sent"][k] = lst[-SENT_RING_MAX:]
        _atomic_write_json(STATE_FILE, state)
    except Exception as e:
        log.error("Erreur écriture state: %s", e)


def sent_has(state: Dict[str, Any], platform: str, entry_id: str) -> bool:
    return entry_id in (state.get("sent", {}).get(platform, []) or [])


def sent_add(state: Dict[str, Any], platform: str, entry_id: str) -> None:
    state.setdefault("sent", {}).setdefault(platform, [])
    lst = state["sent"][platform]
    if entry_id not in lst:
        lst.append(entry_id)
        if len(lst) > SENT_RING_MAX:
            state["sent"][platform] = lst[-SENT_RING_MAX:]


# =========================
# DISCORD CHANNELS (MULTI SERVEURS)
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
    _atomic_write_json(DISCORD_CHANNELS_FILE, channels_dict)


# =========================
# HELPERS CONTENU
# =========================

def truncate_text(text: str, limit: int) -> str:
    text = text or ""
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def determine_importance_emoji(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ["critique", "urgent", "alerte", "attaque", "explosion", "guerre"]):
        return "🔥"
    return "📰"


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


def prettify_summary(text: str, max_chars: int, prefix: str = "› ") -> str:
    text = (text or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Paragraphes non vides
    paras = [p.strip() for p in text.split("\n") if p.strip()]

    # Limite pour éviter les pavés
    if len(paras) > 7:
        paras = paras[:7]

    pretty = "\n\n".join(prefix + p for p in paras)
    return truncate_text(pretty, max_chars)


def extract_tags(entry: Any) -> str:
    tags_out: List[str] = []

    entry_tags = getattr(entry, "tags", None) or []
    for t in entry_tags:
        term = getattr(t, "term", None)
        if not term:
            continue
        term = str(term).strip()

        # Si le RSS met "tag1, tag2" dans UNE seule category, on split
        parts = re.split(r"[;,/|]\s*|\s+#", term.replace("#", " #").strip())
        for p in parts:
            p = re.sub(r"\s+", "", p.strip())
            if not p:
                continue
            if not p.startswith("#"):
                p = "#" + p
            tags_out.append(p)

    # Unique stable
    seen = set()
    clean = []
    for x in tags_out:
        key = x.lower()
        if key not in seen:
            seen.add(key)
            clean.append(x)

    return " ".join(clean)


def extract_author(entry: Any) -> str:
    a = getattr(entry, "author", None)
    if a:
        return str(a).strip()
    dc = getattr(entry, "dc_creator", None)
    if dc:
        return str(dc).strip()
    return "Rédaction"


def extract_category(entry: Any) -> str:
    # Certains flux mettent category comme tags[0], sinon parfois entry.category
    c = getattr(entry, "category", None)
    if c:
        return str(c).strip()
    # fallback: premier tag sans #
    entry_tags = getattr(entry, "tags", None) or []
    if entry_tags:
        term = getattr(entry_tags[0], "term", None)
        if term:
            return str(term).strip()
    return ""


def extract_pub_dt(entry: Any) -> Optional[datetime]:
    # feedparser peut donner published_parsed (time.struct_time)
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if st:
        try:
            return datetime(*st[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    # fallback sur published string RFC822
    pub = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if pub:
        try:
            # feedparser fournit souvent déjà struct_time. Ici fallback minimal.
            # On évite de dépendre d'email.utils ici pour rester simple.
            return datetime.now(timezone.utc)
        except Exception:
            pass
    return None


def format_date(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    # format court lisible
    return dt.astimezone(timezone.utc).strftime("%d %b %Y")


def add_utm(url: str, source: str, medium: str = "social", campaign: str = "rss") -> str:
    try:
        u = urlparse(url)
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        q.setdefault("utm_source", source)
        q.setdefault("utm_medium", medium)
        q.setdefault("utm_campaign", campaign)
        new_query = urlencode(q, doseq=True)
        return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
    except Exception:
        return url


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


async def publish_discord(
    title: str,
    summary: str,
    url: str,
    tags_str: str,
    importance_emoji: str,
    author: str,
    category: str,
    pub_dt: Optional[datetime],
) -> bool:
    try:
        discord_url = add_utm(url, source="discord", medium="social", campaign="rss")

        desc = prettify_summary(summary, DISCORD_SUMMARY_MAX, prefix="› ")

        embed = discord.Embed(
            title=truncate_text(title, 256),
            url=discord_url,
            description=desc,
            color=0x0B0F14,
        )

        meta_bits = []
        if author:
            meta_bits.append(author)
        if category:
            meta_bits.append(category)
        if pub_dt:
            meta_bits.append(format_date(pub_dt))
        meta_line = " • ".join(meta_bits).strip()

        if meta_line:
            embed.add_field(name="Meta", value=truncate_text(meta_line, 1024), inline=False)

        embed.add_field(name="Lire", value=f"[Ouvrir sur Bergfrid]({discord_url})", inline=False)

        if tags_str:
            embed.add_field(name="Tags", value=truncate_text(tags_str, 1024), inline=False)

        embed.set_footer(text="Bergfrid")
        if pub_dt:
            embed.timestamp = pub_dt

        message_content = f"{importance_emoji} **Bergfrid** {tags_str}".strip()

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

        return True
    except Exception as e:
        log.warning("publish_discord exception: %s", e)
        return False


# =========================
# TELEGRAM PUBLISH (ASYNC)
# =========================

async def ensure_http_session() -> Optional["aiohttp.ClientSession"]:
    global _http_session
    if aiohttp is None:
        log.error("aiohttp non installé: Telegram indisponible.")
        return None
    if _http_session is None or _http_session.closed:
        timeout = aiohttp.ClientTimeout(total=20)
        _http_session = aiohttp.ClientSession(timeout=timeout)
    return _http_session


async def publish_telegram(
    title: str,
    summary: str,
    url: str,
    tags_str: str,
    importance_emoji: str,
    author: str,
    category: str,
    pub_dt: Optional[datetime],
) -> bool:
    sess = await ensure_http_session()
    if sess is None:
        return False

    try:
        telegram_url = add_utm(url, source="telegram", medium="social", campaign="rss")

        pretty = prettify_summary(summary, TELEGRAM_SUMMARY_MAX, prefix="› ")

        meta_bits = []
        if author:
            meta_bits.append(author)
        if category:
            meta_bits.append(category)
        if pub_dt:
            meta_bits.append(format_date(pub_dt))
        meta_line = " • ".join(meta_bits).strip()

        # Message propre et compact
        parts = []
        parts.append(f"{importance_emoji} <b>{html.escape(title)}</b>")
        if meta_line:
            parts.append(f"<i>{html.escape(meta_line)}</i>")
        parts.append("")
        parts.append(html.escape(pretty))
        parts.append("")
        parts.append(f"👉 <a href='{html.escape(telegram_url)}'>Lire l'article</a>")

        if tags_str:
            parts.append("")
            parts.append(f"<i>{html.escape(tags_str)}</i>")

        parts.append(f"<i>Source: Bergfrid</i>")

        telegram_text = "\n".join(parts).strip()

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": telegram_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        async with sess.post(endpoint, data=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning("Telegram error status=%s body=%s", resp.status, body[:1000])
                return False

        return True
    except Exception as e:
        log.warning("publish_telegram exception: %s", e)
        return False


# =========================
# RSS FETCH WITH CACHE
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


# =========================
# WATCHER
# =========================

@tasks.loop(minutes=RSS_POLL_MINUTES)
async def bergfrid_watcher() -> None:
    state = load_state()
    last_seen = state.get("last_id")

    try:
        feed = parse_rss_with_cache(state)
        save_state(state)  # persiste etag/modified

        entries = getattr(feed, "entries", None) or []
        if not entries:
            return

        # Cold start: sync sans publier
        if not last_seen:
            newest = entry_stable_id(entries[0])
            state["last_id"] = newest
            save_state(state)
            log.warning("Cold start: sync on last_id=%s", newest)
            return

        backlog: List[Any] = []
        for e in entries:
            eid = entry_stable_id(e)
            if eid == last_seen:
                break
            backlog.append(e)

        if not backlog:
            return

        if len(backlog) > MAX_BACKLOG_POSTS_PER_TICK:
            log.warning("Backlog=%s > max=%s. Troncature.", len(backlog), MAX_BACKLOG_POSTS_PER_TICK)
            backlog = backlog[:MAX_BACKLOG_POSTS_PER_TICK]

        # Publier du plus ancien au plus récent.
        # IMPORTANT: on n'avance last_id que si Discord ET Telegram sont OK
        # et on évite les doublons par plateforme via state.sent
        for e in reversed(backlog):
            eid = entry_stable_id(e)

            title = str(getattr(e, "title", "Sans titre"))
            raw_link = str(getattr(e, "link", "") or "")
            url = urljoin(BASE_DOMAIN, raw_link)

            raw_html = extract_html(e)
            summary = strip_html_to_text(raw_html)

            tags_str = extract_tags(e)
            author = extract_author(e)
            category = extract_category(e)
            pub_dt = extract_pub_dt(e)

            importance_emoji = determine_importance_emoji(summary)

            # Discord
            discord_ok = True
            if not sent_has(state, "discord", eid):
                log.info("Discord publish: %s", title)
                discord_ok = await publish_discord(
                    title=title,
                    summary=summary,
                    url=url,
                    tags_str=tags_str,
                    importance_emoji=importance_emoji,
                    author=author,
                    category=category,
                    pub_dt=pub_dt,
                )
                if discord_ok:
                    sent_add(state, "discord", eid)
                    save_state(state)

            # Telegram
            telegram_ok = True
            if not sent_has(state, "telegram", eid):
                log.info("Telegram publish: %s", title)
                telegram_ok = await publish_telegram(
                    title=title,
                    summary=summary,
                    url=url,
                    tags_str=tags_str,
                    importance_emoji=importance_emoji,
                    author=author,
                    category=category,
                    pub_dt=pub_dt,
                )
                if telegram_ok:
                    sent_add(state, "telegram", eid)
                    save_state(state)

            # Commit seulement si les deux sont OK
            if discord_ok and telegram_ok:
                state["last_id"] = eid
                save_state(state)
            else:
                log.warning("Publication partielle pour id=%s. Stop pour retry au prochain tick.", eid)
                return

    except Exception as e:
        log.warning("Erreur watcher: %s", e)


# =========================
# DISCORD COMMANDS
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
    channels_map[str(ctx.guild.id)] = int(channel.id)
    save_discord_channels(channels_map)
    await ctx.send(f"✅ Ce serveur publiera les nouvelles dans {channel.mention}.")


@bot.command(name="unsetnews")
@commands.has_permissions(manage_channels=True)
async def unset_news_channel(ctx: commands.Context) -> None:
    channels_map = load_discord_channels()
    gid = str(ctx.guild.id)
    if gid in channels_map:
        del channels_map[gid]
        save_discord_channels(channels_map)
        await ctx.send("❌ Canal de nouvelles retiré pour ce serveur.")
    else:
        await ctx.send("ℹ️ Aucun canal n'était configuré.")


@bot.command(name="rsssync")
@commands.has_permissions(manage_channels=True)
async def rss_sync(ctx: commands.Context) -> None:
    """
    Force une sync sur le dernier item RSS sans publier.
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
            if not loop.is_running():
                loop.run_until_complete(_graceful_shutdown())
        except Exception:
            pass
