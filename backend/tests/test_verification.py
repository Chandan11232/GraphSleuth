"""Fix verification: test selection from the graph, the sandbox, judging, memory and the PR draft."""
import subprocess
import uuid

import pytest

from tests.conftest import requires_falkordb

TRACEBACK = '''Refunding an order that does not exist crashes with a 500.

Traceback (most recent call last):
  File "shop/api/handlers.py", line 14, in handle_refund_request
  File "shop/payments/refunds.py", line 17, in process_refund
  File "shop/payments/refunds.py", line 9, in validate_refund_amount
TypeError: 'NoneType' object is not subscriptable'''

# The real fix: restore get_order. It needs an import the function alone cannot add.
FIXED = '''def process_refund(order_id, amount):
    """Refund part or all of an order and notify the customer."""
    order = get_order(order_id)
    validate_refund_amount(order, amount)
    receipt = refund_card(order["customer"], amount)
    mark_refunded(order, amount)
    if order["status"] == "refunded":
        restock_items(order["items"])
    notify_refund(order, amount)
    return {"ok": True, "receipt": receipt}'''

# Avoids the crash but reads a field orders do not have: the existing refund test catches it.
BREAKS = FIXED.replace('refund_card(order["customer"], amount)', 'refund_card(order["email"], amount)')

REGRESSION_TEST = '''import pytest
from shop.orders import OrderNotFound
from shop.payments.refunds import process_refund


def test_refund_of_unknown_order_raises_order_not_found():
    with pytest.raises(OrderNotFound):
        process_refund("does-not-exist", 5.0)
'''

WEAK_TEST = '''from shop.orders import create_order
from shop.payments.refunds import process_refund


def test_partial_refund():
    create_order("o-77", "c-1", [("sku-1", 1)], 10.0)
    assert process_refund("o-77", 4.0)["ok"]
'''


# ------------------------------------------------------------------ unit

def test_undefined_names_finds_missing_imports():
    from app.services.sandbox import undefined_names

    src = "from a import b\n\nX = 1\n\ndef f(y, *args, **kw):\n    z = b(y) + X\n    for i in range(3):\n" \
          "        z += helper(i)\n    try:\n        pass\n    except Oops as e:\n        raise e\n    return len(args)\n"
    assert sorted(undefined_names(src)) == ["Oops", "helper"]


def test_add_import_extends_or_inserts(tmp_path):
    from app.services.sandbox import Workspace, add_import, module_for

    assert module_for("shop/orders.py") == "shop.orders"
    assert module_for("src/pkg/__init__.py") == "pkg"
    (tmp_path / "m.py").write_text('"""doc"""\nfrom shop.orders import find_order  # lookups\nimport os\n\nx = 1\n')
    ws = Workspace(root=str(tmp_path), base=str(tmp_path))
    add_import(ws, "m.py", "shop.orders", "get_order")
    add_import(ws, "m.py", "shop.db", "fetch_product")
    assert ws.read("m.py") == ('"""doc"""\nfrom shop.orders import find_order, get_order  # lookups\nimport os\n'
                               "from shop.db import fetch_product\n\nx = 1\n")


def test_splice_function_finds_moved_code(tmp_path):
    from app.services.sandbox import SandboxError, Workspace, splice_function

    (tmp_path / "f.py").write_text("import os\n\n\ndef g():\n    return 1\n")
    ws = Workspace(root=str(tmp_path), base=str(tmp_path))
    # The graph says line 1, but the function has moved to line 4.
    diff = splice_function(ws, "f.py", "def g():\n    return 1", "def g():\n    return 2", 1)
    assert ws.read("f.py") == "import os\n\n\ndef g():\n    return 2\n"
    assert "-    return 1" in diff and "+    return 2" in diff
    with pytest.raises(SandboxError):
        splice_function(ws, "f.py", "def h():\n    pass", "def h():\n    return 0", 1)
    with pytest.raises(SandboxError):
        ws.path("../outside.py")


def test_verification_policy(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services.sandbox import verification_allowed

    monkeypatch.setattr(settings, "VERIFY_FIXES", "local")
    assert verification_allowed(str(tmp_path)) is None
    assert "VERIFY_FIXES=local" in verification_allowed("https://github.com/a/b")
    monkeypatch.setattr(settings, "VERIFY_FIXES", "all")
    assert verification_allowed("https://github.com/a/b") is None
    monkeypatch.setattr(settings, "VERIFY_FIXES", "off")
    assert "off" in verification_allowed(str(tmp_path))


def test_github_repo_slug():
    from app.services.pull_request import github_repo

    assert github_repo("https://github.com/acme/shop.git") == "acme/shop"
    assert github_repo("https://github.com/acme/shop") == "acme/shop"
    assert github_repo("/demo/acme-shop") is None


# ---------------------------------------------------------- integration

@pytest.fixture(scope="module")
def investigated(demo_repo):
    if requires_falkordb.args[0]:  # the skipif condition: FalkorDB is not reachable
        pytest.skip("FalkorDB is not reachable")
    import asyncio

    from app.core.falkordb import get_falkordb
    from app.services.agent import Investigation, new_id
    from app.services.code_graph import ingest_source

    name = f"repo_test_{uuid.uuid4().hex[:8]}"
    ingest_source(demo_repo, name, "test-repo", "acme-shop")

    async def run():
        inv = Investigation(name, "test-repo", new_id(), new_id(), "Refund of unknown order crashes", TRACEBACK,
                            use_llm=False)
        async for event in inv.run():
            if event["type"] == "result":
                return event["result"]

    result = asyncio.run(run())
    yield {"graph_name": name, "url": demo_repo, "branch": ""}, result
    get_falkordb().delete_graph(name)


async def _verify(repo, inv, patched, test):
    from app.services.verifier import Verification

    ver = Verification(repo, inv, patched_code=patched, test_code=test)
    errors, result = [], None
    async for event in ver.run():
        if event["type"] == "error":
            errors.append(event["message"])
        elif event["type"] == "result":
            result = event["result"]
    assert not errors, errors
    return result


@requires_falkordb
def test_graph_selects_only_tests_that_reach_the_root_cause(investigated):
    from app.services.verifier import impacted_tests

    repo, inv = investigated
    impact = impacted_tests(repo["graph_name"], inv["verdict"]["id"])
    assert impact["total"] == 4
    assert [t["name"] for t in impact["tests"]] == ["test_full_refund_marks_order_refunded"]


@requires_falkordb
async def test_verified_fix_is_saved_and_confirms_the_root_cause(investigated):
    from app.core.falkordb import get_falkordb

    repo, inv = investigated
    result = await _verify(repo, inv, FIXED, REGRESSION_TEST)
    assert result["status"] == "verified", result["headline"]
    rows = {r["name"]: r for r in result["comparison"]}
    assert rows["test_refund_of_unknown_order_raises_order_not_found"]["before"] == "failed"
    assert rows["test_refund_of_unknown_order_raises_order_not_found"]["after"] == "passed"
    assert rows["test_full_refund_marks_order_refunded"] == {**rows["test_full_refund_marks_order_refunded"],
                                                             "before": "passed", "after": "passed"}
    # The graph knew where get_order lives, so the missing import was added.
    assert "+from shop.orders import find_order, mark_refunded, get_order" in result["diff"]

    db = get_falkordb()
    saved = db.ro(repo["graph_name"], """
        MATCH (i:Investigation {id: $id})-[:PROPOSED]->(x:Fix {id: $fix})-[:PATCHES]->(f:Function)
        MATCH (x)-[v:VERIFIED_BY]->(r:TestRun)-[ran:RAN]->(t:Test)
        RETURN f.name AS fn, v.phase AS phase, ran.outcome AS outcome, t.name AS test ORDER BY phase
    """, {"id": inv["id"], "fix": result["id"]})
    assert [(r["fn"], r["phase"], r["outcome"]) for r in saved] == [
        ("process_refund", "original", "passed"), ("process_refund", "patched", "passed")]
    confirmed = db.ro(repo["graph_name"], "MATCH (:Investigation {id: $id})-[c:CONFIRMED]->(f:Function) "
                                          "RETURN f.name AS name, c.source AS source", {"id": inv["id"]})
    assert confirmed == [{"name": "process_refund", "source": "verification"}]

    draft = result["pr_draft"]
    assert "Fixes #13" in draft["body"]  # the open issue about this bug, from the tracker
    assert "always loads orders with `get_order`" in draft["body"]  # the decision the fix restores
    assert {r["login"] for r in draft["reviewers"]} >= {"dana-kim", "priya-raman"}


@requires_falkordb
async def test_fix_that_breaks_an_existing_test_is_caught(investigated):
    repo, inv = investigated
    result = await _verify(repo, inv, BREAKS, REGRESSION_TEST)
    assert result["status"] == "regresses"
    assert "test_full_refund_marks_order_refunded" in result["headline"]


@requires_falkordb
async def test_regression_test_that_passes_before_the_fix_proves_nothing(investigated):
    repo, inv = investigated
    result = await _verify(repo, inv, FIXED, WEAK_TEST)
    assert result["status"] == "not_reproduced"


@requires_falkordb
async def test_patch_downloads_and_applies(investigated, tmp_path):
    from app.services.pull_request import format_patch

    repo, inv = investigated
    result = await _verify(repo, inv, FIXED, REGRESSION_TEST)
    target = tmp_path / "copy"
    subprocess.run(["cp", "-R", repo["url"], str(target)], check=True)
    patch = tmp_path / "fix.patch"
    patch.write_text(format_patch(result))
    applied = subprocess.run(["git", "apply", "--check", str(patch)], cwd=target, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
