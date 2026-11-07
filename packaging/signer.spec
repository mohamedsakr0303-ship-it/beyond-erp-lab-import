# PyInstaller recipe for the BeyondERP Signer on Windows: a windowed program (no console), as a folder that
# packaging/signer.iss installs, with BEYONDERP's name, copyright and version in the program's properties.
#   pyinstaller packaging/signer.spec --noconfirm --distpath dist-signer --workpath build-signer
import re
import sys
from pathlib import Path

HERE = Path(SPECPATH)
ROOT = HERE.parent
VERSION = re.search(r'^VERSION = "([^"]+)"', (ROOT / "signer" / "beyond_signer.py").read_text(encoding="utf-8"), re.M).group(1)
COPYRIGHT = "Copyright (c) 2026 BEYONDERP. All rights reserved."
nums = tuple(int(x) for x in VERSION.split("."))[:3]
nums = nums + (0,) * (4 - len(nums))


def version_info():
    """The program's properties in Windows (Properties → Details): publisher, copyright, product and version."""
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
                                                     VarStruct, VSVersionInfo)
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "BEYONDERP"),
            StringStruct("FileDescription", "BeyondERP Signer"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "BeyondERP-Signer"),
            StringStruct("LegalCopyright", COPYRIGHT),
            StringStruct("LegalTrademarks", "BeyondERP is a trademark of BEYONDERP."),
            StringStruct("OriginalFilename", "BeyondERP-Signer.exe"),
            StringStruct("ProductName", "BeyondERP Signer"),
            StringStruct("ProductVersion", VERSION)])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])])])


a = Analysis([str(ROOT / "signer" / "beyond_signer.py")], pathex=[str(ROOT / "signer")],
             datas=[(str(HERE / "icon.png"), ".")],               # the window's and the tray icon's picture
             hiddenimports=["pystray._win32"],
             excludes=["tkinter", "numpy", "pydoc"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="BeyondERP-Signer", console=False, icon=str(HERE / "icon.ico"),
          version=version_info() if sys.platform == "win32" else None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="BeyondERP-Signer", upx=False)
