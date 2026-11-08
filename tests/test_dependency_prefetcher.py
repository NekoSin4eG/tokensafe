import unittest
import tempfile
import os
from src.dependency_prefetcher import DependencyPrefetcher

class TestDependencyPrefetcher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def test_python_prefetching(self):
        # Create service.py and consumer.py
        service_py = os.path.join(self.temp_dir, "auth_service.py")
        with open(service_py, "w", encoding="utf-8") as f:
            f.write("""
class AuthService:
    def verify_token(self, token: str) -> bool:
        return True
    def revoke_session(self, user_id: str) -> None:
        pass
""")

        consumer_py = os.path.join(self.temp_dir, "app.py")
        with open(consumer_py, "w", encoding="utf-8") as f:
            f.write("""
from .auth_service import AuthService

def handle_request():
    auth = AuthService()
    return auth.verify_token("xyz")
""")

        header, saved = DependencyPrefetcher.prefetch_for_file(consumer_py)
        self.assertIn("TOCP PREFETCHED DEPENDENCIES", header)
        self.assertIn("auth_service.py", header)
        self.assertIn("class AuthService(methods: verify_token, revoke_session)", header)
        self.assertGreater(saved, 500)

    def test_typescript_prefetching(self):
        models_ts = os.path.join(self.temp_dir, "models.ts")
        with open(models_ts, "w", encoding="utf-8") as f:
            f.write("""
export interface Order {
  id: string;
  total: number;
}
""")

        controller_ts = os.path.join(self.temp_dir, "controller.ts")
        with open(controller_ts, "w", encoding="utf-8") as f:
            f.write("""
import { Order } from "./models";

export class OrderController {}
""")

        header, saved = DependencyPrefetcher.prefetch_for_file(controller_ts)
        self.assertIn("TOCP PREFETCHED DEPENDENCIES", header)
        self.assertIn("models.ts", header)
        self.assertIn("interface Order { id: string; total: number; }", header)
        self.assertGreater(saved, 500)

if __name__ == "__main__":
    unittest.main()
