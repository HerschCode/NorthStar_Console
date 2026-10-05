export const fmt = (n: number | null | undefined, d = 0): string =>
  n === null || n === undefined || Number.isNaN(n) ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d })

/** Euros, compact: EUR 1.16M / EUR 88K / EUR 340. BPI 2019 amounts are EUR; never `$`. */
export const eur = (n: number | null | undefined): string => {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const a = Math.abs(n)
  if (a >= 1e6) return `€${fmt(n / 1e6, 2)}M`
  if (a >= 1e3) return `€${fmt(n / 1e3, 0)}K`
  return `€${fmt(n, 0)}`
}

export const pct = (n: number | null | undefined, d = 1): string => (n === null || n === undefined ? '—' : `${fmt(n, d)}%`)

export const hours = (h: number | null | undefined): string => {
  if (h === null || h === undefined) return '—'
  return Math.abs(h) >= 48 ? `${fmt(h / 24, 1)} d` : `${fmt(h, 0)} h`
}

export const dateLabel = (iso: string | null | undefined): string => (iso ? String(iso).slice(0, 10) : '—')

export const shortId = (s: string, n = 14): string => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

/** Human unit string for a metric value ("cases", "%", "EUR"). */
export function withUnit(value: number | string | null | undefined, unit: string | undefined): string {
  if (value === null || value === undefined) return '—'
  if (unit === '%') return pct(Number(value))
  if (unit === 'EUR') return eur(Number(value))
  if (unit === 'probability') return fmt(Number(value), 3)
  const v = typeof value === 'number' ? fmt(value, Math.abs(value) < 10 && !Number.isInteger(value) ? 2 : 0) : String(value)
  return unit ? `${v} ${unit}` : v
}
