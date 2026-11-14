# TokenSafe Real-Repo Benchmark (measured)

> Pre-registered real-repo benchmark. Scenarios are committed BEFORE any measurement (anti cherry-picking; v1 scenarios measured in 784628d remain untouched). All token counts are measured with the calibrated BPE tokenizer on real repository bytes. Every scenario carries deterministic zero-loss gates; a gate failure disqualifies the scenario from the savings table. v2 adds four angles: architecture discovery, raw search leak, git-diff churn, write-egress (output-token axis).

| Repo | Scenario | Baseline tok | Optimized tok | Saved | % | USD (Sonnet input) | Gates |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| flask | symbol_read | 18,864 | 3,784 | 15,080 | 79.9% | $0.0452 | PASS |
| flask | test_output | 6,402 | 6,191 | 211 | 3.3% | $0.0006 | PASS |
| flask | multiturn | 45,336 | 5,304 | 40,032 | 88.3% | $0.1201 | PASS |
| flask | architecture | 53,237 | 176 | 53,061 | 99.7% | $0.1592 | PASS |
| flask | raw_search | 7,967 | 523 | 7,444 | 93.4% | $0.0223 | PASS |
| flask | git_diff | 2,456 | 2,236 | 220 | 9.0% | $0.0007 | PASS |
| flask | write_egress | 7,106 | 44 | 7,062 | 99.4% | $0.1059 | PASS |
| requests | symbol_read | 9,743 | 2,849 | 6,894 | 70.8% | $0.0207 | PASS |
| requests | test_output | 43,130 | 42,725 | 405 | 0.9% | $0.0012 | PASS |
| requests | multiturn | 59,380 | 9,561 | 49,819 | 83.9% | $0.1495 | PASS |
| requests | architecture | 46,841 | 180 | 46,661 | 99.6% | $0.14 | PASS |
| requests | raw_search | 737 | 614 | 123 | 16.7% | $0.0004 | PASS |
| requests | git_diff | 728 | 704 | 24 | 3.3% | $0.0001 | PASS |
| requests | write_egress | 6,334 | 37 | 6,297 | 99.4% | $0.0945 | PASS |
| fastapi | symbol_read | 73,721 | 39,529 | 34,192 | 46.4% | $0.1026 | PASS |
| fastapi | test_output | 751 | 750 | 1 | 0.1% | $0.0 | FAIL |
| fastapi | multiturn | 147,442 | 39,529 | 107,913 | 73.2% | $0.3237 | PASS |
| fastapi | architecture | 92,002 | 164 | 91,838 | 99.8% | $0.2755 | PASS |
| fastapi | raw_search | 1,343 | 609 | 734 | 54.7% | $0.0022 | PASS |
| fastapi | git_diff | 416 | 393 | 23 | 5.5% | $0.0001 | PASS |
| fastapi | write_egress | 2,258 | 51 | 2,207 | 97.7% | $0.0331 | PASS |
| rich | symbol_read | 28,675 | 7,710 | 20,965 | 73.1% | $0.0629 | PASS |
| rich | test_output | 746 | 385 | 361 | 48.4% | $0.0011 | PASS |
| rich | multiturn | 80,036 | 12,329 | 67,707 | 84.6% | $0.2031 | PASS |
| rich | architecture | 76,172 | 158 | 76,014 | 99.8% | $0.228 | PASS |
| rich | raw_search | 3,658 | 502 | 3,156 | 86.3% | $0.0095 | PASS |
| rich | git_diff | 91,307 | 88,133 | 3,174 | 3.5% | $0.0095 | PASS |
| rich | write_egress | 8,159 | 32 | 8,127 | 99.6% | $0.1219 | PASS |
| httpie | symbol_read | 2,933 | 689 | 2,244 | 76.5% | $0.0067 | PASS |
| httpie | test_output | 20,395 | 20,141 | 254 | 1.2% | $0.0008 | PASS |
| httpie | multiturn | 11,476 | 1,767 | 9,709 | 84.6% | $0.0291 | PASS |
| httpie | architecture | 19,020 | 165 | 18,855 | 99.1% | $0.0566 | PASS |
| httpie | raw_search | 9,568 | 462 | 9,106 | 95.2% | $0.0273 | PASS |
| httpie | git_diff | 131 | 138 | 0 | 0.0% | $0.0 | PASS |
| httpie | write_egress | 4,020 | 54 | 3,966 | 98.7% | $0.0595 | PASS |
