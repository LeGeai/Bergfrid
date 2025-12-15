# Bergfrid RSS Publisher

Bot de diffusion automatisée pour **Bergfrid**, média de géopolitique et d’intelligence stratégique.  
Il surveille un flux RSS et publie automatiquement les nouveaux articles sur plusieurs canaux (Discord, Telegram), avec une architecture extensible vers d’autres réseaux sociaux.

---

## 🎯 Objectifs

- Diffuser rapidement les publications Bergfrid sur plusieurs plateformes
- Éviter les doublons et le spam
- Conserver une forme éditoriale propre et lisible
- Fournir une base technique extensible (X, Threads, Instagram, etc.)

---

## 🧱 Architecture générale

Le projet est structuré en **trois couches** :

1. **Core**  
   Logique métier: RSS, parsing, état, normalisation des articles.
2. **Publishers**  
   Modules indépendants chargés de publier sur chaque plateforme.
3. **Runner**  
   Bot Discord + scheduler qui orchestre le tout.

---

## 📁 Arborescence

```bash
├── main.py # Point d’entrée, scheduler RSS, orchestration
├── requirements.txt # Dépendances Python
├── README.md # Documentation
│
├── core/
│ ├── models.py # Modèle Article (structure normalisée)
│ ├── rss.py # Récupération et parsing du flux RSS
│ ├── state.py # Persistance (last_id, etag, anti-doublons)
│ └── utils.py # Helpers (texte, tags, UTM, formatage)
│
├── publishers/
│ ├── base.py # Interface abstraite Publisher
│ ├── discord_pub.py # Publication Discord
│ └── telegram_pub.py # Publication Telegram
│
├── config/
│ └── publish_targets.json # Plateformes actives + configuration
│
├── bergfrid_state.json # État persistant (créé automatiquement)
└── discord_channels.json # Mapping serveurs Discord → salons
```


---

## ⚙️ Technologies utilisées

- **Python 3.10+**
- **discord.py** (bot Discord)
- **feedparser** (RSS)
- **aiohttp** (HTTP async, Telegram)
- **BeautifulSoup4** (nettoyage HTML)
- **asyncio** (scheduler, délais anti-spam)

---

## 📰 Fonctionnement

1. Le bot interroge le flux RSS à intervalle régulier.
2. Les entrées sont comparées à l’état persisté (`last_id`).
3. Les nouveaux articles sont normalisés en objets `Article`.
4. Chaque publisher actif tente la publication.
5. L’article est marqué comme publié **uniquement si toutes les plateformes actives réussissent**.
6. Un délai minimum (30s par défaut) est appliqué entre chaque publication.

---

## 🚀 Installation

### 1. Cloner le dépôt
```bash
git clone https://github.com/bergfrid/rss-publisher.git
cd rss-publisher
```

### 2. Installer les dépendances
```bash
pip install -r requirements.txt
```

### 3. Variables d’environnement requises
```bash
DISCORD_TOKEN=...
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
DISCORD_NEWS_CHANNEL_ID=...

# Optionnels
RSS_POLL_MINUTES=2
ARTICLE_PUBLISH_DELAY_SECONDS=30
```

---

## ▶️ Lancer le bot
```bash
python main.py
```

Au premier lancement, le bot effectue un cold start:
- il se synchronise sur le dernier article RSS
- aucune publication rétroactive n’est effectuée

---

## 🛠 Commandes Discord

!setnews [#canal]
Définit le salon de publication pour le serveur.

!unsetnews
Supprime le salon configuré.

!rsssync
Force une synchronisation RSS sans publier (anti-spam).

---

## 🔧 Configuration des plateformes
config/publish_targets.json
```bash
{
  "enabled": ["discord", "telegram"],
  "discord": {},
  "telegram": {}
}
```

Permet d’activer/désactiver des plateformes sans modifier le code.

---

## 🧩 Extensibilité

Pour ajouter une nouvelle plateforme:

Créer un fichier dans publishers/ (ex: x_pub.py)

Implémenter la méthode publish(article, cfg)

L’enregistrer dans main.py

L’activer via publish_targets.json

Aucune modification du cœur RSS n’est nécessaire.

---

## 🤝 Contribuer

Contributions bienvenues, notamment pour:

nouveaux publishers (X, Threads, Instagram)

amélioration du format éditorial

robustesse et tests

Bonnes pratiques:

code clair, typé

pas de dépendances lourdes inutiles

respect de l’architecture modulaire

---

## 📜 Licence

Ce projet est distribué sous licence MIT.

Libre d’utilisation, de modification et de redistribution, sous réserve de conserver la mention de copyright.

---

## 🧠 Note éditoriale

Bergfrid vise la diffusion d’informations géopolitiques structurées, sans sensationnalisme algorithmique.
Ce bot est conçu comme un outil de diffusion, pas comme un générateur de contenu.