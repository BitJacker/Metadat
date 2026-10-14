"""Crea l'installer MSI di Metadat.

Uso (dalla cartella del progetto):
    python installer/setup_msi.py bdist_msi
"""

import os
import sys

from cx_Freeze import Executable, setup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

VERSION = "1.0.0"
UPGRADE_CODE = "{6B1E2C5A-3F7D-4E8B-9C21-5A0D7E4F8B13}"  # non cambiarlo: serve per gli aggiornamenti

build_exe = {
    "packages": ["av", "tkinterdnd2", "tkintermapview", "pillow_heif", "PIL", "defusedxml", "pypdf",
                 "requests", "certifi", "tkinter"],
    "includes": ["extractor", "video", "mapfix"],
    "include_files": [(os.path.join(SRC, "metadat.ico"), "metadat.ico")],
    "excludes": ["unittest", "pydoc_data", "test"],
    "zip_exclude_packages": ["*"],
    "zip_include_packages": [],
    "include_msvcr": True,
}

bdist_msi = {
    "upgrade_code": UPGRADE_CODE,
    "add_to_path": False,
    "initial_target_dir": r"[ProgramFiles64Folder]Metadat",
    "install_icon": os.path.join(SRC, "metadat.ico"),
    "all_users": True,
    "summary_data": {"author": "Metadat", "comments": "Metadati di foto, video e documenti"},
    "data": {
        "Shortcut": [
            ("DesktopShortcut", "DesktopFolder", "Metadat", "TARGETDIR", "[TARGETDIR]Metadat.exe",
             None, "Metadati di foto, video e documenti", None, None, None, None, "TARGETDIR"),
        ],
    },
}

setup(
    name="Metadat",
    version=VERSION,
    description="Metadat",
    options={"build_exe": build_exe, "bdist_msi": bdist_msi},
    executables=[Executable(
        os.path.join(SRC, "app.py"),
        base="gui",
        target_name="Metadat.exe",
        icon=os.path.join(SRC, "metadat.ico"),
        shortcut_name="Metadat",
        shortcut_dir="ProgramMenuFolder",
    )],
)
