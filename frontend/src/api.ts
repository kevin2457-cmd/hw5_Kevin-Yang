import type {
  Approval,
  ApprovalResult,
  Cash,
  EventList,
  ResetResult,
  RunStarted,
  TicketAgents,
  TicketList,
} from './types'

// 默认连接本机 FastAPI 后端；隔离测试时可以用 VITE_API_BASE 指向测试后端
export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(API_BASE + path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(0, 'offline', `无法连接后端 ${API_BASE}，请确认已在 backend/ 中运行 uvicorn main:app --reload --port 8000。`)
  }
  const text = await res.text()
  const body = text ? JSON.parse(text) : null
  if (!res.ok) {
    const detail = body?.detail
    if (detail && typeof detail === 'object' && 'message' in detail) {
      throw new ApiError(res.status, String(detail.code ?? 'error'), String(detail.message))
    }
    const message = Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg).join('；') : String(detail ?? res.statusText)
    throw new ApiError(res.status, 'error', `请求失败（${res.status}）：${message}`)
  }
  return body as T
}

export const api = {
  tickets: () => request<TicketList>('/api/tickets'),
  cash: () => request<Cash>('/api/cash'),
  approvals: (status = 'pending') => request<Approval[]>(`/api/approvals?status=${encodeURIComponent(status)}`),
  ticketAgents: (ticketId: number) => request<TicketAgents>(`/api/tickets/${ticketId}/agents`),
  events: (ticketId: number, after: number) =>
    request<EventList>(`/api/events?ticket_id=${ticketId}&after=${after}&limit=1000`),
  runTicket: (ticketId: number) => request<RunStarted>(`/api/tickets/${ticketId}/run`, { method: 'POST' }),
  approve: (uid: string, body: { approved_by: string; amount: number; payee: string }) =>
    request<ApprovalResult>(`/api/approvals/${uid}/approve`, { method: 'POST', body: JSON.stringify(body) }),
  reject: (uid: string, body: { rejected_by: string; reason?: string }) =>
    request<ApprovalResult>(`/api/approvals/${uid}/reject`, { method: 'POST', body: JSON.stringify(body) }),
  reset: () => request<ResetResult>('/api/reset', { method: 'POST', body: JSON.stringify({ confirm: 'RESET' }) }),
}
