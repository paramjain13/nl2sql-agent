"""Shared state passed between LangGraph nodes."""
from typing import Any, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # --- inputs ---
    question: str            # the natural-language question
    evidence: str            # BIRD "external knowledge" hint (may be empty)
    db_id: str               # which database
    db_path: str             # resolved path to the .sqlite file
    full_schema: str         # CREATE TABLE dump of the whole DB

    # --- intermediate ---
    linked_schema: str       # schema_linker output: only relevant tables/cols
    candidate_sql: str       # sql_generator / self_corrector output
    exec_result: Any         # rows returned, or None
    error: Optional[str]     # execution error message, or None
    attempts: int            # self-correction attempts so far

    # --- bookkeeping (for the efficiency metric) ---
    llm_calls: int           # count of Claude API calls this question used

    # --- output ---
    final_sql: str
