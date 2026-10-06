import { lazy, Suspense, type ComponentProps } from 'react'
import { Skeleton } from './ui'
import type * as C from './charts'
import type * as G from './chartsGraph'

// ECharts is ~600 KB; it is loaded only when a chart is about to render, so first paint (KPIs, tables) never waits for it.
const Trend = lazy(() => import('./charts').then((m) => ({ default: m.TrendChart })))
const Scatter = lazy(() => import('./charts').then((m) => ({ default: m.RiskValueScatter })))
const Quadrant = lazy(() => import('./charts').then((m) => ({ default: m.SupplierQuadrant })))
const Graph = lazy(() => import('./chartsGraph').then((m) => ({ default: m.ProcessGraph })))
const Series = lazy(() => import('./charts').then((m) => ({ default: m.SeriesChart })))
const Bars = lazy(() => import('./charts').then((m) => ({ default: m.GroupedBars })))

const Box = ({ h, children }: { h: number; children: React.ReactNode }) => (
  <div style={{ minHeight: h }}><Suspense fallback={<Skeleton className="w-full" />}>{children}</Suspense></div>
)

export const TrendChart = (p: ComponentProps<typeof C.TrendChart>) => <Box h={230}><Trend {...p} /></Box>
export const RiskValueScatter = (p: ComponentProps<typeof C.RiskValueScatter>) => <Box h={p.height ?? 300}><Scatter {...p} /></Box>
export const SupplierQuadrant = (p: ComponentProps<typeof C.SupplierQuadrant>) => <Box h={320}><Quadrant {...p} /></Box>
export const ProcessGraph = (p: ComponentProps<typeof G.ProcessGraph>) => <Box h={430}><Graph {...p} /></Box>
export const SeriesChart = (p: ComponentProps<typeof C.SeriesChart>) => <Box h={220}><Series {...p} /></Box>
export const GroupedBars = (p: ComponentProps<typeof C.GroupedBars>) => <Box h={p.height ?? 260}><Bars {...p} /></Box>
export { FLOW_METRIC_LABEL } from './flowMetric'
export type { FlowMetric } from './flowMetric'
