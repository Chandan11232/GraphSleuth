#!/usr/bin/env python3
"""Runs the whole loop against a running API and saves what it proved under evidence/.

    bug report -> investigation -> fix draft -> sandbox verification -> patch + PR description

Usage:
    python demo/create_demo_repo.py /tmp/acme-shop          # or use /demo/acme-shop in Docker
    python scripts/capture_evidence.py --api http://localhost:8000/api/v1 --repo-path /tmp/acme-shop

The corrected function and the regression test come from the fix draft when the server has a model. With
--manual (or when the server runs graph-only) the ones below are used, the way an MCP client such as Claude
Code supplies them through the verify_fix tool. The output says which.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

TITLE = "Refunding an unknown order crashes with a 500"
REPORT = '''Refunding an order that does not exist crashes with a 500.

Traceback (most recent call last):
  File "shop/api/handlers.py", line 14, in handle_refund_request
  File "shop/payments/refunds.py", line 17, in process_refund
  File "shop/payments/refunds.py", line 9, in validate_refund_amount
TypeError: 'NoneType' object is not subscriptable'''

MANUAL_FIX = '''def process_refund(order_id, amount):
    """Refund part or all of an order and notify the customer."""
    order = get_order(order_id)
    validate_refund_amount(order, amount)
    receipt = refund_card(order["customer"], amount)
    mark_refunded(order, amount)
    if order["status"] == "refunded":
        restock_items(order["items"])
    notify_refund(order, amount)
    return {"ok": True, "receipt": receipt}'''

MANUAL_TEST = '''import pytest

from shop.orders import OrderNotFound
from shop.payments.refunds import process_refund


def test_refund_of_unknown_order_raises_order_not_found():
    with pytest.raises(OrderNotFound):
        process_refund("does-not-exist", 5.0)
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000/api/v1")
    ap.add_argument("--repo-path", default="/demo/acme-shop")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "evidence"))
    ap.add_argument("--manual", action="store_true", help="use the fix and test in this script, not the model's")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    api = httpx.Client(base_url=args.api, timeout=600)

    health = api.get("/health").json()
    repo = api.post("/repos", json={"name": "acme-shop", "url": args.repo_path}).json()
    for _ in range(120):
        repo = api.get(f"/repos/{repo['id']}").json()
        if repo["status"] in ("ready", "error"):
            break
        time.sleep(1)
    if repo["status"] != "ready":
        print("ingestion failed:", repo.get("error_message"), file=sys.stderr)
        return 1
    print(f"ingested: {repo['stats']}")

    inv = api.post("/investigations", json={"repository_id": repo["id"], "title": TITLE, "description": REPORT,
                                            "use_llm": bool(health["llm"]["enabled"])}).json()
    for _ in range(600):
        result = api.get(f"/investigations/{inv['id']}").json()
        if result["status"] != "running":
            break
        time.sleep(1)
    print(f"verdict: {result['verdict']['name']} ({result['verdict']['confidence']:.0%}, {result['mode']})")

    draft = api.post(f"/investigations/{inv['id']}/fix/draft").json()
    use_model = not args.manual and draft["patched_code"] and draft["test_code"]
    patched, test = (draft["patched_code"], draft["test_code"]) if use_model else (MANUAL_FIX, MANUAL_TEST)
    source = f"model ({draft.get('test_model')})" if use_model else "supplied by the caller (as an MCP client would)"
    print(f"fix and test: {source}; graph selected {len(draft['impacted_tests'])}/{draft['total_tests']} tests")

    events, verification = [], None
    with api.stream("POST", f"/investigations/{inv['id']}/verify",
                    json={"patched_code": patched, "test_code": test, "test_path": draft["test_path"]}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            ev = json.loads(line[6:])
            events.append(ev)
            if ev["type"] == "step":
                print(f"  {ev['step']['action']}: {ev['step']['result_summary']}")
            elif ev["type"] == "error":
                print("  error:", ev["message"], file=sys.stderr)
            elif ev["type"] == "result":
                verification = ev["result"]
    if not verification:
        return 1
    print(f"status: {verification['status']} ({verification['headline']})")

    patch = api.get(f"/investigations/{inv['id']}/fixes/{verification['id']}/patch").text
    refused = api.post(f"/investigations/{inv['id']}/fixes/{verification['id']}/pull-request", json={"approve": False})
    final = api.get(f"/investigations/{inv['id']}").json()

    (out / "fix.patch").write_text(patch)
    (out / "pull-request.md").write_text(f"# {verification['pr_draft']['title']}\n\n{verification['pr_draft']['body']}\n")
    (out / "verification-run.json").write_text(json.dumps({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "api": args.api,
        "server": {"version": health.get("version"), "llm": health["llm"]},
        "repository": {"name": repo["name"], "stats": repo["stats"]},
        "investigation": {"id": inv["id"], "mode": result["mode"], "verdict": result["verdict"],
                          "steps": [f"{s['action']}: {s['result_summary']}" for s in result["steps"]]},
        "fix_source": source,
        "draft": {k: draft[k] for k in ("impacted_tests", "total_tests", "test_path", "cypher")},
        "verification": {k: verification[k] for k in ("id", "status", "headline", "comparison", "diff", "base_commit",
                                                      "duration_ms")},
        "verification_steps": [e["step"] for e in events if e["type"] == "step"],
        "human_gate": {"request": {"approve": False}, "status_code": refused.status_code,
                       "response": refused.json()},
        "memory_after": {"status": final["status"], "confirmed": final.get("confirmed")},
    }, indent=1, default=str))
    print(f"wrote {out}/verification-run.json, fix.patch, pull-request.md")
    return 0 if verification["status"] == "verified" else 2


if __name__ == "__main__":
    sys.exit(main())
