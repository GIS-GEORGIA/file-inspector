"""The File Inspector panel, shared by the QGIS plugin and the standalone app."""

import os
import shutil

from ..core import detector
from ..core import i18n
from ..core.i18n import t
from . import theme
from .qt_compat import Qt, QtCore, QtGui, QtWidgets, pyqtSignal

SETTINGS_PREFIX = "file_inspector/"


class _AnalyzeThread(QtCore.QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.done.emit(detector.analyze(self.path))
        except Exception as e:  # report every read error to the user
            self.failed.emit(f"{type(e).__name__}: {e}")


class _DropZone(QtWidgets.QFrame):
    fileDropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("active", False)

    def _set_active(self, on):
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, e):
        self._set_active(False)

    def dropEvent(self, e):
        self._set_active(False)
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.fileDropped.emit(u.toLocalFile())
                e.acceptProposedAction()
                return


class InspectorWidget(QtWidgets.QWidget):
    """`settings` is a QSettings-like object. `add_layers` is an optional
    callable(list_of_layer_dicts) -> (added_count, [failed_names]); when it is
    given (inside QGIS) an “Add to map” button is shown."""

    themeChanged = pyqtSignal(bool)   # True = dark

    def __init__(self, settings, add_layers=None, standalone=False, parent=None):
        super().__init__(parent)
        self.setObjectName("FileInspectorRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.settings = settings
        self.add_layers = add_layers
        self.standalone = standalone
        self.result = None
        self._thread = None
        self._dark = False

        i18n.set_lang(str(settings.value(SETTINGS_PREFIX + "lang", "ka")))
        self._theme_mode = str(settings.value(SETTINGS_PREFIX + "theme", "auto"))
        if self._theme_mode not in theme.MODES:
            self._theme_mode = "auto"

        self._build()
        self.apply_theme()
        self.retranslate()

    # ------------------------------------------------------------------ layout
    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)

        # header
        header = QtWidgets.QHBoxLayout()
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(0)
        self.title = QtWidgets.QLabel(objectName="Title")
        self.subtitle = QtWidgets.QLabel(objectName="Subtitle")
        self.subtitle.setWordWrap(True)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        self.lang_label = QtWidgets.QLabel(objectName="Muted")
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItem("ქართული", "ka")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setCurrentIndex(0 if i18n.lang() == "ka" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_lang)
        self.theme_label = QtWidgets.QLabel(objectName="Muted")
        self.theme_combo = QtWidgets.QComboBox()
        for _ in theme.MODES:
            self.theme_combo.addItem("")
        self.theme_combo.setCurrentIndex(theme.MODES.index(self._theme_mode))
        self.theme_combo.currentIndexChanged.connect(self._on_theme)
        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(6)
        for w in (self.lang_label, self.lang_combo, self.theme_label, self.theme_combo):
            controls.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        controls.insertSpacing(2, 10)
        header.addLayout(controls)
        header.setAlignment(controls, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        # drop zone
        self.drop = _DropZone()
        self.drop.fileDropped.connect(self.inspect)
        dz = QtWidgets.QHBoxLayout(self.drop)
        dz.setContentsMargins(16, 12, 16, 12)
        icon = QtWidgets.QLabel("⤓", objectName="DropIcon")
        dz.addWidget(icon)
        hint_box = QtWidgets.QVBoxLayout()
        hint_box.setSpacing(2)
        self.drop_hint = QtWidgets.QLabel()
        self.drop_hint.setWordWrap(True)
        self.drop_hint_small = QtWidgets.QLabel(objectName="Muted")
        self.drop_hint_small.setWordWrap(True)
        hint_box.addWidget(self.drop_hint)
        hint_box.addWidget(self.drop_hint_small)
        dz.addLayout(hint_box, 1)
        self.open_btn = QtWidgets.QPushButton(objectName="Primary")
        self.open_btn.clicked.connect(self._choose_file)
        dz.addWidget(self.open_btn)
        root.addWidget(self.drop)

        # summary card
        self.card = QtWidgets.QFrame(objectName="Card")
        card = QtWidgets.QVBoxLayout(self.card)
        card.setContentsMargins(16, 12, 16, 12)
        top = QtWidgets.QHBoxLayout()
        self.big_type = QtWidgets.QLabel(objectName="BigType")
        self.big_type.setWordWrap(True)
        self.big_type.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.badge = QtWidgets.QLabel(objectName="Badge")
        top.addWidget(self.big_type, 1)
        top.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        card.addLayout(top)
        self.grid = QtWidgets.QGridLayout()
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(4)
        self.facts = {}
        for i, key in enumerate(("lbl_extension", "lbl_size", "mime", "lbl_confidence", "lbl_entropy", "lbl_path")):
            k = QtWidgets.QLabel(objectName="Muted")
            v = QtWidgets.QLabel()
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setWordWrap(key == "lbl_path")
            row, col = (i // 2, (i % 2) * 2) if key != "lbl_path" else (3, 0)
            self.grid.addWidget(k, row, col)
            self.grid.addWidget(v, row, col + 1, 1, 3 if key == "lbl_path" else 1)
            self.facts[key] = (k, v)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(3, 1)
        card.addLayout(self.grid)
        root.addWidget(self.card)

        # warnings
        self.warn = QtWidgets.QFrame(objectName="Warn")
        wl = QtWidgets.QVBoxLayout(self.warn)
        wl.setContentsMargins(12, 8, 12, 8)
        self.warn_label = QtWidgets.QLabel()
        self.warn_label.setWordWrap(True)
        self.warn_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        wl.addWidget(self.warn_label)
        root.addWidget(self.warn)

        # actions
        actions = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton(objectName="Primary")
        self.add_btn.clicked.connect(self._add_to_map)
        self.save_btn = QtWidgets.QPushButton()
        self.save_btn.clicked.connect(self._save_copy)
        self.copy_btn = QtWidgets.QPushButton()
        self.copy_btn.clicked.connect(self._copy_report)
        actions.addWidget(self.add_btn)
        actions.addWidget(self.save_btn)
        actions.addStretch(1)
        actions.addWidget(self.copy_btn)
        root.addLayout(actions)

        # tabs
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(False)
        self.details = self._make_table()
        self.details.cellDoubleClicked.connect(self._open_link)
        self.contents_box = QtWidgets.QWidget()
        cb = QtWidgets.QVBoxLayout(self.contents_box)
        cb.setContentsMargins(0, 6, 0, 0)
        self.contents_title = QtWidgets.QLabel(objectName="Muted")
        self.contents_title.setContentsMargins(10, 0, 0, 0)
        self.contents = self._make_table()
        cb.addWidget(self.contents_title)
        cb.addWidget(self.contents)
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        self.hex = QtWidgets.QPlainTextEdit(readOnly=True)
        self.hex.setFont(mono)
        self.hex.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.strings = QtWidgets.QPlainTextEdit(readOnly=True)
        self.strings.setFont(mono)
        self.tabs.addTab(self.details, "")
        self.tabs.addTab(self.contents_box, "")
        self.tabs.addTab(self.hex, "")
        self.tabs.addTab(self.strings, "")
        root.addWidget(self.tabs, 1)

        self.status = QtWidgets.QLabel(objectName="Status")
        root.addWidget(self.status)

        self.setAcceptDrops(True)
        self._show_result_widgets(False)

    def _make_table(self):
        tw = QtWidgets.QTableWidget()
        tw.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        tw.setAlternatingRowColors(True)
        tw.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        tw.verticalHeader().setVisible(False)
        tw.setShowGrid(False)
        tw.setWordWrap(False)
        tw.horizontalHeader().setHighlightSections(False)
        tw.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return tw

    def _show_result_widgets(self, on):
        for w in (self.card, self.tabs):
            w.setVisible(on)
        self.warn.setVisible(on and bool(self.result and self.result["warnings"]))
        self.add_btn.setVisible(on and self.add_layers is not None and bool(self.result and self.result["qgis_layers"]))
        self.save_btn.setVisible(on and bool(self.result and self.result["ext_mismatch"]))
        self.copy_btn.setVisible(on)

    # drops anywhere on the panel are handled too
    def dragEnterEvent(self, e):
        self.drop.dragEnterEvent(e)

    def dragLeaveEvent(self, e):
        self.drop.dragLeaveEvent(e)

    def dropEvent(self, e):
        self.drop.dropEvent(e)

    # ------------------------------------------------------------------ theme / language
    def apply_theme(self):
        self._dark = theme.apply(self, self._theme_mode, whole_app=self.standalone)
        if self.result:
            self._paint_badge()
        self.themeChanged.emit(self._dark)

    def changeEvent(self, e):
        # follow QGIS / system theme switches while in automatic mode
        if e.type() == QtCore.QEvent.Type.PaletteChange and self._theme_mode == "auto" and not self.standalone:
            dark = theme.resolve("auto")
            if dark != self._dark:
                QtCore.QTimer.singleShot(0, self.apply_theme)
        super().changeEvent(e)

    def _on_theme(self, index):
        self._theme_mode = theme.MODES[index]
        self.settings.setValue(SETTINGS_PREFIX + "theme", self._theme_mode)
        self.apply_theme()

    def _on_lang(self, index):
        i18n.set_lang(self.lang_combo.itemData(index))
        self.settings.setValue(SETTINGS_PREFIX + "lang", i18n.lang())
        self.retranslate()

    def retranslate(self):
        self.title.setText(t("app_title"))
        self.subtitle.setText(t("app_subtitle"))
        self.lang_label.setText(t("language"))
        self.theme_label.setText(t("theme"))
        for i, mode in enumerate(theme.MODES):
            self.theme_combo.setItemText(i, t("theme_" + mode))
        self.drop_hint.setText(t("drop_hint"))
        self.drop_hint_small.setText(t("drop_hint_small"))
        self.open_btn.setText(t("open_file"))
        self.add_btn.setText(t("add_to_map"))
        self.save_btn.setText(t("save_with_ext"))
        self.copy_btn.setText(t("copy_report"))
        for i, key in enumerate(("tab_details", "tab_contents", "tab_hex", "tab_strings")):
            self.tabs.setTabText(i, t(key))
        for key, (k, _v) in self.facts.items():
            k.setText("MIME" if key == "mime" else t(key))
        if self.result:
            self._render()
        if self.standalone:
            self.window().setWindowTitle(t("app_title"))

    # ------------------------------------------------------------------ analysis
    def _choose_file(self):
        start = str(self.settings.value(SETTINGS_PREFIX + "last_dir", os.path.expanduser("~")))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, t("open_file"), start)
        if path:
            self.inspect(path)

    def inspect(self, path):
        if not os.path.isfile(path):
            self.status.setText(t("not_a_file"))
            return
        if self._thread is not None and self._thread.isRunning():
            return
        self.settings.setValue(SETTINGS_PREFIX + "last_dir", os.path.dirname(path))
        self.status.setText(t("analyzing", name=os.path.basename(path)))
        self.open_btn.setEnabled(False)
        self._thread = _AnalyzeThread(path, self)
        self._thread.done.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.finished.connect(lambda: self.open_btn.setEnabled(True))
        self._thread.start()

    def _on_done(self, result):
        self.result = result
        self.status.setText("")
        self._render()
        self.tabs.setCurrentIndex(0)

    def _on_failed(self, error):
        self.status.setText("")
        QtWidgets.QMessageBox.warning(self, t("error"), t("error_open", error=error))

    # ------------------------------------------------------------------ rendering
    def _paint_badge(self):
        color = theme.category_color(self.result["category"], self._dark)
        text_color = "#0b1220" if self._dark else "#ffffff"
        self.badge.setStyleSheet(f"background: {color}; color: {text_color};")

    def _render(self):
        r = self.result
        self.big_type.setText(r["label"])
        self.badge.setText(t("cat_" + r["category"]))
        self._paint_badge()
        ext = "." + r["extension"] if r["extension"] else t("no_extension")
        if r["ext_mismatch"]:
            ext += "  ⚠"
        vals = {
            "lbl_extension": ext,
            "lbl_size": f"{detector.human_size(r['size'])}  ({r['size']:,} B)",
            "mime": r["mime"],
            "lbl_confidence": t("conf_" + r["confidence"]),
            "lbl_entropy": f"{r['entropy']:.2f} / 8",
            "lbl_path": r["path"],
        }
        for key, (_k, v) in self.facts.items():
            v.setText(vals[key])
        self.facts["lbl_entropy"][1].setToolTip(t("entropy_hint"))

        self.warn_label.setText("\n".join("⚠  " + t(k, **a) for k, a in r["warnings"]))

        # details
        rows = r["details"]
        self.details.clear()
        self.details.setColumnCount(2)
        self.details.setRowCount(len(rows))
        self.details.setHorizontalHeaderLabels([t("col_property"), t("col_value")])
        for i, (k, v) in enumerate(rows):
            if isinstance(v, str) and v in ("yes", "no"):
                v = t("v_" + v)
            self.details.setItem(i, 0, QtWidgets.QTableWidgetItem(t(k)))
            item = QtWidgets.QTableWidgetItem(str(v))
            if isinstance(v, str) and v.startswith("http"):
                item.setForeground(QtGui.QColor(theme.colors(self._dark)["accent"]))
                item.setToolTip(v)
            self.details.setItem(i, 1, item)
        self.details.resizeColumnToContents(0)
        self.details.horizontalHeader().setStretchLastSection(True)

        # contents
        c = r["contents"]
        self.contents.clear()
        if c:
            cols = [t(col) if isinstance(col, str) and col.startswith("col_") else str(col) for col in c["columns"]]
            self.contents_title.setText(t(c["title"]) + f"  ·  {len(c['rows'])}")
            self.contents.setColumnCount(len(cols))
            self.contents.setRowCount(len(c["rows"]))
            self.contents.setHorizontalHeaderLabels(cols)
            for i, row in enumerate(c["rows"]):
                for j, val in enumerate(row[:len(cols)]):
                    self.contents.setItem(i, j, QtWidgets.QTableWidgetItem("" if val is None else str(val)))
            self.contents.resizeColumnsToContents()
            for j in range(len(cols)):
                if self.contents.columnWidth(j) > 320:
                    self.contents.setColumnWidth(j, 320)
            self.contents.horizontalHeader().setStretchLastSection(True)
        else:
            self.contents_title.setText(t("no_contents"))
            self.contents.setColumnCount(0)
            self.contents.setRowCount(0)
        self.tabs.setTabEnabled(1, bool(c))

        # hex
        self.hex.setPlainText(t("hex_note", n=len(r["head"])) + "\n\n" + _hexdump(r["head"]))
        # strings
        if r["strings"]:
            self.strings.setPlainText(t("strings_note", n=len(r["strings"])) + "\n\n" + "\n".join(r["strings"]))
        else:
            self.strings.setPlainText(t("no_strings"))

        self._show_result_widgets(True)

    # ------------------------------------------------------------------ actions
    def _open_link(self, row, col):
        item = self.details.item(row, 1)
        if item and item.text().startswith("http"):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(item.text()))

    def _copy_report(self):
        QtWidgets.QApplication.clipboard().setText(detector.report_text(self.result, t))
        self.status.setText(t("copied"))

    def _save_copy(self):
        r = self.result
        ext = next((e for e in r["expected_ext"] if e), "")
        stem = os.path.splitext(r["name"])[0] if r["extension"] else r["name"]
        suggested = os.path.join(os.path.dirname(r["path"]), f"{stem}.{ext}")
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(self, t("save_with_ext"), suggested)
        if not dest:
            return
        try:
            shutil.copy2(r["path"], dest)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, t("error"), str(e))
            return
        self.status.setText(t("saved_copy", path=dest))
        self.inspect(dest)

    def _add_to_map(self):
        if not self.add_layers or not self.result:
            return
        added, failed = self.add_layers(self.result["qgis_layers"])
        msg = t("added_to_map", n=added)
        if failed:
            msg += "  ·  " + "; ".join(t("add_failed", name=n) for n in failed)
        self.status.setText(msg)


def _hexdump(data, width=16):
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        hexpart = hexpart[:3 * 8] + " " + hexpart[3 * 8:]
        text = "".join(chr(b) if 32 <= b < 127 else "·" for b in chunk)
        lines.append(f"{off:08x}  {hexpart:<{3 * width + 1}}  {text}")
    return "\n".join(lines)
