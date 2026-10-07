from __future__ import annotations

import ast
import unittest
from pathlib import Path


class Phase7DispatchScriptTests(unittest.TestCase):
    def test_dispatch_script_always_restores_disabled_state_in_finally(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "dispatch_phase7_manifest.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        tries = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
        self.assertTrue(any(node.finalbody for node in tries))
        self.assertIn('/disable"', source)
        self.assertIn("final_state != original_state", source)


if __name__ == "__main__":
    unittest.main()
