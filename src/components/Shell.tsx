import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { NAV, ALL_NAV } from '../nav'
import { ROLES, useSession, type Role } from '../state/session'
import { useDataStatus } from '../state/status'
import { useApprovals, useFinanceControls, useLimits, useLogin, useOverview, useSearch, useServices } from '../api/hooks'
import { Badge, Button, cx } from './ui'
import { dateLabel } from '../lib/format'

export interface Alert { sev: 'critical' | 'warning' | 'info'; text: string; to?: string }

export function useAlerts(): Alert[] {
  useOverview()                    // every page sees the data status (LIVE / SNAPSHOT) from the first P1 response, not only the overview page
  const status = useDataStatus()
  const services = useServices()
  const controls = useFinanceControls()
  const approvals = useApprovals('pending')
  const limits = useLimits()
  const out: Alert[] = []
  if (!status.live) out.push({ sev: 'warning', text: `Showing a saved snapshot${status.reason ? ` (${status.reason.slice(0, 80)})` : ''}. Numbers are real but not current.`, to: '/data-quality' })
  if (services.data && !services.data.assistant.reachable) out.push({ sev: 'warning', text: `The assistant (P2) is not reachable through the gateway: ${services.data.assistant.detail}. Ask Northstar will fail until it is.`, to: '/observability' })
  if (services.isError) out.push({ sev: 'critical', text: 'The gateway (P3) is unreachable: AI answers, approvals and governance data are unavailable.', to: '/observability' })
  controls.data?.controls.filter((c) => c.status === 'not_valid').forEach((c) => out.push({ sev: 'warning', text: `${c.id.replace(/_/g, ' ')} is not operationally valid (it flags ordinary data).`, to: '/finance/controls' }))
  const a = limits.data?.assistant
  if (a?.summary.startsWith('no free model')) out.push({ sev: 'warning', text: `Every free Gemini/Groq model is at its limit${a.next_available_s != null ? ` — the soonest is back in ${a.next_available_s < 90 ? Math.round(a.next_available_s) + ' s' : Math.round(a.next_available_s / 60) + ' min'}` : ''}. AI answers are labelled templates until then.`, to: '/observability' })
  else if (a?.summary.startsWith('no free-tier key')) out.push({ sev: 'info', text: 'No Gemini or Groq key is configured on the assistant: AI answers are labelled templates (set GEMINI_API_KEY and/or GROQ_API_KEY).', to: '/observability' })
  const g = limits.data?.gateway
  if (g && g.used_by_you != null && g.per_user_daily > 0 && g.used_by_you >= 0.8 * g.per_user_daily) out.push({ sev: 'warning', text: `You have used ${g.used_by_you} of your ${g.per_user_daily} daily AI questions (resets 00:00 UTC).`, to: '/observability' })
  const pending = approvals.data?.approvals.length ?? 0
  if (pending) out.push({ sev: 'warning', text: `${pending} approval${pending === 1 ? '' : 's'} waiting for a human decision.`, to: '/approvals' })
  out.push({ sev: 'info', text: 'No real intervention outcomes are recorded; ROI figures are simulated.', to: '/roi' })
  return out
}

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const path = useRouterState({ select: (s) => s.location.pathname })
  return (
    <aside aria-label="Primary" className={cx('fixed inset-y-0 left-0 z-40 w-60 shrink-0 overflow-y-auto bg-side py-4 text-side-ink transition-transform lg:sticky lg:top-0 lg:h-screen lg:translate-x-0', open ? 'translate-x-0' : '-translate-x-full')}>
      <div className="border-b border-white/10 px-5 pb-3.5">
        <div className="text-[17px] font-bold tracking-wide text-white">NORTHSTAR</div>
        <div className="text-[11px] opacity-70">Procurement Intelligence</div>
      </div>
      <nav>
        {NAV.map((g, gi) => (
          <div key={gi} className="mt-2">
            {g.label && <div className="px-5 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-widest opacity-60">{g.label}</div>}
            {g.items.map((it) => {
              const active = it.to === '/' ? path === '/' : path === it.to || path.startsWith(it.to + '/') || (it.to === '/queue' && path.startsWith('/cases')) || (it.to === '/suppliers' && path.startsWith('/suppliers'))
              return (
                <Link key={it.to} to={it.to} onClick={onClose} aria-current={active ? 'page' : undefined}
                  className={cx('block border-l-[3px] px-5 py-1.5 text-[13.5px] no-underline', active ? 'border-accent bg-side-active text-white' : 'border-transparent text-side-ink hover:bg-white/5')}>
                  {it.label}
                </Link>
              )
            })}
          </div>
        ))}
      </nav>
    </aside>
  )
}

function StatusPill() {
  const s = useDataStatus()
  return (
    <span role="status" data-testid="status-pill" className="inline-flex min-w-[15rem] items-center gap-1.5 whitespace-nowrap rounded-full border border-line bg-panel2 px-2.5 py-0.5 text-xs" title={s.live ? 'Data is served live from the analytics service.' : s.reason ?? 'The live database is unavailable; a committed snapshot is shown.'}>
      <span className={cx('h-2 w-2 rounded-full', s.live ? 'bg-good' : 'bg-warn')} aria-hidden />
      {s.live ? 'LIVE' : `SNAPSHOT${s.asOf ? ' ' + dateLabel(s.asOf) : ''}`} · replay data
    </span>
  )
}

function RoleSwitcher() {
  const { identity, setIdentity } = useSession()
  const login = useLogin()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const f = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', f)
    return () => document.removeEventListener('mousedown', f)
  }, [])
  const pick = async (role: Role) => {
    try {
      const r = await login.mutateAsync(role)
      setIdentity({ token: r.token, userId: r.user_id, role: r.role as Role, expiresAt: r.expires_at })
      qc.invalidateQueries()
      setOpen(false)
    } catch { /* surfaced below */ }
  }
  return (
    <div ref={ref} className="relative">
      <Button aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="role-switcher">
        {identity ? <><span className="font-semibold">{identity.role}</span> <span className="text-muted">· demo identity</span></> : 'Sign in (demo)'}
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 z-50 mt-1 w-64 rounded-md border border-line bg-panel p-1 shadow-lg">
          <p className="px-2 py-1 text-xs text-muted">Demo identity: a signed, short-lived token that proves nothing about a real person. Each sign-in is a new user, so “a different manager” is a different sign-in.</p>
          {ROLES.map((r) => (
            <button key={r} role="menuitem" type="button" onClick={() => pick(r)} className={cx('block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-panel2', identity?.role === r && 'font-semibold')} data-testid={`role-${r}`}>
              {r}
            </button>
          ))}
          {identity && <button role="menuitem" type="button" onClick={() => { setIdentity(null); qc.invalidateQueries(); setOpen(false) }} className="block w-full rounded px-2 py-1.5 text-left text-sm text-muted hover:bg-panel2">Sign out</button>}
          {login.isError && <p role="alert" className="px-2 py-1 text-xs text-bad">{(login.error as Error).message}</p>}
        </div>
      )}
    </div>
  )
}

function ClockPicker() {
  const { asOf, setAsOf } = useSession()
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted" title="Replay clock: open cases and their risk use only events up to this date.">
      <span className="hidden sm:inline">Replay clock</span>
      <input type="date" aria-label="Replay clock date" value={asOf ?? ''} min="2018-01-08" max="2018-10-31" onChange={(e) => setAsOf(e.target.value || null)}
        className="rounded-md border border-line bg-panel px-2 py-1 text-sm text-ink" data-testid="clock" />
      {asOf && <button type="button" className="text-xs underline" onClick={() => setAsOf(null)}>default</button>}
    </label>
  )
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  const search = useSearch(open ? q : '')
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { if (open) { setQ(''); setI(0); setTimeout(() => inputRef.current?.focus(), 0) } }, [open])
  const items = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const pages = ALL_NAV.filter((n) => !ql || n.label.toLowerCase().includes(ql) || (n.keywords ?? '').includes(ql)).slice(0, 8).map((n) => ({ key: n.to, label: n.label, hint: 'page', go: () => nav({ to: n.to }) }))
    const found = (search.data?.results ?? []).map((r) => ({
      key: `${r.type}:${r.id}`, label: r.label, hint: r.type,
      go: () => (r.type === 'case' ? nav({ to: '/cases/$caseId', params: { caseId: r.id } }) : r.type === 'supplier' ? nav({ to: '/suppliers/$supplierId', params: { supplierId: r.id } }) : r.type === 'experiment' ? nav({ to: '/experiments' }) : nav({ to: '/finance/controls' })),
    }))
    const ask = ql.length > 3 ? [{ key: 'ask', label: `Ask Northstar: “${q.trim()}”`, hint: 'ask', go: () => nav({ to: '/ask', search: { q: q.trim() } }) }] : []
    return [...pages, ...found, ...ask]
  }, [q, search.data, nav])
  if (!open) return null
  const run = (idx: number) => { items[idx]?.go(); onClose() }
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-24" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label="Command palette" className="w-full max-w-xl rounded-lg border border-line bg-panel shadow-xl">
        <input ref={inputRef} value={q} onChange={(e) => { setQ(e.target.value); setI(0) }} aria-label="Search pages, cases and suppliers" placeholder="Search pages, case ids, suppliers — or type a question"
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setI((x) => Math.min(x + 1, items.length - 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setI((x) => Math.max(x - 1, 0)) }
            else if (e.key === 'Enter') { e.preventDefault(); run(i) }
            else if (e.key === 'Escape') onClose()
          }}
          className="w-full rounded-t-lg border-b border-line bg-transparent px-4 py-3 text-base outline-none" />
        <ul role="listbox" className="max-h-80 overflow-auto p-1">
          {items.map((it, idx) => (
            <li key={it.key} role="option" aria-selected={idx === i} onMouseEnter={() => setI(idx)} onClick={() => run(idx)} className={cx('flex cursor-pointer items-center justify-between rounded px-3 py-2 text-sm', idx === i && 'bg-accent-soft')}>
              <span>{it.label}</span><Badge>{it.hint}</Badge>
            </li>
          ))}
          {items.length === 0 && <li className="px-3 py-4 text-sm text-muted">No matches</li>}
        </ul>
      </div>
    </div>
  )
}

export function Shell() {
  const { toggleTheme, theme } = useSession()
  const [menu, setMenu] = useState(false)
  const [palette, setPalette] = useState(false)
  const alerts = useAlerts()
  const important = alerts.filter((a) => a.sev !== 'info').length
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((p) => !p) }
      else if (e.key === '/' && !(e.target as HTMLElement).closest('input,textarea,select')) { e.preventDefault(); setPalette(true) }
    }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [])
  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only-focusable absolute z-50 rounded bg-accent px-3 py-1 text-accent-ink">Skip to content</a>
      <Sidebar open={menu} onClose={() => setMenu(false)} />
      {menu && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setMenu(false)} aria-hidden />}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-2 lg:h-14 lg:flex-nowrap">
          <button type="button" className="rounded border border-line px-2 py-1 lg:hidden" aria-label="Open navigation" onClick={() => setMenu(true)}>☰</button>
          <button type="button" onClick={() => setPalette(true)} className="flex min-w-[8rem] flex-1 items-center justify-between rounded-md border border-line bg-panel2 px-3 py-1.5 text-left text-sm text-muted sm:max-w-md" aria-label="Search (Ctrl+K)">
            <span>Search…</span><kbd className="hidden rounded border border-line px-1 text-[11px] sm:inline">Ctrl K</kbd>
          </button>
          <ClockPicker />
          <StatusPill />
          <Link to="/alerts" className="inline-block min-w-[4.5rem] whitespace-nowrap rounded-full border border-line bg-panel2 px-2.5 py-0.5 text-center text-xs text-ink no-underline" aria-label={`Alerts: ${important} need attention`}>Alerts{important ? ` · ${important}` : ''}</Link>
          <RoleSwitcher />
          <Button variant="ghost" onClick={toggleTheme} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>{theme === 'dark' ? 'Light' : 'Dark'}</Button>
        </header>
        <main id="main" className="mx-auto w-full max-w-[1320px] flex-1 px-4 py-5 sm:px-6" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  )
}
