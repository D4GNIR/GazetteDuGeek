"""Tests hors ligne : python3 -m unittest discover tests"""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gazette as g  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
NOW = datetime(2026, 11, 13, 5, 30, tzinfo=timezone.utc)  # 6 h 30 à Paris


def art(title, source, cat="tech", lang="en", hour=1):
    return {"title": title, "link": "https://example.org/" + g.slug(title), "summary": "", "source": source,
            "cat": cat, "lang": lang, "published": datetime(2026, 11, 13, hour, tzinfo=timezone.utc)}


class TextTests(unittest.TestCase):
    def test_clean_text_strips_scripts_and_boilerplate(self):
        t = g.clean_text("<p>Bonjour&nbsp;<b>monde</b><script>alert(1)</script></p><p>The post X appeared first on Y.</p>")
        self.assertEqual(t, "Bonjour monde")

    def test_safe_url(self):
        self.assertEqual(g.safe_url("javascript:alert(1)"), "")
        self.assertEqual(g.safe_url(" https://a.b/c "), "https://a.b/c")

    def test_keywords(self):
        self.assertEqual(g.find_alert("Grosse panne chez Microsoft"), "panne")
        self.assertTrue(g.find_alert("New CVE-2026-1234 exploited"))
        self.assertFalse(g.find_alert("Le panneau solaire du futur"))
        self.assertTrue(g.is_rumor("PS6 release date leak"))
        self.assertTrue(g.is_rumor("Rumeur : un nouveau Zelda"))
        self.assertFalse(g.is_rumor("Fuite de données chez Sega"))  # alerte, pas rumeur

    def test_dates(self):
        self.assertEqual(g.parse_date("Thu, 12 Nov 2026 21:00:00 GMT"), datetime(2026, 11, 12, 21, tzinfo=timezone.utc))
        self.assertEqual(g.parse_date("2026-11-13T04:00:00.000-05:00"), datetime(2026, 11, 13, 9, tzinfo=timezone.utc))
        self.assertEqual(g.parse_date("2026-11-13T04:00:00Z"), datetime(2026, 11, 13, 4, tzinfo=timezone.utc))
        self.assertIsNone(g.parse_date("n'importe quoi"))

    def test_date_fr(self):
        self.assertEqual(g.date_fr(date(2026, 11, 13)), "vendredi 13 novembre 2026")
        self.assertEqual(g.date_fr(date(2026, 12, 1), weekday=False), "1er décembre 2026")


class GroupingTests(unittest.TestCase):
    def test_same_story_across_languages(self):
        topics = g.group([
            art("Panne géante de Microsoft : Outlook et Teams inaccessibles", "Numerama", lang="fr"),
            art("Microsoft Outlook and Teams outage hits millions", "The Verge"),
            art("Bambu Lab unveils a large-format printer", "3DPrint.com", cat="3d"),
        ])
        self.assertEqual(sorted(t["nsites"] for t in topics), [1, 2])
        big = max(topics, key=lambda t: t["nsites"])
        self.assertEqual(big["source"], "Numerama")  # titre en français préféré

    def test_core_merge(self):
        topics = g.group([
            art("Massive Microsoft 365 outage disrupts Outlook, Teams, Copilot", "BleepingComputer", hour=1),
            art("Microsoft's massive Outlook outage continues", "The Verge", hour=2),
            art("Xbox : la panne Microsoft empêche les joueurs de jouer", "Jeuxvideo.com", "gaming", "fr", hour=3),
            art("Microsoft confirms Windows activation failures", "BleepingComputer", hour=4),
        ])
        self.assertEqual(sorted(t["nsites"] for t in topics), [1, 3])

    def test_unrelated_stay_apart(self):
        topics = g.group([art("Apple TV has a YouTube problem", "The Verge"),
                          art("Tesla Model 3 airbag recall", "Numerama")])
        self.assertEqual(len(topics), 2)


class ClassifyTests(unittest.TestCase):
    def topic(self, title, nsites=1, alert="", rumor=False, cat="tech", hour=1):
        return {"title": title, "nsites": nsites, "alert": alert, "rumor": rumor, "cat": cat,
                "score": g.SCORE_PER_SITE * nsites + (g.SCORE_ALERT if alert else 0),
                "latest": datetime(2026, 11, 13, hour, tzinfo=timezone.utc)}

    def test_rules(self):
        ts = [self.topic("big", 4, "panne"), self.topic("three", 3), self.topic("alert", 1, "faille"),
              self.topic("two", 2), self.topic("rumor", 5, rumor=True)] + \
             [self.topic("m%d" % i, cat=c, hour=i) for i, c in enumerate(["tech"] * 8 + ["gaming", "3d"])]
        s = g.classify(ts)
        self.assertEqual(s["une"]["title"], "big")
        # 2 majeurs seulement -> complété par le sujet à 2 sites
        self.assertEqual([t["title"] for t in s["majors"]], ["three", "alert", "two"])
        self.assertEqual([t["title"] for t in s["watch"]], ["rumor"])
        self.assertLessEqual(len(s["minors"]), g.MINOR_MAX)
        # alternance : les catégories rares remontent dans les mineurs
        self.assertIn("gaming", [t["cat"] for t in s["minors"][:3]])
        self.assertIn("3d", [t["cat"] for t in s["minors"][:3]])

    def test_fill_majors_with_two_sites(self):
        s = g.classify([self.topic("a", 2), self.topic("b", 2), self.topic("c", 2), self.topic("d", 1)])
        self.assertEqual(s["une"]["title"], "a")
        self.assertEqual(sorted(t["title"] for t in s["majors"]), ["b", "c"])


class EndToEndTests(unittest.TestCase):
    def test_offline_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for i in range(1, 16):  # 15 vieilles éditions -> nettoyage à 14
                d = out / ("2026-10-%02d" % i)
                d.mkdir()
                (d / "index.html").write_text("x")
            day, s = g.build(NOW, out, offline_dir=FIXTURES)
            self.assertEqual(day, date(2026, 11, 13))
            page = (out / "2026-11-13" / "index.html").read_text("utf-8")
            # Une = la panne, reprise par 3 sites
            self.assertEqual(s["une"]["nsites"], 3)
            self.assertIn("Panne géante de Microsoft", page)
            # rumeur dans « à surveiller »
            self.assertIn("PS6", " ".join(t["title"] for t in s["watch"]))
            # contenu des flux échappé, aucun script ni lien javascript:
            self.assertNotIn("<script", page)
            self.assertNotIn("<img", page)
            self.assertNotIn("javascript:", page)
            self.assertNotIn("appeared first", page)
            # vieil article exclu
            self.assertNotIn("Un vieil article", page)
            # flux en panne signalés en bas de page (Blender cassé, autres absents)
            self.assertIn("Flux en erreur", page)
            self.assertIn("Blender (XML illisible)", page)
            # historique : 14 éditions gardées, accueil à jour
            self.assertEqual(len(g.list_editions(out)), 14)
            self.assertFalse((out / "2026-10-01").exists())
            index = (out / "index.html").read_text("utf-8")
            self.assertIn('href="2026-11-13/"', index)
            self.assertIn("Panne géante", index)
            data = json.loads((out / "2026-11-13" / "edition.json").read_text("utf-8"))
            self.assertEqual(data["date"], "2026-11-13")


class FakeLibreTranslate(BaseHTTPRequestHandler):
    """Imite POST /translate de LibreTranslate : préfixe chaque texte par « [FR] »."""
    calls = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLibreTranslate.calls.append(body)
        q = body["q"]
        out = ["[FR] " + x for x in q] if isinstance(q, list) else "[FR] " + q
        data = json.dumps({"translatedText": out}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start_server(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.old = g.TRANSLATE_URL

    def tearDown(self):
        g.TRANSLATE_URL = self.old

    def test_translated_edition_with_toggle(self):
        srv = start_server(FakeLibreTranslate)
        g.TRANSLATE_URL = "http://127.0.0.1:%d" % srv.server_port
        FakeLibreTranslate.calls.clear()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                _day, s = g.build(NOW, out, offline_dir=FIXTURES)
                page = (out / "2026-11-13" / "index.html").read_text("utf-8")
        finally:
            srv.shutdown()
        self.assertTrue(FakeLibreTranslate.calls)
        self.assertTrue(all(c["source"] == "en" and c["target"] == "fr" for c in FakeLibreTranslate.calls))
        sent = [x for c in FakeLibreTranslate.calls for x in c["q"]]
        self.assertNotIn("Panne géante de Microsoft : Outlook et Teams inaccessibles", sent)  # déjà en français
        self.assertIn("[FR] PS6 release date leak points to 2027", page)
        self.assertIn('<span class="vo" lang="en">PS6 release date leak points to 2027</span>', page)
        self.assertIn('id="vo" class="vo-switch"', page)
        self.assertIn("VF → VO", page)
        self.assertNotIn("Traduction indisponible", page)

    def test_translation_down_still_builds(self):
        g.TRANSLATE_URL = "http://127.0.0.1:9"  # personne n'écoute
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _day, s = g.build(NOW, out, offline_dir=FIXTURES)
            page = (out / "2026-11-13" / "index.html").read_text("utf-8")
        self.assertIn("Traduction indisponible", page)
        self.assertIn("PS6 release date leak", page)
        self.assertNotIn("vo-switch\"", page)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class RefreshServiceTests(unittest.TestCase):
    def test_refresh_button_triggers_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            calls = []
            old_run, old_port, old_daily = g.run_edition, g.SERVE_PORT, g.DAILY_AT
            g.DAILY_AT = ""

            def fake_run(now, out_dir, offline_dir=None, commit=True, notify_phone=True):
                calls.append(notify_phone)
                time.sleep(0.3)
                (out_dir / "2026-11-13").mkdir(exist_ok=True)
                (out_dir / "2026-11-13" / "index.html").write_text("ok")

            g.run_edition = fake_run
            sock = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            port = sock.server_port
            sock.server_close()
            threading.Thread(target=g.serve, args=(out, port), daemon=True).start()
            time.sleep(0.3)
            opener = urllib.request.build_opener(_NoRedirect)
            base = "http://127.0.0.1:%d%s" % (port, g.REFRESH_PATH)
            try:
                for _ in range(2):  # deux clics rapides -> une seule génération
                    with self.assertRaises(urllib.error.HTTPError) as cm:
                        opener.open(urllib.request.Request(base, data=b"", method="POST"))
                    self.assertEqual(cm.exception.code, 303)
                with urllib.request.urlopen(base) as r:  # pendant la génération : page d'attente
                    self.assertIn("Actualisation en cours", r.read().decode())
                time.sleep(0.6)
                with self.assertRaises(urllib.error.HTTPError) as cm:  # ensuite : retour à l'accueil
                    opener.open(base)
                self.assertEqual(cm.exception.headers["Location"], "/")
                with self.assertRaises(urllib.error.HTTPError) as cm:  # qui mène à l'édition du jour
                    opener.open("http://127.0.0.1:%d/" % port)
                self.assertEqual(cm.exception.headers["Location"], "/2026-11-13/")
            finally:
                g.run_edition, g.SERVE_PORT, g.DAILY_AT = old_run, old_port, old_daily
            self.assertEqual(calls, [False])  # une seule génération, sans notification



def free_port():
    sock = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = sock.server_port
    sock.server_close()
    return port


class WebServerTests(unittest.TestCase):
    """Le service sert la gazette entière (NAS / Docker)."""
    daily = ""  # pas d'édition quotidienne dans ces tests

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.calls = []
        self.old_run = g.run_edition

        def fake_run(now, out_dir, offline_dir=None, commit=True, notify_phone=True):
            self.calls.append(now)
            g.build(NOW, out_dir, offline_dir=FIXTURES)

        g.run_edition = fake_run
        self.old_daily, g.DAILY_AT = g.DAILY_AT, self.daily
        self.port = free_port()
        threading.Thread(target=g.serve, args=(self.out, self.port, "127.0.0.1"), daemon=True).start()
        time.sleep(0.3)
        self.opener = urllib.request.build_opener(_NoRedirect)

    def tearDown(self):
        g.run_edition, g.DAILY_AT = self.old_run, self.old_daily
        self.tmp.cleanup()

    def get(self, path):
        try:
            r = self.opener.open("http://127.0.0.1:%d%s" % (self.port, path))
            return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_first_visit_builds_then_serves_files(self):
        code, _h, body = self.get("/")  # aucune édition : génération + page d'attente
        self.assertEqual(code, 200)
        self.assertIn("Actualisation en cours", body.decode())
        time.sleep(1.5)
        self.assertEqual(len(self.calls), 1)
        # l'édition générée « le 13/11 » est d'un autre jour que maintenant -> périmée, mais le délai
        # entre deux générations empêche d'en relancer une : on est envoyé vers l'édition
        code, h, _ = self.get("/")
        self.assertEqual((code, h["Location"]), (303, "/2026-11-13/"))
        self.assertEqual(len(self.calls), 1)
        code, h, body = self.get("/2026-11-13/")
        self.assertEqual(code, 200)
        self.assertIn("Panne géante", body.decode())
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertIn('rel="manifest"', body.decode())
        code, h, body = self.get("/manifest.webmanifest")
        self.assertEqual((code, json.loads(body)["start_url"]), (200, "/"))
        code, h, body = self.get("/apple-touch-icon.png")
        self.assertEqual((code, h["Content-Type"], body[:4]), (200, "image/png", b"\x89PNG"))
        self.assertEqual(self.get("/2026-11-13")[0], 301)
        self.assertEqual(self.get("/index.html")[0], 200)  # archives
        for bad in ("/.gazette.lock", "/../gazette.py", "/%2e%2e/gazette.py", "/2026-11-13/../../x"):
            self.assertEqual(self.get(bad)[0], 404, bad)
        self.assertEqual(self.get("/healthz")[0], 200)

    def test_fresh_edition_is_not_rebuilt(self):
        now = datetime.now(timezone.utc)
        g.build(now, self.out, offline_dir=FIXTURES)
        code, h, _ = self.get("/")
        self.assertEqual((code, h["Location"]), (303, "/%s/" % now.astimezone(g.TZ).date().isoformat()))
        self.assertEqual(self.calls, [])
        self.assertAlmostEqual(g.edition_age(self.out), 0, delta=0.1)


class DailyEditionTests(unittest.TestCase):
    def test_slots_around_6h30_paris(self):
        before = datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc)   # 6 h 00 à Paris
        after = datetime(2026, 11, 14, 6, 0, tzinfo=timezone.utc)    # 7 h 00 à Paris
        last, nxt = g.daily_slot(before, "06:30")
        self.assertEqual((last.isoformat(), nxt.isoformat()),
                         ("2026-11-13T06:30:00+01:00", "2026-11-14T06:30:00+01:00"))
        last, nxt = g.daily_slot(after, "06:30")
        self.assertEqual(nxt.isoformat(), "2026-11-15T06:30:00+01:00")
        # passage à l'heure d'été (29/03/2026) : toujours 6 h 30 heure locale
        _l, nxt = g.daily_slot(datetime(2026, 3, 28, 12, tzinfo=timezone.utc), "06:30")
        self.assertEqual(nxt.isoformat(), "2026-03-29T06:30:00+02:00")

    def test_missed_edition(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertTrue(g.daily_missed(out, NOW, "06:30"))       # aucune édition
            g.build(NOW, out, offline_dir=FIXTURES)                  # faite à 6 h 30 le 13
            self.assertFalse(g.daily_missed(out, NOW + timedelta(hours=10), "06:30"))
            self.assertTrue(g.daily_missed(out, NOW + timedelta(days=1, hours=1), "06:30"))

    def test_service_catches_up_on_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            g.build(NOW, out, offline_dir=FIXTURES)  # dernière édition : 13/11, donc 6 h 30 manquée
            calls, old = [], (g.run_edition, g.DAILY_AT)
            g.run_edition = lambda *a, **k: calls.append(1)
            g.DAILY_AT = "06:30"
            try:
                threading.Thread(target=g.serve, args=(out, free_port(), "127.0.0.1"), daemon=True).start()
                time.sleep(0.5)
            finally:
                g.run_edition, g.DAILY_AT = old
            self.assertEqual(calls, [1])


class FrenchFirstTests(unittest.TestCase):
    def test_french_breaks_ties_but_not_more(self):
        fr = g.make_topic([art("Nouvelle console annoncée", "Numerama", lang="fr")])
        en = g.make_topic([art("New console announced", "The Verge")])
        en2 = g.make_topic([art("Big leak story", "The Verge"), art("Big leak story", "Ars Technica")])
        self.assertGreater(fr["score"], en["score"])
        self.assertGreater(en2["score"], fr["score"])  # 2 sites anglais > 1 site français

    def test_french_briefs_come_first_within_window(self):
        ts = [g.make_topic([art("English news", "The Verge", hour=5)]),
              g.make_topic([art("Brève française", "Numerama", lang="fr", hour=1)]),
              g.make_topic([art("Very old english", "The Verge", hour=0)])]
        out = g.alternate(ts, 3)
        self.assertEqual([t["title"] for t in out], ["Brève française", "English news", "Very old english"])


if __name__ == "__main__":
    unittest.main()
