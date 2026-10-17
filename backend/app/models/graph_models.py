"""The graph data model. Each repository lives in its own FalkorDB graph."""
from enum import Enum
from typing import List


class NodeType(str, Enum):
    REPOSITORY = "Repository"
    FILE = "File"
    CLASS = "Class"
    FUNCTION = "Function"
    TEST = "Test"
    COMMIT = "Commit"
    PERSON = "Person"
    # Agent memory
    BUG_REPORT = "BugReport"
    INVESTIGATION = "Investigation"
    # Company knowledge
    TEAM = "Team"
    ISSUE = "Issue"
    PULL_REQUEST = "PullRequest"
    COMMENT = "Comment"
    DECISION = "Decision"


class EdgeType(str, Enum):
    CONTAINS = "CONTAINS"        # Repository->File, File->Function|Class|Test
    HAS_METHOD = "HAS_METHOD"    # Class->Function
    CALLS = "CALLS"              # Function->Function
    IMPORTS = "IMPORTS"          # File->File
    TESTS = "TESTS"              # Test->Function
    MODIFIES = "MODIFIES"        # Commit->File
    TOUCHED = "TOUCHED"          # Commit->Function (hunk overlaps the function)
    AUTHORED = "AUTHORED"        # Person->Commit
    # Agent memory
    MENTIONS = "MENTIONS"              # BugReport->Function
    INVESTIGATED_IN = "INVESTIGATED_IN"  # BugReport->Investigation
    SUSPECTS = "SUSPECTS"              # Investigation->Function
    CONFIRMED = "CONFIRMED"            # Investigation->Function (human feedback)
    # Company knowledge
    MEMBER_OF = "MEMBER_OF"      # Person->Team
    OWNS = "OWNS"                # Team|Person->File (CODEOWNERS)
    OPENED = "OPENED"            # Person->Issue|PullRequest
    REVIEWED = "REVIEWED"        # Person->PullRequest
    WROTE = "WROTE"              # Person->Comment
    ON = "ON"                    # Comment->Issue|PullRequest
    ABOUT = "ABOUT"              # Comment->File, Decision->Function
    FIXES = "FIXES"              # PullRequest->Issue (closing keywords)
    REFERENCES = "REFERENCES"    # Issue|PullRequest|Comment->Issue|PullRequest (#123)
    INCLUDES = "INCLUDES"        # PullRequest->Commit
    DECIDED_IN = "DECIDED_IN"    # Decision->Issue|PullRequest|Comment
    DECIDED = "DECIDED"          # Person->Decision


INDEX_QUERIES: List[str] = [
    "CREATE INDEX FOR (f:Function) ON (f.id)",
    "CREATE INDEX FOR (f:Function) ON (f.name)",
    "CREATE INDEX FOR (f:File) ON (f.path)",
    "CREATE INDEX FOR (c:Class) ON (c.id)",
    "CREATE INDEX FOR (t:Test) ON (t.id)",
    "CREATE INDEX FOR (c:Commit) ON (c.sha)",
    "CREATE INDEX FOR (p:Person) ON (p.email)",
    "CREATE INDEX FOR (b:BugReport) ON (b.id)",
    "CREATE INDEX FOR (i:Investigation) ON (i.id)",
    "CREATE INDEX FOR (p:Person) ON (p.login)",
    "CREATE INDEX FOR (t:Team) ON (t.name)",
    "CREATE INDEX FOR (i:Issue) ON (i.number)",
    "CREATE INDEX FOR (pr:PullRequest) ON (pr.number)",
    "CREATE INDEX FOR (c:Comment) ON (c.id)",
    "CREATE INDEX FOR (d:Decision) ON (d.id)",
    # Full-text search over split identifiers, path words and docstrings.
    "CREATE FULLTEXT INDEX FOR (f:Function) ON (f.tokens, f.doc, f.body_tokens)",
    "CREATE FULLTEXT INDEX FOR (i:Investigation) ON (i.keywords_text, i.title)",
    "CREATE FULLTEXT INDEX FOR (i:Issue) ON (i.title, i.body)",
    "CREATE FULLTEXT INDEX FOR (pr:PullRequest) ON (pr.title, pr.body)",
    "CREATE FULLTEXT INDEX FOR (c:Comment) ON (c.body)",
    "CREATE FULLTEXT INDEX FOR (d:Decision) ON (d.text)",
]


def get_node_labels() -> List[str]:
    return [nt.value for nt in NodeType]


def get_edge_types() -> List[str]:
    return [et.value for et in EdgeType]
