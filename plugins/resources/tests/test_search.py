"""`search` and `download` (BashCutPro#68): candidates with their licence and nothing downloaded; one pick fetched with
its licence record. The network is faked: the fetch scripts' `get` and finders are replaced."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)

from resources_plugin import cli  # noqa: E402
from resources_plugin import search as finder  # noqa: E402
from resources_plugin.licences import read_sidecar  # noqa: E402
from resources_plugin.mcp import AppError  # noqa: E402

with open(os.path.join(ROOT, "bashcut-plugin.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)

ICONIFY = {"icons": ["mdi:home", "tabler:home"],
           "collections": {"mdi": {"name": "Material Design Icons", "license": {"title": "Apache 2.0", "spdx": "Apache-2.0"},
                                   "author": {"name": "Pictogrammers"}},
                           "tabler": {"name": "Tabler Icons", "license": {"title": "MIT", "spdx": "MIT"}}}}


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = {k: os.environ.get(k) for k in ("FILMCRAFT_DATA_DIR", "PIXABAY_API_KEY", "PEXELS_API_KEY", "FREESOUND_API_KEY")}
        for k in self.env:
            os.environ.pop(k, None)
        os.environ["FILMCRAFT_DATA_DIR"] = self.dir
        # the real scripts (another test may have loaded its stand-in fetch_stock)
        for name in ("fetch_stock", "fetch_audio", "fetch_font"):
            sys.modules.pop(name, None)
        finder.SCRIPTS = REAL_SCRIPTS
        if REAL_SCRIPTS in sys.path:
            sys.path.remove(REAL_SCRIPTS)
        self.stock, self.audio = finder._scripts()
        self.urls = []
        self.saved = (self.stock.get, self.audio.music_candidates, dict(self.stock.SEARCH))

        def fake_get(url, headers=None, timeout=30, tries=3):
            self.urls.append(url)
            if "api.iconify.design/search" in url:
                return json.dumps(ICONIFY).encode()
            if url.startswith("https://files.example/"):
                return b"ID3fake-audio"
            raise OSError("no network in tests: %s" % url)

        self.stock.get = fake_get

    def tearDown(self):
        self.stock.get, self.audio.music_candidates, search = self.saved
        self.stock.SEARCH.clear()
        self.stock.SEARCH.update(search)
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_music_lists_licence_and_downloads_nothing(self):
        self.audio.music_candidates = lambda q, n, s: [
            {"url": "https://files.example/bed.mp3", "title": "Morning Bed", "seconds": 95, "license": "cc0",
             "author": "Ann", "source": "https://openverse.org/audio/1", "tags": ["calm"]}]
        items = finder.search("music", "calm acoustic", 5)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it["kind"], it["license"], it["author"], it["preview"]), ("music", "cc0", "Ann", "https://files.example/bed.mp3"))
        self.assertEqual(it["source"], "https://openverse.org/audio/1")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "bashcut-plugins")), "search downloads nothing")

    def test_icons_carry_their_set_licence(self):
        items = finder.search("icon", "home", 2)
        self.assertEqual([i["id"] for i in items], ["mdi:home", "tabler:home"])
        self.assertEqual(items[0]["license"], "Apache-2.0")
        self.assertEqual(items[0]["author"], "Pictogrammers")
        self.assertTrue(items[0]["preview"].endswith("/mdi/home.svg"))

    def test_photos_without_a_key_use_the_free_sources(self):
        seen = {}

        def free(query, kind, orientation, count, min_short):
            seen["args"] = (query, kind)
            return [{"id": "42", "source": "wikimedia", "page": "https://commons.wikimedia.org/x", "author": "Bo",
                     "license": "CC BY-SA 4.0", "preview": "https://files.example/t.jpg", "url": "https://files.example/p.jpg"},
                    {"id": "43", "source": "wikimedia"}]  # no url: dropped

        self.stock.SEARCH["free"] = free
        items = finder.search("photo", "pottery", 4)
        self.assertEqual(seen["args"], ("pottery", "photo"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["license"], "CC BY-SA 4.0")

    def test_download_writes_the_licence_record_and_credit(self):
        data = finder.download("music", "https://files.example/bed.mp3", "Morning Bed", "CC0", "Ann", "https://openverse.org/audio/1")
        self.assertTrue(os.path.isfile(data["path"]))
        self.assertTrue(data["path"].startswith(os.path.join(self.dir, "bashcut-plugins")))
        self.assertTrue(data["path"].endswith(".mp3"))
        record = read_sidecar(data["path"])
        self.assertEqual(record["license"], "CC0")
        self.assertIn("Morning Bed", data["credit"])
        self.assertIn("by Ann", data["credit"])
        again = finder.download("music", "https://files.example/bed.mp3", "Morning Bed", "CC0")
        self.assertNotEqual(again["path"], data["path"], "a second download never overwrites the first")

    def test_bad_requests_are_refused(self):
        with self.assertRaises(AppError):
            finder.search("music", "  ", 3)
        with self.assertRaises(AppError):
            finder.search("nope", "x", 3)
        with self.assertRaises(AppError):
            finder.download("music", "file:///etc/passwd", "x")
        with self.assertRaises(AppError):
            finder.download("music", "https://elsewhere.example/missing.mp3", "x")

    def test_cli_prints_one_result_object(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["search", "--kind", "icon", "--query", "home", "--count", "2"])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["items"]), 2)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["download", "--kind", "music", "--url", "ftp://x/y"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out.getvalue())["ok"])

    def test_manifest_flags_match_the_parser(self):
        actions = {a["id"]: a for a in MANIFEST["actions"]}
        parser = cli.build_parser()
        for name in ("search", "download"):
            self.assertIn(name, actions)
            self.assertFalse(actions[name]["edits"])
            self.assertEqual(actions[name]["needs"], "none")
            argv = list(actions[name]["args"])
            for p in actions[name]["params"]:
                if p["type"] == "enum":
                    argv += [p["flag"], p["choices"][0]]
                elif p["type"] == "integer":
                    argv += [p["flag"], "3"]
                else:
                    argv += [p["flag"], "https://files.example/x.mp3" if p["id"] == "url" else "x"]
            parser.parse_args(argv)


if __name__ == "__main__":
    unittest.main()
