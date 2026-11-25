"""Standalone application (PyQt6; PyQt5 is used only if PyQt6 is missing).

    python -m file_inspector [FILE]
"""

import os
import sys

from .ui.qt_compat import QtCore, QtGui, QtWidgets
from .ui import theme
from .ui.inspector_widget import InspectorWidget

ICON = os.path.join(os.path.dirname(__file__), "icon.svg")


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = QtWidgets.QApplication(argv)
    app.setApplicationName("File Inspector")
    app.setOrganizationName("GIS GEORGIA")
    app.setWindowIcon(QtGui.QIcon(ICON))
    theme.remember_system_palette()
    app.setStyle("Fusion")

    settings = QtCore.QSettings("GIS GEORGIA", "File Inspector")
    win = QtWidgets.QMainWindow()
    widget = InspectorWidget(settings, standalone=True)
    win.setCentralWidget(widget)
    geometry = settings.value("window/geometry")
    if geometry is not None:
        win.restoreGeometry(geometry)
    else:
        win.resize(980, 760)

    orig_close = win.closeEvent

    def close_event(e):
        settings.setValue("window/geometry", win.saveGeometry())
        orig_close(e)
    win.closeEvent = close_event

    win.show()
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        widget.inspect(os.path.abspath(files[0]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
