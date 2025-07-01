import discord
import requests
import re
import os
import asyncio
import feedparser

# === CONFIGURATION ===
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
CHANNEL_IDS = [int(cid.strip()) for cid in os.environ['CHANNEL_ID'].split(',')]
TELEGRAM_MAX_SIZE_MB = 19

# === AJOUT POUR YOUTUBE ===
YOUTUBE_CHANNEL_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id=UCr0nsR_0Uu55P0vB0GdehCQ"
DISCORD_YOUTUBE_CHANNEL_ID = 1389722714813497485
YOUTUBE_VIDEO_MEMORY_FILE = "last_youtube_video.txt"

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

def discord_to_telegram_html(text):
    text = re.sub(r'~~(.*?)~~', r'<s>\1</s>', text)
    text = re.sub(r'__(.*?)__', r'<u>\1</u>', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
    text = re.sub(r'<u><b>(.*?)</b></u>', r'<b><u>\1</u></b>', text)
    return text

def get_last_youtube_video_id():
    if not os.path.exists(YOUTUBE_VIDEO_MEMORY_FILE):
        return None
    with open(YOUTUBE_VIDEO_MEMORY_FILE, "r") as f:
        return f.read().strip()

def set_last_youtube_video_id(video_id):
    with open(YOUTUBE_VIDEO_MEMORY_FILE, "w") as f:
        f.write(video_id)

async def youtube_watcher():
    await client.wait_until_ready()
    last_video_id = get_last_youtube_video_id()
    while not client.is_closed():
        feed = feedparser.parse(YOUTUBE_CHANNEL_RSS)
        if feed.entries:
            latest_entry = feed.entries[0]
            latest_id = latest_entry['yt_videoid']
            if latest_id != last_video_id:
                youtube_url = latest_entry['link']
                title = latest_entry['title']
                channel = client.get_channel(DISCORD_YOUTUBE_CHANNEL_ID)
                if channel:
                    await channel.send(f"Nouvelle vidéo sur la chaîne : **{title}**\n{youtube_url}")
                set_last_youtube_video_id(latest_id)
                last_video_id = latest_id
        await asyncio.sleep(300)  # Vérifie toutes les 5 minutes

@client.event
async def on_ready():
    print(f'✅ Connecté comme {client.user}')
    # Lance la surveillance YouTube
    client.loop.create_task(youtube_watcher())

@client.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.id not in CHANNEL_IDS:
        return

    content = message.content.strip()
    telegram_text = discord_to_telegram_html(content) if content else ""

    # Envoi sur Discord (texte seul)
    if content:
        await message.channel.send(content)

    # Pièces jointes
    for attachment in message.attachments:
        if attachment.size > TELEGRAM_MAX_SIZE_MB * 1024 * 1024:
            await message.channel.send(f"{content}\n⚠️ Fichier trop lourd : {attachment.filename}")
            continue

        file_bytes = requests.get(attachment.url).content
        await message.channel.send(file=await attachment.to_file())

        if attachment.content_type and attachment.content_type.startswith('image'):
            method = 'sendPhoto'
            files = {'photo': (attachment.filename, file_bytes)}
        elif attachment.content_type and attachment.content_type.startswith('video'):
            method = 'sendVideo'
            files = {'video': (attachment.filename, file_bytes)}
        else:
            method = 'sendDocument'
            files = {'document': (attachment.filename, file_bytes)}

        data = {"chat_id": TELEGRAM_CHAT_ID}
        if telegram_text:
            data["caption"] = telegram_text
            data["parse_mode"] = "HTML"
            telegram_text = ""  # éviter répétition

        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
            data=data,
            files=files
        )
        if not r.ok:
            print(f"❌ Erreur Telegram {r.status_code}: {r.text}")

    # Texte seul vers Telegram
    if telegram_text:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": telegram_text,
                "parse_mode": "HTML"
            }
        )
        if not r.ok:
            print(f"❌ Erreur Telegram (texte): {r.status_code} {r.text}")

    # Supprime le message original
    await message.delete()

client.run(DISCORD_TOKEN)
