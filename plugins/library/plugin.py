#!/usr/bin/env python3
"""Entry point of the BashCutPro plugin `bashcut.library` (see bashcut-plugin.json)."""

import os
import sys

if sys.version_info < (3, 9):
    sys.exit("library needs Python 3.9 or later")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from library_plugin.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
