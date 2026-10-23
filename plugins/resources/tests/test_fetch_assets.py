"""Asset search scripts (P2-S1) with mocked HTTP: no network, keys only from the environment."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.parse

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(relative):
    path = SCRIPTS / Path(relative).name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    if hasattr(value, 'get_file'):
        value.get_file = file_from_get(value)
    return value


def file_from_get(module):
    """A get_file that answers through module.get as it is at call time, so a test that fakes get fakes files too
    (the real get_file runs curl -o; its own tests fake curl)."""
    def get_file(url, path, timeout=300, tries=3, max_bytes=None, agent=None):
        body = module.get(url, timeout=timeout)
        if max_bytes is not None and len(body) > max_bytes:
            raise module.TooLarge('over the cap', url)
        Path(path).write_bytes(body)
        return len(body)
    return get_file


class FakeWeb:
    """Answers get(url, …) from a list of (url prefix, body); records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, *args, **kwargs):
        headers = kwargs.get('headers')
        if headers is None:
            headers = next((a for a in args if isinstance(a, dict)), None)
        self.calls.append((url, headers))
        for prefix, body in self.routes:
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, bytes) else json.dumps(body).encode()
        raise OSError(f'curl exit 22 for {url}')

    def query(self, prefix):
        url = next(u for u, _ in self.calls if u.startswith(prefix))
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def run(module, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        module.main(argv)
    return [json.loads(line) for line in out.getvalue().splitlines()]


PIXABAY_PHOTOS = {'hits': [{'id': 11, 'pageURL': 'https://pixabay.com/photos/pot-11/', 'tags': 'clay, pot',
                            'previewURL': 'https://cdn.pixabay.com/p/11_150.jpg', 'imageWidth': 4000,
                            'imageHeight': 6000, 'largeImageURL': 'https://pixabay.com/get/11_1280.jpg',
                            'user': 'ann'}]}
PIXABAY_VIDEO = {'hits': [{'id': 22, 'pageURL': 'https://pixabay.com/videos/x-22/', 'tags': 'river', 'duration': 14,
                           'user': 'bo', 'videos': {
                               'large': {'url': '', 'width': 0, 'height': 0},
                               'medium': {'url': 'https://cdn.pixabay.com/v/22_medium.mp4', 'width': 1920, 'height': 1080},
                               'small': {'url': 'https://cdn.pixabay.com/v/22_small.mp4', 'width': 1280, 'height': 720},
                               'tiny': {'url': 'https://cdn.pixabay.com/v/22_tiny.mp4', 'width': 960, 'height': 540,
                                        'thumbnail': 'https://cdn.pixabay.com/v/22_tiny.jpg'}}}]}
PEXELS_VIDEO = {'id': 33, 'url': 'https://www.pexels.com/video/33/', 'duration': 9, 'image': 'https://p/33.jpg',
                'user': {'name': 'Cy'}, 'video_files': [
                    {'width': 2160, 'height': 3840, 'link': 'https://videos.pexels.com/33-uhd.mp4'},
                    {'width': 1080, 'height': 1920, 'link': 'https://videos.pexels.com/33-hd.mp4'},
                    {'width': 540, 'height': 960, 'link': 'https://videos.pexels.com/33-sd.mp4'}]}


class StockTests(unittest.TestCase):
    def setUp(self):
        self.stock = load('stock-images/fetch_stock.py')

    def test_no_key_fails_loudly_for_a_named_source_or_video(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as stop:
            run(self.stock, ['--query', 'pot', '--source', 'pixabay'])
        self.assertIn('PIXABAY_API_KEY', str(stop.exception.code))
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as stop:
            run(self.stock, ['--query', 'pot', '--kind', 'video'])
        self.assertIn('PEXELS_API_KEY', str(stop.exception.code))

    def test_no_key_photos_come_from_openverse(self):
        found = {'results': [{'id': 'ab-1', 'url': 'https://live.staticflickr.com/1.jpg', 'width': 1024, 'height': 1536,
                              'license': 'by', 'license_version': '2.0', 'creator': 'Sam', 'title': 'Three pots',
                              'thumbnail': 'https://api.openverse.org/t/1', 'foreign_landing_url': 'https://flickr/1'}]}
        web = FakeWeb([('https://api.openverse.org/v1/images/', found)])
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(self.stock, 'get', web):
            rows = run(self.stock, ['--query', 'clay pot'])
        self.assertEqual(web.query('https://api.openverse.org/')['aspect_ratio'], ['tall'])
        self.assertEqual(rows[0]['license'], 'CC BY 2.0')
        self.assertEqual(rows[0]['source'], 'openverse')

    def test_wikimedia_keeps_the_orientation_and_the_file_licence(self):
        def page(i, w, h):
            return {'pageid': i, 'index': i, 'title': f'File:Pho {i}.jpg', 'imageinfo': [{
                'width': w, 'height': h, 'thumburl': f'https://upload.wikimedia.org/t/{i}.jpg',
                'descriptionurl': f'https://commons.wikimedia.org/wiki/File:Pho_{i}.jpg', 'extmetadata': {
                    'LicenseShortName': {'value': 'CC0'}, 'Artist': {'value': '<a href="x">Ann</a>'}}}]}
        found = {'query': {'pages': {'1': page(1, 4000, 3000), '2': page(2, 3000, 4000)}}}
        web = FakeWeb([('https://commons.wikimedia.org/w/api.php', found)])
        with mock.patch.object(self.stock, 'get', web):
            rows = run(self.stock, ['--source', 'wikimedia', '--query', 'pho'])
        self.assertEqual([r['id'] for r in rows], ['2'])  # portrait by default
        self.assertEqual((rows[0]['license'], rows[0]['author']), ('CC0', 'Ann'))
        self.assertIn('filetype:bitmap', web.query('https://commons.wikimedia.org/')['gsrsearch'][0])

    def test_nasa_video_takes_the_large_rendition(self):
        search = {'collection': {'items': [{'href': 'https://images-assets.nasa.gov/video/A 1/collection.json',
                                            'data': [{'nasa_id': 'A 1', 'title': 'Moon', 'center': 'JSC'}],
                                            'links': [{'render': 'image', 'href': 'https://x/A 1~thumb.jpg'}]}]}}
        files = ['http://images-assets.nasa.gov/video/A 1/A 1~orig.mp4', 'http://images-assets.nasa.gov/video/A 1/A 1~large.mp4']
        web = FakeWeb([('https://images-api.nasa.gov/search', search),
                       ('https://images-assets.nasa.gov/video/A%201/collection.json', files)])
        with mock.patch.object(self.stock, 'get', web):
            rows = run(self.stock, ['--source', 'nasa', '--kind', 'video', '--query', 'moon'])
        self.assertEqual(rows[0]['url'], 'https://images-assets.nasa.gov/video/A%201/A%201~large.mp4')
        self.assertEqual(rows[0]['license'], 'NASA media (public domain)')

    def test_pixabay_search_uses_the_env_key_and_lists_candidates(self):
        web = FakeWeb([('https://pixabay.com/api/', PIXABAY_PHOTOS)])
        with mock.patch.dict(os.environ, {'PIXABAY_API_KEY': 'k-env'}, clear=True), \
                mock.patch.object(self.stock, 'get', web):
            rows = run(self.stock, ['--query', 'clay pot', '--count', '5'])
        query = web.query('https://pixabay.com/api/')
        self.assertEqual(query['key'], ['k-env'])
        self.assertEqual(query['orientation'], ['vertical'])
        self.assertEqual(query['per_page'], ['5'])
        self.assertEqual(rows, [{'id': '11', 'source': 'pixabay', 'kind': 'photo',
                                 'page': 'https://pixabay.com/photos/pot-11/', 'author': 'ann', 'tags': 'clay, pot',
                                 'license': 'Pixabay Content License', 'size': '4000x6000',
                                 'preview': 'https://cdn.pixabay.com/p/11_150.jpg',
                                 'url': 'https://pixabay.com/get/11_1280.jpg'}])

    def test_pixabay_video_download_takes_smallest_rendition_at_least_1080(self):
        web = FakeWeb([('https://pixabay.com/api/videos/', PIXABAY_VIDEO),
                       ('https://cdn.pixabay.com/v/22_medium.mp4', b'mp4-bytes')])
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'PIXABAY_API_KEY': 'k'}, clear=True), \
                mock.patch.object(self.stock, 'get', web):
            rows = run(self.stock, ['--kind', 'video', '--ids', '22', '--dest', folder])
            self.assertEqual(rows[0]['file'], 'pixabay-22-river.mp4')
            self.assertEqual((Path(folder) / rows[0]['file']).read_bytes(), b'mp4-bytes')
        self.assertEqual(web.query('https://pixabay.com/api/videos/')['id'], ['22'])
        self.assertEqual(rows[0]['size'], '1920x1080')
        self.assertEqual(rows[0]['seconds'], 14)

    def test_pexels_key_goes_in_the_header_not_the_url(self):
        web = FakeWeb([('https://api.pexels.com/videos/videos/33', PEXELS_VIDEO),
                       ('https://videos.pexels.com/33-hd.mp4', b'x')])
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'PEXELS_API_KEY': 'secret'}, clear=True), \
                mock.patch.object(self.stock, 'get', web):
            rows = run(self.stock, ['--source', 'pexels', '--kind', 'video', '--ids', '33', '--dest', folder])
        api_url, headers = web.calls[0]
        self.assertNotIn('secret', api_url)
        self.assertEqual(headers, {'Authorization': 'secret'})
        self.assertEqual(rows[0]['size'], '1080x1920')
        self.assertEqual(rows[0]['author'], 'Cy')

    def test_a_missing_id_is_reported_and_the_rest_continue(self):
        web = FakeWeb([('https://pixabay.com/api/?key=k&id=404', {'hits': []}),
                       ('https://pixabay.com/api/?key=k&id=11', PIXABAY_PHOTOS),
                       ('https://pixabay.com/get/11_1280.jpg', b'jpg')])
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'PIXABAY_API_KEY': 'k'}, clear=True), \
                mock.patch.object(self.stock, 'get', web):
            rows = run(self.stock, ['--ids', '404,11', '--dest', folder])
        self.assertEqual(rows[0], {'id': '404', 'source': 'pixabay', 'error': 'not found'})
        self.assertEqual(rows[1]['file'], 'pixabay-11-clay-pot.jpg')

    def test_no_key_in_the_source(self):
        text = (SCRIPTS / 'fetch_stock.py').read_text(encoding='utf-8')
        self.assertNotRegex(text, r'key=[0-9a-f]{10,}|Authorization": "[A-Za-z0-9]{20,}')


FREESOUND = {'results': [{'id': 5, 'name': 'Whoosh 1', 'username': 'dee', 'duration': 1.234,
                          'license': 'http://creativecommons.org/publicdomain/zero/1.0/',
                          'url': 'https://freesound.org/people/dee/sounds/5/',
                          'previews': {'preview-hq-mp3': 'https://cdn.freesound.org/previews/0/5-hq.mp3'}}]}


def openverse_hit(n, licence, version='4.0'):
    return {'url': f'https://cdn.example/{n}.mp3', 'filetype': 'mp3', 'title': f'Track {n}', 'duration': 90_000,
            'license': licence, 'license_version': version, 'creator': f'artist{n}',
            'foreign_landing_url': f'https://www.jamendo.com/track/{n}'}


# What Openverse answers; NC / ND must go even if the API ignored the filter.
OPENVERSE_MUSIC = {'results': [openverse_hit(1, 'by-nc-nd', '3.0'), openverse_hit(2, 'cc0', '1.0'),
                               openverse_hit(3, 'by'), openverse_hit(4, 'by-sa'), openverse_hit(5, 'by-nc'),
                               openverse_hit(6, 'by-nd')]}


class MusicLicenceTests(unittest.TestCase):
    """Music under every licence unless --safe-licence (CC0 / CC BY / CC BY-SA); the licence on every row and file."""

    def setUp(self):
        self.audio = load('audio-mix/fetch_audio.py')

    def test_default_takes_every_licence(self):
        web = FakeWeb([('https://api.openverse.org/v1/audio/', OPENVERSE_MUSIC)])
        with mock.patch.object(self.audio, 'get', web):
            rows = self.audio.music_candidates('calm acoustic', 3, 30)
        self.assertNotIn('license', web.query('https://api.openverse.org/'))
        self.assertIn('CC BY-NC-ND 3.0', [r['license'] for r in rows])

    def test_safe_licence_asks_for_safe_licences_and_drops_the_rest(self):
        web = FakeWeb([('https://api.openverse.org/v1/audio/', OPENVERSE_MUSIC)])
        with mock.patch.object(self.audio, 'get', web):
            rows = self.audio.music_candidates('calm acoustic', 3, 30, any_licence=False)
        self.assertEqual(web.query('https://api.openverse.org/')['license'], ['cc0,by,by-sa'])
        self.assertEqual([r['license'] for r in rows], ['CC0', 'CC BY 4.0', 'CC BY-SA 4.0'])

    def test_short_music_category_fills_up_from_uncategorised_audio(self):
        calls = []

        def web(url, *args, **kwargs):
            calls.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query))
            if 'category=music' in url:
                return json.dumps({'results': []}).encode()
            return json.dumps({'results': [dict(openverse_hit(7, 'cc0'), duration=20_000),
                                           openverse_hit(8, 'by')]}).encode()

        with mock.patch.object(self.audio, 'get', web):
            rows = self.audio.music_candidates('calm acoustic', 2, 30, any_licence=False)
        self.assertEqual([c.get('category') for c in calls], [['music'], None])
        self.assertEqual(calls[1]['license'], ['cc0,by,by-sa'])
        self.assertEqual([(r['title'], r['license']) for r in rows], [('Track 8', 'CC BY 4.0')])

    def test_lines_carry_the_licence_and_a_record_sits_next_to_each_file(self):
        web = FakeWeb([('https://api.openverse.org/v1/audio/', OPENVERSE_MUSIC), ('https://cdn.example/', b'mp3')])
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.audio, 'get', web):
            rows = run(self.audio, ['--dest', folder, '--music', 'calm', '--music-count', '2', '--no-library',
                                     '--safe-licence'])
            record = json.loads((Path(folder) / 'music-track-3-2.mp3.licence.json').read_text(encoding='utf-8'))
        self.assertEqual([r['license'] for r in rows], ['CC0', 'CC BY 4.0'])
        self.assertEqual((record['license'], record['author'], record['source'], record['fetchedBy']),
                         ('CC BY 4.0', 'artist3', 'https://www.jamendo.com/track/3', 'bashcut.resources fetch_audio'))

    def test_vietnamese_title_and_author_survive_the_saved_licence_record(self):
        # the record is JSON with ensure_ascii=False: written in the code page (cp1252 on Windows) it crashed (FX2)
        hit = dict(openverse_hit(9, 'by'), title='Bài hát ru Việt Nam', creator='Nguyễn Văn Đức')
        web = FakeWeb([('https://api.openverse.org/v1/audio/', {'results': [hit]}), ('https://cdn.example/', b'mp3')])
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.audio, 'get', web):
            rows = run(self.audio, ['--dest', folder, '--music', 'ru', '--music-count', '1', '--no-library'])
            (path,) = Path(folder).glob('*.licence.json')
            record = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual((rows[0]['title'], rows[0]['author']), ('Bài hát ru Việt Nam', 'Nguyễn Văn Đức'))
        self.assertEqual((record['title'], record['author'], record['file']), (rows[0]['title'], rows[0]['author'],
                                                                                rows[0]['file']))

    def test_candidate_table_has_a_licence_column(self):
        web = FakeWeb([('https://api.openverse.org/v1/audio/', OPENVERSE_MUSIC), ('https://cdn.example/', b'mp3')])
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.audio, 'get', web), contextlib.redirect_stdout(out):
            self.audio.main(['--dest', folder, '--music', 'calm', '--music-count', '4', '--no-library', '--safe-licence'])
        header, *lines = out.getvalue().splitlines()
        column = header.split('\t').index('licence')
        self.assertEqual([l.split('\t')[column] for l in lines], ['CC0', 'CC BY 4.0', 'CC BY-SA 4.0'])

    def test_nothing_allowed_says_what_to_do(self):
        web = FakeWeb([('https://api.openverse.org/v1/audio/', {'results': [openverse_hit(1, 'by-nc-nd')]})])
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(self.audio, 'get', web):
            rows = run(self.audio, ['--dest', folder, '--music', 'calm', '--no-library', '--safe-licence'])
        self.assertIn('--safe-licence', rows[0]['error'])


class FreesoundTests(unittest.TestCase):
    def setUp(self):
        self.audio = load('audio-mix/fetch_audio.py')

    def test_freesound_first_with_a_key_cc0_filter_token_header(self):
        web = FakeWeb([('https://freesound.org/apiv2/search/', FREESOUND),
                       ('https://cdn.freesound.org/previews/0/5-hq.mp3', b'mp3')])
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'FREESOUND_API_KEY': 'fs-key'}, clear=True), \
                mock.patch.object(self.audio, 'get', web):
            rows = run(self.audio, ['--dest', folder, '--sfx', 'whoosh'])
            self.assertEqual((Path(folder) / 'sfx-whoosh.mp3').read_bytes(), b'mp3')
        url, headers = web.calls[0]
        self.assertNotIn('fs-key', url)
        self.assertEqual(headers, {'Authorization': 'Token fs-key'})
        self.assertEqual(web.query('https://freesound.org/')['filter'],
                         ['license:"Creative Commons 0" duration:[0 TO 5]'])
        self.assertIsNone(web.calls[1][1])  # the preview download carries no key
        self.assertEqual(rows, [{'file': 'sfx-whoosh.mp3', 'kind': 'sfx', 'title': 'Whoosh 1', 'from': 'freesound',
                                 'seconds': 1.2, 'license': 'http://creativecommons.org/publicdomain/zero/1.0/',
                                 'author': 'dee', 'source': 'https://freesound.org/people/dee/sounds/5/'}])

    def test_cc_by_only_after_cc0_found_nothing(self):
        calls = []

        def web(url, *args, **kwargs):
            calls.append(url)
            if url.startswith('https://freesound.org/'):
                licence = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)['filter'][0]
                return json.dumps(FREESOUND if '"Attribution"' in licence else {'results': []}).encode()
            return b'mp3'

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'FREESOUND_API_KEY': 'k'}, clear=True), \
                mock.patch.object(self.audio, 'get', web):
            rows = run(self.audio, ['--dest', folder, '--sfx', 'pop', '--sfx-seconds', '2'])
        self.assertEqual(len([c for c in calls if 'freesound.org/apiv2' in c]), 2)
        self.assertIn('duration%3A%5B0+TO+2%5D', calls[0])
        self.assertEqual(rows[0]['from'], 'freesound')

    def test_without_a_key_falls_back_to_openverse(self):
        found = {'results': [{'url': 'https://cdn.example/pop.mp3', 'filetype': 'mp3', 'title': 'Pop', 'duration': 900,
                              'license': 'cc0', 'creator': 'someone', 'foreign_landing_url': 'https://freesound.org/s/1'}]}
        web = FakeWeb([('https://api.openverse.org/v1/audio/', found), ('https://cdn.example/', b'mp3')])
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {}, clear=True),                 mock.patch.object(self.audio, 'get', web):
            rows = run(self.audio, ['--dest', folder, '--sfx', 'pop'])
        self.assertFalse(any('freesound.org/apiv2' in u or 'mixkit' in u for u, _ in web.calls))
        self.assertEqual(rows[0]['from'], 'openverse')

    def test_openverse_licence_reads_like_the_stock_one(self):
        lic = self.audio.openverse_licence
        self.assertEqual(lic({'license': 'by', 'license_version': '4.0'}), 'CC BY 4.0')
        self.assertEqual(lic({'license': 'by-nc', 'license_version': '3.0'}), 'CC BY-NC 3.0')
        self.assertEqual(lic({'license': 'cc0', 'license_version': '1.0'}), 'CC0')
        self.assertEqual(lic({'license': 'pdm'}), 'Public Domain Mark')
        self.assertIsNone(lic({}))

    def test_openverse_sfx_safe_licence_only_safe_licences(self):
        found = {'results': [dict(OPENVERSE_MUSIC['results'][0], duration=900),
                             dict(OPENVERSE_MUSIC['results'][1], duration=900)]}
        web = FakeWeb([('https://api.openverse.org/v1/audio/', found)])
        with mock.patch.object(self.audio, 'get', web):
            rows = self.audio.openverse_sfx('pop', 5, 2, any_licence=False)
        self.assertEqual(web.query('https://api.openverse.org/')['license'], ['cc0,by,by-sa'])
        self.assertEqual([r['license'] for r in rows], ['CC0'])

    def test_freesound_source_without_key_says_so(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {}, clear=True):
            rows = run(self.audio, ['--dest', folder, '--sfx', 'pop', '--sfx-source', 'freesound'])
        self.assertEqual(rows[0]['error'], 'FREESOUND_API_KEY is not set')


def jpeg(color=(200, 80, 40)):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (60, 90), color).save(buffer, 'JPEG')
    return buffer.getvalue()


try:
    import PIL  # noqa: F401  (the scripts get Pillow from uv; skip sheets where the test Python lacks it)
except ImportError:
    PIL = None


class FakeBashCut:
    """Answers bashcut(*args): library add gives lib-N, analyze gives job-N, jobs status the measured values."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ('library', 'add'):
            return {'item': {'reference': f'project:lib-{len(self.adds)}', 'id': f'lib-{len(self.adds)}'}}
        if args[:2] == ('jobs', 'wait'):
            return {}
        if args[:2] == ('library', 'analyze'):
            return {'job': 'batch|' + args[2] if ',' in args[2] else 'job-' + args[2].split('-')[-1]}
        if args[:2] == ('jobs', 'status'):
            if args[2].startswith('batch|'):  # several IDs: one job, one row per item
                ids = args[2].split('|', 1)[1].split(',')
                return {'state': 'completed', 'finishedAt': 'now', 'result': {'items': [
                    {'id': i, 'measured': self.measured(int(i.split('-')[-1]))} for i in ids]}}
            return {'state': 'completed', 'finishedAt': 'now',
                    'result': {'measured': self.measured(int(args[2].split('-')[-1]))}}
        if args[:2] == ('library', 'place'):
            return {'rev': 7}
        return {}

    @staticmethod
    def measured(n):
        return {'seconds': 1.0 + n, 'lufs': -20.0 - n, 'truePeak': -1.5, 'landmarks': {'onset': 0.02, 'peak': 0.1},
                'beatConfidence': 0.81, 'energy': {'rangeDb': 12.5, 'liftAt': 8.0, 'peakAt': 30.0}}

    @property
    def adds(self):
        return [c for c in self.calls if c[:2] == ('library', 'add')]


PEXELS_SEARCH = {'photos': [
    {'id': 100 + i, 'url': f'https://www.pexels.com/photo/p-{100 + i}/', 'photographer': 'Di', 'alt': f'pot {i}',
     'width': 1080, 'height': 1920, 'src': {'original': f'https://images.pexels.com/{100 + i}.jpeg',
                                            'tiny': f'https://images.pexels.com/{100 + i}-t.jpeg'}}
    for i in range(5)]}


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.audio = load('audio-mix/fetch_audio.py')
        self.stock = load('stock-images/fetch_stock.py')

    def test_sfx_candidates_are_numbered_saved_to_the_library_and_tabled(self):
        results = {'results': [
            {'id': i, 'name': f'Whoosh {i}', 'username': 'dee', 'duration': 1.0, 'license': 'cc0',
             'url': f'https://freesound.org/s/{i}/',
             'previews': {'preview-hq-mp3': f'https://cdn.freesound.org/{i}.mp3'}} for i in range(1, 5)]}
        web = FakeWeb([('https://freesound.org/apiv2/search/', results),
                       ('https://cdn.freesound.org/2.mp3', OSError('curl exit 22')),  # a dead link
                       ('https://cdn.freesound.org/', b'mp3')])
        fake = FakeBashCut()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'FREESOUND_API_KEY': 'k'}, clear=True), \
                mock.patch.object(self.audio, 'get', web), mock.patch.object(self.audio, 'bashcut', fake), \
                mock.patch('fetch_stock.bashcut', fake), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.audio.main(['--dest', folder, '--sfx', 'whoosh', '--sfx-count', '3'])
            files = sorted(p.name for p in Path(folder).glob('*.mp3'))
            manifest = json.loads((Path(folder) / 'candidates.json').read_text(encoding='utf-8'))
        self.assertEqual(files, ['sfx-whoosh-1.mp3', 'sfx-whoosh-2.mp3', 'sfx-whoosh-3.mp3'])
        self.assertEqual([r['title'] for r in manifest], ['Whoosh 1', 'Whoosh 3', 'Whoosh 4'])
        self.assertEqual(len(fake.adds), 3)
        self.assertIn('sfx,whoosh,whoosh', fake.adds[0])
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith('slot\tn\tsec\tLUFS'))
        self.assertEqual(lines[1].split('\t')[:5], ['whoosh', '1', '2.0', '-21.0', '-1.5'])
        cells = lines[1].split('\t')
        self.assertEqual(cells[8:12], ['0.81', '12.5', '8.0', 'freesound'])
        self.assertEqual(cells[13], 'project:lib-1')
        self.assertTrue(cells[12])  # the licence column
        analyzes = [c for c in fake.calls if c[:2] == ('library', 'analyze')]
        self.assertEqual(len(analyzes), 1)  # one job for every candidate
        self.assertEqual(analyzes[0][2], 'project:lib-1,project:lib-2,project:lib-3')

        picked = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, mock.patch('fetch_stock.bashcut', fake), \
                contextlib.redirect_stdout(picked):
            (Path(folder) / 'candidates.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.audio.main(['--dest', folder, '--pick', 'whoosh=2@120:30,3;bed=1@0'])
        lines = [json.loads(line) for line in picked.getvalue().splitlines()]
        self.assertEqual(lines[0], {'slot': 'whoosh', 'n': 2, 'id': 'project:lib-2', 'placed': 120, 'rev': 7})
        self.assertEqual(lines[1], {'slot': 'whoosh', 'n': 3, 'id': 'project:lib-3'})
        self.assertEqual(lines[2]['error'], 'no such candidate in the library')
        place = [c for c in fake.calls if c[:2] == ('library', 'place')]
        self.assertEqual(place, [('library', 'place', 'project:lib-2', '--at-frame', '120', '--base-rev', 'last',
                                  '--duration', '30')])

    def test_video_tiles_take_three_frames_from_bashcut(self):
        calls = []

        def app(*args):
            calls.append(args)
            return {'frames': [{'path': f'/s/f{i}.png'} for i in range(3)]}
        with mock.patch.object(self.stock, 'bashcut', app):
            self.assertEqual(self.stock.video_frames('/v/c.mp4', '/t/pexels-1'), ['/s/f0.png', '/s/f1.png', '/s/f2.png'])
        self.assertEqual(calls, [('media', 'frames', '--file', '/v/c.mp4', '--count', '3', '--size', '320')])

    def test_an_older_app_analyzes_one_id_a_job(self):
        fake = FakeBashCut()
        calls = []

        def old_app(*args):
            if args[:2] == ('library', 'analyze') and ',' in args[2]:
                calls.append(args)
                return {'error': 'project:lib-1,project:lib-2 is not in the library'}
            return fake(*args)
        rows = [{'id': 'project:lib-1'}, {'id': 'project:lib-2'}]
        with mock.patch.object(self.audio, 'bashcut', old_app):
            self.audio.analyze_all(rows, 'project')
        self.assertEqual(len(calls), 1)
        self.assertEqual([r['seconds'] for r in rows], [2.0, 3.0])

    def test_slots_file_and_download_cap(self):
        results = {'results': [{'id': i, 'name': f'Pop {i}', 'duration': 0.5,
                                'previews': {'preview-hq-mp3': f'https://cdn.freesound.org/{i}.mp3'}}
                               for i in range(1, 9)]}
        web = FakeWeb([('https://freesound.org/apiv2/search/', results), ('https://cdn.freesound.org/', b'x' * 600_000)])
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'FREESOUND_API_KEY': 'k'}, clear=True), \
                mock.patch.object(self.audio, 'get', web), contextlib.redirect_stdout(out):
            slots = Path(folder) / 'slots.json'
            slots.write_text(json.dumps([{'slot': 'cut', 'sfx': 'pop', 'count': 2},
                                         {'slot': 'card', 'sfx': 'pop', 'count': 4}]), encoding='utf-8')
            self.audio.main(['--dest', folder, '--slots', str(slots), '--max-mb', '2', '--no-library'])
            files = sorted(p.name for p in Path(folder).glob('*.mp3'))
        self.assertEqual(files, ['sfx-pop-1.mp3', 'sfx-pop-2.mp3', 'sfx-pop-3.mp3', 'sfx-pop-4.mp3'])
        self.assertIn('download cap of 2 MB reached', out.getvalue())
        downloads = [u for u, _ in web.calls if u.startswith('https://cdn.')]
        self.assertEqual(len(downloads), len(set(downloads)))  # the second slot skips the first slot's files

    @unittest.skipIf(PIL is None, 'Pillow not installed')
    def test_stock_candidates_sheet_library_and_pick(self):
        web = FakeWeb([('https://api.pexels.com/v1/search', PEXELS_SEARCH), ('https://images.pexels.com/', jpeg())])
        fake = FakeBashCut()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.dict(os.environ, {'PEXELS_API_KEY': 'k'}, clear=True), \
                mock.patch.object(self.stock, 'get', web), mock.patch.object(self.stock, 'bashcut', fake):
            rows = run(self.stock, ['--source', 'pexels', '--query', 'clay pot', '--candidates', '4', '--dest', folder])
            self.assertEqual(rows[0], {'slot': 'clay-pot', 'candidates': 4, 'inLibrary': 4})
            self.assertTrue(Path(rows[1]['sheets'][0]).exists())
            self.assertEqual(web.query('https://api.pexels.com/')['per_page'], ['4'])
            picked = run(self.stock, ['--dest', folder, '--pick', 'clay-pot=2,9,3@48:60'])
        self.assertEqual(picked[0]['id'], 'project:lib-2')
        self.assertNotIn('placed', picked[0])
        self.assertEqual(picked[1], {'slot': 'clay-pot', 'n': 9, 'error': 'no such candidate'})
        self.assertEqual((picked[2]['placed'], picked[2]['rev']), (48, 7))
        self.assertIn(('library', 'place', 'project:lib-3', '--at-frame', '48', '--base-rev', 'last',
                       '--duration', '60'), fake.calls)
        self.assertEqual(len(fake.adds), 4)  # pick reuses the saved item


FONT_META = {'id': 'be-vietnam-pro', 'family': 'Be Vietnam Pro', 'weights': [400, 600, 800],
             'subsets': ['latin', 'vietnamese'], 'license': 'OFL-1.1', 'type': 'google'}
GOOGLE_LIST = b")]}'\n" + json.dumps({'manifest': {'fileRefs': [
    {'filename': 'static/BeVietnamPro-SemiBold.ttf', 'url': 'https://fonts.gstatic.com/s/semibold.ttf'},
    {'filename': 'BeVietnamPro-ExtraBold.ttf', 'url': 'https://fonts.gstatic.com/s/xb.ttf'},
    {'filename': 'static/BeVietnamPro-ExtraBold.ttf', 'url': 'https://fonts.gstatic.com/s/xb-static.ttf'},
    {'filename': 'static/BeVietnamPro-ExtraBoldItalic.ttf', 'url': 'https://fonts.gstatic.com/s/xbi.ttf'}]}}).encode()


class FontTests(unittest.TestCase):
    def setUp(self):
        self.font = load('captions-text/fetch_font.py')

    def test_search_filters_by_subset_and_name(self):
        catalogue = [{'id': 'be-vietnam-pro', 'family': 'Be Vietnam Pro', 'category': 'sans-serif',
                      'weights': [400, 800], 'styles': ['normal', 'italic'], 'license': 'OFL-1.1', 'type': 'google'},
                     {'id': 'montserrat', 'family': 'Montserrat', 'weights': [400], 'styles': ['normal'],
                      'variable': True, 'type': 'google'}]
        web = FakeWeb([('https://api.fontsource.org/v1/fonts', catalogue)])
        with mock.patch.object(self.font, 'get', web):
            rows = run(self.font, ['--subset', 'vietnamese', '--query', 'vietnam', '--weight', '800'])
        self.assertEqual(web.query('https://api.fontsource.org/'), {'subsets': ['vietnamese'], 'weights': ['800']})
        self.assertEqual(rows, [{'id': 'be-vietnam-pro', 'family': 'Be Vietnam Pro', 'category': 'sans-serif',
                                 'weights': [400, 800], 'italic': True, 'license': 'OFL-1.1', 'type': 'google'}])

    def test_download_takes_whole_static_files_from_google_fonts(self):
        web = FakeWeb([('https://api.fontsource.org/v1/fonts/be-vietnam-pro', FONT_META),
                       ('https://fonts.google.com/download/list?family=Be%20Vietnam%20Pro', GOOGLE_LIST),
                       ('https://fonts.gstatic.com/s/semibold.ttf', b'sb'),
                       ('https://fonts.gstatic.com/s/xb-static.ttf', b'xb')])
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(self.font, 'get', web):
            rows = run(self.font, ['--family', 'Be Vietnam Pro', '--weights', '600,800,700', '--dest', folder])
            self.assertEqual((Path(folder) / 'BeVietnamPro-ExtraBold.ttf').read_bytes(), b'xb')
        self.assertEqual(rows[0], {'family': 'Be Vietnam Pro', 'weight': 700, 'error': 'weights are [400, 600, 800]'})
        self.assertEqual([r['file'] for r in rows[1:]], ['BeVietnamPro-SemiBold.ttf', 'BeVietnamPro-ExtraBold.ttf'])
        self.assertFalse(any('jsdelivr' in u for u, _ in web.calls))  # never Fontsource's per-subset files

    def test_italic_and_variable_only_families(self):
        refs = json.loads(GOOGLE_LIST[5:])['manifest']['fileRefs']
        self.assertEqual(self.font.pick_files(refs, [800], True)[0][1]['url'], 'https://fonts.gstatic.com/s/xbi.ttf')
        variable = [{'filename': 'Foo-VariableFont_wght.ttf', 'url': 'v'},
                    {'filename': 'Foo-Italic-VariableFont_wght.ttf', 'url': 'vi'}]
        self.assertEqual(self.font.pick_files(variable, [700], False), [(None, variable[0])])

    def test_a_family_not_on_google_fonts_is_refused(self):
        web = FakeWeb([('https://api.fontsource.org/v1/fonts/foo', {'family': 'Foo', 'type': 'other',
                                                                     'source': 'https://example.org/foo'})])
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(self.font, 'get', web):
            rows = run(self.font, ['--family', 'Foo', '--dest', folder])
        self.assertIn('not on Google Fonts', rows[0]['error'])


class SharedHelperTests(unittest.TestCase):
    def setUp(self):
        self.stock = load('stock-images/fetch_stock.py')

    def test_slug_limit_and_fallback(self):
        slug = self.stock.slug
        self.assertEqual(slug('Clay pot, Hà Nội!'), 'clay-pot-h-n-i')
        self.assertEqual(len(slug('word ' * 20)), 39)  # 40, then the trailing '-' goes
        self.assertEqual(slug('ab cd', limit=3), 'ab')
        self.assertEqual(slug('x' * 60, limit=None), 'x' * 60)
        self.assertEqual(slug('!!!'), 'stock')
        self.assertEqual(slug('', 'lut'), 'lut')
        self.assertEqual(slug(None, ''), '')

    def test_shared_dir_env_then_plugin_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {'BASHCUT_SHARED_DATA': os.path.join(folder, 'd'),
                                              'BASHCUT_SHARED_CACHE': os.path.join(folder, 'c')}, clear=True):
                self.assertEqual(self.stock.shared_dir('emoji'), Path(folder) / 'd' / 'emoji')
                self.assertEqual(self.stock.shared_dir('luts', 'cache'), Path(folder) / 'c' / 'luts')
            plugin = Path(folder) / 'bashcut-plugins' / 'bashcut.resources'
            with mock.patch.dict(os.environ, {'FILMCRAFT_DATA_DIR': folder}, clear=True):
                self.assertEqual(self.stock.shared_dir('fonts'), plugin / 'shared' / 'fonts')
                self.assertEqual(self.stock.shared_dir('fonts', 'cache'), plugin / 'cache' / 'fonts')
            with mock.patch.dict(os.environ, {'HOME': folder}, clear=True), mock.patch('sys.platform', 'linux'):
                self.assertEqual(self.stock.shared_dir('luts'),
                                 Path(folder) / '.local' / 'share' / 'filmcraft' / 'bashcut-plugins' / 'bashcut.resources'
                                 / 'shared' / 'luts')

    def test_openverse_photo_licence_matches_audio(self):
        audio = load('audio-mix/fetch_audio.py')
        photo = {'id': 'pd-1', 'url': 'https://live.staticflickr.com/1.jpg', 'license': 'pdm', 'creator': 'Library'}
        self.assertEqual(self.stock.openverse_row(photo, 'photo', 1080)['license'], 'Public Domain Mark')
        web = FakeWeb([('https://api.openverse.org/v1/audio/', {'results': [openverse_hit(1, 'pdm', '1.0')]})])
        with mock.patch.object(audio, 'get', web):
            rows = audio.music_candidates('old march', 1, 30)
        self.assertEqual(rows[0]['license'], 'Public Domain Mark')
        for code, version in (('cc0', '1.0'), ('by-sa', '4.0'), ('pdm', None)):
            hit = {'license': code, 'license_version': version}
            self.assertEqual(self.stock.openverse_licence(hit), audio.openverse_licence(hit))

    def test_empty_query_slot_name(self):
        with tempfile.TemporaryDirectory() as folder:
            slots = Path(folder) / 'slots.json'
            slots.write_text(json.dumps([{'query': '!!!'}, {'query': 'Clay pot'}, {'query': '…'}]), encoding='utf-8')
            args = mock.Mock(slots=str(slots), query=None, kind='photo', orientation='portrait', candidates=4)
            read = self.stock.read_slots(args)
        self.assertEqual([s['slot'] for s in read], ['slot1', 'clay-pot', 'slot3'])
        self.assertEqual({s['count'] for s in read}, {4})

    @unittest.skipIf(PIL is None, 'Pillow not installed')
    def test_label_font_has_vietnamese_on_windows_and_linux(self):
        from PIL import ImageFont
        real = ImageFont.truetype
        source = next((p for p in self.stock.LABEL_FONTS if os.path.isabs(p) and covers(p, 'Phở Hà Nội')), None)
        if source is None:
            self.skipTest('no font with Vietnamese on this machine')
        with tempfile.TemporaryDirectory() as windir, mock.patch.dict(os.environ, {'WINDIR': windir}):
            stock = load('stock-images/fetch_stock.py')  # WINDOWS_FONTS is read on import
            fonts = Path(windir) / 'Fonts'
            self.assertIn(str(fonts / 'segoeui.ttf'), stock.LABEL_FONTS)
            for system, there in (('windows', str(fonts / 'arial.ttf')),
                                  ('linux', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                                  ('linux', '/usr/share/fonts/noto/NotoSans-Regular.ttf')):
                asked = []

                def truetype(path, size, *args, **kwargs):
                    asked.append(path)
                    if path != there:  # only that one font is installed
                        raise OSError('cannot open resource')
                    return real(source, size)

                with mock.patch.object(ImageFont, 'truetype', truetype):
                    font = stock.label_font(18)
                self.assertEqual(asked[-1], there, system)
                self.assertEqual(asked, list(stock.LABEL_FONTS[:stock.LABEL_FONTS.index(there) + 1]), system)
                self.assertIsInstance(font, ImageFont.FreeTypeFont, system)
                self.assertTrue(covers(font.path, 'Phở Hà Nội'), system)


def covers(path, text):
    """True when the font file at path has a glyph for every letter of text."""
    try:
        from fontTools.ttLib import TTFont, TTCollection
        font = TTFont(path, fontNumber=0) if not path.lower().endswith('.ttc') else TTCollection(path).fonts[0]
    except ImportError:
        return os.path.isfile(path)  # without fontTools: only that it is there
    except Exception:
        return False
    cmap = font.getBestCmap() or {}
    return all(ord(c) in cmap for c in text if not c.isspace())

if __name__ == '__main__':
    unittest.main()
