// osm-map (the plugin's `osm-map` command runs this file): puts an OpenStreetMap map and a route into a composition folder, so a page can draw the map and
// animate the route without network at render time.
//
//   osm-map --dir D --points "21.0278,105.8342,Hồ Gươm;21.0545,105.8194,Hồ Tây" [--route car|bike|foot|none]
//           [--width 1080] [--height 1920] [--padding 0.2] [--zoom Z] [--legs chain|star]
//           [--places "Name, City;…"] [--amenities "school,hospital,mall,park" --radius 1000]
//           [--style osm|dark|light] [--tiles "https://…/{z}/{x}/{y}.png,…"]
//
// Writes D/map/<z>-<x>-<y>.png (tiles, also cached in the plugin cache folder) and D/map-data.js, which sets
// window.MAP_DATA = {width, height, zoom, tiles: [{src, x, y, size}], points: [{x, y, label, lat, lon}],
// route: [[x, y]…], legs: [{from, to, km, minutes}], km, minutes, profile, attribution}, in canvas pixels.
// With --places "Hồ Hoàn Kiếm, Hà Nội;Hồ Tây, Hà Nội", each named place is looked up on Nominatim (its OpenStreetMap
// outline) and measured: areaM2, areaHa, areaKm2 (outer rings minus holes, on the sphere), perimeterKm (outer rings),
// centre and osm id; MAP_DATA.places then holds each outline in canvas pixels to draw to scale. Without --points the
// map is fitted to the places; without --dir only the measurements are printed (no map).
// With --legs star, every leg runs from the first point (the project) to each other one instead of point to point;
// MAP_DATA.legs[i].line holds each leg's line in canvas pixels either way.
// With --amenities "school,hospital,mall,park" [--radius 1000], one Overpass query counts what lies within the
// radius (metres) of the first point (else the first place): per kind its count and the nearest one (named when one
// is) with its km; same-named duplicates within 300 m count once. With --dir, MAP_DATA.amenities holds the centre,
// the radius in pixels, a marker per place {x, y, kind, name, km} and `detail`, sharper tiles around the circle
// for a zoom onto it (amenity-radius); without --dir only the counts are printed.
// Kinds: the keys of KINDS in amenity-map.js (their colours and icons), each with its OSM tags in AMENITIES and what
// it leaves out in EXCLUDE (school, kindergarten, university, language_school, driving_school, hospital, clinic,
// pharmacy, mall, supermarket, market, park, playground, gym, restaurant, cafe, bank, metro, station, bus). A hospital is amenity=hospital or healthcare=hospital, never a clinic,
// doctor, dentist or pharmacy (by its tags or a name such as "Phòng khám", "Nha khoa", "Dental"): those count as
// clinic when it is asked for. A school is amenity=school without driving or language centres; a mall is
// shop=mall|department_store (supermarkets are their own kind); a park is leisure=park, not a private one, a garden
// or a playground. Answers are cached in the plugin cache folder (osm-amenities/).
// Prints the same summary (without tiles and route) as JSON. Routes come from routing.openstreetmap.de (OSRM run by
// FOSSGIS); "none" joins the points with straight lines (km as the crow flies, no minutes). The map shows
// OpenStreetMap data: the summary and map-data.js carry "© OpenStreetMap contributors" as `attribution` (metadata);
// the examples draw a credit line only when the user asks (their `credit` variable, empty by default).
// --style dark: map-route and amenity-radius draw the map dark (light: in its own colours; osm: their darkMap
// variable decides). --tiles "URL,…" puts your own {z}/{x}/{y} templates first (drawn as they are; a keyed service
// such as CARTO needs its key in the URL: a watermark tile counts as a failure).
// Network failures name the request and the reason (DNS, HTTP status, timeout, refused); each request is tried twice.
// Tiles fall back to the next server (tile.openstreetmap.org → tile.openstreetmap.de → openstreetmap.fr HOT), and
// `tileServer` / `warnings` say which one was used. A failed route falls back to straight lines (warned); a failed --places lookup is skipped
// with a warning when --points are given, else it fails asking for --points "lat,lon,Label" (no probing needed).
import { createRequire } from "node:module";
import { copyFileSync, existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const TILE = 256;
const AGENT = `BashCutPro-HyperFrames/${pluginVersion()} (+https://github.com/dongnguyenvie/bashcutpro-plugins)`;
const PROFILES = { car: "routed-car", bike: "routed-bike", foot: "routed-foot" };
// Overpass servers tried in order (a busy one answers 429 or 504); OSM_OVERPASS_URL replaces the list.
const OVERPASS = (process.env.OSM_OVERPASS_URL
    ?? "https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter").split(",");
const NOMINATIM = process.env.OSM_NOMINATIM_URL ?? "https://nominatim.openstreetmap.org/search";
const ROUTING = process.env.OSM_ROUTING_URL ?? "https://routing.openstreetmap.de";
const RETRY_MS = Number(process.env.OSM_RETRY_MS ?? 800);
const OSM_CREDIT = "© OpenStreetMap contributors";

/**
 * Raster tile servers of OpenStreetMap data, tried in order (a server that fails gives way to the next for the whole
 * map, so the look never mixes). CARTO's basemaps are not among them: without an API key they answer every tile with
 * an "API KEY REQUIRED" watermark (pass them with --tiles when you have a key).
 */
export const TILE_SERVERS = [
    { key: "osm", name: "tile.openstreetmap.org", url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png" },
    { key: "osm-de", name: "tile.openstreetmap.de", url: "https://tile.openstreetmap.de/{z}/{x}/{y}.png" },
    { key: "osm-fr-hot", name: "tile-a.openstreetmap.fr (HOT)", url: "https://tile-a.openstreetmap.fr/hot/{z}/{x}/{y}.png" },
];
/** --style: how the pages draw the tiles. dark: darkened (whatever darkMap says), light: as they are, osm: darkMap. */
export const STYLES = ["osm", "dark", "light"];

/** The servers to try: --tiles templates first (drawn as they are, `custom`), then the OpenStreetMap ones. */
export function tileServers(given = "") {
    const own = String(given ?? "").split(",").map((u) => u.trim()).filter(Boolean).map((url) => ({
        key: `custom-${createHash("sha256").update(url).digest("hex").slice(0, 8)}`, name: new URL(url.replace(/[{}]/g, "")).host, url, custom: true,
    }));
    return [...own, ...TILE_SERVERS];
}

/** Why a request failed, in words an agent can act on: DNS, HTTP status, timeout, refused… */
export function describeFailure(error, seconds = 60) {
    const code = error?.cause?.code ?? error?.code;
    if (error?.status) return `HTTP ${error.status}${error.statusText ? ` ${error.statusText}` : ""}`;
    if (error?.name === "TimeoutError" || error?.name === "AbortError" || code === "UND_ERR_CONNECT_TIMEOUT") return `timeout after ${seconds} s`;
    if (code === "ENOTFOUND" || code === "EAI_AGAIN" || code === "EAI_NONAME") return `DNS: ${error.cause?.hostname ?? "the host"} did not resolve`;
    if (code === "ECONNREFUSED") return "connection refused";
    if (code === "ECONNRESET" || code === "UND_ERR_SOCKET") return "connection reset";
    if (code && /CERT|TLS|SSL/.test(code)) return `TLS: ${code}`;
    return error?.cause?.message ?? error?.message ?? String(error);
}

/** The amenity kinds: one list, amenity-map.js's (the page draws each with its colour and icon). */
export const KINDS = Object.keys(createRequire(import.meta.url)("./amenity-map.js").KINDS);

/** Friendly kinds → the OpenStreetMap tags that make one: a row matches when each of its keys has one of its values. */
export const AMENITIES = {
    school: [{ amenity: "school" }],
    kindergarten: [{ amenity: "kindergarten" }],
    university: [{ amenity: "university|college" }],
    language_school: [{ amenity: "language_school" }],
    driving_school: [{ amenity: "driving_school" }],
    hospital: [{ amenity: "hospital" }, { healthcare: "hospital" }],
    clinic: [{ amenity: "clinic|doctors|dentist" }, { healthcare: "clinic|doctors|dentist" }],
    pharmacy: [{ amenity: "pharmacy" }],
    mall: [{ shop: "mall|department_store" }],
    supermarket: [{ shop: "supermarket" }],
    market: [{ amenity: "marketplace" }],
    park: [{ leisure: "park" }],
    playground: [{ leisure: "playground" }],
    gym: [{ leisure: "fitness_centre|sports_centre" }],
    restaurant: [{ amenity: "restaurant" }],
    cafe: [{ amenity: "cafe" }],
    bank: [{ amenity: "bank" }],
    metro: [{ railway: "station", station: "subway|light_rail|monorail" }, { railway: "station", subway: "yes" }],
    station: [{ railway: "station|halt" }],
    bus: [{ highway: "bus_stop" }],
};

{
    const untagged = KINDS.filter((kind) => !AMENITIES[kind]);
    const unknown = Object.keys(AMENITIES).filter((kind) => !KINDS.includes(kind));
    if (untagged.length || unknown.length) {
        throw new Error(`amenity kinds out of step: no OSM tags for ${untagged.join(", ") || "-"}; not in amenity-map.js KINDS: ${unknown.join(", ") || "-"}`);
    }
}

const CLINICAL = "clinic|doctors|dentist|pharmacy|laboratory|optometrist|physiotherapist";
/**
 * What a kind leaves out though its AMENITIES row matches (mappers tag a dental practice amenity=hospital, a language
 * centre amenity=school): a rule matches when one of its tags has one of its values (a "a;b" value counts each part)
 * or the name has one of its words. `into` is the kind such a place counts as instead, when that kind is asked for.
 */
export const EXCLUDE = {
    hospital: [{
        into: "clinic",
        tags: { amenity: CLINICAL, healthcare: CLINICAL, "healthcare:speciality": "dentistry|orthodontics|optometry" },
        name: /phòng khám|phòng mạch|nha khoa|nhà thuốc|\b(clinic|dental|dentist|dentistry|polyclinic|pharmacy)\b/i,
    }],
    school: [
        { into: "driving_school", tags: { school: "driving" }, name: /lái xe|sát hạch|\bdriving\b/i },
        {
            into: "language_school",
            tags: { school: "language" },
            name: /trung tâm (ngoại ngữ|anh ngữ|tiếng)|\b(language (school|cent(er|re))|english (cent(er|re)|school))\b/i,
        },
    ],
    park: [{ tags: { access: "private|no" } }],
};

function pluginVersion() {
    try {
        return JSON.parse(readFileSync(new URL("../bashcut-plugin.json", import.meta.url))).version;
    } catch {
        return "0";
    }
}

function fail(message) {
    process.stderr.write(`osm-map: ${message} (osm-map --help shows the options)\n`);
    process.exit(2);
}

/** The comment at the top of this file: the options, what is written and what is printed. */
function usage() {
    const lines = readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n");
    const end = lines.findIndex((line) => !line.startsWith("//"));
    return lines.slice(0, end).map((line) => line.replace(/^\/\/ ?/, "")).join("\n").replace(/^\s+/, "");
}

function parseArgs(argv) {
    const options = { route: "car", width: 1080, height: 1920, padding: 0.2 };
    if (argv.includes("--help") || argv.includes("-h")) {
        process.stdout.write(`${usage()}\n`);
        process.exit(0);
    }
    for (let i = 0; i < argv.length; i += 1) {
        const key = argv[i];
        const value = argv[i + 1];
        if (!key.startsWith("--") || value === undefined) fail(`unexpected argument ${key}`);
        options[key.slice(2)] = value;
        i += 1;
    }
    options.places = options.places ? options.places.split(";").map((q) => q.trim()).filter(Boolean) : [];
    options.amenities = options.amenities ? options.amenities.split(",").map((k) => k.trim().toLowerCase()).filter(Boolean) : [];
    if (!options.dir && !options.places.length && !options.amenities.length) fail("--dir is required (the composition folder)");
    if (!options.points && !options.places.length) fail('--points is required: "lat,lon,Label;lat,lon,Label"');
    if (!options.points) {
        if (options.route === "car" && !argv.includes("--route")) options.route = "none";
        return checkNumbers(options);
    }
    return checkNumbers(options);
}

function checkNumbers(options) {
    for (const key of ["width", "height", "padding"]) options[key] = Number(options[key]);
    if (!(options.width > 0 && options.height > 0)) fail("--width and --height must be positive");
    if (!(options.padding >= 0 && options.padding < 0.5)) fail("--padding must be 0–0.5 (share of each side)");
    if (options.route !== "none" && !PROFILES[options.route]) fail("--route must be car, bike, foot or none");
    options.legs ??= "chain";
    options.style ??= "osm";
    if (!STYLES.includes(options.style)) fail("--style must be osm, dark or light");
    if (options.tiles) {
        for (const url of options.tiles.split(",").map((u) => u.trim()).filter(Boolean)) {
            if (!/^https?:\/\/[^/]+\/.*\{z\}.*\{x\}.*\{y\}/.test(url)) fail(`--tiles takes URL templates with {z}, {x} and {y}: ${url}`);
        }
    }
    if (!["chain", "star"].includes(options.legs)) fail("--legs must be chain or star");
    const unknown = options.amenities.filter((kind) => !KINDS.includes(kind));
    if (unknown.length) fail(`unknown amenity ${unknown.join(", ")}: one of ${KINDS.join(", ")}`);
    options.radius = Number(options.radius ?? 1000);
    if (!(options.radius >= 50 && options.radius <= 5000)) fail("--radius must be 50–5000 (metres)");
    options.points = (options.points ?? "").split(";").filter(Boolean).map((part) => {
        const [lat, lon, ...label] = part.split(",");
        const point = { lat: Number(lat), lon: Number(lon), label: label.join(",").trim() };
        if (!(Math.abs(point.lat) <= 85 && Math.abs(point.lon) <= 180)) fail(`not a latitude,longitude: ${part}`);
        return point;
    });
    if (options.points.length < 1 && !options.places.length) fail("give at least one point");
    if (options.zoom !== undefined) {
        options.zoom = Number(options.zoom);
        if (!Number.isInteger(options.zoom) || options.zoom < 1 || options.zoom > 19) fail("--zoom must be 1–19");
    }
    return options;
}

// Web Mercator, in pixels of the whole world at zoom z.
function project(lat, lon, z) {
    const scale = TILE * 2 ** z;
    const sin = Math.sin((lat * Math.PI) / 180);
    return [((lon + 180) / 360) * scale, (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale];
}

function haversineKm(a, b) {
    const rad = Math.PI / 180;
    const h = Math.sin(((b.lat - a.lat) * rad) / 2) ** 2
        + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(((b.lon - a.lon) * rad) / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.sqrt(h));
}

function cacheRoot() {
    return process.env.BASHCUT_PLUGIN_CACHE ?? join(homedir(), ".cache", "bashcut.hyperframes");
}

/** The URL without its query, short enough for a message. */
function shown(url) {
    const u = new URL(url);
    return `${u.protocol}//${u.host}${u.pathname.length > 60 ? `${u.pathname.slice(0, 57)}…` : u.pathname}`;
}

/**
 * GET with a real User-Agent, tried twice (a second after a network error, a timeout, 429 or 5xx). A failure throws
 * an Error naming the request and the reason ("GET https://tile.openstreetmap.org/15/… failed twice: DNS: … did not
 * resolve"), with `.reason` alone.
 */
async function get(url, seconds = 60) {
    let reason;
    for (let attempt = 0; attempt < 2; attempt += 1) {
        if (attempt) await new Promise((done) => setTimeout(done, RETRY_MS));
        try {
            const response = await fetch(url, { headers: { "User-Agent": AGENT }, signal: AbortSignal.timeout(seconds * 1000) });
            // A keyed tile service (CARTO) answers 200 with a watermark tile: as good as a refusal.
            if (response.ok && /^(W\/)?"?wm-/.test(response.headers.get("etag") ?? "")) {
                reason = "watermark tile (the server wants an API key)";
                break;
            }
            if (response.ok) return response;
            reason = describeFailure({ status: response.status, statusText: response.statusText });
            if (response.status !== 429 && response.status < 500) break;
        } catch (error) {
            reason = describeFailure(error, seconds);
        }
    }
    const error = new Error(`GET ${shown(url)} failed: ${reason}`);
    error.reason = reason;
    error.host = new URL(url).host;
    throw error;
}

/** A route's line ([lat, lon]…) cut into one line per leg, at the vertex nearest each stop between the ends. */
export function splitLine(line, points) {
    const far = (a, p) => (a[0] - p.lat) ** 2 + (a[1] - p.lon) ** 2;
    const cuts = [0];
    for (let i = 1; i < points.length - 1; i += 1) {
        let best = cuts[i - 1];
        for (let j = best; j < line.length; j += 1) if (far(line[j], points[i]) < far(line[best], points[i])) best = j;
        cuts.push(best);
    }
    cuts.push(line.length - 1);
    return cuts.slice(1).map((end, i) => line.slice(cuts[i], end + 1));
}

/** Point to point (chain), or from the first point to each other one (star: one routing request per leg). */
async function route(points, profile, shape = "chain") {
    if (shape === "star" && points.length > 2) {
        const each = [];
        for (let i = 1; i < points.length; i += 1) each.push(await routeChain([points[0], points[i]], profile));
        return { line: each.flatMap((r) => r.line), legs: each.map((r, i) => ({ ...r.legs[0], from: 0, to: i + 1 })) };
    }
    return routeChain(points, profile);
}

async function routeChain(points, profile) {
    if (profile === "none" || points.length < 2) {
        const legs = points.slice(1).map((to, i) => ({
            from: i, to: i + 1, km: haversineKm(points[i], to), minutes: null, line: [[points[i].lat, points[i].lon], [to.lat, to.lon]],
        }));
        return { line: points.map((p) => [p.lat, p.lon]), legs };
    }
    const coordinates = points.map((p) => `${p.lon},${p.lat}`).join(";");
    const url = `${ROUTING}/${PROFILES[profile]}/route/v1/driving/${coordinates}`
        + "?overview=full&geometries=geojson";
    const body = await (await get(url)).json();
    if (body.code !== "Ok" || !body.routes?.length) throw new Error(`no route: ${body.message ?? body.code}`);
    const best = body.routes[0];
    const line = best.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    const parts = splitLine(line, points);
    return {
        line,
        legs: best.legs.map((leg, i) => ({ from: i, to: i + 1, km: leg.distance / 1000, minutes: leg.duration / 60, line: parts[i] })),
    };
}

/** [smallest, largest] of many numbers (a long route has more than Math.max(...xs) takes as arguments). */
function extent(xs) {
    return xs.reduce(([lo, hi], x) => [Math.min(lo, x), Math.max(hi, x)], [Infinity, -Infinity]);
}

function fitZoom(line, options) {
    if (options.zoom) return options.zoom;
    const roomX = options.width * (1 - 2 * options.padding);
    const roomY = options.height * (1 - 2 * options.padding);
    for (let z = 17; z >= 2; z -= 1) {
        const xy = line.map(([lat, lon]) => project(lat, lon, z));
        const [x0, x1] = extent(xy.map((p) => p[0]));
        const [y0, y1] = extent(xy.map((p) => p[1]));
        const [spanX, spanY] = [x1 - x0, y1 - y0];
        if (spanX <= roomX && spanY <= roomY) return z;
    }
    return 2;
}

/**
 * The tiles covering the canvas from `origin` at zoom z, from the first server in `servers` that gives them all (a
 * failing one gives way to the next for the whole map, so the look never mixes). Returns {placed, downloaded, server,
 * failed: ["server: reason"]}.
 */
async function tiles(origin, z, options, servers) {
    const failed = [];
    for (const server of servers) {
        try {
            return { ...(await tilesFrom(server, origin, z, options)), server, failed };
        } catch (error) {
            failed.push(`${server.name}: ${error.message}`);
        }
    }
    throw new Error(`no tile server answered (${failed.join("; ")}); give one with --tiles "https://…/{z}/{x}/{y}.png"`);
}

async function tilesFrom(server, origin, z, options) {
    const cache = join(cacheRoot(), server.key === "osm" ? "osm-tiles" : `tiles-${server.key}`);
    const out = join(options.dir, "map");
    const prefix = server.key === "osm" ? "" : `${server.key}-`;
    mkdirSync(cache, { recursive: true });
    mkdirSync(out, { recursive: true });
    const count = 2 ** z;
    const wanted = [];
    for (let ty = Math.floor(origin[1] / TILE); ty * TILE < origin[1] + options.height; ty += 1) {
        for (let tx = Math.floor(origin[0] / TILE); tx * TILE < origin[0] + options.width; tx += 1) {
            if (ty < 0 || ty >= count) continue;
            wanted.push({ tx, ty, wrapped: ((tx % count) + count) % count });
        }
    }
    let downloaded = 0;
    // Two at a time, cached: the OpenStreetMap tile policy asks for a real User-Agent and no bulk downloads.
    const queue = [...wanted];
    async function worker() {
        for (let item = queue.shift(); item; item = queue.shift()) {
            const name = `${prefix}${z}-${item.wrapped}-${item.ty}.png`;
            const cached = join(cache, `${z}-${item.wrapped}-${item.ty}.png`);
            if (!existsSync(cached)) {
                const url = server.url.replace("{z}", z).replace("{x}", item.wrapped).replace("{y}", item.ty);
                const bytes = Buffer.from(await (await get(url, 30)).arrayBuffer());
                writeFileSync(cached, bytes);
                downloaded += 1;
            }
            copyFileSync(cached, join(out, name));
        }
    }
    // One failure stops both workers (Promise.all rejects at once; the other stops at its next tile).
    try {
        await Promise.all([worker(), worker()]);
    } catch (error) {
        queue.length = 0;
        throw error;
    }
    const placed = wanted.map((t) => ({
        src: `map/${prefix}${z}-${t.wrapped}-${t.ty}.png`,
        x: Math.round(t.tx * TILE - origin[0]),
        y: Math.round(t.ty * TILE - origin[1]),
        size: TILE,
    }));
    return { placed, downloaded };
}

const round = (value, digits) => (value === null ? null : Number(value.toFixed(digits)));

const EARTH = 6371008.8;

/** Area of a ring of [lon, lat] on the sphere, in m² (unsigned). */
export function ringArea(ring) {
    const rad = Math.PI / 180;
    let sum = 0;
    for (let i = 0; i < ring.length; i += 1) {
        const [lon1, lat1] = ring[i];
        const [lon2, lat2] = ring[(i + 1) % ring.length];
        sum += (lon2 - lon1) * rad * (2 + Math.sin(lat1 * rad) + Math.sin(lat2 * rad));
    }
    return Math.abs((sum * EARTH * EARTH) / 2);
}

export function ringLengthKm(ring) {
    let km = 0;
    for (let i = 1; i < ring.length; i += 1) {
        km += haversineKm({ lat: ring[i - 1][1], lon: ring[i - 1][0] }, { lat: ring[i][1], lon: ring[i][0] });
    }
    return km;
}

/** areaM2 and perimeterKm of a GeoJSON geometry: outer rings minus holes; a line has a length only. */
export function measure(geometry) {
    const polygons = geometry?.type === "Polygon" ? [geometry.coordinates]
        : geometry?.type === "MultiPolygon" ? geometry.coordinates : [];
    if (polygons.length) {
        let area = 0;
        let perimeter = 0;
        for (const [outer, ...holes] of polygons) {
            area += ringArea(outer) - holes.reduce((sum, hole) => sum + ringArea(hole), 0);
            perimeter += ringLengthKm(outer);
        }
        return { areaM2: area, perimeterKm: perimeter, rings: polygons.map((p) => p[0]) };
    }
    if (geometry?.type === "LineString") return { areaM2: null, perimeterKm: ringLengthKm(geometry.coordinates), rings: [geometry.coordinates] };
    return { areaM2: null, perimeterKm: null, rings: [] };
}

/** A cached answer, or null when there is none or it does not parse (a write cut short): it is asked for again. */
function cached(file) {
    if (!existsSync(file)) return null;
    try {
        return JSON.parse(readFileSync(file, "utf8"));
    } catch {
        return null;
    }
}

async function lookUp(query, last) {
    const cache = join(cacheRoot(), "osm-places");
    mkdirSync(cache, { recursive: true });
    const file = join(cache, `${createHash("sha256").update(query).digest("hex").slice(0, 20)}.json`);
    const kept = cached(file);
    if (kept) return kept;
    // Nominatim's usage policy: one request per second at most, with a real User-Agent.
    const wait = 1100 - (Date.now() - last.at);
    if (wait > 0) await new Promise((done) => setTimeout(done, wait));
    last.at = Date.now();
    const url = `${NOMINATIM}?format=jsonv2&polygon_geojson=1&limit=1&q=${encodeURIComponent(query)}`;
    const found = (await (await get(url)).json())[0];
    if (!found) throw new Error(`OpenStreetMap has no place named "${query}" (add the city or country)`);
    writeFileSync(file, JSON.stringify(found));
    return found;
}

async function places(queries) {
    const last = { at: 0 };
    const out = [];
    for (const query of queries) {
        const found = await lookUp(query, last);
        const measured = measure(found.geojson);
        out.push({
            query,
            name: found.name || String(found.display_name ?? "").split(",")[0],
            osm: `${found.osm_type}/${found.osm_id}`,
            kind: `${found.category}/${found.type}`,
            lat: Number(found.lat),
            lon: Number(found.lon),
            areaM2: measured.areaM2 === null ? null : Math.round(measured.areaM2),
            areaHa: round(measured.areaM2 === null ? null : measured.areaM2 / 1e4, 1),
            areaKm2: round(measured.areaM2 === null ? null : measured.areaM2 / 1e6, 3),
            perimeterKm: round(measured.perimeterKm, 2),
            rings: measured.rings,
        });
    }
    return out;
}

/** The kinds whose places another kind may take over (a clinic tagged hospital comes with the hospital rows). */
const sources = (kind) => [kind, ...Object.keys(EXCLUDE).filter((from) => from !== kind && EXCLUDE[from].some((r) => r.into === kind))];

/** One Overpass query for every kind: places within `radius` metres of `centre`, ways and relations by their centre. */
export function overpassQuery(kinds, centre, radius) {
    const around = `(around:${Math.round(radius)},${centre.lat.toFixed(5)},${centre.lon.toFixed(5)})`;
    const rows = new Set(kinds.flatMap(sources).flatMap((kind) => AMENITIES[kind].map((row) => `nwr${Object.entries(row)
        .map(([key, values]) => `["${key}"~"^(${values})$"]`).join("")}${around};`)));
    return `[out:json][timeout:25];\n(\n${[...rows].join("\n")}\n);\nout center tags qt;`;
}

const matches = (tags, row) => Object.entries(row).every(([key, values]) => values.split("|").includes(tags?.[key]));
const excludes = (tags, rule) => Object.entries(rule.tags ?? {}).some(([key, values]) =>
    String(tags?.[key] ?? "").split(";").some((part) => values.split("|").includes(part.trim())))
    || Boolean(rule.name && tags?.name && rule.name.test(tags.name));

/** Whether a place with these tags counts as `kind`: its row matches and no EXCLUDE rule does, or another kind's rule
 *  sends it here. */
export function isKind(tags, kind) {
    const own = AMENITIES[kind].some((row) => matches(tags, row));
    if (own && !(EXCLUDE[kind] ?? []).some((rule) => excludes(tags, rule))) return true;
    return sources(kind).slice(1).some((from) => AMENITIES[from].some((row) => matches(tags, row))
        && EXCLUDE[from].some((rule) => rule.into === kind && excludes(tags, rule)));
}

/** Overpass elements → per kind {kind, count, nearest {name, km}} and one marker per place, nearest first. */
export function countAmenities(elements, kinds, centre, radius) {
    const markers = [];
    const counted = kinds.map((kind) => {
        const found = [];
        for (const element of elements) {
            if (!isKind(element.tags, kind)) continue;
            const lat = element.lat ?? element.center?.lat;
            const lon = element.lon ?? element.center?.lon;
            if (lat === undefined || lon === undefined) continue;
            // Overpass keeps a park whose edge is in the circle; it counts here when its centre is.
            const km = haversineKm(centre, { lat, lon });
            if (km * 1000 > radius) continue;
            const name = element.tags?.name ?? null;
            // One place mapped twice (a node and its building outline) counts once.
            if (name && found.some((f) => f.name === name && haversineKm(f, { lat, lon }) < 0.3)) continue;
            found.push({ kind, name, lat, lon, km });
        }
        found.sort((a, b) => a.km - b.km);
        markers.push(...found);
        const nearest = found.find((f) => f.name) ?? found[0];
        return { kind, count: found.length, nearest: nearest ? { name: nearest.name, km: round(nearest.km, 2) } : null };
    });
    return { kinds: counted, markers };
}

async function overpass(query) {
    const cache = join(cacheRoot(), "osm-amenities");
    mkdirSync(cache, { recursive: true });
    const file = join(cache, `${createHash("sha256").update(query).digest("hex").slice(0, 20)}.json`);
    const kept = cached(file);
    if (kept) return kept;
    // Overpass usage policy: a real User-Agent, one query at a time, answers cached instead of asked again.
    let failure;
    for (const server of OVERPASS) {
        try {
            const body = await (await get(`${server}?data=${encodeURIComponent(query)}`, 90)).json();
            if (!Array.isArray(body.elements) || /error/i.test(body.remark ?? "")) throw new Error(body.remark ?? "no elements");
            const elements = body.elements.map(({ type, id, lat, lon, center, tags }) => ({ type, id, lat, lon, center, tags }));
            writeFileSync(file, JSON.stringify(elements));
            return elements;
        } catch (error) {
            failure = failure ? `${failure}; ${error.message}` : error.message;
        }
    }
    throw new Error(`Overpass did not answer (${failure}); amenity counts need it (OSM_OVERPASS_URL takes another server)`);
}

async function amenities(options, centre) {
    const elements = await overpass(overpassQuery(options.amenities, centre, options.radius));
    return countAmenities(elements, options.amenities, centre, options.radius);
}

/** At most `limit` points of a ring, evenly picked (outlines drawn on a card need no more). */
function thin(ring, limit = 400) {
    if (ring.length <= limit) return ring;
    const step = ring.length / limit;
    return Array.from({ length: limit }, (_, i) => ring[Math.floor(i * step)]).concat([ring[ring.length - 1]]);
}

/** The centre, the radius and each marker in canvas pixels (`local` turns world pixels at zoom z into canvas ones). */
export function placeAmenities(markers, centre, radius, z, local) {
    const metresPerPixel = (2 * Math.PI * 6378137 * Math.cos((centre.lat * Math.PI) / 180)) / (TILE * 2 ** z);
    const [x, y] = local(project(centre.lat, centre.lon, z));
    return {
        centre: { x, y, label: centre.label, lat: centre.lat, lon: centre.lon },
        radius: round(radius / metresPerPixel, 1),
        markers: markers.map((m) => {
            const [mx, my] = local(project(m.lat, m.lon, z));
            return { x: mx, y: my, kind: m.kind, name: m.name, km: round(m.km, 2) };
        }),
    };
}

async function main() {
    const options = parseArgs(process.argv.slice(2));
    if (options.dir && !existsSync(options.dir)) fail(`${options.dir} does not exist`);
    const warnings = [];
    let found = [];
    try {
        found = await places(options.places);
    } catch (error) {
        const why = `--places lookup failed: ${error.message}`;
        if (!options.points.length) fail(`${why}. Give the places as --points "lat,lon,Label;…" (coordinates from the user, the brief or a named source); outlines and areas need the lookup`);
        warnings.push(`${why}; drawn from --points without outlines or areas`);
    }
    const facts = found.map(({ rings, ...fact }) => fact);
    const first = options.points[0] ?? (found[0] && { lat: found[0].lat, lon: found[0].lon, label: found[0].name });
    const nearby = options.amenities.length ? await amenities(options, first) : null;
    const around = nearby && {
        centre: { lat: first.lat, lon: first.lon, label: first.label }, radiusM: options.radius, kinds: nearby.kinds,
        source: "Overpass API (OpenStreetMap data)",
    };
    // A kind with nothing inside the radius is left out of the amenity-radius legend (showZero puts it back).
    const none = nearby ? nearby.kinds.filter((k) => !k.count).map((k) => k.kind) : [];
    if (none.length) Object.assign(around, { none, note: `nothing found within ${options.radius} m: ${none.join(", ")}; left out of the legend` });
    if (!options.dir) {
        const out = found.length ? { places: facts } : {};
        if (around) out.amenities = around;
        out.attribution = OSM_CREDIT;
        if (found.length) out.source = `Nominatim, ${new URL(NOMINATIM).host}`;
        if (warnings.length) out.warnings = warnings;
        process.stdout.write(`${JSON.stringify(out, null, 2)}\n`);
        return;
    }
    if (!options.points.length) options.points = found.map((p) => ({ lat: p.lat, lon: p.lon, label: p.name }));
    let routed;
    try {
        routed = await route(options.points, options.route, options.legs);
    } catch (error) {
        warnings.push(`route failed (${error.message}); legs are straight lines (km as the crow flies, no minutes)`);
        options.route = "none";
        routed = await route(options.points, "none", options.legs);
    }
    const { line, legs } = routed;
    const outlines = found.flatMap((p) => p.rings.map((ring) => ring.map(([lon, lat]) => [lat, lon])));
    // The radius circle is fitted and centred with the route.
    const reach = around ? [[1, 1], [-1, -1]].map(([a, b]) => [first.lat + (a * options.radius) / 111320,
        first.lon + (b * options.radius) / (111320 * Math.cos((first.lat * Math.PI) / 180))]) : [];
    const z = fitZoom(line.concat(options.points.map((p) => [p.lat, p.lon]), reach, ...outlines), options);
    const xy = line.map(([lat, lon]) => project(lat, lon, z));
    const box = xy.concat(reach.map(([lat, lon]) => project(lat, lon, z)));
    const [minX, maxX] = extent(box.map((p) => p[0]));
    const [minY, maxY] = extent(box.map((p) => p[1]));
    const origin = [(minX + maxX) / 2 - options.width / 2, (minY + maxY) / 2 - options.height / 2];
    const local = ([x, y]) => [Math.round((x - origin[0]) * 10) / 10, Math.round((y - origin[1]) * 10) / 10];
    let base;
    try {
        base = await tiles(origin, z, options, tileServers(options.tiles));
    } catch (error) {
        fail(error.message);
    }
    const { placed, downloaded, server } = base;
    if (base.failed.length) warnings.push(`tiles: ${base.failed.join("; ")}; used ${server.name}`);
    // A star's legs do not add up to one trip: no total.
    const star = options.legs === "star";
    const km = star ? null : legs.reduce((sum, leg) => sum + leg.km, 0);
    const minutes = !star && legs.every((leg) => leg.minutes !== null) ? legs.reduce((sum, leg) => sum + leg.minutes, 0) : null;
    const summary = {
        width: options.width,
        height: options.height,
        zoom: z,
        profile: options.route,
        km: round(km, 2),
        minutes: round(minutes, 1),
        legs: legs.map(({ line: _, ...leg }) => ({ ...leg, km: round(leg.km, 2), minutes: round(leg.minutes, 1) })),
        points: options.points.map((p) => {
            const [x, y] = local(project(p.lat, p.lon, z));
            return { x, y, label: p.label, lat: p.lat, lon: p.lon };
        }),
        attribution: OSM_CREDIT,
        style: options.style,
        tileServer: server.name,
        ...(server.custom ? { customTiles: true } : {}),
        routeSource: options.route === "none" ? "straight lines" : "OSRM, routing.openstreetmap.de (FOSSGIS)",
    };
    if (found.length) summary.places = facts;
    if (warnings.length) summary.warnings = warnings;
    if (around) summary.amenities = around;
    const data = { ...summary, tiles: placed, route: xy.map(local) };
    data.legs = summary.legs.map((leg, i) => ({ ...leg, line: thin(legs[i].line).map(([lat, lon]) => local(project(lat, lon, z))) }));
    let detail = { placed: [], downloaded: 0 };
    if (around) {
        data.amenities = { ...around, ...placeAmenities(nearby.markers, first, options.radius, z, local) };
        // Sharper tiles around the circle, for a page that zooms in on it: zoom z + dz, where the circle would span
        // about three quarters of the canvas's short side; positioned in canvas pixels at `scale` (2^-dz).
        const dz = Math.max(0, Math.min(3, 18 - z, Math.round(Math.log2((0.38 * Math.min(options.width, options.height)) / data.amenities.radius))));
        if (dz > 0) {
            detail = { placed: [], downloaded: 0 };
            const [wx, wy] = project(first.lat, first.lon, z + dz);
            const near = [wx - options.width / 2, wy - options.height / 2];
            try {
                detail = await tiles(near, z + dz, options, [server]);
            } catch (error) {
                warnings.push(`sharper tiles around the circle failed (${error.message}); the page zooms the base tiles`);
            }
            if (detail.placed.length) data.amenities.detail = {
                zoom: z + dz, scale: 2 ** -dz, x: round(near[0] / 2 ** dz - origin[0], 1), y: round(near[1] / 2 ** dz - origin[1], 1),
                tiles: detail.placed,
            };
        }
    }
    if (found.length) {
        data.places = found.map(({ rings, ...fact }) => ({
            ...fact, rings: rings.map((ring) => thin(ring).map(([lon, lat]) => local(project(lat, lon, z)))),
        }));
    }
    writeFileSync(join(options.dir, "map-data.js"),
        `// Written by osm-map. Map data © OpenStreetMap contributors (ODbL).\nwindow.MAP_DATA = ${JSON.stringify(data)};\n`);
    process.stdout.write(`${JSON.stringify({ ...summary, tiles: placed.length + detail.placed.length, downloaded: downloaded + detail.downloaded, file: join(options.dir, "map-data.js") }, null, 2)}\n`);
}

// Compared as real paths: the plugin folder is often reached through a symlink.
if (process.argv[1] && realpathSync(resolve(process.argv[1])) === realpathSync(fileURLToPath(import.meta.url))) {
    main().catch((error) => fail(error.message));
}
