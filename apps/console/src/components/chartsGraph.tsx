import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { GraphChart } from 'echarts/charts'
import { EChart, echarts, useChartColors } from './EChart'
import { fmt, hours } from '../lib/format'
import type { FlowEdge, FlowNode } from '../api/types'
import { FLOW_METRIC_LABEL, type FlowMetric } from './flowMetric'

echarts.use([GraphChart])

/** Directly-follows graph. Nodes in columns by their typical order; edge colour = chosen metric (relative), width = frequency. */
export function ProcessGraph({ nodes, edges, metric, onEdge, order }: { nodes: FlowNode[]; edges: FlowEdge[]; metric: FlowMetric; onEdge: (e: FlowEdge) => void; order?: string[] }) {
  const c = useChartColors()
  const option = useMemo<EChartsOption>(() => {
    const rank = (id: string) => { const i = order?.indexOf(id) ?? -1; return i < 0 ? 99 : i }
    const sorted = [...nodes].sort((a, b) => rank(a.id) - rank(b.id) || b.events - a.events)
    const col: Record<string, number> = {}
    const rowsInCol: Record<number, number> = {}
    sorted.forEach((n, i) => { col[n.id] = Math.min(i, 6) })
    const pos = sorted.map((n) => { const cl = col[n.id]; const r = (rowsInCol[cl] = (rowsInCol[cl] ?? -1) + 1); return { x: 90 + cl * 135, y: 70 + r * 80 + (cl % 2) * 28 } })
    const vals = edges.map((e) => Number(e[metric] ?? 0))
    const maxV = Math.max(1e-9, ...vals)
    const maxT = Math.max(1, ...edges.map((e) => e.transitions))
    const colorFor = (v: number) => (v / maxV > 0.66 ? c.bad : v / maxV > 0.33 ? c.warn : c.good)
    return {
      tooltip: { formatter: (p: unknown) => { const d = p as { dataType: string; data: { name?: string; events?: number; dwell?: number | null; src?: string; tgt?: string; transitions?: number; v?: number } }; return d.dataType === 'edge' ? `${d.data.src} → ${d.data.tgt}<br/>${d.data.transitions} transitions<br/>${FLOW_METRIC_LABEL[metric]}: ${metric === 'breach_contribution_pct' ? fmt(d.data.v, 1) + '%' : hours(d.data.v)}` : `${d.data.name}<br/>${fmt(d.data.events)} events${d.data.dwell != null ? `<br/>median dwell ${hours(d.data.dwell)}` : ''}` } },
      series: [{
        type: 'graph', layout: 'none', roam: false, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 8, symbolSize: [118, 34], symbol: 'roundRect',
        label: { show: true, color: c.text, fontSize: 11, width: 108, overflow: 'truncate' },
        data: sorted.map((n, i) => ({ name: n.label, id: n.id, x: pos[i].x, y: pos[i].y, events: n.events, dwell: n.median_dwell_hours, itemStyle: { color: c.panel, borderColor: c.line, borderWidth: 1.5 } })),
        links: edges.map((e, i) => ({ source: e.from, target: e.to, src: e.from, tgt: e.to, transitions: e.transitions, v: vals[i], idx: i, lineStyle: { color: colorFor(vals[i]), width: 1 + 7 * Math.sqrt(e.transitions / maxT), opacity: 0.7, curveness: 0.18 } })),
        emphasis: { focus: 'adjacency' },
      }],
    }
  }, [nodes, edges, metric, c, order])
  return <EChart option={option} height={430} label={`Process flow graph with ${nodes.length} stages and ${edges.length} transitions, coloured by ${FLOW_METRIC_LABEL[metric]}`} onClick={(p) => { const d = p as { dataType?: string; data?: { idx?: number } }; if (d.dataType === 'edge' && d.data?.idx !== undefined) onEdge(edges[d.data.idx]) }} />
}

