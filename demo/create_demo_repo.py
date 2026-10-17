#!/usr/bin/env python3
"""Creates `demo/acme-shop`: a small synthetic shop backend with real git history.

The history tells a story the agent can uncover from the graph:
  * the payments/orders code was written and tested months ago,
  * a recent refactor of `process_refund` by a different author switched it to
    `find_order`, which returns None for unknown orders, and dropped the guard,
  * the refund tests only cover the happy path.

It also writes the company knowledge around the code: a CODEOWNERS file (committed)
and `.cd-knowledge.json`, an export of the team's tracker with issues, pull requests,
reviews and comments. In it, the review of the original refund PR records a decision
("refund code always loads orders with get_order"), and the later refactor reverses
it with an approval from someone outside the payments team.

Usage:  python demo/create_demo_repo.py [target_dir]
All names, emails and data are fictional.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "acme-shop"

PEOPLE = {
    "priya": ("Priya Raman", "priya@acme-shop.example"),
    "marco": ("Marco Silva", "marco@acme-shop.example"),
    "dana": ("Dana Kim", "dana@acme-shop.example"),
    "lee": ("Lee Okafor", "lee@acme-shop.example"),
}

CODEOWNERS = """
# Default owner for everything not listed below
*                       @priya-raman

/shop/payments/         @acme-shop/payments
/shop/orders.py         @acme-shop/payments
/shop/pricing.py        @acme-shop/storefront
/shop/checkout.py       @acme-shop/storefront
/shop/api/              @acme-shop/storefront
/shop/notifications.py  @lee-okafor
"""

FILES_V1 = {
    "shop/__init__.py": "",
    "shop/db.py": '''
"""Tiny in-memory store standing in for a real database."""

_ORDERS = {}
_PRODUCTS = {
    "sku-1": {"name": "Coffee beans", "price": 14.0, "stock": 40},
    "sku-2": {"name": "Grinder", "price": 89.0, "stock": 5},
}


def fetch_order_row(order_id):
    return _ORDERS.get(order_id)


def save_order_row(order):
    _ORDERS[order["id"]] = order
    return order


def fetch_product(sku):
    return _PRODUCTS.get(sku)


def update_stock(sku, delta):
    _PRODUCTS[sku]["stock"] += delta
''',
    "shop/orders.py": '''
"""Order lookup and lifecycle."""
from shop.db import fetch_order_row, save_order_row


class OrderNotFound(Exception):
    pass


def get_order(order_id):
    """Load an order or raise OrderNotFound."""
    order = fetch_order_row(order_id)
    if order is None:
        raise OrderNotFound(order_id)
    return order


def find_order(order_id):
    """Load an order, returning None when it does not exist."""
    return fetch_order_row(order_id)


def create_order(order_id, customer, items, total):
    order = {"id": order_id, "customer": customer, "items": items, "total": total,
             "status": "paid", "refunded": 0.0}
    return save_order_row(order)


def mark_refunded(order, amount):
    order["refunded"] += amount
    if order["refunded"] >= order["total"]:
        order["status"] = "refunded"
    return save_order_row(order)
''',
    "shop/pricing.py": '''
"""Price calculation for carts."""
from shop.db import fetch_product

TAX_RATE = 0.08


def line_total(sku, quantity):
    product = fetch_product(sku)
    return product["price"] * quantity


def apply_discount(subtotal, code):
    if code == "WELCOME10":
        return subtotal * 0.9
    if code == "BULK" and subtotal > 200:
        return subtotal - 25
    return subtotal


def cart_total(items, discount_code=None):
    subtotal = sum(line_total(sku, qty) for sku, qty in items)
    discounted = apply_discount(subtotal, discount_code)
    return round(discounted * (1 + TAX_RATE), 2)
''',
    "shop/inventory.py": '''
"""Stock reservations."""
from shop.db import fetch_product, update_stock


class OutOfStock(Exception):
    pass


def reserve_items(items):
    for sku, qty in items:
        product = fetch_product(sku)
        if product["stock"] < qty:
            raise OutOfStock(sku)
    for sku, qty in items:
        update_stock(sku, -qty)


def restock_items(items):
    for sku, qty in items:
        update_stock(sku, qty)
''',
    "shop/payments/__init__.py": "",
    "shop/payments/gateway.py": '''
"""Fake payment gateway client."""


class GatewayError(Exception):
    pass


def charge_card(customer, amount):
    if amount <= 0:
        raise GatewayError("amount must be positive")
    return {"customer": customer, "amount": amount, "status": "captured"}


def refund_card(customer, amount):
    if amount <= 0:
        raise GatewayError("refund must be positive")
    return {"customer": customer, "amount": -amount, "status": "refunded"}
''',
    "shop/payments/refunds.py": '''
"""Refund processing."""
from shop.orders import get_order, mark_refunded, OrderNotFound
from shop.payments.gateway import refund_card
from shop.inventory import restock_items


def validate_refund_amount(order, amount):
    remaining = order["total"] - order["refunded"]
    if amount > remaining:
        raise ValueError(f"refund {amount} exceeds remaining {remaining}")


def process_refund(order_id, amount):
    """Refund part or all of an order."""
    try:
        order = get_order(order_id)
    except OrderNotFound:
        return {"ok": False, "error": "order not found"}
    validate_refund_amount(order, amount)
    receipt = refund_card(order["customer"], amount)
    mark_refunded(order, amount)
    if order["status"] == "refunded":
        restock_items(order["items"])
    return {"ok": True, "receipt": receipt}
''',
    "shop/checkout.py": '''
"""Checkout flow: price, reserve stock, charge, create order."""
from shop.pricing import cart_total
from shop.inventory import reserve_items
from shop.payments.gateway import charge_card
from shop.orders import create_order


def checkout(order_id, customer, items, discount_code=None):
    total = cart_total(items, discount_code)
    reserve_items(items)
    charge_card(customer, total)
    return create_order(order_id, customer, items, total)
''',
    "shop/api/__init__.py": "",
    "shop/api/handlers.py": '''
"""HTTP-ish request handlers."""
from shop.checkout import checkout
from shop.payments.refunds import process_refund
from shop.orders import get_order


def handle_checkout(request):
    order = checkout(request["order_id"], request["customer"], request["items"],
                     request.get("discount_code"))
    return {"status": 201, "order": order}


def handle_refund_request(request):
    result = process_refund(request["order_id"], float(request["amount"]))
    return {"status": 200 if result["ok"] else 404, "body": result}


def handle_order_status(request):
    order = get_order(request["order_id"])
    return {"status": 200, "body": {"status": order["status"]}}
''',
    "tests/__init__.py": "",
    "tests/test_pricing.py": '''
from shop.pricing import cart_total, apply_discount


def test_cart_total_applies_tax():
    assert cart_total([("sku-1", 1)]) == 15.12


def test_welcome_discount():
    assert apply_discount(100, "WELCOME10") == 90
''',
    "tests/test_checkout.py": '''
from shop.api.handlers import handle_checkout


def test_checkout_creates_order():
    response = handle_checkout({"order_id": "o-1", "customer": "c-1", "items": [("sku-1", 2)]})
    assert response["status"] == 201
''',
    "tests/test_refunds.py": '''
from shop.orders import create_order
from shop.payments.refunds import process_refund


def test_full_refund_marks_order_refunded():
    create_order("o-9", "c-9", [("sku-1", 1)], 15.12)
    result = process_refund("o-9", 15.12)
    assert result["ok"]
''',
}

# Notification feature added later by Lee.
NOTIFY = '''
"""Customer notifications."""


def send_email(to, subject, body):
    return {"to": to, "subject": subject, "body": body}


def notify_refund(order, amount):
    return send_email(order["customer"], "Refund issued", f"We refunded {amount:.2f}")
'''

# The regression: Dana's refactor switches to find_order() and drops the guard.
REFUNDS_V2 = '''
"""Refund processing."""
from shop.orders import find_order, mark_refunded
from shop.payments.gateway import refund_card
from shop.inventory import restock_items
from shop.notifications import notify_refund


def validate_refund_amount(order, amount):
    remaining = order["total"] - order["refunded"]
    if amount > remaining:
        raise ValueError(f"refund {amount} exceeds remaining {remaining}")


def process_refund(order_id, amount):
    """Refund part or all of an order and notify the customer."""
    order = find_order(order_id)
    validate_refund_amount(order, amount)
    receipt = refund_card(order["customer"], amount)
    mark_refunded(order, amount)
    if order["status"] == "refunded":
        restock_items(order["items"])
    notify_refund(order, amount)
    return {"ok": True, "receipt": receipt}
'''


def git(*args, author=None, date=None):
    env = dict(os.environ)
    if author:
        name, email = PEOPLE[author]
        env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                   GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-C", str(ROOT), *args], check=True, env=env, capture_output=True)


def write(rel, content):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.lstrip("\n"))


def commit(message, author, date, files):
    for rel, content in files.items():
        write(rel, content)
    git("add", "-A")
    git("commit", "-q", "-m", message, author=author, date=date)


# ------------------------------------------------------------------ tracker export

LOGINS = {"priya": "priya-raman", "marco": "marco-silva", "dana": "dana-kim", "lee": "lee-okafor"}
URL = "https://tracker.acme-shop.example/acme-shop/shop"


def pr(number, title, author, merged_at, commit_subject, body, reviews):
    return {"number": number, "title": title, "author": LOGINS[author], "state": "closed",
            "url": f"{URL}/pull/{number}", "created_at": merged_at.replace("T18", "T09"), "merged_at": merged_at,
            "body": body, "commits": [commit_subject],
            "reviews": [{"author": LOGINS[a], "state": st, "body": b, "submitted_at": merged_at.replace("T18", "T16"),
                         "url": f"{URL}/pull/{number}#review-{i}"} for i, (a, st, b) in enumerate(reviews)]}


def issue(number, title, author, created_at, body, state="closed", closed_at=None, labels=()):
    return {"number": number, "title": title, "author": author, "state": state, "url": f"{URL}/issues/{number}",
            "created_at": created_at, "closed_at": closed_at, "body": body, "labels": list(labels)}


def comment(cid, on, author, created_at, body, path=None):
    return {"id": cid, "on": on, "author": LOGINS.get(author, author), "created_at": created_at, "body": body,
            "url": f"{URL}/issues/{on}#comment-{cid}", "path": path}


KNOWLEDGE = {
    "people": [{"login": LOGINS[k], "name": n, "email": e} for k, (n, e) in PEOPLE.items()]
    + [{"login": "sam-patel", "name": "Sam Patel", "email": "sam@acme-shop.example"}],
    "teams": [
        {"name": "payments", "members": ["priya-raman", "dana-kim"]},
        {"name": "storefront", "members": ["marco-silva"]},
        {"name": "platform", "members": ["lee-okafor"]},
        {"name": "support", "members": ["sam-patel"]},
    ],
    "pulls": [
        pr(1, "Initial store and order model", "priya", "2026-06-02T18:00:00Z", "Initial store and order model",
           "In-memory store and the order model (`get_order`, `find_order`, `create_order`).",
           [("marco", "APPROVED", "")]),
        pr(2, "Add pricing with tax and discounts", "marco", "2026-06-10T18:00:00Z", "Add pricing with tax and discounts",
           "Cart totals with 8% tax and the WELCOME10 code.", [("priya", "APPROVED", "")]),
        pr(3, "Add inventory reservations", "marco", "2026-06-18T18:00:00Z", "Add inventory reservations",
           "Reserve stock before charging; `restock_items` for returns.", [("priya", "APPROVED", "")]),
        pr(5, "Payment gateway client", "priya", "2026-07-01T18:00:00Z", "Payment gateway client",
           "Fake gateway with `charge_card` and `refund_card`.", [("dana", "APPROVED", "")]),
        pr(6, "Refund processing with order validation", "priya", "2026-07-15T18:00:00Z",
           "Refund processing with order validation",
           "Fixes #4. Adds `process_refund` for full and partial refunds. Unknown orders: `get_order` raises "
           "OrderNotFound, which `process_refund` turns into ok=False and the handler into a 404.",
           [("dana", "APPROVED", "Looks good.")]),
        pr(7, "Checkout flow and API handlers", "marco", "2026-08-03T18:00:00Z", "Checkout flow and API handlers",
           "Checkout plus the request handlers for checkout, refunds and order status.", [("priya", "APPROVED", "")]),
        pr(9, "Bulk discount code", "marco", "2026-09-12T18:00:00Z", "Bulk discount code",
           "Closes #8. Adds the BULK code to `apply_discount`.", [("priya", "APPROVED", "")]),
        pr(11, "Customer refund notifications", "lee", "2026-10-02T18:00:00Z", "Customer refund notifications",
           "Part of #10: adds `notify_refund` and `send_email`. Wiring it into the refund flow is a follow-up for "
           "the payments team.", [("dana", "APPROVED", "")]),
        pr(12, "Refactor refund flow: notify customer, simplify order lookup", "dana", "2026-10-09T18:00:00Z",
           "Refactor refund flow: notify customer, simplify order lookup",
           "Fixes #10. Calls `notify_refund` after every refund, and simplifies the order lookup in "
           "`process_refund` by switching to `find_order` (no try/except needed).",
           [("lee", "APPROVED", "LGTM, notifications part looks right.")]),
    ],
    "issues": [
        issue(4, "Support refunds (full and partial)", "sam-patel", "2026-06-28T09:00:00Z",
              "Support needs to refund orders from the admin tool, fully or partially. Refunding an order id that "
              "doesn't exist must return a clear 404, not a 500.", closed_at="2026-07-15T18:00:00Z",
              labels=["payments"]),
        issue(8, "Bulk discount for large orders", "sam-patel", "2026-08-20T09:00:00Z",
              "Wholesale customers keep asking for a discount on big orders.", closed_at="2026-09-12T18:00:00Z",
              labels=["pricing"]),
        issue(10, "Email customers when a refund is issued", "sam-patel", "2026-09-20T09:00:00Z",
              "Customers write in asking whether their refund went through. Send them an email.",
              closed_at="2026-10-09T18:00:00Z", labels=["payments", "notifications"]),
        issue(13, "Refunding a deleted order returns 500", "sam-patel", "2026-10-12T09:00:00Z",
              "Support tried to refund order o-4471 after the order was removed. The admin tool shows a 500. "
              "Traceback ends in `validate_refund_amount`: TypeError: 'NoneType' object is not subscriptable.",
              state="open", labels=["payments", "bug"]),
    ],
    "comments": [
        comment("c1", 6, "dana", "2026-07-15T12:10:00Z",
                "Should we use `find_order` here instead of try/except around `get_order`? It's shorter.",
                path="shop/payments/refunds.py"),
        comment("c2", 6, "priya", "2026-07-15T12:40:00Z",
                "No: `find_order` returns None and every caller then has to remember the check. Decision: refund "
                "code always loads orders with `get_order`, so a missing order is an explicit OrderNotFound and "
                "never None.", path="shop/payments/refunds.py"),
        comment("c3", 6, "dana", "2026-07-15T13:05:00Z", "Makes sense, agreed."),
        comment("c4", 8, "marco", "2026-08-25T10:00:00Z",
                "We agreed to make BULK a flat $25 off orders over $200 rather than a percentage, so margins stay "
                "predictable."),
        comment("c5", 8, "priya", "2026-08-25T11:00:00Z", "Fine by me, a flat amount is easier to explain."),
        comment("c6", 10, "lee", "2026-09-22T10:00:00Z",
                "I'll add the email helpers; the payments team should call them from the refund flow."),
        comment("c7", 13, "lee", "2026-10-12T11:30:00Z",
                "Maybe related to #12? It touched `process_refund` last week."),
    ],
}


def write_knowledge():
    """The tracker export sits next to the code, like a CODEOWNERS file but not committed."""
    (ROOT / ".cd-knowledge.json").write_text(json.dumps(KNOWLEDGE, indent=1))


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    git("init", "-q", "-b", "main")

    def pick(*names):
        return {k: v for k, v in FILES_V1.items() if k in names}

    commit("Initial store and order model", "priya", "2026-06-02T10:00:00",
           {**pick("shop/__init__.py", "shop/db.py", "shop/orders.py"), ".github/CODEOWNERS": CODEOWNERS})
    commit("Add pricing with tax and discounts", "marco", "2026-06-10T14:20:00",
           {**pick("tests/__init__.py", "tests/test_pricing.py"),
            "shop/pricing.py": FILES_V1["shop/pricing.py"].replace(
                '    if code == "BULK" and subtotal > 200:\n        return subtotal - 25\n', '')})
    commit("Add inventory reservations", "marco", "2026-06-18T09:05:00", pick("shop/inventory.py"))
    commit("Payment gateway client", "priya", "2026-07-01T16:40:00",
           pick("shop/payments/__init__.py", "shop/payments/gateway.py"))
    commit("Refund processing with order validation", "priya", "2026-07-15T11:30:00",
           pick("shop/payments/refunds.py", "tests/test_refunds.py"))
    commit("Checkout flow and API handlers", "marco", "2026-08-03T13:00:00",
           pick("shop/checkout.py", "shop/api/__init__.py", "shop/api/handlers.py", "tests/test_checkout.py"))
    commit("Bulk discount code", "marco", "2026-09-12T10:10:00", {
        "shop/pricing.py": FILES_V1["shop/pricing.py"],
    })
    commit("Customer refund notifications", "lee", "2026-10-02T15:45:00",
           {"shop/notifications.py": NOTIFY})
    commit("Refactor refund flow: notify customer, simplify order lookup", "dana", "2026-10-09T17:25:00",
           {"shop/payments/refunds.py": REFUNDS_V2})
    write_knowledge()
    print(f"Demo repository created at {ROOT}")


if __name__ == "__main__":
    main()
