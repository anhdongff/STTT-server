from pydantic import BaseModel, EmailStr
from typing import Optional


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserPublic(BaseModel):
    id: int
    email: EmailStr
    display_name: Optional[str]


class SubmitJobRequest(BaseModel):
    file_path: str
    output_type: str
    input_language: str
    output_language: Optional[str]
    type: str
