# TokenSafe Real-Repo Benchmark (measured)

> Pre-registered real-repo benchmark. Scenarios are committed BEFORE any measurement (anti cherry-picking). All token counts are measured with the calibrated BPE tokenizer on real repository bytes. Every scenario carries deterministic zero-loss gates; a gate failure disqualifies the scenario from the savings table.

| Repo | Scenario | Baseline tok | Optimized tok | Saved | % | USD (Sonnet input) | Gates |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| flask | symbol_read | 18,864 | 5,506 | 13,358 | 70.8% | $0.0401 | PASS |
| flask | test_output | 6,391 | 6,391 | 0 | 0.0% | $0.0 | PASS |
| flask | multiturn | 45,336 | 7,628 | 37,708 | 83.2% | $0.1131 | PASS |
| requests | symbol_read | 9,743 | 4,657 | 5,086 | 52.2% | $0.0153 | PASS |
| requests | test_output | 69 | 69 | 0 | 0.0% | $0.0 | FAIL |
| requests | multiturn | 59,380 | 13,413 | 45,967 | 77.4% | $0.1379 | PASS |
| fastapi | symbol_read | 73,721 | 39,420 | 34,301 | 46.5% | $0.1029 | PASS |
| fastapi | test_output | 82 | 82 | 0 | 0.0% | $0.0 | FAIL |
| fastapi | multiturn | 147,442 | 39,420 | 108,022 | 73.3% | $0.3241 | PASS |
| rich | symbol_read | 28,675 | 9,444 | 19,231 | 67.1% | $0.0577 | PASS |
| rich | test_output | 1,018 | 1,018 | 0 | 0.0% | $0.0 | FAIL |
| rich | multiturn | 80,036 | 15,435 | 64,601 | 80.7% | $0.1938 | PASS |
| httpie | test_output | 66 | 66 | 0 | 0.0% | $0.0 | FAIL |
| httpie | multiturn | 5,610 | 1,115 | 4,495 | 80.1% | $0.0135 | PASS |
