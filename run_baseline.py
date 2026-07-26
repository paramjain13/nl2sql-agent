"""v0 BASELINE: one Claude call per question. This is your CONTROL.

Run this first. The EX number it prints is the bar every later feature must beat.

    python run_baseline.py --split mini
"""
import argparse
import json

import config
from agent import prompts
from agent.db import get_full_schema
from agent.llm import call_claude, extract_sql
from eval.harness import evaluate, load_split, resolve_db_path

from tqdm import tqdm


def run_baseline(split: str, limit: int | None = None):
    examples = load_split(split)
    if limit:
        examples = examples[:limit]

    predictions = []
    for ex in tqdm(examples, desc="v0 baseline"):
        db_path = resolve_db_path(split, ex["db_id"])
        schema = get_full_schema(db_path)
        prompt = prompts.SQL_GENERATOR.format(
            schema=schema,
            evidence=ex.get("evidence", "") or "(none)",
            question=ex["question"],
        )
        raw = call_claude(prompt, model=config.MODEL_STRONG)
        predictions.append(extract_sql(raw))

    results = evaluate(predictions, examples, split)
    results["llm_calls_per_q"] = 1.0  # by definition for v0

    print("\n=== v0 BASELINE RESULTS ===")
    print(json.dumps(results, indent=2))
    with open(f"predictions_baseline_{split}.json", "w") as f:
        json.dump({"predictions": predictions, "results": results}, f, indent=2)
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="mini", choices=list(config.SPLITS.keys()))
    p.add_argument("--limit", type=int, default=None, help="cap #examples (for a quick smoke test)")
    args = p.parse_args()
    run_baseline(args.split, args.limit)
