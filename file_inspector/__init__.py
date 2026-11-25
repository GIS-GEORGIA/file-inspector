"""File Inspector: identify a file and its contents by what is inside it.

Works as a QGIS plugin (QGIS 3.44+ on Qt5, QGIS 4.x on Qt6) and as a
standalone PyQt6 application (`python -m file_inspector`).
"""


def classFactory(iface):
    from .plugin import FileInspectorPlugin
    return FileInspectorPlugin(iface)
