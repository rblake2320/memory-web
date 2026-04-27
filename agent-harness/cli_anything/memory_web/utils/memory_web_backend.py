"""Backend module for cli-anything-memory-web.

MemoryWeb is a FastAPI server — the "real software" is the running server
at MW_BASE_URL. This module handles server discovery, connectivity checks,
and raises clear errors if the server is not reachable.

The server is a hard dependency. The CLI is useless without it.
Install instructions:
    cd D:\\memory-web
    C:\\Python312\\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8100
    # or use the provided start scripts:
    # Windows: start.bat  |  PowerShell: start_all.ps1
"""

import os
import urllib.request
import urllib.error
from typing import Optional


_DEFAULT_BASE_URL = "http://localhost:8100"


def find_memory_web_server(base_url: Optional[str] = None) -> str:
    """Discover and validate a reachable MemoryWeb server.

    Args:
        base_url: Override URL. Defaults to MW_BASE_URL env var, then localhost:8100.

    Returns:
        The validated base URL string (no trailing slash).

    Raises:
        RuntimeError: Server is not reachable — with start instructions.
    """
    url = (base_url or os.environ.get("MW_BASE_URL", _DEFAULT_BASE_URL)).rstrip("/")
    health_url = f"{url}/api/health"

    try:
        req = urllib.request.Request(health_url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return url
            raise RuntimeError(f"MemoryWeb health check returned HTTP {resp.status}")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"MemoryWeb server is not reachable at {url}.\n"
            f"Start it with one of:\n"
            f"  cd D:\\memory-web && start.bat\n"
            f"  cd D:\\memory-web && C:\\Python312\\python.exe -m uvicorn app.main:app "
            f"--host 0.0.0.0 --port 8100\n"
            f"Then set MW_BASE_URL if using a non-default address.\n"
            f"Original error: {exc.reason}"
        ) from exc


def check_server_health(base_url: Optional[str] = None) -> dict:
    """Check MemoryWeb server health and return the health response.

    Args:
        base_url: Optional URL override.

    Returns:
        Dict with 'status', 'version', and optionally 'reachable'.

    Raises:
        RuntimeError: Server is not running.
    """
    url = find_memory_web_server(base_url)
    health_url = f"{url}/api/health"
    req = urllib.request.Request(health_url, headers={"Accept": "application/json"})
    import json
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())
