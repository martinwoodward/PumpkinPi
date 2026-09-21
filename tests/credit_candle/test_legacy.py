import hashlib
from pathlib import Path
import unittest

from .support import ROOT


class LegacyTests(unittest.TestCase):
    def test_legacy_sources_remain_tracked_and_untouched_by_new_runtime(self):
        expected = {
            "src/buildtest.py", "src/flametest.py", "src/local_settings.sample",
            "src/pumpkinpi.py", "src/testpatterns.py",
        }
        self.assertEqual({str(path.relative_to(ROOT)) for path in
                          (ROOT / "src").iterdir() if path.is_file()}, expected)
