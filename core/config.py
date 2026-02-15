"""Centralized configuration for Bergfrid bot."""

import os
import json
import logging
from zoneinfo import ZoneInfo

log = logging.getLogger("bergfrid.config")

# =========================
# Tokens & IDs (validated at startup via validate_required_env)
# =========================
DISCORD_TOKEN: str = os.environ.get("DISCORD_TOKEN", "")
TELEGRAM_TOKEN: str = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
DISCORD_OFFICIAL_CHANNEL_ID: int = int(os.getenv("DISCORD_NEWS_CHANNEL_ID", "1330916602425770088"))
DISCORD_LOG_CHANNEL_ID: int = int(os.getenv("DISCORD_LOG_CHANNEL_ID", "0"))

# Twitter / X
TWITTER_API_KEY: str = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET: str = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN: str = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET: str = os.environ.get("TWITTER_ACCESS_SECRET", "")

# Mastodon
MASTODON_INSTANCE_URL: str = os.environ.get("MASTODON_INSTANCE_URL", "")
MASTODON_ACCESS_TOKEN: str = os.environ.get("MASTODON_ACCESS_TOKEN", "")

# Bluesky
BLUESKY_HANDLE: str = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD: str = os.environ.get("BLUESKY_APP_PASSWORD", "")

# =========================
# File paths
# =========================
STATE_FILE: str = os.getenv("STATE_FILE", "bergfrid_state.json")
DISCORD_CHANNELS_FILE: str = os.getenv("DISCORD_CHANNELS_FILE", "discord_channels.json")
TARGETS_FILE: str = os.getenv("PUBLISH_TARGETS_FILE", "config/publish_targets.json")

# =========================
# RSS
# =========================
BERGFRID_RSS_URL: str = "https://bergfrid.com/rss.xml"
BASE_DOMAIN: str = "https://bergfrid.com"
RSS_POLL_MINUTES: float = float(os.getenv("RSS_POLL_MINUTES", "2.0"))
RSS_FETCH_TIMEOUT: float = float(os.getenv("RSS_FETCH_TIMEOUT", "30"))
MAX_BACKLOG_POSTS_PER_TICK: int = int(os.getenv("MAX_BACKLOG_POSTS_PER_TICK", "20"))

# =========================
# Publishing
# =========================
DISCORD_SUMMARY_MAX: int = int(os.getenv("DISCORD_SUMMARY_MAX", "2200"))
TELEGRAM_SUMMARY_MAX: int = int(os.getenv("TELEGRAM_SUMMARY_MAX", "900"))
TWITTER_TWEET_MAX: int = int(os.getenv("TWITTER_TWEET_MAX", "280"))
MASTODON_POST_MAX: int = int(os.getenv("MASTODON_POST_MAX", "500"))
BLUESKY_POST_MAX: int = int(os.getenv("BLUESKY_POST_MAX", "300"))
DISCORD_SEND_DELAY_SECONDS: float = float(os.getenv("DISCORD_SEND_DELAY_SECONDS", "0.2"))
ARTICLE_PUBLISH_DELAY_SECONDS: float = float(os.getenv("ARTICLE_PUBLISH_DELAY_SECONDS", "30"))
SENT_RING_MAX: int = int(os.getenv("SENT_RING_MAX", "250"))

# =========================
# Retry / backoff
# =========================
PUBLISH_MAX_RETRIES: int = int(os.getenv("PUBLISH_MAX_RETRIES", "3"))
PUBLISH_RETRY_BASE_DELAY: float = float(os.getenv("PUBLISH_RETRY_BASE_DELAY", "5"))

# =========================
# Monitoring
# =========================
FAILURE_ALERT_THRESHOLD: int = int(os.getenv("FAILURE_ALERT_THRESHOLD", "5"))

# =========================
# Promo
# =========================
TZ: ZoneInfo = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Paris"))
PROMO_HOUR: int = int(os.getenv("PROMO_HOUR", "22"))
PROMO_MINUTE: int = int(os.getenv("PROMO_MINUTE", "0"))
TIPEEE_URL: str = os.getenv("TIPEEE_URL", "https://fr.tipeee.com/parlement-des-hiboux")
PROMO_WEBSITE_URL: str = os.getenv("PROMO_WEBSITE_URL", "https://www.bergfrid.com")

# =========================
# Reboot notice
# =========================
REBOOT_NOTICE_COOLDOWN_SECONDS: int = int(os.getenv("REBOOT_NOTICE_COOLDOWN_SECONDS", "600"))

# =========================
# Embed styling
# =========================
DISCORD_EMBED_COLOR: int = 0x0B0F14


def validate_required_env() -> None:
    """Validate that required environment variables are set. Call at startup."""
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise EnvironmentError(
            f"Variables d'environnement requises manquantes: {', '.join(missing)}"
        )
    # Twitter: warn if enabled but missing keys
    twitter_vars = [TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]
    if any(twitter_vars) and not all(twitter_vars):
        log.warning("Twitter partiellement configure: certaines cles manquent.")
    # Mastodon: warn if partial
    mastodon_vars = [MASTODON_INSTANCE_URL, MASTODON_ACCESS_TOKEN]
    if any(mastodon_vars) and not all(mastodon_vars):
        log.warning("Mastodon partiellement configure: certaines cles manquent.")
    # Bluesky: warn if partial
    bluesky_vars = [BLUESKY_HANDLE, BLUESKY_APP_PASSWORD]
    if any(bluesky_vars) and not all(bluesky_vars):
        log.warning("Bluesky partiellement configure: certaines cles manquent.")


def load_targets() -> dict:
    """Load publish_targets.json with safe defaults."""
    try:
        with open(TARGETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("publish_targets doit etre un dict")
        data.setdefault("enabled", ["discord", "telegram"])
        data.setdefault("discord", {})
        data.setdefault("telegram", {})
        data.setdefault("twitter", {})
        data.setdefault("mastodon", {})
        data.setdefault("bluesky", {})
        return data
    except FileNotFoundError:
        log.warning("Fichier %s introuvable, valeurs par defaut.", TARGETS_FILE)
        return {"enabled": ["discord", "telegram"], "discord": {}, "telegram": {}}
    except (json.JSONDecodeError, ValueError) as e:
        log.error("Erreur lecture %s: %s", TARGETS_FILE, e)
        return {"enabled": ["discord", "telegram"], "discord": {}, "telegram": {}}


def load_discord_channels_map() -> dict:
    """Load discord_channels.json mapping guild_id -> channel_id."""
    if not os.path.exists(DISCORD_CHANNELS_FILE):
        return {}
    try:
        with open(DISCORD_CHANNELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log.warning("discord_channels.json invalide (pas un dict).")
            return {}
        out = {}
        for k, v in data.items():
            try:
                out[str(k)] = int(v)
            except (ValueError, TypeError):
                log.warning("Entree invalide dans discord_channels.json: %s=%s", k, v)
        return out
    except (json.JSONDecodeError, OSError) as e:
        log.error("Erreur lecture %s: %s", DISCORD_CHANNELS_FILE, e)
        return {}


def save_discord_channels_map(channels_map: dict) -> None:
    """Atomic write of discord_channels.json."""
    tmp = f"{DISCORD_CHANNELS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(channels_map, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DISCORD_CHANNELS_FILE)


def get_all_discord_target_channel_ids() -> list[int]:
    """Return deduplicated list of all Discord target channel IDs."""
    ids = [DISCORD_OFFICIAL_CHANNEL_ID]
    ids.extend(load_discord_channels_map().values())
    return list(dict.fromkeys(ids))
