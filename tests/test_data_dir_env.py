"""A test that isolates the data dir sets both overrides (PY): plugins look at `BASHCUTPRO_DATA_DIR` before
`FILMCRAFT_DATA_DIR` (SB1, bashcut-dirs' order), so a test that sets only `FILMCRAFT_DATA_DIR` runs on real data in a
shell with `BASHCUTPRO_DATA_DIR` set. Flags every function in a test file under `tests/` or `plugins/*/tests/` that
puts `FILMCRAFT_DATA_DIR` into an environment (`os.environ[...] =`, `FILMCRAFT_DATA_DIR=` in a call,
`{"FILMCRAFT_DATA_DIR": ...}` handed to `os.environ.update` / `mock.patch.dict(os.environ, ...)`) and never names
`BASHCUTPRO_DATA_DIR`. The fake engine's `data_dir_env()` / `use_data_dir()` set both."""

import ast
import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY, CURRENT = "FILMCRAFT_DATA_DIR", "BASHCUTPRO_DATA_DIR"


def is_environ(node):
    return isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os"


def has_key(node, name):
    return isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == name for k in node.keys)


def sets(node, name):
    """Whether this node puts `name` into an environment."""
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return any(isinstance(t, ast.Subscript) and is_environ(t.value) and isinstance(t.slice, ast.Constant)
                   and t.slice.value == name for t in targets)
    if isinstance(node, ast.Call):
        if any(k.arg == name for k in node.keywords):
            return True
        func = node.func
        on_environ = (isinstance(func, ast.Attribute) and is_environ(func.value)) or any(is_environ(a) for a in node.args)
        return on_environ and any(has_key(a, name) for a in node.args)
    return False


def names(node, name):
    return any(isinstance(n, ast.Constant) and n.value == name or isinstance(n, ast.keyword) and n.arg == name
               for n in ast.walk(node))


def problems(source, path="<test>"):
    """`path:line function` for every function that sets FILMCRAFT_DATA_DIR and never names BASHCUTPRO_DATA_DIR."""
    out = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or names(fn, CURRENT):
            continue
        for node in ast.walk(fn):
            if sets(node, LEGACY):
                out.append("%s:%d %s" % (path, node.lineno, fn.name))
    return out


def test_files():
    for pattern in ("tests/*.py", "plugins/*/tests/*.py"):
        for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
            if os.path.basename(path) != "bashcut_fake_engine.py":
                yield path


class DataDirEnv(unittest.TestCase):
    def test_no_test_sets_filmcraft_data_dir_alone(self):
        found = []
        for path in test_files():
            with open(path, encoding="utf-8") as f:
                found += problems(f.read(), os.path.relpath(path, ROOT).replace(os.sep, "/"))
        self.assertEqual(found, [], "set BASHCUTPRO_DATA_DIR too (bashcut_fake_engine.use_data_dir / data_dir_env)")

    def test_the_check_flags_each_way_of_setting_it(self):
        for body in ('os.environ["FILMCRAFT_DATA_DIR"] = d',
                     'env = dict(os.environ, FILMCRAFT_DATA_DIR=d)',
                     'os.environ.update({"FILMCRAFT_DATA_DIR": d})',
                     'with mock.patch.dict(os.environ, {"FILMCRAFT_DATA_DIR": d}, clear=True):\n        pass'):
            self.assertEqual(len(problems("def setUp(self):\n    %s\n" % body)), 1, body)

    def test_the_check_passes_both_and_plain_dicts(self):
        for body in ('os.environ["FILMCRAFT_DATA_DIR"] = d\n    os.environ["BASHCUTPRO_DATA_DIR"] = d',
                     'env = dict(os.environ, FILMCRAFT_DATA_DIR=d, BASHCUTPRO_DATA_DIR=d)',
                     'self.assertEqual(data_dir({"FILMCRAFT_DATA_DIR": "/d"}), "/d")',
                     'use_data_dir(d, self)'):
            self.assertEqual(problems("def setUp(self):\n    %s\n" % body), [], body)


if __name__ == "__main__":
    unittest.main()
