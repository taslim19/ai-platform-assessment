from pydantic import BaseModel, Field, ConfigDict, field_validator
from app.gateway import Usage


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.@-]+$')
    password: str = Field(min_length=1, max_length=1024, repr=False)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_in: int


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=16000)

    @field_validator('question')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Question must not be blank')
        return value


class ChatResponse(BaseModel):
    answer: str
    model: str
    usage: Usage
    latency_ms: int
    request_id: str
    cached: bool
