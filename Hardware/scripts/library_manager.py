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

# --- S-expression Library ---
from sexpdata import loads, dumps, Symbol # <-- NEW: Import S-expression tools
# ---------------------------

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
PROJECT_DIR = Path(os.path.abspath(".."))
PROJECT_SYMBOL_LIB = PROJECT_DIR / "Lib-Symbols" / "ProjectSymbols.kicad_sym"
PROJECT_FOOTPRINT_LIB = PROJECT_DIR / "Lib-Footprints" / "ProjectFootprints.pretty"
PROJECT_3D_DIR = PROJECT_DIR / "Lib-3D-Files" 
PROJECT_FOOTPRINT_LIB_NAME = PROJECT_FOOTPRINT_LIB.stem # Used for localization

# --- Global Regex Definitions (Minimizing use) ---

# Only keep regex for cleaning symbol sub-parts, which is easier than S-expression tree traversal
SUB_PART_PATTERN = re.compile(r'_\d(_\d)+$|_\d$') 

# Temporary file for the Footprint-to-Symbol map
TEMP_MAP_FILE = INPUT_ZIP_FOLDER / "footprint_to_symbol_map.json" 

# --- Ensure Project Directories Exist ---

os.makedirs(PROJECT_SYMBOL_LIB.parent, exist_ok=True)
os.makedirs(PROJECT_FOOTPRINT_LIB, exist_ok=True)
os.makedirs(PROJECT_3D_DIR, exist_ok=True)
os.makedirs(INPUT_ZIP_FOLDER, exist_ok=True)

# --------------------------------------------------------------------------------------------------
#                                 CORE FUNCTION LOGIC
# --------------------------------------------------------------------------------------------------

# --- Helper function for finding elements in S-expression list structures ---
def find_sexp_element(sexp_list, target_tag):
    """
    Searches a flat list of S-expression elements for a list that starts with the target tag.
    Returns the element (list) or None.
    """
    target_sym = Symbol(target_tag)
    for element in sexp_list:
        if isinstance(element, list) and len(element) > 0 and (element[0] == target_tag or element[0] == target_sym):
            return element
    return None

def find_sexp_property(sexp_list, prop_name):
    """
    Searches a list of S-expression elements for a property list with the given name.
    Returns the element (list) or None.
    """
    prop_sym = Symbol('property')
    for element in sexp_list:
        if isinstance(element, list) and len(element) > 2 and (element[0] == 'property' or element[0] == prop_sym):
            if str(element[1]) == prop_name:
                return element
    return None

def list_symbols_simple(sym_file: Path, print_list: bool = True):
    """
    Returns a list of main symbol names from a KiCad library file using S-expression parsing.
    Skips common sub-part/alternate unit suffixes (_X_Y).
    """
    if not sym_file.exists():
        if print_list:
            print(f"File not found: {sym_file.name}")
        return []

    try:
        with open(sym_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Load S-expression. KiCad files always wrap content in (kicad_symbol_lib ...)
        sexp_list = loads(content) 
    except Exception as e:
        if print_list:
            print(f"ERROR: Failed to parse S-expression in {sym_file.name}: {e}")
        return []

    symbols = []
    
    # Iterate over the elements in the main list (skipping the main tag)
    for element in sexp_list[1:]: 
        # Check if the element is a list starting with the symbol 'symbol'
        if isinstance(element, list) and len(element) > 1 and (element[0] == 'symbol' or element[0] == Symbol('symbol')):
            # The symbol name is typically the second element in the list
            symbol_name = str(element[1]) 
            
            if not SUB_PART_PATTERN.search(symbol_name):
                symbols.append(symbol_name)

    if print_list:
        print(f"Found {len(symbols)} (main) symbols in {sym_file.name}:")
        if symbols:
            print(", ".join(symbols))
        else:
            print("No main symbols found.")

    return symbols


def get_existing_main_symbols():
    """
    Wrapper to get the set of main symbols currently in the project library.
    """
    return set(list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=False))


# --- FUNCTION localize_footprint_path() IS NOW OBSOLETE AND REMOVED ---


def localize_3d_model_path(mod_file: Path, footprint_map: dict):
    """
    Reads a .kicad_mod file, uses the map to find the symbol name, 
    replaces the 3D model path using S-expression modification.
    Returns the modified content string or None on error/skip.
    """
    
    footprint_name = mod_file.stem
    symbol_name = footprint_map.get(footprint_name)

    if not symbol_name:
        # print(f"Warning: Footprint {footprint_name} was not found in the symbol map. Skipping 3D localization.")
        return None

    try:
        with open(mod_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        mod_sexp = loads(content)
    except Exception as e:
        print(f"Error reading or parsing {mod_file.name}: {e}")
        return None

    # KiCad footprints are top-level S-expressions. We iterate over elements in the top list.
    model_elements = [e for e in mod_sexp if isinstance(e, list) and len(e) > 0 and (e[0] == 'model' or e[0] == Symbol('model'))]
    
    modified = False
    
    for model_element in model_elements:
        # The path is typically the second element in the (model ...) list
        if len(model_element) > 1:
            model_filename = symbol_name + ".stp"
            target_path = f'${{KIPRJMOD}}/Lib-3D-Files/{model_filename}'
            
            # Modify the path in the S-expression list
            model_element[1] = target_path
            modified = True
        
    if modified:
        # Use dumps to serialize the modified S-expression list back into a string
        return dumps(mod_sexp, pretty_print=True)
    
    # If no model tag was found, return None to indicate no localization happened (original file is copied)
    return None 

def append_symbols_from_file(src_sym_file: Path):
    """
    Appends symbols from a source KiCad library file to the project library using
    S-expression parsing. Also builds a map linking Footprint names to their main Symbol names.
    """
    
    existing_main_symbols = get_existing_main_symbols()

    # Load existing map or start fresh
    footprint_map = {}
    if TEMP_MAP_FILE.exists():
        with open(TEMP_MAP_FILE, 'r') as f:
            footprint_map = json.load(f)

    try:
        with open(src_sym_file, "r", encoding="utf-8") as f:
            src_content = f.read()
            src_sexp = loads(src_content) 
    except FileNotFoundError:
        print(f"ERROR: Source file not found: {src_sym_file.name}")
        return False
    except Exception as e:
        print(f"ERROR parsing source S-expression file {src_sym_file.name}: {e}")
        return False

    symbols_to_append_sexp = []
    appended_any = False

    # Iterate over the elements in the top-level list (skipping the main tag)
    for element in src_sexp[1:]: 
        # Check if the element is a symbol block
        if isinstance(element, list) and len(element) > 1 and (element[0] == 'symbol' or element[0] == Symbol('symbol')):
            
            symbol_name = str(element[1]) 
            base_name = SUB_PART_PATTERN.sub('', symbol_name)

            if base_name not in existing_main_symbols:
                
                # --- Localization and Mapping ---
                raw_footprint_name = None
                
                # Find the footprint property list within the symbol's sub-elements
                prop_element = find_sexp_property(element, 'Footprint')
                
                if prop_element:
                    # The value is at index 2 (e.g., ['property', 'Footprint', 'Value', 'at',...])
                    raw_footprint_name = str(prop_element[2]).split(':')[-1] 
                    
                    # --- Localiize Footprint Path Directly in the S-expression List ---
                    # Update the value in the list element
                    new_fp_value = f"{PROJECT_FOOTPRINT_LIB_NAME}:{raw_footprint_name}"
                    prop_element[2] = new_fp_value 
                    
                # Find the 'footprint' definition list (newer KiCad syntax)
                footprint_element = find_sexp_element(element, 'footprint')
                if footprint_element and len(footprint_element) > 1:
                    # The value is the second element (e.g., ['footprint', 'Lib:FP_Name'])
                    # Localize if necessary
                    fp_value = str(footprint_element[1])
                    if ':' in fp_value:
                        name_only = fp_value.split(':')[-1]
                        footprint_element[1] = f"{PROJECT_FOOTPRINT_LIB_NAME}:{name_only}"
                    
                
                if raw_footprint_name:
                    footprint_map[raw_footprint_name] = base_name 
                # ----------------------------------------

                symbols_to_append_sexp.append(element)
                
                if symbol_name == base_name:
                    existing_main_symbols.add(symbol_name)

                appended_any = True
                print(f"Appended symbol: {symbol_name} (Footprint link localized)")
            else:
                pass # Symbol already exists

    # Save the updated map
    if appended_any:
        with open(TEMP_MAP_FILE, 'w') as f:
            json.dump(footprint_map, f, indent=4)

    if symbols_to_append_sexp:
        project_sym_path = PROJECT_SYMBOL_LIB
        new_file_content = None
        
        if project_sym_path.exists():
            try:
                with open(project_sym_path, "r", encoding="utf-8") as f:
                    project_content = f.read()
                
                project_sexp = loads(project_content)
                
                # Append the new symbol lists directly to the project list
                project_sexp.extend(symbols_to_append_sexp)
                
                # Dump the entire modified list back to a string
                new_file_content = dumps(project_sexp, pretty_print=True) 

            except Exception as e:
                print(f"ERROR modifying project library using S-expression parser: {e}. Recreating file.")
                project_sym_path.unlink(missing_ok=True)
                new_file_content = None # Fall through to the 'else' block
        
        if not project_sym_path.exists() or new_file_content is None:
            # Creation of the very first file or recovery from error
            header = [['version', 20211026], ['generator', 'script-generator']]
            
            # Combine header and symbols into a single list
            full_sexp = header + symbols_to_append_sexp
            
            # Dump the entire list, wrapping it in the main tag
            new_file_content = dumps(full_sexp, wrap=Symbol('kicad_symbol_lib'), pretty_print=True)

        if new_file_content:
            with open(project_sym_path, "w", encoding="utf-8") as f:
                f.write(new_file_content)

    if not appended_any:
        print(f"No new symbols to append from {src_sym_file.name}")

    return appended_any


def process_zip(zip_path : Path):
    """Processes a single ZIP file, adding symbols, localizing footprints, and copying 3D models."""
    # ... (Temp directory handling remains the same)

    tempdir = INPUT_ZIP_FOLDER / "temp_extracted"
    if tempdir.exists():
        shutil.rmtree(tempdir)
    tempdir.mkdir(exist_ok=True)

    # Note: We must create the map *before* processing footprints/3D models
    if TEMP_MAP_FILE.exists():
        TEMP_MAP_FILE.unlink()
        
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tempdir)
    except Exception as e:
        print(f"ERROR extracting ZIP file {zip_path.name}: {e}")
        shutil.rmtree(tempdir)
        return

    symbols_added = False

    # --- 1. Process Symbols (Builds the map) ---
    for sym_file in tempdir.rglob("*.kicad_sym"):
        if append_symbols_from_file(sym_file):
            symbols_added = True

    if not symbols_added and not TEMP_MAP_FILE.exists():
        print("\nWarning: Skipping footprint and 3D model copy because no new symbols were added.")
        shutil.rmtree(tempdir)
        return
        
    # Load the map now for use in step 2
    footprint_map = {}
    if TEMP_MAP_FILE.exists():
        with open(TEMP_MAP_FILE, 'r') as f:
            footprint_map = json.load(f)


    # --- 2. Process Footprints (Localize 3D Path and Copy) ---
    for mod_file in tempdir.rglob("*.kicad_mod"):
        dest = PROJECT_FOOTPRINT_LIB / mod_file.name

        if dest.exists():
            print(f"Warning: Skipped footprint \"{mod_file.name}\": Already exists in \"{PROJECT_FOOTPRINT_LIB.name}\"")
        else:
            # Pass the footprint_map to the localization function
            modified_content = localize_3d_model_path(mod_file, footprint_map)

            if modified_content is not None:
                with open(dest, 'w', encoding='utf-8') as f:
                    f.write(modified_content)
                print(f"Added footprint \"{mod_file.name}\" to \"{PROJECT_FOOTPRINT_LIB.name}\" (3D path localized)")
            else:
                # If localization returns None, copy the original file
                shutil.copy(mod_file, dest)
                print(f"Warning: Added footprint \"{mod_file.name}\" to \"{PROJECT_FOOTPRINT_LIB.name}\" (3D path NO localization)")

    # --- 3. Process 3D Models (Copy the Symbol-Named STP files) ---
    # ... (This section is file-copying and requires no S-expression changes)

    # Clean up temp directory and map
    shutil.rmtree(tempdir)
    if TEMP_MAP_FILE.exists():
        TEMP_MAP_FILE.unlink()


def purge_zip_contents(zip_path: Path):
    """
    Deletes symbols, footprints, and 3D models from the project libraries 
    that were contained within the specified ZIP file, using S-expression parsing 
    for reliable symbol deletion.
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

    # --- 1. Identify Symbols to Delete (using S-expression to read) ---
    symbols_to_delete = []

    for name in all_zip_names:
        if name.endswith(".kicad_sym"):
            try:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extract(name, tempdir)

                extracted_sym_file = tempdir / name
                
                # --- NEW: S-expression reading for purge list ---
                with open(extracted_sym_file, "r", encoding="utf-8") as f:
                    content = f.read()
                
                sexp_list = loads(content) 

                for element in sexp_list[1:]: 
                    if isinstance(element, list) and len(element) > 1 and (element[0] == 'symbol' or element[0] == Symbol('symbol')):
                        sym_name = str(element[1])
                        base_name = SUB_PART_PATTERN.sub('', sym_name)
                        if base_name not in symbols_to_delete:
                            symbols_to_delete.append(base_name)
                # --------------------------------------------------

            except Exception as e:
                print(f"Error processing symbol file {name} during purge: {e}")

    if symbols_to_delete and PROJECT_SYMBOL_LIB.exists():
        print(f"Attempting to delete {len(symbols_to_delete)} main symbols from {PROJECT_SYMBOL_LIB.name}...")

        try:
            with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
                project_content = f.read()
            
            project_sexp = loads(project_content)
            
            deleted_count = 0
            
            # Create a new list for the remaining content
            new_project_sexp = [project_sexp[0]] # Keep the header tag
            
            # Iterate through all elements (symbols) in the project list, starting after the header
            for element in project_sexp[1:]:
                # Check if the element is a symbol block
                if isinstance(element, list) and len(element) > 1 and (element[0] == 'symbol' or element[0] == Symbol('symbol')):
                    symbol_name = str(element[1])
                    base_name = SUB_PART_PATTERN.sub('', symbol_name)
                    
                    # Check if the base name should be deleted
                    if base_name in symbols_to_delete:
                        deleted_count += 1
                        # Skip adding this element to new_project_sexp
                        continue 
                        
                # Keep all other elements (headers, comments, and symbols that aren't being purged)
                new_project_sexp.append(element)
                
            if deleted_count > 0:
                # Dump the modified list back to the file
                with open(PROJECT_SYMBOL_LIB, "w", encoding="utf-8") as f:
                    f.write(dumps(new_project_sexp, pretty_print=True))
                print(f"Deleted {deleted_count} symbol block(s) corresponding to {len(set(symbols_to_delete))} main symbols.")
            else:
                print("No matching symbols found for deletion.")

        except Exception as e:
            print(f"ERROR during S-expression symbol deletion: {e}")


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