from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from starlette.requests import HTTPConnection

from .contracts import UserResponse
from .security import token_digest

COOKIE_NAME = "seealpha_session"
SessionCookie = Annotated[str | None, Cookie(alias=COOKIE_NAME)]


@dataclass(frozen=True)
class SessionContext:
    actor: UserResponse
    user: UserResponse
    impersonating: bool
    token_digest: str


def load_session(request: HTTPConnection, session_token: str | None) -> SessionContext:
    # Do not cache this on the connection. Websocket snapshots re-check revocation.
    if not session_token:
        raise HTTPException(status_code=401, detail="未登录。")
    digest = token_digest(session_token)
    record = request.app.state.repository.get_user_for_session(digest)
    if not record:
        raise HTTPException(status_code=401, detail="登录已失效。")
    actor = UserResponse(id=record["id"], username=record["username"])
    effective = actor
    impersonating = False
    target_id = record["impersonate_user_id"]
    if target_id:
        target = request.app.state.repository.get_user_by_id(target_id)
        if target is None:
            request.app.state.repository.set_impersonation(digest, None)
        else:
            effective = UserResponse(id=target["id"], username=target["username"])
            impersonating = True
    return SessionContext(
        actor=actor, user=effective, impersonating=impersonating, token_digest=digest
    )


def require_context(request: HTTPConnection, session_token: SessionCookie = None) -> SessionContext:
    return load_session(request, session_token)


CurrentContext = Annotated[SessionContext, Depends(require_context)]


def require_user(request: HTTPConnection, session_token: SessionCookie = None) -> UserResponse:
    return load_session(request, session_token).user


CurrentUser = Annotated[UserResponse, Depends(require_user)]


def actor_is_admin(request: HTTPConnection, username: str) -> bool:
    return username.casefold() == request.app.state.settings.admin_username.casefold()


def require_admin(request: HTTPConnection, user: CurrentUser) -> UserResponse:
    cookies = getattr(request, "cookies", None)
    token = cookies.get(COOKIE_NAME) if cookies is not None else None
    actor = load_session(request, token).actor if token else user
    if not actor_is_admin(request, actor.username):
        raise HTTPException(status_code=403, detail="需要管理员权限。")
    return actor


AdminUser = Annotated[UserResponse, Depends(require_admin)]
