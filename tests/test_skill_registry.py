"""Regression guard: a timed to-do must route to the tasks skill ALONE.

The old router told Haiku to ALSO spawn the calendar skill for a timed to-do,
which produced duplicate calendar events. This locks in the consolidated rule.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skill_registry import build_router_prompt

SKILLS = {
    "tasks": {"description": "Personal task list."},
    "calendar": {"description": "Calendar events."},
}


class TestRouterPrompt(unittest.TestCase):
    def setUp(self):
        self.prompt = build_router_prompt(SKILLS)

    def test_does_not_dual_spawn_calendar_for_todos(self):
        self.assertNotIn("ALSO include the calendar skill", self.prompt)

    def test_routes_todos_to_tasks_alone(self):
        self.assertIn("tasks", self.prompt)
        self.assertIn("ALONE", self.prompt)


if __name__ == "__main__":
    unittest.main()
