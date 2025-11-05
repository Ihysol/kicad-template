import json
from pathlib import Path

CONFIG_FILE = Path("gui_config.json")
DEFAULT_CONFIG = {"use_symbol_name_for_footprint": False}


def load_gui_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_gui_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)
