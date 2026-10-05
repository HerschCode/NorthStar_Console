// Generates typed path/parameter/request definitions from the services' OpenAPI contracts (openapi/p1|p2|p3.json).
// The specs are copied from each repo's CI-exported file; run `npm run gen:api` after refreshing them.
import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'

mkdirSync('src/api/generated', { recursive: true })
for (const svc of ['p1', 'p2', 'p3']) {
  execFileSync('npx', ['openapi-typescript', `openapi/${svc}.json`, '-o', `src/api/generated/${svc}.d.ts`], { stdio: 'inherit', shell: true })
}
