# Fix: Refunding an unknown order crashes with a 500

Fixes a bug found by Codebase Detective: **Refunding an unknown order crashes with a 500**

## Root cause
`process_refund` in `shop/payments/refunds.py`. process_refund in shop/payments/refunds.py scores highest (0.62). It is named in the report or sits on the stack trace. It was recently changed by Dana Kim ("Refactor refund flow: notify customer, simplify order lookup").

## Decision this restores
> Decision: refund code always loads orders with `get_order`, so a missing order is an explicit OrderNotFound and never None.

Priya Raman recorded this in PullRequest #6. It was later reversed by "Refactor refund flow: notify customer, simplify order lookup" (PR #12).

## Proof
**Verified: the regression test failed on the original code and passes with the fix, and no selected test broke.**

| Test | Original code | With this fix |
|---|---|---|
| `test_refund_of_unknown_order_raises_order_not_found` (new regression test) | FAIL | pass |
| `test_full_refund_marks_order_refunded` | pass | pass |

Tests were selected from the code graph: 1 of 4 tests reach `process_refund` through `TESTS` and `CALLS` edges.
Blast radius: 1 function(s) call into it (handle_refund_request).

## Suggested reviewers
- Dana Kim (@dana-kim): in @payments, which owns /shop/payments/; changed process_refund 1x
- Priya Raman (@priya-raman): in @payments, which owns /shop/payments/; changed process_refund 1x
- Lee Okafor (@lee-okafor): reviewed #12

Fixes #13

---
Investigation `b748255e-13c8-4dbe-8cf7-652ca83b6d31`. Every claim above comes from a node in the FalkorDB graph.
