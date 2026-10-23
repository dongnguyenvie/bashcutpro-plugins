"""The library: save a title and an effect preset from one project, re-apply them on another, packs, search, brand
kits (fake MCP server)."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from library_plugin import __version__, cli  # noqa: E402
from library_plugin.library import KINDS, Library  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")
TPS = 254016000000

with open(os.path.join(os.path.dirname(HERE), "bashcut-plugin.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.old = os.environ.get("FILMCRAFT_DATA_DIR")
        os.environ["FILMCRAFT_DATA_DIR"] = os.path.join(self.dir, "data")
        os.environ.pop("BASHCUT_LIBRARY_DIR", None)
        # project A: a selected title, and a clip with an effect stack
        self.a = self.project("a.json", {"selection": 11, "sequence": {
            "settings": {"frame_rate": {"num": 30, "den": 1}},
            "video": [{"id": 1, "items": [
                {"clip": 11, "start": 0, "end": TPS, "effects": [], "graphic": {"Name": "Lan Pham", "font": "Inter", "fill": "#ffcc00"}},
                {"clip": 12, "start": TPS, "end": 2 * TPS, "effects": [{"effect": "gaussian_blur", "radius": 4}, {"effect": "vignette"}]}]}],
            "audio": [{"id": 2, "items": []}]}})
        # project B: one plain clip
        self.b = self.project("b.json", {"sequence": {
            "settings": {"frame_rate": {"num": 30, "den": 1}},
            "video": [{"id": 1, "items": [{"clip": 21, "start": 0, "end": TPS, "effects": []}]}],
            "audio": [{"id": 2, "items": []}]}})

    def tearDown(self):
        if self.old is None:
            os.environ.pop("FILMCRAFT_DATA_DIR", None)
        else:
            os.environ["FILMCRAFT_DATA_DIR"] = self.old

    def project(self, name, state):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        return path

    def run_cli(self, *argv, project=None):
        out, err = io.StringIO(), io.StringIO()
        extra = ["--cli", FAKE, "--project", project] if project else []
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv) + extra)
        return code, json.loads(out.getvalue())

    def ok(self, *argv, project=None):
        code, result = self.run_cli(*argv, project=project)
        self.assertEqual(code, 0, result)
        return result["data"]

    def state(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def file(self, name, body=b"data"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(body)
        return path


class SaveApplyTests(Base):
    def test_title_and_preset_saved_in_one_project_reapply_in_another(self):
        title = self.ok("save", "--kind", "title", "--title", "Guest lower third", "--tags", "lower-third,guest",
                        "--purpose", "guest names", project=self.a)["item"]
        preset = self.ok("save", "--kind", "effect-preset", "--clip", "12", "--title", "Dreamy blur",
                         "--license", "own", project=self.a)["item"]
        self.assertEqual(title["files"], ["Guest lower third.fcgt"])
        self.assertEqual(preset["params"]["preset"], "Dreamy blur")
        log = [e["id"] for e in self.state(self.a)["log"]]
        self.assertIn("graphics.template.export", log)
        self.assertEqual(log[log.index("presets.save") + 1], "presets.export")

        self.ok("apply", "--id", title["id"], "--seconds", "2", "--values", '{"Name": "Minh Tran"}', project=self.b)
        self.ok("apply", "--id", preset["id"], "--clip", "21", project=self.b)
        b = self.state(self.b)
        clips = b["sequence"]["video"][0]["items"]
        placed = [c for c in clips if c.get("graphic")]
        self.assertEqual(placed[0]["graphic"], {"Name": "Minh Tran", "font": "Inter", "fill": "#ffcc00"})
        self.assertEqual(placed[0]["start"], 2 * TPS)
        plain = next(c for c in clips if c["clip"] == 21)
        self.assertEqual([e["effect"] for e in plain["effects"]], ["gaussian_blur", "vignette"])
        self.assertEqual([e["id"] for e in b["log"]][:3], ["graphics.template.apply", "file.save", "presets.import"])
        lib = Library()
        self.assertEqual(lib.get(title["id"])["uses"], 2)  # saved, then applied
        self.assertIn(os.path.abspath(self.b), lib.get(preset["id"])["recent"])

    def test_lut_save_and_look_apply(self):
        look = self.ok("save", "--kind", "look", "--lut", "lib:teal", "--title", "Teal night", project=self.a)["item"]
        self.ok("apply", "--id", look["id"], "--clip", "21", project=self.b)
        clip = self.state(self.b)["sequence"]["video"][0]["items"][0]
        self.assertTrue(clip["look"].endswith("Teal night.cube"))
        code, result = self.run_cli("save", "--kind", "look", "--title", "x", project=self.a)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))

    def test_sound_range_and_placing(self):
        sound = self.ok("save", "--kind", "sound", "--title", "Whoosh", "--start", "1", "--end", "1.5", project=self.a)["item"]
        export = next(e for e in self.state(self.a)["log"] if e["id"] == "file.exportMedia")["params"]
        self.assertEqual((export["format"], export["range"], export["startSeconds"]), ("wav", "custom", 1.0))
        placed = self.ok("apply", "--id", sound["id"], "--seconds", "3", project=self.b)
        self.assertEqual(placed["track"], "A2")
        b = self.state(self.b)
        self.assertEqual(len(b["sequence"]["audio"]), 2)  # A2 added
        self.assertEqual(b["sequence"]["audio"][1]["items"][0]["start"], 3 * TPS)

    def test_add_from_a_file_and_remove(self):
        sticker = self.ok("add", "--kind", "sticker", "--file", self.file("star.png"), "--title", "Star", "--license", "CC0")["item"]
        folder = Library().folder(sticker["id"])
        self.assertTrue(os.path.isfile(os.path.join(folder, "star.png")))
        self.ok("remove", "--id", sticker["id"])
        self.assertFalse(os.path.exists(folder))
        code, result = self.run_cli("show", "--id", sticker["id"])
        self.assertEqual((code, result["error"]["code"]), (1, "not_found"))

    def test_add_refuses_a_file_that_is_not_the_kind(self):
        code, result = self.run_cli("add", "--kind", "lut", "--file", self.file("card.fcgt"), "--title", "Card")
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))
        self.assertIn(".cube", result["error"]["message"])
        self.assertEqual(self.ok("add", "--kind", "lut", "--file", self.file("warm.CUBE"), "--title", "Warm")["item"]["kind"], "lut")


class SearchTests(Base):
    def test_favourites_then_recent_then_match(self):
        lib = Library()
        plain = lib.add("title", "Bold title", [self.file("a.fcgt", b"a")], tags=["bold"])
        tagged = lib.add("title", "Clean", [self.file("b.fcgt", b"b")], purpose="a bold but clean title", tags=["bold"])
        fav = lib.add("title", "Bold red", [self.file("c.fcgt", b"c")])
        lib.add("sound", "Bold hit", [self.file("d.wav", b"d")])
        lib.favourite(fav["id"])
        lib.used(tagged["id"], "/p/x.fcproj")
        found = [i["id"] for i in lib.search("bold", kind="title", project="/p/x.fcproj")]
        self.assertEqual(found, [fav["id"], tagged["id"], plain["id"]])
        # without a project, the title match outranks a purpose match
        self.assertEqual([i["id"] for i in lib.search("bold", kind="title")][1:], [plain["id"], tagged["id"]])
        self.assertEqual([i["id"] for i in lib.search("bold", tags=["bold"])], [plain["id"], tagged["id"]])
        self.assertEqual(lib.search("nothing like it"), [])
        stats = self.ok("stats")
        self.assertEqual((stats["items"], stats["kinds"]["title"], stats["favourites"]), (4, 3, 1))


class PackTests(Base):
    def test_pack_round_trip_with_clashing_ids(self):
        lib = Library()
        logo = lib.add("sticker", "Logo", [self.file("logo.png", b"logo")], license="own")
        lower = lib.add("title", "Lower third", [self.file("lt.fcgt", b"{}")], license="own")
        kit = self.ok("brand", "new", "--title", "Acme")["item"]
        self.ok("brand", "set", "--id", kit["id"], "--logo", logo["id"], "--lower-third", lower["id"],
                "--colors", "#112233,#ffcc00", "--fonts", "Inter", "--caption-style", '{"font": "Inter", "size": 42}')
        pack = os.path.join(self.dir, "acme.zip")
        self.assertEqual(sorted(self.ok("pack-export", "--out", pack)["items"]), sorted([logo["id"], lower["id"], kit["id"]]))
        with zipfile.ZipFile(pack) as z:
            self.assertIn("library-pack.json", z.namelist())
            self.assertIn("items/logo/logo.png", z.namelist())

        # into an empty library: the same items come back
        os.environ["FILMCRAFT_DATA_DIR"] = os.path.join(self.dir, "other")
        Library().add("sticker", "Logo", [self.file("other-logo.png", b"another logo")])  # takes the id `logo`
        result = self.ok("pack-import", "--path", pack)
        self.assertEqual(len(result["added"]), 3)
        self.assertEqual(result["renamed"], {"logo": "logo-2"})
        imported = Library()
        brand = imported.brand(kit["id"])["params"]["brand"]
        self.assertEqual(brand["logo"], "logo-2")
        self.assertEqual(brand["colors"], ["#112233", "#ffcc00"])
        with open(imported.file_paths(imported.get("logo-2"))[0], "rb") as f:
            self.assertEqual(f.read(), b"logo")
        # importing again adds nothing
        again = self.ok("pack-import", "--path", pack)
        self.assertEqual((again["added"], len(again["skipped"])), ([], 3))

    def test_brand_caption_style_and_lower_third(self):
        lower = self.ok("save", "--kind", "title", "--title", "Lower third", project=self.a)["item"]
        kit = self.ok("brand", "new", "--title", "Acme")["item"]
        self.ok("brand", "set", "--id", kit["id"], "--lower-third", lower["id"], "--caption-style", '{"font": "Inter", "size": 42}')
        self.ok("brand", "apply", "--id", kit["id"], "--part", "captionStyle", project=self.b)
        self.assertEqual(self.state(self.b)["captionStyle"], {"font": "Inter", "size": 42, "track": "C1"})
        self.ok("brand", "apply", "--id", kit["id"], "--part", "lowerThird", "--seconds", "1", project=self.b)
        self.assertTrue(any(c.get("graphic") for c in self.state(self.b)["sequence"]["video"][0]["items"]))
        code, result = self.run_cli("brand", "set", "--id", kit["id"], "--colors", "red")
        self.assertEqual(code, 2)


    @unittest.skip("test ticket: a pack with a wrong sha256 is hashed from its files, so a same-file item is skipped and a different one added")
    def test_import_recomputes_the_pack_hash(self):
        pass


class IndexLockTests(Base):
    @unittest.skip("test ticket: two Library objects read the index, each adds / uses an item, both saves keep both updates (uses add up)")
    def test_concurrent_saves_keep_both_updates(self):
        pass

    @unittest.skip("test ticket: a stale index.json.lock is taken over; a fresh one held past LOCK_WAIT gives LibraryError busy")
    def test_stale_and_held_lock(self):
        pass

class ManifestTests(unittest.TestCase):
    def test_manifest_matches_the_cli(self):
        self.assertEqual(MANIFEST["version"], __version__)
        parser = cli.build_parser()
        commands = set(parser._subparsers._group_actions[0].choices)
        for action in MANIFEST["actions"]:
            self.assertIn(action["args"][0], commands)
        self.assertEqual(set(MANIFEST["actions"][1]["params"][0]["choices"]), set(KINDS))

    def test_engine_commands_are_listed(self):
        used = set()
        for name in ("engine.py",):
            with open(os.path.join(os.path.dirname(HERE), "library_plugin", name), encoding="utf-8") as f:
                text = f.read()
            for command in MANIFEST["app"]["commands"]:
                if '"%s"' % command in text:
                    used.add(command)
        self.assertEqual(used, set(MANIFEST["app"]["commands"]) - {"file.save"})


if __name__ == "__main__":
    unittest.main()
