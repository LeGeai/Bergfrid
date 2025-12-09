import discord
from discord.ext import commands, tasks
import requests
import os
import asyncio
import feedparser
import json
import re

# === CONFIGURATION ESSENTIELLE ===
# Les tokens et IDs DOIVENT être définis dans votre environnement.
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

# --- CONFIGURATION DISCORD ---
try:
    # ID du canal officiel (doit être un entier)
    DISCORD_OFFICIAL_CHANNEL_ID = int(os.environ['DISCORD_NEWS_CHANNEL_ID']) 
except KeyError:
    DISCORD_OFFICIAL_CHANNEL_ID = 1330916602425770088 

# --- CONFIGURATION RSS et FICHIERS ---
BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BERGFRID_MEMORY_FILE = "last_article_link.txt"
DISCORD_CHANNELS_FILE = "discord_channels.json" # Pour les serveurs secondaires

# --- DISCORD SETUP ---
intents = discord.Intents.default()
intents.message_content = True 
intents.guilds = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# --- HELPERS : Mémoire et Persistance ---

def read_memory(file_path):
    """Lit la dernière valeur depuis le fichier mémoire."""
    if not os.path.exists(file_path): return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            # On nettoie les espaces/sauts de ligne et s'assure qu'il y a du contenu
            content = f.read().strip()
            return content if content else None
    except Exception as e:
        print(f"❌ Erreur lecture mémoire: {e}")
        return None

def write_memory(file_path, value):
    """Écrit la valeur dans le fichier mémoire."""
    with open(file_path, "w", encoding="utf-8") as f: f.write(str(value))

def load_discord_channels():
    """Charge les IDs de canaux enregistrés (Serveur ID -> Canal ID)."""
    if not os.path.exists(DISCORD_CHANNELS_FILE): return {}
    with open(DISCORD_CHANNELS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_discord_channels(channels_dict):
    """Sauvegarde les IDs de canaux enregistrés."""
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
    # Limite Discord pour description d'embed : 4096 (on utilise 2000 par sécurité)
    if len(text) > limit:
        return text[:limit-3] + "..."
    return text

# --- FONCTIONS DE PUBLICATION ---

async def publish_discord(title, summary, url, tags_str, importance_emoji):
    """Envoie l'article aux canaux Discord."""
    truncated_summary = truncate_text(summary, 2000) 
    
    embed = discord.Embed(
        title=title,
        url=url,
        description=truncated_summary,
        color=0x000000
    )
    message_content = f"{importance_emoji} **NOUVEL ARTICLE** {tags_str}"

    target_channel_ids = [DISCORD_OFFICIAL_CHANNEL_ID]
    
    channels_map = load_discord_channels()
    target_channel_ids.extend(list(channels_map.values()))

    for channel_id in set(target_channel_ids):
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(content=message_content, embed=embed)
            except Exception as e:
                print(f"❌ Erreur Discord (Canal ID: {channel_id}): {e}")

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

# --- TÂCHE DE SURVEILLANCE RSS PRINCIPALE (CORRIGÉE) ---

@tasks.loop(minutes=2.0)
async def bergfrid_watcher():
    """Vérifie le flux RSS et publie les nouveaux articles."""
    
    last_link = read_memory(BERGFRID_MEMORY_FILE)
    
    # 1. Initialisation (Si aucune mémoire trouvée)
    if last_link is None:
        print("⚠️ Démarrage à froid : Synchronisation RSS...")
        try:
            feed = feedparser.parse(BERGFRID_RSS_URL)
            if feed.entries:
                # IMPORTANT : Stocker le lien brut au démarrage à froid
                last_link = feed.entries[0].link 
                write_memory(BERGFRID_MEMORY_FILE, last_link)
                print(f"✅ Synchronisé sur : {last_link}")
        except Exception as e:
            print(f"❌ Erreur init RSS : {e}")
        return # Attendre la prochaine itération pour la surveillance

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

            # SI NOUVEAU LIEN DÉTECTÉ OU SI LE LIEN CORRIGÉ EST DIFFÉRENT DE LA MÉMOIRE BRUTE
            # Ceci devrait empêcher la boucle infinie si le lien en mémoire est encore l'ancien brute.
            if url != last_link: 
                
                # --- ÉVITER LE BUG DE BOUCLE INFINIE ---
                # Si le dernier lien enregistré n'est PAS un lien corrigé,
                # mais que le lien corrigé actuel correspond, ne pas publier
                # et mettre à jour la mémoire avec le lien corrigé.
                if current_link == last_link and url != last_link:
                    print("ℹ️ Réparation de la mémoire : Mise à jour du lien brut en corrigé.")
                    write_memory(BERGFRID_MEMORY_FILE, url) 
                    return
                # ----------------------------------------
                
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
                
                # Exécution synchrone dans un thread
                bot.loop.run_in_executor(None, publish_telegram, title, summary, url, tags_str, importance_emoji)
                
                # CORRECTION : On sauvegarde le lien CORRIGÉ (url)
                write_memory(BERGFRID_MEMORY_FILE, url) 
                last_link = url 

    except Exception as e:
        print(f"⚠️ Erreur boucle RSS principale : {e}")


# --- ÉVÉNEMENTS & COMMANDES DISCORD ---

@bot.event
async def on_ready():
    print(f'✅ Connecté : {bot.user}')
    if not bergfrid_watcher.is_running():
        bergfrid_watcher.start()
        print("🚀 Tâche de surveillance RSS démarrée (Mode Simple).")

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
if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
