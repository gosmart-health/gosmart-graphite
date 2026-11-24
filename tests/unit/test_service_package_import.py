"""Regression tests ensuring first-party packages import cleanly in a fresh interpreter.

The service and actor packages form an import graph that is only safe when no
module imports package-level attributes of ``gosmart_graphite.service`` while
the package ``__init__`` is still executing (which raises
``ImportError: ... from partially initialized module``). These tests run each
import in a fresh interpreter process so partially-initialized-module cycles
cannot be masked by a lucky import order in the pytest process.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _import_in_fresh_interpreter(code: str) -> None:
    """Execute the given import statements in a fresh Python subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=120,
    )
    assert result.returncode == 0, f"Import failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_service_package_imports_cleanly() -> None:
    _import_in_fresh_interpreter(
        "import gosmart_graphite.service\n"
        "from gosmart_graphite.service import EncRelevantResources, FhirService\n"
        "from gosmart_graphite.service import EmbeddingService, SimilarityMetric, llm_config_service\n"
        "from gosmart_graphite.service import DEFAULT_NLU_MODEL_PATH, ParsedUtterance, SearchService, search_service\n"
        "from gosmart_graphite.model import SearchIncludeLabel, SearchResult\n"
    )


def test_actor_package_imports_cleanly() -> None:
    _import_in_fresh_interpreter(
        "import gosmart_graphite.actor\nfrom gosmart_graphite.actor import FhirActor, notify_fhir\n"
    )


def test_notebook_first_cell_import_order_succeeds() -> None:
    """Mirror the exact import order used by notebooks/vector_emdedding_demo.ipynb cell 1."""
    _import_in_fresh_interpreter(
        "from gosmart_graphite.core.config import Settings\n"
        "from gosmart_graphite.model.llm_config import LLM_Config\n"
        "from gosmart_graphite.service.embedding_service import EmbeddingService, SimilarityMetric\n"
        "from gosmart_graphite.service.llm_config_service import llm_config_service\n"
    )
