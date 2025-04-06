import sys
from cx_Freeze import setup, Executable

# Dependencies
build_exe_options = {
    "packages": ["tkinter", "tkinterdnd2", "vlc", "os", "pathlib"],
    "include_files": [
        "C:/Users/march/Downloads/VLC",  # VLC folder (use this path only)
        "C:/Users/march/AppData/Local/Programs/Python/Python310/Lib/site-packages/tkinterdnd2/tkdnd",  # tkdnd folder for Python 3.10
        "C:/Users/march/OneDrive/Desktop/Ultiplay/playlists"  # Playlist files, if any
    ],
    "excludes": [],
}

# Target
setup(
    name="Ultiplay",
    version="1.0",
    description="Ultiplay with VLC and drag-and-drop",
    options={"build_exe": build_exe_options},
    executables=[Executable("ultiplay.py", base="Win32GUI")],
)