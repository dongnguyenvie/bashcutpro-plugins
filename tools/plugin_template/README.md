# __TITLE__

__SUMMARY__

A BashCutPro plugin (`__ID__`): install it from **Plugins** on the app's right-hand rail, or run it from a shell or
an agent (the skill is in `skills/__NAME__/SKILL.md`).

```sh
python3 plugin.py clips --project film.fcproj        # headless
python3 plugin.py clips --bridge 127.0.0.1:9876      # the open app (bashcutpro --control 9876)
```

Tests: `python3 -m unittest discover -s tests` (no app needed: `tests/fake_filmcraft.py` stands in for the engine, on the shared `tests/bashcut_fake_engine.py`).
