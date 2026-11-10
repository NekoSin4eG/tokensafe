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

    def test_multiline_arrow_functions_and_ternary_expressions(self):
        code = """
// 1. Ternary boolean expression in parentheses must NOT be treated as a function
const gramBalanceDeduct = (useBalanceForGram && userBalance > 0)
  ? Math.min(userBalance, totalCost)
  : 0;

// 2. Ternary returning object literal must NOT fold object into /* ... */
const serverConfig = (isProd && isReady)
  ? { host: 'api.prod.com', port: 443 }
  : { host: 'api.dev.com', port: 8080 };

// 3. Arrow function with return type annotation
const createPayload = (targetUrl: string, currency?: string): PayloadType => {
  const result = validate(targetUrl);
  return { targetUrl, currency, result };
};

// 4. Arrow function with multi-line destructuring parameters
export const ComplexModal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  userBalance,
}) => {
  const [active, setActive] = useState(false);
  return <div className="modal" />;
};
"""
        # Skeletons all
        skel = Skeletonizer.skeletonize_code(code, ".tsx")
        # Ensure ternary expressions are 100% intact
        self.assertIn("const gramBalanceDeduct = (useBalanceForGram && userBalance > 0)", skel)
        self.assertIn("? Math.min(userBalance, totalCost)", skel)
        self.assertIn("const serverConfig = (isProd && isReady)", skel)
        self.assertIn("{ host: 'api.prod.com', port: 443 }", skel)

        # Ensure arrow functions are folded
        self.assertIn("const createPayload = (targetUrl: string, currency?: string): PayloadType => { /* ... */ };", skel)
        self.assertIn("export const ComplexModal: React.FC<ModalProps> = ({", skel)
        self.assertIn("}) => { /* ... */ };", skel)
        self.assertNotIn("const [active, setActive] = useState", skel)

    def test_css_scss_less_design_tokens(self):
        css_code = """
:root {
  --primary-color: #3b82f6;
  --secondary-color: #10b981;
  --font-base: 'Inter', sans-serif;
}

@theme {
  --color-brand: #6366f1;
  --spacing-header: 4rem;
}

$scss-primary: #3b82f6;

.btn-primary {
  background-color: var(--primary-color);
  color: #ffffff;
  padding: 8px 16px;
  border-radius: 4px;
}

.btn-secondary {
  background-color: var(--secondary-color);
  color: #ffffff;
}

@media (max-width: 768px) {
  .btn-primary {
    width: 100%;
  }
  .sidebar {
    display: none;
  }
}
"""
        # Whole-file skeleton: design tokens (:root, @theme) must be preserved in full
        skel_all = Skeletonizer.skeletonize_code(css_code, ".css")
        self.assertIn(":root {", skel_all)
        self.assertIn("--primary-color: #3b82f6;", skel_all)
        self.assertIn("@theme {", skel_all)
        self.assertIn("--color-brand: #6366f1;", skel_all)
        self.assertIn(".btn-primary { /* ... */ }", skel_all)
        self.assertIn(".btn-secondary { /* ... */ }", skel_all)
        self.assertIn("@media (max-width: 768px) {", skel_all)
        self.assertIn(".sidebar { /* ... */ }", skel_all)

        # Graph-guided symbol skeleton: target .btn-primary preserved, .btn-secondary folded
        skel_target = Skeletonizer.skeletonize_code(css_code, ".css", keep_symbols={"btn-primary"})
        self.assertIn("padding: 8px 16px;", skel_target)
        self.assertIn(".btn-secondary { /* ... */ }", skel_target)

    def test_python_folded_functions_docstrings_stripped(self):
        py_code = '''
def target_fn():
    """Keep this docstring."""
    return 42

def inert_fn():
    """This docstring must be stripped."""
    do_something()
'''
        skel = Skeletonizer.skeletonize_code(py_code, ".py", keep_symbols={"target_fn"})
        self.assertIn("Keep this docstring.", skel)
        self.assertNotIn("This docstring must be stripped.", skel)
        self.assertIn("def inert_fn():\n    ...", skel)

    def test_include_deps_flag_in_graph_engine(self):
        from src.graph_engine import GraphEngine
        graph, _ = GraphEngine.for_file("src/guard.py")
        skel_deps = graph.graph_guided_skeletonize("src/guard.py", "inspect_command", include_deps=True)
        skel_no_deps = graph.graph_guided_skeletonize("src/guard.py", "inspect_command", include_deps=False)
        self.assertLess(len(skel_no_deps.splitlines()), len(skel_deps.splitlines()))
        self.assertIn("def _check_raw_code_search", skel_no_deps)
        self.assertIn("def _check_raw_code_search", skel_deps)

if __name__ == "__main__":
    unittest.main()
