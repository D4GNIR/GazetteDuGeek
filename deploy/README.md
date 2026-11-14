# Installation sur un serveur Linux, sans Docker

Alternative à l'installation Docker du [README principal](../README.md). Les fichiers de ce dossier permettent l'ancienne installation : génération à 6 h 30 par timer systemd, pages servies par nginx, commit Gitea et notification ntfy.

Exemple pour un serveur Debian ou Ubuntu, avec le script installé dans `/opt/gazette` et lancé par un utilisateur dédié `gazette`.

Dans les commandes ci-dessous, remplacer `<gitea>` par l'adresse du serveur Gitea et `<utilisateur>` par le compte qui possède le dépôt.

## 1. Clé SSH dédiée

Sur le serveur :

```bash
sudo useradd --system --create-home --home-dir /opt/gazette-home gazette
sudo -u gazette ssh-keygen -t ed25519 -N "" -f /opt/gazette-home/.ssh/id_ed25519 -C gazette@serveur
sudo cat /opt/gazette-home/.ssh/id_ed25519.pub
```

Dans Gitea, ajouter cette clé publique comme **clé de déploiement** du dépôt `gazette`, avec l'écriture autorisée. Elle ne donne accès qu'à ce dépôt.

```bash
sudo mkdir /opt/gazette && sudo chown gazette: /opt/gazette
sudo -u gazette git clone git@<gitea>:<utilisateur>/gazette.git /opt/gazette
sudo -u gazette git -C /opt/gazette config user.name "Gazette"
sudo -u gazette git -C /opt/gazette config user.email "gazette@serveur.lan"
sudo -u gazette cp /opt/gazette/.env.example /opt/gazette/.env   # puis le remplir
sudo -u gazette /opt/gazette/run.sh --no-notify                  # premier essai
```

Le script commite `out/` dans ce clone puis pousse. Si le push échoue, l'édition est quand même générée et servie.

## 2. Planification à 6 h 30, heure de Paris

**Option recommandée : un timer systemd.** Il gère le fuseau horaire et rattrape l'édition si le serveur était éteint à 6 h 30.

```bash
sudo cp /opt/gazette/deploy/gazette.service /opt/gazette/deploy/gazette.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now gazette.timer
systemctl list-timers gazette.timer       # prochaine exécution
journalctl -u gazette.service             # journal des éditions
```

**Option cron** : seulement si le serveur est à l'heure de Paris (`timedatectl set-timezone Europe/Paris`).

```bash
sudo -u gazette crontab -e
# 30 6 * * * /opt/gazette/run.sh >> /opt/gazette-home/gazette.log 2>&1
```

## 3. nginx (réseau local / VPN)

```bash
sudo apt install nginx
sudo cp /opt/gazette/deploy/nginx-gazette.conf /etc/nginx/sites-available/gazette
sudo ln -s /etc/nginx/sites-available/gazette /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Il faut aussi que `gazette.lan` pointe vers le serveur (DNS de la box, Pi-hole ou `/etc/hosts`), et que `GAZETTE_BASE_URL` dans `.env` corresponde à cette adresse. Si nginx tourne avec un autre utilisateur, il doit pouvoir lire `/opt/gazette/out` : `chmod o+rx /opt/gazette /opt/gazette/out`.

**Ne pas ouvrir de port sur la box.** Depuis l'extérieur, on passe par le VPN (WireGuard ou Tailscale).

## 4. Traduction (LibreTranslate)

Docker doit être installé sur le serveur. Le fichier `deploy/libretranslate-compose.yml` ne charge que l'anglais et le français, et n'écoute que sur le serveur lui-même :

```bash
cd /opt/gazette && sudo docker compose -f deploy/libretranslate-compose.yml up -d
curl -s -X POST localhost:5000/translate -H 'Content-Type: application/json' \
     -d '{"q":"Hello world","source":"en","target":"fr"}'
```

Le premier démarrage télécharge les modèles et prend quelques minutes. Il faut ensuite que `.env` contienne `GAZETTE_TRANSLATE_URL=http://127.0.0.1:5000`. Compter environ 1 Go de RAM.

## 5. Bouton « Actualiser »

Le bouton envoie la page vers `/actualiser`. nginx transmet la demande au service local de la gazette (`run.sh --serve`), qui relance une génération sans notification. Pendant la génération, une page « Actualisation en cours » s'affiche, puis l'édition du jour la remplace automatiquement.

```bash
sudo cp /opt/gazette/deploy/gazette-web.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now gazette-web.service
```

La configuration nginx de `deploy/` contient déjà la redirection vers ce service. Si on actualise plusieurs fois le même jour, l'édition du jour est remplacée et chaque version est commitée.

## 6. Notification ntfy

- **Auto-hébergé (recommandé)** : installer ntfy sur le serveur, puis mettre `GAZETTE_NTFY_SERVER=http://ntfy.lan` (accessible via le VPN).
- **Service public** : `GAZETTE_NTFY_SERVER=https://ntfy.sh`, avec un nom de sujet long et secret (`openssl rand -hex 16`). Tous ceux qui connaissent ce nom peuvent lire les notifications.

Dans l'appli ntfy du téléphone, s'abonner au même serveur et au même sujet. Toucher la notification ouvre l'édition du jour. Pour tester :

```bash
sudo -u gazette /opt/gazette/run.sh --no-git
```
