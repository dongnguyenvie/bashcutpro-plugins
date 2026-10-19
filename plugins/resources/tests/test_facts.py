"""fetch_facts.py with canned Wikidata, World Bank and Wikipedia answers: no network, no BashCut."""
import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.parse

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'fetch_facts.py'


def load():
    spec = importlib.util.spec_from_file_location('fetch_facts', SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def quantity(amount, unit='1', rank='normal', when=None, part=None):
    claim = {'rank': rank, 'mainsnak': {'datavalue': {'type': 'quantity', 'value': {
        'amount': amount, 'unit': unit if unit == '1' else f'http://www.wikidata.org/entity/{unit}'}}}}
    qualifiers = {}
    if when:
        qualifiers['P585'] = [{'datavalue': {'value': {'time': f'+{when}T00:00:00Z', 'precision': 11}}}]
    if part:
        qualifiers['P518'] = [{'datavalue': {'value': {'id': part}}}]
    if qualifiers:
        claim['qualifiers'] = qualifiers
    return claim


DALAT = {'labels': {'vi': {'value': 'Đà Lạt'}, 'en': {'value': 'Da Lat'}}, 'claims': {
    'P2044': [quantity('+1500', 'Q11573')],
    'P1082': [quantity('+211696', when='2009-04-01'), quantity('+406105', rank='preferred', when='2019-04-01'),
              quantity('+425000', rank='deprecated', when='2022-01-01')],
    'P2046': [quantity('+394.64', 'Q712226')],
    'P2048': [quantity('+12', 'Q99999')],
    'P17': [{'rank': 'normal', 'mainsnak': {'datavalue': {'type': 'wikibase-entityid', 'value': {'id': 'Q881'}}}}],
    'P571': [{'rank': 'normal', 'mainsnak': {'datavalue': {'type': 'time', 'value': {
        'time': '+1893-06-21T00:00:00Z', 'precision': 9}}}}],
}}
SUN = {'labels': {'en': {'value': 'Sun'}}, 'claims': {'P2076': [quantity('+15710000', 'Q11579', part='Q123')]}}
LABELS = {'Q881': {'labels': {'vi': {'value': 'Việt Nam'}, 'en': {'value': 'Vietnam'}}},
          'Q99999': {'labels': {'en': {'value': 'furlong'}}}, 'Q123': {'labels': {'en': {'value': 'core'}}}}
COUNTRIES = [{'page': 1}, [{'id': 'VNM', 'iso2Code': 'VN', 'name': 'Viet Nam'},
                           {'id': 'FRA', 'iso2Code': 'FR', 'name': 'France'}]]


def wb_point(year, value, code='NY.GDP.PCAP.CD', name='GDP per capita (current US$)'):
    return [{'page': 1}, [{'indicator': {'id': code, 'value': name}, 'country': {'id': 'VN', 'value': 'Viet Nam'},
                           'countryiso3code': 'VNM', 'date': str(year), 'value': value}]]


class Web:
    """Answers get(url) like the three APIs would; records every URL."""

    def __init__(self, missing_pages=()):
        self.calls = []
        self.missing_pages = set(missing_pages)

    def __call__(self, url, *args, **kwargs):
        self.calls.append(url)
        parsed = urllib.parse.urlparse(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        if parsed.netloc == 'www.wikidata.org':
            if q['action'] == 'wbgetentities':
                known = {'Q25281': DALAT, 'Q525': SUN, **LABELS}
                return self.json({'entities': {i: known.get(i, {'missing': ''}) for i in q['ids'].split('|')}})
            hits = {'Mặt trời giả': [{'id': 'Q525'}]}.get(q['search'], [])
            return self.json({'search': hits})
        if parsed.netloc.endswith('.wikipedia.org'):
            if parsed.path.startswith('/api/rest_v1/page/summary/'):
                title = urllib.parse.unquote(parsed.path.rsplit('/', 1)[1])
                if title in self.missing_pages:
                    raise self.module.NotFound('404')
                return self.json({'type': 'standard', 'title': title.replace('_', ' '), 'timestamp': '2026-09-27T01:02:03Z',
                                  'extract': 'Đà Lạt là thành phố ở Tây Nguyên. Thành phố nằm trên cao nguyên.',
                                  'content_urls': {'desktop': {'page': f'https://vi.wikipedia.org/wiki/{title}'}}})
            if q.get('list') == 'search':
                return self.json({'query': {'search': [{'title': 'Đà Lạt'}]}})
            item = {'Đà Lạt': 'Q25281', 'Sun': 'Q525', 'Mercury': None}.get(q['titles'])
            props = {'wikibase_item': item} if item else {'disambiguation': ''} if q['titles'] == 'Mercury' else {}
            return self.json({'query': {'pages': [{'title': q['titles'], 'pageprops': props}]}})
        if parsed.netloc == 'api.worldbank.org':
            if parsed.path == '/v2/country':
                return self.json(COUNTRIES)
            if q.get('date') == '1990':
                return self.json([{'page': 1}, [{**wb_point(1990, None)[1][0]}]])
            return self.json(wb_point(q.get('date') or 2025, 4322.83 if q.get('date') else 5065.99091899299))
        raise OSError(f'curl exit 22 for {url}')

    @staticmethod
    def json(value):
        return json.dumps(value, ensure_ascii=False).encode()


@unittest.skipIf(os.name == "nt" and not sys.flags.utf8_mode,
                 "the scripts read and write files as UTF-8: run with python -X utf8 (the plugin starts them so)")
class FactsTests(unittest.TestCase):
    def setUp(self):
        self.facts = load()
        self.web = Web()
        self.web.module = self.facts
        self.stored = {}
        patcher = mock.patch.object(self.facts, 'get', self.web)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_bashcut(self, *args):
        if args[:2] == ('project', 'data'):
            return self.stored.get('facts')
        if args[:2] == ('project', 'set-data'):
            self.stored['facts'] = json.loads(Path(args[3]).read_text())
            return {'key': 'facts', 'rev': 2}
        return {'error': 'unexpected'}

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(self.facts, 'bashcut', self.fake_bashcut), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.facts.main(argv)
        return out.getvalue(), err.getvalue()

    def rows(self, argv):
        out, _ = self.run_main(['--json', *argv])
        return {r['id']: r for r in json.loads(out)['rows']}

    def test_wikidata_values_units_and_dates(self):
        rows = self.rows(['--lang', 'vi', 'e|Đà Lạt|elevation', 'p|Đà Lạt|population', 'a|Đà Lạt|area',
                          'h|Đà Lạt|height', 'c|Đà Lạt|country', 'i|Đà Lạt|inception', 'y|Đà Lạt|population|2009'])
        self.assertEqual((rows['e']['value'], rows['e']['unit']), (1500, 'm'))
        self.assertEqual(rows['e']['source'], 'https://www.wikidata.org/wiki/Q25281#P2044')
        self.assertEqual(rows['e']['credit'], 'Wikidata')
        # preferred rank wins over the others; deprecated is never used; the count gets its word
        self.assertEqual((rows['p']['value'], rows['p']['unit'], rows['p']['date']), (406105, 'people', '2019-04-01'))
        self.assertEqual((rows['a']['value'], rows['a']['unit']), (394.64, 'km²'))
        self.assertEqual(rows['h']['unit'], 'furlong')  # an unknown unit takes its label
        self.assertEqual(rows['c']['value'], 'Việt Nam')
        self.assertEqual(rows['i']['value'], '1893')  # year precision
        self.assertEqual((rows['y']['value'], rows['y']['date']), (211696, '2009-04-01'))
        self.assertEqual(rows['e']['label'], 'Đà Lạt (Q25281)')
        entity_calls = [u for u in self.web.calls if 'wbgetentities' in u]
        self.assertEqual(len(entity_calls), 2)  # one call for the items, one for unit and value labels

    def test_part_qualifier_and_search_fallback(self):
        rows = self.rows(['s|Sun|temperature', 'f|Mặt trời giả|P2076'])
        self.assertEqual((rows['s']['value'], rows['s']['unit']), (15710000, 'K'))
        self.assertEqual(rows['s']['label'], 'Sun (Q525), core')
        self.assertEqual(rows['f']['value'], 15710000)  # no Wikipedia page: Wikidata's own search

    def test_missing_answers_say_none_with_a_reason(self):
        rows = self.rows(['n|Nowhere|area', 'm|Mercury|mass', 'x|Đà Lạt|P9999', 'z|Đà Lạt|population|1980',
                          'u|Đà Lạt|shoe size'])
        for key in rows:
            self.assertIsNone(rows[key]['value'], key)
            self.assertTrue(rows[key]['note'], key)
        self.assertIn('no Wikidata item', rows['n']['note'])
        self.assertIn('1980', rows['z']['note'])
        self.assertIn('unknown property', rows['u']['note'])
        out, err = self.run_main(['--no-cache', 'n|Nowhere|area'])
        self.assertIn('none', out.splitlines()[1])
        self.assertIn('0/1 answered', err)

    def test_world_bank_country_names_years_and_latest(self):
        rows = self.rows(['a|Việt Nam|wb:gdp per capita|2023', 'b|VN|wb:NY.GDP.PCAP.CD', 'c|Atlantis|wb:gdp',
                          'd|Vietnam|wb:gdp per capita|1990'])
        self.assertEqual((rows['a']['value'], rows['a']['unit'], rows['a']['date']), (4322.83, 'US$', '2023'))
        self.assertEqual(rows['a']['source'], 'https://data.worldbank.org/indicator/NY.GDP.PCAP.CD?locations=VN')
        self.assertEqual((rows['b']['value'], rows['b']['date'], rows['b']['unit']), (5065.99, '2025', 'US$'))
        self.assertTrue(any('mrnev=1' in u for u in self.web.calls))
        self.assertIsNone(rows['c']['value'])
        self.assertIsNone(rows['d']['value'])  # a null year is never filled from another year

    def test_wikipedia_summary_and_search_fallback(self):
        rows = self.rows(['d|Đà Lạt|summary', '{"id": "q", "entity": "Q25281", "summary": true, "lang": "vi"}'])
        self.assertEqual(rows['d']['value'], 'Đà Lạt là thành phố ở Tây Nguyên.')
        self.assertEqual((rows['d']['date'], rows['d']['credit']), ('2026-09-27', 'Wikipedia'))
        self.assertTrue(rows['d']['source'].startswith('https://vi.wikipedia.org/wiki/'))
        self.web.missing_pages.add('Dalat_city')
        rows = self.rows(['--lang', 'vi', 'd|Dalat city|summary'])
        self.assertEqual(rows['d']['label'], 'Đà Lạt')

    def test_questions_file_jsonl_and_duplicate_ids(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'facts.jsonl'
        path.write_text('{"id": "e", "entity": "Đà Lạt", "property": "elevation", "lang": "vi"}\n'
                        '{"id": "g", "entity": "Vietnam", "indicator": "gdp per capita", "year": 2023}\n')
        rows = self.rows(['--questions', str(path)])
        self.assertEqual((rows['e']['value'], rows['g']['value']), (1500, 4322.83))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.facts.main(['a|Sun|mass', 'a|Sun|radius'])

    def test_cache_reuses_answers_and_asks_none_rows_again(self):
        first, _ = self.run_main(['e|Đà Lạt|elevation', 'n|Nowhere|area'])
        self.assertEqual(set(self.stored['facts']['rows']), {'e'})  # a none row is not cached
        calls = len(self.web.calls)
        out, err = self.run_main(['e|Đà Lạt|elevation'])
        self.assertEqual(len(self.web.calls), calls)  # answered from the project, no request
        self.assertIn('(cached)', out)
        self.run_main(['e|Đà Lạt|area'])  # same id, another question: asked again
        self.assertGreater(len(self.web.calls), calls)
        self.assertEqual(self.stored['facts']['rows']['e']['unit'], 'km²')
        self.run_main(['--refresh', 'e|Đà Lạt|area'])
        self.assertNotIn('cached', self.stored['facts']['rows']['e'])

    def test_tsv_keeps_raw_numbers(self):
        out, _ = self.run_main(['--no-cache', '--tsv', 'p|Đà Lạt|population'])
        self.assertEqual(out.splitlines()[1].split('\t')[:3], ['p', '406105', 'people'])


class GetTests(unittest.TestCase):
    def test_transient_curl_errors_are_retried(self):
        facts = load()
        answers = [subprocess.CompletedProcess([], 56, b'', b''), subprocess.CompletedProcess([], 0, b'{}', b'503'),
                   subprocess.CompletedProcess([], 0, b'{"ok": 1}', b'200')]
        with mock.patch.object(facts.subprocess, 'run', side_effect=answers) as run, \
                mock.patch.object(facts.time, 'sleep'):
            self.assertEqual(facts.get_json('https://example.org/x'), {'ok': 1})
        self.assertEqual(run.call_count, 3)
        self.assertIn('bashcut-agent-kit', run.call_args.args[0][run.call_args.args[0].index('-A') + 1])

    def test_not_found_is_not_retried(self):
        facts = load()
        with mock.patch.object(facts.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'404')) \
                as run, self.assertRaises(facts.NotFound):
            facts.get('https://example.org/missing')
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
