from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pykka
import pytest
from fastapi import FastAPI

from tests.utils.config_utils import ConfigUtils

_config_utils = ConfigUtils()


@pytest.fixture
def isolated_app_lifespan(monkeypatch: pytest.MonkeyPatch):
    """Disable production startup side effects for mocked unit API tests."""

    @asynccontextmanager
    async def no_lifespan(app: FastAPI):
        yield

    from gosmart_graphite.main import app

    monkeypatch.setattr(app.router, "lifespan_context", no_lifespan)


@pytest.fixture
def test_config(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Provide the test-specific configuration dictionary from tests/configs.json."""
    module_name = Path(request.path).stem
    func_name = request.node.originalname or request.node.name
    return _config_utils.get_config(func_name, module_name=module_name)


@pytest.fixture(scope="session", autouse=True)
def cleanup_pykka_actors():
    """Session-wide fixture ensuring all Pykka actors are cleanly stopped on test completion."""
    yield
    try:
        pykka.ActorRegistry.stop_all()
    except Exception:
        pass
