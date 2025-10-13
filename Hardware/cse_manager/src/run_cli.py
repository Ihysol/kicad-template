# run_cli.py

import subprocess
import sys
from pathlib import Path


def execute_library_action(zip_paths: list[Path], is_purge: bool = False):
    """
    Executes cli_main.py with the specified arguments in a separate process.

    Args:
        zip_paths: A list of Path objects for the ZIP files to process.
        is_purge: If True, adds the --purge flag.
    """

    # 1. Build the base command
    # Use sys.executable to ensure the correct Python environment is used
    command = [sys.executable, str(Path("cli_main.py").resolve())]

    # 2. Add the action flag
    if is_purge:
        command.append("--purge")

    # 3. Add the ZIP file paths
    # pass the paths as strings
    command.extend([str(p) for p in zip_paths])

    # 4. Execute the command and capture output
    try:
        print(f"Executing: {' '.join(command)}")

        # Run the command
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,  # Raise an exception if the command returns a non-zero exit status
        )

        # Print the output from the CLI script
        print("--- CLI OUTPUT START ---")
        print(result.stdout)
        print("--- CLI OUTPUT END ---")

        return True, result.stdout

    except subprocess.CalledProcessError as e:
        print("--- CLI ERROR START ---")
        print(f"CLI failed with exit code {e.returncode}")
        print(e.stderr)
        print("--- CLI ERROR END ---")
        return False, e.stderr
    except FileNotFoundError:
        print(f"Error: Python executable or cli_main.py not found.")
        return False, "Execution failed."


if __name__ == "__main__":
    # Example usage (for testing this runner file directly)
    # This requires having a test ZIP file and cli_main.py setup
    print("Testing run_cli.py... Requires a valid cli_main.py in the same directory.")
    test_zip = Path("./generate/test_part.zip")  # Use a path that exists in your setup
    success, output = execute_library_action([test_zip], is_purge=False)
    print(f"\nTest Result: {'Success' if success else 'Failure'}")
