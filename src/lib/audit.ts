import type { GovEvent } from '../api/types'

const article = (word: string) => (/^[aeiou]/i.test(word) ? 'an' : 'a')
const STAGE_WORDS: Record<string, string> = { policy: 'policy', taint: 'the taint check', budget: 'the action budget', approval: 'approval' }

/** A gateway audit record in plain language. Human decisions (`approval_granted` / `approval_rejected`) are the gateway's own record of who decided, in what role. */
export function describeGatewayEvent(e: GovEvent): { what: string; detail: string } {
  if (e.kind === 'text') {
    const step = e.phase === 'post_flight' ? 'answer checked' : 'request screened'
    const verdict = e.decision === 'block' ? `blocked${e.layer ? ` by ${e.layer}` : ''}` : 'passed'
    return { what: `${step}: ${verdict}`, detail: e.rule_id ?? '' }
  }
  const tool = e.tool ?? 'action'
  switch (e.effect) {
    case 'require_approval': return { what: `${tool} held for approval`, detail: e.rule ?? '' }
    case 'allow': return { what: `${tool} allowed`, detail: e.rule ?? '' }
    case 'deny': return { what: `${tool} denied by ${STAGE_WORDS[e.stage ?? ''] ?? e.stage ?? 'policy'}`, detail: e.rule ?? '' }
    case 'approval_granted': return { what: `approval granted by ${article(e.role ?? 'person')} ${e.role ?? 'person'}`, detail: e.approval_id ?? '' }
    case 'approval_rejected': return { what: `approval rejected by ${article(e.role ?? 'person')} ${e.role ?? 'person'}`, detail: e.approval_id ?? '' }
    default: return { what: `${tool} ${e.effect ?? ''}`.trim(), detail: e.rule ?? '' }
  }
}

/** The trace id the assistant's ledger recorded on a history row (absent when the request carried none). */
export function ledgerTrace(detail: Record<string, unknown> | undefined): string {
  const t = detail?.trace_id
  return typeof t === 'string' ? t : ''
}
