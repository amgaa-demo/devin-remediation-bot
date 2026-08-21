# Devin Remediation Bot

Event-driven security remediation: scanner findings filed as GitHub issues are
automatically remediated by [Devin](https://devin.ai) sessions, with pull
requests, issue comments, and a live dashboard as observable outputs.

```
GitHub issue labeled `devin-remediate`
        │  (webhook: issues/labeled)
        ▼
┌─────────────────────┐    POST /v3/organizations/{org}/sessions
│  FastAPI service    │ ─────────────────────────────────────────▶  Devin session
│  /webhook           │                                             (isolated VM,
│  background poller  │ ◀─────────────────────────────────────────  clones repo,
│  SQLite state       │    GET session status every 30s             opens PR)
└─────────────────────┘
        │
        ▼
  issue comments (start / result / PR link)
  dashboard at /        metrics at /metrics
```

Issues labeled `needs-human` are deliberately **not** automated — the system
records them as routed to humans. Automation that knows its limits is the point.

## Prerequisites

- A Devin account with the GitHub App installed on your org, scoped to the
  target repository (the superset fork) — Devin must be able to push branches
  and open PRs there.
- A Devin **service user** API key (`cog_...`) and your org ID
  (Devin app → Settings → Service Users).
- A GitHub **fine-grained PAT** with `Issues: Read and write` on the fork
  (used only to post status comments).

## Run

```bash
cp .env.example .env   # fill in real values
docker compose up --build
# dashboard: http://localhost:8000/    metrics: http://localhost:8000/metrics
```

## Wire up the GitHub webhook

The service must be reachable from GitHub. For a local demo use
[smee.io](https://smee.io):

```bash
npx smee-client --url https://smee.io/YOUR_CHANNEL --target http://localhost:8000/webhook
```

Then on the superset fork: **Settings → Webhooks → Add webhook**
- Payload URL: your smee channel URL
- Content type: `application/json`
- Secret: same value as `WEBHOOK_SECRET` in `.env`
- Events: *Let me select individual events* → **Issues** only

## Trigger a remediation

Add the `devin-remediate` label to any prepared issue. Within seconds the
dashboard shows the task as `running` with a link to the live Devin session;
the bot comments on the issue when the session starts and again when it ends
(with the PR link on success).

## Simulate without GitHub (offline demo)

`WEBHOOK_SECRET=` (empty) disables signature verification; then replay a
synthetic event:

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: issues" \
  -d '{
    "action": "labeled",
    "label": {"name": "devin-remediate"},
    "issue": {
      "number": 1,
      "title": "[Security] Bump python-multipart 0.0.29 -> 0.0.31",
      "body": "## Remediation\nBump python-multipart to 0.0.31 in requirements/development.txt ...",
      "html_url": "https://github.com/your-org/superset/issues/1",
      "labels": [{"name": "devin-remediate"}]
    }
  }'
```

## Observability

- **`/` dashboard** — per-task status (queued / running / succeeded / failed /
  routed-to-humans), links to issue, PR, and live Devin session, elapsed time;
  auto-refreshes every 15s.
- **`/metrics` JSON** — totals by status, success rate, average time-to-PR.
- **Structured logs** — every state transition is logged as JSON and recorded
  in an `events` table shown on the dashboard.

Together these answer: *"If I were an engineering leader, how would I know
this is working?"* — count of findings remediated, success rate, mean time to
remediation, and an audit trail per finding.

## Design decisions

- **Issues as the event source.** Any scanner (Dependabot, pip-audit in CI,
  Snyk) can file issues; the label is the explicit human-controllable gate.
- **The issue body IS the work order.** Structured acceptance criteria in the
  issue are passed to Devin verbatim — no prompt logic hidden in code.
- **Triage before automation.** `needs-human` findings (e.g. major-version
  framework upgrades) are recorded but never sent to Devin.
- **SQLite as system of record.** Every transition lands in one file; the
  dashboard and metrics are pure reads. Trivial to swap for Postgres.

## Extending in a real engagement

- Replace manual labels with Dependabot/Snyk webhooks feeding a triage step
  (auto-classify patch/minor bumps as automatable, majors as `needs-human`).
- Use Devin playbooks to encode org-specific conventions once instead of
  per-prompt.
- Add Slack notifications and CI-status gating before requesting review.
