"""Mint, list, and revoke service tokens. Cookie session only; the raw token is shown once."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from fse_ui import audit
from fse_ui.auth import SERVICE_SCOPES, AuthService, Principal

router = APIRouter()

ScopeName = Literal["cards:read", "cards:draft", "cards:backtest"]


def _default_scopes() -> list[ScopeName]:
    return ["cards:read", "cards:draft", "cards:backtest"]


class TokenIn(BaseModel):
    """Ask for the card scopes this token should carry. Empty means all three."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.:-]{0,79}$")
    scopes: list[ScopeName] = Field(default_factory=_default_scopes)
    ttl_s: int | None = Field(default=None, ge=60, le=60 * 60 * 24 * 365)


class TokenMinted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    id_hash: str
    name: str
    scopes: list[str]
    created_at: float
    expires_at: float | None
    shown_once: bool = True


class TokenRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_hash: str
    name: str
    sub: str
    email: str
    scopes: list[str]
    created_at: float
    expires_at: float | None
    revoked_at: float | None


def _who(request: Request) -> Principal:
    who: Principal = request.state.principal
    return who


def _auth(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth
    return service


@router.post("/service-tokens", response_model=TokenMinted)
async def mint_token(body: TokenIn, request: Request) -> TokenMinted:
    """Create a bearer token. The response is the only time ``token`` is available."""
    who = _who(request)
    if who.kind != "session":
        raise HTTPException(status_code=403, detail="service tokens are minted from a session")
    scopes = list(dict.fromkeys(body.scopes))
    if not scopes or set(scopes) - SERVICE_SCOPES:
        raise HTTPException(status_code=422, detail="unknown service-token scope")
    try:
        raw, meta = _auth(request).mint_service_token(who, body.name, scopes, body.ttl_s)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.note(request, name=body.name, scopes=scopes)
    return TokenMinted(token=raw, shown_once=True, **meta)


@router.get("/service-tokens", response_model=list[TokenRow])
async def list_tokens(request: Request) -> list[TokenRow]:
    """Stored tokens. The raw bearer value is not kept."""
    return [TokenRow.model_validate(row) for row in _auth(request).list_service_tokens()]


@router.delete("/service-tokens/{id_hash}")
async def revoke_token(id_hash: str, request: Request) -> dict[str, bool]:
    if not _auth(request).revoke_service_token(id_hash):
        raise HTTPException(status_code=404, detail="no active service token with that id")
    audit.note(request, id_hash=id_hash)
    return {"revoked": True}
