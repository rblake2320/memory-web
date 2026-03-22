"""
MemoryWeb Recall Benchmark Runner (Phase 2b)

Measures end-to-end retrieval recall so improvements are provable.

Usage:
    cd /d/memory-web
    python -m tests.benchmark_recall                  # run all test cases
    python -m tests.benchmark_recall --tier 3         # test only Tier 3
    python -m tests.benchmark_recall --baseline       # save as baseline.json
    python -m tests.benchmark_recall --compare baseline.json  # diff against baseline

Metrics:
    Recall@5  - % of expected facts found in top 5 results
    Recall@10 - % of expected facts found in top 10 results
    MRR       - Mean Reciprocal Rank (how high correct answers rank)

Output: JSON report to stdout + recall_benchmark_results.json
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make app importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

BASE_URL = "http://localhost:8100"
TIMEOUT = 30.0


def _search(query: str, k: int = 10, force_tier: Optional[int] = None) -> List[Dict]:
    """Call /api/search and return results list."""
    payload: Dict[str, Any] = {"query": query, "k": k}
    if force_tier is not None:
        payload["force_tier"] = force_tier
    try:
        resp = httpx.post(f"{BASE_URL}/api/search", json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"  WARN: search failed for '{query[:50]}': {e}", file=sys.stderr)
        return []


def _score_result(
    results: List[Dict],
    expected_facts: List[str],
    k_cutoff: int,
) -> tuple:
    """
    Returns (found_at_k, first_rank) where:
      found_at_k: set of expected facts found in top-k results
      first_rank: lowest rank (1-indexed) where any expected fact was found, or None
    """
    found = set()
    first_rank = None
    result_contents = [r.get("content", "").lower() for r in results[:k_cutoff]]

    for expected in expected_facts:
        expected_lower = expected.lower()
        for rank, content in enumerate(result_contents, start=1):
            # Partial match: expected fact as substring of result content
            if expected_lower in content or content in expected_lower:
                found.add(expected)
                if first_rank is None or rank < first_rank:
                    first_rank = rank
                break

    return found, first_rank


def run_benchmark(
    test_cases: List[Dict],
    force_tier: Optional[int] = None,
    k_vals: List[int] = None,
) -> Dict:
    """
    Run all test cases, return aggregate metrics dict.

    test_cases format:
    [
      {
        "query": "What port does PostgreSQL run on?",
        "expected_facts": ["PostgreSQL runs on port 5432"],
        "category": "configuration",
        "notes": "Basic config recall"
      },
      ...
    ]
    """
    if k_vals is None:
        k_vals = [5, 10]

    max_k = max(k_vals)
    per_query = []
    recall_totals = {k: 0 for k in k_vals}
    mrr_total = 0.0
    n = len(test_cases)

    print(f"\nRunning {n} test cases (force_tier={force_tier or 'cascade'})...")
    print("-" * 60)

    for i, tc in enumerate(test_cases):
        query = tc["query"]
        expected = tc.get("expected_facts", [])
        category = tc.get("category", "unknown")

        t0 = time.monotonic()
        results = _search(query, k=max_k, force_tier=force_tier)
        latency_ms = (time.monotonic() - t0) * 1000

        result_record: Dict[str, Any] = {
            "query": query,
            "category": category,
            "expected_facts": expected,
            "latency_ms": round(latency_ms, 1),
            "top3_results": [r.get("content", "")[:100] for r in results[:3]],
        }

        # Compute recall@k and MRR
        rr = 0.0
        for k in k_vals:
            found, first_rank = _score_result(results, expected, k)
            recall_at_k = len(found) / max(len(expected), 1)
            result_record[f"recall@{k}"] = round(recall_at_k, 3)
            recall_totals[k] += recall_at_k
            if k == max_k and first_rank is not None:
                rr = 1.0 / first_rank

        mrr_total += rr
        result_record["reciprocal_rank"] = round(rr, 4)

        per_query.append(result_record)

        status = "+" if result_record.get(f"recall@{k_vals[0]}", 0) > 0 else "-"
        print(f"  [{i+1:2d}/{n}] {status} R@5={result_record.get('recall@5', 0):.2f} "
              f"R@10={result_record.get('recall@10', 0):.2f} "
              f"RR={rr:.3f} [{category:15s}] {query[:50]}")

    # Aggregate
    aggregate = {
        "timestamp": datetime.utcnow().isoformat(),
        "force_tier": force_tier,
        "n_test_cases": n,
        "metrics": {},
    }
    for k in k_vals:
        aggregate["metrics"][f"recall@{k}"] = round(recall_totals[k] / n, 4) if n else 0.0
    aggregate["metrics"]["mrr"] = round(mrr_total / n, 4) if n else 0.0

    print("-" * 60)
    print(f"\nAggregate metrics ({n} test cases):")
    for metric, val in aggregate["metrics"].items():
        print(f"  {metric}: {val:.3f} ({val*100:.1f}%)")

    return {
        "aggregate": aggregate,
        "per_query": per_query,
    }


def compare_to_baseline(current: Dict, baseline_path: str) -> None:
    """Print diff between current results and a saved baseline."""
    try:
        with open(baseline_path) as f:
            baseline = json.load(f)
    except Exception as e:
        print(f"Could not load baseline: {e}")
        return

    print("\n=== Comparison to baseline ===")
    curr_agg = current["aggregate"]["metrics"]
    base_agg = baseline["aggregate"]["metrics"]

    for metric in curr_agg:
        curr_val = curr_agg.get(metric, 0)
        base_val = base_agg.get(metric, 0)
        delta = curr_val - base_val
        sign = "+" if delta >= 0 else ""
        print(f"  {metric}: {base_val:.3f} → {curr_val:.3f}  ({sign}{delta:+.3f})")


def main():
    parser = argparse.ArgumentParser(description="MemoryWeb recall benchmark")
    parser.add_argument("--tier", type=int, default=None, choices=[1, 2, 3],
                        help="Force a specific retrieval tier")
    parser.add_argument("--baseline", action="store_true",
                        help="Save results as baseline.json")
    parser.add_argument("--compare", type=str, default=None,
                        help="Compare results to this baseline file")
    parser.add_argument("--cases", type=str, default=None,
                        help="Path to custom test cases JSON file")
    args = parser.parse_args()

    # Load test cases
    if args.cases:
        with open(args.cases) as f:
            test_cases = json.load(f)
    else:
        # Default: import from test_recall_benchmark.py
        from test_recall_benchmark import GOLDEN_TEST_CASES
        test_cases = GOLDEN_TEST_CASES

    results = run_benchmark(test_cases, force_tier=args.tier)

    # Save results
    output_path = Path(__file__).parent / "recall_benchmark_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")

    if args.baseline:
        baseline_path = Path(__file__).parent / "recall_benchmark_baseline.json"
        with open(baseline_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Baseline saved to: {baseline_path}")

    if args.compare:
        compare_to_baseline(results, args.compare)


if __name__ == "__main__":
    main()
