// The brand look and JSON variables shared by diagram.js and amenity-map.js. `render` puts this file in front of
// each of them when it copies them next to the page (a page loads only diagram.js or amenity-map.js), and in Node
// they require it.
(function (root) {
    "use strict";

    /**
     * Fills --accent, --background, --text and --secondary and the fonts (--heading-font, --body-font) from the
     * page's variables (`render --brand` sets them from the project's brand), plus `css` in the same <style>. A font
     * is a family or PostScript name on this Mac, System-<Weight> (San Francisco) or a font file in the folder.
     * Returns the CSS families it set, by variable (headingFont, bodyFont).
     */
    function applyLook(v, doc, css = "") {
        const look = doc.documentElement.style;
        for (const key of ["accent", "background", "text", "secondary"]) if (v[key]) look.setProperty(`--${key}`, v[key]);
        const faces = doc.createElement("style");
        const families = {};
        for (const [key, slot] of [["headingFont", "--heading-font"], ["bodyFont", "--body-font"]]) {
            const name = String(v[key] ?? "").trim();
            if (!name) continue;
            const file = /\.(ttf|otf|woff2?)$/i.test(name);
            if (!name.startsWith("System-")) faces.textContent += `@font-face { font-family: "${key}"; src: ${file ? `url("${name}")` : `local("${name}")`}; }`;
            families[key] = name.startsWith("System-") ? "system-ui" : file ? `"${key}"` : `"${name}", "${key}"`;
            look.setProperty(slot, families[key]);
        }
        faces.textContent += css;
        doc.head.appendChild(faces);
        return families;
    }

    /**
     * A JSON variable: an object passes through, empty text is {}. Text that does not parse is `fallback` when one is
     * given, else an Error that names the variable and where it broke (the render fails with it).
     */
    function parseJSON(text, name = "data", fallback = undefined) {
        if (text !== null && typeof text === "object") return text;
        try {
            return JSON.parse(String(text ?? "").trim() || "{}");
        } catch (error) {
            if (fallback !== undefined) return fallback;
            throw new Error(`the ${name} variable is not valid JSON (${error.message})`);
        }
    }

    const api = { applyLook, parseJSON };
    root.BCBrand = api;
    if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
