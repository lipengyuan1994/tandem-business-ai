"""Budget controls reach both inference processes through Compose's merged environment."""

import unittest

from support import ROOT
from test_ops_config import yaml_load


class AiBudgetConfigurationTests(unittest.TestCase):
    def test_budget_settings_reach_only_api_and_worker(self):
        compose = yaml_load((ROOT / "deploy/compose/compose.prod.yml").read_text())
        expected = {
            "AI_DAILY_COST_LIMIT_USD": "${AI_DAILY_COST_LIMIT_USD:-5.00}",
            "AI_MONTHLY_COST_LIMIT_USD": "${AI_MONTHLY_COST_LIMIT_USD:-50.00}",
            "AI_MAX_CONCURRENT_CALLS": "${AI_MAX_CONCURRENT_CALLS:-2}",
            "AI_PUBLIC_INTAKE_PER_HOUR": "${AI_PUBLIC_INTAKE_PER_HOUR:-30}",
            "AI_PUBLIC_INTAKE_MAX_PENDING": "${AI_PUBLIC_INTAKE_MAX_PENDING:-30}",
            "AI_PUBLIC_DAILY_TOKEN_BUDGET_PER_BUSINESS": "${AI_PUBLIC_DAILY_TOKEN_BUDGET_PER_BUSINESS:-200000}",
            "AI_BUDGET_NOTIFICATION_EMAIL": "${AI_BUDGET_NOTIFICATION_EMAIL:-}",
        }
        for name, service in compose["services"].items():
            env = service.get("environment", {})
            with self.subTest(service=name):
                if name in {"api", "worker"}:
                    self.assertEqual({key: env.get(key) for key in expected}, expected)
                else:
                    self.assertFalse(expected.keys() & env.keys())
