import { useState } from 'react'
import { agentStyle, clock, money } from '../meta'
import type { Approval, Cash } from '../types'

export function CashCard({ cash, pending }: { cash: Cash | null; pending: Approval[] }) {
  const pendingTotal = pending.reduce((s, a) => s + a.amount, 0)
  return (
    <section className="panel cash-card" aria-label="现金余额">
      <div className="cash-label">支票账户 checking</div>
      <div className="cash-amount">{cash ? money(cash.balance) : '—'}</div>
      <div className="cash-meta">
        {cash ? <>余额日期 {cash.as_of} · 店铺今天 {cash.today}</> : '正在读取后端余额…'}
      </div>
      {pending.length > 0 && cash && (
        <div className="cash-pending">
          <div>
            待审批合计 <b>{money(pendingTotal)}</b>（{pending.length} 笔）
          </div>
          <div className={cash.balance - pendingTotal < 0 ? 'err-text' : 'muted'}>
            若全部批准，预计剩余 {money(cash.balance - pendingTotal)}
            {cash.balance - pendingTotal < 0 && '：余额不足，后端会拒绝超额付款'}
          </div>
          <div className="tiny muted">预估仅供参考；真实余额以后端扣款结果为准。</div>
        </div>
      )}
    </section>
  )
}

const KIND_LABEL: Record<string, string> = { rent: '房租', invoice: '供应商发票' }

interface ApprovalProps {
  approvals: Approval[]
  approver: string
  onApprover: (v: string) => void
  onApprove: (a: Approval) => Promise<void>
  onReject: (a: Approval) => Promise<void>
}

export function ApprovalPanel({ approvals, approver, onApprover, onApprove, onReject }: ApprovalProps) {
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const act = async (uid: string, fn: () => Promise<void>) => {
    setBusy(uid)
    try {
      await fn()
    } finally {
      setBusy(null)
      setConfirming(null)
    }
  }

  return (
    <section className="panel approvals" aria-label="待审批申请">
      <h2 className="panel-title">
        待人工审批 <span className="count">{approvals.length}</span>
      </h2>
      <label className="approver">
        <span>审批人姓名</span>
        <input value={approver} onChange={(e) => onApprover(e.target.value)} placeholder="填写你的姓名后才能批准" />
      </label>
      {approvals.length === 0 && (
        <p className="empty">目前没有待审批的付款或采购申请。智能体只能提出申请，付款必须由你在这里批准。</p>
      )}
      {approvals.map((a) => {
        const by = agentStyle(a.requested_by)
        const isConfirming = confirming === a.uid
        return (
          <article key={a.uid} className="approval">
            <div className="approval-top">
              <span className="pill pill-amber">⏳ 待审批</span>
              <span className="muted small">
                工单 #{a.ticket_id ?? '—'} · 申请 #{a.id}
              </span>
            </div>
            <div className="approval-payee">{a.payee}</div>
            <div className="approval-amount">{money(a.amount)}</div>
            <dl className="approval-facts">
              <div>
                <dt>类型</dt>
                <dd>
                  {KIND_LABEL[a.kind] ?? a.kind} #{a.ref_id} · 到期 {a.due_date}
                </dd>
              </div>
              <div>
                <dt>用途</dt>
                <dd>{a.reason}</dd>
              </div>
              <div>
                <dt>申请人</dt>
                <dd style={{ color: by.color }}>
                  {by.icon} {by.title} · {clock(a.created_at)}
                </dd>
              </div>
            </dl>
            {!isConfirming ? (
              <div className="approval-actions">
                <button className="btn btn-approve" disabled={!approver.trim() || busy !== null} onClick={() => setConfirming(a.uid)}>
                  批准并支付 {money(a.amount)}
                </button>
                <button
                  className="btn btn-ghost"
                  disabled={!approver.trim() || busy !== null}
                  onClick={() => act(a.uid, () => onReject(a))}
                >
                  拒绝
                </button>
              </div>
            ) : (
              <div className="confirm-box">
                <p>
                  确认以 <b>{approver}</b> 的名义，从 checking 向 <b>{a.payee}</b> 支付 <b>{money(a.amount)}</b>？
                </p>
                <div className="approval-actions">
                  <button className="btn btn-approve" disabled={busy !== null} onClick={() => act(a.uid, () => onApprove(a))}>
                    {busy === a.uid ? '正在扣款…' : '确认支付'}
                  </button>
                  <button className="btn btn-ghost" disabled={busy !== null} onClick={() => setConfirming(null)}>
                    取消
                  </button>
                </div>
              </div>
            )}
            {!approver.trim() && <div className="hint">请先在上方填写审批人姓名。</div>}
          </article>
        )
      })}
    </section>
  )
}

export function AdminPanel({ disabled, onReset }: { disabled: boolean; onReset: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  return (
    <section className="panel admin" aria-label="演示数据">
      <h2 className="panel-title">演示数据</h2>
      <p className="small muted">把工作副本恢复为原始数据库（原始文件不变）。旧的待审批申请会失效，审计记录保留。</p>
      <button
        className="btn btn-danger"
        disabled={disabled || busy}
        onClick={async () => {
          if (window.prompt('这会清除所有付款、申请和工单进度。请输入 RESET 确认：') !== 'RESET') return
          setBusy(true)
          try {
            await onReset()
          } finally {
            setBusy(false)
          }
        }}
      >
        {busy ? '正在重置…' : '重置数据库'}
      </button>
      {disabled && <div className="hint">有工单正在运行时不能重置。</div>}
    </section>
  )
}
