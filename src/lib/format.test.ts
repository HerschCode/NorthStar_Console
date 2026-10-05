import { dateLabel, eur, fmt, hours, pct, withUnit } from './format'

describe('format', () => {
  it('formats euros compactly and never uses a dollar sign', () => {
    expect(eur(1_160_000)).toBe('€1.16M')
    expect(eur(88_370)).toBe('€88K')
    expect(eur(340)).toBe('€340')
    expect(eur(null)).toBe('—')
    expect(eur(12345)).not.toContain('$')
  })
  it('handles nulls and NaN', () => {
    expect(fmt(null)).toBe('—')
    expect(fmt(Number.NaN)).toBe('—')
    expect(pct(undefined)).toBe('—')
    expect(hours(undefined)).toBe('—')
  })
  it('switches hours to days at 48h', () => {
    expect(hours(24)).toBe('24 h')
    expect(hours(96)).toBe('4.0 d')
  })
  it('formats metric values by unit', () => {
    expect(withUnit(23.7, '%')).toBe('23.7%')
    expect(withUnit(544, 'cases')).toBe('544 cases')
    expect(withUnit(0.6384, 'probability')).toBe('0.638')
    expect(withUnit(null, 'EUR')).toBe('—')
  })
  it('trims dates', () => {
    expect(dateLabel('2018-04-16 00:00:00+00:00')).toBe('2018-04-16')
    expect(dateLabel(null)).toBe('—')
  })
})
