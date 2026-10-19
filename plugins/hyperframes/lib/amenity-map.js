// Map pieces for the amenity-radius example: the brand look, the category colours and icons, tiles, pins, the legend
// chips, and the amenity markers laid out so no two icons touch at the scale they are seen at. graphics.render
// copies it next to the page (like GSAP), and the tests load it in Node to check declutter (pure, no DOM).
(function (root) {
    "use strict";

    // MARK: categories (24 × 24 icon paths); a kind not listed gets a dot in the secondary colour

    const CAP = "M12 3 1 9l11 6 9-4.9V17h2V9L12 3zm-7 9.2v4L12 20l7-3.8v-4L12 16l-7-3.8z";
    const CROSS = "M10 3h4v7h7v4h-7v7h-4v-7H3v-4h7z";
    const BAG = "M6 7V6a6 6 0 0 1 12 0v1h3l-1 14H4L3 7h3zm2 0h8V6a4 4 0 0 0-8 0v1z";
    const TREE = "M12 2 5 12h4l-4 6h6v4h2v-4h6l-4-6h4z";
    const TRAIN = "M12 2C8 2 4 2.5 4 6v9.5A3.5 3.5 0 0 0 7.5 19L6 20.5v.5h12v-.5L16.5 19a3.5 3.5 0 0 0 3.5-3.5V6c0-3.5-4-4-8-4zM7.5 17a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm9 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zM18 10H6V6h12v4z";
    const CART = "M7 18a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm10 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM1 2h3.3l1 2H21l-3.6 8H8.1l-.9 2H19v2H4l2.4-4.4L3 4H1z";
    const CUP = "M4 4h14v4h1a3 3 0 0 1 0 6h-1.3A7 7 0 0 1 4 11V4zm14 6v2h1a1 1 0 0 0 0-2h-1zM3 19h16v2H3z";
    const BANK = "M12 2 2 7v2h20V7L12 2zM4 11v7h3v-7H4zm6.5 0v7h3v-7h-3zM17 11v7h3v-7h-3zM2 20v2h20v-2H2z";
    const DOT = "M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14z";
    const KINDS = {
        school: ["#FFC94D", CAP], kindergarten: ["#FFC94D", CAP], university: ["#FFC94D", CAP],
        language_school: ["#FFC94D", CAP], driving_school: ["#FFC94D", CAP],
        hospital: ["#FF5A5F", CROSS], clinic: ["#FF8A8E", CROSS], pharmacy: ["#3DDC97", CROSS],
        mall: ["#FF7AC6", BAG], market: ["#FF9F43", BAG], supermarket: ["#FF9F43", CART],
        park: ["#4CD964", TREE], playground: ["#4CD964", TREE], gym: ["#7C83FD", DOT],
        restaurant: ["#FF9F43", CUP], cafe: ["#C08552", CUP], bank: ["#5AC8FA", BANK],
        metro: ["#5AC8FA", TRAIN], station: ["#5AC8FA", TRAIN], bus: ["#5AC8FA", TRAIN],
    };
    let muted = "#9AA3B2";
    function kindOf(kind) { return KINDS[kind] ?? [muted, DOT]; }
    function icon(kind, size) { return `<svg class="icon" viewBox="0 0 24 24" width="${size}" height="${size}"><path d="${kindOf(kind)[1]}"/></svg>`; }

    // MARK: brand look (bin/graphic fills accent, background, text, secondary and the fonts from the project's brand)

    /** A font is a family or PostScript name on this Mac, System-<Weight> (San Francisco) or a font file in the folder. */
    function applyLook(v, doc) {
        const look = doc.documentElement.style;
        for (const key of ["accent", "background", "text", "secondary"]) if (v[key]) look.setProperty(`--${key}`, v[key]);
        if (v.secondary) muted = v.secondary;
        const faces = doc.createElement("style");
        for (const [key, slot] of [["headingFont", "--heading-font"], ["bodyFont", "--body-font"]]) {
            const name = String(v[key] ?? "").trim();
            if (!name) continue;
            const file = /\.(ttf|otf|woff2?)$/i.test(name);
            if (!name.startsWith("System-")) faces.textContent += `@font-face { font-family: "${key}"; src: ${file ? `url("${name}")` : `local("${name}")`}; }`;
            look.setProperty(slot, name.startsWith("System-") ? "system-ui" : file ? `"${key}"` : `"${name}", "${key}"`);
        }
        doc.head.appendChild(faces);
    }

    /** A JSON variable such as kindLabels; anything that does not parse is {}. */
    function parseJSON(text) {
        try {
            return JSON.parse(text || "{}");
        } catch {
            return {};
        }
    }

    // MARK: declutter (pure: map pixels in, frame pixels out)

    /**
     * Markers laid out so no two icons overlap when the map is drawn at `scale` (map pixels → frame pixels) and each
     * icon is `size` frame pixels across: places of one kind closer than that merge into one marker with a count (the
     * nearest to the centre stands for them), then markers of different kinds are pushed apart, each keeping a leader
     * line back to its place. `fixed` never move: discs such as the project's dot {x, y, r} and boxes such as its name
     * tag {x, y, left, top, w, h}, x and y in map pixels, the rest in frame pixels from that point. The same input
     * always gives the same layout. One entry per marker, in its order:
     * {shown, count, dx, dy}, the icon's offset from its place in frame pixels (a merged marker is not shown).
     */
    function declutter(markers, scale, size, fixed) {
        const gap = size * 1.08;
        const order = markers.map((_, i) => i).sort((a, b) => (markers[a].km ?? 0) - (markers[b].km ?? 0) || a - b);
        const out = markers.map(() => ({ shown: false, count: 0, dx: 0, dy: 0 }));
        const heads = [];
        for (const i of order) {
            const x = markers[i].x * scale;
            const y = markers[i].y * scale;
            const head = heads.find((h) => h.kind === markers[i].kind && Math.hypot(h.ax - x, h.ay - y) < gap);
            if (head) {
                out[head.i].count += 1;
                continue;
            }
            heads.push({ i, kind: markers[i].kind, x, y, ax: x, ay: y });
            Object.assign(out[i], { shown: true, count: 1 });
        }
        const discs = (fixed ?? []).filter((d) => d.r !== undefined).map((d) => ({ x: d.x * scale, y: d.y * scale, min: d.r + gap / 2 }));
        const boxes = (fixed ?? []).filter((d) => d.w > 0 && d.h > 0).map((b) => {
            const [x0, y0] = [b.x * scale + b.left, b.y * scale + b.top];
            return [x0, y0, x0 + b.w, y0 + b.h];
        });
        // Push apart, a pair at a time, until nothing touches (coincident places part along a golden-angle step).
        for (let round = 0; round < 500; round += 1) {
            let moved = false;
            heads.forEach((p, a) => {
                for (const d of discs) moved = part(d, p, d.min, true) || moved;
                for (const box of boxes) moved = clear(box, p, gap / 2) || moved;
                for (let b = a + 1; b < heads.length; b += 1) moved = part(p, heads[b], gap, false, b) || moved;
            });
            if (!moved) break;
        }
        for (const h of heads) Object.assign(out[h.i], { dx: h.x - h.ax, dy: h.y - h.ay });
        return out;
    }

    /** Moves q (and p half of the way, unless p is fixed) so they are `min` apart; true when it moved anything. */
    function part(p, q, min, pFixed, seed = 1) {
        let dx = q.x - p.x;
        let dy = q.y - p.y;
        let d = Math.hypot(dx, dy);
        if (d >= min - 1e-6) return false;
        if (d < 1e-6) [dx, dy, d] = [Math.cos(seed * 2.39996), Math.sin(seed * 2.39996), 1];
        const push = min - d + 0.01;
        const pShare = pFixed ? 0 : 0.5;
        p.x -= (dx / d) * push * pShare;
        p.y -= (dy / d) * push * pShare;
        q.x += (dx / d) * push * (1 - pShare);
        q.y += (dy / d) * push * (1 - pShare);
        return true;
    }

    /** Moves p out of a box [x0, y0, x1, y1] until its edge is `min` away; true when it moved. */
    function clear([x0, y0, x1, y1], p, min) {
        const cx = Math.max(x0, Math.min(x1, p.x));
        const cy = Math.max(y0, Math.min(y1, p.y));
        const d = Math.hypot(p.x - cx, p.y - cy);
        if (d >= min - 1e-6) return false;
        if (d > 1e-6) {
            p.x += ((p.x - cx) / d) * (min - d + 0.01);
            p.y += ((p.y - cy) / d) * (min - d + 0.01);
            return true;
        }
        // Inside: out through the nearest side.
        const exits = [[x0 - p.x - min, 0], [x1 - p.x + min, 0], [0, y0 - p.y - min], [0, y1 - p.y + min]];
        const [dx, dy] = exits.reduce((best, e) => (Math.abs(e[0] + e[1]) < Math.abs(best[0] + best[1]) ? e : best));
        p.x += dx + Math.sign(dx) * 0.01;
        p.y += dy + Math.sign(dy) * 0.01;
        return true;
    }

    /** Pairs of shown markers whose icons overlap (the tests check there are none). */
    function overlapping(markers, layout, scale, size) {
        const at = layout.map((l, i) => ({ ...l, x: markers[i].x * scale + l.dx, y: markers[i].y * scale + l.dy }));
        const pairs = [];
        at.forEach((p, a) => at.forEach((q, b) => {
            if (b > a && p.shown && q.shown && Math.hypot(p.x - q.x, p.y - q.y) < size - 0.5) pairs.push([a, b]);
        }));
        return pairs;
    }

    // MARK: DOM

    /**
     * One element per marker in `layer` (the page moves each `.spot` onto its place); `layout(scale, fixed)` runs
     * declutter and repositions the icons, leader lines and count badges (a fixed {x, y, el} is the box el covers,
     * measured from its offsets: call it at build time, never from the timeline). `items[i].pop` is what the timeline
     * scales.
     */
    function mountMarkers(layer, markers, u) {
        const size = 38 * u;
        const items = markers.map((m) => {
            const spot = document.createElement("div");
            spot.className = "spot";
            const color = kindOf(m.kind)[0];
            spot.innerHTML = `<div class="pop"><s style="background:${color};height:${3 * u}px;margin-top:${-1.5 * u}px"></s>`
                + `<u style="background:${color};width:${10 * u}px;height:${10 * u}px;left:${-5 * u}px;top:${-5 * u}px"></u>`
                + `<b><i style="left:${-size / 2}px;top:${-size / 2}px;width:${size}px;height:${size}px;background:${color}">`
                + `${icon(m.kind, 24 * u)}</i><em style="left:${size * 0.22}px;top:${-size * 0.72}px;font-size:${18 * u}px;`
                + `min-width:${24 * u}px;height:${24 * u}px;padding:0 ${6 * u}px"></em></b></div>`;
            layer.appendChild(spot);
            return { spot, pop: spot.firstChild, point: m, km: m.km };
        });
        // The icon plus its 3 px ring and a little air.
        const outer = size + 8 * u;
        function layout(scale, fixed) {
            const boxes = fixed.map((f) => (f.el ? { x: f.x, y: f.y, left: f.el.offsetLeft, top: f.el.offsetTop, w: f.el.offsetWidth, h: f.el.offsetHeight } : f));
            declutter(markers, scale, outer, boxes).forEach((l, i) => {
                const { spot, pop } = items[i];
                const [leader, foot, mark, badge] = [pop.children[0], pop.children[1], pop.children[2], pop.querySelector("em")];
                spot.style.display = l.shown ? "block" : "none";
                const far = Math.hypot(l.dx, l.dy);
                Object.assign(mark.style, { left: `${l.dx}px`, top: `${l.dy}px` });
                Object.assign(leader.style, { width: `${far}px`, transform: `rotate(${Math.atan2(l.dy, l.dx)}rad)`, display: far > 2 ? "block" : "none" });
                foot.style.display = far > 2 ? "block" : "none";
                badge.textContent = String(l.count);
                badge.style.display = l.count > 1 ? "flex" : "none";
            });
        }
        return { items, layout, size: outer };
    }

    /** Map tiles osm-map fetched (`data` gives the map's size in canvas pixels), as images in `layer`. */
    function mountTiles(layer, data, tiles) {
        Object.assign(layer.style, { width: `${data.width}px`, height: `${data.height}px` });
        for (const tile of tiles) {
            const img = document.createElement("img");
            img.src = tile.src;
            Object.assign(img.style, { left: `${tile.x}px`, top: `${tile.y}px`, width: `${tile.size}px`, height: `${tile.size}px` });
            layer.appendChild(img);
        }
    }

    /**
     * A pin: its dot in `layer`, its tag (first line bold, the rest in the accent) in `tags` so it sits above
     * everything; the page places both on the point and flips the tag left when it would leave the frame.
     */
    function mountPin(layer, tags, lines, size, u) {
        const px = (n) => `${n * u}px`;
        const at = document.createElement("div");
        const label = document.createElement("div");
        for (const el of [at, label]) el.className = "pin";
        at.innerHTML = '<div class="dot"></div>';
        label.innerHTML = '<div class="tag"></div>';
        layer.appendChild(at);
        tags.appendChild(label);
        const dot = at.firstChild;
        Object.assign(dot.style, { left: px(-size / 2), top: px(-size / 2), width: px(size), height: px(size), borderWidth: px(size * 0.18) });
        const tag = label.firstChild;
        Object.assign(tag.style, { padding: `${px(8)} ${px(18)}`, fontSize: px(32), top: px(lines.length > 1 ? -38 : -22) });
        lines.forEach((text, i) => {
            const line = document.createElement(i ? "span" : "b");
            line.textContent = text;
            if (i) line.style.fontSize = px(28);
            tag.appendChild(line);
        });
        if (!lines.length || !lines[0]) tag.style.display = "none";
        return { dot, tag, label, at };
    }

    /** Legend chips, one per kind: icon, count (set by the timeline), label and the nearest line. */
    function mountLegend(legend, kinds, u, labels, nearestOf) {
        const px = (n) => `${n * u}px`;
        return kinds.map((k) => {
            const chip = document.createElement("div");
            chip.className = "chip";
            Object.assign(chip.style, { height: px(116), padding: `0 ${px(22)}`, gap: px(18) });
            chip.innerHTML = `<i style="width:${px(72)};height:${px(72)};background:${kindOf(k.kind)[0]}">${icon(k.kind, 42 * u)}</i>`
                + `<div style="min-width:0"><div class="n" style="font-size:${px(56)}">0</div><div class="k" style="font-size:${px(26)}"></div>`
                + `<div class="near" style="font-size:${px(21)}"></div></div>`;
            chip.querySelector(".k").textContent = labels[k.kind] ?? k.kind;
            chip.querySelector(".near").textContent = nearestOf(k);
            legend.appendChild(chip);
            return { chip, count: k.count, n: chip.querySelector(".n") };
        });
    }

    const api = { KINDS, kindOf, icon, applyLook, parseJSON, declutter, overlapping, mountTiles, mountPin, mountMarkers, mountLegend };
    root.BCAmenityMap = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
