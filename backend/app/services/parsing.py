"""Source parsing with tree-sitter.

Turns one source file into functions, classes, tests, call sites and imports.
Call sites are resolved to graph edges later, once every file has been parsed.
"""
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Tuple

import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

LANGUAGES: Dict[str, Language] = {
    "python": Language(tree_sitter_python.language()),
    "javascript": Language(tree_sitter_javascript.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
    "go": Language(tree_sitter_go.language()),
    "java": Language(tree_sitter_java.language()),
}

EXTENSIONS = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
}

_PARSERS: Dict[str, Parser] = {}

MAX_CODE_CHARS = 6000
JS_FAMILY = {"javascript", "typescript", "tsx"}

BRANCH_NODES = {
    "if_statement", "elif_clause", "for_statement", "for_in_statement", "while_statement",
    "do_statement", "try_statement", "except_clause", "catch_clause", "case_clause",
    "switch_case", "conditional_expression", "ternary_expression", "boolean_operator",
    "expression_case", "communication_case", "enhanced_for_statement", "switch_label",
    "with_statement", "match_statement", "case_pattern",
}

SELF_RECEIVERS = {"self", "cls", "this", "super"}


def language_for(path: str) -> Optional[str]:
    return EXTENSIONS.get(PurePosixPath(path).suffix.lower())


def get_parser(lang: str) -> Parser:
    if lang not in _PARSERS:
        _PARSERS[lang] = Parser(LANGUAGES[lang])
    return _PARSERS[lang]


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path.lower())
    name = p.name
    parts = set(p.parts[:-1])
    if parts & {"tests", "test", "__tests__", "spec", "specs"}:
        return True
    if re.match(r"^test_.*\.py$", name) or name.endswith("_test.py") or name == "conftest.py":
        return True
    if re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", name):
        return True
    if name.endswith("_test.go"):
        return True
    if name.endswith("test.java") or name.endswith("tests.java"):
        return True
    return False


def split_identifier(name: str) -> List[str]:
    """processRefund -> [process, refund]; HTTPServer_v2 -> [http, server, v2]."""
    words: List[str] = []
    for part in re.split(r"[^A-Za-z0-9]+", name):
        words.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part))
    return [w.lower() for w in words if w]


def stable_id(*parts: str) -> str:
    return hashlib.sha1("::".join(parts).encode()).hexdigest()[:16]


@dataclass
class CallSite:
    name: str
    receiver: Optional[str]
    line: int


@dataclass
class FunctionDef:
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str
    code: str
    doc: str
    complexity: int
    class_name: Optional[str] = None
    is_test: bool = False
    calls: List[CallSite] = field(default_factory=list)
    # Anonymous JS callbacks are named from where they are passed or assigned.
    anonymous: bool = False
    # Source minus nested function bodies (what this function itself does).
    own_code: str = ""
    # Callbacks this function registers (event handlers, promise callbacks...).
    registers: List["FunctionDef"] = field(default_factory=list)
    id: str = ""


@dataclass
class ClassDef:
    name: str
    file_path: str
    start_line: int
    end_line: int
    id: str = ""


@dataclass
class ParsedFile:
    path: str
    language: str
    loc: int
    functions: List[FunctionDef] = field(default_factory=list)
    classes: List[ClassDef] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    # Source outside every function: imports, constants, module state. Tests need it.
    module_code: str = ""


def _text(node: Optional[Node], src: bytes) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


class FileParser:
    def __init__(self, path: str, content: str):
        self.path = path
        self.lang = language_for(path) or ""
        self.src = content.encode("utf-8", errors="replace")
        self.test_file = is_test_file(path)
        self.result = ParsedFile(path=path, language=self.lang, loc=content.count("\n") + 1)

    def parse(self) -> ParsedFile:
        if self.lang not in LANGUAGES:
            return self.result
        tree = get_parser(self.lang).parse(self.src)
        self._walk(tree.root_node, class_stack=[])
        self._assign_ids()
        self.result.module_code = module_code(self.src.decode("utf-8", errors="replace"), self.result.functions)
        return self.result

    # ------------------------------------------------------------------ walk

    def _walk(self, node: Node, class_stack: List[str]) -> None:
        cls = self._class_name(node)
        if cls:
            self.result.classes.append(ClassDef(
                name=cls, file_path=self.path,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
            ))
            for child in node.children:
                self._walk(child, class_stack + [cls])
            return

        self._collect_import(node)

        fn = self._function_def(node, class_stack)
        if fn is not None:
            self.result.functions.append(fn)
            # Nested named functions get their own nodes; their calls are not
            # attributed to the outer function.
            body_calls, nested = self._calls_in(node, skip_root=True)
            fn.calls = body_calls
            fn.own_code = self._text_without(node, nested)
            before = len(self.result.functions)
            for n in nested:
                idx = len(self.result.functions)
                self._walk(n, class_stack=[])
                if idx < len(self.result.functions) and self.result.functions[idx].anonymous:
                    fn.registers.append(self.result.functions[idx])
            for inner in self.result.functions[before:]:
                if inner.class_name is None and not inner.anonymous:
                    inner.qualified_name = f"{fn.qualified_name}.{inner.qualified_name}"
            return

        test = self._js_test_case(node)
        if test is not None:
            self.result.functions.append(test)
            return

        for child in node.children:
            self._walk(child, class_stack)

    def _class_name(self, node: Node) -> Optional[str]:
        t = node.type
        if t in ("class_definition", "class_declaration", "abstract_class_declaration",
                 "interface_declaration", "enum_declaration"):
            return _text(node.child_by_field_name("name"), self.src) or None
        if self.lang == "go" and t == "type_spec":
            type_node = node.child_by_field_name("type")
            if type_node is not None and type_node.type in ("struct_type", "interface_type"):
                return _text(node.child_by_field_name("name"), self.src) or None
        return None

    def _function_def(self, node: Node, class_stack: List[str]) -> Optional[FunctionDef]:
        t = node.type
        name_node: Optional[Node] = None
        fn_node = node
        class_name = class_stack[-1] if class_stack else None
        anon_name = self._anonymous_name(node)

        if self.lang == "python" and t == "function_definition":
            if self._is_python_overload(node):
                return None  # typing stubs; the real implementation follows
            name_node = node.child_by_field_name("name")
        elif self.lang in JS_FAMILY:
            if t in ("function_declaration", "generator_function_declaration", "method_definition"):
                name_node = node.child_by_field_name("name")
            elif t == "variable_declarator":
                value = node.child_by_field_name("value")
                if value is not None and value.type in ("arrow_function", "function_expression", "function"):
                    name_node = node.child_by_field_name("name")
                    fn_node = value
                else:
                    return None
            elif t in ("public_field_definition", "field_definition"):
                value = node.child_by_field_name("value")
                if value is not None and value.type in ("arrow_function", "function_expression"):
                    name_node = node.child_by_field_name("name") or node.child_by_field_name("property")
                    fn_node = value
                else:
                    return None
        elif self.lang == "go" and t in ("function_declaration", "method_declaration"):
            name_node = node.child_by_field_name("name")
            if t == "method_declaration":
                recv = _text(node.child_by_field_name("receiver"), self.src)
                m = re.search(r"\*?\s*([A-Za-z_]\w*)\s*\)$", recv)
                class_name = m.group(1) if m else class_name
        elif self.lang == "java" and t in ("method_declaration", "constructor_declaration"):
            name_node = node.child_by_field_name("name")

        if name_node is None and anon_name is None:
            return None
        name = anon_name or _text(name_node, self.src)
        if not name:
            return None
        if anon_name:
            class_name = None

        code = _text(node, self.src)
        signature = code.split("{")[0].split(":\n")[0].strip().splitlines()[0][:200] if code else name
        if self.lang == "python":
            signature = code.split(":\n")[0].replace("\n", " ").strip()[:200]

        is_test = self.test_file and self._looks_like_test(name, node)
        return FunctionDef(
            name=name,
            qualified_name=f"{class_name}.{name}" if class_name else name,
            file_path=self.path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=" ".join(signature.split()),
            code=code[:MAX_CODE_CHARS],
            doc=self._docstring(node, fn_node),
            complexity=1 + self._count_branches(fn_node),
            class_name=class_name,
            is_test=is_test,
            anonymous=anon_name is not None,
        )

    def _text_without(self, node: Node, nested: List[Node]) -> str:
        parts, pos = [], node.start_byte
        for n in sorted(nested, key=lambda n: n.start_byte):
            parts.append(self.src[pos:n.start_byte])
            pos = max(pos, n.end_byte)
        parts.append(self.src[pos:node.end_byte])
        return b" ".join(parts).decode("utf-8", errors="replace")[:MAX_CODE_CHARS]

    def _anonymous_name(self, node: Node) -> Optional[str]:
        """Name for an anonymous JS function from its context:
        form.addEventListener("submit", (e) => ...)  -> form.addEventListener('submit')
        button.onclick = () => ...                    -> button.onclick
        { save: function () {...} }                   -> save
        Test files are skipped: describe/it callbacks are handled as Test nodes."""
        if self.lang not in JS_FAMILY or self.test_file:
            return None
        if node.type not in ("arrow_function", "function_expression", "function"):
            return None
        parent = node.parent
        if parent is None:
            return None
        if parent.type == "arguments" and parent.parent is not None and parent.parent.type == "call_expression":
            callee = _text(parent.parent.child_by_field_name("function"), self.src).split("(")[0][:60]
            first = parent.named_children[0] if parent.named_child_count else None
            label = _text(first, self.src).strip("'\"`")[:40] if first is not None and first.type in ("string", "template_string") else ""
            return f"{callee}('{label}')" if label else f"{callee}(callback)"
        if parent.type == "assignment_expression" and parent.child_by_field_name("right") == node:
            return _text(parent.child_by_field_name("left"), self.src)[:80] or None
        if parent.type == "pair" and parent.child_by_field_name("value") == node:
            return _text(parent.child_by_field_name("key"), self.src).strip("'\"`")[:80] or None
        return None

    def _is_python_overload(self, node: Node) -> bool:
        parent = node.parent
        if parent is None or parent.type != "decorated_definition":
            return False
        return any(c.type == "decorator" and "overload" in _text(c, self.src) for c in parent.children)

    def _looks_like_test(self, name: str, node: Node) -> bool:
        lower = name.lower()
        if self.lang == "python":
            return lower.startswith("test")
        if self.lang == "go":
            return name.startswith("Test")
        if self.lang == "java":
            prev = node.child_by_field_name("modifiers") or (node.children[0] if node.children else None)
            return "@Test" in _text(prev, self.src) or lower.startswith("test")
        return lower.startswith("test")

    def _js_test_case(self, node: Node) -> Optional[FunctionDef]:
        """it('does x', () => {...}) / test('does x', ...) inside a JS/TS test file."""
        if self.lang not in JS_FAMILY or not self.test_file or node.type != "call_expression":
            return None
        callee = _text(node.child_by_field_name("function"), self.src)
        if callee.split(".")[0] not in ("it", "test"):
            return None
        args = node.child_by_field_name("arguments")
        if args is None or args.named_child_count < 2:
            return None
        title_node = args.named_children[0]
        if title_node.type not in ("string", "template_string"):
            return None
        title = _text(title_node, self.src).strip("'\"`")
        describe = self._enclosing_describe(node)
        name = f"{describe} > {title}" if describe else title
        calls, _ = self._calls_in(args.named_children[1], skip_root=False)
        code = _text(node, self.src)
        return FunctionDef(
            name=name, qualified_name=name, file_path=self.path,
            start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
            signature=f"{callee}('{title}')", code=code[:MAX_CODE_CHARS], doc="",
            complexity=1, is_test=True, calls=calls,
        )

    def _enclosing_describe(self, node: Node) -> str:
        names = []
        cur = node.parent
        while cur is not None:
            if cur.type == "call_expression" and _text(cur.child_by_field_name("function"), self.src) == "describe":
                args = cur.child_by_field_name("arguments")
                if args is not None and args.named_child_count:
                    names.append(_text(args.named_children[0], self.src).strip("'\"`"))
            cur = cur.parent
        return " > ".join(reversed(names))

    # ----------------------------------------------------------------- calls

    def _calls_in(self, root: Node, skip_root: bool) -> Tuple[List[CallSite], List[Node]]:
        calls: List[CallSite] = []
        nested: List[Node] = []
        stack = list(reversed(root.children)) if skip_root else [root]
        while stack:
            node = stack.pop()
            if self._is_named_definition(node):
                nested.append(node)
                continue
            site = self._call_site(node)
            if site is not None:
                calls.append(site)
            stack.extend(reversed(node.children))
        return calls, nested

    def _is_named_definition(self, node: Node) -> bool:
        if self._class_name(node):
            return True
        if node.type in ("function_definition", "function_declaration", "method_definition",
                         "method_declaration", "generator_function_declaration", "constructor_declaration"):
            return True
        if node.type == "variable_declarator" and self.lang in JS_FAMILY:
            value = node.child_by_field_name("value")
            return value is not None and value.type in ("arrow_function", "function_expression")
        return self._anonymous_name(node) is not None

    def _call_site(self, node: Node) -> Optional[CallSite]:
        line = node.start_point[0] + 1
        t = node.type
        if self.lang == "python" and t == "call":
            return self._split_callee(node.child_by_field_name("function"), line, "attribute", "object", "attribute")
        if self.lang in JS_FAMILY and t == "call_expression":
            return self._split_callee(node.child_by_field_name("function"), line, "member_expression", "object", "property")
        if self.lang == "go" and t == "call_expression":
            return self._split_callee(node.child_by_field_name("function"), line, "selector_expression", "operand", "field")
        if self.lang == "java" and t == "method_invocation":
            name = _text(node.child_by_field_name("name"), self.src)
            obj = node.child_by_field_name("object")
            return CallSite(name=name, receiver=_text(obj, self.src) if obj is not None else None, line=line)
        if t in ("new_expression", "object_creation_expression"):
            ctor = node.child_by_field_name("constructor") or node.child_by_field_name("type")
            name = _text(ctor, self.src).split(".")[-1].split("<")[0]
            if name:
                return CallSite(name=name, receiver="__new__", line=line)
        return None

    def _split_callee(self, fn: Optional[Node], line: int, member_type: str, obj_field: str, prop_field: str) -> Optional[CallSite]:
        if fn is None:
            return None
        if fn.type in ("identifier", "field_identifier"):
            return CallSite(name=_text(fn, self.src), receiver=None, line=line)
        if fn.type == member_type:
            prop = _text(fn.child_by_field_name(prop_field), self.src)
            obj = _text(fn.child_by_field_name(obj_field), self.src)
            if prop:
                return CallSite(name=prop, receiver=obj, line=line)
        return None

    # --------------------------------------------------------------- helpers

    def _collect_import(self, node: Node) -> None:
        t = node.type
        if self.lang == "python" and t in ("import_statement", "import_from_statement"):
            if t == "import_from_statement":
                mod = node.child_by_field_name("module_name")
                if mod is not None:
                    self.result.imports.append(_text(mod, self.src))
            else:
                for child in node.named_children:
                    self.result.imports.append(_text(child, self.src).split(" as ")[0])
        elif self.lang in JS_FAMILY and t == "import_statement":
            src = node.child_by_field_name("source")
            if src is not None:
                self.result.imports.append(_text(src, self.src).strip("'\"`"))
        elif self.lang in JS_FAMILY and t == "call_expression" and _text(node.child_by_field_name("function"), self.src) == "require":
            args = node.child_by_field_name("arguments")
            if args is not None and args.named_child_count and args.named_children[0].type == "string":
                self.result.imports.append(_text(args.named_children[0], self.src).strip("'\"`"))
        elif self.lang == "go" and t == "import_spec":
            self.result.imports.append(_text(node.child_by_field_name("path"), self.src).strip('"'))
        elif self.lang == "java" and t == "import_declaration":
            self.result.imports.append(_text(node, self.src).replace("import", "").replace("static", "").strip(" ;"))

    def _docstring(self, node: Node, fn_node: Node) -> str:
        if self.lang == "python":
            body = node.child_by_field_name("body")
            if body is not None and body.named_child_count:
                first = body.named_children[0]
                if first.type == "expression_statement" and first.named_child_count and first.named_children[0].type == "string":
                    return _text(first.named_children[0], self.src).strip("\"' \n")[:300]
            return ""
        prev = node.prev_named_sibling
        if prev is None and node.parent is not None and node.parent.type in ("lexical_declaration", "export_statement", "variable_declaration"):
            prev = node.parent.prev_named_sibling
        if prev is not None and prev.type in ("comment", "block_comment", "line_comment"):
            text = _text(prev, self.src)
            text = re.sub(r"\*/\s*$", "", text)
            return re.sub(r"^\s*(/\*\*?|\*|//)\s?", "", text, flags=re.M).strip()[:300]
        return ""

    def _count_branches(self, node: Node) -> int:
        count = 0
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in BRANCH_NODES:
                count += 1
            elif n.type == "binary_expression":
                op = n.child_by_field_name("operator")
                if op is not None and op.type in ("&&", "||", "??"):
                    count += 1
            stack.extend(n.children)
        return count

    def _assign_ids(self) -> None:
        seen: Dict[str, int] = {}
        for c in self.result.classes:
            key = f"class:{c.name}"
            seen[key] = seen.get(key, 0) + 1
            c.id = stable_id(self.path, key, str(seen[key]))
        for f in self.result.functions:
            key = f"fn:{f.qualified_name}"
            seen[key] = seen.get(key, 0) + 1
            f.id = stable_id(self.path, key, str(seen[key]))


def module_code(content: str, functions: List[FunctionDef], limit: int = 1500) -> str:
    inside = set()
    for f in functions:
        inside.update(range(f.start_line, f.end_line + 1))
    kept, blank = [], False
    for n, line in enumerate(content.splitlines(), start=1):
        if n in inside:
            continue
        if not line.strip():
            if not blank and kept:
                kept.append("")
            blank = True
            continue
        kept.append(line)
        blank = False
    return "\n".join(kept).strip()[:limit]


def parse_file(path: str, content: str) -> ParsedFile:
    return FileParser(path, content).parse()


BODY_STOPWORDS = {
    "const", "let", "var", "function", "return", "if", "else", "for", "while", "true", "false", "null", "none",
    "undefined", "this", "self", "def", "new", "await", "async", "import", "from", "class", "try", "catch",
    "except", "raise", "throw", "and", "or", "not", "in", "is", "of", "the", "to", "e", "i", "x",
}


# Event-registration APIs. Their words ("add event listener") are in every handler's name
# and say nothing about what the handler does, so "tasks are not added" would match all of them.
EVENT_APIS = {"addEventListener", "removeEventListener", "addListener", "removeListener"}


def _without_event_apis(text: str) -> str:
    return re.sub(r"\b(" + "|".join(EVENT_APIS) + r")\b", " ", text)


def body_tokens_for(fn: FunctionDef, limit: int = 150) -> str:
    """Words from identifiers and string literals in the body, so a report that
    says "tasks are not appended to the list" can match `list_el.appendChild(task_el)`."""
    words: List[str] = []
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", _without_event_apis(fn.own_code or fn.code)):
        words.extend(split_identifier(ident))
    seen = dict.fromkeys(w for w in words if len(w) > 2 and w not in BODY_STOPWORDS)
    return " ".join(list(seen)[:limit])


def tokens_for(fn: FunctionDef) -> str:
    words = split_identifier(_without_event_apis(fn.name))
    if fn.class_name:
        words += split_identifier(fn.class_name)
    for part in PurePosixPath(fn.file_path).with_suffix("").parts:
        words += split_identifier(part)
    words.append(fn.name.lower())
    return " ".join(dict.fromkeys(words))
