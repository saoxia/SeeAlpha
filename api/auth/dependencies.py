from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from starlette.requests import HTTPConnection

from .contracts import UserResponse
from .security import token_digest

COOKIE_NAME = "seealpha_session"
SessionCookie = Annotated[str | None, Cookie(alias=COOKIE_NAME)]


def load_session(request: HTTPConnection, session_token: str | None) -> UserResponse:
    # Do not cache this on the connection. Websocket snapshots re-check revocation.
    if not session_token:
        raise HTTPException(status_code=401, detail="未登录。")
    record = request.app.state.repository.get_user_for_session(token_digest(session_token))
    if not record:
        raise HTTPException(status_code=401, detail="登录已失效。")
    return UserResponse(id=record["id"], username=record["username"])


def require_user(request: HTTPConnection, session_token: SessionCookie = None) -> UserResponse:
    return load_session(request, session_token)


CurrentUser = Annotated[UserResponse, Depends(require_user)]


def actor_is_admin(request: HTTPConnection, username: str) -> bool:
    return username.casefold() == request.app.state.settings.admin_username.casefold()


def require_trading_user(request: HTTPConnection, user: CurrentUser) -> UserResponse:
    if actor_is_admin(request, user.username):
        raise HTTPException(status_code=403, detail="管理员仅可执行平台管理操作，请使用独立普通用户账户。")
    return user


TradingUser = Annotated[UserResponse, Depends(require_trading_user)]


def require_admin(request: HTTPConnection, user: CurrentUser) -> UserResponse:
    if not actor_is_admin(request, user.username):
        raise HTTPException(status_code=403, detail="需要管理员权限。")
    return user


AdminUser = Annotated[UserResponse, Depends(require_admin)]
