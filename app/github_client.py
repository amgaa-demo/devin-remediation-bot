"""Minimal GitHub REST client: comment on issues to close the feedback loop."""
import httpx

from . import config

HEADERS = {
    "Authorization": f"Bearer {config.GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def comment_on_issue(issue_number: int, body: str):
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/issues/{issue_number}/comments"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=HEADERS, json={"body": body})
        r.raise_for_status()

async def find_pr_for_branch(branch: str) -> str | None:
    """Find a PR whose head is the given branch. Source-of-truth success check."""
    owner = config.GITHUB_REPO.split("/")[0]
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/pulls"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=HEADERS,
                             params={"head": f"{owner}:{branch}", "state": "all"})
        r.raise_for_status()
        prs = r.json()
    return prs[0]["html_url"] if prs else None
