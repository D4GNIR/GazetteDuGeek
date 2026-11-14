# La Gazette du Geek

Une page web au style journal qui résume les actus **tech, gaming, impression 3D et geek** : la Une, les sujets majeurs, les brèves, et les rumeurs à surveiller. Elle tourne sur le NAS dans Docker et s'ouvre depuis l'écran d'accueil du téléphone, via le VPN.

- Un seul script, `gazette.py`, sans dépendance (Python 3.9 ou plus).
- Aucun JavaScript dans les pages. Le thème clair ou sombre suit le téléphone, ou peut être forcé avec `GAZETTE_THEME` (voir « Réglages Docker »).
- Les grosses alertes (panne, faille, rachat…) sont repérées par un bandeau rouge « Alerte ». Chaque sujet indique combien de sites en parlent.
- **Édition automatique à 6 h 30**, heure de Paris, sans cron : c'est le service lui-même qui la lance. Si le NAS était éteint à 6 h 30, l'édition est faite au redémarrage. Le bouton **Actualiser** en génère une nouvelle à tout moment (1 à 2 secondes).
- **Français d'abord** : un sujet couvert par un site français est préféré, à égalité, et affiché avec son titre français. Les sujets anglais restent présents.
- S'installe sur l'écran d'accueil comme une appli (icône, plein écran).
- Historique des 14 dernières éditions.

## Utilisation sur le Mac

Pour tester un réglage avant de le copier sur le NAS :

```bash
python3 gazette.py --check                 # état de chaque flux (OK / KO)
python3 gazette.py --no-git --no-notify    # générer l'édition du jour sans commit ni notif
./run.sh                                   # édition complète avec les réglages de .env
./run.sh --serve                           # sert la gazette sur http://127.0.0.1:8766/
python3 -m unittest discover tests         # tests hors ligne
```

Options utiles pendant la semaine de réglage :

| Option | Effet |
|---|---|
| `--save DOSSIER` | garde une copie brute des flux du jour |
| `--offline DOSSIER` | rejoue des flux enregistrés (`DOSSIER/<nom-du-flux>.xml`) sans réseau |
| `--now 2026-11-13T06:30:00+01:00` | simule une autre heure de génération |
| `--out DOSSIER` | écrit ailleurs que dans `out/` |

Chaque édition contient aussi un `edition.json` : nombre de sites, mot d'alerte détecté et score de chaque sujet. C'est ce fichier qu'on lit pour comprendre un classement.

## Réglages

Tout est en haut de `gazette.py`, section **RÉGLAGES** :

- `FEEDS` : la liste des flux (catégorie, nom, langue, URL). Mettre un `#` devant une ligne pour la désactiver.
- `ALERT_WORDS`, `RUMOR_WORDS` : mots qui envoient un sujet en majeur ou en « à surveiller ». La comparaison se fait sans accents ni majuscules, et `*` accepte n'importe quelle fin de mot.
- `SYNONYMS`, `STOPWORDS`, `GROUP_*` : réglage du regroupement des titres.
- `SCORE_*`, `MAJOR_*`, `MINOR_MAX`, `WATCH_MAX` : seuils de classement.
- `SCORE_PREFERRED_LANG`, `PREFERRED_LANG_HOURS` : la priorité donnée au français (voir plus bas).

Dans Docker, les réglages d'environnement se mettent dans `docker-compose.yml` (rubrique `environment`). Sinon, dans `.env` (copie de `.env.example`, jamais commité).

La traduction automatique (LibreTranslate) et la notification ntfy existent toujours dans le script mais sont **désactivées** : elles ne servent que si `GAZETTE_TRANSLATE_URL` ou `GAZETTE_NTFY_TOPIC` sont remplis.

### Règles de classement (cahier des charges §5)

- **Score** : 2 points par site qui parle du sujet, plus 3 points si un mot d'alerte est présent.
- **À la une** : le sujet au meilleur score (hors rumeurs).
- **Majeurs** (5 au plus) : sujets repris par au moins 3 sites, ou contenant un mot d'alerte. S'il y en a moins de 3, on complète avec les sujets repris par 2 sites.
- **À surveiller** (4 au plus) : sujets où au moins la moitié des articles contient un mot de rumeur. Un seul « reportedly » parmi 7 articles ne suffit pas.
- **En bref** (10 au plus) : le reste, du plus récent au plus ancien, en alternant les catégories.

**Français d'abord, sans écarter l'anglais :**

- Un sujet couvert par au moins un site français gagne 1 point. Un site de plus en vaut 2 : le bonus ne départage donc que des sujets à égalité. Une info reprise par 3 sites anglais passe toujours devant une info d'un seul site français.
- Dans « En bref », une brève française est classée comme si elle avait 6 heures de moins.
- Quand un sujet est couvert en français et en anglais, c'est le titre et le résumé français qui s'affichent. Les sites anglais sont listés dans « Aussi sur ».

### Regroupement

Deux titres parlent du même sujet s'ils partagent au moins 2 mots significatifs, représentant au moins la moitié du titre le plus court. Les mots vides sont ignorés et des synonymes FR/EN sont appliqués (`outage` → `panne`, `CEO` → `PDG`…).

Une deuxième passe rattache ensuite un article isolé à un sujet déjà repris par plusieurs sites, s'il partage 2 mots du « cœur » de ce sujet. Par exemple, « Xbox : la panne Microsoft… » rejoint la grosse panne.

### Flux

| Flux | État au 13/11/2026 |
|---|---|
| Fabbaloo | `/feed` répond 403 aux scripts ; `/feed/atom` fonctionne et c'est lui qui est utilisé. |
| Warhammer Community | 403 sur toutes les adresses testées (protection anti-robots) : **désactivé**. |
| Les 22 autres | OK au 14/11/2026. Au total, 12 flux français et 11 anglais. |

Un flux en erreur n'empêche pas l'édition d'être générée. Il est listé en bas de page.

Quelques sites ont aussi un flux par rubrique (ex. `Frandroid (3D)`, `Numerama (pop culture)`). Ces flux sont placés **avant** le flux général du site : un article présent dans les deux est ainsi classé dans la rubrique (impression 3D, geek) plutôt qu'en tech.

La presse française spécialisée en impression 3D publie peu (quelques articles par semaine) : beaucoup de jours, cette catégorie reste surtout anglophone.

## Installation sur le NAS (Docker)

Un seul conteneur sert tout : les pages, l'icône d'écran d'accueil et le bouton Actualiser. Pas de cron, pas de nginx, pas de base de données.

### 1. Copier les fichiers sur le NAS

Copier `gazette.py`, `Dockerfile` et `docker-compose.yml` dans un dossier du NAS, par exemple `docker/gazette`. Le gestionnaire de fichiers du NAS suffit.

### 2. Lancer

Dans l'application Docker du NAS (UGREEN, Synology…) : **Projet → Créer**, en choisissant ce dossier. Le fichier `docker-compose.yml` est repris tel quel.

Ou en SSH, dans ce dossier :

```bash
sudo docker compose up -d --build
sudo docker compose logs -f          # Ctrl+C pour quitter
```

Les éditions sont rangées dans un volume géré par Docker (`gazette-data`) : aucun réglage de droits n'est nécessaire, et elles survivent à la suppression du conteneur ou du projet.

### 3. Ouvrir depuis le téléphone

- Activer le VPN, puis ouvrir `http://<ip-du-nas>:8642/`. La toute première édition est générée au démarrage du conteneur.
- **iPhone** : dans Safari, **•••** → **Partager** → **Sur l'écran d'accueil**, avec « Ouvrir comme app web » activé, puis **Ajouter**. La gazette s'ouvre ensuite en plein écran, avec sa propre icône.
- **Android** : Chrome → ⋮ → **Ajouter à l'écran d'accueil**. En `http://`, Chrome crée un simple raccourci qui ouvre un onglet. Pour le plein écran, il faut une adresse `https://` (par exemple avec `tailscale serve`, ou le reverse proxy du NAS avec un certificat).

En plein écran, il n'y a ni barre d'adresse ni bouton recharger : c'est le bouton **Actualiser** qui sert à rafraîchir.

### 4. Mettre à jour

Après une modification de `gazette.py` (flux, catégories, réglages), recopier le fichier puis redéployer le projet, ou en SSH :

```bash
docker compose up -d --build
```

Les éditions du volume `gazette-data` sont conservées. L'image est reconstruite à chaque déploiement.

### Réglages Docker

| Variable (`docker-compose.yml`) | Défaut | Effet |
|---|---|---|
| `GAZETTE_DAILY_AT` | `06:30` | Heure de l'édition automatique (heure de Paris). Vide = seulement le bouton. |
| `GAZETTE_AUTO_REFRESH_HOURS` | `0` | Si différent de 0 : nouvelle édition à l'ouverture quand la dernière a plus de N heures. |
| `GAZETTE_THEME` | `auto` | `auto` suit le téléphone, `dark` ou `light` force le thème. |
| port `8642:8080` | | Le premier nombre est le port sur le NAS : le changer s'il est déjà pris. Le second ne bouge pas. |

Le conteneur est durci : système de fichiers en lecture seule (sauf le volume des éditions), aucun privilège Linux, utilisateur sans droits, 256 Mo de mémoire au plus.

**Ne jamais rediriger le port 8642 sur la box.** On y accède depuis l'extérieur uniquement par le VPN.

## Secrets

L'installation Docker n'a besoin d'aucun secret : pas de mot de passe, pas de clé, pas de compte.

Les options désactivées (notification ntfy, traduction) en demandent un. Il va alors dans un fichier `.env`, copié depuis `.env.example`. Ce fichier est exclu du dépôt par `.gitignore` et ne doit jamais être commité. Rien d'autre ne doit contenir d'adresse IP, de nom d'utilisateur ou de mot de passe.

Une autre installation, sur un serveur Linux sans Docker (timer systemd, nginx, commit Gitea, ntfy), est décrite dans [deploy/README.md](deploy/README.md).

## Sécurité

- Le contenu des flux n'est jamais exécuté : le HTML est réduit à du texte brut (scripts supprimés), tout est échappé à l'affichage, et seuls les liens `http(s)://` sont conservés.
- Les flux qui déclarent des entités XML sont refusés, et leur taille est limitée à 5 Mo.
- Le service ne sert que les fichiers du dossier des éditions : pas de liste de dossier, pas de fichier caché, pas de remontée de dossier (`../`). Les pages sont envoyées avec une politique de sécurité stricte (aucun script, aucune ressource externe).
- Le bouton « Actualiser » ne fait que relancer la génération, au plus une fois toutes les 30 secondes.
- Rien ne sort du réseau à part la lecture des flux RSS. Aucune police ni ressource externe n'est chargée.

## Limites connues

- Le regroupement se fait sur les mots des titres : des sujets proches peuvent être fusionnés (par exemple plusieurs articles « GTA 6 » la semaine de sa sortie), ou au contraire séparés.
- Les résumés viennent des flux : certains sont courts, d'autres absents.
- La page n'est pas mise à jour toute seule si elle reste ouverte : il faut la rouvrir ou toucher Actualiser.
- Le service est prévu pour un usage personnel, derrière le VPN : il n'a pas de mot de passe.

## Licence

MIT : chacun peut réutiliser, modifier et partager ce code, à condition de garder la mention de copyright et le texte du fichier [LICENSE](LICENSE).

Les articles affichés restent la propriété de leurs sites. La gazette n'en montre que le titre, un court résumé et un lien vers l'article d'origine.
