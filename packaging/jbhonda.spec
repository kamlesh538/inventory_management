# PyInstaller build: produces dist/JBHonda/ (the app folder the installer ships).
# Run from the project root:  uv run --group build pyinstaller packaging/jbhonda.spec --noconfirm
# ruff: noqa

from pathlib import Path

ROOT = Path(SPECPATH).parent
PKG = ROOT / "src" / "jbhonda"

datas = [
    (str(PKG / "db" / "migrations" / "*.sql"), "jbhonda/db/migrations"),
    (str(PKG / "assets" / "fonts" / "*"), "jbhonda/assets/fonts"),
    (str(PKG / "assets" / "app.ico"), "jbhonda/assets"),
    (str(PKG / "assets" / "app.png"), "jbhonda/assets"),
]

# Qt modules the app doesn't use; leaving them out keeps the installer much smaller.
excludes = [
    "tkinter", "pytest", "pytestqt", "_pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSvgWidgets", "PySide6.QtOpenGLWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=["jbhonda.db.migrations", "jbhonda.assets"],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JBHonda",
    icon=str(PKG / "assets" / "app.ico"),
    console=False,  # a windowed app: no black console box behind it
    upx=False,      # UPX-compressed exes are often flagged by antivirus
)
coll = COLLECT(exe, a.binaries, a.datas, name="JBHonda", upx=False)
