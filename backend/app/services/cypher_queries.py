"""Every Cypher query the product runs, in one place.

All queries are parameterized. Each repository has its own graph, so no
query needs a repo filter.
"""

# --------------------------------------------------------------------------
# Ingestion (writes)
# --------------------------------------------------------------------------

WRITE_REPOSITORY = """
MERGE (r:Repository {id: $id})
SET r.name = $name, r.url = $url, r.ingested_at = timestamp()
"""

WRITE_FILES = """
MATCH (r:Repository {id: $repo_id})
UNWIND $rows AS row
CREATE (f:File {path: row.path, language: row.language, loc: row.loc, is_test: row.is_test,
                module_code: row.module_code})
CREATE (r)-[:CONTAINS]->(f)
"""

WRITE_CLASSES = """
UNWIND $rows AS row
MATCH (file:File {path: row.file_path})
CREATE (c:Class {id: row.id, name: row.name, file_path: row.file_path,
                 start_line: row.start_line, end_line: row.end_line})
CREATE (file)-[:CONTAINS]->(c)
"""

WRITE_FUNCTIONS = """
UNWIND $rows AS row
MATCH (file:File {path: row.file_path})
CREATE (f:Function {id: row.id, name: row.name, qualified_name: row.qualified_name,
                    file_path: row.file_path, start_line: row.start_line, end_line: row.end_line,
                    signature: row.signature, doc: row.doc, code: row.code, complexity: row.complexity,
                    loc: row.loc, tokens: row.tokens, body_tokens: row.body_tokens, class_name: row.class_name,
                    in_test_file: row.in_test_file})
CREATE (file)-[:CONTAINS]->(f)
"""

WRITE_HAS_METHOD = """
UNWIND $rows AS row
MATCH (c:Class {id: row.class_id}), (f:Function {id: row.fn_id})
CREATE (c)-[:HAS_METHOD]->(f)
"""

WRITE_TEST_NODES = """
UNWIND $rows AS row
MATCH (file:File {path: row.file_path})
CREATE (t:Test {id: row.id, name: row.name, file_path: row.file_path,
                start_line: row.start_line, end_line: row.end_line, code: row.code})
CREATE (file)-[:CONTAINS]->(t)
"""

WRITE_CALLS = """
UNWIND $rows AS row
MATCH (a:Function {id: row.src}), (b:Function {id: row.dst})
CREATE (a)-[:CALLS {line: row.line, resolution: row.resolution, count: row.count}]->(b)
"""

WRITE_TESTS = """
UNWIND $rows AS row
MATCH (t:Test {id: row.src}), (f:Function {id: row.dst})
CREATE (t)-[:TESTS {line: row.line}]->(f)
"""

WRITE_IMPORTS = """
UNWIND $rows AS row
MATCH (a:File {path: row.src}), (b:File {path: row.dst})
CREATE (a)-[:IMPORTS]->(b)
"""

WRITE_COMMITS = """
UNWIND $rows AS row
MERGE (p:Person {email: row.email})
ON CREATE SET p.name = row.author
CREATE (c:Commit {sha: row.sha, message: row.message, timestamp: row.timestamp, author: row.author})
CREATE (p)-[:AUTHORED]->(c)
"""

WRITE_MODIFIES = """
UNWIND $rows AS row
MATCH (c:Commit {sha: row.sha}), (f:File {path: row.path})
CREATE (c)-[:MODIFIES {added: row.added, removed: row.removed}]->(f)
"""

WRITE_TOUCHED = """
UNWIND $rows AS row
MATCH (c:Commit {sha: row.sha}), (f:Function {id: row.fn_id})
CREATE (c)-[:TOUCHED]->(f)
"""

# --------------------------------------------------------------------------
# Agent tools (reads)
# --------------------------------------------------------------------------

# Full-text search over split identifier words, path words and docstrings.
SEARCH_FULLTEXT = """
CALL db.idx.fulltext.queryNodes('Function', $q) YIELD node, score
WHERE node.in_test_file = false
RETURN node.id AS id, node.qualified_name AS name, node.file_path AS file_path,
       node.start_line AS start_line, score,
       node.tokens + ' ' + coalesce(node.doc, '') + ' ' + coalesce(node.body_tokens, '') AS text
ORDER BY score DESC
LIMIT $limit
"""

# Exact identifier match (bug reports often name the function).
SEARCH_EXACT_NAME = """
MATCH (f:Function)
WHERE f.name IN $names OR f.qualified_name IN $names
RETURN f.id AS id, f.qualified_name AS name, f.file_path AS file_path, f.start_line AS start_line
LIMIT $limit
"""

# Stack-trace frame: file path suffix + function name + line inside the body.
SEARCH_STACK_FRAME = """
MATCH (f:Function)
WHERE f.name = $name AND f.file_path ENDS WITH $file_suffix
  AND ($line < 0 OR (f.start_line <= $line AND f.end_line >= $line))
RETURN f.id AS id, f.qualified_name AS name, f.file_path AS file_path, f.start_line AS start_line
LIMIT 3
"""

GET_FUNCTION = """
MATCH (f:Function {id: $id})
OPTIONAL MATCH (c:Class)-[:HAS_METHOD]->(f)
RETURN f.id AS id, f.qualified_name AS name, f.file_path AS file_path, f.start_line AS start_line,
       f.end_line AS end_line, f.signature AS signature, f.doc AS doc, f.code AS code,
       f.complexity AS complexity, c.name AS class_name
"""

# Multi-hop: everything that can reach this function within $depth calls.
GET_CALLERS = """
MATCH p = (caller:Function)-[:CALLS*1..3]->(f:Function {id: $id})
WHERE length(p) <= $depth AND caller.id <> $id
WITH caller, min(length(p)) AS distance
RETURN caller.id AS id, caller.qualified_name AS name, caller.file_path AS file_path, distance
ORDER BY distance, name
LIMIT $limit
"""

GET_CALLEES = """
MATCH p = (f:Function {id: $id})-[:CALLS*1..3]->(callee:Function)
WHERE length(p) <= $depth AND callee.id <> $id
WITH callee, min(length(p)) AS distance
RETURN callee.id AS id, callee.qualified_name AS name, callee.file_path AS file_path, distance
ORDER BY distance, name
LIMIT $limit
"""

# Direct tests, plus tests that reach the function through up to 2 calls.
GET_TESTS = """
UNWIND $ids AS fid
MATCH (f:Function {id: fid})
OPTIONAL MATCH (t:Test)-[:TESTS]->(f)
OPTIONAL MATCH (ti:Test)-[:TESTS]->(:Function)-[:CALLS*1..2]->(f)
RETURN f.id AS id, f.qualified_name AS name,
       [x IN collect(DISTINCT t) WHERE x IS NOT NULL | {id: x.id, name: x.name, file_path: x.file_path}] AS direct_tests,
       [x IN collect(DISTINCT ti) WHERE x IS NOT NULL | {id: x.id, name: x.name, file_path: x.file_path}] AS indirect_tests
"""

# Commits whose diff hunks overlapped each function, with their authors.
GET_RECENT_CHANGES = """
UNWIND $ids AS fid
MATCH (f:Function {id: fid})
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(f)
WITH f, c, p ORDER BY c.timestamp DESC
WITH f, collect(CASE WHEN c IS NULL THEN NULL ELSE
       {sha: c.sha, message: c.message, timestamp: c.timestamp, author: p.name, email: p.email} END)[0..$per_fn] AS commits
RETURN f.id AS id, f.qualified_name AS name, commits
"""

# How recent each function's last change is compared with the repo's history.
GET_CHANGE_RECENCY = """
MATCH (c:Commit) WITH max(c.timestamp) AS newest, min(c.timestamp) AS oldest
UNWIND $ids AS fid
MATCH (f:Function {id: fid})
OPTIONAL MATCH (c2:Commit)-[:TOUCHED]->(f)
RETURN f.id AS id, count(c2) AS touches, max(c2.timestamp) AS last_touch, newest, oldest
"""

# Who knows this code: authors of commits that touched the function or its file.
FIND_EXPERTS = """
MATCH (f:Function {id: $id})
OPTIONAL MATCH (p1:Person)-[:AUTHORED]->(:Commit)-[:TOUCHED]->(f)
OPTIONAL MATCH (p2:Person)-[:AUTHORED]->(:Commit)-[:MODIFIES]->(:File {path: f.file_path})
WITH collect(p1) AS fn_people, collect(p2) AS file_people
UNWIND fn_people + file_people AS p
WITH p, size([x IN fn_people WHERE x = p]) AS fn_commits, size([x IN file_people WHERE x = p]) AS file_commits
WHERE p IS NOT NULL
RETURN DISTINCT p.name AS name, p.email AS email, fn_commits, file_commits
ORDER BY fn_commits DESC, file_commits DESC
LIMIT 5
"""

# Risky recent changes: untested functions touched by the last $n commits, with who changed them
# and how many functions call them (most-called first).
RISKY_CHANGES = """
  MATCH (c:Commit)
  WITH c ORDER BY c.timestamp DESC LIMIT $n
  MATCH (p:Person)-[:AUTHORED]->(c)-[:TOUCHED]->(f:Function)
  WHERE f.in_test_file = false
    AND NOT (:Test)-[:TESTS]->(f)
  WITH f,
       collect(DISTINCT p.name) AS changed_by,
       collect(c.message) AS commits,
       max(c.timestamp) AS last_changed
  OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
  RETURN f.id AS id,
         f.qualified_name AS function,
         f.file_path AS file,
         changed_by, commits, last_changed,
         count(DISTINCT caller) AS callers
  ORDER BY callers DESC, last_changed DESC
"""

# Blast radius: transitive callers, the files they live in, and tests that would catch a regression.
BLAST_RADIUS = """
MATCH (f:Function {id: $id})
OPTIONAL MATCH (caller:Function)-[:CALLS*1..4]->(f)
WITH f, collect(DISTINCT caller) AS callers
OPTIONAL MATCH (t:Test)-[:TESTS]->(x:Function) WHERE x = f OR x IN callers
RETURN size(callers) AS affected_functions,
       [c IN callers | c.file_path] AS caller_files,
       collect(DISTINCT t.name)[0..10] AS covering_tests,
       [c IN callers | c.qualified_name][0..15] AS sample_callers
"""

CALL_PATH = """
MATCH (a:Function {id: $src}), (b:Function {id: $dst})
WITH shortestPath((a)-[:CALLS*1..8]->(b)) AS p
WHERE p IS NOT NULL
RETURN [n IN nodes(p) | {id: n.id, name: n.qualified_name, file_path: n.file_path}] AS path
"""

# Graph algorithm: PageRank over the call graph = functions the codebase leans on most.
HOTSPOTS = """
CALL algo.pageRank('Function', 'CALLS') YIELD node, score
WHERE node.in_test_file = false
OPTIONAL MATCH (t:Test)-[:TESTS]->(node)
WITH node, score, count(t) AS tests
RETURN node.id AS id, node.qualified_name AS name, node.file_path AS file_path,
       score, node.complexity AS complexity, tests
ORDER BY score DESC
LIMIT $limit
"""

PAGERANK_FOR = """
CALL algo.pageRank('Function', 'CALLS') YIELD node, score
WITH collect({id: node.id, score: score}) AS all, max(score) AS top
UNWIND all AS row
WITH row, top WHERE row.id IN $ids
RETURN row.id AS id, row.score / top AS centrality
"""

FEATURES_FOR = """
UNWIND $ids AS fid
MATCH (f:Function {id: fid})
RETURN f.id AS id, f.qualified_name AS name, f.file_path AS file_path, f.start_line AS start_line,
       f.complexity AS complexity, f.loc AS loc,
       size([(c:Function)-[:CALLS]->(f) | c]) AS fan_in,
       size([(f)-[:CALLS]->(c:Function) | c]) AS fan_out
"""

# --------------------------------------------------------------------------
# Agent memory (Investigations persist in the repository graph)
# --------------------------------------------------------------------------

SAVE_BUG_REPORT = """
MERGE (b:BugReport {id: $id})
SET b.title = $title, b.description = $description, b.created_at = $created_at, b.status = $status
"""

SAVE_INVESTIGATION = """
MATCH (b:BugReport {id: $bug_id})
MERGE (i:Investigation {id: $id})
SET i.title = $title, i.status = $status, i.summary = $summary, i.created_at = $created_at,
    i.completed_at = $completed_at, i.keywords = $keywords, i.keywords_text = $keywords_text,
    i.mode = $mode, i.payload = $payload
MERGE (b)-[:INVESTIGATED_IN]->(i)
"""

LINK_SUSPECTS = """
MATCH (i:Investigation {id: $id})
UNWIND $rows AS row
MATCH (f:Function {id: row.fn_id})
MERGE (i)-[s:SUSPECTS]->(f)
SET s.rank = row.rank, s.score = row.score
"""

LINK_MENTIONS = """
MATCH (b:BugReport {id: $bug_id})
UNWIND $ids AS fid
MATCH (f:Function {id: fid})
MERGE (b)-[:MENTIONS]->(f)
"""

CONFIRM_ROOT_CAUSE = """
MATCH (i:Investigation {id: $id}), (f:Function {id: $fn_id})
MERGE (i)-[c:CONFIRMED]->(f)
SET c.at = timestamp(), c.note = $note
SET i.status = 'confirmed'
RETURN f.qualified_name AS name
"""

LOAD_INVESTIGATION = """
MATCH (b:BugReport)-[:INVESTIGATED_IN]->(i:Investigation {id: $id})
RETURN i.payload AS payload, b.id AS bug_id, b.title AS title, b.description AS description
"""

LIST_INVESTIGATIONS = """
MATCH (b:BugReport)-[:INVESTIGATED_IN]->(i:Investigation)
OPTIONAL MATCH (i)-[:CONFIRMED]->(cf:Function)
RETURN i.id AS id, b.title AS title, i.status AS status, i.created_at AS created_at,
       i.summary AS summary, collect(cf.qualified_name) AS confirmed
ORDER BY i.created_at DESC
LIMIT $limit
"""

# Recall: earlier investigations that suspected or confirmed these functions or
# their direct neighbours in the call graph.
RECALL_BY_FUNCTIONS = """
UNWIND $ids AS fid
MATCH (f:Function {id: fid})
MATCH (i:Investigation)-[r:SUSPECTS|CONFIRMED]->(g:Function)
WHERE i.id <> $exclude AND (g = f OR (g)-[:CALLS]-(f))
MATCH (b:BugReport)-[:INVESTIGATED_IN]->(i)
RETURN DISTINCT f.id AS fn_id, g.id AS past_fn_id, g.qualified_name AS past_fn, type(r) AS relation,
       i.id AS investigation_id, b.title AS bug_title, i.status AS status, g = f AS same_function
LIMIT 50
"""

RECALL_BY_TEXT = """
CALL db.idx.fulltext.queryNodes('Investigation', $q) YIELD node, score
WHERE node.id <> $exclude
MATCH (b:BugReport)-[:INVESTIGATED_IN]->(node)
OPTIONAL MATCH (node)-[:CONFIRMED]->(cf:Function)
OPTIONAL MATCH (node)-[s:SUSPECTS]->(sf:Function) WHERE s.rank = 1
RETURN node.id AS investigation_id, b.title AS bug_title, node.status AS status, score,
       collect(DISTINCT cf.id) AS confirmed_ids, collect(DISTINCT cf.qualified_name) AS confirmed,
       collect(DISTINCT sf.qualified_name) AS top_suspect
ORDER BY score DESC
LIMIT 5
"""

# --------------------------------------------------------------------------
# Repository stats / visualization
# --------------------------------------------------------------------------

REPO_STATS = """
MATCH (n) RETURN labels(n)[0] AS type, count(n) AS count
"""

EDGE_STATS = """
MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count
"""

LANGUAGE_STATS = """
MATCH (f:File) RETURN f.language AS language, count(f) AS count ORDER BY count DESC
"""

NEIGHBORHOOD = """
MATCH (center) WHERE center.id = $id
OPTIONAL MATCH (center)-[r]-(other)
WHERE NOT other:Repository
RETURN center, collect(DISTINCT {rel: r, node: other})[0..$limit] AS links
"""

SUBGRAPH_BETWEEN = """
MATCH (a)-[r]->(b)
WHERE (a.id IN $ids OR a.sha IN $ids OR a.email IN $ids)
  AND (b.id IN $ids OR b.sha IN $ids OR b.email IN $ids)
RETURN a, r, b
"""

# Test writing: the function, what calls it (how it is used) and what it calls (what to set up or fake).
TEST_CONTEXT = """
MATCH (file:File)-[:CONTAINS]->(f:Function {id: $id})
OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
WITH file, f, collect(DISTINCT caller) AS callers
OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
WITH file, f, callers, collect(DISTINCT callee) AS callees
RETURN f.id AS id, f.qualified_name AS name, f.file_path AS file_path, f.start_line AS start_line,
       f.code AS code, file.module_code AS module_code, size(callers) AS caller_count,
       [c IN callers | {name: c.qualified_name, file_path: c.file_path, code: c.code}][..3] AS callers,
       [c IN callees | {name: c.qualified_name, file_path: c.file_path, code: c.code}][..4] AS callees
"""

# Existing tests that exercise the function's neighbourhood: the closest examples of how this
# repo sets up data and asserts, so a new test can follow the same style.
NEARBY_TESTS = """
MATCH (f:Function {id: $id})
MATCH (t:Test)-[:TESTS]->(:Function)-[:CALLS*1..2]-(f)
WITH t, count(*) AS links
RETURN t.name AS name, t.file_path AS file_path, t.code AS code
ORDER BY links DESC, t.name
LIMIT 2
"""

# Fallback when nothing nearby is tested: any test written in the same language.
ANY_TESTS = """
MATCH (t:Test)
WHERE t.file_path ENDS WITH $ext
RETURN t.name AS name, t.file_path AS file_path, t.code AS code
ORDER BY t.file_path, t.name
LIMIT 2
"""
