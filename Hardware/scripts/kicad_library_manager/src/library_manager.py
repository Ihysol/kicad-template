# SPDX-License-Identifier: BSD-3-Clause
# Cleaned and formatted (conservative refactor) by ChatGPT
# Original functionality preserved

import os
import shutil
import sys
import re
from dotenv import load_dotenv
import zipfile
from pathlib import Path
import json
from datetime import datetime

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
base_path = (
    Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
)

# find kicad project files
project_file = find_upward("*.kicad_pro", base_path)
if not project_file:
    raise RuntimeError("No KiCad project (*.kicad_pro) found.")
PROJECT_DIR = project_file.parent
PROJECT_SYMBOL_LIB = PROJECT_DIR / "symbols" / "ProjectSymbols.kicad_sym"
PROJECT_FOOTPRINT_LIB = PROJECT_DIR / "footprints" / "ProjectFootprints.pretty"
PROJECT_3D_DIR = PROJECT_DIR / "3dmodels"
PROJECT_FOOTPRINT_LIB_NAME = (
    PROJECT_FOOTPRINT_LIB.stem
)  # used for footprint path localization (e.g., ProjectFootprints:FP_Name)

# find script input folder
input_folder_name = os.getenv("INPUT_ZIP_FOLDER", "library_input")
INPUT_ZIP_FOLDER = find_upward(input_folder_name, base_path)
if INPUT_ZIP_FOLDER is None:
    raise RuntimeError(
        f'Input folder "{input_folder_name}" not found in current or parent directories.'
    )

# Create directories if not already there
os.makedirs(PROJECT_SYMBOL_LIB.parent, exist_ok=True)
os.makedirs(PROJECT_FOOTPRINT_LIB, exist_ok=True)
os.makedirs(PROJECT_3D_DIR, exist_ok=True)
os.makedirs(INPUT_ZIP_FOLDER, exist_ok=True)

# --- Global Regex Definitions ---

# Regex to identify and remove symbol sub-part/alternate unit suffixes (e.g., _A, _B_C, _1_1)
SUB_PART_PATTERN = re.compile(r"_\d(_\d)+$|_\d$")

# Temporary file to map footprint names (from .kicad_mod) to their main symbol name (for 3D model path linking)
TEMP_MAP_FILE = INPUT_ZIP_FOLDER / "footprint_to_symbol_map.json"

# --------------------------------------------------------------------------------------------------
#                 CORE FUNCTION LOGIC
# --------------------------------------------------------------------------------------------------

# --- Helper functions for S-expression parsing ---

def find_sexp_element(sexp_list, target_tag):
    """Searches a list of S-expression elements for a list that starts with the target tag."""
    target_sym = Symbol(target_tag)
    for element in sexp_list:
        if (
            isinstance(element, list)
            and len(element) > 0
            and (element[0] == target_tag or element[0] == target_sym)
        ):
            return element
    return None

def find_sexp_property(sexp_list, prop_name):
    """Searches a list of S-expression elements for a KiCad 'property' list with the given name."""
    prop_sym = Symbol("property")
    for element in sexp_list:
        if (
            isinstance(element, list)
            and len(element) > 2
            and (element[0] == "property" or element[0] == prop_sym)
        ):
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
        sexp_list = loads(content)
    except Exception as e:
        if print_list:
            print(f"ERROR: Failed to parse S-expression in {sym_file.name}: {e}")
        return []

    symbols = []

    for element in sexp_list[1:]:
        if (
            isinstance(element, list)
            and len(element) > 1
            and (element[0] == "symbol" or element[0] == Symbol("symbol"))
        ):
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
    """Wrapper to get the set of main symbols currently in the project library for quick lookup."""
    return set(list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=False))

def localize_3d_model_path(mod_file: Path, footprint_map: dict):
    """
    Reads a .kicad_mod (footprint) file, uses the footprint_map to find the associated main symbol name,
    and replaces the 3D model path to use the ${KIPRJMOD} variable and the symbol name.
    Returns the modified content string or None on error or if no model tag is found.
    """

    footprint_name = mod_file.stem
    # Use the footprint name (which might be the symbol name if renaming occurred) to get the symbol name
    symbol_name = footprint_map.get(footprint_name)

    if not symbol_name:
        return None

    try:
        with open(mod_file, "r", encoding="utf-8") as f:
            content = f.read()
        mod_sexp = loads(content)
    except Exception as e:
        print(f"Error reading or parsing {mod_file.name}: {e}")
        return None

    model_elements = [
        e
        for e in mod_sexp
        if isinstance(e, list)
        and len(e) > 0
        and (e[0] == "model" or e[0] == Symbol("model"))
    ]

    modified = False

    for model_element in model_elements:
        if len(model_element) > 1:
            # The new 3D model path uses the symbol name
            model_filename = symbol_name + ".stp"
            target_path = f"${{KIPRJMOD}}/3dmodels/{model_filename}"

            # Overwrite the path in the S-expression list
            model_element[1] = target_path
            modified = True

    if modified:
        return dumps(mod_sexp, pretty_print=True)

    return None

def rename_extracted_assets(tempdir: Path, footprint_map: dict):
    """
    Renames the extracted .kicad_mod and .stp files in the temporary directory
    based on the main symbol name from the footprint_map.
    """
    renamed_count = 0

    # 1. --- Rename Footprints (.kicad_mod) ---
    footprint_files_to_rename = list(tempdir.rglob("*.kicad_mod"))

    for mod_file in footprint_files_to_rename:
        footprint_name = mod_file.stem
        symbol_name = footprint_map.get(footprint_name)

        if symbol_name and symbol_name != footprint_name:
            new_name = symbol_name + mod_file.suffix
            new_path = mod_file.parent / new_name
            if not new_path.exists():
                mod_file.rename(new_path)
                renamed_count += 1
                print(f"Renamed Footprint: {mod_file.name} -> {new_name}")

                # CRITICAL: Update the footprint_map:
                # Remove the old entry, and add the new one (mapping symbol_name to itself)
                del footprint_map[footprint_name]
                footprint_map[symbol_name] = symbol_name

    # 2. --- Rename 3D Models (.stp) ---
    for stp_file in tempdir.rglob("*.stp"):
        model_name_stem = stp_file.stem
        target_symbol_name = None

        # A. Check if the model name matches a key in the (pre-rename) map (original footprint name)
        if model_name_stem in footprint_map:
            target_symbol_name = footprint_map[model_name_stem]

        # B. Check if the model name already matches a symbol name (post-rename name)
        elif model_name_stem in footprint_map.values():
            target_symbol_name = model_name_stem  # It is already the symbol name

        if target_symbol_name and target_symbol_name != model_name_stem:
            new_name = target_symbol_name + stp_file.suffix
            new_path = stp_file.parent / new_name

            if not new_path.exists():
                stp_file.rename(new_path)
                renamed_count += 1
                print(f"Renamed 3D Model: {stp_file.name} -> {new_name}")

    if renamed_count > 0:
        # Re-save the map after renaming to ensure subsequent steps use the new name
        with open(TEMP_MAP_FILE, "w") as f:
            json.dump(footprint_map, f, indent=4)
        print(f"INFO: Saved updated footprint_map with {len(footprint_map)} entries.")

    return renamed_count

def append_symbols_from_file(
    src_sym_file: Path, rename_assets=False
):  # <-- RENAME FLAG ADDED
    """
    Appends symbols from a source KiCad library file to the project library.
    It localizes the footprint link (to the project library) and builds a map linking
    Footprint names to their main Symbol names (stored in TEMP_MAP_FILE).
    If rename_assets is True, the footprint link in the symbol is set to the Symbol Name.
    """

    existing_main_symbols = get_existing_main_symbols()

    # Load existing map or start fresh
    footprint_map = {}
    if TEMP_MAP_FILE.exists():
        with open(TEMP_MAP_FILE, "r") as f:
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
        if (
            isinstance(element, list)
            and len(element) > 1
            and (element[0] == "symbol" or element[0] == Symbol("symbol"))
        ):

            symbol_name = str(element[1])
            base_name = SUB_PART_PATTERN.sub("", symbol_name)

            if base_name not in existing_main_symbols:

                # --- Localization and Mapping ---
                raw_footprint_name = None

                # Find the older 'Footprint' property
                prop_element = find_sexp_property(element, "Footprint")
                if prop_element:
                    raw_footprint_name = str(prop_element[2]).split(":")[-1]

                    # MODIFIED: Use symbol name for the new FP link if renaming is active
                    fp_name_for_link = (
                        base_name if rename_assets else raw_footprint_name
                    )
                    new_fp_value = f"{PROJECT_FOOTPRINT_LIB_NAME}:{fp_name_for_link}"
                    prop_element[2] = new_fp_value

                # Find the newer 'footprint' definition list
                footprint_element = find_sexp_element(element, "footprint")
                if footprint_element and len(footprint_element) > 1:
                    fp_value = str(footprint_element[1])
                    name_only = fp_value.split(":")[-1]

                    # MODIFIED: Use symbol name for the new FP link if renaming is active
                    fp_name_for_link = base_name if rename_assets else name_only
                    footprint_element[1] = (
                        f"{PROJECT_FOOTPRINT_LIB_NAME}:{fp_name_for_link}"
                    )

                    if not raw_footprint_name:
                        raw_footprint_name = name_only

                if raw_footprint_name:
                    # Map the original footprint name to its main symbol name for asset renaming later
                    footprint_map[raw_footprint_name] = base_name

                symbols_to_append_sexp.append(element)

                if symbol_name == base_name:
                    existing_main_symbols.add(symbol_name)

                appended_any = True
                print(f"Appended symbol: {symbol_name} (Footprint link localized)")
            else:
                pass  # Symbol already exists

    # Save the updated map to disk
    if appended_any:
        with open(TEMP_MAP_FILE, "w") as f:
            json.dump(footprint_map, f, indent=4)

    if symbols_to_append_sexp:
        project_sym_path = PROJECT_SYMBOL_LIB
        new_file_content = None

        # Logic to append symbols to the project file (omitted for brevity, assume correct)
        # ... (Same logic as provided in previous snippets) ...

        # Read the existing project library file
        if project_sym_path.exists():
            try:
                with open(project_sym_path, "r", encoding="utf-8") as f:
                    project_content = f.read()
                project_sexp = loads(project_content)
                project_sexp.extend(symbols_to_append_sexp)
                new_file_content = dumps(project_sexp, pretty_print=True)
            except Exception as e:
                print(
                    f"ERROR modifying project library using S-expression parser: {e}. Recreating file."
                )
                project_sym_path.unlink(missing_ok=True)
                new_file_content = None

        if not project_sym_path.exists() or new_file_content is None:
            # Creation of the very first file
            header = [["version", 20211026], ["generator", "script-generator"]]
            full_sexp = header + symbols_to_append_sexp
            new_file_content = dumps(
                full_sexp, wrap=Symbol("kicad_symbol_lib"), pretty_print=True
            )

        if new_file_content:
            with open(project_sym_path, "w", encoding="utf-8") as f:
                f.write(new_file_content)

    if not appended_any:
        print(f"No new symbols to append from {src_sym_file.name}")

    return appended_any

def process_zip(zip_file, rename_assets=False):
    """
    Processes a single ZIP file:
    - Extracts into a temp directory
    - Finds KiCad + 3D folders (supports nested /partname/KiCad structures)
    - Imports symbols, footprints, and 3D models into the project
    """

    # --- Normalize paths ---
    zip_file = Path(str(zip_file).strip()).resolve()
    tempdir = (INPUT_ZIP_FOLDER / "temp_extracted").resolve()

    if tempdir.exists():
        shutil.rmtree(tempdir)
    tempdir.mkdir(exist_ok=True)

    print(f"[DEBUG] Importing ZIP: {zip_file}")
    print(f"[DEBUG] Temporary extraction folder: {tempdir}")

    # --- Extract ZIP ---
    try:
        with zipfile.ZipFile(zip_file, "r") as zip_ref:
            zip_ref.extractall(tempdir)
    except Exception as e:
        print(f"[FAIL] Error extracting ZIP file {zip_file.name}: {e}")
        return

    # --- Detect KiCad + 3D folders automatically ---
    all_dirs = [p for p in tempdir.rglob("*") if p.is_dir()]
    kicad_root = None
    model_root = None

    # Find the first matching folders (case-insensitive)
    for d in all_dirs:
        if d.name.lower() == "kicad":
            kicad_root = d
        elif d.name.lower() == "3d":
            model_root = d

    # If neither found, check if there's a single nested folder (like LIB_MyPart)
    if not kicad_root and not model_root:
        subfolders = [f for f in tempdir.iterdir() if f.is_dir()]
        if len(subfolders) == 1:
            # Dive into that folder
            nested_root = subfolders[0]
            print(f"[DEBUG] Found nested folder: {nested_root.name}")
            for d in nested_root.rglob("*"):
                if d.is_dir() and d.name.lower() == "kicad":
                    kicad_root = d
                elif d.is_dir() and d.name.lower() == "3d":
                    model_root = d

    # Fallbacks
    if not kicad_root:
        kicad_root = tempdir
        print("[WARN] KiCad folder not found, using temp root.")
    if not model_root:
        model_root = tempdir
        print("[WARN] 3D folder not found, using temp root.")

    print(f"[DEBUG] KiCad root detected: {kicad_root}")
    print(f"[DEBUG] 3D root detected: {model_root}")

    # --- Step 1: Import symbols ---
    symbol_files = list(kicad_root.rglob("*.kicad_sym"))
    print(f"[DEBUG] Found {len(symbol_files)} .kicad_sym files.")

    if not symbol_files:
        print(f"[FAIL] No symbol files found in extracted ZIP {zip_file.name}.")
        shutil.rmtree(tempdir)
        return

    symbols_added = False
    for sym_file in symbol_files:
        print(f"[DEBUG] Processing symbol file: {sym_file}")
        if append_symbols_from_file(sym_file, rename_assets=rename_assets):
            symbols_added = True

    if not symbols_added and not TEMP_MAP_FILE.exists():
        print("[WARN] No new symbols added — skipping footprints and 3D models.")
        shutil.rmtree(tempdir)
        return

    # --- Step 2: Load footprint-symbol map ---
    footprint_map = {}
    if TEMP_MAP_FILE.exists():
        with open(TEMP_MAP_FILE, "r") as f:
            footprint_map = json.load(f)
        print(f"[DEBUG] Loaded footprint map with {len(footprint_map)} entries.")

    # --- Step 3: Rename assets (if enabled) ---
    if rename_assets:
        print("[INFO] Renaming of Footprints/3D Models ENABLED.")
        rename_count = rename_extracted_assets(tempdir, footprint_map)
        if rename_count > 0 and TEMP_MAP_FILE.exists():
            with open(TEMP_MAP_FILE, "r") as f:
                footprint_map = json.load(f)
        print(f"[INFO] Renamed {rename_count} assets.")

    # --- Step 4: Import footprints ---
    for mod_file in kicad_root.rglob("*.kicad_mod"):
        dest = PROJECT_FOOTPRINT_LIB / mod_file.name
        if dest.exists():
            print(f'[WARN] Skipped footprint "{mod_file.name}": already exists.')
            continue

        modified = localize_3d_model_path(mod_file, footprint_map)
        if modified:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(modified)
            print(f'[OK] Added footprint "{mod_file.name}" with localized 3D path.')
        else:
            shutil.copy(mod_file, dest)
            print(f'[OK] Added footprint "{mod_file.name}" (no localization).')

    # --- Step 5: Import 3D models ---
    copied_3d_count = 0
    for stp_file in model_root.rglob("*.stp"):
        dest_file = PROJECT_3D_DIR / stp_file.name
        if dest_file.exists():
            print(f'[WARN] Skipped 3D model "{stp_file.name}" (already exists).')
            continue

        try:
            shutil.copy(stp_file, dest_file)
            copied_3d_count += 1
            print(f'[OK] Copied 3D model "{stp_file.name}" → {PROJECT_3D_DIR.name}')
        except Exception as e:
            print(f"[FAIL] Error copying 3D model {stp_file.name}: {e}")

    if copied_3d_count == 0:
        print("[WARN] No new 3D models found or copied.")

    # --- Cleanup ---
    shutil.rmtree(tempdir)
    if TEMP_MAP_FILE.exists():
        TEMP_MAP_FILE.unlink()

    print(f"[OK] Finished importing {zip_file.name}")

def purge_zip_contents(zip_path: Path):
    """
    Deletes symbols, footprints, and 3D models from the project libraries
    that were contained within the specified ZIP file.

    It checks for both original asset names (from the ZIP) and renamed
    assets (named after their main symbol name).
    """
    tempdir = INPUT_ZIP_FOLDER / "temp_extracted_purge"
    if tempdir.exists():
        shutil.rmtree(tempdir)
    tempdir.mkdir(exist_ok=True)

    print(f"\n--- Purging contents of {zip_path.name} ---")

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            all_zip_names = zip_ref.namelist()
    except Exception as e:
        print(f"Error reading ZIP file {zip_path.name}: {e}")
        shutil.rmtree(tempdir)
        return

    # --- 1. Identify Symbols to Delete (Main Symbol Names) ---
    symbols_to_delete = set()
    # Also collect original asset names from the ZIP for a comprehensive check
    original_footprint_stems = set()
    original_stp_stems = set()

    for name in all_zip_names:
        name_path = Path(name)

        if name_path.suffix == ".kicad_sym":
            try:
                # ... (Symbol identification logic remains the same) ...
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extract(name, tempdir)

                extracted_sym_file = tempdir / name

                with open(extracted_sym_file, "r", encoding="utf-8") as f:
                    content = f.read()

                sexp_list = loads(content)

                for element in sexp_list[1:]:
                    if (
                        isinstance(element, list)
                        and len(element) > 1
                        and (element[0] == "symbol" or element[0] == Symbol("symbol"))
                    ):
                        sym_name = str(element[1])
                        base_name = SUB_PART_PATTERN.sub("", sym_name)
                        symbols_to_delete.add(base_name)
                # ... (End of symbol identification logic) ...

            except Exception as e:
                print(f"Error processing symbol file {name} during purge: {e}")

        elif name_path.suffix == ".kicad_mod":
            original_footprint_stems.add(name_path.stem)

        elif name_path.suffix.lower() == ".stp":
            original_stp_stems.add(name_path.stem)

    # --- 1b. Delete Symbols from Project Library (Logic remains the same as previous fix) ---
    if symbols_to_delete and PROJECT_SYMBOL_LIB.exists():
        print(
            f"Attempting to delete {len(symbols_to_delete)} main symbols from {PROJECT_SYMBOL_LIB.name}..."
        )

        try:
            with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
                project_content = f.read()

            project_sexp = loads(project_content)
            deleted_count = 0
            new_project_sexp = [project_sexp[0]]

            for element in project_sexp[1:]:
                if (
                    isinstance(element, list)
                    and len(element) > 1
                    and (element[0] == "symbol" or element[0] == Symbol("symbol"))
                ):
                    symbol_name = str(element[1])
                    base_name = SUB_PART_PATTERN.sub("", symbol_name)

                    if base_name in symbols_to_delete:
                        deleted_count += 1
                        continue

                new_project_sexp.append(element)

            if deleted_count > 0:
                with open(PROJECT_SYMBOL_LIB, "w", encoding="utf-8") as f:
                    f.write(dumps(new_project_sexp, pretty_print=True))
                print(
                    f"Deleted {deleted_count} symbol block(s) corresponding to {len(symbols_to_delete)} main symbols."
                )
            else:
                print("No matching symbols found for deletion.")

        except Exception as e:
            print(f"ERROR during S-expression symbol deletion: {e}")

    # --- 2. Delete Footprints (.kicad_mod) ---
    deleted_fp_count = 0
    stems_checked = set()  # To prevent double-deletion

    # Check for original names AND renamed (symbol) names
    stems_to_check = original_footprint_stems.union(symbols_to_delete)

    for stem in stems_to_check:
        # 1. Check for the original name (or the symbol name, if it was already named that way)
        fp_path_original = PROJECT_FOOTPRINT_LIB / (stem + ".kicad_mod")

        if fp_path_original.exists():
            fp_path_original.unlink()
            deleted_fp_count += 1

        # 2. Check for the renamed name (which is always the symbol name)
        # This is only necessary if the stem we checked above was an original FP name,
        # AND that original FP name is different from the symbol name.
        if (
            stem in original_footprint_stems
            and stem in symbols_to_delete
            and stem not in stems_checked
        ):
            # We already covered the symbol name case implicitly in the union,
            # but to be explicit about deleting both files if they coexist,
            # we must find the corresponding symbol. However, without the original map,
            # we trust that deleting files named after the symbol name is sufficient
            # because the *original* FP name might have been deleted in the previous block.
            pass  # The union handles this case most efficiently.

        stems_checked.add(stem)

    print(f"Deleted {deleted_fp_count} footprints from {PROJECT_FOOTPRINT_LIB.name}.")

    # --- 3. Delete 3D Models (.stp) ---
    deleted_3d_count = 0

    # Check for original 3D model names AND renamed (symbol) 3D model names
    stems_to_check = original_stp_stems.union(symbols_to_delete)

    for stem in stems_to_check:
        stp_path = PROJECT_3D_DIR / (stem + ".stp")

        if stp_path.exists():
            stp_path.unlink()
            deleted_3d_count += 1

    print(f"Deleted {deleted_3d_count} 3D model files from {PROJECT_3D_DIR.name}.")

    shutil.rmtree(tempdir)
    
def export_symbols(selected_symbols: list[str]) -> list[Path]:
    """
    Exports selected symbols from ProjectSymbols.kicad_sym, including their footprints and 3D models.
    Each symbol is exported as a separate ZIP archive into /library_output.
    Structure:
        LIB_partname.zip/
          └── partname/
              ├── KiCad/
              └── 3D/
    """
    from sexpdata import loads, Symbol, dumps
    import unicodedata

    export_paths = []

    try:
        if not selected_symbols:
            print("[FAIL] No symbols provided for export.")
            return []

        if not PROJECT_SYMBOL_LIB.exists():
            print("[FAIL] Project symbol library not found.")
            return []

        # --- Parse symbol library ---
        with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
            content = f.read()
        sym_tree = loads(content)

        # Build symbol → footprint map
        symbol_footprints = {}
        for el in sym_tree[1:]:
            if isinstance(el, list) and len(el) > 1 and str(el[0]) == "symbol":
                sym_name = str(el[1])
                footprint = None
                for item in el:
                    if (
                        isinstance(item, list)
                        and len(item) >= 3
                        and str(item[0]) == "property"
                        and str(item[1]) == "Footprint"
                    ):
                        footprint = str(item[2])
                        break
                if footprint:
                    symbol_footprints[sym_name] = footprint

        # --- Output directory ---
        output_root = INPUT_ZIP_FOLDER.parent / "library_output"
        output_root.mkdir(parents=True, exist_ok=True)

        # Helper: normalize names
        def normalize_name(s: str) -> str:
            return re.sub(r"[^A-Za-z0-9]", "", s).lower()

        # --- Iterate over symbols ---
        for sym in selected_symbols:
            footprint_ref = None
            for name in [sym, f"LIB_{sym}"]:
                if name in symbol_footprints:
                    footprint_ref = symbol_footprints[name]
                    break

            if not footprint_ref:
                print(f"[WARN] Symbol '{sym}' has no footprint assigned, skipping.")
                continue

            # --- Locate footprint file ---
            footprint_basename = footprint_ref.split(":")[-1]
            found_fp = None
            target_norm = normalize_name(footprint_basename)
            for fp in PROJECT_FOOTPRINT_LIB.rglob("*.kicad_mod"):
                if normalize_name(fp.stem) == target_norm:
                    found_fp = fp
                    break

            if not found_fp:
                print(f"[WARN] Footprint '{footprint_basename}' not found for {sym}, skipping.")
                continue

            # --- Extract symbol definition ---
            single_symbol_sexpr = None
            for el in sym_tree[1:]:
                if (
                    isinstance(el, list)
                    and len(el) > 1
                    and str(el[0]) == "symbol"
                    and (str(el[1]) == sym or str(el[1]) == f"LIB_{sym}")
                ):
                    single_symbol_sexpr = el
                    break

            if not single_symbol_sexpr:
                print(f"[WARN] Symbol '{sym}' not found in {PROJECT_SYMBOL_LIB.name}.")
                continue

            # --- Prepare folders ---
            part_name = sym
            zip_name = f"LIB_{part_name}.zip"
            part_folder = output_root / part_name
            kicad_folder = part_folder / "KiCad"
            model_folder = part_folder / "3D"

            if part_folder.exists():
                shutil.rmtree(part_folder)
            kicad_folder.mkdir(parents=True, exist_ok=True)
            model_folder.mkdir(parents=True, exist_ok=True)

            # --- Write symbol file ---
            symbol_out = kicad_folder / f"{part_name}.kicad_sym"
            with open(symbol_out, "w", encoding="utf-8") as f:
                f.write("(kicad_symbol_lib (version 20211014) (generator CSE-Manager)\n")
                f.write("  " + dumps(single_symbol_sexpr, pretty_print=True) + "\n)\n")

            # --- Copy footprint ---
            fp_out = kicad_folder / found_fp.name
            shutil.copy2(found_fp, fp_out)

            # --- Parse 3D models (handles all multiline/indented forms) ---
            model_blocks = []
            collecting = False
            depth = 0
            current_block = []

            with open(found_fp, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not collecting and ("(model" in line or line == "model" or line.endswith("model")):
                        collecting = True
                        depth = line.count("(") - line.count(")")
                        current_block = [line]
                        continue

                    if collecting:
                        current_block.append(line)
                        depth += line.count("(") - line.count(")")
                        if depth <= 0:
                            model_blocks.append(" ".join(current_block))
                            collecting = False
                            current_block = []

            resolved_models = []
            for block in model_blocks:
                try:
                    # Extract quoted or unquoted .stp path
                    match = re.search(r'["\']?([^"\']+\.stp)["\']?', block, flags=re.IGNORECASE)
                    if not match:
                        print(f"[WARN] Could not extract model path from block: {block[:80]}...")
                        continue

                    raw_path = match.group(1).replace("\\", "/")
                    raw_path = unicodedata.normalize("NFKC", raw_path)
                    raw_path = raw_path.encode("ascii", "ignore").decode()

                    # Substitute env vars
                    raw_path = os.path.expandvars(raw_path)
                    for env_var in (
                        "${KICAD7_3DMODEL_DIR}",
                        "${KICAD6_3DMODEL_DIR}",
                        "${KICAD8_3DMODEL_DIR}",
                    ):
                        raw_path = raw_path.replace(env_var, "3d_models")

                    model_path = Path(raw_path)
                    kiprojmod_root = PROJECT_SYMBOL_LIB.parent.parent

                    if "${KIPRJMOD}" in str(model_path):
                        model_path = Path(str(model_path).replace("${KIPRJMOD}", str(kiprojmod_root)))

                    # Resolve and copy
                    if model_path.exists():
                        resolved_models.append(model_path)
                        print(f"[DEBUG] Found 3D model for {sym}: {model_path}")
                    else:
                        rel_model = (PROJECT_FOOTPRINT_LIB.parent / model_path.name).resolve()
                        if rel_model.exists():
                            resolved_models.append(rel_model)
                            print(f"[DEBUG] Found relative 3D model for {sym}: {rel_model}")
                        else:
                            print(f"[WARN] 3D model not found: {model_path}")

                except Exception as e:
                    print(f"[WARN] Failed to parse model block: {e}")

            # --- Copy 3D models ---
            for model_path in resolved_models:
                if model_path.exists():
                    shutil.copy2(model_path, model_folder / model_path.name)

            # --- Create ZIP with correct structure ---
            zip_path = output_root / zip_name
            # Ensure we zip the parent (output_root) but only include part_folder
            shutil.make_archive(
                base_name=str(zip_path.with_suffix("")),
                format="zip",
                root_dir=output_root,
                base_dir=part_folder.name,
            )
            export_paths.append(zip_path)

            print(f"[OK] Exported {zip_name}")
            shutil.rmtree(part_folder)

        if export_paths:
            print(f"[OK] Created {len(export_paths)} ZIP file(s) in {output_root}")
        else:
            print("[WARN] No ZIPs created.")
        print(f"[OK] Output directory: {output_root}")
        return export_paths

    except Exception as e:
        print(f"[FAIL] Export failed: {e}")
        return []