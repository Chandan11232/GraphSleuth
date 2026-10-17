from app.services.code_graph import CallResolver
from app.services.parsing import body_tokens_for, is_test_file, parse_file, split_identifier, tokens_for

PY = '''
from shop.orders import load_order

class PaymentService:
    def process_refund(self, order_id):
        """Refund an order."""
        order = load_order(order_id)
        if order and order.paid:
            return self._issue(order)

    def _issue(self, order):
        return gateway.refund(order.id)
'''


def test_python_functions_classes_and_calls():
    pf = parse_file("shop/payments.py", PY)
    assert [c.name for c in pf.classes] == ["PaymentService"]
    fns = {f.qualified_name: f for f in pf.functions}
    assert set(fns) == {"PaymentService.process_refund", "PaymentService._issue"}
    refund = fns["PaymentService.process_refund"]
    assert refund.doc == "Refund an order."
    assert refund.complexity >= 2
    assert {(c.receiver, c.name) for c in refund.calls} == {(None, "load_order"), ("self", "_issue")}
    assert pf.imports == ["shop.orders"]


def test_calls_are_attributed_to_their_own_function():
    pf = parse_file("a.py", "def a():\n    b()\n\ndef b():\n    c()\n\ndef c():\n    pass\n")
    calls = {f.name: [c.name for c in f.calls] for f in pf.functions}
    assert calls == {"a": ["b"], "b": ["c"], "c": []}


def test_typescript_arrow_functions_and_methods():
    src = "export const price = (i: Item) => tax(i)\nclass Cart { add(i: Item) { return price(i) } }\n"
    pf = parse_file("src/cart.ts", src)
    assert {f.qualified_name for f in pf.functions} == {"price", "Cart.add"}


def test_js_test_cases_become_tests():
    src = "describe('cart', () => { it('sums', () => { expect(cartTotal([])).toBe(0) }) })"
    pf = parse_file("src/cart.test.js", src)
    assert [(f.name, f.is_test) for f in pf.functions] == [("cart > sums", True)]
    assert "cartTotal" in [c.name for c in pf.functions[0].calls]


def test_go_and_java():
    go = parse_file("main.go", "package main\ntype Svc struct{}\nfunc (s *Svc) Run() error { return helper() }\nfunc helper() error { return nil }\n")
    assert {f.qualified_name for f in go.functions} == {"Svc.Run", "helper"}
    java = parse_file("src/main/java/A.java", "class A { int f() { return g(); } int g() { return 1; } }")
    assert {f.qualified_name for f in java.functions} == {"A.f", "A.g"}


def test_test_file_detection():
    assert is_test_file("tests/test_refunds.py")
    assert is_test_file("src/cart.spec.ts")
    assert is_test_file("pkg/server_test.go")
    assert not is_test_file("shop/payments/refunds.py")


def test_identifier_tokens():
    assert split_identifier("processRefund") == ["process", "refund"]
    assert split_identifier("HTTPServer_v2") == ["http", "server", "v", "2"]
    fn = parse_file("shop/refunds.py", "def processRefund():\n    pass\n").functions[0]
    assert "refund" in tokens_for(fn).split()


def test_call_resolution_prefers_imports_and_self():
    orders = parse_file("shop/orders.py", "def get_order(i):\n    return i\n")
    other = parse_file("legacy/orders.py", "def get_order(i):\n    return None\n")
    refunds = parse_file("shop/refunds.py", "from shop.orders import get_order\n\ndef refund(i):\n    return get_order(i)\n")
    resolver = CallResolver([orders, other, refunds])
    edges = resolver.resolve_calls(refunds.functions)
    assert len(edges) == 1
    assert edges[0]["dst"] == orders.functions[0].id
    assert edges[0]["resolution"] == "imported"


def test_python_overload_stubs_are_skipped():
    src = "from typing import overload\n\nclass S:\n    @overload\n    def f(self, x: int) -> int: ...\n    @overload\n    def f(self, x: str) -> str: ...\n    def f(self, x):\n        return x\n"
    pf = parse_file("s.py", src)
    assert [(f.qualified_name, f.start_line) for f in pf.functions] == [("S.f", 8)]


def test_anonymous_js_callbacks_become_functions():
    src = '''window.addEventListener("load", () => {
  const form = document.querySelector("#f");
  form.addEventListener("submit", (e) => {
    list_el.appendChild(task_el);
    btn.onclick = function () { remove(task_el) };
  });
});'''
    pf = parse_file("main.js", src)
    names = [f.qualified_name for f in pf.functions]
    assert names == ["window.addEventListener('load')", "form.addEventListener('submit')", "btn.onclick"]
    load, submit, click = pf.functions
    assert load.registers == [submit] and submit.registers == [click]
    assert "appendChild" in [c.name for c in submit.calls]
    assert "remove" not in [c.name for c in submit.calls]  # belongs to the onclick handler
    edges = CallResolver([pf]).resolve_calls(pf.functions)
    assert {(e["src"], e["dst"], e["resolution"]) for e in edges} >= {
        (load.id, submit.id, "callback"), (submit.id, click.id, "callback")}
    assert "append" in body_tokens_for(submit).split()


def test_event_api_words_are_not_tokens():
    src = 'form.addEventListener("submit", (e) => { list.classList.add("task"); btn.addEventListener("click", go) })'
    fn = parse_file("main.js", src).functions[0]
    assert fn.qualified_name == "form.addEventListener('submit')"
    assert "add" not in tokens_for(fn).split()[:-1] and "listener" not in tokens_for(fn).split()[:-1]
    assert "add" in body_tokens_for(fn).split()  # classList.add is real work
    assert "listener" not in body_tokens_for(fn).split()


def test_module_code_keeps_what_is_outside_functions():
    pf = parse_file("shop/payments.py", PY)
    assert pf.module_code.startswith("from shop.orders import load_order")
    assert "class PaymentService:" in pf.module_code
    assert "load_order(order_id)" not in pf.module_code
