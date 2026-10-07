import pykka
import pytest


@pytest.fixture(scope="session", autouse=True)
def cleanup_pykka_actors():
    """Session-wide fixture ensuring all Pykka actors are cleanly stopped on test completion."""
    yield
    try:
        pykka.ActorRegistry.stop_all()
    except Exception:
        pass
