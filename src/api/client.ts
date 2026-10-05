import createClient from 'openapi-fetch'
import type { paths as P1Paths } from './generated/p1'
import type { paths as P3Paths } from './generated/p3'
import { reportSnapshot } from '../state/status'

// Same-origin proxies in dev/e2e (/p1, /p3); direct service URLs in a production build.
export const P1_BASE = (import.meta.env.VITE_P1_URL as string | undefined) ?? '/p1'
export const P3_BASE = (import.meta.env.VITE_P3_URL as string | undefined) ?? '/p3'

export class ApiError extends Error {
  status: number
  traceId?: string
  constructor(status: number, message: string, traceId?: string) {
    super(message)
    this.status = status
    this.traceId = traceId
  }
}

let token: string | null = null
export const setToken = (t: string | null) => { token = t }
export const getToken = () => token

function hex(bytes: number): string {
  const a = new Uint8Array(bytes)
  crypto.getRandomValues(a)
  return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('')
}
/** W3C trace context: one trace id per user action, carried through gateway -> assistant -> P1. */
export const newTraceparent = (traceId = hex(16)) => `00-${traceId}-${hex(8)}-01`

export interface CallLog { at: number; service: 'p1' | 'p3'; method: string; path: string; status: number; ms: number; traceId: string }
const calls: CallLog[] = []
export const recentCalls = (): CallLog[] => calls.slice(-100)

function withMiddleware<T extends object>(client: ReturnType<typeof createClient<T>>, service: 'p1' | 'p3') {
  const started = new WeakMap<Request, { t: number; trace: string }>()
  client.use({
    onRequest({ request }) {
      const trace = request.headers.get('traceparent')?.split('-')[1] ?? hex(16)
      if (!request.headers.get('traceparent')) request.headers.set('traceparent', newTraceparent(trace))
      if (service === 'p3' && token) request.headers.set('Authorization', `Bearer ${token}`)
      started.set(request, { t: performance.now(), trace })
      return request
    },
    onResponse({ request, response }) {
      const s = started.get(request)
      calls.push({ at: Date.now(), service, method: request.method, path: new URL(request.url, 'http://x').pathname, status: response.status, ms: s ? Math.round(performance.now() - s.t) : 0, traceId: response.headers.get('X-Trace-ID') ?? s?.trace ?? '' })
      if (calls.length > 300) calls.splice(0, 100)
      return response
    },
  })
  return client
}

export const p1 = withMiddleware(createClient<P1Paths>({ baseUrl: P1_BASE }), 'p1')
export const p3 = withMiddleware(createClient<P3Paths>({ baseUrl: P3_BASE }), 'p3')

/** Unwrap an openapi-fetch result: return the JSON body (typed by the caller) or throw ApiError with the server's detail. */
export async function unwrap<T>(res: Promise<{ data?: unknown; error?: unknown; response: Response }>, service: 'p1' | 'p3' = 'p1'): Promise<T> {
  let out: { data?: unknown; error?: unknown; response: Response }
  try {
    out = await res
  } catch (e) {
    throw new ApiError(0, `Could not reach ${service === 'p1' ? 'the analytics service' : 'the gateway'}. ${(e as Error).message}`)
  }
  if (out.error !== undefined || !out.response.ok) {
    const e = out.error as { detail?: unknown } | undefined
    const detail = typeof e?.detail === 'string' ? e.detail : e?.detail ? JSON.stringify(e.detail) : out.response.statusText
    throw new ApiError(out.response.status, detail || `HTTP ${out.response.status}`, out.response.headers.get('X-Trace-ID') ?? undefined)
  }
  const data = out.data as T
  const snap = (data as { snapshot?: { live: boolean; reason?: string; as_of?: string; built_at?: string } } | null)?.snapshot
  if (service === 'p1' && snap) reportSnapshot(snap)
  return data
}
