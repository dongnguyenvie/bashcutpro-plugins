"""Every plugin's manifest describes the command line that really exists (RP7): its version is the package's
`__version__`, its actions are `cli.COMMANDS`, each param is a flag of its action's subcommand of `build_parser()`, an
action that needs a project takes the `connect` flags, and the settings are the ones `settings.SETTINGS` resolves.
Checks only a plugin's own tests can make (choices that mirror a table, which subcommands take a setting) stay in its
`tests/`."""

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_registry  # noqa: E402

# Run in a child Python with the plugin folder on sys.path (every plugin vendors modules of the same name): prints
# the package's version, `cli.COMMANDS`, the option strings of the parser and each subcommand ({"sub cmd": [...]})
# the required positional arguments of each subcommand ({"sub cmd": [dest, ...]}) and the settings table
# ({id: [env vars]}, null without a `settings.SETTINGS`).
DESCRIBE = r"""
import argparse, importlib, json, sys
sys.path.insert(0, sys.argv[1])
package = sys.argv[2]
cli = importlib.import_module(package + ".cli")
def walk(parser, path, out, required):
    options = out.setdefault(" ".join(path), [])
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sub in a.choices.items():
                walk(sub, path + [name], out, required)
        elif not a.option_strings and a.required:
            required.setdefault(" ".join(path), []).append(a.dest)
        options += a.option_strings
    return out
try:
    settings = {k: list(v[0]) for k, v in importlib.import_module(package + ".settings").SETTINGS.items()}
except (ImportError, AttributeError):
    settings = None
required = {}
print(json.dumps({"version": importlib.import_module(package).__version__, "commands": sorted(cli.COMMANDS),
                  "parsers": walk(cli.build_parser(), [], {}, required), "positionals": required, "settings": settings}))
"""

# Commands for the calling agent only (in the plugin's README and skill), with no form in the app.
CLI_ONLY = {"library": {"kinds", "show"}}


def plugins():
    folder = os.path.join(ROOT, "plugins")
    return [os.path.join(folder, name) for name in sorted(os.listdir(folder))
            if os.path.isfile(os.path.join(folder, name, "bashcut-plugin.json"))]


def describe(base):
    module = check_registry.parser_module(base)
    if module is None:
        raise AssertionError("no <package>/cli.py with build_parser() in %s" % base)
    run = subprocess.run([sys.executable, "-c", DESCRIBE, base, module.rsplit(".", 1)[0]],
                         capture_output=True, encoding="utf-8")
    if run.returncode != 0:
        raise AssertionError("%s: %s" % (module, run.stderr.strip()))
    return json.loads(run.stdout)


def subcommand(parsers, args):
    """The longest path of `args` that is a subcommand ("brand show"), as check_registry does."""
    path = []
    for arg in args:
        if " ".join(path + [str(arg)]) not in parsers:
            break
        path.append(str(arg))
    return " ".join(path)


class ManifestCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = {}
        for base in plugins():
            with open(os.path.join(base, "bashcut-plugin.json"), encoding="utf-8") as f:
                cls.found[os.path.basename(base)] = (json.load(f), describe(base))

    def each(self, check):
        self.assertTrue(self.found)
        for name, (manifest, code) in sorted(self.found.items()):
            with self.subTest(plugin=name):
                check(name, manifest, code)

    def test_version_matches_the_code(self):
        self.each(lambda name, manifest, code: self.assertEqual(manifest["version"], code["version"]))

    def test_actions_are_the_commands(self):
        self.each(lambda name, manifest, code: self.assertEqual(
            {a["args"][0] for a in manifest["actions"]} | CLI_ONLY.get(name, set()), set(code["commands"])))

    def test_every_param_is_a_flag_of_its_subcommand(self):
        def check(name, manifest, code):
            for action in manifest["actions"]:
                path = subcommand(code["parsers"], action["args"])
                self.assertEqual(path, " ".join(action["args"]), "action %s is not a subcommand" % action["id"])
                accepted = code["parsers"][path]
                for param in action.get("params") or []:
                    self.assertIn(param["flag"], accepted, "%s %s" % (action["id"], param["flag"]))
                if action["needs"] == "project":
                    for args in manifest["connect"].values():
                        self.assertIn(args[0], accepted, "%s %s" % (action["id"], args[0]))
        self.each(check)

    def test_no_action_needs_an_argument_the_app_cannot_pass(self):
        """The app passes an action's `args` and its params' flags only: a required positional argument the `args`
        do not fill can never be given, so the action always fails (PX: hyperframes `render COMPOSITION`)."""
        def check(name, manifest, code):
            for action in manifest["actions"]:
                path = subcommand(code["parsers"], action["args"])
                given = len(action["args"]) - len(path.split())  # args after the subcommand fill positionals
                self.assertEqual(code["positionals"].get(path, [])[given:], [], "action %s" % action["id"])
        self.each(check)

    def test_settings_match_the_resolver(self):
        def check(name, manifest, code):
            settings = manifest.get("settings") or []
            if code["settings"] is None:
                return
            self.assertEqual({s["id"] for s in settings}, set(code["settings"]))
            for s in settings:
                self.assertEqual(s["env"][0], code["settings"][s["id"]][0], s["id"])
                if s.get("flag"):
                    takers = [a["id"] for a in manifest["actions"]
                              if s["flag"] in code["parsers"][subcommand(code["parsers"], a["args"])]]
                    self.assertTrue(takers, "no action takes setting %s's %s" % (s["id"], s["flag"]))
        self.each(check)


if __name__ == "__main__":
    unittest.main()
