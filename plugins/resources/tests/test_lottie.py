"""Open Lottie animations (#565): useAnimations' package, Noto's list and LottieFiles' search, mocked, no network."""
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fetch_assets import PIL, FakeWeb, load, run  # noqa: E402

try:
    import rlottie_python  # noqa: F401  (setup installs it; skip the real frames where the test Python lacks it)
except ImportError:
    rlottie_python = None


def square(color=(0, 0, 0), frames=30):
    """A 64 px Lottie: one rectangle filled with `color` (0–1) that moves right."""
    return {"v": "5.6.5", "fr": 30, "ip": 0, "op": frames, "w": 64, "h": 64, "ddd": 0, "assets": [], "layers": [
        {"ddd": 0, "ind": 1, "ty": 4, "sr": 1, "ip": 0, "op": frames, "st": 0, "ks": {
            "o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [100, 100, 100]},
            "p": {"a": 1, "k": [{"t": 0, "s": [16, 32, 0], "i": {"x": 0.5, "y": 1}, "o": {"x": 0.5, "y": 0}},
                                {"t": frames, "s": [48, 32, 0]}]}},
         "shapes": [{"ty": "gr", "it": [
             {"ty": "rc", "p": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [24, 24]}, "r": {"a": 0, "k": 0}},
             {"ty": "fl", "c": {"a": 0, "k": [*color, 1]}, "o": {"a": 0, "k": 100}},
             {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]},
              "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}}]}]}]}


def module_text(data):
    """useAnimations' style: a minified JS literal with bare keys and .5 numbers."""
    text = json.dumps({"animationData": data, "interaction": "loop"}, separators=(",", ":"))
    text = text.replace('"v":"5.6.5"', 'v:"5.6.5"').replace('"fr":30', "fr:30").replace('"x":0.5', "x:.5")
    return f"export default {text};"


def package():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        files = {"package/catalog.json": json.dumps([
            {"id": "checkmark", "title": "Checkmark", "export": "checkmark", "category": "Other",
             "keywords": ["check", "done"], "interaction": "loop"},
            {"id": "arrowDown", "title": "Arrow down", "export": "arrowDown", "category": "Arrows",
             "keywords": ["arrow", "down"], "interaction": "hover"},
            {"id": "broken", "title": "Broken", "export": "broken", "keywords": []}]),
            "package/lib/checkmark.mjs": module_text(square()), "package/lib/arrowDown.mjs": module_text(square()),
            "package/lib/broken.mjs": "export default {animationData:{nm:};"}
        for name, text in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(text.encode())
            tar.addfile(info, io.BytesIO(text.encode()))
    return buffer.getvalue()


REGISTRY = {"dist-tags": {"latest": "3.1.1"},
            "versions": {"3.1.1": {"dist": {"tarball": "https://registry.npmjs.org/react-useanimations/-/x.tgz"}}}}
NOTO = {"icons": [{"codepoint": "1f389", "popularity": 736, "tags": [":party-popper:"], "categories": ["Activities"]},
                  {"codepoint": "2705", "popularity": 19, "tags": [":check-mark:", ":check-mark-green:"]}]}
LOTTIEFILES = {"data": {"searchPublicAnimations": {"edges": [
    {"node": {"id": 77, "name": "Confetti burst", "url": "https://lottiefiles.com/animations/burst-77",
              "jsonUrl": "https://assets-v2.lottiefiles.com/a/77.json", "lottieFileSize": 9000,
              "createdBy": {"username": "/maker"}}},
    {"node": {"id": 78, "name": "Huge", "jsonUrl": "https://assets-v2.lottiefiles.com/a/78.json",
              "lottieFileSize": 9_000_000}},
    {"node": {"id": 79, "name": "No json"}}]}}}


def web():
    return FakeWeb([("https://registry.npmjs.org/react-useanimations/-/", package()),
                    ("https://registry.npmjs.org/react-useanimations", REGISTRY),
                    ("https://googlefonts.github.io/", NOTO),
                    ("https://fonts.gstatic.com/", json.dumps(square((1, 0.8, 0))).encode()),
                    ("https://assets-v2.lottiefiles.com/", json.dumps(square((0.2, 0.6, 1))).encode())])


class Send:
    """Answers post(url, body) for LottieFiles; records the bodies."""

    def __init__(self, answer):
        self.answer, self.bodies = answer, []

    def __call__(self, url, body, timeout=30):
        self.bodies.append(body)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def strip(text, out, tile=220):
    from PIL import Image
    Image.new("RGBA", (tile, tile), (0, 0, 0, 0)).save(out)
    return {"fps": 30.0, "frames": 30, "seconds": 1.0, "width": 64, "height": 64}


@unittest.skipIf(os.name == "nt" and not sys.flags.utf8_mode,
                 "the scripts read and write files as UTF-8: run with python -X utf8 (the plugin starts them so)")
class LottieTests(unittest.TestCase):
    def setUp(self):
        self.lottie = load("fetch_lottie.py")
        self.shared = tempfile.TemporaryDirectory()
        self.addCleanup(self.shared.cleanup)
        patch = mock.patch.dict(os.environ, {"BASHCUT_SHARED_DATA": self.shared.name})
        patch.start()
        self.addCleanup(patch.stop)

    def test_js_literal_becomes_json(self):
        text = ("export default {animationData:{nm:\"a:b\",k:[.5,-.25,1e-3,12],x:'it\\'s',e:true,n:null,"
                "s:\"wiggle(2, .5)\"},interaction:\"loop\"};")
        got = self.lottie.js_object(text)
        self.assertEqual(got["animationData"], {"nm": "a:b", "k": [0.5, -0.25, 0.001, 12], "x": "it's", "e": True,
                                                "n": None, "s": "wiggle(2, .5)"})

    def test_index_once_with_every_icon_file_and_broken_ones_left_out(self):
        fetch = web()
        rows, seconds = self.lottie.ingest(fetch=fetch)
        self.assertIsNotNone(seconds)
        self.assertEqual([r["key"] for r in rows], ["ua-checkmark", "ua-arrowDown", "noto-1f389", "noto-2705"])
        self.assertEqual(rows[0]["tags"], ["check", "done", "Other"])
        self.assertTrue(rows[0]["loop"])
        self.assertFalse(rows[1]["loop"])
        self.assertEqual((rows[2]["name"], rows[2]["emoji"]), ("party popper", "🎉"))
        saved = json.loads((Path(self.shared.name) / "lottie/useanimations/checkmark.json").read_text())
        self.assertEqual(saved["layers"][0]["ks"]["p"]["k"][0]["i"]["x"], 0.5)
        again, seconds = self.lottie.ingest(fetch=fetch)
        self.assertIsNone(seconds)
        self.assertEqual(len(again), 4)

    def test_rank_by_name_tags_and_synonyms(self):
        rows, _ = self.lottie.ingest(fetch=web())
        self.assertEqual(self.lottie.rank("check", rows, 3, "all")[0][1]["key"], "ua-checkmark")
        self.assertEqual(self.lottie.rank("confetti", rows, 3, "all")[0][1]["key"], "noto-1f389")  # synonym
        self.assertEqual(self.lottie.rank("check", rows, 3, "noto")[0][1]["key"], "noto-2705")
        self.assertEqual(self.lottie.rank("loader", rows, 3, "all"), [])

    def test_palette_counts_static_animated_solid_and_gradient(self):
        data = {"layers": [{"ty": 1, "sc": "#FF0000"}, {"shapes": [
            {"ty": "fl", "c": {"a": 0, "k": [0, 0, 0, 1]}},
            {"ty": "st", "c": {"a": 1, "k": [{"s": [1, 1, 1, 1]}, {"s": [0, 0, 0, 1]}]}},
            {"ty": "gf", "g": {}}]}]}
        self.assertEqual(self.lottie.palette(data), ["#000000", "#ff0000", "#ffffff", "gradient"])

    def test_lottiefiles_search_is_cached_per_word_and_drops_big_files(self):
        send = Send(LOTTIEFILES)
        rows = self.lottie.lottiefiles("confetti", 3, 800, send=send)
        self.assertEqual([r["key"] for r in rows], ["lf-77"])
        self.assertEqual((rows[0]["author"], rows[0]["page"]), ("maker", "https://lottiefiles.com/animations/burst-77"))
        self.assertEqual(send.bodies[0]["variables"], {"q": "confetti", "n": 6})
        self.lottie.lottiefiles("confetti", 3, 800, send=send)
        self.assertEqual(len(send.bodies), 1)

    @unittest.skipIf(PIL is None, "Pillow not installed")
    def test_sheet_mixes_sources_then_pick_copies_and_prints_the_place_call(self):
        send = Send(LOTTIEFILES)
        with tempfile.TemporaryDirectory() as dest, tempfile.TemporaryDirectory() as project, \
                mock.patch.object(self.lottie, "get", web()), mock.patch.object(self.lottie, "post", send), \
                mock.patch.object(self.lottie, "frame_strip", strip):
            lines = run(self.lottie, ["--words", "check,confetti", "--dest", dest, "--count", "2"])
            self.assertEqual(lines[0]["ingested"], 4)
            self.assertEqual(lines[1], {"slot": "check", "candidates": 2,
                                        "sources": {"useanimations": 1, "lottiefiles": 1}})
            self.assertEqual(lines[2]["sources"], {"noto": 1, "lottiefiles": 1})
            self.assertTrue(Path(lines[3]["sheets"][0]).exists())
            rows = json.loads((Path(dest) / "lottie.json").read_text())
            self.assertEqual(rows[0]["size"], "ua Checkmark mono")
            self.assertEqual(rows[1]["license"], "Lottie Simple License")
            picked = run(self.lottie, ["--dest", dest, "--pick", "check=1;confetti=2@120:45;confetti=9",
                                       "--save-to", project])
            check, burst, missing = picked
            burst_fill = json.loads(Path(burst["file"]).read_text())["layers"][0]["shapes"][0]["it"][1]["c"]["k"]
        self.assertEqual(Path(check["file"]), Path(project) / "check-ua-checkmark.json")
        self.assertEqual((check["license"], check["mono"]), ("MIT", True))
        self.assertEqual(check["place"], f"graphic place lottie --set file={check['file']}")
        self.assertEqual((burst["author"], burst["source"]), ("maker", "lottiefiles"))
        self.assertTrue(burst["place"].endswith("lf-77.json --at-frame 120 --duration 45"))
        self.assertEqual(burst_fill, [0.2, 0.6, 1, 1])
        self.assertEqual(missing["error"], "no such candidate")

    @unittest.skipIf(PIL is None, "Pillow not installed")
    def test_lottiefiles_failing_leaves_the_index_matches(self):
        with tempfile.TemporaryDirectory() as dest, mock.patch.object(self.lottie, "get", web()), \
                mock.patch.object(self.lottie, "post", Send(OSError("curl exit 6"))), \
                mock.patch.object(self.lottie, "frame_strip", strip):
            lines = run(self.lottie, ["--words", "check", "--dest", dest, "--count", "4"])
        self.assertEqual(lines[1]["sources"], {"useanimations": 1, "noto": 1})
        self.assertIn("lottiefiles: curl exit 6", lines[1]["error"])

    @unittest.skipIf(PIL is None or rlottie_python is None, "Pillow or rlottie not installed")
    def test_frame_strip_draws_four_moments(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "strip.png"
            got = self.lottie.frame_strip(json.dumps(square()), out)
            image = Image.open(out)
            self.assertEqual(image.size, (220, 220))
            # the square moves right: its centre column is further right in each later quarter
            centres = []
            for j in range(4):
                quarter = image.crop(((j % 2) * 110, (j // 2) * 110, (j % 2) * 110 + 110, (j // 2) * 110 + 110))
                xs = [x for x in range(110) for y in range(110) if quarter.getpixel((x, y))[3] > 128]
                centres.append(sum(xs) / len(xs))
        self.assertEqual(got, {"fps": 30.0, "frames": 30, "seconds": 1.0, "width": 64, "height": 64})
        self.assertEqual(centres, sorted(centres))
        self.assertGreater(centres[3] - centres[0], 20)
        with self.assertRaises(ValueError):
            self.lottie.frame_strip("{}", Path(folder) / "none.png")


if __name__ == "__main__":
    unittest.main()
