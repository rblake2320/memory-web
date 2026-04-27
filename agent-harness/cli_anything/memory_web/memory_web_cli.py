"""cli-anything-memory-web — CLI harness for the MemoryWeb personal AI memory system.

Usage:
    cli-anything-memory-web                     # Enter interactive REPL
    cli-anything-memory-web --help              # Show all commands
    cli-anything-memory-web status              # Check server + pipeline health
    cli-anything-memory-web memory list         # List memories
    cli-anything-memory-web search query "..."  # Semantic search
    cli-anything-memory-web ingest session /path/to/session.jsonl

Environment variables:
    MW_BASE_URL   Base URL of the MemoryWeb server (default: http://localhost:8100)
    MW_API_KEY    Optional API key (X-API-Key header)
"""

import json
import os
import sys
from typing import Optional

import click

from .core.client import MemoryWebClient
from .core import memories as mem_ops
from .core import search as search_ops
from .core import ingest as ingest_ops
from .utils.repl_skin import ReplSkin

_VERSION = "1.0.0"


def _make_client(ctx: click.Context) -> MemoryWebClient:
    """Build the HTTP client from context or env."""
    base_url = ctx.obj.get("base_url") if ctx.obj else None
    api_key = ctx.obj.get("api_key") if ctx.obj else None
    return MemoryWebClient(base_url=base_url, api_key=api_key)


def _output(data, as_json: bool):
    """Print data as JSON or human-readable."""
    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        _pretty(data)


def _pretty(data):
    """Simple human-readable printer for dicts/lists."""
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                click.echo(f"  {k}:")
                if isinstance(v, list):
                    for item in v[:20]:
                        click.echo(f"    - {item}")
                else:
                    for kk, vv in v.items():
                        click.echo(f"    {kk}: {vv}")
            else:
                click.echo(f"  {k}: {v}")
    elif isinstance(data, list):
        for item in data[:50]:
            click.echo(f"  - {item}")
    else:
        click.echo(f"  {data}")


# ── Root group ────────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.option("--base-url", envvar="MW_BASE_URL", default="http://localhost:8100",
              help="MemoryWeb server URL [env: MW_BASE_URL]")
@click.option("--api-key", envvar="MW_API_KEY", default="",
              help="API key [env: MW_API_KEY]")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Output as JSON (machine-readable)")
@click.version_option(version=_VERSION, prog_name="cli-anything-memory-web")
@click.pass_context
def cli(ctx: click.Context, base_url: str, api_key: str, as_json: bool):
    """cli-anything-memory-web — Agent-friendly CLI for MemoryWeb.

    MemoryWeb is a provenance-aware tiered AI memory system using
    FastAPI + PostgreSQL + pgvector.

    Run without a subcommand to enter the interactive REPL.
    """
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["api_key"] = api_key
    ctx.obj["as_json"] = as_json

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ── Status commands ───────────────────────────────────────────────────────────

@cli.group()
def status():
    """Server health and pipeline diagnostics."""


@status.command("health")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def status_health(ctx: click.Context, as_json: bool):
    """Quick health check — returns version and status."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = client.health()
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@status.command("full")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def status_full(ctx: click.Context, as_json: bool):
    """Full status: services + DB stats + pipeline health."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = client.status()
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            # Human-readable summary
            services = data.get("services", [])
            stats = data.get("stats", {})
            pipeline = data.get("pipeline_health", {})

            click.echo("\n  Services:")
            for svc in services:
                ok = svc.get("healthy", False)
                name = svc.get("name", "?")
                detail = svc.get("detail", "")
                icon = "OK  " if ok else "FAIL"
                suffix = f" ({detail})" if detail else ""
                click.echo(f"    [{icon}] {name}{suffix}")

            click.echo("\n  Statistics:")
            for k, v in stats.items():
                click.echo(f"    {k}: {v}")

            if pipeline:
                click.echo("\n  Pipeline:")
                for k, v in pipeline.items():
                    click.echo(f"    {k}: {v}")
            click.echo()
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@status.command("verify")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def status_verify(ctx: click.Context, as_json: bool):
    """Verify the append-only event log hash chain integrity."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.verify_event_log(client)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


# ── Memory commands ───────────────────────────────────────────────────────────

@cli.group()
def memory():
    """Memory CRUD: list, get, delete, mark-helpful."""


@memory.command("list")
@click.option("--page", default=1, help="Page number")
@click.option("--page-size", default=50, help="Results per page (max 200)")
@click.option("--category", default=None, help="Filter by category")
@click.option("--min-importance", default=None, type=int,
              help="Minimum importance score (1-5)")
@click.option("--include-deleted", is_flag=True, default=False,
              help="Include tombstoned memories")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def memory_list(ctx: click.Context, page: int, page_size: int,
                category: Optional[str], min_importance: Optional[int],
                include_deleted: bool, as_json: bool):
    """List memories sorted by importance (highest first)."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.list_memories(client, page=page, page_size=page_size,
                                     category=category, min_importance=min_importance,
                                     include_tombstoned=include_deleted)
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            total = data.get("total", 0)
            items = data.get("items", [])
            click.echo(f"\n  {total} total memories (page {page}, showing {len(items)})\n")
            for m in items:
                mid = m.get("id", "?")
                imp = m.get("importance", "?")
                fact = m.get("fact", "")[:120]
                cat = m.get("category", "")
                click.echo(f"  [{mid:>6}] imp={imp} {cat:12} {fact}")
            click.echo()
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@memory.command("get")
@click.argument("memory_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def memory_get(ctx: click.Context, memory_id: int, as_json: bool):
    """Get a single memory with full provenance chain."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.get_memory(client, memory_id)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@memory.command("provenance")
@click.argument("memory_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def memory_provenance(ctx: click.Context, memory_id: int, as_json: bool):
    """Show provenance chain for a memory."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.get_provenance(client, memory_id)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@memory.command("history")
@click.argument("memory_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def memory_history(ctx: click.Context, memory_id: int, as_json: bool):
    """Show lifecycle event history for a memory."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.get_history(client, memory_id)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@memory.command("helpful")
@click.argument("memory_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def memory_helpful(ctx: click.Context, memory_id: int, as_json: bool):
    """Signal a memory was useful (increments helpful_count, boosts utility_score)."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.mark_helpful(client, memory_id)
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            score = data.get("utility_score", "?")
            # helpful_count may not be in MemoryOut schema depending on server version
            hc = data.get("helpful_count", "updated")
            click.echo(f"  Marked #{memory_id} helpful. utility_score={score} ({hc})")
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@memory.command("delete")
@click.argument("memory_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt")
@click.pass_context
def memory_delete(ctx: click.Context, memory_id: int, as_json: bool, yes: bool):
    """Soft-delete (tombstone) a memory. Reversible via retain/restore."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    if not yes and not as_json:
        click.confirm(f"  Delete memory #{memory_id}?", abort=True)
    try:
        data = mem_ops.delete_memory(client, memory_id)
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            preview = data.get("fact_preview", "")
            click.echo(f"  Tombstoned #{memory_id}: {preview}")
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


# ── Conversation commands ─────────────────────────────────────────────────────

@cli.group()
def convo():
    """Conversations and their segments."""


@convo.command("list")
@click.option("--source-id", default=None, type=int, help="Filter by source ID")
@click.option("--limit", default=50, help="Max results")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def convo_list(ctx: click.Context, source_id: Optional[int], limit: int, as_json: bool):
    """List ingested conversations."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.list_conversations(client, source_id=source_id, limit=limit)
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            click.echo(f"\n  {len(data)} conversation(s)\n")
            for c in data:
                cid = c.get("id", "?")
                title = c.get("title", "")[:80]
                cnt = c.get("message_count", "?")
                click.echo(f"  [{cid:>6}] {cnt:>4} msgs  {title}")
            click.echo()
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@convo.command("segments")
@click.argument("conversation_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def convo_segments(ctx: click.Context, conversation_id: int, as_json: bool):
    """List segments for a conversation."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.get_conversation_segments(client, conversation_id)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


# ── Source commands ───────────────────────────────────────────────────────────

@cli.group()
def source():
    """Source management: list, delete, invalidate, restore."""


@source.command("list")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def source_list(ctx: click.Context, as_json: bool):
    """List all ingested data sources."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.list_sources(client)
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            click.echo(f"\n  {len(data)} source(s)\n")
            for s in data:
                sid = s.get("id", "?")
                stype = s.get("source_type", "?")
                path = s.get("source_path", "")[:60]
                msgs = s.get("message_count", "?")
                invalid = " [INVALIDATED]" if s.get("invalidated_at") else ""
                click.echo(f"  [{sid:>6}] {stype:12} {msgs:>5} msgs  {path}{invalid}")
            click.echo()
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@source.command("delete")
@click.argument("source_id", type=int)
@click.option("--hard", is_flag=True, default=False, help="Immediate purge (irreversible)")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def source_delete(ctx: click.Context, source_id: int, hard: bool, yes: bool, as_json: bool):
    """Delete a source and cascade to all derived records."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    mode = "HARD (irreversible)" if hard else "soft (tombstone)"
    if not yes and not as_json:
        click.confirm(f"  Delete source #{source_id} ({mode})?", abort=True)
    try:
        data = mem_ops.delete_source(client, source_id, hard=hard)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@source.command("invalidate")
@click.argument("source_id", type=int)
@click.option("--reason", default="", help="Reason for invalidation")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def source_invalidate(ctx: click.Context, source_id: int, reason: str, as_json: bool):
    """Mark a source retroactively wrong; cascades confidence demotion."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.invalidate_source(client, source_id, reason=reason)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@source.command("restore")
@click.argument("source_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def source_restore(ctx: click.Context, source_id: int, as_json: bool):
    """Restore a previously invalidated source."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.restore_source(client, source_id)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


# ── Search commands ───────────────────────────────────────────────────────────

@cli.group()
def search():
    """Semantic and filtered memory search."""


@search.command("query")
@click.argument("query_text")
@click.option("--k", default=10, help="Max results (1-100)")
@click.option("--tier", default=None, type=int, help="Force specific tier (1|2|3)")
@click.option("--min-tier", default=1, help="Minimum tier to use")
@click.option("--include-deleted", is_flag=True, default=False)
@click.option("--include-superseded", is_flag=True, default=False)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def search_query(ctx: click.Context, query_text: str, k: int,
                 tier: Optional[int], min_tier: int,
                 include_deleted: bool, include_superseded: bool, as_json: bool):
    """Run semantic search across all memory tiers."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = search_ops.semantic_search(
            client, query=query_text, k=k,
            force_tier=tier, min_tier=min_tier,
            include_tombstoned=include_deleted,
            include_superseded=include_superseded,
        )
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            click.echo(search_ops.format_search_results(data))
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@search.command("by-tag")
@click.argument("axis")
@click.argument("value")
@click.option("--k", default=10, help="Max results")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def search_tag(ctx: click.Context, axis: str, value: str, k: int, as_json: bool):
    """Search by tag axis/value (e.g. domain programming, intent learning)."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = search_ops.search_by_tag(client, axis=axis, value=value, k=k)
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            click.echo(search_ops.format_search_results(data))
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@search.command("by-entity")
@click.argument("name")
@click.option("--k", default=10, help="Max results")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def search_entity(ctx: click.Context, name: str, k: int, as_json: bool):
    """Search memories mentioning a named entity."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = search_ops.search_by_entity(client, name=name, k=k)
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            click.echo(search_ops.format_search_results(data))
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@search.command("by-date")
@click.option("--from", "date_from", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--to", "date_to", default=None, help="End date (YYYY-MM-DD)")
@click.option("--query", default="", help="Optional text filter")
@click.option("--k", default=10, help="Max results")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def search_date(ctx: click.Context, date_from: Optional[str], date_to: Optional[str],
                query: str, k: int, as_json: bool):
    """Search memories within a date range."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = search_ops.search_by_date(client, date_from=date_from, date_to=date_to,
                                         query=query, k=k)
        if as_json:
            click.echo(json.dumps(data, indent=2, default=str))
        else:
            click.echo(search_ops.format_search_results(data))
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


# ── Ingest commands ───────────────────────────────────────────────────────────

@cli.group()
def ingest():
    """Ingest data into MemoryWeb from various sources."""


@ingest.command("session")
@click.argument("path")
@click.option("--force", is_flag=True, default=False, help="Re-ingest even if unchanged")
@click.option("--wait", is_flag=True, default=False, help="Poll until task completes")
@click.option("--timeout", default=120, help="Wait timeout in seconds")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def ingest_session(ctx: click.Context, path: str, force: bool, wait: bool,
                   timeout: int, as_json: bool):
    """Ingest a Claude session JSONL file."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = ingest_ops.ingest_session(client, path=path, force=force)
        if wait:
            task_id = data.get("task_id")
            if task_id:
                data = ingest_ops.poll_task(client, task_id, timeout=timeout)
        _output(data, as_json)
    except (ConnectionError, RuntimeError, TimeoutError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@ingest.command("all-sessions")
@click.option("--directory", default=None, help="Override sessions directory on server")
@click.option("--force", is_flag=True, default=False)
@click.option("--wait", is_flag=True, default=False)
@click.option("--timeout", default=300, help="Wait timeout in seconds")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def ingest_all(ctx: click.Context, directory: Optional[str], force: bool,
               wait: bool, timeout: int, as_json: bool):
    """Ingest all session JSONL files in the server's sessions directory."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = ingest_ops.ingest_all_sessions(client, directory=directory, force=force)
        if wait:
            task_id = data.get("task_id")
            if task_id:
                data = ingest_ops.poll_task(client, task_id, timeout=timeout)
        _output(data, as_json)
    except (ConnectionError, RuntimeError, TimeoutError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@ingest.command("sample")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def ingest_sample_cmd(ctx: click.Context, as_json: bool):
    """Load built-in sample conversations (safe to call multiple times)."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = ingest_ops.ingest_sample(client)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@ingest.command("status")
@click.argument("task_id")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def ingest_status_cmd(ctx: click.Context, task_id: str, as_json: bool):
    """Check the status of an async ingest task."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = ingest_ops.ingest_status(client, task_id)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


@ingest.command("pipeline")
@click.argument("source_id", type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def ingest_pipeline(ctx: click.Context, source_id: int, as_json: bool):
    """Manually trigger the full processing pipeline for a source."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = ingest_ops.run_pipeline(client, source_id)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


# ── Certificate commands ──────────────────────────────────────────────────────

@cli.group()
def cert():
    """Answer certificates — provenance records for query responses."""


@cert.command("list")
@click.option("--limit", default=50, help="Max results")
@click.option("--stale-only", is_flag=True, default=False,
              help="Only show certificates backed by invalidated memories")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def cert_list(ctx: click.Context, limit: int, stale_only: bool, as_json: bool):
    """List answer certificates."""
    client = _make_client(ctx)
    as_json = as_json or ctx.obj.get("as_json", False)
    try:
        data = mem_ops.list_certificates(client, limit=limit, stale_only=stale_only)
        _output(data, as_json)
    except (ConnectionError, RuntimeError) as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            click.echo(f"  Error: {exc}", err=True)
        sys.exit(1)


# ── REPL ──────────────────────────────────────────────────────────────────────

@cli.command("repl")
@click.pass_context
def repl(ctx: click.Context):
    """Enter the interactive REPL (default when no subcommand given)."""
    skin = ReplSkin("memory-web", version=_VERSION)
    skin.print_banner()

    pt_session = skin.create_prompt_session()

    COMMANDS = {
        "status health":       "Quick health check",
        "status full":         "Full pipeline status",
        "status verify":       "Verify event log integrity",
        "memory list":         "List memories (importance-sorted)",
        "memory get <id>":     "Get memory with provenance",
        "memory helpful <id>": "Mark memory as helpful",
        "memory delete <id>":  "Soft-delete a memory",
        "search query <text>": "Semantic search",
        "search by-tag <axis> <value>": "Tag-based search",
        "search by-entity <name>": "Entity search",
        "convo list":          "List conversations",
        "source list":         "List data sources",
        "ingest session <path>": "Ingest a session JSONL",
        "ingest sample":       "Load built-in sample data",
        "cert list":           "List answer certificates",
        "help":                "Show this help",
        "quit / exit":         "Exit the REPL",
    }

    while True:
        try:
            line = skin.get_input(pt_session, context="memory-web")
        except (KeyboardInterrupt, EOFError):
            skin.print_goodbye()
            break

        line = line.strip()
        if not line:
            continue

        if line in ("quit", "exit", "q"):
            skin.print_goodbye()
            break

        if line in ("help", "h", "?"):
            skin.help(COMMANDS)
            continue

        # Parse and invoke via Click's standalone mode
        # Build an argv list from the line
        import shlex
        try:
            args = shlex.split(line)
        except ValueError as exc:
            skin.error(f"Parse error: {exc}")
            continue

        try:
            # Preserve context obj so subcommands get base_url/api_key
            obj = ctx.obj.copy() if ctx.obj else {}
            standalone_ctx = cli.make_context(
                "cli-anything-memory-web",
                args,
                parent=None,
                obj=obj,
            )
            with standalone_ctx:
                cli.invoke(standalone_ctx)
        except SystemExit as exc:
            if exc.code and exc.code != 0:
                skin.error(f"Command failed (exit {exc.code})")
        except click.exceptions.UsageError as exc:
            skin.error(str(exc))
        except click.exceptions.Abort:
            skin.warning("Aborted")
        except Exception as exc:
            skin.error(f"Unexpected error: {exc}")


def main():
    """Entry point for the cli-anything-memory-web console script."""
    cli(obj={})


if __name__ == "__main__":
    main()
