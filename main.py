import asyncio
import logging
from datetime import datetime, time as dtime, timezone

import discord
from discord.ext import commands, tasks

from core.config import (
    DISCORD_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    DISCORD_OFFICIAL_CHANNEL_ID, DISCORD_LOG_CHANNEL_ID,
    DISCORD_TWITTER_CHANNEL_ID, DISCORD_SAINTS_CHANNEL_ID, DISCORD_EMBED_COLOR,
    DISCORD_SUMMARY_MAX, TELEGRAM_SUMMARY_MAX, TWITTER_TWEET_MAX,
    MASTODON_POST_MAX, BLUESKY_POST_MAX,
    DISCORD_SEND_DELAY_SECONDS,
    STATE_FILE, BERGFRID_RSS_URL, BASE_DOMAIN,
    RSS_POLL_MINUTES, RSS_FETCH_TIMEOUT, MAX_BACKLOG_POSTS_PER_TICK,
    ARTICLE_PUBLISH_DELAY_SECONDS, SENT_RING_MAX,
    TZ, PROMO_HOUR, PROMO_MINUTE, MORNING_HOUR, MORNING_MINUTE,
    TIPEEE_URL, PROMO_WEBSITE_URL, PRIERES_URL,
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
bot = commands.Bot(command_prefix="bg!", intents=intents, help_command=None)

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


async def send_publish_log(article_title: str, results: dict) -> None:
    """Send a publication status summary to the Discord log channel."""
    if not DISCORD_LOG_CHANNEL_ID:
        return
    ch = await resolve_discord_channel(DISCORD_LOG_CHANNEL_ID)
    if not ch:
        return

    lines = [f"\U0001f4cb **{article_title}**"]
    for platform in ("discord", "telegram", "mastodon", "bluesky"):
        status = results.get(platform)
        if status is None:
            continue  # not enabled / not attempted
        icon = "\u2705" if status else "\u274c"
        lines.append(f"{icon} {platform.capitalize()}")

    try:
        await ch.send("\n".join(lines))
    except Exception as e:
        log.warning("Erreur envoi log publication: %s", e)


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
    """Send an alert message to the Discord log channel only."""
    if not DISCORD_LOG_CHANNEL_ID:
        return
    ch = await resolve_discord_channel(DISCORD_LOG_CHANNEL_ID)
    if not ch:
        return
    try:
        await ch.send(f"\u26a0\ufe0f **Alerte**: {message}")
    except Exception as e:
        log.warning("Erreur envoi alerte dans le canal de logs: %s", e)


async def send_twitter_draft(article) -> None:
    """Send a Twitter-ready text to the dedicated Discord channel for copy-paste."""
    if not DISCORD_TWITTER_CHANNEL_ID:
        return
    ch = await resolve_discord_channel(DISCORD_TWITTER_CHANNEL_ID)
    if not ch:
        return

    from core.utils import determine_importance_emoji, truncate_text, add_utm
    emoji = determine_importance_emoji(article.summary)
    url = add_utm(article.url, source="twitter", medium="social", campaign="rss")

    # Build tweet: emoji + title + summary + hashtags + URL
    parts = [f"{emoji} {article.title}"]
    if article.social_summary:
        parts.append("")
        parts.append(article.social_summary)
    if article.tags:
        parts.append("")
        parts.append(" ".join(article.tags[:5]))
    parts.append("")
    parts.append(url)

    tweet = "\n".join(parts)
    # Twitter counts URLs as 23 chars; truncate summary if needed
    if len(tweet) > 280:
        budget = 280 - len(f"{emoji} {article.title}") - 23 - 4  # newlines
        if article.tags:
            tag_line = " ".join(article.tags[:5])
            budget -= len(tag_line) - 2
        else:
            tag_line = ""
        summary = truncate_text(article.social_summary, max(0, budget))
        parts = [f"{emoji} {article.title}"]
        if summary:
            parts.append("")
            parts.append(summary)
        if tag_line:
            parts.append("")
            parts.append(tag_line)
        parts.append("")
        parts.append(url)
        tweet = "\n".join(parts)

    try:
        await ch.send(f"```\n{tweet}\n```")
    except Exception as e:
        log.warning("Erreur envoi Twitter draft: %s", e)


def _today_str() -> str:
    return datetime.now(TZ).date().isoformat()


def _utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# =========================
# MESSAGES SPECIAUX
# =========================

def build_night_promo_discord() -> str:
    return (
        "\U0001f319 **22h \u2014 Fin de nos publications pour la journ\u00e9e.**\n"
        "Sauf urgence, nous reprenons demain \u00e0 9h15.\n"
        "\n"
        "\u2500\u2500\u2500\n"
        "\n"
        "Vous \u00eates ceux qui font ce m\u00e9dia. Vous \u00eates le c\u0153ur de notre travail.\n"
        "Pour nous soutenir : abonnez-vous, aimez, partagez et commentez "
        "sur tous nos r\u00e9seaux sociaux \u2014 mais surtout sur notre site web.\n"
        "\n"
        f"\U0001f310 {PROMO_WEBSITE_URL}\n"
        f"\u2615 {TIPEEE_URL}\n"
        "\n"
        "\u2500\u2500\u2500\n"
        "\n"
        "Nous vous souhaitons une agr\u00e9able nuit. "
        "Que Dieu vous garde et vous guide. \U0001f64f\n"
        f"\U0001f54e Pri\u00e8re du soir \u2192 {PRIERES_URL}"
    )


def build_night_promo_telegram() -> str:
    return (
        "\U0001f319 <b>22h \u2014 Fin de nos publications pour la journ\u00e9e.</b>\n"
        "Sauf urgence, nous reprenons demain \u00e0 9h15.\n"
        "\n"
        "Vous \u00eates ceux qui font ce m\u00e9dia. Vous \u00eates le c\u0153ur de notre travail.\n"
        "Pour nous soutenir : abonnez-vous, aimez, partagez et commentez "
        "sur tous nos r\u00e9seaux sociaux \u2014 mais surtout sur notre site web.\n"
        "\n"
        f"\U0001f310 <a href='{PROMO_WEBSITE_URL}'>Visiter le site</a>\n"
        f"\u2615 <a href='{TIPEEE_URL}'>Nous soutenir sur Tipeee</a>\n"
        "\n"
        "Nous vous souhaitons une agr\u00e9able nuit. "
        "Que Dieu vous garde et vous guide. \U0001f64f\n"
        f"\U0001f54e <a href='{PRIERES_URL}'>Pri\u00e8re du soir</a>"
    )


def _is_sunday() -> bool:
    return datetime.now(TZ).weekday() == 6


def build_morning_discord() -> str:
    sunday = (
        "\n\U0001f54d Nous vous souhaitons un joyeux dimanche et une bonne messe !\n"
        if _is_sunday() else ""
    )
    return (
        "\u2600\ufe0f **Bonjour \u00e0 tous !**\n"
        "Il est 9h, nous allons reprendre notre activit\u00e9 normale.\n"
        f"{sunday}"
        "\n"
        "Pensez \u00e0 nous suivre et nous soutenir ! "
        "Vous \u00eates ceux qui font vivre notre m\u00e9dia.\n"
        "\n"
        "Que Dieu veille sur votre journ\u00e9e. \U0001f64f\n"
        f"\U0001f54e Pri\u00e8re du jour \u2192 {PRIERES_URL}"
    )


def build_morning_telegram() -> str:
    sunday = (
        "\n\U0001f54d Nous vous souhaitons un joyeux dimanche et une bonne messe !\n"
        if _is_sunday() else ""
    )
    return (
        "\u2600\ufe0f <b>Bonjour \u00e0 tous !</b>\n"
        "Il est 9h, nous allons reprendre notre activit\u00e9 normale.\n"
        f"{sunday}"
        "\n"
        "Pensez \u00e0 nous suivre et nous soutenir ! "
        "Vous \u00eates ceux qui font vivre notre m\u00e9dia.\n"
        "\n"
        "Que Dieu veille sur votre journ\u00e9e. \U0001f64f\n"
        f"\U0001f54e <a href='{PRIERES_URL}'>Pri\u00e8re du jour</a>"
    )


def build_angelus() -> str:
    return (
        "\U0001f54e **Ang\u00e9lus**\n"
        "\n"
        "\u2123. L\u2019ange du Seigneur apporta l\u2019annonce \u00e0 Marie,\n"
        "\u211f. Et elle con\u00e7ut du Saint-Esprit.\n"
        "\n"
        "*Je vous salue, Marie, pleine de gr\u00e2ces ; "
        "le Seigneur est avec vous ; vous \u00eates b\u00e9nie entre toutes les femmes, "
        "et J\u00e9sus le fruit de vos entrailles est b\u00e9ni. "
        "Sainte Marie, M\u00e8re de Dieu, priez pour nous, pauvres p\u00e9cheurs, "
        "maintenant et \u00e0 l\u2019heure de notre mort. Amen.*\n"
        "\n"
        "\u2123. Voici la Servante du Seigneur,\n"
        "\u211f. Qu\u2019il me soit fait selon votre parole.\n"
        "\n"
        "*Je vous salue, Marie, pleine de gr\u00e2ces ; "
        "le Seigneur est avec vous ; vous \u00eates b\u00e9nie entre toutes les femmes, "
        "et J\u00e9sus le fruit de vos entrailles est b\u00e9ni. "
        "Sainte Marie, M\u00e8re de Dieu, priez pour nous, pauvres p\u00e9cheurs, "
        "maintenant et \u00e0 l\u2019heure de notre mort. Amen.*\n"
        "\n"
        "\u2123. Et le Verbe s\u2019est fait chair,\n"
        "\u211f. Et il a habit\u00e9 parmi nous.\n"
        "\n"
        "*Je vous salue, Marie, pleine de gr\u00e2ces ; "
        "le Seigneur est avec vous ; vous \u00eates b\u00e9nie entre toutes les femmes, "
        "et J\u00e9sus le fruit de vos entrailles est b\u00e9ni. "
        "Sainte Marie, M\u00e8re de Dieu, priez pour nous, pauvres p\u00e9cheurs, "
        "maintenant et \u00e0 l\u2019heure de notre mort. Amen.*\n"
        "\n"
        "\u2500\u2500\u2500\n"
        "\n"
        "**Oraison**\n"
        "\u2123. Priez pour nous, sainte M\u00e8re de Dieu,\n"
        "\u211f. Afin que nous soyons rendus dignes des promesses du Christ.\n"
        "\n"
        "*Prions. Que votre gr\u00e2ce, Seigneur notre P\u00e8re, se r\u00e9pande en nos c\u0153urs : "
        "par le message de l\u2019Ange vous nous avez fait conna\u00eetre l\u2019Incarnation "
        "de votre Fils bien-aim\u00e9, conduisez-nous par sa passion et par sa croix "
        "jusqu\u2019\u00e0 la gloire de la r\u00e9surrection. "
        "Par J\u00e9sus, le Christ, notre Seigneur. Amen.*\n"
        "\n"
        f"\U0001f54e {PRIERES_URL}"
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

    # Envoyer uniquement dans le canal de logs Discord
    if DISCORD_LOG_CHANNEL_ID:
        ch = await resolve_discord_channel(DISCORD_LOG_CHANNEL_ID)
        if ch:
            try:
                await ch.send("\U0001f504 **Mise \u00e0 jour effectu\u00e9e.**")
            except Exception as e:
                log.warning("Erreur envoi reboot notice dans le canal de logs: %s", e)

    state["last_reboot_notice_ts"] = _utc_ts()
    state_store.save(state)
    log.info("Reboot/maj notice: sent (log channel).")


# =========================
# SEED (anti-doublon au redemarrage)
# =========================

def _seed_state_from_entries(entries, state, enabled) -> None:
    """Seed state from current RSS entries without publishing.

    Marks all current entries as already sent on every enabled platform,
    so that only genuinely NEW articles get published after a redeploy.
    """
    all_platforms = {"discord", "telegram"}
    all_platforms.update(_optional_publishers.keys())
    active = all_platforms & set(enabled)

    count = 0
    for entry in entries:
        article = entry_to_article(entry, BASE_DOMAIN)
        eid = article.id
        for platform in active:
            state_store.sent_add(state, platform, eid)
        count += 1

    if entries:
        newest = entry_to_article(entries[0], BASE_DOMAIN)
        state["last_id"] = newest.id

    state_store.save(state)
    log.info("Seed: %d articles marques comme deja envoyes sur %s.", count, ", ".join(sorted(active)))


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

    # Recovery mode A: state vide — seed sans publier
    if not last_seen:
        log.info("State vide: seed depuis le flux RSS (pas de publication).")
        _seed_state_from_entries(entries, state, enabled)
        return

    backlog = feed_to_backlog(feed, last_seen)

    # Recovery mode B: last_id introuvable — seed sans publier
    if backlog and len(backlog) == len(entries):
        log.warning("last_id introuvable dans le feed. Seed sans publication.")
        _seed_state_from_entries(entries, state, enabled)
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
        pub_results = {}  # platform -> True/False

        # Discord
        if "discord" in enabled and not StateStore.sent_has(state, "discord", eid):
            log.info("Publication Discord: %s", article.title)
            discord_ok = await discord_pub.publish(article, targets.get("discord", {}))
            pub_results["discord"] = discord_ok
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
            pub_results["telegram"] = telegram_ok
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
                pub_results[platform] = plat_ok
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

        # Log de publication sur Discord
        if pub_results:
            await send_publish_log(article.title, pub_results)

        # Twitter draft pour copier-coller
        if published_any:
            await send_twitter_draft(article)

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
            await send_publish_log(article.title, {platform: ok})
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
# BONJOUR 09:00 (matin)
# =========================

@tasks.loop(time=dtime(hour=MORNING_HOUR, minute=MORNING_MINUTE, tzinfo=TZ))
async def morning_message():
    state = state_store.load()
    today = _today_str()

    if state.get("morning_sent_date") == today:
        log.info("Morning message: skip (already sent today).")
        return

    targets = load_targets()
    enabled = set(targets.get("enabled", ["discord", "telegram"]))

    log.info("Morning message: dispatch")

    if "discord" in enabled:
        await send_discord_text_to_targets(build_morning_discord())

    if "telegram" in enabled:
        await send_telegram_text(build_morning_telegram(), disable_preview=True)

    state["morning_sent_date"] = today
    state_store.save(state)


# =========================
# ANGELUS (7h, 12h, 19h)
# =========================

_angelus_times = [
    dtime(hour=7, minute=0, tzinfo=TZ),
    dtime(hour=12, minute=0, tzinfo=TZ),
    dtime(hour=19, minute=0, tzinfo=TZ),
]


@tasks.loop(time=_angelus_times)
async def angelus_task():
    if not DISCORD_SAINTS_CHANNEL_ID:
        return

    ch = await resolve_discord_channel(DISCORD_SAINTS_CHANNEL_ID)
    if not ch:
        log.warning("Angelus: canal saints introuvable (%d).", DISCORD_SAINTS_CHANNEL_ID)
        return

    now = datetime.now(TZ)
    hour_label = f"{now.hour}h"
    log.info("Angelus: envoi (%s)", hour_label)

    try:
        await ch.send(build_angelus())
    except Exception as e:
        log.warning("Erreur envoi Angelus: %s", e)


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

    if not morning_message.is_running():
        morning_message.start()
        log.info("Tache matin demarree: chaque jour a %02d:%02d (%s)", MORNING_HOUR, MORNING_MINUTE, TZ.key)

    if not angelus_task.is_running():
        angelus_task.start()
        log.info("Tache Angelus demarree: 7h, 12h, 19h (%s)", TZ.key)


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


@bot.command(name="help")
async def help_command(ctx: commands.Context):
    """Show available commands."""
    embed = discord.Embed(
        title="Bergfrid",
        description=(
            "Publication automatique des articles de "
            "[bergfrid.com](https://www.bergfrid.com) "
            "sur Discord, Telegram, Mastodon et Bluesky.\n\n"
            "*Chaque nouvel article est relay\u00e9 ici en temps r\u00e9el.*"
        ),
        color=DISCORD_EMBED_COLOR,
    )

    embed.add_field(
        name="\u2500\u2500\u2500  Commandes  \u2500\u2500\u2500",
        value="`bg!help` \u2500 Affiche cette aide",
        inline=False,
    )

    # Admin commands: show only if user has manage_channels
    is_admin = ctx.channel.permissions_for(ctx.author).manage_channels if ctx.guild else False
    if is_admin:
        admin_cmds = (
            "`bg!setnews [#canal]` \u2500 D\u00e9finir le canal de publication\n"
            "`bg!unsetnews` \u2500 Retirer le canal de publication\n"
            "`bg!rsssync` \u2500 Synchroniser l'\u00e9tat RSS\n"
            "`bg!preview <nom>` \u2500 Pr\u00e9visualiser un message *(canal log)*"
        )
        embed.add_field(
            name="\u2500\u2500\u2500  Administration  \u2500\u2500\u2500",
            value=admin_cmds,
            inline=False,
        )

    embed.set_footer(text="bergfrid.com \u2014 M\u00e9dia ind\u00e9pendant")
    await ctx.send(embed=embed)


@bot.command(name="preview")
@commands.has_permissions(manage_channels=True)
async def preview_message(ctx: commands.Context, nom: str = ""):
    """Preview special messages (admin only, log channel only)."""
    if DISCORD_LOG_CHANNEL_ID and ctx.channel.id != DISCORD_LOG_CHANNEL_ID:
        await ctx.send("\u26d4 Cette commande n'est disponible que dans le canal de logs.")
        return

    nom = nom.strip().lower()

    # Article-based previews
    if nom in ("x", "article"):
        state = state_store.load()
        feed = await parse_rss_with_cache(BERGFRID_RSS_URL, BASE_DOMAIN, state, timeout=RSS_FETCH_TIMEOUT)
        entries = getattr(feed, "entries", None) or []
        if not entries:
            await ctx.send("\u26a0\ufe0f Flux RSS vide ou inaccessible.")
            return
        article = entry_to_article(entries[0], BASE_DOMAIN)

        if nom == "x":
            from core.utils import determine_importance_emoji, truncate_text, add_utm
            emoji = determine_importance_emoji(article.summary)
            url = add_utm(article.url, source="twitter", medium="social", campaign="rss")
            parts = [f"{emoji} {article.title}"]
            if article.social_summary:
                parts.append("")
                parts.append(article.social_summary)
            if article.tags:
                parts.append("")
                parts.append(" ".join(article.tags[:5]))
            parts.append("")
            parts.append(url)
            tweet = "\n".join(parts)
            if len(tweet) > 280:
                budget = 280 - len(f"{emoji} {article.title}") - 23 - 4
                tag_line = " ".join(article.tags[:5]) if article.tags else ""
                if tag_line:
                    budget -= len(tag_line) - 2
                summary = truncate_text(article.social_summary, max(0, budget))
                parts = [f"{emoji} {article.title}"]
                if summary:
                    parts.append("")
                    parts.append(summary)
                if tag_line:
                    parts.append("")
                    parts.append(tag_line)
                parts.append("")
                parts.append(url)
                tweet = "\n".join(parts)
            await ctx.send(f"\U0001f50d **Preview : Dernier article (format Twitter/X)**\n\u2500\u2500\u2500\n```\n{tweet}\n```")
        else:
            from core.utils import determine_importance_emoji, prettify_summary, truncate_text, add_utm
            url = add_utm(article.url, source="discord", medium="social", campaign="rss")
            emoji = determine_importance_emoji(article.summary)
            desc = prettify_summary(article.summary, DISCORD_SUMMARY_MAX, prefix="", max_paragraphs=4)
            if article.tags:
                desc = f"{desc}\n\n{' '.join(article.tags[:6])}"
            embed = discord.Embed(
                title=truncate_text(f"{emoji} {article.title}", 256),
                url=url, description=desc, color=DISCORD_EMBED_COLOR,
            )
            if article.image_url:
                embed.set_image(url=article.image_url)
            footer_parts = []
            if article.category:
                footer_parts.append(article.category)
            if article.author:
                footer_parts.append(article.author)
            if footer_parts:
                embed.set_footer(text=" \u00b7 ".join(footer_parts))
            if article.published_at:
                embed.timestamp = article.published_at
            await ctx.send("\U0001f50d **Preview : Dernier article (format Discord)**\n\u2500\u2500\u2500")
            await ctx.send(embed=embed)
        return

    previews = {
        "nuit": ("Bonne nuit (Discord)", build_night_promo_discord()),
        "nuit-tg": ("Bonne nuit (Telegram)", build_night_promo_telegram()),
        "matin": ("Bonjour (Discord)", build_morning_discord()),
        "matin-tg": ("Bonjour (Telegram)", build_morning_telegram()),
        "angelus": ("Ang\u00e9lus", build_angelus()),
        "reboot": ("Mise \u00e0 jour", "\U0001f504 **Mise \u00e0 jour effectu\u00e9e.**"),
    }

    if not nom or nom not in previews:
        all_noms = ", ".join(f"`{k}`" for k in list(previews.keys()) + ["x", "article"])
        await ctx.send(f"\U0001f4cb Messages disponibles : {all_noms}\nUsage : `bg!preview <nom>`")
        return

    label, content = previews[nom]
    await ctx.send(f"\U0001f50d **Preview : {label}**\n\u2500\u2500\u2500\n{content}")


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
