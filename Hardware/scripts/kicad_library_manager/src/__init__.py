import wx
import os
import subprocess
from pathlib import Path

class KiCadLibraryManagerPlugin(ActionPlugin):
    def defaults(self):
        self.name = "KiCad Library Manager"
        self.category = "Library Management"
        self.description = "Manage and import/export KiCad symbol and footprint libraries with GUI"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(
            os.path.dirname(__file__), "icon.png"
        )

    def Run(self):
        """Called when user clicks the toolbar button in KiCad."""
        script_path = Path(__file__).parent / "main_gui.py"

        # run your DearPyGui app in a separate Python process
        subprocess.Popen(["python", str(script_path)])
