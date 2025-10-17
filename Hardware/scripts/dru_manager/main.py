from sexpdata import loads, Symbol
from pathlib import Path
import shutil

def find_upward(target: str, start_path: Path) -> Path | None:
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

def get_pcb_layer_count(pcb_file: Path):
    # --- Parse the PCB file as S-expression
    with open(pcb_file, "r", encoding="utf-8") as f:
        sexpr = loads(f.read())

    # --- Find the (layers ...) block
    layers_block = None
    for e in sexpr:
        if isinstance(e, list) and len(e) > 0 and e[0] == Symbol("layers"):
            layers_block = e
            break

    if not layers_block:
        print("No (layers ...) block found in PCB file")
        exit()

    # --- Extract copper layers
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

pcb = find_upward("*.kicad_pcb", Path.cwd())

if pcb:
    print(f"Found board: {pcb}")
        
else:
    print("No .kicad_pcb file found")
    exit()

layer_count = get_pcb_layer_count(pcb)
print(f"Detected {layer_count} copper layers in {pcb.name}")

dru_template_dir = find_upward("dru_templates", Path.cwd())

src = find_upward("dru_{}_layer.kicad_dru".format(layer_count), dru_template_dir)
dst = find_upward("Project.kicad_dru", Path.cwd())

shutil.copyfile(src, dst)

print(f"✅ Applied {src.name} → {dst.parent.name}/{dst.name}")