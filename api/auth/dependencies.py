from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from starlette.requests import HTTPConnection

from .contracts import UserResponse
from .security import token_digest

COOKIE_NAME = "seealpha_session"
SessionCookie = Annotated[str | None, Cookie(alias=COOKIE_NAME)]


def require_user(request: HTTPConnection, session_token: SessionCookie = None) -> UserResponse:
    if not session_token:
        raise HTTPException(status_code=401, detail="未登录。")
    user = request.app.state.repository.get_user_for_session(token_digest(session_token))
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效。")
    return UserResponse(id=user["id"], username=user["username"])


CurrentUser = Annotated[UserResponse, Depends(require_user)]


def require_admin(request: HTTPConnection, user: CurrentUser) -> UserResponse:
    if user.username.casefold() != request.app.state.settings.admin_username.casefold():
        raise HTTPException(status_code=403, detail="需要管理员权限。")
    return user


AdminUser = Annotated[UserResponse, Depends(require_admin)]
