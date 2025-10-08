# library_manager.py - Emojis Removed

import os
import shutil
import sys 
import re
from dotenv import load_dotenv
import tempfile
import zipfile
from pathlib import Path
import json

# --- Environment Setup (Load once for all functions) ---

load_dotenv()

# Attempt to get environment variables. These must be defined in the calling environment or .env.
try:
    # GLOBAL_SYMBOL_LIB = Path(os.getenv("GLOBAL_SYMBOL_LIB"))
    # GLOBAL_FOOTPRINT_LIB = Path(os.getenv("GLOBAL_FOOTPRINT_LIB"))
    INPUT_ZIP_FOLDER = Path(os.getenv("INPUT_ZIP_FOLDER"))
except TypeError:
    # Handle case where .env is missing or variables aren't set
    raise ValueError("Missing GLOBAL_SYMBOL_LIB, GLOBAL_FOOTPRINT_LIB, or INPUT_ZIP_FOLDER in .env")

# Define project paths relative to the script's directory (assuming this is run from the project root)
# Note: PROJECT_DIR is defined relative to where the script is run from.
# In a fused app, this might need slight adjustment, but for now, it mirrors your original code.
PROJECT_DIR = Path(os.path.abspath(".."))
PROJECT_SYMBOL_LIB = PROJECT_DIR / "Lib-Symbols" / "ProjectSymbols.kicad_sym"
PROJECT_FOOTPRINT_LIB = PROJECT_DIR / "Lib-Footprints" / "ProjectFootprints.pretty"
PROJECT_3D_DIR = PROJECT_DIR / "Lib-3D-Files" 

# --- Global Regex Definitions ---

SUB_PART_PATTERN = re.compile(r'_\d(_\d)+$|_\d$')
SYMBOL_BLOCK_PATTERN = re.compile(r'^\s*\(symbol\s+"([^"]+)".*?\n\)', re.DOTALL | re.MULTILINE)
FOOTPRINT_PROPERTY_LINE_PATTERN = re.compile(r'^\s*\(property\s+"Footprint".*\)', re.MULTILINE)
MODEL_PATH_PATTERN = re.compile(r'(\(model\s+)"?([^"\s\)]+)"?', re.DOTALL)
FOOTPRINT_VALUE_PATTERN = re.compile(r'\(property\s+"Footprint"\s+"([^"]+)"') 

# Temporary file for the Footprint-to-Symbol map
TEMP_MAP_FILE = INPUT_ZIP_FOLDER / "footprint_to_symbol_map.json" 

# --- Ensure Project Directories Exist ---

os.makedirs(PROJECT_SYMBOL_LIB.parent, exist_ok=True)
os.makedirs(PROJECT_FOOTPRINT_LIB, exist_ok=True)
os.makedirs(PROJECT_3D_DIR, exist_ok=True)
os.makedirs(INPUT_ZIP_FOLDER, exist_ok=True)

# --------------------------------------------------------------------------------------------------
#                                 CORE FUNCTION LOGIC
# --------------------------------------------------------------------------------------------------
def get_existing_main_symbols():
    """
    Wrapper to get the set of main symbols currently in the project library.
    """
    return set(list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=False))


def localize_footprint_path(symbol_block_text: str, project_lib_name: str) -> str:
    """
    Replaces the footprint value in the symbol's property field to use the local 
    project library name (e.g., "ProjectFootprints:").
    """
    
    def line_replacement_func(match):
        """Inner function to perform substitution on a single 'Footprint' property line."""
        line = match.group(0)
        
        inner_match = re.search(r'(\(property\s+"Footprint"\s+")([^"]+)(".*)', line)
        
        if inner_match:
            footprint_name = inner_match.group(2)
            
            if ':' in footprint_name:
                name_only = footprint_name.split(':')[-1]
                new_footprint_value = f"{project_lib_name}:{name_only}"
            else:
                new_footprint_value = f"{project_lib_name}:{footprint_name}"
            
            return f'{inner_match.group(1)}{new_footprint_value}{inner_match.group(3)}'
        
        return line

    localized_text = FOOTPRINT_PROPERTY_LINE_PATTERN.sub(line_replacement_func, symbol_block_text)
    
    localized_text = re.sub(
        r'(\(footprint\s+"[^":]+):([^"]+)"',
        lambda m: f'(footprint "{project_lib_name}:{m.group(2)}")',
        localized_text
    )

    return localized_text


def localize_3d_model_path(mod_file: Path):
    """
    Reads a .kicad_mod file, uses the temporary map to find the correct 3D model name 
    (based on the symbol name), replaces the global path, and returns the modified content.
    """
    if not TEMP_MAP_FILE.exists():
        print(f"Error: Map file not found. Cannot determine correct 3D model name for {mod_file.name}.")
        return None
        
    with open(TEMP_MAP_FILE, 'r') as f:
        footprint_map = json.load(f)

    footprint_name = mod_file.stem
    symbol_name = footprint_map.get(footprint_name)
    
    if not symbol_name:
        print(f"Warning: Footprint {footprint_name} was not found in the symbol map. Skipping 3D localization.")
        return None

    try:
        with open(mod_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {mod_file.name}: {e}")
        return None

    model_filename = symbol_name + ".stp"
    target_path = f'${{KIPRJMOD}}/Lib-3D-Files/{model_filename}'

    modified_content = MODEL_PATH_PATTERN.sub(
        lambda m: f'{m.group(1)}"{target_path}"',
        content
    )
    
    if content == modified_content and "(model" in content:
        print(f"Warning: Could not localize 3D model path in {mod_file.name}. Check model path format.")
    
    return modified_content


# Part of library_manager.py

def list_symbols_simple(sym_file: Path, print_list: bool = True):
    """
    Returns a list of main symbol names from a KiCad library file.
    Skips common sub-part/alternate unit suffixes (_X_Y).
    """
    if not sym_file.exists():
        if print_list:
            print(f"File not found: {sym_file.name}")
        return []

    with open(sym_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    all_symbols = re.findall(r'\(symbol\s+"([^"]+)"', content) 
    
    symbols = []
    
    for name in all_symbols:
        if not SUB_PART_PATTERN.search(name):
            symbols.append(name)

    if print_list:
        print(f"Found {len(symbols)} (main) symbols in {sym_file.name}:")
        # --- MODIFIED OUTPUT FORMAT ---
        if symbols:
            print(", ".join(symbols))
        else:
            print("No main symbols found.")
        # ----------------------------
            
    return symbols


def append_symbols_from_file(src_sym_file: Path):
    """
    Appends symbols from a source KiCad library file to the project library.
    Also builds a map linking Footprint names to their main Symbol names.
    """
    PROJECT_FOOTPRINT_LIB_NAME = PROJECT_FOOTPRINT_LIB.stem 

    existing_main_symbols = set(list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=False))
    
    # Load existing map or start fresh
    footprint_map = {}
    if TEMP_MAP_FILE.exists():
        with open(TEMP_MAP_FILE, 'r') as f:
            footprint_map = json.load(f)

    try:
        with open(src_sym_file, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: Source file not found: {src_sym_file.name}")
        return False
        
    symbols_to_append_text = []
    appended_any = False

    for match in SYMBOL_BLOCK_PATTERN.finditer(content):
        symbol_name = match.group(1)
        symbol_block_text = match.group(0)
        
        base_name = SUB_PART_PATTERN.sub('', symbol_name)
        
        if base_name not in existing_main_symbols:
            
            # --- Footprint Extraction for Mapping ---
            footprint_match = FOOTPRINT_VALUE_PATTERN.search(symbol_block_text)
            if footprint_match:
                raw_footprint_name = footprint_match.group(1).split(':')[-1] 
                footprint_map[raw_footprint_name] = base_name 
            # ----------------------------------------
            
            # LOCALIZE FOOTPRINT PATH
            symbol_block_text_localized = localize_footprint_path(
                symbol_block_text, 
                PROJECT_FOOTPRINT_LIB_NAME 
            )
            
            symbols_to_append_text.append(symbol_block_text_localized)
            
            if symbol_name == base_name:
                existing_main_symbols.add(symbol_name)
                
            appended_any = True
            print(f"Appended symbol: {symbol_name} (Footprint link localized)")
        else:
            pass
    
    # Save the updated map
    if appended_any:
        with open(TEMP_MAP_FILE, 'w') as f:
            json.dump(footprint_map, f, indent=4)
        
    if symbols_to_append_text:
        project_sym_path = PROJECT_SYMBOL_LIB
        new_symbol_content = '\n' + '\n'.join(symbols_to_append_text) + '\n' 

        if project_sym_path.exists():
            with open(project_sym_path, "r", encoding="utf-8") as f:
                existing_content = f.read()
            
            content_before_closing_paren = existing_content.rstrip()
            
            if content_before_closing_paren.endswith(')'):
                content_before_closing_paren = content_before_closing_paren[:-1]
                new_file_content = content_before_closing_paren.rstrip() + new_symbol_content + ')'
            else:
                print("Warning: KiCad symbol file is malformed. Appending to end.")
                new_file_content = existing_content + new_symbol_content
        else:
            new_file_content = (
                '(kicad_symbol_lib (version 20211026) (generator "script-generator")'
                f'{new_symbol_content}'
                ')\n'
            )

        with open(project_sym_path, "w", encoding="utf-8") as f:
            f.write(new_file_content)
        
        # print(f"Appended {len(symbols_to_append_text)} symbols to {project_sym_path.name}")

    if not appended_any:
        print(f"No new symbols to append from {src_sym_file.name}")
        
    return appended_any


def process_zip(zip_path : Path):
    """Processes a single ZIP file, adding symbols, localizing footprints, and copying 3D models."""
    
    tempdir = INPUT_ZIP_FOLDER / "temp_extracted"
    if tempdir.exists():
        shutil.rmtree(tempdir)
    tempdir.mkdir(exist_ok=True)
    
    if TEMP_MAP_FILE.exists():
        TEMP_MAP_FILE.unlink()
    
    # print(f"Extracting to: {tempdir}")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(tempdir)
        
    symbols_added = False
    
    # --- 1. Process Symbols (Builds the map) ---
    for sym_file in tempdir.rglob("*.kicad_sym"):
        if append_symbols_from_file(sym_file):
            symbols_added = True
        
    if not symbols_added:
        print("\nWarning: Skipping footprint and 3D model copy because no new symbols were added.")
        shutil.rmtree(tempdir)
        if TEMP_MAP_FILE.exists():
            TEMP_MAP_FILE.unlink()
        return

    # --- 2. Process Footprints (Localize 3D Path and Copy) ---
    for mod_file in tempdir.rglob("*.kicad_mod"):
        dest = PROJECT_FOOTPRINT_LIB / mod_file.name
        
        if dest.exists():
            print(f"Warning: Skipped footprint \"{mod_file.name}\": Already exists in \"{PROJECT_FOOTPRINT_LIB.name}\"")
        else:
            modified_content = localize_3d_model_path(mod_file)
            
            if modified_content is not None:
                with open(dest, 'w', encoding='utf-8') as f:
                    f.write(modified_content)
                print(f"Added footprint \"{mod_file.name}\" to \"{PROJECT_FOOTPRINT_LIB.name}\" (3D path localized)")
            else:
                shutil.copy(mod_file, dest)
                print(f"Warning: Added footprint \"{mod_file.name}\" to \"{PROJECT_FOOTPRINT_LIB.name}\" (3D path NO localization)")
        
    # --- 3. Process 3D Models (Copy the Symbol-Named STP files) ---
    for step_file in tempdir.rglob("*stp"):
        dest = PROJECT_3D_DIR / step_file.name
        
        if dest.exists():
            print(f"Warning: Skipped 3D model \"{step_file.name}\": Already exists in \".\\{PROJECT_3D_DIR.name}\"")
        else:
            shutil.copy(step_file, dest)
            print(f"Added 3D model \"{step_file.name}\" to \".\\{PROJECT_3D_DIR.name}\"")

    # Clean up temp directory and map
    shutil.rmtree(tempdir)
    if TEMP_MAP_FILE.exists():
        TEMP_MAP_FILE.unlink()

def purge_zip_contents(zip_path: Path):
    """
    Deletes symbols, footprints, and 3D models from the project libraries 
    that were contained within the specified ZIP file.
    """
    tempdir = INPUT_ZIP_FOLDER / "temp_extracted_purge"
    if tempdir.exists():
        shutil.rmtree(tempdir)
    tempdir.mkdir(exist_ok=True)
    
    print(f"\n--- Purging contents of {zip_path.name} ---")
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            all_zip_names = zip_ref.namelist()
    except Exception as e:
        print(f"Error reading ZIP file {zip_path.name}: {e}")
        shutil.rmtree(tempdir)
        return

    # --- 1. Identify and Delete Symbols ---
    symbols_to_delete = []
    
    for name in all_zip_names:
        if name.endswith(".kicad_sym"):
            try:
                # Extract only the symbol file temporarily to read its contents
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extract(name, tempdir)
                
                extracted_sym_file = tempdir / name
                with open(extracted_sym_file, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # Get the main symbol names (base names)
                all_symbols = re.findall(r'\(symbol\s+"([^"]+)"', content) 
                
                for sym_name in all_symbols:
                    base_name = SUB_PART_PATTERN.sub('', sym_name)
                    if base_name not in symbols_to_delete:
                        symbols_to_delete.append(base_name)
                        
            except Exception as e:
                print(f"Error processing symbol file {name} during purge: {e}")

    if symbols_to_delete and PROJECT_SYMBOL_LIB.exists():
        print(f"Attempting to delete {len(symbols_to_delete)} main symbols from {PROJECT_SYMBOL_LIB.name}...")
        
        with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
            project_content = f.read()
        
        deleted_count = 0
        
        for base_name in symbols_to_delete:
            # Regex targets the symbol block, matching the base name or any variant: "(symbol "BASE_NAME...")"
            delete_pattern = re.compile(rf'^\s*\(symbol\s+"{re.escape(base_name)}(_.*?)?".*?\n\)', re.DOTALL | re.MULTILINE)
            
            new_content, count = delete_pattern.subn('', project_content)
            
            if new_content != project_content:
                project_content = new_content
                deleted_count += count
        
        if deleted_count > 0:
            # Write the modified content back to the symbol library
            with open(PROJECT_SYMBOL_LIB, "w", encoding="utf-8") as f:
                f.write(project_content)
            print(f"Deleted {deleted_count} symbol block(s) corresponding to {len(symbols_to_delete)} main symbols.")
        else:
            print("No matching symbols found for deletion.")

    # --- 2. Identify and Delete Footprints (.kicad_mod) ---
    footprint_names_in_zip = [Path(name).name for name in all_zip_names if name.endswith(".kicad_mod")]
    
    deleted_fp_count = 0
    for fp_name in footprint_names_in_zip:
        fp_path = PROJECT_FOOTPRINT_LIB / fp_name
        if fp_path.exists():
            fp_path.unlink()
            deleted_fp_count += 1
            
    print(f"Deleted {deleted_fp_count} footprints from {PROJECT_FOOTPRINT_LIB.name}.")


    # --- 3. Identify and Delete 3D Models (.stp) ---
    deleted_3d_count = 0
    stp_names_in_zip = [Path(name).name for name in all_zip_names if name.lower().endswith(".stp")]
    
    for stp_name in stp_names_in_zip:
        stp_path = PROJECT_3D_DIR / stp_name
        if stp_path.exists():
            stp_path.unlink()
            deleted_3d_count += 1

    print(f"Deleted {deleted_3d_count} 3D model files from {PROJECT_3D_DIR.name}.")

    # Cleanup temp directory
    shutil.rmtree(tempdir)