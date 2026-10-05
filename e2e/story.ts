import { expect, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'

/** With STORY_SHOTS=1 the story saves a storyboard frame per step to docs/demo/ (a video needs `npx playwright install ffmpeg`, which is opt-in). */
async function snap(page: Page, name: string) {
  if (!process.env.STORY_SHOTS) return
  mkdirSync('docs/demo', { recursive: true })
  await page.waitForTimeout(500)
  await page.screenshot({ path: `docs/demo/${name}.png` })
}

export async function signInAs(page: Page, role: string) {
  await page.getByTestId('role-switcher').click()
  await page.getByTestId(`role-${role}`).click()
  await expect(page.getByTestId('role-switcher')).toContainText(role)
}

/** The 10-step demo story from the product brief. `mode` only changes what the final execution status may be. */
export async function runDemoStory(page: Page, mode: 'fixtures' | 'live') {
  // 1. Overview: the at-risk count is a link into the queue
  await page.goto('/')
  await expect(page.getByText('Open cases').first()).toBeVisible()
  await expect(page.getByText('At risk (experimental)')).toBeVisible()
  await page.getByRole('link', { name: /At risk \(experimental\)/ }).click()
  await expect(page).toHaveURL(/\/queue/)
  await snap(page, '02-action-center')

  // 2. Queue -> a case (the first row, the highest expected loss)
  const firstRow = page.locator('tbody tr[data-click="true"]').first()
  await expect(firstRow).toBeVisible()
  await firstRow.click()
  await expect(page).toHaveURL(/\/cases\//)
  const caseUrl = page.url()
  await expect(page.getByRole('heading', { name: /^Case / })).toBeVisible()
  await snap(page, '03-case-360')

  // 3. "Why is this at risk?" through the gateway, with claim-level support marks and an evidence drawer
  await signInAs(page, 'analyst')
  await page.getByRole('button', { name: 'Why is this case at risk?' }).click()
  const result = page.getByTestId('ask-result')
  await expect(result).toBeVisible({ timeout: 60_000 })
  await expect(page.getByTestId('gateway-chip')).toContainText('Passed the AI gateway')
  await expect(page.getByTestId('ask-answer')).not.toBeEmpty()
  await snap(page, '04-ask-with-claims')
  const evidenceBtn = result.getByRole('list', { name: 'Claims and their support' }).getByRole('button').first()
  if (await evidenceBtn.count()) {
    await evidenceBtn.click()
    await expect(page.getByRole('dialog', { name: 'Evidence' })).toBeVisible()
    await page.keyboard.press('Escape')
  }

  // 4-5. "Can we hold payment?" -> Propose -> the gateway holds it for a different person
  await page.getByTestId('propose-open').click()
  await page.getByTestId('propose-submit').click()
  const proposed = page.getByTestId('propose-result')
  await expect(proposed).toBeVisible({ timeout: 30_000 })
  await expect(proposed).toContainText(/gateway held/i)
  await expect(proposed).toContainText('waiting for a different manager or admin')
  await snap(page, '05-gateway-holds-proposal')
  await page.getByRole('button', { name: 'Close' }).click()

  // 6. Switch role to manager (a new demo user) and approve; the requester could not
  await signInAs(page, 'manager')
  await page.goto('/interventions')
  await expect(page.getByTestId('count-held')).not.toHaveText('0', { timeout: 20_000 })
  await page.locator('tbody tr[data-click="true"]').first().click()
  const approve = page.getByTestId('approve')
  await expect(approve).toBeEnabled({ timeout: 20_000 })
  await approve.click()
  const after = page.getByText(mode === 'live' ? /executed|execution failed/i : /executed/i).first()
  await expect(after).toBeVisible({ timeout: 30_000 })
  await snap(page, '06-second-user-approves')

  // 6b. Separation of duties: a manager's own proposal cannot be approved by that manager
  await page.goto(caseUrl)
  await page.getByTestId('propose-open').click()
  await page.getByTestId('propose-submit').click()
  await expect(page.getByTestId('propose-result')).toContainText(/gateway held/i, { timeout: 30_000 })
  await page.getByRole('button', { name: 'Close' }).click()
  await page.goto('/interventions')
  await page.locator('tbody tr[data-click="true"]').first().click()
  await expect(page.getByTestId('approve')).toBeDisabled()
  await expect(page.getByTestId('why-disabled')).toContainText('Separation of duties')
  await snap(page, '07-separation-of-duties')

  // 7. The audit log shows the chain
  await page.goto('/audit')
  await expect(page.getByText(/intervention #\d+ → gateway held/i).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/→ approved/i).first()).toBeVisible()
  await expect(page.getByText('approval granted by a manager').first()).toBeVisible()        // the gateway's own record of who decided, in what role
  await snap(page, '08-audit-log')

  // 8. Back on the overview the ledger count has moved
  await page.goto('/')
  await expect(page.getByTestId('ledger-count')).toContainText(/[1-9]\d* proposed/, { timeout: 20_000 })
  await snap(page, '09-overview-updated')
}
