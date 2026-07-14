from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class LegacyEntrypointBoundaryTests(unittest.TestCase):
    def test_main_py_refuses_to_start_the_retired_v0_runtime(self) -> None:
        result = subprocess.run(
            [sys.executable, "main.py", "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("retired", result.stderr)
        self.assertIn("cpa-keeper", result.stderr)


if __name__ == "__main__":
    unittest.main()
