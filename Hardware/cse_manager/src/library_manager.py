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
from sexpdata import loads, dumps, Symbol 
# ---------------------------

def find_upward(target: str, start_path: Path) -> Path | None:
    """
    Search upward from start_path to find either:
    - a folder with the given name, OR
    - a file matching the given glob pattern.

    Returns the Path to the folder/file, or None if not found.
    """
    for parent in [start_path] + list(start_path.parents):
        # Folder search (exact match)
        candidate = parent / target
        if candidate.exists() and candidate.is_dir():
            return candidate

        # File search (wildcard pattern, e.g. "*.kicad_pro")
        matches = list(parent.glob(target))
        if matches:
            return matches[0]

    return None

# --- Environment Setup (Load once for all functions) ---

load_dotenv()

# get path of executable / script
base_path = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent

# find kicad project files
project_file = find_upward("*.kicad_pro", base_path)
if not project_file:
    raise RuntimeError("No KiCad project (*.kicad_pro) found.")
PROJECT_DIR = project_file.parent
PROJECT_SYMBOL_LIB = PROJECT_DIR / "Lib-Symbols" / "ProjectSymbols.kicad_sym"
PROJECT_FOOTPRINT_LIB = PROJECT_DIR / "Lib-Footprints" / "ProjectFootprints.pretty"
PROJECT_3D_DIR = PROJECT_DIR / "Lib-3D-Files" 
PROJECT_FOOTPRINT_LIB_NAME = PROJECT_FOOTPRINT_LIB.stem  # used for footprint path localization (e.g., ProjectFootprints:FP_Name)

# find script input folder
input_folder_name = os.getenv("INPUT_ZIP_FOLDER", "library_input")
INPUT_ZIP_FOLDER = find_upward(input_folder_name, base_path)
if INPUT_ZIP_FOLDER is None:
    raise RuntimeError(f"Input folder \"{input_folder_name}\" not found in current or parent directories.")

# Create directories if not already there
os.makedirs(PROJECT_SYMBOL_LIB.parent, exist_ok=True)
os.makedirs(PROJECT_FOOTPRINT_LIB, exist_ok=True)
os.makedirs(PROJECT_3D_DIR, exist_ok=True)
os.makedirs(INPUT_ZIP_FOLDER, exist_ok=True)

# --- Global Regex Definitions (Minimizing use) ---

# Regex to identify and remove symbol sub-part/alternate unit suffixes (e.g., _A, _B_C, _1_1)
SUB_PART_PATTERN = re.compile(r'_\d(_\d)+$|_\d$') 

# Temporary file to map footprint names (from .kicad_mod) to their main symbol name (for 3D model path linking)
TEMP_MAP_FILE = INPUT_ZIP_FOLDER / "footprint_to_symbol_map.json" 


# --------------------------------------------------------------------------------------------------
#                                 CORE FUNCTION LOGIC
# --------------------------------------------------------------------------------------------------

# --- Helper function for finding elements in S-expression list structures ---
def find_sexp_element(sexp_list, target_tag):
    """
    Searches a flat list of S-expression elements for a list that starts with the target tag.
    Returns the element (list) or None.
    """
    target_sym = Symbol(target_tag)
    for element in sexp_list:
        # Check if the element is a list starting with the target tag string or Symbol
        if isinstance(element, list) and len(element) > 0 and (element[0] == target_tag or element[0] == target_sym):
            return element
    return None

def find_sexp_property(sexp_list, prop_name):
    """
    Searches a list of S-expression elements for a KiCad 'property' list with the given name.
    Returns the element (list) or None.
    """
    prop_sym = Symbol('property')
    for element in sexp_list:
        # Check for the structure: ['property', 'Name', 'Value', ...]
        if isinstance(element, list) and len(element) > 2 and (element[0] == 'property' or element[0] == prop_sym):
            # Check if the property name (index 1) matches
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
        
        # Load S-expression. The top level is (kicad_symbol_lib (symbol ...))
        sexp_list = loads(content) 
    except Exception as e:
        if print_list:
            print(f"ERROR: Failed to parse S-expression in {sym_file.name}: {e}")
        return []

    symbols = []
    
    # Iterate over the elements in the main list, skipping the top-level tag (kicad_symbol_lib)
    for element in sexp_list[1:]: 
        # Identify elements that are a KiCad symbol block: (symbol "Name" ...)
        if isinstance(element, list) and len(element) > 1 and (element[0] == 'symbol' or element[0] == Symbol('symbol')):
            # The symbol name is the second element in the list
            symbol_name = str(element[1]) 
            
            # Filter out sub-parts or alternate units to only list the main symbol
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
    Wrapper to get the set of main symbols currently in the project library for quick lookup.
    """
    return set(list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=False))


def localize_3d_model_path(mod_file: Path, footprint_map: dict):
    """
    Reads a .kicad_mod (footprint) file, uses the footprint_map to find the associated main symbol name, 
    and replaces the 3D model path to use the ${KIPRJMOD} variable.
    Returns the modified content string or None on error or if no model tag is found.
    """
    
    footprint_name = mod_file.stem
    # The symbol name is used to generate the localized 3D model filename (SymbolName.stp)
    symbol_name = footprint_map.get(footprint_name)

    if not symbol_name:
        return None

    try:
        with open(mod_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        mod_sexp = loads(content)
    except Exception as e:
        print(f"Error reading or parsing {mod_file.name}: {e}")
        return None

    # KiCad footprints are top-level S-expressions. The 'model' tag is for 3D model links.
    model_elements = [e for e in mod_sexp if isinstance(e, list) and len(e) > 0 and (e[0] == 'model' or e[0] == Symbol('model'))]
    
    modified = False
    
    for model_element in model_elements:
        # The path is typically the second element in the (model ...) list
        if len(model_element) > 1:
            # The new 3D model path uses the symbol name and the KiCad project variable
            model_filename = symbol_name + ".stp"
            target_path = f'${{KIPRJMOD}}/Lib-3D-Files/{model_filename}'
            
            # Overwrite the path in the S-expression list
            model_element[1] = target_path
            modified = True
        
    if modified:
        # Serialize the modified S-expression list back into a KiCad formatted string
        return dumps(mod_sexp, pretty_print=True)
    
    # Return None if no 'model' tag was found
    return None 

def append_symbols_from_file(src_sym_file: Path):
    """
    Appends symbols from a source KiCad library file to the project library.
    It localizes the footprint link within the symbol and builds a map linking 
    Footprint names to their main Symbol names (stored in TEMP_MAP_FILE).
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

    # Iterate over the symbol blocks (skipping the main tag)
    for element in src_sexp[1:]: 
        # Check if the element is a symbol block
        if isinstance(element, list) and len(element) > 1 and (element[0] == 'symbol' or element[0] == Symbol('symbol')):
            
            symbol_name = str(element[1]) 
            # Get the name without sub-part suffixes (e.g., U1_A -> U1)
            base_name = SUB_PART_PATTERN.sub('', symbol_name)

            if base_name not in existing_main_symbols:
                
                # --- Localization and Mapping ---
                raw_footprint_name = None
                
                # Find the older 'Footprint' property (e.g., ['property', 'Footprint', 'Value', ...])
                prop_element = find_sexp_property(element, 'Footprint')
                
                if prop_element:
                    # The value is the third element. Split by ':' to get the name only.
                    raw_footprint_name = str(prop_element[2]).split(':')[-1] 
                    
                    # Localize the footprint path to the project library name
                    new_fp_value = f"{PROJECT_FOOTPRINT_LIB_NAME}:{raw_footprint_name}"
                    prop_element[2] = new_fp_value 
                    
                # Find the newer 'footprint' definition list (e.g., ['footprint', 'Lib:FP_Name'])
                footprint_element = find_sexp_element(element, 'footprint')
                if footprint_element and len(footprint_element) > 1:
                    fp_value = str(footprint_element[1])
                    if ':' in fp_value:
                        name_only = fp_value.split(':')[-1]
                        # Localize the footprint path
                        footprint_element[1] = f"{PROJECT_FOOTPRINT_LIB_NAME}:{name_only}"
                        # If the property wasn't found, use the name from the 'footprint' element for mapping
                        if not raw_footprint_name:
                            raw_footprint_name = name_only
                    
                
                if raw_footprint_name:
                    # Map the raw footprint name to its main symbol name for 3D localization later
                    footprint_map[raw_footprint_name] = base_name 
                # ----------------------------------------

                symbols_to_append_sexp.append(element)
                
                if symbol_name == base_name:
                    existing_main_symbols.add(symbol_name)

                appended_any = True
                print(f"Appended symbol: {symbol_name} (Footprint link localized)")
            else:
                pass # Symbol already exists

    # Save the updated map to disk
    if appended_any:
        with open(TEMP_MAP_FILE, 'w') as f:
            json.dump(footprint_map, f, indent=4)

    if symbols_to_append_sexp:
        project_sym_path = PROJECT_SYMBOL_LIB
        new_file_content = None
        
        if project_sym_path.exists():
            try:
                # Read the existing project library file
                with open(project_sym_path, "r", encoding="utf-8") as f:
                    project_content = f.read()
                
                project_sexp = loads(project_content)
                
                # Append the new symbol lists directly to the project list
                project_sexp.extend(symbols_to_append_sexp)
                
                # Serialize the entire modified list back to a string
                new_file_content = dumps(project_sexp, pretty_print=True) 

            except Exception as e:
                print(f"ERROR modifying project library using S-expression parser: {e}. Recreating file.")
                project_sym_path.unlink(missing_ok=True)
                new_file_content = None # Fall through to the 'else' block
        
        if not project_sym_path.exists() or new_file_content is None:
            # Creation of the very first file or recovery from error: add KiCad library header
            header = [['version', 20211026], ['generator', 'script-generator']]
            
            # Combine header and symbols into a single list
            full_sexp = header + symbols_to_append_sexp
            
            # Wrap the content in the main KiCad tag for a new file
            new_file_content = dumps(full_sexp, wrap=Symbol('kicad_symbol_lib'), pretty_print=True)

        if new_file_content:
            # Write the final, updated symbol library content
            with open(project_sym_path, "w", encoding="utf-8") as f:
                f.write(new_file_content)

    if not appended_any:
        print(f"No new symbols to append from {src_sym_file.name}")

    return appended_any


def process_zip(zip_path : Path):
    """Processes a single ZIP file: extracts, adds symbols (localizing footprint links and building a map), 
    localizes 3D model paths in footprints, and copies footprints and 3D models to project folders."""
    
    tempdir = INPUT_ZIP_FOLDER / "temp_extracted"
    # Ensure a clean temporary directory
    if tempdir.exists():
        shutil.rmtree(tempdir)
    tempdir.mkdir(exist_ok=True)

    # Clear any previous map file before starting the new process
    if TEMP_MAP_FILE.exists():
        TEMP_MAP_FILE.unlink()
        
    try:
        # Extract all contents of the ZIP file
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tempdir)
    except Exception as e:
        print(f"ERROR extracting ZIP file {zip_path.name}: {e}")
        shutil.rmtree(tempdir)
        return

    symbols_added = False

    # --- 1. Process Symbols (Builds the footprint_to_symbol_map) ---
    for sym_file in tempdir.rglob("*.kicad_sym"):
        if append_symbols_from_file(sym_file):
            symbols_added = True

    # If no symbols were added and no map was created, skip the rest
    if not symbols_added and not TEMP_MAP_FILE.exists():
        print("\nWarning: Skipping footprint and 3D model copy because no new symbols were added.")
        shutil.rmtree(tempdir)
        return
        
    # Load the map now, which contains the Footprint name -> Main Symbol name link
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
            # Modify the content string, localizing the 3D model path if a mapping exists
            modified_content = localize_3d_model_path(mod_file, footprint_map)

            if modified_content is not None:
                # Write the localized content
                with open(dest, 'w', encoding='utf-8') as f:
                    f.write(modified_content)
                print(f"Added footprint \"{mod_file.name}\" to \"{PROJECT_FOOTPRINT_LIB.name}\" (3D path localized)")
            else:
                # If localization failed or wasn't needed, copy the original file
                shutil.copy(mod_file, dest)
                print(f"Warning: Added footprint \"{mod_file.name}\" to \"{PROJECT_FOOTPRINT_LIB.name}\" (3D path NO localization)")

    # --- 3. Process 3D Models (Copy the Symbol-Named STP files) ---
    copied_3d_count = 0
    for stp_file in tempdir.rglob("*.stp"):
        # Copy to the 3D directory using its original name (which should match the symbol name)
        dest_file = PROJECT_3D_DIR / stp_file.name
        
        if dest_file.exists():
            print(f"Warning: Skipped 3D model \"{stp_file.name}\": Already exists in \"{PROJECT_3D_DIR.name}\"")
            continue
        
        try:
            shutil.copy(stp_file, dest_file)
            print(f"Copied 3D model \"{stp_file.name}\" to \"{PROJECT_3D_DIR.name}\"")
            copied_3d_count += 1
        except Exception as e:
            print(f"ERROR copying 3D model {stp_file.name}: {e}")

    if copied_3d_count == 0:
        print("No new 3D model files found or copied.")

    # Clean up temp directory and map file
    shutil.rmtree(tempdir)
    if TEMP_MAP_FILE.exists():
        TEMP_MAP_FILE.unlink()


def purge_zip_contents(zip_path: Path):
    """
    Deletes symbols, footprints, and 3D models from the project libraries 
    that were contained within the specified ZIP file. Uses S-expression parsing 
    to reliably remove symbol blocks and their associated sub-parts.
    """
    tempdir = INPUT_ZIP_FOLDER / "temp_extracted_purge"
    # Ensure a clean temporary directory for reading the ZIP contents
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
    # This list will contain the base names (e.g., 'Resistor')
    symbols_to_delete = []

    for name in all_zip_names:
        if name.endswith(".kicad_sym"):
            try:
                # Extract the symbol file to read its contents
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extract(name, tempdir)

                extracted_sym_file = tempdir / name
                
                # Load S-expression content of the symbol file from the ZIP
                with open(extracted_sym_file, "r", encoding="utf-8") as f:
                    content = f.read()
                
                sexp_list = loads(content) 

                # Iterate through all symbol blocks in the extracted file
                for element in sexp_list[1:]: 
                    if isinstance(element, list) and len(element) > 1 and (element[0] == 'symbol' or element[0] == Symbol('symbol')):
                        sym_name = str(element[1])
                        # Get the base name (e.g., U1_A -> U1)
                        base_name = SUB_PART_PATTERN.sub('', sym_name)
                        if base_name not in symbols_to_delete:
                            symbols_to_delete.append(base_name)

            except Exception as e:
                print(f"Error processing symbol file {name} during purge: {e}")

    # --- 1b. Delete Symbols from Project Library ---
    if symbols_to_delete and PROJECT_SYMBOL_LIB.exists():
        print(f"Attempting to delete {len(symbols_to_delete)} main symbols from {PROJECT_SYMBOL_LIB.name}...")

        try:
            # Read the entire project library file
            with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
                project_content = f.read()
            
            project_sexp = loads(project_content)
            
            deleted_count = 0
            
            # Start the new list with the header tag (kicad_symbol_lib)
            new_project_sexp = [project_sexp[0]] 
            
            # Iterate through all elements in the project list, starting after the header
            for element in project_sexp[1:]:
                # Check if the element is a symbol block
                if isinstance(element, list) and len(element) > 1 and (element[0] == 'symbol' or element[0] == Symbol('symbol')):
                    symbol_name = str(element[1])
                    base_name = SUB_PART_PATTERN.sub('', symbol_name)
                    
                    # If the base name is scheduled for deletion, skip this element
                    if base_name in symbols_to_delete:
                        deleted_count += 1
                        continue 
                        
                # Keep all other elements (headers, comments, and symbols that aren't being purged)
                new_project_sexp.append(element)
                
            if deleted_count > 0:
                # Dump the filtered list back to the file
                with open(PROJECT_SYMBOL_LIB, "w", encoding="utf-8") as f:
                    f.write(dumps(new_project_sexp, pretty_print=True))
                print(f"Deleted {deleted_count} symbol block(s) corresponding to {len(set(symbols_to_delete))} main symbols.")
            else:
                print("No matching symbols found for deletion.")

        except Exception as e:
            print(f"ERROR during S-expression symbol deletion: {e}")


    # --- 2. Identify and Delete Footprints (.kicad_mod) ---
    # Footprints are deleted by file name
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
    # 3D models are deleted by file name
    stp_names_in_zip = [Path(name).name for name in all_zip_names if name.lower().endswith(".stp")]

    for stp_name in stp_names_in_zip:
        stp_path = PROJECT_3D_DIR / stp_name
        if stp_path.exists():
            stp_path.unlink()
            deleted_3d_count += 1

    print(f"Deleted {deleted_3d_count} 3D model files from {PROJECT_3D_DIR.name}.")

    # Cleanup temp directory
    shutil.rmtree(tempdir)