#!/usr/bin/env python3
"""La Gazette du Geek — génère chaque matin une page HTML d'actus à partir de flux RSS/Atom.

Un seul fichier, bibliothèque standard uniquement (Python 3.9+).

    python3 gazette.py                 # édition du jour (fetch, HTML, nettoyage, commit, notif)
    python3 gazette.py --check         # teste chaque flux et affiche son état
    python3 gazette.py --no-git --no-notify
    python3 gazette.py --offline tests/fixtures --now 2026-11-13T06:30:00+01:00 --out /tmp/out

Tous les réglages sont dans la section RÉGLAGES ci-dessous. Les secrets (sujet ntfy, jeton)
se passent par variables d'environnement (voir .env.example).
"""

import argparse
import concurrent.futures
import email.utils
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

# =====================================================================================
# RÉGLAGES
# =====================================================================================

HERE = Path(__file__).resolve().parent

# Catégories : clé -> (libellé, couleur du badge)
CATEGORIES = {
    "tech": ("Tech", "#2563eb"),
    "gaming": ("Gaming", "#16a34a"),
    "3d": ("Impression 3D", "#ea580c"),
    "geek": ("Geek", "#9333ea"),
    "ia": ("IA", "#0891b2"),
}

# Catégories par mots-clés : un article venant d'un flux des catégories listées passe dans la
# nouvelle catégorie si son TITRE contient un de ces mots. Sans accents ni majuscules, « * » accepte
# n'importe quelle fin de mot. Exception : les sigles écrits en MAJUSCULES (IA, AI, LLM…) ne
# reconnaissent que les majuscules, pour que « j'ai » ou « mai » ne deviennent pas de l'IA.
# Mots volontairement absents : Copilot (la panne Microsoft 365 partirait en IA), Claude, Llama,
# Mistral seul (prénom, animal, vent).
KEYWORD_CATEGORIES = [
    ("ia", ("tech", "geek"), [
        "IA", "AI", "AGI", "LLM*", "GPT*", "ChatGPT", "OpenAI", "Anthropic", "Gemini", "DeepSeek",
        "Mistral AI", "Grok", "xAI", "Perplexity", "Midjourney", "Sora", "Sam Altman",
        "intelligence artificielle", "artificial intelligence", "IA générative", "generative AI",
        "chatbot*", "agent* IA", "AI agent*", "machine learning", "deep learning",
        "apprentissage automatique", "réseau* de neurones", "neural network*",
    ]),
]

# Flux : (catégorie, nom affiché, langue, URL). Mettre un « # » devant une ligne pour la désactiver.
# Si un même article apparaît dans deux flux (ex. Hackaday), c'est le premier listé qui gagne.
FEEDS = [
    # Rubriques d'un site déjà présent : placées avant son flux général pour que leurs articles
    # soient classés dans la bonne catégorie (le premier flux listé gagne).
    ("3d", "Frandroid (3D)", "fr", "https://www.frandroid.com/tag/impression-3d/feed"),
    ("geek", "Numerama (pop culture)", "fr", "https://www.numerama.com/pop-culture/feed/"),
    ("tech", "Numerama", "fr", "https://www.numerama.com/feed/"),
    ("tech", "Korben", "fr", "https://korben.info/feed"),
    ("tech", "The Verge", "en", "https://www.theverge.com/rss/index.xml"),
    ("tech", "Ars Technica", "en", "https://feeds.arstechnica.com/arstechnica/index"),
    ("tech", "BleepingComputer", "en", "https://www.bleepingcomputer.com/feed/"),
    ("tech", "Frandroid", "fr", "https://www.frandroid.com/feed"),
    ("gaming", "Jeuxvideo.com", "fr", "https://www.jeuxvideo.com/rss/rss.xml"),
    ("gaming", "Gamekult", "fr", "https://www.gamekult.com/feed.xml"),
    ("gaming", "Rock Paper Shotgun", "en", "https://www.rockpapershotgun.com/feed"),
    ("gaming", "Eurogamer", "en", "https://www.eurogamer.net/feed"),
    ("gaming", "GamesIndustry.biz", "en", "https://www.gamesindustry.biz/feed"),
    ("3d", "3DPrint.com", "en", "https://3dprint.com/feed/"),
    # /feed renvoie 403 aux scripts, la variante Atom passe.
    ("3d", "Fabbaloo", "en", "https://www.fabbaloo.com/feed/atom"),
    ("3d", "3Dnatives", "fr", "https://www.3dnatives.com/feed/"),
    ("3d", "Les Imprimantes 3D", "fr", "https://www.lesimprimantes3d.fr/feed/"),
    ("3d", "Hackaday (3D)", "en", "https://hackaday.com/category/3d-printer-hacks/feed/"),
    ("geek", "Journal du Geek", "fr", "https://www.journaldugeek.com/feed/"),
    ("geek", "Begeek", "fr", "https://www.begeek.fr/feed"),
    ("geek", "ActuaBD", "fr", "https://www.actuabd.com/spip.php?page=backend"),
    ("geek", "Hackaday", "en", "https://hackaday.com/blog/feed/"),
    # Warhammer Community bloque les scripts (403, protection anti-robots) : désactivé.
    # ("geek", "Warhammer Community", "en", "https://www.warhammer-community.com/feed/"),
    ("geek", "Blender", "en", "https://www.blender.org/feed/"),
    # IA : flux dédiés en français. Les articles IA des flux Tech et Geek y arrivent aussi par
    # mots-clés (voir KEYWORD_CATEGORIES).
    ("ia", "ActuIA", "fr", "https://www.actuia.com/feed/"),
    ("ia", "Siècle Digital (IA)", "fr", "https://siecledigital.fr/category/intelligence-artificielle/feed/"),
    ("ia", "Les Numériques (IA)", "fr", "https://www.lesnumeriques.com/intelligence-artificielle/rss.xml"),
    ("ia", "Le Monde Informatique (IA)", "fr", "https://www.lemondeinformatique.fr/flux-rss/thematique/intelligence-artificielle/rss.xml"),
    ("ia", "Journal du Net (IA)", "fr", "https://www.journaldunet.com/intelligence-artificielle/rss/"),
]

# Fenêtre de récupération (24 h + marge)
WINDOW_HOURS = 30

# Classement
SCORE_PER_SITE = 2          # points par site qui parle du sujet
SCORE_ALERT = 3             # bonus si un mot d'alerte est présent
MAJOR_MIN_SITES = 3         # repris par N sites -> majeur
MAJOR_MIN = 3               # si moins de N majeurs, on complète avec les sujets à 2 sites
MAJOR_MAX = 5
MINOR_MAX = 10
WATCH_MAX = 4
PREFERRED_LANG = "fr"       # langue préférée pour le titre affiché d'un sujet
# Priorité au français sans écarter l'anglais :
# - un sujet couvert par au moins un site français gagne SCORE_PREFERRED_LANG point. C'est moins
#   qu'un site de plus (SCORE_PER_SITE), donc ça ne départage que les sujets à égalité ;
# - dans « En bref », une brève française est classée comme si elle était PREFERRED_LANG_HOURS
#   heures plus récente.
SCORE_PREFERRED_LANG = 1
PREFERRED_LANG_HOURS = 6

# Regroupement : deux titres parlent du même sujet s'ils partagent au moins
# GROUP_MIN_SHARED mots significatifs ET si ces mots représentent au moins GROUP_MIN_RATIO
# du titre le plus court.
GROUP_MIN_SHARED = 2
GROUP_MIN_RATIO = 0.5
# Deuxième passe : un article (ou petit groupe) rejoint un sujet déjà repris par plusieurs
# sites s'il partage au moins GROUP_CORE_SHARED mots avec le « cœur » de ce sujet
# (mots présents chez au moins deux sites). Ex. « Xbox : la panne Microsoft… » rejoint la panne.
GROUP_CORE_SHARED = 2
# Synonymes ramenés à un même mot pour regrouper FR/EN (après suppression des accents et du « s »).
SYNONYMS = {
    "outage": "panne", "down": "panne",
    "vulnerability": "faille", "vulnerabilite": "faille", "flaw": "faille", "zero-day": "faille", "0-day": "faille",
    "acquisition": "rachat", "acquire": "rachat", "acquires": "rachat", "acquired": "rachat",
    "acquiert": "rachat", "rachete": "rachat", "rachetee": "rachat", "buy": "rachat", "buys": "rachat",
    "layoff": "licenciement", "lays": "licenciement",
    "resign": "demission", "resigns": "demission", "demissionne": "demission", "quits": "demission",
    "ceo": "pdg", "patron": "pdg",
    "ban": "interdiction", "banned": "interdiction", "interdit": "interdiction",
}
# Un sujet n'est classé « rumeur » que si au moins cette part de ses articles en contient une.
RUMOR_MIN_SHARE = 0.5

# Mots-clés (comparés sans accents ni majuscules, sur le titre ; « * » = n'importe quelle fin)
ALERT_WORDS = [
    "panne", "pannes", "en panne", "hors service", "outage", "outages", "is down", "went down",
    "faille", "failles", "vulnerabilite*", "vulnerabilit*", "zero-day", "zero day", "0-day", "cve",
    "ransomware", "rancongiciel*", "cyberattaque*", "cyberattack*", "piratage", "hacked", "hackers",
    "breach", "fuite de donnees", "data leak", "data breach", "malware", "ddos",
    "en fuite", "fuite massive", "donnees personnelles", "adresses e-mail", "mots de passe",
    "rachat", "rachete", "rachetee", "rachete par", "acquisition", "acquiert", "acquires", "acquired",
    "licenciement*", "layoff*", "lays off", "fermeture", "ferme ses portes", "shuts down",
    "shutting down", "faillite", "bankrupt*", "mise a jour d'urgence", "emergency update",
    "patch d'urgence", "emergency patch", "rappel de produit", "recall",
]
RUMOR_WORDS = [
    "rumeur*", "rumor*", "rumour*", "leak", "leaks", "leaked", "leaker", "fuite", "fuites",
    "fuite*", "teaser", "teasers", "tease", "teases", "teased", "date de sortie", "release date",
    "aurait", "auraient", "reportedly", "insider", "selon nos informations", "pourrait sortir",
    "non confirme*", "unconfirmed", "spotted", "apercu dans",
]

# Mots ignorés pour le regroupement des titres
STOPWORDS = set("""
le la les un une des du de d l au aux et ou en a à dans sur pour par avec sans sous ce cet cette
ces son sa ses leur leurs nos vos notre votre qui que quoi dont est sont etait ete etre avoir a
ont plus moins tres tout tous toute toutes pas ne ni se si il ils elle elles on nous vous je tu
mais comme aussi deja encore enfin voici voila apres avant entre chez vers quand comment pourquoi
nouveau nouvelle nouveaux nouvelles premier premiere jeu jeux test video officiel officielle
annonce annonces disponible disponibles mise jour maj prix promo bon plan offre meilleur meilleurs
the a an and or of to in on for with without by at from as is are was were be been has have had
it its this that these those your our their his her you we they not but if then than so just how
why what when who which new now more most all any can will would could should may might into out
about over after before up down get gets got make makes made first one two here there game games
review hands-on trailer update updates week today year day new best deal deals price
""".split())

# Sortie
OUT_DIR = HERE / "out"
KEEP_EDITIONS = 14

# Réseau
FETCH_TIMEOUT = 15          # secondes par flux
FETCH_WORKERS = 8
MAX_FEED_BYTES = 5 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Affichage
# Thème : "auto" suit le réglage clair/sombre du téléphone, "dark" toujours sombre, "light" toujours clair.
THEME = os.environ.get("GAZETTE_THEME", "auto")
SUMMARY_MAX = 280
TIMEZONE = "Europe/Paris"

# Git (F10) : commit de out/ dans le dépôt qui contient ce script, puis push.
GIT_COMMIT = os.environ.get("GAZETTE_GIT", "1") != "0"   # 0 = pas de commit (ex. Docker)
GIT_PUSH = True

# Notification ntfy (F11). Secrets dans l'environnement : voir .env.example.
NTFY_SERVER = os.environ.get("GAZETTE_NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("GAZETTE_NTFY_TOPIC", "")   # vide = pas de notification
NTFY_TOKEN = os.environ.get("GAZETTE_NTFY_TOKEN", "")
BASE_URL = os.environ.get("GAZETTE_BASE_URL", "http://gazette.lan")  # URL publique de out/

# Traduction des sujets étrangers par LibreTranslate auto-hébergé (rien ne sort du réseau).
# Vide = pas de traduction. Ex. : http://127.0.0.1:5000
TRANSLATE_URL = os.environ.get("GAZETTE_TRANSLATE_URL", "")
TRANSLATE_KEY = os.environ.get("GAZETTE_TRANSLATE_KEY", "")
TRANSLATE_TIMEOUT = 50      # secondes pour l'ensemble des traductions d'une édition
TRANSLATE_BATCH = 40        # textes envoyés par requête

# Bouton « Actualiser » : POST vers ce chemin, servi par `gazette.py --serve` derrière nginx.
# Vide = pas de bouton.
REFRESH_PATH = os.environ.get("GAZETTE_REFRESH_PATH", "/actualiser")
SERVE_PORT = int(os.environ.get("GAZETTE_SERVE_PORT", "8766"))
SERVE_HOST = os.environ.get("GAZETTE_SERVE_HOST", "127.0.0.1")  # 0.0.0.0 dans Docker
REFRESH_COOLDOWN = 30       # secondes minimum entre deux actualisations (anti-rafale)
# Édition quotidienne générée par le service (`--serve`), heure de Paris. Vide = pas d'édition
# automatique. Si le service était arrêté à cette heure-là, l'édition est faite à son redémarrage.
DAILY_AT = os.environ.get("GAZETTE_DAILY_AT", "06:30")
# À l'ouverture de la page, une nouvelle édition est générée si la dernière a plus de N heures
# (ou date d'un autre jour). 0 = jamais (défaut) : seulement 6 h 30 et le bouton Actualiser.
# Une première édition est toujours générée à l'ouverture s'il n'y en a encore aucune.
AUTO_REFRESH_HOURS = float(os.environ.get("GAZETTE_AUTO_REFRESH_HOURS", "0"))

# =====================================================================================
# Fin des réglages
# =====================================================================================

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(TIMEZONE)
except Exception:  # pas de base tz sur la machine : heure locale
    TZ = datetime.now().astimezone().tzinfo

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def date_fr(d, weekday=True):
    s = "%d %s %d" % (d.day, MOIS[d.month - 1], d.year)
    if d.day == 1:
        s = "1er" + s[1:]
    return (JOURS[d.weekday()] + " " + s) if weekday else s


# -------------------------------------------------------------------------------------
# Texte
# -------------------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "noscript"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


_BOILERPLATE = [
    re.compile(r"(The post|L[’']article|Cet article) .{0,300}? (appeared first on|est apparu en premier sur|a été publié).*$",
               re.I | re.S),
    re.compile(r"\b(Read more|Lire la suite|Continue reading)\b.*$", re.I | re.S),
    re.compile(r"\[(…|\.\.\.)\]\s*$"),
]


def clean_text(raw):
    """HTML -> texte brut, sans balise ni script. Le résultat doit encore être échappé."""
    if not raw:
        return ""
    p = _TextExtractor()
    try:
        p.feed(raw)
        p.close()
        text = "".join(p.parts)
    except Exception:
        text = re.sub(r"<[^>]*>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    for rx in _BOILERPLATE:
        text = rx.sub("", text).strip()
    return text


def shorten(text, limit=SUMMARY_MAX):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.-–— ")
    return cut + "…"


def normalize(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def safe_url(url):
    url = (url or "").strip()
    return url if re.match(r"^https?://", url, re.I) else ""


def tokens(title):
    words = re.findall(r"[a-z0-9]+(?:[-+][a-z0-9]+)*", normalize(title))
    out = set()
    for w in words:
        if w in STOPWORDS:
            continue
        has_digit = any(c.isdigit() for c in w)
        if len(w) < 3 and not has_digit:
            continue
        if not has_digit and len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        w = SYNONYMS.get(w, w)
        if w not in STOPWORDS:
            out.add(w)
    return out


def _compile_words(words):
    parts = []
    for w in words:
        w = normalize(w)
        if w.endswith("*"):
            parts.append(re.escape(w[:-1]) + r"\w*")
        else:
            parts.append(re.escape(w))
    parts.sort(key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)")


def _compile_acronyms(words):
    """Sigles en majuscules, sensibles à la casse. « L'IA » et « AI's » sont reconnus, mais pas
    « J'AI » ni « N'AI » (titres français tout en majuscules)."""
    parts = sorted((re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w)) for w in words)
    parts.sort(key=len, reverse=True)
    return re.compile(r"(?<!\w)(?<![JjNn]['’])(?:" + "|".join(parts) + r")(?!\w)")


def _compile_rules(rules):
    compiled = []
    for cat, sources, words in rules:
        upper = [w for w in words if w.rstrip("*").isupper()]
        other = [w for w in words if not w.rstrip("*").isupper()]
        compiled.append((cat, frozenset(sources), _compile_acronyms(upper) if upper else None,
                         _compile_words(other) if other else None))
    return compiled


KEYWORD_RULES = _compile_rules(KEYWORD_CATEGORIES)


def keyword_category(article):
    """Catégorie de l'article après application de KEYWORD_CATEGORIES (titre seulement)."""
    title = article["title"]
    for cat, sources, acronyms, words in KEYWORD_RULES:
        if article["cat"] not in sources:
            continue
        if (acronyms and acronyms.search(title)) or (words and words.search(normalize(title))):
            return cat
    return article["cat"]


ALERT_RX = _compile_words(ALERT_WORDS)
RUMOR_RX = _compile_words(RUMOR_WORDS)


def find_alert(title):
    m = ALERT_RX.search(normalize(title))
    return m.group(0) if m else ""


def is_rumor(title):
    # On retire d'abord les expressions d'alerte (« fuite de données » n'est pas une rumeur).
    text = ALERT_RX.sub(" ", normalize(title))
    return bool(RUMOR_RX.search(text))


# -------------------------------------------------------------------------------------
# Lecture des flux
# -------------------------------------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child(el, *names):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _child_text(el, *names):
    for name in names:
        c = _child(el, name)
        if c is not None:
            t = "".join(c.itertext()).strip()
            if t:
                return t
    return ""


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d is not None:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?", s)
    if not m:
        return None
    y, mo, d, h, mi, se, tz = m.groups()
    tzinfo = timezone.utc
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        tz = tz[1:].replace(":", "")
        tzinfo = timezone(sign * timedelta(hours=int(tz[:2]), minutes=int(tz[2:])))
    try:
        return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(se or 0), tzinfo=tzinfo)
    except ValueError:
        return None


_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _fix_entities(data):
    """Remplace les entités HTML (&nbsp; …) inconnues de XML par leur forme numérique."""
    def repl(m):
        name = m.group(1)
        if name in _XML_ENTITIES:
            return m.group(0)
        ch = html.unescape("&%s;" % name)
        if ch == "&%s;" % name:
            return "&amp;" + name + ";"
        return "".join("&#%d;" % ord(c) for c in ch)
    return re.sub(r"&([A-Za-z][A-Za-z0-9]*);", repl, data)


def parse_feed(data, feed):
    """Analyse un flux RSS 2.0 / RSS 1.0 / Atom. Renvoie une liste d'articles (dict)."""
    cat, name, lang, _url = feed
    if b"<!ENTITY" in data[:4096]:
        raise ValueError("flux refusé (déclaration d'entités XML)")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        text = data.decode("utf-8", "replace")
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
        root = ET.fromstring(_fix_entities(text))
    articles = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind not in ("item", "entry"):
            continue
        title = clean_text(_child_text(el, "title"))
        if not title:
            continue
        link = ""
        if kind == "entry":
            for c in el:
                if _local(c.tag) == "link" and c.get("rel", "alternate") == "alternate":
                    link = c.get("href", "")
                    break
        if not link:
            lk = _child(el, "link")
            if lk is not None:
                link = (lk.text or "").strip() or lk.get("href", "")
        if not link:
            g = _child(el, "guid", "id")
            if g is not None and (g.text or "").startswith("http"):
                link = g.text.strip()
        summary = _child_text(el, "description", "summary") or _child_text(el, "encoded", "content")
        published = parse_date(_child_text(el, "pubdate", "published", "date", "updated", "issued"))
        articles.append({
            "title": title,
            "link": safe_url(link),
            "summary": shorten(clean_text(summary)),
            "published": published,
            "source": name,
            "cat": cat,
            "lang": lang,
        })
    return articles


SAVE_DIR = None  # --save : copie brute des flux, pour rejouer une journée avec --offline


def fetch_feed(feed, offline_dir=None):
    _cat, name, _lang, url = feed
    if offline_dir:
        path = Path(offline_dir) / (slug(name) + ".xml")
        if not path.exists():
            raise FileNotFoundError("fichier absent : %s" % path.name)
        return parse_feed(path.read_bytes(), feed)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        data = resp.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("flux trop volumineux")
    if SAVE_DIR:
        Path(SAVE_DIR).mkdir(parents=True, exist_ok=True)
        (Path(SAVE_DIR) / (slug(name) + ".xml")).write_bytes(data)
    return parse_feed(data, feed)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", normalize(name)).strip("-")


def _error_text(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP %d" % exc.code
    if isinstance(exc, urllib.error.URLError):
        return "connexion impossible (%s)" % exc.reason
    if isinstance(exc, ET.ParseError):
        return "XML illisible"
    return str(exc) or exc.__class__.__name__


def fetch_all(feeds, offline_dir=None):
    """Récupère tous les flux en parallèle. Un flux en erreur n'arrête pas les autres (F2)."""
    results, status = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(fetch_feed, f, offline_dir): f for f in feeds}
        for fut in concurrent.futures.as_completed(futures):
            f = futures[fut]
            try:
                results[f] = fut.result()
                status.append({"name": f[1], "ok": True, "count": len(results[f])})
            except Exception as exc:  # noqa: BLE001 — tout flux défaillant est simplement signalé
                status.append({"name": f[1], "ok": False, "error": _error_text(exc)})
                log("  ✗ %s : %s" % (f[1], _error_text(exc)))
    articles, seen = [], set()
    for f in feeds:  # ordre de FEEDS : le premier flux gagne en cas de doublon
        for a in results.get(f, []):
            key = a["link"] or (a["source"], a["title"])
            if key in seen:
                continue
            seen.add(key)
            articles.append(a)
    order = {f[1]: i for i, f in enumerate(feeds)}
    status.sort(key=lambda s: order.get(s["name"], 0))
    return articles, status


def recent(articles, now, hours=WINDOW_HOURS):
    start = now - timedelta(hours=hours)
    out = []
    for a in articles:
        p = a["published"]
        if p is None:
            continue  # sans date, impossible de savoir s'il est du jour
        if p > now + timedelta(hours=1):
            p = a["published"] = now  # date dans le futur : fuseau mal déclaré
        if p >= start:
            out.append(a)
    return out


# -------------------------------------------------------------------------------------
# Regroupement et classement
# -------------------------------------------------------------------------------------

def same_topic(ta, tb):
    shared = len(ta & tb)
    if shared < GROUP_MIN_SHARED:
        return False
    return shared / max(1, min(len(ta), len(tb))) >= GROUP_MIN_RATIO


def group(articles):
    """Regroupe les articles qui parlent du même sujet (F3)."""
    arts = sorted(articles, key=lambda a: a["published"])
    clusters = []  # liste de listes d'articles
    for a in arts:
        a["_tok"] = tokens(a["title"])
        best, best_score = None, 0.0
        for c in clusters:
            for b in c:
                if same_topic(a["_tok"], b["_tok"]):
                    score = len(a["_tok"] & b["_tok"]) / max(1, min(len(a["_tok"]), len(b["_tok"])))
                    if score > best_score:
                        best, best_score = c, score
        if best is None:
            clusters.append([a])
        else:
            best.append(a)
    clusters = merge_into_cores(clusters)
    return [make_topic(c) for c in clusters]


def core(cluster):
    """Mots partagés par au moins deux sites d'un groupe."""
    by_site = {}
    for a in cluster:
        by_site.setdefault(a["source"], set()).update(a["_tok"])
    if len(by_site) < 2:
        return set()
    count = {}
    for toks in by_site.values():
        for t in toks:
            count[t] = count.get(t, 0) + 1
    return {t for t, n in count.items() if n >= 2}


def merge_into_cores(clusters):
    """Rattache les petits groupes aux gros sujets multi-sites dont ils partagent le cœur."""
    clusters = sorted(clusters, key=len)
    i = 0
    while i < len(clusters):
        c = clusters[i]
        words = core(c) or set().union(*(a["_tok"] for a in c))
        best, best_n = None, GROUP_CORE_SHARED - 1
        for d in clusters[i + 1:]:
            n = len(words & core(d))
            if n > best_n:
                best, best_n = d, n
        if best is not None:
            best.extend(c)
            clusters.pop(i)
        else:
            i += 1
    return clusters


def make_topic(arts):
    sources = []
    for a in arts:
        if a["source"] not in sources:
            sources.append(a["source"])
    pref = [a for a in arts if a["lang"] == PREFERRED_LANG and a["summary"]] or \
           [a for a in arts if a["lang"] == PREFERRED_LANG] or \
           [a for a in arts if a["summary"]] or arts
    main = pref[0]  # le plus ancien = celui qui a sorti l'info
    cats = {}
    for a in arts:
        cats[a["cat"]] = cats.get(a["cat"], 0) + 1
    cat = max(cats, key=lambda k: (cats[k], k == main["cat"]))
    alert = ""
    for a in arts:
        alert = alert or find_alert(a["title"])
    rumor = sum(1 for a in arts if is_rumor(a["title"])) >= RUMOR_MIN_SHARE * len(arts)
    others, seen = [], {main["source"]}
    for a in arts:
        if a["source"] not in seen:
            seen.add(a["source"])
            others.append({"source": a["source"], "link": a["link"], "title": a["title"]})
    nsites = len(sources)
    preferred = any(a["lang"] == PREFERRED_LANG for a in arts)
    return {
        "title": main["title"],
        "link": main["link"],
        "summary": main["summary"],
        "source": main["source"],
        "lang": main["lang"],
        "cat": cat,
        "others": others,
        "nsites": nsites,
        "alert": alert,
        "rumor": rumor,
        "preferred": preferred,
        "score": SCORE_PER_SITE * nsites + (SCORE_ALERT if alert else 0)
                 + (SCORE_PREFERRED_LANG if preferred else 0),
        "latest": max(a["published"] for a in arts),
        "first": min(a["published"] for a in arts),
    }


def is_major(t):
    return t["nsites"] >= MAJOR_MIN_SITES or bool(t["alert"])  # (2 sites + alerte) inclus


def alternate(topics, limit):
    """Du plus récent au plus ancien, en alternant les catégories (F12).

    Les sujets en français sont avancés de PREFERRED_LANG_HOURS heures."""
    bonus = timedelta(hours=PREFERRED_LANG_HOURS)
    key = lambda t: t["latest"] + (bonus if t.get("preferred") else timedelta(0))  # noqa: E731
    buckets = {}
    for t in sorted(topics, key=key, reverse=True):
        buckets.setdefault(t["cat"], []).append(t)
    out = []
    while len(out) < limit and any(buckets.values()):
        cats = sorted((c for c in buckets if buckets[c]), key=lambda c: key(buckets[c][0]), reverse=True)
        for c in cats:
            if len(out) >= limit:
                break
            out.append(buckets[c].pop(0))
    return out


def classify(topics):
    """Répartit les sujets en une / majeurs / mineurs / à surveiller (F4, F5, F6)."""
    by_score = sorted(topics, key=lambda t: (t["score"], t["latest"]), reverse=True)
    watch_all = [t for t in by_score if t["rumor"]]
    news = [t for t in by_score if not t["rumor"]]
    une = news[0] if news else (watch_all.pop(0) if watch_all else None)
    rest = [t for t in news if t is not une]
    majors = [t for t in rest if is_major(t)][:MAJOR_MAX]
    if len(majors) < MAJOR_MIN:
        for t in rest:
            if len(majors) >= MAJOR_MIN:
                break
            if t not in majors and t["nsites"] >= 2:
                majors.append(t)
    taken = set(id(t) for t in majors)
    minors = alternate([t for t in rest if id(t) not in taken], MINOR_MAX)
    return {"une": une, "majors": majors, "minors": minors, "watch": watch_all[:WATCH_MAX]}


# -------------------------------------------------------------------------------------
# HTML
# -------------------------------------------------------------------------------------

E = lambda s: html.escape(str(s), quote=True)  # noqa: E731

THEME_LIGHT = ("--bg:#ece6da;--paper:#faf7f1;--card:#ffffff;--ink:#1a1916;--soft:#3b3833;--muted:#6e675b;"
               "--rule:#ddd4c4;--chip:#f1ebe0;--accent:#c0262d;--hot:#fff1ef;--ribbon:#c0262d;--lift:0%;"
               "--shadow:0 1px 2px rgba(50,35,10,.06),0 8px 22px rgba(50,35,10,.07)")
THEME_DARK = ("--bg:#0c0e12;--paper:#12151b;--card:#191d24;--ink:#eef1f5;--soft:#cdd3dc;--muted:#8c95a3;"
              "--rule:#272c35;--chip:#1d222a;--accent:#ff6b6b;--hot:#2a1519;--ribbon:#d33a40;--lift:45%;"
              "--shadow:0 1px 2px rgba(0,0,0,.45),0 10px 28px rgba(0,0,0,.4)")

THEME_COLORS = ("#faf7f1", "#12151b")   # couleur de la barre d'état : clair, sombre

CSS = """
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:17px/1.55 "Iowan Old Style","Palatino Linotype","Book Antiqua",Charter,Georgia,serif}
.sans,.dateline,.chips,h2.sec,.badge,.alert,.sites,.ribbon,.meta,.btn,footer,.editions .n{
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:inherit;text-decoration:none}
h3 a,h4 a{background:linear-gradient(currentColor,currentColor) 0 100%/0 1px no-repeat;transition:background-size .25s}
h3 a:hover,h4 a:hover{background-size:100% 1px}
/* Toute la carte ouvre l'article : le lien du titre est étiré sur la carte (lien « étiré »).
   Les liens « Aussi sur » restent au-dessus et gardent leur propre destination. */
.une,.card,.minor li,.watch li{position:relative}
.une h3 a::after,.card h3 a::after,.minor h4 a::after,.watch h4 a::after{content:"";position:absolute;inset:0;border-radius:inherit}
.une .meta,.card .meta,.minor .meta,.watch .meta{position:relative;z-index:1}
.une,.card,.minor li,.watch li{-webkit-tap-highlight-color:transparent;transition:transform .15s,box-shadow .15s,background-color .15s}
.une:hover h3 a,.card:hover h3 a,.minor li:hover h4 a,.watch li:hover h4 a{background-size:100% 1px}
.une:active,.card:active{transform:scale(.985)}
.minor li:active,.watch li:active{background:var(--chip);border-radius:0 8px 8px 0}
h3 a:focus-visible,h4 a:focus-visible{outline:none}
h3 a:focus-visible::after,h4 a:focus-visible::after{outline:2px solid var(--accent);outline-offset:2px}
.meta a{padding:4px 0}
a:focus-visible,.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.page{max-width:1120px;margin:0 auto;padding-block:18px 32px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.sheet{background:var(--paper);border:1px solid var(--rule);border-radius:18px;padding:22px 32px 30px;box-shadow:var(--shadow)}

/* En-tête */
header.mast{text-align:center;padding-bottom:16px;margin-bottom:8px;border-bottom:1px solid var(--rule)}
.dateline{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;
  font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
.dateline a:hover{color:var(--accent)}
.mast h1{font-size:clamp(40px,8.5vw,80px);line-height:.95;margin:18px 0 6px;letter-spacing:-.025em;font-weight:900}
.mast h1 span{color:var(--accent)}
.mast .tag{font-style:italic;color:var(--muted);margin:0 0 14px;font-size:16px}
.chips{display:flex;justify-content:center;flex-wrap:wrap;gap:8px;margin:0 0 14px;font-size:12.5px}
.chip{display:inline-flex;align-items:center;gap:7px;background:var(--chip);border:1px solid var(--rule);border-radius:999px;padding:5px 11px 5px 9px;color:var(--soft)}
.chip i{width:9px;height:9px;border-radius:50%;background:var(--c)}
.chip b{font-weight:700;color:var(--ink)}
.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.btn{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;line-height:1;
  color:var(--ink);background:var(--card);border:1px solid var(--rule);border-radius:999px;padding:7px 12px;margin:0}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.refresh{display:inline;margin:0}
.nw{white-space:nowrap}

/* Rubriques */
h2.sec{display:flex;align-items:center;gap:12px;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.16em;
  color:var(--accent);margin:30px 0 14px}
h2.sec::after{content:"";flex:1;height:1px;background:var(--rule)}
h2.sec small{color:var(--muted);font-weight:600;letter-spacing:.08em}

/* Étiquettes */
.badge{display:inline-block;font-size:10.5px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;line-height:1;
  color:#fff;background:var(--c);padding:5px 8px;border-radius:5px;vertical-align:middle}
.alert,.sites{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;line-height:1;
  padding:4px 7px;border-radius:5px;vertical-align:middle;margin-left:6px}
.alert{color:var(--accent);border:1px solid currentColor}
.sites{color:var(--muted);border:1px solid var(--rule)}
.meta{font-size:12.5px;line-height:1.5;color:var(--muted);margin-top:10px}
.meta a{text-decoration:underline;text-decoration-color:var(--rule);text-underline-offset:2px}
.meta a:hover{color:var(--accent);text-decoration-color:currentColor}
.tr{font-style:italic}

/* À la une */
.une{position:relative;background:var(--card);border:1px solid var(--rule);border-left:6px solid var(--c);border-radius:14px;
  padding:22px 26px 20px;box-shadow:var(--shadow)}
.une h3{font-size:clamp(28px,4.8vw,48px);line-height:1.08;margin:12px 0 12px;font-weight:800;letter-spacing:-.01em}
.une p{font-size:19px;margin:0;max-width:64ch;color:var(--soft)}
.une.hot{border-color:var(--accent);border-left-color:var(--accent);background:linear-gradient(180deg,var(--hot),var(--card) 70%)}
.ribbon{display:inline-flex;align-items:center;gap:8px;background:var(--ribbon);color:#fff;font-size:12px;font-weight:800;
  letter-spacing:.14em;text-transform:uppercase;padding:7px 12px;border-radius:7px;margin:0 0 4px}
.ribbon::before{content:"";width:8px;height:8px;border-radius:50%;background:#fff;animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.ribbon::before{animation:none}}

/* Sujets majeurs */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:18px}
.card{background:var(--card);border:1px solid var(--rule);border-top:4px solid var(--c);border-radius:12px;padding:16px 18px 14px;
  box-shadow:var(--shadow);transition:transform .2s}
.card:hover{transform:translateY(-2px)}
.card.hot{border-top-color:var(--accent);background:linear-gradient(180deg,var(--hot),var(--card) 55%)}
.card h3{font-size:21px;line-height:1.22;margin:10px 0 8px}
.card p{margin:0;font-size:15.5px;color:var(--soft)}

/* En bref + à surveiller */
.cols{display:grid;grid-template-columns:2fr 1fr;gap:30px;align-items:start}
.minor{list-style:none;margin:0;padding:0;columns:2 280px;column-gap:26px}
.minor li{break-inside:avoid;padding:2px 0 2px 14px;margin-bottom:18px;border-left:3px solid var(--c)}
.minor .badge,.watch .badge{background:none;color:var(--c);color:color-mix(in srgb,var(--c),#fff var(--lift));padding:0}
.minor h4{font-size:17px;line-height:1.28;margin:5px 0 4px}
.minor p{margin:0;font-size:14.5px;color:var(--muted)}
aside.watch{background:var(--chip);border:1px dashed var(--rule);border-radius:14px;padding:4px 18px 10px}
aside.watch h2.sec{margin-top:16px}
.watch ul{list-style:none;margin:0;padding:0}
.watch li{padding:0 0 12px;margin-bottom:12px;border-bottom:1px dashed var(--rule)}
.watch li:last-child{border:0;margin:0}
.watch h4{font-size:16px;line-height:1.3;margin:5px 0 2px;font-style:italic}
.empty{color:var(--muted);font-style:italic}

/* Pied de page */
footer{margin-top:30px;border-top:1px solid var(--rule);padding-top:12px;font-size:12.5px;line-height:1.6;color:var(--muted)}
footer .ko{color:var(--accent)}

/* Archives */
.editions{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.editions li{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;background:var(--card);border:1px solid var(--rule);
  border-radius:12px;padding:14px 18px}
.editions li:first-child{border-left:5px solid var(--accent)}
.editions .d{font-weight:700;min-width:13em}
.editions .d:hover{color:var(--accent)}
.editions .u{color:var(--muted);flex:1;min-width:14em;font-style:italic}

/* Bouton VF / VO (sans JavaScript) */
.vo-switch{position:absolute;opacity:0;pointer-events:none}
.vo{display:none}
.vo-switch:checked ~ * .vo{display:inline}
.vo-switch:checked ~ * .vf{display:none}
.vo-switch:focus-visible ~ header .vo-btn{outline:2px solid var(--accent)}

@media (max-width:720px){
  .page{padding-block:10px 24px;padding-left:max(8px,env(safe-area-inset-left,0px));padding-right:max(8px,env(safe-area-inset-right,0px))}
  .sheet{padding:16px 16px 22px;border-radius:14px}
  .cols{grid-template-columns:1fr}
  .dateline{justify-content:center}
  .une{padding:18px 18px 16px}
  .une p{font-size:17.5px}
}
"""


def theme_css():
    """Couleurs selon THEME : « auto » suit le téléphone, sinon toujours sombre ou toujours clair."""
    if THEME == "dark":
        return ":root{%s;color-scheme:dark}" % THEME_DARK
    if THEME == "light":
        return ":root{%s;color-scheme:light}" % THEME_LIGHT
    return (":root{%s;color-scheme:light}"
            "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){%s;color-scheme:dark}}"
            ":root[data-theme=dark]{%s;color-scheme:dark}" % (THEME_LIGHT, THEME_DARK, THEME_DARK))


def page(title, body):
    scheme = {"dark": "dark", "light": "light"}.get(THEME, "light dark")
    return ("<!doctype html>\n<html lang=\"fr\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<meta name=\"color-scheme\" content=\"%s\">\n"
            "<meta name=\"robots\" content=\"noindex\">\n%s"
            "<title>%s</title>\n<style>%s%s</style>\n</head>\n<body>\n<div class=\"page\"><div class=\"sheet\">\n%s\n"
            "</div></div>\n</body>\n</html>\n") % (scheme, app_head(), E(title), theme_css(), CSS, body)


def app_head():
    """Balises pour l'ajout à l'écran d'accueil (icône, nom, plein écran)."""
    light, dark = THEME_COLORS
    colors = {"dark": [(dark, "")], "light": [(light, "")]}.get(
        THEME, [(light, ' media="(prefers-color-scheme: light)"'), (dark, ' media="(prefers-color-scheme: dark)"')])
    return ("".join('<meta name="theme-color" content="%s"%s>\n' % c for c in colors) +
            '<link rel="manifest" href="/manifest.webmanifest">\n'
            '<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">\n'
            '<link rel="apple-touch-icon" href="/apple-touch-icon.png">\n'
            '<meta name="apple-mobile-web-app-capable" content="yes">\n'
            '<meta name="mobile-web-app-capable" content="yes">\n'
            '<meta name="apple-mobile-web-app-title" content="Gazette">\n')


def cat_line():
    """Sous-titre « Tech · Gaming · … » tiré de CATEGORIES."""
    return " · ".join(label for label, _color in CATEGORIES.values())


def cat_color(cat):
    return CATEGORIES.get(cat, (cat, "#6b7280"))[1]


def badge(cat):
    label, color = CATEGORIES.get(cat, (cat, "#6b7280"))
    return '<span class="badge" style="--c:%s">%s</span>' % (E(color), E(label))


def vfvo(vf, vo):
    """Texte traduit + original ; le bouton VF/VO (CSS pur) choisit lequel s'affiche."""
    if not vf or vf == vo:
        return E(vo)
    return '<span class="vf">%s</span><span class="vo" lang="en">%s</span>' % (E(vf), E(vo))


def headline(t, tag):
    title = vfvo(t.get("title_vf"), t["title"])
    if t["link"]:
        title = '<a href="%s" rel="noopener noreferrer">%s</a>' % (E(t["link"]), title)
    return "<%s>%s</%s>" % (tag, title, tag)


def meta(t):
    parts = ["Source : " + E(t["source"])]
    if t.get("title_vf"):
        parts[0] += ' <span class="vf tr">(traduit)</span>'
    if t["others"]:
        links = []
        for o in t["others"]:
            if o["link"]:
                links.append('<a href="%s" rel="noopener noreferrer" title="%s">%s</a>'
                             % (E(o["link"]), E(o["title"]), E(o["source"])))
            else:
                links.append(E(o["source"]))
        parts.append("Aussi sur : " + ", ".join(links))
    return '<div class="meta">%s</div>' % " · ".join(parts)


def tags(t, with_alert=True):
    s = badge(t["cat"])
    if with_alert and t["alert"]:
        s += '<span class="alert" title="%s">Alerte</span>' % E(t["alert"])
    if t["nsites"] >= 2:
        s += '<span class="sites">%d sites</span>' % t["nsites"]
    return s


def summary(t):
    return "<p>%s</p>" % vfvo(t.get("summary_vf"), t["summary"]) if t["summary"] else ""


def chips(sections):
    count = {}
    for t in all_topics(sections):
        count[t["cat"]] = count.get(t["cat"], 0) + 1
    out = []
    for key, (label, color) in CATEGORIES.items():
        out.append('<span class="chip" style="--c:%s"><i></i>%s <b>%d</b></span>' % (E(color), E(label), count.get(key, 0)))
    return '<div class="chips">%s</div>' % "".join(out)


def masthead(day, right, generated=None, translated=False, sections=None):
    when = "Édition de %d h %02d" % (generated.hour, generated.minute) if generated else "Édition du matin"
    tools = []
    if translated:
        tools.append('<label for="vo" class="btn vo-btn" title="Basculer entre traduction et version originale">'
                     '<span class="vf">VF → VO</span><span class="vo">VO → VF</span></label>')
    if REFRESH_PATH:
        tools.append('<form class="refresh" method="post" action="%s"><button class="btn" type="submit">'
                     '↻ Actualiser</button></form>' % E(REFRESH_PATH))
    return ('<header class="mast"><div class="dateline"><span><span class="nw">%s</span> · <span class="nw">%s</span></span><span class="tools">%s</span></div>'
            '<h1><a href="../">La Gazette <span>du</span> Geek</a></h1>'
            '<p class="tag">%s</p>%s'
            '<div class="dateline">%s</div></header>') % (
        E(date_fr(day).capitalize()), E(when), "".join(tools), E(cat_line()),
        chips(sections) if sections else "", right)


def render_edition(day, sections, status, stats, prev_day=None, generated=None, translate_error=""):
    nav = ['<a href="../index.html">Toutes les éditions →</a>']
    if prev_day:
        nav.insert(0, '<a href="../%s/">← %s</a>' % (E(prev_day.isoformat()), E(date_fr(prev_day, weekday=False))))
    else:
        nav.insert(0, "<span></span>")
    translated = any(t.get("title_vf") for t in all_topics(sections))
    b = ['<input type="checkbox" id="vo" class="vo-switch" aria-label="Version originale">'] if translated else []
    b.append(masthead(day, "".join(nav), generated, translated, sections))

    une = sections["une"]
    b.append('<h2 class="sec">À la une</h2>')
    if une:
        hot = bool(une["alert"])
        ribbon = '<div class="ribbon" title="%s">Alerte</div><br>' % E(une["alert"]) if hot else ""
        b.append('<article class="une%s" style="--c:%s">%s%s%s%s%s</article>' % (
            " hot" if hot else "", E(cat_color(une["cat"])), ribbon, tags(une, with_alert=False),
            headline(une, "h3"), summary(une), meta(une)))
    else:
        b.append('<p class="empty">Aucun article récupéré ce matin.</p>')

    if sections["majors"]:
        b.append('<h2 class="sec">Les sujets majeurs <small>%d</small></h2><div class="grid">' % len(sections["majors"]))
        for t in sections["majors"]:
            b.append('<article class="card%s" style="--c:%s">%s%s%s%s</article>' % (
                " hot" if t["alert"] else "", E(cat_color(t["cat"])), tags(t), headline(t, "h3"), summary(t), meta(t)))
        b.append("</div>")

    b.append('<div class="cols"><section>')
    b.append('<h2 class="sec">En bref</h2>')
    if sections["minors"]:
        b.append('<ul class="minor">')
        for t in sections["minors"]:
            b.append('<li style="--c:%s">%s%s%s%s</li>' % (
                E(cat_color(t["cat"])), tags(t), headline(t, "h4"), summary(t), meta(t)))
        b.append("</ul>")
    else:
        b.append('<p class="empty">Rien d\'autre ce matin.</p>')
    b.append('</section><aside class="watch"><h2 class="sec">À surveiller</h2>')
    if sections["watch"]:
        b.append("<ul>")
        for t in sections["watch"]:
            b.append("<li>%s%s%s</li>" % (badge(t["cat"]), headline(t, "h4"), meta(t)))
        b.append("</ul>")
    else:
        b.append('<p class="empty">Pas de rumeur notable.</p>')
    b.append("</aside></div>")

    ko = [s for s in status if not s["ok"]]
    foot = ["%d articles · %d sujets · %d sources sur %d" % (
        stats["articles"], stats["topics"], len(status) - len(ko), len(status))]
    if ko:
        foot.append('<span class="ko">Flux en erreur : %s</span>' % ", ".join(
            "%s (%s)" % (E(s["name"]), E(s["error"])) for s in ko))
    if translate_error:
        foot.append('<span class="ko">Traduction indisponible : %s</span>' % E(translate_error))
    if generated:
        foot.append("Générée le %s à %s" % (E(date_fr(generated.date(), weekday=False)), E(generated.strftime("%H:%M"))))
    b.append("<footer>%s</footer>" % "<br>".join(foot))
    return page("La Gazette du Geek — " + date_fr(day), "\n".join(b))


def list_editions(out_dir):
    days = []
    if out_dir.is_dir():
        for p in out_dir.iterdir():
            if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name) and (p / "index.html").exists():
                try:
                    days.append(datetime.strptime(p.name, "%Y-%m-%d").date())
                except ValueError:
                    pass
    return sorted(days, reverse=True)


def render_index(out_dir):
    days = list_editions(out_dir)
    items = []
    for d in days:
        une = ""
        try:
            data = json.loads((out_dir / d.isoformat() / "edition.json").read_text("utf-8"))
            une = data.get("une_vf") or data.get("une", "") or ""
        except (OSError, ValueError):
            pass
        items.append('<li><a class="d" href="%s/">%s</a><span class="u">%s</span></li>'
                     % (E(d.isoformat()), E(date_fr(d).capitalize()), E(une)))
    body = ('<header class="mast"><div class="dateline"><span>Archives</span><span>%d éditions</span></div>'
            '<h1>La Gazette <span>du</span> Geek</h1><p class="tag">%s</p><div class="dateline" style="justify-content:center">'
            '<span>%s</span></div></header><h2 class="sec">Éditions disponibles</h2>') % (
                len(days), E(cat_line()),
                ('<a href="%s/">Lire l\'édition du jour →</a>' % E(days[0].isoformat())) if days else "")
    body += '<ul class="editions">%s</ul>' % "".join(items) if items else '<p class="empty">Aucune édition pour l\'instant.</p>'
    return page("La Gazette du Geek — éditions", body)


def icon_png(size):
    """Icône de l'appli (une page de journal sur fond rouge), en PNG, sans bibliothèque."""
    import struct
    import zlib
    red, paper, ink, line = (192, 38, 45), (250, 247, 241), (26, 25, 22), (196, 187, 172)

    def box(x, y, x0, y0, x1, y1, r=0.0):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    shapes = [((0.25, 0.21, 0.75, 0.79, 0.035), paper), ((0.31, 0.27, 0.69, 0.355, 0), ink),
              ((0.31, 0.41, 0.47, 0.57, 0.01), red)]
    for y0, x0 in ((0.42, 0.51), (0.48, 0.51), (0.54, 0.51), (0.62, 0.31), (0.68, 0.31)):
        shapes.append(((x0, y0, 0.69 if y0 < 0.66 else 0.58, y0 + 0.03, 0), line))
    rows = []
    for py in range(size):
        y = (py + 0.5) / size
        row = bytearray(b"\0")
        for px in range(size):
            x = (px + 0.5) / size
            color = red
            for (x0, y0, x1, y1, r), c in shapes:
                if box(x, y, x0, y0, x1, y1, r):
                    color = c
            row += bytes(color)
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


APP_ICONS = {"icon-512.png": 512, "icon-192.png": 192, "apple-touch-icon.png": 180}


def write_app_files(out_dir):
    """Manifeste et icônes pour « Ajouter à l'écran d'accueil »."""
    light, dark = THEME_COLORS
    color = dark if THEME == "dark" else light
    manifest = {
        "name": "La Gazette du Geek", "short_name": "Gazette", "lang": "fr",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": color, "theme_color": color,
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }
    (out_dir / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    for name, size in APP_ICONS.items():
        if not (out_dir / name).exists():
            (out_dir / name).write_bytes(icon_png(size))


def cleanup(out_dir, keep=KEEP_EDITIONS):
    """Supprime les éditions au-delà des `keep` plus récentes (F8)."""
    removed = []
    for d in list_editions(out_dir)[keep:]:
        shutil.rmtree(out_dir / d.isoformat())
        removed.append(d.isoformat())
    return removed


# -------------------------------------------------------------------------------------
# Traduction (LibreTranslate)
# -------------------------------------------------------------------------------------

def all_topics(sections):
    out = [sections["une"]] if sections.get("une") else []
    for k in ("majors", "minors", "watch"):
        out.extend(sections.get(k, []))
    return out


def _libretranslate(texts, source, target):
    payload = {"q": texts, "source": source, "target": target, "format": "text"}
    if TRANSLATE_KEY:
        payload["api_key"] = TRANSLATE_KEY
    req = urllib.request.Request(TRANSLATE_URL.rstrip("/") + "/translate", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "gazette"}, method="POST")
    with urllib.request.urlopen(req, timeout=TRANSLATE_TIMEOUT) as resp:
        data = json.loads(resp.read(MAX_FEED_BYTES).decode("utf-8"))
    out = data.get("translatedText") if isinstance(data, dict) else None
    if isinstance(out, str) and len(texts) == 1:
        out = [out]
    if not isinstance(out, list) or len(out) != len(texts) or not all(isinstance(x, str) for x in out):
        raise ValueError("réponse inattendue du serveur de traduction")
    return out


def translate_sections(sections):
    """Ajoute title_vf / summary_vf aux sujets affichés qui ne sont pas en PREFERRED_LANG.

    Renvoie un message d'erreur (vide si tout va bien). En cas d'échec, l'édition garde les originaux.
    """
    if not TRANSLATE_URL:
        return ""
    jobs = {}  # langue source -> [(sujet, champ, texte)]
    for t in all_topics(sections):
        if t.get("lang", PREFERRED_LANG) == PREFERRED_LANG:
            continue
        for field in ("title", "summary"):
            if t.get(field):
                jobs.setdefault(t["lang"], []).append((t, field, t[field]))
    if not jobs:
        return ""
    t0, n = time.monotonic(), 0
    try:
        for lang, items in jobs.items():
            for i in range(0, len(items), TRANSLATE_BATCH):
                chunk = items[i:i + TRANSLATE_BATCH]
                done = _libretranslate([x[2] for x in chunk], lang, PREFERRED_LANG)
                for (t, field, _orig), vf in zip(chunk, done):
                    vf = re.sub(r"\s+", " ", clean_text(vf)).strip()
                    if vf:
                        t[field + "_vf"] = vf
                        n += 1
    except Exception as exc:  # noqa: BLE001 — la traduction est un bonus, jamais bloquante
        log("traduction : échec (%s)" % _error_text(exc))
        return _error_text(exc)
    log("traduction : %d textes en %.1f s" % (n, time.monotonic() - t0))
    return ""


# -------------------------------------------------------------------------------------
# Git et notification
# -------------------------------------------------------------------------------------

def git_commit(out_dir, day, push=GIT_PUSH):
    repo = HERE

    def git(*args, timeout=60):
        return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, timeout=timeout)

    try:
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            log("git : %s n'est pas un dépôt, commit ignoré" % repo)
            return False
        try:
            rel = str(out_dir.resolve().relative_to(repo))
        except ValueError:
            log("git : %s est hors du dépôt, commit ignoré" % out_dir)
            return False
        git("add", "-A", "--", rel)
        if git("diff", "--cached", "--quiet").returncode == 0:
            log("git : rien à enregistrer")
            return True
        r = git("commit", "-m", "Édition du %s" % day.isoformat())
        if r.returncode != 0:
            log("git commit : %s" % (r.stderr.strip() or r.stdout.strip()))
            return False
        if push:
            r = git("push", "--quiet", timeout=90)
            if r.returncode != 0:
                log("git push : %s" % r.stderr.strip())
                return False
        log("git : édition enregistrée%s" % (" et poussée" if push else ""))
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("git : %s" % exc)
        return False


def notify(day, sections):
    if not NTFY_TOPIC:
        log("ntfy : pas de sujet configuré (GAZETTE_NTFY_TOPIC), notification ignorée")
        return False
    une = sections["une"]
    lines = [(une.get("title_vf") or une["title"]) if une else "Pas d'article ce matin."]
    if sections["majors"]:
        lines.append("+ %d sujet%s majeur%s" % ((len(sections["majors"]),) + (("s", "s") if len(sections["majors"]) > 1 else ("", ""))))
    payload = {
        "topic": NTFY_TOPIC,
        "title": "La Gazette du Geek · " + date_fr(day),
        "message": "\n".join(lines),
        "click": BASE_URL.rstrip("/") + "/" + day.isoformat() + "/",
        "tags": ["newspaper"],
    }
    headers = {"Content-Type": "application/json", "User-Agent": "gazette"}
    if NTFY_TOKEN:
        headers["Authorization"] = "Bearer " + NTFY_TOKEN
    req = urllib.request.Request(NTFY_SERVER.rstrip("/") + "/", data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        log("ntfy : notification envoyée")
        return True
    except Exception as exc:  # noqa: BLE001
        log("ntfy : échec (%s)" % _error_text(exc))
        return False


# -------------------------------------------------------------------------------------
# Programme principal
# -------------------------------------------------------------------------------------

def topic_json(t):
    if not t:
        return None
    d = {k: v for k, v in t.items() if k not in ("latest", "first")}
    d["latest"] = t["latest"].isoformat()
    return d


def build(now, out_dir, offline_dir=None, feeds=FEEDS):
    t0 = time.monotonic()
    day = now.astimezone(TZ).date()
    log("Gazette du %s : lecture de %d flux…" % (day.isoformat(), len(feeds)))
    articles, status = fetch_all(feeds, offline_dir)
    fresh = recent(articles, now)
    for a in fresh:
        a["cat"] = keyword_category(a)
    topics = group(fresh)
    sections = classify(topics)
    stats = {"articles": len(fresh), "topics": len(topics)}
    translate_error = translate_sections(sections)

    out_dir.mkdir(parents=True, exist_ok=True)
    previous = [d for d in list_editions(out_dir) if d < day]
    ed_dir = out_dir / day.isoformat()
    ed_dir.mkdir(exist_ok=True)
    (ed_dir / "index.html").write_text(
        render_edition(day, sections, status, stats, previous[0] if previous else None, now.astimezone(TZ),
                       translate_error), "utf-8")
    (ed_dir / "edition.json").write_text(json.dumps({
        "date": day.isoformat(),
        "generated": now.isoformat(),
        "une": sections["une"]["title"] if sections["une"] else "",
        "une_vf": (sections["une"].get("title_vf") or "") if sections["une"] else "",
        "stats": stats,
        "feeds": status,
        "sections": {k: ([topic_json(t) for t in v] if isinstance(v, list) else topic_json(v))
                     for k, v in sections.items()},
    }, ensure_ascii=False, indent=1), "utf-8")
    removed = cleanup(out_dir)
    (out_dir / "index.html").write_text(render_index(out_dir), "utf-8")
    write_app_files(out_dir)
    log("%d articles, %d sujets · une : %s · %d majeurs, %d mineurs, %d à surveiller · %.1f s%s" % (
        stats["articles"], stats["topics"], sections["une"]["title"] if sections["une"] else "—",
        len(sections["majors"]), len(sections["minors"]), len(sections["watch"]), time.monotonic() - t0,
        (" · supprimées : " + ", ".join(removed)) if removed else ""))
    log("→ %s" % (ed_dir / "index.html"))
    return day, sections


class RunLock:
    """Empêche deux générations simultanées (timer de 6 h 30 + bouton Actualiser)."""

    def __init__(self, out_dir):
        self.path = out_dir / ".gazette.lock"
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "w")
        try:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        except ImportError:  # Windows : pas de verrou
            pass
        return self

    def __exit__(self, *exc):
        self.fh.close()


def run_edition(now, out_dir, offline_dir=None, commit=True, notify_phone=True):
    with RunLock(out_dir):
        day, sections = build(now, out_dir, offline_dir)
        if GIT_COMMIT and commit:
            git_commit(out_dir, day)
    if notify_phone:
        notify(day, sections)
    return day, sections


STATIC_TYPES = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
                ".png": "image/png", ".webmanifest": "application/manifest+json"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
                               "manifest-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def last_generated(out_dir):
    """Date de génération de la dernière édition, ou None."""
    days = list_editions(out_dir)
    if not days:
        return None
    try:
        data = json.loads((out_dir / days[0].isoformat() / "edition.json").read_text("utf-8"))
        return parse_date(data.get("generated", ""))
    except (OSError, ValueError):
        return None


def edition_age(out_dir, now=None):
    """Âge (en heures) de la dernière édition, ou None s'il n'y en a pas. Une édition d'un autre jour
    compte comme périmée (âge infini)."""
    now = now or datetime.now(timezone.utc)
    days = list_editions(out_dir)
    if not days:
        return None
    if days[0] != now.astimezone(TZ).date():
        return float("inf")
    generated = last_generated(out_dir)
    if generated is None:
        return float("inf")
    return (now - generated).total_seconds() / 3600


def daily_slot(now, at=DAILY_AT):
    """(dernier passage prévu, prochain passage prévu) autour de `now`, à l'heure `at` de Paris."""
    hour, minute = (int(x) for x in at.split(":"))
    local = now.astimezone(TZ)
    today = datetime(local.year, local.month, local.day, hour, minute, tzinfo=TZ)
    if today <= local:
        return today, datetime.combine(today.date() + timedelta(days=1), today.timetz())
    return datetime.combine(today.date() - timedelta(days=1), today.timetz()), today


def daily_missed(out_dir, now=None, at=DAILY_AT):
    """Vrai si l'édition du dernier passage prévu n'a pas été faite (service arrêté à cette heure-là)."""
    now = now or datetime.now(timezone.utc)
    last, _next = daily_slot(now, at)
    generated = last_generated(out_dir)
    return generated is None or generated < last


def serve(out_dir, port=SERVE_PORT, host=SERVE_HOST):
    """Sert la gazette (pages, icônes) et le bouton « Actualiser ».

    - chaque jour à DAILY_AT : nouvelle édition (rattrapée au démarrage si elle a été manquée).
    - GET /            : dernière édition (générée à ce moment-là s'il n'y en a aucune, ou si
                         AUTO_REFRESH_HOURS est réglé et qu'elle est trop vieille).
    - POST REFRESH_PATH : relance une génération (une fois toutes les REFRESH_COOLDOWN s au plus).
    - GET /healthz     : « ok » (surveillance Docker), ne déclenche rien.
    Seuls les fichiers de out_dir sont servis, sans liste de dossier ni fichier caché.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import unquote, urlsplit

    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve()
    refresh = REFRESH_PATH or "/actualiser"
    state = {"running": False, "last": float("-inf")}
    lock = threading.Lock()

    def job():
        try:
            run_edition(datetime.now(timezone.utc), out_dir, notify_phone=False)
        except Exception as exc:  # noqa: BLE001
            log("actualisation : échec (%s)" % exc)
        finally:
            with lock:
                state["running"] = False
                state["last"] = time.monotonic()

    def start_job(reason):
        with lock:
            start = not state["running"] and time.monotonic() - state["last"] >= REFRESH_COOLDOWN
            if start:
                state["running"] = True
        if start:
            log("actualisation : %s" % reason)
            threading.Thread(target=job, daemon=True).start()
        return start

    def latest_url():
        days = list_editions(out_dir)
        return "/%s/" % days[0].isoformat() if days else None

    def waiting_page():
        back = latest_url()
        body = ('<header class="mast"><h1>La Gazette <span>du</span> Geek</h1></header>'
                '<h2 class="sec">Actualisation en cours</h2>'
                '<p>Lecture des flux… la page se recharge toute seule dans quelques secondes.</p>')
        if back:
            body += '<p class="meta"><a href="%s">Revenir à la dernière édition</a></p>' % E(back)
        return page("Actualisation…", body).replace(
            "<head>\n", '<head>\n<meta http-equiv="refresh" content="3;url=/">\n', 1)

    class Handler(BaseHTTPRequestHandler):
        server_version = "gazette"
        sys_version = ""

        def _send(self, code, body=b"", location=None, ctype="text/html; charset=utf-8", cache="no-cache",
                  head=False):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", cache)
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def _file(self, path, head):
            rel = unquote(path).lstrip("/")
            parts = [x for x in rel.split("/") if x]
            if any(x.startswith(".") or x in ("..",) or "\\" in x for x in parts):
                return self._send(404, "Introuvable", head=head)
            target = root.joinpath(*parts) if parts else root
            if target.is_dir():
                if not path.endswith("/"):
                    return self._send(301, "", path + "/", head=head)
                target = target / "index.html"
            try:
                target = target.resolve()
                target.relative_to(root)
                data = target.read_bytes() if target.is_file() else None
            except (OSError, ValueError):
                data = None
            ctype = STATIC_TYPES.get(target.suffix)
            if data is None or ctype is None:
                return self._send(404, "Introuvable", head=head)
            cache = "max-age=86400" if target.suffix == ".png" else "no-cache"
            self._send(200, data, ctype=ctype, cache=cache, head=head)

        def do_POST(self):
            if urlsplit(self.path).path != refresh or not REFRESH_PATH:
                return self._send(404, "Introuvable")
            start_job("demandée")
            self._send(303, "", refresh)

        def do_GET(self, head=False):
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self._send(200, "ok", ctype="text/plain; charset=utf-8", cache="no-store", head=head)
            with lock:
                running = state["running"]
            if path == refresh:
                if running:
                    return self._send(200, waiting_page(), cache="no-store", head=head)
                return self._send(303, "", "/", head=head)
            if path == "/":
                age = edition_age(out_dir)
                stale = age is None or (AUTO_REFRESH_HOURS > 0 and age >= AUTO_REFRESH_HOURS)
                if running or (stale and start_job("édition de plus de %g h" % AUTO_REFRESH_HOURS
                                                   if age is not None else "première édition")):
                    return self._send(200, waiting_page(), cache="no-store", head=head)
                url = latest_url()
                if url:
                    return self._send(303, "", url, cache="no-store", head=head)
                return self._send(503, page("La Gazette du Geek", '<h2 class="sec">Pas encore d\'édition</h2>'
                                            '<p>La génération a échoué : réessayez dans deux minutes.</p>'),
                                  cache="no-store", head=head)
            return self._file(path, head)

        def do_HEAD(self):
            self.do_GET(head=True)

        def log_message(self, fmt, *args):
            pass

    def daily():
        """Édition quotidienne à DAILY_AT, plus rattrapage si le service était arrêté à l'heure prévue."""
        if daily_missed(out_dir, at=DAILY_AT):
            start_job("édition de %s manquée, rattrapage" % DAILY_AT)
        while True:
            _last, nxt = daily_slot(datetime.now(timezone.utc), DAILY_AT)
            while datetime.now(timezone.utc) < nxt:  # attente par petits pas : suit les changements d'heure
                time.sleep(min(60.0, max(1.0, (nxt - datetime.now(timezone.utc)).total_seconds())))
            if not start_job("édition de %s" % DAILY_AT):
                time.sleep(REFRESH_COOLDOWN + 1)  # une génération était en cours : on réessaie une fois
                start_job("édition de %s" % DAILY_AT)

    if DAILY_AT:
        daily_slot(datetime.now(timezone.utc), DAILY_AT)  # erreur immédiate si l'heure est mal écrite
        threading.Thread(target=daily, daemon=True).start()
        log("Édition quotidienne à %s (heure de Paris)" % DAILY_AT)

    httpd = ThreadingHTTPServer((host, port), Handler)
    log("Gazette servie sur http://%s:%d/ (dossier %s)" % (host, port, out_dir))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def check(offline_dir=None):
    _articles, status = fetch_all(FEEDS, offline_dir)
    width = max(len(s["name"]) for s in status)
    for s in status:
        print("%s  %s  %s" % ("OK " if s["ok"] else "KO ", s["name"].ljust(width),
                              ("%d articles" % s["count"]) if s["ok"] else s["error"]))
    return 0 if all(s["ok"] for s in status) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Génère La Gazette du Geek.")
    ap.add_argument("--check", action="store_true", help="tester les flux puis quitter")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="dossier de sortie (défaut : out/)")
    ap.add_argument("--offline", metavar="DOSSIER", help="lire les flux depuis DOSSIER/<nom-du-flux>.xml")
    ap.add_argument("--save", metavar="DOSSIER", help="enregistrer les flux bruts dans DOSSIER (rejouables avec --offline)")
    ap.add_argument("--now", help="date/heure de génération ISO (tests)")
    ap.add_argument("--no-git", action="store_true", help="ne pas faire de commit")
    ap.add_argument("--no-notify", action="store_true", help="ne pas envoyer de notification")
    ap.add_argument("--serve", action="store_true", help="lancer le service du bouton « Actualiser »")
    args = ap.parse_args(argv)

    global SAVE_DIR
    SAVE_DIR = args.save
    if args.check:
        return check(args.offline)
    now = parse_date(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        ap.error("--now : date illisible")
    out_dir = args.out.resolve()
    if args.serve:
        return serve(out_dir)
    run_edition(now, out_dir, args.offline, commit=not args.no_git, notify_phone=not args.no_notify)
    return 0


if __name__ == "__main__":
    sys.exit(main())
