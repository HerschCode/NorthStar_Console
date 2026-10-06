import { ApiError, newTraceparent, unwrap } from './client'
import { getStatus, resetStatus } from '../state/status'

const res = (status: number, data?: unknown, error?: unknown) =>
  Promise.resolve({ data, error, response: new Response(null, { status, headers: { 'X-Trace-ID': 'tr1' } }) })

describe('api client', () => {
  it('generates W3C traceparents', () => {
    expect(newTraceparent('4bf92f3577b34da6a3ce929d0e0e4736')).toMatch(/^00-4bf92f3577b34da6a3ce929d0e0e4736-[0-9a-f]{16}-01$/)
    expect(newTraceparent()).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/)
  })
  it('unwraps data and records snapshot responses', async () => {
    resetStatus()
    const d = await unwrap<{ x: number }>(res(200, { x: 1, snapshot: { live: false, reason: 'db down', built_at: '2026-10-04' } }))
    expect(d.x).toBe(1)
    expect(getStatus().live).toBe(false)
  })
  it('throws ApiError with the server detail and trace id', async () => {
    await expect(unwrap(res(401, undefined, { detail: 'authentication required' }), 'p3')).rejects.toMatchObject({ status: 401, message: 'authentication required', traceId: 'tr1' })
  })
  it('turns a network failure into status 0', async () => {
    await expect(unwrap(Promise.reject(new Error('Failed to fetch')))).rejects.toBeInstanceOf(ApiError)
    await expect(unwrap(Promise.reject(new Error('Failed to fetch')))).rejects.toMatchObject({ status: 0 })
  })
})
