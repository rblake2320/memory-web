"""
MemoryWeb OpenAI-Compatible Proxy — port 4141
Intercepts every chat/completions request, queries MemoryWeb for relevant
memories, injects them into the system prompt, then forwards to OpenAI.

Run:
    D:\\memory-web\\.venv\\Scripts\\python.exe memoryweb_proxy.py

Wire clients:
    OPENAI_BASE_URL=http://localhost:4141/v1
    or in Python: OpenAI(base_url="http://localhost:4141/v1")

SECURITY: Binds to 127.0.0.1 only. Never exposed on the network.
          Does NOT log Authorization headers.
"""

import os
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import uvicorn

MW_URL = os.environ.get("MEMORYWEB_URL", "http://127.0.0.1:8100")
OPENAI_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
MAX_MEMORY_CHARS = 2000  # hard cap on injected memory text to avoid context overflow

app = FastAPI(title="MemoryWeb OpenAI Proxy", version="1.0.0")


def _extract_user_text(messages: list) -> str:
    """Pull the most recent user message text for the memory query."""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                return content[:1000]
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                return " ".join(parts)[:1000]
    return ""


def _inject_memories(messages: list, memories_block: str) -> list:
    """Prepend memories to the system message, or create one."""
    enriched = list(messages)
    sys_idx = next(
        (i for i, m in enumerate(enriched) if m.get("role") == "system"), None
    )
    if sys_idx is not None:
        enriched[sys_idx] = {
            **enriched[sys_idx],
            "content": enriched[sys_idx]["content"] + memories_block,
        }
    else:
        enriched.insert(0, {"role": "system", "content": memories_block.strip()})
    return enriched


async def _query_memoryweb(query: str) -> str:
    """
    Query MemoryWeb. Returns formatted memory block string or empty string.
    Never raises — if MemoryWeb is down the proxy degrades to passthrough.
    """
    if not query:
        return ""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.post(
                f"{MW_URL}/api/search",
                json={"query": query, "limit": 5},
            )
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    snippets = "\n".join(
                        f"- {item.get('content', '')[:400]}" for item in results
                    )
                    # Enforce hard cap
                    if len(snippets) > MAX_MEMORY_CHARS:
                        snippets = snippets[:MAX_MEMORY_CHARS] + "..."
                    return (
                        f"\n\n[RELEVANT MEMORIES FROM MEMORYWEB]\n{snippets}\n"
                        "[END MEMORIES]\n"
                    )
    except Exception:
        pass
    return ""


def _forward_headers(request: Request) -> dict:
    """Build safe forward headers. Never log the Authorization value."""
    headers = {"Content-Type": "application/json"}
    # Prefer client-provided auth (their key), fall back to env var
    auth = request.headers.get("authorization") or (
        f"Bearer {OPENAI_KEY}" if OPENAI_KEY else None
    )
    if auth:
        headers["Authorization"] = auth
    return headers


@app.post("/v1/chat/completions")
async def proxy_chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    user_text = _extract_user_text(messages)
    memories_block = await _query_memoryweb(user_text)
    if memories_block:
        body["messages"] = _inject_memories(messages, memories_block)

    headers = _forward_headers(request)

    if stream:
        async def generate():
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{OPENAI_URL}/v1/chat/completions",
                    json=body,
                    headers=headers,
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{OPENAI_URL}/v1/chat/completions",
                json=body,
                headers=headers,
            )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type="application/json",
        )


@app.get("/v1/models")
async def proxy_models(request: Request):
    """Pass-through for model listing so clients can enumerate available models."""
    headers = _forward_headers(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{OPENAI_URL}/v1/models", headers=headers)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type="application/json",
    )


@app.get("/health")
async def health():
    # Check MemoryWeb reachability without leaking its contents
    mw_ok = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{MW_URL}/api/health")
            mw_ok = r.status_code == 200
    except Exception:
        pass
    return {
        "status": "ok",
        "memoryweb": "ok" if mw_ok else "unreachable",
        "memoryweb_url": MW_URL,
        "openai_target": OPENAI_URL,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=4141, log_level="warning")
