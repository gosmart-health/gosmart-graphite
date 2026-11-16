"""Administrative and operational CLI package for Gosmart.Health GRAPHITE."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

cli_app: Any = None

_cli_file = Path(__file__).resolve().parent.parent / "cli.py"
if _cli_file.exists():
    _spec = spec_from_file_location("_gosmart_cli_module", _cli_file)
    if _spec and _spec.loader:
        _mod = module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        cli_app = getattr(_mod, "cli_app", None)

__all__ = ["cli_app"]
