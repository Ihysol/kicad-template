# KiCAD Project Template <- Change to your project name!
<!-- Keep this line! Rendered picture of PCB is going to be displayed after Production files are pushed to branch! -->
<!--  -->
<!--  -->
![](Docs/board_preview.png)
<!--  -->
<!--  -->
<!--  -->

This is a template for a clean kicad repository. Use it to create your future projects!

**DISCLAMER**: *KiCADs file structure makes it hard for multiple people to work on the project on the same time. So limit the active work on the project files to one person at a given time and split other work like research or tests to other members and switch if given tasks are finished.* 

## HOW TO USE <------ START HERE!
1. Clone this repository
2. Setup your KiCAD project
    - Add symbols, footprints and 3d-models for parts in the respective Lib-* folders.
    - Check if there are already available footprints for your parts online (https://componentsearchengine.com/ or on distributor websites like mouser ect.)
    - I would recommend using the library loader program (https://www.samacsys.com/library-loader/), add the symbol and footprint folders of the library loader to the global libraries of your kicad (only need to do it once).
    - Add the libraries to the ProjectSymbols or ProjectFootprints libraries already existing in this Template. Other added Libraries ect. must be used as local libraries (NOT global ones).
3. Change this README.md to what your project is about (look at exampleREADME.md)

HINTS: 
- Please create TAGS for pcbs that were ordered. This way there is a snapshot in time what the pcb file looked like at the time of ordering.
- To make your life easier, refer to this repository for automatic generation of carts on mouser: https://github.com/Ihysol/KiCAD_BOM_Mouser_Order_Script
- Violations that make the project or repository unrepresentable might revoke your rights to work on this repository until the issues are resolved.

## Folder structure
The folders in this template are __all__ to be used! 

The **Hardware** folder will contain all KiCAD related files, including the symbols, footprints and 3dfiles if there are any. If graphic items are realized through custom footprints, add them in the footprints folder.

**Docs** are for notes, documentation and changelogs on the projects function. Any noteworthy informations and even problems during the development should be written down there. Datasheets are to be linked externally if possible (url) and not placed in there to minimize the size of the repository. After pushing gerber files, schematics and board layers are pushed into Docs folder.

The **Production** folder is for the eventually generated gerberfiles to be ordered as finalized pcbs. Nothing else.

## Use git for version control
**INFO**: Read the disclaimer on top.

Use the main branch for your **current** hardware version. 

If an pcb was ordered, **create a TAG of the commit that was ordered** and name it appropriately (e.g. v1.0). This will keep this version and prevent alteration of the documentation. TAGs will be used as reference for the hardware at the current time (if other people need to work on it). Also update the Docs folder by adding changelogs.

One repository/project should only be for **ONE** hardware. Split multiple pcbs with different purposes into their own repository and apply all above rules by using this template again.