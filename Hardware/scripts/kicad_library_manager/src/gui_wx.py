import wx
import wx.dataview as dv
from pathlib import Path
import threading
import sys

from gui_core import (
    APP_VERSION,
    load_config,
    save_config,
    clear_log,
    process_action,
    export_action,
    update_drc_rules,
    refresh_file_list,
    show_native_folder_dialog,
    open_folder_in_explorer,
    open_output_folder,
    toggle_selection_mode,
    initial_load,
    on_tab_change,
    USE_SYMBOLNAME_KEY
)
from library_manager import INPUT_ZIP_FOLDER

# ===============================
# Logging
# ===============================
import logging

# --- Ensure sys.stdout/stderr exist (important for PyInstaller GUI builds) ---
if not hasattr(sys, "stdout") or sys.stdout is None:
    class DevNull:
        def write(self, *_): pass
        def flush(self): pass
    sys.stdout = DevNull()
    sys.stderr = DevNull()

logger = logging.getLogger("kicad_library_manager")
logger.setLevel(logging.DEBUG)

# --- Formatter with auto-clean for duplicate prefixes ---
class CleanFormatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        # Strip manually embedded tags like [INFO] [DEBUG]
        for tag in ("[INFO]", "[DEBUG]", "[WARN]", "[ERROR]", "[OK]"):
            msg = msg.replace(tag, "").strip()
        record.message = msg
        return super().format(record)

formatter = CleanFormatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

# --- Console handler (for debug mode or when running via python gui_wx.py) ---
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

# --- Remove broken handlers (from PyInstaller --noconsole builds) ---
for h in list(logger.handlers):
    if isinstance(h, logging.StreamHandler) and getattr(h.stream, "write", None) is None:
        logger.removeHandler(h)

# --- Do not propagate logs to root (prevents duplication) ---
logger.propagate = False


# --- GUI Log handler (later attached to wx.TextCtrl in MainFrame) ---
class WxGuiLogHandler(logging.Handler):
    """Custom logging handler that forwards logs into wx TextCtrl."""
    def __init__(self, gui_frame):
        super().__init__()
        self.gui_frame = gui_frame

    def emit(self, record):
        try:
            msg = formatter.format(record)
            wx.CallAfter(self.gui_frame.append_log, msg)
        except Exception:
            pass


# ===============================
# drag & drop feature
# ===============================

class ZipFileDropTarget(wx.FileDropTarget):
    """Handles drag-and-drop of .zip archives onto the ZIP list."""
    def __init__(self, parent_frame):
        super().__init__()
        self.parent = parent_frame

    def OnDropFiles(self, x, y, filenames):
        import shutil
        from pathlib import Path
        from gui_core import refresh_file_list
        from library_manager import INPUT_ZIP_FOLDER

        dropped = []
        INPUT_ZIP_FOLDER.mkdir(parents=True, exist_ok=True)

        for f in filenames:
            p = Path(f)
            if p.is_file() and p.suffix.lower() == ".zip":
                target = INPUT_ZIP_FOLDER / p.name
                try:
                    if target != p:
                        shutil.copy2(p, target)
                    dropped.append(target)
                except Exception as e:
                    wx.CallAfter(self.parent.append_log, f"[ERROR] Failed to copy {p.name}: {e}")

        if dropped:
            wx.CallAfter(self.parent.append_log, f"[OK] Added {len(dropped)} ZIP archive(s).")
            wx.CallAfter(refresh_file_list, self.parent.shim)

        return True




# ===============================
# DearPyGui compatibility shim
# ===============================
class DpgShim:
    """Compatibility layer so gui_core calls work with wxPython."""

    def __init__(self, gui):
        self.gui = gui
        self._uuid_counter = 0
        self._values = {"scroll_flag_int": 0, "current_path_text": ""}
        self._visible = set()

    # ----- storage / values -----
    def set_value(self, tag, value):
        self._values[tag] = value
        self.gui.set_value(tag, value)

    def get_value(self, tag):
        # Simulate DPG tags for backend compatibility
        if tag == "use_symbol_name_chkbox":
            return self.chk_use_symbol_name.GetValue()
        if tag == "current_path_text":
            return self._values.get(tag, self.gui.current_folder_txt.GetLabel())
        if tag == "source_tab_bar":
            sel = self.gui.notebook.GetSelection()
            if sel == 0:
                return "zip_tab"
            elif sel == 1:
                return "symbol_tab"
            elif sel == 2:
                return "drc_tab"
            return ""
        return self._values.get(tag, 0)

    def does_item_exist(self, tag):
        return True

    # ----- logging -----
    def add_text(self, text, **kwargs):
        if text:
            self.gui.append_log(text)

    def add_input_text(self, default_value="", **kwargs):
        if default_value:
            self.gui.append_log(default_value)
        return self.generate_uuid()

    def bind_item_theme(self, *args, **kwargs): pass
    def set_y_scroll(self, *args, **kwargs): pass

    def generate_uuid(self):
        self._uuid_counter += 1
        return f"wx_uuid_{self._uuid_counter}"

    # ----- visibility -----
    def show_item(self, tag):
        self._visible.add(tag)
        self.gui.show_section(tag, True)

    def hide_item(self, tag):
        if tag in self._visible:
            self._visible.remove(tag)
        self.gui.show_section(tag, False)

    # ----- minimal layout mocks -----
    def group(self, *args, **kwargs):
        class DummyCtx:
            def __enter__(self_): return None
            def __exit__(self_, exc_type, exc, tb): return False
        return DummyCtx()

    def add_checkbox(self, *args, **kwargs): return self.generate_uuid()
    def add_button(self, *args, **kwargs): return self.generate_uuid()
    def add_separator(self, *args, **kwargs): return self.generate_uuid()
    def add_child_window(self, *args, **kwargs): return self.generate_uuid()
    def get_item_label(self, tag):
        if tag == "symbol_tab": return "Export Project Symbols"
        if tag == "zip_tab": return "Import ZIP Archives"
        if tag == "drc_tab": return "DRC Manager"
        return str(tag)

    def get_item_children(self, tag, slot): return []
    def get_item_type(self, tag): return "mvAppItemType::mvCheckbox"
    def set_item_label(self, tag, label): pass

    def delete_item(self, tag, children_only=False):
        if tag == "log_text_container":
            self.gui.clear_log()

    def refresh_symbol_list(self, *args, **kwargs):
        try:
            from gui_core import list_project_symbols
            symbols = list_project_symbols()
        except Exception:
            symbols = []
        if hasattr(self.gui, "symbol_list"):
            lst = self.gui.symbol_list
            lst.Clear()
            for sym in symbols:
                lst.Append(sym)
                
    def _make_status_icon(self, colour: wx.Colour, size=12):
        """Create a small coloured square bitmap for status indicators."""
        bmp = wx.Bitmap(size, size)
        dc = wx.MemoryDC(bmp)
        dc.SetBrush(wx.Brush(colour))
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.DrawRectangle(0, 0, size, size)
        dc.SelectObject(wx.NullBitmap)
        return bmp

    # ----- file/symbol list hooks -----
    def build_file_list_ui(self, *args, **kwargs):
        """Populate DataView list with coloured bitmap status icons + text."""
        try:
            from gui_core import GUI_FILE_DATA
        except Exception:
            return

        lst = self.gui.zip_file_list
        model = lst.GetStore()
        model.DeleteAllItems()

        # Map backend statuses to colours + display text
        status_styles = {
            "NEW":              (wx.Colour(0, 255, 0),       "NEW"),
            "PARTIAL":          (wx.Colour(255, 160, 0),     "IN PROJECT"),
            "MISSING_SYMBOL":   (wx.Colour(255, 80, 80),     "Missing Symbol (cannot import)"),
            "MISSING_FOOTPRINT":(wx.Colour(255, 80, 80),     "Missing Footprint (cannot import)"),
            "ERROR":            (wx.Colour(255, 80, 80),     "ERROR"),
            "NONE":             (wx.Colour(180, 180, 180),   "MISSING"),
        }

        for row in GUI_FILE_DATA:
            name = row.get("name", "unknown.zip")
            raw_status = row.get("status", "")
            colour, text = status_styles.get(raw_status, (wx.Colour(180, 180, 180), raw_status or "—"))

            # Disabled (invalid) if missing symbol or footprint
            is_disabled = raw_status in ("MISSING_SYMBOL", "MISSING_FOOTPRINT")

            icon = self._make_status_icon(colour)
            icontext = wx.dataview.DataViewIconText(f" {text}", icon)

            # Checkbox value = False if disabled (so cannot be imported)
            # “Delete” column still available.
            model.AppendItem([not is_disabled and raw_status != "PARTIAL", name, icontext, "double-click to delete"])




    # Hook into gui_ui so backend calls are redirected to wxPython
    import types, sys

    # Create a dummy module "gui_ui" dynamically
    gui_ui = types.ModuleType("gui_ui")
    sys.modules["gui_ui"] = gui_ui

    # Redirect backend functions to wx equivalents
    gui_ui.build_file_list_ui = lambda dpg: dpg.build_file_list_ui()
    gui_ui.refresh_symbol_list = lambda dpg: dpg.refresh_symbol_list()

# ===============================
# Main GUI Frame
# ===============================
class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title=f"KiCad Library Manager (wxPython) — {APP_VERSION}", size=(980, 800))
        self.shim = DpgShim(self)
        self._values = {}
        self.InitUI()
        self.Centre()
        self.Show()
        initial_load(self.shim)
        cfg = load_config()
        self.chk_use_symbol_name.SetValue(cfg.get(USE_SYMBOLNAME_KEY, False))
        # --- logger setup (only add once) ---
        if not any(isinstance(h, WxGuiLogHandler) for h in logger.handlers):
            gui_handler = WxGuiLogHandler(self)
            gui_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
            logger.addHandler(gui_handler)
            logger.propagate = False  # prevent double-printing via root logger

        
    # ---------- Layout ----------
    def InitUI(self):
        panel = wx.Panel(self)
        self.panel = panel
        vbox = wx.BoxSizer(wx.VERTICAL)

        # --- Folder selection ---
        box1 = wx.StaticBox(panel, label="Select Archive Folder")
        s1 = wx.StaticBoxSizer(box1, wx.VERTICAL)
        h_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_select = wx.Button(panel, label="Select ZIP Folder")
        self.btn_open = wx.Button(panel, label="Open ZIP Folder")
        h_buttons.Add(self.btn_select, 0, wx.RIGHT, 8)
        h_buttons.Add(self.btn_open, 0)
        self.current_folder_txt = wx.StaticText(panel, label="Current Folder: (Initializing...)")
        s1.Add(h_buttons, 0, wx.BOTTOM, 5)
        s1.Add(self.current_folder_txt, 0, wx.TOP, 2)
        vbox.Add(s1, 0, wx.EXPAND | wx.ALL, 8)

        # --- Tabs ---
        self.notebook = wx.Notebook(panel)
        self.tab_zip = wx.Panel(self.notebook)
        self.tab_symbol = wx.Panel(self.notebook)
        self.tab_drc = wx.Panel(self.notebook)
        self.notebook.AddPage(self.tab_zip, "Import ZIP Archives")
        self.notebook.AddPage(self.tab_symbol, "Export Project Symbols")
        self.notebook.AddPage(self.tab_drc, "DRC Manager")
        vbox.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 8)

        # --- ZIP tab content ---
        self.zip_vbox = wx.BoxSizer(wx.VERTICAL)

        # === Top controls ===
        h_zip_top = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refresh_zips = wx.Button(self.tab_zip, label="Refresh ZIPs")
        self.chk_master_zip = wx.CheckBox(self.tab_zip, label="Select All")
        h_zip_top.Add(self.btn_refresh_zips, 0, wx.RIGHT, 8)
        h_zip_top.Add(self.chk_master_zip, 0, wx.ALIGN_CENTER_VERTICAL)
        self.zip_vbox.Add(h_zip_top, 0, wx.BOTTOM, 5)

        # === ZIP file list ===
        self.zip_file_list = dv.DataViewListCtrl(
            self.tab_zip,
            style=dv.DV_ROW_LINES | dv.DV_VERT_RULES | dv.DV_SINGLE
        )
        self.zip_file_list.AppendToggleColumn("", width=40)
        self.zip_file_list.AppendTextColumn("Archive Name", width=300)
        self.zip_file_list.AppendIconTextColumn("Status", width=250, align=wx.ALIGN_LEFT)
        self.zip_file_list.AppendTextColumn("Delete", width=80, align=wx.ALIGN_CENTER)
        self.zip_file_list.SetDropTarget(ZipFileDropTarget(self))
        self.zip_file_list.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, self.on_zip_row_clicked)
        self.zip_file_list.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, self.on_zip_delete_clicked)
        
        # dynamically resize columns to always fill 100%
        self.zip_file_list.Bind(wx.EVT_SIZE, self.on_resize_zip_columns)        
        self.zip_vbox.Add(wx.StaticText(self.tab_zip, label="ZIP Archives:"), 0, wx.BOTTOM, 5)
        self.zip_vbox.Add(self.zip_file_list, 1, wx.EXPAND | wx.BOTTOM, 5)

        # === Option checkbox ===
        self.chk_use_symbol_name = wx.CheckBox(
            self.tab_zip,
            label="Use symbol name as footprint and 3D model name"
        )
        self.zip_vbox.Add(self.chk_use_symbol_name, 0, wx.TOP | wx.BOTTOM, 5)

        # === Process / Purge buttons ===
        self.btn_process = wx.Button(self.tab_zip, label="PROCESS / IMPORT")
        self.btn_purge = wx.Button(self.tab_zip, label="PURGE / DELETE")
        h_zip_btns = wx.BoxSizer(wx.HORIZONTAL)
        h_zip_btns.Add(self.btn_process, 0, wx.RIGHT, 8)
        h_zip_btns.Add(self.btn_purge, 0)
        self.zip_vbox.Add(h_zip_btns, 0, wx.TOP, 5)

        self.tab_zip.SetSizer(self.zip_vbox)

        # --- Symbol tab content ---
        self.sym_vbox = wx.BoxSizer(wx.VERTICAL)
        
        h_sym_top = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refresh_symbols = wx.Button(self.tab_symbol, label="Refresh Symbols")
        self.chk_master_symbols = wx.CheckBox(self.tab_symbol, label="Select All")
        
        # --- fix ghost button / layout artifact ---
        self.btn_refresh_symbols.SetMinSize((150, -1))
        self.chk_master_symbols.SetMinSize((100, -1))
        self.btn_refresh_symbols.SetWindowStyleFlag(wx.BU_EXACTFIT)

        h_sym_top.Add(self.btn_refresh_symbols, 0, wx.RIGHT, 8)
        h_sym_top.Add(self.chk_master_symbols, 0, wx.ALIGN_CENTER_VERTICAL)
        self.sym_vbox.Add(h_sym_top, 0, wx.BOTTOM, 5)
        
        self.symbol_list = wx.CheckListBox(self.tab_symbol)
        self.sym_vbox.Add(wx.StaticText(self.tab_symbol, label="Project Symbols:"), 0, wx.BOTTOM, 5)
        self.sym_vbox.Add(self.symbol_list, 1, wx.EXPAND | wx.BOTTOM, 5)

        # --- Export + Orphan Delete Buttons ---
        self.btn_export = wx.Button(self.tab_symbol, label="EXPORT SELECTED")
        self.btn_open_output = wx.Button(self.tab_symbol, label="OPEN OUTPUT FOLDER")
        self.btn_delete_orphans = wx.Button(self.tab_symbol, label="DELETE SELECTED")
        self.tab_symbol.Layout()
        self.btn_delete_orphans.SetForegroundColour(wx.RED)

        h_sym_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_delete_selected = wx.Button(self.tab_symbol, label="DELETE SELECTED")
        self.btn_delete_selected.SetForegroundColour(wx.RED)

        h_sym_btns.Add(self.btn_export, 0, wx.RIGHT, 8)
        h_sym_btns.Add(self.btn_open_output, 0, wx.RIGHT, 8)
        h_sym_btns.Add(self.btn_delete_selected, 0)
        self.sym_vbox.Add(h_sym_btns, 0, wx.TOP, 5)
        self.tab_symbol.SetSizer(self.sym_vbox)


        # --- DRC tab content ---
        self.drc_vbox = wx.BoxSizer(wx.VERTICAL)
        self.btn_drc = wx.Button(self.tab_drc, label="Update DRC Rules")
        self.drc_vbox.Add(wx.StaticText(self.tab_drc, label="Auto-Apply DRC Rules Based on PCB Layer Count:"), 0, wx.BOTTOM, 5)
        self.drc_vbox.Add(self.btn_drc, 0, wx.BOTTOM, 5)
        self.tab_drc.SetSizer(self.drc_vbox)

        # --- Log output ---
        self.log_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        vbox.Add(self.log_ctrl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)


        # --- Footer ---
        footer_box = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_author = wx.Button(panel, label="By: Ihysol (Tobias Gent)", style=wx.BU_EXACTFIT)
        self.btn_author.SetForegroundColour(wx.Colour(50, 50, 255))
        self.btn_author.SetBackgroundColour(panel.GetBackgroundColour())
        self.btn_author.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        footer_box.Add(self.btn_author, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 10)
        self.btn_issues = wx.Button(panel, label="Report Bug / Suggest Feature", style=wx.BU_EXACTFIT)
        self.btn_issues.SetForegroundColour(wx.Colour(50, 50, 255))
        self.btn_issues.SetBackgroundColour(panel.GetBackgroundColour())
        self.btn_issues.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        footer_box.Add(self.btn_issues, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 10)
        self.lbl_version = wx.StaticText(panel, label=f"Version: {APP_VERSION}")
        footer_box.AddStretchSpacer(1)
        footer_box.Add(self.lbl_version, 0, wx.ALIGN_CENTER_VERTICAL)
        vbox.Add(footer_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.SetSizer(vbox)

        # --- Bind events ---
        self.Bind(wx.EVT_BUTTON, lambda e: self.open_url("https://github.com/Ihysol"), self.btn_author)
        self.Bind(wx.EVT_BUTTON, lambda e: self.open_url("https://github.com/Ihysol/kicad-template"), self.btn_issues)
        self.Bind(wx.EVT_BUTTON, self.on_refresh_symbols, self.btn_refresh_symbols)
        self.Bind(wx.EVT_CHECKLISTBOX, self.on_symbol_item_toggled, self.symbol_list)
        self.Bind(wx.EVT_CHECKBOX, self.on_use_symbol_name_toggled, self.chk_use_symbol_name)
        self.Bind(wx.EVT_BUTTON, self.on_delete_selected, self.btn_delete_selected)


        self.Bind(wx.EVT_CHECKBOX, self.on_master_symbols_toggle, self.chk_master_symbols)
        self.Bind(wx.EVT_BUTTON, self.on_export, self.btn_export)
        self.Bind(wx.EVT_BUTTON, self.on_open_output, self.btn_open_output)
        self.Bind(wx.EVT_BUTTON, self.on_select_zip_folder, self.btn_select)
        self.Bind(wx.EVT_BUTTON, self.on_open_folder, self.btn_open)
        self.Bind(wx.EVT_BUTTON, self.on_process, self.btn_process)
        self.Bind(wx.EVT_BUTTON, self.on_purge, self.btn_purge)
        self.Bind(wx.EVT_BUTTON, self.on_drc_update, self.btn_drc)
        self.Bind(wx.EVT_BUTTON, self.on_refresh_zips, self.btn_refresh_zips)
        self.Bind(wx.EVT_CHECKBOX, self.on_master_zip_toggle, self.chk_master_zip)
        self.zip_file_list.Bind(dv.EVT_DATAVIEW_ITEM_VALUE_CHANGED, self.on_zip_checkbox_changed)
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_tab_changed)

    # ---------- Event handlers ----------
    def on_resize_zip_columns(self, event):
        """Keep ZIP list columns evenly split (33% each) when resized."""
        event.Skip()
        total_width = self.zip_file_list.GetClientSize().width
        toggle_col_width = 40  # keep the first checkbox column fixed
        usable_width = max(total_width - toggle_col_width, 0)

        # Split remaining width equally among the 3 visible columns
        col_width = usable_width // 3
        self.zip_file_list.GetColumn(1).SetWidth(col_width)  # Archive Name
        self.zip_file_list.GetColumn(2).SetWidth(col_width)  # Status
        self.zip_file_list.GetColumn(3).SetWidth(col_width)  # Delete

    
    def on_delete_selected(self, event):
        """Delete selected symbols (and linked footprints + 3D models)."""
        from library_manager import PROJECT_SYMBOL_LIB, PROJECT_FOOTPRINT_LIB, PROJECT_3D_DIR
        from sexpdata import loads, dumps, Symbol
        import re, wx

        lst = self.symbol_list
        total = lst.GetCount()
        selected = [lst.GetString(i) for i in range(total) if lst.IsChecked(i)]

        if not selected:
            self.append_log("[WARN] No symbols selected for deletion.")
            return

        dlg = wx.MessageDialog(
            self,
            f"Delete {len(selected)} selected symbol(s) and their linked assets?",
            "Confirm Delete",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        )
        if dlg.ShowModal() != wx.ID_YES:
            dlg.Destroy()
            return
        dlg.Destroy()

        deleted_syms = deleted_fp = deleted_3d = 0
        linked_footprints = set()

        try:
            with open(PROJECT_SYMBOL_LIB, "r", encoding="utf-8") as f:
                sym_data = loads(f.read())
        except Exception as e:
            self.append_log(f"[ERROR] Failed to parse symbol library: {e}")
            return

        new_sym_data = [sym_data[0]]

        # Remove selected symbols and collect linked footprints
        for el in sym_data[1:]:
            if not (isinstance(el, list) and len(el) > 1 and str(el[0]) == "symbol"):
                new_sym_data.append(el)
                continue

            sym_name = str(el[1])
            if sym_name in selected:
                for item in el:
                    if (
                        isinstance(item, list)
                        and len(item) >= 3
                        and str(item[0]) == "property"
                        and str(item[1]) == "Footprint"
                    ):
                        fp_name = str(item[2]).split(":")[-1]
                        linked_footprints.add(fp_name)
                deleted_syms += 1
                continue
            new_sym_data.append(el)

        # Save updated symbol library
        if deleted_syms:
            try:
                with open(PROJECT_SYMBOL_LIB, "w", encoding="utf-8") as f:
                    f.write(dumps(new_sym_data, pretty_print=True))
                self.append_log(f"[OK] Deleted {deleted_syms} symbol(s) from project library.")
            except Exception as e:
                self.append_log(f"[ERROR] Failed to update symbol lib: {e}")

        # Delete linked footprints and 3D models
        for fp_name in linked_footprints:
            fp_path = PROJECT_FOOTPRINT_LIB / f"{fp_name}.kicad_mod"
            if fp_path.exists():
                try:
                    fp_path.unlink()
                    deleted_fp += 1
                    self.append_log(f"[OK] Deleted footprint: {fp_path.name}")
                except Exception as e:
                    self.append_log(f"[ERROR] Could not delete {fp_path.name}: {e}")

            stp_path = PROJECT_3D_DIR / f"{fp_name}.stp"
            if stp_path.exists():
                try:
                    stp_path.unlink()
                    deleted_3d += 1
                    self.append_log(f"[OK] Deleted 3D model: {stp_path.name}")
                except Exception as e:
                    self.append_log(f"[ERROR] Could not delete {stp_path.name}: {e}")

        self.append_log(
            f"[INFO] Deleted {deleted_syms} symbols, {deleted_fp} footprints, {deleted_3d} 3D models."
        )

        # --- refresh export list (UI + backend sync) ---
        def _refresh_ui():
            try:
                self.shim.refresh_symbol_list()  # <---- key fix: this is the wx-side repopulation
                self.append_log("[OK] Export list refreshed.")
            except Exception as e:
                self.append_log(f"[WARN] Could not refresh symbol list: {e}")

        wx.CallAfter(_refresh_ui)


    
    def on_zip_delete_clicked(self, event):
        """Delete the ZIP file when clicking the Delete column."""
        item = event.GetItem()
        if not item.IsOk():
            return

        model = self.zip_file_list.GetStore()
        row = model.GetRow(item)
        if row < 0:
            return

        col = event.GetColumn()
        # Delete column index: 3 (0=checkbox,1=name,2=status,3=delete)
        if col != 3:
            return

        from gui_core import GUI_FILE_DATA, refresh_file_list
        from library_manager import INPUT_ZIP_FOLDER

        if row >= len(GUI_FILE_DATA):
            return
        path = GUI_FILE_DATA[row]["path"]
        zip_path = Path(path)

        # Confirm deletion
        dlg = wx.MessageDialog(
            self,
            f"Delete '{zip_path.name}' from the input folder?",
            "Confirm Delete",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        )
        if dlg.ShowModal() != wx.ID_YES:
            dlg.Destroy()
            return
        dlg.Destroy()

        try:
            zip_path.unlink(missing_ok=True)
            self.append_log(f"[OK] Deleted {zip_path.name}")
        except Exception as e:
            self.append_log(f"[ERROR] Could not delete {zip_path.name}: {e}")
            return

        # Refresh list immediately
        refresh_file_list(self.shim)

    
    def on_zip_row_clicked(self, event):
        """Toggle checkbox immediately when clicking any cell in the row."""
        selections = self.zip_file_list.GetSelections()
        if not selections:
            return
        model = self.zip_file_list.GetStore()
        item = selections[0]
        row = model.GetRow(item)
        if row < 0:
            return

        # toggle value
        current = model.GetValueByRow(row, 0)
        model.SetValueByRow(not current, row, 0)
        self.zip_file_list.Refresh()  # force visual update immediately

        # update master checkbox status
        self.on_zip_checkbox_changed(None)
    
    def on_use_symbol_name_toggled(self, event):
        """Save preference to backend config."""
        from gui_core import save_config, USE_SYMBOLNAME_KEY
        value = self.chk_use_symbol_name.IsChecked()
        save_config(USE_SYMBOLNAME_KEY, value)
        state = "enabled" if value else "disabled"
        self.append_log(f"[INFO] 'Use symbol name as footprint/3D model' {state}.")
        
    def open_url(self, url):
        import webbrowser
        webbrowser.open_new_tab(url)

    def on_master_zip_toggle(self, event):
        """Select or deselect all checkboxes in the DataView list."""
        checked = self.chk_master_zip.IsChecked()
        model = self.zip_file_list.GetStore()
        total = model.GetCount()

        # Update all checkbox values
        for row in range(total):
            model.SetValueByRow(checked, row, 0)

        # Force the view to repaint (Windows quirk)
        self.zip_file_list.Refresh()

        # Update label
        self.chk_master_zip.SetLabel("Deselect All" if checked else "Select All")
        event.Skip()


    def on_zip_checkbox_changed(self, event):
        model = self.zip_file_list.GetStore()
        total = model.GetCount()
        checked = sum(1 for row in range(total) if model.GetValueByRow(row, 0))
        all_checked = checked == total and total > 0
        self.chk_master_zip.SetValue(all_checked)
        self.chk_master_zip.SetLabel("Deselect All" if all_checked else "Select All")
        if event:
            event.Skip()


    def on_symbol_item_toggled(self, event):
        lst = self.symbol_list
        total = lst.GetCount()
        checked = sum(1 for i in range(total) if lst.IsChecked(i))
        all_checked = checked == total and total > 0
        self.chk_master_symbols.SetValue(all_checked)
        self.chk_master_symbols.SetLabel("Deselect All" if all_checked else "Select All")
        if event:
            event.Skip()

    def on_refresh_symbols(self, event):
        self.append_log("[INFO] Refreshing symbols...")
        try:
            from gui_core import refresh_symbol_list
            refresh_symbol_list(self.shim)
            self.append_log("Symbol list refreshed.")
        except Exception as e:
            self.append_log(f"[ERROR] Failed to refresh symbols: {e}")

    def on_refresh_zips(self, event):
        refresh_file_list(self.shim)

    def on_master_symbols_toggle(self, event):
        checked = self.chk_master_symbols.IsChecked()
        lst = self.symbol_list
        for i in range(lst.GetCount()):
            lst.Check(i, checked)
        self.chk_master_symbols.SetLabel("Deselect All" if checked else "Select All")
        if event:
            event.Skip()

    def set_value(self, tag, value):
        if tag == "use_symbol_name_chkbox":
            self.chk_use_symbol_name.SetValue(bool(value))
        if tag == "current_path_text":
            self.current_folder_txt.SetLabel(value)
        self._values[tag] = value

    def append_log(self, text):
        if "[FAIL]" in text or "[ERROR]" in text:
            self.log_ctrl.SetDefaultStyle(wx.TextAttr(wx.RED))
        elif "[OK]" in text or "[SUCCESS]" in text:
            self.log_ctrl.SetDefaultStyle(wx.TextAttr(wx.GREEN))
        elif "[WARN]" in text:
            self.log_ctrl.SetDefaultStyle(wx.TextAttr(wx.Colour(255, 165, 0)))
        else:
            self.log_ctrl.SetDefaultStyle(wx.TextAttr(wx.WHITE))
        self.log_ctrl.AppendText(text + "\n")
        self.log_ctrl.SetDefaultStyle(wx.TextAttr(wx.WHITE))

    def clear_log(self):
        self.log_ctrl.Clear()

    def show_section(self, tag, visible: bool):
        pass

    def on_select_zip_folder(self, event):
        show_native_folder_dialog(self.shim)

    def on_open_folder(self, event):
        open_folder_in_explorer(self.shim)

    def on_process(self, event):
        process_action(self.shim, None, None, False)

    def on_purge(self, event):
        process_action(self.shim, None, None, True)

    def on_export(self, event):
        export_action(self.shim)

    def on_open_output(self, event):
        open_output_folder(self.shim)

    def on_drc_update(self, event):
        update_drc_rules(self.shim)

    def on_tab_changed(self, event):
        """Automatically refresh the correct list when switching tabs."""
        new_sel = self.notebook.GetSelection()
        if hasattr(self, "_last_tab") and new_sel == self._last_tab:
            event.Skip()
            return
        self._last_tab = new_sel
        sel = self.notebook.GetSelection()
        tab_label = self.notebook.GetPageText(sel)

        if "Import ZIP" in tab_label:
            self.append_log("[INFO] Refreshing ZIP archive list...")
            try:
                refresh_file_list(self.shim)
                self.append_log("[OK] ZIP archive list refreshed.")
            except Exception as e:
                self.append_log(f"[ERROR] Failed to refresh ZIP list: {e}")

        elif "Export Project" in tab_label:
            self.append_log("[INFO] Refreshing project symbol list...")
            try:
                from gui_core import refresh_symbol_list
                refresh_symbol_list(self.shim)
                self.append_log("[OK] Project symbol list refreshed.")
            except Exception as e:
                self.append_log(f"[ERROR] Failed to refresh symbol list: {e}")

        elif "DRC" in tab_label:
            self.append_log("[INFO] DRC Manager ready.")

        # Keep original backend behavior
        on_tab_change(self.shim)
        event.Skip()


# --- Patch backend ZIP selection handling for DataViewListCtrl ---
import gui_core


def _wx_get_active_files_for_processing(dpg):
    """Return checked ZIP paths from DataViewListCtrl that are valid for import."""
    try:
        from gui_core import GUI_FILE_DATA
    except Exception:
        return []

    gui = getattr(dpg, "gui", None)
    if not gui or not hasattr(gui, "zip_file_list"):
        return []

    model = gui.zip_file_list.GetStore()
    selected_paths = []
    for i in range(model.GetCount()):
        checked = model.GetValueByRow(i, 0)
        if not checked or i >= len(GUI_FILE_DATA):
            continue

        entry = GUI_FILE_DATA[i]
        path = entry.get("path")
        status = entry.get("status", "")
        if status in ("MISSING_SYMBOL", "MISSING_FOOTPRINT"):
            gui.append_log(f"[WARN] Skipping {Path(path).name}: missing required files.")
            continue

        if path:
            selected_paths.append(path)

    if not selected_paths:
        gui.append_log("[WARN] No valid ZIPs selected for import (must contain both symbol + footprint).")

    return selected_paths


gui_core.get_active_files_for_processing = _wx_get_active_files_for_processing

def _wx_collect_selected_symbols_for_export(dpg):
    """Return checked symbol names using wx.CheckListBox selections."""
    try:
        from gui_core import list_project_symbols
        symbols = list_project_symbols()
    except Exception:
        symbols = []
    gui = getattr(dpg, "gui", None)
    if not gui or not hasattr(gui, "symbol_list"):
        return []
    lst = gui.symbol_list
    checked = [i for i in range(lst.GetCount()) if lst.IsChecked(i)]
    return [symbols[i] for i in checked if i < len(symbols)]

gui_core.collect_selected_symbols_for_export = _wx_collect_selected_symbols_for_export

# ===============================
# wx.App entry
# ===============================
class KiCadApp(wx.App):
    def OnInit(self):
        self.frame = MainFrame()
        self.frame.Show()
        return True

if __name__ == "__main__":
    app = KiCadApp(False)
    app.MainLoop()
