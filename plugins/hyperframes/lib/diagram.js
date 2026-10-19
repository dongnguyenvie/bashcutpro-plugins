// Diagram layout and motion shared by the flow-diagram, mind-map, isometric-system and big-canvas examples: the
// agent passes nodes, edges and steps as JSON, never coordinates; this script lays them out for the canvas the page
// renders at (9:16 or 16:9) and builds the GSAP timeline. graphics.render copies it next to the page (like GSAP), and
// the tests load it in Node to check the layout (no two node boxes overlap).
//
// data: {title?, nodes: [{id, label, icon?, group?}], edges: [{from, to, label?}], root? (mind-map centre),
//        direction? ("lr" | "tb", flow; auto by default), steps: [{at, focus: [ids], reveal: [ids], note?}]}
//   at: seconds from the graphic's start (bin/graphic --steps-from voice|sections fills it from the voice lines or
//   section markers); a step without a number is spread evenly over the graphic.
//   A node in no step's reveal is there from the start; reveal brings it in on that step; focus lights it up (the
//   rest dims) until the next step; packets run along the edges that touch a focused node.
// Growing diagram (one scene for a whole explainer): a node or edge with its own `at` (seconds, or "@words" in
//   graphic plan) comes in then and lights up, a new edge carries data dots until the next one lands (growthSteps);
//   a node without `at` is there from the start. Variables t0 and total render one slice of the scene: t0 is the
//   scene second this render starts at and total the scene's length, so slices rendered per section meet without a
//   jump (graphic plan sets both for rows that share render.scene). Knobs (a variable or the same data key):
//   ghosts (later blocks faint and dashed from the start), recap (scene second from which everything is lit and every
//   edge runs dots, staggered), bottomSafe (nothing below this line: 0–1 of the height or px; 0.62 on 9:16 by
//   default). A flow edge against the flow whose straight line would cross nodes goes round the stage (aroundPath).
// big-canvas data adds stops: [{at, focus: id | [x, y, w, h] (0–1 of the canvas), zoom?, note?}], summary?.
(function (root) {
    "use strict";

    // MARK: text measure (an estimate the layout and the tests share; the page shrinks a label that still overflows)

    function charWidth(cp, ch) {
        if (cp >= 0x2e80) return 1.0;
        if (ch === " ") return 0.3;
        if (/[il.,:;'|!]/.test(ch)) return 0.3;
        if (/[fjrt()[\]\-]/.test(ch)) return 0.42;
        if (/[MW]/.test(ch)) return 0.92;
        if (/[mw]/.test(ch)) return 0.84;
        if (/[0-9]/.test(ch)) return 0.6;
        if (ch !== ch.toLowerCase()) return 0.7;
        return 0.58;
    }

    function textWidth(text, size) {
        let sum = 0;
        for (const ch of String(text)) sum += charWidth(ch.codePointAt(0), ch);
        return sum * size * 1.04;
    }

    /** Greedy word wrap into lines no wider than `width`, or null when a word or the line count does not fit. */
    function wrap(text, width, size, maxLines) {
        const words = String(text).trim().split(/\s+/).filter(Boolean);
        if (!words.length) return [""];
        const lines = [];
        let line = "";
        for (const word of words) {
            if (textWidth(word, size) > width) return null;
            const next = line ? `${line} ${word}` : word;
            if (textWidth(next, size) <= width) line = next;
            else {
                lines.push(line);
                line = word;
            }
        }
        lines.push(line);
        return lines.length <= maxLines ? lines : null;
    }

    /** The largest font from maxFont down to minFont at which `text` fits w × h, with its lines. */
    function fitLabel(text, w, h, maxFont, minFont) {
        for (let font = maxFont; font >= minFont; font *= 0.93) {
            const lines = wrap(text, w, font, Math.max(1, Math.min(3, Math.floor(h / (font * 1.18)))));
            if (lines) return { font, lines, width: Math.max(...lines.map((l) => textWidth(l, font))) };
        }
        const font = minFont;
        const perLine = Math.max(4, Math.floor(w / (font * 0.6)));
        const flat = String(text).trim();
        const lines = [];
        for (let i = 0; i < flat.length && lines.length < 3; i += perLine) lines.push(flat.slice(i, i + perLine));
        return { font, lines, width: Math.min(w, Math.max(...lines.map((l) => textWidth(l, font)))), broken: true };
    }

    // MARK: graph helpers

    function readData(data) {
        const nodes = (data.nodes ?? []).map((n, i) => ({ ...n, id: String(n.id ?? `n${i}`), label: String(n.label ?? n.id ?? "") }));
        const ids = new Set(nodes.map((n) => n.id));
        const edges = (data.edges ?? []).filter((e) => ids.has(String(e.from)) && ids.has(String(e.to)) && String(e.from) !== String(e.to))
            .map((e) => ({ ...e, from: String(e.from), to: String(e.to) }));
        return { nodes, edges };
    }

    /**
     * Layer of each node: longest path from the sources (Kahn's order; a cycle is broken at its first node). With
     * `dummies`, an edge that skips layers gets a waypoint in each layer between its ends, ordered with the nodes, so
     * it runs through a slot of its own instead of through the nodes of those layers.
     */
    function layering(nodes, edges, { dummies = false } = {}) {
        const out = new Map(nodes.map((n) => [n.id, []]));
        const indeg = new Map(nodes.map((n) => [n.id, 0]));
        for (const e of edges) {
            out.get(e.from).push(e.to);
            indeg.set(e.to, indeg.get(e.to) + 1);
        }
        const layer = new Map(nodes.map((n) => [n.id, 0]));
        const done = new Set();
        const queue = nodes.filter((n) => indeg.get(n.id) === 0).map((n) => n.id);
        while (done.size < nodes.length) {
            if (!queue.length) queue.push(nodes.find((n) => !done.has(n.id)).id);
            const id = queue.shift();
            if (done.has(id)) continue;
            done.add(id);
            for (const to of out.get(id)) {
                if (done.has(to)) continue;
                layer.set(to, Math.max(layer.get(to), layer.get(id) + 1));
                indeg.set(to, indeg.get(to) - 1);
                if (indeg.get(to) === 0) queue.push(to);
            }
        }
        const count = Math.max(...layer.values()) + 1;
        const rows = Array.from({ length: count }, () => []);
        for (const n of nodes) rows[layer.get(n.id)].push(n.id);
        const near = new Map(nodes.map((n) => [n.id, []]));
        const link = (a, b) => {
            near.get(a).push(b);
            near.get(b).push(a);
        };
        const waypoints = new Map();
        edges.forEach((e, k) => {
            const la = layer.get(e.from);
            const lb = layer.get(e.to);
            if (!dummies || lb - la < 2) return link(e.from, e.to);
            const chain = [];
            for (let l = la + 1; l < lb; l += 1) {
                const id = `\u0000${k}:${l}`;
                layer.set(id, l);
                near.set(id, []);
                rows[l].push(id);
                chain.push(id);
            }
            [e.from, ...chain, e.to].forEach((id, i, all) => i && link(all[i - 1], id));
            waypoints.set(k, chain);
        });
        // Order inside a layer by the mean position of the nodes it links to in the layers before (two sweeps).
        const pos = new Map();
        const place = () => rows.forEach((row) => row.forEach((id, i) => pos.set(id, (i + 0.5) / row.length)));
        place();
        for (let sweep = 0; sweep < 2; sweep += 1) {
            for (let r = 1; r < rows.length; r += 1) {
                const mean = (id) => {
                    const linked = near.get(id).filter((o) => layer.get(o) < r);
                    return linked.length ? linked.reduce((s, o) => s + pos.get(o), 0) / linked.length : pos.get(id);
                };
                rows[r] = rows[r].map((id, i) => ({ id, key: mean(id), i })).sort((a, b) => a.key - b.key || a.i - b.i).map((x) => x.id);
                place();
            }
        }
        return { rows, layer, waypoints };
    }

    function overlaps(a, b, gap = 0) {
        return Math.abs(a.x - b.x) < (a.w + b.w) / 2 + gap - 0.01 && Math.abs(a.y - b.y) < (a.h + b.h) / 2 + gap - 0.01;
    }

    /** Pairs of node boxes that overlap (the tests' check). */
    function overlapping(nodes, gap = 0) {
        const pairs = [];
        for (let i = 0; i < nodes.length; i += 1) {
            for (let j = i + 1; j < nodes.length; j += 1) if (overlaps(nodes[i], nodes[j], gap)) pairs.push([nodes[i].id, nodes[j].id]);
        }
        return pairs;
    }

    function clampInto(n, area) {
        n.x = Math.min(Math.max(n.x, area.x + n.w / 2), area.x + area.w - n.w / 2);
        n.y = Math.min(Math.max(n.y, area.y + n.h / 2), area.y + area.h - n.h / 2);
    }

    /** Pushes overlapping boxes apart inside `area`; shrinks every box (and its font) a little when that is not enough. */
    function separate(nodes, area, gap) {
        for (let round = 0; round < 30; round += 1) {
            for (let iter = 0; iter < 150; iter += 1) {
                let moved = false;
                for (let i = 0; i < nodes.length; i += 1) {
                    for (let j = i + 1; j < nodes.length; j += 1) {
                        const a = nodes[i];
                        const b = nodes[j];
                        if (!overlaps(a, b, gap)) continue;
                        moved = true;
                        const dx = b.x - a.x;
                        const dy = b.y - a.y;
                        const ox = (a.w + b.w) / 2 + gap - Math.abs(dx);
                        const oy = (a.h + b.h) / 2 + gap - Math.abs(dy);
                        if (ox < oy) {
                            const s = dx > 0 || (dx === 0 && i < j) ? 1 : -1;
                            a.x -= (s * ox) / 2;
                            b.x += (s * ox) / 2;
                        } else {
                            const s = dy > 0 || (dy === 0 && i < j) ? 1 : -1;
                            a.y -= (s * oy) / 2;
                            b.y += (s * oy) / 2;
                        }
                    }
                }
                nodes.forEach((n) => clampInto(n, area));
                if (!moved) break;
            }
            if (!overlapping(nodes, gap / 2).length) return nodes;
            for (const n of nodes) {
                for (const key of ["w", "h", "font", "dy", "size", "labelW", "labelH"]) if (n[key] !== undefined) n[key] *= 0.92;
            }
        }
        return nodes;
    }

    /** A node box sized to its label inside at most bw × bh. */
    function sizeNode(n, bw, bh, u, { maxFont = 46, minFont = 22, pad = 22, icon = true } = {}) {
        const iconW = icon && n.icon ? maxFont * u * 1.35 : 0;
        const fit = fitLabel(n.label, bw - pad * 2 * u - iconW, bh - pad * 1.6 * u, maxFont * u, minFont * u);
        // A word cut in two reads worse than a box without its icon.
        if (fit.broken && iconW) return { ...sizeNode(n, bw, bh, u, { maxFont, minFont, pad, icon: false }), icon: undefined };
        const w = Math.min(bw, Math.max(fit.width + pad * 2 * u + iconW, Math.min(bw, 150 * u)));
        const h = Math.min(bh, Math.max(fit.lines.length * fit.font * 1.18 + pad * 1.6 * u, 84 * u));
        return { ...n, w, h, font: fit.font, lines: fit.lines, ...(fit.broken ? { broken: true } : {}) };
    }

    // MARK: layouts (all return {nodes: [{id, x, y, w, h, font, lines, …}], edges, unit, …}; x, y are centres)

    function flowLayout(data, area, unit, direction, fonts = {}) {
        const { nodes, edges } = readData(data);
        if (!nodes.length) return { nodes: [], edges: [], unit, direction: "lr" };
        const { rows, layer, waypoints } = layering(nodes, edges, { dummies: true });
        const byId = new Map(nodes.map((n) => [n.id, n]));
        const widest = Math.max(...rows.map((r) => r.length));
        const build = (dir) => {
            const lr = dir === "lr";
            const main = lr ? area.w : area.h;
            const cross = lr ? area.h : area.w;
            const cellMain = main / rows.length;
            const cellCross = cross / widest;
            const bw = Math.min(lr ? cellMain * 0.74 : cellCross * 0.86, 470 * unit);
            const bh = Math.min(lr ? cellCross * 0.76 : cellMain * 0.6, 230 * unit);
            const out = [];
            const ways = [];
            rows.forEach((row, r) => {
                row.forEach((id, i) => {
                    const along = (r + 0.5) * cellMain;
                    const across = (i + 0.5 + (widest - row.length) / 2) * cellCross;
                    // A waypoint is a small box: separate() keeps it off the nodes like any other.
                    const n = byId.has(id) ? sizeNode(byId.get(id), bw, bh, unit, fonts) : { id, w: 36 * unit, h: 36 * unit, way: true };
                    n.x = area.x + (lr ? along : across);
                    n.y = area.y + (lr ? across : along);
                    n.depth = layer.get(id);
                    (n.way ? ways : out).push(n);
                });
            });
            return { nodes: out, ways, edges, unit, direction: dir, minFont: Math.min(...out.map((n) => n.font)), broken: out.filter((n) => n.broken).length };
        };
        let chosen;
        if (direction === "lr" || direction === "tb") chosen = build(direction);
        else {
            // The canvas's own way (left→right on 16:9, top→down on 9:16) unless its labels get small and the other
            // way's are clearly larger.
            const lr = build("lr");
            const tb = build("tb");
            const prefer = area.w >= area.h ? lr : tb;
            const other = prefer === lr ? tb : lr;
            // A label cut mid-word is worse than either: the way with fewer of them wins first.
            chosen = other.broken < prefer.broken ? other : other.broken > prefer.broken ? prefer
                : prefer.minFont < Math.max(30, fonts.minFont ?? 0) * unit && other.minFont > prefer.minFont * 1.15 ? other : prefer;
        }
        separate([...chosen.nodes, ...chosen.ways], area, 12 * unit);
        const wayById = new Map(chosen.ways.map((w) => [w.id, w]));
        chosen.edges = edges.map((e, k) => (waypoints.has(k) ? { ...e, via: waypoints.get(k).map((id) => wayById.get(id)) } : e));
        delete chosen.ways;
        return chosen;
    }

    /** Tree from the root (data.root, else the first node nothing points to), children in edge order. */
    function tree(nodes, edges, rootId) {
        const ids = nodes.map((n) => n.id);
        const pointed = new Set(edges.map((e) => e.to));
        const top = rootId && ids.includes(String(rootId)) ? String(rootId) : ids.find((id) => !pointed.has(id)) ?? ids[0];
        const near = new Map(ids.map((id) => [id, []]));
        for (const e of edges) {
            near.get(e.from).push(e.to);
            near.get(e.to).push(e.from);
        }
        const parent = new Map([[top, null]]);
        const children = new Map(ids.map((id) => [id, []]));
        const order = [top];
        for (let k = 0; k < order.length; k += 1) {
            for (const next of near.get(order[k])) {
                if (parent.has(next)) continue;
                parent.set(next, order[k]);
                children.get(order[k]).push(next);
                order.push(next);
            }
        }
        // Nodes no edge reaches hang off the root.
        for (const id of ids) {
            if (parent.has(id)) continue;
            parent.set(id, top);
            children.get(top).push(id);
            order.push(id);
        }
        const depth = new Map([[top, 0]]);
        for (const id of order.slice(1)) depth.set(id, depth.get(parent.get(id)) + 1);
        return { top, parent, children, depth, order };
    }

    function mindLayout(data, area, unit) {
        const { nodes, edges } = readData(data);
        if (!nodes.length) return { nodes: [], edges: [], unit };
        const t = tree(nodes, edges, data.root);
        const byId = new Map(nodes.map((n) => [n.id, n]));
        const weight = new Map();
        const weigh = (id) => {
            const kids = t.children.get(id);
            const w = kids.length ? kids.reduce((s, k) => s + weigh(k), 0) : 1;
            weight.set(id, w);
            return w;
        };
        weigh(t.top);
        const D = Math.max(...t.depth.values());
        const landscape = area.w >= area.h;
        const cx = area.x + area.w / 2;
        const cy = area.y + area.h / 2;
        const size = (d) => (d === 0 ? [Math.min(area.w * 0.46, 480 * unit), 190 * unit, 54] : d === 1
            ? [Math.min(area.w * 0.4, 400 * unit), 150 * unit, 44] : [Math.min(area.w * 0.36, 340 * unit), 124 * unit, 38]);
        const out = new Map();
        const rootSize = size(0);
        out.set(t.top, { ...sizeNode(byId.get(t.top), rootSize[0], rootSize[1], unit, { maxFont: rootSize[2] }), x: cx, y: cy, depth: 0 });
        const leafSize = size(Math.max(1, D));
        const rx = area.w / 2 - leafSize[0] / 2;
        const ry = area.h / 2 - leafSize[1] / 2;
        const radius = (d) => (D <= 1 ? 1 : 0.6 + (0.4 * (d - 1)) / (D - 1));
        const visit = (id, from, span) => {
            let start = from;
            for (const kid of t.children.get(id)) {
                const share = (span * weight.get(kid)) / weight.get(id);
                const angle = start + share / 2;
                const d = t.depth.get(kid);
                const [bw, bh, font] = size(d);
                const n = sizeNode(byId.get(kid), bw, bh, unit, { maxFont: font });
                n.x = cx + rx * radius(d) * Math.cos(angle);
                n.y = cy + ry * radius(d) * Math.sin(angle);
                n.depth = d;
                n.angle = angle;
                out.set(kid, n);
                visit(kid, start, share);
                start += share;
            }
        };
        const kids = t.children.get(t.top).length;
        // Branches face the long sides first: left and right on 16:9, up and down on 9:16.
        const start = (landscape ? -Math.PI / 2 : -Math.PI) + (kids === 1 ? Math.PI / 2 : 0);
        visit(t.top, start, Math.PI * 2);
        const placed = t.order.map((id) => out.get(id));
        separate(placed, area, 22 * unit);
        const tight = edges.filter((e) => t.parent.get(e.to) === e.from || t.parent.get(e.from) === e.to);
        const treeEdges = t.order.slice(1).map((id) => {
            const given = tight.find((e) => (e.from === t.parent.get(id) && e.to === id) || (e.to === t.parent.get(id) && e.from === id));
            return { from: t.parent.get(id), to: id, label: given?.label };
        });
        return { nodes: placed, edges: treeEdges, unit, tree: t };
    }

    /** Blocks on an isometric grid: layer → one grid axis, place in the layer → the other. */
    function isoLayout(data, area, unit, fonts = {}) {
        const { nodes, edges } = readData(data);
        if (!nodes.length) return { nodes: [], edges: [], unit };
        const { rows, layer } = layering(nodes, edges);
        const byId = new Map(nodes.map((n) => [n.id, n]));
        const s = 0.4;
        const lift = 0.3;
        const labelRoom = 120 * unit;
        // Two ways onto the grid: layers along a grid axis (the diagram runs down-right) or along both (layers run
        // straight down, the nodes of a layer side by side); the one that gives larger blocks wins.
        const fit = (mapping) => {
            const grid = new Map();
            rows.forEach((row, r) => row.forEach((id, i) => {
                const j = (i - (row.length - 1) / 2) * (mapping === "axis" ? 1.5 : 1);
                grid.set(id, mapping === "axis" ? { gx: r, gy: j } : { gx: r + j * 0.75, gy: r - j * 0.75 });
            }));
            const keys = [...grid.values()];
            const spanX = Math.max(...keys.map((g) => g.gx - g.gy)) - Math.min(...keys.map((g) => g.gx - g.gy)) + 2 * s;
            const spanY = (Math.max(...keys.map((g) => g.gx + g.gy)) - Math.min(...keys.map((g) => g.gx + g.gy))) * 0.5 + s + lift * s;
            const c = Math.min(area.w / (spanX + 0.9), (area.h - labelRoom - 40 * unit) / Math.max(spanY, 0.5), 420 * unit);
            return { grid, keys, c };
        };
        const axis = fit("axis");
        const down = fit("down");
        const { grid, keys, c } = (area.w >= area.h ? down.c > axis.c * 1.2 : down.c * 1.2 >= axis.c) ? down : axis;
        const midX = (Math.max(...keys.map((g) => g.gx - g.gy)) + Math.min(...keys.map((g) => g.gx - g.gy))) / 2;
        const midY = (Math.max(...keys.map((g) => g.gx + g.gy)) + Math.min(...keys.map((g) => g.gx + g.gy))) / 4;
        const out = nodes.map((node) => {
            const g = grid.get(node.id);
            const px = area.x + area.w / 2 + (g.gx - g.gy - midX) * c;
            const py = area.y + (area.h - labelRoom) / 2 + ((g.gx + g.gy) * 0.5 - midY) * c;
            const blockW = 2 * s * c;
            const blockH = s * c + lift * s * c;
            const label = fitLabel(node.label, Math.max(blockW * 1.25, 230 * unit), (fonts.maxFont ? fonts.maxFont * 2.7 : 96) * unit, (fonts.maxFont ?? 36) * unit, (fonts.minFont ?? 20) * unit);
            const labelW = label.width + 28 * unit;
            const labelH = label.lines.length * label.font * 1.18 + 18 * unit;
            const w = Math.max(blockW, labelW);
            const h = blockH + 14 * unit + labelH;
            const top = py - (s * c) / 2;
            return {
                ...node, x: px, y: top + h / 2, w, h, font: label.font, lines: label.lines, depth: layer.get(node.id),
                gx: g.gx, gy: g.gy, size: s * c, dy: (s * c) / 2 - h / 2, labelW, labelH, lift,
            };
        });
        separate(out, area, 10 * unit);
        return { nodes: out, edges, unit, cell: c };
    }

    // MARK: drawing

    function rng(seed) {
        let a = 0;
        for (const ch of String(seed)) a = (Math.imul(a ^ ch.codePointAt(0), 2654435761) >>> 0);
        return () => {
            a = (a + 0x6d2b79f5) >>> 0;
            let t = a;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    /** Where the line from a box's centre towards (tx, ty) leaves the box. */
    function boxEdge(n, tx, ty) {
        const dx = tx - n.x;
        const dy = ty - n.y;
        if (!dx && !dy) return { x: n.x, y: n.y };
        const k = Math.min(Math.abs(n.w / 2 / (dx || 1e-9)), Math.abs(n.h / 2 / (dy || 1e-9)));
        return { x: n.x + dx * k, y: n.y + dy * k };
    }

    /** Whether the segment p→q crosses the box r (centre x, y; w, h). */
    function segmentHits(p, q, r) {
        const x0 = r.x - r.w / 2;
        const x1 = r.x + r.w / 2;
        const y0 = r.y - r.h / 2;
        const y1 = r.y + r.h / 2;
        let t0 = 0;
        let t1 = 1;
        const dx = q.x - p.x;
        const dy = q.y - p.y;
        for (const [num, den] of [[p.x - x0, -dx], [x1 - p.x, dx], [p.y - y0, -dy], [y1 - p.y, dy]]) {
            if (den === 0) {
                if (num < 0) return false;
            } else {
                const t = num / den;
                if (den < 0) t0 = Math.max(t0, t);
                else t1 = Math.min(t1, t);
            }
        }
        return t0 < t1;
    }

    function pointToSegment(p, a, b) {
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const t = dx || dy ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy))) : 0;
        return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy);
    }

    /** Whether the segments p0→p1 and q0→q1 cross away from their ends. */
    function segmentsCross(p0, p1, q0, q1) {
        const side = (a, b, c) => Math.sign((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x));
        return side(p0, p1, q0) * side(p0, p1, q1) < 0 && side(q0, q1, p0) * side(q0, q1, p1) < 0;
    }

    /**
     * An isometric connector from block top to block top along the grid axes: one corner (either axis first), two (a
     * step a quarter, half or three quarters of the way), or straight when every grid route runs under a block. It
     * takes the route whose legs cross the fewest blocks and labels (the joined blocks' own labels too) and that stays
     * inside `bounds` (the safe area) and runs along or across no connector drawn before (`taken`: their points; the new
     * route is added), the simpler one on a tie.
     */
    function isoPath(a, b, others, bounds, taken = []) {
        const p = { x: a.x, y: a.y + a.dy };
        const q = { x: b.x, y: b.y + b.dy };
        const dx = q.x - p.x;
        const dy = q.y - p.y;
        // d = u·(1, ½) + v·(−1, ½): the two grid axes on screen.
        const u = (dx + 2 * dy) / 2;
        const v = (2 * dy - dx) / 2;
        const U = (k) => ({ x: k, y: k / 2 });
        const V = (k) => ({ x: -k, y: k / 2 });
        const at = (...steps) => steps.reduce((o, s) => ({ x: o.x + s.x, y: o.y + s.y }), p);
        const routes = [[at(U(u))], [at(V(v))]];
        for (const t of [0.5, 0.25, 0.75]) routes.push([at(U(u * t)), at(U(u * t), V(v))], [at(V(v * t)), at(V(v * t), U(u))]);
        routes.push([]);
        const score = (corners, i) => {
            const pts = [p, ...corners, q];
            let hits = 0;
            for (const n of others) {
                // The block (its top face and sides; not the two the connector joins) and its label plate.
                const lift = n.size * n.lift * 2;
                const block = { x: n.x, y: n.y + n.dy + lift / 2, w: n.size * 2, h: n.size + lift };
                const label = { x: n.x, y: n.y + n.h / 2 - n.labelH / 2, w: n.labelW, h: n.labelH };
                for (const r of n === a || n === b ? [label] : [block, label]) {
                    for (let k = 1; k < pts.length; k += 1) hits += segmentHits(pts[k - 1], pts[k], r);
                }
            }
            const out = corners.some((m) => m.x < bounds.x || m.x > bounds.x + bounds.w || m.y < bounds.y || m.y > bounds.y + bounds.h);
            // Shared legs (a bus no one can read) and crossings with the connectors already drawn.
            let shared = 0;
            for (const line of taken) {
                for (let k = 1; k < pts.length; k += 1) {
                    for (let j = 1; j < line.length; j += 1) {
                        const [s0, s1, t0, t1] = [pts[k - 1], pts[k], line[j - 1], line[j]];
                        // A corner of one on the other's leg reads as a junction.
                        const corner = (k < pts.length - 1 && pointToSegment(s1, t0, t1) < 8) || (j < line.length - 1 && pointToSegment(t1, s0, s1) < 8);
                        const near = corner || [0.25, 0.5, 0.75].some((f) => pointToSegment({ x: s0.x + (s1.x - s0.x) * f, y: s0.y + (s1.y - s0.y) * f }, t0, t1) < 6);
                        if (near) shared += 1;
                        else if (segmentsCross(s0, s1, t0, t1)) shared += 0.3;
                    }
                }
            }
            return hits * 10 + (out ? 100 : 0) + shared * 4 + i * 0.1;
        };
        const best = routes.map((r, i) => ({ r, s: score(r, i) })).sort((x, y) => x.s - y.s)[0].r;
        taken.push([p, ...best, q]);
        return `M${p.x},${p.y} ${best.map((m) => `L${m.x},${m.y}`).join(" ")} L${q.x},${q.y}`;
    }

    function edgePath(a, b, kind, direction, sketch, seed, unit, via) {
        let p;
        let q;
        let c1;
        let c2;
        if (kind === "flow") {
            const lr = direction === "lr";
            const forward = lr ? b.x > a.x + 1 : b.y > a.y + 1;
            if (forward && via?.length) {
                // Through the waypoints of a long edge: one S-curve per layer, leaving and entering along the flow.
                const pts = [lr ? { x: a.x + a.w / 2, y: a.y } : { x: a.x, y: a.y + a.h / 2 }, ...via.map((w) => ({ x: w.x, y: w.y })),
                    lr ? { x: b.x - b.w / 2, y: b.y } : { x: b.x, y: b.y - b.h / 2 }];
                let d = `M${pts[0].x},${pts[0].y}`;
                for (let i = 1; i < pts.length; i += 1) {
                    const s0 = pts[i - 1];
                    const s1 = pts[i];
                    const m = lr ? (s1.x - s0.x) / 2 : (s1.y - s0.y) / 2;
                    d += lr ? ` C${s0.x + m},${s0.y} ${s1.x - m},${s1.y} ${s1.x},${s1.y}` : ` C${s0.x},${s0.y + m} ${s1.x},${s1.y - m} ${s1.x},${s1.y}`;
                }
                return d;
            }
            if (forward) {
                p = lr ? { x: a.x + a.w / 2, y: a.y } : { x: a.x, y: a.y + a.h / 2 };
                q = lr ? { x: b.x - b.w / 2, y: b.y } : { x: b.x, y: b.y - b.h / 2 };
                const m = lr ? (q.x - p.x) / 2 : (q.y - p.y) / 2;
                c1 = lr ? { x: p.x + m, y: p.y } : { x: p.x, y: p.y + m };
                c2 = lr ? { x: q.x - m, y: q.y } : { x: q.x, y: q.y - m };
            }
        }
        if (!p) {
            p = boxEdge(a, b.x, b.y);
            q = boxEdge(b, a.x, a.y);
            const mx = (p.x + q.x) / 2;
            const my = (p.y + q.y) / 2;
            const bend = kind === "mind" ? 0.12 : 0.18;
            c1 = { x: mx - (q.y - p.y) * bend, y: my + (q.x - p.x) * bend };
            c2 = c1;
        }
        if (!sketch) return `M${p.x},${p.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${q.x},${q.y}`;
        const r = rng(seed);
        const j = () => (r() - 0.5) * 14 * unit;
        return `M${p.x + j()},${p.y + j()} C${c1.x + j() * 2},${c1.y + j() * 2} ${c2.x + j() * 2},${c2.y + j() * 2} ${q.x},${q.y}`;
    }

    /**
     * A flow edge that runs against the flow (a closing edge, last → first) and whose straight line would cross other
     * nodes goes round the stage instead: out of a's side, along the free side of the column (row on 16:9) and into
     * b's side. Returns {d, label: {x, y, transform}} with the label set outside the line, or null when the edge runs
     * with the flow or its straight line is clear. `bounds` is the room the line may use.
     */
    function aroundPath(a, b, nodes, direction, bounds, unit) {
        const lr = direction === "lr";
        const along = (n) => (lr ? n.x : n.y);
        if (along(b) >= along(a) - 1) return null;
        const p0 = boxEdge(a, b.x, b.y);
        const q0 = boxEdge(b, a.x, a.y);
        if (!nodes.some((n) => n !== a && n !== b && segmentHits(p0, q0, n))) return null;
        const lo = Math.min(along(a), along(b)) - 1;
        const hi = Math.max(along(a), along(b)) + 1;
        const span = nodes.filter((n) => along(n) >= lo && along(n) <= hi);
        // Cross axis: x in a top-down column, y in a left-right row.
        const c = (n) => (lr ? n.y : n.x);
        const half = (n) => (lr ? n.h : n.w) / 2;
        const far = Math.max(...span.map((n) => c(n) + half(n)));
        const near = Math.min(...span.map((n) => c(n) - half(n)));
        const [b0, b1] = lr ? [bounds.y, bounds.y + bounds.h] : [bounds.x, bounds.x + bounds.w];
        const after = b1 - far >= near - b0;
        const room = after ? b1 - far : near - b0;
        const off = Math.max(18 * unit, Math.min(56 * unit, room * 0.45));
        const line = after ? far + off : near - off;
        const s = after ? 1 : -1;
        const ends = [a, b].map((n) => c(n) + s * half(n));
        const r = Math.min(34 * unit, Math.abs(along(a) - along(b)) / 2, off);
        const pt = (u, v) => (lr ? `${v},${u}` : `${u},${v}`);
        const dirAlong = along(b) < along(a) ? -1 : 1;
        const d = `M${pt(ends[0], along(a))} L${pt(line - s * r, along(a))} Q${pt(line, along(a))} ${pt(line, along(a) + dirAlong * r)}`
            + ` L${pt(line, along(b) - dirAlong * r)} Q${pt(line, along(b))} ${pt(line - s * r, along(b))} L${pt(ends[1], along(b))}`;
        const mid = (along(a) + along(b)) / 2;
        const gap = 12 * unit;
        const label = lr
            ? { x: mid, y: line + s * gap, transform: after ? "translate(-50%, 0)" : "translate(-50%, -100%)" }
            : { x: line + s * gap, y: mid, transform: after ? "translate(0, -50%)" : "translate(-100%, -50%)" };
        return { d, label, side: lr ? (after ? "below" : "above") : after ? "right" : "left" };
    }

    /** A rounded-rectangle outline (wobbly when sketched) for draw-on. */
    function boxPath(n, sketch, seed, unit) {
        const x0 = n.x - n.w / 2;
        const y0 = n.y - n.h / 2;
        const x1 = n.x + n.w / 2;
        const y1 = n.y + n.h / 2;
        const r = Math.min(26 * unit, n.h / 3);
        if (!sketch) {
            return `M${x0 + r},${y0} H${x1 - r} Q${x1},${y0} ${x1},${y0 + r} V${y1 - r} Q${x1},${y1} ${x1 - r},${y1} H${x0 + r} Q${x0},${y1} ${x0},${y1 - r} V${y0 + r} Q${x0},${y0} ${x0 + r},${y0} Z`;
        }
        const g = rng(seed);
        const j = () => (g() - 0.5) * 8 * unit;
        return `M${x0 + r + j()},${y0 + j()} L${x1 - r + j()},${y0 + j()} Q${x1 + j()},${y0 + j()} ${x1 + j()},${y0 + r} L${x1 + j()},${y1 - r + j()} Q${x1},${y1} ${x1 - r + j()},${y1 + j()} L${x0 + r + j()},${y1 + j()} Q${x0},${y1} ${x0 + j()},${y1 - r} L${x0 + j()},${y0 + r + j()} Q${x0},${y0} ${x0 + r + 10 * unit},${y0 + j()}`;
    }

    const SVG = "http://www.w3.org/2000/svg";

    function el(tag, attrs, parent, ns) {
        const node = ns ? document.createElementNS(SVG, tag) : document.createElement(tag);
        for (const [k, v] of Object.entries(attrs ?? {})) {
            if (k === "text") node.textContent = v;
            else if (k === "style") node.style.cssText = v;
            else node.setAttribute(k, v);
        }
        if (parent) parent.appendChild(node);
        return node;
    }

    const CSS = `
.dg-layer { position: absolute; left: 0; top: 0; overflow: visible; pointer-events: none; }
.dg-title { position: absolute; left: 0; right: 0; text-align: center; font-family: var(--heading-font), Arial, sans-serif;
  font-weight: 800; color: var(--text); letter-spacing: -0.01em; line-height: 1.15; }
.dg-title span { display: inline-block; padding: .16em .55em .2em; border-radius: .35em; box-shadow: 0 .2em .6em rgba(0,0,0,.28);
  background: color-mix(in srgb, var(--background) 90%, transparent); }
.dg-node { position: absolute; display: flex; align-items: center; justify-content: center; gap: .35em; opacity: 0; }
.dg-plate { position: absolute; inset: 0; border-radius: var(--dg-radius); background: color-mix(in srgb, var(--background) 92%, transparent);
  box-shadow: 0 14px 34px rgba(0,0,0,.35); border: var(--dg-border) solid color-mix(in srgb, var(--text) 22%, transparent); }
.dg-sketch .dg-plate { border-color: transparent; box-shadow: none; }
.dg-ring { position: absolute; inset: calc(var(--dg-border) * -1.5); border-radius: var(--dg-radius); border: calc(var(--dg-border) * 2.2) solid var(--accent);
  box-shadow: 0 0 calc(var(--dg-border) * 9) color-mix(in srgb, var(--accent) 55%, transparent); opacity: 0; }
.dg-label { position: relative; font-family: var(--heading-font), Arial, sans-serif; font-weight: 700; color: var(--text); line-height: 1.18;
  text-align: center; white-space: nowrap; }
.dg-icon { position: relative; line-height: 1; }
.dg-icon img { width: 1.25em; height: 1.25em; object-fit: contain; display: block; }
.dg-edge-label { position: absolute; transform: translate(-50%, -50%); padding: .18em .6em; border-radius: 99px; white-space: nowrap;
  background: color-mix(in srgb, var(--background) 88%, transparent); color: var(--text); font-family: var(--body-font), Arial, sans-serif;
  font-weight: 600; opacity: 0; }
.dg-packet { position: absolute; left: 0; top: 0; border-radius: 50%; background: var(--accent); opacity: 0;
  box-shadow: 0 0 calc(var(--dg-border) * 5) var(--accent); offset-rotate: 0deg; }
.dg-note { position: absolute; padding: .7em .9em; background: #FFE27A; color: #2B2412; border-radius: .2em; font-family: var(--hand-font);
  font-weight: 700; line-height: 1.2; box-shadow: 0 .4em 1em rgba(0,0,0,.28); opacity: 0; text-align: left; }
.dg-sketch .dg-label, .dg-sketch .dg-title { font-family: var(--hand-font); }
`;

    function brand(v) {
        const look = document.documentElement.style;
        for (const key of ["accent", "background", "text", "secondary"]) if (v[key]) look.setProperty(`--${key}`, v[key]);
        const faces = document.createElement("style");
        for (const [key, slot] of [["headingFont", "--heading-font"], ["bodyFont", "--body-font"]]) {
            const name = String(v[key] ?? "").trim();
            if (!name) continue;
            const file = /\.(ttf|otf|woff2?)$/i.test(name);
            if (!name.startsWith("System-")) faces.textContent += `@font-face { font-family: "${key}"; src: ${file ? `url("${name}")` : `local("${name}")`}; }`;
            const family = name.startsWith("System-") ? "system-ui" : file ? `"${key}"` : `"${name}", "${key}"`;
            look.setProperty(slot, family);
            if (key === "headingFont") look.setProperty("--hand-font", family);
        }
        faces.textContent += CSS;
        document.head.appendChild(faces);
    }

    /** The canvas the page renders at: the root's data-width/height (bin/graphic sets them to the project's). */
    function canvas(rootEl) {
        const W = Number(rootEl.dataset.width) || 1080;
        const H = Number(rootEl.dataset.height) || 1920;
        for (const node of [document.documentElement, document.body, rootEl]) {
            node.style.width = `${W}px`;
            node.style.height = `${H}px`;
        }
        return { W, H, unit: Math.min(W, H) / 1080, END: Number(rootEl.dataset.duration) || 6 };
    }

    /**
     * The area a diagram fills: inside 72 px, under the title, above a phone's caption band on 9:16 and, on 16:9,
     * above the band the step notes take.
     */
    function areaFor(W, H, unit, hasTitle, hasNotes = false, bottomSafe = 0) {
        const portrait = H > W;
        const top = (hasTitle ? (portrait ? 0.15 : 0.17) : portrait ? 0.1 : 0.08) * H;
        const asked = safeLine(bottomSafe, H);
        const bottom = asked !== null ? Math.max(asked, top + 0.2 * H) : (portrait ? 0.76 : hasNotes ? 0.8 : 0.92) * H;
        return { x: 72 * unit, y: top, w: W - 144 * unit, h: bottom - top };
    }

    /** bottomSafe as a y in px: a share of the height (0–1) or px from the top (> 1); null when not given. */
    function safeLine(value, H) {
        const n = Number(value);
        if (!Number.isFinite(n) || n <= 0) return null;
        return Math.min(H, n <= 1 ? n * H : n);
    }

    /** The growing diagram's default bottomSafe: a 9:16 caption pill sits at ~66–73 % of the height. */
    const GROWING_BOTTOM_SAFE = 0.62;

    /** Steps with times: a number is kept; the others are spread evenly from 0.9 s to the end. */
    function timedSteps(steps, END) {
        const list = (steps ?? []).map((s) => ({ ...s, focus: [].concat(s.focus ?? []).map(String), reveal: [].concat(s.reveal ?? []).map(String) }));
        const free = list.filter((s) => typeof s.at !== "number" || !Number.isFinite(s.at));
        const span = Math.max(0.5, END - 1.6);
        free.forEach((s, i) => (s.at = Math.round((0.9 + (span * i) / Math.max(1, free.length)) * 100) / 100));
        return list.sort((a, b) => a.at - b.at);
    }

    /**
     * Steps from the nodes' and edges' own `at` (a growing diagram): at each time the nodes that come in are revealed
     * and lit, and the edges that come in ("from>to" in `edges`) glow and carry dots with their ends lit.
     */
    function growthSteps(data) {
        const slots = new Map();
        const slot = (at) => {
            const key = Math.round(at * 100) / 100;
            if (!slots.has(key)) slots.set(key, { at: key, focus: [], reveal: [], edges: [], grown: true });
            return slots.get(key);
        };
        const ids = new Set((data.nodes ?? []).map((n, i) => String(n.id ?? `n${i}`)));
        (data.nodes ?? []).forEach((n, i) => {
            if (typeof n.at !== "number" || !Number.isFinite(n.at)) return;
            const s = slot(Math.max(0, n.at));
            const id = String(n.id ?? `n${i}`);
            s.reveal.push(id);
            s.focus.push(id);
        });
        for (const e of data.edges ?? []) {
            if (typeof e.at !== "number" || !Number.isFinite(e.at) || !ids.has(String(e.from)) || !ids.has(String(e.to))) continue;
            const s = slot(Math.max(0, e.at));
            s.edges.push(`${e.from}>${e.to}`);
            for (const id of [String(e.from), String(e.to)]) if (!s.focus.includes(id)) s.focus.push(id);
        }
        return [...slots.values()].sort((a, b) => a.at - b.at);
    }

    /**
     * The recap (growing-diagram `recap`, scene seconds): from then to the end every node is lit and every edge glows
     * and runs dots, the edges starting one after another (stagger, s) so the whole chain reads in order. null when
     * recap is not a time inside the scene.
     */
    function recapStep(value, layout, END) {
        const at = Number(value);
        if (!Number.isFinite(at) || at <= 0 || at >= END - 0.8 || !layout.nodes.length) return null;
        const edges = layout.edges.map((e) => `${e.from}>${e.to}`);
        const stagger = edges.length > 1 ? Math.max(0.1, Math.min(0.45, (END - 0.5 - at - 1) / edges.length)) : 0;
        return { at: Math.round(at * 100) / 100, focus: layout.nodes.map((n) => n.id), reveal: [], edges, grown: true, recap: true, stagger };
    }

    /** The scene slice a render shows: t0 (scene seconds at its first frame) and the scene's END (total or t0 + END). */
    function sceneSlice(v, data, END) {
        const t0 = Math.max(0, Number(data.t0 ?? v.t0) || 0);
        const total = Number(data.total ?? v.total) || 0;
        return { t0, sceneEnd: total > 0 ? Math.max(total, t0 + 0.1) : t0 + END };
    }

    function fromToState(tl, target, a, b, at, duration, ease) {
        tl.fromTo(target, a, { ...b, duration, ease: ease ?? "power2.out", immediateRender: false }, at);
    }

    /**
     * Builds a flow, mind or iso diagram on `rootEl` into the paused timeline `tl`. v: the page's variables (data JSON,
     * title, sketch, packets, brand ids).
     */
    function mount(tl, v, kind, rootEl, options = {}) {
        brand(v);
        const { W, H, unit, END: span } = canvas(rootEl);
        const data = typeof v.data === "string" ? JSON.parse(v.data || "{}") : v.data ?? {};
        // A slice of a longer scene: build the whole scene on its own timeline and play t0 … t0 + span of it.
        const { t0: sliceAt, sceneEnd } = sceneSlice(v, data, span);
        const END = sceneEnd;
        const outer = tl;
        if (sliceAt > 0 || Math.abs(sceneEnd - span) > 1e-3) tl = root.gsap.timeline({ paused: true });
        const grown = growthSteps(data);
        const growing = grown.length > 0;
        const fonts = options.fonts ?? {};
        const dimTo = Number.isFinite(data.dim) ? data.dim : growing ? 0.7 : 0.42;
        const title = data.title ?? v.title ?? "";
        const sketch = Boolean(data.sketch ?? v.sketch);
        if (sketch) rootEl.classList.add("dg-sketch");
        rootEl.style.setProperty("--dg-border", `${3 * unit}px`);
        rootEl.style.setProperty("--dg-radius", `${(kind === "mind" ? 40 : 22) * unit}px`);
        // bottomSafe (growing-diagram): nothing below this line (captions); 0 / unset there is 0.62 of the height on 9:16.
        const knob = (key) => data[key] ?? v[key];
        let bottomSafe = Number(knob("bottomSafe")) || 0;
        if (!(bottomSafe > 0) && v.bottomSafe !== undefined && H > W) bottomSafe = GROWING_BOTTOM_SAFE;
        const area = areaFor(W, H, unit, Boolean(title), (data.steps ?? []).some((s) => s.note), bottomSafe);
        const layout = kind === "flow" ? flowLayout(data, area, unit, data.direction, fonts) : kind === "mind" ? mindLayout(data, area, unit) : isoLayout(data, area, unit, fonts);
        const byId = new Map(layout.nodes.map((n) => [n.id, n]));
        if (kind === "iso") layout.nodes.forEach((n) => (n.cell = layout.cell));
        const recap = recapStep(knob("recap"), layout, END);
        if (recap) grown.push(recap);
        const steps = timedSteps(data.steps, END).concat(grown).sort((a, b) => a.at - b.at);
        const groups = [...new Set(layout.nodes.map((n) => n.group).filter(Boolean))];
        const tint = (n) => {
            const g = groups.indexOf(n.group);
            return g < 0 ? null : ["var(--secondary)", "#4FC3F7", "#81C784", "#BA68C8", "#FF8A65"][g % 6];
        };

        if (title) {
            // On a plate in the node colour: the title stays readable over any footage, dark text or light.
            const t = el("div", { class: "dg-title" }, rootEl);
            el("span", { text: title }, t);
            const portrait = H > W;
            t.style.top = `${(portrait ? 0.075 : 0.05) * H}px`;
            t.style.fontSize = `${Math.min(64 * unit, ((W - 160 * unit) / Math.max(1, textWidth(title, 1) + 1.1)))}px`;
            t.style.padding = `0 ${72 * unit}px`;
            fromToState(tl, t, { opacity: 0, y: 30 * unit }, { opacity: 1, y: 0 }, 0.15, 0.5, "power3.out");
        }

        // Edges under the nodes (iso blocks hide the ends of their connectors).
        const svg = el("svg", { class: "dg-layer", width: W, height: H, viewBox: `0 0 ${W} ${H}` }, rootEl, true);
        const edgeLayer = el("g", {}, svg, true);
        const blockLayer = el("g", {}, svg, true);
        const htmlLayer = el("div", { class: "dg-layer", style: `width:${W}px;height:${H}px` }, rootEl);
        const overSvg = el("svg", { class: "dg-layer", width: W, height: H, viewBox: `0 0 ${W} ${H}` }, rootEl, true);

        // When each node shows: from the start unless a step reveals it.
        const revealAt = new Map();
        for (const s of steps) for (const id of s.reveal) if (byId.has(id) && !revealAt.has(id)) revealAt.set(id, s.at);
        const initial = layout.nodes.filter((n) => !revealAt.has(n.id));
        const order = kind === "mind" ? initial.sort((a, b) => a.depth - b.depth) : initial.sort((a, b) => a.depth - b.depth || a.y - b.y);
        const stagger = Math.min(0.2, 1.3 / Math.max(1, order.length));
        order.forEach((n, i) => revealAt.set(n.id, 0.35 + i * stagger));
        const shown = (id) => revealAt.get(id);

        // Nodes.
        const parts = new Map();
        const sorted = [...layout.nodes].sort((a, b) => a.y - b.y);
        for (const n of sorted) {
            const box = el("div", { class: "dg-node" }, htmlLayer);
            if (kind === "iso") {
                // The block: top face and two sides, the label on a plate under it.
                const s = n.size;
                const cx = n.x;
                const cy = n.y + n.dy;
                const lift = n.lift * s * 2;
                const g = el("g", { opacity: 0 }, blockLayer, true);
                const fill = tint(n) ?? "var(--secondary)";
                el("polygon", { points: `${cx - s},${cy} ${cx},${cy + s / 2} ${cx},${cy + s / 2 + lift} ${cx - s},${cy + lift}`, style: `fill:${fill};filter:brightness(.72)` }, g, true);
                el("polygon", { points: `${cx + s},${cy} ${cx},${cy + s / 2} ${cx},${cy + s / 2 + lift} ${cx + s},${cy + lift}`, style: `fill:${fill};filter:brightness(.52)` }, g, true);
                const top = el("polygon", { points: `${cx - s},${cy} ${cx},${cy - s / 2} ${cx + s},${cy} ${cx},${cy + s / 2}`, style: `fill:${fill}` }, g, true);
                const glow = el("polygon", { points: top.getAttribute("points"), style: `fill:var(--accent);opacity:0;stroke:var(--accent);stroke-width:${6 * unit}` }, g, true);
                const shade = el("polygon", { points: `${cx - s},${cy} ${cx},${cy - s / 2} ${cx + s},${cy} ${cx + s},${cy + lift} ${cx},${cy + s / 2 + lift} ${cx - s},${cy + lift}`, style: "fill:#000;opacity:0" }, g, true);
                if (n.icon) {
                    const icon = el("text", { x: cx, y: cy + s * 0.18, "text-anchor": "middle", fill: "#FFFFFF", "data-layout-ignore": "", style: `font-size:${s * 0.55}px`, text: /\.(png|jpe?g|svg|webp)$/i.test(n.icon) ? "" : n.icon }, g, true);
                    if (/\.(png|jpe?g|svg|webp)$/i.test(n.icon)) {
                        icon.remove();
                        el("image", { "data-layout-ignore": "", href: n.icon, x: cx - s * 0.3, y: cy - s * 0.32, width: s * 0.6, height: s * 0.6 }, g, true);
                    }
                }
                box.style.cssText = `left:${n.x - n.labelW / 2}px;top:${n.y + n.h / 2 - n.labelH}px;width:${n.labelW}px;height:${n.labelH}px;`;
                parts.set(n.id, { box, block: g, glow, shade, ring: null });
            } else {
                box.style.cssText = `left:${n.x - n.w / 2}px;top:${n.y - n.h / 2}px;width:${n.w}px;height:${n.h}px;`;
                const plate = el("div", { class: "dg-plate" }, box);
                const color = tint(n);
                if (color) plate.style.borderColor = color;
                const ring = el("div", { class: "dg-ring" }, box);
                if (sketch) {
                    const outline = el("path", { d: boxPath(n, true, n.id, unit), fill: "none", stroke: color ?? "var(--text)", "stroke-width": 4 * unit, "stroke-linecap": "round", opacity: 0 }, overSvg, true);
                    parts.set(n.id, { box, ring, outline });
                } else parts.set(n.id, { box, ring });
            }
            if (n.icon && kind !== "iso") {
                const icon = el("div", { class: "dg-icon", style: `font-size:${n.font * 1.15}px` }, box);
                if (/\.(png|jpe?g|svg|webp)$/i.test(n.icon)) el("img", { src: n.icon }, icon);
                else icon.textContent = n.icon;
            }
            if (kind === "iso") el("div", { class: "dg-plate" }, box);
            const label = el("div", { class: "dg-label", style: `font-size:${n.font}px` }, box);
            n.lines.forEach((line, i) => {
                if (i) el("br", {}, label);
                label.appendChild(document.createTextNode(line));
            });
            // The estimate is close but not exact: shrink a label that still overflows its box.
            const room = (kind === "iso" ? n.labelW : n.w) - 16 * unit - (n.icon && kind !== "iso" ? n.font * 1.4 : 0);
            if (label.scrollWidth > room) label.style.fontSize = `${n.font * (room / label.scrollWidth)}px`;
        }

        // Ghosts (growing-diagram `ghosts`): every block that comes in later shows faint and dashed from the start, so
        // the hook already shows the whole system's shape; each ghost fades as its own block pops in.
        if (growing && knob("ghosts") === true) {
            sorted.forEach((n, i) => {
                const t0 = shown(n.id);
                if (!(t0 > 0.6)) return;
                const p = parts.get(n.id);
                const ghost = p.box.cloneNode(true);
                ghost.classList.add("dg-ghost");
                ghost.style.opacity = "0";
                for (const plate of ghost.querySelectorAll(".dg-plate")) {
                    plate.style.cssText += "border-style:dashed;border-color:color-mix(in srgb, var(--text) 70%, transparent);box-shadow:none;background:transparent;";
                }
                htmlLayer.insertBefore(ghost, p.box);
                const fade = [ghost];
                if (p.block) {
                    // An iso block: its outline only, dashed.
                    const shape = p.block.cloneNode(true);
                    shape.setAttribute("opacity", "0");
                    for (const poly of [...shape.children]) {
                        if (poly.tagName.toLowerCase() !== "polygon") continue;
                        if (poly.style.opacity === "0") poly.remove();
                        else poly.style.cssText = `fill:none;stroke:var(--text);stroke-width:${3 * unit}px;stroke-dasharray:${12 * unit} ${9 * unit};filter:none`;
                    }
                    blockLayer.insertBefore(shape, p.block);
                    fade.push(shape);
                }
                for (const g of fade) {
                    fromToState(tl, g, { opacity: 0 }, { opacity: 0.32 }, 0.3 + Math.min(i * 0.12, 1.2), 0.5);
                    fromToState(tl, g, { opacity: 0.32 }, { opacity: 0 }, Math.max(0.9, t0 - 0.3), 0.25, "power1.in");
                }
            });
        }

        // Edges: drawn on once both ends are there.
        const edgeParts = [];
        const taken = [];
        const stage = { x: 24 * unit, y: area.y - 40 * unit, w: W - 48 * unit, h: area.h + 40 * unit };
        layout.edges.forEach((e, i) => {
            const a = byId.get(e.from);
            const b = byId.get(e.to);
            const around = kind === "flow" ? aroundPath(a, b, layout.nodes, layout.direction, stage, unit) : null;
            const d = kind === "iso" ? isoPath(a, b, layout.nodes, { x: 60 * unit, y: 60 * unit, w: W - 120 * unit, h: H - 120 * unit }, taken)
                : around ? around.d : edgePath(a, b, kind, layout.direction, sketch, `${e.from}>${e.to}`, unit, e.via);
            const base = el("path", { d, fill: "none", stroke: "var(--text)", "stroke-opacity": kind === "iso" ? 0.55 : 0.7, "stroke-width": (kind === "iso" ? 7 : 5) * unit, "stroke-linecap": "round", "stroke-linejoin": "round" }, edgeLayer, true);
            const hot = el("path", { d, fill: "none", stroke: "var(--accent)", "stroke-width": (kind === "iso" ? 9 : 7) * unit, "stroke-linecap": "round", "stroke-linejoin": "round", opacity: 0 }, edgeLayer, true);
            const length = base.getTotalLength();
            base.style.strokeDasharray = `${length}`;
            base.style.strokeDashoffset = `${length}`;
            const own = typeof e.at === "number" && Number.isFinite(e.at) ? e.at : 0;
            const at = Math.max(own, Math.max(shown(e.from), shown(e.to)) + 0.2);
            fromToState(tl, base, { strokeDashoffset: length }, { strokeDashoffset: 0 }, at, 0.5, "power2.inOut");
            let arrow = null;
            if (kind !== "mind") {
                const end = base.getPointAtLength(length);
                const before = base.getPointAtLength(Math.max(0, length - 12 * unit));
                const angle = (Math.atan2(end.y - before.y, end.x - before.x) * 180) / Math.PI;
                const sz = 16 * unit;
                arrow = el("polygon", { points: `0,0 ${-sz * 1.6},${-sz} ${-sz * 1.6},${sz}`, fill: "var(--text)", "fill-opacity": 0.8, transform: `translate(${end.x},${end.y}) rotate(${angle})`, opacity: 0 }, edgeLayer, true);
                fromToState(tl, arrow, { opacity: 0 }, { opacity: 1 }, at + 0.42, 0.15);
            }
            let tag = null;
            if (e.label) {
                const size = (growing ? 34 : 24) * unit;
                let { x, y } = base.getPointAtLength(length / 2);
                let transform = "";
                if (around) {
                    // Beside the line, away from the nodes, when it fits the frame; on the line otherwise.
                    const w = textWidth(e.label, size) + size * 1.2;
                    const h = size * 1.6;
                    const fits = around.side === "right" ? around.label.x + w <= W - 8 * unit : around.side === "left" ? around.label.x - w >= 8 * unit
                        : around.side === "below" ? around.label.y + h <= H - 8 * unit : around.label.y - h >= 8 * unit;
                    if (fits) ({ x, y, transform } = around.label);
                }
                tag = el("div", { class: "dg-edge-label", text: e.label, style: `left:${x}px;top:${y}px;font-size:${size}px;${transform ? `transform:${transform};` : ""}` }, htmlLayer);
                fromToState(tl, tag, { opacity: 0, scale: 0.8 }, { opacity: 1, scale: 1 }, at + 0.35, 0.3);
            }
            edgeParts.push({ e, key: `${e.from}>${e.to}`, base, hot, length, d, at, arrow, tag });
        });

        // Node motion: hidden → shown, then focused or dimmed per step.
        const HIDDEN = { opacity: 0, scale: 0.7, ring: 0, dim: 0 };
        const stateAt = (id, step) => {
            if (!step || !step.focus.length) return { opacity: 1, scale: 1, ring: 0, dim: 0 };
            return step.focus.includes(id) ? { opacity: 1, scale: 1.07, ring: 1, dim: 0 } : { opacity: dimTo, scale: 1, ring: 0, dim: 1 };
        };
        const apply = (id, from, to, at, duration, ease) => {
            const p = parts.get(id);
            if (kind === "iso") {
                // A dimmed block stays solid (connectors would show through) and is shaded instead.
                const solid = (st) => (st.opacity > 0 ? 1 : 0);
                fromToState(tl, p.block, { opacity: solid(from), y: from.opacity ? 0 : -40 * unit }, { opacity: solid(to), y: 0 }, at, duration, ease);
                fromToState(tl, p.box, { opacity: from.opacity, scale: from.scale }, { opacity: to.opacity, scale: to.scale }, at, duration, ease);
                fromToState(tl, p.glow, { opacity: from.ring * 0.45 }, { opacity: to.ring * 0.45 }, at, duration);
                fromToState(tl, p.shade, { opacity: from.dim * 0.5 }, { opacity: to.dim * 0.5 }, at, duration);
            } else {
                fromToState(tl, p.box, { opacity: from.opacity, scale: from.scale }, { opacity: to.opacity, scale: to.scale }, at, duration, ease);
                fromToState(tl, p.ring, { opacity: from.ring }, { opacity: to.ring }, at, duration);
                if (p.outline) fromToState(tl, p.outline, { opacity: from.opacity * 0.9 }, { opacity: to.opacity * 0.9 }, at, duration);
            }
        };
        for (const n of layout.nodes) {
            const t0 = shown(n.id);
            const active = [...steps].reverse().find((s) => s.at <= t0 + 1e-6);
            let state = stateAt(n.id, active);
            apply(n.id, HIDDEN, state, Math.max(0.05, t0 - 0.25), 0.45, "back.out(1.6)");
            const p = parts.get(n.id);
            if (p.outline) {
                const len = p.outline.getTotalLength();
                p.outline.style.strokeDasharray = `${len}`;
                p.outline.style.strokeDashoffset = `${len}`;
                fromToState(tl, p.outline, { strokeDashoffset: len }, { strokeDashoffset: 0 }, Math.max(0.05, t0 - 0.25), 0.6, "power1.inOut");
            }
            for (const s of steps) {
                if (s.at <= t0 + 1e-6) continue;
                const next = stateAt(n.id, s);
                if (JSON.stringify(next) === JSON.stringify(state)) continue;
                apply(n.id, state, next, s.at - 0.15, 0.35);
                state = next;
            }
        }

        // Focused edges glow and carry packets until the next step.
        const packets = data.packets ?? v.packets ?? kind === "iso";
        steps.forEach((s, k) => {
            if (!s.focus.length) return;
            const until = Math.min(k + 1 < steps.length ? steps[k + 1].at : END - 0.5, END - 0.5);
            for (const part of edgeParts) {
                const { e } = part;
                // A grown step lights its own new edges and those of the nodes it brings in; any other step every edge
                // touching a lit node.
                const lit = s.grown ? s.edges.includes(part.key) || s.reveal.includes(e.from) || s.reveal.includes(e.to)
                    : s.focus.includes(e.from) || s.focus.includes(e.to);
                if (!lit) continue;
                // The recap starts its edges one after another, in the data's order.
                const from = Math.max(s.at, part.at + 0.5) + (s.stagger ? s.edges.indexOf(part.key) * s.stagger : 0);
                if (until - from < 0.3) continue;
                fromToState(tl, part.hot, { opacity: 0 }, { opacity: 0.9 }, from - 0.1, 0.25);
                fromToState(tl, part.hot, { opacity: 0.9 }, { opacity: 0 }, until - 0.15, 0.25);
                if (!packets) continue;
                const per = Math.min(1.6, Math.max(0.7, part.length / (520 * unit)));
                const runs = Math.max(1, Math.floor((until - from) / per));
                for (const lag of [0, per / 2]) {
                    if (lag && runs < 2) continue;
                    const dot = el("div", { class: "dg-packet", style: `width:${16 * unit}px;height:${16 * unit}px;margin:${-8 * unit}px 0 0 ${-8 * unit}px;offset-path:path('${part.d}')` }, htmlLayer);
                    tl.fromTo(dot, { opacity: 0 }, { opacity: 1, duration: 0.12, immediateRender: false }, from + lag);
                    tl.fromTo(dot, { offsetDistance: "0%" }, { offsetDistance: "100%", duration: per, ease: "none", repeat: runs - (lag ? 2 : 1), immediateRender: false }, from + lag);
                    tl.fromTo(dot, { opacity: 1 }, { opacity: 0, duration: 0.12, immediateRender: false }, from + lag + per * (runs - (lag ? 1 : 0)) - 0.12);
                }
            }
        });

        // Step notes: a sticky note low in the frame while its step lasts.
        steps.forEach((s, k) => {
            if (!s.note) return;
            const until = k + 1 < steps.length ? steps[k + 1].at : END - 0.5;
            const note = el("div", { class: "dg-note", text: s.note }, rootEl);
            note.style.cssText += `font-size:${34 * unit}px;max-width:${W * 0.7}px;left:${W * 0.15}px;top:${area.y + area.h + 24 * unit}px;transform:rotate(-2deg)`;
            fromToState(tl, note, { opacity: 0, y: 30 * unit }, { opacity: 1, y: 0 }, s.at + 0.2, 0.35, "back.out(1.7)");
            fromToState(tl, note, { opacity: 1 }, { opacity: 0 }, until - 0.25, 0.25);
        });

        // Exit on the composition's end (bin/graphic plan stretches data-duration to the span).
        tl.fromTo([svg, htmlLayer, overSvg], { opacity: 1, y: 0 }, { opacity: 0, y: -40 * unit, duration: 0.45, ease: "power2.in", immediateRender: false }, END - 0.5);
        const heading = rootEl.querySelector(".dg-title");
        if (heading) tl.fromTo(heading, { opacity: 1 }, { opacity: 0, duration: 0.4, immediateRender: false }, END - 0.5);
        if (tl !== outer) {
            // The slice: scene time t0 at this render's first frame, one scene second per second.
            tl.duration();
            outer.fromTo(tl, { time: sliceAt }, { time: Math.min(sliceAt + span, END), duration: span, ease: "none", immediateRender: false }, 0);
        }
        return layout;
    }

    // MARK: big canvas

    /** The camera frame (centre and scale) that shows box b in the screen band y0…y1 (px) of a W × H screen. */
    function frameBox(b, W, H, maxScale, y0 = 0, y1 = H) {
        const s = Math.min((W * 0.9) / b.w, ((y1 - y0) * 0.9) / b.h, maxScale);
        return { cx: b.x + b.w / 2, cy: b.y + b.h / 2 - ((y0 + y1) / 2 - H / 2) / s, s };
    }

    function unionBox(nodes, pad) {
        const x0 = Math.min(...nodes.map((n) => n.x - n.w / 2)) - pad;
        const y0 = Math.min(...nodes.map((n) => n.y - n.h / 2)) - pad;
        const x1 = Math.max(...nodes.map((n) => n.x + n.w / 2)) + pad;
        const y1 = Math.max(...nodes.map((n) => n.y + n.h / 2)) + pad;
        return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    }

    /** The big-canvas world: the mind-map laid out on a canvas `size` screens wide and tall. */
    function canvasLayout(data, W, H, size) {
        const k = Math.max(1.2, Math.min(4, Number(size) || 2));
        // A tall canvas is kept squarer than the screen: a radial map uses the width, and the end view (width-bound
        // on 9:16) keeps its labels larger. A wide one leaves out the summary band, so the end view fills the width.
        const worldW = W * k;
        const worldH = W >= H ? H * k * (data.summary ? 0.8 : 1) : Math.min(H * k, W * k * 1.25);
        // Labels are laid out 1.25× (16:9: 1.35×) larger than a screen's: the end view shows the whole map at ~1/k and they stay
        // ≥ ~40 px at 1080 wide.
        const unit = (Math.min(worldW, worldH) / 1080) * (W >= H ? 1.35 : 1.25);
        const area = { x: 80 * unit, y: 80 * unit, w: worldW - 160 * unit, h: worldH - 160 * unit };
        const layout = mindLayout(data, area, unit);
        return { ...layout, worldW, worldH, k, unit };
    }

    /**
     * One canvas several screens big, drawn on branch by branch while the camera flies to each stop, ending on the
     * whole map. v: data (mind-map data + stops, summary), size, pen, brand ids.
     */
    function mountCanvas(tl, v, rootEl) {
        brand(v);
        const { W, H, unit: screenUnit, END } = canvas(rootEl);
        const data = typeof v.data === "string" ? JSON.parse(v.data || "{}") : v.data ?? {};
        rootEl.classList.add("dg-sketch");
        const world = canvasLayout(data, W, H, data.size ?? v.size);
        const { worldW, worldH, unit } = world;
        rootEl.style.setProperty("--dg-border", `${3 * unit}px`);
        rootEl.style.setProperty("--dg-radius", `${36 * unit}px`);
        const byId = new Map(world.nodes.map((n) => [n.id, n]));
        const t = world.tree;
        const view = el("div", { class: "dg-layer", style: `width:${worldW}px;height:${worldH}px;transform-origin:0 0;` }, rootEl);
        view.setAttribute("data-layout-allow-overflow", "");
        // The paper runs a canvas past each edge: the camera frames a branch near the edge without showing its end.
        const paper = el("div", { class: "dg-layer", style: `left:${-worldW}px;top:${-worldH}px;width:${worldW * 3}px;height:${worldH * 3}px;background-color:var(--background);background-image:radial-gradient(color-mix(in srgb, var(--text) 16%, transparent) ${2.2 * unit}px, transparent ${2.6 * unit}px);background-size:${44 * unit}px ${44 * unit}px;` }, view);
        paper.setAttribute("data-layout-allow-overflow", "");
        const under = el("div", { class: "dg-layer", style: `width:${worldW}px;height:${worldH}px` }, view);
        const svg = el("svg", { class: "dg-layer", width: worldW, height: worldH, viewBox: `0 0 ${worldW} ${worldH}` }, view, true);
        const html = el("div", { class: "dg-layer", style: `width:${worldW}px;height:${worldH}px` }, view);
        const ink = "var(--text)";

        // Stops: given, else the root and then each branch.
        let stops = (data.stops ?? []).map((s) => ({ ...s }));
        if (!stops.length) stops = [t.top, ...t.children.get(t.top)].map((id) => ({ focus: id }));
        const free = stops.filter((s) => typeof s.at !== "number");
        const outro = Math.min(3.2, Math.max(2, END * 0.12));
        free.forEach((s, i) => (s.at = 0.6 + ((END - outro - 1.2) * i) / Math.max(1, free.length)));
        stops.sort((a, b) => a.at - b.at);

        // What each stop draws: its node's subtree not drawn before (the root first).
        const drawn = new Set();
        const subtree = (id) => [id, ...t.children.get(id).flatMap((k) => subtree(k))];
        const plan = stops.map((s) => {
            const ids = typeof s.focus === "string" && byId.has(String(s.focus)) ? subtree(String(s.focus)).filter((id) => !drawn.has(id)) : [];
            const own = typeof s.focus === "string" && String(s.focus) === t.top ? [t.top] : ids;
            own.forEach((id) => drawn.add(id));
            return own;
        });
        if (!drawn.has(t.top) && plan.length) {
            plan[0].unshift(t.top);
            drawn.add(t.top);
        }
        const rest = world.nodes.map((n) => n.id).filter((id) => !drawn.has(id));

        // A pen follows each stroke while it is drawn (one per stroke, its path fixed: no state carried between seeks).
        const PEN = `<svg viewBox="0 0 70 70" width="100%" height="100%"><g transform="rotate(38 35 35)"><rect x="29" y="-14" width="12" height="62" rx="3" fill="var(--accent)"/><rect x="29" y="-14" width="12" height="10" rx="3" fill="#333"/><polygon points="29,48 41,48 35,66" fill="#E8C9A0"/><polygon points="33,59 37,59 35,66" fill="#222"/></g></svg>`;
        const usePen = (data.pen ?? v.pen) !== false;

        const strokes = (d, at, duration, width, color, parent = svg) => {
            const path = el("path", { d, fill: "none", stroke: color, "stroke-width": width, "stroke-linecap": "round", "stroke-linejoin": "round" }, parent, true);
            const len = path.getTotalLength();
            path.style.strokeDasharray = `${len}`;
            path.style.strokeDashoffset = `${len}`;
            fromToState(tl, path, { strokeDashoffset: len }, { strokeDashoffset: 0 }, at, duration, "power1.inOut");
            if (usePen && duration >= 0.2) {
                const pen = el("div", { class: "dg-layer", style: `width:${80 * unit}px;height:${80 * unit}px;margin:${-80 * unit * 0.93}px 0 0 ${-80 * unit * 0.36}px;offset-path:path('${d}');offset-rotate:0deg;offset-anchor:0 0;opacity:0` }, html);
                pen.innerHTML = PEN;
                fromToState(tl, pen, { opacity: 0 }, { opacity: 1 }, at, 0.01, "none");
                fromToState(tl, pen, { offsetDistance: "0%" }, { offsetDistance: "100%" }, at, duration, "power1.inOut");
                fromToState(tl, pen, { opacity: 1 }, { opacity: 0 }, at + duration, 0.01, "none");
            }
            return path;
        };

        const drawNode = (n, at, dur) => {
            const color = n.depth === 0 ? "var(--accent)" : ink;
            const parent = t.parent.get(n.id);
            let start = at;
            if (parent !== null && parent !== undefined) {
                const p = byId.get(parent);
                strokes(edgePath(p, n, "mind", null, true, `${parent}>${n.id}`, unit), start, dur * 0.4, 5 * unit, ink);
                start += dur * 0.4;
            }
            const plate = el("div", { class: "dg-plate", style: `left:${n.x - n.w / 2}px;top:${n.y - n.h / 2}px;width:${n.w}px;height:${n.h}px;position:absolute;opacity:0;background:color-mix(in srgb, var(--background) 70%, ${n.depth === 0 ? "var(--accent)" : "white"} 30%)` }, under);
            strokes(boxPath(n, true, n.id, unit), start, dur * 0.45, (n.depth === 0 ? 7 : 5) * unit, color);
            fromToState(tl, plate, { opacity: 0 }, { opacity: 1 }, start + dur * 0.2, dur * 0.3);
            const label = el("div", { class: "dg-label", "data-layout-allow-overflow": "", style: `position:absolute;left:${n.x - n.w / 2}px;top:${n.y - n.h / 2}px;width:${n.w}px;height:${n.h}px;display:flex;align-items:center;justify-content:center;font-size:${n.font}px;opacity:0` }, html);
            const inner = el("div", {}, label);
            n.lines.forEach((line, i) => {
                if (i) el("br", {}, inner);
                inner.appendChild(document.createTextNode(line));
            });
            if (inner.scrollWidth > n.w - 16 * unit) inner.style.fontSize = `${n.font * ((n.w - 16 * unit) / inner.scrollWidth)}px`;
            fromToState(tl, label, { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1 }, start + dur * 0.4, dur * 0.25);
            return start + dur * 0.45;
        };

        // Camera. A stop frames its branch (the root stop: the root and its children) centred in the part of the screen
        // its note leaves free; the note takes the band (top or bottom) that covers fewer labels already drawn. The end
        // view frames the whole map above the summary note. Zoom in at most 0.8 (labels ~1.5× their size at the end).
        rootEl.style.background = "var(--background)";
        const maxScale = 0.8;
        const portrait = H > W;
        const noteFont = 40 * screenUnit;
        const noteHeight = (text, font, maxW) => {
            const lines = wrap(text, maxW - font * 1.8, font, 6)?.length ?? 3;
            return lines * font * 1.2 + font * 1.4;
        };
        // Screen bands a note can take, and what is left for the picture: [noteTop, freeTop, freeBottom].
        const bands = (h) => ({
            bottom: [H * (portrait ? 0.7 : 0.94) - h, H * 0.05, H * (portrait ? 0.7 : 0.94) - h - 24 * screenUnit],
            top: [H * 0.06, H * 0.06 + h + 24 * screenUnit, H * (portrait ? 0.86 : 0.95)],
        });
        const toScreen = (f, n) => ({ x: W / 2 + (n.x - f.cx) * f.s, y: H / 2 + (n.y - f.cy) * f.s, w: n.w * f.s, h: n.h * f.s });
        const all = world.nodes;
        const summaryH = data.summary ? noteHeight(data.summary, 44 * screenUnit, W * 0.74) : 0;
        const summaryTop = H * (portrait ? 0.86 : 0.95) - summaryH;
        const overview = frameBox(unionBox(all, 40 * unit), W, H, maxScale, H * 0.04, data.summary ? summaryTop - 20 * screenUnit : H * 0.96);
        // A frame on box in the band y0…y1 that leaves no label cut at the screen's edge: the smallest pan (the branch
        // staying in the band) that takes every cut label in or pushes it out, zooming out step by step (never below the
        // end view) until one does.
        const frameWith = (box, y0, y1, others) => {
            const m = 12 * screenUnit;
            const cuts = (g) => others.filter((n) => {
                const r = toScreen(g, n);
                const inside = r.x - r.w / 2 >= 0 && r.x + r.w / 2 <= W && r.y - r.h / 2 >= 0 && r.y + r.h / 2 <= H;
                // Off by the margin: a sketched outline wobbles past its box.
                const outside = r.x + r.w / 2 <= -m || r.x - r.w / 2 >= W + m || r.y + r.h / 2 <= -m || r.y - r.h / 2 >= H + m;
                return !inside && !outside;
            });
            const settle = (b, scale) => {
                const f = frameBox(b, W, H, scale, y0, y1);
                const s0 = toScreen(f, { x: b.x + b.w / 2, y: b.y + b.h / 2, w: b.w, h: b.h });
                const lim = { l: s0.x - s0.w / 2 - m, r: W - m - (s0.x + s0.w / 2), u: s0.y - s0.h / 2 - y0, d: y1 - (s0.y + s0.h / 2) };
                // Screen moves of the content that put a label's edge on the screen's edge (in, with a margin, or out).
                const xs = [0];
                const ys = [0];
                for (const n of others) {
                    const r = toScreen(f, n);
                    xs.push(W - m - (r.x + r.w / 2), W + m - (r.x - r.w / 2) + 1, m - (r.x - r.w / 2), -m - (r.x + r.w / 2) - 1);
                    ys.push(H - m - (r.y + r.h / 2), H + m - (r.y - r.h / 2) + 1, m - (r.y - r.h / 2), -m - (r.y + r.h / 2) - 1);
                }
                let best = null;
                for (const dx of xs.filter((v) => v >= -lim.l && v <= lim.r)) {
                    for (const dy of ys.filter((v) => v >= -lim.u && v <= lim.d)) {
                        const g = { ...f, cx: f.cx - dx / f.s, cy: f.cy - dy / f.s };
                        const score = cuts(g).length * 1e6 + Math.hypot(dx, dy);
                        if (!best || score < best.score) best = { g, score };
                    }
                }
                return best;
            };
            let best = settle(box, maxScale);
            for (let scale = best.g.s * 0.92; best.score >= 1e6 && scale >= overview.s; scale *= 0.92) {
                const next = settle(box, scale);
                if (next.score < best.score) best = next;
            }
            return best.g;
        };
        const drawnBefore = new Set();
        const frames = stops.map((s, i) => {
            let box;
            let focusIds = [];
            if (Array.isArray(s.focus) && s.focus.length === 4) box = { x: s.focus[0] * worldW, y: s.focus[1] * worldH, w: s.focus[2] * worldW, h: s.focus[3] * worldH };
            else if (byId.has(String(s.focus))) {
                const id = String(s.focus);
                focusIds = id === t.top ? [id, ...t.children.get(id)] : subtree(id);
                // Only what is on the canvas by the end of the stop: the root stop draws the root alone.
                const there = focusIds.filter((k) => drawnBefore.has(k) || plan[i].includes(k));
                box = unionBox((there.length ? there : focusIds).map((k) => byId.get(k)), 50 * unit);
            } else box = unionBox(all, 40 * unit);
            // Labels on screen by the end of this stop that are not the branch.
            const visible = all.filter((n) => (drawnBefore.has(n.id) || plan[i].includes(n.id)) && !focusIds.includes(n.id));
            let f;
            if (!s.note) f = frameWith(box, H * 0.05, H * 0.95, visible);
            else {
                const h = noteHeight(s.note, noteFont, W * 0.72);
                const options = Object.entries(bands(h)).map(([side, [noteTop, y0, y1]]) => {
                    const g = frameWith(box, y0, y1, visible);
                    // They may sit under the note.
                    const hits = visible.map((n) => toScreen(g, n)).filter((r) => r.x + r.w / 2 > W * 0.12 && r.x - r.w / 2 < W * 0.88
                        && r.y + r.h / 2 > noteTop && r.y - r.h / 2 < noteTop + h).length;
                    return { side, noteTop, g, hits };
                });
                const pick = options.sort((a, b) => a.hits - b.hits || (a.side === "bottom" ? -1 : 1))[0];
                f = pick.g;
                s.noteTop = pick.noteTop;
            }
            if (typeof s.zoom === "number" && s.zoom > 0) f.s = overview.s * s.zoom;
            plan[i].forEach((id) => drawnBefore.add(id));
            return f;
        });
        const place = (f) => ({ x: W / 2 - f.cx * f.s, y: H / 2 - f.cy * f.s, scale: f.s });
        let current = frames[0] ?? overview;
        gsap.set(view, { ...place(current), transformOrigin: "0 0" });
        stops.forEach((s, i) => {
            if (i === 0) return;
            const pan = Math.min(1.1, Math.max(0.5, (s.at - stops[i - 1].at) * 0.35));
            fromToState(tl, view, place(current), place(frames[i]), Math.max(0, s.at - pan), pan, "power2.inOut");
            current = frames[i];
        });
        const outAt = END - outro;
        fromToState(tl, view, place(current), place(overview), outAt, Math.min(1.4, outro * 0.5), "power2.inOut");

        // Drawing: each stop draws its part from its start, finished before the camera leaves.
        stops.forEach((s, i) => {
            const next = i + 1 < stops.length ? stops[i + 1].at : outAt;
            const room = next - s.at - (i + 1 < stops.length ? Math.min(1.1, Math.max(0.5, (next - s.at) * 0.35)) : 0) - 0.15;
            const ids = plan[i];
            if (!ids.length) return;
            const each = Math.max(0.35, Math.min(1.6, room / ids.length));
            let at = s.at;
            for (const id of ids) at = drawNode(byId.get(id), at, each);
            if (s.note) {
                const note = el("div", { class: "dg-note", text: s.note }, rootEl);
                note.style.cssText += `font-size:${noteFont}px;max-width:${W * 0.72}px;left:${W * 0.14}px;top:${s.noteTop}px;transform:rotate(-2deg)`;
                fromToState(tl, note, { opacity: 0, y: 40 * screenUnit, rotation: -6 }, { opacity: 1, y: 0, rotation: -2 }, Math.min(at, next - 0.8), 0.4, "back.out(1.7)");
                fromToState(tl, note, { opacity: 1 }, { opacity: 0 }, next - 0.35, 0.3);
            }
        });
        let late = outAt;
        for (const id of rest) late = drawNode(byId.get(id), late, 0.5);
        if (data.summary) {
            const note = el("div", { class: "dg-note", text: data.summary }, rootEl);
            note.style.cssText += `font-size:${44 * screenUnit}px;max-width:${W * 0.74}px;left:${W * 0.13}px;top:${summaryTop}px;transform:rotate(-2deg)`;
            fromToState(tl, note, { opacity: 0, y: 40 * screenUnit }, { opacity: 1, y: 0 }, outAt + 0.9, 0.45, "back.out(1.7)");
            fromToState(tl, note, { opacity: 1 }, { opacity: 0 }, END - 0.5, 0.4);
        }
        tl.fromTo(view, { opacity: 1 }, { opacity: 0, duration: 0.45, ease: "power1.in", immediateRender: false }, END - 0.5);
        return world;
    }

    const api = { textWidth, wrap, fitLabel, layering, flowLayout, mindLayout, isoLayout, canvasLayout, overlapping, separate, timedSteps, growthSteps, recapStep, aroundPath, safeLine, sceneSlice, areaFor, mount, mountCanvas, GROWING_BOTTOM_SAFE };
    root.BCDiagram = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
