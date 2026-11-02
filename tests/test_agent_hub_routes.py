import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentHubRoutesTests(unittest.TestCase):
    def test_every_hub_card_targets_an_existing_view(self):
        source = (ROOT / "ai_agents.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        app_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AIAgentsApp")
        methods = {node.name for node in app_class.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        hub = next(node for node in app_class.body if isinstance(node, ast.FunctionDef) and node.name == "_show_agents_hub")
        definitions = next(
            node.value for node in ast.walk(hub)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "agent_defs" for target in node.targets)
        )
        card_routes = {
            item.elts[1].value: item.elts[4].attr
            for item in definitions.elts
            if isinstance(item, ast.Tuple) and len(item.elts) == 5
        }
        self.assertEqual(len(card_routes), 8)
        for title, route in card_routes.items():
            with self.subTest(agent=title):
                self.assertIn(route, methods)

    def test_non_maic_cards_have_history_rail_ids(self):
        source = (ROOT / "ai_agents.py").read_text(encoding="utf-8")
        self.assertIn('"Personal Growth Mentor": "personal-growth"', source)
        self.assertIn('"Study Agent": "study"', source)
        self.assertIn('"Teacher Audit Logs": "audit"', source)
        self.assertIn("self._open_agent_workspace(agent_id, agent_name, agent_cmd)", source)
        self.assertIn("self._show_agent_rail(agent_id, title, view)", source)


if __name__ == "__main__":
    unittest.main()
