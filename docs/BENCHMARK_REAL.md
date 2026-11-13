# TokenSafe Real-Repo Benchmark (measured)

> Pre-registered real-repo benchmark. Scenarios are committed BEFORE any measurement (anti cherry-picking; v1 scenarios measured in 784628d remain untouched). All token counts are measured with the calibrated BPE tokenizer on real repository bytes. Every scenario carries deterministic zero-loss gates; a gate failure disqualifies the scenario from the savings table. v2 adds four angles: architecture discovery, raw search leak, git-diff churn, write-egress (output-token axis).

| Repo | Scenario | Baseline tok | Optimized tok | Saved | % | USD (Sonnet input) | Gates |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| flask | symbol_read | 18,864 | 5,506 | 13,358 | 70.8% | $0.0401 | PASS |
| flask | test_output | 6,405 | 6,405 | 0 | 0.0% | $0.0 | PASS |
| flask | multiturn | 45,336 | 7,628 | 37,708 | 83.2% | $0.1131 | PASS |
| flask | architecture | 53,237 | 176 | 53,061 | 99.7% | $0.1592 | PASS |
| flask | raw_search | 7,967 | 523 | 7,444 | 93.4% | $0.0223 | PASS |
| flask | git_diff | 2,456 | 2,236 | 220 | 9.0% | $0.0007 | PASS |
| flask | write_egress | 7,106 | 44 | 7,062 | 99.4% | $0.1059 | PASS |
| requests | symbol_read | 9,743 | 4,657 | 5,086 | 52.2% | $0.0153 | PASS |
| requests | test_output | 69 | 69 | 0 | 0.0% | $0.0 | FAIL |
| requests | multiturn | 59,380 | 13,413 | 45,967 | 77.4% | $0.1379 | PASS |
| requests | architecture | 46,841 | 180 | 46,661 | 99.6% | $0.14 | PASS |
| requests | raw_search | 737 | 614 | 123 | 16.7% | $0.0004 | PASS |
| requests | git_diff | 728 | 704 | 24 | 3.3% | $0.0001 | PASS |
| requests | write_egress | 6,334 | 37 | 6,297 | 99.4% | $0.0945 | PASS |
| fastapi | symbol_read | 73,721 | 39,420 | 34,301 | 46.5% | $0.1029 | PASS |
| fastapi | test_output | 82 | 82 | 0 | 0.0% | $0.0 | FAIL |
| fastapi | multiturn | 147,442 | 39,420 | 108,022 | 73.3% | $0.3241 | PASS |
| fastapi | architecture | 92,002 | 164 | 91,838 | 99.8% | $0.2755 | PASS |
| fastapi | raw_search | 1,343 | 609 | 734 | 54.7% | $0.0022 | PASS |
| fastapi | git_diff | 416 | 393 | 23 | 5.5% | $0.0001 | PASS |
| fastapi | write_egress | 2,258 | 51 | 2,207 | 97.7% | $0.0331 | PASS |
| rich | symbol_read | 28,675 | 9,444 | 19,231 | 67.1% | $0.0577 | PASS |
| rich | test_output | 1,018 | 1,018 | 0 | 0.0% | $0.0 | FAIL |
| rich | multiturn | 80,036 | 15,435 | 64,601 | 80.7% | $0.1938 | PASS |
| rich | architecture | 76,172 | 158 | 76,014 | 99.8% | $0.228 | PASS |
| rich | raw_search | 3,658 | 502 | 3,156 | 86.3% | $0.0095 | PASS |
| rich | git_diff | 91,307 | 88,133 | 3,174 | 3.5% | $0.0095 | PASS |
| rich | write_egress | 8,159 | 32 | 8,127 | 99.6% | $0.1219 | PASS |
| httpie | test_output | 66 | 66 | 0 | 0.0% | $0.0 | FAIL |
| httpie | multiturn | 5,610 | 1,115 | 4,495 | 80.1% | $0.0135 | PASS |
| httpie | architecture | 19,020 | 165 | 18,855 | 99.1% | $0.0566 | PASS |
| httpie | raw_search | 9,568 | 462 | 9,106 | 95.2% | $0.0273 | PASS |
| httpie | git_diff | 131 | 138 | 0 | 0.0% | $0.0 | PASS |
| httpie | write_egress | 4,020 | 54 | 3,966 | 98.7% | $0.0595 | PASS |
