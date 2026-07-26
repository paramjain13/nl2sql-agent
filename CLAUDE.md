# CLAUDE.md — Project context for Claude Code

## What this project is
A **text-to-SQL agent** built on **LangGraph** that answers natural-language questions
over relational databases by generating and self-correcting SQL. Benchmarked on **BIRD-SQL**.

## Goals (in priority order)
1. **Resume/interview project** — the architecture and evaluation must be defensible in interviews.
2. **Publishable workshop result** — efficiency-Pareto + ablation study, NOT beating SOTA.

## Non-negotiable design rules
- **Eval harness is built and validated FIRST.** No feature work until we can measure Execution Accuracy (EX).
- **Every change is measured.** Log EX and #LLM-calls before/after each node is added.
- **The human (Param) owns architecture decisions.** Claude Code implements, debugs, and runs experiments — it does NOT silently redesign the agent. If you think a design choice is wrong, explain why and ask.
- **Iterate on Mini-Dev (500 examples), report on full dev.** Keep API costs low.
- **Generated SQL is read-only.** Never allow INSERT/UPDATE/DELETE/DROP in executed SQL.

## Architecture (LangGraph state machine)
Nodes, in order, sharing one `AgentState`:
1. `schema_linker`   — pick only relevant tables/columns (biggest accuracy lever)
2. `sql_generator`   — Claude drafts SQL from linked schema + BIRD evidence
3. `executor`        — run SQL on SQLite (no LLM), capture result or error
4. `validator`       — cheap checks; route to done or self-correct
5. `self_corrector`  — Claude rewrites SQL using the execution error (loop, max N tries)

## Metrics
- **EX (Execution Accuracy):** predicted-SQL result set == gold-SQL result set.
- **#LLM calls / question:** the efficiency axis. This is our differentiator.
- **R-VES:** BIRD's efficiency-weighted score (add later).

## Commands
- `python run_baseline.py --split mini`  → v0 single-call baseline
- `python run_agent.py --split mini`     → full agent
- Both write predictions + print EX via `eval/harness.py`.

## Current status
Scaffold stage. First job: get BIRD Mini-Dev loading, run `run_baseline.py`, get the v0 EX number.
