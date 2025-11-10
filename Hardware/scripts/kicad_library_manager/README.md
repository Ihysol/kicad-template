# CSE Manager GUI

**CSE Manager** is a GUI tool designed to automate the import and management of electronic components from [componentsearchengine.com](https://componentsearchengine.com/) into your KiCad project.

---

## Features

* Import components from `.zip` files downloaded from componentsearchengine.com
* Automatically generate KiCad symbols, footprints, and library links
* Export parts to the correct project folders in this template
* GUI-based workflow; no command-line interaction required (optional: `cli_main.py`)

---

## Usage

* Use the `cse_manager.exe` or install dependencies manually and run `gui_app.py` (or `cli_main.py`)
* The script will look for `.zip` files in the folder specified in the `.env` file (default: `library_input`)

### Run the GUI

```bash
pip install -r requirements.txt
python3 gui_app.py
```

Or run the standalone executable (`cse_manager.exe` on Windows or `cse_manager` on Linux).

### Workflow

1. Browse or search for the desired component on [componentsearchengine.com](https://componentsearchengine.com/).
2. Download and put the `.zip` files in the folder specified in `.env` (default: `library_input`).
3. Launch the GUI (or press the refresh button if already running).
4. Select the desired parts from the list and press the **Process** button.
5. The GUI will place symbols, footprints, and update library references automatically.

> **Note:** Parts already present in the project (determined by name comparison in `.kicad_sym` files) will be deselected by default to prevent overwriting. To re-import such a component, you must purge it first.

---

## Folder Structure

* **library_input/** – Folder for `.zip` files or component definitions to be imported. `.zip` files here are ignored by Git.

## Build the executable

```
# Windows:
pyinstaller --onefile --name kicad_library_manager --noconsole gui_wx.py

# Linux:
pyinstaller --onefile --name kicad_library_manager --noconsole gui_wx.py
```

