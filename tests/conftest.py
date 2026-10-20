import os

# Tests must never call a real provider, even when a developer's .env selects one.
os.environ["TRIAGE_LLM_PROVIDER"] = "mock"
