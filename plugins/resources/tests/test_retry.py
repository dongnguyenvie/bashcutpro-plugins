"""Downloads retry transient curl errors and HTTP 429/5xx, then name what was skipped (BashCut #654); no network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fetch_assets import PEXELS_SEARCH, PIL, FakeBashCut, jpeg, load, run  # noqa: E402


def done(code, body=b'', status=b'200'):
    return subprocess.CompletedProcess([], code, body, status if code == 0 else b'000')


class FakeCurl:
    """Answers subprocess.run(["curl", …, url]) per URL prefix from a list of answers; the last one repeats."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, command, **kwargs):
        url = command[-1]
        with self.lock:
            self.calls.append(url)
            seen = sum(1 for u in self.calls if u == url)
        for prefix, answers in self.routes:
            if url.startswith(prefix):
                return answers[min(seen, len(answers)) - 1]
        return done(0, b'', b'404')

    def count(self, url):
        return sum(1 for u in self.calls if u == url)


class GetTests(unittest.TestCase):
    def setUp(self):
        self.stock = load('stock-images/fetch_stock.py')
        sleep = mock.patch.object(self.stock.time, 'sleep')
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_a_reset_and_a_503_are_tried_again_with_backoff(self):
        answers = [done(56), done(0, b'busy', b'503'), done(0, b'picture', b'200')]
        with mock.patch.object(self.stock.subprocess, 'run', side_effect=answers) as curl:
            self.assertEqual(self.stock.get('https://upload.wikimedia.org/a.jpg'), b'picture')
        self.assertEqual(curl.call_count, 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1.5, 3.0])

    def test_every_transient_code_and_429_is_retried(self):
        for answer in [*(done(code) for code in (6, 7, 28, 35, 56)), done(0, b'', b'429'), done(0, b'', b'502')]:
            with mock.patch.object(self.stock.subprocess, 'run', side_effect=[answer, done(0, b'ok')]) as curl:
                self.assertEqual(self.stock.get('https://example.org/x'), b'ok')
            self.assertEqual(curl.call_count, 2, answer)

    def test_gives_up_after_three_tries_with_the_reason(self):
        with mock.patch.object(self.stock.subprocess, 'run', return_value=done(56)) as curl, \
                self.assertRaises(self.stock.FetchError) as failed:
            self.stock.get('https://api.openverse.org/x.jpg?token=1')
        self.assertEqual(curl.call_count, 3)
        self.assertEqual(failed.exception.reason, 'curl exit 56 after 3 tries')
        self.assertEqual(str(failed.exception), 'curl exit 56 after 3 tries for https://api.openverse.org/x.jpg')

    def test_a_404_or_a_bad_url_is_not_retried(self):
        for answer, reason in ((done(0, b'gone', b'404'), 'http 404'), (done(3), 'curl exit 3')):
            with mock.patch.object(self.stock.subprocess, 'run', return_value=answer) as curl, \
                    self.assertRaises(OSError) as failed:
                self.stock.get('https://example.org/missing')
            self.assertEqual((curl.call_count, failed.exception.reason), (1, reason))
        self.sleep.assert_not_called()

    def test_fetch_audio_downloads_with_the_same_get(self):
        audio = load('audio-mix/fetch_audio.py')
        self.assertEqual((audio.get.__module__, audio.get.__name__), ('fetch_stock', 'get'))


@unittest.skipIf(PIL is None, 'Pillow not installed')
class BatchTests(unittest.TestCase):
    def setUp(self):
        self.stock = load('stock-images/fetch_stock.py')
        for patch in (mock.patch.object(self.stock.time, 'sleep'),
                      mock.patch.dict(os.environ, {'PEXELS_API_KEY': 'k'}, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def batch(self, curl, folder):
        fake = FakeBashCut()
        with mock.patch.object(self.stock.subprocess, 'run', curl), mock.patch.object(self.stock, 'bashcut', fake):
            rows = run(self.stock, ['--source', 'pexels', '--query', 'clay pot', '--candidates', '3', '--dest', folder])
        return rows, fake, json.loads((Path(folder) / 'candidates.json').read_text())

    def test_a_flaky_file_is_retried_until_it_arrives(self):
        flaky = 'https://images.pexels.com/101.jpeg?auto=compress&w=1080'
        curl = FakeCurl([('https://api.pexels.com/', [done(0, json.dumps(PEXELS_SEARCH).encode())]),
                         (flaky, [done(56), done(56), done(0, jpeg())]),
                         ('https://images.pexels.com/', [done(0, jpeg())])])
        with tempfile.TemporaryDirectory() as folder:
            rows, fake, manifest = self.batch(curl, folder)
        self.assertEqual(rows[0], {'slot': 'clay-pot', 'candidates': 3, 'inLibrary': 3})
        self.assertEqual(curl.count(flaky), 3)
        self.assertEqual(len(fake.adds), 3)
        self.assertFalse(any(r.get('error') for r in manifest))

    def test_a_file_that_keeps_failing_is_named_and_the_rest_are_kept(self):
        dead = 'https://images.pexels.com/102.jpeg?auto=compress&w=1080'
        curl = FakeCurl([('https://api.pexels.com/', [done(0, json.dumps(PEXELS_SEARCH).encode())]),
                         (dead, [done(56)]), ('https://images.pexels.com/', [done(0, jpeg())])])
        with tempfile.TemporaryDirectory() as folder:
            rows, fake, manifest = self.batch(curl, folder)
            files = sorted(p.name for p in Path(folder).glob('pexels-*'))
        self.assertEqual(curl.count(dead), 3)
        self.assertEqual(rows[0], {'slot': 'clay-pot', 'candidates': 3, 'inLibrary': 2, 'skipped': [
            {'n': 3, 'source': 'pexels', 'id': '102', 'url': dead,
             'error': 'download failed: curl exit 56 after 3 tries'}]})
        self.assertEqual(len(files), 2)
        self.assertEqual(len(fake.adds), 2)
        self.assertIn('sheets', rows[1])
        self.assertEqual(next(r for r in manifest if r.get('n') == 3)['error'],
                         'download failed: curl exit 56 after 3 tries')


if __name__ == '__main__':
    unittest.main()
