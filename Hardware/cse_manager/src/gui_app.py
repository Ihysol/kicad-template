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

# Import Tkinter for the native file dialog
import tkinter as tk
from tkinter import filedialog as fd

FONT_SIZE = 18

# Cache of main symbols already in the project library
PROJECT_EXISTING_SYMBOLS = set() 
# List of dictionaries storing data for each ZIP file in the UI
GUI_FILE_DATA = [] 
# Stores the complete history of log messages (for the copy function)
full_log_history = [] 

# Attempt to import necessary paths and functions from the library manager
try:
  from library_manager import INPUT_ZIP_FOLDER, get_existing_main_symbols
  CLI_SCRIPT_PATH = Path(__file__).parent / "cli_main.py"
except ImportError as e:
  # Fallback/Dummy paths and function if library_manager is not found
  INPUT_ZIP_FOLDER = Path.cwd() 
  CLI_SCRIPT_PATH = Path.cwd() / "cli_main_dummy.py"
  def get_existing_main_symbols(): return {"RESISTOR_1", "CAP_POL_SMD"} 

# --- Constants for DPG Tags ---
WINDOW_WIDTH = 900
WINDOW_HEIGHT = 650 
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
    if getattr(sys, 'frozen', False):
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
# --- CORE LOGIC & EXECUTION ---
# ===================================================

def execute_library_action(paths, is_purge, rename_assets: bool):
    """
    Executes the CLI either as subprocess (in .py mode) or direct import (in .exe mode).
    """
    success = False
    output_lines = []

    running_as_exe = getattr(sys, 'frozen', False)

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
                    success = (e.code == 0)
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

            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
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
    log_message(None, None, f"INFO: Updated existing symbol cache with {len(PROJECT_EXISTING_SYMBOLS)} symbols.", is_cli_output=False)
  except Exception as e:
    log_message(None, None, f"[ERROR] Failed to load existing symbols: {e}", is_cli_output=False)
    PROJECT_EXISTING_SYMBOLS = set() 


def check_zip_for_existing_symbols(zip_paths: list[Path]):
  """
  Scans the provided ZIP files and checks if their names suggest they 
  contain symbols already present in the project library.
  It populates the GUI_FILE_DATA list with status and tooltip info.
  """
  global GUI_FILE_DATA
  GUI_FILE_DATA.clear()

  if not zip_paths: return

  # Use a temporary directory for safe extraction/scanning, though we only read names here
  with tempfile.TemporaryDirectory() as temp_dir_name:
    for p in zip_paths:
      zip_data = {
        'path': p,
        'name': p.name,
        'status': 'NEW', 
        'tooltip': 'No KiCad symbols found.'
      }
      try:
        with zipfile.ZipFile(p, 'r') as zf:
          # Check for the existence of symbol files within the ZIP
          sym_files = [name for name in zf.namelist() if name.lower().endswith(".kicad_sym")]
          if not sym_files:
            zip_data['status'] = 'NONE'
            zip_data['tooltip'] = 'ZIP does not contain any .kicad_sym files.'
            GUI_FILE_DATA.append(zip_data)
            continue
            
          # Simple check: see if any existing symbol name is part of the ZIP filename
          found_partial = False
          for existing_sym in PROJECT_EXISTING_SYMBOLS:
            if existing_sym.lower() in p.stem.lower():
              zip_data['status'] = 'PARTIAL'
              zip_data['tooltip'] = f"Contains symbols (e.g. '{existing_sym}') already in library. Unchecked by default to prevent accidental override."
              found_partial = True
              break
          
          if not found_partial:
            zip_data['status'] = 'NEW'
            zip_data['tooltip'] = f"Contains {len(sym_files)} symbol file(s). Appears new."

      except Exception as e:
        log_message(None, None, f"ERROR: Could not scan ZIP {p.name} for symbols: {e}", is_cli_output=False)
        zip_data['status'] = 'ERROR'
        zip_data['tooltip'] = f"Could not scan: {e}"

      GUI_FILE_DATA.append(zip_data)


# ===================================================
# --- DPG UTILITIES ---
# ===================================================

def log_message(sender, app_data, user_data: str, add_timestamp: bool = True, is_cli_output: bool = False):
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
  elif "[FAIL]" in user_data_upper or "[ERROR]" in user_data_upper or "CRITICAL ERROR" in user_data_upper:
    theme_tag = "error_log_theme"
  elif "[OK]" in user_data_upper or "[SUCCESS]" in user_data_upper:
    theme_tag = "success_log_theme"
  
  # Create a non-editable input text item for the log entry
  new_text_item = dpg.add_input_text(
    default_value=log_entry_full,
    parent=LOG_TEXT_TAG,
    readonly=True,
    width=-1, 
    tag=dpg.generate_uuid()
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
    height=400
  ):
    dpg.add_text("This is the full, raw log. Use CTRL+A to select all and CTRL+C to copy.")
    dpg.add_separator()
    dpg.add_input_text(
      default_value="\n".join(full_log_history),
      multiline=True,
      readonly=True,
      width=-1,
      height=-1,
      tag=FULL_LOG_TEXT_TAG
    )


def build_file_list_ui():
  """Generates the checkboxes and status indicators for all ZIP files in GUI_FILE_DATA."""
  global GUI_FILE_DATA
  dpg.delete_item(FILE_CHECKBOXES_CONTAINER, children_only=True)
  dpg.set_value(FILE_COUNT_TAG, f"Total files found: {len(GUI_FILE_DATA)}")
  
  if not GUI_FILE_DATA:
    with dpg.group(parent=FILE_CHECKBOXES_CONTAINER):
      dpg.add_text("No ZIP files loaded. Select a folder to begin.", color=[255, 165, 0])
    return
  
  with dpg.group(parent=FILE_CHECKBOXES_CONTAINER):
    for i, data in enumerate(GUI_FILE_DATA):
      tag = f"checkbox_{i}" 
      status = data['status']
      
      status_text = ""
      status_color = (200, 200, 200)
      is_new = True
      
      # Determine UI appearance and default check state based on scan status
      if status == 'PARTIAL':
        status_text = "(Partial Match/Existing Symbols)"
        status_color = (255, 165, 0)
        is_new = False
      elif status == 'NEW':
        status_text = "(New)"
        status_color = (0, 255, 0)
        is_new = True
      elif status == 'ERROR':
        status_text = "(Error Scanning)"
        status_color = (255, 0, 0)
        is_new = False
      elif status == 'NONE':
        status_text = "(No Symbols Found)"
        status_color = (150, 150, 150)
        is_new = False

      with dpg.group(horizontal=True):
        checkbox = dpg.add_checkbox(
          label=data['name'], 
          default_value=is_new,
          tag=tag
        )
        with dpg.tooltip(parent=checkbox):
          dpg.add_text(data['tooltip'])
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
      active_paths.append(data['path'])
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

  # --- NEW: Get the state of the renaming checkbox ---
  rename_assets = False
  if not is_purge and dpg.does_item_exist("rename_assets_chk"):
    rename_assets = dpg.get_value("rename_assets_chk")
  # ----------------------------------------------------

  action_name = "PURGE" if is_purge else "PROCESS"
  log_message(None, None, f"--- Initiating {action_name} for {len(active_files)} active file(s) ---")
  if not is_purge and rename_assets:
    log_message(None, None, "INFO: Renaming of Footprints/3D Models is ENABLED.")
  
  # Execute the library action in a subprocess, passing the new flag
  success, output = execute_library_action(active_files, is_purge=is_purge, rename_assets=rename_assets)
  
  # Stream the CLI output to the log window
  for line in output.splitlines():
    log_message(None, None, line, add_timestamp=False, is_cli_output=True) 
  
  if success:
    log_message(None, None, f"[OK] {action_name} SUCCESSFUL. Refreshing display...")
    # Update cache and re-scan the currently loaded ZIPs to reflect changes
    update_existing_symbols_cache() 
    current_zip_paths = [data['path'] for data in GUI_FILE_DATA]
    check_zip_for_existing_symbols(current_zip_paths)
    build_file_list_ui()
  else:
    log_message(None, None, f"[FAIL] {action_name} FAILED. See output above.")
  
  log_message(None, None, "------------------------------------------------------", add_timestamp=False)
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
      initialdir=str(INPUT_ZIP_FOLDER.resolve())
    )
    if not folder_path_str: return []
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
    log_message(None, None, "ERROR: Could not determine current folder path.", is_cli_output=False)
    return
    
  folder_path_str = current_path_text.replace("Current Folder: ", "")
  
  if not folder_path_str or folder_path_str.startswith('('):
    log_message(None, None, "ERROR: No valid folder path is currently set.", is_cli_output=False)
    return

  folder_path = Path(folder_path_str)

  if not folder_path.exists() or not folder_path.is_dir():
    log_message(None, None, f"ERROR: Folder path does not exist: {folder_path_str}", is_cli_output=False)
    return

  try:
    # Use appropriate command based on the operating system
    if sys.platform == "win32":
      os.startfile(folder_path_str)
    elif sys.platform == "darwin":
      subprocess.Popen(["open", folder_path_str])
    else:
      subprocess.Popen(["xdg-open", folder_path_str])
      
    log_message(None, None, f"INFO: Opened folder in explorer: {folder_path_str}", is_cli_output=False)

  except Exception as e:
    log_message(None, None, f"ERROR: Failed to open folder in explorer: {e}", is_cli_output=False)


def show_native_folder_dialog(sender, app_data):
  """Triggers the folder selection dialog and initiates UI reload if files are found."""
  paths = select_zip_folder()
  
  if not paths:
    log_message(None, None, "Folder selection cancelled or no ZIP files found. Retaining current folder view.")
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
  
  if folder_path_str.startswith('('):
    log_message(None, None, "ERROR: Cannot refresh. No valid folder path is currently set.")
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
    log_message(None, None, f"ERROR: Input folder not found at '{target_folder}'. Skipping initial load.")
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
    log_message(None, None, f"Successfully loaded {len(valid_paths)} ZIP file(s) from default path.")
    dpg.show_item(ACTION_SECTION_TAG)
  else:
    log_message(None, None, "No ZIP files found in the default folder.")
  build_file_list_ui()


def open_url(sender, app_data, url):
  """Opens a specified URL in the default web browser."""
  try:
    webbrowser.open_new_tab(url)
    log_message(None, None, f"INFO: Opened URL: {url}", is_cli_output=False)
  except Exception as e:
    log_message(None, None, f"ERROR: Failed to open web browser: {e}", is_cli_output=False)


# ===================================================
# --- GUI SETUP ---
# ===================================================

def create_gui():
  """Sets up the DearPyGui context, themes, and main window layout."""
  dpg.create_context()
  
  
  load_font_recursively("NotoSans-Regular.ttf", size=FONT_SIZE)


  
  dpg.create_viewport(
    title='KiCad Library Manager', 
    width=WINDOW_WIDTH, 
    height=WINDOW_HEIGHT,
    resizable=True 
  )
  dpg.setup_dearpygui()

  # --- Theme setup ---
  with dpg.theme() as global_theme:
    with dpg.theme_component(dpg.mvAll):
      dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (25, 25, 25))
  dpg.bind_theme(global_theme)
  
  # --- Log Color Themes (Themed InputText to act as read-only colored text) ---
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

  # --- Hyperlink Theme (Makes buttons look like clickable blue text) ---
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
  with dpg.window(tag="main_window", label="KiCad Library Manager", width=WINDOW_WIDTH, height=WINDOW_HEIGHT):
    dpg.set_primary_window("main_window", True)
    
    # 1. Folder Selection and Current Path
    dpg.add_text("1. Select Archive Folder (ZIPs will be scanned automatically):", color=[0, 255, 255])
    
    with dpg.group(horizontal=True):
      dpg.add_button(label="Open Folder", callback=show_native_folder_dialog) 
      
      # Button to open the current path in the OS file explorer
      explorer_button = dpg.add_button(
        label=f"Open in Explorer", 
        callback=open_folder_in_explorer
      )

    dpg.add_text(
        "Current Folder: (Initializing...)", 
        tag=CURRENT_PATH_TAG, 
        wrap=0, 
        color=[150, 150, 255]
    )

    dpg.add_separator()
    
    # 2. Active Files Header, Count, and Refresh (Refresh Button Moved Here)
    dpg.add_text("2. Active ZIP Archives for Processing (Status Check on Load):", color=[255, 255, 0])
    
    with dpg.group(horizontal=True):
        dpg.add_text("Total files found: 0", tag=FILE_COUNT_TAG, color=[0, 255, 0])
        
        # Refresh Button is located here (Section 2)
        dpg.add_button(
        label="Refresh List", 
        callback=refresh_file_list,
        small=True 
        )
    
    # Container for the file checkboxes and status text
    with dpg.child_window(tag=FILE_CHECKBOXES_CONTAINER, width=-1, height=180, border=True):
        pass 
    
    # 3. Action Buttons and Toggles (Hidden until ZIPs are loaded)
    # *** THIS WAS THE SECTION THAT WAS DUPLICATED ***
    with dpg.group(tag=ACTION_SECTION_TAG, show=False): 
        with dpg.group(horizontal=True):
            dpg.add_button(label="Select All", callback=lambda s, a: toggle_all_checkboxes(s, a, True))
            dpg.add_button(label="Deselect All", callback=lambda s, a: toggle_all_checkboxes(s, a, False))

            dpg.add_separator()
            
        with dpg.group(horizontal=True, horizontal_spacing=20):
            # Button to initiate the library processing (import/copy)
            dpg.add_button(
                label="PROCESS / IMPORT", 
                tag="process_btn", 
                callback=lambda s, a: process_action(s, a, False),
                width=200
            )
        
            # --- NEW CHECKBOX FOR RENAMING LOGIC ---
            rename_chk = dpg.add_checkbox(
                label="Rename Footprints / 3D Models",
                default_value=False, # Default to True for a safety feature
                tag="rename_assets_chk"
            )
        with dpg.tooltip(parent=rename_chk):
            dpg.add_text("If checked, the system attempts to rename footprints and .step files inside the ZIP to match the primary symbol name before import.")
        # ---------------------------------------

        # Button to initiate the library purging (delete)
        dpg.add_button(
            label="PURGE / DELETE", 
            tag="purge_btn", 
            callback=lambda s, a: process_action(s, a, True),
            width=200
        )
        dpg.add_text("NOTE: Only checked files will be used.") 
        
        dpg.add_separator() # This separator correctly ends Section 3 logic

    # Log Output Section
    with dpg.group(horizontal=True):
        dpg.add_text("CLI Output Log:")
        dpg.add_button(label="Clear Log", callback=clear_log, small=True) 
        dpg.add_button(label="Show Full Log", callback=show_full_log_popup, small=True)

    # Log Text Area (Display actual log entries)
    with dpg.child_window(tag=LOG_WINDOW_CHILD_TAG, width=-1, height=150, border=True):
        dpg.add_group(tag=LOG_TEXT_TAG, width=-1) 
        
    # Hidden tag used to control auto-scrolling
    dpg.add_input_int(tag=SCROLL_FLAG_TAG, default_value=0, show=False)

    dpg.add_separator()
    
    # Hyperlinks Section
    with dpg.group(horizontal=True):
        # Author Link
        author_link = dpg.add_button(
            label="By: Ihysol (Tobias Gent)",
            callback=lambda s, a: open_url(s, a, "https://github.com/Ihysol"), 
            small=True
        )
        dpg.bind_item_theme(author_link, HYPERLINK_THEME_TAG)
        
        dpg.add_text("")
        
        # Issues Link
        issues_link = dpg.add_button(
            label="Report Bug / Suggest Feature",
            callback=lambda s, a: open_url(s, a, "https://github.com/Ihysol/kicad-template"), 
            small=True
        )
        dpg.bind_item_theme(issues_link, HYPERLINK_THEME_TAG)

    # --- FINAL SETUP AND INITIAL LOAD ---
    dpg.show_viewport()
    
    # Run the initial scan/load logic
    initial_load() 
    
    dpg.start_dearpygui()
    dpg.destroy_context()

if __name__ == "__main__":
  # Check for required dependencies before starting the GUI
  try:
    import dearpygui.dearpygui as dpg
  except ImportError:
    print("Error: DearPyGui is not installed. Please install it: pip install dearpygui")
    sys.exit(1)
    
  try:
    import tkinter as tk
  except ImportError:
    print("Error: tkinter is required for the native file dialog but is not available.")
    sys.exit(1)
    
  create_gui()