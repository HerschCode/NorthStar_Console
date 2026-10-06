import { useSyncExternalStore } from 'react'

export interface DataStatus { live: boolean; reason?: string; asOf?: string; seen: boolean }
let status: DataStatus = { live: true, seen: false }
const subs = new Set<() => void>()

/** Called for every P1 response that carries a `snapshot` field; any snapshot response flips the whole app to SNAPSHOT. */
export function reportSnapshot(s: { live: boolean; reason?: string; as_of?: string; built_at?: string }) {
  const next: DataStatus = s.live ? { live: true, seen: true } : { live: false, reason: s.reason, asOf: s.as_of ?? s.built_at, seen: true }
  if (JSON.stringify(next) !== JSON.stringify(status)) {
    status = next
    subs.forEach((f) => f())
  }
}
export const resetStatus = () => { status = { live: true, seen: false }; subs.forEach((f) => f()) }
export const getStatus = () => status
export function useDataStatus(): DataStatus {
  return useSyncExternalStore((cb) => { subs.add(cb); return () => subs.delete(cb) }, getStatus, getStatus)
}
