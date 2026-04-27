"""HTTP client for the MemoryWeb API.

All requests go through this module. Auth and base URL are read from
environment variables so callers never hardcode credentials.

Environment variables:
    MW_BASE_URL  — API base URL (default: http://localhost:8100)
    MW_API_KEY   — Optional API key sent as X-API-Key header
"""

import os
from typing import Any, Dict, Optional
import urllib.request
import urllib.parse
import urllib.error
import json as _json

_DEFAULT_BASE_URL = "http://localhost:8100"
_TIMEOUT = 30  # seconds


class MemoryWebClient:
    """Thin HTTP client wrapping the MemoryWeb REST API."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url or os.environ.get("MW_BASE_URL", _DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.environ.get("MW_API_KEY", "")

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _request(self, method: str, path: str, params: Optional[Dict] = None,
                 body: Optional[Any] = None) -> Any:
        """Execute an HTTP request and return the parsed JSON response.

        Raises:
            ConnectionError: server is unreachable
            RuntimeError: non-2xx HTTP response
        """
        url = f"{self.base_url}{path}"
        if params:
            # Filter out None values
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        data = _json.dumps(body).encode("utf-8") if body is not None else None

        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                return _json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = _json.loads(raw).get("detail", raw)
            except Exception:
                detail = raw
            raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(
                f"Cannot connect to MemoryWeb at {self.base_url}. "
                f"Is the server running? Error: {exc.reason}"
            ) from exc

    # ── Status / Health ────────────────────────────────────────────────

    def health(self) -> Dict:
        return self._request("GET", "/api/health")

    def status(self) -> Dict:
        return self._request("GET", "/api/status")

    # ── Memories ──────────────────────────────────────────────────────

    def list_memories(self, page: int = 1, page_size: int = 50,
                      category: Optional[str] = None,
                      min_importance: Optional[int] = None,
                      include_tombstoned: bool = False) -> Dict:
        return self._request("GET", "/api/memories", params={
            "page": page,
            "page_size": page_size,
            "category": category,
            "min_importance": min_importance,
            "include_tombstoned": str(include_tombstoned).lower(),
        })

    def get_memory(self, memory_id: int) -> Dict:
        return self._request("GET", f"/api/memories/{memory_id}")

    def get_memory_provenance(self, memory_id: int) -> Any:
        return self._request("GET", f"/api/memories/{memory_id}/provenance")

    def get_memory_history(self, memory_id: int) -> Dict:
        return self._request("GET", f"/api/memories/{memory_id}/history")

    def mark_helpful(self, memory_id: int) -> Dict:
        return self._request("POST", f"/api/memories/{memory_id}/helpful")

    def delete_memory(self, memory_id: int) -> Dict:
        return self._request("DELETE", f"/api/memories/{memory_id}")

    # ── Search ────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 10,
               filters: Optional[Dict] = None,
               force_tier: Optional[int] = None,
               min_tier: int = 1,
               include_tombstoned: bool = False,
               include_superseded: bool = False) -> Dict:
        body: Dict[str, Any] = {
            "query": query,
            "k": k,
            "min_tier": min_tier,
            "include_tombstoned": include_tombstoned,
            "include_superseded": include_superseded,
        }
        if filters:
            body["filters"] = filters
        if force_tier is not None:
            body["force_tier"] = force_tier
        return self._request("POST", "/api/search", body=body)

    def search_by_tag(self, axis: str, value: str, k: int = 10) -> Dict:
        return self._request("GET", "/api/search/by-tag",
                             params={"axis": axis, "value": value, "k": k})

    def search_by_entity(self, name: str, k: int = 10) -> Dict:
        return self._request("GET", "/api/search/by-entity",
                             params={"name": name, "k": k})

    def search_by_date(self, date_from: Optional[str] = None,
                       date_to: Optional[str] = None,
                       query: str = "", k: int = 10) -> Dict:
        return self._request("GET", "/api/search/by-date",
                             params={"date_from": date_from, "date_to": date_to,
                                     "query": query, "k": k})

    # ── Ingest ────────────────────────────────────────────────────────

    def ingest_session(self, path: str, force: bool = False) -> Dict:
        return self._request("POST", "/api/ingest/session",
                             body={"path": path, "force": force})

    def ingest_all_sessions(self, directory: Optional[str] = None, force: bool = False) -> Dict:
        return self._request("POST", "/api/ingest/session/all",
                             body={"directory": directory, "force": force})

    def ingest_shared_chat(self, directory: Optional[str] = None,
                           limit: Optional[int] = None, force: bool = False) -> Dict:
        return self._request("POST", "/api/ingest/shared-chat",
                             body={"directory": directory, "limit": limit, "force": force})

    def ingest_sqlite(self, path: Optional[str] = None) -> Dict:
        return self._request("POST", "/api/ingest/sqlite-memory",
                             body={"path": path})

    def ingest_sample(self) -> Dict:
        return self._request("POST", "/api/ingest/sample")

    def ingest_status(self, task_id: str) -> Dict:
        return self._request("GET", f"/api/ingest/status/{task_id}")

    def list_sources(self) -> Any:
        return self._request("GET", "/api/ingest/sources")

    def run_pipeline(self, source_id: int) -> Dict:
        return self._request("POST", f"/api/ingest/pipeline/{source_id}")

    # ── Conversations ─────────────────────────────────────────────────

    def list_conversations(self, source_id: Optional[int] = None,
                           limit: int = 50, offset: int = 0) -> Any:
        return self._request("GET", "/api/conversations",
                             params={"source_id": source_id, "limit": limit, "offset": offset})

    def get_conversation_segments(self, conversation_id: int) -> Any:
        return self._request("GET", f"/api/conversations/{conversation_id}/segments")

    # ── Sources ───────────────────────────────────────────────────────

    def delete_source(self, source_id: int, hard: bool = False) -> Dict:
        return self._request("DELETE", f"/api/sources/{source_id}",
                             params={"hard": str(hard).lower()})

    def invalidate_source(self, source_id: int, reason: str = "") -> Dict:
        return self._request("POST", f"/api/sources/{source_id}/invalidate",
                             body={"reason": reason})

    def restore_source(self, source_id: int) -> Dict:
        return self._request("POST", f"/api/sources/{source_id}/restore")

    # ── Certificates ──────────────────────────────────────────────────

    def list_certificates(self, limit: int = 50, offset: int = 0,
                          stale_only: bool = False) -> Dict:
        return self._request("GET", "/api/certificates",
                             params={"limit": limit, "offset": offset,
                                     "stale_only": str(stale_only).lower()})

    def get_certificate(self, cert_id: int) -> Dict:
        return self._request("GET", f"/api/certificates/{cert_id}")

    # ── Event log ─────────────────────────────────────────────────────

    def verify_event_log(self) -> Dict:
        return self._request("GET", "/api/event_log/verify")
