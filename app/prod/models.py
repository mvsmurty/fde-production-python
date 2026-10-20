from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

Channel = Literal["email", "whatsapp", "web", "phone"]
Category = Literal["billing", "technical", "account", "general", "other"]
Priority = Literal["P1", "P2", "P3"]
Sentiment = Literal["positive", "neutral", "negative", "unknown"]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_CHANNEL_ALIASES = {"mail": "email", "wa": "whatsapp", "website": "web", "call": "phone"}


class TicketIn(BaseModel):
    """The contract at the edge. Anything that doesn't fit is rejected here, not deep inside."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    ticket_id: str = Field(pattern=r"^TCK-\d+$", examples=["TCK-10293"])
    channel: Channel
    customer_email: EmailStr
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=5000)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        key = value.strip().lower().replace("-", "").replace(" ", "")
        return _CHANNEL_ALIASES.get(key, key)


class LLMClassification(BaseModel):
    """LLM output is untrusted input too."""

    category: Category
    priority: Priority


class TicketAnalysis(BaseModel):
    ticket_id: str
    summary: str | None
    category: Category
    priority: Priority
    sentiment: Sentiment
    needs_human_review: bool
    degraded: bool
    warnings: list[str]
    trace_id: str
