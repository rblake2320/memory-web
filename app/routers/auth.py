"""
JWT authentication endpoints.

Active only when MW_AUTH_ENABLED=True in config.
When disabled, all endpoints return 501 with a helpful message.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.auth import (
    authenticate_user,
    create_tenant,
    create_user,
    issue_jwt,
    verify_jwt,
)
from ..config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_security = HTTPBearer(auto_error=False)

_AUTH_DISABLED = HTTPException(
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    detail=(
        "Auth is disabled on this instance (MW_AUTH_ENABLED=false). "
        "Set MW_AUTH_ENABLED=true and configure MW_JWT_SECRET to enable."
    ),
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    password: str
    tenant_name: str          # creates a new tenant with auto-slug
    tenant_slug: Optional[str] = None  # optional override


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: str
    plan: str


class MeResponse(BaseModel):
    user_id: int
    email: str
    tenant_id: str
    tenant_slug: str
    plan: str
    role: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if not settings.MW_AUTH_ENABLED:
        raise _AUTH_DISABLED
    # Derive slug from name if not supplied
    slug = req.tenant_slug or req.tenant_name.lower().replace(" ", "-")[:100]
    tenant = create_tenant(db, name=req.tenant_name, slug=slug)
    user = create_user(db, tenant_id=tenant.id, email=req.email, password=req.password)
    db.commit()
    token = issue_jwt(user_id=user.id, tenant_id=str(tenant.id), plan=tenant.plan)
    return TokenResponse(access_token=token, tenant_id=str(tenant.id), plan=tenant.plan)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    if not settings.MW_AUTH_ENABLED:
        raise _AUTH_DISABLED
    user, tenant = authenticate_user(db, email=req.email, password=req.password)
    token = issue_jwt(user_id=user.id, tenant_id=str(tenant.id), plan=tenant.plan)
    return TokenResponse(access_token=token, tenant_id=str(tenant.id), plan=tenant.plan)


@router.get("/me", response_model=MeResponse)
def me(
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
):
    if not settings.MW_AUTH_ENABLED:
        raise _AUTH_DISABLED
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
        )
    claims = verify_jwt(credentials.credentials)
    from ..models import User, Tenant
    user = db.query(User).filter(User.id == claims["sub"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    return MeResponse(
        user_id=user.id,
        email=user.email,
        tenant_id=str(user.tenant_id),
        tenant_slug=tenant.slug if tenant else "unknown",
        plan=tenant.plan if tenant else "community",
        role=user.role,
    )
