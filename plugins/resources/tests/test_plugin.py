"""The plugin around the scripts: the bashcut shim, licence records, credits, import and placing (fake MCP server)."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from resources_plugin import __version__, cli, shim  # noqa: E402
from resources_plugin.licences import credit_line, merge_notes, read_sidecar, write_sidecar  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")
TPS = 254016000000
ENV_KEYS = ("FILMCRAFT_DATA_DIR", "BASHCUT_RESOURCES_SCRIPTS", "BASHCUT_RESOURCES_PENDING", "BASHCUT_RESOURCES_PYTHON")

with open(os.path.join(os.path.dirname(HERE), "bashcut-plugin.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.dest = os.path.join(self.dir, "media", "stock")
        self.project = os.path.join(self.dir, "state.json")
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({"notes": "Brief: a pottery video."}, f)
        self.env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["FILMCRAFT_DATA_DIR"] = self.dir
        os.environ["BASHCUT_RESOURCES_SCRIPTS"] = os.path.join(HERE, "scripts")

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_cli(self, *argv, project=True):
        out, err = io.StringIO(), io.StringIO()
        extra = ["--cli", FAKE, "--project", self.project] if project else []
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv) + extra)
        return code, json.loads(out.getvalue())

    def state(self):
        with open(self.project, encoding="utf-8") as f:
            return json.load(f)


class ShimTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pending = os.path.join(self.dir, "pending.json")
        os.environ["BASHCUT_RESOURCES_PENDING"] = self.pending

    def tearDown(self):
        os.environ.pop("BASHCUT_RESOURCES_PENDING", None)

    def test_library_add_writes_the_licence_next_to_the_file_and_place_queues_it(self):
        path = os.path.join(self.dir, "logo.png")
        open(path, "wb").close()
        added = shim.answer(["library", "add", "--kind", "sticker", "--file", path, "--id", "figma", "--license",
                             "CC0 1.0", "--author", "Simple Icons", "--source", "https://simpleicons.org/?q=figma"], "fetch_logos.py")
        self.assertEqual(added["reference"], "file:" + path)
        record = read_sidecar(path)
        self.assertEqual((record["license"], record["author"], record["fetchedBy"]), ("CC0 1.0", "Simple Icons", "bashcut.resources fetch_logos.py"))
        placed = shim.answer(["library", "place", added["reference"], "--at-frame", "120", "--duration", "45", "--base-rev", "last"])
        self.assertEqual(placed["queued"], {"file": path, "frame": 120, "duration": 45})
        with open(self.pending, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["place"], [{"file": path, "frame": 120, "duration": 45}])

    def test_what_bashcutpro_cannot_do_is_an_error_the_scripts_handle(self):
        for argv in (["library", "analyze", "x"], ["fonts", "import", "a.ttf"], ["context", "get"], ["jobs", "wait", "j"],
                     ["media", "frames", "--file", "v.mp4"], ["timeline", "get"]):
            self.assertIn("error", shim.answer(argv), argv)
        self.assertIn("error", shim.answer(["library", "place", "project:abc", "--at-frame", "1"]))

    def test_project_data_round_trip_with_merge(self):
        data = os.path.join(self.dir, "project-data.json")
        source = os.path.join(self.dir, "style.json")
        with open(source, "w", encoding="utf-8") as f:
            json.dump({"set": "open-peeps"}, f)
        self.assertEqual(shim.answer(["project", "data", "illustrations"], data_file=data), {})
        shim.answer(["project", "set-data", "illustrations", source, "--merge", "--base-rev", "last"], data_file=data)
        with open(source, "w", encoding="utf-8") as f:
            json.dump({"palette": ["#fff"]}, f)
        shim.answer(["project", "set-data", "illustrations", source, "--merge"], data_file=data)
        self.assertEqual(shim.answer(["project", "data", "illustrations"], data_file=data), {"set": "open-peeps", "palette": ["#fff"]})


class CreditTests(unittest.TestCase):
    def test_credits_block_is_replaced_not_repeated(self):
        a = {"file": "a.jpg", "title": "Clay pot", "author": "Ann", "license": "CC BY 4.0", "source": "https://x/a"}
        b = {"file": "b.mp3"}
        notes = merge_notes("Brief.", [a])
        self.assertIn("- Clay pot, by Ann, CC BY 4.0, https://x/a", notes)
        again = merge_notes(notes, [a, b])
        self.assertEqual(again.count("Credits (fetched"), 1)
        self.assertEqual(again.count("Clay pot"), 1)
        self.assertIn(credit_line(b), again)
        self.assertIn("licence unknown", credit_line(b))
        self.assertTrue(again.startswith("Brief."))

    def test_an_existing_record_is_kept_when_asked(self):
        path = os.path.join(tempfile.mkdtemp(), "x.jpg")
        open(path, "wb").close()
        write_sidecar(path, {"license": "CC0"})
        self.assertEqual(write_sidecar(path, {"license": "other"}, keep=True)["license"], "CC0")


class FetchTests(Base):
    def test_no_credits_in_the_notes_unless_asked(self):
        code, out = self.run_cli("fetch", "--script", "stock", "--import", "--args", json.dumps(["--ids", "pot", "--dest", self.dest]))
        self.assertEqual((code, out["data"]["credits"]), (0, 0))
        self.assertEqual(read_sidecar(os.path.join(self.dest, "pot.jpg"))["license"], "CC BY 4.0")  # the record stays
        state = self.state()
        self.assertEqual(state["notes"], "Brief: a pottery video.")
        self.assertFalse(any(e["id"] in ("project.notes", "project.setNotes") for e in state["log"]))
        self.assertNotIn("credits", out["message"])

    def test_download_records_licences_and_imports_with_credits(self):
        code, out = self.run_cli("fetch", "--script", "stock", "--import", "--credits", "--args", json.dumps(["--ids", "pot,river", "--dest", self.dest]))
        self.assertEqual(code, 0, out)
        data = out["data"]
        pot = os.path.join(self.dest, "pot.jpg")
        self.assertEqual(read_sidecar(pot)["license"], "CC BY 4.0")
        self.assertEqual(read_sidecar(pot)["source"], "https://commons.wikimedia.org/wiki/File:pot.jpg")
        self.assertEqual(sorted(os.path.basename(i["path"]) for i in data["imported"]), ["pot.jpg", "river.jpg", "unlicensed.jpg"])
        self.assertEqual([os.path.basename(p) for p in data["unlicensed"]], ["unlicensed.jpg"])
        self.assertEqual(data["text"], ["a line that is not JSON"])
        state = self.state()
        self.assertTrue(state["notes"].startswith("Brief: a pottery video."))
        self.assertIn("pot.jpg, by Ann Ó, CC BY 4.0", state["notes"])
        self.assertEqual(state["saved"], 1)
        self.assertFalse(any(e["id"] == "timeline.place" for e in state["log"]))

    def test_script_media_import_is_done_by_the_plugin(self):
        code, out = self.run_cli("fetch", "--script", "stock", "--", "--ids", "pot", "--dest", self.dest, "--import", project=False)
        self.assertEqual(code, 0, out)
        queued = next(l for l in out["data"]["lines"] if l.get("queued"))
        self.assertEqual(len(queued["files"]), 2)
        self.assertNotIn("imported", out["data"])  # no connection: nothing imported

    def test_pick_places_at_the_frame_on_a_new_video_track(self):
        code, out = self.run_cli("fetch", "--script", "stock", "--credits", "--args", "--pick slot=1@12:30 --dest " + self.dest.replace("\\", "/"))
        self.assertEqual(code, 0, out)
        data = out["data"]
        line = data["lines"][0]
        self.assertEqual((line["placed"], line["analyzeError"]), (12, "measuring audio is not available in BashCutPro yet"))
        self.assertEqual(len(data["placed"]), 1)
        state = self.state()
        v2 = state["sequence"]["video"][1]["items"]
        self.assertEqual((v2[0]["start"], v2[0]["end"] - v2[0]["start"]), (12 * TPS // 30, 30 * (TPS // 30)))
        self.assertIn("Pick, by Bo, CC0", state["notes"])

    def test_pick_without_a_connection_is_left_pending(self):
        code, out = self.run_cli("fetch", "--script", "stock", "--args", json.dumps(["--pick", "s=1@5", "--dest", self.dest]), project=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["data"]["pending"][0]["frame"], 12)
        self.assertIn("not placed", out["message"])

    def test_import_needs_a_connection_and_import_folder_takes_recorded_files(self):
        code, out = self.run_cli("fetch", "--script", "stock", "--import", "--args", "--ids pot", project=False)
        self.assertEqual((code, out["error"]["code"]), (2, "bad_args"))
        self.run_cli("fetch", "--script", "stock", "--args", json.dumps(["--ids", "pot", "--dest", self.dest]), project=False)
        code, out = self.run_cli("import", "--folder", self.dest, "--frame", "0", "--duration", "60")
        self.assertEqual(code, 0, out)
        self.assertEqual([os.path.basename(i["path"]) for i in out["data"]["imported"]], ["pot.jpg"])
        self.assertEqual(out["data"]["placed"][0]["track"], "V2")
        code, out = self.run_cli("credits", "--folder", self.dest, project=False)
        self.assertEqual(out["data"]["credits"], ["- pot.jpg, by Ann Ó, CC BY 4.0, https://commons.wikimedia.org/wiki/File:pot.jpg"])

    @unittest.skip("test ticket: setup with a step that prints to stdout (fake python/uv): stdout is exactly one JSON object, the step output is on stderr")
    def test_setup_stdout_is_one_json_object(self):
        pass


    @unittest.skip("test ticket: a script that exits non-zero with no lines after its files were imported reports success with a note")
    def test_fetch_succeeds_when_files_were_imported_before_the_script_failed(self):
        pass

class OutputTests(unittest.TestCase):
    def test_the_result_is_utf8_whatever_the_console_code_page(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "x.png")
        open(path, "wb").close()
        write_sidecar(path, {"license": "CC0", "author": "Ann Ó Nguyễn"})
        env = dict(os.environ, PYTHONIOENCODING="cp1252:replace")
        done = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "plugin.py"), "credits", "--folder", folder],
                              stdout=subprocess.PIPE, env=env, check=True)
        self.assertIn("Ann Ó Nguyễn", json.loads(done.stdout.decode("utf-8"))["data"]["credits"][0])


class ManifestTests(unittest.TestCase):
    def flags(self, command):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if a.dest == "command").choices[command]
        return {o for a in sub._actions for o in a.option_strings}

    def test_manifest_matches_the_command_line(self):
        self.assertEqual(MANIFEST["version"], __version__)
        self.assertEqual({a["args"][0] for a in MANIFEST["actions"]}, set(cli.COMMANDS))
        for action in MANIFEST["actions"]:
            accepted = self.flags(action["args"][0])
            for param in action["params"]:
                self.assertIn(param["flag"], accepted, "%s %s" % (action["id"], param["flag"]))
            if action["needs"] == "project":
                for arg in MANIFEST["connect"].values():
                    self.assertIn(arg[0], accepted)
        fetch = next(a for a in MANIFEST["actions"] if a["id"] == "fetch")
        script = next(p for p in fetch["params"] if p["id"] == "script")
        self.assertEqual(sorted(script["choices"]), sorted(cli.SCRIPTS))

    def test_every_script_is_carried_over_and_secrets_are_env_only(self):
        for name, _, _ in cli.SCRIPTS.values():
            self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(HERE), "scripts", name + ".py")), name)
        for s in MANIFEST["settings"]:
            self.assertEqual(s["type"], "secret")
            self.assertNotIn("default", s)


if __name__ == "__main__":
    unittest.main()
