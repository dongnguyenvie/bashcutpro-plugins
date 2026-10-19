"""Model backends: what looks at a still.

Each backend loads a still exported by the engine and answers:
- `matte(image, kind)`: a 0..1 matte of the person (`kind="person"`) or of the main object (`"object"`);
- `faces(image)`: face boxes `{x, y, w, h, score}` as shares of the picture;
- `smooth(previous, matte, weight)`: the matte blended with the previous frame's (steadier edges over time);
- `save(path, image, matte, mode)`: a PNG, `rgba` (the picture cut out) or `matte` (grey);
- `coverage(matte)`, `bbox(matte)`: how much is kept, and its box.

`OnnxBackend` runs the models listed in models.json with onnxruntime (U²-Net human segmentation for people,
IS-Net general use for objects, YuNet for faces). `FakeBackend` (VISION_FAKE=1) reads colours instead of running a
model: red pixels are a person, blue an object, yellow a face. Tests and CI use it; it needs no package.
"""

import os

from .images import cutout_rows, read_bmp, write_png
from .mcp import AppError

PERSON_MODEL = "u2net-human-seg"
OBJECT_MODEL = "isnet-general-use"
FACE_MODEL = "yunet-face-2023mar"
MODELS = [PERSON_MODEL, OBJECT_MODEL, FACE_MODEL]
FILES = {PERSON_MODEL: "u2net_human_seg.onnx", OBJECT_MODEL: "isnet-general-use.onnx",
         FACE_MODEL: "face_detection_yunet_2023mar.onnx"}
FACE_SCORE = 0.6
NMS_OVERLAP = 0.3


def iou(a, b):
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def nms(boxes, overlap=NMS_OVERLAP):
    kept = []
    for box in sorted(boxes, key=lambda b: -b["score"]):
        if all(iou(box, k) < overlap for k in kept):
            kept.append(box)
    return kept


def share_box(x0, y0, x1, y1, width, height, score=1.0):
    """Plain floats (JSON-ready) even when the corners are numpy values."""
    x0, y0 = max(0.0, float(x0)), max(0.0, float(y0))
    x1, y1 = min(float(width), float(x1)), min(float(height), float(y1))
    return {"x": round(x0 / width, 4), "y": round(y0 / height, 4), "w": round((x1 - x0) / width, 4),
            "h": round((y1 - y0) / height, 4), "score": round(float(score), 3)}


class FakeBackend:
    name = "fake"

    def load(self, path):
        with open(path, "rb") as f:
            return read_bmp(f.read())

    def size(self, image):
        return image.width, image.height

    def matte(self, image, kind):
        if kind == "person":
            keep = lambda r, g, b: r > 180 and g < 90 and b < 90  # noqa: E731
        else:
            keep = lambda r, g, b: b > 180 and r < 90  # noqa: E731
        return [[1.0 if keep(*p) else 0.0 for p in row] for row in image.pixels]

    def faces(self, image):
        points = [(x, y) for y, row in enumerate(image.pixels) for x, (r, g, b) in enumerate(row)
                  if r > 180 and g > 180 and b < 90]
        if not points:
            return []
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return [share_box(min(xs), min(ys), max(xs) + 1, max(ys) + 1, image.width, image.height, 0.99)]

    def smooth(self, previous, matte, weight):
        if previous is None:
            return matte
        return [[p * (1 - weight) + m * weight for p, m in zip(pr, mr)] for pr, mr in zip(previous, matte)]

    def save(self, path, image, matte, mode):
        write_png(path, image.width, image.height, cutout_rows(image, matte, mode), 1 if mode == "matte" else 4)

    def coverage(self, matte):
        cells = sum(len(r) for r in matte)
        return sum(sum(r) for r in matte) / cells if cells else 0.0

    def bbox(self, matte, threshold=0.5):
        points = [(x, y) for y, row in enumerate(matte) for x, a in enumerate(row) if a >= threshold]
        if not points:
            return None
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return share_box(min(xs), min(ys), max(xs) + 1, max(ys) + 1, len(matte[0]), len(matte))


class OnnxBackend:
    """onnxruntime + numpy + Pillow (installed by `setup`); model files from models.json (`models --download`)."""

    name = "onnx"

    def __init__(self, models):
        try:
            import numpy
            import onnxruntime
            from PIL import Image
        except ImportError as error:
            raise AppError("not_installed", "the model packages are missing (%s): run `setup`" % error)
        self.np, self.ort, self.Image = numpy, onnxruntime, Image
        self.models = models
        self.sessions = {}

    def session(self, model_id):
        if model_id not in self.sessions:
            if self.models.missing(model_id):
                raise AppError("no_model", "%s is not downloaded: run `models --download --model %s`" % (model_id, model_id))
            path = os.path.join(self.models.folder(model_id), FILES[model_id])
            self.sessions[model_id] = self.ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        return self.sessions[model_id]

    def load(self, path):
        return self.np.asarray(self.Image.open(path).convert("RGB"))

    def size(self, image):
        return image.shape[1], image.shape[0]

    def matte(self, image, kind):
        np = self.np
        model = PERSON_MODEL if kind == "person" else OBJECT_MODEL
        side = 320 if kind == "person" else 1024
        mean, std = ((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)) if kind == "person" else ((0.5, 0.5, 0.5), (1.0, 1.0, 1.0))
        small = np.asarray(self.Image.fromarray(image).resize((side, side), self.Image.LANCZOS), dtype=np.float32)
        small = small / max(float(small.max()), 1e-6)
        small = (small - np.array(mean, dtype=np.float32)) / np.array(std, dtype=np.float32)
        session = self.session(model)
        out = session.run(None, {session.get_inputs()[0].name: small.transpose(2, 0, 1)[None].astype(np.float32)})[0]
        pred = out.reshape(out.shape[-2], out.shape[-1])
        low, high = float(pred.min()), float(pred.max())
        pred = (pred - low) / (high - low) if high > low else np.zeros_like(pred)
        height, width = image.shape[:2]
        grey = self.Image.fromarray((pred * 255).astype(np.uint8)).resize((width, height), self.Image.BILINEAR)
        return np.asarray(grey, dtype=np.float32) / 255.0

    def faces(self, image):
        np = self.np
        session = self.session(FACE_MODEL)
        shape = session.get_inputs()[0].shape
        height_in = shape[2] if isinstance(shape[2], int) else 640
        width_in = shape[3] if isinstance(shape[3], int) else 640
        small = np.asarray(self.Image.fromarray(image).resize((width_in, height_in), self.Image.BILINEAR), dtype=np.float32)
        blob = small[:, :, ::-1].transpose(2, 0, 1)[None].copy()  # BGR, NCHW, 0..255
        names = [o.name for o in session.get_outputs()]
        values = dict(zip(names, session.run(None, {session.get_inputs()[0].name: blob})))
        boxes = []
        for stride in (8, 16, 32):
            cls = values.get("cls_%d" % stride)
            obj = values.get("obj_%d" % stride)
            reg = values.get("bbox_%d" % stride)
            if cls is None or obj is None or reg is None:
                continue
            cols = width_in // stride
            score = np.sqrt(np.clip(cls.reshape(-1), 0, 1) * np.clip(obj.reshape(-1), 0, 1))
            reg = reg.reshape(-1, 4)
            for index in np.nonzero(score >= FACE_SCORE)[0]:
                row, col = divmod(int(index), cols)
                cx, cy = (col + reg[index][0]) * stride, (row + reg[index][1]) * stride
                w, h = np.exp(reg[index][2]) * stride, np.exp(reg[index][3]) * stride
                boxes.append(share_box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, width_in, height_in, score[index]))
        return nms(boxes)

    def smooth(self, previous, matte, weight):
        return matte if previous is None or previous.shape != matte.shape else previous * (1 - weight) + matte * weight

    def save(self, path, image, matte, mode):
        np = self.np
        alpha = (np.clip(matte, 0, 1) * 255).astype(np.uint8)
        if mode == "matte":
            self.Image.fromarray(alpha, "L").save(path)
        else:
            self.Image.fromarray(np.dstack([image, alpha]), "RGBA").save(path)

    def coverage(self, matte):
        return float(matte.mean())

    def bbox(self, matte, threshold=0.5):
        ys, xs = self.np.nonzero(matte >= threshold)
        if not len(xs):
            return None
        return share_box(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, matte.shape[1], matte.shape[0])


def backend(models_loader):
    """The fake backend under VISION_FAKE=1, else the ONNX one (`models_loader()` gives a bashcut_models.Models)."""
    if os.environ.get("VISION_FAKE") == "1":
        return FakeBackend()
    return OnnxBackend(models_loader())
