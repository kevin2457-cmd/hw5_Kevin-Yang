import { AGENTS, AGENT_ORDER, agentStyle } from '../meta'
import type { AgentWork, TicketAgents } from '../types'
import { RichText } from './RichText'

const STATE_LABEL: Record<AgentWork['state'], string> = { idle: '本次未参与', working: '工作中', done: '已完成' }

export function AgentRoster({ data, hasRun }: { data: TicketAgents | null; hasRun: boolean }) {
  const running = data?.run?.status === 'running'
  const byName = new Map(data?.agents.map((a) => [a.agent, a]))
  return (
    <section className="panel" aria-label="五个智能体">
      <h2 className="panel-title">
        智能体团队 <span className="muted">各角色在本工单中的工作</span>
      </h2>
      <div className="roster">
        {AGENT_ORDER.map((name) => {
          const s = AGENTS[name]
          const w = byName.get(name)
          const state = w?.state ?? 'idle'
          return (
            <article
              key={name}
              className={`agent-card agent-${state}`}
              style={{ '--agent': s.color, '--agent-soft': s.soft } as React.CSSProperties}
            >
              <header>
                <span className="agent-icon" aria-hidden>
                  {s.icon}
                </span>
                <div className="min0">
                  <div className="agent-name">{s.title}</div>
                  <div className="agent-tag">{s.tag}</div>
                </div>
                <span className={`agent-state state-${state}`}>
                  {state === 'working' && <span className="dot-pulse" aria-hidden />}
                  {!hasRun || (running && state === 'idle') ? '待命' : STATE_LABEL[state]}
                </span>
              </header>
              <div className="agent-body">
                {!hasRun && <p className="muted small">尚未运行。</p>}
                {hasRun && state === 'idle' && <p className="muted small">{running ? '暂未被委派，随时待命。' : '本次未参与'}</p>}
                {state === 'working' && w?.last_activity && <p className="agent-now">正在：{w.last_activity}</p>}
                {state === 'done' && (
                  <p className="agent-summary" title={w?.summary ?? ''}>
                    {w?.summary ? <RichText text={w.summary} /> : '已结束，但没有交回总结。'}
                  </p>
                )}
                {w && (w.tools_used.length > 0 || w.delegated_to.length > 0) && (
                  <div className="chips">
                    {w.delegated_to.map((t) => (
                      <span key={t} className="chip chip-agent" style={{ '--agent': agentStyle(t).color } as React.CSSProperties}>
                        → {agentStyle(t).title}
                      </span>
                    ))}
                    {w.tools_used.map((t) => (
                      <code key={t} className="chip chip-tool">
                        {t}
                      </code>
                    ))}
                  </div>
                )}
                {w?.needs_human && <div className="human-flag">需要真人处理</div>}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
