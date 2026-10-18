"""Opens the pull request for a verified fix, through the GitHub REST API.

Only on a person's explicit approval (the API requires approve=true and the id of
the exact fix), only for GitHub repositories, and only with a token that can push.
The branch is created from the commit the fix was verified against; if the file
has changed on GitHub since then, nothing is written.
"""
import base64
import re
from typing import Any, Dict, Optional

import httpx

API = "https://api.github.com"


class PullRequestError(Exception):
    pass


def github_repo(url: str) -> Optional[str]:
    m = re.match(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


async def open_pull_request(repo_url: str, token: str, ver: Dict[str, Any]) -> Dict[str, Any]:
    slug = github_repo(repo_url)
    if not slug:
        raise PullRequestError("Pull requests can only be opened for repositories connected from a github.com URL. "
                               "Download the patch instead.")
    draft = ver["pr_draft"]
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=30) as gh:
        async def call(method: str, path: str, **kw) -> Any:
            r = await gh.request(method, path, **kw)
            if r.status_code >= 400:
                msg = r.json().get("message", r.text[:200]) if r.headers.get("content-type", "").startswith(
                    "application/json") else r.text[:200]
                raise PullRequestError(f"GitHub {method} {path.split('?')[0]}: {r.status_code} {msg}")
            return r.json() if r.content else {}

        info = await call("GET", f"/repos/{slug}")
        base_branch = info["default_branch"]
        base_sha = ver.get("base_commit") or (await call("GET", f"/repos/{slug}/git/ref/heads/{base_branch}"))["object"]["sha"]
        await call("POST", f"/repos/{slug}/git/refs", json={"ref": f"refs/heads/{draft['branch']}", "sha": base_sha})
        for path, text in ver["files"].items():
            existing = None
            try:
                existing = await call("GET", f"/repos/{slug}/contents/{path}?ref={draft['branch']}")
            except PullRequestError as e:
                if " 404 " not in str(e):
                    raise
            body = {"message": f"{draft['title']}\n\nVerified by GraphSleuth ({ver['headline']}).",
                    "content": base64.b64encode(text.encode()).decode(), "branch": draft["branch"]}
            if existing:
                body["sha"] = existing["sha"]
            await call("PUT", f"/repos/{slug}/contents/{path}", json=body)
        pr = await call("POST", f"/repos/{slug}/pulls", json={
            "title": draft["title"], "head": draft["branch"], "base": base_branch, "body": draft["body"],
            "draft": ver["status"] != "verified"})
        requested = [r["login"] for r in draft["reviewers"] if r.get("login")]
        review_note = None
        if requested:
            try:
                await call("POST", f"/repos/{slug}/pulls/{pr['number']}/requested_reviewers",
                           json={"reviewers": requested})
            except PullRequestError as e:  # reviewers must be collaborators; the PR is still open
                review_note = str(e)
    return {"number": pr["number"], "url": pr["html_url"], "branch": draft["branch"], "base": base_branch,
            "draft": pr.get("draft", False), "requested_reviewers": requested, "review_note": review_note}


def format_patch(ver: Dict[str, Any]) -> str:
    """The fix as a mailbox patch (git am / git apply), with the PR description as its message."""
    draft = ver["pr_draft"]
    diff = ver["diff"]
    if ver.get("test_code"):
        lines = ver["test_code"].rstrip("\n").splitlines()
        diff += (f"--- /dev/null\n+++ b/{ver['test_path']}\n@@ -0,0 +1,{len(lines)} @@\n"
                 + "".join(f"+{ln}\n" for ln in lines))
    return (f"From: GraphSleuth <graphsleuth@localhost>\nSubject: [PATCH] {draft['title']}\n\n"
            f"{draft['body']}\n---\n{diff}")
