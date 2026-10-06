// Keeps the console's pinned copies of the services' OpenAPI contracts (openapi/p1|p2|p3.json) equal to what each service publishes.
//
//   node scripts/sync-specs.mjs --check            compare with each repository's main branch on GitHub (what CI does)
//   node scripts/sync-specs.mjs --check --local    compare with sibling checkouts instead
//   node scripts/sync-specs.mjs [--local]          overwrite the pinned copies, then run `npm run gen:api` and commit both
//
// Sibling checkouts: ../operations-performance and ../operations-assistant, and NORTHSTAR_P3_DIR (default ../llm-security-gateway); override
// with NORTHSTAR_P1_DIR / NORTHSTAR_P2_DIR. Specs are compared as parsed JSON, so formatting and line endings never count as drift.
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const args = new Set(process.argv.slice(2))
const check = args.has('--check')
const local = args.has('--local')

const SERVICES = {
  p1: { repo: 'operations-performance', dir: process.env.NORTHSTAR_P1_DIR ?? '../operations-performance' },
  p2: { repo: 'operations-assistant', dir: process.env.NORTHSTAR_P2_DIR ?? '../operations-assistant' },
  p3: { repo: 'llm-security-gateway', dir: process.env.NORTHSTAR_P3_DIR ?? '../llm-security-gateway' },
}

async function producerSpec(svc, { repo, dir }) {
  if (local) return readFileSync(resolve(dir, 'openapi', `${svc}.json`), 'utf8')
  const url = `https://raw.githubusercontent.com/HerschCode/${repo}/main/openapi/${svc}.json`
  // A connect timeout to GitHub is transient and would turn a CI run red for no reason: retry a few times before giving up. A real answer (even a 404) is never retried.
  for (let attempt = 1; ; attempt++) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(20_000) })
      if (!res.ok) throw Object.assign(new Error(`${url} answered ${res.status}`), { final: true })
      return await res.text()
    } catch (err) {
      if (err.final || attempt === 4) throw err
      await new Promise((resolve) => setTimeout(resolve, 2000 * attempt))
    }
  }
}

const operations = (spec) => Object.entries(spec.paths ?? {}).flatMap(([path, item]) => Object.keys(item).filter((m) => ['get', 'post', 'put', 'patch', 'delete'].includes(m)).map((m) => `${m.toUpperCase()} ${path}`))

let drift = 0
for (const [svc, where] of Object.entries(SERVICES)) {
  const file = `openapi/${svc}.json`
  const upstream = await producerSpec(svc, where)
  const pinned = readFileSync(file, 'utf8')
  const same = JSON.stringify(JSON.parse(upstream)) === JSON.stringify(JSON.parse(pinned))
  if (same) { console.log(`${svc}: in sync`); continue }
  const had = new Set(operations(JSON.parse(pinned)))
  const has = new Set(operations(JSON.parse(upstream)))
  const added = [...has].filter((o) => !had.has(o))
  const removed = [...had].filter((o) => !has.has(o))
  console.log(`${svc}: ${check ? 'DRIFT' : 'updated'} (+${added.length} -${removed.length} operations${added.length || removed.length ? `: ${[...added.map((o) => `+${o}`), ...removed.map((o) => `-${o}`)].join(', ')}` : '; schema details changed'})`)
  if (check) drift++
  else writeFileSync(file, upstream.endsWith('\n') ? upstream : `${upstream}\n`)
}
if (check && drift) {
  console.error(`\n${drift} pinned spec(s) differ from what the service publishes. Run: node scripts/sync-specs.mjs${local ? ' --local' : ''} && npm run gen:api, then fix any type errors and commit.`)
  process.exit(1)
}
if (!check) console.log('\nNow run: npm run gen:api && npm run typecheck')
