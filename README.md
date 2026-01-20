# KiCAD Project Template

<!-- Keep this line! Rendered picture of PCB will be displayed after production files are pushed to branch -->

![](Docs/img/board_preview_top.png)
![](Docs/img/board_preview_bottom.png)


This template provides a clean KiCad repository structure for embedded hardware projects, including a GUI-based import script (`kicad_library_manager`) for component management.

---

## Features

Clean structure for hardware development with automated component import and library management.

**DISCLAIMER:** KiCad files are difficult to edit simultaneously. Limit active work on project files to one person at a time; other tasks like research or tests can be done in parallel.

---

## Getting Started

1. **Clone the repository:**

```bash
git clone https://github.com/Ihysol/kicad-template.git
```

2. **Set up your KiCad project:**

* Add symbols, footprints, and 3D models in `Lib-*` folders
* Check for existing footprints online (ComponentSearchEngine, Mouser, Digi-Key)
* Add parts either using the `kicad_library_manager` GUI or manually
* By default, `kicad_library_manager` uses the `library_input` folder located in the `kicad_library_manager` folder to read component files
* Keep all other libraries local; do not use global libraries

3. **Update this README** to describe your project

---

## Ordering Your PCB

* Use Git tags for PCBs sent to production to snapshot the PCB at that moment
* Use the [KiCAD BOM Mouser Order Script](https://github.com/Ihysol/KiCAD_BOM_Mouser_Order_Script) to generate a BOM cart automatically

---

## Folder Structure

* **Hardware/**: KiCad files, symbols, footprints, and 3D models. Add custom footprints here
* **Production/**: Final Gerber files for PCB ordering
* **Docs/**: Documentation, notes, and changelogs. Link datasheets externally and update after production

---

## Version Control

* Use the main branch for the current hardware version
* Tag commits corresponding to production-ready PCBs (e.g., `v1.0`)
* Keep each repository dedicated to a single hardware design
* Use separate repositories for multiple PCB projects

---

## Scripts & Tools

* The `kicad_library_manager` GUI is located in `Hardware/cse_manager/`
* Imports and organizes components automatically
* Can be run as a Python script or standalone executable
* By default, it uses the `library_input` folder inside the `cse_makicad_library_managernager` folder to read component files

---

## Best Practices

* Always include local libraries in the repository; avoid using global KiCad libraries
* Use tags to reference production-ready hardware versions
* Limit simultaneous editing on KiCad files to **one person** at a time
