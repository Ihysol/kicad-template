# KiCAD Project Template

<!-- Keep this line! Rendered picture of PCB will be displayed after production files are pushed to branch -->

![](Docs/board_preview.png)

This is a ready-to-use template for a clean KiCad repository, designed for embedded hardware projects.

---

## Features

* Clean structure for embedded hardware development.
* GUI-based import script (`cse_manager`) for adding parts from [componentsearchengine.com](https://componentsearchengine.com/).

**DISCLAIMER:** KiCad’s file structure makes simultaneous editing by multiple people difficult. Limit active work on the project files to one person at a time. Other work, such as research or tests, should be done in parallel, switching as tasks are completed.

---

## How to Use

### 1. Clone this repository

```bash
git clone https://github.com/Ihysol/kicad-template.git
```

### 2. Set up your KiCad project

1. Add symbols, footprints, and 3D models for parts in the respective `Lib-*` folders.
2. Check if footprints are available online (via [componentsearchengine.com](https://componentsearchengine.com/) or distributor websites like Mouser, Digi-Key, etc.).
3. Add the parts:

   * **Automatic**: Use the `cse_manager` script included in this repository to add parts automatically.
   * **Manual**: Add files manually and link them properly in KiCad.
4. All other included libraries **must** be local and uploaded with this repository (do **not** use global libraries).

### 3. Update this README

* Describe your project and its purpose.

---

## About Ordering Your PCB

### Use Tags

* Create Git tags for PCBs sent to production.
* This provides a snapshot of the PCB at the time of ordering.

### Generate a Mouser Cart

* Use the [KiCAD BOM Mouser Order Script](https://github.com/Ihysol/KiCAD_BOM_Mouser_Order_Script) to generate a cart from your BOM automatically.

---

## Folder Structure

All folders in this template are intended to be used:

* **Hardware/** – contains all KiCad-related files, including symbols, footprints, and 3D models.

  * Add custom footprints here if needed.
* **Production/** – for generated Gerber files ready for PCB ordering. Only include finalized outputs here.
* **Docs/** – notes, documentation, and changelogs.

  * Include relevant information about development, issues, and design decisions.
  * Datasheets should be linked externally when possible (to minimize repository size).
  * After pushing Gerber files, schematics, and board layers can be added to the Docs folder.

---

## Version Control with KiCad

* Use the **main branch** for your current hardware version.
* When a PCB is ordered:

  1. **Create a tag** for the commit corresponding to the ordered version (e.g., `v1.0`).
  2. Update the `Docs/` folder with changelogs and relevant notes.
* Each repository/project should contain **only one hardware design**.

  * For multiple PCBs with different purposes, create separate repositories using this template.

---

## Scripts & Tools

### cse_manager

* Location: `Hardware/cse_manager/`
* Provides a GUI to import and organize parts from componentsearchengine.com.
* Can be run as a Python script or, if built with PyInstaller, as a standalone executable.

---

## Best Practices

* Always include local libraries in the repository; avoid using global KiCad libraries.
* Use tags to reference production-ready hardware versions.
* Keep generated files separate (e.g., in `Production/` or temporary folders) and ignore them in Git.
* Limit simultaneous editing on KiCad files to one person at a time.
