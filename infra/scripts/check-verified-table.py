#!/usr/bin/env python3
"""Keep the README's "What is verified" table honest.

The table says what is proven and *which CI job proves it*. This check makes those claims
mechanical, so the table cannot quietly rot:

  * every row links to a job that exists in the named workflow, and the link's line anchor is the
    line where that job is defined (so the link opens the job that does the checking);
  * the counts the README, docs/tour.md and docs/cloud-security.md state (module tests, policy tests,
    mutation tests, policy rules, runbook assumptions) equal what the repository actually contains.

It cannot re-run the checks; that is what the CI jobs it links to are for. It only stops the README
from pointing at a job that was renamed, moved or removed, or from quoting a count nobody updated.

    python3 scripts/check-verified-table.py          # check (CI, scripts/check.sh)
    python3 scripts/check-verified-table.py --fix    # rewrite stale line anchors in place

Standard library only.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
WORKFLOWS = ROOT / ".github" / "workflows"
BLOB = "https://github.com/HerschCode/northstar-infra/blob/main/.github/workflows/"

# [`ci` › `terraform`](https://github.com/.../ci.yml#L29)
LINK = re.compile(r"\[`(?P<workflow>[\w-]+)` › `(?P<job>[\w-]+)`\]\((?P<url>[^)\s]+)\)")
URL = re.compile(re.escape(BLOB) + r"(?P<file>[\w-]+\.yml)#L(?P<line>\d+)$")


def job_lines(workflow: Path) -> dict:
    """{job id: 1-based line number} for the jobs: block of a workflow file."""
    jobs, in_jobs = {}, False
    for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
        elif in_jobs:
            match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if match:
                jobs[match.group(1)] = number
            elif line.strip() and not line.startswith((" ", "#")):
                break  # the next top-level key
    return jobs


def count_module_tests() -> int:
    return sum(len(re.findall(r'^run "', f.read_text(encoding="utf-8"), re.M))
               for f in ROOT.glob("modules/*/tests/*.tftest.hcl"))


def count_policy_tests() -> int:
    return sum(len(re.findall(r"^test_\w+", f.read_text(encoding="utf-8"), re.M))
               for f in ROOT.glob("policies/tests/*_test.rego"))


def count_mutations() -> int:
    return len(list((ROOT / "policies" / "mutations").glob("*.tfmutation")))


def count_rules() -> int:
    pattern = re.compile(r"\[(?:IAM|PUB|UNK|RUN|SEC|GCS|BQ|WIF|KEY|COST)-\d{3}\]")
    ids = set()
    for f in ROOT.glob("policies/*.rego"):
        ids.update(pattern.findall(f.read_text(encoding="utf-8")))
    return len(ids)


WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}


def count_assumptions() -> int:
    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    section = runbook.split("## Assumptions to confirm on first apply", 1)[1]
    section = re.split(r"^## ", section, maxsplit=1, flags=re.M)[0]
    return len(re.findall(r"^\d+\. ", section, re.M))


def as_number(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token.lower()]


def verified_section(lines: list) -> range:
    start = next((i for i, l in enumerate(lines) if l.startswith("## What is verified")), None)
    if start is None:
        raise SystemExit('README.md has no "## What is verified" heading')
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return range(start, end)


def main() -> int:
    fix = "--fix" in sys.argv[1:]
    text = README.read_text(encoding="utf-8")
    lines = text.split("\n")
    section = verified_section(lines)
    problems, rows, links = [], 0, 0

    # ---- every row links to a real job, at the right line -------------------------------------
    indexes = {}
    for i in section:
        line = lines[i]
        if not line.startswith("|") or re.match(r"^\|[\s:|-]+\|$", line) or line.startswith("| What"):
            continue
        rows += 1
        row_links = list(LINK.finditer(line))
        if not row_links:
            problems.append(f"README.md:{i + 1}: row has no link to a CI job: {line[:70]}")
            continue
        # Last link first: a fix replaces a span of the line, and must not shift the spans still to come.
        for link in reversed(row_links):
            links += 1
            url = URL.match(link["url"])
            if not url:
                problems.append(f"README.md:{i + 1}: link is not {BLOB}<file>.yml#L<n>: {link['url']}")
                continue
            if url["file"] != link["workflow"] + ".yml":
                problems.append(f"README.md:{i + 1}: link text says `{link['workflow']}` but the URL is {url['file']}")
                continue
            workflow = WORKFLOWS / url["file"]
            if not workflow.exists():
                problems.append(f"README.md:{i + 1}: {url['file']} does not exist")
                continue
            jobs = indexes.setdefault(url["file"], job_lines(workflow))
            if link["job"] not in jobs:
                problems.append(f"README.md:{i + 1}: {url['file']} has no job `{link['job']}` (it has: {', '.join(jobs)})")
                continue
            if int(url["line"]) != jobs[link["job"]]:
                if fix:
                    fixed = f"{BLOB}{url['file']}#L{jobs[link['job']]}"
                    lines[i] = lines[i][:link.start("url")] + fixed + lines[i][link.end("url"):]
                else:
                    problems.append(f"README.md:{i + 1}: `{link['job']}` is defined at {url['file']}:{jobs[link['job']]}, "
                                    f"the link says L{url['line']} (run with --fix)")

    # ---- the counts the docs state are the counts in the repository ----------------------------
    readme = "\n".join(lines)
    docs = {"README.md": readme}
    for name in ("docs/tour.md", "docs/cloud-security.md"):
        docs[name] = (ROOT / name).read_text(encoding="utf-8")
    modules, policy_tests, mutations = count_module_tests(), count_policy_tests(), count_mutations()
    rules, assumptions = count_rules(), count_assumptions()
    checks = [  # (file, pattern with one group, actual, what)
        ("README.md", r"^\| Module unit tests[^|]*\|[^|]*?\b(\d+) passing", modules, "module tests"),
        ("README.md", r"^\| Policy unit tests[^|]*\|[^|]*?\b(\d+) passing", policy_tests, "policy unit tests"),
        ("README.md", r"^\| Policy mutation tests[^|]*\|[^|]*?\b(\d+) of \d+ bad changes", mutations, "mutation tests"),
        ("README.md", r"^\| Policy mutation tests[^|]*\|[^|]*?\b\d+ of (\d+) bad changes", mutations, "mutation tests"),
        ("README.md", r"\*\*(\d+) deliberately bad changes", mutations, "mutation tests"),
        ("README.md", r"\b(\d+) rules \(no Owner", rules, "policy rules"),
        ("README.md", r"\b(\w+) specific assumptions", assumptions, "runbook assumptions"),
        ("docs/cloud-security.md", r"lists the (\w+) assumptions", assumptions, "runbook assumptions"),
        ("docs/cloud-security.md", r"\b(\d+) deliberately bad changes", mutations, "mutation tests"),
        ("docs/cloud-security.md", r"\*\*(\d+) unit tests\*\*", policy_tests, "policy unit tests"),
        ("docs/cloud-security.md", r"\*\*(\d+) mutation tests\*\*", mutations, "mutation tests"),
        ("docs/tour.md", r"\b(\d+) unit tests plus \d+ mutation tests", policy_tests, "policy unit tests"),
        ("docs/tour.md", r"\b\d+ unit tests plus (\d+) mutation tests", mutations, "mutation tests"),
        ("docs/tour.md", r"\b(\w+) assumptions wait", assumptions, "runbook assumptions"),
    ]
    for name, pattern, actual, what in checks:
        found = re.search(pattern, docs[name], re.M)
        if not found:
            problems.append(f"{name}: could not find the stated count of {what} (pattern {pattern!r}); "
                            "if the wording changed, update scripts/check-verified-table.py")
        elif as_number(found.group(1)) != actual:
            problems.append(f"{name} says {found.group(1)} {what}, the repository has {actual}")

    if fix:
        README.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"verified table ok: {rows} rows, {links} links to CI jobs, counts match "
          f"({modules} module tests, {policy_tests} policy tests, {mutations} mutations, "
          f"{rules} rules, {assumptions} assumptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
