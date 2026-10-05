import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProvBadge, MetricValue } from './Prov'
import { DataTable, ErrorState } from './ui'
import { ClaimList } from './domain'
import { ApiError } from '../api/client'
import { getStatus, reportSnapshot, resetStatus } from '../state/status'

describe('provenance', () => {
  it('labels every provenance and exposes source and n in the tooltip', () => {
    render(<ProvBadge p="simulated" source="config/expected_loss.yaml" n={12} />)
    const b = screen.getByText('simulated')
    expect(b).toHaveAttribute('data-provenance', 'simulated')
    expect(b.getAttribute('title')).toContain('Source: config/expected_loss.yaml')
    expect(b.getAttribute('title')).toContain('n = 12')
  })
  it('renders a metric with its unit and badge, and n/a for null', () => {
    const { rerender } = render(<MetricValue m={{ value: 544, unit: 'cases', provenance: 'measured', source: 's' }} />)
    expect(screen.getByTestId('metric-value')).toHaveTextContent('544 cases')
    expect(screen.getByText('measured')).toBeInTheDocument()
    rerender(<MetricValue m={{ value: null, unit: '%', provenance: 'pending', source: 's' }} />)
    expect(screen.getByTestId('metric-value')).toHaveTextContent('n/a')
  })
})

describe('DataTable', () => {
  const rows = [{ id: 'a', n: 1 }, { id: 'b', n: 2 }]
  it('is keyboard operable and supports selection', async () => {
    const click = vi.fn()
    const sel = vi.fn()
    render(<DataTable caption="t" rows={rows} rowKey={(r) => r.id} onRowClick={click} selectable selected={new Set()} onSelect={sel} columns={[{ key: 'id', header: 'Id', render: (r) => r.id }]} />)
    expect(screen.getByRole('table', { name: 't' })).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Select b'))
    expect(sel).toHaveBeenCalledWith(new Set(['b']))
    const row = screen.getByText('a').closest('tr')!
    row.focus()
    await userEvent.keyboard('{Enter}')
    expect(click).toHaveBeenCalledWith(rows[0])
  })
})

describe('ErrorState', () => {
  it('distinguishes unreachable, unauthenticated and other failures', () => {
    const { rerender } = render(<ErrorState error={new ApiError(0, 'down')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Service unreachable')
    rerender(<ErrorState error={new ApiError(401, 'authentication required')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Sign in to see this')
    rerender(<ErrorState error={new ApiError(500, 'boom', 'abc123')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('trace abc123')
  })
})

describe('ClaimList', () => {
  it('marks unsupported claims and opens evidence by id', async () => {
    const open = vi.fn()
    render(<ClaimList onEvidence={open} claims={[
      { text: '544 cases are late.', supported: true, evidence_ids: ['e2'], reason: 'ok' },
      { text: 'About 900 are at risk.', supported: false, evidence_ids: ['e3'], reason: 'figure(s) not found in the cited evidence' },
    ]} />)
    const items = within(screen.getByLabelText('Claims and their support')).getAllByRole('listitem')
    expect(items[0]).toHaveAttribute('data-supported', 'true')
    expect(items[1]).toHaveAttribute('data-supported', 'false')
    expect(items[1]).toHaveTextContent('Not verified')
    await userEvent.click(screen.getByRole('button', { name: 'e3' }))
    expect(open).toHaveBeenCalledWith(['e3'])
  })
})

describe('data status', () => {
  it('flips to snapshot and back with the latest response', () => {
    resetStatus()
    reportSnapshot({ live: false, reason: 'quota', built_at: '2026-10-04T11:47:57' })
    expect(getStatus()).toMatchObject({ live: false, reason: 'quota' })
    reportSnapshot({ live: true })
    expect(getStatus().live).toBe(true)
  })
})
