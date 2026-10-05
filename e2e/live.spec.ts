import { test, expect } from '@playwright/test'
import { runDemoStory } from './story'

// Runs the SAME demo story against the real local stack: P1 (8000), P2 (8001), P3 (8002) — see scripts/dev.ps1.
// With P1 pointed at a closed database port the data is the committed snapshot, and executing an approved intervention cannot
// reach P1's ledger, so the final status may be "execution failed": the story accepts executed or execution failed in live mode
// and says so. Everything before that (ask, gateway checks, hold, second-user approval, audit) is real.
test('demo story against the live local stack', async ({ page, request }) => {
  const services = await request.get('http://127.0.0.1:8002/v1/services')
  test.skip(!services.ok(), 'the local stack is not running (scripts/dev.ps1)')
  await runDemoStory(page, 'live')
})
