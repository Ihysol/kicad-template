# cli_main.py - FIXED Argument Parsing

import sys
from pathlib import Path
import argparse 
import os 
import io 
import locale

# =========================================================
# ⚠️ UNICODE FIX: Force standard output to use UTF-8 encoding)
# =========================================================
if sys.platform.startswith('win'):
    if locale.getpreferredencoding(False) not in ['UTF-8', 'utf8']:
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
        except Exception:
            pass
# =========================================================

# Import the core logic from the separate file
from library_manager import (
    INPUT_ZIP_FOLDER, 
    PROJECT_SYMBOL_LIB, 
    list_symbols_simple, 
    process_zip, 
    purge_zip_contents
)

def parse_arguments():
    """
    Parses command-line arguments to determine action and files, and includes help documentation.
    """
    parser = argparse.ArgumentParser(
        description="KiCad Library Manager CLI: Tool for processing or purging symbols, footprints, and 3D files from ZIP archives into a project library.",
        formatter_class=argparse.RawTextHelpFormatter 
    ) 

    # set mode argument
    parser.add_argument(
        'action',
        choices=['process', 'purge'],
        help='The action to perform: "process" (import) or "purge" (delete).'
    )
    
    # INPUT_ZIP_FOLDER override argument (default: ./generate)
    parser.add_argument(
        '--input_folder',
        type=str,
        help=f"Override the source folder containing ZIP files.\n(DEFAULT: '{INPUT_ZIP_FOLDER}')"
    )

    # Positional arguments for specific ZIP files
    parser.add_argument(
        'zip_files',
        nargs='*', 
        type=str,
        default=[],
        help="One or more specific ZIP file paths to process or purge.\n"
            "If provided, only these files are acted upon.\n"
            "If omitted, ALL ZIP files in the --input_folder are used."
    )
    
    return parser.parse_args()


def main():
    """
    Main function to determine mode (process or purge) and iterate over zip files.
    """
    args = parse_arguments()
    
    # determine source folder
    source_folder = Path(args.input_folder) if args.input_folder else INPUT_ZIP_FOLDER

    # list of found ZIP files
    zip_paths = []
    
    if args.zip_files:
        # Use only the ZIP files specified in the command line (now correctly shifted)
        zip_paths = [Path(f) for f in args.zip_files]
    else:
        # Fallback: process all ZIP files in the source_folder
        zip_paths = list(source_folder.glob("*.zip"))
        
    # Determine action and mode name based on the consumed positional argument
    is_purge = args.action == 'purge'
    action_func = purge_zip_contents if is_purge else process_zip
    mode_name = "PURGE" if is_purge else "PROCESSING"
    
    if not zip_paths:
        print(f"Warning: No ZIP files found in '{source_folder}' to process/purge.")
        # Print final symbol list (which is the same as initial)
        print("\n--- Final List of Main Symbols ---")
        list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=True)
        return

    # --- Start Processing/Purging ---
    for zip_file in zip_paths:
        print(f"\n--- {mode_name} {zip_file.name} ---")
        # Now zip_file is a Path object to the ZIP file, not the string 'process'
        action_func(zip_file) 
        
    # Print final symbol list
    print("\n--- Final List of Main Symbols ---")
    list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=True)

if __name__ == "__main__": 
    main()