import wx
from pathlib import Path
import threading
from gui_core import (
    APP_VERSION,
    load_config,
    save_config,
    clear_log,
    log_message,
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
)


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
        val = self._values.get(tag)
        if val is None:
            return 0
        return val

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

    def bind_item_theme(self, *args, **kwargs):
        pass

    def set_y_scroll(self, *args, **kwargs):
        pass

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
    def get_item_label(self, tag): return str(tag)
    def get_item_children(self, tag, slot): return []
    def get_item_type(self, tag): return "mvAppItemType::mvCheckbox"
    def set_item_label(self, tag, label): pass
    
    def delete_item(self, tag, children_only=False):
        """Handle DearPyGui's delete_item() calls."""
        # Clear the wx log if it's the log container
        if tag == "log_text_container":
            self.gui.clear_log()
        # Clear ZIP file list when the backend rebuilds it
        elif tag == "file_checkboxes_container":
            if hasattr(self.gui, "zip_file_list"):
                self.gui.zip_file_list.Clear()
        # Clear symbol list when refreshing symbols
        elif tag == "symbol_checkboxes_container":
            if hasattr(self.gui, "symbol_list"):
                self.gui.symbol_list.Clear()
        # Otherwise, just ignore silently


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

    # ---------- Layout ----------
    def InitUI(self):
        panel = wx.Panel(self)
        self.panel = panel
        vbox = wx.BoxSizer(wx.VERTICAL)

        # --- Folder selection section ---
        box1 = wx.StaticBox(panel, label="1. Select Archive Folder")
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
        self.zip_file_list = wx.ListBox(self.tab_zip)
        self.btn_process = wx.Button(self.tab_zip, label="PROCESS / IMPORT")
        self.btn_purge = wx.Button(self.tab_zip, label="PURGE / DELETE")
        h_zip_btns = wx.BoxSizer(wx.HORIZONTAL)
        h_zip_btns.Add(self.btn_process, 0, wx.RIGHT, 8)
        h_zip_btns.Add(self.btn_purge, 0)
        self.zip_vbox.Add(wx.StaticText(self.tab_zip, label="ZIP Archives:"), 0, wx.BOTTOM, 5)
        self.zip_vbox.Add(self.zip_file_list, 1, wx.EXPAND | wx.BOTTOM, 5)
        self.zip_vbox.Add(h_zip_btns, 0, wx.TOP, 5)
        self.tab_zip.SetSizer(self.zip_vbox)

        # --- Symbol tab content ---
        self.sym_vbox = wx.BoxSizer(wx.VERTICAL)
        self.symbol_list = wx.ListBox(self.tab_symbol)
        self.btn_export = wx.Button(self.tab_symbol, label="EXPORT SELECTED")
        self.btn_open_output = wx.Button(self.tab_symbol, label="OPEN OUTPUT FOLDER")
        h_sym_btns = wx.BoxSizer(wx.HORIZONTAL)
        h_sym_btns.Add(self.btn_export, 0, wx.RIGHT, 8)
        h_sym_btns.Add(self.btn_open_output, 0)
        self.sym_vbox.Add(wx.StaticText(self.tab_symbol, label="Project Symbols:"), 0, wx.BOTTOM, 5)
        self.sym_vbox.Add(self.symbol_list, 1, wx.EXPAND | wx.BOTTOM, 5)
        self.sym_vbox.Add(h_sym_btns, 0, wx.TOP, 5)
        self.tab_symbol.SetSizer(self.sym_vbox)

        # --- DRC tab content ---
        self.drc_vbox = wx.BoxSizer(wx.VERTICAL)
        self.btn_drc = wx.Button(self.tab_drc, label="Update DRC Rules")
        self.drc_vbox.Add(wx.StaticText(self.tab_drc, label="Auto-Apply DRC Rules Based on PCB Layer Count:"), 0, wx.BOTTOM, 5)
        self.drc_vbox.Add(self.btn_drc, 0, wx.BOTTOM, 5)
        self.tab_drc.SetSizer(self.drc_vbox)

        # --- Progress + log ---
        self.gauge = wx.Gauge(panel, range=100, size=(-1, 22))
        self.log_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        vbox.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        vbox.Add(self.log_ctrl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        panel.SetSizer(vbox)

        # --- Bind events ---
        self.Bind(wx.EVT_BUTTON, self.on_select_zip_folder, self.btn_select)
        self.Bind(wx.EVT_BUTTON, self.on_open_folder, self.btn_open)
        self.Bind(wx.EVT_BUTTON, self.on_process, self.btn_process)
        self.Bind(wx.EVT_BUTTON, self.on_purge, self.btn_purge)
        self.Bind(wx.EVT_BUTTON, self.on_export, self.btn_export)
        self.Bind(wx.EVT_BUTTON, self.on_open_output, self.btn_open_output)
        self.Bind(wx.EVT_BUTTON, self.on_drc_update, self.btn_drc)
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_tab_changed)

    # ---------- Backend connection ----------
    def set_value(self, tag, value):
        if tag == "current_path_text":
            self.current_folder_txt.SetLabel(value)
        self._values[tag] = value

    def get_value(self, tag):
        if tag == "current_path_text":
            return self.current_folder_txt.GetLabel()
        return self._values.get(tag)

    def has_item(self, tag):
        return True

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
        """Optional: control visibility for grouped elements (not needed yet)."""
        pass

    # ---------- Event handlers ----------
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
        on_tab_change(self.shim)
        event.Skip()


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
