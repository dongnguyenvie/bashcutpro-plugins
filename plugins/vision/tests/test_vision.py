"""Cutout, subjects / reframe, OCR and descriptions through the fake MCP server, with the fake model backend
(VISION_FAKE=1: red is a person, yellow a face, blue an object). No model is downloaded or run."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, PLUGIN)

from vision_plugin.backends import FakeBackend, nms  # noqa: E402
from vision_plugin.images import Image, read_png_size  # noqa: E402
from vision_plugin.ocr import parse_tsv  # noqa: E402
from vision_plugin.timeline import reframe_steps  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")
TPS = 254016000000
PERSON = {"width": 48, "height": 27,
          "person": {"x": 0.1, "y": 0.2, "w": 0.2, "h": 0.7, "dx": 0.1},
          "face": {"x": 0.13, "y": 0.22, "w": 0.1, "h": 0.18, "dx": 0.1}}
OBJECT = {"width": 48, "height": 27, "object": {"x": 0.4, "y": 0.3, "w": 0.3, "h": 0.4}}


def clip(cid, item, start=0, seconds=0.5):
    return {"clip": cid, "item": item, "name": "clip %d" % cid, "start": start, "duration": int(TPS * seconds),
            "sourceIn": 0, "sourceOut": int(TPS * seconds), "speed": 1.0, "enabled": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, "demo.fcproj")
        self.write_state(64, 36, [clip(1, 7)], {"7": PERSON})

    def write_state(self, width, height, clips, frames):
        state = {"sequence": {"settings": {"width": width, "height": height, "frame_rate": {"num": 10, "den": 1}},
                              "video": [{"name": "V1", "items": clips}]},
                 "frames": frames}
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def read_state(self):
        with open(self.state, encoding="utf-8") as f:
            return json.load(f)

    def run_cli(self, *args, project=True, env=None):
        full = [sys.executable, os.path.join(PLUGIN, "plugin.py")] + list(args)
        if project:
            full += ["--project", self.state, "--cli", FAKE]
        environment = dict(os.environ, VISION_FAKE="1", PATH="/nonexistent", BASHCUT_MODELS_DIR=os.path.join(self.dir, "models"),
                           FILMCRAFT_DATA_DIR=os.path.join(self.dir, "data"))
        environment.update(env or {})
        out = subprocess.run(full, capture_output=True, env=environment)
        return out.returncode, json.loads(out.stdout.decode("utf-8"))

    def commands(self, command_id):
        return [entry["params"] for entry in self.read_state()["log"] if entry["id"] == command_id]


class CutoutTests(Base):
    def test_person_is_cut_out_imported_as_a_sequence_and_placed_above(self):
        code, result = self.run_cli("cutout", "--clip", "1")
        self.assertEqual(code, 0, result)
        data = result["data"]
        self.assertEqual((data["kind"], data["frames"], data["track"]), ("person", 5, "V2"))
        frames = sorted(os.listdir(data["folder"]))
        self.assertEqual(frames[0], "cutout_00001.png")
        self.assertEqual(read_png_size(os.path.join(data["folder"], frames[0])), (48, 27))
        with open(os.path.join(data["folder"], frames[0]), "rb") as f:
            self.assertEqual(f.read()[25], 6)  # colour type RGBA
        state = self.read_state()
        self.assertEqual(state["imported"][0]["paths"], [os.path.join(data["folder"], frames[0])])
        self.assertEqual(self.commands("file.import")[0]["imageSequence"], True)
        self.assertEqual(self.commands("prefs.set")[0]["value"], "10")  # imported at the sequence's rate
        self.assertEqual(state["prefs"]["media.indeterminateTimebase"], "25")  # and the preference put back
        placed = state["sequence"]["video"][1]["items"][0]
        self.assertEqual((placed["start"], placed["duration"]), (0, int(TPS * 0.5)))
        self.assertEqual(state["saved"], 1)
        self.assertGreater(data["coverage"], 0.05)

    def test_without_a_person_the_main_object_is_cut_out_as_a_matte(self):
        self.write_state(64, 36, [clip(1, 8)], {"8": OBJECT})
        code, result = self.run_cli("cutout", "--output", "matte", "--no-place")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["data"]["kind"], "object")
        first = os.path.join(result["data"]["folder"], "cutout_00001.png")
        with open(first, "rb") as f:
            self.assertEqual(f.read()[25], 0)  # grey
        self.assertEqual(self.commands("timeline.place"), [])

    def test_too_long_a_clip_is_refused(self):
        code, result = self.run_cli("cutout", "--max-frames", "3")
        self.assertEqual((code, result["error"]["code"]), (1, "too_long"))

    def test_smoothing_blends_with_the_previous_frame(self):
        fake = FakeBackend()
        self.assertEqual(fake.smooth([[0.0, 1.0]], [[1.0, 1.0]], 0.6), [[0.6, 1.0]])
        self.assertEqual(fake.smooth(None, [[1.0]], 0.6), [[1.0]])


class SubjectsTests(Base):
    def test_faces_go_to_the_sidecar_and_reframe_follows_the_face(self):
        self.write_state(27, 48, [clip(1, 7, seconds=1.0)], {"7": dict(PERSON, person=dict(PERSON["person"], dx=0.05),
                                                                         face=dict(PERSON["face"], dx=0.05))})
        code, result = self.run_cli("subjects", "--samples", "4", "--reframe")
        self.assertEqual(code, 0, result)
        sidecar_file = os.path.join(self.dir, "demo.vision.json")
        self.assertEqual(result["data"]["sidecar"], sidecar_file)
        with open(sidecar_file, encoding="utf-8") as f:
            sidecar = json.load(f)
        self.assertEqual(sidecar["schema"], "bashcut-vision/1")
        samples = sidecar["clips"]["1"]["subjects"]["samples"]
        self.assertEqual(len(samples), 4)
        self.assertTrue(all(len(s["faces"]) == 1 and len(s["persons"]) == 1 for s in samples))
        motion = self.read_state()["sequence"]["video"][0]["items"][0]["motion"]
        self.assertAlmostEqual(motion["scale"], 48 / 27 * 100, places=2)
        xs = [k["value"][0] for k in motion["keyframes"]]
        self.assertEqual(len(xs), 4)
        self.assertEqual(xs, sorted(xs, reverse=True))  # the face moves right, the picture moves left
        shown = 48 * 48 / 27
        self.assertTrue(all(27 - shown / 2 <= x <= shown / 2 for x in xs))  # the picture always covers the frame
        self.assertEqual(self.read_state()["saved"], 1)

    def test_bridge_mode_needs_a_sidecar_path(self):
        code, result = self.run_cli("subjects", "--bridge", "127.0.0.1:1", "--cli", FAKE, project=False)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))

    def test_reframe_without_subjects_changes_nothing(self):
        steps, plan = reframe_steps(clip(1, 7), [{"sourceTime": 0, "faces": [], "persons": []}],
                                    {"settings": {"width": 1080, "height": 1920}}, (1920, 1080))
        self.assertEqual((steps, plan), ([], None))


class TextAndDescriptionTests(Base):
    def test_ocr_without_tesseract_hands_off_then_stores_the_answer(self):
        code, result = self.run_cli("ocr", "--samples", "2")
        self.assertEqual(code, 3, result)
        data = result["data"]
        self.assertEqual(data["then"], "ocr-apply")
        self.assertEqual(len(data["frames"]), 2)
        self.assertTrue(all(os.path.isfile(f["path"]) and f["path"].endswith(".png") for f in data["frames"]))
        answer = os.path.join(self.dir, "answer.json")
        with open(answer, "w", encoding="utf-8") as f:
            json.dump({"frames": [{"clip": 1, "time": data["frames"][0]["time"],
                                   "text": [{"text": "Xin chào", "x": 0.2, "y": 0.85, "w": 0.6, "h": 0.08}]}]}, f)
        code, result = self.run_cli("ocr-apply", "--sidecar", data["sidecar"], "--answer", answer, project=False)
        self.assertEqual(code, 0, result)
        with open(data["sidecar"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["clips"]["1"]["text"][0]["lines"][0]["text"], "Xin chào")

    def test_describe_hands_off_then_stores_descriptions(self):
        code, result = self.run_cli("describe")
        self.assertEqual((code, result["data"]["then"]), (3, "describe-apply"))
        self.assertEqual(len(result["data"]["frames"]), 3)
        answer = os.path.join(self.dir, "answer.json")
        with open(answer, "w", encoding="utf-8") as f:
            json.dump({"clips": [{"clip": 1, "description": "A presenter walks left to right.", "tags": ["medium", "indoor"]},
                                 {"clip": 2, "description": ""}]}, f)
        code, result = self.run_cli("describe-apply", "--sidecar", result["data"]["sidecar"], "--answer", answer, project=False)
        self.assertEqual((code, result["data"]["stored"]), (0, 1))

    def test_tesseract_tsv_becomes_lines_with_boxes(self):
        tsv = "\n".join([
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "5\t1\t1\t1\t1\t1\t100\t900\t80\t40\t91.5\tHello",
            "5\t1\t1\t1\t1\t2\t190\t902\t90\t40\t88.0\tworld",
            "5\t1\t2\t1\t1\t1\t10\t10\t30\t20\t20.0\tnoise",
        ])
        lines = parse_tsv(tsv, 1000, 1000)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["text"], "Hello world")
        self.assertEqual((lines[0]["x"], lines[0]["y"], lines[0]["w"]), (0.1, 0.9, 0.18))


    @unittest.skip("test ticket: a Tesseract that runs past the timeout ends ocr with ocr_failed JSON, not a traceback")
    def test_tesseract_timeout_is_an_ocr_failed_error(self):
        pass

    @unittest.skip("test ticket: ocr-apply with string and float times next to stored int times stores ints, sorted")
    def test_ocr_apply_turns_the_agent_times_into_ticks(self):
        pass

    @unittest.skip("test ticket: ocr-apply with a time that is not a number exits 2 (bad_args) and saves nothing")
    def test_ocr_apply_refuses_a_time_that_is_not_a_number(self):
        pass

    @unittest.skip("test ticket: a second ocr/describe run leaves only its own frames in vision-frames; ocr with Tesseract removes the folder")
    def test_agent_frames_do_not_pile_up(self):
        pass

class ModelsTests(Base):
    def test_models_are_listed_from_models_json(self):
        code, result = self.run_cli("models", project=False)
        self.assertEqual(code, 0, result)
        ids = [m["id"] for m in result["data"]["models"]]
        self.assertEqual(ids, ["u2net-human-seg", "isnet-general-use", "yunet-face-2023mar"])
        self.assertFalse(any(m["downloaded"] for m in result["data"]["models"]))

    def test_download_fetches_only_the_named_model(self):
        import argparse
        from unittest import mock
        from vision_plugin import cli
        fetched = []
        found = mock.Mock(missing=lambda m: True, model=lambda m: {"title": m}, folder=lambda m: m,
                          fetch=lambda m: fetched.append(m))
        with mock.patch.object(cli, "models", return_value=found):
            cli.cmd_models(argparse.Namespace(download=True, model="yunet-face-2023mar"))
        self.assertEqual(fetched, ["yunet-face-2023mar"])

    def test_setup_dry_run(self):
        code, result = self.run_cli("setup", "--dry-run", project=False)
        self.assertEqual(code, 0, result)
        self.assertIn("onnxruntime>=1.17", result["data"]["steps"][-1])

    @unittest.skip("test ticket: setup with a step that prints to stdout (fake python/uv): stdout is exactly one JSON object, the step output is on stderr")
    def test_setup_stdout_is_one_json_object(self):
        pass

    def test_nms_keeps_the_best_of_overlapping_boxes(self):
        boxes = [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "score": 0.9}, {"x": 0.11, "y": 0.1, "w": 0.2, "h": 0.2, "score": 0.7},
                 {"x": 0.6, "y": 0.1, "w": 0.2, "h": 0.2, "score": 0.8}]
        self.assertEqual([b["score"] for b in nms(boxes)], [0.9, 0.8])

    def test_fake_backend_finds_the_face(self):
        image = Image(4, 2, [[(0, 0, 0), (230, 220, 40), (0, 0, 0), (0, 0, 0)], [(0, 0, 0)] * 4])
        self.assertEqual(FakeBackend().faces(image)[0]["x"], 0.25)


if __name__ == "__main__":
    unittest.main()
