"""Prometheus metrics. Labels are low-cardinality only (route templates, enums); never ids,
guest text or other personal data."""
from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
AGENT_TURNS = Counter("agent_turns_total", "Agent turns", ["intent", "outcome"])
AGENT_LATENCY = Histogram("agent_turn_duration_seconds", "Agent turn latency",
                          buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
AGENT_STEPS = Histogram("agent_turn_steps", "Graph steps per turn", buckets=(1, 2, 3, 4, 6, 8, 12))
TOOL_CALLS = Counter("tool_calls_total", "Tool calls", ["tool", "result"])
TOOL_LATENCY = Histogram("tool_call_duration_seconds", "Tool latency", ["tool"],
                         buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 5))
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["direction"])
LLM_CALLS = Counter("llm_calls_total", "LLM calls", ["result"])
KB_ANSWERS = Counter("knowledge_answers_total", "Knowledge answers", ["result"])
KB_CACHE = Counter("knowledge_cache_total", "Answer cache lookups", ["result"])
TICKETS = Counter("tickets_created_total", "Tickets created", ["category"])
RESERVATIONS = Counter("reservation_events_total", "Reservation events", ["event"])
OUTBOUND = Counter("outbound_messages_total", "Outbound delivery attempts", ["channel", "result"])
WEBHOOKS = Counter("webhook_events_total", "Inbound webhook deliveries", ["provider", "result"])
RATE_LIMITED = Counter("rate_limited_total", "Requests rejected by rate limits", ["scope"])
RATE_LIMIT_ERRORS = Counter("rate_limit_backend_errors_total", "Rate limit backend failures")
