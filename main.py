import discord
from discord.ext import commands, tasks
import requests
import os
import asyncio
import feedparser
import json
import re
from urllib.parse import quote_plus

# === CONFIGURATION DES VARIABLES D'ENVIRONNEMENT ===
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

# Placeholders pour les futures plateformes (à définir)
TWITTER_API_URL = os.environ.get('TWITTER_API_URL', 'https://api.twitter.com/2/tweets')
TWITTER_BEARER_TOKEN = os.environ.get('TWITTER_BEARER_TOKEN')

WHATSAPP_API_URL = os.environ.get('WHATSAPP_API_URL', 'https://graph.facebook.com/v19.0/PHONE_ID/messages')
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN')
WHATSAPP_PHONE_ID = os.environ.get('WHATSAPP_PHONE_ID')

LINKEDIN_ACCESS_TOKEN = os.environ.get('LINKEDIN_ACCESS_TOKEN')
LINKEDIN_PERSON_URN = os.environ.get('LINKEDIN_PERSON_URN')

# --- CONFIGURATION DISCORD ---
try:
    DISCORD_OFFICIAL_CHANNEL_ID = int(os.environ['DISCORD_NEWS_CHANNEL_ID']) 
except KeyError:
    DISCORD_OFFICIAL_CHANNEL_ID = 1330916602425770088 

# --- CONFIGURATION RSS et FICHIERS ---
BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BERGFRID_MEMORY_FILE = "last_article_link.txt"
DISCORD_CHANNELS_FILE = "discord_channels.json"

# --- LIMITES DE CONTENU ---
DISCORD_TEXT_LIMIT = 2000
TELEGRAM_TEXT_LIMIT = 4096
TWITTER_TEXT_LIMIT = 280
THREADS_TEXT_LIMIT = 500
LINKEDIN_TEXT_LIMIT = 1300

# --- DISCORD SETUP ---
intents = discord.Intents.default()
intents.message_content = True 
intents.guilds = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# --- HELPERS : Mémoire et Persistance ---

def read_memory(file_path):
    if not os.path.exists(file_path): return None
    with open(file_path, "r", encoding="utf-8") as f: return f.read().strip()

def write_memory(file_path, value):
    with open(file_path, "w", encoding="utf-8") as f: f.write(str(value))

def load_discord_channels():
    if not os.path.exists(DISCORD_CHANNELS_FILE): return {}
    with open(DISCORD_CHANNELS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_discord_channels(channels_dict):
    with open(DISCORD_CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels_dict, f, indent=4)

# --- LOGIQUE DE CONTENU ---

def determine_importance_and_emoji(summary):
    if "critique" in summary.lower() or "urgent" in summary.lower():
        return "🔥", "Haute"
    return "📰", "Normale"

def truncate_text(text, limit):
    if len(text) > limit:
        return text[:limit-3] + "..."
    return text

# --- FONCTIONS DE PUBLICATION MODULAIRES (Omises pour la concision, elles sont inchangées) ---

async def publish_discord(title, summary, url, tags_str, importance_emoji):
    """Envoie l'article aux canaux Discord."""
    truncated_summary = truncate_text(summary, DISCORD_TEXT_LIMIT) 
    
    embed = discord.Embed(
        title=title,
        url=url,
        description=truncated_summary,
        color=0x000000
    )
    message_content = f"{importance_emoji} **NOUVEL ARTICLE** {tags_str}"

    target_channel_ids = []
    if DISCORD_OFFICIAL_CHANNEL_ID:
         target_channel_ids.append(DISCORD_OFFICIAL_CHANNEL_ID)

    channels_map = load_discord_channels()
    target_channel_ids.extend(list(channels_map.values()))

    for channel_id in set(target_channel_ids):
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(content=message_content, embed=embed)
            except Exception as e:
                print(f"❌ Erreur Discord (Canal ID: {channel_id}): {e}")
        else:
            print(f"⚠️ Canal Discord ID {channel_id} introuvable.")

def publish_telegram(title, summary, url, tags_str, importance_emoji):
    """Envoie l'article à Telegram (synchrone)."""
    truncated_summary = truncate_text(summary, 3000) 
    
    telegram_text = (
        f"{importance_emoji} <b>{title}</b>\n\n"
        f"{truncated_summary}\n\n"
        f"👉 <a href='{url}'>Lire l'article</a>\n\n"
        f"<i>{tags_str}</i>"
    )
    
    telegram_data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": telegram_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=telegram_data)
    except Exception as e:
        print(f"❌ Erreur Telegram: {e}")

def publish_whatsapp(title, summary, url, tags_str, importance_emoji):
    """Envoie l'article à WhatsApp (synchrone) (Placeholder)."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        print("ℹ️ WhatsApp : Non configuré (token ou ID cible manquant).")
        return
    # ... (logique d'envoi WhatsApp simulée) ...
    print("✅ Message WhatsApp simulé envoyé.")

def publish_twitter_threads(title, url, tags_str, importance_emoji, platform_limit):
    """Gère la publication sur Twitter et Threads (Placeholder)."""
    if platform_limit == TWITTER_TEXT_LIMIT and not TWITTER_BEARER_TOKEN:
         print("ℹ️ Twitter : Non configuré (token manquant).")
         return
    # ... (logique d'envoi Twitter/Threads simulée) ...
    print(f"✅ Post {'Twitter' if platform_limit == TWITTER_TEXT_LIMIT else 'Threads'} simulé généré.")

def publish_linkedin(title, summary, url, tags_str, importance_emoji):
    """Envoie l'article à LinkedIn (synchrone) (Placeholder)."""
    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_URN:
        print("ℹ️ LinkedIn : Non configuré (token ou URN manquant).")
        return
    # ... (logique d'envoi LinkedIn simulée) ...
    print("✅ Post LinkedIn simulé généré.")


# --- TÂCHE DE SURVEILLANCE RSS PRINCIPALE (CORRIGÉE) ---

@tasks.loop(minutes=2.0)
async def bergfrid_watcher():
    """Vérifie le flux RSS et publie les nouveaux articles sur toutes les plateformes."""
    
    last_link = read_memory(BERGFRID_MEMORY_FILE)
    
    if last_link is None:
        try:
            feed = feedparser.parse(BERGFRID_RSS_URL)
            if feed.entries:
                # CORRECTION INITIALISATION : Stocker le lien brut lors du démarrage à froid.
                # Cela permet à la correction de se faire à la première boucle suivante.
                last_link = feed.entries[0].link 
                write_memory(BERGFRID_MEMORY_FILE, last_link) 
        except Exception:
            pass
        return 

    # 2. Boucle de surveillance
    try:
        feed = feedparser.parse(BERGFRID_RSS_URL)
        
        if feed.entries:
            latest_entry = feed.entries[0]
            current_link = latest_entry.link
            
            # --- CORRECTION DU LIEN ---
            base_domain = "https://bergfrid.com"
            if "localhost" in current_link or "127.0.0.1" in current_link or current_link.startswith('/'):
                if current_link.startswith('/'):
                    path = current_link
                else:
                    try:
                        path_parts = current_link.split('://', 1)[-1].split('/', 1)
                        path = '/' + path_parts[-1] if len(path_parts) > 1 else ''
                    except Exception:
                        path = ""
                corrected_link = base_domain + path
            else:
                corrected_link = current_link

            url = corrected_link 
            # ---------------------------

            # SI NOUVEAU LIEN DÉTECTÉ (La comparaison utilise le 'url' corrigé)
            if url != last_link: 
                
                # Extraction & Préparation des données
                title = latest_entry.title
                summary = latest_entry.description
                summary = re.sub(r'<[^>]+>', '', summary)
                tags = [f"#{t.term}" for t in latest_entry.tags] if 'tags' in latest_entry else []
                tags_str = " ".join(tags)
                importance_emoji, _ = determine_importance_and_emoji(summary)

                print(f"📣 Nouvelle publication : {title} ({importance_emoji})")

                # --- ENVOI PAR PLATEFORME ---
                await publish_discord(title, summary, url, tags_str, importance_emoji)
                bot.loop.run_in_executor(None, publish_telegram, title, summary, url, tags_str, importance_emoji)
                bot.loop.run_in_executor(None, publish_whatsapp, title, summary, url, tags_str, importance_emoji)
                bot.loop.run_in_executor(None, publish_twitter_threads, title, url, tags_str, importance_emoji, TWITTER_TEXT_LIMIT)
                bot.loop.run_in_executor(None, publish_twitter_threads, title, url, tags_str, importance_emoji, THREADS_TEXT_LIMIT)
                bot.loop.run_in_executor(None, publish_linkedin, title, summary, url, tags_str, importance_emoji)

                # CORRECTION CRUCIALE : On sauvegarde le lien CORRIGÉ (url) pour la prochaine itération
                write_memory(BERGFRID_MEMORY_FILE, url) 
                last_link = url 

    except Exception as e:
        print(f"⚠️ Erreur boucle RSS principale : {e}")


# --- ÉVÉNEMENTS & COMMANDES DISCORD (Omises pour la concision) ---
@bot.event
async def on_ready():
    print(f'✅ Connecté : {bot.user}')
    if not bergfrid_watcher.is_running():
        bergfrid_watcher.start()
        print("🚀 Tâche de surveillance RSS démarrée.")

@bot.command(name="setnews")
@commands.has_permissions(manage_channels=True)
async def set_news_channel(ctx, channel: discord.TextChannel = None):
    channel = ctx.channel if channel is None else channel
    channels_map = load_discord_channels()
    guild_id_str = str(ctx.guild.id)
    channels_map[guild_id_str] = channel.id
    save_discord_channels(channels_map)
    await ctx.send(f"✅ Ce serveur publiera les nouvelles dans le canal {channel.mention}.")

@bot.command(name="unsetnews")
@commands.has_permissions(manage_channels=True)
async def unset_news_channel(ctx):
    channels_map = load_discord_channels()
    guild_id_str = str(ctx.guild.id)
    if guild_id_str in channels_map:
        del channels_map[guild_id_str]
        save_discord_channels(channels_map)
        await ctx.send("❌ Le canal de nouvelles a été retiré pour ce serveur.")
    else:
        await ctx.send("ℹ️ Aucun canal de nouvelles n'était configuré pour ce serveur.")

# --- Démarrage du bot ---
if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
