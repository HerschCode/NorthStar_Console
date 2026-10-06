// Lighthouse against the production build (vite preview, proxying to the local stack). Fails below 90 for
// performance, accessibility and best practices. Browser: Edge (CHROME_PATH to override).
import { spawn, execSync } from 'node:child_process'
import { mkdirSync, writeFileSync, existsSync } from 'node:fs'
import lighthouse from 'lighthouse'
import * as chromeLauncher from 'chrome-launcher'

const PAGES = ['/', '/queue', '/process', '/security', '/finance/controls']
const THRESHOLD = 0.9
const chromePath = process.env.CHROME_PATH ?? ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'].find(existsSync)
if (!chromePath) { console.error('No Chromium-based browser found; set CHROME_PATH'); process.exit(2) }

execSync('npx vite build', { stdio: 'inherit', shell: true })
const preview = spawn('npx', ['vite', 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort'], { shell: true, stdio: 'ignore' })
await new Promise((r) => setTimeout(r, 4000))
const chrome = await chromeLauncher.launch({ chromePath, chromeFlags: ['--headless=new', '--no-sandbox'] })
let failed = false
const summary = []
try {
  for (const p of PAGES) {
    const r = await lighthouse(`http://127.0.0.1:4173${p}`, { port: chrome.port, output: 'json', onlyCategories: ['performance', 'accessibility', 'best-practices'], logLevel: 'error' })
    const s = Object.fromEntries(Object.entries(r.lhr.categories).map(([k, v]) => [k, Math.round((v.score ?? 0) * 100)]))
    summary.push({ page: p, ...s })
    console.log(p.padEnd(22), JSON.stringify(s))
    if (Object.values(s).some((v) => v < THRESHOLD * 100)) failed = true
  }
} finally {
  await chrome.kill()
  preview.kill()
  try { execSync('taskkill /F /T /PID ' + preview.pid, { stdio: 'ignore' }) } catch {}
}
mkdirSync('lighthouse-reports', { recursive: true })
writeFileSync('lighthouse-reports/summary.json', JSON.stringify({ threshold: THRESHOLD * 100, results: summary }, null, 2))
process.exit(failed ? 1 : 0)
