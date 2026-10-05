import { useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, useChartColors } from './EChart'
import { eur, fmt, hours } from '../lib/format'

function DataToggle({ summary, children }: { summary: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="text-xs text-muted hover:underline">{open ? '▾' : '▸'} {summary}</button>
      {open && <div className="mt-1 max-h-48 overflow-auto text-xs">{children}</div>}
    </div>
  )
}

/** Breach rate by month with its Wilson interval as a band. Right-censoring is explained next to the chart, not hidden. */
export function TrendChart({ series }: { series: { month: string; n: number; breach_rate: number; ci95: [number, number] }[] }) {
  const c = useChartColors()
  const option = useMemo<EChartsOption>(() => ({
    grid: { left: 44, right: 12, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis', formatter: (ps: unknown) => { const p = (ps as { dataIndex: number }[])[0]; const s = series[p.dataIndex]; return `${s.month}<br/>${fmt(s.breach_rate, 1)}% (95% CI ${fmt(s.ci95[0], 1)}–${fmt(s.ci95[1], 1)}%), n = ${s.n}` } },
    xAxis: { type: 'category', data: series.map((s) => s.month), axisLine: { lineStyle: { color: c.line } } },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%', color: c.muted }, splitLine: { lineStyle: { color: c.grid } } },
    series: [
      { name: 'lower', type: 'line', data: series.map((s) => s.ci95[0]), stack: 'ci', symbol: 'none', lineStyle: { opacity: 0 }, silent: true },
      { name: '95% interval', type: 'line', data: series.map((s) => +(s.ci95[1] - s.ci95[0]).toFixed(2)), stack: 'ci', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: c.accent, opacity: 0.18 }, silent: true },
      { name: 'Breach rate', type: 'line', data: series.map((s) => s.breach_rate), symbolSize: 7, lineStyle: { color: c.accent, width: 2 }, itemStyle: { color: c.accent } },
    ],
  }), [series, c])
  return (
    <div>
      <EChart option={option} height={230} label={`Breach rate by month on the realistic target, ${series.length} months, with 95% intervals`} />
      <DataToggle summary="View data">
        <table><thead><tr><th>Month</th><th>n</th><th>Breach %</th><th>95% CI</th></tr></thead><tbody>{series.map((s) => <tr key={s.month}><td>{s.month}</td><td>{s.n}</td><td>{fmt(s.breach_rate, 1)}</td><td>{fmt(s.ci95[0], 1)}–{fmt(s.ci95[1], 1)}</td></tr>)}</tbody></table>
      </DataToggle>
    </div>
  )
}

export interface RiskPoint { id: string; risk: number; value: number; tier: string; label: string }
/** Risk x value scatter (log value). Click a dot to open the case. */
export function RiskValueScatter({ points, onOpen, height = 300 }: { points: RiskPoint[]; onOpen: (id: string) => void; height?: number }) {
  const c = useChartColors()
  const option = useMemo<EChartsOption>(() => {
    const hot = new Set(['CRITICAL', 'HIGH'])
    const mk = (pts: RiskPoint[], color: string, name: string) => ({ name, type: 'scatter' as const, symbolSize: 7, itemStyle: { color, opacity: 0.65 }, data: pts.map((p) => ({ value: [Math.max(p.value, 1), p.risk], id: p.id, tip: p.label })) })
    return {
      grid: { left: 52, right: 12, top: 14, bottom: 40 },
      legend: { bottom: 0, textStyle: { color: c.muted }, itemHeight: 8 },
      tooltip: { trigger: 'item', formatter: (p: unknown) => { const d = (p as { data: { value: number[]; tip: string } }).data; return `${d.tip}<br/>${eur(d.value[0])} · P(breach) ${fmt(d.value[1], 2)}` } },
      xAxis: { type: 'log', name: 'Order value so far (EUR, log)', nameLocation: 'middle', nameGap: 24, axisLabel: { color: c.muted, formatter: (v: number) => eur(v) }, splitLine: { lineStyle: { color: c.grid } } },
      yAxis: { type: 'value', name: 'P(breach)', min: 0, max: 1, axisLabel: { color: c.muted }, splitLine: { lineStyle: { color: c.grid } } },
      series: [mk(points.filter((p) => !hot.has(p.tier)), c.accent, 'Other open cases'), mk(points.filter((p) => hot.has(p.tier)), c.bad, 'CRITICAL / HIGH expected loss')],
    }
  }, [points, c])
  return <EChart option={option} height={height} label={`Scatter of ${points.length} open cases by order value and breach probability`} onClick={(p) => { const id = (p as { data?: { id?: string } }).data?.id; if (id) onOpen(id) }} />
}

export interface QuadPoint { id: string; x: number; y: number; r: number; label: string; hot: boolean }
export function SupplierQuadrant({ points, onOpen }: { points: QuadPoint[]; onOpen: (id: string) => void }) {
  const c = useChartColors()
  const maxR = Math.max(1, ...points.map((p) => p.r))
  const option = useMemo<EChartsOption>(() => ({
    grid: { left: 52, right: 12, top: 14, bottom: 40 },
    tooltip: { formatter: (p: unknown) => (p as { data: { tip: string } }).data.tip },
    xAxis: { type: 'log', name: 'Open expected loss + 1 (EUR, log)', nameLocation: 'middle', nameGap: 24, axisLabel: { color: c.muted, formatter: (v: number) => eur(v) }, splitLine: { lineStyle: { color: c.grid } } },
    yAxis: { type: 'value', name: 'Breach rate %', min: 0, max: 100, axisLabel: { color: c.muted }, splitLine: { lineStyle: { color: c.grid } } },
    series: [{ type: 'scatter', symbolSize: (v: number[]) => 8 + 22 * Math.sqrt(v[2] / maxR), itemStyle: { color: c.accent, opacity: 0.55 }, data: points.map((p) => ({ value: [p.x, p.y, p.r], id: p.id, tip: p.label, itemStyle: p.hot ? { color: c.bad, opacity: 0.7 } : undefined })) }],
  }), [points, c, maxR])
  return <EChart option={option} height={320} label={`Supplier quadrant: ${points.length} suppliers by open expected loss and breach rate; bubble size is closed cases`} onClick={(p) => { const id = (p as { data?: { id?: string } }).data?.id; if (id) onOpen(id) }} />
}


export function SeriesChart({ series, label }: { series: { t: number; requests: number; blocked: number }[]; label: string }) {
  const c = useChartColors()
  const option = useMemo<EChartsOption>(() => ({
    grid: { left: 40, right: 12, top: 24, bottom: 28 },
    legend: { top: 0, textStyle: { color: c.muted }, itemHeight: 8 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: series.map((s) => new Date(s.t * 1000).toISOString().slice(11, 16)), axisLabel: { color: c.muted } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: c.muted }, splitLine: { lineStyle: { color: c.grid } } },
    series: [
      { name: 'Requests', type: 'bar', data: series.map((s) => s.requests), itemStyle: { color: c.accent, opacity: 0.55 } },
      { name: 'Blocked', type: 'bar', data: series.map((s) => s.blocked), itemStyle: { color: c.bad, opacity: 0.8 } },
    ],
  }), [series, c])
  return <EChart option={option} height={220} label={label} />
}

export function GroupedBars({ categories, groups, label, height = 260, pctAxis = false }: { categories: string[]; groups: { name: string; data: (number | null)[]; tone: 'accent' | 'good' | 'warn' | 'bad' | 'info' | 'muted' }[]; label: string; height?: number; pctAxis?: boolean }) {
  const c = useChartColors()
  const option = useMemo<EChartsOption>(() => ({
    grid: { left: 44, right: 12, top: 28, bottom: 28 },
    legend: { top: 0, textStyle: { color: c.muted }, itemHeight: 8 },
    tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => fmt(Number(v), 3) },
    xAxis: { type: 'category', data: categories, axisLabel: { color: c.muted } },
    yAxis: { type: 'value', axisLabel: { color: c.muted, formatter: pctAxis ? '{value}%' : '{value}' }, splitLine: { lineStyle: { color: c.grid } } },
    series: groups.map((g) => ({ name: g.name, type: 'bar' as const, data: g.data, itemStyle: { color: c[g.tone === 'muted' ? 'muted' : g.tone] } })),
  }), [categories, groups, c, pctAxis])
  return <EChart option={option} height={height} label={label} />
}
