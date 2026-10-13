import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    error: str | None
    duration_ms: int | None
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    doc_key: str
    doc_type: str
    language: str
    version: int
    effective_date: date | None
    is_active: bool
    is_confidential: bool
    is_demo: bool
    status: str
    source: str | None
    created_at: datetime


class DocumentDetail(DocumentOut):
    chunk_count: int
    jobs: list[JobOut]


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    audience: Literal["guest", "staff"] = "guest"
    top_k: int = Field(default=5, ge=1, le=20)
    language: str | None = None


class PassageOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    doc_type: str
    version: int
    effective_date: date | None
    heading: str
    text: str
    cosine: float
    coverage: float


class CitationOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    version: int
    heading: str


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    audience: Literal["guest", "staff"] = "guest"


class AskOut(BaseModel):
    answer: str
    abstained: bool
    reason: str
    citations: list[CitationOut]
