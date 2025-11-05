# main_gui.py
"""
Launcher entry point for the KiCad Library Manager GUI.
Performs dependency checks and starts the DearPyGui interface.
"""

import sys

def main():
    # --- dependency check ---
    try:
        import dearpygui.dearpygui as dpg  # noqa: F401
    except ImportError:
        print("Error: DearPyGui is not installed. Please install it with:")
        print("       pip install dearpygui")
        sys.exit(1)

    try:
        import tkinter as tk  # noqa: F401
    except ImportError:
        print("Error: tkinter is required for the native file dialog but is missing.")
        print("Please install a Python build that includes tkinter.")
        sys.exit(1)

    # --- start GUI ---
    from gui_ui import create_gui
    create_gui()


if __name__ == "__main__":
    main()
