"""One import point for Qt (qt-compat-shim: the standalone app cannot reach qgis.PyQt).

Inside QGIS the plugin must use QGIS's own binding through ``qgis.PyQt``
(PyQt5 on QGIS 3.44, PyQt6 on QGIS 4.x). The standalone app uses PyQt6 and
falls back to PyQt5 only if PyQt6 is not installed.

Code in this package uses fully scoped enums (``Qt.AlignmentFlag.AlignLeft``)
and ``exec()``, which work on both PyQt5 5.15 and PyQt6.
"""

import sys

IN_QGIS = "qgis.utils" in sys.modules or "qgis.core" in sys.modules

if IN_QGIS:
    from qgis.PyQt import QtCore, QtGui, QtWidgets  # noqa: F401
    from qgis.PyQt.QtCore import QT_VERSION_STR
else:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: F401
        from PyQt6.QtCore import QT_VERSION_STR
    except ImportError:
        from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: F401
        from PyQt5.QtCore import QT_VERSION_STR

Qt = QtCore.Qt
pyqtSignal = QtCore.pyqtSignal
QT6 = QT_VERSION_STR.startswith("6")

# Qt6 moved QAction (and QShortcut) from QtWidgets to QtGui
QAction = getattr(QtGui, "QAction", None) or getattr(QtWidgets, "QAction")
