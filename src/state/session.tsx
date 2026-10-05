import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { setToken } from '../api/client'

export type Role = 'viewer' | 'analyst' | 'manager' | 'finance' | 'admin'
export const ROLES: Role[] = ['viewer', 'analyst', 'manager', 'finance', 'admin']

export interface Identity { token: string; userId: string; role: Role; expiresAt: number }

interface SessionValue {
  /** Replay clock (YYYY-MM-DD). null means "the service default clock". */
  asOf: string | null
  setAsOf: (d: string | null) => void
  identity: Identity | null
  setIdentity: (i: Identity | null) => void
  theme: 'light' | 'dark'
  toggleTheme: () => void
}

const Ctx = createContext<SessionValue | null>(null)

function load<T>(key: string, fallback: T): T {
  try {
    const raw = sessionStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}
function save(key: string, value: unknown) {
  try { sessionStorage.setItem(key, JSON.stringify(value)) } catch { /* storage may be unavailable (private mode) */ }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [asOf, setAsOfState] = useState<string | null>(() => load<string | null>('ns-asof', null))
  const [identity, setIdentityState] = useState<Identity | null>(() => {
    const i = load<Identity | null>('ns-identity', null)
    return i && i.expiresAt * 1000 > Date.now() ? i : null
  })
  const [theme, setTheme] = useState<'light' | 'dark'>(() => load<'light' | 'dark'>('ns-theme', window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))

  useEffect(() => { setToken(identity?.token ?? null) }, [identity])
  useEffect(() => { document.documentElement.setAttribute('data-theme', theme) }, [theme])

  const setAsOf = useCallback((d: string | null) => { setAsOfState(d); save('ns-asof', d) }, [])
  const setIdentity = useCallback((i: Identity | null) => { setIdentityState(i); save('ns-identity', i); setToken(i?.token ?? null) }, [])
  const toggleTheme = useCallback(() => setTheme((t) => { const n = t === 'dark' ? 'light' : 'dark'; save('ns-theme', n); return n }), [])

  const value = useMemo(() => ({ asOf, setAsOf, identity, setIdentity, theme, toggleTheme }), [asOf, setAsOf, identity, setIdentity, theme, toggleTheme])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useSession(): SessionValue {
  const v = useContext(Ctx)
  if (!v) throw new Error('useSession must be used inside <SessionProvider>')
  return v
}
