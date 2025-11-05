# plugin/__init__.py
import wx
import pcbnew
from .gui_wx import KiCadLibraryManagerFrame

class LibraryManagerPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "KiCad Library Manager"
        self.category = "Library Tools"
        self.description = "Manage, import and export KiCad libraries"
        self.icon_file_name = self._icon_path()

    def _icon_path(self):
        import os
        return os.path.join(os.path.dirname(__file__), "..", "resources", "icon.png")

    def Run(self):
        # Use existing wx.App if running inside KiCad
        app = wx.GetApp() or wx.App(False)
        frame = KiCadLibraryManagerFrame(None)
        frame.Show()

LibraryManagerPlugin().register()
