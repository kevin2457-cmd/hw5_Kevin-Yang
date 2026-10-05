import type { AgentName, Ticket } from './types'

export interface AgentStyle {
  title: string
  tag: string
  icon: string
  color: string
  soft: string
}

// 五个智能体：不同颜色 + 图标 + 角色标签
export const AGENTS: Record<AgentName, AgentStyle> = {
  boss: { title: '总负责人', tag: '统筹决策', icon: '🎓', color: '#b0812a', soft: '#f7ecd4' },
  inventory: { title: '库存负责人', tag: '货架与补货', icon: '📦', color: '#1f7470', soft: '#dcefed' },
  finance: { title: '财务负责人', tag: '现金与付款', icon: '💰', color: '#2f6f3e', soft: '#e0efe2' },
  facilities: { title: '场地负责人', tag: '租约与店面', icon: '🏛️', color: '#a64b2a', soft: '#f6e3da' },
  customer_service: { title: '客服负责人', tag: '客户草稿', icon: '💬', color: '#6c4a8a', soft: '#ece3f3' },
}

export const AGENT_ORDER: AgentName[] = ['boss', 'inventory', 'finance', 'facilities', 'customer_service']

export function agentStyle(name: string | null | undefined): AgentStyle {
  if (name && name in AGENTS) return AGENTS[name as AgentName]
  if (name === 'human') return { title: '人工审批人', tag: '真人', icon: '✍️', color: '#13294b', soft: '#e3e8f1' }
  return { title: '系统', tag: '后端', icon: '⚙️', color: '#5b6573', soft: '#eceef1' }
}

export const TICKET_TYPES: Record<string, string> = {
  customer_order: '客户订单',
  rent_notice: '房租通知',
  price_override: '价格调整',
}

export type DisplayStatus = 'running' | 'waiting' | 'resolved' | 'failed' | 'limited' | 'open'

export const STATUS_LABEL: Record<DisplayStatus, string> = {
  running: '运行中',
  waiting: '等待审批',
  resolved: '已解决',
  failed: '运行失败',
  limited: '达到运行限制',
  open: '未处理',
}

/** 只依据后端返回的真实状态判断；等待审批、失败、达到限制都不会被当作已解决。 */
export function displayStatus(t: Ticket, starting = false): DisplayStatus {
  if (starting || t.running || t.last_run?.status === 'running') return 'running'
  if (t.status === 'resolved') return 'resolved'
  if (t.status === 'waiting_approval') return 'waiting'
  if (t.last_run?.status === 'failed' || t.last_run?.status === 'interrupted') return 'failed'
  if (t.last_run?.status === 'stopped_by_limit') return 'limited'
  return 'open'
}

export const money = (n: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n)

export const clock = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString('zh-CN', { hour12: false })
}
