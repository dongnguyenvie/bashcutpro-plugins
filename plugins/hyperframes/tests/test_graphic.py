"""Runtime discovery, compositions, rendering and placement, with a fake Node.js, a fake HyperFrames and a fake MCP
server: nothing is installed, no browser runs."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from hyperframes_plugin import cli  # noqa: E402
from hyperframes_plugin import composition as comp  # noqa: E402
from hyperframes_plugin.mcp import AppError  # noqa: E402
from hyperframes_plugin.runtime import find_node, parse_version, paths, pinned, status  # noqa: E402

FAKE_APP = os.path.join(HERE, "fake_filmcraft.py")
FAKE_NODE = os.path.join(HERE, "fake_node.py")
TPS = 254016000000
ENV_KEYS = ("FILMCRAFT_DATA_DIR", "BASHCUT_HYPERFRAMES_NODE", "BASHCUT_HYPERFRAMES_CHROME", "HYPERFRAMES_BROWSER_PATH",
            "BASHCUT_HYPERFRAMES_FFMPEG", "BASHCUT_HYPERFRAMES_FORMAT", "FAKE_HF_LOG", "FAKE_CHROME", "FAKE_NODE_VERSION")
PAGE = """<!doctype html>
<html data-composition-variables='[{"id":"title","type":"string","default":"Hello"},{"id":"count","type":"number","default":1},{"id":"accent","type":"color","default":"#ffffff"}]'>
<!-- A test card. -->
<!-- Pitfalls:
  - one
  - two -->
<body><div data-composition-id="main" data-width="1920" data-height="1080" data-duration="1"></div>
<script src="gsap.min.js"></script>%s</body></html>
"""


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Base(unittest.TestCase):
    installed = True

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        self.addCleanup(self.restore)
        os.environ["FILMCRAFT_DATA_DIR"] = self.dir  # no settings, runtime or Chrome from the real machine
        os.environ["BASHCUT_HYPERFRAMES_NODE"] = FAKE_NODE
        os.environ["BASHCUT_HYPERFRAMES_FFMPEG"] = os.path.join(self.dir, "no-ffmpeg")
        os.environ["FAKE_HF_LOG"] = os.path.join(self.dir, "hf.log")
        if self.installed:
            self.install()
        self.composition = os.path.join(self.dir, "card")
        touch(os.path.join(self.composition, "index.html"), PAGE % "")
        self.project = os.path.join(self.dir, "state.json")
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({}, f)

    def restore(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def install(self):
        """What `setup` leaves behind, with the fake HyperFrames as the CLI."""
        p = paths()
        os.makedirs(os.path.dirname(p["cli"]))
        shutil.copyfile(os.path.join(HERE, "fake_hyperframes.py"), p["cli"])
        touch(p["hyperframes"], json.dumps({"version": pinned()["hyperframes"]}))
        touch(p["gsap"], "/* gsap */")
        touch(p["lottie"], "/* lottie */")
        for name in ("katex.min.js", "katex.min.css", os.path.join("fonts", "KaTeX_Main-Regular.woff2")):
            touch(os.path.join(p["katex"], name))
        chrome = os.path.join(self.dir, "chrome-headless-shell")
        touch(chrome)
        touch(p["chromePathFile"], chrome)

    def run_cli(self, *argv, project=False):
        out, err = io.StringIO(), io.StringIO()
        extra = ["--cli", FAKE_APP, "--project", self.project] if project else []
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv) + extra)
        return code, json.loads(out.getvalue())

    def state(self):
        with open(self.project, encoding="utf-8") as f:
            return json.load(f)

    def hf_calls(self, command):
        try:
            with open(os.environ["FAKE_HF_LOG"], encoding="utf-8") as f:
                calls = [json.loads(line) for line in f]
        except OSError:
            return []
        return [c for c in calls if c["args"][0] == command]

    def commands(self):
        return [entry["id"] for entry in self.state()["log"]]


class RuntimeTests(Base):
    installed = False

    def test_versions(self):
        self.assertEqual(parse_version("v22.19.0\n"), (22, 19, 0))
        self.assertIsNone(parse_version("not node"))

    def test_node_too_old_is_refused(self):
        os.environ["FAKE_NODE_VERSION"] = "v20.11.1"
        with self.assertRaises(AppError) as caught:
            find_node(FAKE_NODE)
        self.assertEqual(caught.exception.code, "no_node")
        self.assertIn("v20.11.1", caught.exception.message)

    def test_check_lists_what_is_missing(self):
        report = status({"node": FAKE_NODE, "chrome": "", "ffmpeg": ""})
        self.assertEqual(report["node"]["version"], "22.20.0")
        self.assertFalse(report["ready"])
        self.assertEqual(len(report["missing"]), 2)  # the runtime and Chrome
        code, out = self.run_cli("check")
        self.assertEqual((code, out["error"]["code"]), (1, "dependency_missing"))

    def test_render_without_the_runtime_says_run_setup(self):
        code, out = self.run_cli("render", self.composition, "--out", self.dir)
        self.assertEqual(code, 1)
        self.assertIn("setup", out["error"]["message"])

    def test_setup_dry_run_lists_the_steps(self):
        bin_dir = os.path.join(self.dir, "bin")
        node = os.path.join(bin_dir, "node.py")
        os.makedirs(bin_dir)
        shutil.copyfile(FAKE_NODE, node)
        touch(os.path.join(bin_dir, "npm.cmd" if sys.platform == "win32" else "npm"))
        code, out = self.run_cli("setup", "--dry-run", "--node", node)
        self.assertEqual(code, 0, out)
        steps = out["data"]["steps"]
        self.assertEqual(steps[0][1:2], ["ci"])
        self.assertEqual(steps[1][-2:], ["browser", "ensure"])
        self.assertFalse(os.path.exists(paths()["runtime"]))


class CompositionTests(Base):
    installed = False

    def test_values_are_typed_and_checked(self):
        decl = comp.declared(PAGE % "")
        long_text = os.path.join(self.dir, "title.txt")
        touch(long_text, "From a file")
        vals, warnings = comp.values(decl, ["count=3", "title=@" + long_text], brand={"accent": "#f00", "bodyFont": "Inter"})
        self.assertEqual(vals, {"count": 3, "title": "From a file", "accent": "#f00"})
        self.assertEqual(warnings, [])
        vals, warnings = comp.values(decl, ["accent=#0f0", "headingFont=Inter"], base={"count": 2}, unset=["count"])
        self.assertEqual(vals, {"accent": "#0f0"})
        self.assertEqual(len(warnings), 1)
        for bad in (["nope=1"], ["count=many"], ["count"]):
            with self.assertRaises(AppError):
                comp.values(decl, bad)

    def test_values_are_written_as_defaults_and_duration_stretched(self):
        html = comp.with_defaults(PAGE % "", comp.declared(PAGE % ""), {"title": "It's & done"})
        self.assertEqual(comp.declared(html)[0]["default"], "It's & done")
        self.assertEqual(comp.root_number(comp.with_duration(html, 2.5), "duration"), 2.5)
        self.assertEqual(comp.pitfalls(html), ["one", "two"])

    def test_every_example_reads(self):
        rows = comp.examples()
        self.assertGreaterEqual(len(rows), 30)
        for row in rows:
            self.assertTrue(row["seconds"], row["name"])
            full = comp.examples(row["name"])[0]
            self.assertIsInstance(full["variables"], list)

    def test_digest_follows_files_and_inputs(self):
        page = "index.html"
        first = comp.digest(self.composition, page, {"title": "A"}, 30, None, False)
        self.assertEqual(first, comp.digest(self.composition, page, {"title": "A"}, 30, None, False))
        self.assertNotEqual(first, comp.digest(self.composition, page, {"title": "B"}, 30, None, False))
        touch(os.path.join(self.composition, "renders", "old.png"))
        self.assertEqual(first, comp.digest(self.composition, page, {"title": "A"}, 30, None, False))
        touch(os.path.join(self.composition, "style.css"))
        self.assertNotEqual(first, comp.digest(self.composition, page, {"title": "A"}, 30, None, False))


class RenderTests(Base):
    def test_check_is_ready(self):
        code, out = self.run_cli("check")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["data"]["ready"])

    @unittest.skip("test ticket: png and mov renders of one graphic get separate outputs and records (RP14)")
    def test_png_and_mov_keep_separate_records(self):
        pass

    @unittest.skip("test ticket: a name with a dot (a.b) gets its own record, not a.graphic.json (RP14)")
    def test_a_dotted_name_keeps_its_own_record(self):
        pass

    @unittest.skip("test ticket: reused / lint / warnings never land in the .graphic.json record (RP14)")
    def test_the_record_holds_no_transient_keys(self):
        pass

    def test_render_then_reuse(self):
        out_dir = os.path.join(self.dir, "renders")
        code, out = self.run_cli("render", self.composition, "--set", "title=Hi", "--out", out_dir)
        self.assertEqual(code, 0, out)
        data = out["data"]
        self.assertEqual((data["frames"], data["renderFps"], data["width"], data["height"]), (30, 30, 4, 2))
        self.assertTrue(os.path.isfile(data["media"]))
        self.assertTrue(data["media"].endswith("frame_000001.png"))
        self.assertTrue(os.path.isfile(data["record"]))
        call = self.hf_calls("render")[0]
        self.assertEqual(call["defaults"]["title"], "Hi")
        self.assertIn("gsap.min.js", call["files"])
        self.assertIsNone(call["env"]["PRODUCER_EXPERIMENTAL_FAST_CAPTURE"])
        self.assertFalse(os.path.exists(os.path.join(self.composition, "gsap.min.js")))  # the source is untouched
        code, again = self.run_cli("render", self.composition, "--set", "title=Hi", "--out", out_dir)
        self.assertTrue(again["data"]["reused"])
        self.assertEqual(len(self.hf_calls("render")), 1)

    def test_draft_and_duration(self):
        code, out = self.run_cli("render", self.composition, "--draft", "--duration", "2", "--out", self.dir)
        self.assertEqual(code, 0, out)
        self.assertEqual((out["data"]["frames"], out["data"]["renderFps"]), (30, 15))
        self.assertEqual(self.hf_calls("render")[0]["env"]["PRODUCER_EXPERIMENTAL_FAST_CAPTURE"], "true")

    def test_an_example_by_name(self):
        code, out = self.run_cli("render", "stat-counter", "--set", "to=250", "--duration", "1", "--out", self.dir)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.hf_calls("render")[0]["defaults"]["to"], 250)
        self.assertIn("stat-counter-", out["data"]["output"])

    def test_strict_refuses_lint_errors(self):
        touch(os.path.join(self.composition, "index.html"), PAGE % "<!-- LINT_ERROR -->")
        code, out = self.run_cli("render", self.composition, "--strict", "--out", self.dir)
        self.assertEqual((code, out["error"]["code"]), (1, "lint_failed"))
        code, out = self.run_cli("render", self.composition, "--out", self.dir)
        self.assertEqual(out["data"]["lint"]["errors"], 1)

    def test_mov_needs_ffmpeg(self):
        code, out = self.run_cli("render", self.composition, "--format", "mov", "--out", self.dir)
        self.assertEqual((code, out["error"]["code"]), (1, "dependency_missing"))
        self.assertEqual(self.hf_calls("render"), [])

    def test_snapshot_renders_nothing(self):
        code, out = self.run_cli("render", self.composition, "--snapshot", "0.5,1", "--out", os.path.join(self.dir, "s"))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out["data"]["pngs"]), 2)
        self.assertEqual(self.hf_calls("render"), [])


class PlaceTests(Base):
    def place(self, *extra):
        code, out = self.run_cli("place", self.composition, "--seconds", "2", *extra, project=True)
        self.assertEqual(code, 0, out)
        return out["data"]

    def test_place_on_v2(self):
        data = self.place()
        self.assertEqual(data["tracksAdded"], 1)
        self.assertTrue(data["saved"])
        state = self.state()
        self.assertEqual(self.commands(), ["sequence.addTracks", "file.importImageSequence", "timeline.place", "file.save"])
        place = state["log"][2]["params"]
        self.assertEqual((place["track"], place["seconds"], place["insert"], place["audio"]), ("V2", 2.0, False, False))
        clip = state["sequence"]["video"][1]["items"][0]
        self.assertEqual((clip["start"], clip["end"]), (2 * TPS, 3 * TPS))
        self.assertTrue(data["record"].startswith(os.path.join(self.dir, "graphics", "renders")))
        with open(data["record"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["placed"][0]["item"], data["item"])

    @unittest.skip("test ticket: on a 25 fps sequence (fake settings.frame_rate 25/1) --frame 50 places at 2 s "
                   "and renders at 25 fps (RP14)")
    def test_place_follows_the_sequence_frame_rate(self):
        pass

    def test_place_needs_a_start(self):
        code, out = self.run_cli("place", self.composition, project=True)
        self.assertEqual((code, out["error"]["code"]), (2, "bad_args"))

    def test_rerender_swaps_the_media(self):
        data = self.place()
        code, out = self.run_cli("rerender", data["record"], "--set", "title=Changed", project=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["data"]["items"], [data["item"]])
        swap = next(e for e in self.state()["log"] if e["id"] == "media.replaceFootage")
        self.assertEqual(swap["params"], {"item": data["item"], "path": out["data"]["media"]})
        self.assertNotEqual(out["data"]["media"], data["media"])
        self.assertEqual(self.hf_calls("render")[-1]["defaults"]["title"], "Changed")
        code, again = self.run_cli("rerender", out["data"]["record"], project=True)
        self.assertEqual(again["data"]["items"], [])
        self.assertIn("Nothing changed", again["message"])

    def test_rerender_reports_a_new_length(self):
        data = self.place()
        code, out = self.run_cli("rerender", data["record"], "--duration", "2", project=True)
        self.assertEqual(out["data"]["lengthChanged"], {"from": 1.0, "to": 2.0})

    def test_finalise_renders_drafts_at_full_fps(self):
        data = self.place("--draft")
        self.assertEqual(data["renderFps"], 15)
        code, out = self.run_cli("finalise", project=True)
        self.assertEqual(code, 0, out)
        done = out["data"]["finalised"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["items"], [data["item"]])
        with open(done[0]["to"] + ".graphic.json", encoding="utf-8") as f:
            record = json.load(f)
        self.assertEqual((record["draft"], record["renderFps"], record["frames"]), (False, 30, 30))


if __name__ == "__main__":
    unittest.main()
