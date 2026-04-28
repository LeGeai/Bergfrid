# Recréer le bot Discord et le pointer vers un nouveau serveur

Tutoriel pour remplacer le bot Discord (mort) et migrer Bergfrid vers un autre serveur Discord.

---

## 1. Créer une nouvelle application Discord

1. Va sur <https://discord.com/developers/applications>
2. Clique **New Application** → donne-lui un nom (ex. `Bergfrid`)
3. Onglet **Bot** dans la sidebar → **Add Bot** (ou **Reset Token** si déjà créé)
4. **Copie le token immédiatement** (il ne sera plus affiché ensuite) — c'est ton nouveau `DISCORD_TOKEN`

---

## 2. Activer les intents privilégiés

Toujours dans l'onglet **Bot**, scroll jusqu'à **Privileged Gateway Intents** et coche :

- **MESSAGE CONTENT INTENT** (obligatoire — [main.py:67](main.py#L67) fait `intents.message_content = True`)
- **SERVER MEMBERS INTENT** (utile, pas strictement requis)

Puis clique **Save Changes**.

---

## 3. Définir les permissions

Onglet **OAuth2** → **URL Generator** :

- **Scopes** : coche `bot`
- **Bot Permissions** : coche
  - `View Channels`
  - `Send Messages`
  - `Embed Links`
  - `Add Reactions`
  - `Create Public Threads`
  - `Send Messages in Threads`
  - `Manage Messages`
  - `Read Message History`

Discord génère une URL d'invitation en bas de page.

---

## 4. Inviter le bot sur le nouveau serveur

1. Copie l'URL générée
2. Ouvre-la dans un navigateur
3. Sélectionne le serveur Discord cible
4. Autorise

> Tu dois avoir la permission `Manage Server` sur ce Discord pour l'inviter.

---

## 5. Récupérer les IDs des canaux

Dans le client Discord :

1. Active le **Mode Développeur** : `Paramètres utilisateur` → `Avancés` → `Mode développeur` ON
2. Clic droit sur chaque canal cible → **Copier l'identifiant du salon**

Tu auras besoin de :

| Variable | Rôle | Référence code |
|---|---|---|
| `DISCORD_NEWS_CHANNEL_ID` | Canal officiel des articles | [core/config.py:16](core/config.py#L16) |
| `DISCORD_LOG_CHANNEL_ID` | Logs de publication, alertes, reboot notice | [core/config.py:17](core/config.py#L17) |
| `DISCORD_TWITTER_CHANNEL_ID` | Drafts Twitter à copier-coller | [core/config.py:18](core/config.py#L18) |
| `DISCORD_SAINTS_CHANNEL_ID` | Angélus à 7h, 12h, 19h | [core/config.py:19](core/config.py#L19) |

---

## 6. Mettre à jour les variables d'environnement

Dans ton hébergeur (Render / Railway / Fly / etc.) ou ton `.env` local :

```bash
DISCORD_TOKEN=<nouveau token de l'étape 1>
DISCORD_NEWS_CHANNEL_ID=<id du canal articles>
DISCORD_LOG_CHANNEL_ID=<id du canal logs>     # optionnel mais recommandé
DISCORD_TWITTER_CHANNEL_ID=<id>               # optionnel
DISCORD_SAINTS_CHANNEL_ID=<id>                # optionnel (Angélus)
```

---

## 7. Nettoyer l'ancien mapping multi-serveurs

[discord_channels.json](discord_channels.json) contient le mapping `guild_id → channel_id` alimenté par la commande `bg!setnews`. Comme tu changes de Discord, vide-le :

```json
{}
```

Sinon le bot va tenter de poster sur des canaux d'anciens serveurs (échecs `Forbidden` / `NotFound` non bloquants — voir [publishers/discord_pub.py:36-44](publishers/discord_pub.py#L36-L44) — mais bruit dans les logs).

---

## 8. Reset du state (optionnel)

Si tu veux que le bot reposte les articles récents sur le nouveau Discord :

- soit **vide** [bergfrid_state.json](bergfrid_state.json) → cold start qui *seed* sans publier (pas idéal si tu veux du contenu visible immédiatement) ;
- soit **supprime juste** l'entrée `sent.discord` dans le state pour que le **catchup** rattrape les ~5 derniers articles (`CATCHUP_WINDOW = 5` dans [main.py:674](main.py#L674)) — à condition qu'ils aient été publiés sur au moins une autre plateforme (Telegram / Bluesky / Mastodon).

---

## 9. Redéployer et vérifier

Après redéploiement, surveille dans le canal de logs :

- `🔄 Mise à jour effectuée.` (reboot notice — voir [main.py:485-503](main.py#L485-L503))
- Les `📋 <titre>  ✅ Discord` au prochain article publié

Dans n'importe quel salon où le bot voit les messages, tape `bg!help` pour vérifier qu'il répond.

---

## Commandes Discord disponibles

Après installation, ces commandes sont utilisables sur le serveur :

| Commande | Permission requise | Effet |
|---|---|---|
| `bg!help` | aucune | Affiche l'aide |
| `bg!setnews [#canal]` | `Manage Channels` | Définit le canal de publication pour ce serveur |
| `bg!unsetnews` | `Manage Channels` | Retire le canal configuré |
| `bg!rsssync` | `Manage Channels` | Synchronise `last_id` sur le dernier article (sans publier) |
| `bg!preview <nom>` | `Manage Channels` + dans le canal de logs | Prévisualise un message (`nuit`, `matin`, `angelus`, `nuit-tg`, `matin-tg`, `reboot`, `article`, `x`) |
