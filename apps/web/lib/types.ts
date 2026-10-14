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
