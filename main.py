import discord
import requests
import os
import asyncio
import feedparser
import json

# === CONFIGURATION ===
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

# ID du canal Discord où publier les news (Mets l'ID de ton canal #actus ici)
# Tu peux aussi le mettre en variable d'env : int(os.environ['DISCORD_NEWS_CHANNEL_ID'])
DISCORD_NEWS_CHANNEL_ID = 1330916602425770088

# RSS du Site
BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BERGFRID_MEMORY_FILE = "last_article_link.txt"

# Discord Setup
intents = discord.Intents.default()
client = discord.Client(intents=intents)

# --- HELPERS ---

def read_memory(file_path):
    if not os.path.exists(file_path): return None
    with open(file_path, "r") as f: return f.read().strip()

def write_memory(file_path, value):
    with open(file_path, "w") as f: f.write(str(value))

# --- TÂCHE DE SURVEILLANCE RSS ---

async def bergfrid_watcher():
    await client.wait_until_ready()
    
    # 1. Initialisation (Render)
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

    print("🚀 Bot opérationnel (Mode Simplifié)")

    # 2. Boucle de surveillance
    while not client.is_closed():
        try:
            feed = feedparser.parse(BERGFRID_RSS_URL)
            
            if feed.entries:
                latest_entry = feed.entries[0]
                current_link = latest_entry.link
                
                # SI NOUVEAU LIEN DÉTECTÉ
                if current_link != last_link and last_link is not None:
                    
                    # Extraction des données
                    title = latest_entry.title
                    summary = latest_entry.description
                    # Nettoyage sommaire du HTML dans le résumé
                    summary = summary.replace("<p>", "").replace("</p>", "").replace("<br>", "\n")
                    
                    url = current_link
                    
                    # Gestion des tags pour l'affichage
                    tags = []
                    if 'tags' in latest_entry:
                        tags = [f"#{t.term}" for t in latest_entry.tags]
                    tags_str = " ".join(tags)

                    print(f"📣 Nouvelle publication : {title}")

                    # --- A. DISCORD (SIMPLE) ---
                    channel = client.get_channel(DISCORD_NEWS_CHANNEL_ID)
                    if channel:
                        # On crée un encadré noir simple : Titre + Lien + Résumé
                        embed = discord.Embed(
                            title=title, 
                            url=url, 
                            description=summary, 
                            color=0x000000 # Noir
                        )
                        # Pas d'image, pas d'auteur, juste l'info.
                        
                        try:
                            await channel.send(content=f"🚨 **DÉPÊCHE** {tags_str}", embed=embed)
                        except Exception as e:
                            print(f"Erreur Discord: {e}")

                    # --- B. TELEGRAM (TEXTE PUR) ---
                    # Format : Titre (Gras) / Résumé / Lien
                    telegram_text = f"🚨 <b>{title}</b>\n\n{summary}\n\n👉 <a href='{url}'>Lire l'article</a>\n\n<i>{tags_str}</i>"
                    
                    telegram_data = {
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": telegram_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False # On laisse l'aperçu du lien généré par Telegram
                    }

                    try:
                        # On utilise sendMessage uniquement (plus léger que sendPhoto)
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=telegram_data)
                    except Exception as e:
                        print(f"Erreur Telegram: {e}")

                    # Mise à jour mémoire
                    write_memory(BERGFRID_MEMORY_FILE, current_link)
                    last_link = current_link
            
        except Exception as e:
            print(f"⚠️ Erreur boucle RSS : {e}")
        
        await asyncio.sleep(120) # Pause de 2 minutes

@client.event
async def on_ready():
    print(f'✅ Connecté : {client.user}')
    client.loop.create_task(bergfrid_watcher())

client.run(DISCORD_TOKEN)
