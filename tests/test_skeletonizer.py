import unittest
import tempfile
import os
from src.skeletonizer import Skeletonizer

class TestSkeletonizer(unittest.TestCase):
    def test_python_skeletonization(self):
        py_code = """
class BillingService:
    def __init__(self, key: str):
        self.key = key
        self.connected = True
        print("Billing init")

    def process_invoice(self, amount: float) -> bool:
        if amount <= 0:
            return False
        return True
"""
        skel = Skeletonizer.skeletonize_code(py_code, ".py")
        self.assertIn("class BillingService:", skel)
        self.assertIn("def process_invoice(self, amount: float) -> bool:", skel)
        self.assertNotIn("print(\"Billing init\")", skel)
        self.assertIn("...", skel)

    def test_typescript_skeletonization(self):
        ts_code = """
export class UserService {
  private cache: Map<string, any>;

  constructor(apiKey: string) {
    this.cache = new Map();
    console.log("Ready");
  }

  async findUser(id: string): Promise<User> {
    const res = await db.query(id);
    return res;
  }
}
"""
        skel = Skeletonizer.skeletonize_code(ts_code, ".ts")
        self.assertIn("export class UserService {", skel)
        self.assertIn("async findUser(id: string): Promise<User> { /* ... */ }", skel)
        self.assertNotIn("console.log(\"Ready\");", skel)

    def test_file_skeleton_caching(self):
        # Create file with realistic multi-line methods to meet size threshold
        temp = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".py")
        temp.write("class BigWorker:\n")
        for i in range(1, 100):
            temp.write(f"""    def task_{i}(self, data: dict) -> bool:
        if not data:
            return False
        total = sum(data.get("values", []))
        self.db.update({i}, total)
        return total > 100\n""")
        temp.flush()
        temp.close()

        skel_path, orig_t, skel_t = Skeletonizer.get_skeleton_file(temp.name)
        self.assertIsNotNone(skel_path)
        self.assertTrue(os.path.exists(skel_path))
        self.assertLess(skel_t, orig_t)

        if os.path.exists(temp.name):
            os.remove(temp.name)
        if skel_path and os.path.exists(skel_path):
            os.remove(skel_path)

if __name__ == "__main__":
    unittest.main()
