"""The five LangGraph nodes. Each returns a partial state update (a dict)."""
import config
from agent import prompts
from agent.db import execute_sql
from agent.llm import call_claude, extract_sql
from agent.state import AgentState


def schema_linker(state: AgentState) -> dict:
    """Node 1: reduce the full schema to only the relevant tables/columns.

    Biggest single accuracy lever on BIRD — schemas are large and noisy.
    Uses the CHEAP model on purpose (part of the efficiency story).
    """
    prompt = prompts.SCHEMA_LINKER.format(
        full_schema=state["full_schema"],
        question=state["question"],
        evidence=state.get("evidence", "") or "(none)",
    )
    linked = call_claude(prompt, model=config.MODEL_CHEAP)
    return {
        "linked_schema": linked or state["full_schema"],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def sql_generator(state: AgentState) -> dict:
    """Node 2: draft SQL from the linked schema + evidence. STRONG model."""
    prompt = prompts.SQL_GENERATOR.format(
        schema=state.get("linked_schema") or state["full_schema"],
        evidence=state.get("evidence", "") or "(none)",
        question=state["question"],
    )
    raw = call_claude(prompt, model=config.MODEL_STRONG)
    return {
        "candidate_sql": extract_sql(raw),
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def executor(state: AgentState) -> dict:
    """Node 3: run the candidate SQL. No LLM. Captures result or error."""
    rows, error = execute_sql(state["db_path"], state["candidate_sql"])
    return {"exec_result": rows, "error": error or None}


def validator(state: AgentState) -> dict:
    """Node 4: cheap checks. Just bumps attempts; routing logic is in graph.py.

    Kept as a node (not just an edge) so you can add smarter semantic checks
    later without touching the graph wiring — good ablation surface.
    """
    return {"attempts": state.get("attempts", 0) + 1}


def self_corrector(state: AgentState) -> dict:
    """Node 5: rewrite the SQL using the execution error. STRONG model."""
    prompt = prompts.SELF_CORRECTOR.format(
        schema=state.get("linked_schema") or state["full_schema"],
        evidence=state.get("evidence", "") or "(none)",
        question=state["question"],
        sql=state["candidate_sql"],
        error=state.get("error") or "Result looked wrong (empty or unexpected).",
    )
    raw = call_claude(prompt, model=config.MODEL_STRONG)
    return {
        "candidate_sql": extract_sql(raw),
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def finalize(state: AgentState) -> dict:
    """Terminal node: commit the current candidate as the final answer."""
    return {"final_sql": state.get("candidate_sql", "")}
