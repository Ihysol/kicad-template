import sys
from pathlib import Path
import argparse
import locale
import io

def process_cli_action(paths, action, rename_assets=False):
    """
    paths: list of Path objects
    action: "process", "purge", or "export"
    rename_assets: bool
    Returns: (success: bool, output_str: str)
    """
    from library_manager import process_zip, purge_zip_contents, export_symbols

    output_lines = []
    success = True

    try:
        for p in paths:
            if action == "process":
                output_lines.append(f"PROCESS {p.name}")
                try:
                    process_zip(p, rename_assets=rename_assets)
                    output_lines.append(f"[OK] {p.name} processed successfully.")
                except Exception as e:
                    output_lines.append(f"[ERROR] {p.name} failed: {e}")
                    success = False

            elif action == "purge":
                output_lines.append(f"PURGE {p.name}")
                try:
                    purge_zip_contents(p)
                    output_lines.append(f"[OK] {p.name} purged successfully.")
                except Exception as e:
                    output_lines.append(f"[ERROR] {p.name} failed during purge: {e}")
                    success = False

            elif action == "export":
                output_lines.append(f"EXPORT {p}")
                try:
                    export_symbols([p.name] if isinstance(p, Path) else [str(p)])
                    output_lines.append(f"[OK] Exported symbols to output ZIP.")
                except Exception as e:
                    output_lines.append(f"[ERROR] Export failed: {e}")
                    success = False

        return success, "\n".join(output_lines)
    except Exception as e:
        return False, f"CLI action failed globally: {e}"

# =========================================================
# UNICODE FIX (Windows): Ensure UTF-8 output for console
# =========================================================
if sys.platform.startswith("win"):
    if locale.getpreferredencoding(False) not in ["UTF-8", "utf8"]:
        try:
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", errors="replace"
            )
        except Exception:
            pass

# =========================================================
# Import library logic
# =========================================================
from library_manager import (
    INPUT_ZIP_FOLDER,
    PROJECT_SYMBOL_LIB,
    list_symbols_simple,
    process_zip,
    purge_zip_contents,
    export_symbols,
)

def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="KiCad Library Manager CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "action",
        choices=["process", "purge", "export"],
        help='Action: "process" (import), "purge" (delete), or "export" (create ZIP).',
    )

    parser.add_argument(
        "--rename-assets",
        action="store_true",
        help="(Process Only) Rename associated footprints and .stp files to match the symbol name before processing.",
    )

    parser.add_argument(
        "--input_folder",
        type=str,
        help=f"Override the source folder containing ZIP files (default: '{INPUT_ZIP_FOLDER}')",
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=[],
        help="Symbol names to export (for 'export' action).",
    )

    parser.add_argument(
        "zip_files",
        nargs="*",
        type=str,
        default=[],
        help="ZIP files to act upon. If empty, all zips in folder are used (for process/purge).",
    )
    return parser.parse_args(argv)

def run_cli_action(action: str, zip_files=None, input_folder=None, rename_assets=False, symbols=None):
    """
    Core runner: can be called from GUI or CLI.
    Returns (success: bool, output: str).
    """
    from io import StringIO
    import contextlib

    if zip_files is None:
        zip_files = []
    if symbols is None:
        symbols = []

    argv = [action]
    if input_folder:
        argv += ["--input_folder", str(input_folder)]
    if rename_assets:
        argv.append("--rename-assets")
    if symbols:
        argv += ["--symbols"] + symbols
    argv += zip_files

    args = parse_arguments(argv)

    source_folder = Path(args.input_folder) if args.input_folder else INPUT_ZIP_FOLDER
    buffer = StringIO()

    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        if args.action == "export":
            if not args.symbols:
                print("ERROR: No symbols specified for export.")
                return False, buffer.getvalue()

            print(f"\n--- EXPORTING {len(args.symbols)} SYMBOL(S) ---")
            success = export_symbols(args.symbols)
            print("\n--- EXPORT COMPLETE ---")
            return success, buffer.getvalue()

        # For process/purge actions:
        zip_paths = (
            [Path(f) for f in args.zip_files]
            if args.zip_files
            else list(source_folder.glob("*.zip"))
        )

        is_purge = args.action == "purge"
        mode_name = "PURGE" if is_purge else "PROCESSING"

        if not zip_paths:
            print(f"Warning: No ZIP files found in '{source_folder}'.")
            print("\n--- Final List of Main Symbols ---")
            list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=True)
            return False, buffer.getvalue()

        for zip_file in zip_paths:
            print(f"\n--- {mode_name} {zip_file.name} ---")
            if is_purge:
                purge_zip_contents(zip_file)
            else:
                process_zip(zip_file, rename_assets=args.rename_assets)

        print("\n--- Final List of Main Symbols ---")
        list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=True)

    return True, buffer.getvalue()

def main():
    args = parse_arguments()
    success, output = run_cli_action(
        action=args.action,
        zip_files=args.zip_files,
        input_folder=args.input_folder,
        rename_assets=args.rename_assets,
        symbols=args.symbols,
    )
    print(output)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()