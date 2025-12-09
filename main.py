import discord
import requests
import re
import os
import asyncio
import feedparser
import json

# === CONFIGURATION ===
# Assure-toi que ces variables sont bien dans ton environnement
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
CHANNEL_IDS = [int(cid.strip()) for cid in os.environ['CHANNEL_ID'].split(',')]
TELEGRAM_MAX_SIZE_MB = 19

# === CONFIGURATION YOUTUBE ===
YOUTUBE_CHANNEL_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id=UCr0nsR_0Uu55P0vB0GdehCQ"
DISCORD_YOUTUBE_CHANNEL_ID = 1389722714813497485
YOUTUBE_VIDEO_MEMORY_FILE = "last_youtube_video.txt"

# === CONFIGURATION BERGFRID (NOUVEAU) ===
BERGFRID_API_URL = "https://bergfrid.com/api/v1/articles?lang=fr-FR"
DISCORD_NEWS_CHANNEL_ID = 1389722714813497485 # METS L'ID DU CANAL DISCORD "ACTUS" ICI
BERGFRID_MEMORY_FILE = "last_article_id.txt"

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# --- HELPERS ---

def discord_to_telegram_html(text):
    text = re.sub(r'~~(.*?)~~', r'<s>\1</s>', text)
    text = re.sub(r'__(.*?)__', r'<u>\1</u>', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
    text = re.sub(r'<u><b>(.*?)</b></u>', r'<b><u>\1</u></b>', text)
    return text

def read_memory(file_path):
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r") as f:
        return f.read().strip()

def write_memory(file_path, value):
    with open(file_path, "w") as f:
        f.write(str(value))

# --- TÂCHE 1 : YOUTUBE ---
async def youtube_watcher():
    await client.wait_until_ready()
    last_video_id = read_memory(YOUTUBE_VIDEO_MEMORY_FILE)
    
    while not client.is_closed():
        try:
            feed = feedparser.parse(YOUTUBE_CHANNEL_RSS)
            if feed.entries:
                latest_entry = feed.entries[0]
                latest_id = latest_entry['yt_videoid']
                
                if latest_id != last_video_id:
                    youtube_url = latest_entry['link']
                    title = latest_entry['title']
                    
                    msg_discord = f"📺 **Nouvelle vidéo :** {title}\n{youtube_url}"
                    msg_telegram = f"📺 <b>Nouvelle vidéo :</b> {title}\n{youtube_url}"
                    
                    # Discord
                    channel = client.get_channel(DISCORD_YOUTUBE_CHANNEL_ID)
                    if channel: await channel.send(msg_discord)
                    
                    # Telegram
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg_telegram, "parse_mode": "HTML"}
                    )
                    
                    write_memory(YOUTUBE_VIDEO_MEMORY_FILE, latest_id)
                    last_video_id = latest_id
        except Exception as e:
            print(f"Erreur YouTube Watcher: {e}")
            
        await asyncio.sleep(300) # 5 minutes

# --- TÂCHE 2 : BERGFRID ARTICLES (NOUVEAU) ---
async def bergfrid_watcher():
    await client.wait_until_ready()
    last_article_id = read_memory(BERGFRID_MEMORY_FILE)
    
    print("🔍 Démarrage surveillance Bergfrid API...")

    while not client.is_closed():
        try:
            # On appelle l'API publique
            r = requests.get(BERGFRID_API_URL, headers={"User-Agent": "BergfridBot/1.0"})
            if r.ok:
                data = r.json()
                articles = data.get('data', [])
                
                if articles:
                    latest_article = articles[0]
                    # On compare les IDs (UUID)
                    if latest_article['id'] != last_article_id:
                        # === C'EST NOUVEAU ===
                        meta = latest_article['meta']
                        title = meta.get('title', 'Nouvel Article')
                        summary = meta.get('summary', '')
                        url = latest_article.get('url', 'https://bergfrid.com')
                        image = meta.get('image')
                        tags = [f"#{t}" for t in meta.get('tags', [])]
                        tags_str = " ".join(tags)
                        
                        # --- 1. DISCORD ---
                        channel = client.get_channel(DISCORD_NEWS_CHANNEL_ID)
                        if channel:
                            embed = discord.Embed(title=title, description=summary, url=url, color=0x000000) # Noir
                            embed.set_author(name="Bergfrid | Dépêche", icon_url="https://bergfrid.com/favicon.svg")
                            if image: embed.set_image(url=image)
                            embed.set_footer(text=f"Publié le {latest_article['published_at'][:10]}")
                            
                            await channel.send(content=f"🚨 **NOUVELLE DÉPÊCHE** {tags_str}", embed=embed)

                        # --- 2. TELEGRAM ---
                        telegram_text = f"🚨 <b>{title}</b>\n\n{summary}\n\n👉 <a href='{url}'>Lire l'article complet</a>\n\n<i>{tags_str}</i>"
                        
                        if image:
                            # Si image, on envoie une photo avec légende
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                                data={"chat_id": TELEGRAM_CHAT_ID, "caption": telegram_text, "parse_mode": "HTML"},
                                files={"photo": requests.get(image).content}
                            )
                        else:
                            # Sinon texte simple
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                data={"chat_id": TELEGRAM_CHAT_ID, "text": telegram_text, "parse_mode": "HTML", "disable_web_page_preview": False}
                            )

                        # Mise à jour mémoire
                        print(f"✅ Article diffusé : {title}")
                        write_memory(BERGFRID_MEMORY_FILE, latest_article['id'])
                        last_article_id = latest_article['id']
            
        except Exception as e:
            print(f"⚠️ Erreur Bergfrid Watcher: {e}")
        
        await asyncio.sleep(120) # Vérifie toutes les 2 minutes pour être réactif

# --- EVENTS ---

@client.event
async def on_ready():
    print(f'✅ Connecté comme {client.user}')
    # On lance les tâches de fond
    client.loop.create_task(youtube_watcher())
    client.loop.create_task(bergfrid_watcher())

@client.event
async def on_message(message):
    # (Ton code existant de relais Discord -> Telegram)
    if message.author.bot: return
    if message.channel.id not in CHANNEL_IDS: return

    content = message.content.strip()
    telegram_text = discord_to_telegram_html(content) if content else ""
    
    # ... (Le reste de ta logique de relais fichiers/images reste inchangé ici) ...
    # Je ne le recopie pas pour alléger, mais garde ton bloc "for attachment in message.attachments"
    
    # Note : Pour éviter le doublon (le bot reposte ce qu'il vient de poster via l'API),
    # tu peux ajouter une condition au début : if message.author.id == client.user.id: return

client.run(DISCORD_TOKEN)
