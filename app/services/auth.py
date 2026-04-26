"""
Authentication service: user creation, JWT issuance, tenant management.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import uuid

import bcrypt
import jwt
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..config import settings
from ..models import User, Tenant, TenantApiKey


# ---------------------------------------------------------------------------
# Tenant management
# ---------------------------------------------------------------------------

def create_tenant(db: Session, *, name: str, slug: str, plan: str = "community") -> Tenant:
    """Create a new tenant. Raises 409 if slug already exists."""
    existing = db.query(Tenant).filter(Tenant.slug == slug).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tenant slug '{slug}' is already taken.",
        )
    tenant = Tenant(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        plan=plan,
    )
    db.add(tenant)
    db.flush()  # get id without committing
    return tenant


def get_tenant_by_slug(db: Session, slug: str) -> Optional[Tenant]:
    return db.query(Tenant).filter(Tenant.slug == slug).first()


def get_tenant_by_id(db: Session, tenant_id: str) -> Optional[Tenant]:
    return db.query(Tenant).filter(Tenant.id == tenant_id).first()


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


def create_user(
    db: Session, *, tenant_id, email: str, password: str, role: str = "user"
) -> User:
    """Create a new user. Raises 409 if email already registered."""
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email format.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered.",
        )
    pwd_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash=pwd_hash,
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def authenticate_user(db: Session, *, email: str, password: str) -> Tuple[User, Tenant]:
    """Verify credentials. Returns (user, tenant) or raises 401."""
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=500, detail="Tenant not found for user.")
    if tenant.disabled_at is not None:
        raise HTTPException(status_code=403, detail="This account has been disabled.")
    return user, tenant


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def issue_jwt(*, user_id: int, tenant_id: str, plan: str) -> str:
    """Issue a signed JWT with tenant_id, plan, and expiry."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "plan": plan,
        "iat": now,
        "exp": now + timedelta(hours=settings.MW_JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.MW_JWT_SECRET, algorithm=settings.MW_JWT_ALGORITHM)


def verify_jwt(token: str) -> dict:
    """Verify and decode a JWT. Raises 401 on invalid/expired."""
    try:
        claims = jwt.decode(
            token,
            settings.MW_JWT_SECRET,
            algorithms=[settings.MW_JWT_ALGORITHM],
        )
        return claims
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

def generate_api_key(
    db: Session, *, tenant_id, name: str, scopes: list[str]
) -> Tuple[str, TenantApiKey]:
    """
    Generate a new API key. Returns (plaintext_key, TenantApiKey record).
    The plaintext key is shown ONCE — caller must return it to the user immediately.
    """
    raw_key = f"mbk_{secrets.token_urlsafe(32)}"
    prefix = raw_key[:12]
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    record = TenantApiKey(
        tenant_id=tenant_id,
        key_prefix=prefix,
        key_hash=key_hash,
        name=name,
        scopes=scopes,
    )
    db.add(record)
    db.flush()
    return raw_key, record


def verify_api_key(
    db: Session, raw_key: str
) -> Optional[Tuple[TenantApiKey, Tenant]]:
    """
    Verify an API key. Returns (key_record, tenant) if valid, None if not found/revoked.
    Updates last_used_at.
    """
    if not raw_key or len(raw_key) < 12:
        return None
    prefix = raw_key[:12]
    record = db.query(TenantApiKey).filter(
        TenantApiKey.key_prefix == prefix,
        TenantApiKey.revoked_at.is_(None),
    ).first()
    if not record:
        return None
    if not bcrypt.checkpw(raw_key.encode(), record.key_hash.encode()):
        return None
    # Update last_used_at
    record.last_used_at = datetime.now(timezone.utc)
    tenant = db.query(Tenant).filter(Tenant.id == record.tenant_id).first()
    return record, tenant
