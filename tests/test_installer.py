import unittest
from src.installer import AgentInstaller, AGENTS_REGISTRY

class TestAgentInstaller(unittest.TestCase):
    def test_detection_runs_without_exceptions(self):
        detected = AgentInstaller.detect_available_agents()
        self.assertIn("antigravity", detected)
        self.assertIn("claude_desktop", detected)
        self.assertIn("cursor", detected)
        self.assertIn("windsurf", detected)
        self.assertIn("cline", detected)

if __name__ == "__main__":
    unittest.main()
