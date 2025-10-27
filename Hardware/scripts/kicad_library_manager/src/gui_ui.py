# gui_ui.py
"""
DearPyGui layout + widget creation for the KiCad Library Manager GUI.

All business logic, logging, persistence, and CLI calls live in gui_core.py.
This file only builds the window, tabs, buttons, and connects callbacks.
"""

from __future__ import annotations
import dearpygui.dearpygui as dpg
from pathlib import Path

# -------------------------
# Internal imports
# -------------------------
from gui_core import (
    FONT_SIZE,
    RENAME_ASSETS_KEY,
    load_config,
    save_config,
    log_message,
    clear_log,
    show_full_log_popup,
    process_action,
    toggle_selection_mode,
    open_folder_in_explorer,
    show_native_folder_dialog,
    refresh_file_list,
    open_output_folder,
    export_action,
    update_drc_rules,
    initial_load,
    open_url,
    GUI_FILE_DATA,
)

# -------------------------
# File list UI builder
# -------------------------


def build_file_list_ui(dpg):
    """Rebuild ZIP file checkboxes from gui_core.GUI_FILE_DATA."""
    from gui_core import FILE_CHECKBOXES_CONTAINER, FILE_COUNT_TAG

    dpg.delete_item(FILE_CHECKBOXES_CONTAINER, children_only=True)
    dpg.set_value(FILE_COUNT_TAG, f"Total files found: {len(GUI_FILE_DATA)}")

    if not GUI_FILE_DATA:
        with dpg.group(parent=FILE_CHECKBOXES_CONTAINER):
            dpg.add_text(
                "No ZIP files loaded. Select a folder to begin.", color=[255, 165, 0]
            )
        return

    with dpg.group(parent=FILE_CHECKBOXES_CONTAINER):
        for i, data in enumerate(GUI_FILE_DATA):
            tag = f"checkbox_{i}"
            status = data["status"]

            status_text = ""
            status_color = (200, 200, 200)
            is_new = True

            if status == "PARTIAL":
                status_text = "(Partial Match/Existing Symbols)"
                status_color = (255, 165, 0)
                is_new = False
            elif status == "NEW":
                status_text = "(New)"
                status_color = (0, 255, 0)
                is_new = True
            elif status == "ERROR":
                status_text = "(Error Scanning)"
                status_color = (255, 0, 0)
                is_new = False
            elif status == "NONE":
                status_text = "(No Symbols Found)"
                status_color = (150, 150, 150)
                is_new = False

            with dpg.group(horizontal=True):
                checkbox = dpg.add_checkbox(
                    label=data["name"], default_value=is_new, tag=tag
                )
                with dpg.tooltip(parent=checkbox):
                    dpg.add_text(data["tooltip"])
                dpg.add_text(status_text, color=status_color)


# -------------------------
# Project symbol list UI
# -------------------------


def refresh_symbol_list(dpg):
    """Rebuild checkboxes for symbols in ProjectSymbols.kicad_sym."""
    from gui_core import list_project_symbols

    symbols = list_project_symbols()
    dpg.delete_item("symbol_checkboxes_container", children_only=True)
    dpg.set_value("symbol_count_text", f"Total symbols found: {len(symbols)}")

    if not symbols:
        with dpg.group(parent="symbol_checkboxes_container"):
            dpg.add_text(
                "No symbols found in ProjectSymbols.kicad_sym.", color=[255, 100, 100]
            )
        return

    with dpg.group(parent="symbol_checkboxes_container"):
        for i, name in enumerate(symbols):
            tag = f"symbol_checkbox_{i}"
            dpg.add_checkbox(label=name, tag=tag, default_value=False)


# -------------------------
# Font loading
# -------------------------


def find_font_recursively(font_name: str) -> Path | None:
    """Search recursively for a font file in common folders."""
    import sys

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    for root in [base, base / "src", base / "fonts", base / "src" / "fonts"]:
        if root.exists():
            for path in root.rglob(font_name):
                return path
    print(f"⚠️ Font '{font_name}' not found in {base} or common subdirectories.")
    return None


def load_font_recursively(font_name: str, size: int = 18):
    """Load a font or fallback to DearPyGui default."""
    font_path = find_font_recursively(font_name)
    if not font_path:
        print(f"⚠️ Using default DearPyGui font (couldn't find '{font_name}')")
        return
    with dpg.font_registry():
        font = dpg.add_font(str(font_path), size)
        dpg.bind_font(font)
        print(f"✅ Loaded font: {font_path}")


# -------------------------
# GUI creation
# -------------------------


def create_gui():
    """Main GUI window creation (identical visuals)."""
    dpg.create_context()

    # Load config
    config = load_config()
    rename_default = config.get(RENAME_ASSETS_KEY, False)

    # Font
    load_font_recursively("NotoSans-Regular.ttf", size=FONT_SIZE)

    dpg.create_viewport(
        title="KiCad Library Manager", width=900, height=750, resizable=True
    )
    dpg.setup_dearpygui()

    # ---------- Theme setup ----------
    with dpg.theme() as global_theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (25, 25, 25))
    dpg.bind_theme(global_theme)

    def setup_log_theme(tag, color):
        with dpg.theme(tag=tag):
            with dpg.theme_component(dpg.mvInputText):
                dpg.add_theme_color(dpg.mvThemeCol_Text, color)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (0, 0, 0, 0))

    setup_log_theme("default_log_theme", (200, 200, 200))
    setup_log_theme("cli_output_theme", (140, 140, 140))
    setup_log_theme("error_log_theme", (255, 50, 50))
    setup_log_theme("success_log_theme", (0, 255, 0))

    with dpg.theme(tag="hyperlink_theme"):
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (30, 30, 50, 50))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (150, 150, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 0, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 0)

    # ---------- Main window ----------
    with dpg.window(
        tag="main_window", label="KiCad Library Manager", width=900, height=750
    ):
        dpg.set_primary_window("main_window", True)

        # Folder Selection
        dpg.add_text(
            "1. Select Archive Folder (ZIPs will be scanned automatically):",
            color=[0, 255, 255],
        )
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Select ZIP-Folder",
                callback=lambda s, a: show_native_folder_dialog(dpg, s, a),
            )
            dpg.add_button(
                label="Open ZIP-Folder",
                callback=lambda s, a: open_folder_in_explorer(dpg, s, a),
            )
        dpg.add_text(
            "Current Folder: (Initializing...)",
            tag="current_path_text",
            wrap=0,
            color=[150, 150, 255],
        )
        dpg.add_separator()

        # Tabs
        dpg.add_text("2. Select Mode:", color=[255, 255, 0])
        with dpg.tab_bar(
            tag="source_tab_bar",
            callback=lambda s, a, u=None: from_core_on_tab_change(dpg, s, a, u),
        ):
            with dpg.tab(label="Import ZIP Archives", tag="zip_tab"):
                with dpg.group(horizontal=True):
                    dpg.add_text(
                        "Total ZIP files found: 0",
                        tag="file_count_text",
                        color=[0, 255, 0],
                    )
                    dpg.add_button(
                        label="Refresh ZIPs",
                        callback=lambda s, a: refresh_file_list(dpg, s, a),
                        small=True,
                    )
                    dpg.add_button(
                        label="Select All",
                        tag="toggle_select_zip_btn",
                        callback=lambda s, a: toggle_selection_mode(
                            dpg, "file_checkboxes_container", "toggle_select_zip_btn"
                        ),
                        small=True,
                    )
                with dpg.child_window(
                    tag="file_checkboxes_container", width=-1, height=180, border=True
                ):
                    pass

            with dpg.tab(label="Export Project Symbols", tag="symbol_tab"):
                with dpg.group(horizontal=True):
                    dpg.add_text(
                        "Total symbols found: 0",
                        tag="symbol_count_text",
                        color=[0, 255, 0],
                    )
                    dpg.add_button(
                        label="Refresh Symbols",
                        callback=lambda s, a: refresh_symbol_list(dpg),
                        small=True,
                    )
                    dpg.add_button(
                        label="Select All",
                        tag="toggle_select_symbol_btn",
                        callback=lambda s, a: toggle_selection_mode(
                            dpg,
                            "symbol_checkboxes_container",
                            "toggle_select_symbol_btn",
                        ),
                        small=True,
                    )
                with dpg.child_window(
                    tag="symbol_checkboxes_container", width=-1, height=180, border=True
                ):
                    pass

            with dpg.tab(label="DRC Manager", tag="drc_tab"):
                dpg.add_text(
                    "Auto-Apply DRC Rules Based on PCB Layer Count", color=[255, 255, 0]
                )
                dpg.add_spacer(height=5)
                dpg.add_button(
                    label="Update DRC Rules",
                    width=220,
                    callback=lambda s, a: update_drc_rules(dpg, s, a),
                )
                dpg.add_separator()
                dpg.add_text("- Detects nearest .kicad_pcb file")
                dpg.add_text("- Counts copper layers automatically")
                dpg.add_text(
                    "- Copies matching dru_X_layer.kicad_dru to Project.kicad_dru"
                )

        dpg.add_separator()

        # Action groups
        with dpg.group(tag="zip_action_group", horizontal=True, horizontal_spacing=20):
            dpg.add_button(
                label="PROCESS / IMPORT",
                tag="process_btn",
                width=200,
                callback=lambda s, a: process_action(dpg, s, a, False),
            )
            dpg.add_button(
                label="PURGE / DELETE",
                tag="purge_btn",
                width=200,
                callback=lambda s, a: process_action(dpg, s, a, True),
            )
            rename_chk = dpg.add_checkbox(
                label="Use Symbolname as Footprint and 3D-Model name",
                default_value=rename_default,
                tag="rename_assets_chk",
                callback=lambda s, a, u=None: save_config(
                    RENAME_ASSETS_KEY, dpg.get_value(s)
                ),
            )
            with dpg.tooltip(parent=rename_chk):
                dpg.add_text(
                    "If checked, the system renames footprint and 3D model files "
                    "inside ZIPs to match the primary symbol name before import."
                )

        with dpg.group(
            tag="symbol_action_group",
            horizontal=True,
            horizontal_spacing=20,
            show=False,
        ):
            dpg.add_button(
                label="EXPORT SELECTED",
                tag="export_btn",
                width=200,
                callback=lambda s, a: export_action(dpg, s, a),
            )
            dpg.add_button(
                label="OPEN OUTPUT FOLDER",
                tag="open_output_btn",
                width=200,
                callback=lambda s, a: open_output_folder(dpg, s, a),
            )

        dpg.add_text("NOTE: Only checked files will be used.")
        dpg.add_separator()

        # Log output
        with dpg.group(horizontal=True):
            dpg.add_text("CLI Output Log:")
            dpg.add_button(
                label="Clear Log", callback=lambda s, a: clear_log(dpg), small=True
            )
            dpg.add_button(
                label="Show Full Log",
                callback=lambda s, a: show_full_log_popup(dpg),
                small=True,
            )

        with dpg.child_window(
            tag="log_window_child", width=-1, height=150, border=True
        ):
            dpg.add_group(tag="log_text_container", width=-1)

        dpg.add_input_int(tag="scroll_flag_int", default_value=0, show=False)
        dpg.add_separator()

        # Footer
        with dpg.group(horizontal=True):
            author = dpg.add_button(
                label="By: Ihysol (Tobias Gent)",
                callback=lambda s, a: open_url(dpg, s, a, "https://github.com/Ihysol"),
                small=True,
            )
            dpg.bind_item_theme(author, "hyperlink_theme")
            dpg.add_spacer(width=5)
            issues = dpg.add_button(
                label="Report Bug / Suggest Feature",
                callback=lambda s, a: open_url(
                    dpg, s, a, "https://github.com/Ihysol/kicad-template"
                ),
                small=True,
            )
            dpg.bind_item_theme(issues, "hyperlink_theme")

    # Launch
    dpg.show_viewport()
    initial_load(dpg)
    dpg.start_dearpygui()
    dpg.destroy_context()


# Helper alias for tab callback (avoids import loop)
def from_core_on_tab_change(dpg, sender, app_data, user_data):
    from gui_core import on_tab_change

    return on_tab_change(dpg, sender, app_data, user_data)
