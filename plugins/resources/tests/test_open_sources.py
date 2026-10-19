"""Internet Archive (#562), open emoji (#563) and Simple Icons logos (#564) with mocked HTTP, no network."""
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
from test_fetch_assets import PIL, FakeBashCut, FakeWeb, jpeg, load, run  # noqa: E402

SKILL = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SKILL))

ARCHIVE_SEARCH = {'response': {'docs': [
    {'identifier': 'Apollo11Film', 'title': 'Apollo 11 film', 'year': 1969, 'licenseurl': 'http://pd',
     'collection': ['fav-someone', 'nasa']},
    {'identifier': 'LongFeature', 'title': 'Feature', 'year': 1970, 'collection': 'movies'},
    {'identifier': 'Locked', 'title': 'Locked', 'year': 1969}]}}
APOLLO_META = {'metadata': {}, 'files': [
    {'name': 'a.mpeg', 'size': '900000000'},
    {'name': 'a.mp4', 'size': '120000000', 'width': '640', 'height': '480', 'length': '1700.5'},
    {'name': 'a_512kb.mp4', 'size': '40000000', 'width': '320', 'height': '240', 'length': '1700.4'},
    {'name': 'secret.mp4', 'size': '1000', 'private': 'true'},
    *({'name': f'a.thumbs/a_{i:06d}.jpg', 'size': '900'} for i in range(0, 600, 60))]}
FEATURE_META = {'metadata': {}, 'files': [{'name': 'f.mp4', 'size': '300000000', 'length': '01:30:00'}]}
LOCKED_META = {'metadata': {'access-restricted-item': 'true'}, 'files': [{'name': 'l.mp4', 'size': '1'}]}
PHOTO_META = {'metadata': {}, 'files': [
    {'name': 'scan.jpg', 'size': '3000000', 'format': 'JPEG'}, {'name': 'scan_thumb.jpg', 'size': '9000',
                                                                 'format': 'JPEG Thumb'},
    {'name': 'huge.png', 'size': '50000000', 'format': 'PNG'}, {'name': 'small.jpg', 'size': '200000'}]}


@unittest.skipIf(PIL is None, 'Pillow not installed')  # the fake archive serves JPEG thumbnails
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.stock = load('stock-images/fetch_stock.py')

    def web(self):
        return FakeWeb([('https://archive.org/advancedsearch.php', ARCHIVE_SEARCH),
                        ('https://archive.org/metadata/Apollo11Film', APOLLO_META),
                        ('https://archive.org/metadata/LongFeature', FEATURE_META),
                        ('https://archive.org/metadata/Locked', LOCKED_META),
                        ('https://archive.org/metadata/Scan', PHOTO_META),
                        ('https://archive.org/download/', jpeg()), ('https://archive.org/services/img/', jpeg())])

    def test_video_search_query_rendition_frames_and_order(self):
        web = self.web()
        self.stock.ARCHIVE.update(years='1960-1975', public_domain=True, file_mb=80)
        with mock.patch.object(self.stock, 'get', web):
            rows = self.stock.archive_search('moon landing', 'video', 'portrait', 8, 1080)
        q = web.query('https://archive.org/advancedsearch.php')['q'][0]
        self.assertIn('(moon landing) AND mediatype:(movies) AND year:[1960 TO 1975]', q)
        self.assertIn('publicdomain', q)
        self.assertEqual([r['id'] for r in rows], ['Apollo11Film', 'LongFeature'])  # restricted dropped
        apollo, feature = rows
        self.assertTrue(apollo['url'].endswith('/Apollo11Film/a_512kb.mp4'))  # the largest MP4 within 80 MB
        self.assertEqual((apollo['size'], apollo['seconds'], apollo['mb']), ('320x240', 1700.4, 40.0))
        self.assertEqual((apollo['year'], apollo['collection']), (1969, 'nasa'))
        self.assertEqual(len(apollo['frames']), 3)
        self.assertTrue(apollo['deferred'])
        self.assertEqual((feature['seconds'], feature['mb']), (5400.0, 300.0))  # over the cap: smallest, last

    def test_photo_takes_the_largest_picture_under_12_mb(self):
        with mock.patch.object(self.stock, 'get', self.web()):
            row = self.stock.archive_by_id('Scan', 'photo', 1080)
        self.assertTrue(row['url'].endswith('/Scan/scan.jpg'))
        self.assertNotIn('deferred', row)

    def test_documentary_routes_ids_and_slot_years(self):
        self.assertEqual(self.stock.year_range('1969'), (1969, 1969))
        self.assertEqual(self.stock.year_range('1960–1975'), (1960, 1975))
        with mock.patch.object(self.stock, 'archive_by_id', return_value='a'), \
                mock.patch.object(self.stock, 'wikimedia_by_id', return_value='w'), \
                mock.patch.object(self.stock, 'openverse_by_id', return_value='o'):
            got = [self.stock.documentary_by_id(i, 'photo', 1080)
                   for i in ('12345', '0b1c2d3e-0000-4000-8000-123456789abc', 'Apollo11Film')]
        self.assertEqual(got, ['w', 'o', 'a'])

    @unittest.skipIf(PIL is None, 'Pillow not installed')
    def test_slots_defer_archive_video_and_pick_downloads_it(self):
        web, fake = self.web(), FakeBashCut()
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(self.stock, 'get', web), \
                mock.patch.object(self.stock, 'bashcut', fake):
            slots = Path(folder) / 'slots.json'
            slots.write_text(json.dumps([{'slot': 'moon', 'query': 'apollo', 'kind': 'video', 'year': '1969',
                                          'count': 2}]))
            rows = run(self.stock, ['--source', 'archive', '--slots', str(slots), '--dest', folder])
            self.assertEqual(rows[0], {'slot': 'moon', 'candidates': 2, 'deferred': 2})
            self.assertIn('year:[1969 TO 1969]', web.query('https://archive.org/advancedsearch.php')['q'][0])
            self.assertFalse([u for u, _ in web.calls if u.endswith('.mp4')])  # nothing big before the pick
            picked = run(self.stock, ['--dest', folder, '--pick', 'moon=1@30'])
            self.assertTrue((Path(folder) / picked[0]['file']).exists())
        self.assertEqual((picked[0]['placed'], picked[0]['id']), (30, 'project:lib-1'))
        add = next(c for c in fake.calls if c[:2] == ('library', 'add'))
        self.assertIn('1969', add[add.index('--tags') + 1])
        self.assertIn('nasa', add[add.index('--tags') + 1])


NOTO = {'icons': [{'codepoint': '1f525', 'popularity': 700, 'tags': [':fire:']},
                  {'codepoint': '1f31e', 'popularity': 900, 'tags': [':sun-with-face:']},
                  {'codepoint': '263a_fe0f', 'popularity': 800, 'tags': [':warm-smile:']}]}
OPENMOJI = [{'emoji': '🔥', 'hexcode': '1F525', 'annotation': 'fire', 'tags': 'flame, tool', 'openmoji_tags': 'hot'},
            {'emoji': '🌞', 'hexcode': '1F31E', 'annotation': 'sun with face', 'tags': 'sun',
             'openmoji_tags': 'fire, hot'},
            {'emoji': '💰', 'hexcode': '1F4B0', 'annotation': 'money bag', 'tags': 'bag, dollar, money'},
            {'emoji': '👍🏻', 'hexcode': '1F44D-1F3FB', 'annotation': 'thumbs up', 'skintone': '1'}]
CLDR_VI = {'annotations': {'annotations': {'🔥': {'default': ['lửa', 'ngọn lửa'], 'tts': ['lửa']},
                                           '💰': {'default': ['tiền', 'túi'], 'tts': ['túi tiền']}}}}


class EmojiTests(unittest.TestCase):
    def setUp(self):
        self.emoji = load('stock-images/fetch_emoji.py')
        self.rows = self.emoji.build_index(NOTO['icons'], OPENMOJI, CLDR_VI)

    def test_index_joins_sets_and_vietnamese_without_skin_tones(self):
        by_key = {r['key']: r for r in self.rows}
        self.assertEqual(sorted(by_key), ['1f31e', '1f4b0', '1f525', '263a'])
        self.assertEqual((by_key['1f525']['noto'], by_key['1f525']['openmoji']), ('1f525', '1F525'))
        self.assertNotIn('noto', by_key['1f4b0'])
        self.assertEqual(by_key['263a']['emoji'], '☺️')
        self.assertEqual(by_key['1f4b0']['vi'], ['túi tiền', 'tiền', 'túi'])

    def test_exact_name_beats_a_more_used_tag_and_vietnamese_words_match(self):
        self.assertEqual([r['key'] for _, r in self.emoji.rank('fire', self.rows, 3, 'both')], ['1f525', '1f31e'])
        self.assertEqual(self.emoji.rank('tiền', self.rows, 3, 'both')[0][1]['key'], '1f4b0')
        self.assertEqual(self.emoji.rank('cash', self.rows, 3, 'both')[0][1]['key'], '1f4b0')  # synonym
        self.assertEqual(self.emoji.rank('fire', self.rows, 3, 'noto')[0][1]['key'], '1f525')
        self.assertEqual(self.emoji.rank('money', self.rows, 3, 'noto'), [])  # Noto has no 💰 here

    @unittest.skipIf(PIL is None, 'Pillow not installed')
    def test_sheet_once_then_pick_saves_the_animated_webp(self):
        from PIL import Image
        webp = io.BytesIO()
        Image.new('RGBA', (64, 64), (255, 80, 0, 255)).save(webp, 'WEBP')
        web = FakeWeb([('https://googlefonts.github.io/', NOTO), ('https://cdn.jsdelivr.net/npm/openmoji', OPENMOJI),
                       ('https://cdn.jsdelivr.net/npm/cldr', CLDR_VI), ('https://fonts.gstatic.com/', webp.getvalue())])
        fake = FakeBashCut()
        with tempfile.TemporaryDirectory() as shared, tempfile.TemporaryDirectory() as dest, \
                mock.patch.dict(os.environ, {'BASHCUT_SHARED_DATA': shared}), \
                mock.patch.object(self.emoji, 'get', web), mock.patch.object(self.emoji, 'bashcut', fake):
            lines = run(self.emoji, ['--words', 'fire,lửa', '--dest', dest, '--set', 'noto'])
            self.assertEqual(lines[0]['ingested'], 4)
            self.assertEqual(lines[1], {'slot': 'fire', 'candidates': 2, 'animated': 2, 'best': '🔥🌞'})
            self.assertEqual(lines[2]['best'], '🔥')
            self.assertTrue(Path(lines[3]['sheets'][0]).exists())
            again = run(self.emoji, ['--words', 'fire', '--dest', dest, '--set', 'noto'])
            self.assertNotIn('ingested', again[0])  # the index is fetched once
            picked = run(self.emoji, ['--dest', dest, '--pick', 'fire=1@90:30', '--place-size', '0.2'])
        self.assertEqual((picked[0]['emoji'], picked[0]['animated'], picked[0]['placed']), ('🔥', True, 90))
        add = next(c for c in fake.calls if c[:2] == ('library', 'add'))
        self.assertTrue(add[add.index('--file') + 1].endswith('noto/1f525.webp'))
        self.assertEqual(add[add.index('--id') + 1], 'emoji-noto-1f525')
        self.assertIn(('library', 'place', 'project:lib-1', '--at-frame', '90', '--base-rev', 'last', '--duration', '30',
                       '--size', '0.2'), fake.calls)


ICONS = [{'title': 'Figma', 'slug': 'figma', 'hex': 'F24E1E'}, {'title': 'Notion', 'slug': 'notion', 'hex': '000000'},
         {'title': 'Google', 'slug': 'google', 'hex': '4285F4'},
         {'title': 'Google Ads', 'slug': 'googleads', 'hex': '4285F4'},
         {'title': 'Visual Studio Code', 'slug': 'visualstudiocode', 'hex': '007ACC',
          'aliases': {'aka': ['VS Code', 'vscode']}}]
SVG = '<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M0 0h24v24H0z"/></svg>'


class LogoTests(unittest.TestCase):
    def setUp(self):
        self.logos = load('stock-images/fetch_logos.py')

    def test_match_slug_title_alias_suffix_and_close(self):
        self.assertEqual(self.logos.match('Figma', ICONS)[0]['slug'], 'figma')
        self.assertEqual(self.logos.match('VS Code', ICONS)[0]['slug'], 'visualstudiocode')
        self.assertEqual(self.logos.match('studio code', ICONS)[0]['slug'], 'visualstudiocode')
        icon, close = self.logos.match('goog', ICONS)
        self.assertIsNone(icon)
        self.assertEqual([i['slug'] for i in close][:2], ['google', 'googleads'])

    def test_brand_colour_falls_back_to_white_or_black_under_3_to_1(self):
        self.assertEqual(self.logos.colour_for('F24E1E', 'brand', 'dark')[0], '#F24E1E')
        self.assertEqual(self.logos.colour_for('000000', 'brand', 'dark'), ('#FFFFFF', 21.0))
        self.assertEqual(self.logos.colour_for('FFFFFF', 'brand', 'light')[0], '#000000')
        self.assertEqual(self.logos.colour_for('F24E1E', 'white', 'light')[0], '#FFFFFF')
        self.assertEqual(self.logos.colour_for('F24E1E', '#123456', '#FFFFFF')[0], '#123456')
        self.assertIn('fill="#FFFFFF"', self.logos.tinted(SVG, '#FFFFFF'))

    def test_ingest_extracts_the_package_once(self):
        package = io.BytesIO()
        with tarfile.open(fileobj=package, mode='w:gz') as tar:
            for name, body in (('package/data/simple-icons.json', json.dumps(ICONS)),
                               ('package/icons/figma.svg', SVG)):
                info = tarfile.TarInfo(name)
                info.size = len(body.encode())
                tar.addfile(info, io.BytesIO(body.encode()))
        registry = {'dist-tags': {'latest': '16.0.0'},
                    'versions': {'16.0.0': {'dist': {'tarball': 'https://registry.npmjs.org/si.tgz'}}}}
        web = FakeWeb([('https://registry.npmjs.org/simple-icons', registry),
                       ('https://registry.npmjs.org/si.tgz', package.getvalue())])
        with tempfile.TemporaryDirectory() as shared, mock.patch.dict(os.environ, {'BASHCUT_SHARED_DATA': shared}):
            icons, seconds = self.logos.ingest(fetch=web)
            again, later = self.logos.ingest(fetch=web)
            self.assertTrue((Path(shared) / 'logos/simple-icons/icons/figma.svg').exists())
        self.assertEqual((len(icons), len(again), later), (5, 5, None))
        self.assertEqual(len(web.calls), 2)

    def test_iconify_fallback_needs_an_exact_name(self):
        web = FakeWeb([('https://api.iconify.design/search', {'icons': ['logos:canva-icon', 'devicon:canvas']}),
                       ('https://api.iconify.design/logos/canva-icon.svg', SVG.encode())])
        self.assertEqual(self.logos.iconify_logo('Canva', web)[0], 'logos:canva-icon')
        self.assertEqual(self.logos.iconify_logo('Canv', web), (None, None))


if __name__ == '__main__':
    unittest.main()
