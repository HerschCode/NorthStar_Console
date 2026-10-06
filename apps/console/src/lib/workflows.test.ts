import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// Supply-chain guards on the repository's own workflows: a floating tag (`@v4`) can be moved by whoever controls the action's repository, a commit SHA cannot.
const DIR = join(process.cwd(), '.github', 'workflows')
const files = readdirSync(DIR).filter((f) => f.endsWith('.yml'))

describe('workflows', () => {
  it('has workflows to check', () => {
    expect(files).toContain('ci.yml')
  })
  it.each(files)('%s pins every action to a full commit SHA', (file) => {
    const uses = [...readFileSync(join(DIR, file), 'utf8').matchAll(/^\s*-?\s*uses:\s*(\S+)/gm)].map((m) => m[1])
    expect(uses.length).toBeGreaterThan(0)
    for (const u of uses) expect(u, `${file}: ${u}`).toMatch(/@[0-9a-f]{40}$/)
  })
  it.each(files)('%s declares its permissions at the top level', (file) => {
    expect(readFileSync(join(DIR, file), 'utf8')).toMatch(/^permissions:/m)
  })
  it('the CI workflow cannot write to the repository', () => {
    expect(readFileSync(join(DIR, 'ci.yml'), 'utf8')).toMatch(/^permissions:\s*\{\s*contents:\s*read\s*\}/m)
  })
})
