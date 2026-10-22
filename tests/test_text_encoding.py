"""Every text file a plugin opens names its encoding (FX2): on Windows the default is the code page (cp1252 on a
Western install), so a JSON written with ensure_ascii=False, a licence name or a Vietnamese title crashes or comes
back garbled. Flags `read_text` / `write_text`, built-in `open` / `io.open` in text mode, `NamedTemporaryFile` in
text mode and `subprocess` calls with `text=True` that leave the encoding out, in every Python file under plugins/."""

import ast
import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP_FILES = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}
SUBPROCESS = {"run", "Popen", "check_output", "call", "check_call"}


def keyword(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def binary(mode):
    """True when every mode the expression can take is a binary one ("rb", "ab" if … else "wb")."""
    modes = [n.value for n in ast.walk(mode) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return bool(modes) and all("b" in m for m in modes)


def problem(call):
    """What is wrong with this call, or None."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
    has_encoding = keyword(call, "encoding") is not None
    if name == "read_text" and isinstance(func, ast.Attribute):
        if not call.args and not has_encoding:
            return "read_text() without encoding="
    elif name == "write_text" and isinstance(func, ast.Attribute):
        if len(call.args) < 2 and not has_encoding:
            return "write_text() without encoding="
    elif name == "open" and (isinstance(func, ast.Name) or owner == "io"):
        mode = call.args[1] if len(call.args) > 1 else keyword(call, "mode")
        if (mode is None or not binary(mode)) and len(call.args) < 4 and not has_encoding:
            return "open() in text mode without encoding="
    elif name in TEMP_FILES:
        mode = call.args[0] if call.args else keyword(call, "mode")
        if mode is not None and not binary(mode) and not has_encoding:
            return name + "() in text mode without encoding="
    elif name in SUBPROCESS and owner == "subprocess":
        text = keyword(call, "text") or keyword(call, "universal_newlines")
        if isinstance(text, ast.Constant) and text.value is True and not has_encoding:
            return "subprocess.%s(text=True) without encoding=" % name
    return None


def problems(source, filename="<source>"):
    return [(filename, node.lineno, why) for node in ast.walk(ast.parse(source, filename))
            if isinstance(node, ast.Call) for why in [problem(node)] if why]


def plugin_files():
    for path in sorted(glob.glob(os.path.join(ROOT, "plugins", "**", "*.py"), recursive=True)):
        if "__pycache__" not in path:
            yield path


class TextEncodingTests(unittest.TestCase):
    def test_every_text_file_in_the_plugins_names_its_encoding(self):
        found = []
        for path in plugin_files():
            with open(path, encoding="utf-8") as f:
                found += problems(f.read(), os.path.relpath(path, ROOT))
        self.assertEqual([], ["%s:%d %s" % row for row in found])

    def test_the_check_catches_a_planted_bad_call(self):
        bad = '\n'.join([
            'Path(p).read_text()',
            'Path(p).write_text(json.dumps(x, ensure_ascii=False))',
            'open(p)',
            'open(p, "w")',
            'with open(p, "a" if more else "w") as f: pass',
            'io.open(p, mode="r")',
            'tempfile.NamedTemporaryFile("w", suffix=".json")',
            'subprocess.run(["bashcut"], capture_output=True, text=True)',
        ])
        self.assertEqual(sorted(line for _, line, _ in problems(bad)), list(range(1, 9)))
        good = '\n'.join([
            'Path(p).read_text(encoding="utf-8")',
            'Path(p).read_text("utf-8", "replace")',
            'Path(p).write_text(text, encoding="utf-8")',
            'Path(p).write_text(text, "utf-8")',
            'open(p, encoding="utf-8")',
            'open(p, "wb")',
            'open(p, "ab" if resumed else "wb")',
            'open(p, mode="rb")',
            'tarfile.open(fileobj=b, mode="r:gz")',
            'Image.open(p)',
            'tempfile.NamedTemporaryFile(suffix=".png")',
            'tempfile.NamedTemporaryFile("w", encoding="utf-8")',
            'subprocess.run(["curl"], capture_output=True)',
            'subprocess.run(["bashcut"], capture_output=True, text=True, encoding="utf-8")',
        ])
        self.assertEqual(problems(good), [])


if __name__ == "__main__":
    unittest.main()
