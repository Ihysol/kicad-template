from sexpdata import loads, Symbol
from pathlib import Path
import shutil
import sys


def find_upward(target: str, start_path: Path) -> Path | None:
    """Search upward through parent folders for a matching file or directory."""
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


def get_pcb_layer_count(pcb_file: Path) -> int:
    """Parse a .kicad_pcb file and return the number of copper layers."""
    with open(pcb_file, "r", encoding="utf-8") as f:
        sexpr = loads(f.read())

    layers_block = None
    for e in sexpr:
        if isinstance(e, list) and len(e) > 0 and e[0] == Symbol("layers"):
            layers_block = e
            break

    if not layers_block:
        print("No (layers ...) block found in PCB file")
        pause_if_frozen()
        sys.exit(1)

    # --- Extract copper layers ---
    copper_layers = [
        layer for layer in layers_block[1:]
        if isinstance(layer, list)
        and len(layer) > 1
        and str(layer[1]).endswith(".Cu")
    ]

    print(f"🧩 {len(copper_layers)} copper layers detected:")
    for layer in copper_layers:
        print("  -", layer[1])

    return len(copper_layers)


def pause_if_frozen():
    """Pause terminal when running as a PyInstaller executable."""
    if getattr(sys, "frozen", False):
        print("Press Enter to exit...")
        input()


# --- Main logic ---
print("🔍 Searching for KiCad board...")

pcb = find_upward("*.kicad_pcb", Path.cwd())

if pcb:
    print(f"Found board: {pcb}")
else:
    print("No .kicad_pcb file found")
    pause_if_frozen()
    sys.exit(1)

layer_count = get_pcb_layer_count(pcb)
print(f"Detected {layer_count} copper layers in {pcb.name}")

# --- Find DRC template directory ---
dru_template_dir = find_upward("dru_templates", Path.cwd())
if not dru_template_dir:
    print("No 'dru_templates' folder found")
    pause_if_frozen()
    sys.exit(1)

# --- Choose correct template ---
template_name = f"dru_{layer_count}_layer.kicad_dru"
if layer_count >= 4:
    template_name = "dru_4_layer.kicad_dru"

src = find_upward(template_name, dru_template_dir)
if not src or not src.exists():
    print(f"Could not find template: {template_name}")
    pause_if_frozen()
    sys.exit(1)

# --- Find target DRC file ---
dst = find_upward("Project.kicad_dru", Path.cwd())
if not dst:
    dst = Path.cwd() / "Project.kicad_dru"

# --- Copy file ---
shutil.copyfile(src, dst)
print(f"Applied {src.name} -> {dst.parent.name}/{dst.name}")

pause_if_frozen()
