from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from api_service.enum import JobType, OutputType, VerificationCodeType


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=64)


class SendVerifyCodeRequest(BaseModel):
    email: EmailStr
    type: VerificationCodeType


class SignInRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=64)


class VerifyNewAccountRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)


class ForgetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6, max_length=64)


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
    output_type: OutputType
    input_language: str
    output_language: Optional[str]
    type: JobType
    metadata: Optional[dict]
