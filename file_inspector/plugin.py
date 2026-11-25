"""QGIS plugin wrapper: a dock panel plus a layer context-menu action."""

import os

from qgis.core import (Qgis, QgsProject, QgsProviderRegistry, QgsRasterLayer,
                       QgsSettings, QgsVectorLayer)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QDockWidget

from .core.i18n import t
from .ui.inspector_widget import InspectorWidget
from .ui.qt_compat import QAction

ICON = os.path.join(os.path.dirname(__file__), "icon.svg")


class FileInspectorPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.widget = None
        self.action = None
        self.layer_action = None

    # ------------------------------------------------------------------ lifecycle
    def initGui(self):
        self.action = QAction(QIcon(ICON), t("menu"), self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle)
        self.iface.addPluginToMenu(t("menu"), self.action)
        self.iface.addToolBarIcon(self.action)

        self.layer_action = QAction(QIcon(ICON), t("inspect_layer"), self.iface.mainWindow())
        self.layer_action.triggered.connect(self._inspect_current_layer)
        for ltype in (Qgis.LayerType.Vector, Qgis.LayerType.Raster):
            self.iface.addCustomActionForLayerType(self.layer_action, "", ltype, True)

    def unload(self):
        if self.layer_action:
            self.iface.removeCustomActionForLayerType(self.layer_action)
        if self.action:
            self.iface.removePluginMenu(t("menu"), self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
        self.dock = self.widget = self.action = self.layer_action = None

    # ------------------------------------------------------------------ panel
    def _ensure_dock(self):
        if self.dock:
            return
        self.widget = InspectorWidget(QgsSettings(), add_layers=self._add_layers)
        self.dock = QDockWidget(t("app_title"), self.iface.mainWindow())
        self.dock.setObjectName("FileInspectorDock")
        self.dock.setWidget(self.widget)
        self.dock.visibilityChanged.connect(self._dock_visibility)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

    def _toggle(self, on):
        self._ensure_dock()
        self.dock.setVisible(on)

    def _dock_visibility(self, visible):
        if self.action and self.action.isChecked() != visible:
            self.action.blockSignals(True)
            self.action.setChecked(visible)
            self.action.blockSignals(False)
        if visible and self.widget:
            self.dock.setWindowTitle(t("app_title"))

    def _inspect_current_layer(self):
        layer = self.iface.activeLayer()
        path = None
        if layer is not None:
            parts = QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source())
            path = parts.get("path")
            # QGIS strips /vsizip/ into "vsiPrefix"; "{archive}/member" keeps braces
            if path and path.startswith("{") and "}" in path:
                path = path[1:path.index("}")]
        self._ensure_dock()
        self.dock.show()
        self.dock.raise_()
        if path and os.path.isfile(path):
            self.widget.inspect(path)
        else:
            self.iface.messageBar().pushWarning(t("app_title"), t("no_layer_file"))

    # ------------------------------------------------------------------ add to map
    def _add_layers(self, specs):
        added, failed = 0, []
        project = QgsProject.instance()
        for spec in specs:
            if spec["type"] == "raster":
                layer = QgsRasterLayer(spec["uri"], spec["name"], spec["provider"])
            else:
                layer = QgsVectorLayer(spec["uri"], spec["name"], spec["provider"])
            if layer.isValid():
                project.addMapLayer(layer)
                added += 1
            else:
                failed.append(spec["name"])
        return added, failed
