import os
from pathlib import Path

import pytest

# Tests must never call a real provider, even when a developer's .env selects one.
os.environ["TRIAGE_LLM_PROVIDER"] = "mock"


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    # The lab tests start red on purpose; run them only when asked for by name.
    if collection_path.name == "test_lab.py":
        return not any("test_lab" in str(arg) for arg in config.args)
    return None
