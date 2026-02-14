import asyncio
import logging
from datetime import datetime, time as dtime, timezone

import discord
from discord.ext import commands, tasks

from core.config import (
    DISCORD_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    DISCORD_OFFICIAL_CHANNEL_ID,
    DISCORD_SUMMARY_MAX, TELEGRAM_SUMMARY_MAX, TWITTER_TWEET_MAX,
    MASTODON_POST_MAX, BLUESKY_POST_MAX,
    DISCORD_SEND_DELAY_SECONDS,
    STATE_FILE, BERGFRID_RSS_URL, BASE_DOMAIN,
    RSS_POLL_MINUTES, RSS_FETCH_TIMEOUT, MAX_BACKLOG_POSTS_PER_TICK,
    ARTICLE_PUBLISH_DELAY_SECONDS, SENT_RING_MAX,
    TZ, PROMO_HOUR, PROMO_MINUTE, TIPEEE_URL, PROMO_WEBSITE_URL,
    FAILURE_ALERT_THRESHOLD,
    PUBLISH_MAX_RETRIES, PUBLISH_RETRY_BASE_DELAY,
    REBOOT_NOTICE_COOLDOWN_SECONDS,
    TWITTER_API_KEY, TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET,
    MASTODON_INSTANCE_URL, MASTODON_ACCESS_TOKEN,
    BLUESKY_HANDLE, BLUESKY_APP_PASSWORD,
    validate_required_env, load_targets,
    load_discord_channels_map, save_discord_channels_map,
    get_all_discord_target_channel_ids,
)
from core.state import StateStore
from core.rss import parse_rss_with_cache, feed_to_backlog, entry_to_article
from core.monitoring import HealthMonitor

from publishers.discord_pub import DiscordPublisher
from publishers.telegram_pub import TelegramPublisher
from publishers.twitter_pub import TwitterPublisher
from publishers.mastodon_pub import MastodonPublisher
from publishers.bluesky_pub import BlueskyPublisher

try:
    import aiohttp
except ImportError:
    aiohttp = None


# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bergfrid")


# =========================
# INIT
# =========================

validate_required_env()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

state_store = StateStore(STATE_FILE, sent_ring_max=SENT_RING_MAX)

discord_pub = DiscordPublisher(
    bot=bot,
    official_channel_id=DISCORD_OFFICIAL_CHANNEL_ID,
    send_delay=DISCORD_SEND_DELAY_SECONDS,
    summary_max=DISCORD_SUMMARY_MAX,
)

telegram_pub = TelegramPublisher(
    token=TELEGRAM_TOKEN,
    chat_id=TELEGRAM_CHAT_ID,
    summary_max=TELEGRAM_SUMMARY_MAX,
    max_retries=PUBLISH_MAX_RETRIES,
    retry_base_delay=PUBLISH_RETRY_BASE_DELAY,
)

_twitter_keys = [TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]
if all(_twitter_keys):
    twitter_pub = TwitterPublisher(
        api_key=TWITTER_API_KEY,
        api_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_secret=TWITTER_ACCESS_SECRET,
        tweet_max=TWITTER_TWEET_MAX,
        max_retries=PUBLISH_MAX_RETRIES,
        retry_base_delay=PUBLISH_RETRY_BASE_DELAY,
    )
    log.info("Twitter publisher: ACTIVE (4 cles configurees).")
else:
    twitter_pub = None
    missing = []
    if not TWITTER_API_KEY:
        missing.append("TWITTER_API_KEY")
    if not TWITTER_API_SECRET:
        missing.append("TWITTER_API_SECRET")
    if not TWITTER_ACCESS_TOKEN:
        missing.append("TWITTER_ACCESS_TOKEN")
    if not TWITTER_ACCESS_SECRET:
        missing.append("TWITTER_ACCESS_SECRET")
    log.warning("Twitter publisher: DESACTIVE. Variables manquantes: %s", ", ".join(missing))

if all([MASTODON_INSTANCE_URL, MASTODON_ACCESS_TOKEN]):
    mastodon_pub = MastodonPublisher(
        instance_url=MASTODON_INSTANCE_URL,
        access_token=MASTODON_ACCESS_TOKEN,
        post_max=MASTODON_POST_MAX,
        max_retries=PUBLISH_MAX_RETRIES,
        retry_base_delay=PUBLISH_RETRY_BASE_DELAY,
    )
    log.info("Mastodon publisher: ACTIVE (%s).", MASTODON_INSTANCE_URL)
else:
    mastodon_pub = None
    log.info("Mastodon publisher: DESACTIVE (variables non configurees).")

if all([BLUESKY_HANDLE, BLUESKY_APP_PASSWORD]):
    bluesky_pub = BlueskyPublisher(
        handle=BLUESKY_HANDLE,
        app_password=BLUESKY_APP_PASSWORD,
        post_max=BLUESKY_POST_MAX,
        max_retries=PUBLISH_MAX_RETRIES,
        retry_base_delay=PUBLISH_RETRY_BASE_DELAY,
    )
    log.info("Bluesky publisher: ACTIVE (@%s).", BLUESKY_HANDLE)
else:
    bluesky_pub = None
    log.info("Bluesky publisher: DESACTIVE (variables non configurees).")

# Dict des publishers optionnels (sans message de recovery special)
_optional_publishers = {}
if twitter_pub:
    _optional_publishers["twitter"] = twitter_pub
if mastodon_pub:
    _optional_publishers["mastodon"] = mastodon_pub
if bluesky_pub:
    _optional_publishers["bluesky"] = bluesky_pub

health = HealthMonitor(alert_threshold=FAILURE_ALERT_THRESHOLD)


# =========================
# HELPERS
# =========================

async def resolve_discord_channel(cid: int):
    ch = bot.get_channel(cid)
    if ch is not None:
        return ch
    try:
        return await bot.fetch_channel(cid)
    except Exception as e:
        log.warning("Impossible de resoudre le canal Discord %d: %s", cid, e)
        return None


async def send_discord_text_to_targets(text: str) -> None:
    for cid in get_all_discord_target_channel_ids():
        ch = await resolve_discord_channel(cid)
        if not ch:
            continue
        try:
            await ch.send(content=text)
        except Exception as e:
            log.warning("Erreur envoi texte Discord canal %d: %s", cid, e)
        await asyncio.sleep(DISCORD_SEND_DELAY_SECONDS)


async def send_telegram_text(text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> bool:
    if aiohttp is None:
        log.error("aiohttp non installe: impossible d'envoyer sur Telegram.")
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
                    log.warning("Telegram msg special erreur status=%s body=%s", resp.status, body[:600])
                    return False
        return True
    except Exception as e:
        log.warning("Telegram msg special exception: %s", e)
        return False


async def send_alert_to_platforms(message: str) -> None:
    """Send an alert message to all enabled platforms."""
    targets = load_targets()
    enabled = set(targets.get("enabled", []))
    if "discord" in enabled:
        await send_discord_text_to_targets(f"\u26a0\ufe0f **Alerte Bergfrid**: {message}")
    if "telegram" in enabled:
        await send_telegram_text(f"\u26a0\ufe0f <b>Alerte Bergfrid</b>: {message}")


def _today_str() -> str:
    return datetime.now(TZ).date().isoformat()


def _utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# =========================
# MESSAGES SPECIAUX
# =========================

def build_night_promo_discord() -> str:
    return (
        "\U0001f319 **Belle nuit \u00e0 chacun d'entre vous.**\n"
        "Que Dieu vous garde. \U0001f64f\n\n"
        "Vous \u00eates ceux qui font notre m\u00e9dia, vous \u00eates notre c\u0153ur.\n"
        f"\U0001f58b\ufe0f Pensez \u00e0 nous rendre visite sur le site : {PROMO_WEBSITE_URL}\n"
        f"\u2615 Et si vous avez le c\u0153ur et l'opportunit\u00e9, vous pouvez nous soutenir ici : {TIPEEE_URL}"
    )


def build_night_promo_telegram() -> str:
    return (
        "\U0001f319 <b>Belle nuit \u00e0 chacun d'entre vous.</b>\n"
        "Que Dieu vous garde. \U0001f64f\n\n"
        "Vous \u00eates ceux qui font notre m\u00e9dia, vous \u00eates notre c\u0153ur.\n"
        f"\U0001f58b\ufe0f <a href='{PROMO_WEBSITE_URL}'>Visiter le site</a>\n"
        f"\u2615 <a href='{TIPEEE_URL}'>Faire un don et financer le m\u00e9dia (Tipeee)</a>"
    )


def build_reboot_notice_discord() -> str:
    return (
        "\U0001f504 **Mise \u00e0 jour effectu\u00e9e**\n"
        "Le logiciel de diffusion des actualit\u00e9s vient d'\u00eatre mis \u00e0 jour pour votre confort.\n"
        f"N'h\u00e9sitez pas \u00e0 visiter notre site : {PROMO_WEBSITE_URL}"
    )


def build_reboot_notice_telegram() -> str:
    return (
        "\U0001f504 <b>Mise \u00e0 jour effectu\u00e9e</b>\n"
        "Le logiciel de diffusion des actualit\u00e9s vient d'\u00eatre mis \u00e0 jour pour votre confort.\n"
        f"N'h\u00e9sitez pas \u00e0 visiter <a href='{PROMO_WEBSITE_URL}'>le site</a>."
    )


def build_recovery_notice_discord() -> str:
    return (
        "\u2139\ufe0f **Note**: la m\u00e9moire de publication a \u00e9t\u00e9 r\u00e9initialis\u00e9e, "
        "nous reprenons depuis le dernier article.\n"
        f"Si vous pensez avoir loup\u00e9 des publications, consultez {PROMO_WEBSITE_URL}."
    )


def build_recovery_notice_telegram() -> str:
    return (
        "\u2139\ufe0f <b>Note</b>: la m\u00e9moire de publication a \u00e9t\u00e9 r\u00e9initialis\u00e9e, "
        "nous reprenons depuis le dernier article.\n"
        f"Si vous pensez avoir loup\u00e9 des publications, consultez "
        f"<a href='{PROMO_WEBSITE_URL}'>bergfrid.com</a>."
    )


# =========================
# REBOOT NOTICE
# =========================

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
# RECOVERY (factored)
# =========================

async def _recovery_publish(article, state, enabled, targets, reason: str) -> None:
    """Publish a single article in recovery mode with recovery notice."""
    eid = article.id
    published_any = False
    log.warning("Recovery (%s): publication de '%s'", reason, article.title)

    if "discord" in enabled and not StateStore.sent_has(state, "discord", eid):
        ok = await discord_pub.publish(article, targets.get("discord", {}))
        if ok:
            published_any = True
            state_store.sent_add(state, "discord", eid)
            mark_article_published_today(state)
            state_store.save(state)
            await send_discord_text_to_targets(build_recovery_notice_discord())
            health.record_success("discord")
        else:
            if health.record_failure("discord"):
                await send_alert_to_platforms(
                    f"Discord a echoue {health.get_failures('discord')} fois consecutivement."
                )

    if "telegram" in enabled and not StateStore.sent_has(state, "telegram", eid):
        ok = await telegram_pub.publish(article, targets.get("telegram", {}))
        if ok:
            published_any = True
            state_store.sent_add(state, "telegram", eid)
            mark_article_published_today(state)
            state_store.save(state)
            await send_telegram_text(build_recovery_notice_telegram(), disable_preview=True)
            health.record_success("telegram")
        else:
            if health.record_failure("telegram"):
                await send_alert_to_platforms(
                    f"Telegram a echoue {health.get_failures('telegram')} fois consecutivement."
                )

    # Plateformes optionnelles (twitter, mastodon, bluesky)
    for platform, pub in _optional_publishers.items():
        if platform in enabled and not StateStore.sent_has(state, platform, eid):
            ok = await pub.publish(article, targets.get(platform, {}))
            if ok:
                published_any = True
                state_store.sent_add(state, platform, eid)
                mark_article_published_today(state)
                state_store.save(state)
                health.record_success(platform)
            else:
                if health.record_failure(platform):
                    await send_alert_to_platforms(
                        f"{platform.capitalize()} a echoue {health.get_failures(platform)} fois consecutivement."
                    )

    state["last_id"] = eid
    state_store.save(state)

    if published_any:
        await asyncio.sleep(ARTICLE_PUBLISH_DELAY_SECONDS)


# =========================
# WATCHER RSS
# =========================

def mark_article_published_today(state: dict) -> None:
    state["last_article_published_date"] = _today_str()


@tasks.loop(minutes=RSS_POLL_MINUTES)
async def bergfrid_watcher():
    targets = load_targets()
    enabled = set(targets.get("enabled", ["discord", "telegram"]))

    state = state_store.load()
    last_seen = state.get("last_id")

    feed = await parse_rss_with_cache(
        BERGFRID_RSS_URL, BASE_DOMAIN, state, timeout=RSS_FETCH_TIMEOUT
    )
    state_store.save(state)  # persist etag/modified even if no publish

    entries = getattr(feed, "entries", None) or []
    if not entries:
        return

    # Recovery mode A: state vide
    if not last_seen:
        article = entry_to_article(entries[0], BASE_DOMAIN)
        await _recovery_publish(article, state, enabled, targets, reason="state vide")
        return

    backlog = feed_to_backlog(feed, last_seen)

    # Recovery mode B: last_id introuvable dans le feed
    if backlog and len(backlog) == len(entries):
        log.warning("last_id introuvable dans le feed. Mode recovery.")
        article = entry_to_article(entries[0], BASE_DOMAIN)
        await _recovery_publish(article, state, enabled, targets, reason="last_id introuvable")
        return

    if not backlog:
        await _catchup_missing_platforms(entries, state, enabled, targets)
        return

    if len(backlog) > MAX_BACKLOG_POSTS_PER_TICK:
        log.warning("Backlog=%d > max=%d. Troncature.", len(backlog), MAX_BACKLOG_POSTS_PER_TICK)
        backlog = backlog[:MAX_BACKLOG_POSTS_PER_TICK]

    # Publication du plus ancien au plus recent
    for entry in reversed(backlog):
        article = entry_to_article(entry, BASE_DOMAIN)
        eid = article.id
        published_any = False
        all_ok = True

        # Discord
        if "discord" in enabled and not StateStore.sent_has(state, "discord", eid):
            log.info("Publication Discord: %s", article.title)
            discord_ok = await discord_pub.publish(article, targets.get("discord", {}))
            if discord_ok:
                published_any = True
                state_store.sent_add(state, "discord", eid)
                mark_article_published_today(state)
                state_store.save(state)
                health.record_success("discord")
            else:
                all_ok = False
                if health.record_failure("discord"):
                    await send_alert_to_platforms(
                        f"Discord a echoue {health.get_failures('discord')} fois consecutivement."
                    )

        # Telegram
        if "telegram" in enabled and not StateStore.sent_has(state, "telegram", eid):
            log.info("Publication Telegram: %s", article.title)
            telegram_ok = await telegram_pub.publish(article, targets.get("telegram", {}))
            if telegram_ok:
                published_any = True
                state_store.sent_add(state, "telegram", eid)
                mark_article_published_today(state)
                state_store.save(state)
                health.record_success("telegram")
            else:
                all_ok = False
                if health.record_failure("telegram"):
                    await send_alert_to_platforms(
                        f"Telegram a echoue {health.get_failures('telegram')} fois consecutivement."
                    )

        # Plateformes optionnelles (twitter, mastodon, bluesky)
        for platform, pub in _optional_publishers.items():
            if platform in enabled and not StateStore.sent_has(state, platform, eid):
                log.info("Publication %s: %s", platform, article.title)
                plat_ok = await pub.publish(article, targets.get(platform, {}))
                if plat_ok:
                    published_any = True
                    state_store.sent_add(state, platform, eid)
                    mark_article_published_today(state)
                    state_store.save(state)
                    health.record_success(platform)
                else:
                    all_ok = False
                    if health.record_failure(platform):
                        await send_alert_to_platforms(
                            f"{platform.capitalize()} a echoue {health.get_failures(platform)} fois consecutivement."
                        )

        if all_ok:
            state["last_id"] = eid
            state_store.save(state)
        else:
            log.warning("Publication partielle pour id=%s. Stop pour retry au prochain tick.", eid)
            return

        if published_any:
            await asyncio.sleep(ARTICLE_PUBLISH_DELAY_SECONDS)

    # Rattrapage: plateformes qui ont manque des articles recents
    await _catchup_missing_platforms(entries, state, enabled, targets)


# =========================
# CATCHUP (rattrapage par plateforme)
# =========================

CATCHUP_WINDOW = 5  # nombre d'articles recents a verifier


async def _catchup_missing_platforms(entries, state, enabled, targets):
    """Publie les articles recents manquants sur les plateformes en retard."""
    publishers = {"discord": discord_pub, "telegram": telegram_pub}
    publishers.update(_optional_publishers)

    for entry in entries[:CATCHUP_WINDOW]:
        article = entry_to_article(entry, BASE_DOMAIN)
        eid = article.id

        for platform, pub in publishers.items():
            if platform not in enabled or pub is None:
                continue
            if StateStore.sent_has(state, platform, eid):
                continue

            # Seulement rattraper si au moins une autre plateforme l'a deja publie
            sent_elsewhere = any(
                StateStore.sent_has(state, other, eid)
                for other in publishers if other != platform
            )
            if not sent_elsewhere:
                continue

            log.info("Rattrapage %s: %s", platform, article.title)
            ok = await pub.publish(article, targets.get(platform, {}))
            if ok:
                state_store.sent_add(state, platform, eid)
                mark_article_published_today(state)
                state_store.save(state)
                health.record_success(platform)
            else:
                if health.record_failure(platform):
                    await send_alert_to_platforms(
                        f"{platform.capitalize()} a echoue {health.get_failures(platform)} fois consecutivement."
                    )
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

    # Ne pas renvoyer plusieurs fois la meme date
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
    log.info("Connecte: %s", bot.user)

    # Message reboot/maj avec cooldown anti-spam
    await send_reboot_notice_if_needed()

    if not bergfrid_watcher.is_running():
        bergfrid_watcher.start()
        log.info("Tache RSS demarree: %s min", RSS_POLL_MINUTES)

    if not nightly_promo.is_running():
        nightly_promo.start()
        log.info("Tache promo demarree: chaque jour a %02d:%02d (%s)", PROMO_HOUR, PROMO_MINUTE, TZ.key)


@bot.command(name="setnews")
@commands.has_permissions(manage_channels=True)
async def set_news_channel(ctx: commands.Context, channel: discord.TextChannel = None):
    channel = ctx.channel if channel is None else channel

    channels_map = load_discord_channels_map()
    channels_map[str(ctx.guild.id)] = int(channel.id)
    save_discord_channels_map(channels_map)

    await ctx.send(f"\u2705 Ce serveur publiera les nouvelles dans {channel.mention}.")


@bot.command(name="unsetnews")
@commands.has_permissions(manage_channels=True)
async def unset_news_channel(ctx: commands.Context):
    channels_map = load_discord_channels_map()
    gid = str(ctx.guild.id)
    if gid in channels_map:
        del channels_map[gid]
        save_discord_channels_map(channels_map)
        await ctx.send("\u274c Canal de nouvelles retire pour ce serveur.")
    else:
        await ctx.send("\u2139\ufe0f Aucun canal n'\u00e9tait configure.")


@bot.command(name="rsssync")
@commands.has_permissions(manage_channels=True)
async def rss_sync(ctx: commands.Context):
    state = state_store.load()
    feed = await parse_rss_with_cache(BERGFRID_RSS_URL, BASE_DOMAIN, state, timeout=RSS_FETCH_TIMEOUT)
    state_store.save(state)

    entries = getattr(feed, "entries", None) or []
    if not entries:
        await ctx.send("\u26a0\ufe0f Flux RSS vide ou inaccessible.")
        return

    newest_entry = entries[0]
    article = entry_to_article(newest_entry, BASE_DOMAIN)
    state["last_id"] = article.id
    state_store.save(state)

    await ctx.send(f"\u2705 Synchronise sur last_id={state['last_id']} (aucune publication).")


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
