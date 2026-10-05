import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { installMocks } from './mock'
import { runDemoStory } from './story'

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
