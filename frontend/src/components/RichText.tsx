import { Fragment } from 'react'

/** 智能体的文字偶尔带有 **加粗** 标记：只把它转成粗体，其余原样显示（不解析 HTML）。 */
export function RichText({ text }: { text: string | null | undefined }) {
  if (!text) return null
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return (
    <span className="rich">
      {parts.map((p, i) =>
        p.startsWith('**') && p.endsWith('**') && p.length > 4 ? <b key={i}>{p.slice(2, -2)}</b> : <Fragment key={i}>{p}</Fragment>,
      )}
    </span>
  )
}
