"""Downloads retry transient curl errors and HTTP 429/5xx, then name what was skipped (BashCut #654); no network."""
import importlib.util
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
from test_fetch_assets import PEXELS_SEARCH, PIL, SCRIPTS, FakeBashCut, FakeWeb, jpeg, load, run  # noqa: E402

try:
    import numpy as np  # fetch_luts needs it
except ImportError:
    np = None


def unswapped():
    """fetch_stock with its own get_file, which runs curl -o (load() swaps it for one that answers through get)."""
    spec = importlib.util.spec_from_file_location('fetch_stock', SCRIPTS / 'fetch_stock.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_every_script_downloads_with_the_same_get(self):
        for name in ('facts', 'font', 'fonts', 'luts', 'lottie'):
            if name == 'luts' and np is None:
                continue  # fetch_luts imports numpy
            script = load(f'fetch_{name}.py')
            self.assertEqual((script.get.__module__, script.get.__name__), ('fetch_stock', 'get'), name)
            source = (SCRIPTS / f'fetch_{name}.py').read_text(encoding='utf-8')
            self.assertNotIn('urlopen', source, name)
            self.assertNotRegex(source, r'(?m)^def get\(', name)

    def test_a_post_sends_the_body_and_is_retried(self):
        body = b'{"query": "{ search }"}'
        answers = [done(0, b'busy', b'503'), done(0, b'{"data": 1}')]
        with mock.patch.object(self.stock.subprocess, 'run', side_effect=answers) as curl:
            got = self.stock.get('https://graphql.lottiefiles.com/', headers={'Content-Type': 'application/json'},
                                 data=body)
        self.assertEqual(got, b'{"data": 1}')
        self.assertEqual(curl.call_count, 2)
        for call in curl.call_args_list:
            command = call.args[0]
            self.assertEqual(call.kwargs['input'], body)
            self.assertIn('--data-binary', command)
            self.assertEqual(command[command.index('--data-binary') + 1], '@-')
            self.assertEqual(command[command.index('-H') + 1], 'Content-Type: application/json')
            self.assertEqual(command[-1], 'https://graphql.lottiefiles.com/')
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1.5])

    def test_the_agent_is_sent_and_a_404_raises_not_found(self):
        with mock.patch.object(self.stock.subprocess, 'run', return_value=done(0, b'gone', b'404')) as curl, \
                self.assertRaises(self.stock.NotFound) as failed:
            self.stock.get('https://en.wikipedia.org/api/rest_v1/page/summary/Nowhere', agent='bashcut-facts/1.0')
        command = curl.call_args.args[0]
        self.assertEqual(command[command.index('-A') + 1], 'bashcut-facts/1.0')
        self.assertEqual(curl.call_count, 1)
        self.assertIsInstance(failed.exception, self.stock.FetchError)
        self.assertEqual(failed.exception.reason, 'http 404')
        self.sleep.assert_not_called()
        with mock.patch.object(self.stock.subprocess, 'run', return_value=done(0, b'ok')) as curl:
            self.stock.get('https://example.org/x')
        command = curl.call_args.args[0]
        self.assertEqual(command[command.index('-A') + 1], self.stock.AGENT)

    def test_get_file_writes_through_a_part_file(self):
        stock = unswapped()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'clip.mp4'
            part = Path(folder) / 'clip.mp4.part'
            seen = []

            def curl(command, **kwargs):
                target = Path(command[command.index('-o') + 1])
                seen.append((target, part.exists()))
                if len(seen) == 1:
                    target.write_bytes(b'half')  # a reset halfway
                    return done(56)
                target.write_bytes(b'the whole clip')
                return done(0)

            with mock.patch.object(stock.subprocess, 'run', side_effect=curl):
                size = stock.get_file('https://videos.pexels.com/1.mp4', path)
            self.assertEqual(size, len(b'the whole clip'))
            self.assertEqual(path.read_bytes(), b'the whole clip')
            self.assertFalse(part.exists())
            self.assertEqual(seen, [(part, False), (part, False)])  # the failed try left no .part behind
            self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1.5])
            with mock.patch.object(stock.subprocess, 'run', side_effect=lambda c, **k: done(56)) as curl, \
                    self.assertRaises(stock.FetchError) as failed:
                stock.get_file('https://videos.pexels.com/2.mp4', Path(folder) / 'other.mp4')
            self.assertEqual((curl.call_count, failed.exception.reason), (3, 'curl exit 56 after 3 tries'))
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['clip.mp4'])

    def test_get_file_over_the_cap_stops_and_is_not_retried(self):
        stock = unswapped()
        cap = 1000
        # (curl exit, bytes it left in the .part): the size was announced, an unknown size cut at the cap, an old
        # curl that did not stop
        for code, written in ((63, 0), (56, cap), (0, cap + 500)):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'big.mp4'

                def curl(command, **kwargs):
                    if written:
                        Path(command[command.index('-o') + 1]).write_bytes(b'x' * written)
                    return done(code)

                with mock.patch.object(stock.subprocess, 'run', side_effect=curl) as run_curl, \
                        self.assertRaises(stock.TooLarge) as failed:
                    stock.get_file('https://videos.pexels.com/big.mp4', path, max_bytes=cap)
                command = run_curl.call_args.args[0]
                self.assertEqual(command[command.index('--max-filesize') + 1], str(cap), code)
                self.assertEqual(run_curl.call_count, 1, code)
                self.assertIsInstance(failed.exception, stock.FetchError)
                self.assertIn('cap', failed.exception.reason)
                self.assertEqual(list(Path(folder).iterdir()), [], code)
        self.sleep.assert_not_called()

    @unittest.skipIf(PIL is None, 'Pillow not installed')
    def test_a_video_over_the_cap_is_deferred(self):
        def video(n):
            return {'id': n, 'url': f'https://www.pexels.com/video/{n}/', 'duration': 9, 'image': f'https://images.pexels.com/{n}.jpg',
                    'user': {'name': 'Cy'}, 'video_files': [
                        {'width': 1080, 'height': 1920, 'link': f'https://videos.pexels.com/{n}-hd.mp4'}],
                    'video_pictures': [{'picture': f'https://images.pexels.com/{n}-{i}.jpg'} for i in range(3)]}
        web = FakeWeb([('https://api.pexels.com/videos/search', {'videos': [video(1), video(2), video(3)]}),
                       ('https://videos.pexels.com/1-', b'1' * 1_500_000),  # over the 1 MB cap: deferred
                       ('https://videos.pexels.com/2-', b'2' * 400_000),  # fits what is left
                       ('https://videos.pexels.com/3-', b'3' * 400_000),
                       ('https://images.pexels.com/', jpeg())])
        fake = FakeBashCut()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'PEXELS_API_KEY': 'k'}, clear=True), \
                mock.patch.object(self.stock, 'get', web), mock.patch.object(self.stock, 'bashcut', fake):
            rows = run(self.stock, ['--source', 'pexels', '--kind', 'video', '--query', 'river', '--candidates', '3',
                                    '--dest', folder, '--max-mb', '1'])
            files = sorted(p.name for p in Path(folder).glob('*.mp4'))
            manifest = json.loads((Path(folder) / 'candidates.json').read_text(encoding='utf-8'))
        self.assertEqual(rows[0], {'slot': 'river', 'candidates': 3, 'inLibrary': 2, 'deferred': 1})
        self.assertTrue(rows[1]['capped'])
        self.assertEqual(files, ['pexels-2-stock.mp4', 'pexels-3-stock.mp4'])  # no tags: named before 'words' is set
        first = next(r for r in manifest if r.get('n') == 1)
        self.assertTrue(first['deferred'])
        self.assertNotIn('error', first)
        self.assertEqual(len(fake.adds), 2)

    def test_save_reuses_an_item_and_place_reports_its_error(self):
        added = {'already': {'error': 'the library already has project:already'}, 'other': {'error': 'disk full'},
                 'new': {'item': {'reference': 'project:lib-9'}}}
        calls = []

        def fake(*args, retry=True):
            calls.append(args)
            if args[:2] == ('library', 'add'):
                return added[args[args.index('--id') + 1]]
            if args[2] == 'project:gone':
                return {'error': 'no such library item: project:gone'}
            return {'rev': 7}

        with mock.patch.object(self.stock, 'bashcut', fake):
            self.assertEqual(self.stock.save('project', 'already', '--kind', 'clip'), {'id': 'project:already'})
            self.assertEqual(self.stock.save('project', 'other'), {'error': 'disk full'})
            self.assertEqual(self.stock.save('project', 'new'), {'id': 'project:lib-9'})
            self.assertEqual(self.stock.place('project:gone', 12),
                             {'placeError': 'no such library item: project:gone'})
            self.assertEqual(self.stock.place('project:lib-9', 48, duration=60), {'placed': 48, 'rev': 7})
        self.assertEqual(calls[0][:6], ('library', 'add', '--scope', 'project', '--id', 'already'))
        self.assertEqual(calls[-1], ('library', 'place', 'project:lib-9', '--at-frame', '48', '--base-rev', 'last',
                                     '--duration', '60'))

    def test_a_stale_revision_is_retried_in_every_script(self):
        stale = json.dumps({'error': {'message': 'the project changed', 'data': {'category': 'stale_revision'}}})
        for name in ('luts', 'facts', 'fonts'):
            if name == 'luts' and np is None:
                continue
            script = load(f'fetch_{name}.py')
            self.assertEqual((script.bashcut.__module__, script.bashcut.__name__), ('fetch_stock', 'bashcut'), name)
            answers = [subprocess.CompletedProcess([], 1, stale, ''), subprocess.CompletedProcess([], 0, '{}', ''),
                       subprocess.CompletedProcess([], 0, '{"id": "user:x"}', '')]
            with mock.patch('subprocess.run', side_effect=answers) as cli:
                self.assertEqual(script.bashcut('library', 'add', '--id', 'x'), {'id': 'user:x'}, name)
            self.assertEqual([c.args[0][1:3] for c in cli.call_args_list],
                             [['library', 'add'], ['context', 'get'], ['library', 'add']], name)
            again = [subprocess.CompletedProcess([], 1, stale, '')] * 3
            with mock.patch('subprocess.run', side_effect=again) as cli:
                self.assertEqual(script.bashcut('library', 'add'), {'error': 'the project changed'}, name)
            self.assertEqual(cli.call_count, 3, name)  # once more only


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
        return rows, fake, json.loads((Path(folder) / 'candidates.json').read_text(encoding='utf-8'))

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
