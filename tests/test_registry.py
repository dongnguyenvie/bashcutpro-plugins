"""The registry and every plugin manifest follow the format in README.md."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import check_registry  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_registry_and_manifests_are_consistent(self):
        self.assertEqual(check_registry.check(), [])


if __name__ == "__main__":
    unittest.main()
