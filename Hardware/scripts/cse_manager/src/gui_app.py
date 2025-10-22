import dearpygui.dearpygui as dpg
from pathlib import Path
import os
import sys
from datetime import datetime
import subprocess
import zipfile
import tempfile
import re
import webbrowser
import json  # <-- ADDED FOR PERSISTENCE

# Import Tkinter for the native file dialog
import tkinter as tk
from tkinter import filedialog as fd
from sexpdata import loads, Symbol



FONT_SIZE = 18

# Cache of main symbols already in the project library
PROJECT_EXISTING_SYMBOLS = set()
# List of dictionaries storing data for each ZIP file in the UI
GUI_FILE_DATA = []
# Stores the complete history of log messages (for the copy function)
full_log_history = []

# --- Constants for Persistence ---
if getattr(sys, "frozen", False):
    # Running as .exe: store config next to the exe itself
    CONFIG_FILE = Path(sys.executable).resolve().parent / "gui_config.json"
else:
    # Running as .py
    CONFIG_FILE = Path(__file__).parent / "gui_config.json"

RENAME_ASSETS_KEY = "rename_assets_default"
# ---------------------------------

# Attempt to import necessary paths and functions from the library manager
try:
    from library_manager import INPUT_ZIP_FOLDER, get_existing_main_symbols

    CLI_SCRIPT_PATH = Path(__file__).parent / "cli_main.py"
except ImportError as e:
    # Fallback/Dummy paths and function if library_manager is not found
    INPUT_ZIP_FOLDER = Path.cwd()
    CLI_SCRIPT_PATH = Path.cwd() / "cli_main_dummy.py"

    def get_existing_main_symbols():
        return {"RESISTOR_1", "CAP_POL_SMD"}


# --- Constants for DPG Tags ---
WINDOW_WIDTH = 900
WINDOW_HEIGHT = 750
CURRENT_PATH_TAG = "current_path_text"
FILE_COUNT_TAG = "file_count_text"
FILE_CHECKBOXES_CONTAINER = "file_checkboxes_container"
SCROLL_FLAG_TAG = "scroll_flag_int"
ACTION_SECTION_TAG = "action_section_group"
LOG_TEXT_TAG = "log_text_container"
LOG_WINDOW_CHILD_TAG = "log_window_child"
FULL_LOG_POPUP_TAG = "full_log_popup"
FULL_LOG_TEXT_TAG = "full_log_text_area"
HYPERLINK_THEME_TAG = "hyperlink_theme"
# -------------------------


def find_font_recursively(font_name: str) -> Path | None:
    """
    Search recursively for a font file.
    Works for both source (.py) and frozen (.exe) modes.
    """
    # Determine base path depending on environment
    if getattr(sys, "frozen", False):
        # Running from PyInstaller bundle (.exe)
        base_dir = Path(sys.executable).resolve().parent
    else:
        # Running from source
        base_dir = Path(__file__).resolve().parent

    # Common fallback search paths
    search_paths = [
        base_dir,
        base_dir / "src",
        base_dir / "fonts",
        base_dir / "src" / "fonts",
    ]

    # Try each search path recursively
    for root in search_paths:
        if root.exists():
            for path in root.rglob(font_name):
                return path

    print(f"⚠️ Font '{font_name}' not found in {base_dir} or common subdirectories.")
    return None


def load_font_recursively(font_name: str, size: int = 18):
    """Try to load the font recursively; fallback to default if missing."""
    font_path = find_font_recursively(font_name)
    if not font_path:
        print(f"⚠️ Using default DearPyGui font (couldn't find '{font_name}')")
        return

    with dpg.font_registry():
        font = dpg.add_font(str(font_path), size)
        dpg.bind_font(font)
        print(f"✅ Loaded font: {font_path}")

    with dpg.font_registry():
        font = dpg.add_font(str(font_path), size)
        dpg.bind_font(font)
        print(f"✅ Loaded font: {font_path}")


# ===================================================
# --- PERSISTENCE LOGIC (NEW) ---
# ===================================================


def load_config() -> dict:
    """Loads the configuration dictionary from the JSON file."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            # Handle potential file corruption or format error
            return {}
    return {}


def save_config(key: str, value: any):
    """Saves a single key-value pair to the configuration file."""
    config = load_config()
    config[key] = value
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"ERROR: Could not save configuration to {CONFIG_FILE.name}: {e}")


def update_config_rename_checkbox(sender, app_data, user_data):
    """Callback to save the state of the rename checkbox."""
    save_config(RENAME_ASSETS_KEY, dpg.get_value(sender))


# ===================================================
# --- CORE LOGIC & EXECUTION ---
# ===================================================

def list_project_symbols():
    """Returns a list of all symbol names in the current ProjectSymbols.kicad_sym."""
    from library_manager import PROJECT_SYMBOL_LIB, SUB_PART_PATTERN
    if not PROJECT_SYMBOL_LIB.exists():
        return []

    try:
        with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
            content = f.read()
        sexp = loads(content)
    except Exception as e:
        print(f"ERROR reading symbol library: {e}")
        return []

    symbols = []
    for el in sexp[1:]:
        if isinstance(el, list) and len(el) > 1 and str(el[0]) == "symbol":
            name = str(el[1])
            base = SUB_PART_PATTERN.sub("", name)
            if base not in symbols:
                symbols.append(base)
    return symbols

def execute_library_action(paths, is_purge, rename_assets: bool):
    """
    Executes the CLI either as subprocess (in .py mode) or direct import (in .exe mode).
    """
    success = False
    output_lines = []

    running_as_exe = getattr(sys, "frozen", False)

    try:
        if running_as_exe:
            # Direct import avoids launching a 2nd EXE instance
            import cli_main
            from io import StringIO
            import contextlib

            # Capture output
            buffer = StringIO()
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                argv_backup = sys.argv
                sys.argv = ["cli_main", "purge" if is_purge else "process"]
                if rename_assets and not is_purge:
                    sys.argv.append("--rename-assets")
                sys.argv.extend([str(p) for p in paths])

                try:
                    cli_main.main()  # assuming your CLI script has main()
                    success = True
                except SystemExit as e:
                    success = e.code == 0
                finally:
                    sys.argv = argv_backup

            output_lines = buffer.getvalue().splitlines()

        else:
            # Normal subprocess for development (when not bundled)
            python_exe = sys.executable
            action_str = "purge" if is_purge else "process"
            cmd = [python_exe, str(CLI_SCRIPT_PATH), action_str]
            if rename_assets and not is_purge:
                cmd.append("--rename-assets")
            cmd.extend([str(p) for p in paths])

            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore"
            )
            output_lines = (result.stdout + result.stderr).splitlines()
            success = result.returncode == 0

    except Exception as e:
        output_lines = [f"CRITICAL ERROR: {e}"]
        success = False

    return success, "\n".join(output_lines)


def update_existing_symbols_cache():
    """
    Refreshes the global cache of symbols currently present in the project library
    by calling a function from `library_manager.py`.
    """
    global PROJECT_EXISTING_SYMBOLS
    try:
        PROJECT_EXISTING_SYMBOLS = get_existing_main_symbols()
        log_message(
            None,
            None,
            f"INFO: Updated existing symbol cache with {len(PROJECT_EXISTING_SYMBOLS)} symbols.",
            is_cli_output=False,
        )
    except Exception as e:
        log_message(
            None,
            None,
            f"[ERROR] Failed to load existing symbols: {e}",
            is_cli_output=False,
        )
        PROJECT_EXISTING_SYMBOLS = set()


def check_zip_for_existing_symbols(zip_paths: list[Path]):
    """
    Scans the provided ZIP files and checks if their names suggest they
    contain symbols already present in the project library.
    It populates the GUI_FILE_DATA list with status and tooltip info.
    """
    global GUI_FILE_DATA
    GUI_FILE_DATA.clear()

    if not zip_paths:
        return

    # Use a temporary directory for safe extraction/scanning, though we only read names here
    with tempfile.TemporaryDirectory() as temp_dir_name:
        for p in zip_paths:
            zip_data = {
                "path": p,
                "name": p.name,
                "status": "NEW",
                "tooltip": "No KiCad symbols found.",
            }
            try:
                with zipfile.ZipFile(p, "r") as zf:
                    # Check for the existence of symbol files within the ZIP
                    sym_files = [
                        name
                        for name in zf.namelist()
                        if name.lower().endswith(".kicad_sym")
                    ]
                    if not sym_files:
                        zip_data["status"] = "NONE"
                        zip_data["tooltip"] = (
                            "ZIP does not contain any .kicad_sym files."
                        )
                        GUI_FILE_DATA.append(zip_data)
                        continue

                    # Simple check: see if any existing symbol name is part of the ZIP filename
                    found_partial = False
                    for existing_sym in PROJECT_EXISTING_SYMBOLS:
                        if existing_sym.lower() in p.stem.lower():
                            zip_data["status"] = "PARTIAL"
                            zip_data["tooltip"] = (
                                f"Contains symbols (e.g. '{existing_sym}') already in library. Unchecked by default to prevent accidental override."
                            )
                            found_partial = True
                            break

                    if not found_partial:
                        zip_data["status"] = "NEW"
                        zip_data["tooltip"] = (
                            f"Contains {len(sym_files)} symbol file(s). Appears new."
                        )

            except Exception as e:
                log_message(
                    None,
                    None,
                    f"ERROR: Could not scan ZIP {p.name} for symbols: {e}",
                    is_cli_output=False,
                )
                zip_data["status"] = "ERROR"
                zip_data["tooltip"] = f"Could not scan: {e}"

            GUI_FILE_DATA.append(zip_data)


# ===================================================
# --- DPG UTILITIES ---
# ===================================================


def log_message(
    sender,
    app_data,
    user_data: str,
    add_timestamp: bool = True,
    is_cli_output: bool = False,
):
    """Adds a message to the DPG log window and the full log history."""
    global full_log_history
    if not user_data:
        dpg.add_text(" ", parent=LOG_TEXT_TAG, tag=dpg.generate_uuid())
        full_log_history.append("")
        return

    log_entry_full = user_data
    if add_timestamp:
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        log_entry_full = f"{timestamp} {user_data}"

    full_log_history.append(log_entry_full)

    # Determine theme based on log content
    theme_tag = "default_log_theme"
    user_data_upper = log_entry_full.upper()

    if is_cli_output:
        theme_tag = "cli_output_theme"
    elif (
        "[FAIL]" in user_data_upper
        or "[ERROR]" in user_data_upper
        or "CRITICAL ERROR" in user_data_upper
    ):
        theme_tag = "error_log_theme"
    elif "[OK]" in user_data_upper or "[SUCCESS]" in user_data_upper:
        theme_tag = "success_log_theme"

    # Create a non-editable input text item for the log entry
    new_text_item = dpg.add_input_text(
        default_value=log_entry_full,
        parent=LOG_TEXT_TAG,
        readonly=True,
        width=-1,
        tag=dpg.generate_uuid(),
    )
    dpg.bind_item_theme(new_text_item, theme_tag)

    # Force auto-scroll to the bottom of the log window
    current_scroll_value = dpg.get_value(SCROLL_FLAG_TAG)
    dpg.set_value(SCROLL_FLAG_TAG, current_scroll_value + 1)

    if dpg.does_item_exist(LOG_WINDOW_CHILD_TAG):
        dpg.set_y_scroll(LOG_WINDOW_CHILD_TAG, -1.0)


def clear_log(sender, app_data):
    """Clears the visual log and the log history cache."""
    global full_log_history
    dpg.delete_item(LOG_TEXT_TAG, children_only=True)
    full_log_history.clear()
    log_message(None, None, "Log cleared.", add_timestamp=True)
    log_message(None, None, "Ready.", add_timestamp=True)


def show_full_log_popup(sender, app_data):
    """Displays a modal window with the entire log history for easy copying."""
    global full_log_history
    if dpg.does_item_exist(FULL_LOG_POPUP_TAG):
        dpg.set_value(FULL_LOG_TEXT_TAG, "\n".join(full_log_history))
        dpg.show_item(FULL_LOG_POPUP_TAG)
        return

    with dpg.window(
        label="Full Log for Copying (No Colors)",
        modal=True,
        show=True,
        tag=FULL_LOG_POPUP_TAG,
        width=800,
        height=400,
    ):
        dpg.add_text(
            "This is the full, raw log. Use CTRL+A to select all and CTRL+C to copy."
        )
        dpg.add_separator()
        dpg.add_input_text(
            default_value="\n".join(full_log_history),
            multiline=True,
            readonly=True,
            width=-1,
            height=-1,
            tag=FULL_LOG_TEXT_TAG,
        )


def build_file_list_ui():
    """Generates the checkboxes and status indicators for all ZIP files in GUI_FILE_DATA."""
    global GUI_FILE_DATA
    dpg.delete_item(FILE_CHECKBOXES_CONTAINER, children_only=True)
    dpg.set_value(FILE_COUNT_TAG, f"Total files found: {len(GUI_FILE_DATA)}")

    if not GUI_FILE_DATA:
        with dpg.group(parent=FILE_CHECKBOXES_CONTAINER):
            dpg.add_text(
                "No ZIP files loaded. Select a folder to begin.", color=[255, 165, 0]
            )
        return

    with dpg.group(parent=FILE_CHECKBOXES_CONTAINER):
        for i, data in enumerate(GUI_FILE_DATA):
            tag = f"checkbox_{i}"
            status = data["status"]

            status_text = ""
            status_color = (200, 200, 200)
            is_new = True

            # Determine UI appearance and default check state based on scan status
            if status == "PARTIAL":
                status_text = "(Partial Match/Existing Symbols)"
                status_color = (255, 165, 0)
                is_new = False
            elif status == "NEW":
                status_text = "(New)"
                status_color = (0, 255, 0)
                is_new = True
            elif status == "ERROR":
                status_text = "(Error Scanning)"
                status_color = (255, 0, 0)
                is_new = False
            elif status == "NONE":
                status_text = "(No Symbols Found)"
                status_color = (150, 150, 150)
                is_new = False

            with dpg.group(horizontal=True):
                checkbox = dpg.add_checkbox(
                    label=data["name"], default_value=is_new, tag=tag
                )
                with dpg.tooltip(parent=checkbox):
                    dpg.add_text(data["tooltip"])
                dpg.add_text(status_text, color=status_color)


def toggle_all_checkboxes(sender, app_data, value):
    """Sets the check status of all file checkboxes."""
    global GUI_FILE_DATA
    for i in range(len(GUI_FILE_DATA)):
        tag = f"checkbox_{i}"
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, value)


def get_active_files_for_processing():
    """Returns a list of Path objects for all currently checked ZIP files."""
    global GUI_FILE_DATA
    active_paths = []
    for i, data in enumerate(GUI_FILE_DATA):
        tag = f"checkbox_{i}"
        if dpg.does_item_exist(tag) and dpg.get_value(tag):
            active_paths.append(data["path"])
    return active_paths


def process_action(sender, app_data, is_purge):
    """
    Handles button clicks for PROCESS/PURGE actions:
    1. Gathers checked files.
    2. Executes the CLI script.
    3. Prints output to the log.
    4. Refreshes the symbol cache and file list UI.
    """
    active_files = get_active_files_for_processing()
    if not active_files:
        log_message(None, None, "ERROR: No active ZIP files selected for action.")
        return

    # --- Get the state of the renaming checkbox ---
    rename_assets = False
    if not is_purge and dpg.does_item_exist("rename_assets_chk"):
        rename_assets = dpg.get_value("rename_assets_chk")
    # ----------------------------------------------------

    action_name = "PURGE" if is_purge else "PROCESS"
    log_message(
        None,
        None,
        f"--- Initiating {action_name} for {len(active_files)} active file(s) ---",
    )
    if not is_purge and rename_assets:
        log_message(None, None, "INFO: Renaming of Footprints/3D Models is ENABLED.")

    # Execute the library action in a subprocess, passing the new flag
    success, output = execute_library_action(
        active_files, is_purge=is_purge, rename_assets=rename_assets
    )

    # Stream the CLI output to the log window
    for line in output.splitlines():
        log_message(None, None, line, add_timestamp=False, is_cli_output=True)

    if success:
        log_message(None, None, f"[OK] {action_name} SUCCESSFUL. Refreshing display...")
        # Update cache and re-scan the currently loaded ZIPs to reflect changes
        update_existing_symbols_cache()
        current_zip_paths = [data["path"] for data in GUI_FILE_DATA]
        check_zip_for_existing_symbols(current_zip_paths)
        build_file_list_ui()
    else:
        log_message(None, None, f"[FAIL] {action_name} FAILED. See output above.")

    log_message(
        None,
        None,
        "------------------------------------------------------",
        add_timestamp=False,
    )
    log_message(None, None, "", add_timestamp=False)


def _init_tkinter_root():
    """Initializes and hides the Tkinter root window for the native dialog."""
    root = tk.Tk()
    root.withdraw()
    return root


def select_zip_folder():
    """Uses Tkinter's native dialog to select a folder and returns a list of contained ZIP files."""
    root = _init_tkinter_root()
    try:
        folder_path_str = fd.askdirectory(
            title="Select Folder Containing ZIP Archives",
            initialdir=str(INPUT_ZIP_FOLDER.resolve()),
        )
        if not folder_path_str:
            return []
        folder_path = Path(folder_path_str)
        # Find all .zip files in the selected directory
        zip_files = list(folder_path.glob("*.zip"))
        return zip_files
    finally:
        # Clean up the Tkinter window
        root.destroy()


def open_folder_in_explorer(sender, app_data):
    """Opens the currently displayed folder path in the OS file explorer/finder."""
    current_path_text = dpg.get_value(CURRENT_PATH_TAG)

    # Extract the actual path string from the DPG text value
    if not current_path_text.startswith("Current Folder: "):
        log_message(
            None,
            None,
            "ERROR: Could not determine current folder path.",
            is_cli_output=False,
        )
        return

    folder_path_str = current_path_text.replace("Current Folder: ", "")

    if not folder_path_str or folder_path_str.startswith("("):
        log_message(
            None,
            None,
            "ERROR: No valid folder path is currently set.",
            is_cli_output=False,
        )
        return

    folder_path = Path(folder_path_str)

    if not folder_path.exists() or not folder_path.is_dir():
        log_message(
            None,
            None,
            f"ERROR: Folder path does not exist: {folder_path_str}",
            is_cli_output=False,
        )
        return

    try:
        # Use appropriate command based on the operating system
        if sys.platform == "win32":
            os.startfile(folder_path_str)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder_path_str])
        else:
            subprocess.Popen(["xdg-open", folder_path_str])

        log_message(
            None,
            None,
            f"INFO: Opened folder in explorer: {folder_path_str}",
            is_cli_output=False,
        )

    except Exception as e:
        log_message(
            None,
            None,
            f"ERROR: Failed to open folder in explorer: {e}",
            is_cli_output=False,
        )

def open_output_folder(sender=None, app_data=None):
    """Reuses the existing open_folder_in_explorer() to open the output folder."""
    try:
        from library_manager import INPUT_ZIP_FOLDER
        output_folder = INPUT_ZIP_FOLDER.parent / "library_output"
        os.makedirs(output_folder, exist_ok=True)

        # Temporarily set the GUI label so open_folder_in_explorer() reads the right path
        dpg.set_value(CURRENT_PATH_TAG, f"Current Folder: {output_folder}")
        open_folder_in_explorer(sender, app_data)

        # Restore the previous path afterwards (optional)
        input_folder = INPUT_ZIP_FOLDER.resolve()
        dpg.set_value(CURRENT_PATH_TAG, f"Current Folder: {input_folder}")

    except Exception as e:
        log_message(None, None, f"ERROR: Could not open output folder: {e}")

def show_native_folder_dialog(sender, app_data):
    """Triggers the folder selection dialog and initiates UI reload if files are found."""
    paths = select_zip_folder()

    if not paths:
        log_message(
            None,
            None,
            "Folder selection cancelled or no ZIP files found. Retaining current folder view.",
        )
        return

    # A folder was selected and paths were found
    selected_folder_str = str(paths[0].parent.resolve())
    log_message(None, None, f"Found {len(paths)} ZIP file(s).")

    # Reload the UI with the newly selected folder's content
    reload_folder_from_path(selected_folder_str)


def refresh_file_list(sender, app_data):
    """
    Manually triggers a refresh of the file list by re-scanning the current folder path.
    """
    current_path_text = dpg.get_value(CURRENT_PATH_TAG)
    if not current_path_text.startswith("Current Folder: "):
        log_message(None, None, "ERROR: Cannot refresh. Current path is invalid.")
        return

    folder_path_str = current_path_text.replace("Current Folder: ", "")

    if folder_path_str.startswith("("):
        log_message(
            None, None, "ERROR: Cannot refresh. No valid folder path is currently set."
        )
        return

    log_message(None, None, f"Manually refreshing file list for: {folder_path_str}")
    reload_folder_from_path(folder_path_str)


def reload_folder_from_path(folder_path_str):
    """
    Performs the core logic of loading a folder:
    1. Updates the existing symbol cache.
    2. Scans the folder for ZIP files.
    3. Updates the UI text and checkboxes.
    """
    folder_path = Path(folder_path_str).resolve()
    if not folder_path.exists() or not folder_path.is_dir():
        log_message(None, None, f"ERROR: Folder not found at '{folder_path}'.")
        check_zip_for_existing_symbols([])
        build_file_list_ui()
        return
    try:
        update_existing_symbols_cache()
        paths = list(folder_path.glob("*.zip"))
        valid_paths = [p for p in paths if p.exists()]
        check_zip_for_existing_symbols(valid_paths)
        dpg.set_value(CURRENT_PATH_TAG, f"Current Folder: {folder_path.resolve()}")
        # Show the action buttons only if ZIP files were found
        if valid_paths:
            dpg.show_item(ACTION_SECTION_TAG)
        else:
            dpg.hide_item(ACTION_SECTION_TAG)
        build_file_list_ui()
    except Exception as e:
        log_message(None, None, f"ERROR scanning folder: {e}")
        check_zip_for_existing_symbols([])
        build_file_list_ui()


def initial_load():
    """Performs setup tasks and loads the default folder path on application startup."""
    update_existing_symbols_cache()
    target_folder = INPUT_ZIP_FOLDER.resolve()
    dpg.set_value(CURRENT_PATH_TAG, f"Current Folder: {target_folder}")
    if not target_folder.exists() or not target_folder.is_dir():
        log_message(
            None,
            None,
            f"ERROR: Input folder not found at '{target_folder}'. Skipping initial load.",
        )
        dpg.set_value(CURRENT_PATH_TAG, "Current Folder: (Path Error)")
        return
    log_message(None, None, f"Checking default folder: '{target_folder}'")
    try:
        paths = list(target_folder.glob("*.zip"))
        valid_paths = [p for p in paths if p.exists()]
    except Exception as e:
        log_message(None, None, f"ERROR scanning folder: {e}")
        valid_paths = []
    check_zip_for_existing_symbols(valid_paths)
    if valid_paths:
        log_message(
            None,
            None,
            f"Successfully loaded {len(valid_paths)} ZIP file(s) from default path.",
        )
        dpg.show_item(ACTION_SECTION_TAG)
    else:
        log_message(None, None, "No ZIP files found in the default folder.")
    build_file_list_ui()
    refresh_symbol_list()



def open_url(sender, app_data, url):
    """Opens a specified URL in the default web browser."""
    try:
        webbrowser.open_new_tab(url)
        log_message(None, None, f"INFO: Opened URL: {url}", is_cli_output=False)
    except Exception as e:
        log_message(
            None, None, f"ERROR: Failed to open web browser: {e}", is_cli_output=False
        )

def on_tab_change(sender, app_data, user_data):
    """Callback triggered when switching between ZIP and Symbol tabs."""
    active_tab_label = dpg.get_item_label(app_data)
    if active_tab_label == "ZIP Archives":
        dpg.show_item("zip_action_group")
        dpg.hide_item("symbol_action_group")
    elif active_tab_label == "Project Symbols":
        dpg.hide_item("zip_action_group")
        dpg.show_item("symbol_action_group")

def refresh_symbol_list():
    """Refreshes the list of symbols found in ProjectSymbols.kicad_sym."""
    symbols = list_project_symbols()
    dpg.delete_item("symbol_checkboxes_container", children_only=True)
    dpg.set_value("symbol_count_text", f"Total symbols found: {len(symbols)}")

    if not symbols:
        with dpg.group(parent="symbol_checkboxes_container"):
            dpg.add_text("No symbols found in ProjectSymbols.kicad_sym.", color=[255, 100, 100])
        return

    with dpg.group(parent="symbol_checkboxes_container"):
        for i, name in enumerate(symbols):
            tag = f"symbol_checkbox_{i}"
            dpg.add_checkbox(label=name, tag=tag, default_value=False)
            
# ===================================================
# --- GUI SETUP ---
# ===================================================

def create_gui():
    """Sets up the DearPyGui context, themes, and main window layout."""
    dpg.create_context()

    # --- Load Configuration for initial values ---
    config = load_config()
    rename_default = config.get(RENAME_ASSETS_KEY, False)

    load_font_recursively("NotoSans-Regular.ttf", size=FONT_SIZE)

    dpg.create_viewport(
        title="KiCad Library Manager",
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        resizable=True,
    )
    dpg.setup_dearpygui()

    # --- Theme setup ---
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (25, 25, 25))
    dpg.bind_theme(global_theme)

    # --- Log Color Themes ---
    def setup_log_theme(tag, color):
        with dpg.theme(tag=tag):
            with dpg.theme_component(dpg.mvInputText):
                dpg.add_theme_color(dpg.mvThemeCol_Text, color)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (0, 0, 0, 0))

    setup_log_theme("default_log_theme", (200, 200, 200))
    setup_log_theme("cli_output_theme", (140, 140, 140))
    setup_log_theme("error_log_theme", (255, 50, 50))
    setup_log_theme("success_log_theme", (0, 255, 0))

    # --- Hyperlink Theme ---
    with dpg.theme(tag=HYPERLINK_THEME_TAG):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (30, 30, 50, 50))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (150, 150, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0)

    # --- Main Window Layout ---
    with dpg.window(
        tag="main_window",
        label="KiCad Library Manager",
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
    ):
        dpg.set_primary_window("main_window", True)

        dpg.add_text("1. Select Archive Folder (ZIPs will be scanned automatically):", color=[0, 255, 255])
        with dpg.group(horizontal=True):
            dpg.add_button(label="Select ZIP-Folder", callback=show_native_folder_dialog)
            dpg.add_button(label=f"Open ZIP-Folder", callback=open_folder_in_explorer)

        dpg.add_text("Current Folder: (Initializing...)", tag=CURRENT_PATH_TAG, wrap=0, color=[150, 150, 255])
        dpg.add_separator()

        dpg.add_text("2. Select Input Source:", color=[255, 255, 0])

        # --- Tab Bar ---
        with dpg.tab_bar(tag="source_tab_bar", callback=on_tab_change):
            # ZIP Archive Tab
            with dpg.tab(label="ZIP Archives", tag="zip_tab"):
                with dpg.group(horizontal=True):
                    dpg.add_text("Total ZIP files found: 0", tag=FILE_COUNT_TAG, color=[0, 255, 0])
                    dpg.add_button(label="Refresh ZIPs", callback=refresh_file_list, small=True)
                with dpg.child_window(tag=FILE_CHECKBOXES_CONTAINER, width=-1, height=180, border=True):
                    pass

            # Project Symbols Tab
            with dpg.tab(label="Project Symbols", tag="symbol_tab"):
                with dpg.group(horizontal=True):
                    dpg.add_text("Total symbols found: 0", tag="symbol_count_text", color=[0, 255, 0])
                    dpg.add_button(label="Refresh Symbols", callback=lambda s,a: refresh_symbol_list(), small=True)
                with dpg.child_window(tag="symbol_checkboxes_container", width=-1, height=180, border=True):
                    pass

        # --- Action Section ---
        with dpg.group(tag=ACTION_SECTION_TAG, show=False):
            # ZIP Actions
            with dpg.group(tag="zip_action_group", horizontal=True, horizontal_spacing=20):
                dpg.add_button(
                    label="PROCESS / IMPORT",
                    tag="process_btn",
                    callback=lambda s, a: process_action(s, a, False),
                    width=200,
                )
                dpg.add_button(
                    label="PURGE / DELETE",
                    tag="purge_btn",
                    callback=lambda s, a: process_action(s, a, True),
                    width=200,
                )

            # Symbol Actions
            with dpg.group(tag="symbol_action_group", horizontal=True, horizontal_spacing=20, show=False):
                dpg.add_button(
                    label="EXPORT SELECTED",
                    tag="export_btn",
                    callback=lambda s, a: export_action(s, a),
                    width=200,
                )
                dpg.add_button(
                    label="OPEN OUTPUT FOLDER",
                    tag="open_output_btn",
                    callback=open_output_folder,
                    width=200,
                )

            dpg.add_text("NOTE: Only checked files will be used.")
            dpg.add_separator()

        # --- Log Section ---
        with dpg.group(horizontal=True):
            dpg.add_text("CLI Output Log:")
            dpg.add_button(label="Clear Log", callback=clear_log, small=True)
            dpg.add_button(label="Show Full Log", callback=show_full_log_popup, small=True)

        with dpg.child_window(tag=LOG_WINDOW_CHILD_TAG, width=-1, height=150, border=True):
            dpg.add_group(tag=LOG_TEXT_TAG, width=-1)

        dpg.add_input_int(tag=SCROLL_FLAG_TAG, default_value=0, show=False)
        dpg.add_separator()

        with dpg.group(horizontal=True):
            author_link = dpg.add_button(label="By: Ihysol (Tobias Gent)", callback=lambda s, a: open_url(s, a, "https://github.com/Ihysol"), small=True)
            dpg.bind_item_theme(author_link, HYPERLINK_THEME_TAG)
            dpg.add_spacer(width=5)
            issues_link = dpg.add_button(label="Report Bug / Suggest Feature", callback=lambda s, a: open_url(s, a, "https://github.com/Ihysol/kicad-template"), small=True)
            dpg.bind_item_theme(issues_link, HYPERLINK_THEME_TAG)

    dpg.show_viewport()
    initial_load()
    dpg.start_dearpygui()
    dpg.destroy_context()
    
    
def export_action(sender, app_data):
    """Exports selected project symbols (only if both symbol + footprint exist)."""
    from library_manager import (
        export_symbols,
        PROJECT_SYMBOL_LIB,
        PROJECT_FOOTPRINT_LIB,
    )
    from sexpdata import loads, Symbol

    active_tab = dpg.get_item_label(dpg.get_value("source_tab_bar"))
    if active_tab != "Project Symbols":
        log_message(None, None, "Export is only available in the Project Symbols tab.")
        return

    # --- Collect selected symbols ---
    selected_symbols = []
    children = dpg.get_item_children("symbol_checkboxes_container", 1)
    if children:
        for group in children:
            for child in dpg.get_item_children(group, 1):
                if (
                    dpg.get_item_type(child) == "mvAppItemType::mvCheckbox"
                    and dpg.get_value(child)
                ):
                    selected_symbols.append(dpg.get_item_label(child))

    if not selected_symbols:
        log_message(None, None, "[WARN] No symbols selected for export.")
        return

    # --- Parse ProjectSymbols.kicad_sym to resolve footprint field ---
    try:
        with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
            content = f.read()
        sexp = loads(content)
    except Exception as e:
        log_message(None, None, f"[FAIL] Could not read symbol library: {e}")
        return

    # Build a dictionary: {symbol_name: footprint_name}
    symbol_footprints = {}
    for el in sexp[1:]:
        if isinstance(el, list) and len(el) > 1 and str(el[0]) == "symbol":
            sym_name = str(el[1])
            footprint_field = None
            for item in el:
                if isinstance(item, list) and len(item) >= 2 and str(item[0]) == "property":
                    if len(item) > 2 and str(item[1]) == "Footprint":
                        footprint_field = str(item[2])
                        break
            if footprint_field:
                symbol_footprints[sym_name] = footprint_field

    valid_symbols = []
    missing_footprints = []

    # --- Resolve and verify footprints ---
    for sym in selected_symbols:
        # Try both LIB_ and non-prefixed
        match_candidates = [sym, f"LIB_{sym}"]

        footprint_name = None
        for candidate in match_candidates:
            if candidate in symbol_footprints:
                footprint_name = symbol_footprints[candidate]
                break

        if not footprint_name:
            missing_footprints.append(sym)
            continue

        # Extract only the footprint file name (strip library prefix if any)
        footprint_basename = footprint_name.split(":")[-1]

        # Search recursively inside ProjectFootprints.pretty
        found_fp = None
        for fp in PROJECT_FOOTPRINT_LIB.rglob("*.kicad_mod"):
            if fp.stem == footprint_basename:
                found_fp = fp
                break

        if found_fp:
            valid_symbols.append(sym)
        else:
            missing_footprints.append(sym)

    # --- Logging results ---
    if not valid_symbols:
        log_message(None, None, "[FAIL] No valid symbols found (missing or unresolved footprints).")
        return

    if missing_footprints:
        log_message(
            None,
            None,
            f"[WARN] Skipping {len(missing_footprints)} symbol(s) missing or unresolved footprints: "
            f"{', '.join(missing_footprints)}",
        )

    # --- Export ---
    export_paths = export_symbols(valid_symbols)
    if export_paths:
        log_message(None, None, f"[OK] Exported {len(export_paths)} ZIP file(s) successfully.")
        # Each entry is a Path; show directory info safely
        output_dir = export_paths[0].parent if hasattr(export_paths[0], "parent") else None
        if output_dir:
            log_message(None, None, f"[OK] Output directory: {output_dir}")
        else:
            log_message(None, None, "[WARN] Could not determine output directory.")
    else:
        log_message(None, None, "[FAIL] Export returned no files.")


if __name__ == "__main__":
    # Check for required dependencies before starting the GUI
    try:
        import dearpygui.dearpygui as dpg
    except ImportError:
        print(
            "Error: DearPyGui is not installed. Please install it: pip install dearpygui"
        )
        sys.exit(1)

    try:
        import tkinter as tk
    except ImportError:
        print(
            "Error: tkinter is required for the native file dialog but is not available."
        )
        sys.exit(1)

    create_gui()
