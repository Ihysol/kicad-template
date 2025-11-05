# gui_wx.py
import wx
import uuid
from pathlib import Path
from gui_core import (
    APP_VERSION,
    load_config,
    save_config,
    process_action,
    refresh_file_list,
    update_drc_rules,
    export_action,
    clear_log,
    show_full_log_popup,
    open_output_folder,
    open_folder_in_explorer,
    show_native_folder_dialog,
    log_message,
)

# -------------------------------------------------------------------------
# DearPyGui compatibility shim for gui_core
# -------------------------------------------------------------------------
class DPGCompatMixin:
    """Shim to emulate minimal DearPyGui API for gui_core compatibility."""

    def __init__(self):
        # tag -> wx control mapping
        self.widgets: dict[str, wx.Window] = {}

    # ----- DearPyGui-like helpers -----
    def does_item_exist(self, tag):
        return tag in self.widgets

    def get_value(self, tag):
        w = self.widgets.get(tag)
        if isinstance(w, wx.TextCtrl):
            return w.GetValue()
        elif isinstance(w, wx.CheckBox):
            return w.GetValue()
        elif isinstance(w, wx.StaticText):
            return w.GetLabel()
        return None

    def set_value(self, tag, value):
        w = self.widgets.get(tag)
        if isinstance(w, wx.TextCtrl):
            w.SetValue(str(value))
        elif isinstance(w, wx.StaticText):
            w.SetLabel(str(value))
        elif isinstance(w, wx.CheckBox):
            w.SetValue(bool(value))

    def add_text(self, text, parent=None, tag=None):
        ctrl = wx.StaticText(parent or self, label=text)
        if tag:
            self.widgets[tag] = ctrl
        return ctrl

    def delete_item(self, tag, children_only=False):
        if tag in self.widgets:
            w = self.widgets[tag]
            if isinstance(w, wx.Window):
                w.Destroy()
            del self.widgets[tag]

    def add_input_text(self, default_value="", parent=None, readonly=False, width=-1, tag=None):
        """Redirect log lines from gui_core.log_message() into wx.TextCtrl."""
        log_box = self.widgets.get("log_text_container")
        if isinstance(log_box, wx.TextCtrl):
            log_box.AppendText(default_value + "\n")
        if tag:
            self.widgets[tag] = log_box
        return log_box

    def generate_uuid(self):
        return str(uuid.uuid4())

    def bind_item_theme(self, *args, **kwargs):
        """No-op for theme binding compatibility."""
        pass


# -------------------------------------------------------------------------
# Main GUI
# -------------------------------------------------------------------------
class KiCadLibraryManagerFrame(wx.Frame, DPGCompatMixin):
    def __init__(self, parent=None, title="KiCad Library Manager"):
        # initialize both parents explicitly
        wx.Frame.__init__(self, parent, title=title, size=(900, 750))
        DPGCompatMixin.__init__(self)

        self.config = load_config()
        self.panel = wx.Panel(self)
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)

        # build UI
        self._build_header()
        self._build_tabs()
        self._build_action_buttons()
        self._build_log_area()
        self._build_footer()

        self.panel.SetSizer(self.main_sizer)
        self.Centre()
        self.Show()

        # First log message
        log_message(self, None, None, "Ready.", add_timestamp=True)

        # Auto-load default folder
        from gui_core import INPUT_ZIP_FOLDER, initial_load
        self.set_value("current_path_text", f"Current Folder: {INPUT_ZIP_FOLDER.resolve()}")
        initial_load(self)

    # ------------------------------------------------------------------
    # Header section
    # ------------------------------------------------------------------
    def _build_header(self):
        label = wx.StaticText(
            self.panel,
            label="1. Select Archive Folder (ZIPs will be scanned automatically):",
        )
        label.SetForegroundColour(wx.Colour(0, 70, 160))
        self.main_sizer.Add(label, 0, wx.ALL, 5)

        row = wx.BoxSizer(wx.HORIZONTAL)
        select_btn = wx.Button(self.panel, label="Select ZIP-Folder")
        open_btn = wx.Button(self.panel, label="Open ZIP-Folder")
        row.Add(select_btn, 0, wx.RIGHT, 5)
        row.Add(open_btn, 0)
        self.main_sizer.Add(row, 0, wx.ALL, 5)

        self.current_path = wx.StaticText(
            self.panel, label="Current Folder: (Initializing...)"
        )
        self.current_path.SetForegroundColour(wx.Colour(0, 70, 160))
        self.main_sizer.Add(self.current_path, 0, wx.ALL, 5)
        self.widgets["current_path_text"] = self.current_path

        select_btn.Bind(wx.EVT_BUTTON, self.on_select_zip_folder)
        open_btn.Bind(wx.EVT_BUTTON, self.on_open_zip_folder)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------
    def _build_tabs(self):
        self.notebook = wx.Notebook(self.panel)
        self.tab_import = wx.Panel(self.notebook)
        self.tab_symbols = wx.Panel(self.notebook)
        self.tab_drc = wx.Panel(self.notebook)

        self._build_import_tab(self.tab_import)
        self._build_symbols_tab(self.tab_symbols)
        self._build_drc_tab(self.tab_drc)

        self.notebook.AddPage(self.tab_import, "Import ZIP Archives")
        self.notebook.AddPage(self.tab_symbols, "Export Project Symbols")
        self.notebook.AddPage(self.tab_drc, "DRC Manager")

        self.main_sizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)

    def _build_import_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)
        header = wx.BoxSizer(wx.HORIZONTAL)
        self.file_count = wx.StaticText(panel, label="Total ZIP files found: 0")
        self.file_count.SetForegroundColour(wx.Colour(0, 130, 0))
        header.Add(self.file_count, 0, wx.RIGHT, 5)
        self.widgets["file_count_text"] = self.file_count

        refresh_btn = wx.Button(panel, label="Refresh ZIPs")
        header.Add(refresh_btn, 0)
        sizer.Add(header, 0, wx.ALL, 5)

        self.zip_list = wx.ScrolledWindow(panel, size=(-1, 180))
        self.zip_list.SetScrollRate(5, 5)
        self.zip_sizer = wx.BoxSizer(wx.VERTICAL)
        self.zip_list.SetSizer(self.zip_sizer)
        sizer.Add(self.zip_list, 1, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(sizer)

        refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh_zips)

    def _build_symbols_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)
        header = wx.BoxSizer(wx.HORIZONTAL)
        self.symbol_count = wx.StaticText(panel, label="Total symbols found: 0")
        self.symbol_count.SetForegroundColour(wx.Colour(0, 130, 0))
        header.Add(self.symbol_count, 0, wx.RIGHT, 5)
        self.widgets["symbol_count_text"] = self.symbol_count

        refresh_btn = wx.Button(panel, label="Refresh Symbols")
        header.Add(refresh_btn, 0)
        sizer.Add(header, 0, wx.ALL, 5)

        self.symbol_list = wx.ScrolledWindow(panel, size=(-1, 180))
        self.symbol_list.SetScrollRate(5, 5)
        self.symbol_sizer = wx.BoxSizer(wx.VERTICAL)
        self.symbol_list.SetSizer(self.symbol_sizer)
        sizer.Add(self.symbol_list, 1, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(sizer)

        refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh_symbols)

    def _build_drc_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)
        txt = wx.StaticText(
            panel, label="Auto-Apply DRC Rules Based on PCB Layer Count"
        )
        txt.SetForegroundColour(wx.Colour(200, 100, 0))
        sizer.Add(txt, 0, wx.ALL, 5)
        btn = wx.Button(panel, label="Update DRC Rules", size=(220, -1))
        sizer.Add(btn, 0, wx.ALL, 5)
        panel.SetSizer(sizer)
        btn.Bind(wx.EVT_BUTTON, self.on_update_drc)

    # ------------------------------------------------------------------
    # Action buttons
    # ------------------------------------------------------------------
    def _build_action_buttons(self):
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.process_btn = wx.Button(self.panel, label="PROCESS / IMPORT")
        self.purge_btn = wx.Button(self.panel, label="PURGE / DELETE")
        row.Add(self.process_btn, 0, wx.RIGHT, 10)
        row.Add(self.purge_btn, 0, wx.RIGHT, 10)
        self.main_sizer.Add(row, 0, wx.ALL, 5)

        self.process_btn.Bind(wx.EVT_BUTTON, self.on_process)
        self.purge_btn.Bind(wx.EVT_BUTTON, self.on_purge)

    # ------------------------------------------------------------------
    # Log Area
    # ------------------------------------------------------------------
    def _build_log_area(self):
        lbl = wx.StaticText(self.panel, label="CLI Output Log:")
        self.main_sizer.Add(lbl, 0, wx.ALL, 5)
        self.log_box = wx.TextCtrl(
            self.panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
            size=(-1, 150),
        )
        self.main_sizer.Add(self.log_box, 1, wx.EXPAND | wx.ALL, 5)
        self.widgets["log_text_container"] = self.log_box

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------
    def _build_footer(self):
        footer = wx.BoxSizer(wx.HORIZONTAL)
        author = wx.StaticText(self.panel, label="By: Ihysol (Tobias Gent)")
        author.SetForegroundColour(wx.Colour(30, 30, 60))
        footer.Add(author, 0, wx.RIGHT, 10)
        version = wx.StaticText(self.panel, label=f"Version: {APP_VERSION}")
        footer.Add(version, 0)
        self.main_sizer.Add(footer, 0, wx.ALL, 5)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def on_select_zip_folder(self, event):
        show_native_folder_dialog(self, event, None)

    def on_open_zip_folder(self, event):
        open_folder_in_explorer(self, event, None)

    def on_update_drc(self, event):
        update_drc_rules(self, event, None)

    def on_refresh_zips(self, event):
        refresh_file_list(self, event, None)

    def on_refresh_symbols(self, event):
        log_message(self, None, None, "Refreshing symbols list...", add_timestamp=True)

    def on_process(self, event):
        process_action(self, event, None, False)

    def on_purge(self, event):
        process_action(self, event, None, True)


# -------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------
def run_gui():
    app = wx.App(False)
    frame = KiCadLibraryManagerFrame()
    app.MainLoop()


if __name__ == "__main__":
    run_gui()
