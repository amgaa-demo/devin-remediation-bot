"""Event-driven remediation service.

Flow:
  GitHub issue labeled `devin-remediate`
    -> POST /webhook (HMAC-verified)
    -> create Devin session with the issue body as work order
    -> background poller tracks the session
    -> on completion: comment on the issue, record metrics
  GET /          -> HTML dashboard (observability for humans)
  GET /metrics   -> JSON metrics (observability for machines)
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, devin_client, github_client, store

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
)
log = logging.getLogger("remediation-bot")

app = FastAPI(title="Devin Remediation Bot")


# --------------------------------------------------------------------------- #
# Webhook intake
# --------------------------------------------------------------------------- #

def verify_signature(payload: bytes, signature: str | None):
    if not config.WEBHOOK_SECRET:
        return  # signature checking disabled (local simulation mode)
    if not signature:
        raise HTTPException(401, "Missing X-Hub-Signature-256")
    expected = "sha256=" + hmac.new(
        config.WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Bad webhook signature")


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
):
    payload = await request.body()
    verify_signature(payload, x_hub_signature_256)
    body = json.loads(payload)

    if x_github_event != "issues":
        return {"ok": True, "ignored": f"event={x_github_event}"}

    action = body.get("action")
    issue = body.get("issue") or {}
    number = issue.get("number")
    title = issue.get("title", "")
    labels = {l["name"] for l in issue.get("labels", [])}
    event_label = (body.get("label") or {}).get("name")

    # Trigger: the `devin-remediate` label being added.
    if action == "labeled" and event_label == config.TRIGGER_LABEL:
        if config.HUMAN_LABEL in labels:
            store.upsert_task(number, title, issue["html_url"], "skipped_human")
            store.log_event(number, "skip", f"issue #{number} carries {config.HUMAN_LABEL}; not automated")
            log.info(json.dumps(f"skipped #{number}: needs-human"))
            return {"ok": True, "skipped": "needs-human"}
        asyncio.create_task(start_remediation(number, title, issue.get("body") or "", issue["html_url"]))
        return {"ok": True, "triggered": number}

    if action == "labeled" and event_label == config.HUMAN_LABEL:
        store.upsert_task(number, title, issue["html_url"], "skipped_human")
        store.log_event(number, "skip", f"issue #{number} routed to humans")
        return {"ok": True, "recorded": "needs-human"}

    return {"ok": True, "ignored": f"action={action}, label={event_label}"}


async def start_remediation(number: int, title: str, body: str, issue_url: str):
    store.upsert_task(number, title, issue_url, "queued")
    store.log_event(number, "trigger", f"label received; creating Devin session for issue #{number}")
    prompt = devin_client.build_prompt(config.GITHUB_REPO, number, title, body)
    try:
        session = await devin_client.create_session(prompt)
    except Exception as e:  # noqa: BLE001 - record any failure, don't crash the app
        store.upsert_task(number, title, issue_url, "failed", error=str(e))
        store.log_event(number, "error", f"session creation failed: {e}")
        log.error(json.dumps(f"session creation failed for #{number}: {e}"))
        return
    store.upsert_task(number, title, issue_url, "running",
                      session_id=session["session_id"], session_url=session.get("url"))
    store.log_event(number, "session_created",
                    f"session {session['session_id']} -> {session.get('url')}")
    log.info(json.dumps(f"issue #{number} -> session {session['session_id']}"))
    try:
        await github_client.comment_on_issue(
            number,
            f"🤖 Remediation started. Devin session: {session.get('url', session['session_id'])}",
        )
    except Exception as e:  # noqa: BLE001
        store.log_event(number, "warn", f"could not comment on issue: {e}")


# --------------------------------------------------------------------------- #
# Background poller
# --------------------------------------------------------------------------- #

async def poll_forever():
    while True:
        try:
            await poll_once()
        except Exception as e:  # noqa: BLE001
            log.error(json.dumps(f"poll loop error: {e}"))
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


async def poll_once():
    for task in store.active_tasks():
        if not task["session_id"]:
            continue
        number = task["issue_number"]
        try:
            session = await devin_client.get_session(task["session_id"])
        except Exception as e:  # noqa: BLE001
            store.log_event(number, "warn", f"status poll failed: {e}")
            continue

        status = session.get("status", "")
        detail = devin_client.extract_status_detail(session)
        pr_url = devin_client.extract_pr_url(session)

        if status in devin_client.TERMINAL_STATUSES:
            success = status == "exit" and pr_url is not None
            final = "succeeded" if success else "failed"
            store.update_status(number, final, status_detail=detail, pr_url=pr_url,
                                error=None if success else f"terminal status={status}",
                                completed=True)
            store.log_event(number, "done", f"session ended: {status} / {detail}; pr={pr_url}")
            log.info(json.dumps(f"issue #{number} finished: {final} pr={pr_url}"))
            emoji = "✅" if success else "❌"
            msg = (f"{emoji} Remediation {'complete' if success else 'did not complete'}.\n\n"
                   f"- Devin session: {task['session_url']}\n"
                   f"- Status: `{status}` ({detail})\n")
            if pr_url:
                msg += f"- Pull request: {pr_url}\n"
            try:
                await github_client.comment_on_issue(number, msg)
            except Exception as e:  # noqa: BLE001
                store.log_event(number, "warn", f"could not comment on issue: {e}")
        else:
            store.update_status(number, "running", status_detail=detail, pr_url=pr_url)


@app.on_event("startup")
async def startup():
    store.init()
    asyncio.create_task(poll_forever())
    log.info(json.dumps("remediation bot started"))


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #

@app.get("/metrics")
async def metrics():
    return JSONResponse(store.metrics())


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    m = store.metrics()
    tasks = store.all_tasks()
    events = store.recent_events(20)

    def badge(status: str) -> str:
        colors = {"succeeded": "#1a7f37", "failed": "#cf222e", "running": "#9a6700",
                  "queued": "#57606a", "skipped_human": "#8250df"}
        return (f'<span style="background:{colors.get(status, "#57606a")};color:#fff;'
                f'padding:2px 10px;border-radius:12px;font-size:12px">{status}</span>')

    rows = "".join(
        f"<tr><td><a href='{t['issue_url']}'>#{t['issue_number']}</a></td>"
        f"<td>{t['issue_title']}</td>"
        f"<td>{badge(t['status'])}</td>"
        f"<td>{t['status_detail'] or ''}</td>"
        f"<td>{f'<a href=\"{t['pr_url']}\">PR</a>' if t['pr_url'] else ''}</td>"
        f"<td>{f'<a href=\"{t['session_url']}\">session</a>' if t['session_url'] else ''}</td>"
        f"<td>{round((t['completed_at'] or time.time()) - t['created_at'])}s</td></tr>"
        for t in tasks
    )
    event_rows = "".join(
        f"<tr><td>{time.strftime('%H:%M:%S', time.localtime(e['ts']))}</td>"
        f"<td>{'#' + str(e['issue_number']) if e['issue_number'] else ''}</td>"
        f"<td>{e['kind']}</td><td>{e['message']}</td></tr>"
        for e in events
    )
    sr = f"{m['success_rate'] * 100:.0f}%" if m["success_rate"] is not None else "–"
    avg = f"{m['avg_time_to_success_seconds']:.0f}s" if m["avg_time_to_success_seconds"] else "–"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Remediation Dashboard</title>
<meta http-equiv="refresh" content="15">
<style>
  body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
         margin: 40px; color: #1f2328; background: #fff; }}
  h1 {{ font-size: 18px; letter-spacing: 0.04em; text-transform: uppercase; }}
  .cards {{ display: flex; gap: 16px; margin: 24px 0; }}
  .card {{ border: 1px solid #d0d7de; border-radius: 6px; padding: 16px 24px; }}
  .card .n {{ font-size: 28px; font-weight: 700; }}
  .card .l {{ font-size: 12px; color: #57606a; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #d0d7de;
            font-size: 13px; }}
  th {{ font-size: 11px; text-transform: uppercase; color: #57606a; }}
  h2 {{ font-size: 14px; margin-top: 32px; }}
  a {{ color: #0969da; }}
</style></head><body>
<h1>Devin Remediation Bot — {config.GITHUB_REPO}</h1>
<div class="cards">
  <div class="card"><div class="n">{m['total_tasks']}</div><div class="l">total tasks</div></div>
  <div class="card"><div class="n">{m['by_status'].get('running', 0) + m['by_status'].get('queued', 0)}</div><div class="l">active</div></div>
  <div class="card"><div class="n">{m['by_status'].get('succeeded', 0)}</div><div class="l">succeeded</div></div>
  <div class="card"><div class="n">{m['by_status'].get('failed', 0)}</div><div class="l">failed</div></div>
  <div class="card"><div class="n">{m['by_status'].get('skipped_human', 0)}</div><div class="l">routed to humans</div></div>
  <div class="card"><div class="n">{sr}</div><div class="l">success rate</div></div>
  <div class="card"><div class="n">{avg}</div><div class="l">avg time to PR</div></div>
</div>
<table>
<tr><th>Issue</th><th>Title</th><th>Status</th><th>Detail</th><th>PR</th><th>Session</th><th>Elapsed</th></tr>
{rows or '<tr><td colspan="7">No tasks yet — label an issue to begin.</td></tr>'}
</table>
<h2>Recent events</h2>
<table>
<tr><th>Time</th><th>Issue</th><th>Kind</th><th>Message</th></tr>
{event_rows or '<tr><td colspan="4">No events yet.</td></tr>'}
</table>
</body></html>"""
