"""Company brain: answers questions about how the codebase and the team around it
work, with every claim tied to a node in the graph.

1. Find what the question is about (functions and files named in it).
2. Run graph tools: `why_code` (history, pull requests, decisions, drift),
   `who_owns` (CODEOWNERS, authors, reviewers) and `search_knowledge` (full text
   over issues, pull requests, comments and decisions).
3. Build an answer from those results, citing each source as [n]. If an LLM is
   available it also writes a short summary from the same numbered sources. The
   graph answer stays the answer: a small model can misread the facts, so its
   summary is shown separately, to be checked against them.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.core.config import settings
from app.services.graph_tools import GraphTools, ToolResult
from app.services.knowledge import mentioned_functions
from app.services.llm import LLMUnavailable, get_llm

logger = structlog.get_logger()

WHO = re.compile(r"\b(who|whom|owner|owns|own|ask|review|reviewer|reviewers|expert|experts|knows|responsible|"
                 r"maintain\w*|contact)\b", re.I)
PATH = re.compile(r"[\w.\-]+(?:/[\w.\-]+)+|[\w\-]+\.(?:py|js|jsx|ts|tsx|go|java)\b")


def _when(ms: Optional[int]) -> str:
    if not ms:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class Sources:
    """Numbered citations, one per graph node."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []
        self.index: Dict[str, int] = {}

    def cite(self, key: str, **fields: Any) -> str:
        if key not in self.index:
            self.items.append({"n": len(self.items) + 1, "key": key, **fields})
            self.index[key] = len(self.items)
        return f"[{self.index[key]}]"


def _pr_ref(src: Sources, pr: Dict[str, Any], why: str) -> str:
    return src.cite(pr["id"], type="PullRequest", number=pr["number"], title=pr["title"], url=pr.get("url"),
                    author=pr.get("opened_by"), why=why)


def owning_people(owners: Optional[Dict[str, Any]]) -> Tuple[set, str]:
    if not owners:
        return set(), ""
    people = {m for t in owners["teams"] for m in t["members"]} | {o["name"] for o in owners["owners"]}
    label = ", ".join("@" + t["team"] for t in owners["teams"]) or ", ".join(o["name"] for o in owners["owners"])
    return people, label


def explain_history(data: Dict[str, Any], src: Sources, owners: Optional[Dict[str, Any]] = None,
                    full: bool = True) -> List[str]:
    """Sentences about one function: its last change and the review it got, decisions that govern it
    (and later changes that may break them), earlier changes, and open issues that mention it."""
    fn = data["function"]["name"]
    lines: List[str] = []
    changes = data["changes"] if full else []
    if changes:
        latest = changes[0]
        pr = latest["pull_request"]
        if pr:
            approved = [r["name"] for r in pr["reviews"] if r["state"] == "APPROVED"]
            line = (f"`{fn}` was last changed in PR #{pr['number']} \"{pr['title']}\" by "
                    f"{pr['opened_by'] or latest['author']} {_pr_ref(src, pr, f'last change to {fn}')}"
                    + (f", approved by {', '.join(approved)}" if approved else ", with no approving review"))
            if pr["fixes"]:
                line += ". It fixed " + ", ".join(
                    f"#{i['number']} \"{i['title']}\" " + src.cite(i["id"], type="Issue", number=i["number"],
                                                                   title=i["title"], url=i.get("url"),
                                                                   author=i.get("opened_by"),
                                                                   why=f"fixed by PR #{pr['number']}")
                    for i in pr["fixes"])
            lines.append(line + ".")
            team, label = owning_people(owners)
            author = pr["opened_by"] or latest["author"]
            if team and approved and not (set(approved) & (team - {author})) and team - {author}:
                lines.append(f"None of its approvers is in {label}, which owns `{owners['file']}`"
                             f" ({', '.join(sorted(team - {author}))} did not review it).")
        else:
            lines.append(f"`{fn}` was last changed by {latest['author']} in \"{latest['message']}\" "
                         f"({_when(latest['timestamp'])}); no pull request is linked to that commit.")
    for d in data["decisions"]:
        if d["id"] in src.index:
            continue
        cite = src.cite(d["id"], type="Decision", number=d["item_number"], title=d["item_title"], url=d.get("url"),
                        author=d.get("author"), text=d["text"], why=f"decision governing {fn}")
        line = (f"{d['author'] or 'Someone'} recorded a decision in #{d['item_number']} "
                f"\"{d['item_title']}\": \"{d['text']}\" {cite}.")
        if d["drift"]:
            later = d["drift"][0]
            who = later["author"] + (f", approved by {', '.join(later['reviewers'])}" if later["reviewers"] else "")
            line += (f" The code it governs ({', '.join(later['functions'])}) has changed since, in "
                     + (f"PR #{later['pr']} " if later["pr"] else "")
                     + f"\"{later['message']}\" ({who}), so check whether the decision still holds.")
        lines.append(line)
    older = [c for c in changes[1:] if c["pull_request"]]
    if older:
        lines.append("Earlier changes: " + "; ".join(
            f"PR #{c['pull_request']['number']} \"{c['pull_request']['title']}\" by {c['author']} "
            + _pr_ref(src, c["pull_request"], f"earlier change to {fn}") for c in older[:3]) + ".")
    for d in [d for d in data["discussions"] if d["state"] == "open"][:3] if full else []:
        lines.append(f"Open {('issue' if d['type'] == 'Issue' else 'pull request')} #{d['number']} \"{d['title']}\" "
                     f"mentions it " + src.cite(d["id"], type=d["type"], number=d["number"], title=d["title"],
                                                url=d.get("url"), author=d.get("opened_by"),
                                                why=f"open item mentioning {fn}") + ".")
    return lines


def explain_owners(data: Dict[str, Any], src: Sources) -> List[str]:
    lines = []
    if data["teams"]:
        for t in data["teams"]:
            cite = src.cite(t["id"], type="Team", title=f"@{t['team']}", why=f"CODEOWNERS {t['pattern']}")
            lines.append(f"`{data['file']}` is owned by @{t['team']} ({', '.join(t['members']) or 'no members on record'}) "
                         f"through the CODEOWNERS rule `{t['pattern']}` {cite}.")
    elif data["owners"]:
        lines.append(f"`{data['file']}` is owned by " + ", ".join(o["name"] for o in data["owners"])
                     + f" (CODEOWNERS `{data['owners'][0]['pattern']}`).")
    else:
        lines.append(f"`{data['file']}` has no CODEOWNERS entry.")
    if data["ask"]:
        lines.append("Best people to ask: " + "; ".join(
            f"{p['name']} ({', '.join(p['reasons'][:3])})" for p in data["ask"][:3]) + ".")
    return lines


def explain_matches(rows: List[Dict[str, Any]], src: Sources, limit: int = 3) -> List[str]:
    lines = []
    for r in rows[:limit]:
        label = {"Decision": "Decision", "Comment": "Comment", "Issue": "Issue", "PullRequest": "PR"}[r["type"]]
        cite = src.cite(r["id"], type=r["type"], number=r["number"], title=r["title"], url=r.get("url"),
                        author=r.get("author"), text=r["text"][:300],
                        why="full-text match: " + ", ".join(r["matched_words"]))
        snippet = " ".join(r["text"].split())[:180]
        where = f"#{r['number']} \"{r['title']}\""
        if r["type"] in ("Decision", "Comment"):
            lines.append(f"{label} by {r['author'] or 'someone'} on {where}: \"{snippet}\" {cite}.")
        else:
            lines.append(f"{label} {where} by {r['author'] or 'someone'}: \"{snippet}\" {cite}.")
    return lines


class Brain:
    def __init__(self, graph_name: str):
        self.tools = GraphTools(graph_name)
        self.llm = get_llm()

    def subjects(self, question: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        names = {r["name"] for r in self.tools._ro("MATCH (f:Function) WHERE f.in_test_file = false "
                                                   "RETURN DISTINCT f.name AS name", {})}
        found = mentioned_functions(question, names)
        # Plain words that are function names too ("checkout"), when long enough to be deliberate.
        found |= {w for w in re.findall(r"[A-Za-z_]\w{4,}", question) if w in names}
        fns = [f for f in (self.tools.resolve(n) for n in sorted(found, key=lambda n: question.find(n))) if f]
        path = next(iter(PATH.findall(question)), None)
        return fns, path

    async def ask(self, question: str, use_llm: bool = True) -> Dict[str, Any]:
        fns, path = self.subjects(question)
        steps: List[ToolResult] = []
        src = Sources()
        lines: List[str] = []
        who = bool(WHO.search(question))

        primary = fns[0] if fns else None
        owners = None
        if path or primary:
            res = self.tools.who_owns(path or primary["id"])
            steps.append(res)
            owners = res.data
            if owners and who:
                lines += explain_owners(owners, src)
        for i, fn in enumerate(fns[:3]):
            res = self.tools.why_code(fn["id"])
            steps.append(res)
            if res.data:
                # The first function named is the subject; later ones only add their decisions.
                lines += explain_history(res.data, src, owners if i == 0 else None, full=i == 0 and not who)
        found = self.tools.search_knowledge(question)
        steps.append(found)
        cited = {s["key"] for s in src.items}
        # With graph facts in hand, only strong full-text matches are worth adding.
        extra = [r for r in found.data if r["id"] not in cited and r["score"] >= (0.45 if lines else 0.3)]
        if extra:
            related = explain_matches(extra, src, limit=3 if not lines else 2)
            lines += (["Related records:"] if lines else []) + related
        if not lines:
            lines.append("Nothing in the graph answers this yet. Name a function or file, or ask about a decision, "
                         "an issue or who owns some code.")

        answer = "\n".join(lines)
        mode, model, summary = "graph", None, None
        if use_llm and self.llm.enabled and src.items:
            model = await self.llm.pick_model(settings.OLLAMA_AGENT_MODEL)
            if model:
                summary = await self._summarize(model, question, answer, src)
                mode = "graph+llm" if summary else "graph"

        visited = set()
        for s in steps:
            visited |= s.visited
        return {
            "question": question,
            "answer": answer,
            "summary": summary,
            "mode": mode,
            "model": model,
            "subjects": {"functions": [{"id": f["id"], "name": f["name"], "file_path": f["file_path"]} for f in fns],
                         "path": path},
            "sources": src.items,
            "steps": [{"tool": s.tool, "args": s.args, "summary": s.summary, "query": "\n\n".join(s.cypher)}
                      for s in steps],
            "evidence_ids": sorted(v for v in visited if v),
        }

    async def _summarize(self, model: str, question: str, draft: str, src: Sources) -> Optional[str]:
        listing = "\n".join(
            f"[{s['n']}] {s['type']} #{s.get('number') or ''} \"{s.get('title') or ''}\" by {s.get('author') or '?'}"
            + (f": {s['text']}" if s.get("text") else "") for s in src.items)
        prompt = (f"Question: {question}\n\nFacts found in the company graph (each cites its source):\n{draft}\n\n"
                  f"Sources:\n{listing}\n\nAnswer the question in at most 5 sentences using only these facts. Keep "
                  "the [n] citations after each claim they support. If the facts do not answer the question, say "
                  "what is missing. No preamble.")
        try:
            reply = await self.llm.chat(model, [
                {"role": "system", "content": "You answer questions about a software team from its records. "
                                              "Never state anything without a [n] citation."},
                {"role": "user", "content": prompt}])
        except LLMUnavailable as e:
            logger.warning("Brain summary failed", error=str(e)[:200])
            return None
        text = reply["content"].strip()
        cites = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
        # Only keep a summary that cites real sources.
        if not cites or max(cites) > len(src.items):
            return None
        return text
