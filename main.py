import os
import asyncio
import logging
import json
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from core.state import StateStore
from core.rss import parse_rss_with_cache, feed_to_backlog, entry_to_article

# Publishers
from publishers.discord_pub import DiscordPublisher
from publishers.telegram_pub import TelegramPublisher

# Telegram direct sender (pour messages spéciaux)
try:
    import aiohttp
except ImportError:
    aiohttp = None


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bergfrid-bot")


# =========================
# ENV / CONFIG
# =========================

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DISCORD_OFFICIAL_CHANNEL_ID = int(os.getenv("DISCORD_NEWS_CHANNEL_ID", "1330916602425770088"))

STATE_FILE = os.getenv("STATE_FILE", "bergfrid_state.json")
DISCORD_CHANNELS_FILE = os.getenv("DISCORD_CHANNELS_FILE", "discord_channels.json")
TARGETS_FILE = os.getenv("PUBLISH_TARGETS_FILE", "config/publish_targets.json")

BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BASE_DOMAIN = "https://bergfrid.com"

RSS_POLL_MINUTES = float(os.getenv("RSS_POLL_MINUTES", "2.0"))
MAX_BACKLOG_POSTS_PER_TICK = int(os.getenv("MAX_BACKLOG_POSTS_PER_TICK", "20"))

DISCORD_SUMMARY_MAX = int(os.getenv("DISCORD_SUMMARY_MAX", "2200"))
TELEGRAM_SUMMARY_MAX = int(os.getenv("TELEGRAM_SUMMARY_MAX", "900"))

DISCORD_SEND_DELAY_SECONDS = float(os.getenv("DISCORD_SEND_DELAY_SECONDS", "0.2"))

# Délai minimum entre 2 articles publiés (anti-spam)
ARTICLE_PUBLISH_DELAY_SECONDS = float(os.getenv("ARTICLE_PUBLISH_DELAY_SECONDS", "30"))

# Mémoire anti-doublons
SENT_RING_MAX = int(os.getenv("SENT_RING_MAX", "250"))

# Timezone / planning
TZ = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Paris"))

# Bonne nuit (22h par défaut)
PROMO_HOUR = int(os.getenv("PROMO_HOUR", "22"))
PROMO_MINUTE = int(os.getenv("PROMO_MINUTE", "0"))

# Liens promo
PROMO_WEBSITE_URL = os.getenv("PROMO_WEBSITE_URL", "https://www.bergfrid.com")
TIPEEE_URL = os.getenv("TIPEEE_URL", "https://fr.tipeee.com/parlement-des-hiboux")

# Anti-spam reboot/maj (même message)
REBOOT_NOTICE_COOLDOWN_SECONDS = int(os.getenv("REBOOT_NOTICE_COOLDOWN_SECONDS", "600"))  # 10 min


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


def load_discord_channels_map() -> dict:
    try:
        if os.path.exists(DISCORD_CHANNELS_FILE):
            with open(DISCORD_CHANNELS_FILE, "r", encoding="utf-8") as f:
                m = json.load(f)
            return m if isinstance(m, dict) else {}
        return {}
    except Exception:
        return {}


def get_all_discord_target_channel_ids() -> list[int]:
    ids = [DISCORD_OFFICIAL_CHANNEL_ID]
    m = load_discord_channels_map()
    for _, v in m.items():
        try:
            ids.append(int(v))
        except Exception:
            pass

    out = []
    seen = set()
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


async def resolve_discord_channel(cid: int):
    ch = bot.get_channel(cid)
    if ch is not None:
        return ch
    try:
        return await bot.fetch_channel(cid)
    except Exception:
        return None


async def send_discord_text_to_targets(text: str) -> None:
    for cid in get_all_discord_target_channel_ids():
        ch = await resolve_discord_channel(cid)
        if not ch:
            continue
        try:
            await ch.send(content=text)
        except Exception:
            pass
        await asyncio.sleep(0.2)


async def send_telegram_text(text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> bool:
    if aiohttp is None:
        log.error("aiohttp non installé: impossible d'envoyer Telegram (messages spéciaux).")
        return False

    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(endpoint, data=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("Telegram special msg error status=%s body=%s", resp.status, body[:600])
                    return False
        return True
    except Exception as e:
        log.warning("Telegram special msg exception: %s", e)
        return False


# =========================
# MESSAGES SPECIAUX
# =========================

def build_night_promo_discord() -> str:
    return (
        "🌙 **Belle nuit à chacun d’entre vous.**\n"
        "Que Dieu vous garde. 🙏\n\n"
        "Vous êtes ceux qui font notre média, vous êtes notre cœur.\n"
        f"🖋️ Pensez à nous rendre visite sur le site : {PROMO_WEBSITE_URL}\n"
        f"☕ Et si vous avez le cœur et l’opportunité, vous pouvez nous soutenir ici : {TIPEEE_URL}"
    )


def build_night_promo_telegram() -> str:
    return (
        "🌙 <b>Belle nuit à chacun d’entre vous.</b>\n"
        "Que Dieu vous garde. 🙏\n\n"
        "Vous êtes ceux qui font notre média, vous êtes notre cœur.\n"
        f"🖋️ <a href='{PROMO_WEBSITE_URL}'>Visiter le site</a>\n"
        f"☕ <a href='{TIPEEE_URL}'>Faire un don et financer le média (Tipeee)</a>"
    )


def build_reboot_notice_discord() -> str:
    return (
        "🔄 **Mise à jour effectuée**\n"
        "Le logiciel de diffusion des actualités vient d’être mis à jour pour votre confort.\n"
        f"N’hésitez pas à visiter notre site : {PROMO_WEBSITE_URL}"
    )


def build_reboot_notice_telegram() -> str:
    return (
        "🔄 <b>Mise à jour effectuée</b>\n"
        "Le logiciel de diffusion des actualités vient d’être mis à jour pour votre confort.\n"
        f"N’hésitez pas à visiter <a href='{PROMO_WEBSITE_URL}'>le site</a>."
    )


def build_recovery_notice_discord() -> str:
    return (
        "ℹ️ **Note**: la mémoire de publication a été réinitialisée, "
        "nous reprenons depuis le dernier article.\n"
        f"Si vous pensez avoir loupé des publications, consultez {PROMO_WEBSITE_URL}."
    )


def build_recovery_notice_telegram() -> str:
    return (
        "ℹ️ <b>Note</b>: la mémoire de publication a été réinitialisée, "
        "nous reprenons depuis le dernier article.\n"
        f"Si vous pensez avoir loupé des publications, consultez <a href='{PROMO_WEBSITE_URL}'>bergfrid.com</a>."
    )


def _today_str() -> str:
    return datetime.now(TZ).date().isoformat()


def _utc_ts() -> int:
    return int(datetime.utcnow().timestamp())


def should_send_reboot_notice(state: dict) -> bool:
    last_ts = int(state.get("last_reboot_notice_ts", 0) or 0)
    now = _utc_ts()
    return (now - last_ts) >= REBOOT_NOTICE_COOLDOWN_SECONDS


async def send_reboot_notice_if_needed():
    state = state_store.load()

    if not should_send_reboot_notice(state):
        log.info("Reboot/maj notice: skip (cooldown).")
        return

    targets = load_targets()
    enabled = set(targets.get("enabled", ["discord", "telegram"]))

    if "discord" in enabled:
        await send_discord_text_to_targets(build_reboot_notice_discord())

    if "telegram" in enabled:
        await send_telegram_text(build_reboot_notice_telegram(), disable_preview=True)

    state["last_reboot_notice_ts"] = _utc_ts()
    state_store.save(state)
    log.info("Reboot/maj notice: sent.")


# =========================
# DISCORD BOT INIT
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

state_store = StateStore(STATE_FILE, sent_ring_max=SENT_RING_MAX)

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


# =========================
# WATCHER RSS
# =========================

def mark_article_published_today(state: dict) -> None:
    # Global: au moins un article publié aujourd'hui (peu importe la plateforme).
    state["last_article_published_date"] = _today_str()


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

    # Recovery mode A: state vide -> publier seulement le dernier + message d'info
    if not last_seen:
        newest_entry = entries[0]
        article = entry_to_article(newest_entry, BASE_DOMAIN)
        eid = article.id

        published_any = False

        # Discord
        if "discord" in enabled and not StateStore.sent_has(state, "discord", eid):
            log.warning("Recovery publish (Discord): %s", article.title)
            ok = await discord_pub.publish(article, targets.get("discord", {}))
            if ok:
                published_any = True
                state_store.sent_add(state, "discord", eid)
                mark_article_published_today(state)
                state_store.save(state)
                await send_discord_text_to_targets(build_recovery_notice_discord())

        # Telegram
        if "telegram" in enabled and not StateStore.sent_has(state, "telegram", eid):
            log.warning("Recovery publish (Telegram): %s", article.title)
            ok = await telegram_pub.publish(article, targets.get("telegram", {}))
            if ok:
                published_any = True
                state_store.sent_add(state, "telegram", eid)
                mark_article_published_today(state)
                state_store.save(state)
                await send_telegram_text(build_recovery_notice_telegram(), disable_preview=True)

        # Commit last_id (même si une plateforme est désactivée)
        state["last_id"] = eid
        state_store.save(state)

        # Cooldown si on a effectivement publié
        if published_any:
            await asyncio.sleep(ARTICLE_PUBLISH_DELAY_SECONDS)
        return

    backlog = feed_to_backlog(feed, last_seen)

    # Recovery mode B: last_id pas retrouvé dans le feed -> éviter spam -> dernier seul + note
    if backlog and len(backlog) == len(entries):
        log.warning("last_id introuvable dans le feed. Mode recovery: publication du dernier uniquement.")
        newest_entry = entries[0]
        article = entry_to_article(newest_entry, BASE_DOMAIN)
        eid = article.id

        published_any = False

        if "discord" in enabled and not StateStore.sent_has(state, "discord", eid):
            ok = await discord_pub.publish(article, targets.get("discord", {}))
            if ok:
                published_any = True
                state_store.sent_add(state, "discord", eid)
                mark_article_published_today(state)
                state_store.save(state)
                await send_discord_text_to_targets(build_recovery_notice_discord())

        if "telegram" in enabled and not StateStore.sent_has(state, "telegram", eid):
            ok = await telegram_pub.publish(article, targets.get("telegram", {}))
            if ok:
                published_any = True
                state_store.sent_add(state, "telegram", eid)
                mark_article_published_today(state)
                state_store.save(state)
                await send_telegram_text(build_recovery_notice_telegram(), disable_preview=True)

        state["last_id"] = eid
        state_store.save(state)

        if published_any:
            await asyncio.sleep(ARTICLE_PUBLISH_DELAY_SECONDS)
        return

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
                mark_article_published_today(state)
                state_store.save(state)

        # Telegram
        telegram_ok = True
        if "telegram" in enabled and not StateStore.sent_has(state, "telegram", eid):
            log.info("Telegram publish: %s", article.title)
            telegram_ok = await telegram_pub.publish(article, targets.get("telegram", {}))
            if telegram_ok:
                published_any = True
                state_store.sent_add(state, "telegram", eid)
                mark_article_published_today(state)
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


# =========================
# PROMO 22:00 (bonne nuit)
# =========================

@tasks.loop(time=dtime(hour=PROMO_HOUR, minute=PROMO_MINUTE, tzinfo=TZ))
async def nightly_promo():
    state = state_store.load()
    today = _today_str()

    # Ne rien envoyer s'il n'y a eu aucune publication aujourd'hui
    if state.get("last_article_published_date") != today:
        log.info("Nightly promo: skip (no article published today).")
        return

    # Ne pas renvoyer plusieurs fois la même date
    if state.get("nightly_promo_sent_date") == today:
        log.info("Nightly promo: skip (already sent today).")
        return

    targets = load_targets()
    enabled = set(targets.get("enabled", ["discord", "telegram"]))

    log.info("Nightly promo: dispatch")

    if "discord" in enabled:
        await send_discord_text_to_targets(build_night_promo_discord())

    if "telegram" in enabled:
        await send_telegram_text(build_night_promo_telegram(), disable_preview=True)

    state["nightly_promo_sent_date"] = today
    state_store.save(state)


# =========================
# EVENTS / COMMANDS
# =========================

@bot.event
async def on_ready():
    log.info("Connecté: %s", bot.user)

    # Message reboot/maj (même message), avec cooldown anti-spam
    await send_reboot_notice_if_needed()

    if not bergfrid_watcher.is_running():
        bergfrid_watcher.start()
        log.info("Tâche RSS démarrée: %s min", RSS_POLL_MINUTES)

    if not nightly_promo.is_running():
        nightly_promo.start()
        log.info("Tâche promo démarrée: chaque jour à %02d:%02d (%s)", PROMO_HOUR, PROMO_MINUTE, TZ.key)


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
