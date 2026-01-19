import csv
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import wx
import wx.dataview as dv
from sexpdata import Symbol, loads

from gui_core import (
    APP_VERSION,
    USE_SYMBOLNAME_KEY,
    SHOW_LOG_KEY,
    export_symbols_with_checks,
    list_project_symbols,
    load_config,
    open_folder_in_explorer,
    open_output_folder,
    process_archives,
    save_config,
    scan_zip_folder,
    update_drc_rules,
)
from library_manager import INPUT_ZIP_FOLDER, PROJECT_DIR

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


# --- Shared icon helper for buttons ---
ICON_SIZE = (16, 16)
def set_button_icon(btn: wx.Button, art_id, position=wx.RIGHT):
    """Attach a standard wx art bitmap to a button when available."""
    bmp = wx.ArtProvider.GetBitmap(art_id, wx.ART_BUTTON, ICON_SIZE)
    if bmp.IsOk():
        btn.SetBitmap(bmp, position)


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

        dropped = []
        target_dir = self.parent.current_folder
        target_dir.mkdir(parents=True, exist_ok=True)

        for f in filenames:
            p = Path(f)
            if p.is_file() and p.suffix.lower() == ".zip":
                target = target_dir / p.name
                try:
                    if target != p:
                        shutil.copy2(p, target)
                    dropped.append(target)
                except Exception as e:
                    wx.CallAfter(self.parent.append_log, f"[ERROR] Failed to copy {p.name}: {e}")

        if dropped:
            wx.CallAfter(self.parent.append_log, f"[OK] Added {len(dropped)} ZIP archive(s).")
            wx.CallAfter(self.parent.refresh_zip_list)

        return True


# ===============================
# board preview panel (image + crop overlay)
# ===============================
class BoardPreviewPanel(wx.Panel):
    """Panel that draws a scaled bitmap and interactive crop rectangle overlay."""
    def __init__(self, parent, on_crop_change=None, on_select=None):
        super().__init__(parent, style=wx.BORDER_SIMPLE)
        self._bmp = None
        self._scaled = None
        self._last_size = wx.Size(0, 0)
        self._crop = (0, 100, 0, 100)  # x0, x1, y0, y1 (percent)
        self._image_rect = None  # wx.Rect of scaled bitmap in panel coords
        self._drag_mode = None  # "move" or edges like "l","r","t","b","lt","rb",...
        self._drag_anchor = None  # (x,y) at drag start
        self._crop_at_drag = None
        self._on_crop_change = on_crop_change
        self._on_select = on_select
        self._loading = False
        self._loading_text = "Rendering..."
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE, self._on_resize)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)
        self.Bind(wx.EVT_LEFT_UP, self._on_left_up)
        self.Bind(wx.EVT_MOTION, self._on_mouse_move)

    def set_bitmap(self, bmp: wx.Bitmap | None):
        self._bmp = bmp if (bmp and bmp.IsOk()) else None
        self._scaled = None
        self.Refresh()

    def set_crop(self, crop: tuple[int, int, int, int]):
        self._crop = crop
        self.Refresh()

    def get_crop(self) -> tuple[int, int, int, int]:
        return self._crop

    def set_loading(self, loading: bool, text: str | None = None):
        self._loading = loading
        if text:
            self._loading_text = text
        self.Refresh()

    def _on_resize(self, event):
        event.Skip()
        self._scaled = None
        self.Refresh()

    def _get_scaled_bitmap(self) -> wx.Bitmap | None:
        if not self._bmp:
            return None
        size = self.GetClientSize()
        if size.width < 10 or size.height < 10:
            return None
        if self._scaled and self._last_size == size:
            return self._scaled
        img = self._bmp.ConvertToImage()
        w, h = img.GetWidth(), img.GetHeight()
        scale = min(size.width / w, size.height / h)
        new_w = max(int(w * scale), 1)
        new_h = max(int(h * scale), 1)
        img = img.Scale(new_w, new_h, wx.IMAGE_QUALITY_HIGH)
        self._scaled = wx.Bitmap(img)
        self._last_size = size
        return self._scaled

    def _on_paint(self, event):
        dc = wx.PaintDC(self)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        bmp = self._get_scaled_bitmap()
        if bmp:
            panel_size = self.GetClientSize()
            x = (panel_size.width - bmp.GetWidth()) // 2
            y = (panel_size.height - bmp.GetHeight()) // 2
            self._image_rect = wx.Rect(x, y, bmp.GetWidth(), bmp.GetHeight())
            dc.DrawBitmap(bmp, x, y, True)

            # draw crop rectangle
            x0, x1, y0, y1 = self._crop
            rect_x0 = x + int(bmp.GetWidth() * x0 / 100)
            rect_x1 = x + int(bmp.GetWidth() * x1 / 100)
            rect_y0 = y + int(bmp.GetHeight() * y0 / 100)
            rect_y1 = y + int(bmp.GetHeight() * y1 / 100)
            rect_w = max(rect_x1 - rect_x0, 1)
            rect_h = max(rect_y1 - rect_y0, 1)
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.SetPen(wx.Pen(wx.Colour(220, 50, 50), 2))
            dc.DrawRectangle(rect_x0, rect_y0, rect_w, rect_h)

        if self._loading:
            panel_size = self.GetClientSize()
            dc.SetBrush(wx.Brush(wx.Colour(0, 0, 0, 80)))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(0, 0, panel_size.width, panel_size.height)
            dc.SetTextForeground(wx.Colour(255, 255, 255))
            dc.DrawLabel(self._loading_text, wx.Rect(0, 0, panel_size.width, panel_size.height), alignment=wx.ALIGN_CENTER)

    def _on_left_down(self, event):
        if not self._image_rect:
            return
        pos = event.GetPosition()
        if self._on_select:
            self._on_select()
        if not self._image_rect.Contains(pos):
            return
        self._drag_mode = self._hit_test(pos)
        if not self._drag_mode:
            return
        self._drag_anchor = (pos.x, pos.y)
        self._crop_at_drag = self._crop
        self.CaptureMouse()

    def _on_left_up(self, event):
        if self.HasCapture():
            self.ReleaseMouse()
        self._drag_mode = None
        self._drag_anchor = None
        self._crop_at_drag = None

    def _on_mouse_move(self, event):
        if not self._drag_mode or not self._drag_anchor or not self._crop_at_drag:
            return
        if not self._image_rect:
            return
        pos = event.GetPosition()
        new_crop = self._compute_crop_from_drag(pos)
        if new_crop and self._on_crop_change:
            self._on_crop_change(new_crop)

    def _hit_test(self, pos) -> str | None:
        rect = self._image_rect
        if not rect:
            return None
        x0, x1, y0, y1 = self._crop
        left = rect.x + int(rect.width * x0 / 100)
        right = rect.x + int(rect.width * x1 / 100)
        top = rect.y + int(rect.height * y0 / 100)
        bottom = rect.y + int(rect.height * y1 / 100)
        margin = 6

        near_left = abs(pos.x - left) <= margin
        near_right = abs(pos.x - right) <= margin
        near_top = abs(pos.y - top) <= margin
        near_bottom = abs(pos.y - bottom) <= margin

        if near_left and near_top:
            return "lt"
        if near_right and near_top:
            return "rt"
        if near_left and near_bottom:
            return "lb"
        if near_right and near_bottom:
            return "rb"
        if near_left:
            return "l"
        if near_right:
            return "r"
        if near_top:
            return "t"
        if near_bottom:
            return "b"

        if left < pos.x < right and top < pos.y < bottom:
            return "move"
        return None

    def _compute_crop_from_drag(self, pos) -> tuple[int, int, int, int] | None:
        rect = self._image_rect
        if not rect:
            return None
        x0, x1, y0, y1 = self._crop_at_drag
        min_size = 2  # percent

        def clamp(v, lo=0, hi=100):
            return max(min(v, hi), lo)

        # convert mouse to percent
        px = clamp(int((pos.x - rect.x) * 100 / rect.width))
        py = clamp(int((pos.y - rect.y) * 100 / rect.height))

        mode = self._drag_mode
        if mode == "move":
            dx = int((pos.x - self._drag_anchor[0]) * 100 / rect.width)
            dy = int((pos.y - self._drag_anchor[1]) * 100 / rect.height)
            nx0 = clamp(x0 + dx)
            nx1 = clamp(x1 + dx)
            ny0 = clamp(y0 + dy)
            ny1 = clamp(y1 + dy)
            # keep size
            if nx1 - nx0 < min_size:
                nx1 = nx0 + min_size
            if ny1 - ny0 < min_size:
                ny1 = ny0 + min_size
            if nx1 > 100:
                nx0 -= nx1 - 100
                nx1 = 100
            if ny1 > 100:
                ny0 -= ny1 - 100
                ny1 = 100
            if nx0 < 0:
                nx1 -= nx0
                nx0 = 0
            if ny0 < 0:
                ny1 -= ny0
                ny0 = 0
            return (nx0, nx1, ny0, ny1)

        nx0, nx1, ny0, ny1 = x0, x1, y0, y1
        if "l" in mode:
            nx0 = clamp(min(px, nx1 - min_size))
        if "r" in mode:
            nx1 = clamp(max(px, nx0 + min_size))
        if "t" in mode:
            ny0 = clamp(min(py, ny1 - min_size))
        if "b" in mode:
            ny1 = clamp(max(py, ny0 + min_size))
        return (nx0, nx1, ny0, ny1)




# ===============================
# Main GUI Frame
# ===============================
class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title=f"KiCad Library Manager (wxPython) - {APP_VERSION}", size=(1120, 800))
        self.current_folder = INPUT_ZIP_FOLDER.resolve()
        self.zip_rows = []
        self.InitUI()
        self._configure_logger()
        self.Centre()
        self.Show()

        # Kick off background load so the frame appears immediately
        threading.Thread(target=self._post_init_load, daemon=True).start()

    def _post_init_load(self):
        """Load config and initial data without blocking the UI thread."""
        cfg = load_config()
        try:
            zip_rows = scan_zip_folder(self.current_folder)
        except Exception as e:
            logger.error(f"Failed to scan ZIP folder: {e}")
            zip_rows = []
        try:
            symbols = list_project_symbols()
        except Exception as e:
            logger.error(f"Failed to load project symbols: {e}")
            symbols = []

        wx.CallAfter(self._apply_initial_data, cfg, zip_rows, symbols)

    def _apply_initial_data(self, cfg, zip_rows, symbols):
        self.chk_use_symbol_name.SetValue(cfg.get(USE_SYMBOLNAME_KEY, False))
        self.refresh_zip_list(rows=zip_rows)
        self.refresh_symbol_list(symbols=symbols)

    def _configure_logger(self):
        """Attach GUI log handler once."""
        if not any(isinstance(h, WxGuiLogHandler) for h in logger.handlers):
            gui_handler = WxGuiLogHandler(self)
            gui_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
            )
            logger.addHandler(gui_handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False  # prevent double-printing via root logger
    # ---------- Layout ----------
    def InitUI(self):
        panel = wx.Panel(self)
        self.panel = panel
        vbox = wx.BoxSizer(wx.VERTICAL)

        # --- Project opener ---
        self.project_file = self._find_project_file()
        proj_label = (
            f"Project: {self.project_file}"
            if self.project_file
            else "Project: not found"
        )
        proj_box = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_open_project = wx.Button(panel, label="Open KiCad Project in New Window")
        self.btn_open_project.Enable(bool(self.project_file))
        set_button_icon(self.btn_open_project, wx.ART_NORMAL_FILE)
        self.lbl_project = wx.StaticText(panel, label=proj_label)
        proj_box.Add(self.btn_open_project, 0, wx.RIGHT, 8)
        proj_box.Add(self.lbl_project, 0, wx.ALIGN_CENTER_VERTICAL)
        vbox.Add(proj_box, 0, wx.EXPAND | wx.ALL, 8)
        self.log_popup = None
        self.log_popup_ctrl = None

        # --- Tabs ---
        self.notebook = wx.Notebook(panel)
        self.tab_zip = wx.Panel(self.notebook)
        self.tab_symbol = wx.Panel(self.notebook)
        self.tab_drc = wx.Panel(self.notebook)
        self.tab_board = wx.Panel(self.notebook)
        self.tab_mouser = MouserAutoOrderTab(self.notebook, log_callback=self.append_log)
        self.notebook.AddPage(self.tab_zip, "Import ZIP Archives")
        self.notebook.AddPage(self.tab_symbol, "Export Project Symbols")
        self.notebook.AddPage(self.tab_drc, "DRC Manager")
        self.notebook.AddPage(self.tab_board, "Board Images")
        self.notebook.AddPage(self.tab_mouser, "Mouser Auto Order")
        vbox.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 8)

        # --- ZIP tab content ---
        self.zip_vbox = wx.BoxSizer(wx.VERTICAL)

        # === Folder selection (ZIP tab only) ===
        box1 = wx.StaticBox(self.tab_zip, label="Select Archive Folder")
        s1 = wx.StaticBoxSizer(box1, wx.VERTICAL)
        h_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_select = wx.Button(self.tab_zip, label="Select ZIP Folder")
        self.btn_open = wx.Button(self.tab_zip, label="Open ZIP Folder")
        set_button_icon(self.btn_select, wx.ART_NEW_DIR)
        set_button_icon(self.btn_open, wx.ART_FOLDER_OPEN)
        h_buttons.Add(self.btn_select, 0, wx.RIGHT, 8)
        h_buttons.Add(self.btn_open, 0)
        self.current_folder_txt = wx.StaticText(
            self.tab_zip, label="Current Folder: (Initializing...)"
        )
        s1.Add(h_buttons, 0, wx.BOTTOM, 5)
        s1.Add(self.current_folder_txt, 0, wx.TOP, 2)
        self.zip_vbox.Add(s1, 0, wx.EXPAND | wx.ALL, 8)

        # === Top controls ===
        h_zip_top = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refresh_zips = wx.Button(self.tab_zip, label="Refresh ZIPs")
        set_button_icon(self.btn_refresh_zips, wx.ART_REDO)
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
        set_button_icon(self.btn_process, wx.ART_GO_FORWARD)
        set_button_icon(self.btn_purge, wx.ART_DELETE)
        h_zip_btns = wx.BoxSizer(wx.HORIZONTAL)
        h_zip_btns.Add(self.btn_process, 0, wx.RIGHT, 8)
        h_zip_btns.Add(self.btn_purge, 0)
        self.zip_vbox.Add(h_zip_btns, 0, wx.TOP, 5)

        self.tab_zip.SetSizer(self.zip_vbox)

        # --- Symbol tab content ---
        self.sym_vbox = wx.BoxSizer(wx.VERTICAL)
        
        h_sym_top = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_refresh_symbols = wx.Button(self.tab_symbol, label="Refresh Symbols")
        set_button_icon(self.btn_refresh_symbols, wx.ART_REDO)
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
        set_button_icon(self.btn_export, wx.ART_FILE_SAVE_AS)
        set_button_icon(self.btn_open_output, wx.ART_FOLDER_OPEN)
        set_button_icon(self.btn_delete_orphans, wx.ART_DELETE)
        self.tab_symbol.Layout()
        self.btn_delete_orphans.SetForegroundColour(wx.RED)

        h_sym_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_delete_selected = wx.Button(self.tab_symbol, label="DELETE SELECTED")
        set_button_icon(self.btn_delete_selected, wx.ART_DELETE)
        self.btn_delete_selected.SetForegroundColour(wx.RED)

        h_sym_btns.Add(self.btn_export, 0, wx.RIGHT, 8)
        h_sym_btns.Add(self.btn_open_output, 0, wx.RIGHT, 8)
        h_sym_btns.Add(self.btn_delete_selected, 0)
        self.sym_vbox.Add(h_sym_btns, 0, wx.TOP, 5)
        self.tab_symbol.SetSizer(self.sym_vbox)


        # --- DRC tab content ---
        self.drc_vbox = wx.BoxSizer(wx.VERTICAL)
        self.btn_drc = wx.Button(self.tab_drc, label="Update DRC Rules")
        set_button_icon(self.btn_drc, wx.ART_TICK_MARK)
        self.drc_vbox.Add(wx.StaticText(self.tab_drc, label="Auto-Apply DRC Rules Based on PCB Layer Count:"), 0, wx.BOTTOM, 5)
        self.drc_vbox.Add(self.btn_drc, 0, wx.BOTTOM, 5)
        self.tab_drc.SetSizer(self.drc_vbox)

        # --- Board Images tab content ---
        self.board_vbox = wx.BoxSizer(wx.VERTICAL)

        self.board_controls = wx.BoxSizer(wx.HORIZONTAL)
        self.board_controls.Add(
            wx.StaticText(self.tab_board, label="Generate top/bottom board images from .kicad_pcb:"),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            12,
        )
        self.btn_generate_board = wx.Button(self.tab_board, label="Generate from .kicad_pcb")
        set_button_icon(self.btn_generate_board, wx.ART_EXECUTABLE_FILE)
        self.btn_render_custom = wx.Button(self.tab_board, label="Crop to Frame")
        set_button_icon(self.btn_render_custom, wx.ART_EXECUTABLE_FILE)
        self.btn_save_board = wx.Button(self.tab_board, label="Save Previews")
        set_button_icon(self.btn_save_board, wx.ART_FILE_SAVE_AS)
        self.board_controls.Add(self.btn_generate_board, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.board_controls.Add(self.btn_render_custom, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        self.board_controls.Add(self.btn_save_board, 0, wx.ALIGN_CENTER_VERTICAL)
        self.board_vbox.Add(self.board_controls, 0, wx.EXPAND | wx.ALL, 8)

        self.board_sizes = wx.FlexGridSizer(cols=4, vgap=4, hgap=8)
        self.board_sizes.AddGrowableCol(1, 0)
        self.board_sizes.AddGrowableCol(3, 0)

        self.board_sizes.Add(wx.StaticText(self.tab_board, label="Width"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_render_w = wx.TextCtrl(self.tab_board, value="2560", size=(70, -1))
        self.board_sizes.Add(self.txt_render_w, 0, wx.ALIGN_CENTER_VERTICAL)
        self.board_sizes.Add(wx.StaticText(self.tab_board, label="Height"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_render_h = wx.TextCtrl(self.tab_board, value="1440", size=(70, -1))
        self.board_sizes.Add(self.txt_render_h, 0, wx.ALIGN_CENTER_VERTICAL)
        self.board_vbox.Add(self.board_sizes, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.preset_radio = wx.RadioBox(
            self.tab_board,
            label="Resolution Preset",
            choices=["1080p", "2K", "4K", "8K"],
            majorDimension=4,
            style=wx.RA_SPECIFY_COLS,
        )
        self.preset_radio.SetSelection(1)  # 2K default
        self.board_vbox.Add(self.preset_radio, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.lbl_crop_help = wx.StaticText(
            self.tab_board,
            label="Tip: Drag the red box to move/resize the crop frame.",
        )
        self.lbl_crop_help.SetFont(wx.Font(wx.FontInfo().Bold().Italic()))
        self.lbl_crop_help.SetForegroundColour(wx.Colour(200, 80, 20))
        self.board_vbox.Add(self.lbl_crop_help, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.board_hbox = wx.BoxSizer(wx.HORIZONTAL)
        self.board_image_panel_top = BoardPreviewPanel(
            self.tab_board,
            on_crop_change=self._set_crop,
            on_select=lambda: self._set_active_preview("top"),
        )
        self.board_image_panel_bottom = BoardPreviewPanel(
            self.tab_board,
            on_crop_change=self._set_crop,
            on_select=lambda: self._set_active_preview("bottom"),
        )
        self.board_image_label_top = wx.StaticText(self.board_image_panel_top, label="")
        self.board_image_label_top.SetBackgroundColour(wx.Colour(240, 240, 240))
        self.board_image_label_top.SetForegroundColour(wx.Colour(60, 60, 60))
        self.board_image_label_bottom = wx.StaticText(self.board_image_panel_bottom, label="")
        self.board_image_label_bottom.SetBackgroundColour(wx.Colour(240, 240, 240))
        self.board_image_label_bottom.SetForegroundColour(wx.Colour(60, 60, 60))

        placeholder_top = self._make_placeholder_bitmap((520, 360), "Top image")
        placeholder_bottom = self._make_placeholder_bitmap((520, 360), "Bottom image")
        self.board_source_top = placeholder_top
        self.board_source_bottom = placeholder_bottom
        self._set_board_images(placeholder_top, placeholder_bottom)
        self._set_crop((0, 100, 0, 100))
        self.active_side = "top"
        self._set_active_preview("top")
        self.board_image_panel_top.Bind(wx.EVT_SIZE, self.on_board_image_resize)
        self.board_image_panel_bottom.Bind(wx.EVT_SIZE, self.on_board_image_resize)

        self.board_hbox.Add(self.board_image_panel_top, 1, wx.EXPAND | wx.ALL, 8)
        self.board_hbox.Add(self.board_image_panel_bottom, 1, wx.EXPAND | wx.ALL, 8)
        self.board_vbox.Add(self.board_hbox, 1, wx.EXPAND)
        self.tab_board.SetSizer(self.board_vbox)

        # --- Log output ---
        self.log_panel = wx.Panel(panel)
        log_box = wx.BoxSizer(wx.VERTICAL)
        log_header = wx.BoxSizer(wx.HORIZONTAL)
        log_header.Add(wx.StaticText(self.log_panel, label="Log:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.btn_toggle_log = wx.Button(self.log_panel, label="Open Log Window")
        set_button_icon(self.btn_toggle_log, wx.ART_LIST_VIEW)
        log_header.Add(self.btn_toggle_log, 0)
        log_box.Add(log_header, 0, wx.BOTTOM, 4)

        self.log_ctrl = wx.TextCtrl(self.log_panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        self.log_ctrl.SetMinSize((-1, 120))  # keep compact on main window
        log_box.Add(self.log_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.log_panel.SetSizer(log_box)
        vbox.Add(self.log_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)


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
        self.chk_show_log = wx.CheckBox(panel, label="Show log")
        self.lbl_version = wx.StaticText(panel, label=f"Version: {APP_VERSION}")
        footer_box.AddStretchSpacer(1)
        footer_box.Add(self.chk_show_log, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        footer_box.Add(self.lbl_version, 0, wx.ALIGN_CENTER_VERTICAL)
        vbox.Add(footer_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.SetSizer(vbox)
        # Apply persisted log visibility
        cfg = load_config()
        show_log = cfg.get(SHOW_LOG_KEY, True)
        self.chk_show_log.SetValue(show_log)
        self._apply_log_visibility(show_log, save_pref=False)

        # --- Bind events ---
        self.Bind(wx.EVT_BUTTON, lambda e: self.open_url("https://github.com/Ihysol"), self.btn_author)
        self.Bind(wx.EVT_BUTTON, lambda e: self.open_url("https://github.com/Ihysol/kicad-template"), self.btn_issues)
        self.Bind(wx.EVT_BUTTON, self.on_open_project, self.btn_open_project)
        self.Bind(wx.EVT_BUTTON, self.on_refresh_symbols, self.btn_refresh_symbols)
        self.Bind(wx.EVT_CHECKLISTBOX, self.on_symbol_item_toggled, self.symbol_list)
        self.Bind(wx.EVT_CHECKBOX, self.on_use_symbol_name_toggled, self.chk_use_symbol_name)
        self.Bind(wx.EVT_CHECKBOX, self.on_toggle_show_log, self.chk_show_log)
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
        self.Bind(wx.EVT_BUTTON, self.on_toggle_log, self.btn_toggle_log)
        self.Bind(wx.EVT_BUTTON, self.on_generate_board_images, self.btn_generate_board)
        self.Bind(wx.EVT_BUTTON, self.on_generate_custom_board_images, self.btn_render_custom)
        self.Bind(wx.EVT_BUTTON, self.on_save_board_images, self.btn_save_board)
        self.Bind(wx.EVT_RADIOBOX, self.on_preset_changed, self.preset_radio)

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
        wx.CallAfter(self.refresh_symbol_list)


    
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

        if row >= len(self.zip_rows):
            return
        zip_path = Path(self.zip_rows[row].get("path", ""))

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
        self.refresh_zip_list()

    
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
        value = self.chk_use_symbol_name.IsChecked()
        cfg = load_config()
        cfg[USE_SYMBOLNAME_KEY] = value
        save_config(cfg)
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
        self.refresh_symbol_list()

    def on_refresh_zips(self, event):
        self.refresh_zip_list()

    def on_master_symbols_toggle(self, event):
        checked = self.chk_master_symbols.IsChecked()
        lst = self.symbol_list
        for i in range(lst.GetCount()):
            lst.Check(i, checked)
        self.chk_master_symbols.SetLabel("Deselect All" if checked else "Select All")
        if event:
            event.Skip()

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
        if self.log_popup_ctrl:
            self.log_popup_ctrl.AppendText(text + "\n")

    def _log_ui(self, text: str):
        wx.CallAfter(self.append_log, text)

    def _set_board_busy(self, busy: bool):
        self._board_busy = busy
        self.btn_generate_board.Enable(not busy)
        self.btn_render_custom.Enable(not busy)
        self.btn_save_board.Enable(not busy)
        self.preset_radio.Enable(not busy)
        self.txt_render_w.Enable(not busy)
        self.txt_render_h.Enable(not busy)

    def _load_and_set_board_images(self, top_path: str | None, bottom_path: str | None):
        try:
            bmp_top = wx.Bitmap(wx.Image(top_path)) if top_path else None
            bmp_bottom = wx.Bitmap(wx.Image(bottom_path)) if bottom_path else None
            self._set_board_images(bmp_top, bmp_bottom)
            if top_path:
                self.last_top_image_path = top_path
            if bottom_path:
                self.last_bottom_image_path = bottom_path
        except Exception as e:
            self.append_log(f"[WARN] Could not load PNG preview: {e}")

    def clear_log(self):
        self.log_ctrl.Clear()

    def on_select_zip_folder(self, event):
        dlg = wx.DirDialog(
            self,
            "Select Folder Containing ZIP Archives",
            str(self.current_folder),
            style=wx.DD_DIR_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self.current_folder = Path(dlg.GetPath())
            self.refresh_zip_list()
        dlg.Destroy()

    def on_open_folder(self, event):
        open_folder_in_explorer(self.current_folder)

    def on_process(self, event):
        self.run_process_action(is_purge=False)

    def on_purge(self, event):
        self.run_process_action(is_purge=True)

    def on_export(self, event):
        selected_symbols = self.collect_selected_symbols_for_export()
        success, _ = export_symbols_with_checks(selected_symbols)
        if success:
            self.append_log("[OK] Export complete.")

    def on_open_output(self, event):
        open_output_folder()

    def on_drc_update(self, event):
        update_drc_rules()

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
            self.refresh_zip_list()
            self.append_log("[OK] ZIP archive list refreshed.")

        elif "Export Project" in tab_label:
            self.append_log("[INFO] Refreshing project symbol list...")
            self.refresh_symbol_list()
            self.append_log("[OK] Project symbol list refreshed.")

        elif "DRC" in tab_label:
            self.append_log("[INFO] DRC Manager ready.")

        elif "Board Images" in tab_label:
            self.append_log("[INFO] Board Images ready.")

        elif "mouser" in tab_label.lower():
            self.append_log("[INFO] Mouser Auto Order ready.")
        event.Skip()

    def _find_project_file(self) -> Path | None:
        """Return the first .kicad_pro in PROJECT_DIR (if any)."""
        candidates = sorted(PROJECT_DIR.glob("*.kicad_pro"))
        return candidates[0] if candidates else None

    def _find_project_pcb(self) -> Path | None:
        """Return preferred PCB file (matching .kicad_pro stem if possible)."""
        proj_files = sorted(PROJECT_DIR.glob("*.kicad_pro"))
        if proj_files:
            preferred = PROJECT_DIR / f"{proj_files[0].stem}.kicad_pcb"
            if preferred.exists():
                return preferred
        fallback = PROJECT_DIR / "Project.kicad_pcb"
        if fallback.exists():
            return fallback
        top_level = sorted(PROJECT_DIR.glob("*.kicad_pcb"))
        if top_level:
            return top_level[0]
        nested = sorted(PROJECT_DIR.rglob("*.kicad_pcb"))
        if nested:
            return nested[0]
        return None

    def _count_copper_layers(self, pcb_path: Path) -> int | None:
        """Return number of copper layers in PCB file, or None on parse error."""
        try:
            with pcb_path.open("r", encoding="utf-8") as f:
                sexpr = loads(f.read())
        except Exception as e:
            self.append_log(f"[ERROR] Failed to parse PCB file: {e}")
            return None

        layers_block = None
        for e in sexpr:
            if isinstance(e, list) and e and e[0] == Symbol("layers"):
                layers_block = e
                break
        if not layers_block:
            return 0

        copper_layers = [
            layer
            for layer in layers_block[1:]
            if isinstance(layer, list)
            and len(layer) > 1
            and str(layer[1]).endswith(".Cu")
        ]
        return len(copper_layers)

    def _find_kicad_cli(self) -> str | None:
        """Locate kicad-cli via PATH or common install locations."""
        kicad_cli = shutil.which("kicad-cli")
        if kicad_cli:
            return kicad_cli

        if os.name == "nt":
            candidates = []
            for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
                base = os.environ.get(env_key)
                if not base:
                    continue
                kicad_root = Path(base) / "KiCad"
                if not kicad_root.exists():
                    continue
                for bin_path in kicad_root.glob("*\\bin\\kicad-cli.exe"):
                    candidates.append(bin_path)
            if candidates:
                return str(sorted(candidates)[-1])

        return None

    def _find_svg_converter(self) -> tuple[str | None, str | None]:
        """
        Return (tool, mode) for converting SVG -> PNG.
        mode in {"magick", "rsvg", "inkscape"}.
        """
        magick = shutil.which("magick")
        if magick:
            return magick, "magick"
        rsvg = shutil.which("rsvg-convert")
        if rsvg:
            return rsvg, "rsvg"
        inkscape = shutil.which("inkscape")
        if inkscape:
            return inkscape, "inkscape"
        return None, None

    def _make_placeholder_bitmap(self, size, text: str) -> wx.Bitmap:
        width, height = size
        bmp = wx.Bitmap(width, height)
        dc = wx.MemoryDC(bmp)
        dc.SetBackground(wx.Brush(wx.Colour(235, 235, 235)))
        dc.Clear()
        dc.SetPen(wx.Pen(wx.Colour(200, 200, 200)))
        dc.DrawRectangle(0, 0, width, height)
        dc.SetTextForeground(wx.Colour(120, 120, 120))
        dc.DrawLabel(text, wx.Rect(0, 0, width, height), alignment=wx.ALIGN_CENTER)
        dc.SelectObject(wx.NullBitmap)
        return bmp

    def _set_board_images(self, top_bmp: wx.Bitmap | None, bottom_bmp: wx.Bitmap | None):
        if top_bmp and top_bmp.IsOk():
            self.board_source_top = top_bmp
            self.board_image_panel_top.set_bitmap(top_bmp)
            self._update_image_label(self.board_image_label_top, top_bmp, self.board_image_panel_top)
        if bottom_bmp and bottom_bmp.IsOk():
            self.board_source_bottom = bottom_bmp
            self.board_image_panel_bottom.set_bitmap(bottom_bmp)
            self._update_image_label(self.board_image_label_bottom, bottom_bmp, self.board_image_panel_bottom)

    def _update_image_label(self, label: wx.StaticText, bmp: wx.Bitmap, panel: wx.Panel):
        label.SetLabel(f"{bmp.GetWidth()} x {bmp.GetHeight()} px")
        self._position_image_label(label, panel)

    def _position_image_label(self, label: wx.StaticText, panel: wx.Panel):
        padding = 6
        size = label.GetBestSize()
        panel_size = panel.GetClientSize()
        x = max(panel_size.width - size.width - padding, 0)
        y = max(panel_size.height - size.height - padding, 0)
        label.SetPosition((x, y))
        label.Raise()

    def on_board_image_resize(self, event):
        event.Skip()
        self._set_board_images(
            getattr(self, "board_source_top", None),
            getattr(self, "board_source_bottom", None),
        )
        self._position_image_label(self.board_image_label_top, self.board_image_panel_top)
        self._position_image_label(self.board_image_label_bottom, self.board_image_panel_bottom)

    def _get_crop_values(self) -> tuple[int, int, int, int]:
        return self.board_image_panel_top.get_crop()

    def _set_crop(self, crop: tuple[int, int, int, int]):
        self.board_image_panel_top.set_crop(crop)
        self.board_image_panel_bottom.set_crop(crop)

    def _set_active_preview(self, side: str):
        self.active_side = side
        active_color = wx.Colour(230, 240, 255)
        inactive_color = self.tab_board.GetBackgroundColour()
        if side == "top":
            self.board_image_panel_top.SetBackgroundColour(active_color)
            self.board_image_panel_bottom.SetBackgroundColour(inactive_color)
        else:
            self.board_image_panel_top.SetBackgroundColour(inactive_color)
            self.board_image_panel_bottom.SetBackgroundColour(active_color)
        self.board_image_panel_top.Refresh()
        self.board_image_panel_bottom.Refresh()

    def _set_render_preset(self, width: int, height: int):
        self.txt_render_w.SetValue(str(width))
        self.txt_render_h.SetValue(str(height))

    def on_preset_changed(self, event):
        idx = self.preset_radio.GetSelection()
        presets = {
            0: (1920, 1080),
            1: (2560, 1440),
            2: (3840, 2160),
            3: (7680, 4320),
        }
        if idx in presets:
            w, h = presets[idx]
            self._set_render_preset(w, h)

    def _auto_crop_from_alpha(self, img: wx.Image, margin_px: int = 20) -> tuple[int, int, int, int] | None:
        """Return crop rectangle in percent based on non-transparent pixels."""
        if not img.HasAlpha():
            return None
        w, h = img.GetWidth(), img.GetHeight()
        data = img.GetAlpha()
        if data is None:
            return None

        min_x, min_y = w, h
        max_x, max_y = -1, -1
        idx = 0
        for y in range(h):
            for x in range(w):
                if data[idx] > 0:
                    if x < min_x: min_x = x
                    if y < min_y: min_y = y
                    if x > max_x: max_x = x
                    if y > max_y: max_y = y
                idx += 1
        if max_x < min_x or max_y < min_y:
            return None

        min_x = max(min_x - margin_px, 0)
        min_y = max(min_y - margin_px, 0)
        max_x = min(max_x + margin_px, w - 1)
        max_y = min(max_y + margin_px, h - 1)

        x0 = int(min_x * 100 / w)
        x1 = int((max_x + 1) * 100 / w)
        y0 = int(min_y * 100 / h)
        y1 = int((max_y + 1) * 100 / h)
        x0 = max(0, min(100, x0))
        x1 = max(0, min(100, x1))
        y0 = max(0, min(100, y0))
        y1 = max(0, min(100, y1))
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, x1, y0, y1)

    def _parse_size_field(self, ctrl: wx.TextCtrl, label: str) -> int | None:
        raw = ctrl.GetValue().strip()
        if not raw.isdigit():
            self.append_log(f"[ERROR] {label} must be a positive integer.")
            return None
        val = int(raw)
        if val <= 0:
            self.append_log(f"[ERROR] {label} must be greater than 0.")
            return None
        return val

    def on_generate_custom_board_images(self, event):
        """Crop existing generated images using the current crop frame."""
        if getattr(self, "_board_busy", False):
            return
        self._set_board_busy(True)

        def worker():
            log = self._log_ui
            top = getattr(self, "last_top_image_path", None) or getattr(self, "base_top_image_path", None)
            bottom = getattr(self, "last_bottom_image_path", None) or getattr(self, "base_bottom_image_path", None)
            if not top or not bottom or not Path(top).exists() or not Path(bottom).exists():
                log("[ERROR] No generated PNGs available to crop. Click 'Generate Board Images' first.")
                wx.CallAfter(self._set_board_busy, False)
                return

            wx.CallAfter(self.board_image_panel_top.set_loading, True, "Cropping...")
            wx.CallAfter(self.board_image_panel_bottom.set_loading, True, "Cropping...")

            top_path = Path(top)
            bottom_path = Path(bottom)
            cropped_top = self._apply_crop_to_png(
                top_path,
                top_path.with_name(f"{top_path.stem}_crop{top_path.suffix}"),
                True,
                log_fn=log,
            )
            cropped_bottom = self._apply_crop_to_png(
                bottom_path,
                bottom_path.with_name(f"{bottom_path.stem}_crop{bottom_path.suffix}"),
                True,
                log_fn=log,
            )
            if not cropped_top or not cropped_bottom:
                wx.CallAfter(self.board_image_panel_top.set_loading, False)
                wx.CallAfter(self.board_image_panel_bottom.set_loading, False)
                wx.CallAfter(self._set_board_busy, False)
                return

            wx.CallAfter(self._load_and_set_board_images, str(cropped_top), str(cropped_bottom))
            wx.CallAfter(self.board_image_panel_top.set_loading, False)
            wx.CallAfter(self.board_image_panel_bottom.set_loading, False)
            log("[OK] Cropped previews updated.")
            wx.CallAfter(self._set_crop, (0, 100, 0, 100))
            wx.CallAfter(self._set_board_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def on_generate_board_images(self, event):
        """Generate top/bottom board images via kicad-cli (full size, no crop)."""
        if getattr(self, "_board_busy", False):
            return
        self._set_board_busy(True)
        threading.Thread(
            target=self._render_board_images,
            args=((2560, 1440), (2560, 1440), False),
            daemon=True,
        ).start()

    def on_save_board_images(self, event):
        """Save last generated images as board_preview_top/bottom.png."""
        top = getattr(self, "last_top_image_path", None)
        bottom = getattr(self, "last_bottom_image_path", None)
        if not top or not bottom or not Path(top).exists() or not Path(bottom).exists():
            self.append_log("[ERROR] No generated PNGs available to save.")
            return

        dst_dir = PROJECT_DIR.parent / "Docs" / "img"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_top = dst_dir / "board_preview_top.png"
        dst_bottom = dst_dir / "board_preview_bottom.png"
        try:
            shutil.copy2(top, dst_top)
            shutil.copy2(bottom, dst_bottom)
        except Exception as e:
            self.append_log(f"[ERROR] Failed to save previews: {e}")
            return
        self.append_log(f"[OK] Saved previews: {dst_top.name}, {dst_bottom.name}")

    def _apply_crop_to_png(
        self,
        src: Path,
        dst: Path,
        apply_crop: bool,
        log_fn=None,
    ) -> Path | None:
        if not src.exists():
            return None
        try:
            img = wx.Image(str(src))
        except Exception as e:
            if log_fn:
                log_fn(f"[WARN] Could not open image for cropping: {e}")
            else:
                self.append_log(f"[WARN] Could not open image for cropping: {e}")
            return None

        x0, x1, y0, y1 = self._get_crop_values()
        if not apply_crop:
            return src
        if (x0, x1, y0, y1) == (0, 100, 0, 100):
            return src

        w, h = img.GetWidth(), img.GetHeight()
        cx0 = int(w * x0 / 100)
        cx1 = int(w * x1 / 100)
        cy0 = int(h * y0 / 100)
        cy1 = int(h * y1 / 100)
        if cx1 <= cx0 or cy1 <= cy0:
            if log_fn:
                log_fn("[ERROR] Invalid crop rectangle.")
            else:
                self.append_log("[ERROR] Invalid crop rectangle.")
            return None

        cropped = img.GetSubImage(wx.Rect(cx0, cy0, cx1 - cx0, cy1 - cy0))
        if not cropped.IsOk():
            if log_fn:
                log_fn("[ERROR] Crop failed.")
            else:
                self.append_log("[ERROR] Crop failed.")
            return None
        cropped.SaveFile(str(dst), wx.BITMAP_TYPE_PNG)
        return dst

    def _render_board_images(self, top_size: tuple[int, int], bottom_size: tuple[int, int], apply_crop: bool):
        log = self._log_ui
        ui = wx.CallAfter
        def finish():
            ui(self.board_image_panel_top.set_loading, False)
            ui(self.board_image_panel_bottom.set_loading, False)
            ui(self._set_board_busy, False)

        pcb = self._find_project_pcb()
        if not pcb:
            log("[ERROR] No .kicad_pcb file found in project.")
            finish()
            return
        layer_count = self._count_copper_layers(pcb)
        if layer_count is None:
            finish()
            return
        if layer_count == 0:
            log("[ERROR] No copper layers found. No board to render.")
            finish()
            return

        kicad_cli = self._find_kicad_cli()
        if not kicad_cli:
            log("[ERROR] kicad-cli not found. Please add it to PATH or install KiCad.")
            finish()
            return

        if not hasattr(self, "board_temp_dir"):
            self.board_temp_dir = Path(tempfile.mkdtemp(prefix="kicad_board_"))
        output_dir = self.board_temp_dir
        top_svg = output_dir / f"{pcb.stem}_top.svg"
        bottom_svg = output_dir / f"{pcb.stem}_bottom.svg"
        top_png = output_dir / f"{pcb.stem}_top.png"
        bottom_png = output_dir / f"{pcb.stem}_bottom.png"

        log(f"[INFO] Exporting board images from {pcb.name} ...")
        ui(self.board_image_panel_top.set_loading, True, "Rendering...")
        ui(self.board_image_panel_bottom.set_loading, True, "Rendering...")

        # Prefer direct render (PNG). Fallback to SVG export if render is unavailable.
        cmd_render_top = [
            kicad_cli,
            "pcb",
            "render",
            "--output",
            str(top_png),
            "--width",
            str(top_size[0]),
            "--height",
            str(top_size[1]),
            "--side",
            "top",
            "--background",
            "transparent",
            "--quality",
            "high",
            str(pcb),
        ]
        cmd_render_bottom = [
            kicad_cli,
            "pcb",
            "render",
            "--output",
            str(bottom_png),
            "--width",
            str(bottom_size[0]),
            "--height",
            str(bottom_size[1]),
            "--side",
            "bottom",
            "--background",
            "transparent",
            "--quality",
            "high",
            str(pcb),
        ]

        try:
            res_top = subprocess.run(cmd_render_top, capture_output=True, text=True)
            res_bottom = subprocess.run(cmd_render_bottom, capture_output=True, text=True)
        except Exception as e:
            log(f"[ERROR] kicad-cli call failed: {e}")
            finish()
            return

        if res_top.returncode == 0 and res_bottom.returncode == 0 and top_png.exists():
            self.base_top_image_path = str(top_png)
            self.base_bottom_image_path = str(bottom_png)
            crop_top = self._apply_crop_to_png(
                top_png, output_dir / f"{pcb.stem}_top_crop.png", apply_crop, log_fn=log
            )
            crop_bottom = self._apply_crop_to_png(
                bottom_png, output_dir / f"{pcb.stem}_bottom_crop.png", apply_crop, log_fn=log
            )
            try:
                img_top = wx.Image(str(crop_top or top_png))
                if not apply_crop:
                    auto_crop = self._auto_crop_from_alpha(img_top, margin_px=20)
                    if auto_crop:
                        ui(self._set_crop, auto_crop)
                ui(
                    self._load_and_set_board_images,
                    str(crop_top or top_png),
                    str(crop_bottom or bottom_png),
                )
            except Exception as e:
                log(f"[WARN] Could not load PNG preview: {e}")
            if apply_crop and (crop_top or crop_bottom):
                log(f"[OK] Board images saved: {top_png.name}, {bottom_png.name} (cropped)")
            else:
                log(f"[OK] Board images saved: {top_png.name}, {bottom_png.name}")
            finish()
            return

        render_err = (res_top.stderr or res_top.stdout or "").strip()
        if render_err:
            log(f"[WARN] Render failed, falling back to SVG export: {render_err}")

        cmd_top = [
            kicad_cli,
            "pcb",
            "export",
            "svg",
            str(pcb),
            "--output",
            str(top_svg),
            "--layers",
            "F.Cu,F.SilkS,Edge.Cuts",
        ]
        cmd_bottom = [
            kicad_cli,
            "pcb",
            "export",
            "svg",
            str(pcb),
            "--output",
            str(bottom_svg),
            "--layers",
            "B.Cu,B.SilkS,Edge.Cuts",
        ]

        try:
            res_top = subprocess.run(cmd_top, capture_output=True, text=True)
            res_bottom = subprocess.run(cmd_bottom, capture_output=True, text=True)
        except Exception as e:
            log(f"[ERROR] kicad-cli call failed: {e}")
            finish()
            return

        if res_top.returncode != 0:
            err = res_top.stderr.strip() or res_top.stdout.strip() or "Unknown error"
            log(f"[ERROR] Top image export failed: {err}")
            finish()
            return

        if res_bottom.returncode != 0:
            err = res_bottom.stderr.strip() or res_bottom.stdout.strip() or "Unknown error"
            log(f"[ERROR] Bottom image export failed: {err}")
            finish()
            return

        converter, mode = self._find_svg_converter()
        if converter and top_svg.exists() and bottom_svg.exists():
            log("[INFO] Converting SVGs to PNG for preview...")
            try:
                if mode == "magick":
                    subprocess.run([converter, str(top_svg), str(top_png)], capture_output=True, text=True)
                    subprocess.run([converter, str(bottom_svg), str(bottom_png)], capture_output=True, text=True)
                elif mode == "rsvg":
                    subprocess.run([converter, str(top_svg), "-o", str(top_png)], capture_output=True, text=True)
                    subprocess.run([converter, str(bottom_svg), "-o", str(bottom_png)], capture_output=True, text=True)
                elif mode == "inkscape":
                    subprocess.run([converter, str(top_svg), "--export-type=png", f"--export-filename={top_png}"], capture_output=True, text=True)
                    subprocess.run([converter, str(bottom_svg), "--export-type=png", f"--export-filename={bottom_png}"], capture_output=True, text=True)
            except Exception as e:
                log(f"[WARN] SVG conversion failed: {e}")

        if top_png.exists():
            self.base_top_image_path = str(top_png)
            self.base_bottom_image_path = str(bottom_png)
            crop_top = self._apply_crop_to_png(
                top_png, output_dir / f"{pcb.stem}_top_crop.png", apply_crop, log_fn=log
            )
            crop_bottom = self._apply_crop_to_png(
                bottom_png, output_dir / f"{pcb.stem}_bottom_crop.png", apply_crop, log_fn=log
            )
            try:
                ui(
                    self._load_and_set_board_images,
                    str(crop_top or top_png),
                    str(crop_bottom or bottom_png),
                )
            except Exception as e:
                log(f"[WARN] Could not load PNG preview: {e}")
        else:
            ui(
                self._set_board_images,
                self._make_placeholder_bitmap((520, 360), "Top SVG saved (no PNG preview)"),
                self._make_placeholder_bitmap((520, 360), "Bottom SVG saved (no PNG preview)"),
            )

        log(
            f"[OK] Board images saved: {top_svg.name}, {bottom_svg.name}"
            + (f" (PNG preview: {top_png.name}, {bottom_png.name})" if top_png.exists() else "")
        )
        finish()

    def on_toggle_log(self, event):
        """Open or focus a separate log window without resizing main layout."""
        if self.log_popup and self.log_popup.IsShown():
            self.log_popup.Raise()
            self.log_popup.Restore()
            return

        dlg = wx.Frame(self, title="Log", size=(900, 500))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        ctrl.SetValue(self.log_ctrl.GetValue())
        sizer.Add(ctrl, 1, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(sizer)
        dlg.Centre()

        def on_close(evt):
            self.log_popup = None
            self.log_popup_ctrl = None
            evt.Skip()

        dlg.Bind(wx.EVT_CLOSE, on_close)
        self.log_popup = dlg
        self.log_popup_ctrl = ctrl
        dlg.Show()

    def _apply_log_visibility(self, show: bool, save_pref: bool = True):
        """Show/hide inline log panel and save preference."""
        self.log_panel.Show(show)
        self.log_panel.SetMinSize((-1, 120 if show else 0))
        self.btn_toggle_log.Enable(show)
        if not show and self.log_popup:
            self.log_popup.Destroy()
            self.log_popup = None
            self.log_popup_ctrl = None
        # Re-layout immediately so other content expands/collapses without restart
        self.log_panel.Layout()
        if hasattr(self, "panel"):
            self.panel.Layout()
        self.Layout()
        self.SendSizeEvent()
        if save_pref:
            cfg = load_config()
            cfg[SHOW_LOG_KEY] = show
            save_config(cfg)

    def on_toggle_show_log(self, event):
        """Handle 'Show log' checkbox toggling."""
        show = self.chk_show_log.IsChecked()
        self._apply_log_visibility(show)

    def on_open_project(self, event):
        """Open the KiCad project file with the default application."""
        if not self.project_file or not self.project_file.exists():
            self.append_log("[ERROR] No .kicad_pro file found to open.")
            return
        try:
            if os.name == "nt":
                os.startfile(str(self.project_file))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.project_file)])
            else:
                subprocess.Popen(["xdg-open", str(self.project_file)])
            self.append_log(f"[OK] Opening project: {self.project_file.name}")
        except Exception as e:
            self.append_log(f"[ERROR] Could not open project: {e}")

    # ---------- Data helpers ----------
    def _make_status_icon(self, colour: wx.Colour, size=12):
        bmp = wx.Bitmap(size, size)
        dc = wx.MemoryDC(bmp)
        dc.SetBrush(wx.Brush(colour))
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.DrawRectangle(0, 0, size, size)
        dc.SelectObject(wx.NullBitmap)
        return bmp

    def refresh_zip_list(self, rows=None):
        """Scan current folder and rebuild ZIP DataView list."""
        self.current_folder.mkdir(parents=True, exist_ok=True)
        self.current_folder_txt.SetLabel(f"Current Folder: {self.current_folder}")
        if rows is None:
            rows = scan_zip_folder(self.current_folder)
        self.zip_rows = rows

        model = self.zip_file_list.GetStore()
        model.DeleteAllItems()

        status_styles = {
            "NEW": (wx.Colour(0, 255, 0), "NEW"),
            "PARTIAL": (wx.Colour(255, 160, 0), "IN PROJECT"),
            "MISSING_SYMBOL": (wx.Colour(255, 80, 80), "Missing Symbol (cannot import)"),
            "MISSING_FOOTPRINT": (wx.Colour(255, 80, 80), "Missing Footprint (cannot import)"),
            "ERROR": (wx.Colour(255, 80, 80), "ERROR"),
            "NONE": (wx.Colour(180, 180, 180), "MISSING"),
        }

        for row in self.zip_rows:
            raw_status = row.get("status", "")
            colour, text = status_styles.get(raw_status, (wx.Colour(180, 180, 180), raw_status or "-"))
            is_disabled = raw_status in ("MISSING_SYMBOL", "MISSING_FOOTPRINT")
            icontext = wx.dataview.DataViewIconText(f" {text}", self._make_status_icon(colour))
            model.AppendItem(
                [not is_disabled and raw_status != "PARTIAL", row.get("name", "unknown.zip"), icontext, "double-click to delete"]
            )

        self.chk_master_zip.SetValue(False)
        self.chk_master_zip.SetLabel("Select All")
        self.zip_file_list.Refresh()

    def refresh_symbol_list(self, symbols=None):
        if symbols is None:
            symbols = list_project_symbols()
        self.symbol_list.Clear()
        for sym in symbols:
            self.symbol_list.Append(sym)
        self.chk_master_symbols.SetValue(False)
        self.chk_master_symbols.SetLabel("Select All")

    def collect_selected_symbols_for_export(self):
        lst = self.symbol_list
        return [lst.GetString(i) for i in range(lst.GetCount()) if lst.IsChecked(i)]

    def _get_selected_zip_paths(self) -> list[Path]:
        model = self.zip_file_list.GetStore()
        selected: list[Path] = []
        for i in range(model.GetCount()):
            if not model.GetValueByRow(i, 0):
                continue
            if i >= len(self.zip_rows):
                continue
            entry = self.zip_rows[i]
            status = entry.get("status", "")
            if status in ("MISSING_SYMBOL", "MISSING_FOOTPRINT"):
                self.append_log(f"[WARN] Skipping {entry.get('name','?')}: missing required files.")
                continue
            path = entry.get("path")
            if path:
                selected.append(Path(path))
        if not selected:
            self.append_log("[WARN] No valid ZIPs selected for import (must contain both symbol + footprint).")
        return selected

    def run_process_action(self, *, is_purge: bool):
        active_files = self._get_selected_zip_paths()
        if not active_files:
            return

        use_symbolname_as_ref = self.chk_use_symbol_name.GetValue()
        ok = process_archives(
            active_files,
            is_purge=is_purge,
            rename_assets=False,
            use_symbol_name=use_symbolname_as_ref,
        )
        if ok:
            self.append_log("[OK] Action complete. Refreshing lists...")
            self.refresh_zip_list()
            self.refresh_symbol_list()
        else:
            self.append_log("[FAIL] Action failed. See log for details.")

class BOMFileDropTarget(wx.FileDropTarget):
    """Enable drag-and-drop of BOM CSV files onto the Mouser tab."""
    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    def OnDropFiles(self, x, y, filenames):
        for f in filenames:
            p = Path(f)
            if p.is_file() and p.suffix.lower() == ".csv":
                self.panel.current_bom_file = str(p)
                wx.CallAfter(self.panel.load_bom, str(p))
                wx.CallAfter(self.panel.log, f"[OK] Loaded BOM via drag-and-drop: {p.name}")
                return True
        wx.CallAfter(self.panel.log, "[WARN] Only CSV files are accepted for BOM import.")
        return False

class MouserAutoOrderTab(wx.Panel):
    """Tab to load BOM CSV, filter items and submit order via Mouser API (in-tab GUI)."""

    def __init__(self, parent, log_callback=None):
        """
        Initialize the panel with controls for loading BOM, selecting columns,
        setting multiplier, and submitting orders. Also sets up the data view.
        """
        super().__init__(parent)
        import mouser_integration as mouser  # heavy import: defer until tab creation
        self.mouser = mouser
        self.log_callback = log_callback
        self.bom_handler = self.mouser.BOMHandler() # BOM parsing utility
        self.order_client = self.mouser.MouserOrderClient() # Mouser order API client
        self.current_bom_file = None
        self.current_data_array = None
        self.temp_bom_dir = Path(tempfile.gettempdir()) / "kicad_mouser_bom"
        self.temp_bom_dir.mkdir(parents=True, exist_ok=True)

        s = wx.BoxSizer(wx.VERTICAL)

        # Top controls
        top = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_generate_bom = wx.Button(self, label="Generate Project BOM")
        self.btn_generate_bom.SetToolTip(
            "Use kicad-cli to generate a BOM from the current project, then load it here."
        )
        self.btn_open_bom = wx.Button(self, label="Open BOM CSV...")
        self.btn_open_bom.SetToolTip("Open a BOM CSV file to load parts from.")
        self.btn_submit = wx.Button(self, label="Submit Cart to Mouser")
        self.btn_submit.SetToolTip(
            "Submit the current selection to Mouser shopping cart via API.\n\n"
            "Make sure that your API key is set in the environment variables and account details contains your address to show EUR currency in Logger."
        )
        
        # Set button icons
        set_button_icon(self.btn_generate_bom, wx.ART_REPORT_VIEW)
        set_button_icon(self.btn_open_bom, wx.ART_FOLDER_OPEN)
        set_button_icon(self.btn_submit, wx.ART_GO_DIR_UP)

        top.Add(self.btn_generate_bom, 1, wx.EXPAND | wx.RIGHT, 6)
        top.Add(self.btn_open_bom, 1, wx.EXPAND | wx.RIGHT, 6)
        sep = wx.StaticLine(self, style=wx.LI_VERTICAL)
        top.Add(sep, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        top.Add(self.btn_submit, 1, wx.EXPAND)
        s.Add(top, 0, wx.ALL, 0)
        s.AddSpacer(10)

        # ---------- Configuration row: MNR column selection + multiplier ----------
        cfg = wx.BoxSizer(wx.HORIZONTAL)
        lbl_mnr_column = wx.StaticText(self, label="Select Mouser Number Column:")
        lbl_mnr_column.SetToolTip("Select which column in the BOM contains the Mouser Part Numbers.")
        cfg.Add(lbl_mnr_column, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 0)
        
        self.choice_mnr = wx.ComboBox(self, choices=[], style=wx.CB_READONLY)
        cfg.Add(self.choice_mnr, 0, wx.RIGHT, 12)

        lbl_multiplier = wx.StaticText(self, label="Multiplier:")
        lbl_multiplier.SetToolTip("Number of parts to order, e.g. 5: order 5x the BOM quantity for 5 PCBs.")
        cfg.Add(lbl_multiplier, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 0)
        self.multiplier = wx.SpinCtrl(self, min=1, max=1000, initial=5)
        cfg.Add(self.multiplier, 0, wx.RIGHT, 12)

        s.Add(cfg, 0, wx.ALL | wx.EXPAND, 0)
        s.AddSpacer(10)
        
        # ---------- Label + master toggle ----------
        header = wx.BoxSizer(wx.HORIZONTAL)
        self.label_dv_header = wx.StaticText(self, label="Components to order (check to include):")
        self.label_dv_header.SetToolTip("Checked items will be included in the order; uncheck to exclude specific lines.")
        header.Add(self.label_dv_header, 0, wx.ALIGN_CENTER_VERTICAL)
        header.AddStretchSpacer()
        self.chk_master_mouser = wx.CheckBox(self, label="Select All")
        self.chk_master_mouser.SetToolTip("Toggle all BOM lines on or off.")
        header.Add(self.chk_master_mouser, 0, wx.ALIGN_CENTER_VERTICAL)
        s.Add(header, 0, wx.LEFT | wx.RIGHT | wx.TOP, 0)

        # ---------- DataView for BOM rows ----------
        self.dv = dv.DataViewListCtrl(self, style=dv.DV_ROW_LINES | dv.DV_VERT_RULES)
        self.col_exclude = self.dv.AppendToggleColumn("Include", width=80) # Checkbox to include items
        self.col_ref     = self.dv.AppendTextColumn("Reference", width=250)
        self.col_mnr     = self.dv.AppendTextColumn("Mouser Number", width=200)
        self.col_qty     = self.dv.AppendTextColumn("Qty", width=80)
        self.col_extra   = self.dv.AppendTextColumn(
            "Extra Qty", width=90, mode=dv.DATAVIEW_CELL_EDITABLE
        )
        self.dv.SetDropTarget(BOMFileDropTarget(self))
        self.SetDropTarget(BOMFileDropTarget(self))
        s.Add(self.dv, 1, wx.EXPAND | wx.ALL, 0)

        self.SetSizer(s)

        # ---------- Bind events ----------
        self.btn_generate_bom.Bind(wx.EVT_BUTTON, self.on_generate_bom)
        self.btn_open_bom.Bind(wx.EVT_BUTTON, self.on_open_bom)
        self.btn_submit.Bind(wx.EVT_BUTTON, self.on_submit_order)
        self.choice_mnr.Bind(wx.EVT_COMBOBOX, self.on_mnr_changed)
        self.Bind(wx.EVT_CHECKBOX, self.on_master_mouser_toggle, self.chk_master_mouser)
        self.dv.Bind(dv.EVT_DATAVIEW_ITEM_VALUE_CHANGED, self.on_mouser_checkbox_changed)

    # ---------- Logging helper ----------
    def log(self, text):
        """Log to panel and optionally forward to main logger."""
        if self.log_callback:
            try:
                self.log_callback(text)
            except Exception:
                pass

    # ---------- Event handlers ----------
    def on_open_bom(self, evt):
        """Open file dialog to pick a BOM CSV file and load it."""
        with wx.FileDialog(self, "Open BOM CSV", wildcard="CSV files (*.csv)|*.csv",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,) as fdg:
            if fdg.ShowModal() != wx.ID_OK:
                return
            path = fdg.GetPath()
            self.current_bom_file = path
            self.load_bom(path)


    def on_generate_bom(self, evt):
        """Generate BOM from current KiCad project using kicad-cli, then open and load it."""
        schematic = self._find_project_schematic()
        if not schematic:
            self.log("[ERROR] Keine .kicad_sch Datei im Projekt gefunden.")
            return

        kicad_cli = self._find_kicad_cli()
        if not kicad_cli:
            self.log("[ERROR] kicad-cli nicht gefunden. Bitte in PATH aufnehmen.")
            return

        bom_path = self.temp_bom_dir / f"{schematic.stem}_bom.csv"

        fields = "Reference,Value,Footprint,MNR,LCSC,${QUANTITY}"
        labels = "Reference,Value,Footprint,MNR,LCSC,Qty"
        cmd = [
            kicad_cli,
            "sch",
            "export",
            "bom",
            str(schematic),
            "--output",
            str(bom_path),
            "--fields",
            fields,
            "--labels",
            labels,
            "--exclude-dnp",
        ]
        self.log(f"[INFO] Generiere BOM aus {schematic.name} ...")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            self.log(f"[ERROR] kicad-cli Aufruf fehlgeschlagen: {e}")
            return

        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip() or "Unbekannter Fehler"
            self.log(f"[ERROR] BOM-Export fehlgeschlagen: {err}")
            return

        self._normalize_bom_headers(bom_path)
        self.current_bom_file = str(bom_path)
        self.load_bom(str(bom_path))
        self.log(f"[OK] BOM erzeugt und in Mouser-Tab geladen: {bom_path.name}")

    def _find_project_schematic(self) -> Path | None:
        """Return preferred schematic for BOM export (matching the .kicad_pro stem)."""
        proj_files = sorted(PROJECT_DIR.glob("*.kicad_pro"))
        if proj_files:
            preferred = PROJECT_DIR / f"{proj_files[0].stem}.kicad_sch"
            if preferred.exists():
                return preferred
        fallback = PROJECT_DIR / "Project.kicad_sch"
        if fallback.exists():
            return fallback
        top_level = sorted(PROJECT_DIR.glob("*.kicad_sch"))
        if top_level:
            return top_level[0]
        nested = sorted(PROJECT_DIR.rglob("*.kicad_sch"))
        if nested:
            return nested[0]
        return None

    def _normalize_bom_headers(self, bom_path: Path):
        """Rename KiCad 'Quantity' column to 'Qty' for Mouser importer expectations."""
        try:
            with open(bom_path, newline="", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                fieldnames = reader[0].keys() if reader else []
        except Exception as e:
            self.log(f"[WARN] BOM konnte nicht geprüft werden: {e}")
            return

        if not fieldnames:
            return

        fieldnames = list(fieldnames)
        mapped = ["Qty" if h == "Quantity" else h for h in fieldnames]
        if mapped == fieldnames:
            return

        try:
            with open(bom_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=mapped)
                writer.writeheader()
                for row in reader:
                    writer.writerow({new: row.get(old, "") for new, old in zip(mapped, fieldnames)})
        except Exception as e:
            self.log(f"[WARN] Konnte BOM-Header nicht anpassen: {e}")

    def load_bom(self, bom_path):
        """
        Load BOM CSV using BOMHandler, populate MNR choices and DataView.
        """
        try:
            self.log(f"[INFO] Loading BOM: {bom_path}")
            data = self.bom_handler.process_bom_file(bom_path)
            
            # Ensure required headers exist
            headers = list(data.keys())
            if "Reference" not in headers or "Qty" not in headers:
                self.log("[ERROR] BOM missing required columns (Reference/Qty).")
                return
            
            # Build MNR choices: any header except Reference/Qty
            choices = [h for h in headers if h not in ("Reference", "Qty")]
            if not choices:
                choices = [self.mouser.CSV_MOUSER_COLUMN_NAME] # fallback: default MNR column name
            self.choice_mnr.Clear()
            self.choice_mnr.AppendItems(choices)
            self.choice_mnr.SetSelection(0)
            self.multiplier.SetValue(5) # reset multiplier to default

            self.current_data_array = data
            
            # Populate DataView
            self.dv.GetStore().DeleteAllItems()
            mnr_col = self.choice_mnr.GetValue() or choices[0]
            self.col_mnr.SetTitle(mnr_col)
            refs = data.get("Reference", [])
            mnrs = data.get(mnr_col, [""] * len(refs))
            qtys = data.get("Qty", [""] * len(refs))
            for ref, mnr, qty in zip(refs, mnrs, qtys):
                self.dv.GetStore().AppendItem([True, str(ref), str(mnr), str(qty), "0"])
                
            self.log(f"[OK] Loaded {len(refs)} BOM rows.")
            self._update_master_mouser_state()
        except Exception as e:
            self.log(f"[ERROR] Failed to load BOM: {e}")
            
    def on_mnr_changed(self, evt):
        """Update DataView column when user selects a different MNR column."""
        if not self.current_data_array:
            return

        mnr_col = self.choice_mnr.GetValue()
        self.col_mnr.SetTitle(mnr_col)
        
        store = self.dv.GetStore()
        store.DeleteAllItems()

        refs = self.current_data_array.get("Reference", [])
        mnrs = self.current_data_array.get(mnr_col, [""] * len(refs))
        qtys = self.current_data_array.get("Qty", [""] * len(refs))

        for ref, mnr, qty in zip(refs, mnrs, qtys):
            store.AppendItem([True, str(ref), str(mnr), str(qty), "0"])
        self._update_master_mouser_state()

    def _update_master_mouser_state(self):
        """Sync master checkbox label/value based on current rows."""
        store = self.dv.GetStore()
        total = store.GetCount()
        checked = sum(1 for row in range(total) if store.GetValueByRow(row, 0))
        all_checked = total > 0 and checked == total
        self.chk_master_mouser.SetValue(all_checked)
        self.chk_master_mouser.SetLabel("Deselect All" if all_checked else "Select All")

    def on_master_mouser_toggle(self, event):
        """Select or deselect all BOM rows."""
        checked = self.chk_master_mouser.IsChecked()
        store = self.dv.GetStore()
        for row in range(store.GetCount()):
            store.SetValueByRow(checked, row, 0)
        self.dv.Refresh()
        self.chk_master_mouser.SetLabel("Deselect All" if checked else "Select All")
        if event:
            event.Skip()

    def on_mouser_checkbox_changed(self, event):
        """Handle per-row checkbox toggles to keep master state in sync."""
        self._update_master_mouser_state()
        if event:
            event.Skip()

    # ---------- Data preparation ----------
    def collect_selected_for_order(self):
        """
        Collect checked DataView rows for ordering.
        Returns a dict compatible with MouserOrderClient.
        """
        store = self.dv.GetStore()
        refs, mnrs, qtys = [], [], []
        extras = []
        col_mnr_name = self.choice_mnr.GetValue() or self.mouser.CSV_MOUSER_COLUMN_NAME
        for row in range(store.GetCount()):
            include_flag = store.GetValueByRow(row, 0)
            if not include_flag:
                continue
            refs.append(store.GetValueByRow(row, 1))
            mnrs.append(store.GetValueByRow(row, 2))
            qtys.append(store.GetValueByRow(row, 3))
            extras.append(store.GetValueByRow(row, 4))
        return {
            "Reference": refs,
            col_mnr_name: mnrs,
            "MNR_Column_Name": col_mnr_name,
            "Qty": qtys,
            "ExtraQty": extras,
            "Multiplier": int(self.multiplier.GetValue() or 1),
        }

    def on_submit_order(self, evt):
        """Submit selected BOM items to Mouser cart using a background thread."""
        if not self.current_data_array:
            self.log("[WARN] No BOM loaded.")
            return

        data_for_order = self.collect_selected_for_order()
        if not data_for_order["Reference"]:
            self.log("[WARN] No items selected for ordering.")
            return

        t = threading.Thread(target=self._run_order_thread, args=(data_for_order,), daemon=True)
        t.start()
    
    def _run_order_thread(self, data_for_order):
        """
        Run order submission in background thread.
        Redirect stdout/stderr to GUI log, retry API requests on failure.
        """
        import sys as _sys
        
        class GuiWriter:
            """Redirect stdout/stderr to GUI log callback."""
            def __init__(self, write_fn):
                self.write_fn = write_fn
            def write(self, s):
                if s is None:
                    return
                for line in str(s).splitlines():
                    if line.strip() != "":
                        wx.CallAfter(self.write_fn, line)
            def flush(self): pass

        old_stdout, old_stderr = _sys.stdout, _sys.stderr
        _sys.stdout, _sys.stderr = GuiWriter(self.log), GuiWriter(self.log)

        try:
            # Retry mechanism for API submission
            attempts = 0
            success = False
            for attempts in range(self.mouser.API_TIMEOUT_MAX_RETRIES):
                self.log(f"Attempt {attempts+1}/{self.mouser.API_TIMEOUT_MAX_RETRIES}")
                try:
                    ok = self.order_client.order_parts_from_data_array(dict(data_for_order))
                except Exception as e:
                    ok = False
                    self.log(f"Exception during order attempt: {e}")
                if ok:
                    success = True
                    break
                if attempts < self.mouser.API_TIMEOUT_MAX_RETRIES - 1:
                    self.log(f"Attempt failed. Retrying in {self.mouser.API_TIMEOUT_SLEEP_S} s...")
                    time.sleep(self.mouser.API_TIMEOUT_SLEEP_S)
            self.log(f"[INFO] Final result: Attempts → {attempts+1}, Success → {success}")
        finally:
            _sys.stdout, _sys.stderr = old_stdout, old_stderr


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
