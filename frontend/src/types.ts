export interface Customer {
  id: number;
  name: string;
  contact: string;
  note: string;
  order_count?: number;
}

export interface OrderBrief {
  id: number;
  order_no: string;
  customer_id: number;
  customer_name: string;
  product: string;
  quantity: number;
  delivered_qty?: number;
  delivery_date: string;
  status: string;
  owner: string;
  pending_count: number;
  deleted_at?: string | null;
}

export interface Supply {
  product: string;
  on_hand: number;
  reserved: number;
  committable: number;
  available_qty: number;
  earliest_completion: string;
  note: string;
  updated_at?: string;
  updated_by?: string;
}

export interface BusinessEvent {
  id: number;
  kind: "event";
  order_id: number;
  source_id: number;
  source_title: string;
  source_type: string;
  business_time: string | null;
  event_type: string;
  field_name: string | null;
  current_value: string | null;
  proposed_value: string | null;
  proposal_origin: string;
  evidence_excerpt: string;
  evidence_row: number | null;
  evidence_col: number | null;
  status: string;
  confidence: number;
  explanation: string;
  conflict_flag: boolean;
  conflict_note: string;
  needs_review: boolean;
  created_at: string;
}

export interface Risk {
  id: number;
  order_id: number;
  source_id: number | null;
  code: string;
  message: string;
  event_ids: number[];
  evidence_excerpt: string;
  created_at: string;
}

export interface TaskSuggestion {
  id: number;
  kind: "task";
  order_id: number;
  source_id: number | null;
  suggestion_key: string;
  title: string;
  owner: string;
  due_date: string | null;
  reason: string;
  action: string;
  target_task_id: number | null;
  order_status: string | null;
  event_ids: number[];
  status: string;
  evidence_excerpt: string;
  created_at: string;
}

export interface TaskItem {
  id: number;
  kind?: "task" | "suggestion";
  suggestion_id?: number | null;
  action?: string;
  order_id: number | null;
  order_no: string;
  suggestion_key: string;
  title: string;
  owner: string;
  due_date: string | null;
  status: string;
  reason: string;
  event_ids: number[];
  source_id: number | null;
  evidence_excerpt: string;
  created_at: string;
  updated_at: string;
}

export interface AuditItem {
  id: number;
  object_type: string;
  object_id: number;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  operator: string;
  note: string;
  created_at: string;
}

export interface ImportRecord {
  id: number;
  scope: "order" | "customer";
  title: string;
  created_at: string;
  summary: string;
}

export interface OrderDetail {
  order: OrderBrief;
  customer: Customer | null;
  supply: Supply | null;
  timeline: BusinessEvent[];
  audits: AuditItem[];
  imports: ImportRecord[];
}

export interface Drafts {
  events: BusinessEvent[];
  risks: Risk[];
  task_suggestions: TaskSuggestion[];
  source_id?: number;
}

export interface Sample {
  id: string;
  order_no: string;
  title: string;
  type: string;
  business_time?: string;
  text: string;
}

export interface WarehouseItem {
  product: string;
  on_hand: number;
  reserved: number;
  committable: number;
  earliest_completion: string;
  note: string;
  updated_at?: string;
  updated_by?: string;
  order_id: number | null;
}

export interface AssistantAction {
  type:
    | "navigate"
    | "open_evidence"
    | "create_source"
    | "confirm_draft"
    | "reject_draft"
    | "patch_task"
    | "update_supplies"
    | "delete_supply";
  route?: "workbench" | "review" | "tasks" | "warehouse";
  order_id?: number | null;
  order_no?: string;
  customer_name?: string;
  product?: string;
  source_id?: number | null;
  excerpt?: string;
  row?: number | null;
  col?: number | null;
  source_type?: string;
  title?: string;
  text?: string;
  kind?: "event" | "task";
  draft_id?: number;
  apply_to_order?: boolean;
  can_apply?: boolean;
  field_name?: string;
  current_value?: string;
  proposed_value?: string;
  reason?: string;
  owner?: string;
  due_date?: string | null;
  task_id?: number;
  summary?: string;
  items?: { product: string; on_hand: number; earliest_completion: string; note?: string; name?: string }[];
  before?: { title?: string; owner?: string; status?: string; due_date?: string | null };
  patch?: { title?: string; owner?: string; status?: string; due_date?: string };
}

export interface Evidence {
  id: number;
  order_id: number;
  type: string;
  title: string;
  raw_text: string;
  table: { columns: string[]; rows: string[][] } | null;
  business_time: string | null;
  file_name: string | null;
  created_at: string;
}
