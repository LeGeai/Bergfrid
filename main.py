import discord
import requests
import os
import asyncio
import json

# === CONFIGURATION ===
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

# ID du canal Discord où publier les news (Mets l'ID de ton canal #actus ici)
# Tu peux aussi le mettre en variable d'env : int(os.environ['DISCORD_NEWS_CHANNEL_ID'])
DISCORD_NEWS_CHANNEL_ID = 1330916602425770088

# Config API Site
BERGFRID_API_URL = "https://bergfrid.com/api/v1/articles?lang=fr-FR"
BERGFRID_MEMORY_FILE = "last_article_id.txt"

# Configuration Discord Minima
intents = discord.Intents.default()
client = discord.Client(intents=intents)

# --- FONCTIONS UTILITAIRES ---

def read_memory(file_path):
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r") as f:
        return f.read().strip()

def write_memory(file_path, value):
    with open(file_path, "w") as f:
        f.write(str(value))

# --- TÂCHE DE SURVEILLANCE ---

async def bergfrid_watcher():
    await client.wait_until_ready()
    
    # 1. Initialisation Intelligente (Spécial Render)
    # Si le fichier mémoire est vide (ce qui arrive à chaque déploiement Render),
    # on récupère le dernier ID en ligne MAIS on ne le poste pas.
    # On le stocke juste pour dire "C'est bon, je suis à jour, je surveille les suivants".
    last_article_id = read_memory(BERGFRID_MEMORY_FILE)
    
    if last_article_id is None:
        print("⚠️ Démarrage à froid (Render) : Synchronisation de l'état initial...")
        try:
            r = requests.get(BERGFRID_API_URL, headers={"User-Agent": "BergfridBot/1.0"})
            if r.ok:
                data = r.json()
                # Si des articles existent, on prend le plus récent comme point de départ
                if data.get('data') and len(data['data']) > 0:
                    last_article_id = data['data'][0]['id']
                    write_memory(BERGFRID_MEMORY_FILE, last_article_id)
                    print(f"✅ Synchronisé sur le dernier article : {last_article_id}")
                else:
                    print("ℹ️ Aucun article trouvé sur le site pour l'instant.")
        except Exception as e:
            print(f"❌ Erreur critique lors de l'initialisation : {e}")

    print("🚀 Bot opérationnel : En attente de nouveaux articles...")

    # 2. Boucle Infinie
    while not client.is_closed():
        try:
            r = requests.get(BERGFRID_API_URL, headers={"User-Agent": "BergfridBot/1.0"})
            
            if r.ok:
                data = r.json()
                articles = data.get('data', [])
                
                if articles:
                    latest_article = articles[0]
                    current_id = latest_article['id']
                    
                    # SI C'EST UN NOUVEL ID
                    if current_id != last_article_id and last_article_id is not None:
                        
                        # Récupération des infos
                        meta = latest_article['meta']
                        title = meta.get('title', 'Nouvel Article')
                        summary = meta.get('summary', 'Lire la suite sur le site...')
                        url = latest_article.get('url', 'https://bergfrid.com')
                        image = meta.get('image') # URL absolue
                        tags = [f"#{t}" for t in meta.get('tags', [])]
                        tags_str = " ".join(tags)
                        date_str = latest_article.get('published_at', '')[:10]

                        print(f"📣 Nouvelle publication détectée : {title}")

                        # --- A. ENVOI DISCORD ---
                        channel = client.get_channel(DISCORD_NEWS_CHANNEL_ID)
                        if channel:
                            embed = discord.Embed(
                                title=title, 
                                description=summary, 
                                url=url, 
                                color=0x000000 # Couleur Noire (Style Bergfrid)
                            )
                            embed.set_author(name="Bergfrid | Dépêche", icon_url="https://bergfrid.com/favicon.svg")
                            if image: 
                                embed.set_image(url=image)
                            embed.set_footer(text=f"Publié le {date_str}")
                            
                            try:
                                await channel.send(content=f"🚨 **NOUVELLE DÉPÊCHE** {tags_str}", embed=embed)
                            except Exception as e:
                                print(f"Erreur envoi Discord: {e}")

                        # --- B. ENVOI TELEGRAM ---
                        # Construction du message HTML
                        telegram_text = f"🚨 <b>{title}</b>\n\n{summary}\n\n👉 <a href='{url}'>Lire l'article complet</a>\n\n<i>{tags_str}</i>"
                        
                        telegram_data = {
                            "chat_id": TELEGRAM_CHAT_ID,
                            "parse_mode": "HTML"
                        }

                        try:
                            if image:
                                # Si image, on utilise sendPhoto
                                telegram_data["caption"] = telegram_text
                                # On télécharge l'image temporairement pour l'envoyer à Telegram
                                img_r = requests.get(image)
                                if img_r.ok:
                                    requests.post(
                                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                                        data=telegram_data,
                                        files={"photo": img_r.content}
                                    )
                                else:
                                    # Fallback si l'image ne charge pas
                                    telegram_data["text"] = telegram_text
                                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=telegram_data)
                            else:
                                # Sinon message texte simple
                                telegram_data["text"] = telegram_text
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                    data=telegram_data
                                )
                        except Exception as e:
                            print(f"Erreur envoi Telegram: {e}")

                        # Mise à jour de la mémoire
                        write_memory(BERGFRID_MEMORY_FILE, current_id)
                        last_article_id = current_id
            
            else:
                print(f"⚠️ API Site inaccessible ({r.status_code})")

        except Exception as e:
            print(f"⚠️ Erreur boucle de surveillance : {e}")
        
        # Pause de 2 minutes
        await asyncio.sleep(120)

@client.event
async def on_ready():
    print(f'✅ Connecté à Discord en tant que {client.user}')
    # Lancement de la tâche unique
    client.loop.create_task(bergfrid_watcher())

client.run(DISCORD_TOKEN)
