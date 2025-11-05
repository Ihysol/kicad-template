import os
import sys
import subprocess
from pathlib import Path
from pcbnew import ActionPlugin  # KiCad stellt das bereit

class KiCadLibraryManagerPlugin(ActionPlugin):
    def defaults(self):
        self.name = "KiCad Library Manager"
        self.category = "Library Management"
        self.description = "Manage and import/export KiCad symbol and footprint libraries"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__), "icon.png")

    def Run(self):
        """Launch DearPyGui interface."""
        plugin_dir = Path(__file__).parent
        main_gui = plugin_dir / "main_gui.py"
        python_exe = sys.executable
        subprocess.Popen([python_exe, str(main_gui)])
