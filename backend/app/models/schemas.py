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
