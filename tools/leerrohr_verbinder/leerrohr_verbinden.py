# -*- coding: utf-8 -*-
"""
Leerrohr-Verbinden – 2‑Stufen-Flow:
1) Knoten wählen -> Trassen am Knoten in listWidget_Leerohr1 (links), Rest in rechts
2) Links & rechts je eine Trasse wählen -> 'Auswahl bestätigen' -> Leerrohre je Modus
Danach: bestehender Ablauf (Balken/Verbinden/Import) wie gehabt.

Zusatz:
- pushButton_Knoten startet Knotenauswahl am Kartenfenster.
- checkBox_1 / checkBox_2 schalten Modus je Seite: parallel / lotrecht.
- Alte Karten-Pick-Variante (Über Karte wählen) bleibt nutzbar.
"""

import base64, json
from html import escape
from PyQt5.QtPrintSupport import QPrinter
from PyQt5.QtCore import Qt, QSettings, QPointF, QLineF, QObject, QEvent, QRectF, QSizeF, QMarginsF, QRect, pyqtSignal
from PyQt5.QtGui import ( 
    QPainter, QPixmap, QTextDocument, QFont, QPageLayout, QPageSize, 
    QPen, QBrush, QColor, QPolygonF, QPainterPath, QFontMetricsF, QIcon
)
from PyQt5.QtWidgets import (
    QDialog, QListWidgetItem, QGraphicsScene, QGraphicsRectItem, QGraphicsPolygonItem, QFileDialog,
    QGraphicsLineItem, QMenu, QAbstractItemView, QGraphicsPathItem, QGraphicsSimpleTextItem, QGraphicsItem
)
from qgis.core import QgsProject, QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsWkbTypes, QgsCoordinateTransform
from qgis.gui import QgsMapToolEmitPoint, QgsHighlight, QgsMapTool, QgsVertexMarker
import psycopg2
from . import resources_rc
from .leerrohr_verbinder_dialog import Ui_KabelVerlegungsToolDialogBase


# --------- klickbares Rohr-Kästchen ---------
class ClickableRect(QGraphicsRectItem):
    def __init__(self, x, y, size, side, bar_idx, lr_id, rohrnr, occupied, on_click,
                 prim_hex="#808080", prim_name="grau"):
        super().__init__(x, y, size, size)
        self.side = side            # 1 = links, 2 = rechts
        self.bar_idx = bar_idx      # Zeilenindex des Balkens
        self.lr_id = lr_id          # Leerrohr-ID
        self.rohrnr = rohrnr
        self.occupied = occupied    # global belegt (DB)
        self.used = False           # in aktueller Sitzung verbunden
        self.on_click = on_click
        self.prim_hex = prim_hex
        self.prim_name = prim_name
        self.setFlag(QGraphicsRectItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)

    def mousePressEvent(self, ev):
        if not self.occupied and not self.used and callable(self.on_click):
            self.on_click(self.side, self, self.rohrnr)
        super().mousePressEvent(ev)

from PyQt5.QtCore import Qt, pyqtSignal
from qgis.gui import QgsMapTool, QgsVertexMarker
from qgis.core import QgsGeometry, QgsProject, QgsCoordinateTransform

class _SplitPointPickTool(QgsMapTool):
    # Kompatibilitäts-Signal: existiert, damit alte disconnect()-Aufrufe nicht krachen
    canvasClicked = pyqtSignal()

    def __init__(self, canvas, lr_geom_map_crs: QgsGeometry, on_fix, forbidden_ranges=None):
        super().__init__(canvas)
        self.canvas = canvas
        self.lr_geom = lr_geom_map_crs
        self.on_fix = on_fix
        self.forbidden = list(forbidden_ranges or [])
        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.marker.setIconSize(12)
        self.marker.setPenWidth(2)
        self.marker.setColor(Qt.red)
        self.marker.hide()
        self.setCursor(Qt.CrossCursor)

    def _closest_on_lr(self, map_pt):
        try:
            return self.lr_geom.closestSegmentWithContext(map_pt)[1]
        except Exception:
            return map_pt

    def _length(self) -> float:
        try:
            return float(self.lr_geom.length() or 0.0)
        except Exception:
            return 0.0

    def _fraction01(self, map_pt) -> float:
        """
        0..1 entlang der Linie: Länge vom Start bis zum projizierten Punkt / Gesamtlänge
        """
        L = self._length()
        if L <= 0.0:
            return 0.0
        try:
            pt = self.lr_geom.closestSegmentWithContext(map_pt)[1]
            tmp = QgsGeometry(self.lr_geom)
            _ = tmp.splitGeometry([pt], False)
            l0 = float(tmp.length() or 0.0)
            return max(0.0, min(1.0, l0 / L))
        except Exception:
            return 0.0

    def _nearest_allowed_fraction(self, s: float) -> float:
        """
        Liegt s in einem verbotenen Intervall, auf den nächsten Rand klemmen.
        """
        if not self.forbidden:
            return s
        for a, b in self.forbidden:
            if a <= s <= b:
                # zum nahesten Rand springen
                return a if (s - a) <= (b - s) else b
        return s

    def _point_at_fraction(self, s: float):
        """
        Erzeugt Punkt auf der Linie bei s (0..1).
        """
        L = self._length()
        if L <= 0.0:
            return None
        try:
            d = max(0.0, min(L, s * L))
            ptg = self.lr_geom.interpolate(d)
            return ptg.asPoint() if ptg else None
        except Exception:
            return None

    def canvasMoveEvent(self, e):
        try:
            mp = self.toMapCoordinates(e.pos())
            snap_pt = self._closest_on_lr(mp)
            s = self._fraction01(snap_pt)
            s2 = self._nearest_allowed_fraction(s)
            if s2 != s:
                p = self._point_at_fraction(s2)
                if p is not None:
                    from qgis.core import QgsPointXY
                    snap_pt = QgsPointXY(p)
            self.marker.setCenter(snap_pt)
            self.marker.show()
        except Exception:
            pass

    def canvasPressEvent(self, e):
        if e.button() == Qt.LeftButton:
            mp = self.toMapCoordinates(e.pos())
            snap_pt = self._closest_on_lr(mp)
            s = self._fraction01(snap_pt)
            s2 = self._nearest_allowed_fraction(s)
            if s2 != s:
                p = self._point_at_fraction(s2)
                if p is not None:
                    from qgis.core import QgsPointXY
                    snap_pt = QgsPointXY(p)
            if callable(self.on_fix):
                self.on_fix(snap_pt)
            try:
                self.canvasClicked.emit()
            except Exception:
                pass
            self.canvas.unsetMapTool(self)
        elif e.button() == Qt.RightButton:
            self.canvas.unsetMapTool(self)

    def deactivate(self):
        super().deactivate()
        try:
            self.marker.hide()
        except Exception:
            pass

# --------- Linie mit Status (Kontextmenü) ---------
class ConnLine(QGraphicsLineItem):
    def __init__(self, line: QLineF, status_id, on_change_status, on_click=None, left_rect=None, right_rect=None):
        super().__init__(line)
        self.status_id = status_id
        self.on_change_status = on_change_status
        self.on_click = on_click
        self.left_rect = left_rect
        self.right_rect = right_rect
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsLineItem.ItemIsSelectable, True)

    def mousePressEvent(self, ev):
        # Linksklick: Auswahl/Hervorhebung
        if ev.button() == Qt.LeftButton and callable(self.on_click):
            self.on_click(self)
        super().mousePressEvent(ev)

    def contextMenuEvent(self, ev):
        tool = LeerrohrVerbindenTool.instance
        if not tool:
            return
        menu = QMenu()
        for sid, (name, _hex) in tool.status_lut.items():
            act = menu.addAction(name)
            act.setData(sid)
            if sid == self.status_id:
                act.setCheckable(True); act.setChecked(True)
        chosen = menu.exec_(ev.screenPos())
        if chosen:
            self.status_id = chosen.data()
            if callable(self.on_change_status):
                self.on_change_status(self)



class LeerrohrVerbindenTool(QDialog):
    instance = None

    VERBUND_FIELDS = ["VERBUND", "VERBUNDNUMMER", "VERBUND_NR", "VERBUNDNR", "VERBUND_ID"]

    # Layout-Konstanten
    SQ = 20                # Kantenlänge Kästchen
    GAP = 3                # Abstand zwischen Kästchen
    ROW_VSPACE = 44        # vertikaler Abstand zwischen Balken
    LEFT_MARGIN = 16
    RIGHT_MARGIN = 16

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.iface = iface
        self.ui = Ui_KabelVerlegungsToolDialogBase()
        self.ui.setupUi(self)

        css = """
        QListWidget::item:selected,
        QListWidget::item:selected:!active,
        QListView::item:selected,
        QListView::item:selected:!active {
            background: #3874F2;   /* Windows-Blue */
            color: white;
        }
        """
        self.ui.listWidget_Leerohr1.setStyleSheet(css)
        self.ui.listWidget_Leerohr2.setStyleSheet(css)
        LeerrohrVerbindenTool.instance = self

        # DB / Settings
        self.settings = QSettings("SiegeleCo", "ToolBox")
        self.db = None
        self.is_connected = False
        self._load_db()

        # Auswahl / Zustand
        self.map_tool = None
        self.highlight = None           # generisches Highlight (Leerrohre/Trassen)
        self.target_button = None       # 'lr1' | 'lr2'
        self.sel_lr1_list = []          # [{id, SUBTYP, SUBTYP_CHAR, VERBUND}, ...]
        self.sel_lr2_list = []
        self.sel_rect_left = None
        self.paired = []                # [(ClickableRect left, ClickableRect right, ConnLine)]
        self._wire_parallel_split_ui()

        # 2‑Stufen-Steuerung
        self.phase = "trassen"          # "trassen" -> "leerrohre"
        self.sel_node_id = None
        self.sel_tr_left = None         # Trassen-ID links
        self.sel_tr_right = None        # Trassen-ID rechts

        # Balken-Container
        self.left_bars = []
        self.right_bars = []

        # Szene
        self.scene = QGraphicsScene()
        self.ui.graphicsView_Auswahl_Rrohr1.setScene(self.scene)

        # ListWidgets: Mehrfachauswahl, kein Live-Redraw
        self.ui.listWidget_Leerohr1.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.ui.listWidget_Leerohr2.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # Signals: Karten-Pick & Bestätigen
        self.ui.pushButton_split1.clicked.connect(lambda: self.start_pick('lr1'))
        self.ui.pushButton_split2.clicked.connect(lambda: self.start_pick('lr2'))
        self.ui.pushButton_Auswahl.clicked.connect(self._on_confirm_click)  # <-- NEU: Phase-abhängig
        self.ui.pushButton_verbindung.clicked.connect(self.start_pick_relations)

        self.ui.pushButton_automatisch.clicked.connect(self.auto_pair_active)
        self.ui.pushButton_verbindung_loeschen.clicked.connect(self.clear_pairs)
        self.ui.pushButton_Datenpruefung.clicked.connect(self.run_check)
        self.ui.pushButton_Import.clicked.connect(self.import_pairs)
        self.ui.pushButton_PDF.clicked.connect(self.on_export_pdf_clicked)

        # Drop-downs nur für Auto/Löschen – keine Grafikbindung
        self.ui.comboBox_AktivLR1.currentIndexChanged.connect(lambda *_: self._status("Aktives Paar geändert."))
        self.ui.comboBox_AktivLR2.currentIndexChanged.connect(lambda *_: self._status("Aktives Paar geändert."))

        # Status-LUT + global
        self.status_lut = self._load_status_lut()
        self.default_status_id = next(iter(self.status_lut.keys()), 1)
        self._fill_status_global()

        # Buttons deaktivieren bis beide Seiten eine bestätigte Auswahl haben
        for bn in ("pushButton_automatisch","pushButton_verbindung_loeschen","pushButton_Datenpruefung","pushButton_Import"):
            getattr(self.ui, bn).setEnabled(False)

        # Redraw bei Größenänderung
        self.ui.graphicsView_Auswahl_Rrohr1.viewport().installEventFilter(self)

        self._reset_list_view()
        self._status("1) Regler setzen, 2) Knoten wählen, 3) Trassen links+rechts wählen, 4) 'Auswahl bestätigen'.")

        # Checkbox als Schieregler mit Text
        self.ui.checkBox_1.toggled.connect(lambda v: self._toggle_text(self.ui.checkBox_1, v, "parallel", "lotrecht"))
        self.ui.checkBox_2.toggled.connect(lambda v: self._toggle_text(self.ui.checkBox_2, v, "parallel", "lotrecht"))

        self.ui.checkBox_1.toggled.connect(self._on_mode_changed)
        self.ui.checkBox_2.toggled.connect(self._on_mode_changed)

        # Initial setzen
        self._toggle_text(self.ui.checkBox_1, self.ui.checkBox_1.isChecked(), "parallel", "lotrecht")
        self._toggle_text(self.ui.checkBox_2, self.ui.checkBox_2.isChecked(), "parallel", "lotrecht")

        # NEU: Knoten-Pick
        self.ui.pushButton_Knoten.clicked.connect(self.start_pick_node)
        self.ui.listWidget_Leerohr1.itemSelectionChanged.connect(self._on_left_trasse_chosen)

    # --- NEU: persistente DB-Verbindung + Cursor-Helfer ---
    def _ensure_conn(self):
        """Sorgt für eine langlebige psycopg2-Connection in self._conn."""
        if not (self.is_connected and self.db):
            self._conn = None
            return None
        try:
            if getattr(self, "_conn", None) is None or self._conn.closed:
                import psycopg2
                self._conn = psycopg2.connect(**self.db)
                self._conn.autocommit = False
            return self._conn
        except Exception:
            self._conn = None
            return None

    def _cursor(self):
        """Context-Manager für Cursor auf der persistenten Verbindung."""
        conn = self._ensure_conn()
        if conn is None:
            # Dummy-Kontextmanager
            from contextlib import contextmanager
            @contextmanager
            def _dummy():
                yield None
            return _dummy()
        return conn.cursor()

    def _close_conn(self):
        try:
            if getattr(self, "_conn", None):
                self._conn.close()
        except Exception:
            pass
        self._conn = None

    def _toggle_text(self, cb, checked, on_txt, off_txt):
        """Setzt den Text der Checkbox je nach Zustand."""
        if cb is None:
            return
        cb.setText(on_txt if checked else off_txt)

    # ---------- Event-Filter (Resize -> Redraw) ----------
    def eventFilter(self, obj: QObject, ev):
        if obj is self.ui.graphicsView_Auswahl_Rrohr1.viewport() and ev.type() == QEvent.Resize:
            # nur neu zeichnen, wenn es bereits eine bestätigte Auswahl gibt
            if self.sel_lr1_list or self.sel_lr2_list:
                self._draw_all()
        return super().eventFilter(obj, ev)

    # ---------- Helper ----------
    def _status(self, msg, ok=True):
        self.ui.label_Status.setText(msg)
        self.ui.label_Status.setStyleSheet("background-color: lightgreen;" if ok else "background-color: lightcoral;")

    def showEvent(self, ev):
        super().showEvent(ev); self.raise_(); self.activateWindow()

    def closeEvent(self, event):
        """Sicher schließen: Map-Tool & Marker räumen und Singleton freigeben."""
        try:
            canvas = self.iface.mapCanvas() if hasattr(self, "iface") else None
            tool = getattr(self, "map_tool", None)

            if tool is not None and hasattr(tool, "canvasClicked"):
                try:
                    tool.canvasClicked.disconnect()
                except Exception:
                    pass

            if canvas is not None and tool is not None:
                try:
                    canvas.unsetMapTool(tool)
                except Exception:
                    pass
            self.map_tool = None

            # Marker/RubberBands wegräumen
            for dname in ("split_markers",):
                d = getattr(self, dname, None)
                if isinstance(d, dict):
                    for mk in d.values():
                        try:
                            if mk: mk.hide()
                        except Exception:
                            pass
                    setattr(self, dname, {})

            for attr in ("result_rb", "tmp_rb"):
                rb = getattr(self, attr, None)
                if rb:
                    try: rb.reset()
                    except Exception: pass
                    setattr(self, attr, None)

            if hasattr(self, "highlights") and self.highlights:
                for h in self.highlights:
                    try: h.hide()
                    except Exception: pass
                self.highlights = []
        except Exception:
            pass

        # WICHTIG: Singleton freigeben
        try:
            type(self).instance = None
        except Exception:
            pass

        try:
            super().closeEvent(event)
        except Exception:
            pass

    def _load_db(self):
        u = self.settings.value("connection_username","")
        pw = self.settings.value("connection_password","")
        env = self.settings.value("connection_umgebung","Testumgebung")
        if not (u and pw and env):
            self._status("Kein Setup aktiv.", ok=False); return
        pwd = base64.b64decode(pw.encode()).decode() if pw else ""
        host = "172.30.0.4" if env=="Testumgebung" else "172.30.0.3"
        self.db = dict(dbname="qwc_services", user=u, password=pwd, host=host, port="5432", sslmode="disable")
        try:
            with psycopg2.connect(**self.db) as _c: pass
            self.is_connected = True
        except Exception as e:
            self._status(f"DB-Fehler: {e}", ok=False)

    def _load_status_lut(self):
        """
        Lädt die LUT lwl.LUT_Rohr_Status und baut:
        self.status_lut = { id: (STATUS-Name, FarbeHex) }
        Hinweis: In deiner LUT gibt es keine FARBE_HEX-Spalte -> Farben werden gemappt.
        """
        def _status_color_by_name(name: str) -> str:
            name = (name or "").strip().lower()
            mapping = {
                "geplant":    "#6e6e6e",  # grau
                "aktiv":      "#0a7d00",  # grün
                "belegt":     "#1f77b4",  # blau
                "beschädigt": "#d62728",  # rot
            }
            return mapping.get(name, "#000000")

        lut = {}
        if self.is_connected and self.db:
            try:
                with psycopg2.connect(**self.db) as conn, conn.cursor() as cur:
                    cur.execute('SELECT "id","STATUS","WERT","BESCHREIBUNG" FROM lwl."LUT_Rohr_Status" ORDER BY "id"')
                    for sid, status_txt, wert, beschr in cur.fetchall():
                        name = (status_txt or f"Status {sid}").strip()
                        hexcol = _status_color_by_name(name)
                        lut[int(sid)] = (name, hexcol)
            except Exception:
                pass

        if not lut:
            # Fallback falls DB nicht erreichbar ist
            lut = {
                1: ("geplant",    "#6e6e6e"),
                2: ("aktiv",      "#0a7d00"),
                3: ("belegt",     "#1f77b4"),
                4: ("beschädigt", "#d62728"),
            }
        return lut

    def _fill_status_global(self):
        """
        Befüllt das Dropdown mit STATUS-Text (Data = id) und setzt Default.
        Zusätzlich: Änderung wendet Status auf ALLE Linien an.
        """
        cb = self.ui.comboBox_StatusGlobal
        cb.blockSignals(True)
        cb.clear()

        for sid in sorted(self.status_lut.keys()):
            name, _hx = self.status_lut[sid]
            cb.addItem(name, sid)

        # Default 'aktiv' wenn vorhanden
        default_sid = None
        for sid, (name, _hx) in self.status_lut.items():
            if (name or "").strip().lower() == "aktiv":
                default_sid = sid; break
        if default_sid is not None:
            idx = cb.findData(default_sid)
            if idx >= 0: cb.setCurrentIndex(idx)
        elif cb.count() > 0:
            cb.setCurrentIndex(0)

        if cb.count() > 0:
            self.default_status_id = cb.currentData()

        cb.blockSignals(False)
        cb.currentIndexChanged.connect(self._on_status_global_combo)

    def _on_node_click_for_relations(self, pt):
        """Knoten bestimmen und LR↔LR-Verbindungen dieses Knotens in listWidget_Verbindungen anzeigen."""
        node_id = self._find_nearest_node(pt)
        if not node_id:
            self._status("Kein Knoten gefunden.", ok=False); return
        self.sel_node_id = node_id
        self.phase = "verbindungen"

        rels = self._load_relations_for_node(node_id)
        if not rels:
            self._status(f"Knoten {node_id}: keine Leerrohr-Verbindungen gefunden.", ok=False)
        else:
            self._status(f"Knoten {node_id}: {len(rels)} Leerrohr-Verbindungen gefunden. Auswahl treffen und bestätigen.")

        # nur im Verbindungs-Widget anzeigen (Mehrfachauswahl)
        self._fill_listwidget_verbindungen(rels)

        # Maptool aus
        try:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
        finally:
            self.map_tool = None

        # Maptool aus
        try:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
        finally:
            self.map_tool = None

    def _fill_listwidget_verbindungen(self, relations_lr_lr):
        """
        listWidget_Verbindungen für die Auswahl der LR↔LR-Verbindungen befüllen (Mehrfachauswahl).
        """
        lw = self.ui.listWidget_Verbindungen
        lw.clear()
        lw.setSelectionMode(QAbstractItemView.ExtendedSelection)

        head = QListWidgetItem("Leerrohr-Verbindungen am Knoten (Mehrfachauswahl möglich):")
        head.setFlags(head.flags() & ~Qt.ItemIsSelectable)
        lw.addItem(head)

        for d in relations_lr_lr:
            sid = d.get("status_id", self.default_status_id)
            sname = self.status_lut.get(sid, ("", ""))[0]
            txt = f'LR{d["lr_left"]} ↔ LR{d["lr_right"]} [{sname}]'
            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, d)
            lw.addItem(it)

    def _load_relations_for_node(self, node_id):
        """
        LR↔LR-Verbindungen am Knoten laden.
        Rückgabe: Liste von Dicts: {'kind':'lrrel','lr_left', 'lr_right', 'status_id'}
        Seitenzuordnung stabil nach klein/groß der LR-ID.
        """
        out = []
        if not (self.is_connected and self.db):
            return out
        try:
            with psycopg2.connect(**self.db) as conn, conn.cursor() as cur:
                # 1) primär über ID_KNOTEN
                cur.execute("""
                    SELECT "ID_LEERROHR_1","ID_LEERROHR_2","STATUS"
                    FROM lwl."LWL_Leerrohr_Leerrohr_rel"
                    WHERE "ID_KNOTEN" = %s
                """, (int(node_id),))
                rows = cur.fetchall()

                # 2) Fallback: beide Leerrohre liegen an diesem Knoten (VON/NACH)
                if not rows:
                    cur.execute("""
                        SELECT r."ID_LEERROHR_1", r."ID_LEERROHR_2", r."STATUS"
                        FROM lwl."LWL_Leerrohr_Leerrohr_rel" r
                        JOIN lwl."LWL_Leerrohr" a ON a.id = r."ID_LEERROHR_1"
                        JOIN lwl."LWL_Leerrohr" b ON b.id = r."ID_LEERROHR_2"
                        WHERE %s IN (a."VONKNOTEN", a."NACHKNOTEN")
                        AND %s IN (b."VONKNOTEN", b."NACHKNOTEN")
                    """, (int(node_id), int(node_id)))
                    rows = cur.fetchall()

            for lr1, lr2, sid in rows:
                if lr1 is None or lr2 is None:
                    continue
                L = int(lr1); R = int(lr2)
                if L > R: L, R = R, L
                out.append({
                    "kind": "lrrel",
                    "lr_left": L,
                    "lr_right": R,
                    "status_id": int(sid) if sid is not None else self.default_status_id
                })
        except Exception:
            pass
        return out

    def _fill_list_relations(self, lw, relations):
        lw.clear()
        head = QListWidgetItem("Bestehende Verbindungen am Knoten:")
        head.setFlags(head.flags() & ~Qt.ItemIsSelectable)
        lw.addItem(head)

        for d in relations:
            sid = d.get("status_id", self.default_status_id)
            sname = self.status_lut.get(sid, ("", ""))[0]
            txt = f'LR{d["lr_left"]}/R{d["nr_left"]} → LR{d["lr_right"]}/R{d["nr_right"]} [{sname}]'
            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, d)
            lw.addItem(it)

    def _wire_parallel_split_ui(self):
        """Aktiviert Split-Buttons nur wenn Seite im Parallel-Modus ist; startet Split-Pick."""
        # Buttons initial aus
        self.ui.pushButton_split1.setEnabled(False)
        self.ui.pushButton_split2.setEnabled(False)

        # Zustände merken
        self.split_points = {"left": None, "right": None}   # QgsPointXY in Map-CRS
        self.split_markers = {"left": None, "right": None}  # QgsVertexMarker

        # Modus-Änderungen verfolgen
        def _update():
            self.ui.pushButton_split1.setEnabled(self._is_parallel_side(1))
            self.ui.pushButton_split2.setEnabled(self._is_parallel_side(2))
        self.ui.checkBox_1.stateChanged.connect(_update)
        self.ui.checkBox_2.stateChanged.connect(_update)
        _update()

        # Clicks
        self.ui.pushButton_split1.clicked.connect(lambda: self._start_split_pick(side=1))
        self.ui.pushButton_split2.clicked.connect(lambda: self._start_split_pick(side=2))

    def _start_split_pick(self, side: int):
        """
        Startet die Splitpunkt-Erfassung entlang des aktuell in comboBox_AktivLR[1|2] gewählten Leerrohrs.
        - rotes Kreuz gleitet entlang der LR-Geometrie (Map-CRS)
        - Linksklick fixiert den Punkt
        - verbotene Zonen: ±0,10 m um HE-Andockpunkte werden automatisch ausgelassen
        """
        # aktives LR ermitteln
        lr_id = None
        try:
            cb = self.ui.comboBox_AktivLR1 if side == 1 else self.ui.comboBox_AktivLR2
            data = cb.currentData() if cb else None
            if isinstance(data, dict) and "id" in data:
                lr_id = int(data["id"])
            elif isinstance(data, int):
                lr_id = data
        except Exception:
            lr_id = None

        if not lr_id:
            self._status("Kein aktives Leerrohr ausgewählt.", ok=False)
            return

        # LR-Geometrie (Layer-CRS -> Map-CRS)
        lr_layer = self._get_layer("LWL_Leerrohr")
        if not lr_layer:
            self._status("Layer 'LWL_Leerrohr' nicht gefunden.", ok=False)
            return
        from qgis.core import QgsFeatureRequest, QgsGeometry, QgsCoordinateTransform, QgsProject
        feat = next((f for f in lr_layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {lr_id}'))), None)
        if not feat or not feat.geometry():
            self._status("Leerrohr-Geometrie nicht gefunden.", ok=False)
            return

        try:
            g = QgsGeometry(feat.geometry())
            tr = QgsCoordinateTransform(lr_layer.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance())
            _ = g.transform(tr)
        except Exception as e:
            self._status(f"CRS-Transformation fehlgeschlagen: {e}", ok=False)
            return

        # Verbotsintervalle berechnen (± 0,10 m um HE-Andockpunkte)
        forbidden = []
        try:
            forbidden = self._compute_forbidden_ranges_for_he(lr_id, g) or []
        except Exception:
            forbidden = []

        # Fix-Callback
        def _fix(pt_map_xy):
            # Prozent (0..100) für UI-Feedback berechnen
            perc = self._project_fraction(g, pt_map_xy)  # 0..100
            key = "left" if side == 1 else "right"
            pos01 = max(0.0, min(1.0, (perc / 100.0)))
            self.split_position[key] = pos01
            # Label aktualisieren
            label = self.ui.label_gewaehltes_leerrohr1 if side == 1 else self.ui.label_gewaehltes_leerrohr2
            try:
                label.setText(f"Split @ {perc:.1f}% von {self._format_lr_label(lr_id)}")
                label.setStyleSheet("background-color: lightgreen;")
            except Exception:
                pass
            self._status("Splitpunkt gesetzt.")

        # MapTool starten (mit verbotenen Intervallen)
        canvas = self.iface.mapCanvas()
        self.map_tool = _SplitPointPickTool(canvas, g, _fix, forbidden_ranges=forbidden)
        canvas.setMapTool(self.map_tool)
        self._status("Bewege das rote Kreuz entlang des Leerrohrs. Linksklick fixiert den Splitpunkt.")

    def _project_fraction(self, line_geom_map_crs: QgsGeometry, pt_map_xy) -> float:
        """
        projiziert Punkt auf Liniengeometrie und liefert 0..100 (%) entlang.
        """
        try:
            res = line_geom_map_crs.closestSegmentWithContext(pt_map_xy)
            snap_pt = res[1]
            # Länge akkumulieren
            l_total = line_geom_map_crs.length()
            if l_total <= 0:
                return 0.0
            # Vom Start bis Snap messen: trick über splitAtPoint
            tmp = QgsGeometry(line_geom_map_crs)
            parts = tmp.splitGeometry([snap_pt], False)
            # splitGeometry gibt (ok, [Geoms]) je nach Version; fallback:
            g0 = tmp  # vor Split verbleibende Teilgeometrie
            l0 = g0.length() if g0 else 0.0
            # Sicherheit: clamp
            frac = max(0.0, min(1.0, l0 / l_total))
            return 100.0 * frac
        except Exception:
            return 0.0

    # =====================================================================
    # KARTEN-AUSWAHL – LEERROHR (alte Variante bleibt verfügbar)
    # =====================================================================
    def start_pick_relations(self):
        """Kompatibilität: Button 'Verbindung wählen' soll LR↔LR in listWidget_Leerohr1_2 laden."""
        return self.start_pick_lr_relations()

    def start_pick(self, side):
        """
        Kompatibilitäts-Wrapper für alte Aufrufe (z. B. Buttons, die noch start_pick('lr1')/('lr2') nutzen).
        Nutzt den neuen Split-Pick-Flow und entfernt die alte canvasClicked-Signal-Logik vollständig.
        side: 'lr1'/'lr2' oder 1/2
        """
        try:
            # Seite normalisieren
            if isinstance(side, str):
                s = 1 if side.lower() in ('lr1', 'left', 'l', '1') else 2
            else:
                s = int(side)

            # Kein (Dis)connect von canvasClicked mehr – der neue MapTool nutzt Events.
            self._start_split_pick(s)
        except Exception as e:
            self._status(f"Split-Pick konnte nicht gestartet werden: {e}", ok=False)

    def _find_verbund_field(self, layer):
        names = [f.name() for f in layer.fields()]
        for c in self.VERBUND_FIELDS:
            if c in names: return c
        return names[0] if names else "id"

    def _get_subtyp_char(self, subtyp_id):
        """Liefert Bezeichnung zum Subtyp; robust bzgl. Spaltennamen + kleiner Cache."""
        if not (self.is_connected and self.db and subtyp_id):
            return str(subtyp_id)
        sid = int(subtyp_id)
        # kleiner Cache
        if not hasattr(self, "_subtyp_cache"):
            self._subtyp_cache = {}
        if sid in self._subtyp_cache:
            return self._subtyp_cache[sid]

        label = None
        try:
            with psycopg2.connect(**self.db) as conn, conn.cursor() as cur:
                # versuche beide Schreibweisen + Fallbacks
                for col in ("SUBTYP_char", "SUBTYP_CHAR", "BEZEICHNUNG", "NAME"):
                    try:
                        cur.execute(f'SELECT "{col}" FROM lwl."LUT_Leerrohr_SubTyp" WHERE "id"=%s', (sid,))
                        row = cur.fetchone()
                        if row and row[0]:
                            label = row[0]
                            break
                    except Exception:
                        continue
        except Exception:
            pass

        if label is None:
            label = str(subtyp_id)
        self._subtyp_cache[sid] = label
        return label

    def _on_canvas_click(self, pt):
        layer_list = QgsProject.instance().mapLayersByName("LWL_Leerrohr")
        if not layer_list:
            self._status("Layer 'LWL_Leerrohr' nicht gefunden.", ok=False); return
        layer = layer_list[0]

        mup = self.iface.mapCanvas().mapUnitsPerPixel(); radius = 10*mup
        rect = QgsGeometry.fromPointXY(QgsPointXY(pt)).buffer(radius,1).boundingBox()
        feats = list(layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)))
        if not feats:
            self._status("Kein Leerrohr im Toleranzbereich.", ok=False); return

        verb_field = self._find_verbund_field(layer)
        lw = self.ui.listWidget_Leerohr1 if self.target_button=='lr1' else self.ui.listWidget_Leerohr2
        lab = self.ui.label_gewaehltes_leerrohr1 if self.target_button=='lr1' else self.ui.label_gewaehltes_leerrohr2

        lw.clear()
        for f in feats:
            verb = f[verb_field] if verb_field in f.fields().names() else None
            label_subtyp = self._get_subtyp_char(f["SUBTYP"])
            it = QListWidgetItem(f"{label_subtyp}; Verbund {verb if verb is not None else '-'}")
            it.setData(Qt.UserRole, f)
            lw.addItem(it)

        lab.setText("Auswahl in Liste treffen → 'Auswahl bestätigen' klicken")
        lab.setStyleSheet("background-color: khaki;")

        # Highlight + Maptool zurücksetzen
        if self.highlight: self.highlight.hide(); self.highlight=None
        self.highlight = QgsHighlight(self.iface.mapCanvas(), feats[0].geometry(), layer)
        self.highlight.setColor(Qt.yellow); self.highlight.setWidth(3); self.highlight.show()

        self.iface.mapCanvas().unsetMapTool(self.map_tool); self.map_tool=None

    # =====================================================================
    # NEU: 2‑STUFEN-FLOW – KNOTEN → TRASSEN → LEERROHRE
    # =====================================================================
    def start_pick_node(self):
        """Knoten am Kartenfenster wählen."""
        self._status("Karte klicken → Knoten wählen.")
        self.phase = "trassen"
        self.sel_tr_left = None; self.sel_tr_right = None
        if self.map_tool:
            try: self.map_tool.canvasClicked.disconnect()
            except TypeError: pass
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self._on_node_click_for_trassen)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def _on_node_click_for_trassen(self, pt):
        node_id = self._find_nearest_node(pt)
        if not node_id:
            self._status("Kein Knoten gefunden.", ok=False); return
        self.sel_node_id = node_id
        self._status(f"Knoten {node_id} gewählt. Links Trasse wählen, dann rechts.")

        # Trassen am Knoten laden
        trassen = self._load_trassen_for_node(node_id)  # Liste (id, label, geom)
        # Links alle Trassen, rechts leer
        self._fill_list_trassen(self.ui.listWidget_Leerohr1, trassen)
        self.ui.listWidget_Leerohr2.clear()

        # Highlight ALLER Arme am Knoten
        if self.highlight: self.highlight.hide(); self.highlight=None
        tr_layer = self._get_layer("LWL_Trasse")
        if tr_layer and trassen:
            try:
                # Mache Multi-Geom (vereinigt)
                geoms = [g for _,_,g in trassen if g is not None]
                if geoms:
                    multi = geoms[0]
                    for gg in geoms[1:]:
                        try:
                            multi = multi.combine(gg)
                        except Exception:
                            pass
                    self.highlight = QgsHighlight(self.iface.mapCanvas(), multi, tr_layer)
                    self.highlight.setColor(Qt.yellow); self.highlight.setWidth(3); self.highlight.show()
            except Exception:
                pass

        # Maptool aus
        self.iface.mapCanvas().unsetMapTool(self.map_tool); self.map_tool=None

    def _get_layer(self, name):
        lst = QgsProject.instance().mapLayersByName(name)
        return lst[0] if lst else None

    def _find_nearest_node(self, pt):
        """Findet Knoten-ID: bevorzugt aus 'LWL_Knoten', sonst via Trassen-Endpunkte."""
        # 1) Direkter Treffer in LWL_Knoten
        kn_layer = self._get_layer("LWL_Knoten")
        if kn_layer:
            mup = self.iface.mapCanvas().mapUnitsPerPixel(); radius = 10*mup
            rect = QgsGeometry.fromPointXY(QgsPointXY(pt)).buffer(radius,1).boundingBox()
            for f in kn_layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                fid_name = "id" if "id" in f.fields().names() else f.fields().names()[0]
                val = f[fid_name]
                return int(val) if val not in (None,"") else None

        # 2) Nächster Trassen-Endpunkt
        tr_layer = self._get_layer("LWL_Trasse")
        if not tr_layer:
            return None
        mup = self.iface.mapCanvas().mapUnitsPerPixel(); radius = 15*mup
        bb = QgsGeometry.fromPointXY(QgsPointXY(pt)).buffer(radius, 1).boundingBox()
        nearest = None
        for f in tr_layer.getFeatures(QgsFeatureRequest().setFilterRect(bb)):
            names = f.fields().names()
            from_nm = "VONKNOTEN" if "VONKNOTEN" in names else ("FROMNODE" if "FROMNODE" in names else None)
            to_nm   = "NACHKNOTEN" if "NACHKNOTEN" in names else ("TONODE" if "TONODE" in names else None)
            if not (from_nm and to_nm): continue
            # prüfe Distanz zum Linienende
            geom = f.geometry()
            if not geom or geom.isEmpty(): continue
            try:
                line = geom.constGet()  # QgsLineString/QgsGeometry intern
            except Exception:
                line = None
            if line:
                start = QgsPointXY(line.startPoint())
                end   = QgsPointXY(line.endPoint())
                pxy   = QgsPointXY(pt)
                d1 = (pxy.x()-start.x())**2 + (pxy.y()-start.y())**2
                d2 = (pxy.x()-end.x())**2 + (pxy.y()-end.y())**2
                if nearest is None or d1 < nearest[0]:
                    nearest = (d1, int(f[from_nm]))
                if nearest is None or d2 < nearest[0]:
                    nearest = (d2, int(f[to_nm]))
        return nearest[1] if nearest else None

    def _load_trassen_for_node(self, node_id):
        """Lädt Trassen-Features, die am Knoten beginnen/enden. Rückgabe: [(id,label,geom), ...]"""
        out = []
        tr_layer = self._get_layer("LWL_Trasse")
        if not tr_layer:
            return out
        names = [f.name() for f in tr_layer.fields()]
        from_nm = "VONKNOTEN" if "VONKNOTEN" in names else ("FROMNODE" if "FROMNODE" in names else None)
        to_nm   = "NACHKNOTEN" if "NACHKNOTEN" in names else ("TONODE" if "TONODE" in names else None)
        id_nm   = "id" if "id" in names else names[0] if names else "id"

        if not (from_nm and to_nm):
            return out

        expr = f'"{from_nm}" = {node_id} OR "{to_nm}" = {node_id}'
        for f in tr_layer.getFeatures(QgsFeatureRequest().setFilterExpression(expr)):
            tid = int(f[id_nm]) if f[id_nm] not in (None,"") else None
            if tid is None: continue
            dir_txt = "→" if f[from_nm] == node_id else "←"
            other = int(f[to_nm]) if f[from_nm]==node_id else int(f[from_nm])
            label = f"Trasse {tid} {dir_txt} Knoten {other}"
            out.append((tid, label, f.geometry()))
        # stabile Sortierung nach ID
        out.sort(key=lambda t: t[0])
        return out

    def _fill_list_trassen(self, lw, trassen):
        lw.clear()
        for t in trassen:
            # t kann id, tuple, dict sein
            if isinstance(t, tuple):
                tid, label, _ = t
            elif isinstance(t, dict):
                tid = t.get("id") or t.get("lr_id") or t.get("trasse_id") or t.get("ID")
                label = t.get("label") or t.get("name") or f"Trasse {tid}"
            else:
                tid = t
                label = f"Trasse {t}"
            if tid is None:
                continue
            it = QListWidgetItem(str(label))
            try:
                it.setData(Qt.UserRole, int(tid))
            except Exception:
                # Fallback: speichere dict, aber klick-handler kann's lesen
                it.setData(Qt.UserRole, {"id": tid})
            lw.addItem(it)

    def _on_left_trasse_chosen(self):
        """Links gewählt → rechts verbleibende anzeigen (robust gegen dict im UserRole)."""
        if self.phase != "trassen":
            return
        it = self.ui.listWidget_Leerohr1.currentItem()
        if not it:
            return

        sel_raw = it.data(Qt.UserRole)
        sel_id = self._extract_userrole_id(sel_raw)
        if sel_id is None:
            # freundlich abbrechen, keine Exception mehr
            self.iface.messageBar().pushWarning("Leerrohr verbinden", "Keine gültige ID links ermittelbar.")
            return

        self.sel_tr_left = int(sel_id)

        # rechte Liste = alle linken außer der gewählten
        left_ids = []
        for i in range(self.ui.listWidget_Leerohr1.count()):
            raw = self.ui.listWidget_Leerohr1.item(i).data(Qt.UserRole)
            rid = self._extract_userrole_id(raw)
            if rid is not None:
                left_ids.append(int(rid))

        rest = [tid for tid in left_ids if tid != self.sel_tr_left]

        # vorhandene Füllroutine wiederverwenden
        self._fill_list_trassen(self.ui.listWidget_Leerohr2, rest)

    def _on_right_trasse_chosen(self):
        """Rechts gewählt (symmetrisch, robust)."""
        if self.phase != "trassen":
            return
        it = self.ui.listWidget_Leerohr2.currentItem()
        if not it:
            return

        sel_raw = it.data(Qt.UserRole)
        sel_id = self._extract_userrole_id(sel_raw)
        if sel_id is None:
            self.iface.messageBar().pushWarning("Leerrohr verbinden", "Keine gültige ID rechts ermittelbar.")
            return

        self.sel_tr_right = int(sel_id)

        # hier ggf. deine bestehende Weiterlogik aufrufen (unverändert)
        try:
            self._after_both_trassen_selected()
        except Exception:
            pass

    def start_pick_lr_relations(self):
        """Map-Pick: bestehende Leerrohr↔Leerrohr-Verbindungen (LWL_Leerrohr_Leerrohr_rel) am Knoten laden."""
        self._status("Karte klicken → Knoten für Leerrohr-Verbindungen wählen.")
        self.phase = "lr_lr"
        if self.map_tool:
            try: self.map_tool.canvasClicked.disconnect()
            except TypeError: pass
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self._on_node_click_for_lr_relations)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def _on_node_click_for_lr_relations(self, pt):
        """Knoten bestimmen und LR↔LR-Verbindungen dieses Knotens in listWidget_Leerohr1_2 anzeigen."""
        node_id = self._find_nearest_node(pt)
        if not node_id:
            self._status("Kein Knoten gefunden.", ok=False); return
        self.sel_node_id = node_id

        rels = self._load_lr_relations_for_node(node_id)
        if not rels:
            self._status(f"Knoten {node_id}: keine Leerrohr-Verbindungen gefunden.", ok=False)
        else:
            self._status(f"Knoten {node_id}: {len(rels)} Leerrohr-Verbindungen gefunden. Auswahl treffen und bestätigen.")

        # >>> WICHTIG: Ergebnis in listWidget_Leerohr1_2, NICHT in listWidget_Verbindungen!
        self._fill_listwidget_leerrohr1_2(rels)

        try:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
        finally:
            self.map_tool = None

    def _load_lr_relations_for_node(self, node_id):
        """
        LR↔LR-Verbindungen am Knoten laden.
        Rückgabe: Liste Dicts: {'kind':'lrrel','lr_left','lr_right','status_id'} (left<right stabilisiert).
        """
        out = []
        if not (self.is_connected and self.db):
            return out
        try:
            with psycopg2.connect(**self.db) as conn, conn.cursor() as cur:
                # 1) bevorzugt über ID_KNOTEN
                cur.execute("""
                    SELECT "ID_LEERROHR_1","ID_LEERROHR_2","STATUS"
                    FROM lwl."LWL_Leerrohr_Leerrohr_rel"
                    WHERE "ID_KNOTEN" = %s
                """, (int(node_id),))
                rows = cur.fetchall()

                # 2) Fallback: beide Leerrohre führen am Knoten vorbei (VON/NACH)
                if not rows:
                    cur.execute("""
                        SELECT r."ID_LEERROHR_1", r."ID_LEERROHR_2", r."STATUS"
                        FROM lwl."LWL_Leerrohr_Leerrohr_rel" r
                        JOIN lwl."LWL_Leerrohr" a ON a.id = r."ID_LEERROHR_1"
                        JOIN lwl."LWL_Leerrohr" b ON b.id = r."ID_LEERROHR_2"
                        WHERE %s IN (a."VONKNOTEN", a."NACHKNOTEN")
                        AND %s IN (b."VONKNOTEN", b."NACHKNOTEN")
                    """, (int(node_id), int(node_id)))
                    rows = cur.fetchall()

            for lr1, lr2, sid in rows:
                if lr1 is None or lr2 is None: continue
                L, R = int(lr1), int(lr2)
                if L > R: L, R = R, L
                out.append({
                    "kind": "lrrel",
                    "lr_left": L,
                    "lr_right": R,
                    "status_id": int(sid) if sid is not None else self.default_status_id
                })
        except Exception:
            pass

        # Dedupe
        seen=set(); uniq=[]
        for d in out:
            k=(d["lr_left"], d["lr_right"])
            if k in seen: continue
            seen.add(k); uniq.append(d)
        return uniq

    def _fill_listwidget_leerrohr1_2(self, relations_lr_lr):
        """listWidget_Leerohr1_2 mit LR↔LR-Verbindungen befüllen (Mehrfachauswahl) – mit formatierten LR-Labels."""
        lw = self.ui.listWidget_Leerohr1_2
        lw.clear()
        lw.setSelectionMode(QAbstractItemView.ExtendedSelection)

        head = QListWidgetItem("Leerrohr-Verbindungen am Knoten (Mehrfachauswahl):")
        head.setFlags(head.flags() & ~Qt.ItemIsSelectable)
        lw.addItem(head)

        for d in relations_lr_lr:
            sid = d.get("status_id", self.default_status_id)
            sname = self.status_lut.get(sid, ("", ""))[0]

            l_lbl = self._format_lr_label(d["lr_left"])
            r_lbl = self._format_lr_label(d["lr_right"])
            txt = f'{l_lbl} ↔ {r_lbl} [{sname}]'

            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, d)  # Dict bleibt unverändert für weitere Verarbeitung
            lw.addItem(it)

    def _on_confirm_click(self):
        """
        Bestätigen:
        - 'lr_lr' ODER 'verbindungen' → Auswahl aus listWidget_Leerohr1_2 übernehmen (LR↔LR)
        - 'trassen'                   → Trassen→Leerrohre (bestehender Flow)
        - 'leerrohre'                 → on_apply_selection()
        """
        # A) LR↔LR-Auswahl (robust: akzeptiere 'lr_lr' und 'verbindungen')
        if getattr(self, "phase", None) in ("lr_lr", "verbindungen"):
            lw = self.ui.listWidget_Leerohr1_2
            sel = [it.data(Qt.UserRole) for it in lw.selectedItems() if isinstance(it.data(Qt.UserRole), dict)]
            sel = [d for d in sel if d.get("kind") == "lrrel"]
            if not sel:
                self._status("Bitte mindestens eine Leerrohr-Verbindung wählen.", ok=False); return
            self._build_from_lr_rel_selection(sel)
            return

        # B) Trassen-Phase (wie gehabt)
        if getattr(self, "phase", None) == "trassen":
            it_r = self.ui.listWidget_Leerohr2.currentItem()
            if not (self.sel_tr_left and it_r):
                self._status("Bitte links & rechts je eine Trasse wählen.", ok=False); return
            self.sel_tr_right = int(it_r.data(Qt.UserRole))

            mode_left  = "parallel" if self.ui.checkBox_1.isChecked() else "lotrecht"
            mode_right = "parallel" if self.ui.checkBox_2.isChecked() else "lotrecht"

            lr_left  = self._leerrohre_for_trasse_and_mode(self.sel_tr_left,  side="left",  mode=mode_left)
            lr_right = self._leerrohre_for_trasse_and_mode(self.sel_tr_right, side="right", mode=mode_right)
            lr_left  = self._dedupe_feats_by_id(lr_left)
            lr_right = self._dedupe_feats_by_id(lr_right)
            lr_left, lr_right = self._dedupe_across_parallel_sides(lr_left, lr_right, mode_left, mode_right)

            self.scene.clear(); self.paired.clear(); self.sel_rect_left = None
            self.left_bars, self.right_bars = [], []
            self._fill_listwidget_from_feats(self.ui.listWidget_Leerohr1, lr_left)
            self._fill_listwidget_from_feats(self.ui.listWidget_Leerohr2, lr_right)

            for bn in ("pushButton_automatisch","pushButton_verbindung_loeschen","pushButton_Datenpruefung","pushButton_Import"):
                getattr(self.ui, bn).setEnabled(False)

            self.phase = "leerrohre"
            self._status("Trassen bestätigt. Jetzt Leerrohre wählen → 'Auswahl bestätigen'.")
            return

        # C) Leerrohre-Phase (wie gehabt)
        self.on_apply_selection()

    def _build_from_lr_rel_selection(self, rel_items):
        """
        Ausgewählte LR↔LR-Verbindungen übernehmen:
        - Links/rechts: involvierte Leerrohre als Bars zeichnen
        - Combos/Labels befüllen
        - listWidget_Leerohr1 / listWidget_Leerohr2 entsprechend zeigen
        - bestehende Rohr↔Rohr-Linien automatisch dazuladen
        """
        # 1) IDs links/rechts
        left_ids, right_ids = [], []
        for d in rel_items:
            L, R = int(d["lr_left"]), int(d["lr_right"])
            if L not in left_ids: left_ids.append(L)
            if R not in right_ids: right_ids.append(R)

        # 2) LR-Infos laden
        def _fetch_lr_info(lr_id):
            layer = self._get_layer("LWL_Leerrohr")
            if not layer:
                return {"id": lr_id, "SUBTYP": None, "SUBTYP_CHAR": str(lr_id), "VERBUND": None}
            f = next(layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id"={int(lr_id)}')), None)
            if not f:
                return {"id": lr_id, "SUBTYP": None, "SUBTYP_CHAR": str(lr_id), "VERBUND": None}
            verb_field = self._find_verbund_field(layer)
            names = f.fields().names()
            sub = f["SUBTYP"] if "SUBTYP" in names else None
            return {"id": int(lr_id), "SUBTYP": sub, "SUBTYP_CHAR": self._get_subtyp_char(sub),
                    "VERBUND": f[verb_field] if verb_field in names else None,
                    "VONKNOTEN": f["VONKNOTEN"] if "VONKNOTEN" in names else (f["FROMNODE"] if "FROMNODE" in names else None),
                    "NACHKNOTEN": f["NACHKNOTEN"] if "NACHKNOTEN" in names else (f["TONODE"] if "TONODE" in names else None)}

        self.sel_lr1_list = [_fetch_lr_info(x) for x in left_ids]
        self.sel_lr2_list = [_fetch_lr_info(x) for x in right_ids]

        # 3) Combos/Labels
        self._fill_combo_from_list('lr1', self.sel_lr1_list)
        self._fill_combo_from_list('lr2', self.sel_lr2_list)
        self._set_sel_label('lr1', len(self.sel_lr1_list))
        self._set_sel_label('lr2', len(self.sel_lr2_list))

        # 4) listWidgets links/rechts passend zeigen (nur Info/Bestätigung für den Nutzer)
        self._fill_listwidget_from_feats(self.ui.listWidget_Leerohr1, self.sel_lr1_list)
        self._fill_listwidget_from_feats(self.ui.listWidget_Leerohr2, self.sel_lr2_list)

        # 5) Zeichenfläche aufbauen
        self.scene.clear(); self.paired.clear(); self.sel_rect_left = None
        self.left_bars, self.right_bars = [], []
        self._warm_caches()
        self._draw_all()
        self._draw_existing_relations()

        # 6) Buttons frei
        for bn in ("pushButton_automatisch","pushButton_verbindung_loeschen","pushButton_Datenpruefung","pushButton_Import"):
            getattr(self.ui, bn).setEnabled(True)

        self.phase = "leerrohre"
        self._status(f"{len(rel_items)} LR-Verbindung(en) übernommen. Jetzt Röhrchen verbinden oder Import/Update.")

    def _is_parallel_side(self, side: int) -> bool:
        """Side: 1=links, 2=rechts."""
        if side == 1:
            return self.ui.checkBox_1.isChecked()
        return self.ui.checkBox_2.isChecked()

    def _fill_listwidget_from_feats(self, lw, feats_like):
        """Füllt das ListWidget ohne Duplikate (pro Liste)."""
        feats_like = self._dedupe_feats_by_id(feats_like or [])
        lw.clear()
        for d in feats_like:
            label = f'{d.get("SUBTYP_CHAR", d.get("SUBTYP","?"))}; Verbund {d.get("VERBUND","-")}'
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, d)
            lw.addItem(it)

    # =====================================================================
    # LEERROHRE je TRASSE & MODUS ermitteln
    # =====================================================================
    def _parse_id_trasse_neu(self, val):
        """Gibt Liste der Trassen-IDs (int) aus ID_TRASSE_NEU zurück. Erwartet JSON/JSONB-ähnliche Struktur."""
        out = []
        if val is None:
            return out
        try:
            data = val
            if isinstance(val, str):
                data = json.loads(val)
            if isinstance(data, dict) and "list" in data:
                data = data["list"]
            if isinstance(data, list):
                for el in data:
                    # el kann dict {"id":123,"index":0,"reverse":false} sein
                    if isinstance(el, dict) and "id" in el:
                        out.append(int(el["id"]))
                    elif isinstance(el, (int, str)):
                        out.append(int(el))
        except Exception:
            pass
        return out

    def _leerrohre_for_trasse_and_mode(self, trasse_id, side, mode):
        """
        Filtert LWL_Leerrohr in Abhängigkeit von Knoten (self.sel_node_id), Trasse und Modus.
        - lotrecht: FROM/TO == node && (Trasse am Anfang/Ende ODER toleriert, falls Datenlage unklar)
        - parallel: NICHT FROM/TO == node && Trasse im Verlauf && echter Durchlauf über node
        Rückgabe-Dicts enthalten immer VONKNOTEN/NACHKNOTEN (Fallback: FROMNODE/TONODE).
        """
        result = []
        node_id = self.sel_node_id
        if node_id is None:
            return result

        lr_layer = self._get_layer("LWL_Leerrohr")
        if not lr_layer:
            return result

        names = [f.name() for f in lr_layer.fields()]
        id_nm   = "id" if "id" in names else names[0]
        from_nm = "VONKNOTEN" if "VONKNOTEN" in names else ("FROMNODE" if "FROMNODE" in names else None)
        to_nm   = "NACHKNOTEN" if "NACHKNOTEN" in names else ("TONODE"   if "TONODE"   in names else None)
        sub_nm  = "SUBTYP" if "SUBTYP" in names else None
        verb_field  = self._find_verbund_field(lr_layer)
        id_trneu_nm = "ID_TRASSE_NEU" if "ID_TRASSE_NEU" in names else None

        req = QgsFeatureRequest()
        if id_trneu_nm:
            req = req.setFilterExpression(f'"{id_trneu_nm}" IS NOT NULL')

        for f in lr_layer.getFeatures(req):
            if id_nm not in f.fields().names() or (sub_nm is None):
                continue

            # Verlaufsliste
            tr_list = self._parse_id_trasse_neu(f[id_trneu_nm]) if id_trneu_nm else []
            if trasse_id not in tr_list:
                continue

            f_id = int(f[id_nm]) if f[id_nm] not in (None, "") else None
            if f_id is None:
                continue

            from_matches = (from_nm and f[from_nm] not in (None, "") and int(f[from_nm]) == node_id)
            to_matches   = (to_nm   and f[to_nm]   not in (None, "") and int(f[to_nm])   == node_id)
            ends_at_node = bool(from_matches or to_matches)

            if mode == "lotrecht":
                if not ends_at_node:
                    continue
                # plausibel am Anfang/Ende der Trassenliste?
                at_start = (len(tr_list) > 0 and int(tr_list[0]) == int(trasse_id))
                at_end   = (len(tr_list) > 0 and int(tr_list[-1]) == int(trasse_id))
                if not (at_start or at_end):
                    pass  # tolerieren
            else:
                # parallel: NICHT am Knoten enden/anfangen …
                if ends_at_node:
                    continue
                # … und echter Durchlauf über node
                if not self._passes_through_node(tr_list, node_id):
                    continue

            d = {
                "id": f_id,
                "SUBTYP": f[sub_nm],
                "SUBTYP_CHAR": self._get_subtyp_char(f[sub_nm]),
                "VERBUND": f[verb_field] if verb_field in f.fields().names() else None,
                "VONKNOTEN": f[from_nm] if from_nm in f.fields().names() else None,
                "NACHKNOTEN": f[to_nm]   if to_nm   in f.fields().names() else None,
            }
            result.append(d)

        result.sort(key=lambda d: (str(d.get("SUBTYP_CHAR", d.get("SUBTYP"))), int(d["id"])))
        return result

    def _trassenliste_berührt_knoten(self, tr_ids, node_id):
        """Prüft, ob mindestens eine der Trassen (IDs) am node_id beginnt/endet."""
        tr_layer = self._get_layer("LWL_Trasse")
        if not tr_layer or not tr_ids:
            return False
        names = [f.name() for f in tr_layer.fields()]
        from_nm = "VONKNOTEN" if "VONKNOTEN" in names else ("FROMNODE" if "FROMNODE" in names else None)
        to_nm   = "NACHKNOTEN" if "NACHKNOTEN" in names else ("TONODE" if "TONODE" in names else None)
        if not (from_nm and to_nm):
            return False
        expr = f'"id" IN ({",".join(str(int(t)) for t in tr_ids)})'
        try:
            for f in tr_layer.getFeatures(QgsFeatureRequest().setFilterExpression(expr)):
                if int(f[from_nm]) == node_id or int(f[to_nm]) == node_id:
                    return True
        except Exception:
            pass
        return False

    def _extract_userrole_id(self, data):
        """
        Robust: holt eine int-ID aus data (int/float/str/dict/QVariant).
        Gibt None zurück, wenn nichts Sinnvolles drin ist.
        """
        try:
            from qgis.PyQt.QtCore import QVariant
        except Exception:
            QVariant = None

        # direkte Typen
        if isinstance(data, int):
            return data
        if isinstance(data, float):
            return int(data)
        if isinstance(data, str):
            s = data.strip()
            return int(s) if s.isdigit() else None

        # dict mit gängigen Schlüsseln
        if isinstance(data, dict):
            for k in ("id", "lr_id", "trasse_id", "ID", "Id", "pk"):
                v = data.get(k)
                if v is None:
                    continue
                try:
                    return int(v)
                except Exception:
                    pass
            return None

        # QVariant (falls aus Qt)
        if QVariant is not None and isinstance(data, QVariant):
            try:
                # zuerst int
                iv = int(data)
                return iv
            except Exception:
                try:
                    s = str(data)
                    return int(s) if s.isdigit() else None
                except Exception:
                    return None

        # unbekannt
        return None

    # =====================================================================
    # AUSWAHL ÜBERNEHMEN (bestehender Flow)
    # =====================================================================
    def on_apply_selection(self):
        self.sel_lr1_list = self._collect_side('lr1')
        self.sel_lr2_list = self._collect_side('lr2')

        # Parallel -> pro Seite nur 1 LR zulassen
        if self._is_parallel_side(1) and len(self.sel_lr1_list) > 1:
            self.sel_lr1_list = self.sel_lr1_list[:1]
            self._status("Parallel (links): es wird nur 1 Leerrohr genutzt.", ok=True)
        if self._is_parallel_side(2) and len(self.sel_lr2_list) > 1:
            self.sel_lr2_list = self.sel_lr2_list[:1]
            self._status("Parallel (rechts): es wird nur 1 Leerrohr genutzt.", ok=True)

        self._fill_combo_from_list('lr1', self.sel_lr1_list)
        self._fill_combo_from_list('lr2', self.sel_lr2_list)

        self._set_sel_label('lr1', len(self.sel_lr1_list))
        self._set_sel_label('lr2', len(self.sel_lr2_list))

        ready = bool(self.sel_lr1_list and self.sel_lr2_list)
        for bn in ("pushButton_automatisch","pushButton_verbindung_loeschen","pushButton_Datenpruefung","pushButton_Import"):
            getattr(self.ui, bn).setEnabled(ready)

        self._warm_caches()
        self._draw_all()
        self._reset_list_view()
        self._status("Auswahl übernommen.")

    def _collect_side(self, side):
        lw = self.ui.listWidget_Leerohr1 if side == 'lr1' else self.ui.listWidget_Leerohr2
        items = lw.selectedItems()
        out = []
        for it in items:
            d = it.data(Qt.UserRole)
            # d kann bereits ein Dict sein (2-Stufen-Flow) ODER QgsFeature (alte Pick-Variante)
            if isinstance(d, dict):
                out.append(d)
                continue
            f = d
            names = f.fields().names()
            subtyp = f["SUBTYP"] if "SUBTYP" in names else None

            # Endknoten robust ermitteln
            von_nm = "VONKNOTEN" if "VONKNOTEN" in names else ("FROMNODE" if "FROMNODE" in names else None)
            nach_nm = "NACHKNOTEN" if "NACHKNOTEN" in names else ("TONODE" if "TONODE" in names else None)

            dd = {
                "id": f["id"],
                "SUBTYP": subtyp,
                "SUBTYP_CHAR": self._get_subtyp_char(subtyp),
                "VERBUND": None,
                "VONKNOTEN": f[von_nm] if von_nm else None,
                "NACHKNOTEN": f[nach_nm] if nach_nm else None,
            }
            for c in self.VERBUND_FIELDS:
                if c in names:
                    dd["VERBUND"] = f[c]; break
            out.append(dd)
        return out

    def _fill_combo_from_list(self, side, lst):
        cb = getattr(self.ui, "comboBox_AktivLR1" if side == 'lr1' else "comboBox_AktivLR2", None)
        if cb is None:
            return
        cb.blockSignals(True)
        cb.clear()

        par = self._is_parallel_side(1 if side == 'lr1' else 2)
        if par and len(lst) == 1:
            lr = dict(lst[0])  # enthält idealerweise VONKNOTEN / NACHKNOTEN
            von = lr.get("VONKNOTEN")
            nach = lr.get("NACHKNOTEN")

            # Einträge mit Knotenbezeichnung
            data_von = {"id": lr["id"], "dir": "VON", "is_split": True,
                        "VONKNOTEN": von, "NACHKNOTEN": nach}
            data_nach = {"id": lr["id"], "dir": "NACH", "is_split": True,
                        "VONKNOTEN": von, "NACHKNOTEN": nach}

            cb.addItem(self._dir_label(data_von), data_von)
            cb.addItem(self._dir_label(data_nach), data_nach)
        else:
            for d in lst:
                cb.addItem(f'{d.get("SUBTYP_CHAR", d.get("SUBTYP", "?"))}; Verbund {d.get("VERBUND", "-")}', d)

        if cb.count() > 0:
            cb.setCurrentIndex(0)
        cb.blockSignals(False)

    def _set_sel_label(self, side, n):
        lab = self.ui.label_gewaehltes_leerrohr1 if side=='lr1' else self.ui.label_gewaehltes_leerrohr2
        lab.setText(f"{n} ausgewählt" if n else "–")
        lab.setStyleSheet("background-color: lightgreen;" if n else "background-color: lightcoral;")

    # ---------- Farben / Belegung ----------
    # --- ERSATZ: nutzt _farben_cache (subtyp->nr->(prim,sec)) ---
    def _get_rohr_farben(self, subtyp_id):
        if not subtyp_id:
            return {}
        if hasattr(self, "_farben_cache") and self._farben_cache.get(int(subtyp_id)):
            return dict(self._farben_cache[int(subtyp_id)])
        # Fallback (wenn caches nicht warm sind): einmalig nachladen
        with self._cursor() as cur:
            res = {}
            if cur is None:
                return res
            try:
                cur.execute('SELECT "ROHRNUMMER","FARBCODE" FROM lwl."LUT_Rohr_Beschreibung" WHERE "ID_SUBTYP"=%s',
                            (int(subtyp_id),))
                for nr, farb in cur.fetchall():
                    if farb and '/' in farb:
                        p, s = [c.strip() for c in str(farb).split('/', 1)]
                        s = None if (not s or s.strip('#') == '000000') else s
                    else:
                        p, s = farb, None
                    res[int(nr)] = (p or "#808080", s)
            except Exception:
                pass
            return res

    def _compute_forbidden_ranges_for_he(self, lr_id: int, lr_geom_map_crs) -> list:
        """
        Verbotene s-Intervalle (0..1) entlang der LR-Geometrie:
        ±0,10 m um HE-Andockpunkte. Erkennt HEs sowohl
        - über LWL_Rohr.ID_HAUSEINFÜHRUNG (falls gesetzt) als auch
        - direkt über LWL_Hauseinfuehrung (Fallback).
        """
        from qgis.core import QgsProject, QgsFeatureRequest, QgsGeometry, QgsCoordinateTransform
        ranges = []
        try:
            total_len = float(lr_geom_map_crs.length() or 0.0)
        except Exception:
            total_len = 0.0
        if total_len <= 0.0:
            return ranges

        delta_s = 0.10 / total_len  # 10 cm → s

        # Helper: s-Fraction (0..1) eines Map-Punktes auf LR
        def _fraction01(map_pt) -> float:
            L = float(lr_geom_map_crs.length() or 0.0)
            if L <= 0.0:
                return 0.0
            try:
                pt = lr_geom_map_crs.closestSegmentWithContext(map_pt)[1]
                tmp = QgsGeometry(lr_geom_map_crs)
                _ = tmp.splitGeometry([pt], False)
                l0 = float(tmp.length() or 0.0)
                return max(0.0, min(1.0, l0 / L))
            except Exception:
                return 0.0

        # 1) Versuch über LWL_Rohr (ID_HAUSEINFÜHRUNG gesetzt)
        with self._cursor() as cur:
            if cur:
                cur.execute("""
                    SELECT "FROM_POS","TO_POS"
                    FROM lwl."LWL_Rohr"
                    WHERE "ID_LEERROHR"=%s AND "ID_HAUSEINFÜHRUNG" IS NOT NULL
                """, (int(lr_id),))
                rows = cur.fetchall() or []
                for fpos, tpos in rows:
                    try:
                        f = float(fpos) if fpos is not None else 0.0
                        t = float(tpos) if tpos is not None else 0.0
                    except Exception:
                        continue
                    mid = max(0.0, min(1.0, (f + t) * 0.5 if (t > 0.0 or f > 0.0) else f))
                    a = max(0.0, mid - delta_s)
                    b = min(1.0, mid + delta_s)
                    if a < b:
                        ranges.append((a, b))

        # 2) Fallback über LWL_Hauseinfuehrung (Geometrie projizieren), falls noch nichts gefunden
        if not ranges:
            he_layer = None
            for lyr in QgsProject.instance().mapLayers().values():
                if lyr.name() == "LWL_Hauseinfuehrung":
                    he_layer = lyr
                    break
            if he_layer:
                # Transform HE → Map-CRS
                tr_he = QgsCoordinateTransform(he_layer.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance())
                req = QgsFeatureRequest().setFilterExpression(f'"ID_LEERROHR" = {int(lr_id)}')
                for feat in he_layer.getFeatures(req):
                    g = feat.geometry()
                    if not g:
                        continue
                    try:
                        gg = QgsGeometry(g)
                        _ = gg.transform(tr_he)
                    except Exception:
                        continue
                    try:
                        # Andockpunkt: der Linienendpunkt, der der LR am nächsten liegt
                        pts = gg.asPolyline() or []
                        if not pts:
                            continue
                        p1 = pts[0]; p2 = pts[-1]
                        from qgis.core import QgsPointXY
                        d1 = lr_geom_map_crs.distance(QgsGeometry.fromPointXY(QgsPointXY(p1)))
                        d2 = lr_geom_map_crs.distance(QgsGeometry.fromPointXY(QgsPointXY(p2)))
                        anchor = p1 if d1 <= d2 else p2
                        s = _fraction01(anchor)
                        a = max(0.0, s - delta_s)
                        b = min(1.0, s + delta_s)
                        if a < b:
                            ranges.append((a, b))
                    except Exception:
                        continue

        # Intervalle vereinigen
        if not ranges:
            return ranges
        ranges.sort()
        merged = []
        ca, cb = ranges[0]
        for a, b in ranges[1:]:
            if a <= cb:
                cb = max(cb, b)
            else:
                merged.append((ca, cb))
                ca, cb = a, b
        merged.append((ca, cb))
        return merged

    # --- ERSATZ: nutzt _belegung_cache (lr_id->nr->(occupied,rid)) ---
    def get_freie_rohrnummern(self, lr_id):
        """Gibt sortierte Liste freier ROHRNUMMERN am aktuellen Knoten zurück."""
        belegung = self._get_rohr_belegung(lr_id)
        frei = [nr for nr, (occ, _) in belegung.items() if not occ]
        return sorted(frei)

    def get_belegte_rohrnummern(self, lr_id):
        """Gibt sortierte Liste belegter ROHRNUMMERN am aktuellen Knoten zurück."""
        belegung = self._get_rohr_belegung(lr_id)
        belegt = [nr for nr, (occ, _) in belegung.items() if occ]
        return sorted(belegt)

    def on_node_selected(self, node_id: int):
        """
        Setzt den aktiven Verbinder-/Knoten-Kontext für das LVT.
        Wichtig: invalidiert den Belegungs-Cache, damit pro Knoten neu bewertet wird.
        """
        try:
            self.sel_node_id = int(node_id)
        except Exception:
            self.sel_node_id = None

        # Cache leeren (node-spezifisch)
        self._belegung_cache = {}

        # Optional: UI-Refresh für beide Seiten, falls du dafür Methoden hast
        try:
            self._refresh_side_ui(1)
            self._refresh_side_ui(2)
        except Exception:
            pass

    def _get_rohr_belegung(self, lr_id):
        """
        Belegung je ROHRNUMMER für EIN Leerrohr am AKTUELL GEWÄHLTEN KNOTEN.
        - 'Belegt' == es existiert mind. EINE Rohr↔Rohr-Relation (lwl."LWL_Rohr_Rohr_rel")
        für irgendein Segment dieser Nummer mit rr."ID_KNOTEN" == self.sel_node_id.
        - Hauseinführungen an ANDEREN Knoten zählen NICHT.
        Rückgabe: { rohrnr:int -> (occupied:bool, repr_rohr_id:int|None) }
        """
        res = {}

        # Knoten-Kontext ist Pflicht (pro Knoten unterschiedliche Belegung!)
        node_id = getattr(self, "sel_node_id", None)
        try:
            lr_id = int(lr_id)
            node_id = int(node_id) if node_id is not None else None
        except Exception:
            return res

        if node_id is None:
            # kein Knoten gewählt → alles als frei behandeln
            return res

        # Node-spezifischer Cache
        key = (lr_id, node_id)
        if getattr(self, "_belegung_cache", None) and key in self._belegung_cache:
            return dict(self._belegung_cache[key])

        with self._cursor() as cur:
            if cur is None:
                return res

            # 1) Alle Rohr-Segmente des Leerrohrs sammeln: {nr -> set(rohr_ids)}
            cur.execute("""
                SELECT id, "ROHRNUMMER"
                FROM lwl."LWL_Rohr"
                WHERE "ID_LEERROHR" = %s
                ORDER BY "ROHRNUMMER" NULLS LAST, id
            """, (lr_id,))
            ids_by_nr = {}
            for rid, rnr in cur.fetchall() or []:
                if rnr is None:
                    continue
                nr = int(rnr)
                ids_by_nr.setdefault(nr, set()).add(int(rid))

            if not ids_by_nr:
                self._belegung_cache = getattr(self, "_belegung_cache", {})
                self._belegung_cache[key] = res
                return res

            # 2) Flache ID-Liste
            all_ids = sorted({rid for s in ids_by_nr.values() for rid in s})

            # 3) Nur Relationen AM aktuellen Knoten berücksichtigen
            cur.execute("""
                SELECT "ID_ROHR_1", "ID_ROHR_2"
                FROM lwl."LWL_Rohr_Rohr_rel"
                WHERE "ID_KNOTEN" = %s
                AND ("ID_ROHR_1" = ANY(%s) OR "ID_ROHR_2" = ANY(%s))
            """, (node_id, all_ids, all_ids))
            occupied_ids = set()
            for a, b in cur.fetchall() or []:
                if a is not None: occupied_ids.add(int(a))
                if b is not None: occupied_ids.add(int(b))

            # 4) Aggregation pro ROHRNUMMER (OR über alle Segmente der Nummer)
            out = {}
            for nr, idset in ids_by_nr.items():
                occ = any(rid in occupied_ids for rid in idset)
                repr_id = min(idset) if idset else None
                out[nr] = (occ, repr_id)

        # Cache speichern (pro (LR, Knoten))
        self._belegung_cache = getattr(self, "_belegung_cache", {})
        self._belegung_cache[key] = dict(out)
        return out

    # --- ERSATZ: nutzt _color_hex_cache ---
    def _color_hexes_db(self, lr_id: int, rohrnr: int):
        key = (int(lr_id), int(rohrnr))
        if hasattr(self, "_color_hex_cache") and key in self._color_hex_cache:
            return self._color_hex_cache[key]
        # Fallback: 1 Query
        with self._cursor() as cur:
            try:
                cur.execute('SELECT "SUBTYP" FROM lwl."LWL_Leerrohr" WHERE id=%s', (int(lr_id),))
                row = cur.fetchone()
                if not row or row[0] is None:
                    return (None, None)
                subtyp = int(row[0])
                cur.execute('SELECT "FARBCODE" FROM lwl."LUT_Rohr_Beschreibung" WHERE "ID_SUBTYP"=%s AND "ROHRNUMMER"=%s LIMIT 1',
                            (subtyp, int(rohrnr)))
                r = cur.fetchone()
                if not r or not r[0]:
                    return (None, None)
                val = str(r[0]).strip()
                if "/" in val:
                    p, s = [x.strip() or None for x in val.split("/", 1)]
                else:
                    p, s = val, None
                return (p, s)
            except Exception:
                return (None, None)

    # --- ERSATZ: nutzt _color_name_cache ---
    def _color_name_db(self, lr_id: int, rohrnr: int) -> str:
        key = (int(lr_id), int(rohrnr))
        if hasattr(self, "_color_name_cache") and key in self._color_name_cache:
            return self._color_name_cache[key]
        # Fallback: 1 Query
        with self._cursor() as cur:
            try:
                cur.execute('SELECT "SUBTYP" FROM lwl."LWL_Leerrohr" WHERE id=%s', (int(lr_id),))
                row = cur.fetchone()
                if not row or row[0] is None:
                    return None
                subtyp = int(row[0])
                cur.execute('SELECT "FARBE" FROM lwl."LUT_Rohr_Beschreibung" WHERE "ID_SUBTYP"=%s AND "ROHRNUMMER"=%s LIMIT 1',
                            (subtyp, int(rohrnr)))
                r = cur.fetchone()
                if not r or not r[0]:
                    return None
                txt = str(r[0]).strip()
                if "/" in txt:
                    txt = txt.split("/", 1)[0].strip()
                return None if txt.startswith("#") else txt
            except Exception:
                return None


    def _format_lr_label(self, lr_id: int) -> str:
        """Label eines Leerrohres wie in listWidget_Leerohr1/2:
        '<SUBTYP_CHAR>; Verbund <VERBUND>'  (Fallback: 'LR<id>')."""
        if not hasattr(self, "_lr_label_cache"):
            self._lr_label_cache = {}

        try:
            lr_id = int(lr_id)
        except Exception:
            return f"LR{lr_id}"

        if lr_id in self._lr_label_cache:
            return self._lr_label_cache[lr_id]

        lyr = self._get_layer("LWL_Leerrohr")
        if not lyr:
            lbl = f"LR{lr_id}"
            self._lr_label_cache[lr_id] = lbl
            return lbl

        f = next(lyr.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id"={lr_id}')), None)
        if not f:
            lbl = f"LR{lr_id}"
            self._lr_label_cache[lr_id] = lbl
            return lbl

        names = f.fields().names()
        sub = f["SUBTYP"] if "SUBTYP" in names else None
        sub_char = self._get_subtyp_char(sub) if sub is not None else None
        verb_field = self._find_verbund_field(lyr)
        verb = f[verb_field] if verb_field in names else None

        lbl = f"{sub_char or sub or f'LR{lr_id}'}; Verbund {verb if verb not in (None, '') else '-'}"
        self._lr_label_cache[lr_id] = lbl
        return lbl

    # --- NEU: Batch-Caches vor dem Zeichnen/Listenaufbau füllen ---
    def _warm_caches(self):
        """Lädt in einem Rutsch: Rohr-Farben (Hex + Name) je Subtyp und Belegung je Leerrohr."""
        # Ziel‑Caches
        self._farben_cache = {}        # {subtyp -> {nr: (prim_hex, sec_hex)}}
        self._color_hex_cache = {}     # {(lr_id, nr) -> (prim_hex, sec_hex)}
        self._color_name_cache = {}    # {(lr_id, nr) -> name}
        self._belegung_cache = {}      # {lr_id -> {nr: (occupied_bool, rid)}}

        # benötigte IDs sammeln
        lr_ids = set()
        subtyps = set()
        for d in (self.sel_lr1_list or []):
            if d.get("id"):      lr_ids.add(int(d["id"]))
            if d.get("SUBTYP"):  subtyps.add(int(d["SUBTYP"]))
        for d in (self.sel_lr2_list or []):
            if d.get("id"):      lr_ids.add(int(d["id"]))
            if d.get("SUBTYP"):  subtyps.add(int(d["SUBTYP"]))

        if not lr_ids and not subtyps:
            return

        # Mapping lr_id -> subtyp holen (robust, falls SUBTYP mal None war)
        with self._cursor() as cur:
            if cur is None:
                return
            try:
                if lr_ids:
                    cur.execute('SELECT id, "SUBTYP" FROM lwl."LWL_Leerrohr" WHERE id = ANY(%s)', (list(lr_ids),))
                    lr_to_sub = {int(i): (int(s) if s is not None else None) for i, s in cur.fetchall()}
                    subtyps |= {s for s in lr_to_sub.values() if s is not None}
                else:
                    lr_to_sub = {}
            except Exception:
                lr_to_sub = {}

        # LUT: Farbcodes + Farbnamen je Subtyp
        if subtyps:
            with self._cursor() as cur:
                try:
                    cur.execute(
                        'SELECT "ID_SUBTYP","ROHRNUMMER","FARBCODE","FARBE" '
                        'FROM lwl."LUT_Rohr_Beschreibung" WHERE "ID_SUBTYP" = ANY(%s)',
                        (list(subtyps),)
                    )
                    for sid, nr, farbcode, farbe in cur.fetchall():
                        if sid is None or nr is None:
                            continue
                        sid = int(sid); nr = int(nr)
                        prim_hex, sec_hex = None, None
                        if farbcode:
                            val = str(farbcode).strip()
                            if "/" in val:
                                p, s = [x.strip() or None for x in val.split("/", 1)]
                                prim_hex, sec_hex = p, s
                            else:
                                prim_hex = val
                        # Cache pro Subtyp
                        self._farben_cache.setdefault(sid, {})[nr] = (prim_hex, sec_hex)
                        # Für alle Leerrohre dieses Subtyps später per (lr_id,nr) mappen
                    # Farbnamen (Primär) für Text
                    # (wir nutzen nur den Teil vor '/', analog bestehender Logik)
                    cur.execute(
                        'SELECT "ID_SUBTYP","ROHRNUMMER","FARBE" '
                        'FROM lwl."LUT_Rohr_Beschreibung" WHERE "ID_SUBTYP" = ANY(%s)',
                        (list(subtyps),)
                    )
                    farbnamen = {}
                    for sid, nr, name in cur.fetchall():
                        if sid is None or nr is None or not name:
                            continue
                        txt = str(name).strip()
                        if "/" in txt:
                            txt = txt.split("/", 1)[0].strip()
                        farbnamen[(int(sid), int(nr))] = (None if txt.startswith("#") else txt)
                except Exception:
                    farbnamen = {}

            # nun (lr_id, nr) füllen
            for lr_id, sid in lr_to_sub.items():
                if sid is None:
                    continue
                for nr, (p, s) in self._farben_cache.get(sid, {}).items():
                    self._color_hex_cache[(lr_id, nr)] = (p, s)
                    self._color_name_cache[(lr_id, nr)] = farbnamen.get((sid, nr))

        # Belegung: Rohre + vorhandene Rohr↔Rohr‑Relationen
        if lr_ids:
            with self._cursor() as cur:
                try:
                    # alle Röhrchen je Leerrohr
                    cur.execute(
                        'SELECT "id","ID_LEERROHR","ROHRNUMMER","STATUS" FROM lwl."LWL_Rohr" '
                        'WHERE "ID_LEERROHR" = ANY(%s)',
                        (list(lr_ids),)
                    )
                    rid_by_lr_nr = {}   # {(lr_id,nr) -> rid}
                    used_by_rid = {}    # rid -> bool(VERWENDET)
                    for rid, lr, nr, st in cur.fetchall():
                        if lr is None or nr is None or rid is None:
                            continue
                        rid = int(rid); lr = int(lr); nr = int(nr)
                        rid_by_lr_nr[(lr, nr)] = rid
                        used_by_rid[rid] = (str(st).upper() == "VERWENDET") if st is not None else False

                    # welche RIDs sind durch Relationen belegt?
                    all_rids = list(used_by_rid.keys())
                    related = set()
                    if all_rids:
                        # zwei Seiten prüfen, um Indexe optimal zu nutzen
                        cur.execute(
                            'SELECT "ID_ROHR_1","ID_ROHR_2" FROM lwl."LWL_Rohr_Rohr_rel" '
                            'WHERE "ID_ROHR_1" = ANY(%s) OR "ID_ROHR_2" = ANY(%s)',
                            (all_rids, all_rids)
                        )
                        for a, b in cur.fetchall():
                            if a is not None: related.add(int(a))
                            if b is not None: related.add(int(b))

                    # beleg_cache je lr_id befüllen
                    for (lr, nr), rid in rid_by_lr_nr.items():
                        occ = (rid in related) or used_by_rid.get(rid, False)
                        self._belegung_cache.setdefault(lr, {})[nr] = (occ, rid)
                except Exception:
                    pass

    # ---------- Zeichnen (links oben, rechts unten) ----------
    def _simulate_bar(self, lr):
        farben = self._get_rohr_farben(lr["SUBTYP"])
        beleg  = self._get_rohr_belegung(lr["id"])
        n = max([0]+list(farben.keys())+list(beleg.keys()))
        width = (n*self.SQ + (n-1)*self.GAP) if n>0 else 0
        return {}, {}, width

    def _label_bar(self, base_x, base_y, d, right=False):
        """Beschriftung oberhalb der Bar – mit Knoten-Richtungstext und Clamping auf View-Breite."""
        font = QFont(); font.setPointSize(9); font.setBold(True)

        # Richtungstext (unterstützt 'A'/'B' und 'VON'/'NACH')
        dir_txt = ""
        dir_key = d.get("dir")
        if dir_key in ("A", "VON", "B", "NACH"):
            kn_id = d.get("VONKNOTEN") if dir_key in ("A", "VON") else d.get("NACHKNOTEN")
            dir_txt = f" (Richtung {self._knoten_label(kn_id)})"

        txt = f'{d.get("SUBTYP_CHAR", d.get("SUBTYP", "?"))}{dir_txt}; Verbund {d.get("VERBUND", "-")}'

        # Ziel-X bestimmen (rechts: bevorzugt rechtsbündig am Bar)
        try:
            _, _, bar_w = self._simulate_bar(d)
        except Exception:
            bar_w = 120

        # Viewbreite holen
        view = getattr(self.ui, "graphicsView_Auswahl_Rrohr1", None)
        W = max(300, view.viewport().width()) if view else 800

        fm = QFontMetricsF(font)
        tw = fm.horizontalAdvance(txt)
        label_margin = getattr(self, "LABEL_MARGIN", 10)

        if right:
            # rechtsbündig an Bar
            label_x = base_x + max(0.0, bar_w - tw)
        else:
            # linksbündig an Bar
            label_x = base_x

        # Horizontal clampen
        if label_x < label_margin:
            label_x = label_margin
        if label_x + tw > W - label_margin:
            label_x = max(label_margin, W - label_margin - tw)

        # Y leicht über Bar, nicht negativ
        y_top = max(2.0, base_y - 18.0)
        y_base = y_top + fm.ascent()

        # mit weißem Halo
        path = QPainterPath(); path.addText(label_x, y_base, font, txt)
        halo = QGraphicsPathItem(path)
        halo.setPen(QPen(QColor("#ffffff"), 3))
        halo.setBrush(QBrush(Qt.NoBrush))
        halo.setZValue(9)
        self.scene.addItem(halo)

        label = QGraphicsSimpleTextItem(txt)
        label.setFont(font)
        label.setBrush(QBrush(Qt.black))
        label.setZValue(10)
        label.setPos(label_x, y_top)
        self.scene.addItem(label)

    def _draw_bar(self, base_x, base_y, side, bar_idx, lr):
        rects={}; ids={}
        farben = self._get_rohr_farben(lr["SUBTYP"])
        beleg  = self._get_rohr_belegung(lr["id"])
        n = max([0]+list(farben.keys())+list(beleg.keys()))
        sq=self.SQ; gap=self.GAP
        x=base_x
        font_num = QFont(); font_num.setPointSize(8)

        # kleine Inline-Funktion: prim/sek-Farbe entsättigen + etwas transparenter
        def _desat(hex_or_qcolor):
            c = QColor(hex_or_qcolor)
            h, s, v, a = c.getHsv()
            s = max(0, int(s * 0.33))    # ~55% weniger Sättigung
            a = max(0, int(a * 0.85))    # leicht transparenter
            c.setHsv(h, s, v, a)
            return c

        for nr in range(1, n+1):
            prim, sec = farben.get(nr, ("#808080", None))
            occupied, rid = beleg.get(nr, (False, None))
            prim_name = self._color_name(prim)

            r = ClickableRect(x, base_y, sq, side, bar_idx, lr["id"], nr, occupied, self.on_rect_click,
                            prim_hex=prim, prim_name=prim_name)
            self.scene.addItem(r)

            # diagonal füllen (zwei Dreiecke)
            p1=[QPointF(x,base_y), QPointF(x+sq,base_y), QPointF(x,base_y+sq)]
            p2=[QPointF(x+sq,base_y), QPointF(x+sq,base_y+sq), QPointF(x,base_y+sq)]
            t1=QGraphicsPolygonItem(QPolygonF(p1), r)
            t2=QGraphicsPolygonItem(QPolygonF(p2), r)

            if occupied:
                # statt Grau: prim/sek entsättigt + leicht transparenter
                c1 = _desat(prim)
                c2 = _desat(sec) if sec else c1
                t1.setBrush(QBrush(c1))
                t2.setBrush(QBrush(c2))
                r.setPen(QPen(Qt.red, 2))
                r.setToolTip(f"Rohr {nr}: belegt")
            else:
                c1 = QColor(prim)
                c2 = QColor(sec) if sec else c1
                t1.setBrush(QBrush(c1))
                t2.setBrush(QBrush(c2))
                r.setToolTip(f"Rohr {nr}: frei ({prim_name})")

            t1.setPen(QPen(Qt.NoPen))
            t2.setPen(QPen(Qt.NoPen))

            # Nummer mittig
            txt=self.scene.addText(str(nr), font_num)
            bb=txt.boundingRect(); txt.setDefaultTextColor(Qt.black)
            txt.setPos(x+(sq-bb.width())/2, base_y+(sq-bb.height())/2)

            rects[nr]=r; ids[nr]=rid
            x += sq + gap

        width = (n*sq + (n-1)*gap) if n>0 else 0
        return rects, ids, width

    def _draw_all(self):
        self.scene.clear()
        self._clear_selection_highlight()
        self.paired.clear()
        self.sel_rect_left = None
        self.left_bars, self.right_bars = [], []

        view = self.ui.graphicsView_Auswahl_Rrohr1.viewport()
        W = max(400, view.width())
        H = max(240, view.height())
        margin = 16
        row_h = self.ROW_VSPACE

        # LINKS
        items_left = list(self.sel_lr1_list)
        if self._is_parallel_side(1) and len(items_left) == 1:
            # Duplikat mit Richtungen A/B
            base = dict(items_left[0])
            items_left = [dict(base, dir='A'), dict(base, dir='B')]

        y_left = margin
        for idx, d in enumerate(items_left):
            y = y_left + idx * row_h
            rects, ids, width = self._draw_bar(self.LEFT_MARGIN, y, side=1, bar_idx=idx, lr=d)
            self._label_bar(self.LEFT_MARGIN, y, d)
            bar = {'lr': d, 'y': y, 'rects': rects, 'ids': ids, 'width': width}
            if 'dir' in d: bar['dir'] = d['dir']
            self.left_bars.append(bar)

        # RECHTS
        items_right = list(self.sel_lr2_list)
        if self._is_parallel_side(2) and len(items_right) == 1:
            base = dict(items_right[0])
            items_right = [dict(base, dir='A'), dict(base, dir='B')]

        nR = len(items_right)
        top_of_right = margin if nR <= 0 else max(margin, H - margin - self.SQ - (nR - 1) * row_h)
        for idx, d in enumerate(items_right):
            y = top_of_right + idx * row_h
            _, _, bw = self._simulate_bar(lr=d)
            base_x = max(self.LEFT_MARGIN + 10, W - self.RIGHT_MARGIN - bw)
            rects, ids, _ = self._draw_bar(base_x, y, side=2, bar_idx=idx, lr=d)
            self._label_bar(base_x, y, d, right=True)
            bar = {'lr': d, 'y': y, 'rects': rects, 'ids': ids, 'width': bw}
            if 'dir' in d: bar['dir'] = d['dir']
            self.right_bars.append(bar)

        self.scene.setSceneRect(0, 0, W, H)

        # bestehende DB-Verbindungen einzeichnen
        self._draw_existing_relations()

    # ---------- Farbname für Liste ----------
    def _color_name(self, hexcode):
        if not hexcode: return "?"
        h=hexcode.strip().lower()
        table={"#ff0000":"rot","#00ff00":"grün","#0000ff":"blau","#ffff00":"gelb","#ffa500":"orange",
               "#a52a2a":"braun","#808080":"grau","#000000":"schwarz","#ffffff":"weiß","#800080":"violett",
               "#ff00ff":"magenta","#00ffff":"türkis","#ffc0cb":"rosa"}
        return table.get(h,h)

    # ---------- Interaktion Verbinden ----------
    def on_rect_click(self, side, rect_item: ClickableRect, rohrnr: int):
        if rect_item.occupied or rect_item.used: return
        if side == 1:
            self.sel_rect_left = rect_item
            self._highlight(rect_item, True)
            self._status(f"Links Rohr {rohrnr} gewählt → rechts klicken.")
            return
        if not self.sel_rect_left:
            self._status("Bitte zuerst links ein Rohr wählen.", ok=False); return
        left = self.sel_rect_left; right = rect_item
        self._make_pair(left, right)
        self.sel_rect_left = None
        self._status("Verbunden. Weiter …")

    def _highlight(self, rect, active):
        pen=QPen(Qt.blue if active else Qt.black); pen.setWidth(3 if active else 1); rect.setPen(pen)

    def _knoten_label(self, knoten_id):
        """BEZEICHNUNG des Knotens holen; Fallback: ID als Text."""
        if knoten_id in (None, ""):
            return "?"
        try:
            kid = int(knoten_id)
        except Exception:
            return str(knoten_id)

        # 1) Versuch: Layer
        lyr = self._get_layer("LWL_Knoten")
        if lyr:
            names = [f.name() for f in lyr.fields()]
            id_nm = "id" if "id" in names else (names[0] if names else None)
            if id_nm:
                f = next(lyr.getFeatures(QgsFeatureRequest().setFilterExpression(f'"{id_nm}"={kid}')), None)
                if f:
                    for lab_nm in ("BEZEICHNUNG", "Bezeichnung", "NAME", "Name", "LABEL", "Label"):
                        if lab_nm in f.fields().names():
                            val = f[lab_nm]
                            if val not in (None, ""):
                                return str(val)

        # 2) Fallback: DB
        if self.is_connected and self.db:
            try:
                with psycopg2.connect(**self.db) as conn, conn.cursor() as cur:
                    cur.execute('SELECT "BEZEICHNUNG" FROM lwl."LWL_Knoten" WHERE id=%s', (kid,))
                    row = cur.fetchone()
                    if row and row[0] not in (None, ""):
                        return str(row[0])
            except Exception:
                pass

        return str(knoten_id)

    def _dir_label(self, d):
        """Erzeugt 'Richtung <Knotenlabel>' anhand d['dir'] und VON/NACH-Knoten im Datensatz."""
        if not isinstance(d, dict) or "dir" not in d:
            return ""
        if d["dir"] == "VON":
            kid = d.get("VONKNOTEN")
        else:
            kid = d.get("NACHKNOTEN")
        return f"Richtung {self._knoten_label(kid)}"

    def _is_valid_graphics_item(self, item):
        """Robuste Gültigkeitsprüfung für QGraphicsItems (auch nach scene.clear())."""
        if item is None:
            return False
        try:
            import sip
            try:
                if sip.isdeleted(item):  # PyQt: C++-Objekt bereits freigegeben
                    return False
            except Exception:
                pass
        except Exception:
            pass
        try:
            sc = item.scene()
            return sc is not None
        except Exception:
            return False


    def _clear_selection_highlight(self):
        """Vorherige Auswahl (Linie + Kästchen) sicher zurücksetzen."""
        entry = getattr(self, "selected_entry", None)
        if not entry:
            return

        line = entry.get("line")
        if self._is_valid_graphics_item(line):
            try:
                pen = self._pen_for_status(getattr(line, "status_id", self.default_status_id))
                pen.setWidth(2)
                line.setPen(pen)
            except Exception:
                pass

        for rect in (entry.get("left"), entry.get("right")):
            if self._is_valid_graphics_item(rect):
                try:
                    self._highlight(rect, False)
                except Exception:
                    pass

        self.selected_entry = None

    def _select_connection_entry(self, entry):
        """Optische Auswahl setzen (mit Gültigkeitsprüfungen)."""
        # alte Auswahl weg
        self._clear_selection_highlight()

        # neue Auswahl nur setzen, wenn alles noch gültig
        line = entry.get("line")
        if not self._is_valid_graphics_item(line):
            self.selected_entry = None
            return

        self.selected_entry = entry
        try:
            pen = self._pen_for_status(getattr(line, "status_id", self.default_status_id))
            pen.setWidth(4)  # dicker = ausgewählt
            line.setPen(pen)
        except Exception:
            pass

        for rect in (entry.get("left"), entry.get("right")):
            if self._is_valid_graphics_item(rect):
                try:
                    self._highlight(rect, True)
                except Exception:
                    pass

    def _on_line_clicked(self, conn_line: "ConnLine"):
        """Callback von ConnLine bei Linksklick → finde Entry, markiere Auswahl."""
        for e in self.paired:
            if isinstance(e, dict) and e.get("line") is conn_line:
                self._select_connection_entry(e)
                self._status("Verbindung markiert (Klick auf Linie).")
                return


    def _edge_points(self, left_rect: ClickableRect, right_rect: ClickableRect):
        # Start = UNTERKANTE Mitte des linken Kästchens
        lbl = left_rect.mapToScene(left_rect.rect().bottomLeft())
        lbr = left_rect.mapToScene(left_rect.rect().bottomRight())
        sx = (lbl.x() + lbr.x()) / 2.0
        sy = lbl.y()

        # Ziel = OBERKANTE Mitte des rechten Kästchens
        rtl = right_rect.mapToScene(right_rect.rect().topLeft())
        rtr = right_rect.mapToScene(right_rect.rect().topRight())
        ex = (rtl.x() + rtr.x()) / 2.0
        ey = rtl.y()

        return QLineF(sx, sy, ex, ey)

    def _pen_for_status(self, status_id):
        col = self.status_lut.get(status_id, ("","#000000"))[1]
        return QPen(QColor(col), 2)

    def _make_pair(self, left_rect: "ClickableRect", right_rect: "ClickableRect"):
        """Neue Linie erzeugen (origin='new'), in self.paired aufnehmen und Liste zentral neu aufbauen."""
        left_rect.used = True
        right_rect.used = True
        self._highlight(left_rect, False)
        self._highlight(right_rect, False)

        line = ConnLine(
            self._edge_points(left_rect, right_rect),
            self.default_status_id,
            self._on_line_status_changed,
            on_click=self._on_line_clicked,
            left_rect=left_rect,
            right_rect=right_rect,
        )
        line.setPen(self._pen_for_status(line.status_id))
        line.setZValue(-100)
        self.scene.addItem(line)

        entry = {
            "left": left_rect,
            "right": right_rect,
            "line": line,
            "rid_left": self._rect_to_rid(left_rect),
            "rid_right": self._rect_to_rid(right_rect),
            "origin": "new",
            "initial_status_id": None,
        }
        self.paired.append(entry)

        # Liste strikt aus self.paired neu aufbauen → keine Duplikate, immer synchron zur Grafik
        self._rebuild_list()

    def _on_line_status_changed(self, conn_line: "ConnLine"):
        """
        Wird von ConnLine aufgerufen, wenn der Status (per Kontextmenü) geändert wurde.
        Aktualisiert die Linienfarbe und synchronisiert die Listenansicht.
        """
        try:
            # Farbe anhand Status neu setzen
            conn_line.setPen(self._pen_for_status(conn_line.status_id))
        except Exception:
            pass

        # Liste immer aus self.paired neu aufbauen (grafikführend)
        self._rebuild_list()

    # ---------- DB-Relationen nachzeichnen ----------
    def _draw_existing_relations(self):
        """
        Bestehende Verbindungen zeichnen und Liste EINMAL zentral neu aufbauen.
        Verhindert Duplikate, auch wenn die Methode mehrfach aufgerufen wird.
        """
        if not (self.is_connected and self.db):
            self._rebuild_list()
            return

        # schon vorhandene Paare (aus evtl. vorherigen Aufrufen)
        existing_pairs = set()
        for e in self.paired:
            rl, rr = e.get("rid_left"), e.get("rid_right")
            if rl is not None and rr is not None:
                existing_pairs.add((min(int(rl), int(rr)), max(int(rl), int(rr))))

        left_rid_to_rect, right_rid_to_rect = {}, {}
        left_ids, right_ids = set(), set()

        for b in self.left_bars:
            for nr, rid in b['ids'].items():
                if rid:
                    rid = int(rid)
                    left_rid_to_rect[rid] = b['rects'][nr]
                    left_ids.add(rid)

        for b in self.right_bars:
            for nr, rid in b['ids'].items():
                if rid:
                    rid = int(rid)
                    right_rid_to_rect[rid] = b['rects'][nr]
                    right_ids.add(rid)

        if not left_ids or not right_ids:
            self._rebuild_list()
            return

        try:
            with psycopg2.connect(**self.db) as conn, conn.cursor() as cur:
                cur.execute('''
                    SELECT "ID_ROHR_1","ID_ROHR_2","STATUS"
                    FROM lwl."LWL_Rohr_Rohr_rel"
                    WHERE ( "ID_ROHR_1" = ANY(%s) AND "ID_ROHR_2" = ANY(%s) )
                    OR ( "ID_ROHR_1" = ANY(%s) AND "ID_ROHR_2" = ANY(%s) )
                ''', (list(left_ids), list(right_ids), list(right_ids), list(left_ids)))
                rows = cur.fetchall()
        except Exception:
            rows = []

        seen_pairs_this_call = set()

        for a, b, status_id in rows:
            a = int(a); b = int(b)
            # links/rechts zuordnen
            if a in left_rid_to_rect and b in right_rid_to_rect:
                lrect, rrect = left_rid_to_rect[a], right_rid_to_rect[b]
                rid_left, rid_right = a, b
            elif b in left_rid_to_rect and a in right_rid_to_rect:
                lrect, rrect = left_rid_to_rect[b], right_rid_to_rect[a]
                rid_left, rid_right = b, a
            else:
                continue

            pair_norm = (min(rid_left, rid_right), max(rid_left, rid_right))
            # schon vorhanden (vorheriger Aufruf) oder in diesem Aufruf bereits verarbeitet?
            if pair_norm in existing_pairs or pair_norm in seen_pairs_this_call:
                continue
            seen_pairs_this_call.add(pair_norm)

            sid = int(status_id) if status_id is not None else self.default_status_id
            line = ConnLine(
                self._edge_points(lrect, rrect),
                sid,
                self._on_line_status_changed,
                on_click=self._on_line_clicked,
                left_rect=lrect,
                right_rect=rrect,
            )
            line.setPen(self._pen_for_status(sid))
            line.setZValue(-100)
            self.scene.addItem(line)

            lrect.used = True
            rrect.used = True

            entry = {
                "left": lrect,
                "right": rrect,
                "line": line,
                "rid_left": rid_left,
                "rid_right": rid_right,
                "origin": "existing",
                "initial_status_id": sid,
            }
            self.paired.append(entry)

            # Buchhaltung (falls du das für Import/Delta nutzt)
            if not hasattr(self, "loaded_pairs_initial"):
                self.loaded_pairs_initial = set()
            if not hasattr(self, "loaded_status_by_pair"):
                self.loaded_status_by_pair = {}
            if not hasattr(self, "loaded_lr_pairs_initial"):
                self.loaded_lr_pairs_initial = set()
            self.loaded_pairs_initial.add(pair_norm)
            self.loaded_status_by_pair[pair_norm] = sid
            self.loaded_lr_pairs_initial.add((lrect.lr_id, rrect.lr_id))

        # WICHTIG: Liste nur EINMAL neu aufbauen
        self._rebuild_list()

    # ---------- Tabellarische Ansicht ----------
    def _reset_list_view(self):
        """listWidget_Verbindungen für Rohr↔Rohr-Tabelle vorbereiten (nach Übernahme)."""
        lw = self.ui.listWidget_Verbindungen
        lw.clear()
        lw.setSelectionMode(QAbstractItemView.ExtendedSelection)
        head = QListWidgetItem("Rohr 1 → Rohr 2 (Status):")
        head.setFlags(head.flags() & ~Qt.ItemIsSelectable)
        lw.addItem(head)


    def _append_row(self, entry):
        lw = self.ui.listWidget_Verbindungen
        sid = getattr(entry["line"], "status_id", self.default_status_id)
        stat = self.status_lut.get(sid, ("", ""))[0]
        tag = "DB" if entry.get("origin") == "existing" else "neu"

        l = entry["left"]; r = entry["right"]
        left_lbl  = f'{self._format_lr_label(l.lr_id)} · Rohr {l.rohrnr}'
        right_lbl = f'{self._format_lr_label(r.lr_id)} · Rohr {r.rohrnr}'

        text = f'[{tag}] {left_lbl} → {right_lbl} [{stat}]'
        lw.addItem(QListWidgetItem(text))

    def _rebuild_list(self):
        """
        Gruppiert nach Leerrohr-Paaren; Header fett/groß.
        Zeilen: eingerückt + Farbkacheln (1–2 je Seite).
        """
        lw = self.ui.listWidget_Verbindungen
        lw.clear()
        lw.setSelectionMode(QAbstractItemView.ExtendedSelection)

        groups, group_status = {}, {}
        for e in self.paired:
            l, r = e["left"], e["right"]
            lrL, lrR = int(l.lr_id), int(r.lr_id)
            key = (lrL, lrR) if lrL <= lrR else (lrR, lrL)
            groups.setdefault(key, []).append(e)
            sid = getattr(e.get("line"), "status_id", self.default_status_id)
            group_status.setdefault(key, set()).add(int(sid) if sid is not None else self.default_status_id)

        def _stat_text(sids):
            if not sids: return ""
            if len(sids) == 1:
                sid = list(sids)[0]
                return self.status_lut.get(sid, ("", ""))[0]
            return "gemischt"

        base_font = lw.font()
        header_font = QFont(base_font); header_font.setBold(True); header_font.setPointSize(max(8, base_font.pointSize() + 1))

        first = True
        for (lrA, lrB) in sorted(groups.keys()):
            if not first:
                sep = QListWidgetItem(""); sep.setFlags(sep.flags() & ~Qt.ItemIsSelectable); lw.addItem(sep)
            first = False

            hdr = QListWidgetItem(f'{self._format_lr_label(lrA)} ↔ {self._format_lr_label(lrB)} [{_stat_text(group_status[(lrA, lrB)])}]')
            hdr.setFont(header_font); hdr.setFlags(hdr.flags() & ~Qt.ItemIsSelectable)
            lw.addItem(hdr)

            entries = groups[(lrA, lrB)]
            entries.sort(key=lambda e: (int(e["left"].lr_id), int(e["left"].rohrnr), int(e["right"].rohrnr)))

            for e in entries:
                line = e.get("line")
                sid = getattr(line, "status_id", self.default_status_id)
                stat = self.status_lut.get(sid, ("", ""))[0]

                l = e["left"]; r = e["right"]
                # Farbnamen (Primär) für Text
                l_name = self._color_name_db(int(l.lr_id), int(l.rohrnr)) or getattr(l, "prim_name", "")
                r_name = self._color_name_db(int(r.lr_id), int(r.rohrnr)) or getattr(r, "prim_name", "")

                # Hexfarben (Primär/Sekundär) für Icons
                lp, ls = self._color_hexes_db(int(l.lr_id), int(l.rohrnr))
                rp, rs = self._color_hexes_db(int(r.lr_id), int(r.rohrnr))

                item = QListWidgetItem(f'    #{l.rohrnr} {l_name} → #{r.rohrnr} {r_name} [{stat}]')
                item.setIcon(self._color_icon_pair(lp or getattr(l, "prim_hex", None),
                                                ls, rp or getattr(r, "prim_hex", None), rs,
                                                size=12, gap=2))
                lw.addItem(item)

    def export_verbindungen_to_pdf(self, out_path: str):
        """
        Exportiert die komplette, gruppierte Liste als PDF (unabhängig vom sichtbaren Bereich).
        """
        try:
            from html import escape
            groups = {}
            group_status = {}
            for e in self.paired:
                l, r = e["left"], e["right"]
                lrL, lrR = int(l.lr_id), int(r.lr_id)
                key = (lrL, lrR) if lrL <= lrR else (lrR, lrL)
                groups.setdefault(key, []).append(e)
                sid = getattr(e.get("line"), "status_id", self.default_status_id)
                group_status.setdefault(key, set()).add(int(sid) if sid is not None else self.default_status_id)

            def _stat_text(sids):
                if not sids:
                    return ""
                if len(sids) == 1:
                    sid = list(sids)[0]
                    return self.status_lut.get(sid, ("", ""))[0]
                return "gemischt"

            # HTML aufbauen
            parts = ['<html><head><meta charset="utf-8"></head><body style="font-family:Sans-Serif;">']
            for (lrA, lrB) in sorted(groups.keys()):
                header = f'{escape(self._format_lr_label(lrA))} &#8646; {escape(self._format_lr_label(lrB))} [{escape(_stat_text(group_status[(lrA, lrB)]))}]'
                parts.append(f'<h3 style="margin:6px 0 4px 0;">{header}</h3>')
                parts.append('<ul style="margin:0 0 8px 24px; padding:0;">')
                entries = groups[(lrA, lrB)]
                entries.sort(key=lambda e: (int(e["left"].lr_id), int(e["left"].rohrnr), int(e["right"].rohrnr)))
                for e in entries:
                    line = e.get("line")
                    sid = getattr(line, "status_id", self.default_status_id)
                    stat = self.status_lut.get(sid, ("", ""))[0]

                    l = e["left"]; r = e["right"]
                    l_name = self._color_name_db(int(l.lr_id), int(l.rohrnr)) or getattr(l, "prim_name", "")
                    r_name = self._color_name_db(int(r.lr_id), int(r.rohrnr)) or getattr(r, "prim_name", "")

                    item = f'#{int(l.rohrnr)} {escape(str(l_name))} → #{int(r.rohrnr)} {escape(str(r_name))} [{escape(stat)}]'
                    parts.append(f'<li style="margin:0 0 2px 0;">{item}</li>')
                parts.append('</ul>')
            parts.append('</body></html>')
            html = ''.join(parts)

            # PDF schreiben
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(out_path)
            # A4, schmale Ränder
            try:
                from PyQt5.QtGui import QPageLayout, QPageSize
                layout = QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(10, 10, 10, 10))
                printer.setPageLayout(layout)
            except Exception:
                pass

            doc = QTextDocument()
            doc.setHtml(html)
            doc.print(printer)

            self._status(f"PDF exportiert: {out_path}")
        except Exception as e:
            self._status(f"PDF-Export fehlgeschlagen: {e}", ok=False)

    def _html_for_full_verbindungs_liste(self) -> str:
        from html import escape

        # ===== Schriftgrößen (pt) =====
        HDR_PT    = 32   # Gruppen-Header: "SRV-G … ⇆ … [geplant]"
        ROW_PT    = 24   # Zeilen: "■■#1 rot → #1 rot [geplant]"
        CHIP_PT   = 28   # Kästchen-Quadrate
        # ==============================

        # Gruppen bilden
        groups, group_status = {}, {}
        for e in getattr(self, "paired", []) or []:
            l, r = e.get("left"), e.get("right")
            if not l or not r:
                continue
            try:
                a, b = int(l.lr_id), int(r.lr_id)
            except Exception:
                continue
            key = (a, b) if a <= b else (b, a)
            groups.setdefault(key, []).append(e)
            sid = int(getattr(e.get("line"), "status_id", getattr(self, "default_status_id", 0)))
            group_status.setdefault(key, set()).add(sid)

        def _stat_text(sids):
            if not sids: return ""
            if len(sids) == 1:
                s = next(iter(sids))
                return self.status_lut.get(s, ("", ""))[0] if hasattr(self, "status_lut") else str(s)
            return "gemischt"

        def chip(hexcol: str) -> str:
            return f'<span class="chip" style="color:{escape(hexcol)};">&#9632;</span>' if hexcol else ""

        css = (
            f'<style>'
            f'body{{margin:0;padding:0;font-family:Sans-Serif;font-size:{ROW_PT}pt;line-height:1.20;}}'
            f'.gh{{font-size:{HDR_PT}pt;line-height:1.10;margin:4pt 0 2pt 0;}}'
            f'.glabel{{font-size:1em;font-weight:700;}}'                 # erbt Headergröße
            f'ul{{list-style:none;margin:2pt 0 6pt 0;padding:0;}}'       # kompakter Gruppenabstand
            f'li{{margin:0;padding:0;}}'
            f'.row{{margin:0.3pt 0;white-space:nowrap;}}'                # enger Zeilenabstand
            f'.chip{{font-size:{CHIP_PT}pt;line-height:0.95;margin-right:3pt;vertical-align:baseline;}}'
            f'</style>'
        )

        parts = [f'<html><head><meta charset="utf-8">{css}</head><body>']

        for (lrA, lrB) in sorted(groups):
            hdr = (
                f'<span class="glabel">{escape(self._format_lr_label(lrA))}</span> &#8646; '
                f'<span class="glabel">{escape(self._format_lr_label(lrB))}</span> '
                f'[{escape(_stat_text(group_status.get((lrA, lrB), set())))}]'
            )
            # WICHTIG: kein <h3>, sondern eigener Block .gh
            parts.append(f'<p class="gh">{hdr}</p>')

            entries = groups[(lrA, lrB)]
            entries.sort(key=lambda e: (int(e["left"].rohrnr), int(e["right"].rohrnr)))
            for e in entries:
                l, r = e["left"], e["right"]
                sid  = int(getattr(e.get("line"), "status_id", getattr(self, "default_status_id", 0)))
                stat = escape(self.status_lut.get(sid, ("", ""))[0]) if hasattr(self, "status_lut") else str(sid)

                l_name = self._color_name_db(int(l.lr_id), int(l.rohrnr)) or getattr(l, "prim_name", "")
                r_name = self._color_name_db(int(r.lr_id), int(r.rohrnr)) or getattr(r, "prim_name", "")
                lp, _  = self._color_hexes_db(int(l.lr_id), int(l.rohrnr))
                rp, _  = self._color_hexes_db(int(r.lr_id), int(r.rohrnr))

                line = f'{chip(lp)}{chip(rp)}' \
                    f'#{int(l.rohrnr)} {escape(str(l_name))} &#8594; ' \
                    f'#{int(r.rohrnr)} {escape(str(r_name))} [{stat}]'
                parts.append(f'<p class="row">{line}</p>')

        parts.append('</body></html>')
        return "".join(parts)

    def _color_icon_pair(self, l_prim: str, l_sec: str, r_prim: str, r_sec: str, size: int = 12, gap: int = 2):
        """
        Baut ein QIcon mit (links: 1–2 Kacheln) | (rechts: 1–2 Kacheln).
        """
        def _norm(c):
            return c if (isinstance(c, str) and c.startswith("#") and len(c) == 7) else None

        l_prim, l_sec = _norm(l_prim), _norm(l_sec)
        r_prim, r_sec = _norm(r_prim), _norm(r_sec)

        left_cnt  = 2 if l_sec else 1
        right_cnt = 2 if r_sec else 1
        spacer = gap * 3  # Abstand zwischen den Seiten

        w = left_cnt * size + right_cnt * size + gap * (left_cnt - 1 + right_cnt - 1) + spacer
        h = size
        px = QPixmap(w, h)
        px.fill(Qt.transparent)

        p = QPainter(px)
        x = 0
        # links
        for idx, col in enumerate(([l_prim, l_sec] if l_sec else [l_prim])):
            if col:
                p.fillRect(QRect(x, 0, size, size), QColor(col))
            x += size + (gap if (l_sec and idx == 0) else 0)
        x += spacer - (gap if l_sec else 0)
        # rechts
        for idx, col in enumerate(([r_prim, r_sec] if r_sec else [r_prim])):
            if col:
                p.fillRect(QRect(x, 0, size, size), QColor(col))
            x += size + (gap if (r_sec and idx == 0) else 0)
        p.end()

        return QIcon(px)

    # ---------- Auto nur für aktives Paar ----------
    def _find_bar_by_lr(self, side, lr_id):
        bars = self.left_bars if side==1 else self.right_bars
        for b in bars:
            if b['lr']['id']==lr_id: return b
        return None

    def _find_bar_for_combo_data(self, side: int, data):
        """Sucht den Bar auf Seite side anhand des Combo-Datums (mit evtl. Richtungsflag)."""
        bars = self.left_bars if side == 1 else self.right_bars
        if not data:
            return None
        lr_id = data["id"] if isinstance(data, dict) else (data.get("id") if hasattr(data, "get") else None)
        want_dir = data.get("dir") if isinstance(data, dict) else None
        for b in bars:
            if b['lr']['id'] == lr_id:
                if want_dir is None or b.get('dir') == want_dir:
                    return b
        return None

    def auto_pair_active(self):
        d1 = self.ui.comboBox_AktivLR1.currentData()
        d2 = self.ui.comboBox_AktivLR2.currentData()
        if not (d1 and d2):
            self._status("Aktives Paar oben wählen.", ok=False); return

        b_left  = self._find_bar_for_combo_data(1, d1)
        b_right = self._find_bar_for_combo_data(2, d2)
        if not (b_left and b_right):
            self._status("Aktives Paar nicht in Zeichnung.", ok=False); return

        made=0
        free_left  = sorted([n for n,r in b_left['rects'].items()  if not (r.occupied or r.used)])
        free_right = sorted([n for n,r in b_right['rects'].items() if not (r.occupied or r.used)])
        t=1
        while True:
            l = next((n for n in free_left  if n>=t and not b_left['rects'][n].used), None)
            r = next((n for n in free_right if n>=t and not b_right['rects'][n].used), None)
            if l is None or r is None: break
            self._make_pair(b_left['rects'][l], b_right['rects'][r]); made+=1; t+=1
        self._status(f"Automatisch verbunden: {made} Paare.", ok=(made>0))

    # ---------- Löschen / Check ----------
    def clear_pairs(self):
        """Entfernt alle Linien (neu & geladen) aus der Grafik und gibt Röhrchen frei (sicher)."""
        # Auswahl zuerst safen zurücksetzen
        self._clear_selection_highlight()

        for entry in list(self.paired):
            line = entry.get("line")
            if self._is_valid_graphics_item(line):
                try:
                    self.scene.removeItem(line)
                except Exception:
                    pass
            for rect in (entry.get("left"), entry.get("right")):
                if self._is_valid_graphics_item(rect):
                    try:
                        rect.used = False
                        rect.occupied = False
                        rect.setPen(QPen(Qt.black, 1))
                    except Exception:
                        pass

        self.paired.clear()
        self._reset_list_view()
        self._status("Alle (visuellen) Verbindungen entfernt. Neuverbinden möglich.")

    def run_check(self):
        if not (self.sel_lr1_list and self.sel_lr2_list):
            self._status("Bitte links & rechts wählen.", ok=False); return
        self._status("Datenprüfung: ok.")

    def _db_create_virtual_node(self, cur, lr_id: int, map_x: float, map_y: float, map_srid: int, common_knoten_id: int, position: float | None) -> int:
        """
        Legt einen *virtuellen* Knoten direkt in lwl."LWL_Knoten" an.
        - Transform: map_srid → 31254
        - Setzt: TYP='Virtueller Knoten', SUBTYP='split', LEERROHR_ID, POSITION (0..1)
        - KEINE CREATE*-Felder (übernehmen Trigger)
        """
        if not isinstance(common_knoten_id, int):
            raise ValueError("Kein gültiger ID_KNOTEN gesetzt (self.sel_node_id fehlt).")

        pos_val = None if position is None else float(max(0.0, min(1.0, position)))

        try:
            if pos_val is None:
                cur.execute(f"""
                    INSERT INTO lwl."LWL_Knoten"
                        ("TYP","SUBTYP","LEERROHR_ID","geom")
                    VALUES
                        ('Virtueller Knoten','split', %s,
                        ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s), %s), 31254))
                    RETURNING id
                """, (int(lr_id), float(map_x), float(map_y), int(map_srid)))
            else:
                cur.execute(f"""
                    INSERT INTO lwl."LWL_Knoten"
                        ("TYP","SUBTYP","LEERROHR_ID","geom","POSITION")
                    VALUES
                        ('Virtueller Knoten','split', %s,
                        ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s), %s), 31254),
                        %s)
                    RETURNING id
                """, (int(lr_id), float(map_x), float(map_y), int(map_srid), pos_val))

            new_id = cur.fetchone()[0]
            if not isinstance(new_id, int):
                raise RuntimeError("INSERT in LWL_Knoten hat keine gültige ID geliefert.")
            return new_id
        except Exception as e:
            raise RuntimeError(f'Virtueller Knoten konnte nicht angelegt werden: {e}')

    def _ensure_virtual_nodes_for_splits(self, cur):
        """
        Erzeugt – falls gesetzt – für left/right je einen virtuellen Knoten in der DB.
        Übergibt zusätzlich POSITION (0..1) an den Insert.
        """
        canvas = self.iface.mapCanvas()
        map_srid = canvas.mapSettings().destinationCrs().postgisSrid()

        self.split_virtual_node_ids = {"left": None, "right": None}

        if not getattr(self, "sel_node_id", None):
            raise RuntimeError("Es ist kein Knoten gewählt. Ohne Knoten keine Verbindung und keine virtuellen Knoten.")

        def _active_lr_id(side: str) -> int | None:
            cb = self.ui.comboBox_AktivLR1 if side == "left" else self.ui.comboBox_AktivLR2
            if cb is None:
                return None
            data = cb.currentData()
            if isinstance(data, dict) and "id" in data:
                return int(data["id"])
            if isinstance(data, int):
                return data
            return None

        for side in ("left", "right"):
            pt = (self.split_points.get(side) if hasattr(self, "split_points") else None)
            if not pt:
                continue

            lr_id = _active_lr_id(side)
            if not isinstance(lr_id, int):
                raise RuntimeError(f"Aktives Leerrohr für Seite '{side}' nicht verfügbar.")

            pos01 = None
            if hasattr(self, "split_position"):
                pos01 = self.split_position.get(side)

            kid = self._db_create_virtual_node(
                cur=cur,
                lr_id=lr_id,
                map_x=pt.x(),
                map_y=pt.y(),
                map_srid=map_srid,
                common_knoten_id=int(self.sel_node_id),
                position=pos01
            )
            self.split_virtual_node_ids[side] = kid

    def _start_split_pick(self, side: int):
        """
        Startet die Splitpunkt-Erfassung entlang des aktuell in comboBox_AktivLR[1|2] gewählten Leerrohrs.
        - rotes Kreuz gleitet entlang der LR-Geometrie (Map-CRS)
        - Linksklick fixiert den Punkt
        - speichert zusätzlich self.split_position['left'|'right'] (0..1)
        """
        # aktives LR je Seite auslesen
        lr_id = None
        try:
            cb = self.ui.comboBox_AktivLR1 if side == 1 else self.ui.comboBox_AktivLR2
            data = cb.currentData() if cb else None
            if isinstance(data, dict) and "id" in data:
                lr_id = int(data["id"])
            elif isinstance(data, int):
                lr_id = data
        except Exception:
            lr_id = None

        if not lr_id:
            self._status("Kein aktives Leerrohr ausgewählt.", ok=False)
            return

        lr_layer = self._get_layer("LWL_Leerrohr")
        if not lr_layer:
            self._status("Layer 'LWL_Leerrohr' nicht gefunden.", ok=False)
            return
        feat = next((f for f in lr_layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {lr_id}'))), None)
        if not feat or not feat.geometry():
            self._status("Leerrohr-Geometrie nicht gefunden.", ok=False)
            return

        try:
            g = QgsGeometry(feat.geometry())
            tr = QgsCoordinateTransform(lr_layer.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance())
            _ = g.transform(tr)
        except Exception as e:
            self._status(f"CRS-Transformation fehlgeschlagen: {e}", ok=False)
            return

        canvas = self.iface.mapCanvas()
        if not hasattr(self, "split_points"):
            self.split_points = {"left": None, "right": None}
        if not hasattr(self, "split_markers"):
            self.split_markers = {"left": None, "right": None}
        if not hasattr(self, "split_position"):
            self.split_position = {"left": None, "right": None}  # 0..1

        def _fix(pt_map_xy):
            key = "left" if side == 1 else "right"
            self.split_points[key] = pt_map_xy

            # Marker aktualisieren
            try:
                mk = self.split_markers.get(key)
                if mk: mk.hide()
            except Exception:
                pass
            mk = QgsVertexMarker(canvas)
            mk.setIconType(QgsVertexMarker.ICON_CROSS)
            mk.setIconSize(14)
            mk.setPenWidth(3)
            mk.setColor(Qt.red)
            mk.setCenter(pt_map_xy)
            mk.show()
            self.split_markers[key] = mk

            # Position (0..1) entlang der Linie berechnen
            perc = self._project_fraction(g, pt_map_xy)  # 0..100
            pos01 = max(0.0, min(1.0, (perc / 100.0)))
            self.split_position[key] = pos01

            # UI-Feedback
            label = self.ui.label_gewaehltes_leerrohr1 if side == 1 else self.ui.label_gewaehltes_leerrohr2
            try:
                label.setText(f"Split @ {perc:.1f}% von {self._format_lr_label(lr_id)}")
                label.setStyleSheet("background-color: lightgreen;")
            except Exception:
                pass
            self._status("Splitpunkt gesetzt.")

        self.map_tool = _SplitPointPickTool(canvas, g, _fix)
        canvas.setMapTool(self.map_tool)
        self._status("Bewege das rote Kreuz entlang des Leerrohrs. Linksklick fixiert den Splitpunkt.")

    # ---------- Import ----------
    def import_pairs(self):
        """
        Persistiert alle Änderungen:
        - Vor JEDEM Schreiben: falls Splitpunkte gesetzt → virtuelle Knoten in DB anlegen.
        - Rohr↔Rohr: INSERT neue, UPDATE Status geänderter, DELETE entfernte
        - Leerrohr↔Leerrohr: Status = einheitlicher Rohr-Status, sonst min(Status)
            * Insert/Update gemäß Aggregat
            * Delete, wenn zwischen den beiden Leerrohren keine Rohr-Paare mehr existieren
        - ID_KNOTEN wird IMMER aus self.sel_node_id gesetzt (nie NULL).
        """
        if not (self.is_connected and self.db):
            self._status("Import: keine DB-Verbindung.", ok=False)
            return

        if not getattr(self, "sel_node_id", None):
            self._status("Import: Es ist kein Knoten gewählt (ID_KNOTEN fehlt).", ok=False)
            return

        # --- aktuelle Szene in Sets überführen ---
        current_pairs = set()      # {(rid_min,rid_max)}
        current_status = {}        # {(rid_min,rid_max): status_id}
        lr_pairs_current = {}      # {(lr_left,lr_right): [status_id,]}

        for entry in self.paired:
            ra, rb = entry.get("rid_left"), entry.get("rid_right")
            if not (isinstance(ra, int) and isinstance(rb, int)):
                continue
            pair = (min(ra, rb), max(ra, rb))
            sid = int(getattr(entry["line"], "status_id", self.default_status_id))
            current_pairs.add(pair)
            current_status[pair] = sid

            lr_pair = (min(entry["left"].lr_id, entry["right"].lr_id),
                    max(entry["left"].lr_id, entry["right"].lr_id))
            lr_pairs_current.setdefault(lr_pair, []).append(sid)

        initial_pairs = getattr(self, "loaded_pairs_initial", set())
        initial_status = getattr(self, "loaded_status_by_pair", {})
        initial_lr_pairs = getattr(self, "loaded_lr_pairs_initial", set())

        to_insert = current_pairs - initial_pairs
        to_delete = initial_pairs - current_pairs
        to_update = set(p for p in (current_pairs & initial_pairs)
                        if current_status.get(p) != initial_status.get(p))

        try:
            import psycopg2
            with psycopg2.connect(**self.db) as conn:
                conn.autocommit = False
                with conn.cursor() as cur:
                    user = self.settings.value("connection_username", "unknown")

                    # Session als Verbinder kennzeichnen (wirkt nur innerhalb der Tx)
                    cur.execute("SET LOCAL application_name = 'leerrohr_verbinder'")

                    # --- NEU: virtuelle Knoten für gesetzte Splitpunkte erzeugen ---
                    # (macht nichts, wenn keine Splitpunkte gesetzt sind)
                    self._ensure_virtual_nodes_for_splits(cur)

                    # --- DELETE (Rohr↔Rohr) ---
                    del_cnt = 0
                    for a, b in to_delete:
                        cur.execute("""
                            DELETE FROM lwl."LWL_Rohr_Rohr_rel"
                            WHERE LEAST("ID_ROHR_1","ID_ROHR_2")=%s
                            AND GREATEST("ID_ROHR_1","ID_ROHR_2")=%s
                        """, (a, b))
                        del_cnt += cur.rowcount

                    # --- UPDATE (Rohr↔Rohr) ---
                    upd_cnt = 0
                    for a, b in to_update:
                        sid = current_status[(a, b)]
                        cur.execute("""
                            UPDATE lwl."LWL_Rohr_Rohr_rel"
                            SET "STATUS"=%s, "UPDATEUSER"=%s, "UPDATETIME"=now()
                            WHERE ( "ID_ROHR_1"=%s AND "ID_ROHR_2"=%s )
                            OR ( "ID_ROHR_1"=%s AND "ID_ROHR_2"=%s )
                        """, (sid, user, a, b, b, a))
                        upd_cnt += cur.rowcount

                    # --- INSERT (Rohr↔Rohr) ---
                    ins_cnt = 0

                    # Map (rid_min,rid_max) -> (lr_left, lr_right)
                    rid_to_lr = {}
                    for entry in self.paired:
                        ra, rb = entry.get("rid_left"), entry.get("rid_right")
                        if not (isinstance(ra, int) and isinstance(rb, int)):
                            continue
                        pair = (min(ra, rb), max(ra, rb))
                        rid_to_lr[pair] = (entry["left"].lr_id, entry["right"].lr_id)

                    # IMMER: ID_KNOTEN = gewählter Knoten
                    kn = int(self.sel_node_id)

                    for a, b in to_insert:
                        sid = current_status[(a, b)]
                        cur.execute("""
                            INSERT INTO lwl."LWL_Rohr_Rohr_rel"
                            ("ID_ROHR_1","ID_ROHR_2","STATUS","CREATEUSER","CREATETIME","ID_KNOTEN")
                            SELECT %s,%s,%s,%s,now(),%s
                            WHERE NOT EXISTS (
                            SELECT 1 FROM lwl."LWL_Rohr_Rohr_rel"
                            WHERE (("ID_ROHR_1"=%s AND "ID_ROHR_2"=%s) OR ("ID_ROHR_1"=%s AND "ID_ROHR_2"=%s))
                            )
                        """, (a, b, sid, user, kn, a, b, b, a))
                        ins_cnt += cur.rowcount

                    # --- LR↔LR-Relation: Aggregat-Status je LR-Paar ---
                    lr_pairs_all = set(lr_pairs_current.keys()) | set(initial_lr_pairs)
                    for lr_left, lr_right in lr_pairs_all:
                        cur.execute("""
                            SELECT DISTINCT rel."STATUS"
                            FROM lwl."LWL_Rohr_Rohr_rel" rel
                            JOIN lwl."LWL_Rohr" a ON a.id = rel."ID_ROHR_1"
                            JOIN lwl."LWL_Rohr" b ON b.id = rel."ID_ROHR_2"
                            WHERE (a."ID_LEERROHR"=%s AND b."ID_LEERROHR"=%s)
                            OR (a."ID_LEERROHR"=%s AND b."ID_LEERROHR"=%s)
                        """, (lr_left, lr_right, lr_right, lr_left))
                        st_list = [int(x[0]) for x in cur.fetchall()]

                        if not st_list:
                            cur.execute("""
                                DELETE FROM lwl."LWL_Leerrohr_Leerrohr_rel"
                                WHERE ( "ID_LEERROHR_1"=%s AND "ID_LEERROHR_2"=%s )
                                OR ( "ID_LEERROHR_1"=%s AND "ID_LEERROHR_2"=%s )
                            """, (lr_left, lr_right, lr_right, lr_left))
                            continue

                        agg = st_list[0] if all(s == st_list[0] for s in st_list) else min(st_list)

                        # IMMER: ID_KNOTEN = gewählter Knoten
                        cur.execute("""
                            SELECT id FROM lwl."LWL_Leerrohr_Leerrohr_rel"
                            WHERE ( "ID_LEERROHR_1"=%s AND "ID_LEERROHR_2"=%s )
                            OR ( "ID_LEERROHR_1"=%s AND "ID_LEERROHR_2"=%s )
                            LIMIT 1
                        """, (lr_left, lr_right, lr_right, lr_left))
                        row = cur.fetchone()
                        if row:
                            cur.execute("""
                                UPDATE lwl."LWL_Leerrohr_Leerrohr_rel"
                                SET "STATUS"=%s, "UPDATEUSER"=%s, "UPDATETIME"=now(),
                                    "ID_KNOTEN"=%s
                                WHERE id=%s
                            """, (agg, user, kn, row[0]))
                        else:
                            cur.execute("""
                                INSERT INTO lwl."LWL_Leerrohr_Leerrohr_rel"
                                ("ID_LEERROHR_1","ID_LEERROHR_2","STATUS","VERBUND_TYP","CREATEUSER","CREATETIME","ID_KNOTEN")
                                VALUES (%s,%s,%s,%s,%s,now(),%s)
                            """, (lr_left, lr_right, agg, "standard", user, kn))

                conn.commit()

            # neuen Ausgangszustand setzen
            self.loaded_pairs_initial = set(current_pairs)
            self.loaded_status_by_pair = dict(current_status)
            self.loaded_lr_pairs_initial = set(lr_pairs_current.keys())

            self._status("Import/Update ok. (Virtuelle Knoten wurden – falls vorhanden – angelegt.)")
        except Exception as e:
            self._status(f"Import fehlgeschlagen: {e}", ok=False)

    def _on_mode_changed(self, *_):
        """Reaktiviert die Leerrohrlisten beim Umschalten parallel/lotrecht nach bestätigter Trassenauswahl."""
        if self.phase != "leerrohre" or self.sel_node_id is None:
            return

        # WICHTIG: Auswahl vor scene.clear() aufräumen!
        self._clear_selection_highlight()

        mode_left  = "parallel" if self.ui.checkBox_1.isChecked() else "lotrecht"
        mode_right = "parallel" if self.ui.checkBox_2.isChecked() else "lotrecht"

        lr_left  = self._leerrohre_for_trasse_and_mode(self.sel_tr_left,  side="left",  mode=mode_left)
        lr_right = self._leerrohre_for_trasse_and_mode(self.sel_tr_right, side="right", mode=mode_right)

        # Dedupe
        lr_left  = self._dedupe_feats_by_id(lr_left)
        lr_right = self._dedupe_feats_by_id(lr_right)
        lr_left, lr_right = self._dedupe_across_parallel_sides(lr_left, lr_right, mode_left, mode_right)

        # Szene neu
        try:
            self.scene.clear()
        except Exception:
            pass
        self.paired.clear()
        self.sel_rect_left = None
        self.left_bars, self.right_bars = [], []

        self._fill_listwidget_from_feats(self.ui.listWidget_Leerohr1, lr_left)
        self._fill_listwidget_from_feats(self.ui.listWidget_Leerohr2, lr_right)

        for bn in ("pushButton_automatisch","pushButton_verbindung_loeschen","pushButton_Datenpruefung","pushButton_Import"):
            getattr(self.ui, bn).setEnabled(False)

        self._status("Modus geändert. Leerrohre neu wählen → 'Auswahl bestätigen'.")

    def _passes_through_node(self, tr_list, node_id):
        """
        True, wenn das Leerrohr den Knoten wirklich 'passiert':
        Es gibt mindestens ein Paar ADJAZENTER Trassen im Verlauf,
        die beide den node_id als FROM/TO besitzen.
        """
        if not tr_list or len(tr_list) < 2:
            return False

        tr_layer = self._get_layer("LWL_Trasse")
        if not tr_layer:
            return False

        names = [f.name() for f in tr_layer.fields()]
        from_nm = "VONKNOTEN" if "VONKNOTEN" in names else ("FROMNODE" if "FROMNODE" in names else None)
        to_nm   = "NACHKNOTEN" if "NACHKNOTEN" in names else ("TONODE" if "TONODE" in names else None)
        if not (from_nm and to_nm):
            return False

        # Welche Trassen aus tr_list liegen überhaupt am node_id an?
        tr_ids = list({int(t) for t in tr_list if t is not None})
        if not tr_ids:
            return False

        expr = f'"id" IN ({",".join(str(t) for t in tr_ids)})'
        tr_node = set()
        try:
            for f in tr_layer.getFeatures(QgsFeatureRequest().setFilterExpression(expr)):
                if int(f[from_nm]) == node_id or int(f[to_nm]) == node_id:
                    tr_node.add(int(f["id"]))
        except Exception:
            return False

        # Adjazente Paare im Verlauf prüfen
        for i in range(len(tr_list)-1):
            a, b = int(tr_list[i]), int(tr_list[i+1])
            if a in tr_node and b in tr_node:
                return True
        return False

    def _dedupe_feats_by_id(self, feats_like):
        """Entfernt Duplikate anhand d['id'] (stabile Reihenfolge, erster gewinnt)."""
        seen = set()
        out = []
        for d in feats_like:
            _id = int(d.get("id")) if d and "id" in d else None
            if _id is None or _id in seen:
                continue
            seen.add(_id)
            out.append(d)
        return out

    def _dedupe_across_parallel_sides(self, left_list, right_list, mode_left, mode_right):
        """
        Wenn BEIDE Seiten 'parallel' sind: kein identisches Leerrohr in beiden Listen.
        Entfernt Überschneidungen aus der RECHTEN Liste.
        """
        if mode_left != "parallel" or mode_right != "parallel":
            return self._dedupe_feats_by_id(left_list), self._dedupe_feats_by_id(right_list)

        left_list = self._dedupe_feats_by_id(left_list)
        right_list = self._dedupe_feats_by_id(right_list)

        left_ids = {int(d["id"]) for d in left_list if "id" in d}
        right_nodup = [d for d in right_list if int(d.get("id")) not in left_ids]
        return left_list, right_nodup

    def _rect_to_rid(self, rect: "ClickableRect"):
        """Ermittelt die ID des LWL_Rohr-Datensatzes (rid) zum Kästchen."""
        bars = self.left_bars if rect.side == 1 else self.right_bars
        if rect.bar_idx < 0 or rect.bar_idx >= len(bars):
            return None
        b = bars[rect.bar_idx]
        return b['ids'].get(rect.rohrnr)
    
    def _on_status_global_combo(self, *_):
        """Setzt den gewählten Status (ID) auf ALLE Linien (neu & geladen) – ohne Crash bei gelöschten Items."""
        sid = self.ui.comboBox_StatusGlobal.currentData()
        if sid is None:
            return
        self.default_status_id = sid

        for entry in self.paired:
            line = entry.get("line")
            if not self._is_valid_graphics_item(line):
                continue
            try:
                line.status_id = sid
                pen = self._pen_for_status(sid)
                if entry is getattr(self, "selected_entry", None):
                    pen.setWidth(4)
                line.setPen(pen)
            except Exception:
                pass

        self._rebuild_list()
        self._status("Status global angewendet.")

    def on_export_pdf_clicked(self):
        """PDF-Export: Seite 1 = Grafik (Screenshot), Seite 2 = Tabelle (HTML), Logo-Offset 5.8 mm."""
        from PyQt5.QtGui import QPainter, QPdfWriter, QPagedPaintDevice, QFont, QPen, QColor, QTextDocument
        from PyQt5.QtWidgets import QFileDialog
        from PyQt5.QtCore import QRectF, Qt, QDateTime, QSizeF

        # feste Logo-Parameter
        self.logo_height_mm = 12.0
        self.logo_x_offset_mm = 0.0
        self.logo_y_offset_mm = 5.0

        # Datei wählen
        fn, _ = QFileDialog.getSaveFileName(self, "PDF speichern",
                                            "leerrohr_verbindungen.pdf", "PDF (*.pdf)")
        if not fn:
            return
        if not fn.lower().endswith(".pdf"):
            fn += ".pdf"

        # Writer
        writer = QPdfWriter(fn)
        writer.setResolution(300)  # 300 dpi
        writer.setPageSize(QPagedPaintDevice.A4)

        res = writer.resolution()
        mm = lambda v: (v / 25.4) * float(res)
        margin   = mm(10)
        header_h = mm(20)
        footer_h = mm(10)

        def draw_header(p, title, page_w):
            logo = self._load_logo_pixmap()
            # Logo links (Offset 5.8 mm)
            if not logo.isNull():
                lw, lh = float(logo.width()), float(logo.height())
                target_h = mm(getattr(self, "logo_height_mm", 12.0))
                scale = target_h / lh if lh > 0 else 1.0
                draw_w = lw * scale
                draw_h = target_h
                x = margin + mm(getattr(self, "logo_x_offset_mm", 0.0))
                y = margin + mm(getattr(self, "logo_y_offset_mm", 5.8))
                p.drawPixmap(QRectF(x, y, draw_w, draw_h), logo, QRectF(0, 0, lw, lh))

            # Titel rechtsbündig
            f = QFont(); f.setPointSize(14); f.setBold(True)
            p.setFont(f); p.setPen(QPen(QColor("#000")))
            title_rect = QRectF(margin, margin, page_w - 2*margin, header_h)
            p.drawText(title_rect, Qt.AlignVCenter | Qt.AlignRight, title)

            # Trennlinie
            pen = QPen(QColor("#999")); pen.setWidthF(0.6)
            p.setPen(pen)
            y_line = margin + header_h + mm(1)
            p.drawLine(margin, y_line, page_w - margin, y_line)
            return y_line

        def draw_footer(p, page_w, page_h, page_no):
            footer_top = page_h - margin - footer_h
            f2 = QFont(); f2.setPointSize(8)
            p.setFont(f2); p.setPen(QPen(QColor("#000")))
            rect = QRectF(margin, footer_top, page_w - 2*margin, footer_h)
            p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft,
                    QDateTime.currentDateTime().toString("dd.MM.yyyy HH:mm"))
            p.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, f"Seite {page_no}")
            return footer_top

        painter = QPainter(writer)
        try:
            page_w = float(writer.width())
            page_h = float(writer.height())

            # --- Seite 1: Grafik ---
            y_line = draw_header(painter, "Leerrohr‑Verbindungen – Übersicht", page_w)
            footer_top = draw_footer(painter, page_w, page_h, 1)
            content_rect = QRectF(margin, y_line + mm(2),
                                page_w - 2*margin,
                                max(10.0, footer_top - mm(2) - (y_line + mm(2))))

            pix = self.ui.graphicsView_Auswahl_Rrohr1.viewport().grab()
            if not pix.isNull():
                self._draw_pixmap_fit(painter, pix, content_rect)
            else:
                painter.drawText(content_rect, Qt.AlignCenter, "Keine Grafik vorhanden.")

            # --- Seite 2: Tabelle (HTML wie im Tool-Style) ---
            writer.newPage()
            y_line = draw_header(painter, "Rohr↔Rohr‑Verbindungen – Tabelle", page_w)
            footer_top = draw_footer(painter, page_w, page_h, 2)
            content_rect = QRectF(margin, y_line + mm(2),
                                page_w - 2*margin,
                                max(10.0, footer_top - mm(2) - (y_line + mm(2))))

            doc = QTextDocument()
            html = self._html_for_full_verbindungs_liste()
            doc.setHtml(html)
            doc.setPageSize(QSizeF(content_rect.width(), content_rect.height()))
            painter.save()
            painter.translate(content_rect.left(), content_rect.top())
            doc.drawContents(painter, QRectF(0, 0, content_rect.width(), content_rect.height()))
            painter.restore()

        finally:
            painter.end()

        self._status(f"PDF exportiert: {fn}", ok=True)

    def _load_logo_pixmap(self):
        """Logo robust laden:
        1) self.logo_path (falls gesetzt)
        2) Dateipfad im Plugin-Ordner: <plugin>/icons/logo.png
        3) mehrere Qt-Resource-Pfade (falls resources_rc registriert ist)
        """
        from PyQt5.QtGui import QPixmap
        import os

        # 1) expliziter Pfad (optional): self.logo_path = r"...\plugins\ToolBox_SiegeleCo\icons\logo.png"
        p = getattr(self, "logo_path", None)
        if p and os.path.exists(p):
            pm = QPixmap(p)
            if not pm.isNull():
                return pm

        # 2) Plugin-Ordner relativ zu diesem Modul
        #    ...\plugins\ToolBox_SiegeleCo\tools\leerrohr_verbinder\leerrohr_verbinden.py
        #    -> zwei Ebenen hoch = Plugin-Root
        plugin_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        fs_candidates = [
            os.path.join(plugin_root, 'icons', 'logo.png'),
            os.path.join(plugin_root, 'resources', 'logo.png'),
        ]
        for path in fs_candidates:
            if os.path.exists(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    return pm

        # 3) Qt-Resources – mehrere mögliche Präfixe testen
        #    (resources_rc muss importiert sein – ist es bei dir bereits)
        res_candidates = [
            ':/plugins/ToolBox_SiegeleCo/icons/logo.png',
            ':/plugins/ToolBox_SiegeleCo/resources/logo.png',
            ':/plugins/ToolBox_SiegeleCo/logo.png',
            ':/ToolBox_SiegeleCo/icons/logo.png',
            ':/icons/logo.png',
        ]
        for r in res_candidates:
            pm = QPixmap(r)
            if not pm.isNull():
                return pm

        # nichts gefunden
        return QPixmap()


    def _draw_pixmap_fit(self, painter, pixmap, target_rect):
        """Zeichnet ein Pixmap proportional skaliert in target_rect (best fit, zentriert)."""
        from PyQt5.QtCore import QRectF  # lokal, damit beim Klassendefinitions-Zeitpunkt nichts fehlt

        if pixmap is None or pixmap.isNull():
            return
        src_w = float(pixmap.width())
        src_h = float(pixmap.height())
        if src_w <= 0 or src_h <= 0:
            return

        scale = min(target_rect.width() / src_w, target_rect.height() / src_h)
        draw_w = src_w * scale
        draw_h = src_h * scale
        dx = target_rect.x() + (target_rect.width() - draw_w) / 2.0
        dy = target_rect.y() + (target_rect.height() - draw_h) / 2.0
        painter.drawPixmap(QRectF(dx, dy, draw_w, draw_h), pixmap, QRectF(0, 0, src_w, src_h))

    def _mm_to_px(self, mm: float, dpi: int) -> float:
        return (mm / 25.4) * float(dpi)



