# config_manager.py

import json
from pathlib import Path

# Define the path to the configuration file (will be created next to the script)
CONFIG_FILE = Path('gui_config.json')

# Default state if the config file doesn't exist
DEFAULT_CHECKBOX_STATE = False 


def load_gui_config():
    """
    Loads the last saved state of the GUI controls from the config file.
    Returns the boolean state for the 'rename_assets' checkbox.
    """
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                config_data = json.load(f)
                return config_data.get("rename_assets", DEFAULT_CHECKBOX_STATE)
        except json.JSONDecodeError:
            print(f"Warning: Could not read JSON from {CONFIG_FILE}. Using default state.")
            return DEFAULT_CHECKBOX_STATE
    
    return DEFAULT_CHECKBOX_STATE


def save_gui_config(rename_assets_state: bool):
    """
    Saves the current state of the GUI controls to the config file.
    
    rename_assets_state: The current boolean value of the checkbox.
    """
    try:
        config_data = {}
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                try:
                    config_data = json.load(f)
                except json.JSONDecodeError:
                    pass
        
        config_data["rename_assets"] = rename_assets_state
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
        
    except Exception as e:
        print(f"Error saving configuration to {CONFIG_FILE}: {e}")