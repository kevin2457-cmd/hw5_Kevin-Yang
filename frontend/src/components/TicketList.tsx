import { TICKET_TYPES, displayStatus } from '../meta'
import type { Ticket } from '../types'
import { StatusBadge } from './StatusBadge'

interface Props {
  tickets: Ticket[] | null
  selectedId: number
  starting: Set<number>
  onSelect: (id: number) => void
}

export function TicketList({ tickets, selectedId, starting, onSelect }: Props) {
  return (
    <section className="panel ticket-list" aria-label="工单列表">
      <h2 className="panel-title">
        工单 <span className="muted">Desk Tickets</span>
      </h2>
      {tickets === null && <p className="empty">正在加载工单…</p>}
      {tickets?.length === 0 && <p className="empty">后端没有返回工单。</p>}
      <ul>
        {tickets?.map((t) => {
          const st = displayStatus(t, starting.has(t.id))
          return (
            <li key={t.id}>
              <button
                className={`ticket-card ${t.id === selectedId ? 'selected' : ''} edge-${st}`}
                onClick={() => onSelect(t.id)}
                aria-pressed={t.id === selectedId}
              >
                <div className="ticket-card-top">
                  <span className="ticket-no">#{t.id}</span>
                  <StatusBadge status={st} />
                </div>
                <div className="ticket-subject">{t.subject}</div>
                <div className="ticket-meta">
                  <span className="pill">{TICKET_TYPES[t.type] ?? t.type}</span>
                  <span className="truncate">{t.requester}</span>
                </div>
                {t.pending_approvals > 0 && (
                  <div className="ticket-flag">待审批申请 {t.pending_approvals} 笔</div>
                )}
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
