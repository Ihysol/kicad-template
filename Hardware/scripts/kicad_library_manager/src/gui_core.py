# gui_core.py
"""
Core logic for the KiCad Library Manager GUI:
- config persistence
- log aggregation / theming hooks
- ZIP/symbol scanning
- CLI invocation
- DRC updater
- misc helpers shared by UI and launcher

This file has NO DearPyGui layout code.
"""

from __future__ import annotations

import os
import sys
import json
import re
import shutil
import zipfile
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Set

import tkinter as tk
from tkinter import filedialog as fd

from sexpdata import loads, Symbol

# =========================
# Globals mirrored from original
# =========================

FONT_SIZE = 18

# cache of main symbols already in the project library
PROJECT_EXISTING_SYMBOLS: Set[str] = set()

# GUI_FILE_DATA: one dict per zip row (path, status, tooltip...)
GUI_FILE_DATA: List[Dict[str, Any]] = []

# full log buffer for popup
full_log_history: List[str] = []

# runtime-provided DearPyGui tags (UI defines these; we just reference them)
CURRENT_PATH_TAG = "current_path_text"
FILE_COUNT_TAG = "file_count_text"
FILE_CHECKBOXES_CONTAINER = "file_checkboxes_container"
SCROLL_FLAG_TAG = "scroll_flag_int"
LOG_TEXT_TAG = "log_text_container"
LOG_WINDOW_CHILD_TAG = "log_window_child"
FULL_LOG_POPUP_TAG = "full_log_popup"
FULL_LOG_TEXT_TAG = "full_log_text_area"

# ---------------------------
# CONFIG / persistence
# ---------------------------

if getattr(sys, "frozen", False):
    CONFIG_FILE = Path(sys.executable).resolve().parent / "gui_config.json"
else:
    CONFIG_FILE = Path(__file__).parent / "gui_config.json"

RENAME_ASSETS_KEY = "rename_assets_default"


def load_config() -> Dict[str, Any]:
    """Load persisted GUI settings (checkbox state, etc)."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # corrupted config? act like empty
            return {}
    return {}


def save_config(key: str, value: Any) -> None:
    """Store one key/value back to disk."""
    cfg = load_config()
    cfg[key] = value
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"ERROR: Could not save configuration to {CONFIG_FILE.name}: {e}")


# ---------------------------
# Library-manager hooks
# ---------------------------

try:
    # happy path: run inside template with library_manager available
    from library_manager import (
        INPUT_ZIP_FOLDER,
        PROJECT_SYMBOL_LIB,
        PROJECT_FOOTPRINT_LIB,
        get_existing_main_symbols,
    )

    CLI_SCRIPT_PATH = Path(__file__).parent / "cli_main.py"

except ImportError:
    # fallback so GUI can still launch in isolation
    INPUT_ZIP_FOLDER = Path.cwd()
    PROJECT_SYMBOL_LIB = Path.cwd() / "ProjectSymbols.kicad_sym"
    PROJECT_FOOTPRINT_LIB = Path.cwd() / "ProjectFootprints.pretty"

    CLI_SCRIPT_PATH = Path.cwd() / "cli_main_dummy.py"

    def get_existing_main_symbols() -> Set[str]:
        return {"RESISTOR_1", "CAP_POL_SMD"}


# ---------------------------
# DearPyGui-light helpers
# ---------------------------


def dpg_safe_get_value(dpg, tag: str, default=None):
    """Read a DearPyGui value defensively."""
    try:
        return dpg.get_value(tag)
    except Exception:
        return default


def dpg_safe_set_value(dpg, tag: str, value) -> None:
    """Write a DearPyGui value defensively."""
    try:
        dpg.set_value(tag, value)
    except Exception:
        pass


def dpg_safe_item_label(dpg, tag: str, fallback: str = "") -> str:
    try:
        return dpg.get_item_label(tag)
    except Exception:
        return fallback


# ---------------------------
# LOGGING (uses DearPyGui at runtime)
# ---------------------------


def log_message(
    dpg,
    sender,
    app_data,
    user_data: str,
    add_timestamp: bool = True,
    is_cli_output: bool = False,
):
    """
    Append a line to:
    - the scrolling GUI log (colored input_text rows)
    - the full_log_history buffer (plain text)
    Behavior unchanged from original.
    """
    global full_log_history

    if not user_data:
        if dpg.does_item_exist(LOG_TEXT_TAG):
            dpg.add_text(" ", parent=LOG_TEXT_TAG, tag=dpg.generate_uuid())
        full_log_history.append("")
        return

    log_entry_full = user_data
    if add_timestamp:
        ts = datetime.now().strftime("[%H:%M:%S]")
        log_entry_full = f"{ts} {user_data}"

    full_log_history.append(log_entry_full)

    # pick theme based on content
    theme_tag = "default_log_theme"
    upper = log_entry_full.upper()
    if is_cli_output:
        theme_tag = "cli_output_theme"
    elif "[FAIL]" in upper or "[ERROR]" in upper or "CRITICAL ERROR" in upper:
        theme_tag = "error_log_theme"
    elif "[OK]" in upper or "[SUCCESS]" in upper:
        theme_tag = "success_log_theme"

    # show line in GUI log pane
    if dpg.does_item_exist(LOG_TEXT_TAG):
        new_item = dpg.add_input_text(
            default_value=log_entry_full,
            parent=LOG_TEXT_TAG,
            readonly=True,
            width=-1,
            tag=dpg.generate_uuid(),
        )
        dpg.bind_item_theme(new_item, theme_tag)

    # bump scroll flag (UI will auto-scroll)
    if dpg.does_item_exist(SCROLL_FLAG_TAG):
        cur = dpg.get_value(SCROLL_FLAG_TAG)
        dpg.set_value(SCROLL_FLAG_TAG, cur + 1)

    if dpg.does_item_exist(LOG_WINDOW_CHILD_TAG):
        dpg.set_y_scroll(LOG_WINDOW_CHILD_TAG, -1.0)


def clear_log(dpg, sender=None, app_data=None):
    """Wipe log window + buffer and re-seed standard messages."""
    global full_log_history
    if dpg.does_item_exist(LOG_TEXT_TAG):
        dpg.delete_item(LOG_TEXT_TAG, children_only=True)
    full_log_history.clear()
    log_message(dpg, None, None, "Log cleared.", add_timestamp=True)
    log_message(dpg, None, None, "Ready.", add_timestamp=True)


def show_full_log_popup(dpg, sender=None, app_data=None):
    """
    Open (or update + re-show) the modal 'full log' popup
    with raw text for copy/paste.
    """
    global full_log_history
    big_text = "\n".join(full_log_history)

    if dpg.does_item_exist(FULL_LOG_POPUP_TAG):
        dpg.set_value(FULL_LOG_TEXT_TAG, big_text)
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
            default_value=big_text,
            multiline=True,
            readonly=True,
            width=-1,
            height=-1,
            tag=FULL_LOG_TEXT_TAG,
        )


# ---------------------------
# KiCad symbol access / cache
# ---------------------------


def list_project_symbols() -> List[str]:
    """
    Parse ProjectSymbols.kicad_sym and return unique 'main' symbols.
    Pulls SUB_PART_PATTERN from library_manager if possible.
    """
    try:
        from library_manager import SUB_PART_PATTERN
    except ImportError:
        SUB_PART_PATTERN = re.compile(r"_\d(_\d)+$|_\d$")

    if not PROJECT_SYMBOL_LIB.exists():
        return []

    try:
        with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
            sexp = loads(f.read())
    except Exception as e:
        print(f"ERROR reading symbol library: {e}")
        return []

    symbols: List[str] = []
    for el in sexp[1:]:
        if isinstance(el, list) and len(el) > 1 and str(el[0]) == "symbol":
            name = str(el[1])
            base = SUB_PART_PATTERN.sub("", name)
            if base not in symbols:
                symbols.append(base)
    return symbols


def update_existing_symbols_cache(dpg):
    """
    Refresh PROJECT_EXISTING_SYMBOLS via library_manager.get_existing_main_symbols()
    and emit a log line.
    """
    global PROJECT_EXISTING_SYMBOLS
    try:
        PROJECT_EXISTING_SYMBOLS = get_existing_main_symbols()
        log_message(
            dpg,
            None,
            None,
            f"INFO: Updated existing symbol cache with {len(PROJECT_EXISTING_SYMBOLS)} symbols.",
            is_cli_output=False,
        )
    except Exception as e:
        PROJECT_EXISTING_SYMBOLS = set()
        log_message(
            dpg,
            None,
            None,
            f"[ERROR] Failed to load existing symbols: {e}",
            is_cli_output=False,
        )


# ---------------------------
# ZIP scanning
# ---------------------------


def check_zip_for_existing_symbols(zip_paths: List[Path]):
    """
    Populate GUI_FILE_DATA with info about each ZIP:
    - status: NEW / PARTIAL / NONE / ERROR
    - tooltip: explanation
    Mirrors original behavior.
    """
    global GUI_FILE_DATA, PROJECT_EXISTING_SYMBOLS
    GUI_FILE_DATA.clear()

    if not zip_paths:
        return

    with tempfile.TemporaryDirectory() as _tmp:
        for p in zip_paths:
            row = {
                "path": p,
                "name": p.name,
                "status": "NEW",
                "tooltip": "No KiCad symbols found.",
            }

            try:
                with zipfile.ZipFile(p, "r") as zf:
                    sym_files = [
                        n for n in zf.namelist() if n.lower().endswith(".kicad_sym")
                    ]

                    if not sym_files:
                        row["status"] = "NONE"
                        row["tooltip"] = "ZIP does not contain any .kicad_sym files."
                        GUI_FILE_DATA.append(row)
                        continue

                    # naive heuristic: does the ZIP filename contain
                    # an already-existing symbol name?
                    found_partial = False
                    for existing_sym in PROJECT_EXISTING_SYMBOLS:
                        if existing_sym.lower() in p.stem.lower():
                            row["status"] = "PARTIAL"
                            row["tooltip"] = (
                                f"Contains symbols (e.g. '{existing_sym}') already in library. "
                                "Unchecked by default to prevent accidental override."
                            )
                            found_partial = True
                            break

                    if not found_partial:
                        row["status"] = "NEW"
                        row["tooltip"] = (
                            f"Contains {len(sym_files)} symbol file(s). Appears new."
                        )

            except Exception as e:
                row["status"] = "ERROR"
                row["tooltip"] = f"Could not scan: {e}"

            GUI_FILE_DATA.append(row)


# ---------------------------
# CLI call (process/purge)
# ---------------------------


def execute_library_action(paths: List[Path], is_purge: bool, rename_assets: bool):
    """
    Execute cli_main.py "process" or "purge".
    Keeps same branching between frozen .exe and dev .py.
    Returns (success_bool, joined_output_str)
    """
    success = False
    output_lines: List[str] = []
    running_as_exe = getattr(sys, "frozen", False)

    try:
        if running_as_exe:
            import cli_main
            from io import StringIO
            import contextlib

            buf = StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                argv_backup = sys.argv
                sys.argv = ["cli_main", "purge" if is_purge else "process"]
                if rename_assets and not is_purge:
                    sys.argv.append("--rename-assets")
                sys.argv.extend([str(p) for p in paths])

                try:
                    cli_main.main()
                    success = True
                except SystemExit as e:
                    success = e.code == 0
                finally:
                    sys.argv = argv_backup

            output_lines = buf.getvalue().splitlines()

        else:
            python_exe = sys.executable
            action_str = "purge" if is_purge else "process"

            cmd = [python_exe, str(CLI_SCRIPT_PATH), action_str]
            if rename_assets and not is_purge:
                cmd.append("--rename-assets")
            cmd.extend([str(p) for p in paths])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )

            output_lines = (result.stdout + result.stderr).splitlines()
            success = result.returncode == 0

    except Exception as e:
        success = False
        output_lines = [f"CRITICAL ERROR: {e}"]

    return success, "\n".join(output_lines)


def get_active_files_for_processing(dpg) -> List[Path]:
    """
    Look at GUI_FILE_DATA and read each matching checkbox_{i}.
    Return list[Path] of selected ZIPs.
    """
    active: List[Path] = []
    for i, row in enumerate(GUI_FILE_DATA):
        cb_tag = f"checkbox_{i}"
        if dpg.does_item_exist(cb_tag) and dpg.get_value(cb_tag):
            active.append(row["path"])
    return active


def process_action(dpg, sender, app_data, is_purge: bool):
    """
    Main handler behind PROCESS / PURGE buttons.
    - gather selected ZIPs
    - run CLI
    - stream output to log_message()
    - refresh cache/UI after success
    """
    active_files = get_active_files_for_processing(dpg)
    if not active_files:
        log_message(dpg, None, None, "ERROR: No active ZIP files selected for action.")
        return

    # read rename checkbox (only relevant for PROCESS)
    rename_assets = False
    if not is_purge and dpg.does_item_exist("rename_assets_chk"):
        rename_assets = dpg.get_value("rename_assets_chk")

    action_name = "PURGE" if is_purge else "PROCESS"
    log_message(
        dpg,
        None,
        None,
        f"--- Initiating {action_name} for {len(active_files)} active file(s) ---",
    )

    if not is_purge and rename_assets:
        log_message(
            dpg,
            None,
            None,
            "INFO: Renaming of Footprints/3D Models is ENABLED.",
        )

    ok, output = execute_library_action(
        active_files, is_purge=is_purge, rename_assets=rename_assets
    )

    # mirror CLI output into GUI log
    for line in output.splitlines():
        log_message(dpg, None, None, line, add_timestamp=False, is_cli_output=True)

    if ok:
        log_message(
            dpg,
            None,
            None,
            f"[OK] {action_name} SUCCESSFUL. Refreshing display...",
        )
        # reload symbol cache and rescan same folder
        update_existing_symbols_cache(dpg)
        current_folder = get_current_folder_path(dpg)
        if current_folder is not None:
            reload_folder_from_path(dpg, str(current_folder))
    else:
        log_message(
            dpg,
            None,
            None,
            f"[FAIL] {action_name} FAILED. See output above.",
        )

    log_message(
        dpg,
        None,
        None,
        "------------------------------------------------------",
        add_timestamp=False,
    )
    log_message(dpg, None, None, "", add_timestamp=False)


# ---------------------------
# DRC updater
# ---------------------------


def update_drc_rules(dpg, sender=None, app_data=None):
    """
    Auto-select correct .kicad_dru template based on copper layer count,
    copy it to Project.kicad_dru, log results.
    Behavior preserved.
    """
    try:
        # Step 1: find .kicad_pcb up the tree
        cwd = Path.cwd()
        pcb = None
        for parent in [cwd] + list(cwd.parents):
            hits = list(parent.glob("*.kicad_pcb"))
            if hits:
                pcb = hits[0]
                break

        if not pcb:
            log_message(dpg, None, None, "[FAIL] No .kicad_pcb file found.")
            return

        log_message(dpg, None, None, f"Found PCB file: {pcb.name}")

        # Step 2: parse layers
        with open(pcb, "r", encoding="utf-8") as f:
            sexpr = loads(f.read())

        layers_block = None
        for e in sexpr:
            if isinstance(e, list) and e and e[0] == Symbol("layers"):
                layers_block = e
                break

        if not layers_block:
            log_message(
                dpg, None, None, "[FAIL] No (layers ...) block found in PCB file."
            )
            return

        copper_layers = [
            layer
            for layer in layers_block[1:]
            if isinstance(layer, list)
            and len(layer) > 1
            and str(layer[1]).endswith(".Cu")
        ]
        layer_count = len(copper_layers)
        log_message(dpg, None, None, f"🧩 Detected {layer_count} copper layers")

        # Step 3: find dru_templates folder
        dru_template_dir = None
        for parent in [cwd] + list(cwd.parents):
            cand = parent / "dru_templates"
            if cand.exists() and cand.is_dir():
                dru_template_dir = cand
                break

        if not dru_template_dir:
            log_message(dpg, None, None, "[FAIL] No 'dru_templates' folder found.")
            return

        src = None
        for fp in dru_template_dir.glob(f"dru_{layer_count}_layer.kicad_dru"):
            src = fp
            break

        if not src or not src.exists():
            log_message(
                dpg,
                None,
                None,
                f"[FAIL] No template found for {layer_count} layers.",
            )
            return

        # Step 4: find Project.kicad_dru
        dst = None
        for parent in [cwd] + list(cwd.parents):
            hits = list(parent.glob("Project.kicad_dru"))
            if hits:
                dst = hits[0]
                break
        if not dst:
            dst = Path.cwd() / "Project.kicad_dru"

        # Step 5: copy + log
        shutil.copyfile(src, dst)
        log_message(dpg, None, None, f"[OK] Applied {src.name} -> {dst.name}")
        log_message(dpg, None, None, "[SUCCESS] DRC updated successfully.")

    except Exception as e:
        log_message(dpg, None, None, f"[FAIL] DRC update failed: {e}")


# ---------------------------
# Folder / selection helpers
# ---------------------------


def _init_tk_root():
    """Create + hide Tk root so we can show OS-native file dialog."""
    root = tk.Tk()
    root.withdraw()
    return root


def select_zip_folder(initial_dir: Path | None = None) -> List[Path]:
    """Native folder picker -> return list of *.zip files in that folder."""
    init_dir = str(initial_dir or INPUT_ZIP_FOLDER.resolve())

    root = _init_tk_root()
    try:
        chosen = fd.askdirectory(
            title="Select Folder Containing ZIP Archives",
            initialdir=init_dir,
        )
        if not chosen:
            return []
        folder = Path(chosen)
        return list(folder.glob("*.zip"))
    finally:
        root.destroy()


def open_url(dpg, sender, app_data, url: str):
    """Open link in default browser and log."""
    import webbrowser

    try:
        webbrowser.open_new_tab(url)
        log_message(
            dpg,
            None,
            None,
            f"INFO: Opened URL: {url}",
            is_cli_output=False,
        )
    except Exception as e:
        log_message(
            dpg,
            None,
            None,
            f"ERROR: Failed to open web browser: {e}",
            is_cli_output=False,
        )


def open_folder_in_explorer(dpg, sender=None, app_data=None):
    """
    Open whatever path is shown in CURRENT_PATH_TAG using OS file explorer.
    (Same logic as before, just pulled out.)
    """
    cur_txt = dpg_safe_get_value(dpg, CURRENT_PATH_TAG, "")
    if not cur_txt.startswith("Current Folder: "):
        log_message(
            dpg,
            None,
            None,
            "ERROR: Could not determine current folder path.",
            is_cli_output=False,
        )
        return

    folder_str = cur_txt.replace("Current Folder: ", "")

    if (not folder_str) or folder_str.startswith("("):
        log_message(
            dpg,
            None,
            None,
            "ERROR: No valid folder path is currently set.",
            is_cli_output=False,
        )
        return

    folder_path = Path(folder_str)
    if not (folder_path.exists() and folder_path.is_dir()):
        log_message(
            dpg,
            None,
            None,
            f"ERROR: Folder path does not exist: {folder_str}",
            is_cli_output=False,
        )
        return

    try:
        if sys.platform == "win32":
            os.startfile(folder_str)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder_str])
        else:
            subprocess.Popen(["xdg-open", folder_str])

        log_message(
            dpg,
            None,
            None,
            f"INFO: Opened folder in explorer: {folder_str}",
            is_cli_output=False,
        )

    except Exception as e:
        log_message(
            dpg,
            None,
            None,
            f"ERROR: Failed to open folder in explorer: {e}",
            is_cli_output=False,
        )


def open_output_folder(dpg, sender=None, app_data=None):
    """
    Opens /library_output in system explorer.
    Uses same trick as original code: temporarily stuffs CURRENT_PATH_TAG,
    reuses open_folder_in_explorer(), restores it.
    """
    try:
        output_folder = INPUT_ZIP_FOLDER.parent / "library_output"
        os.makedirs(output_folder, exist_ok=True)

        prev_val = dpg_safe_get_value(dpg, CURRENT_PATH_TAG, "")
        dpg_safe_set_value(dpg, CURRENT_PATH_TAG, f"Current Folder: {output_folder}")
        open_folder_in_explorer(dpg)
        dpg_safe_set_value(dpg, CURRENT_PATH_TAG, prev_val)

    except Exception as e:
        log_message(dpg, None, None, f"ERROR: Could not open output folder: {e}")


def get_current_folder_path(dpg) -> Path | None:
    """
    Parse CURRENT_PATH_TAG's text into a real Path or None.
    Ex: "Current Folder: C:\foo" -> Path("C:\foo")
    """
    cur = dpg_safe_get_value(dpg, CURRENT_PATH_TAG, "")
    if not isinstance(cur, str) or not cur.startswith("Current Folder: "):
        return None
    path_str = cur.replace("Current Folder: ", "")
    if not path_str or path_str.startswith("("):
        return None
    return Path(path_str)


def refresh_file_list(dpg, sender=None, app_data=None):
    """
    Button callback for "Refresh ZIPs".
    Just rescans whatever CURRENT_PATH_TAG is pointing at.
    """
    folder = get_current_folder_path(dpg)
    if folder is None:
        log_message(dpg, None, None, "ERROR: Cannot refresh. Current path is invalid.")
        return
    log_message(dpg, None, None, f"Manually refreshing file list for: {folder}")
    reload_folder_from_path(dpg, str(folder))


def show_native_folder_dialog(dpg, sender=None, app_data=None):
    """
    Folder picker -> scan ZIPs -> load into GUI.
    Keeps same log text as original.
    """
    paths = select_zip_folder(initial_dir=get_current_folder_path(dpg))
    if not paths:
        log_message(
            dpg,
            None,
            None,
            "Folder selection cancelled or no ZIP files found. Retaining current folder view.",
        )
        return

    picked_folder = str(paths[0].parent.resolve())
    log_message(dpg, None, None, f"Found {len(paths)} ZIP file(s).")
    reload_folder_from_path(dpg, picked_folder)


def reload_folder_from_path(dpg, folder_path_str: str):
    """
    Core rescan logic (used on init + after PROCESS/PURGE + manual refresh + folder pick).
    - update symbol cache
    - scan folder for *.zip
    - update CURRENT_PATH_TAG
    - rebuild checkbox list via gui_ui.build_file_list_ui()
    Also toggles visibility of zip_action_group / symbol_action_group based on results.
    """
    from gui_ui import build_file_list_ui  # avoid circular import at module load

    folder_path = Path(folder_path_str).resolve()
    if not folder_path.exists() or not folder_path.is_dir():
        log_message(dpg, None, None, f"ERROR: Folder not found at '{folder_path}'.")
        check_zip_for_existing_symbols([])
        build_file_list_ui(dpg)
        return

    try:
        update_existing_symbols_cache(dpg)
        zip_candidates = list(folder_path.glob("*.zip"))
        valid_paths = [p for p in zip_candidates if p.exists()]

        check_zip_for_existing_symbols(valid_paths)
        dpg_safe_set_value(
            dpg, CURRENT_PATH_TAG, f"Current Folder: {folder_path.resolve()}"
        )

        # only show zip actions if folder actually has ZIPs
        if valid_paths and dpg.does_item_exist("zip_action_group"):
            dpg.show_item("zip_action_group")
        elif dpg.does_item_exist("zip_action_group"):
            dpg.hide_item("zip_action_group")

        # symbol action group is only shown on Symbol tab; hide for now
        if dpg.does_item_exist("symbol_action_group"):
            dpg.hide_item("symbol_action_group")

        build_file_list_ui(dpg)

    except Exception as e:
        log_message(dpg, None, None, f"ERROR scanning folder: {e}")
        check_zip_for_existing_symbols([])
        build_file_list_ui(dpg)


def initial_load(dpg):
    """
    Run at startup:
    - set CURRENT_PATH_TAG to INPUT_ZIP_FOLDER
    - scan default folder for ZIPs
    - populate GUI_FILE_DATA + checkbox list
    - set visibility of action groups
    - refresh symbol list
    """
    from gui_ui import build_file_list_ui, refresh_symbol_list  # avoid circular import

    update_existing_symbols_cache(dpg)

    target_folder = INPUT_ZIP_FOLDER.resolve()
    dpg_safe_set_value(dpg, CURRENT_PATH_TAG, f"Current Folder: {target_folder}")

    if not (target_folder.exists() and target_folder.is_dir()):
        log_message(
            dpg,
            None,
            None,
            f"ERROR: Input folder not found at '{target_folder}'. Skipping initial load.",
        )
        dpg_safe_set_value(dpg, CURRENT_PATH_TAG, "Current Folder: (Path Error)")
        return

    log_message(dpg, None, None, f"Checking default folder: '{target_folder}'")

    try:
        zips_here = list(target_folder.glob("*.zip"))
        valid_paths = [p for p in zips_here if p.exists()]
    except Exception as e:
        log_message(dpg, None, None, f"ERROR scanning folder: {e}")
        valid_paths = []

    check_zip_for_existing_symbols(valid_paths)

    if valid_paths:
        log_message(
            dpg,
            None,
            None,
            f"Successfully loaded {len(valid_paths)} ZIP file(s) from default path.",
        )
        if dpg.does_item_exist("zip_action_group"):
            dpg.show_item("zip_action_group")
    else:
        log_message(dpg, None, None, "No ZIP files found in the default folder.")
        if dpg.does_item_exist("zip_action_group"):
            dpg.hide_item("zip_action_group")

    if dpg.does_item_exist("symbol_action_group"):
        dpg.hide_item("symbol_action_group")

    build_file_list_ui(dpg)
    refresh_symbol_list(dpg)


# ---------------------------
# Export logic (Project Symbols tab)
# ---------------------------


def collect_selected_symbols_for_export(dpg) -> List[str]:
    """
    Walks symbol_checkboxes_container and returns names of all checked symbols.
    """
    selected: List[str] = []
    if not dpg.does_item_exist("symbol_checkboxes_container"):
        return selected

    container_children = dpg.get_item_children("symbol_checkboxes_container", 1)
    if not container_children:
        return selected

    for group in container_children:
        for child in dpg.get_item_children(group, 1):
            if dpg.get_item_type(child) == "mvAppItemType::mvCheckbox":
                if dpg.get_value(child):
                    selected.append(dpg.get_item_label(child))

    return selected


def export_action(dpg, sender=None, app_data=None):
    """
    EXPORT SELECTED button callback.
    Gathers selected symbols, validates each has footprint(+3D),
    then calls library_manager.export_symbols().
    Logs everything just like original.
    """
    # make sure we're actually on the Symbols tab
    try:
        active_tab_tag = dpg.get_value("source_tab_bar")
        active_tab_label = dpg.get_item_label(active_tab_tag)
    except Exception:
        active_tab_label = ""

    if "symbol" not in active_tab_label.lower():
        log_message(
            dpg,
            None,
            None,
            "[WARN] Export is only available in the Project Symbols tab.",
        )
        return

    from library_manager import export_symbols

    selected_symbols = collect_selected_symbols_for_export(dpg)
    if not selected_symbols:
        log_message(dpg, None, None, "[WARN] No symbols selected for export.")
        return

    # Parse ProjectSymbols.kicad_sym
    try:
        with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
            sexp = loads(f.read())
    except Exception as e:
        log_message(dpg, None, None, f"[FAIL] Could not read symbol library: {e}")
        return

    # Build {symbol_name: footprint_name}
    symbol_footprints: Dict[str, str] = {}
    for el in sexp[1:]:
        if isinstance(el, list) and len(el) > 1 and str(el[0]) == "symbol":
            sym_name = str(el[1])
            footprint_field = None
            for item in el:
                if (
                    isinstance(item, list)
                    and len(item) >= 2
                    and str(item[0]) == "property"
                    and len(item) > 2
                    and str(item[1]) == "Footprint"
                ):
                    footprint_field = str(item[2])
                    break
            if footprint_field:
                symbol_footprints[sym_name] = footprint_field

    valid_symbols: List[Dict[str, Any]] = []
    missing_footprints: List[str] = []
    missing_models: List[str] = []

    # Check each chosen symbol
    for sym in selected_symbols:
        # handle symbol vs LIB_symbol naming
        footprint_name = None
        for candidate in (sym, f"LIB_{sym}"):
            if candidate in symbol_footprints:
                footprint_name = symbol_footprints[candidate]
                break

        if not footprint_name:
            missing_footprints.append(sym)
            continue

        footprint_basename = footprint_name.split(":")[-1]

        # lookup .kicad_mod in PROJECT_FOOTPRINT_LIB
        found_fp = None
        for fp in PROJECT_FOOTPRINT_LIB.rglob("*.kicad_mod"):
            if fp.stem == footprint_basename:
                found_fp = fp
                break

        if not found_fp:
            missing_footprints.append(sym)
            continue

        # parse 3D model refs from footprint
        model_files: List[Path] = []
        try:
            with open(found_fp, "r", encoding="utf-8") as ff:
                for raw_line in ff:
                    line = raw_line.strip()
                    if line.startswith("(model "):
                        # simplest extraction: first token after "(model"
                        segment = line.split("(model", 1)[1]
                        segment = segment.split(")", 1)[0].strip().strip('"')
                        expanded = os.path.expandvars(segment)
                        expanded = (
                            expanded.replace("${KICAD7_3DMODEL_DIR}", "3d_models")
                            .replace("${KICAD6_3DMODEL_DIR}", "3d_models")
                            .replace("${KICAD8_3DMODEL_DIR}", "3d_models")
                        )
                        model_files.append(Path(expanded))
        except Exception:
            pass

        # verify existence of 3D models
        resolved_models: List[Path] = []
        for m in model_files:
            if m.is_absolute() and m.exists():
                resolved_models.append(m)
            else:
                test_path = (PROJECT_FOOTPRINT_LIB.parent / m).resolve()
                if test_path.exists():
                    resolved_models.append(test_path)
                else:
                    missing_models.append(str(m))

        if resolved_models:
            log_message(
                dpg,
                None,
                None,
                f"[INFO] Found {len(resolved_models)} 3D file(s) for {sym}: "
                + ", ".join(m.name for m in resolved_models),
            )

        valid_symbols.append(
            {"symbol": sym, "footprint": found_fp, "models": resolved_models}
        )

    if not valid_symbols:
        log_message(
            dpg,
            None,
            None,
            "[FAIL] No valid symbols found (missing or unresolved footprints).",
        )
        return

    if missing_footprints:
        log_message(
            dpg,
            None,
            None,
            f"[WARN] Missing footprints for: {', '.join(missing_footprints)}",
        )

    if missing_models:
        log_message(
            dpg,
            None,
            None,
            f"[WARN] Missing 3D models: {', '.join(missing_models)}",
        )

    export_paths = export_symbols([entry["symbol"] for entry in valid_symbols])

    if export_paths:
        log_message(
            dpg,
            None,
            None,
            f"[OK] Exported {len(export_paths)} ZIP file(s) successfully.",
        )
        outdir = export_paths[0].parent if hasattr(export_paths[0], "parent") else None
        if outdir:
            log_message(
                dpg,
                None,
                None,
                f"[OK] Output directory: {outdir}",
            )
        else:
            log_message(
                dpg,
                None,
                None,
                "[WARN] Could not determine output directory.",
            )
    else:
        log_message(dpg, None, None, "[FAIL] Export returned no files.")


# ---------------------------
# Tab / selection helpers
# ---------------------------


def toggle_selection_mode(dpg, container_tag: str, btn_tag: str):
    """
    Select All / Deselect All logic used in both ZIP and Symbol tabs.
    Behavior unchanged, just parameterized.
    """
    if not (container_tag and btn_tag):
        log_message(
            dpg, None, None, "[WARN] Invalid toggle_selection_mode call (missing args)."
        )
        return

    if not dpg.does_item_exist(container_tag):
        log_message(dpg, None, None, f"[WARN] Container '{container_tag}' not found.")
        return

    btn_label = dpg.get_item_label(btn_tag)
    select_mode = btn_label == "Select All"

    def walk_checkboxes(root_id):
        found = []
        if not dpg.does_item_exist(root_id):
            return found
        kids_groups = dpg.get_item_children(root_id, 1)
        if not kids_groups:
            return found
        for kid in kids_groups:
            if dpg.get_item_type(kid) == "mvAppItemType::mvCheckbox":
                found.append(kid)
            else:
                found.extend(walk_checkboxes(kid))
        return found

    boxes = walk_checkboxes(container_tag)
    if not boxes:
        log_message(dpg, None, None, "[WARN] No checkboxes found to toggle.")
        return

    for cb in boxes:
        dpg.set_value(cb, select_mode)

    dpg.set_item_label(btn_tag, "Deselect All" if select_mode else "Select All")

    log_message(
        dpg,
        None,
        None,
        f"{'Selected' if select_mode else 'Deselected'} {len(boxes)} items in {container_tag}.",
    )


def on_tab_change(dpg, sender=None, app_data=None, user_data=None):
    """
    Called when user switches tabs.
    Shows/hides zip_action_group, symbol_action_group, etc.
    Emits the same log lines you had.
    """
    try:
        active_tab = dpg.get_item_label(dpg.get_value("source_tab_bar"))
    except Exception:
        log_message(dpg, None, None, "[WARN] Could not detect active tab.")
        return

    active = active_tab.lower().strip()

    if "zip" in active or "import" in active:
        if dpg.does_item_exist("zip_action_group"):
            dpg.show_item("zip_action_group")
        if dpg.does_item_exist("symbol_action_group"):
            dpg.hide_item("symbol_action_group")
        log_message(dpg, None, None, "[INFO] Switched to ZIP Archives tab.")

    elif "symbol" in active or "export" in active:
        if dpg.does_item_exist("zip_action_group"):
            dpg.hide_item("zip_action_group")
        if dpg.does_item_exist("symbol_action_group"):
            dpg.show_item("symbol_action_group")
        # refresh symbol list on tab enter
        from gui_ui import refresh_symbol_list

        refresh_symbol_list(dpg)
        log_message(dpg, None, None, "[INFO] Switched to Project Symbols tab.")

    elif "drc" in active:
        if dpg.does_item_exist("zip_action_group"):
            dpg.hide_item("zip_action_group")
        if dpg.does_item_exist("symbol_action_group"):
            dpg.hide_item("symbol_action_group")
        log_message(dpg, None, None, "[INFO] Switched to DRC Manager tab.")

    else:
        if dpg.does_item_exist("zip_action_group"):
            dpg.hide_item("zip_action_group")
        if dpg.does_item_exist("symbol_action_group"):
            dpg.hide_item("symbol_action_group")
        log_message(dpg, None, None, "[WARN] Unknown tab selected.")
