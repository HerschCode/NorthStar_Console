import { test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { signInAs } from './story'

// Regenerates docs/screenshots/*.png from the LIVE local stack (npm run screenshots). Dark and light, desktop and phone.
const SHOTS: [string, string][] = [
  ['overview', '/'], ['action-center', '/queue'], ['suppliers', '/suppliers'], ['process-mining', '/process'], ['ap-controls', '/finance/controls'],
  ['working-capital', '/finance/working-capital'], ['interventions', '/interventions'], ['ai-security', '/security'], ['tool-permissions', '/permissions'],
  ['attack-lab', '/attack-lab'], ['model-health', '/models'], ['evidence', '/evidence'],
]

test('screenshots', async ({ page }) => {
  mkdirSync('docs/screenshots', { recursive: true })
  await page.goto('/')
  await signInAs(page, 'manager')
  for (const theme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: theme })
    await page.evaluate((t) => { sessionStorage.setItem('ns-theme', JSON.stringify(t)) }, theme)
    for (const [name, path] of SHOTS) {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      await page.waitForTimeout(800)
      await page.screenshot({ path: `docs/screenshots/${name}-${theme}.png`, fullPage: false })
    }
  }
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.screenshot({ path: 'docs/screenshots/overview-phone.png' })
})
