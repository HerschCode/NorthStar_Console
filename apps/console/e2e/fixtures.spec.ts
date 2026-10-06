import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { installMocks } from './mock'
import { runDemoStory, signInAs } from './story'

test.describe('fixtures (no services)', () => {
  test.beforeEach(async ({ page }) => { await installMocks(page) })

  test('demo story: overview → queue → case → ask → propose → approve (second user) → audit → overview', async ({ page }) => {
    await runDemoStory(page, 'fixtures')
  })

  test('every number carries a provenance badge on the overview', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('Open cases').first()).toBeVisible()
    const values = page.getByTestId('metric-value')
    expect(await values.count()).toBeGreaterThan(6)
    for (const kpiLabel of ['Open cases', 'Already past target', 'Simulated expected loss', 'Interventions recorded']) {
      const card = page.locator('div.rounded-lg', { has: page.getByText(kpiLabel, { exact: true }) }).first()
      await expect(card.locator('[data-provenance]').first()).toBeVisible()
    }
    await expect(page.getByTestId('status-pill')).toContainText('LIVE')
  })

  test('snapshot state is shown when P1 reports it', async ({ page }) => {
    const overview = JSON.parse(readFileSync(join(fileURLToPath(new URL('.', import.meta.url)), 'fixtures', 'overview.json'), 'utf-8'))
    overview.snapshot = { live: false, reason: 'database unavailable', built_at: '2026-10-04T11:47:57' }
    await page.route('**/p1/v1/overview**', (route) => route.fulfill({ json: overview }))
    await page.goto('/')
    await expect(page.getByTestId('status-pill')).toContainText('SNAPSHOT 2026-10-04')
    await page.goto('/alerts')
    await expect(page.getByText(/saved snapshot/i)).toBeVisible()
  })

  test('model health shows registered champion, challenger, integrity status and registry events', async ({ page }) => {
    await page.route('**/p1/v1/mlops/registry', (route) => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        name: 'sla_risk',
        champion: {
          version: 'champion-v3',
          registered_at: '2026-10-06T12:00:00Z',
          sha256: 'a'.repeat(64),
          data_fingerprint: 'train-window-v8',
          metrics: { roc_auc: 0.84, brier: 0.17, ece: 0.03, n_test: 580 },
          notes: 'approved temporal holdout',
        },
        challenger: {
          version: 'candidate-v4',
          registered_at: '2026-10-06T13:00:00Z',
          sha256: 'b'.repeat(64),
          data_fingerprint: 'train-window-v9',
          metrics: { roc_auc: 0.86, brier: 0.16, ece: 0.02, n_test: 610 },
          notes: 'awaiting review',
        },
        versions: ['champion-v3', 'candidate-v4'],
        champion_hash_verified: false,
        events: [{
          at: '2026-10-06T13:05:00Z',
          event: 'promotion_held',
          detail: { summary: 'awaiting independent approval' },
        }],
        note: '',
      }),
    }))

    await page.goto('/models')
    const registry = page.getByRole('region', { name: /model registry/i })
    await expect(registry).toContainText('champion-v3')
    await expect(registry).toContainText('candidate-v4')
    await expect(registry).toContainText('hash mismatch')
    await expect(registry).toContainText('promotion_held')
    await expect(registry).toContainText('awaiting independent approval')
    await expect(registry.getByRole('table', { name: 'Registry events' })).toBeVisible()
  })

  test('replay clock re-queries with as_of', async ({ page }) => {
    const seen: string[] = []
    page.on('request', (r) => { const u = new URL(r.url()); if (u.pathname.endsWith('/v1/overview')) seen.push(u.searchParams.get('as_of') ?? '') })
    await page.goto('/')
    await expect(page.getByText('Open cases').first()).toBeVisible()
    await page.getByTestId('clock').fill('2018-03-05')
    await expect.poll(() => seen.includes('2018-03-05')).toBeTruthy()
  })

  test('loading, empty and error states exist', async ({ page }) => {
    await page.route('**/p1/v1/risk-map**', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) }))
    await page.goto('/risk-map')
    await expect(page.getByRole('alert')).toContainText('boom')
    await page.route('**/p1/v1/queue**', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ as_of: 'x', total_matching: 0, limit: 100, ranking: { value: null, unit: '', provenance: 'simulated', source: 's', note: '' }, driver_note: '', rows: [] }) }))
    await page.goto('/queue')
    await expect(page.getByText('No open cases match at this clock.')).toBeVisible()
  })

  test('free-tier limits: exhausted models, gateway allowance and the limits card are explained in words', async ({ page }) => {
    await page.route('**/p3/v1/ask', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      blocked: false, answer: '(Template answer: no language model is configured.) 544 cases: already late.', abstained: false, claims: [], evidence: [], model: 'template-fallback', trace_id: 'a'.repeat(32), cost_usd: 0, latency_ms: 5,
      gateway: { decision: 'allow', layers: {}, latency_ms: 1, trace_id: 'a'.repeat(32) }, notes: ['All free models are at their limits right now'],
      limits: { exhausted: true, retry_after_s: 9, other: [], models: [
        { provider: 'gemini', model: 'gemini-2.5-flash', scope: 'day', retry_after_s: 41520, message: 'Gemini (Google AI Studio free tier) · gemini-2.5-flash: per-day request limit reached — try again in 11.5 h' },
        { provider: 'groq', model: 'openai/gpt-oss-120b', scope: 'tokens', retry_after_s: 9, message: 'Groq (free plan) · openai/gpt-oss-120b: per-minute token limit reached — try again in 9 s' }] } }) }))
    await page.goto('/ask')
    await signInAs(page, 'analyst')
    await page.getByTestId('ask-input').fill('How many open cases are already late?')
    await page.getByTestId('ask-submit').click()
    const callout = page.getByTestId('limits-callout')
    await expect(callout).toContainText('Every free Gemini and Groq model is limited right now')
    await expect(callout).toContainText('9 s')
    await expect(page.getByText('per-day request limit reached')).toBeVisible()
    await expect(page.getByText('per-minute token limit reached')).toBeVisible()
    // the gateway's own allowance is explained in its own words
    await page.route('**/p3/v1/ask', (route) => route.fulfill({ status: 429, contentType: 'application/json', headers: { 'Retry-After': '7200' }, body: JSON.stringify({ detail: 'Daily AI question allowance for this demo identity is used (40). It protects the shared Gemini/Groq free-tier quota and resets at 00:00 UTC (in 2.0 h).' }) }))
    await page.getByTestId('ask-submit').click()
    await expect(page.getByTestId('allowance-callout')).toContainText('protects the shared Gemini/Groq free-tier quota')
    await page.goto('/observability')
    const card = page.getByTestId('limits-card')
    await expect(card).toContainText('gemini-2.5-flash')
    await expect(card).toContainText('cooling down · minute')
    await expect(card).toContainText('no key')
    await expect(card).toContainText('of 40 questions used by you today')
  })

  test('command palette opens with Ctrl+K and navigates', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')                                  // the shortcut is attached once the shell has mounted; a key pressed before that is simply lost
    await expect(page.getByRole('button', { name: 'Search (Ctrl+K)' })).toBeVisible()
    await page.keyboard.press('Control+k')
    await page.getByLabel('Search pages, cases and suppliers').fill('process')
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/process/)
  })

  const pages = ['/', '/queue', '/suppliers', '/process', '/finance/controls', '/finance/working-capital', '/roi', '/permissions', '/attack-lab', '/models', '/data-quality', '/lineage', '/architecture', '/experiments', '/evidence']
  for (const path of pages) {
    test(`axe: no serious or critical violations on ${path}`, async ({ page }) => {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      await expect(page.locator('main')).toBeVisible()
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
      const bad = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
      expect(bad.map((v) => `${v.id}: ${v.nodes.length} nodes — ${v.help}`)).toEqual([])
    })
  }

  test('phone width (375px) has no horizontal page scroll', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    for (const path of ['/', '/queue', '/process', '/finance/controls', '/security']) {
      await page.goto(path)
      await expect(page.locator('main')).toBeVisible()
      await page.waitForLoadState('networkidle')
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow, `${path} overflows by ${overflow}px`).toBeLessThanOrEqual(1)
    }
  })
})
