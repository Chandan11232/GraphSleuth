import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("FALKORDB_HOST", "localhost")
os.environ.setdefault("FALKORDB_PORT", "6379")
os.environ["LLM_PROVIDER"] = "none"
os.environ["REGISTRY_GRAPH"] = f"cd_registry_test_{uuid.uuid4().hex[:8]}"

DEMO_SCRIPT = Path(__file__).resolve().parents[2] / "demo" / "create_demo_repo.py"


def falkordb_available() -> bool:
    from app.core.falkordb import get_falkordb
    try:
        return get_falkordb().ping()
    except Exception:
        return False


requires_falkordb = pytest.mark.skipif(not falkordb_available(), reason="FalkorDB is not reachable")


@pytest.fixture(scope="session")
def demo_repo(tmp_path_factory) -> str:
    target = tmp_path_factory.mktemp("demo") / "acme-shop"
    subprocess.run([sys.executable, str(DEMO_SCRIPT), str(target)], check=True, capture_output=True)
    return str(target)
