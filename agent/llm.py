"""Thin wrapper around the Claude API (the LLM the agent calls at runtime)."""
from anthropic import Anthropic

import config

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def call_claude(prompt: str, model: str, system: str = "") -> str:
    """Single-turn Claude call. Returns the text content.

    Note: this is the agent's *runtime* LLM. It is NOT Claude Code (which builds
    this repo). Every call here counts toward the efficiency metric.
    """
    resp = _client.messages.create(
        model=model,
        max_tokens=config.MAX_TOKENS,
        system=system or "You are a precise text-to-SQL assistant.",
        messages=[{"role": "user", "content": prompt}],
    )
    # Concatenate any text blocks in the response.
    return "".join(block.text for block in resp.content if block.type == "text").strip()


def extract_sql(text: str) -> str:
    """Pull SQL out of a model response that may wrap it in ```sql fences."""
    t = text.strip()
    if "```" in t:
        # take the content of the first fenced block
        parts = t.split("```")
        block = parts[1] if len(parts) > 1 else t
        if block.lower().startswith("sql"):
            block = block[3:]
        return block.strip().rstrip(";").strip()
    return t.rstrip(";").strip()
