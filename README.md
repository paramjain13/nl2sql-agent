# NL→SQL Agent (LangGraph + Claude)

A text-to-SQL agent that answers natural-language questions over relational
databases by generating and **self-correcting** SQL. Built on **LangGraph**,
powered by the **Claude API**, benchmarked on **BIRD-SQL**.

The design goal is not to beat SOTA — it's to hit strong accuracy at a **fraction
of the LLM calls**, and to show (via ablation) **which agent components actually
matter**.

## Architecture

```
question → schema_linker → sql_generator → executor → validator ─┬─→ finalize
                                              ▲                    │
                                              └── self_corrector ◀─┘  (loop, max N)
```

| Node | Job | Model |
|------|-----|-------|
| schema_linker | keep only relevant tables/columns | cheap |
| sql_generator | draft SQL from linked schema + evidence | strong |
| executor | run SQL on SQLite (no LLM) | — |
| validator | route: done or self-correct | — |
| self_corrector | fix SQL using the execution error | strong |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add your ANTHROPIC_API_KEY
```

### Get the data
Download BIRD dev + Mini-Dev from the official BIRD-bench site and unzip so that:
```
data/mini_dev/mini_dev.json
data/mini_dev/dev_databases/{db_id}/{db_id}.sqlite
data/dev/dev.json
data/dev/dev_databases/{db_id}/{db_id}.sqlite
```
(If the downloaded field/dir names differ, update `config.py`. Dev gold SQL is
under the `SQL` key; the harness also falls back to `query`.)

## Run

```bash
# smoke test on 10 examples first (cheap)
python run_baseline.py --split mini --limit 10

# full v0 baseline (your CONTROL number)
python run_baseline.py --split mini

# the full agent
python run_agent.py --split mini
```

## Results (fill in as you go)

| System | Split | EX | LLM calls / q |
|--------|-------|-----|---------------|
| v0 baseline (single call) | mini | 64.4% | 1.0 |
| + schema linking | mini | _ | _ |
| + self-correction | mini | _ | _ |
| **full agent** | dev | _ | _ |

v0 baseline per-difficulty breakdown (mini, 500 examples, our CONTROL number):

| Difficulty | n | EX |
|------------|-----|-----|
| simple | 148 | 75.0% |
| moderate | 250 | 62.8% |
| challenging | 102 | 52.9% |

Reference: human ≈ 92.96% EX · current SOTA ≈ 81–82% EX (heavy test-time scaling).
Your neighborhood as a lean single-agent: ~55–72% EX.

## Ablation (the paper's centerpiece)

Turn each node off, re-run, record the EX drop:

```bash
# e.g. disable schema linking by editing graph.py entry point,
# or gate nodes behind a flag — Claude Code can add an --ablate flag.
```

| Configuration | EX | Δ vs full |
|---------------|-----|-----------|
| full agent | _ | — |
| − schema linking | _ | _ |
| − self-correction | _ | _ |
| − evidence in prompts | _ | _ |

## Notes
- Executed SQL is **read-only** (mutations are blocked in `agent/db.py`).
- Iterate on Mini-Dev (500) to keep cost low; report final numbers on full dev.
- For leaderboard-exact EX, swap `eval/harness.py`'s comparator for BIRD's
  official evaluator.
```
