import discord
from discord.ext import commands, tasks
import requests
import os
import asyncio
import feedparser
import json
import re # Pour le nettoyage de texte HTML

# === CONFIGURATION ===
# Les tokens et IDs sont récupérés des variables d'environnement.
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

# Canal Discord "Officiel" (Canal par défaut qui ne changera jamais)
# On le garde en dur OU on le met dans une variable d'environnement pour plus de flexibilité.
# J'ai choisi de le garder en variable d'environnement pour coller à la pratique standard.
try:
    # L'ID DOIT être un entier
    DISCORD_OFFICIAL_CHANNEL_ID = int(os.environ['DISCORD_NEWS_CHANNEL_ID']) 
except KeyError:
    # Si la variable n'est pas trouvée (pour le test), on met une valeur par défaut.
    # Dans un environnement de production, cette ligne devrait générer une erreur.
    DISCORD_OFFICIAL_CHANNEL_ID = 1330916602425770088 

# Fichiers de persistance
BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BERGFRID_MEMORY_FILE = "last_article_link.txt"
DISCORD_CHANNELS_FILE = "discord_channels.json" # Pour les serveurs supplémentaires

# Limites de caractères pour les messages
DISCORD_TEXT_LIMIT = 4000
TELEGRAM_TEXT_LIMIT = 4096 # Limite réelle est de 4096 octets

# Discord Setup (Utilisation de commands.Bot pour les commandes)
intents = discord.Intents.default()
# Nécessaire pour les commandes sur les serveurs
intents.message_content = True 
intents.guilds = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# --- HELPERS : Mémoire et Persistance ---

def read_memory(file_path):
    """Lit une valeur simple depuis un fichier."""
    if not os.path.exists(file_path): return None
    with open(file_path, "r", encoding="utf-8") as f: return f.read().strip()

def write_memory(file_path, value):
    """Écrit une valeur simple dans un fichier."""
    with open(file_path, "w", encoding="utf-8") as f: f.write(str(value))

def load_discord_channels():
    """Charge les IDs de canaux enregistrés (Serveur ID -> Canal ID)."""
    if not os.path.exists(DISCORD_CHANNELS_FILE): return {}
    with open(DISCORD_CHANNELS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {} # Retourne vide en cas de fichier corrompu

def save_discord_channels(channels_dict):
    """Sauvegarde les IDs de canaux enregistrés."""
    with open(DISCORD_CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels_dict, f, indent=4)

# --- NOUVELLE LOGIQUE : Gestion de Contenu ---

def determine_importance_and_emoji(summary):
    """
    Détermine l'importance du contenu pour choisir un émoji.
    C'est ici qu'une logique d'analyse de texte plus complexe serait ajoutée.
    Pour l'instant, c'est une implémentation simple/par défaut.
    """
    # Règle simple : Si le résumé contient "critique" ou "urgent", importance élevée.
    if "critique" in summary.lower() or "urgent" in summary.lower():
        return "🔥", "Haute"
    return "📰", "Normale"

def truncate_text(text, limit):
    """Tronque le texte pour respecter la limite Discord/Telegram."""
    if len(text) > limit:
        return text[:limit-3] + "..."
    return text

# --- TÂCHES D'ENVOI ---

async def publish_discord(title, summary, url, tags_str, importance_emoji, is_official=False):
    """Envoie l'article à tous les canaux Discord enregistrés et au canal officiel."""
    
    # 1. Préparation du contenu
    # Tronquer le résumé pour l'embed (max 4096 caractères pour la description, mais on est prudent)
    truncated_summary = truncate_text(summary, 2000) 
    
    # Création de l'Embed
    embed = discord.Embed(
        title=title,
        url=url,
        description=truncated_summary,
        color=0x000000 # Noir
    )
    
    # Contenu du message (au-dessus de l'embed)
    message_content = f"{importance_emoji} **NOUVEL ARTICLE** {tags_str}"

    # 2. Canaux Cibles
    target_channel_ids = []
    
    # Canal Officiel (toujours inclus)
    if DISCORD_OFFICIAL_CHANNEL_ID:
         target_channel_ids.append(DISCORD_OFFICIAL_CHANNEL_ID)

    # Canaux Supplémentaires (chargés depuis le fichier)
    if not is_official:
        # On ne charge la liste que si on en a besoin
        channels_map = load_discord_channels()
        target_channel_ids.extend(list(channels_map.values()))

    # 3. Envoi
    for channel_id in set(target_channel_ids): # Utiliser un set pour éviter les doublons
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(content=message_content, embed=embed)
            except Exception as e:
                print(f"Erreur Discord (Canal ID: {channel_id}): {e}")
        else:
            print(f"⚠️ Canal Discord ID {channel_id} introuvable.")


def publish_telegram(title, summary, url, tags_str, importance_emoji):
    """Envoie l'article à Telegram (synchrone car requests)."""
    
    # 1. Préparation du contenu
    # Le résumé doit être nettoyé du HTML (déjà fait dans bergfrid_watcher)
    # On tronque pour être sûr
    truncated_summary = truncate_text(summary, 3000) 
    
    # Format : Émoji/Titre (Gras) / Résumé / Lien / Tags (Italique)
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

    # 2. Envoi
    try:
        # On utilise sendMessage uniquement
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=telegram_data)
    except Exception as e:
        print(f"Erreur Telegram: {e}")

# --- TÂCHE DE SURVEILLANCE RSS (Modifiée) ---

@tasks.loop(minutes=2.0)
async def bergfrid_watcher():
    """Tâche périodique de vérification du flux RSS et de publication des nouveaux articles."""
    
    # 1. Initialisation (Lecture de la dernière publication)
    last_link = read_memory(BERGFRID_MEMORY_FILE)
    
    if last_link is None:
        print("⚠️ Démarrage à froid : Synchronisation RSS...")
        try:
            feed = feedparser.parse(BERGFRID_RSS_URL)
            if feed.entries:
                last_link = feed.entries[0].link
                write_memory(BERGFRID_MEMORY_FILE, last_link)
                print(f"✅ Synchronisé sur : {last_link}")
        except Exception as e:
            print(f"❌ Erreur init RSS : {e}")
        return # Fin de l'initialisation, on attend la prochaine boucle

    # 2. Boucle de surveillance (le corps de la tâche)
    try:
        feed = feedparser.parse(BERGFRID_RSS_URL)
        
        if feed.entries:
            latest_entry = feed.entries[0]
            current_link = latest_entry.link
            
            # SI NOUVEAU LIEN DÉTECTÉ
            if current_link != last_link:
                
                # Extraction des données
                title = latest_entry.title
                summary = latest_entry.description
                # Nettoyage sommaire du HTML
                summary = re.sub(r'<[^>]+>', '', summary) # Enlève la plupart des tags HTML
                
                url = current_link
                
                # Gestion des tags
                tags = [f"#{t.term}" for t in latest_entry.tags] if 'tags' in latest_entry else []
                tags_str = " ".join(tags)

                # NOUVEAU : Détermination de l'importance et de l'émoji
                importance_emoji, _ = determine_importance_and_emoji(summary)

                print(f"📣 Nouvelle publication : {title} ({importance_emoji})")

                # --- A. DISCORD (Utilisation de la fonction modulaire) ---
                await publish_discord(title, summary, url, tags_str, importance_emoji)

                # --- B. TELEGRAM (Utilisation de la fonction modulaire) ---
                # Comme requests.post est synchrone, on utilise bot.loop.run_in_executor
                # pour ne pas bloquer le bot Discord.
                await bot.loop.run_in_executor(None, publish_telegram, title, summary, url, tags_str, importance_emoji)

                # Mise à jour mémoire
                write_memory(BERGFRID_MEMORY_FILE, current_link)
                # Mise à jour du lien pour la prochaine itération
                read_memory(BERGFRID_MEMORY_FILE) 

    except Exception as e:
        print(f"⚠️ Erreur boucle RSS : {e}")

# --- ÉVÉNEMENTS DISCORD ---

@bot.event
async def on_ready():
    """Se déclenche quand le bot est prêt."""
    print(f'✅ Connecté : {bot.user} (ID: {bot.user.id})')
    
    # Démarrage de la tâche de surveillance
    if not bergfrid_watcher.is_running():
        bergfrid_watcher.start()
        print("🚀 Tâche de surveillance RSS démarrée.")

# --- COMMANDES DISCORD ---

@bot.command(name="setnews")
@commands.has_permissions(manage_channels=True)
async def set_news_channel(ctx, channel: discord.TextChannel = None):
    """
    Définit le canal actuel ou spécifié comme canal de news pour ce serveur.
    Usage : !setnews [\#canal]
    """
    if channel is None:
        channel = ctx.channel # Utilise le canal où la commande est tapée

    channels_map = load_discord_channels()
    guild_id_str = str(ctx.guild.id)
    
    channels_map[guild_id_str] = channel.id
    save_discord_channels(channels_map)
    
    await ctx.send(f"✅ Ce serveur publiera les nouvelles dans le canal {channel.mention}.")

@bot.command(name="unsetnews")
@commands.has_permissions(manage_channels=True)
async def unset_news_channel(ctx):
    """
    Retire l'enregistrement du canal de news pour ce serveur.
    Usage : !unsetnews
    """
    channels_map = load_discord_channels()
    guild_id_str = str(ctx.guild.id)
    
    if guild_id_str in channels_map:
        del channels_map[guild_id_str]
        save_discord_channels(channels_map)
        await ctx.send("❌ Le canal de nouvelles a été retiré pour ce serveur.")
    else:
        await ctx.send("ℹ️ Aucun canal de nouvelles n'était configuré pour ce serveur.")

# --- Démarrage du bot ---
# Utilisez bot.run(DISCORD_TOKEN) car nous utilisons commands.Bot
bot.run(DISCORD_TOKEN)
