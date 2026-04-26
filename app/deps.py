"""
FastAPI dependencies for tenant extraction and auth.
"""
from typing import Optional

from fastapi import Depends, HTTPException, Header, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_db, set_tenant_context, DEFAULT_TENANT_ID
from .config import settings

# Re-export for routers that only need get_db / Session
__all__ = ["get_db", "Session", "get_current_tenant_id"]

_bearer = HTTPBearer(auto_error=False)

DEFAULT_TENANT_UUID = DEFAULT_TENANT_ID


async def get_current_tenant_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    db: Session = Depends(get_db),
) -> str:
    """
    Extract tenant_id from request using strict precedence:
    1. JWT claims (highest priority — always trusted)
    2. API key lookup (direct tenant identification)
    3. X-Tenant-ID header (ONLY allowed in dev mode — never in prod)
    4. Default tenant (dev/community fallback)

    Sets the thread-local tenant context for downstream DB calls.
    """
    tenant_id = DEFAULT_TENANT_UUID

    if settings.MW_AUTH_ENABLED:
        if credentials:
            # Path 1: JWT
            from .services.auth import verify_jwt
            claims = verify_jwt(credentials.credentials)
            tenant_id = claims.get("tenant_id", DEFAULT_TENANT_UUID)
        elif request.headers.get("X-API-Key"):
            # Path 2: API key
            from .services.auth import verify_api_key
            raw_key = request.headers["X-API-Key"]
            result = verify_api_key(db, raw_key)
            if result is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid API key.",
                )
            _, tenant = result
            tenant_id = str(tenant.id)
        # Path 3: X-Tenant-ID is intentionally ignored when auth is enabled —
        # it must not be trusted as a privilege-escalation vector in production.
    else:
        # Auth disabled — dev/community mode only
        if x_tenant_id:
            tenant_id = x_tenant_id  # dev convenience only

    set_tenant_context(tenant_id)
    return tenant_id
