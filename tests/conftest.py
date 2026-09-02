"""pytest fixtures + loaders for the hyphen-named scripts in bin/.

Note: the spotdl-era scripts (audit/redownload/dedup) and their fixtures were
retired in 0.6.0 — the missing-only batch driver plus the verifier made that
side pipeline obsolete.

The bin/*.py files have hyphens in their names (so they're invokable as CLI
commands), which makes them un-importable via plain `import migrate-to-flat`.
We use importlib to load them as modules for testing instead.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin"


def _load(name: str, file_name: str):
    """Load a hyphen-named .py file under bin/ as a module."""
    sys.path.insert(0, str(BIN))
    spec = importlib.util.spec_from_file_location(name, BIN / file_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[name] = module
    return module


@pytest.fixture(scope="session")
def verify():
    return _load("verify_and_cleanup", "verify-and-cleanup.py")


@pytest.fixture(scope="session")
def migrate():
    return _load("migrate_to_flat", "migrate-to-flat.py")
