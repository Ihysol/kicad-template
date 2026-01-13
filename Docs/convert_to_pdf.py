from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
MARKDOWN_DIR = ROOT / "markdown"
PDF_DIR = ROOT / "pdf"


def run_command(cmd, description):
    print(f"Running: {description}")
    try:
        subprocess.run(cmd, check=True)
        print(f"{description} completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error during {description}: {e}")
        return False


def build_pdf(md_path):
    output_path = PDF_DIR / f"{md_path.stem}.pdf"
    cmd = [
        "pandoc",
        str(md_path),
        "-o",
        str(output_path),
        "--pdf-engine=xelatex",
        "--resource-path",
        str(ROOT / "img"),
        "--number-sections",
    ]
    return run_command(cmd, f"PDF conversion: {md_path.name}")


def main():
    if not MARKDOWN_DIR.exists():
        print(f"Markdown folder not found: {MARKDOWN_DIR}")
        return 1

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    md_files = sorted(MARKDOWN_DIR.glob("*.md"))
    if not md_files:
        print(f"No markdown files found in {MARKDOWN_DIR}")
        return 0

    success = True
    for md_file in md_files:
        if not build_pdf(md_file):
            success = False

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
