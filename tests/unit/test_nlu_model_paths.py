from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "file_path",
    [
        ".env.example",
        ".github/workflows/ci.yml",
        "devop-scripts/nlu-train.sh",
        "docker/Dockerfile",
        "src/gosmart_graphite/service/search_service.py",
    ],
)
def test_nlu_bundle_paths_use_renamed_directory(file_path):
    root = Path(__file__).resolve().parents[2]
    contents = (root / file_path).read_text(encoding="utf-8")
    assert "nlu_models/intent_models.zip" in contents
    assert "--out models/intent_models.zip" not in contents
