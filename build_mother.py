# -*- coding: utf-8 -*-
"""打包母体 exe 并复制到桌面。"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
ICON = os.path.join(ROOT, "assets", "icon.ico")

cmd = [
    PY, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "监考系统",
    "--icon", ICON,
    "--add-data", os.path.join("bank.json") + ";.",
    "--add-data", os.path.join("assets", "icon.ico") + ";assets",
    "--hidden-import", "core",
    "author_app.py",
]
print("RUN:", " ".join(cmd))
r = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
print(r.stdout[-4000:])
print("returncode:", r.returncode)

exe = os.path.join(ROOT, "dist", "监考系统.exe")
if os.path.exists(exe):
    desktop = os.path.join(os.path.expanduser("~"), "Desktop", "监考系统.exe")
    shutil.copyfile(exe, desktop)
    print("COPIED TO:", desktop, os.path.getsize(desktop), "bytes")
else:
    print("EXE NOT FOUND")
