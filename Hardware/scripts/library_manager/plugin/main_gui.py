# main_gui.py (wx version)
import wx
from gui_wx import KiCadLibraryManagerFrame

def main():
    app = wx.App(False)
    frame = KiCadLibraryManagerFrame()
    app.MainLoop()

if __name__ == "__main__":
    main()
