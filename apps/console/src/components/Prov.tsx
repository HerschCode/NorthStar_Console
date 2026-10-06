import type { Metric, Provenance } from '../api/types'
import { withUnit } from '../lib/format'
import { cx } from './ui'

export const PROV_LABEL: Record<Provenance, string> = {
  measured: 'measured', simulated: 'simulated', experimental: 'experimental', pending: 'pending', descriptive: 'descriptive',
}
const PROV_STYLE: Record<Provenance, string> = {
  measured: 'bg-good-soft text-good',
  simulated: 'bg-warn-soft text-warn',
  experimental: 'bg-info-soft text-info',
  pending: 'bg-panel2 text-muted border border-line',
  descriptive: 'bg-panel2 text-muted border border-line',
}
const PROV_HELP: Record<Provenance, string> = {
  measured: 'Computed from data in the log.',
  simulated: 'Rests on an assumption (shown), not on observed outcomes.',
  experimental: 'Weak model signal; see Model Health for its measured quality.',
  pending: 'The data this needs does not exist yet.',
  descriptive: 'A fact about the case, not a model output.',
}

/** Every number in the UI that comes from an API carries this badge; the tooltip names the source and sample size. */
export function ProvBadge({ p, source, n, note, as_of }: { p: Provenance; source?: string; n?: number; note?: string; as_of?: string | null }) {
  const tip = [PROV_HELP[p], source && `Source: ${source}`, n !== undefined && `n = ${n}`, as_of && `as of ${String(as_of).slice(0, 10)}`, note].filter(Boolean).join('\n')
  return (
    <span title={tip} aria-label={`${PROV_LABEL[p]}. ${tip.replace(/\n/g, ' ')}`} data-provenance={p}
      className={cx('inline-flex cursor-help items-center rounded px-1.5 py-px text-[10.5px] font-semibold uppercase tracking-wide', PROV_STYLE[p])}>
      {PROV_LABEL[p]}
    </span>
  )
}

/** A metric value with its unit and badge. `fmtValue` overrides the default unit formatting. */
export function MetricValue({ m, fmtValue, className }: { m: Metric | undefined | null; fmtValue?: (v: Metric['value']) => string; className?: string }) {
  if (!m) return <span className="text-muted">—</span>
  const shown = fmtValue ? fmtValue(m.value) : withUnit(m.value, m.unit)
  return (
    <span className={cx('inline-flex flex-wrap items-baseline gap-1.5', className)}>
      <span className="tabular-nums" data-testid="metric-value">{m.value === null ? 'n/a' : shown}</span>
      <ProvBadge p={m.provenance} source={m.source} n={m.n} note={m.note} as_of={m.as_of} />
    </span>
  )
}
