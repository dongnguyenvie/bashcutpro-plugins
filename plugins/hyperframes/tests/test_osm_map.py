"""lib/osm-map.mjs in Node (skipped without Node.js on PATH): the route extent, the amenity kind lists and the
answer cache. The script runs on a copy whose private helpers are exported; fetch is a stub, nothing goes online."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "lib")
NODE = shutil.which("node")
HELPERS = "extent, fitZoom, lookUp, overpass"


@unittest.skipUnless(NODE, "needs Node.js on PATH")
class OsmMapTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lib = os.path.join(self.dir, "lib")
        os.makedirs(self.lib)
        for name in ("osm-map.mjs", "amenity-map.js", "brand.js"):
            shutil.copyfile(os.path.join(LIB, name), os.path.join(self.lib, name))
        with open(os.path.join(self.lib, "osm-map.mjs"), "a", encoding="utf-8") as f:
            f.write("\nexport { %s };\n" % HELPERS)
        self.cache = os.path.join(self.dir, "cache")

    def node(self, body):
        """Runs `body` in an ES module after `import * as osm` from the copy; its stdout parsed as JSON."""
        script = os.path.join(self.dir, "driver.mjs")
        with open(script, "w", encoding="utf-8") as f:
            f.write("const osm = await import(%s);\n%s\n" % (
                json.dumps(Path(self.lib, "osm-map.mjs").as_uri()), body))
        env = dict(os.environ, BASHCUT_PLUGIN_CACHE=self.cache, OSM_RETRY_MS="0",
                   OSM_NOMINATIM_URL="http://nominatim.test/search", OSM_OVERPASS_URL="http://overpass.test/api")
        return subprocess.run([NODE, script], capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)

    def answer(self, body):
        done = self.node(body)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def cache_file(self, folder, query):
        path = os.path.join(self.cache, folder, hashlib.sha256(query.encode("utf-8")).hexdigest()[:20] + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def test_extent_over_200k_points(self):
        out = self.answer("""
            const xs = Array.from({ length: 200001 }, (_, i) => (i * 7919) % 200001 - 100000);
            const line = Array.from({ length: 200000 }, (_, i) => [21 + i / 2e6, 105.8 + i / 1e6]);
            console.log(JSON.stringify({ extent: osm.extent(xs), empty: osm.extent([]).map(String),
                zoom: osm.fitZoom(line, { width: 1080, height: 1920, padding: 0.2 }) }));
        """)
        self.assertEqual(out["extent"], [-100000, 100000])
        self.assertEqual(out["empty"], ["Infinity", "-Infinity"])
        self.assertTrue(2 <= out["zoom"] <= 17, out)

    def test_kinds_and_amenities_must_match(self):
        out = self.answer("console.log(JSON.stringify({ kinds: osm.KINDS, tagged: Object.keys(osm.AMENITIES) }));")
        self.assertEqual(sorted(out["kinds"]), sorted(out["tagged"]))
        path = os.path.join(self.lib, "amenity-map.js")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("const KINDS = {", text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace("const KINDS = {", 'const KINDS = {\n        zoo: ["#000", CAP],', 1))
        done = self.node("console.log('loaded');")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("amenity kinds out of step", done.stderr)
        self.assertIn("no OSM tags for zoo", done.stderr)

    def test_a_corrupt_cache_is_asked_again(self):
        place = self.cache_file("osm-places", "Hồ Gươm, Hà Nội")
        with open(place, "w", encoding="utf-8") as f:
            f.write('{"lat": "21.0')  # a write cut short
        query = "[out:json];node(around:1000,21,105)[amenity=school];out;"
        elements = self.cache_file("osm-amenities", query)
        with open(elements, "w", encoding="utf-8") as f:
            f.write("[")
        out = self.answer("""
            const asked = [];
            globalThis.fetch = async (url) => {
                asked.push(new URL(url).host);
                const body = url.includes("nominatim") ? [{ lat: "21.0285", lon: "105.8524", name: "Hồ Gươm" }]
                    : { elements: [{ type: "node", id: 1, lat: 21, lon: 105, tags: { amenity: "school" } }] };
                return new Response(JSON.stringify(body), { status: 200 });
            };
            const found = await osm.lookUp("Hồ Gươm, Hà Nội", { at: 0 });
            const listed = await osm.overpass(%s);
            const again = await osm.lookUp("Hồ Gươm, Hà Nội", { at: 0 });
            console.log(JSON.stringify({ asked, found: found.name, listed: listed.length, again: again.name }));
        """ % json.dumps(query))
        self.assertEqual(out, {"asked": ["nominatim.test", "overpass.test"], "found": "Hồ Gươm", "listed": 1,
                               "again": "Hồ Gươm"})  # the second lookUp reads the rewritten cache
        with open(place, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["name"], "Hồ Gươm")
        with open(elements, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
