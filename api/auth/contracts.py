from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: int
    username: str


class AuthResponse(BaseModel):
    user: UserResponse
    actor: UserResponse | None = None
    impersonating: bool = False
    is_admin: bool = False
