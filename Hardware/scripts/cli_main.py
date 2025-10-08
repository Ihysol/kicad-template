import sys
from pathlib import Path
import argparse 
import os 
import io 
import locale

# =========================================================
# UNICODE FIX: Ensure UTF-8 output on Windows for proper console display
# =========================================================
if sys.platform.startswith('win'):
    if locale.getpreferredencoding(False) not in ['UTF-8', 'utf8']:
        try:
            # Re-wrap stdout/stderr buffers with UTF-8 encoding
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
        except Exception:
            pass
# =========================================================

# Import the core library management logic
from library_manager import (
    INPUT_ZIP_FOLDER, 
    PROJECT_SYMBOL_LIB, 
    list_symbols_simple, 
    process_zip, 
    purge_zip_contents
)

def parse_arguments():
    """
    Sets up and executes the argparse configuration for the CLI.
    This defines the 'action' (positional) and 'zip_files' (positional/list) arguments.
    """
    parser = argparse.ArgumentParser(
        description="KiCad Library Manager CLI: Tool for processing or purging symbols, footprints, and 3D files from ZIP archives into a project library.",
        formatter_class=argparse.RawTextHelpFormatter 
    ) 

    # Define the required positional argument for the operation mode
    parser.add_argument(
        'action',
        choices=['process', 'purge'],
        help='The action to perform: "process" (import) or "purge" (delete).'
    )
    
    # Optional argument to override the default source directory
    parser.add_argument(
        '--input_folder',
        type=str,
        help=f"Override the source folder containing ZIP files.\n(DEFAULT: '{INPUT_ZIP_FOLDER}')"
    )

    # Positional argument to accept one or more specific ZIP file paths
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
    Main execution function: parses arguments, determines target files, 
    and dispatches the 'process' or 'purge' action for each file.
    """
    args = parse_arguments()
    
    # Set the source folder, using the override if provided
    source_folder = Path(args.input_folder) if args.input_folder else INPUT_ZIP_FOLDER

    # Initialize the list of files to act upon
    zip_paths = []
    
    if args.zip_files:
        # If specific paths were provided (typically from the GUI), use them
        zip_paths = [Path(f) for f in args.zip_files]
    else:
        # Otherwise, scan the source folder for all ZIP files
        zip_paths = list(source_folder.glob("*.zip"))
        
    # Select the appropriate function and label based on the 'action' argument
    is_purge = args.action == 'purge'
    action_func = purge_zip_contents if is_purge else process_zip
    mode_name = "PURGE" if is_purge else "PROCESSING"
    
    if not zip_paths:
        print(f"Warning: No ZIP files found in '{source_folder}' to process/purge.")
        # Output the current state of the main symbol library
        print("\n--- Final List of Main Symbols ---")
        list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=True)
        return

    # --- Execute the Action on each selected ZIP file ---
    for zip_file in zip_paths:
        print(f"\n--- {mode_name} {zip_file.name} ---")
        # Call the core library function (process_zip or purge_zip_contents)
        action_func(zip_file) 
        
    # Concluding step: show the final state of the main symbol library
    print("\n--- Final List of Main Symbols ---")
    list_symbols_simple(PROJECT_SYMBOL_LIB, print_list=True)

if __name__ == "__main__": 
    main()