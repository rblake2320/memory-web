"""Search operations for the MemoryWeb CLI."""

from typing import Any, Dict, Optional

from .client import MemoryWebClient


def semantic_search(client: MemoryWebClient, query: str, k: int = 10,
                    force_tier: Optional[int] = None,
                    min_tier: int = 1,
                    include_tombstoned: bool = False,
                    include_superseded: bool = False,
                    filters: Optional[Dict] = None) -> Dict:
    """Run a full tiered semantic search (Tier 1 → 2 → 3).

    Args:
        query:              Natural-language search query.
        k:                  Max results to return (1–100).
        force_tier:         Run exactly this tier only (1|2|3). Overrides min_tier.
        min_tier:           Minimum tier to use (1=keyword, 2=fuzzy, 3=vector).
        include_tombstoned: Include deleted memories.
        include_superseded: Include memories invalidated by contradiction detection.
        filters:            Dict of tag-axis filters, e.g. {"domain": "programming"}.
    """
    return client.search(
        query=query,
        k=k,
        force_tier=force_tier,
        min_tier=min_tier,
        include_tombstoned=include_tombstoned,
        include_superseded=include_superseded,
        filters=filters,
    )


def search_by_tag(client: MemoryWebClient, axis: str, value: str, k: int = 10) -> Dict:
    """Search memories by a specific tag axis/value pair.

    Args:
        axis:  Tag axis (domain|intent|sensitivity|importance|project).
        value: Tag value to match.
        k:     Max results.
    """
    return client.search_by_tag(axis=axis, value=value, k=k)


def search_by_entity(client: MemoryWebClient, name: str, k: int = 10) -> Dict:
    """Search memories mentioning a named entity.

    Args:
        name: Entity name (person, project, technology, etc.)
        k:    Max results.
    """
    return client.search_by_entity(name=name, k=k)


def search_by_date(client: MemoryWebClient,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None,
                   query: str = "",
                   k: int = 10) -> Dict:
    """Search memories within a date range.

    Args:
        date_from: ISO date string (YYYY-MM-DD), inclusive start.
        date_to:   ISO date string (YYYY-MM-DD), inclusive end.
        query:     Optional text query to combine with date filter.
        k:         Max results.
    """
    return client.search_by_date(
        date_from=date_from,
        date_to=date_to,
        query=query,
        k=k,
    )


def format_search_results(response: Dict, verbose: bool = False) -> str:
    """Format search results for human-readable display.

    Args:
        response: SearchResponse dict from the API.
        verbose:  If True, include provenance details.

    Returns:
        Formatted string ready to print.
    """
    results = response.get("results", [])
    total = response.get("total", len(results))
    tier_used = response.get("tier_used", "?")

    lines = [f"  Found {total} result(s) via Tier {tier_used}\n"]
    for i, r in enumerate(results, 1):
        score = r.get("score", 0.0)
        content = r.get("content", "")
        rtype = r.get("result_type", "memory")
        rid = r.get("id", "?")
        tier = r.get("tier", "?")
        tombstoned = r.get("tombstoned", False)

        prefix = "[DELETED] " if tombstoned else ""
        lines.append(f"  {i}. [{rtype} #{rid}] (score={score:.3f}, tier={tier})")
        lines.append(f"     {prefix}{content[:200]}")

        if verbose:
            prov = r.get("provenance", [])
            if prov:
                for p in prov[:2]:
                    src = p.get("source_path", "")
                    lines.append(f"     Provenance: {src}")
        lines.append("")

    return "\n".join(lines)
