import subprocess
import os

cmd_pdf = [
    "pandoc",
    "documentation.md",
    "-o", "output.pdf",
    "--pdf-engine=xelatex",
    "--resource-path=./img",
    "--number-sections"
]

def run_command(cmd, description):
    print(f"Running: {description}")
    try:
        subprocess.run(cmd, check=True)
        print(f"✓ {description} completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Error during {description}: {e}")
        return False

if __name__ == "__main__":
    run_command(cmd_pdf, "PDF conversion")
