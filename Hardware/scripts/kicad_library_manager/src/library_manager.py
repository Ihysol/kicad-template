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

from uuid import uuid4

KICAD8_SCHEMA = 20221018
KICAD9_SCHEMA = 20240115

def _find_symbol_uuid_block(sym_node):
    """Return index of (uuid "...") inside this symbol node, or -1 if not present."""
    for idx, child in enumerate(sym_node):
        if isinstance(child, list) and child and str(child[0]) == "uuid":
            return idx
    return -1

def _strip_children_by_head(sym_node, banned_heads: set[str]):
    """Return a copy of sym_node with any direct child whose head is in banned_heads removed."""
    cleaned = []
    for child in sym_node:
        if isinstance(child, list) and child:
            head = str(child[0])
            if head in banned_heads:
                continue
        cleaned.append(child)
    return cleaned

def detect_project_version(start_path: Path) -> int:
    """
    Detect the KiCad project version (schema number) by searching upward for project files.

    Priority:
    1. Read (generator_version "X.Y") from the nearest .kicad_sch
    2. Fallback to (version XXXXX) field from .kicad_pro or .kicad_pcb
    3. Default → KiCad 8 schema (20221018)
    """
    from sexpdata import loads, Symbol

    def major_to_schema(ver_str: str) -> int:
        """Map generator_version major to KiCad schema number."""
        try:
            major = int(ver_str.split(".")[0])
        except Exception:
            return 20221018
        return 20240115 if major >= 9 else 20221018

    # 1️⃣ Try schematic for generator_version
    sch_file = find_upward("*.kicad_sch", start_path)
    if sch_file and sch_file.exists():
        try:
            with open(sch_file, encoding="utf-8") as f:
                sch_data = loads(f.read())
            for node in sch_data:
                if (
                    isinstance(node, list)
                    and len(node) >= 2
                    and (node[0] == Symbol("generator_version") or node[0] == "generator_version")
                ):
                    ver = str(node[1]).strip('"')
                    schema = major_to_schema(ver)
                    print(f"[DEBUG] Detected generator_version {ver} → schema {schema}")
                    return schema
        except Exception as e:
            print(f"[WARN] Failed to parse {sch_file.name} for generator_version: {e}")

    # 2️⃣ Try .kicad_pro or .kicad_pcb
    for pattern in ["*.kicad_pro", "*.kicad_pcb"]:
        proj_file = find_upward(pattern, start_path)
        if proj_file and proj_file.exists():
            try:
                with open(proj_file, encoding="utf-8") as f:
                    proj_data = loads(f.read())
                for node in proj_data:
                    if (
                        isinstance(node, list)
                        and len(node) >= 2
                        and (node[0] == Symbol("version") or node[0] == "version")
                    ):
                        ver_val = int(node[1])
                        print(f"[DEBUG] Detected version {ver_val} from {proj_file.name}")
                        return ver_val
            except Exception as e:
                print(f"[WARN] Failed to parse {proj_file.name} for version: {e}")

    # 3️⃣ Default fallback
    print("[WARN] Could not detect KiCad version; defaulting to KiCad 8 (20221018).")
    return 20221018


def convert_symbol_expr(sym_node, src_schema: int, dst_schema: int):
    """
    Convert a single (symbol "NAME" ...) node between KiCad 8 <-> 9 without touching geometry.
    Assumes sym_node is exactly the list that starts with 'symbol'.

    Rules:
    - 8 -> 9:
        drop pin_names/pin_numbers
        insert uuid after symbol name if missing
    - 9 -> 8:
        drop uuid
        keep everything else
    - same -> passthrough
    """
    if not (isinstance(sym_node, list) and len(sym_node) >= 2 and str(sym_node[0]) == "symbol"):
        return sym_node  # not a symbol? return as-is

    # clone to avoid mutating caller's list
    out = [c for c in sym_node]

    if src_schema == dst_schema:
        return out

    # upgrading: KiCad 8 -> KiCad 9
    if src_schema < KICAD9_SCHEMA and dst_schema >= KICAD9_SCHEMA:
        # 1. remove pin_names / pin_numbers
        out = _strip_children_by_head(out, {"pin_names", "pin_numbers"})

        # 2. ensure uuid exists immediately after symbol name
        # expected structure: [ "symbol", "NAME", (uuid "..."), (in_bom ...), ... ]
        # so index 0 = "symbol", index 1 = "NAME"
        uuid_idx = _find_symbol_uuid_block(out)
        if uuid_idx == -1:
            if len(out) >= 2:
                out.insert(2, [Symbol("uuid"), str(uuid4())])
            else:
                # degenerate / malformed symbol, just append
                out.append([Symbol("uuid"), str(uuid4())])

        return out

    # downgrading: KiCad 9 -> KiCad 8
    if src_schema >= KICAD9_SCHEMA and dst_schema < KICAD9_SCHEMA:
        # remove uuid (but ONLY at top level of the symbol, do not recurse)
        out = _strip_children_by_head(out, {"uuid"})
        # we do NOT add pin_names/pin_numbers back; KiCad 8 will still parse.
        return out

    # fallback (shouldn't happen, but safe)
    return out

def detect_schema_from_sexp(root_sexp: list) -> int:
    """Look for (version XXXXX) at top level of a .kicad_sym file."""
    if isinstance(root_sexp, list):
        for node in root_sexp:
            if isinstance(node, list) and node and str(node[0]) == "version":
                try:
                    return int(node[1])
                except Exception:
                    pass
    # Default assume KiCad 8 if not found
    return KICAD8_SCHEMA

def convert_symbol_file_sexp(root_sexp: list, dst_schema: int) -> list:
    """
    Convert a parsed .kicad_sym S-expression tree to the target KiCad schema (8 ↔ 9).
    Recursively removes or inserts version-specific tags and rewrites (version …).
    """
    if not isinstance(root_sexp, list):
        return root_sexp

    src_schema = detect_schema_from_sexp(root_sexp)

    # --- 1. Convert all (symbol …) blocks recursively ---
    new_root = []
    for node in root_sexp:
        if isinstance(node, list) and node and str(node[0]) == "symbol":
            new_root.append(_convert_symbol_recursive(node, src_schema, dst_schema))
        else:
            new_root.append(node)

    # --- 2. Ensure a correct (version …) header exists ---
    fixed = []
    has_version = False
    for node in new_root:
        if isinstance(node, list) and node and str(node[0]) == "version":
            fixed.append([Symbol("version"), dst_schema])
            has_version = True
        else:
            fixed.append(node)
    if not has_version:
        fixed.insert(1, [Symbol("version"), dst_schema])

    # --- 3. Downgrade cleanup for KiCad 8 ---
    if dst_schema < KICAD9_SCHEMA:
        banned = {"uuid", "extends", "lib_id", "template", "style", "parent"}

        def deep_clean(node):
            """Recursively strip banned KiCad-9 fields from nested lists."""
            if not isinstance(node, list):
                return node
            if not node:
                return node
            head = str(node[0])
            if head in banned:
                return None
            cleaned = []
            for child in node:
                sub = deep_clean(child)
                if sub is not None:
                    cleaned.append(sub)
            return cleaned

        cleaned_root = []
        for n in fixed:
            c = deep_clean(n)
            if c is not None:
                cleaned_root.append(c)
        fixed = cleaned_root

    return fixed


def _convert_symbol_recursive(sym_node, src_schema, dst_schema):
    """
    Convert one (symbol ...) block and all nested (symbol ...) sub-blocks.
    ex:
    (symbol "IR4302"
        ...
        (symbol "IR4302_1_1"
        ...
        )
    )
    """
    # first convert this symbol itself:
    converted_top = convert_symbol_expr(sym_node, src_schema, dst_schema)

    # now walk its children and also convert any nested (symbol ...)
    out_children = []
    for child in converted_top:
        if isinstance(child, list) and child and str(child[0]) == "symbol":
            out_children.append(_convert_symbol_recursive(child, src_schema, dst_schema))
        else:
            out_children.append(child)

    return out_children



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

def ensure_project_symbol_header(project_sym_path: Path, project_version: int):
    """
    Ensure the ProjectSymbols.kicad_sym file has a valid (version ...) and (generator ...) header
    matching the detected KiCad project version.

    It safely replaces or inserts the header tags without touching symbols.
    """
    from sexpdata import loads, dumps, Symbol

    if not project_sym_path.exists():
        return

    try:
        with open(project_sym_path, "r", encoding="utf-8") as f:
            content = f.read()
        sexpr = loads(content)
    except Exception as e:
        print(f"[WARN] Failed to parse {project_sym_path.name} for header update: {e}")
        return

    if not isinstance(sexpr, list) or len(sexpr) == 0:
        return

    # Select the correct schema number
    target_schema = 20240115 if project_version >= 20240115 else 20221018

    # Remove old (version ...) and (generator ...) lines if they exist
    sexpr = [
        x
        for x in sexpr
        if not (
            isinstance(x, list)
            and len(x) > 0
            and str(x[0]) in ("version", "generator", "generator_version")
        )
    ]

    # Insert new header fields after the root symbol list marker
    sexpr.insert(1, [Symbol("version"), target_schema])
    sexpr.insert(2, [Symbol("generator"), "CSE-Manager"])
    if target_schema >= 20240115:
        sexpr.insert(3, [Symbol("generator_version"), "9.0"])
    else:
        sexpr.insert(3, [Symbol("generator_version"), "8.0"])

    # Rewrite file
    try:
        with open(project_sym_path, "w", encoding="utf-8") as f:
            f.write(
                dumps(
                    sexpr,
                    wrap=Symbol("kicad_symbol_lib"),
                    pretty_print=True,
                )
            )
        print(f"[INFO] Updated header in {project_sym_path.name} → schema {target_schema}")
    except Exception as e:
        print(f"[WARN] Could not write updated header for {project_sym_path.name}: {e}")


def normalize_expr_for_project(expr, project_version: int):
    """
    Cleans and adjusts KiCad S-expressions for compatibility between KiCad 8 and 9.
    - KiCad 8: Removes unsupported tags, hides and repositions properties neatly to the right
               of the rightmost pin, spaced vertically, left-aligned.
    - KiCad 9: Ensures missing UUIDs exist.
    """
    from sexpdata import Symbol
    from uuid import uuid4

    HIDDEN_OFFSET_MARGIN_X = 20  # mm extra spacing to the right of rightmost pin
    HIDDEN_OFFSET_STEP_Y = -2    # vertical spacing between properties
    HIDDEN_ROT = 0

    # --------------------------------------------------------------------------
    # Utility functions
    # --------------------------------------------------------------------------

    def deep_strip(e):
        """Remove KiCad 9-only constructs that KiCad 8 can't parse."""
        banned = {
            Symbol("uuid"), Symbol("extends"), Symbol("template"),
            Symbol("lib_id"), Symbol("style"), Symbol("parent"),
            Symbol("embedded_fonts"), Symbol("text_styles"),
        }
        if not isinstance(e, list):
            return e
        if e and e[0] in banned:
            return None
        cleaned = []
        for sub in e:
            sub_clean = deep_strip(sub)
            if sub_clean is not None:
                cleaned.append(sub_clean)
        return cleaned

    def strip_hide_flags(e):
        """Remove invalid or misplaced (hide yes) tags before re-adding them."""
        if not isinstance(e, list):
            return e
        cleaned = []
        for sub in e:
            if isinstance(sub, list):
                if len(sub) == 2 and str(sub[0]) == "hide" and str(sub[1]) == "yes":
                    continue
                sub = strip_hide_flags(sub)
                if sub is not None:
                    cleaned.append(sub)
            else:
                cleaned.append(sub)
        return cleaned

    def add_uuids(e):
        """Ensure all (symbol ...) blocks have UUIDs for KiCad 9."""
        if isinstance(e, list):
            newnode = [add_uuids(x) for x in e]
            if e and e[0] == Symbol("symbol") and not any(
                isinstance(i, list) and i and i[0] == Symbol("uuid") for i in e
            ):
                newnode.insert(2, [Symbol("uuid"), str(uuid4())])
            return newnode
        return e

    def ensure_pin_headers(sym):
        """Add missing (pin_names)/(pin_numbers) for KiCad 8 compatibility."""
        if not (isinstance(sym, list) and sym and sym[0] == Symbol("symbol")):
            return sym

        has_pin_names = any(isinstance(i, list) and i and i[0] == Symbol("pin_names") for i in sym)
        has_pin_numbers = any(isinstance(i, list) and i and i[0] == Symbol("pin_numbers") for i in sym)

        insert_pos = 2 if len(sym) > 2 else len(sym)
        if not has_pin_numbers:
            sym.insert(insert_pos, [Symbol("pin_numbers"), Symbol("hide")])
            insert_pos += 1
        if not has_pin_names:
            sym.insert(insert_pos, [Symbol("pin_names"), [Symbol("offset"), 0], Symbol("hide")])

        for i, sub in enumerate(sym):
            if isinstance(sub, list) and sub and sub[0] == Symbol("symbol"):
                sym[i] = ensure_pin_headers(sub)
        return sym

    # --------------------------------------------------------------------------
    # Find rightmost pin X coordinate
    # --------------------------------------------------------------------------
    def get_rightmost_pin_x(symbol_node):
        """Scan all (pin ...) elements and return the maximum X coordinate."""
        max_x = 0
        for child in symbol_node:
            if isinstance(child, list) and len(child) > 0 and child[0] == Symbol("pin"):
                # find (at x y rot)
                for elem in child:
                    if isinstance(elem, list) and elem and elem[0] == Symbol("at"):
                        try:
                            x = float(elem[1])
                            if x > max_x:
                                max_x = x
                        except Exception:
                            pass
        return max_x

    # --------------------------------------------------------------------------
    # Fix property layout
    # --------------------------------------------------------------------------
    def fix_property_layout_recursive(node, rightmost_x=0, prop_index=[0]):
        """
        Recursively process every (property ...) block:
        - Keep Reference/Value visible and untouched.
        - Place hidden properties right of rightmost pin + margin, spaced vertically.
        - Left-align all hidden properties.
        """
        if not isinstance(node, list):
            return node

        if len(node) > 0 and node[0] == Symbol("property"):
            name = str(node[1]).strip('"') if len(node) > 1 else ""
            if name not in ("Reference", "Value"):
                # remove old (at ...), (hide ...), and (effects ...) blocks
                node[:] = [
                    x
                    for x in node
                    if not (
                        isinstance(x, list)
                        and x
                        and x[0] in (Symbol("at"), Symbol("hide"), Symbol("effects"))
                    )
                ]

                # Compute new coordinates
                x_offset = rightmost_x + HIDDEN_OFFSET_MARGIN_X
                y_offset = HIDDEN_OFFSET_STEP_Y * prop_index[0]

                node.append([Symbol("at"), x_offset, y_offset, HIDDEN_ROT])
                node.append([
                    Symbol("effects"),
                    [Symbol("justify"), Symbol("left")]
                ])
                node.append([Symbol("hide"), Symbol("yes")])
                prop_index[0] += 1

        # Recurse into child lists
        for i, sub in enumerate(node):
            if isinstance(sub, list):
                node[i] = fix_property_layout_recursive(sub, rightmost_x, prop_index)

        return node

    # --------------------------------------------------------------------------
    # Main processing logic
    # --------------------------------------------------------------------------
    if project_version < 20240115:  # KiCad 8 mode
        expr = deep_strip(expr)
        expr = strip_hide_flags(expr)
        expr = ensure_pin_headers(expr)

        # For each symbol block → compute rightmost pin + reposition properties
        if isinstance(expr, list):
            for i, e in enumerate(expr):
                if isinstance(e, list) and e and e[0] == Symbol("symbol"):
                    rightmost_x = get_rightmost_pin_x(e)
                    expr[i] = fix_property_layout_recursive(e, rightmost_x, prop_index=[0])

        # Force version tag
        found = False
        for i, e in enumerate(expr):
            if isinstance(e, list) and e and e[0] == Symbol("version"):
                expr[i][1] = 20221018
                found = True
                break
        if not found:
            expr.insert(1, [Symbol("version"), 20221018])

    else:  # KiCad 9 mode
        expr = add_uuids(expr)
        found = False
        for i, e in enumerate(expr):
            if isinstance(e, list) and e and e[0] == Symbol("version"):
                expr[i][1] = 20240115
                found = True
                break
        if not found:
            expr.insert(1, [Symbol("version"), 20240115])

    return expr











def append_symbols_from_file(src_sym_file: Path, rename_assets=False):
    """
    Appends symbols from a source KiCad library file to the project library.
    Automatically detects and converts between KiCad 8 and 9 symbol formats.
    Ensures correct top-level wrapping and header for KiCad compatibility.
    """

    existing_main_symbols = get_existing_main_symbols()

    # Load or initialize the footprint map
    footprint_map = {}
    if TEMP_MAP_FILE.exists():
        with open(TEMP_MAP_FILE, "r") as f:
            footprint_map = json.load(f)

    try:
        with open(src_sym_file, "r", encoding="utf-8") as f:
            src_content = f.read()
            src_sexp = loads(src_content)

        # --- Detect project KiCad version (schema) ---
        project_version = detect_project_version(PROJECT_DIR)

        # --- Normalize and clean symbols for target KiCad version ---
        src_sexp = normalize_expr_for_project(src_sexp, project_version)
        print(f"[INFO] Cleaned {src_sym_file.name} for KiCad schema {project_version}")

    except FileNotFoundError:
        print(f"[ERROR] Source file not found: {src_sym_file.name}")
        return False
    except Exception as e:
        print(f"[ERROR] Parsing S-expression in {src_sym_file.name}: {e}")
        return False

    # --- Collect symbols to append ---
    symbols_to_append_sexp = []
    appended_any = False

    for element in src_sexp[1:]:
        if (
            isinstance(element, list)
            and len(element) > 1
            and (element[0] == "symbol" or element[0] == Symbol("symbol"))
        ):
            symbol_name = str(element[1])
            base_name = SUB_PART_PATTERN.sub("", symbol_name)

            if base_name not in existing_main_symbols:
                # --- Handle footprint localization ---
                raw_footprint_name = None

                # (property "Footprint" "LIB:NAME")
                prop_element = find_sexp_property(element, "Footprint")
                if prop_element:
                    raw_footprint_name = str(prop_element[2]).split(":")[-1]
                    fp_name_for_link = base_name if rename_assets else raw_footprint_name
                    prop_element[2] = f"{PROJECT_FOOTPRINT_LIB_NAME}:{fp_name_for_link}"

                # (footprint "LIB:NAME")
                footprint_element = find_sexp_element(element, "footprint")
                if footprint_element and len(footprint_element) > 1:
                    fp_value = str(footprint_element[1])
                    name_only = fp_value.split(":")[-1]
                    fp_name_for_link = base_name if rename_assets else name_only
                    footprint_element[1] = f"{PROJECT_FOOTPRINT_LIB_NAME}:{fp_name_for_link}"
                    if not raw_footprint_name:
                        raw_footprint_name = name_only

                # Map footprint to symbol for renaming
                if raw_footprint_name:
                    footprint_map[raw_footprint_name] = base_name

                symbols_to_append_sexp.append(element)
                existing_main_symbols.add(base_name)
                appended_any = True
                print(f"[OK] Appended symbol: {symbol_name}")
            else:
                print(f"[SKIP] Symbol already exists: {symbol_name}")

    # --- Save footprint-symbol map ---
    if appended_any:
        with open(TEMP_MAP_FILE, "w") as f:
            json.dump(footprint_map, f, indent=4)

    project_sym_path = PROJECT_SYMBOL_LIB
    new_file_content = None

    # --- If the project file already exists, append ---
    if project_sym_path.exists():
        try:
            with open(project_sym_path, "r", encoding="utf-8") as f:
                project_content = f.read()
            project_sexp = loads(project_content)

            # Ensure it's wrapped in (kicad_symbol_lib ...)
            if not (isinstance(project_sexp, list) and len(project_sexp) > 0 and str(project_sexp[0]) == "kicad_symbol_lib"):
                project_sexp = [Symbol("kicad_symbol_lib")] + project_sexp

            project_sexp.extend(symbols_to_append_sexp)
            new_file_content = dumps(project_sexp, pretty_print=True, wrap=Symbol("kicad_symbol_lib"))
        except Exception as e:
            print(f"[WARN] Error modifying project library: {e}. Recreating file.")
            project_sym_path.unlink(missing_ok=True)
            new_file_content = None

    # --- If file doesn't exist or recreation needed ---
    if not project_sym_path.exists() or new_file_content is None:
        target_schema = 20240115 if project_version >= 20240115 else 20221018
        generator_version = "9.0" if target_schema >= 20240115 else "8.0"
        header = [
            ["version", target_schema],
            ["generator", "CSE-Manager"],
            ["generator_version", generator_version],
        ]
        full_sexp = header + symbols_to_append_sexp
        new_file_content = dumps(full_sexp, wrap=Symbol("kicad_symbol_lib"), pretty_print=True)

    # --- Write back the file ---
    if new_file_content:
        with open(project_sym_path, "w", encoding="utf-8") as f:
            f.write(new_file_content)

        # Ensure header correctness
        ensure_project_symbol_header(project_sym_path, project_version)

    if not appended_any:
        print(f"[WARN] No new symbols added from {src_sym_file.name}")

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