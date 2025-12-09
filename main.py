import discord
from discord.ext import commands, tasks
import requests
import os
import asyncio
import feedparser
import json
import re
from urllib.parse import quote_plus # Pour encoder les URL dans les posts

# === CONFIGURATION DES VARIABLES D'ENVIRONNEMENT ===
# Les tokens et IDs DOIVENT être définis dans votre environnement.
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

# Placeholders pour les futures plateformes (à définir)
TWITTER_API_URL = os.environ.get('TWITTER_API_URL', 'https://api.twitter.com/2/tweets')
TWITTER_BEARER_TOKEN = os.environ.get('TWITTER_BEARER_TOKEN')

WHATSAPP_API_URL = os.environ.get('WHATSAPP_API_URL', 'https://graph.facebook.com/v19.0/PHONE_ID/messages')
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN')
WHATSAPP_PHONE_ID = os.environ.get('WHATSAPP_PHONE_ID') # Numéro cible ou ID du canal

LINKEDIN_ACCESS_TOKEN = os.environ.get('LINKEDIN_ACCESS_TOKEN')
LINKEDIN_PERSON_URN = os.environ.get('LINKEDIN_PERSON_URN')

# --- CONFIGURATION DISCORD ---
try:
    # ID du canal officiel (doit être un entier)
    DISCORD_OFFICIAL_CHANNEL_ID = int(os.environ['DISCORD_NEWS_CHANNEL_ID']) 
except KeyError:
    # Fallback pour le développement local si la variable n'est pas définie
    DISCORD_OFFICIAL_CHANNEL_ID = 1330916602425770088 

# --- CONFIGURATION RSS et FICHIERS ---
BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BERGFRID_MEMORY_FILE = "last_article_link.txt"
DISCORD_CHANNELS_FILE = "discord_channels.json" # Serveur ID -> Canal ID

# --- LIMITES DE CONTENU ---
# Limites recommandées pour éviter les erreurs
DISCORD_TEXT_LIMIT = 2000 # Description d'embed
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
    """Détermine l'importance du contenu pour choisir un émoji."""
    if "critique" in summary.lower() or "urgent" in summary.lower():
        return "🔥", "Haute"
    return "📰", "Normale"

def truncate_text(text, limit):
    """Tronque le texte pour respecter la limite."""
    if len(text) > limit:
        return text[:limit-3] + "..."
    return text

# --- FONCTIONS DE PUBLICATION MODULAIRES ---

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

    # WhatsApp est souvent limité aux templates. Ceci est un message texte simple.
    whatsapp_text = (
        f"{importance_emoji} *{title}*\n\n"
        f"{truncate_text(summary, 300)}\n\n"
        f"🔗 {url}"
    )
    
    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type': 'application/json'
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": WHATSAPP_PHONE_ID,
        "type": "text",
        "text": {"body": whatsapp_text}
    }
    
    try:
        requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
        print("✅ Message WhatsApp simulé envoyé.")
    except Exception as e:
        print(f"❌ Erreur WhatsApp: {e}")

def publish_twitter_threads(title, url, tags_str, importance_emoji, platform_limit):
    """Gère la publication sur Twitter et Threads (très similaires, synchrones)."""
    if platform_limit == TWITTER_TEXT_LIMIT:
        print("ℹ️ Twitter : Non configuré (token manquant).")
        return
    if platform_limit == THREADS_TEXT_LIMIT:
        print("ℹ️ Threads : Non configuré (token manquant).")
        return
        
    # Format simple : Titre + Tags + Lien (le plus important pour respecter la limite)
    post_content = f"{importance_emoji} {title} {tags_str} | Lire: {url}"
    post_content = truncate_text(post_content, platform_limit)

    # --- SIMULATION D'ENVOI ---
    # Ici, vous auriez besoin des vrais clients et tokens d'API pour Twitter/Threads
    print(f"✅ Post {platform_limit}-caractères généré : {post_content}")
    # Simuler un appel API réussi
    

def publish_linkedin(title, summary, url, tags_str, importance_emoji):
    """Envoie l'article à LinkedIn (synchrone) (Placeholder)."""
    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_URN:
        print("ℹ️ LinkedIn : Non configuré (token ou URN manquant).")
        return

    # Format professionnel : Titre, Résumé court, Lien
    post_content = (
        f"{importance_emoji} {title}\n\n"
        f"{truncate_text(summary, LINKEDIN_TEXT_LIMIT - 100)}\n\n"
        f"{tags_str}\n\n"
        f"{url}"
    )
    
    # --- SIMULATION D'ENVOI ---
    # L'API LinkedIn est complexe (registerUpload, création de post)
    print(f"✅ Post LinkedIn simulé généré : {truncate_text(post_content, LINKEDIN_TEXT_LIMIT)}")

# --- TÂCHE DE SURVEILLANCE RSS PRINCIPALE ---

@tasks.loop(minutes=2.0)
async def bergfrid_watcher():
    """Vérifie le flux RSS et publie les nouveaux articles sur toutes les plateformes."""
    
    last_link = read_memory(BERGFRID_MEMORY_FILE)
    
    if last_link is None:
        # 1. Initialisation (Lecture et écriture du dernier lien)
        try:
            feed = feedparser.parse(BERGFRID_RSS_URL)
            if feed.entries:
                last_link = feed.entries[0].link
                write_memory(BERGFRID_MEMORY_FILE, last_link)
        except Exception:
            pass # Ne pas bloquer l'initialisation pour les erreurs RSS

        return 

    # 2. Boucle de surveillance
    try:
        feed = feedparser.parse(BERGFRID_RSS_URL)
        
        if feed.entries:
            latest_entry = feed.entries[0]
            current_link = latest_entry.link
            
            # --- CORRECTION DU LIEN (Stratégie de substitution agressive) ---
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
            # -----------------------------------------------------------------

            # SI NOUVEAU LIEN DÉTECTÉ
            if url != last_link:
                
                # Extraction & Préparation des données
                title = latest_entry.title
                summary = latest_entry.description
                summary = re.sub(r'<[^>]+>', '', summary) # Nettoyage HTML
                tags = [f"#{t.term}" for t in latest_entry.tags] if 'tags' in latest_entry else []
                tags_str = " ".join(tags)
                importance_emoji, _ = determine_importance_and_emoji(summary)

                print(f"📣 Nouvelle publication : {title} ({importance_emoji})")

                # --- ENVOI PAR PLATEFORME ---
                
                # A. Discord (Asynchrone)
                await publish_discord(title, summary, url, tags_str, importance_emoji)

                # B. Telegram (Synchrone, exécuté dans un thread pour ne pas bloquer)
                bot.loop.run_in_executor(None, publish_telegram, title, summary, url, tags_str, importance_emoji)

                # C. WhatsApp (Synchrone, exécuté dans un thread)
                bot.loop.run_in_executor(None, publish_whatsapp, title, summary, url, tags_str, importance_emoji)

                # D. Twitter (X) & Threads (Synchrone, exécuté dans un thread)
                bot.loop.run_in_executor(None, publish_twitter_threads, title, url, tags_str, importance_emoji, TWITTER_TEXT_LIMIT)
                bot.loop.run_in_executor(None, publish_twitter_threads, title, url, tags_str, importance_emoji, THREADS_TEXT_LIMIT)
                
                # E. LinkedIn (Synchrone, exécuté dans un thread)
                bot.loop.run_in_executor(None, publish_linkedin, title, summary, url, tags_str, importance_emoji)

                # Mise à jour mémoire
                write_memory(BERGFRID_MEMORY_FILE, current_link) 
                last_link = current_link 

    except Exception as e:
        print(f"⚠️ Erreur boucle RSS principale : {e}")


# --- ÉVÉNEMENTS & COMMANDES DISCORD ---

@bot.event
async def on_ready():
    """Se déclenche quand le bot est prêt."""
    print(f'✅ Connecté : {bot.user}')
    if not bergfrid_watcher.is_running():
        bergfrid_watcher.start()
        print("🚀 Tâche de surveillance RSS démarrée.")

@bot.command(name="setnews")
@commands.has_permissions(manage_channels=True)
async def set_news_channel(ctx, channel: discord.TextChannel = None):
    """Définit le canal de news pour ce serveur. Usage : !setnews [\#canal]"""
    channel = ctx.channel if channel is None else channel
    channels_map = load_discord_channels()
    guild_id_str = str(ctx.guild.id)
    
    channels_map[guild_id_str] = channel.id
    save_discord_channels(channels_map)
    
    await ctx.send(f"✅ Ce serveur publiera les nouvelles dans le canal {channel.mention}.")

@bot.command(name="unsetnews")
@commands.has_permissions(manage_channels=True)
async def unset_news_channel(ctx):
    """Retire l'enregistrement du canal de news. Usage : !unsetnews"""
    channels_map = load_discord_channels()
    guild_id_str = str(ctx.guild.id)
    
    if guild_id_str in channels_map:
        del channels_map[guild_id_str]
        save_discord_channels(channels_map)
        await ctx.send("❌ Le canal de nouvelles a été retiré pour ce serveur.")
    else:
        await ctx.send("ℹ️ Aucun canal de nouvelles n'était configuré pour ce serveur.")

# --- Démarrage du bot ---
# Utilisez bot.run(DISCORD_TOKEN)
if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
