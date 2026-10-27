"""Username/password login with opaque, revocable server-side sessions."""

import time

from fastapi import APIRouter, HTTPException, Request, Response

from .contracts import AuthResponse, LoginRequest, UserResponse
from .dependencies import COOKIE_NAME, CurrentUser, SessionCookie
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
    return AuthResponse(user=UserResponse(id=user["id"], username=user["username"]))


@router.get("/me", response_model=AuthResponse)
def me(user: CurrentUser) -> AuthResponse:
    return AuthResponse(user=user)


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
