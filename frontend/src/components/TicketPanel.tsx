import { STATUS_LABEL, TICKET_TYPES, clock, displayStatus } from '../meta'
import type { Ticket, TicketAgents } from '../types'
import { RichText } from './RichText'
import { StatusBadge } from './StatusBadge'

interface Props {
  ticket: Ticket | undefined
  agents: TicketAgents | null
  starting: boolean
  busyReset: boolean
  onRun: () => void
}

const RUN_STATUS: Record<string, string> = {
  running: '运行中',
  completed: '已完成运行',
  stopped_by_limit: '达到运行限制而停止',
  failed: '运行失败',
  interrupted: '运行中断（后端可能已重启）',
}

export function TicketPanel({ ticket, agents, starting, busyReset, onRun }: Props) {
  if (!ticket) return <section className="panel ticket-panel"><p className="empty">请选择左侧的工单。</p></section>
  const st = displayStatus(ticket, starting)
  const run = agents?.run ?? null
  const boss = agents?.agents.find((a) => a.agent === 'boss')

  let disabledReason: string | null = null
  if (st === 'running') disabledReason = '智能体正在处理这张工单'
  else if (st === 'resolved') disabledReason = '工单已解决（重置数据库后才能再次运行）'
  else if (st === 'waiting') disabledReason = '等待人工审批，请在右侧处理付款申请'
  else if (busyReset) disabledReason = '数据库正在重置'

  return (
    <section className={`panel ticket-panel edge-${st}`} aria-label="当前工单">
      <div className="ticket-head">
        <div className="min0">
          <div className="eyebrow">
            工单 #{ticket.id} · {TICKET_TYPES[ticket.type] ?? ticket.type} · 创建于 {ticket.created_at.slice(0, 16).replace('T', ' ')}
          </div>
          <h1 className="ticket-title">{ticket.subject}</h1>
          <div className="ticket-requester">申请人：{ticket.requester}</div>
        </div>
        <div className="ticket-actions">
          <StatusBadge status={st} />
          <button className="btn btn-primary" onClick={onRun} disabled={disabledReason !== null}>
            {st === 'running' ? (
              <>
                <span className="spinner" aria-hidden /> 团队工作中…
              </>
            ) : (
              '▶ 启动智能体团队'
            )}
          </button>
          {disabledReason && st !== 'running' && <div className="hint">{disabledReason}</div>}
        </div>
      </div>

      <dl className="facts">
        {ticket.sku && (
          <div>
            <dt>商品</dt>
            <dd>
              <code>{ticket.sku}</code> · 尺码 {ticket.size ?? '—'} · 数量 {ticket.qty ?? '—'}
            </dd>
          </div>
        )}
        <div>
          <dt>原文备注</dt>
          <dd className="quote">“{ticket.notes ?? '—'}”</dd>
        </div>
        <div>
          <dt>数据库状态</dt>
          <dd>
            <code>{ticket.status}</code>
            {ticket.pending_approvals > 0 && <span className="warn-text"> · 待审批申请 {ticket.pending_approvals} 笔</span>}
          </dd>
        </div>
      </dl>

      {run ? (
        <div className={`run-strip run-${run.status}`}>
          <span>
            最近一次运行 <code>{run.run_id}</code>：<strong>{RUN_STATUS[run.status] ?? run.status}</strong>
          </span>
          <span className="muted">
            {clock(run.started_at)}
            {run.ended_at ? ` → ${clock(run.ended_at)}` : ''}
          </span>
          {run.stop_reason && <div className="run-reason">原因：{run.stop_reason}</div>}
        </div>
      ) : (
        <div className="run-strip run-none">这张工单还没有运行记录。点击“启动智能体团队”后，过程会实时显示在下方。</div>
      )}

      {boss?.summary && run?.status !== 'running' && (
        <div className="verdict">
          <div className="verdict-label">🎓 总负责人的最终判断</div>
          <p><RichText text={boss.summary} /></p>
          {st === 'waiting' && <p className="warn-text">⏳ 需要人工批准付款后，工单才会标记为“{STATUS_LABEL.resolved}”。</p>}
        </div>
      )}
    </section>
  )
}
