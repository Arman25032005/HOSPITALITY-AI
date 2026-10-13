import time
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.config import Settings
from app.conversations.state import ConversationState
from app.llm.base import LLMProvider
from app.models import AgentExecution, Conversation, Hotel, User
from app.rag.embeddings import EmbeddingProvider
from app.rag.vectorstore import VectorStore


@dataclass
class Reply:
    text: str
    citations: list[dict] = field(default_factory=list)
    awaiting_confirmation: bool = False
    reservation_codes: list[str] = field(default_factory=list)
    ticket_numbers: list[str] = field(default_factory=list)
    outcome: str = "answered"  # answered|abstained|action_pending|action_done|escalated|handoff|fallback|needs_info|error
    data: dict = field(default_factory=dict)


@dataclass
class AgentContext:
    db: Session
    settings: Settings
    hotel: Hotel
    conv: Conversation
    state: ConversationState
    actor: str  # "guest" | "staff"
    user: User | None
    llm: LLMProvider | None
    embedder: EmbeddingProvider
    store: VectorStore
    execution: AgentExecution
    today: date = field(default_factory=date.today)
    facts: set[str] = field(default_factory=set)  # codes/numbers read back from the database
    steps: int = 0
    tool_calls: int = 0
    failed_calls: set[str] = field(default_factory=set)
    trace: list[dict] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: float = field(default_factory=time.monotonic)

    @property
    def org_id(self):
        return self.hotel.organization_id

    @property
    def hotel_id(self):
        return self.hotel.id

    def note(self, node: str, info: str = "") -> None:
        self.trace.append({"node": node, "ms": int((time.monotonic() - self.started) * 1000),
                           "info": info[:200]})
