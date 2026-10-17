"""Company knowledge: the people, teams, issues, pull requests, reviews and
discussions around a repository, linked into the same graph as its code.

Two sources produce the same normalized shape:
  * GitHub's REST API for github.com repositories (public data only), and
  * a `.cd-knowledge.json` file in the repository root (used by the demo repo and
    for exports from other trackers).

Writing is idempotent (everything is MERGEd), so the same code does the first
load at ingestion and later incremental syncs that only fetch what changed.
"""
import json
import os
import re
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx
import structlog

from app.core.falkordb import get_falkordb
from app.models.graph_models import INDEX_QUERIES
from app.services import cypher_queries as Q

logger = structlog.get_logger()

KNOWLEDGE_FILE = ".cd-knowledge.json"
CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
GITHUB_API = "https://api.github.com"
BATCH = 500
KNOWLEDGE_LABELS = {"Issue", "PullRequest", "Comment", "Decision", "Team"}

# "Fixes #12", "closes #3, resolves #4" (GitHub's closing keywords).
CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.I)
REFERENCE = re.compile(r"(?<![\w/])#(\d+)\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
# Sentences that record a decision. Kept literal so every Decision node can be traced to the words that made it one.
DECISION = re.compile(
    r"\bdecision\s*:|\b(?:decided|we agreed|agreed to|agreed on|settled on|the plan is|going forward|from now on|"
    r"we chose|in favou?r of|we(?:'ll| will) (?:use|keep|go with|always|never|not)|let'?s go with|"
    r"we(?:'re| are) (?:going with|not going to|going to keep)|not going to (?:add|support|change|merge|accept)|"
    r"we (?:don'?t|do not|won'?t|will not) (?:want|support|accept|add)|won'?t fix|wontfix|"
    r"must always|should always|should never|must never)\b", re.I)
BACKTICK = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`")
WORD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")


def iso_to_ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def github_slug(url: str) -> Optional[Tuple[str, str]]:
    parsed = urlparse(url or "")
    if parsed.hostname not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


# ----------------------------------------------------------------- text rules

def closing_refs(text: str) -> List[int]:
    return sorted({int(n) for n in CLOSING.findall(text or "")})


def references(text: str) -> List[int]:
    return sorted({int(n) for n in REFERENCE.findall(text or "")})


def decision_sentences(text: str) -> List[str]:
    out = []
    for raw in SENTENCE.split(text or ""):
        s = raw.strip().lstrip("-*> ").strip()
        if 12 <= len(s) <= 400 and DECISION.search(s):
            out.append(s)
    return out


def mentioned_functions(text: str, names: Set[str]) -> Set[str]:
    """Function names a text refers to: anything in backticks, plus bare words that are
    distinctive enough (snake_case or camelCase) not to be ordinary English."""
    hits = {m.split(".")[-1] for m in BACKTICK.findall(text or "")}
    for w in WORD.findall(text or ""):
        if "_" in w or re.search(r"[a-z][A-Z]", w):
            hits.add(w)
    return {h for h in hits if h in names}


def parse_codeowners(content: str) -> List[Tuple[str, List[str]]]:
    rules = []
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        owners = [o for o in owners if o.startswith("@") or "@" in o]
        if owners:
            rules.append((pattern, owners))
    return rules


def codeowners_match(pattern: str, path: str) -> bool:
    """GitHub CODEOWNERS matching (gitignore style, the subset that real files use)."""
    anchored = pattern.startswith("/")
    p = pattern.strip("/")
    if pattern == "*":
        return True
    if pattern.endswith("/"):  # a directory: everything below it
        return path.startswith(p + "/") or (not anchored and f"/{p}/" in f"/{path}")
    if "/" not in p and not anchored:  # a bare name or glob matches at any depth
        return any(fnmatch(part, p) for part in path.split("/")) or fnmatch(path, p)
    if fnmatch(path, p) or path.startswith(p + "/"):
        return True
    return p.endswith("/**") and path.startswith(p[:-3])


def file_owners(rules: List[Tuple[str, List[str]]], paths: Iterable[str]) -> Dict[str, Tuple[str, List[str]]]:
    """The last matching rule wins, as on GitHub."""
    out = {}
    for path in paths:
        for pattern, owners in rules:
            if codeowners_match(pattern, path):
                out[path] = (pattern, owners)
    return out


def read_codeowners(repo_path: str) -> List[Tuple[str, List[str]]]:
    for rel in CODEOWNERS_PATHS:
        full = os.path.join(repo_path, rel)
        if os.path.isfile(full):
            with open(full, encoding="utf-8", errors="replace") as fh:
                return parse_codeowners(fh.read())
    return []


# --------------------------------------------------------------------- sources

def empty() -> Dict[str, Any]:
    return {"source": "none", "people": [], "teams": [], "issues": [], "pulls": [], "comments": []}


def load_file(repo_path: str) -> Optional[Dict[str, Any]]:
    full = os.path.join(repo_path, KNOWLEDGE_FILE)
    if not os.path.isfile(full):
        return None
    with open(full, encoding="utf-8") as fh:
        data = json.load(fh)
    return {**empty(), **data, "source": "file"}


class GitHubSource:
    """Reads public issues, pull requests, reviews and comments over the REST API.

    Without a token GitHub allows 60 requests an hour, so the number of pull
    requests whose reviews are read is capped harder in that case.
    """

    def __init__(self, owner: str, repo: str, token: Optional[str] = None, max_items: int = 200):
        self.base = f"{GITHUB_API}/repos/{owner}/{repo}"
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(headers=headers, timeout=30)
        self.max_items = max_items
        self.review_limit = 40 if token else 15

    def _pages(self, path: str, params: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        page = 1
        while len(out) < limit:
            resp = self.client.get(f"{self.base}{path}", params={**params, "per_page": 100, "page": page})
            if resp.status_code in (403, 429) and out:
                logger.warning("GitHub rate limit reached; keeping what was fetched", path=path)
                break
            resp.raise_for_status()
            rows = resp.json()
            out.extend(rows)
            if len(rows) < 100:
                break
            page += 1
        return out[:limit]

    def fetch(self, since: Optional[str] = None) -> Dict[str, Any]:
        data = empty()
        data["source"] = "github"
        since_params = {"since": since} if since else {}
        people: Dict[str, Dict[str, Any]] = {}

        def person(user: Optional[Dict[str, Any]]) -> Optional[str]:
            if not user or user.get("type") == "Bot":
                return None
            login = user["login"]
            people.setdefault(login, {"login": login, "name": login, "email": None})
            return login

        # Commit authors give the login -> email link that joins GitHub people to git authors.
        for c in self._pages("/commits", {}, 300 if not since else 100):
            login = person(c.get("author"))
            if login and (c.get("commit") or {}).get("author"):
                people[login]["email"] = people[login]["email"] or c["commit"]["author"]["email"].lower()
                people[login]["name"] = c["commit"]["author"]["name"] or login

        items = self._pages("/issues", {"state": "all", "sort": "updated", "direction": "desc", **since_params},
                            self.max_items)
        for it in items:
            row = {"number": it["number"], "title": it["title"], "body": it.get("body") or "",
                   "state": it["state"], "url": it["html_url"], "author": person(it.get("user")),
                   "created_at": it["created_at"], "closed_at": it.get("closed_at"),
                   "labels": [lb["name"] for lb in it.get("labels", []) if isinstance(lb, dict)]}
            if "pull_request" in it:
                row.update(merged_at=(it["pull_request"] or {}).get("merged_at"), commits=[], reviews=[])
                data["pulls"].append(row)
            else:
                data["issues"].append(row)

        if data["pulls"]:
            wanted = {p["number"]: p for p in data["pulls"]}
            for pr in self._pages("/pulls", {"state": "all", "sort": "updated", "direction": "desc"},
                                  min(self.max_items, 300)):
                if pr["number"] in wanted and pr.get("merge_commit_sha") and pr.get("merged_at"):
                    wanted[pr["number"]]["merge_commit_sha"] = pr["merge_commit_sha"]
            merged = [p for p in data["pulls"] if p.get("merged_at")][: self.review_limit]
            for pr in merged:
                try:
                    for rv in self._pages(f"/pulls/{pr['number']}/reviews", {}, 50):
                        who = person(rv.get("user"))
                        if who:
                            pr["reviews"].append({"author": who, "state": rv["state"], "body": rv.get("body") or "",
                                                  "submitted_at": rv.get("submitted_at"),
                                                  "url": rv.get("html_url")})
                except httpx.HTTPError as e:
                    logger.warning("Could not read reviews", pr=pr["number"], error=str(e))
                    break

        numbers = {i["number"] for i in data["issues"]} | {p["number"] for p in data["pulls"]}
        for path in ("/issues/comments", "/pulls/comments"):
            for cm in self._pages(path, {"sort": "updated", "direction": "desc", **since_params}, 500):
                url = cm.get("issue_url") or cm.get("pull_request_url") or ""
                num = int(url.rstrip("/").split("/")[-1]) if url else None
                who = person(cm.get("user"))
                if num in numbers and who:
                    data["comments"].append({"id": str(cm["id"]), "on": num, "author": who, "body": cm.get("body") or "",
                                             "created_at": cm["created_at"], "url": cm["html_url"],
                                             "path": cm.get("path")})
        data["people"] = list(people.values())
        return data


def fetch_knowledge(repo_path: Optional[str], url: str, token: Optional[str],
                    since: Optional[str] = None) -> Dict[str, Any]:
    if repo_path:
        local = load_file(repo_path)
        if local:
            return local
    slug = github_slug(url)
    if slug:
        return GitHubSource(*slug, token=token).fetch(since)
    return empty()


# --------------------------------------------------------------------- writing

class KnowledgeWriter:
    def __init__(self, graph_name: str):
        self.graph = graph_name
        self.db = get_falkordb()

    def _batched(self, cypher: str, rows: List[dict]) -> None:
        for i in range(0, len(rows), BATCH):
            self.db.query(self.graph, cypher, {"rows": rows[i:i + BATCH]})

    def write(self, data: Dict[str, Any], repo_path: Optional[str] = None,
              local_commits: Optional[Dict[str, List[str]]] = None) -> Dict[str, int]:
        """`local_commits` maps a merge commit sha to the commits it brought in (read from the clone)."""
        # A graph ingested before knowledge existed gets its indexes on the first sync.
        existing = {r["label"] for r in self.db.ro(self.graph, "CALL db.indexes() YIELD label RETURN label", {})} \
            if self.db.graph_exists(self.graph) else set()
        for q in INDEX_QUERIES:
            label = re.search(r"\(\w+:(\w+)\)", q).group(1)
            if label in KNOWLEDGE_LABELS and label not in existing:
                try:
                    self.db.query(self.graph, q)
                except Exception:  # already there in another form
                    pass
        people = {p["login"]: p for p in data.get("people", []) if p.get("login")}
        # Everyone who appears anywhere needs a Person node, joined to git authors by email where known.
        logins: Set[str] = set()
        for row in data.get("issues", []) + data.get("pulls", []) + data.get("comments", []):
            if row.get("author"):
                logins.add(row["author"])
        for pr in data.get("pulls", []):
            logins.update(r["author"] for r in pr.get("reviews", []) if r.get("author"))
        for team in data.get("teams", []):
            logins.update(team.get("members", []))
        person_rows = []
        for login in sorted(logins | set(people)):
            p = people.get(login, {})
            person_rows.append({"login": login, "name": p.get("name") or login,
                                "email": (p.get("email") or f"{login}@users.noreply.github.com").lower()})
        self._batched(Q.WRITE_KNOWLEDGE_PEOPLE, person_rows)
        self._batched(Q.WRITE_TEAMS, [{"name": t["name"], "members": t.get("members", [])}
                                      for t in data.get("teams", [])])

        def item(row: Dict[str, Any]) -> Dict[str, Any]:
            return {"number": row["number"], "title": row["title"][:300], "body": (row.get("body") or "")[:8000],
                    "state": row.get("state", "open"), "url": row.get("url", ""), "author": row.get("author"),
                    "created_at": row.get("created_at"), "created_ms": iso_to_ms(row.get("created_at")),
                    "closed_ms": iso_to_ms(row.get("closed_at")), "labels": row.get("labels", []),
                    "merged_ms": iso_to_ms(row.get("merged_at"))}

        issues = [item(i) for i in data.get("issues", [])]
        pulls = [item(p) for p in data.get("pulls", [])]
        self._batched(Q.WRITE_ISSUES, issues)
        self._batched(Q.WRITE_PULLS, pulls)

        comments = [{"id": c["id"], "on": c["on"], "author": c.get("author"), "body": (c.get("body") or "")[:8000],
                     "url": c.get("url", ""), "created_at": c.get("created_at"),
                     "created_ms": iso_to_ms(c.get("created_at")), "path": c.get("path")}
                    for c in data.get("comments", [])]
        reviews = [{"number": pr["number"], "author": r["author"], "state": r.get("state", "COMMENTED"),
                    "body": (r.get("body") or "")[:4000], "at_ms": iso_to_ms(r.get("submitted_at")),
                    "url": r.get("url") or pr.get("url", "")}
                   for pr in data.get("pulls", []) for r in pr.get("reviews", []) if r.get("author")]
        self._batched(Q.WRITE_COMMENTS, comments)
        self._batched(Q.WRITE_COMMENT_FILES, [c for c in comments if c["path"]])
        self._batched(Q.WRITE_REVIEWS, reviews)
        # Review summaries are discussion too ("LGTM, but we agreed to ..."), so they become comments.
        self._batched(Q.WRITE_COMMENTS, [
            {"id": f"review-{r['number']}-{i}", "on": r["number"], "author": r["author"], "body": r["body"],
             "url": r["url"], "created_at": None, "created_ms": r["at_ms"], "path": None}
            for i, r in enumerate(reviews) if r["body"].strip()])

        stats = {"issues": len(issues), "pull_requests": len(pulls), "comments": len(comments),
                 "reviews": len(reviews), "teams": len(data.get("teams", []))}
        stats.update(self._link_text(data, reviews))
        stats["pr_commit_links"] = self._link_commits(data, local_commits or {})
        if repo_path:
            stats["owned_files"] = self._write_owners(read_codeowners(repo_path))
        return stats

    def _link_text(self, data: Dict[str, Any], reviews: List[dict]) -> Dict[str, int]:
        """Edges that come from what people wrote: PR -FIXES-> issue, -REFERENCES-> other items,
        -MENTIONS-> functions, and Decision nodes for sentences that record a decision."""
        names = {r["name"] for r in self.db.ro(self.graph, "MATCH (f:Function) RETURN DISTINCT f.name AS name", {})}
        # On a public tracker anyone can comment; only people who commit to the repo make its decisions.
        deciders = None
        if data.get("source") == "github":
            deciders = {r["login"] for r in self.db.ro(
                self.graph, "MATCH (p:Person)-[:AUTHORED]->(:Commit) WHERE p.login IS NOT NULL "
                            "RETURN DISTINCT p.login AS login", {})}
        numbers = {i["number"] for i in data.get("issues", [])} | {p["number"] for p in data.get("pulls", [])}
        fixes, refs, mentions, decisions, about = [], [], [], [], []

        def scan(kind: str, key: Any, text: str, url: str, author: Optional[str], at_ms: Optional[int],
                 own_number: Optional[int]) -> None:
            for n in references(text):
                if n in numbers and n != own_number:
                    refs.append({"kind": kind, "key": key, "number": n})
            for name in mentioned_functions(text, names):
                mentions.append({"kind": kind, "key": key, "name": name})
            for i, sentence in enumerate(decision_sentences(text) if deciders is None or author in deciders else []):
                did = f"{kind}-{key}-{i}"
                decisions.append({"id": did, "kind": kind, "key": key, "text": sentence,
                                  "url": url, "author": author, "at_ms": at_ms})
                about.extend({"id": did, "name": name} for name in mentioned_functions(sentence, names))

        for issue in data.get("issues", []):
            scan("issue", issue["number"], f"{issue['title']}\n{issue.get('body') or ''}", issue.get("url", ""),
                 issue.get("author"), iso_to_ms(issue.get("created_at")), issue["number"])
        for pr in data.get("pulls", []):
            text = f"{pr['title']}\n{pr.get('body') or ''}"
            fixes += [{"pr": pr["number"], "issue": n} for n in closing_refs(text) if n in numbers]
            scan("pr", pr["number"], text, pr.get("url", ""), pr.get("author"),
                 iso_to_ms(pr.get("merged_at") or pr.get("created_at")), pr["number"])
        for c in data.get("comments", []):
            scan("comment", c["id"], c.get("body") or "", c.get("url", ""), c.get("author"),
                 iso_to_ms(c.get("created_at")), None)
        for i, r in enumerate(reviews):
            if r["body"].strip():
                scan("comment", f"review-{r['number']}-{i}", r["body"], r["url"], r["author"], r["at_ms"], None)

        refs = [r for r in refs if not (r["kind"] == "pr" and {"pr": r["key"], "issue": r["number"]} in fixes)]
        self._batched(Q.WRITE_FIXES, fixes)
        for kind, label, key in (("issue", "Issue", "number"), ("pr", "PullRequest", "number"),
                                 ("comment", "Comment", "id")):
            self._batched(Q.write_references(label, key), [r for r in refs if r["kind"] == kind])
            self._batched(Q.write_mentions(label, key), [m for m in mentions if m["kind"] == kind])
            self._batched(Q.write_decisions(label, key), [d for d in decisions if d["kind"] == kind])
        self._batched(Q.WRITE_DECISION_ABOUT, about)
        return {"fixes_links": len(fixes), "references": len(refs), "function_mentions": len(mentions),
                "decisions": len(decisions)}

    def _link_commits(self, data: Dict[str, Any], local_commits: Dict[str, List[str]]) -> int:
        """PR -INCLUDES-> Commit, by merge commit sha (squash and rebase merges), the commits a
        merge commit brought in, or commit subjects listed in a knowledge file."""
        rows = []
        for pr in data.get("pulls", []):
            sha = pr.get("merge_commit_sha")
            for s in ([sha] if sha else []) + local_commits.get(sha or "", []):
                rows.append({"number": pr["number"], "sha": s, "subject": None})
            for ref in pr.get("commits", []):
                rows.append({"number": pr["number"], "sha": ref if re.fullmatch(r"[0-9a-f]{7,40}", ref) else None,
                             "subject": ref})
        if not rows:
            return 0
        res = 0
        for i in range(0, len(rows), BATCH):
            out = self.db.query(self.graph, Q.WRITE_PR_COMMITS, {"rows": rows[i:i + BATCH]})
            res += out[0]["links"] if out else 0
        return res

    def _write_owners(self, rules: List[Tuple[str, List[str]]]) -> int:
        if not rules:
            return 0
        paths = [r["path"] for r in self.db.ro(self.graph, "MATCH (f:File) RETURN f.path AS path", {})]
        owned = file_owners(rules, paths)
        team_rows, person_rows = [], []
        for path, (pattern, owners) in owned.items():
            for o in owners:
                if o.startswith("@") and "/" in o:
                    team_rows.append({"path": path, "team": o[1:].split("/", 1)[1], "pattern": pattern})
                elif o.startswith("@"):
                    person_rows.append({"path": path, "login": o[1:], "pattern": pattern})
                else:
                    person_rows.append({"path": path, "email": o.lower(), "pattern": pattern})
        self._batched(Q.WRITE_TEAM_OWNS, team_rows)
        self._batched(Q.WRITE_PERSON_OWNS, [r for r in person_rows if "login" in r])
        self._batched(Q.WRITE_EMAIL_OWNS, [r for r in person_rows if "email" in r])
        return len(owned)


def ingest_knowledge(graph_name: str, repo_path: Optional[str], url: str, token: Optional[str],
                     local_commits: Optional[Dict[str, List[str]]] = None,
                     since: Optional[str] = None) -> Dict[str, int]:
    """Fetch and write. Never raises: missing knowledge must not fail a code ingestion."""
    try:
        data = fetch_knowledge(repo_path, url, token, since)
    except Exception as e:
        logger.warning("Knowledge fetch failed", url=url, error=str(e))
        return {"knowledge_error": 1}
    try:
        writer = KnowledgeWriter(graph_name)
        stats = writer.write(data, repo_path, local_commits)
        get_falkordb().query(graph_name, Q.MARK_KNOWLEDGE_SYNC,
                             {"source": data["source"], "at": datetime.now(timezone.utc).isoformat()})
        return stats
    except Exception as e:
        logger.warning("Knowledge write failed", error=str(e))
        return {"knowledge_error": 1}
