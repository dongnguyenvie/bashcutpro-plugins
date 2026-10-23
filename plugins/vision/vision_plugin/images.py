"""A cutout as PNG rows for `bashcut_images.write_png`.

An image is `bashcut_images.Image(width, height, pixels)` with `pixels` rows of (r, g, b) tuples, top row first; a
matte is rows of floats 0..1. The ONNX backend uses numpy and Pillow instead; the standard-library stills
(`bashcut_images`) are what the fake backend and the tests run on, and what writes the PNG frames when Pillow is
missing.
"""


def cutout_rows(image, matte, mode):
    """PNG rows: `rgba` (the picture with the matte as alpha) or `matte` (the matte as grey)."""
    out = []
    for pixels, alphas in zip(image.pixels, matte):
        if mode == "matte":
            out.append([int(round(a * 255)) for a in alphas])
        else:
            row = []
            for (r, g, b), a in zip(pixels, alphas):
                row += (r, g, b, int(round(a * 255)))
            out.append(row)
    return out
