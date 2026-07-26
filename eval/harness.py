"""Evaluation harness: load BIRD data, compute Execution Accuracy (EX).

BUILD AND TRUST THIS BEFORE ANY FEATURE WORK. You can't improve what you can't
measure. EX = predicted-SQL result set equals gold-SQL result set.
"""
import json
import os
from typing import Any, Dict, List

import config
from agent.db import execute_sql, get_full_schema


def load_split(split: str) -> List[Dict[str, Any]]:
    """Load a BIRD split (list of examples) from config.SPLITS."""
    path = config.SPLITS[split]
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_db_path(split: str, db_id: str) -> str:
    """Path to a database's .sqlite file for a given split."""
    return os.path.join(config.DB_ROOT[split], db_id, f"{db_id}.sqlite")


def result_set(rows: List[Any]) -> frozenset:
    """Order-independent comparison key for a result set.

    BIRD's official EX compares result sets. We normalize each row to a tuple
    of stringified cells so int/float/str mismatches don't cause false negatives
    on otherwise-correct answers. (Swap in the official evaluator later if you
    want leaderboard-exact numbers.)
    """
    return frozenset(tuple(str(c) for c in row) for row in rows)


def is_correct(db_path: str, pred_sql: str, gold_sql: str) -> bool:
    """EX: do predicted and gold SQL produce the same result set?"""
    pred_rows, pred_err = execute_sql(db_path, pred_sql)
    if pred_err:
        return False
    gold_rows, gold_err = execute_sql(db_path, gold_sql)
    if gold_err:
        # gold failing means a data/setup issue; don't credit or blame the model
        return False
    return result_set(pred_rows) == result_set(gold_rows)


def gold_sql_of(example: Dict[str, Any]) -> str:
    """BIRD stores gold SQL under 'SQL' (dev) — fall back to 'query'."""
    return example.get("SQL") or example.get("query") or ""


def evaluate(predictions: List[str], examples: List[Dict[str, Any]], split: str) -> Dict[str, Any]:
    """Compute EX over a list of predicted SQL strings aligned with examples."""
    assert len(predictions) == len(examples), "predictions/examples length mismatch"
    correct = 0
    by_difficulty: Dict[str, List[int]] = {}

    for pred, ex in zip(predictions, examples):
        db_path = resolve_db_path(split, ex["db_id"])
        ok = is_correct(db_path, pred, gold_sql_of(ex))
        correct += int(ok)
        diff = ex.get("difficulty", "unknown")
        by_difficulty.setdefault(diff, []).append(int(ok))

    ex_score = correct / len(examples) if examples else 0.0
    diff_breakdown = {
        d: {"n": len(v), "ex": sum(v) / len(v)} for d, v in by_difficulty.items()
    }
    return {
        "EX": ex_score,
        "correct": correct,
        "total": len(examples),
        "by_difficulty": diff_breakdown,
    }
