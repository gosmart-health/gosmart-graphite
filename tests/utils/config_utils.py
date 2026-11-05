import json
from pathlib import Path
from typing import Any


class ConfigUtils:
    """Helper utility to load test configuration fixtures and evaluate numeric comparisons."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        path = Path(config_path) if config_path is not None else Path(__file__).resolve().parent.parent / "configs.json"
        with path.open("r", encoding="utf-8") as f:
            self.configs: dict[str, Any] = json.load(f)

    def get_config(self, key: str, module_name: str | None = None) -> Any:
        if module_name and isinstance(self.configs.get(module_name), dict) and key in self.configs[module_name]:
            return self.configs[module_name][key]
        if key in self.configs:
            return self.configs[key]
        for section in self.configs.values():
            if isinstance(section, dict) and key in section:
                return section[key]
        raise KeyError(f"Config key not found: {key}")

    @staticmethod
    def compare(val: int | float, target: str) -> bool:
        target_str = target.strip()
        if target_str.startswith("ge"):
            return val >= float(target_str[2:])
        elif target_str.startswith("le"):
            return val <= float(target_str[2:])
        elif target_str.startswith("eq"):
            return val == float(target_str[2:])
        elif target_str.startswith("gt"):
            return val > float(target_str[2:])
        elif target_str.startswith("lt"):
            return val < float(target_str[2:])
        else:
            raise ValueError(f"Invalid target: {target}")
