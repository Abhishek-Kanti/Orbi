# Orbi — EVAL-04: LangGraph + MOD-03 Task Handoff Prototype

Isolated validation prototype for `EVAL-04` (`docs/02-hld/system-architecture.md` Rev 1.4 §C.2, §D.1–D.3, §E.4, §E.5, §L.1–L.2, and `ADR-004`).

## Prerequisites

- Docker & `docker-compose`
- Python 3.12+

## Quickstart

1. Start real PostgreSQL 16:

```bash
docker-compose -f evals/eval-04-langgraph-handoff/docker-compose.yml up -d
```

2. Create a Python 3.12 virtual environment and install dependencies:

```bash
uv --no-config --cache-dir evals/eval-04-langgraph-handoff/.uv-cache venv evals/eval-04-langgraph-handoff/.venv --python 3.12
uv --no-config --cache-dir evals/eval-04-langgraph-handoff/.uv-cache pip install --python evals/eval-04-langgraph-handoff/.venv/Scripts/python.exe -e evals/eval-04-langgraph-handoff
```

3. Run all automated experiments and latency benchmarks:

```bash
evals/eval-04-langgraph-handoff/.venv/Scripts/python.exe -m pytest evals/eval-04-langgraph-handoff/tests -v -s
```

4. Run the interactive walkthrough CLI:

```bash
evals/eval-04-langgraph-handoff/.venv/Scripts/python.exe -m eval04.cli
```

## Outputs

- `benchmark_results.json`: Measured p50/p95/min/max/mean latency (ms) across checkpoint persistence, suspension, resumption, and crash recovery.
- `EVAL-04-REPORT.md`: Detailed scenario test report, empirical measurements, limitations, and architectural decision report.
