"""Thin client for the Devin v3 Organization API.

Endpoints used:
  POST {base}/organizations/{org_id}/sessions          -> create session
  GET  {base}/organizations/{org_id}/sessions/{id}     -> poll status

Terminal statuses per docs: "exit", "error", "suspended".
The session object exposes pull_requests: [{"pr_state": ..., "pr_url": ...}].
"""
import httpx

from . import config

HEADERS = {
    "Authorization": f"Bearer {config.DEVIN_API_KEY}",
    "Content-Type": "application/json",
}
BASE = f"{config.DEVIN_API_BASE}/organizations/{config.DEVIN_ORG_ID}"

# Statuses after which a session will not make further progress.
TERMINAL_STATUSES = {"exit", "error", "suspended"}
def is_complete(session: dict) -> bool:
    """Done = hard-terminal status, or running with sub-status 'finished'."""
    return (session.get("status") in TERMINAL_STATUSES
            or extract_status_detail(session) == "finished")


def build_prompt(repo: str, issue_number: int, issue_title: str, issue_body: str) -> str:
    """Turn a GitHub issue into a self-contained Devin work order."""
    return f"""You are remediating a scanner finding in the repository {repo}.

Work on GitHub issue #{issue_number}: {issue_title}

Full issue body (follow its Remediation and Acceptance criteria sections exactly):
---
{issue_body}
---

Rules:
- Create a branch named devin/issue-{issue_number} from master.
- Make the minimal change that satisfies every acceptance criterion. Do not modify anything else.
- Open a pull request against master in {repo}. The PR title must follow the convention in the issue. The PR description must reference issue #{issue_number} and list the advisory IDs or findings resolved.
- If an acceptance criterion cannot be satisfied, stop and explain why in the session instead of guessing.
"""


async def create_session(prompt: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{BASE}/sessions", headers=HEADERS, json={"prompt": prompt})
        r.raise_for_status()
        return r.json()  # contains session_id and url


async def get_session(session_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{BASE}/sessions/{session_id}", headers=HEADERS)
        r.raise_for_status()
        return r.json()


def extract_pr_url(session: dict) -> str | None:
    prs = session.get("pull_requests") or []
    if prs:
        return prs[0].get("pr_url")
    return None


def extract_status_detail(session: dict) -> str | None:
    # Field name for the sub-status varies across doc versions; read defensively.
    for key in ("status_detail", "status_enum", "detail"):
        if session.get(key):
            return str(session[key])
    return None
