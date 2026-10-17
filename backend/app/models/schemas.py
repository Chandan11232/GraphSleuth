from typing import Optional

from pydantic import BaseModel, Field


class RepositoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    # An https:// git URL, or a server-side directory when ALLOW_LOCAL_REPOS is on.
    url: str = Field(..., min_length=1, max_length=500)
    branch: Optional[str] = None
    github_token: Optional[str] = None


class InvestigationCreate(BaseModel):
    repository_id: str
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10, max_length=20000)
    use_llm: bool = True


class ConfirmRootCause(BaseModel):
    function_id: str
    note: str = ""


class BrainQuestion(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)
    use_llm: bool = True


class VerifyFix(BaseModel):
    # The corrected root-cause function (preferred), or a unified diff.
    patched_code: str = Field("", max_length=50000)
    diff: str = Field("", max_length=100000)
    # A regression test that should fail before the fix and pass after it.
    test_code: str = Field("", max_length=50000)
    test_path: str = Field("", max_length=300)


class OpenPullRequest(BaseModel):
    # A person must approve opening the pull request; the agent never does it on its own.
    approve: bool = False
    github_token: Optional[str] = None
