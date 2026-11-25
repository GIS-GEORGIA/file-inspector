"""Light / dark themes and the automatic ("hybrid") mode that follows the
system or QGIS. In QGIS the style is applied to the panel only; the
standalone app also sets the application palette so dialogs match."""

from .qt_compat import Qt, QtGui, QtWidgets

MODES = ("auto", "light", "dark")

LIGHT = {
    "bg": "#f5f6f8", "surface": "#ffffff", "surface2": "#eef1f5", "border": "#d6dbe2",
    "text": "#1b2129", "muted": "#5d6878", "accent": "#1f6feb", "accent_hover": "#1859c4",
    "accent_text": "#ffffff", "warn_bg": "#fff5d9", "warn_border": "#f0c75e", "warn_text": "#6b4400",
    "ok": "#1a7f37", "selection": "#cfe0ff", "drop": "#f9fbff",
}
DARK = {
    "bg": "#15181d", "surface": "#1e232a", "surface2": "#262c35", "border": "#343c48",
    "text": "#e4e8ee", "muted": "#9aa6b6", "accent": "#4d8eff", "accent_hover": "#6ba1ff",
    "accent_text": "#0b1220", "warn_bg": "#3a2f13", "warn_border": "#8a6a1f", "warn_text": "#ffd98a",
    "ok": "#56d364", "selection": "#28406b", "drop": "#1a1f26",
}

# badge colour per category: (light, dark)
CATEGORY_COLORS = {
    "gis_vector": ("#1a7f37", "#3fb950"), "gis_raster": ("#9a6700", "#d29922"),
    "gis_other": ("#0969da", "#58a6ff"), "image": ("#8250df", "#bc8cff"),
    "document": ("#cf222e", "#ff7b72"), "spreadsheet": ("#116329", "#56d364"),
    "archive": ("#953800", "#f0883e"), "database": ("#0550ae", "#79c0ff"),
    "text": ("#57606a", "#8b949e"), "code": ("#6639ba", "#d2a8ff"),
    "audio": ("#bf3989", "#f778ba"), "video": ("#bf3989", "#f778ba"),
    "executable": ("#a40e26", "#ff6a69"), "font": ("#57606a", "#8b949e"),
    "security": ("#a40e26", "#ff6a69"), "unknown": ("#57606a", "#8b949e"),
}


def system_is_dark():
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    hints = QtGui.QGuiApplication.styleHints()
    if hasattr(hints, "colorScheme") and hasattr(Qt, "ColorScheme"):   # Qt >= 6.5
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    return app.palette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128


def resolve(mode):
    if mode == "dark":
        return True
    if mode == "light":
        return False
    return system_is_dark()


def colors(dark):
    return DARK if dark else LIGHT


def category_color(category, dark):
    pair = CATEGORY_COLORS.get(category, CATEGORY_COLORS["unknown"])
    return pair[1] if dark else pair[0]


def stylesheet(dark):
    c = colors(dark)
    return f"""
QWidget#FileInspectorRoot {{ background: {c['bg']}; color: {c['text']}; }}
QWidget#FileInspectorRoot QLabel {{ color: {c['text']}; background: transparent; }}
QLabel#Title {{ font-size: 17pt; font-weight: 600; }}
QLabel#Subtitle, QLabel#Muted, QLabel#Status {{ color: {c['muted']}; }}
QLabel#BigType {{ font-size: 15pt; font-weight: 600; }}
QLabel#Badge {{ border-radius: 9px; padding: 2px 10px; font-weight: 600; color: #ffffff; }}
QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame#DropZone {{ background: {c['drop']}; border: 2px dashed {c['border']}; border-radius: 12px; }}
QFrame#DropZone[active="true"] {{ border-color: {c['accent']}; background: {c['surface2']}; }}
QLabel#DropIcon {{ font-size: 26pt; color: {c['accent']}; }}
QFrame#Warn {{ background: {c['warn_bg']}; border: 1px solid {c['warn_border']}; border-radius: 8px; }}
QFrame#Warn QLabel {{ color: {c['warn_text']}; }}
QPushButton {{ background: {c['surface2']}; color: {c['text']}; border: 1px solid {c['border']};
               border-radius: 7px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QPushButton#Primary {{ background: {c['accent']}; color: {c['accent_text']}; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
QComboBox {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']};
             border-radius: 6px; padding: 4px 8px; min-width: 90px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['text']};
                               selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; background: {c['surface']}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 7px 14px; border: none;
                border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTableWidget, QPlainTextEdit {{ background: {c['surface']}; color: {c['text']}; border: none;
                                gridline-color: {c['border']}; selection-background-color: {c['selection']};
                                selection-color: {c['text']}; alternate-background-color: {c['surface2']}; }}
QHeaderView::section {{ background: {c['surface2']}; color: {c['muted']}; border: none;
                        border-bottom: 1px solid {c['border']}; padding: 5px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['surface2']}; border: none; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; border: none; width: 10px; height: 10px; }}
QScrollBar::handle {{ background: {c['border']}; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; }}
"""


def app_palette(dark):
    c = colors(dark)
    p = QtGui.QPalette()
    role = QtGui.QPalette.ColorRole
    for r, key in ((role.Window, "bg"), (role.WindowText, "text"), (role.Base, "surface"),
                   (role.AlternateBase, "surface2"), (role.Text, "text"), (role.Button, "surface2"),
                   (role.ButtonText, "text"), (role.ToolTipBase, "surface"), (role.ToolTipText, "text"),
                   (role.Highlight, "accent"), (role.HighlightedText, "accent_text"),
                   (role.PlaceholderText, "muted"), (role.Link, "accent")):
        p.setColor(r, QtGui.QColor(c[key]))
    disabled = QtGui.QPalette.ColorGroup.Disabled
    for r in (role.Text, role.ButtonText, role.WindowText):
        p.setColor(disabled, r, QtGui.QColor(c["muted"]))
    return p


_system_palette = None


def remember_system_palette():
    """Call once at start-up, before any palette is set, so that the automatic
    mode can still ask the platform whether it is dark."""
    global _system_palette
    app = QtWidgets.QApplication.instance()
    if app is not None and _system_palette is None:
        _system_palette = QtGui.QPalette(app.palette())


def apply(widget, mode, whole_app=False):
    """Style `widget` (and, for the standalone app, the whole application).
    Returns True when the dark theme was applied."""
    if whole_app:
        app = QtWidgets.QApplication.instance()
        if mode == "auto" and _system_palette is not None:
            # back to the platform palette before asking it whether it is dark
            app.setPalette(_system_palette)
        dark = resolve(mode)
        app.setPalette(app_palette(dark))
    else:
        dark = resolve(mode)
    widget.setStyleSheet(stylesheet(dark))
    return dark
