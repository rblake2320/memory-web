#!/usr/bin/env python3
"""
MemoryWeb context query helper for Codex sessions.

Usage:
    python D:\\memory-web\\mw_query.py "task or project description"

Queries MemoryWeb (local first, Spark-2 fallback) and prints a formatted
context block for the AI to consume before starting work.
"""
import sys
import json
import urllib.request
import urllib.error

LOCAL = "http://localhost:8100"
REMOTE = "https://memoryweb.ultrarag.app"
MAX_K = 8
MIN_SCORE = 0.01


def query(base: str, prompt: str, k: int = MAX_K) -> dict | None:
    try:
        body = json.dumps({"query": prompt[:500], "k": k}).encode()
        req = urllib.request.Request(
            f"{base}/api/search",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def health(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=2) as r:
            return json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def main():
    prompt = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else ""
    if not prompt:
        print("Usage: python mw_query.py <task description>")
        sys.exit(1)

    # Try local first, then Spark-2 tunnel
    data = query(LOCAL, prompt)
    source = LOCAL
    if data is None:
        data = query(REMOTE, prompt)
        source = REMOTE

    if data is None:
        print("<memory_context>\nMemoryWeb: OFFLINE — no memories loaded.\n</memory_context>")
        sys.exit(0)

    results = data.get("results", [])
    good = [r for r in results if r.get("score", 0) >= MIN_SCORE]
    tiers = data.get("tiers_used", [])
    latency = data.get("latency_ms", 0)

    lines = [
        "<memory_context>",
        f"MemoryWeb ({source}): {len(good)} memories (latency {latency:.0f}ms, tiers={tiers}):",
    ]
    for i, r in enumerate(good, 1):
        score = r.get("score", 0)
        tier = r.get("tier", "?")
        content = r.get("content", "")[:250]
        lines.append(f"  [{i}] (score={score:.2f} tier={tier}) {content}")

    if not good:
        lines.append("  No memories matched this query.")

    lines.append("</memory_context>")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
