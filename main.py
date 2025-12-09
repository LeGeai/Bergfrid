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

# Config API Site
BERGFRID_RSS_URL = "https://bergfrid.com/rss.xml"
BERGFRID_MEMORY_FILE = "last_article_id.txt"

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

    print("🚀 Bot opérationnel (Mode RSS)")

    # 2. Boucle
    while not client.is_closed():
        try:
            feed = feedparser.parse(BERGFRID_RSS_URL)
            
            if feed.entries:
                latest_entry = feed.entries[0]
                current_link = latest_entry.link
                
                # SI NOUVEAU LIEN
                if current_link != last_link and last_link is not None:
                    
                    # Extraction des données RSS
                    title = latest_entry.title
                    summary = latest_entry.description
                    # Nettoyage sommaire du résumé (enlève les balises HTML si besoin)
                    summary = summary.replace("<p>", "").replace("</p>", "")
                    
                    url = current_link
                    date_str = latest_entry.published
                    
                    # Gestion de l'image (Media RSS)
                    image = None
                    if 'media_content' in latest_entry:
                        image = latest_entry.media_content[0]['url']
                    
                    # Gestion des tags (Category)
                    tags = []
                    if 'tags' in latest_entry:
                        tags = [f"#{t.term}" for t in latest_entry.tags]
                    tags_str = " ".join(tags)

                    print(f"📣 Nouvelle publication : {title}")

                    # --- DISCORD ---
                    channel = client.get_channel(DISCORD_NEWS_CHANNEL_ID)
                    if channel:
                        embed = discord.Embed(title=title, description=summary, url=url, color=0x000000)
                        embed.set_author(name="Bergfrid | Dépêche", icon_url="https://bergfrid.com/favicon.svg")
                        if image: embed.set_image(url=image)
                        embed.set_footer(text=f"Publié le {date_str}")
                        
                        try:
                            await channel.send(content=f"🚨 **NOUVELLE DÉPÊCHE** {tags_str}", embed=embed)
                        except Exception as e:
                            print(f"Erreur Discord: {e}")

                    # --- TELEGRAM ---
                    telegram_text = f"🚨 <b>{title}</b>\n\n{summary}\n\n👉 <a href='{url}'>Lire l'article</a>\n\n<i>{tags_str}</i>"
                    telegram_data = {"chat_id": TELEGRAM_CHAT_ID, "parse_mode": "HTML"}

                    try:
                        if image:
                            telegram_data["caption"] = telegram_text
                            # On télécharge l'image
                            img_r = requests.get(image)
                            if img_r.ok:
                                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=telegram_data, files={"photo": img_r.content})
                            else:
                                telegram_data["text"] = telegram_text
                                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=telegram_data)
                        else:
                            telegram_data["text"] = telegram_text
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=telegram_data)
                    except Exception as e:
                        print(f"Erreur Telegram: {e}")

                    # Mise à jour mémoire
                    write_memory(BERGFRID_MEMORY_FILE, current_link)
                    last_link = current_link
            
        except Exception as e:
            print(f"⚠️ Erreur boucle RSS : {e}")
        
        await asyncio.sleep(120)

@client.event
async def on_ready():
    print(f'✅ Connecté : {client.user}')
    client.loop.create_task(bergfrid_watcher())

client.run(DISCORD_TOKEN)
