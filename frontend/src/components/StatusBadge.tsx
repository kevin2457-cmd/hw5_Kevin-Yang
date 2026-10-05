import { STATUS_LABEL, type DisplayStatus } from '../meta'

const ICON: Record<DisplayStatus, string> = {
  running: '◐',
  waiting: '⏳',
  resolved: '✓',
  failed: '✕',
  limited: '⛔',
  open: '○',
}

export function StatusBadge({ status }: { status: DisplayStatus }) {
  return (
    <span className={`status status-${status}`}>
      <span className="status-icon" aria-hidden>
        {ICON[status]}
      </span>
      {STATUS_LABEL[status]}
    </span>
  )
}
