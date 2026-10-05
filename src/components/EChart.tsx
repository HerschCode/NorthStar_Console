import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, AriaComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import { useSession } from '../state/session'

echarts.use([BarChart, LineChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, AriaComponent, CanvasRenderer])

export interface ChartColors { text: string; muted: string; line: string; grid: string; panel: string; accent: string; good: string; warn: string; bad: string; info: string }

/** Reads the active design tokens so charts follow the light/dark theme (state colours only for state). */
export function useChartColors(): ChartColors {
  const { theme } = useSession()
  return useMemo(() => {
    const cs = getComputedStyle(document.documentElement)
    const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d
    return { text: v('--text', '#172b3a'), muted: v('--muted', '#566b7d'), line: v('--border', '#dce5ee'), grid: v('--grid', '#e8eef4'), panel: v('--panel', '#fff'), accent: v('--accent', '#067a86'), good: v('--good', '#167048'), warn: v('--warn', '#8f5405'), bad: v('--bad', '#b8332d'), info: v('--info', '#2f60a8') }
    // theme is the trigger: tokens change when [data-theme] flips
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme])
}

/** Register extra series types on the same echarts instance (the graph series is only needed on the process page). */
export { echarts }

export function EChart({ option, height = 260, label, onClick }: { option: EChartsOption; height?: number; label: string; onClick?: (params: unknown) => void }) {
  const c = useChartColors()
  const merged = useMemo<EChartsOption>(() => ({
    backgroundColor: 'transparent',
    textStyle: { color: c.muted, fontFamily: 'inherit' },
    aria: { enabled: true, decal: { show: false } },
    animation: !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
    ...option,
  }), [option, c])
  return (
    <div role="img" aria-label={label} style={{ height }}>
      <ReactEChartsCore echarts={echarts} option={merged} style={{ height: '100%', width: '100%' }} notMerge lazyUpdate onEvents={onClick ? { click: onClick } : undefined} opts={{ renderer: 'canvas' }} />
    </div>
  )
}
