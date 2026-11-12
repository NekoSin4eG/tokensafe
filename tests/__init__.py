import os

# Deterministic test signing keypair for unit tests (isolated from production keys)
os.environ.setdefault("TOKENSAFE_PRIVATE_KEY", "db13e3ddc4a9856417ad2236b2a4e96cf62b5a825dd044409b3f693bc77ff700")
os.environ.setdefault("TOKENSAFE_PUBLIC_KEY", "8a2c9d550180ab5b00a06174cb6aecfd712740c59d0bdcff66a56515348c855e")

# Bind the test keypair inside the unittest harness only (runtime LicenseManager ignores env public keys)
from src import license_manager as _ts_test_lm
_ts_test_lm.DEFAULT_ED25519_PUBLIC_KEY = os.environ["TOKENSAFE_PUBLIC_KEY"]
