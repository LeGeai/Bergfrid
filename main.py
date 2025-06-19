import discord
import requests
import re
import os

# === CONFIGURATION ===
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
CHANNEL_IDS = [int(cid.strip()) for cid in os.environ['CHANNEL_ID'].split(',')]
TELEGRAM_MAX_SIZE_MB = 19

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

@client.event
async def on_ready():
    print(f'✅ Connecté comme {client.user}')

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
