export type Role = "admin" | "manager" | "receptionist" | "department_staff" | "guest";
export interface Me { id: string; email: string; full_name: string; role: Role; organization_id: string }
export interface Hotel { id: string; name: string; slug: string; timezone: string; currency: string }
export interface Department { id: string; code: string; name: string }
export interface StaffMember { id: string; full_name: string; role: string; departments: string[] }

export type TicketStatus = "open" | "assigned" | "in_progress" | "resolved" | "closed" | "escalated";
export type Priority = "low" | "normal" | "high" | "urgent";
export interface Ticket {
  id: string; ticket_number: string; category: string; description: string;
  room_number: string | null; priority: Priority; status: TicketStatus;
  department_id: string | null; assigned_to: string | null; due_at: string | null;
  resolved_at: string | null; internal_notes: { author_id: string; at: string; text: string }[];
  department_name?: string | null; assignee_name?: string | null; created_at?: string | null;
  guest_name?: string | null;
}
export interface HistoryEvent {
  action: string; actor_type: string; actor_name: string | null; created_at: string;
  details: Record<string, unknown>;
}

export interface Reservation {
  id: string; confirmation_code: string; status: string; check_in: string; check_out: string;
  adults: number; total_price: string; created_at: string; room_type_id: string;
  room_type_name: string; room_number: string | null;
  guest: { id: string; full_name: string; email: string | null; phone: string | null };
}
export interface InventoryRow {
  room_type_id: string; room_type_name: string;
  nights: { night: string; total: number; booked: number; rate: string }[];
}

export interface ConversationRow {
  id: string; channel: string; status: string; guest_name: string | null;
  last_activity: string; handoff_ticket: string | null;
}
export interface ChatMessage {
  id: string; role: "user" | "assistant" | "staff" | "system"; content: string;
  created_at: string; citations?: Citation[];
}
export interface Citation { title: string; version: number; heading: string; document_id: string; chunk_id: string }

export interface Approval {
  id: string; kind: string; status: string; payload: Record<string, unknown>;
  created_at: string; decided_at: string | null;
}
export interface Notification {
  id: string; kind: string; severity: "info" | "warning" | "critical"; title: string;
  entity_type: string | null; entity_id: string | null; created_at: string; read: boolean;
}
export interface Analytics {
  window_days: number; open_requests: number; open_by_department: Record<string, number>;
  open_by_priority: Record<string, number>; overdue: number; created_in_window: number;
  avg_resolution_hours: number | null; escalations_in_window: number;
  complaints_in_window: number; complaint_themes: Record<string, number>;
  active_reservations: number; reservations_created: number; reservations_cancelled: number;
  avg_feedback_rating: number | null;
  feedback_trend: { date: string; avg_rating: number; count: number }[];
  tickets_per_day: { date: string; count: number }[];
}
export interface Integration {
  id: string | null; provider: string; enabled: boolean; config: Record<string, string>;
  credentials_present: boolean; webhook_url: string | null; status: string;
}

export interface KnowledgeDoc {
  id: string; title: string; doc_key: string; doc_type: string; language: string; version: number;
  effective_date: string | null; is_active: boolean; is_confidential: boolean; is_demo: boolean;
  status: string; source: string | null; created_at: string;
}
export interface IngestionJob { id: string; status: string; error: string | null; duration_ms: number | null; created_at: string }
export interface KnowledgeDocDetail extends KnowledgeDoc { chunk_count: number; jobs: IngestionJob[] }
export interface Passage {
  chunk_id: string; document_id: string; title: string; doc_type: string; version: number;
  effective_date: string | null; heading: string; text: string; cosine: number; coverage: number;
}
export interface AskResult { answer: string; abstained: boolean; reason: string; citations: Citation[] }

export interface AiKpis {
  window_days: number; turns: number; conversations: number; resolved_conversations: number;
  handed_off_conversations: number; unresolved_conversations: number;
  resolution_rate: number | null; handoff_rate: number | null; turn_escalation_rate: number | null;
  knowledge_abstain_rate: number | null; reservation_flows_started: number;
  reservation_completion_rate: number | null; agent_tickets_created: number;
  agent_ticket_resolution_rate: number | null; tool_calls: number; tool_failures: number;
  tool_success_rate: number | null; retrieval_avg_latency_ms: number | null;
  turn_latency_p50_ms: number | null; turn_latency_p95_ms: number | null;
  model_latency_avg_ms: number | null; tokens_in: number; tokens_out: number;
  estimated_cost_usd: number; estimated_cost_per_conversation_usd: number | null;
  avg_feedback_rating: number | null; feedback_count: number; low_rated_answers: number;
  outcomes: Record<string, number>;
}
export interface ToolCallTrace { tool: string; success: boolean; error: string | null; latency_ms: number | null; arguments: Record<string, unknown> }
export interface AiExecution {
  id: string; created_at: string; channel: string; correlation_id: string; intent: string | null;
  outcome: string | null; steps: number; latency_ms: number | null; llm_ms: number; tokens_in: number;
  tokens_out: number; estimated_cost_usd: number; trace: unknown[]; tools: ToolCallTrace[];
}
export interface ToolStat { tool: string; calls: number; failures: number; success_rate: number | null; avg_latency_ms: number | null }
export interface Unanswered { at: string; intent: string | null; outcome: string; question: string | null; conversation_id: string | null }
export interface Flagged { at: string; rating: number; comment: string | null; answer: string; question: string | null; outcome: string | null; conversation_id: string }
export interface AuditRow {
  id: string; at: string; action: string; actor_type: string; actor_name: string | null;
  entity_type: string | null; entity_id: string | null; details: Record<string, unknown>;
}

export interface AdminUser {
  id: string; email: string; full_name: string; role: string; is_active: boolean;
  last_login_at: string | null;
  assignments: { hotel_id: string; department_id: string | null; hotel_name: string; department_name: string | null }[];
}
