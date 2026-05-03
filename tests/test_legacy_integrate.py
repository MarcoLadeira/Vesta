import unittest

from opcoding.integrate import launch_plan


class LegacyIntegrateTests(unittest.TestCase):
    def test_launch_plan_keeps_tool_and_args_separate(self):
        plan = launch_plan("copilot", ["suggest", "tests"])

        self.assertEqual(plan["tool"], "copilot")
        self.assertEqual(plan["tool_args"], ["suggest", "tests"])
        self.assertEqual(plan["opai_args"][0], "launch")

    def test_launch_plan_rejects_unknown_tool(self):
        with self.assertRaises(ValueError):
            launch_plan("missing", [])


if __name__ == "__main__":
    unittest.main()
