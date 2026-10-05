import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE, ApiError, api } from './api'
import { AgentRoster } from './components/AgentRoster'
import { AdminPanel, ApprovalPanel, CashCard } from './components/SidePanels'
import { TicketList } from './components/TicketList'
import { TicketPanel } from './components/TicketPanel'
import { Timeline } from './components/Timeline'
import { money } from './meta'
import type { AgentEvent, Approval, Cash, Ticket, TicketAgents } from './types'

type Notice = { kind: 'ok' | 'error' | 'info'; text: string }

const FAST_POLL = 2000 // 有工单运行时
const SLOW_POLL = 6000

export default function App() {
  const [tickets, setTickets] = useState<Ticket[] | null>(null)
  const [today, setToday] = useState('')
  const [cash, setCash] = useState<Cash | null>(null)
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [selectedId, setSelectedId] = useState<number>(() => Number(localStorage.getItem('cc.selected')) || 101)
  const [agents, setAgents] = useState<TicketAgents | null>(null)
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [eventsLoading, setEventsLoading] = useState(true)
  const [online, setOnline] = useState<boolean | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [starting, setStarting] = useState<Set<number>>(new Set())
  const [resetting, setResetting] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [approver, setApprover] = useState(() => localStorage.getItem('cc.approver') ?? '')

  const selectedRef = useRef(selectedId)
  const cursor = useRef({ ticketId: selectedId, after: -1 })
  const busyRef = useRef(false)

  const refresh = useCallback(async () => {
    const id = selectedRef.current
    try {
      const [t, c, a, ag] = await Promise.all([api.tickets(), api.cash(), api.approvals('pending'), api.ticketAgents(id)])
      if (cursor.current.ticketId !== id) cursor.current = { ticketId: id, after: -1 }
      const ev = await api.events(id, cursor.current.after)
      if (selectedRef.current !== id) return // 用户已切换工单，丢弃旧响应
      setTickets(t.tickets)
      setToday(t.today)
      setCash(c)
      setApprovals(a)
      setAgents(ag)
      if (ev.events.length) setEvents((prev) => [...prev, ...ev.events])
      cursor.current.after = ev.last_id
      setEventsLoading(false)
      setOnline(true)
      setLoadError(null)
      busyRef.current = t.tickets.some((x) => x.running || x.last_run?.status === 'running')
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOnline(false)
      else setLoadError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  // 轮询：有工单运行时 2 秒一次，否则 6 秒一次；刷新页面后完全从后端恢复状态
  useEffect(() => {
    let alive = true
    let timer: number | undefined
    const loop = async () => {
      await refresh()
      if (alive) timer = window.setTimeout(loop, busyRef.current ? FAST_POLL : SLOW_POLL)
    }
    loop()
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [refresh])

  const select = (id: number) => {
    if (id === selectedRef.current) return
    selectedRef.current = id
    cursor.current = { ticketId: id, after: -1 }
    localStorage.setItem('cc.selected', String(id))
    setSelectedId(id)
    setAgents(null)
    setEvents([])
    setEventsLoading(true)
    refresh()
  }

  const runTicket = async (id: number) => {
    if (starting.has(id)) return
    setStarting((s) => new Set(s).add(id))
    try {
      const r = await api.runTicket(id)
      setNotice({ kind: 'info', text: `工单 #${id} 已交给智能体团队（运行 ${r.run_id}）。` })
      busyRef.current = true
      await refresh()
    } catch (e) {
      setNotice({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setStarting((s) => {
        const n = new Set(s)
        n.delete(id)
        return n
      })
    }
  }

  const approve = async (a: Approval) => {
    try {
      await api.approve(a.uid, { approved_by: approver.trim(), amount: a.amount, payee: a.payee })
      const fresh = await api.cash() // 以后端为准重新读取余额，不在前端自行扣减
      setCash(fresh)
      setNotice({ kind: 'ok', text: `已批准：向 ${a.payee} 支付 ${money(a.amount)}。后端确认的当前余额为 ${money(fresh.balance)}。` })
    } catch (e) {
      setNotice({ kind: 'error', text: `批准失败：${e instanceof Error ? e.message : String(e)}` })
    }
    await refresh()
  }

  const reject = async (a: Approval) => {
    try {
      await api.reject(a.uid, { rejected_by: approver.trim() })
      setNotice({ kind: 'info', text: `已拒绝向 ${a.payee} 支付 ${money(a.amount)} 的申请，没有扣款。` })
    } catch (e) {
      setNotice({ kind: 'error', text: `拒绝失败：${e instanceof Error ? e.message : String(e)}` })
    }
    await refresh()
  }

  const reset = async () => {
    setResetting(true)
    try {
      const r = await api.reset()
      cursor.current = { ticketId: selectedRef.current, after: -1 }
      setEvents([])
      setNotice({
        kind: 'ok',
        text: `${r.message}作废申请 ${r.voided_pending_requests.length} 笔；余额 ${r.cash_account ? money(r.cash_account.balance) : '—'}。`,
      })
    } catch (e) {
      setNotice({ kind: 'error', text: `重置失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setResetting(false)
      await refresh()
    }
  }

  useEffect(() => localStorage.setItem('cc.approver', approver), [approver])
  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(() => setNotice(null), notice.kind === 'error' ? 15000 : 8000)
    return () => window.clearTimeout(t)
  }, [notice])

  const ticket = tickets?.find((t) => t.id === selectedId)
  const anyRunning = !!tickets?.some((t) => t.running) || starting.size > 0
  const run = agents?.run ?? null

  // 只显示当前这次运行的事件，以及运行开始之后针对本工单的人工审批 / 状态更新
  const visibleEvents = useMemo(() => {
    if (!run) return []
    return events.filter((e) => e.run_id === run.run_id || (e.run_id === 'backend' && e.time >= run.started_at))
  }, [events, run])

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <img src="/crest.svg" alt="" width={34} height={34} />
          <div>
            <div className="brand-name">Campus Customs</div>
            <div className="brand-sub">校园文创店 · 智能体工作台</div>
          </div>
        </div>
        <div className="topbar-right">
          {today && <span className="today">店铺今天 {today}</span>}
          <span className={`conn conn-${online === null ? 'loading' : online ? 'on' : 'off'}`}>
            {online === null ? '连接中…' : online ? '后端已连接' : '后端未连接'}
          </span>
        </div>
      </header>

      {online === false && (
        <div className="banner banner-error">
          无法连接后端 <code>{API_BASE}</code>。请在 <code>hw5/backend</code> 中运行 <code>uvicorn main:app --reload --port 8000</code>，页面会自动重连。
        </div>
      )}
      {loadError && online !== false && <div className="banner banner-error">请求失败：{loadError}</div>}
      {notice && (
        <div className={`toast toast-${notice.kind}`} role="status" onClick={() => setNotice(null)}>
          {notice.text}
        </div>
      )}

      <main className="layout">
        <aside className="col-left">
          <TicketList tickets={tickets} selectedId={selectedId} starting={starting} onSelect={select} />
        </aside>

        <div className="col-main">
          <TicketPanel
            ticket={ticket}
            agents={agents}
            starting={starting.has(selectedId)}
            busyReset={resetting}
            onRun={() => runTicket(selectedId)}
          />
          <AgentRoster data={agents} hasRun={!!run} />
          <Timeline events={visibleEvents} run={run} loading={eventsLoading} />
        </div>

        <aside className="col-right">
          <CashCard cash={cash} pending={approvals} />
          <ApprovalPanel approvals={approvals} approver={approver} onApprover={setApprover} onApprove={approve} onReject={reject} />
          <AdminPanel disabled={anyRunning || resetting} onReset={reset} />
        </aside>
      </main>
    </div>
  )
}
