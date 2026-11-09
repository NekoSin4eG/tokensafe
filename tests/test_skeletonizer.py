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

    def test_react_tsx_components_and_control_flow(self):
        tsx_code = """
import React, { useState } from 'react';

export const ModalInner: React.FC<ModalProps> = ({
  title,
  isOpen,
}) => {
  const [val, setVal] = useState(0);

  try {
    if (val > 0) {
      setVal(0);
    }
  } catch (err: any) {
    console.error(err);
  }

  const handleClick = (e: any) => {
    runClick();
  };

  return <div>{title}</div>;
};

export const Modal: React.FC<ModalProps> = (props) => {
  return <ModalInner {...props} />;
};
"""
        # 1. Whole-file skeleton: both components folded, control flow intact
        skel_all = Skeletonizer.skeletonize_code(tsx_code, ".tsx")
        self.assertIn("ModalInner: React.FC<ModalProps> = ({", skel_all)
        self.assertIn("}) => { /* ... */ };", skel_all)
        self.assertIn("Modal: React.FC<ModalProps> = (props) => { /* ... */ };", skel_all)
        self.assertNotIn("console.error", skel_all)

        # 2. Selective graph skeleton: Target Modal preserved, ModalInner folded
        skel_target = Skeletonizer.skeletonize_code(tsx_code, ".tsx", keep_symbols={"Modal"})
        self.assertIn("ModalInner: React.FC<ModalProps> = ({", skel_target)
        self.assertIn("}) => { /* ... */ };", skel_target)
        self.assertIn("return <ModalInner {...props} />;", skel_target)

        # 3. Target ModalInner preserved: catch and if must NOT be folded into /* ... */
        skel_inner = Skeletonizer.skeletonize_code(tsx_code, ".tsx", keep_symbols={"ModalInner"})
        self.assertIn("catch (err: any) {", skel_inner)
        self.assertIn("if (val > 0) {", skel_inner)
        self.assertNotIn("catch (err: any) { /* ... */ }", skel_inner)
        self.assertNotIn("if (val > 0) { /* ... */ }", skel_inner)
        self.assertIn("handleClick = (e: any) => { /* ... */ };", skel_inner)

    def test_go_skeletonization(self):
        go_code = """
package service

type Config struct {
    Timeout int
}

func NewService(cfg Config) *Service {
    if cfg.Timeout <= 0 {
        cfg.Timeout = 30
    }
    return &Service{cfg: cfg}
}

func (s *Service) Execute(ctx context.Context) error {
    return nil
}
"""
        skel = Skeletonizer.skeletonize_code(go_code, ".go")
        self.assertIn("type Config struct {", skel)
        self.assertIn("func NewService(cfg Config) *Service { /* ... */ }", skel)
        self.assertIn("func (s *Service) Execute(ctx context.Context) error { /* ... */ }", skel)
        self.assertNotIn("cfg.Timeout = 30", skel)

    def test_rust_skeletonization(self):
        rs_code = """
pub struct Client {
    url: String,
}

impl Client {
    pub fn new(url: String) -> Self {
        if url.is_empty() {
            panic!("empty");
        }
        Client { url }
    }

    pub async fn send(&self) -> Result<(), Error> {
        Ok(())
    }
}
"""
        skel = Skeletonizer.skeletonize_code(rs_code, ".rs")
        self.assertIn("impl Client {", skel)
        self.assertIn("pub fn new(url: String) -> Self { /* ... */ }", skel)
        self.assertIn("pub async fn send(&self) -> Result<(), Error> { /* ... */ }", skel)
        self.assertNotIn("panic!(\"empty\")", skel)

    def test_java_csharp_skeletonization(self):
        java_code = """
public class AccountManager {
    private final Logger log;

    public AccountManager(Logger log) {
        this.log = log;
    }

    public async Task<Account> FindAccount(String id) throws Exception {
        if (id == null) {
            throw new Exception("null id");
        }
        return db.get(id);
    }
}
"""
        skel = Skeletonizer.skeletonize_code(java_code, ".java")
        self.assertIn("public class AccountManager {", skel)
        self.assertIn("public AccountManager(Logger log) { /* ... */ }", skel)
        self.assertIn("public async Task<Account> FindAccount(String id) throws Exception { /* ... */ }", skel)
        self.assertNotIn("throw new Exception", skel)

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
