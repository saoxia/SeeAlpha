"""Username/password login with opaque, revocable server-side sessions."""

import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .contracts import AuthResponse, LoginRequest, UserResponse
from .dependencies import (
    COOKIE_NAME,
    AdminUser,
    CurrentContext,
    SessionContext,
    SessionCookie,
    actor_is_admin,
)
from .security import (
    DUMMY_PASSWORD_HASH,
    create_session_token,
    hash_password,
    login_identity_digest,
    password_needs_rehash,
    token_digest,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, request: Request, response: Response) -> AuthResponse:
    repository = request.app.state.repository
    settings = request.app.state.settings
    username = payload.username.strip()
    # Ignore caller-supplied forwarding headers. Behind the dev proxy the limit is per username.
    client = request.client.host if request.client else "unknown"
    identity = login_identity_digest(client, username)
    if repository.is_login_rate_limited(identity):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试。")
    user = repository.get_user_by_username(username)
    password_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    valid = verify_password(password_hash, payload.password)
    if not user or not valid:
        repository.record_login_failure(identity)
        raise HTTPException(status_code=401, detail="用户名或密码错误。")
    repository.clear_login_failures(identity)
    if password_needs_rehash(user["password_hash"]):
        repository.update_password(user["id"], hash_password(payload.password))
    token = create_session_token()
    repository.create_session(
        user["id"], token_digest(token), int(time.time()) + settings.session_seconds
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.session_seconds,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    signed_in = UserResponse(id=user["id"], username=user["username"])
    return AuthResponse(user=signed_in, is_admin=actor_is_admin(request, signed_in.username))


def _session_response(request: Request, context: SessionContext) -> AuthResponse:
    return AuthResponse(
        user=context.user,
        actor=context.actor if context.impersonating else None,
        impersonating=context.impersonating,
        is_admin=actor_is_admin(request, context.actor.username),
    )


@router.get("/me", response_model=AuthResponse)
def me(request: Request, context: CurrentContext) -> AuthResponse:
    return _session_response(request, context)


class ImpersonateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    username: str = Field(min_length=1, max_length=64)


@router.post("/impersonate", response_model=AuthResponse)
def start_impersonation(
    payload: ImpersonateRequest, request: Request, context: CurrentContext, _: AdminUser,
) -> AuthResponse:
    if context.impersonating:
        raise HTTPException(status_code=409, detail="请先退出当前用户视图。")
    target = request.app.state.repository.get_user_by_username(payload.username)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在。")
    if target["id"] == context.actor.id:
        raise HTTPException(status_code=409, detail="已经是当前用户。")
    request.app.state.repository.set_impersonation(context.token_digest, target["id"])
    return AuthResponse(
        user=UserResponse(id=target["id"], username=target["username"]),
        actor=context.actor,
        impersonating=True,
        is_admin=True,
    )


@router.post("/impersonate/stop", response_model=AuthResponse)
def stop_impersonation(request: Request, context: CurrentContext, _: AdminUser) -> AuthResponse:
    request.app.state.repository.set_impersonation(context.token_digest, None)
    return AuthResponse(user=context.actor, impersonating=False, is_admin=True)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session_token: SessionCookie = None):
    if session_token:
        request.app.state.repository.delete_session(token_digest(session_token))
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=request.app.state.settings.cookie_secure,
        samesite="strict",
    )
    response.status_code = 204
    return response
