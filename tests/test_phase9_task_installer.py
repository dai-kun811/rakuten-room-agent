from __future__ import annotations

import re
import unittest
from pathlib import Path


class Phase9TaskInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "install-room-phase8-orchestrator.ps1"
        ).read_text(encoding="utf-8")

    def test_phase9_tasks_have_separate_bounded_execution_limits(self) -> None:
        self.assertRegex(
            self.script,
            re.compile(
                r"\$engagementSettings\s*=.*?ExecutionTimeLimit "
                r"\(New-TimeSpan -Minutes 75\)",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            self.script,
            re.compile(
                r"\$auditSettings\s*=.*?ExecutionTimeLimit "
                r"\(New-TimeSpan -Minutes 45\)",
                re.DOTALL,
            ),
        )

    def test_engagement_does_not_reuse_orchestrator_settings(self) -> None:
        engagement_task = re.search(
            r"\$engagementTask\s*=\s*New-ScheduledTask(?P<body>.*?)\$auditAction",
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(engagement_task)
        self.assertIn("-Settings $engagementSettings", engagement_task.group("body"))
        self.assertNotIn("-Settings $orchestratorSettings", engagement_task.group("body"))


if __name__ == "__main__":
    unittest.main()
