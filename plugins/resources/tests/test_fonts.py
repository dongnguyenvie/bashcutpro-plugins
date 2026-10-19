"""Open font library (captions-text/fetch_fonts.py) with mocked web and bashcut: no network, no app."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
SYSTEM = Path('/System/Library/Fonts/Supplemental')


def load():
    path = SCRIPTS / 'fetch_fonts.py'
    spec = importlib.util.spec_from_file_location('fetch_fonts', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def has(module):
    return importlib.util.find_spec(module) is not None


def run(module, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        module.main(argv)
    return [json.loads(line) for line in out.getvalue().splitlines()]


CATALOGUE = [
    {'id': 'be-vietnam-pro', 'family': 'Be Vietnam Pro', 'subsets': ['latin', 'vietnamese'], 'category': 'sans-serif',
     'weights': [100, 400, 700, 900], 'styles': ['normal', 'italic'], 'type': 'google', 'lastModified': '2025-01-01'},
    {'id': 'anton', 'family': 'Anton', 'subsets': ['latin', 'vietnamese'], 'category': 'sans-serif',
     'weights': [400], 'styles': ['normal'], 'type': 'google', 'lastModified': '2025-01-01'},
    {'id': 'lora', 'family': 'Lora', 'subsets': ['latin', 'vietnamese'], 'category': 'serif',
     'weights': [400, 700], 'styles': ['normal'], 'type': 'google', 'lastModified': '2025-01-01'},
    {'id': 'latin-only', 'family': 'Latin Only', 'subsets': ['latin'], 'category': 'sans-serif',
     'weights': [400], 'styles': ['normal'], 'type': 'google', 'lastModified': '2025-01-01'},
    {'id': 'not-google', 'family': 'Not Google', 'subsets': ['latin', 'vietnamese'], 'category': 'display',
     'weights': [400], 'type': 'other'},
]
MEASURES = {'Be Vietnam Pro': {'xh': 0.72, 'width': 1.05, 'contrast': 1.12, 'stem': 0.18, 'round': 1.18, 'ends': 1.0},
            'Anton': {'xh': 0.85, 'width': 0.62, 'contrast': 1.3, 'stem': 0.25, 'round': 0.67, 'ends': 1.0},
            'Lora': {'xh': 0.71, 'width': 1.05, 'contrast': 2.6, 'stem': 0.17, 'round': 1.1, 'ends': 2.6}}


class FontLibraryTests(unittest.TestCase):
    def setUp(self):
        self.ff = load()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'BASHCUT_SHARED_DATA': str(root / 'data'),
                                                'BASHCUT_SHARED_CACHE': str(root / 'cache')})
        self.env.start()
        self.measured = []

        def fake_measure(font, folder):
            self.measured.append(font['family'])
            font['m'] = dict(MEASURES.get(font['family'], MEASURES['Be Vietnam Pro']))
            return font
        self.catalogue = [dict(f) for f in CATALOGUE]
        patches = [mock.patch.object(self.ff, 'get', lambda url, **kw: json.dumps(self.catalogue).encode()),
                   mock.patch.object(self.ff, 'measure_family', fake_measure)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_tags_from_measures(self):
        tag = lambda family, **extra: self.ff.tags_of({'category': 'sans-serif', 'weights': [400, 900],
                                                       'm': {**MEASURES[family], **extra}})
        self.assertIn('condensed', tag('Anton'))
        self.assertIn('bold-display', tag('Anton'))
        self.assertIn('geometric', tag('Be Vietnam Pro'))
        self.assertIn('mono-line', tag('Be Vietnam Pro'))
        self.assertIn('rounded', tag('Be Vietnam Pro', ends=0.6))
        self.assertIn('high-contrast', tag('Lora'))
        self.assertNotIn('rounded', self.ff.tags_of({'category': 'serif', 'm': {**MEASURES['Lora'], 'ends': 0.5}}))

    def test_ingest_prints_one_line_and_measures_only_new_or_changed_families(self):
        first = run(self.ff, ['--ingest'])
        self.assertEqual(len(first), 1)
        self.assertEqual({k: first[0][k] for k in ('families', 'vietnamese', 'measured', 'new', 'changed')},
                         {'families': 4, 'vietnamese': 3, 'measured': 3, 'new': 4, 'changed': 0})
        self.assertEqual(sorted(self.measured), ['Anton', 'Be Vietnam Pro', 'Lora'])  # Latin Only is not measured
        self.measured.clear()
        self.assertEqual(run(self.ff, ['--ingest'])[0]['new'], 0)
        self.assertEqual(self.measured, [])
        self.catalogue[2]['weights'] = [400, 500, 700]
        again = run(self.ff, ['--ingest'])[0]
        self.assertEqual((again['changed'], self.measured), (1, ['Lora']))

    def test_ingest_limit_and_metadata_only(self):
        self.assertEqual(run(self.ff, ['--ingest', '--no-measure'])[0]['measured'], 0)
        self.assertEqual(run(self.ff, ['--ingest', '--limit', '1'])[0]['measured'], 1)

    def test_rank_uses_target_tags_and_earlier_picks_of_the_recipe(self):
        run(self.ff, ['--ingest'])
        families = self.ff.read_json(self.ff.shared('data') / 'index.json', {})['families']
        scopes = self.ff.scopes_for('food', None)
        order = self.ff.rank(families, 'vietnamese', 'title', ['condensed'], {}, scopes)
        self.assertEqual(order[0], 'anton')
        self.assertNotIn('latin-only', order)
        self.assertNotIn('not-google', order)
        picks = {}
        self.ff.count(picks, scopes, ['lora'], 'picked')
        self.assertEqual(self.ff.rank(families, 'vietnamese', 'title', ['condensed'], picks, scopes)[0], 'lora')
        other = self.ff.scopes_for('travel', None)  # another recipe: only the small all-recipes lift
        self.assertEqual(self.ff.rank(families, 'vietnamese', 'title', ['condensed'], picks, other)[0], 'anton')
        self.assertEqual(self.ff.rank(families, 'vietnamese', 'caption', ['serif'], {}, scopes), ['lora'])

    @unittest.skipUnless(has('PIL') and (SYSTEM / 'Arial.ttf').exists(), 'needs Pillow and macOS fonts')
    def test_candidates_draw_one_sheet_and_leave_out_fonts_missing_letters(self):
        run(self.ff, ['--ingest'])
        arial = str(SYSTEM / 'Arial.ttf')
        full = {ord(c): 'g' for c in set(self.ff.LETTERS['vietnamese'] + 'Phở bò ĐàLạt,0123456789')}
        lacking = {k: v for k, v in full.items() if k != ord('ộ')}
        cmap = lambda path: lacking if 'lora' in str(path) else full

        def subset(family, weight, text, folder):
            path = Path(folder) / f"{self.ff.single.font_id(family)}.ttf"
            path.write_bytes(Path(arial).read_bytes())
            return path
        with mock.patch.object(self.ff, 'subset_font', subset), mock.patch.object(self.ff, 'cmap_of', cmap), \
                mock.patch.object(self.ff, 'bashcut', lambda *a: {'plan': {'recipe': 'food'}}):
            rows = run(self.ff, ['--candidates', '--role', 'caption', '--n', '3', '--text', 'Phở bò Đà Lạt'])
        head, lines = rows[0], rows[1:]
        self.assertTrue(Path(head['sheet']).exists())
        self.assertEqual((head['recipe'], head['glyphMisses'], head['shown']), ('food', 1, 2))
        self.assertEqual([r['family'] for r in lines], ['Be Vietnam Pro', 'Anton'])  # Lora lacks ộ
        self.assertEqual(set(lines[0]), {'n', 'family', 'weight', 'tags'})
        self.assertEqual(lines[0]['weight'], 700)
        picks = json.loads((self.ff.shared('data') / 'picks.json').read_text())
        self.assertEqual(picks['recipe:food']['be-vietnam-pro'], {'shown': 1, 'picked': 0})

    def test_pick_imports_the_whole_file_and_saves_a_text_preset(self):
        manifest = {'role': 'title', 'recipe': 'food', 'scopes': ['all', 'recipe:food'], 'text': 'x',
                    'rows': [{'n': 1, 'id': 'anton', 'family': 'Anton', 'weight': 400, 'category': 'sans-serif'}]}
        self.ff.write_json(self.ff.shared('cache') / 'candidates.json', manifest)
        downloads, calls = [], []

        def download(family, weights, italic, dest):
            downloads.append((family, weights))
            (Path(dest) / 'Anton-Regular.ttf').write_bytes(b'ttf')
            return [{'file': 'Anton-Regular.ttf', 'family': family, 'weight': 400}]

        def bashcut(*args):
            calls.append(args)
            return {'ok': True, 'fonts': ['Anton-Regular']} if args[:2] == ('fonts', 'import') else {'item': {}}
        with mock.patch.object(self.ff.single, 'download', download), mock.patch.object(self.ff, 'bashcut', bashcut), \
                mock.patch.object(self.ff, 'postscript_name', lambda path, weight: 'Anton-Regular'):
            rows = run(self.ff, ['--pick', '1,4'])
            run(self.ff, ['--pick', '1'])  # the file is fetched once
        self.assertEqual(rows[0], {'n': 1, 'family': 'Anton', 'font': 'Anton-Regular',
                                   'preset': 'project:font-anton-400-title'})
        self.assertEqual(rows[1], {'n': 4, 'error': 'no such candidate'})
        self.assertEqual(downloads, [('Anton', [400])])
        self.assertTrue((self.ff.shared('data') / 'files/anton/Anton-400.ttf').exists())
        add = next(c for c in calls if c[:2] == ('library', 'add'))
        params = json.loads(add[add.index('--params') + 1])
        self.assertEqual(params, {'textPreset': 'hook-title', 'textStyle': {'font': 'Anton-Regular'}})
        self.assertIn('--kind', add)
        picks = json.loads((self.ff.shared('data') / 'picks.json').read_text())
        self.assertEqual(picks['recipe:food']['anton']['picked'], 2)

    def test_pick_without_candidates_says_so(self):
        self.assertIn('run --candidates', run(self.ff, ['--pick', '1'])[0]['error'])


@unittest.skipUnless(has('PIL') and (SYSTEM / 'Arial Narrow.ttf').exists(), 'needs Pillow and macOS fonts')
class MeasureTests(unittest.TestCase):
    def test_measures_split_narrow_rounded_and_high_contrast_faces(self):
        ff = load()
        tags = lambda name, category='sans-serif': ff.tags_of({'category': category,
                                                               'm': ff.measure(SYSTEM / name)})
        self.assertIn('condensed', tags('Arial Narrow.ttf'))
        self.assertNotIn('condensed', tags('Arial.ttf'))
        self.assertIn('rounded', tags('Arial Rounded Bold.ttf'))
        self.assertNotIn('rounded', tags('Arial.ttf'))
        self.assertIn('high-contrast', tags('Didot.ttc', 'serif') if (SYSTEM / 'Didot.ttc').exists()
                      else tags('Bodoni 72.ttc', 'serif'))


if __name__ == '__main__':
    unittest.main()
