"""Run the FULL LangGraph agent over a split and report EX + efficiency.

    python run_agent.py --split mini

Compare its EX and llm_calls_per_q against run_baseline.py. The gap is your result.
"""
import argparse
import json

import config
from agent.db import get_full_schema
from agent.graph import build_agent
from eval.harness import evaluate, load_split, resolve_db_path

from tqdm import tqdm


def run_agent(split: str, limit: int | None = None):
    examples = load_split(split)
    if limit:
        examples = examples[:limit]

    agent = build_agent()
    predictions = []
    total_calls = 0

    for ex in tqdm(examples, desc="agent"):
        db_path = resolve_db_path(split, ex["db_id"])
        init_state = {
            "question": ex["question"],
            "evidence": ex.get("evidence", ""),
            "db_id": ex["db_id"],
            "db_path": db_path,
            "full_schema": get_full_schema(db_path),
            "attempts": 0,
            "llm_calls": 0,
        }
        final = agent.invoke(init_state)
        predictions.append(final.get("final_sql", ""))
        total_calls += final.get("llm_calls", 0)

    results = evaluate(predictions, examples, split)
    results["llm_calls_per_q"] = total_calls / len(examples) if examples else 0.0

    print("\n=== FULL AGENT RESULTS ===")
    print(json.dumps(results, indent=2))
    with open(f"predictions_agent_{split}.json", "w") as f:
        json.dump({"predictions": predictions, "results": results}, f, indent=2)
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="mini", choices=list(config.SPLITS.keys()))
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    run_agent(args.split, args.limit)
