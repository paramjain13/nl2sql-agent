"""Wire the nodes into a LangGraph state machine with a self-correction loop."""
from langgraph.graph import END, StateGraph

import config
from agent import nodes
from agent.state import AgentState


def _route_after_validation(state: AgentState) -> str:
    """Decide: finish, or loop back to self-correct?

    Loop if there was an execution error AND we still have attempts left.
    (You can make this smarter later — e.g. also loop on empty results.)
    """
    has_error = state.get("error") is not None
    attempts_left = state.get("attempts", 0) < config.MAX_CORRECTION_ATTEMPTS
    if has_error and attempts_left:
        return "self_corrector"
    return "finalize"


def build_agent():
    """Return a compiled LangGraph agent."""
    g = StateGraph(AgentState)

    g.add_node("schema_linker", nodes.schema_linker)
    g.add_node("sql_generator", nodes.sql_generator)
    g.add_node("executor", nodes.executor)
    g.add_node("validator", nodes.validator)
    g.add_node("self_corrector", nodes.self_corrector)
    g.add_node("finalize", nodes.finalize)

    g.set_entry_point("schema_linker")
    g.add_edge("schema_linker", "sql_generator")
    g.add_edge("sql_generator", "executor")
    g.add_edge("executor", "validator")

    # conditional: loop to self_corrector or go to finalize
    g.add_conditional_edges(
        "validator",
        _route_after_validation,
        {"self_corrector": "self_corrector", "finalize": "finalize"},
    )
    # after correcting, re-execute and re-validate
    g.add_edge("self_corrector", "executor")
    g.add_edge("finalize", END)

    return g.compile()
