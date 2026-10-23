"""Generation against a fake Gemini / OpenAI server on 127.0.0.1, and importing through a fake MCP server.

No real key, no network: every call goes to tests/fake_service.py or tests/fake_filmcraft.py.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from image_gen import cli  # noqa: E402
from image_gen.picture import decode_png  # noqa: E402
from image_gen.service import ImageError, Service, Style, nearest, safe_name  # noqa: E402
from image_gen.settings import resolve  # noqa: E402
from fake_service import FakeService  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")
TPS = 254016000000
ENV_KEYS = ("BASHCUTPRO_DATA_DIR", "FILMCRAFT_DATA_DIR", "BASHCUT_IMAGE_SERVICE", "BASHCUT_IMAGE_API_KEY", "BASHCUT_IMAGE_MODEL",
            "BASHCUT_IMAGE_ENDPOINT", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "FFMPEG")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.project = os.path.join(self.dir, "state.json")
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["FILMCRAFT_DATA_DIR"] = self.dir  # no settings file from the real machine
        self.fake = FakeService()
        self.ref = os.path.join(self.dir, "mascot.png")
        with open(self.ref, "wb") as f:
            f.write(self.fake.picture)

    def tearDown(self):
        self.fake.close()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_cli(self, *argv, service="gemini", project=False):
        out, err = io.StringIO(), io.StringIO()
        extra = ["--service", service, "--endpoint", self.fake.url, "--no-ffmpeg"] if argv[0] != "import" else []
        if service == "gemini" and argv[0] != "import":
            extra += ["--api-key", "test-key"]
        if project:
            extra += ["--cli", FAKE, "--project", self.project]
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv) + extra)
        return code, json.loads(out.getvalue())

    def state(self):
        with open(self.project, encoding="utf-8") as f:
            return json.load(f)

    def scenes(self, value):
        path = os.path.join(self.dir, "scenes.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f)
        return path


class UnitTests(unittest.TestCase):
    def test_style_text(self):
        self.assertEqual(Style().text("a cat"), "a cat\nNo text, letters or watermarks in the picture.")
        text = Style("flat pastel vector").text("a cat")
        self.assertTrue(text.startswith("Style: flat pastel vector\nScene: a cat"))

    def test_nearest_shape(self):
        self.assertEqual(nearest(1080, 1920, ["1:1", "9:16", "16:9"]), "9:16")
        self.assertEqual(nearest(1080, 1920, ["1:1", "2:3", "3:2"]), "2:3")
        self.assertEqual(nearest(1000, 1000, ["1:1", "2:3", "3:2"]), "1:1")

    def test_safe_name(self):
        self.assertEqual(safe_name("beat 3/ảnh"), "beat-3--nh")
        self.assertEqual(safe_name(""), "picture")

    def test_key_rules(self):
        with self.assertRaises(ImageError) as caught:
            Service("gemini", "")
        self.assertEqual(caught.exception.code, "no_key")
        Service("openai", "", endpoint="http://127.0.0.1:8080")  # a local server needs no key
        with self.assertRaises(ImageError) as caught:
            Service("openai", "k", endpoint="http://example.com")
        self.assertEqual(caught.exception.code, "bad_endpoint")

    def test_settings_never_read_the_key_from_the_file(self):
        values = resolve({}, env={"OPENAI_API_KEY": "sk-env", "BASHCUT_IMAGE_SERVICE": "openai"},
                         file_values={"apiKey": "from-file", "model": "dall-e-3"})
        self.assertEqual((values["service"], values["apiKey"], values["model"]), ("openai", "sk-env", "dall-e-3"))


class ServiceTests(Base):
    def test_gemini_sends_style_references_seed_and_shape(self):
        service = Service("gemini", "k1", endpoint=self.fake.url)
        style = Style("watercolour", [self.ref], 7)
        data = service.generate(style.text("a fox"), style, 1080, 1920)
        self.assertTrue(data.startswith(b"\x89PNG"))
        request = self.fake.requests[-1]
        self.assertEqual(request["path"], "/v1beta/models/gemini-2.5-flash-image:generateContent")
        self.assertEqual(request["headers"]["x-goog-api-key"], "k1")
        body = json.loads(request["body"])
        parts = body["contents"][0]["parts"]
        self.assertIn("Style: watercolour", parts[0]["text"])
        self.assertIn("reference pictures", parts[0]["text"])
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(body["generationConfig"]["imageConfig"]["aspectRatio"], "9:16")
        self.assertEqual(body["generationConfig"]["seed"], 7)

    def test_openai_generations_and_edits(self):
        service = Service("openai", "sk-1", endpoint=self.fake.url)
        service.generate("a fox", Style(), 1920, 1080)
        request = self.fake.requests[-1]
        self.assertEqual(request["path"], "/v1/images/generations")
        self.assertEqual(request["headers"]["authorization"], "Bearer sk-1")
        self.assertEqual(json.loads(request["body"]), {"model": "gpt-image-1", "prompt": "a fox", "size": "1536x1024", "n": 1})
        service.generate("a fox", Style(references=[self.ref]), 1080, 1920)
        request = self.fake.requests[-1]
        self.assertEqual(request["path"], "/v1/images/edits")
        self.assertIn("multipart/form-data", request["headers"]["content-type"])
        self.assertIn(b'name="image[]"; filename="mascot.png"', request["body"])
        self.assertIn(b"1024x1536", request["body"])

    def test_local_server_gets_no_key_header(self):
        Service("openai", "", endpoint=self.fake.url).generate("a fox", Style(), 512, 512)
        self.assertNotIn("authorization", self.fake.requests[-1]["headers"])

    def test_busy_is_tried_again(self):
        self.fake.busy_once = True
        waits = []
        Service("gemini", "k", endpoint=self.fake.url, sleep=waits.append).generate("a fox", Style(), 512, 512)
        self.assertEqual((len(self.fake.requests), waits), (2, [3]))

    def test_network_error_is_tried_again(self):
        self.fake.answers = ["drop"]
        waits = []
        data = Service("gemini", "k", endpoint=self.fake.url, sleep=waits.append).generate("a fox", Style(), 512, 512)
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertEqual((len(self.fake.requests), waits), (2, [3]))

    def test_quota_waits_as_asked(self):
        self.fake.answers = [(429, {"error": {"message": "Resource has been exhausted", "details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}})]
        waits, said = [], []
        service = Service("gemini", "k", endpoint=self.fake.url, sleep=waits.append, on_wait=said.append)
        self.assertTrue(service.generate("a fox", Style(), 512, 512).startswith(b"\x89PNG"))
        self.assertEqual((len(self.fake.requests), waits), (2, [8.0]))
        self.assertIn("waiting 7 s", said[0])
        self.fake.answers = [(429, {"error": {"message": "Quota exceeded for metric: generate_content_free_tier_requests, "
                                                         "limit: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}})]
        with self.assertRaises(ImageError) as caught:
            service.generate("a fox", Style(), 512, 512)
        self.assertEqual(caught.exception.code, "quota")
        self.assertIn("for today", caught.exception.message)
        self.assertEqual((len(self.fake.requests), waits), (3, [8.0]))

    def test_wrong_model_is_refused(self):
        with self.assertRaises(ImageError) as caught:
            Service("gemini", "k", model="gpt-image-1")
        self.assertEqual(caught.exception.code, "wrong_model")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["generate", "--prompt", "a fox", "--service", "gemini", "--model", "gpt-image-1",
                             "--api-key", "k", "--no-ffmpeg", "--out", os.path.join(self.dir, "pics")])
        self.assertEqual((code, json.loads(out.getvalue())["error"]["code"]), (1, "wrong_model"))
        self.assertEqual(self.fake.requests, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "pics", "images.json")))

    def test_edits_busy_is_tried_again(self):
        self.fake.busy_once = True
        waits = []
        service = Service("openai", "sk-1", endpoint=self.fake.url, sleep=waits.append)
        self.assertTrue(service.generate("a fox", Style(references=[self.ref]), 1080, 1920).startswith(b"\x89PNG"))
        first, again = self.fake.requests
        self.assertEqual((first["path"], again["path"]), ("/v1/images/edits", "/v1/images/edits"))
        self.assertEqual(first["body"], again["body"])
        self.assertEqual(first["headers"]["content-type"], again["headers"]["content-type"])
        self.assertIn(b'filename="mascot.png"', again["body"])
        self.assertEqual(waits, [3])


class GenerateTests(Base):
    def test_batch_writes_fitted_pictures_and_a_record(self):
        out = os.path.join(self.dir, "pics")
        scenes = self.scenes({"scenes": [{"id": "hook", "prompt": "a fox on a hill", "seconds": 1.5},
                                         {"id": "beat 2", "prompt": "the fox asleep"}], "width": 90, "height": 160})
        code, result = self.run_cli("generate", "--scenes", scenes, "--style-text", "flat pastel", "--reference", self.ref,
                                    "--out", out)
        self.assertEqual(code, 0, result)
        data = result["data"]
        self.assertEqual([i["id"] for i in data["images"]], ["hook", "beat 2"])
        self.assertEqual(os.path.basename(data["images"][1]["path"]), "beat-2.png")
        self.assertEqual(data["images"][0]["seconds"], 1.5)
        with open(data["images"][0]["path"], "rb") as f:
            self.assertEqual(decode_png(f.read())[:2], (90, 160))
        with open(os.path.join(out, "images.json"), encoding="utf-8") as f:
            record = json.load(f)["images"]
        self.assertTrue(all(e["aiGenerated"] and e["model"] == "gemini-2.5-flash-image" for e in record))
        self.assertEqual(record[0]["style"]["prompt"], "flat pastel")

    def test_the_style_is_locked_in_the_folder(self):
        out = os.path.join(self.dir, "pics")
        self.assertEqual(self.run_cli("generate", "--prompt", "one", "--style-text", "ink", "--out", out)[0], 0)
        code, result = self.run_cli("generate", "--prompt", "two", "--out", out)
        self.assertEqual(code, 0)
        self.assertEqual(result["data"]["style"]["prompt"], "ink")
        self.assertIn("Style: ink", json.loads(self.fake.requests[-1]["body"])["contents"][0]["parts"][0]["text"])
        code, result = self.run_cli("generate", "--prompt", "three", "--style-text", "oil", "--out", out)
        self.assertEqual((code, result["error"]["code"]), (1, "style_locked"))
        code, result = self.run_cli("generate", "--prompt", "three", "--style-text", "oil", "--restyle", "--out", out)
        self.assertEqual((code, result["data"]["style"]["prompt"]), (0, "oil"))
        out_style = io.StringIO()
        with redirect_stdout(out_style):
            cli.main(["style", "--out", out])
        self.assertEqual(json.loads(out_style.getvalue())["data"]["style"]["prompt"], "oil")

    def test_a_refused_scene_is_kept_out_and_the_rest_made(self):
        self.fake.refuse_word = "forbidden"
        code, result = self.run_cli("generate", "--prompt", "a forbidden thing", "--prompt", "a fox",
                                    "--out", os.path.join(self.dir, "pics"))
        self.assertEqual(code, 0)
        self.assertEqual([i["id"] for i in result["data"]["images"]], ["s2"])
        self.assertEqual(result["data"]["failed"][0]["id"], "s1")
        self.assertIn("IMAGE_SAFETY", result["data"]["failed"][0]["message"])

    def test_a_refused_key_stops_the_batch(self):
        self.fake.refuse_key = True
        code, result = self.run_cli("generate", "--prompt", "a", "--prompt", "b", "--out", os.path.join(self.dir, "pics"))
        self.assertEqual((code, result["error"]["code"]), (1, "auth"))
        self.assertEqual(len(self.fake.requests), 1)

    def test_usage_errors(self):
        code, result = self.run_cli("generate", "--out", self.dir)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))
        code, result = self.run_cli("generate", "--prompt", "a", "--reference", "/no/such.png", "--out", self.dir)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))


class ImportTests(Base):
    def test_illustrate_imports_labels_and_places_timed_scenes(self):
        scenes = self.scenes({"scenes": [{"id": "hook", "prompt": "a fox", "seconds": 2, "duration": 3},
                                         {"id": "spare", "prompt": "a hill"}], "width": 64, "height": 64})
        code, result = self.run_cli("illustrate", "--scenes", scenes, project=True)
        self.assertEqual(code, 0, result)
        state = self.state()
        self.assertEqual(len(state["items"]), 2)
        self.assertTrue(all(i["metadata"]["Comment"] == "AI-generated image (gemini-2.5-flash-image)" for i in state["items"]))
        self.assertEqual(state["items"][0]["metadata"]["Description"], "a fox")
        self.assertEqual(len(state["sequence"]["video"]), 2)  # V2 added
        placed = state["sequence"]["video"][1]["items"]
        self.assertEqual(len(placed), 1)
        self.assertEqual((placed[0]["start"], placed[0]["end"]), (2 * TPS, 5 * TPS))
        self.assertEqual(state["saved"], 1)
        self.assertTrue(result["data"]["out"].endswith(os.path.join("generated", "images")))
        ids = [s["id"] for s in state["log"]]
        self.assertEqual(ids.count("sequence.addTracks"), 1)

    def test_import_labels_a_known_picture_and_reports_overwrites(self):
        out = os.path.join(self.dir, "pics")
        code, made = self.run_cli("generate", "--prompt", "a fox", "--width", "64", "--height", "64", "--out", out)
        self.assertEqual(code, 0)
        path = made["data"]["images"][0]["path"]
        for start in (0, 1):
            code, result = self.run_cli("import", "--image", path, "--seconds", str(start), "--duration", "4",
                                        "--track", "V1", project=True)
            self.assertEqual(code, 0, result)
        self.assertEqual(len(result["data"]["pictures"][0]["replaced"]), 1)
        self.assertEqual(self.state()["items"][0]["metadata"]["Description"], "a fox")

    def test_import_needs_a_project_and_seconds_per_image(self):
        code, result = self.run_cli("import", "--image", self.ref, "--seconds", "1", "--seconds", "2", project=True)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))
        code, result = self.run_cli("import", "--image", self.ref)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))


class DownloadTests(Base):
    def test_a_dead_openai_link_fails_one_scene(self):
        self.fake.dead_link_word = "unreachable"
        out = os.path.join(self.dir, "pics")
        code, result = self.run_cli("generate", "--prompt", "an unreachable fox", "--prompt", "a fox", "--width", "64",
                                    "--height", "64", "--out", out, service="openai")
        self.assertEqual(code, 0, result)
        data = result["data"]
        self.assertEqual([i["id"] for i in data["images"]], ["s2"])
        self.assertEqual((data["failed"][0]["id"], data["failed"][0]["code"]), ("s1", "network"))
        self.assertIn("picture link did not answer", data["failed"][0]["message"])
        self.assertFalse(os.path.exists(os.path.join(out, "s1.png")))
        with open(os.path.join(out, "images.json"), encoding="utf-8") as f:
            self.assertEqual([e["id"] for e in json.load(f)["images"]], ["s2"])


if __name__ == "__main__":
    unittest.main()
