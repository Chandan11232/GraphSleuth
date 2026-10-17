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
# One row per name, since the same person often commits under more than one email.
FIND_EXPERTS = """
MATCH (file:File)-[:CONTAINS]->(f:Function {id: $id})
MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:MODIFIES]->(file)
WITH f, p, count(DISTINCT c) AS file_commits
OPTIONAL MATCH (p)-[:AUTHORED]->(c2:Commit)-[:TOUCHED]->(f)
WITH p, file_commits, count(DISTINCT c2) AS fn_commits
WITH p.name AS name, collect(p.email) AS emails, toInteger(sum(fn_commits)) AS fn_commits,
     toInteger(sum(file_commits)) AS file_commits
RETURN name, emails[0] AS email, fn_commits, file_commits
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
OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
WITH f, count(DISTINCT caller) AS fan_in
OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
RETURN f.id AS id, f.qualified_name AS name, f.file_path AS file_path, f.start_line AS start_line,
       f.complexity AS complexity, f.loc AS loc, fan_in, count(DISTINCT callee) AS fan_out
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

# --------------------------------------------------------------------------
# Company knowledge (issues, pull requests, reviews, discussions, owners)
# --------------------------------------------------------------------------

# GitHub logins are joined to git authors through the commit email.
WRITE_KNOWLEDGE_PEOPLE = """
UNWIND $rows AS row
MERGE (p:Person {email: row.email})
ON CREATE SET p.name = row.name
SET p.login = row.login
"""

WRITE_TEAMS = """
UNWIND $rows AS row
MERGE (t:Team {name: row.name})
SET t.id = 'team-' + row.name
WITH t, row
UNWIND row.members AS login
MATCH (p:Person {login: login})
MERGE (p)-[:MEMBER_OF]->(t)
"""

WRITE_ISSUES = """
UNWIND $rows AS row
MERGE (i:Issue {number: row.number})
SET i.id = 'issue-' + toString(row.number), i.title = row.title, i.body = row.body, i.state = row.state,
    i.url = row.url, i.created_at = row.created_at, i.created_ms = row.created_ms, i.closed_ms = row.closed_ms,
    i.labels = row.labels
WITH i, row WHERE row.author IS NOT NULL
MATCH (p:Person {login: row.author})
MERGE (p)-[:OPENED]->(i)
"""

WRITE_PULLS = """
UNWIND $rows AS row
MERGE (pr:PullRequest {number: row.number})
SET pr.id = 'pr-' + toString(row.number), pr.title = row.title, pr.body = row.body, pr.state = row.state,
    pr.url = row.url, pr.created_at = row.created_at, pr.created_ms = row.created_ms, pr.merged_ms = row.merged_ms
WITH pr, row WHERE row.author IS NOT NULL
MATCH (p:Person {login: row.author})
MERGE (p)-[:OPENED]->(pr)
"""

WRITE_REVIEWS = """
UNWIND $rows AS row
MATCH (pr:PullRequest {number: row.number}), (p:Person {login: row.author})
MERGE (p)-[r:REVIEWED]->(pr)
SET r.state = row.state, r.at_ms = row.at_ms
"""

# Issue and PR numbers share one sequence on GitHub, so a comment's target is whichever exists.
WRITE_COMMENTS = """
UNWIND $rows AS row
OPTIONAL MATCH (i:Issue {number: row.on})
OPTIONAL MATCH (pr:PullRequest {number: row.on})
WITH row, coalesce(i, pr) AS target
WHERE target IS NOT NULL
MERGE (c:Comment {id: row.id})
SET c.body = row.body, c.url = row.url, c.created_at = row.created_at, c.created_ms = row.created_ms,
    c.path = row.path
MERGE (c)-[:ON]->(target)
WITH c, row WHERE row.author IS NOT NULL
MATCH (p:Person {login: row.author})
MERGE (p)-[:WROTE]->(c)
"""

# Review comments left on a line of a file.
WRITE_COMMENT_FILES = """
UNWIND $rows AS row
MATCH (c:Comment {id: row.id}), (f:File {path: row.path})
MERGE (c)-[:ABOUT]->(f)
"""

WRITE_FIXES = """
UNWIND $rows AS row
MATCH (pr:PullRequest {number: row.pr}), (i:Issue {number: row.issue})
MERGE (pr)-[:FIXES]->(i)
"""


def write_references(label: str, key: str) -> str:
    return f"""
UNWIND $rows AS row
MATCH (s:{label} {{{key}: row.key}})
OPTIONAL MATCH (i:Issue {{number: row.number}})
OPTIONAL MATCH (pr:PullRequest {{number: row.number}})
WITH s, coalesce(i, pr) AS t
WHERE t IS NOT NULL
MERGE (s)-[:REFERENCES]->(t)
"""


def write_mentions(label: str, key: str) -> str:
    return f"""
UNWIND $rows AS row
MATCH (s:{label} {{{key}: row.key}})
MATCH (f:Function {{name: row.name}})
WHERE f.in_test_file = false
MERGE (s)-[:MENTIONS]->(f)
"""


def write_decisions(label: str, key: str) -> str:
    return f"""
UNWIND $rows AS row
MATCH (s:{label} {{{key}: row.key}})
MERGE (d:Decision {{id: row.id}})
SET d.text = row.text, d.url = row.url, d.at_ms = row.at_ms
MERGE (d)-[:DECIDED_IN]->(s)
WITH d, row WHERE row.author IS NOT NULL
MATCH (p:Person {{login: row.author}})
MERGE (p)-[:DECIDED]->(d)
"""


# A decision is about the functions its own sentence names.
WRITE_DECISION_ABOUT = """
UNWIND $rows AS row
MATCH (d:Decision {id: row.id})
MATCH (f:Function {name: row.name})
WHERE f.in_test_file = false
MERGE (d)-[:ABOUT]->(f)
"""

# By sha (merge, squash or rebase commit) or, for knowledge files, by commit subject.
WRITE_PR_COMMITS = """
UNWIND $rows AS row
MATCH (pr:PullRequest {number: row.number})
MATCH (c:Commit)
WHERE (row.sha IS NOT NULL AND c.sha STARTS WITH row.sha) OR (row.subject IS NOT NULL AND c.message = row.subject)
MERGE (pr)-[:INCLUDES]->(c)
RETURN count(*) AS links
"""

WRITE_TEAM_OWNS = """
UNWIND $rows AS row
MATCH (f:File {path: row.path})
MERGE (t:Team {name: row.team})
SET t.id = 'team-' + row.team
MERGE (t)-[o:OWNS]->(f)
SET o.pattern = row.pattern
"""

WRITE_PERSON_OWNS = """
UNWIND $rows AS row
MATCH (f:File {path: row.path})
MERGE (p:Person {login: row.login})
ON CREATE SET p.name = row.login, p.email = row.login + '@users.noreply.github.com'
MERGE (p)-[o:OWNS]->(f)
SET o.pattern = row.pattern
"""

WRITE_EMAIL_OWNS = """
UNWIND $rows AS row
MATCH (f:File {path: row.path}), (p:Person {email: row.email})
MERGE (p)-[o:OWNS]->(f)
SET o.pattern = row.pattern
"""

MARK_KNOWLEDGE_SYNC = """
MATCH (r:Repository)
SET r.knowledge_source = $source, r.knowledge_synced_at = $at
"""

KNOWLEDGE_STATUS = """
MATCH (r:Repository)
OPTIONAL MATCH (i:Issue)
WITH r, count(i) AS issues
OPTIONAL MATCH (p:PullRequest)
WITH r, issues, count(p) AS pull_requests
OPTIONAL MATCH (d:Decision)
WITH r, issues, pull_requests, count(d) AS decisions
OPTIONAL MATCH (t:Team)
RETURN r.knowledge_source AS source, r.knowledge_synced_at AS synced_at, issues, pull_requests, decisions,
       count(t) AS teams
"""

# Why is this function the way it is: each commit that changed it, newest first, with its pull request.
FUNCTION_HISTORY = """
MATCH (f:Function {id: $id})<-[:TOUCHED]-(c:Commit)
OPTIONAL MATCH (author:Person)-[:AUTHORED]->(c)
OPTIONAL MATCH (pr:PullRequest)-[:INCLUDES]->(c)
WITH c, author, pr ORDER BY c.timestamp DESC
RETURN c.sha AS sha, c.message AS message, c.timestamp AS timestamp, author.name AS author,
       author.email AS email, pr.number AS pr
LIMIT $limit
"""

# The conversation behind a pull request: who opened and reviewed it, and the issues it fixed.
PR_CONTEXT = """
UNWIND $numbers AS n
MATCH (pr:PullRequest {number: n})
OPTIONAL MATCH (opener:Person)-[:OPENED]->(pr)
OPTIONAL MATCH (rev:Person)-[r:REVIEWED]->(pr)
WITH pr, opener, collect(DISTINCT CASE WHEN rev IS NULL THEN NULL ELSE {name: rev.name, state: r.state} END) AS reviews
OPTIONAL MATCH (pr)-[:FIXES]->(i:Issue)
OPTIONAL MATCH (reporter:Person)-[:OPENED]->(i)
WITH pr, opener, reviews,
     collect(DISTINCT CASE WHEN i IS NULL THEN NULL ELSE
       {id: i.id, number: i.number, title: i.title, url: i.url, state: i.state, opened_by: reporter.name} END) AS fixes
OPTIONAL MATCH (cm:Comment)-[:ON]->(pr)
RETURN pr.id AS id, pr.number AS number, pr.title AS title, pr.url AS url, pr.state AS state,
       pr.merged_ms AS merged_ms, opener.name AS opened_by, reviews, fixes, count(cm) AS comments
"""

# Issues, pull requests and comments that name the function.
FUNCTION_DISCUSSIONS = """
MATCH (f:Function {id: $id})<-[:MENTIONS]-(s)
WHERE s:Issue OR s:PullRequest OR s:Comment
OPTIONAL MATCH (s)-[:ON]->(parent)
WITH coalesce(parent, s) AS item, count(*) AS hits
OPTIONAL MATCH (who:Person)-[:OPENED]->(item)
RETURN labels(item)[0] AS type, item.id AS id, item.number AS number, item.title AS title, item.url AS url,
       item.state AS state, who.name AS opened_by, hits
ORDER BY number DESC
LIMIT 10
"""

# Every decision and where it was made.
DECISIONS = """
MATCH (d:Decision)-[:DECIDED_IN]->(src)
OPTIONAL MATCH (src)-[:ON]->(parent)
WITH d, src, coalesce(parent, src) AS item
OPTIONAL MATCH (who:Person)-[:DECIDED]->(d)
RETURN d.id AS id, d.text AS text, d.url AS url, d.at_ms AS at_ms, who.name AS author,
       labels(item)[0] AS item_type, item.id AS item_id, item.number AS item_number, item.title AS item_title,
       labels(src)[0] AS source_type
ORDER BY at_ms DESC
LIMIT $limit
"""

# The code a decision governs: functions its sentence names, functions changed by the pull request
# it was made in, and functions changed by the pull request that fixed the issue it was made in.
# (Plain MATCHes rather than pattern comprehensions, which newer FalkorDB versions evaluate differently.)
DECISION_FUNCTIONS = """
UNWIND $ids AS did
MATCH (d:Decision {id: did})-[:ABOUT]->(f:Function)
RETURN d.id AS id, f.id AS fn_id, f.qualified_name AS name
UNION
UNWIND $ids AS did
MATCH (d:Decision {id: did})-[:DECIDED_IN]->(src)
OPTIONAL MATCH (src)-[:ON]->(parent)
WITH d, coalesce(parent, src) AS item
MATCH (item)-[:INCLUDES]->(:Commit)-[:TOUCHED]->(f:Function)
RETURN d.id AS id, f.id AS fn_id, f.qualified_name AS name
UNION
UNWIND $ids AS did
MATCH (d:Decision {id: did})-[:DECIDED_IN]->(src)
OPTIONAL MATCH (src)-[:ON]->(parent)
WITH d, coalesce(parent, src) AS item
MATCH (item)<-[:FIXES]-(:PullRequest)-[:INCLUDES]->(:Commit)-[:TOUCHED]->(f:Function)
RETURN d.id AS id, f.id AS fn_id, f.qualified_name AS name
"""

# Knowledge drift: code a decision governs that has been changed since, by commits from outside
# the pull request that made the decision.
DECISION_DRIFT = """
MATCH (d:Decision)
OPTIONAL MATCH (d)-[:DECIDED_IN]->(pr1:PullRequest)
OPTIONAL MATCH (d)-[:DECIDED_IN]->(:Comment)-[:ON]->(pr2:PullRequest)
OPTIONAL MATCH (d)-[:DECIDED_IN]->(:Issue)<-[:FIXES]-(pr3:PullRequest)
WITH d, coalesce(pr1, pr2, pr3) AS pr
WHERE pr IS NOT NULL
MATCH (pr)-[:INCLUDES]->(:Commit)-[:TOUCHED]->(f:Function)<-[:TOUCHED]-(later:Commit)
WHERE later.timestamp > coalesce(pr.merged_ms, d.at_ms, 0) AND NOT (pr)-[:INCLUDES]->(later)
OPTIONAL MATCH (author:Person)-[:AUTHORED]->(later)
OPTIONAL MATCH (lpr:PullRequest)-[:INCLUDES]->(later)
OPTIONAL MATCH (rev:Person)-[:REVIEWED]->(lpr)
WITH d, f, later, author, lpr, collect(DISTINCT rev.name) AS reviewers
RETURN d.id AS id, f.id AS fn_id, f.qualified_name AS function, later.sha AS sha, later.message AS message,
       later.timestamp AS timestamp, author.name AS author, lpr.number AS pr, reviewers
ORDER BY timestamp DESC
"""

# Who owns a file: CODEOWNERS teams (with members) and people.
FILE_OWNERS = """
MATCH (file:File {path: $path})
OPTIONAL MATCH (t:Team)-[o:OWNS]->(file)
OPTIONAL MATCH (m:Person)-[:MEMBER_OF]->(t)
WITH file, t, o, collect(DISTINCT m.name) AS members
WITH file, collect(DISTINCT CASE WHEN t IS NULL THEN NULL ELSE
       {id: t.id, team: t.name, pattern: o.pattern, members: members} END) AS teams
OPTIONAL MATCH (p:Person)-[po:OWNS]->(file)
RETURN teams, collect(DISTINCT CASE WHEN p IS NULL THEN NULL ELSE
       {name: p.name, email: p.email, pattern: po.pattern} END) AS people
"""

FILE_AUTHORS = """
MATCH (file:File {path: $path})<-[:MODIFIES]-(c:Commit)<-[:AUTHORED]-(p:Person)
RETURN p.name AS name, p.email AS email, count(c) AS commits, max(c.timestamp) AS last_commit
ORDER BY commits DESC, last_commit DESC
LIMIT 6
"""

FUNCTION_AUTHORS = """
MATCH (f:Function {id: $id})<-[:TOUCHED]-(c:Commit)<-[:AUTHORED]-(p:Person)
RETURN p.name AS name, p.email AS email, count(c) AS fn_commits
ORDER BY fn_commits DESC
"""

# People who reviewed pull requests that changed the file: they know it without having written it.
FILE_REVIEWERS = """
MATCH (file:File {path: $path})<-[:MODIFIES]-(:Commit)<-[:INCLUDES]-(pr:PullRequest)<-[r:REVIEWED]-(p:Person)
RETURN p.name AS name, p.email AS email, count(DISTINCT pr) AS reviews, collect(DISTINCT pr.number)[0..5] AS prs
ORDER BY reviews DESC
LIMIT 6
"""

FIND_FILE = """
MATCH (f:File)
WHERE f.path = $path OR f.path ENDS WITH $suffix
RETURN f.path AS path
ORDER BY size(f.path)
LIMIT 1
"""

OWNERSHIP_MAP = """
MATCH (o)-[r:OWNS]->(f:File)
OPTIONAL MATCH (m:Person)-[:MEMBER_OF]->(o)
WITH o, r.pattern AS pattern, count(DISTINCT f) AS files, collect(DISTINCT m.name) AS members
RETURN labels(o)[0] AS kind, o.id AS id, coalesce(o.name, o.login) AS owner, pattern, files, members
ORDER BY files DESC, owner
"""

# Knowledge held by one person: non-test files only one person has ever changed, most-called first.
SINGLE_AUTHOR_FILES = """
MATCH (f:File)
WHERE f.is_test = false
MATCH (p:Person)-[:AUTHORED]->(:Commit)-[:MODIFIES]->(f)
WITH f, collect(DISTINCT p.name) AS authors
WHERE size(authors) = 1
OPTIONAL MATCH (o)-[:OWNS]->(f)
OPTIONAL MATCH (f)-[:CONTAINS]->(:Function)<-[:CALLS]-(caller:Function)
RETURN f.path AS path, authors[0] AS only_author, count(DISTINCT o) AS owners, count(DISTINCT caller) AS callers
ORDER BY owners, callers DESC, path
LIMIT $limit
"""

SEARCH_ISSUES = """
CALL db.idx.fulltext.queryNodes('Issue', $q) YIELD node, score
OPTIONAL MATCH (who:Person)-[:OPENED]->(node)
RETURN 'Issue' AS type, node.id AS id, node.number AS number, node.title AS title, node.body AS text,
       node.url AS url, node.state AS state, who.name AS author, node.created_ms AS at_ms, score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_PULLS = """
CALL db.idx.fulltext.queryNodes('PullRequest', $q) YIELD node, score
OPTIONAL MATCH (who:Person)-[:OPENED]->(node)
RETURN 'PullRequest' AS type, node.id AS id, node.number AS number, node.title AS title, node.body AS text,
       node.url AS url, node.state AS state, who.name AS author, coalesce(node.merged_ms, node.created_ms) AS at_ms,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_COMMENTS = """
CALL db.idx.fulltext.queryNodes('Comment', $q) YIELD node, score
MATCH (node)-[:ON]->(item)
OPTIONAL MATCH (who:Person)-[:WROTE]->(node)
RETURN 'Comment' AS type, node.id AS id, item.number AS number, item.title AS title, node.body AS text,
       node.url AS url, item.id AS item_id, labels(item)[0] AS item_type, who.name AS author,
       node.created_ms AS at_ms, score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_DECISIONS = """
CALL db.idx.fulltext.queryNodes('Decision', $q) YIELD node, score
MATCH (node)-[:DECIDED_IN]->(src)
OPTIONAL MATCH (src)-[:ON]->(parent)
WITH node, score, coalesce(parent, src) AS item
OPTIONAL MATCH (who:Person)-[:DECIDED]->(node)
RETURN 'Decision' AS type, node.id AS id, item.number AS number, item.title AS title, node.text AS text,
       node.url AS url, item.id AS item_id, labels(item)[0] AS item_type, who.name AS author,
       node.at_ms AS at_ms, score
ORDER BY score DESC
LIMIT $limit
"""

# --------------------------------------------------------------------------
# Fix verification: which tests to run, and what the sandbox proved
# --------------------------------------------------------------------------

# Test impact analysis: every test whose TESTS edge reaches the function directly or
# through up to 3 CALLS hops. These are the tests a change to it can break.
TEST_IMPACT = """
MATCH (f:Function {id: $id})
MATCH p = (t:Test)-[:TESTS]->(:Function)-[:CALLS*0..3]->(f)
WITH t, min(length(p)) AS hops
RETURN t.id AS id, t.name AS name, t.file_path AS file_path, hops - 1 AS distance
ORDER BY distance, file_path, name
"""

COUNT_TESTS = """
MATCH (t:Test) RETURN count(t) AS n
"""

SAVE_FIX = """
MATCH (i:Investigation {id: $investigation_id}), (f:Function {id: $fn_id})
CREATE (x:Fix {id: $id, status: $status, summary: $summary, created_at: $created_at, payload: $payload})
CREATE (i)-[:PROPOSED]->(x)
CREATE (x)-[:PATCHES]->(f)
RETURN x.id AS id
"""

# One TestRun per phase (the original code, then the patched code), linked to every Test node it ran.
SAVE_TEST_RUN = """
MATCH (x:Fix {id: $fix_id})
CREATE (r:TestRun {id: $id, phase: $phase, passed: $passed, failed: $failed, errors: $errors,
                   duration_ms: $duration_ms, at: $at})
CREATE (x)-[:VERIFIED_BY {phase: $phase}]->(r)
RETURN r.id AS id
"""

LINK_TEST_RUN = """
UNWIND $rows AS row
MATCH (r:TestRun {id: $run_id}), (t:Test {id: row.test_id})
CREATE (r)-[:RAN {outcome: row.outcome}]->(t)
"""

# A fix whose regression test failed before the patch and passed after it is machine evidence that the
# root cause was right, so it is remembered the same way as a human confirmation.
CONFIRM_BY_VERIFICATION = """
MATCH (i:Investigation {id: $investigation_id}), (f:Function {id: $fn_id})
MERGE (i)-[c:CONFIRMED]->(f)
SET c.at = timestamp(), c.note = $note, c.source = 'verification'
SET i.status = 'confirmed'
RETURN f.qualified_name AS name
"""

LIST_FIXES = """
MATCH (:Investigation {id: $investigation_id})-[:PROPOSED]->(x:Fix)
RETURN x.id AS id, x.status AS status, x.payload AS payload
ORDER BY x.created_at DESC
"""

GET_FIX = """
MATCH (i:Investigation)-[:PROPOSED]->(x:Fix {id: $id})
RETURN i.id AS investigation_id, x.payload AS payload
"""

UPDATE_FIX = """
MATCH (x:Fix {id: $id}) SET x.payload = $payload, x.status = $status
"""

LOGINS_FOR = """
MATCH (p:Person) WHERE p.email IN $emails OR p.name IN $names
RETURN p.name AS name, p.email AS email, p.login AS login
"""

# A pull request the agent opened, linked to the fix it carries.
SAVE_OPENED_PR = """
MATCH (x:Fix {id: $fix_id})
MERGE (pr:PullRequest {id: $pr_id})
SET pr.number = $number, pr.title = $title, pr.url = $url, pr.state = 'open', pr.created_ms = timestamp(),
    pr.body = $body
MERGE (x)-[:OPENED_AS]->(pr)
"""

# Where a top-level function or class is defined, for adding an import a patch forgot.
DEFINITIONS_OF = """
MATCH (n) WHERE (n:Function OR n:Class) AND n.name IN $names AND n.file_path ENDS WITH '.py'
  AND NOT n.file_path CONTAINS 'test'
  AND (n:Class OR n.qualified_name = n.name)
RETURN n.name AS name, n.file_path AS file_path
"""
