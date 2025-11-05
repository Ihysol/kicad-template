# cli_main.py
"""
KiCad Library Manager CLI
=========================
Unified CLI entry point for processing, purging, or exporting component ZIPs.

Usage examples:
    python cli_main.py process my_part.zip --rename-assets
    python cli_main.py purge my_part.zip
    python cli_main.py export --symbols U1 U2 U3
"""

import sys
import locale
import argparse
from pathlib import Path
from io import StringIO
import contextlib

# --- UTF-8 fix for Windows console ---
if sys.platform.startswith("win"):
    if locale.getpreferredencoding(False).lower() not in {"utf-8", "utf8"}:
        try:
            sys.stdout = StringIO()
            sys.stderr = StringIO()
        except Exception:
            pass

# --- Import project logic ---
from library_manager import (
    INPUT_ZIP_FOLDER,
    PROJECT_SYMBOL_LIB,
    process_zip,
    purge_zip_contents,
    export_symbols,
    list_symbols_simple,
)


# =========================================================
# CLI argument parser
# =========================================================
def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="KiCad Library Manager CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "action",
        choices=["process", "purge", "export"],
        help='Action: "process" (import), "purge" (delete), or "export" (create ZIP)',
    )
    parser.add_argument(
        "--rename-assets",
        action="store_true",
        help="(Process only) Rename footprints and 3D models to match the symbol name.",
    )
    parser.add_argument(
        "--input-folder",
        type=str,
        help=f"Folder containing ZIPs (default: {INPUT_ZIP_FOLDER})",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=[],
        help="Symbol names for export (used with 'export' action).",
    )
    parser.add_argument(
        "zip_files",
        nargs="*",
        type=str,
        help="ZIP files to process or purge. If empty, uses all ZIPs in input folder.",
    )
    parser.add_argument(
        "--use-symbol-name",
        action="store_true",
        help="Use symbol name as footprint and 3D model name.",
    )



    return parser.parse_args(argv)


# =========================================================
# Main runner function
# =========================================================
def run_cli_action(action: str, zip_files=None, input_folder=None, rename_assets=False, use_symbol_name=False, symbols=None):
    """
    Unified entry point for CLI and GUI.

    Returns:
        (success: bool, output: str)
    """
    if zip_files is None:
        zip_files = []
    if symbols is None:
        symbols = []

    args = parse_arguments(
        [action]
        + (["--input-folder", str(input_folder)] if input_folder else [])
        + (["--rename-assets"] if rename_assets else [])
        + (["--use-symbol-name"] if use_symbol_name else [])
        + (["--symbols"] + symbols if symbols else [])
        + zip_files
    )


    output = StringIO()
    success = True

    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        folder = Path(args.input_folder) if args.input_folder else INPUT_ZIP_FOLDER

        if args.action == "export":
            if not args.symbols:
                print("[FAIL] No symbols specified for export.")
                return False, output.getvalue()

            print(f"--- EXPORTING {len(args.symbols)} SYMBOL(S) ---")
            export_symbols(args.symbols)
            print("--- EXPORT COMPLETE ---")
            return True, output.getvalue()

        # For process/purge:
        zip_paths = [Path(z) for z in args.zip_files] if args.zip_files else list(folder.glob("*.zip"))
        if not zip_paths:
            print(f"[WARN] No ZIP files found in '{folder}'.")
            list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=True)
            return False, output.getvalue()

        for z in zip_paths:
            print(f"\n--- {args.action.upper()} {z.name} ---")
            try:
                if args.action == "purge":
                    purge_zip_contents(z)
                else:
                    process_zip(z, rename_assets=args.rename_assets, use_symbol_name=args.use_symbol_name)

                print(f"[OK] {z.name} done.")
            except Exception as e:
                print(f"[FAIL] {z.name} failed: {e}")
                success = False

        print("\n--- FINAL SYMBOL LIST ---")
        list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=True)

    return success, output.getvalue()


# =========================================================
# CLI entry point
# =========================================================
def main():
    args = parse_arguments()
    success, out = run_cli_action(
        action=args.action,
        zip_files=args.zip_files,
        input_folder=args.input_folder,
        rename_assets=args.rename_assets,
        use_symbol_name=args.use_symbol_name,
        symbols=args.symbols,
    )

    print(out)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
