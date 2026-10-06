import type { GovEvent } from '../api/types'
import { describeGatewayEvent, ledgerTrace } from './audit'

const action = (over: Partial<GovEvent>): GovEvent => ({ kind: 'action', ts: 1, session: 's', tool: 'propose_intervention', ...over })

describe('describeGatewayEvent', () => {
  it('says who decided, in what role, for a human approval or rejection', () => {
    expect(describeGatewayEvent(action({ effect: 'approval_granted', role: 'manager', approval_id: 'apr_1' }))).toEqual({ what: 'approval granted by a manager', detail: 'apr_1' })
    expect(describeGatewayEvent(action({ effect: 'approval_rejected', role: 'admin' })).what).toBe('approval rejected by an admin')
  })
  it('describes the authorisation decisions in words', () => {
    expect(describeGatewayEvent(action({ effect: 'require_approval', rule: 'manager-standard' }))).toEqual({ what: 'propose_intervention held for approval', detail: 'manager-standard' })
    expect(describeGatewayEvent(action({ effect: 'allow' })).what).toBe('propose_intervention allowed')
    expect(describeGatewayEvent(action({ effect: 'deny', stage: 'taint' })).what).toBe('propose_intervention denied by the taint check')
    expect(describeGatewayEvent(action({ effect: 'deny', stage: 'budget' })).what).toBe('propose_intervention denied by the action budget')
  })
  it('describes the text screening steps, naming the layer that blocked', () => {
    expect(describeGatewayEvent({ kind: 'text', ts: 1, session: 's', phase: 'pre_flight', decision: 'block', layer: 'rule_based', rule_id: 'RB-001' })).toEqual({ what: 'request screened: blocked by rule_based', detail: 'RB-001' })
    expect(describeGatewayEvent({ kind: 'text', ts: 1, session: 's', phase: 'post_flight', decision: 'allow' }).what).toBe('answer checked: passed')
  })
  it('does not break on an effect it has not seen', () => {
    expect(describeGatewayEvent(action({ effect: 'something_new' })).what).toBe('propose_intervention something_new')
  })
})

describe('ledgerTrace', () => {
  it('reads the trace id when present and is empty otherwise', () => {
    expect(ledgerTrace({ trace_id: 'a'.repeat(32) })).toBe('a'.repeat(32))
    expect(ledgerTrace({})).toBe('')
    expect(ledgerTrace(undefined)).toBe('')
    expect(ledgerTrace({ trace_id: 7 })).toBe('')
  })
})
