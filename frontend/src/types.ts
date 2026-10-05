// 与 backend/main.py 的响应结构一一对应

export type AgentName = 'boss' | 'inventory' | 'finance' | 'facilities' | 'customer_service'

export type RunStatus = 'running' | 'completed' | 'stopped_by_limit' | 'failed' | 'interrupted'

export interface LastRun {
  run_id: string
  status: RunStatus
  started_at: string
  ended_at: string | null
  stop_reason: string | null
}

export interface Ticket {
  id: number
  type: string
  requester: string
  subject: string
  sku: string | null
  size: string | null
  qty: number | null
  notes: string | null
  created_at: string
  status: 'open' | 'waiting_approval' | 'resolved' | string
  resolved: boolean
  pending_approvals: number
  running: boolean
  latest_run_id: string | null
  last_run: LastRun | null
}

export interface TicketList {
  today: string
  tickets: Ticket[]
}

export interface Cash {
  account: string
  balance: number
  as_of: string
  today: string
}

export interface Approval {
  uid: string
  id: number
  ticket_id: number | null
  kind: 'rent' | 'invoice' | string
  ref_id: number
  payee: string
  amount: number
  account: string
  due_date: string
  requested_by: string
  reason: string
  status: string
  approved_by: string | null
  decided_at: string | null
  payment_id: number | null
  created_at: string
}

export interface ApprovalResult {
  ok: boolean
  request: Approval
  payment: Record<string, unknown> | null
  cash_account: { name: string; balance: number } | null
  ticket_status: string | null
}

export interface AgentEvent {
  id: number
  time: string
  run_id: string
  ticket_id: number | null
  agent: string
  agent_title: string
  action: string
  target: string | null
  message: string
  input: string | null
  result: string | null
  status: string
  needs_human_approval: boolean
  depth: number
}

export interface EventList {
  events: AgentEvent[]
  last_id: number
}

export interface AgentWork {
  agent: AgentName
  title: string
  participated: boolean
  state: 'idle' | 'working' | 'done'
  summary: string | null
  last_activity: string | null
  tools_used: string[]
  delegated_to: string[]
  needs_human: boolean
}

export interface TicketAgents {
  ticket_id: number
  run: LastRun | null
  agents: AgentWork[]
}

export interface RunStarted {
  run_id: string
  ticket_id: number
  status: 'running'
  message: string
}

export interface ResetResult {
  ok: boolean
  voided_pending_requests: Record<string, unknown>[]
  cash_account: { name: string; balance: number } | null
  message: string
}
