import { useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { ApiError } from '../api/client'

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(' ')
}

export function Card({ title, subtitle, actions, children, className, id }: { title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; id?: string }) {
  return (
    <section id={id} className={cx('rounded-lg border border-line bg-panel p-4 shadow-sm min-w-0', className)} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || actions) && (
        <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && <h2 className="text-[15px] font-semibold leading-tight">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

const tone: Record<string, string> = {
  good: 'bg-good-soft text-good', warn: 'bg-warn-soft text-warn', bad: 'bg-bad-soft text-bad', info: 'bg-info-soft text-info', neutral: 'bg-panel2 text-muted border border-line',
}
export function Badge({ tone: t = 'neutral', children, title }: { tone?: keyof typeof tone; children: ReactNode; title?: string }) {
  return <span title={title} className={cx('inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-semibold', tone[t])}>{children}</span>
}

export function Button({ variant = 'default', className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'default' | 'primary' | 'danger' | 'ghost' }) {
  const v = { default: 'border border-line bg-panel hover:border-accent', primary: 'bg-accent text-accent-ink border border-accent hover:opacity-90', danger: 'border border-line bg-panel text-bad hover:border-bad', ghost: 'border border-transparent hover:bg-panel2' }[variant]
  return <button type="button" {...p} className={cx('rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed', v, className)} />
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx('skeleton', className ?? 'h-6 w-full')} />
}
export function LoadingBlock({ label = 'Loading', rows = 3, minHeight }: { label?: string; rows?: number; minHeight?: number }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2" style={{ minHeight }}>
      <span className="sr-only">{label}…</span>
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} className={i === 0 ? 'h-7 w-1/3' : 'h-5 w-full'} />)}
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const e = error as ApiError
  const unreachable = e?.status === 0
  return (
    <div role="alert" className="rounded-md border border-bad/40 bg-bad-soft p-3 text-sm text-ink">
      <p className="font-semibold text-bad">{unreachable ? 'Service unreachable' : e?.status === 401 ? 'Sign in to see this' : e?.status === 429 ? 'Limit reached' : 'Something went wrong'}</p>
      <p className="mt-1 text-muted">{e?.message ?? String(error)}</p>
      {e?.traceId && <p className="mt-1 font-mono text-xs text-muted">trace {e.traceId}</p>}
      {retry && <Button className="mt-2" onClick={retry}>Retry</Button>}
    </div>
  )
}

export function EmptyState({ title, hint, children }: { title: string; hint?: ReactNode; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-line p-6 text-center">
      <p className="font-medium">{title}</p>
      {hint && <p className="mx-auto mt-1 max-w-xl text-sm text-muted">{hint}</p>}
      {children && <div className="mt-3">{children}</div>}
    </div>
  )
}

/** Renders loading / error / data for a TanStack Query result. `empty` is shown when `isEmpty(data)` is true. */
export function Async<T>({ q, children, isEmpty, empty, rows, minHeight }: { minHeight?: number; q: { data?: T; isLoading: boolean; isError: boolean; error: unknown; refetch: () => unknown }; children: (d: T) => ReactNode; isEmpty?: (d: T) => boolean; empty?: ReactNode; rows?: number }) {
  if (q.isLoading) return <LoadingBlock rows={rows} minHeight={minHeight} />
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />
  if (q.data === undefined) return null
  if (isEmpty?.(q.data)) return <>{empty ?? <EmptyState title="Nothing to show" />}</>
  return <>{children(q.data)}</>
}

export function Callout({ tone: t = 'info', title, children }: { tone?: 'info' | 'warn' | 'bad' | 'good'; title?: string; children: ReactNode }) {
  const c = { info: 'border-info/40 bg-info-soft', warn: 'border-warn/40 bg-warn-soft', bad: 'border-bad/40 bg-bad-soft', good: 'border-good/40 bg-good-soft' }[t]
  return (
    <div role="note" className={cx('rounded-md border p-3 text-sm', c)}>
      {title && <p className="mb-0.5 font-semibold">{title}</p>}
      <div className="text-ink">{children}</div>
    </div>
  )
}

export function Tabs<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-md border border-line bg-panel2 p-0.5">
      {options.map((o) => (
        <button key={o.value} role="tab" type="button" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={cx('rounded px-3 py-1 text-sm', value === o.value ? 'bg-panel font-semibold shadow-sm' : 'text-muted hover:text-ink')}>{o.label}</button>
      ))}
    </div>
  )
}

export function Grid({ cols = 2, children, className }: { cols?: 1 | 2 | 3 | 4; children: ReactNode; className?: string }) {
  const c = { 1: 'grid-cols-1', 2: 'grid-cols-1 lg:grid-cols-2', 3: 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3', 4: 'grid-cols-2 xl:grid-cols-4' }[cols]
  return <div className={cx('grid gap-3', c, className)}>{children}</div>
}

export interface Column<R> { key: string; header: string; render: (r: R) => ReactNode; align?: 'right'; width?: string }
export function DataTable<R>({ rows, columns, rowKey, onRowClick, caption, selectable, selected, onSelect, maxHeight }: {
  rows: R[]; columns: Column<R>[]; rowKey: (r: R) => string; onRowClick?: (r: R) => void; caption: string
  selectable?: boolean; selected?: Set<string>; onSelect?: (s: Set<string>) => void; maxHeight?: string
}) {
  return (
    <div className="overflow-auto" style={{ maxHeight }}>
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-panel">
          <tr>
            {selectable && <th scope="col" className="w-8"><span className="sr-only">Select</span></th>}
            {columns.map((c) => <th key={c.key} scope="col" className={c.align === 'right' ? 'text-right' : ''} style={{ width: c.width }}>{c.header}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const k = rowKey(r)
            return (
              <tr key={k} data-click={onRowClick ? 'true' : undefined} onClick={() => onRowClick?.(r)}
                onKeyDown={(e) => { if (onRowClick && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onRowClick(r) } }} tabIndex={onRowClick ? 0 : undefined}>
                {selectable && (
                  <td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`Select ${k}`} checked={selected?.has(k) ?? false}
                      onChange={(e) => { const n = new Set(selected); if (e.target.checked) n.add(k); else n.delete(k); onSelect?.(n) }} />
                  </td>
                )}
                {columns.map((c) => <td key={c.key} className={c.align === 'right' ? 'text-right tabular-nums' : ''}>{c.render(r)}</td>)}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function Collapsible({ summary, children }: { summary: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="text-xs text-muted underline-offset-2 hover:underline">{open ? '▾' : '▸'} {summary}</button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  )
}

export function Bar({ value, max = 100, tone: t = 'accent' }: { value: number; max?: number; tone?: 'accent' | 'good' | 'warn' | 'bad' }) {
  const color = { accent: 'bg-accent', good: 'bg-good', warn: 'bg-warn', bad: 'bg-bad' }[t]
  return (
    <div className="h-2 w-full overflow-hidden rounded bg-panel2" role="presentation">
      <div className={cx('h-full', color)} style={{ width: `${Math.max(1.5, Math.min(100, (100 * Math.abs(value)) / (max || 1)))}%` }} />
    </div>
  )
}
