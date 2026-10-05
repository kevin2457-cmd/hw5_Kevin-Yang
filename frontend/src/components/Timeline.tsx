import { useEffect, useRef } from 'react'
import { agentStyle, clock } from '../meta'
import type { AgentEvent, LastRun } from '../types'
import { RichText } from './RichText'

const KIND: Record<string, { label: string; cls: string }> = {
  run_start: { label: '开始', cls: 'k-system' },
  agent_start: { label: '上岗', cls: 'k-system' },
  agent_message: { label: '发言', cls: 'k-message' },
  mcp_tool_result: { label: '工具', cls: 'k-tool' },
  delegate: { label: '委派', cls: 'k-delegate' },
  delegation_result: { label: '回复', cls: 'k-reply' },
  delegation_refused: { label: '委派被拒', cls: 'k-error' },
  agent_finished: { label: '完成', cls: 'k-done' },
  run_end: { label: '结束', cls: 'k-system' },
  ticket_status: { label: '状态', cls: 'k-system' },
  human_approval: { label: '人工', cls: 'k-human' },
  human_rejection: { label: '人工', cls: 'k-human' },
  database_reset: { label: '重置', cls: 'k-system' },
  run_error: { label: '错误', cls: 'k-error' },
}

function Body({ e }: { e: AgentEvent }) {
  const target = agentStyle(e.target)
  switch (e.action) {
    case 'agent_message':
      return <p><RichText text={e.result} /></p>
    case 'delegate':
      return (
        <p>
          委派给 <b style={{ color: target.color }}>{target.icon} {target.title}</b>：{e.input}
        </p>
      )
    case 'delegation_result':
      return (
        <p>
          <b style={{ color: target.color }}>{target.icon} {target.title}</b> 交回结果：<RichText text={e.result} />
        </p>
      )
    case 'mcp_tool_result':
      return (
        <details>
          <summary>
            调用 <code>{e.target}</code>
            {e.status === 'refused' && <span className="warn-text">（被拒绝）</span>}
            {e.status === 'error' && <span className="err-text">（出错）</span>}
          </summary>
          {e.input && <div className="io"><span>参数</span>{e.input}</div>}
          <div className="io"><span>结果</span>{e.result}</div>
        </details>
      )
    case 'agent_finished':
      return <p><RichText text={e.result} /></p>
    default:
      return <p>{e.message}</p>
  }
}

export function Timeline({ events, run, loading }: { events: AgentEvent[]; run: LastRun | null; loading: boolean }) {
  const endRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLOListElement>(null)

  // 用户停在底部时，新事件到来自动滚动
  useEffect(() => {
    const el = listRef.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [events.length])

  const route = events
    .filter((e) => e.action === 'delegate')
    .map((e) => [agentStyle(e.agent), agentStyle(e.target)] as const)

  return (
    <section className="panel timeline" aria-label="实时工作过程">
      <h2 className="panel-title">
        实时工作过程 <span className="muted">来自审计记录 · 每 2 秒刷新</span>
        {run?.status === 'running' && <span className="live">● LIVE</span>}
      </h2>

      {route.length > 0 && (
        <div className="route" aria-label="协作路线">
          <span className="route-label">协作路线</span>
          {route.map(([from, to], i) => (
            <span key={i} className="route-hop">
              <span style={{ color: from.color }}>{from.icon} {from.title}</span>
              <span className="arrow">→</span>
              <span style={{ color: to.color }}>{to.icon} {to.title}</span>
            </span>
          ))}
        </div>
      )}

      {loading && events.length === 0 && <p className="empty">正在加载事件…</p>}
      {!loading && events.length === 0 && (
        <p className="empty">{run ? '这次运行还没有可显示的事件。' : '还没有事件。启动智能体团队后，这里会逐条显示发言、委派和工具调用。'}</p>
      )}

      <ol className="events" ref={listRef}>
        {events.map((e) => {
          const who = agentStyle(e.agent)
          const kind = KIND[e.action] ?? { label: e.action, cls: 'k-system' }
          return (
            <li
              key={e.id}
              className={`event ${kind.cls}`}
              style={{ '--agent': who.color, '--agent-soft': who.soft, marginLeft: `${Math.min(e.depth, 3) * 14}px` } as React.CSSProperties}
            >
              <div className="event-head">
                <span className="event-who">
                  {who.icon} {e.agent_title || who.title}
                </span>
                <span className="event-kind">{kind.label}</span>
                {e.needs_human_approval && <span className="event-human">需人工</span>}
                <time>{clock(e.time)}</time>
              </div>
              <div className="event-body">
                <Body e={e} />
              </div>
            </li>
          )
        })}
        <div ref={endRef} />
      </ol>
    </section>
  )
}
