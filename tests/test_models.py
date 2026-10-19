"""models.json, tools/check_models.py and the downloader (tools/bashcut_models.py) against a local HTTP server."""

import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import bashcut_models  # noqa: E402
import check_models  # noqa: E402

BODY = bytes(range(256)) * 4096  # 1 MB
SHA = hashlib.sha256(BODY).hexdigest()


class Handler(http.server.BaseHTTPRequestHandler):
    """/good serves BODY (honours Range), /bad serves other bytes, /norange ignores Range, /missing is a 404."""

    requests = []

    def do_GET(self):
        Handler.requests.append((self.path, self.headers.get("Range")))
        if self.path == "/missing":
            self.send_error(404)
            return
        body = BODY if self.path in ("/good", "/norange") else b"x" * len(BODY)
        start = 0
        ranged = self.headers.get("Range")
        if ranged and self.path != "/norange":
            start = int(ranged.split("=")[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(body) - 1, len(body)))
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])

    def log_message(self, *args):
        pass


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        Handler.requests = []

    def models(self, url, mirror=None, sha=SHA):
        f = {"path": "sub/model.bin", "size": len(BODY), "sha256": sha, "url": self.base + url}
        if mirror:
            f["mirror"] = self.base + mirror
        manifest = {"schema": "bashcut-models/1", "models": [{"id": "demo", "license": "MIT", "files": [f]}]}
        return bashcut_models.Models(manifest, root=self.root, log=lambda message: None)

    def target(self):
        return os.path.join(self.root, "demo", "sub", "model.bin")

    def test_good_file_is_verified_and_cached(self):
        models = self.models("/good")
        folder = models.fetch("demo")
        with open(self.target(), "rb") as f:
            self.assertEqual(f.read(), BODY)
        self.assertEqual(folder, os.path.join(self.root, "demo"))
        models.fetch("demo")  # cached: no second request
        self.assertEqual(len(Handler.requests), 1)

    def test_wrong_hash_is_refused_and_nothing_is_kept(self):
        with self.assertRaises(bashcut_models.ModelError) as raised:
            self.models("/bad").fetch("demo")
        self.assertEqual(raised.exception.code, "download_failed")
        self.assertIn("SHA-256", raised.exception.message)
        self.assertFalse(os.path.exists(self.target()))
        self.assertFalse(os.path.exists(self.target() + ".part"))

    def test_mirror_is_used_when_the_primary_fails(self):
        self.models("/missing", mirror="/good").fetch("demo")
        self.assertTrue(os.path.isfile(self.target()))
        self.assertEqual([p for p, _ in Handler.requests], ["/missing", "/good"])

    def test_resume_asks_for_the_rest_only(self):
        os.makedirs(os.path.dirname(self.target()))
        with open(self.target() + ".part", "wb") as f:
            f.write(BODY[:300000])
        self.models("/good").fetch("demo")
        self.assertEqual(Handler.requests, [("/good", "bytes=300000-")])
        with open(self.target(), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), SHA)

    def test_server_ignoring_the_range_starts_over(self):
        os.makedirs(os.path.dirname(self.target()))
        with open(self.target() + ".part", "wb") as f:
            f.write(b"stale" * 100)
        self.models("/norange").fetch("demo")
        with open(self.target(), "rb") as f:
            self.assertEqual(f.read(), BODY)

    def test_missing_checksum_refuses_before_downloading(self):
        with self.assertRaises(bashcut_models.ModelError) as raised:
            self.models("/good", sha="").fetch("demo")
        self.assertEqual(raised.exception.code, "no_checksum")
        self.assertEqual(Handler.requests, [])

    def test_hf_cache_links_the_snapshot(self):
        manifest = {"schema": "bashcut-models/1", "models": [{
            "id": "demo", "license": "MIT", "huggingface": {"repo": "org/name", "revision": "a" * 40},
            "files": [{"path": "sub/model.bin", "size": len(BODY), "sha256": SHA, "url": self.base + "/good"}]}]}
        models = bashcut_models.Models(manifest, root=self.root, log=lambda message: None)
        models.fetch("demo")
        hf_home = os.path.join(self.root, "hf")
        snapshot = models.hf_cache("demo", hf_home)
        self.assertTrue(os.path.isfile(os.path.join(snapshot, "sub", "model.bin")))
        with open(os.path.join(hf_home, "hub", "models--org--name", "refs", "main")) as f:
            self.assertEqual(f.read(), "a" * 40)


class CheckTests(unittest.TestCase):
    def test_repository_models_are_fine(self):
        self.assertEqual(check_models.check(), [])

    def test_problems_are_reported(self):
        bad = {"schema": "bashcut-models/1", "models": [
            {"id": "a", "license": "MIT", "huggingface": {"repo": "o/n", "revision": "b" * 40},
             "files": [{"path": "x", "size": 3, "sha256": "", "url": "https://huggingface.co/o/n/resolve/main/x"}]},
            {"id": "b", "files": [{"path": "../y", "size": 0, "sha256": "c" * 64, "url": "ftp://host/y"}]},
        ]}
        problems, _ = check_models.check_models(bad)
        text = "\n".join(problems)
        for expected in ("has no SHA-256", "not pinned", "license is required", "inside the model folder",
                         "size in bytes", "https URL"):
            self.assertIn(expected, text)

    def test_every_listed_model_is_loadable(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models = bashcut_models.Models.load(os.path.join(root, "models.json"), root=tempfile.gettempdir())
        for mid in models.models:
            self.assertTrue(models.model(mid)["files"])


if __name__ == "__main__":
    unittest.main()
