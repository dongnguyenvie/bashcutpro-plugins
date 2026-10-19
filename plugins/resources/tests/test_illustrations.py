"""Open illustration sets (#560): tagging, recolour, slot matching and ingest on tiny SVG fixtures, no network."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

SKILL = Path(__file__).resolve().parents[1] / 'scripts'


def load():
    sys.path.insert(0, str(SKILL))
    spec = importlib.util.spec_from_file_location('fetch_illustrations', SKILL / 'fetch_illustrations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PEEP = '''<?xml version="1.0" encoding="UTF-8"?>
<svg width="213px" height="715px" viewBox="0 0 213 715" version="1.1" xmlns="http://www.w3.org/2000/svg">
<title>peep-standing-7</title>
<g id="a-person/standing">
 <g id="pose/standing/crossed_arms-1"><path id="🎨-Background" fill="#FFFFFF" d="M0 0h10v10z"/>
  <path id="🖍-Ink" fill="#000000" d="M0 0h1v1z"/></g>
 <g id="Head"><g id="head/Long-Curly"><path id="🎨-Background" fill="#FFFFFF" d="M0 0h5v5z"/></g>
  <g id="face/Smile-Big" fill="#000000"><path id="🖍-Ink" d="M0 0h1v1z"/></g>
  <g id="accessories/*-None"/></g>
</g></svg>'''

DOODLE = '''<svg width="1024px" height="768px" viewBox="0 0 1024 768" xmlns="http://www.w3.org/2000/svg">
<title>Illustrations/reading-side</title>
<g id="Illustrations/reading-side"><path id="Accent" fill="#FF5678" d="M0 0h9v9z"/>
<path id="Shade" fill="#CF536D" d="M0 0h3v3z"/><path id="Shade2" fill="#CF536D" d="M0 0h2v2z"/>
<g id="Ink" fill="#000000"><path d="M0 0h1v1z"/></g></g></svg>'''


class Tagging(unittest.TestCase):
    def setUp(self):
        self.f = load()

    def test_peep_tags_from_layer_names(self):
        tags, pose = self.f.tags_for('peep-standing-7', PEEP)
        self.assertEqual(pose, 'standing')
        for word in ('standing', 'crossed', 'arms', 'smile', 'big', 'long', 'curly'):
            self.assertIn(word, tags)
        self.assertNotIn('none', tags)
        self.assertNotIn('peep', tags)

    def test_doodle_tags_from_file_name(self):
        tags, pose = self.f.tags_for('reading-side', DOODLE)
        self.assertEqual(pose, 'reading')
        self.assertIn('side', tags)

    def test_shape(self):
        self.assertAlmostEqual(self.f.svg_shape(PEEP), 213 / 715, places=2)
        self.assertAlmostEqual(self.f.svg_shape(DOODLE), 1024 / 768, places=2)


class Recolour(unittest.TestCase):
    def setUp(self):
        self.f = load()

    def colours(self, svg):
        return {c.upper() for c in re.findall(r'#[0-9A-Fa-f]{6}', svg)}

    def test_accent_group_takes_palette_with_lightness_offset(self):
        out = self.f.recolour(DOODLE, ['#2B6CB0', '#F2B544'])
        found = self.colours(out)
        self.assertNotIn('#FF5678', found)
        self.assertNotIn('#CF536D', found)
        self.assertIn('#000000', found)  # ink stays
        # the darker shade of the same hue maps to a darker version of the same palette colour, not to palette[1]
        mapping = self.f.colour_map({'#FF5678': 1, '#CF536D': 2}, ['#2B6CB0', '#F2B544'])
        self.assertEqual(len({m for m in mapping.values()}), 2)
        self.assertNotIn('#F2B544', mapping.values())

    def test_two_hue_groups_take_two_palette_colours(self):
        mapping = self.f.colour_map({'#FF0000': 5, '#00AA00': 2, '#888888': 9}, ['#123456', '#ABCDEF'])
        self.assertEqual(mapping['#FF0000'], '#123456')
        self.assertEqual(mapping['#00AA00'], '#ABCDEF')
        self.assertNotIn('#888888', mapping)  # grey is neutral

    def test_peep_clothes_tinted_skin_and_ink_kept(self):
        out = self.f.recolour(PEEP, ['#2B6CB0'], ('body/', 'pose/'))
        pose = out[out.index('pose/standing'):out.index('Head')]
        head = out[out.index('head/Long-Curly'):]
        self.assertIn('#2B6CB0', pose)
        self.assertIn('#000000', pose)
        self.assertNotIn('#2B6CB0', head)
        self.assertIn('#FFFFFF', head)

    def test_no_palette_is_unchanged(self):
        self.assertEqual(self.f.recolour(DOODLE, []), DOODLE)

    def test_parse_palette(self):
        self.assertEqual(self.f.parse_palette('#2b6cb0, F2B544 #abc'), ['#2B6CB0', '#F2B544', '#AABBCC'])
        with self.assertRaises(ValueError):
            self.f.parse_palette('blue')


class Slots(unittest.TestCase):
    def setUp(self):
        self.f = load()
        self.items = [
            {'id': 'coffee', 'pose': 'coffee', 'tags': ['coffee']},
            {'id': 'reading', 'pose': 'reading', 'tags': ['reading']},
            {'id': 'running', 'pose': 'running', 'tags': ['running']},
            {'id': 'peep-1', 'pose': 'standing', 'tags': ['standing', 'pointing', 'finger', 'explaining']},
            {'id': 'peep-2', 'pose': 'bust', 'tags': ['bust', 'tired']},
            {'id': 'peep-3', 'pose': 'sitting', 'tags': ['sitting', 'wheelchair', 'smile']},
        ]

    def test_exact_and_synonym_matches_rank_first(self):
        slots = [{'slot': 'study', 'query': 'student reading a book'}, {'slot': 'idea', 'query': 'explain the idea'},
                 {'slot': 'drink', 'query': 'drinks'}]
        ranked = self.f.rank(slots, self.items, 3)
        self.assertEqual(ranked[0][0][0]['id'], 'reading')
        self.assertEqual(ranked[1][0][0]['id'], 'peep-1')
        self.assertEqual(ranked[2][0][0]['id'], 'coffee')
        self.assertTrue(all(len(r) == 3 for r in ranked))

    def test_unmatched_slots_get_varied_fill(self):
        slots = [{'slot': 'a', 'query': 'zzz'}, {'slot': 'b', 'query': 'qqq'}]
        first, second = self.f.rank(slots, self.items, 2)
        self.assertEqual([s for _, s in first], [0, 0])
        self.assertNotEqual({i['id'] for i, _ in first}, {i['id'] for i, _ in second})

    def test_read_slots_text_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / 's.txt'
            text.write_text('# explainer\nhook: worried person\nmoney: buy online\n')
            slots = self.f.read_slots(text, 8)
            self.assertEqual([(s['slot'], s['query'], s['count']) for s in slots],
                             [('hook', 'worried person', 8), ('money', 'buy online', 8)])
            js = Path(tmp) / 's.json'
            js.write_text(json.dumps([{'query': 'team at work', 'count': 4}]))
            self.assertEqual(self.f.read_slots(js, 8)[0], {'slot': 'team-at-work', 'query': 'team at work', 'count': 4})


PART = '''import React from "react";

const SvgHoodie = props => React.createElement("g", {
  id: "Body/Hoodie",
  fillRule: "evenodd"
}, React.createElement("defs", null, React.createElement("path", {
  d: "M0 0h4v4z",
  id: "path-1"
})), React.createElement("mask", {
  id: "mask-2",
  fill: "white"
}, React.createElement("use", {
  xlinkHref: "#path-1"
})), React.createElement("path", {
  d: "M0,0 L10,10 Z",
  fill: "#FF4133",
  mask: "url(#mask-2)",
  fillOpacity: 0.5
}), React.createElement("path", {
  d: "M1 1h2v2z",
  fill: "#B28B67"
}));

export default SvgHoodie;'''

UNDRAW_PAGE = ('<html><script id="__NEXT_DATA__" type="application/json">'
               + json.dumps({'props': {'pageProps': {'totalPages': 3, 'illustrations': [
                   {'title': 'Personal Finance', 'media': 'https://cdn.undraw.co/illustration/personal-finance_ab12.svg',
                    'newSlug': 'personal-finance_ab12'}]}}}) + '</script></html>')


class Sources(unittest.TestCase):
    def setUp(self):
        self.f = load()

    def test_jsx_part_becomes_svg_with_prefixed_ids(self):
        el = self.f.jsx_to_svg(PART, 'toHoodie')
        svg = self.f.ET.tostring(el, encoding='unicode')
        self.assertIn('fill-rule="evenodd"', svg)
        self.assertIn('fill-opacity="0.5"', svg)
        self.assertIn('id="toHoodie-mask-2"', svg)
        self.assertIn('url(#toHoodie-mask-2)', svg)
        self.assertIn('href="#toHoodie-path-1"', svg)
        self.assertNotIn('React', svg)

    def test_humaaans_assembled_from_package_parts(self):
        import io
        import tarfile
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
            for name in ('head/Afro', 'torso/Hoodie', 'standing/Jogging', 'sitting/Wheelchair', 'sitting/SkinnyJeans'):
                data = PART.encode()
                info = tarfile.TarInfo(f'package/lib/body-parts/{name}.js')
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'svg').mkdir()
            items = self.f.humaaans_items('humaaans', folder, lambda url: buffer.getvalue())
            self.assertEqual([i['id'] for i in items], ['standing-jogging-hoodie-afro', 'sitting-wheelchair-hoodie-afro'])
            self.assertIn('jogging', items[0]['tags'])
            svg = (folder / 'svg' / 'standing-jogging-hoodie-afro.svg').read_text()
            self.assertEqual(svg.count('<path d="M0,0 L10,10 Z"'), 3)  # head, legs and torso
            out = self.f.recolour(svg, ['#2B6CB0'], keep=self.f.SETS['humaaans']['keep'])
            self.assertIn('#2B6CB0', out)
            self.assertIn('#B28B67', out)  # skin kept

    def test_undraw_page_and_only_accent(self):
        items, pages = self.f.undraw_page(UNDRAW_PAGE)
        self.assertEqual(pages, 3)
        self.assertEqual(items[0]['id'], 'personal-finance_ab12')
        self.assertEqual(items[0]['tags'], ['personal', 'finance'])
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><path fill="#6c63ff"/><path fill="#ffb8b8"/></svg>'
        out = self.f.recolour(svg, ['#2B6CB0'], only=self.f.SETS['undraw']['only'])
        self.assertIn('#2B6CB0', out)
        self.assertIn('#ffb8b8', out)  # skin is not the accent

    def test_stem_joins_word_forms(self):
        self.assertEqual(self.f.stem('save'), self.f.stem('savings'))
        self.assertEqual(self.f.stem('running'), 'run')
        self.assertEqual(self.f.stem('stories'), 'story')


class Ingest(unittest.TestCase):
    def test_fetch_once_into_shared_folder(self):
        f = load()
        page = ('<img src="https://cdn.prod.website-files.com/5e51/abc_peep-standing-7.svg">'
                '<img src="https://cdn.prod.website-files.com/5e51/abc_peep-standing-7.svg">'
                '<img src="https://cdn.prod.website-files.com/5e51/def_logo-figma.svg">').encode()
        calls = []

        def fetch(url):
            calls.append(url)
            return PEEP.encode()

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'BASHCUT_SHARED_DATA': tmp}), \
                mock.patch.object(f, 'get', return_value=page):
            index, seconds = f.ingest('open-peeps', fetch=fetch)
            self.assertIsNotNone(seconds)
            self.assertEqual([i['id'] for i in index['items']], ['peep-standing-7'])
            self.assertEqual(index['items'][0]['pose'], 'standing')
            self.assertTrue((Path(tmp) / 'illustrations/open-peeps/svg/peep-standing-7.svg').exists())
            again, seconds = f.ingest('open-peeps', fetch=fetch)
            self.assertIsNone(seconds)
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
