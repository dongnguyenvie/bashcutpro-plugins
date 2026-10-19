import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

try:
    import numpy as np
    from PIL import Image
except ImportError:  # the scripts run under uv with numpy and Pillow; skip where the test Python lacks them
    np = None

FOLDER = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, FOLDER / f'{name}.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def cube(fn, size=5):
    """A .cube text of a colour function fn on a size³ grid (R varies fastest)."""
    g = np.linspace(0, 1, size)
    rows = [fn(np.array([r, gg, b])) for b in g for gg in g for r in g]
    return f'LUT_3D_SIZE {size}\n' + '\n'.join(' '.join(f'{v:.5f}' for v in row) for row in rows) + '\n'


def hald(fn, level=2):
    """A HaldCLUT PNG (level² cube size, side level³) of fn."""
    n, side = level * level, level ** 3
    i = np.arange(side * side)
    rgb = np.stack([i % n, (i // n) % n, i // (n * n)], -1) / (n - 1)
    out = np.clip(np.array([fn(c) for c in rgb]), 0, 1)
    buf = io.BytesIO()
    Image.fromarray((out.reshape(side, side, 3) * 255).round().astype(np.uint8)).save(buf, 'PNG')
    return buf.getvalue()


@unittest.skipIf(np is None, 'numpy and Pillow not installed')
class FingerprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = load('fetch_luts')

    def test_titles(self):
        f = load('fetch_luts')
        cases = [('gmic', 'creative-bleachbypass_1', 'Bleach bypass 1'), ('gmic', 'creative-tealorange_2', 'Teal orange 2'),
                 ('gmic', 'negative_color-agfa_vista_200', 'Agfa Vista 200'), ('gmic', 'toddblankenship-bluehour', 'Blue hour'),
                 ('gmic', 'debrup_travel_films-cine-10', 'Cine 10'),
                 ('rawtherapee', 'Kodak-Kodak T-Max 400', 'Kodak T-Max 400'),
                 ('rawtherapee', 'Polaroid-Polaroid 669 2 -', 'Polaroid 669 2 -'),
                 ('rawtherapee', 'CreativePack-1-CrispWarm', 'Creative Pack 1 Crisp Warm'),
                 ('local', 'my_teal-look', 'My teal look'), ('local', 'TealOrange', 'Teal Orange')]
        for source, name, expected in cases:
            self.assertEqual(f.title(source, name), expected, name)

    def test_identity_cube_measures_unchanged(self):
        table = self.f.parse_cube(cube(lambda c: c))
        fp = self.f.fingerprint(lambda c: self.f.apply_lut(c, table))
        self.assertAlmostEqual(fp['contrast'], 1, places=2)
        self.assertAlmostEqual(fp['sat'], 1, places=2)
        self.assertLess(abs(fp['warm']) + abs(fp['skin_hue']) + abs(fp['black']), 0.5)
        self.assertEqual(self.f.auto_tags(fp), [])
        self.assertFalse(self.f.is_log(fp))

    def test_tags_from_numbers(self):
        warm = self.f.fingerprint(lambda c: np.clip(c * [1.12, 1.0, 0.85], 0, 1))
        self.assertIn('warm', self.f.auto_tags(warm))
        grey = self.f.fingerprint(lambda c: np.repeat(self.f.luma(c)[..., None], 3, -1))
        self.assertIn('bw', self.f.auto_tags(grey))
        matte = self.f.fingerprint(lambda c: 0.08 + c * 0.84)
        self.assertIn('matte', self.f.auto_tags(matte))

    def test_log_lut_detected(self):
        steep = self.f.fingerprint(lambda c: np.clip((c - 0.5) * 3 + 0.5, 0, 1))
        self.assertTrue(self.f.is_log(steep))

    def test_domain_and_broken_files(self):
        table = self.f.parse_cube('DOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n' + cube(lambda c: c, 3))
        self.assertAlmostEqual(float(self.f.apply_lut(np.array([0.5, 0.5, 0.5]), table)[0]), 0.25, places=3)
        for text in ('LUT_1D_SIZE 4\n0 0 0\n', 'LUT_3D_SIZE 3\n0 0 0\n', 'nothing'):
            with self.assertRaises(ValueError):
                self.f.parse_cube(text)

    def test_hald_matches_the_same_cube(self):
        fn = lambda c: np.clip(c ** 0.8 * [1.05, 1.0, 0.9], 0, 1)
        from_hald = self.f.hald_to_table(Image.open(io.BytesIO(hald(fn))))
        from_cube = self.f.parse_cube(cube(fn, 4))
        self.assertEqual(from_hald.shape, (4, 4, 4, 3))
        self.assertLess(float(np.abs(from_hald - from_cube).max()), 0.01)
        self.assertEqual(self.f.resample(from_hald, 9).shape, (9, 9, 9, 3))


@unittest.skipIf(np is None, 'numpy and Pillow not installed')
class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        env = mock.patch.dict(os.environ, {'BASHCUT_SHARED_DATA': str(self.tmp / 'data'),
                                           'BASHCUT_SHARED_CACHE': str(self.tmp / 'cache')})
        env.start()
        self.addCleanup(env.stop)
        self.f = load('fetch_luts')
        own = self.tmp / 'own'
        own.mkdir()
        (own / 'warm.cube').write_text(cube(lambda c: np.clip(c * [1.15, 1.0, 0.8], 0, 1)))
        (own / 'warm-copy.cube').write_text(cube(lambda c: np.clip(c * [1.15, 1.0, 0.8], 0, 1)))  # same bytes
        (own / 'warm-near.cube').write_text(cube(lambda c: np.clip(c * [1.151, 1.0, 0.8], 0, 1)))  # same look
        (own / 'teal.png').write_bytes(hald(lambda c: np.clip(c * [0.85, 1.0, 1.15], 0, 1)))
        (own / 'log.cube').write_text(cube(lambda c: np.clip((c - 0.5) * 3 + 0.5, 0, 1)))
        (own / 'broken.cube').write_text('LUT_3D_SIZE 3\n1 2\n')
        self.own = own
        self.added = []

    def run_main(self, *args):
        calls = []

        def fake(*argv, retry=True):
            calls.append(argv)
            if argv[:2] == ('library', 'list'):  # what earlier runs added
                return {'items': [{'params': json.loads(dict(zip(a[2::2], a[3::2]))['--params'])}
                                  for a in self.added], 'next': None}
            return {'id': 'x'}

        out = io.StringIO()
        with mock.patch.object(self.f, 'bashcut', fake), mock.patch('sys.stdout', out):
            self.f.main(['--source', '', '--folder', str(self.own), *args])
        adds = [c for c in calls if c[:2] == ('library', 'add')]
        self.added += adds
        return out.getvalue().strip().splitlines(), adds

    def test_one_line_with_duplicates_log_and_broken(self):
        lines, adds = self.run_main()
        self.assertEqual(lines, ['3 added, 2 duplicates, 1 log, 1 broken, 0 already in the library'])
        self.assertEqual(len(adds), 3)
        args = dict(zip(adds[0][2::2], adds[0][3::2]))
        params = json.loads(args['--params'])
        self.assertIn('fp', params['lut'])
        self.assertTrue(Path(params['lut']['file']).exists())
        self.assertTrue(args['--tags'].startswith('lut,local'))
        tags = [dict(zip(a[2::2], a[3::2]))['--tags'] for a in adds]
        self.assertTrue(any('log' in t for t in tags))
        self.assertTrue(any('warm' in t for t in tags))
        again, adds = self.run_main()  # files kept from the first run are not measured again
        self.assertEqual(adds, [])
        self.assertEqual(again, ['0 added, 1 duplicates, 0 log, 1 broken, 4 already in the library'])

    def test_title_is_readable_and_the_stem_stays_the_id(self):
        _, adds = self.run_main()
        warm = next(dict(zip(a[2::2], a[3::2])) for a in adds if dict(zip(a[2::2], a[3::2]))['--id'] == 'lut-local-warm-copy')
        self.assertEqual(warm['--name'], 'Warm copy')
        params = json.loads(warm['--params'])
        self.assertEqual(params['lutName'], 'Warm copy')
        self.assertEqual(params['lut']['name'], 'warm-copy')
        self.assertEqual(Path(params['lut']['file']).name, 'warm-copy.cube')

    def test_earlier_looks_are_renamed_unless_renamed_since(self):
        calls = []
        items = [
            {'id': 'lut-gmic-creative-bleachbypass-1', 'name': 'creative-bleachbypass_1',
             'params': {'lutName': 'creative-bleachbypass_1', 'lut': {'source': 'gmic', 'sha': 'a'}}},
            {'id': 'lut-gmic-creative-anime', 'name': 'My anime',  # renamed by someone: kept
             'params': {'lutName': 'creative-anime', 'lut': {'source': 'gmic'}}},
            {'id': 'lut-gmic-creative-moonlight', 'name': 'Moonlight',  # already has its title
             'params': {'lutName': 'Moonlight', 'lut': {'source': 'gmic', 'name': 'creative-moonlight'}}},
        ]
        with mock.patch.object(self.f, 'bashcut', lambda *a, **k: calls.append(a) or {'id': 'x'}):
            self.assertEqual(self.f.retitle(items, 'user'), 1)
        self.assertEqual(len(calls), 1)
        args = dict(zip(calls[0][3::2], calls[0][4::2]))
        self.assertEqual(calls[0][:3], ('library', 'update', 'lut-gmic-creative-bleachbypass-1'))
        self.assertEqual(args['--name'], 'Bleach bypass 1')
        params = json.loads(args['--params'])
        self.assertEqual((params['lutName'], params['lut']['name'], params['lut']['sha']),
                         ('Bleach bypass 1', 'creative-bleachbypass_1', 'a'))

    def test_limit_caps_what_is_looked_at(self):
        lines, adds = self.run_main('--limit', '2')
        self.assertLessEqual(len(adds), 2)
        self.assertEqual(len(lines), 1)

    def test_failed_source_is_one_line(self):
        def boom(quota):
            raise OSError('curl exit 22')
            yield
        out = io.StringIO()
        with mock.patch.object(self.f, 'gmic', boom), mock.patch.object(self.f, 'bashcut', lambda *a, **k: {}), \
                mock.patch('sys.stdout', out):
            self.f.main(['--source', 'gmic', '--no-library'])
        self.assertEqual(out.getvalue().splitlines()[0], 'gmic: skipped (curl exit 22)')
        self.assertEqual(len(out.getvalue().splitlines()), 2)


if __name__ == '__main__':
    unittest.main()
