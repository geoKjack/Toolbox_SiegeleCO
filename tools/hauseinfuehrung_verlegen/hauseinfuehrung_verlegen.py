# -*- coding: utf-8 -*-
"""
HauseinfuehrungsVerlegungsTool
Verlegt Hauseinführungen durch Auswahl von Parent Leerrohr, Verlauf und Endpunkten.
"""

from PyQt5.QtCore import Qt, QRectF, QObject, pyqtSignal, QPointF, QSettings, QDateTime
from PyQt5.QtWidgets import QDialog, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsRectItem, QGraphicsPolygonItem, QDialogButtonBox, QLineEdit, QTextEdit, QGraphicsLineItem
from qgis.core import QgsProject, Qgis, QgsFeatureRequest, QgsCoordinateTransform, QgsDataSourceUri, QgsWkbTypes, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsFeature, QgsCoordinateReferenceSystem, QgsMessageLog
from qgis.gui import QgsHighlight, QgsMapToolEmitPoint, QgsRubberBand, QgsMapTool
from PyQt5.QtGui import QColor, QBrush, QFont, QPolygonF, QMouseEvent, QPen
import psycopg2
import base64
import json
from .hauseinfuehrung_verlegen_dialog import Ui_HauseinfuehrungsVerlegungsToolDialogBase

class GuidedStartLineTool(QgsMapTool):
    """
    Geführte HA-Digitalisierung:
    - mode="ha": Abzweigung von Bestands-HE → Vorschau + Übernahme des Korridors
                 ENTLANG DER BESTEHENDEN HE vom LR-Ende bis zum Abzweigpunkt.
    - mode="lr": Andocken am Leerrohr → KEIN Korridor, nur Schnittpunkt (optional clamping).
    - Nach dem 1. Klick: freie Digitalisierung mit Live-Vorschau (rote Linie).
    - init_preview(geom): rote Linie nach Import sofort anzeigen.
    """
    def __init__(self, canvas, snap_ref_geom, on_finish, mode="lr",
                 vkg_hint_id=None, persist_rb=None, free_interval=None, parent_lr_geom=None):
        super().__init__(canvas)
        from qgis.gui import QgsVertexMarker
        self.canvas = canvas
        self.snap_ref_geom = snap_ref_geom      # QgsGeometry (Point/Line/MultiLine): HE-Geom bei mode="ha", LR-Geom bei mode="lr"
        self.on_finish = on_finish
        self.mode = mode
        self.vkg_hint_id = vkg_hint_id
        self.persist_rb = persist_rb
        self.points = []
        self.guided_done = False
        self.free_interval = free_interval      # (a,b) in [0..1] nur für mode="lr"
        self.parent_lr_geom = parent_lr_geom    # Geom des Parent-LR; bei mode="ha" zum Erkennen des LR-Endes der HE nutzen

        self.tmp_rb = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self.tmp_rb.setWidth(2); self.tmp_rb.setColor(Qt.red)

        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setIconType(self.marker.ICON_CROSS)
        self.marker.setIconSize(12)
        self.marker.setPenWidth(2)
        self.marker.hide()
        self.setCursor(Qt.CrossCursor)

    # ---------- Public helper ----------
    def init_preview(self, geom: QgsGeometry):
        """Import-Preview: rote Linie sofort anzeigen (z.B. nach erfolgreichem Import)."""
        try:
            if geom and not geom.isEmpty():
                self.tmp_rb.setToGeometry(geom, None)
                if self.persist_rb:
                    self.persist_rb.setToGeometry(geom, None)
        except Exception:
            pass

    # ---------- Geometry helpers ----------
    def _toMap(self, pos):
        return self.canvas.getCoordinateTransform().toMapCoordinates(pos.x(), pos.y())

    def _closest_on(self, geom_line, mappt):
        return QgsPointXY(geom_line.closestSegmentWithContext(mappt)[1])

    def _polyline_parts(self, g: QgsGeometry):
        if not g or g.isEmpty(): return []
        if g.isMultipart():
            m = g.asMultiPolyline() or []
            return [[QgsPointXY(p) for p in part] for part in m if part]
        pl = g.asPolyline() or []
        return [[QgsPointXY(p) for p in pl]] if pl else []

    def _line_length(self, pts):
        d = 0.0
        for i in range(1, len(pts)):
            dx = pts[i].x()-pts[i-1].x(); dy = pts[i].y()-pts[i-1].y()
            d += (dx*dx + dy*dy) ** 0.5
        return d

    def _project_fraction(self, geom_line, pt):
        """s ∈ [0..1] entlang geom_line (für clamping)."""
        parts = self._polyline_parts(geom_line)
        if not parts: return 0.0
        p = self._closest_on(geom_line, pt)
        seg = parts[0]
        total = self._line_length(seg) or 1e-12
        run = 0.0; best = 0.0; bestd = 1e18
        for i in range(1, len(seg)):
            a, b = seg[i-1], seg[i]
            ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
            vx, vy = bx-ax, by-ay
            wx, wy = p.x()-ax, p.y()-ay
            denom = (vx*vx + vy*vy) or 1e-12
            t = max(0.0, min(1.0, (vx*wx + vy*wy)/denom))
            cx, cy = ax + t*vx, ay + t*vy
            d2 = (p.x()-cx)**2 + (p.y()-cy)**2
            if d2 < bestd:
                bestd = d2
                best = (run + ( (vx*vx + vy*vy) ** 0.5 ) * t) / total
            run += ((vx*vx + vy*vy) ** 0.5)
        return max(0.0, min(1.0, best))

    def _clamp_on_interval(self, geom_line, mappt):
        """Nur für mode='lr': projiziere auf Linie und clamp s in [a,b]."""
        if self.mode != "lr" or not self.free_interval or not geom_line or geom_line.isEmpty():
            return self._closest_on(geom_line, mappt)
        a, b = self.free_interval
        a = max(0.0, min(1.0, float(a))); b = max(0.0, min(1.0, float(b)))
        if a >= b:
            return None
        s = self._project_fraction(geom_line, mappt)
        s = max(a, min(b, s))
        parts = self._polyline_parts(geom_line); seg = parts[0]
        total = self._line_length(seg) or 1e-12
        target = s * total
        acc = 0.0
        for i in range(1, len(seg)):
            a0, a1 = seg[i-1], seg[i]
            seglen = ((a1.x()-a0.x())**2 + (a1.y()-a0.y())**2) ** 0.5
            if acc + seglen >= target:
                t = (target - acc) / (seglen or 1e-12)
                return QgsPointXY(a0.x() + t*(a1.x()-a0.x()), a0.y() + t*(a1.y()-a0.y()))
            acc += seglen
        return QgsPointXY(seg[-1])

    # ---------- HA-Korridor (vom LR-Ende!) ----------
    def _pick_lr_end_index_for_ha(self, he_geom: QgsGeometry, lr_geom_hint: QgsGeometry):
        """
        Bestimmt, welches Ende der Bestands-HE am LR liegt.
        Nutzt die Distanz der beiden Endpunkte zu lr_geom_hint.
        Rückgabe: 0 oder len(poly)-1 (Index des LR-Endes).
        """
        parts = self._polyline_parts(he_geom)
        if not parts: return 0
        poly = parts[0]
        if len(poly) < 2 or not lr_geom_hint or lr_geom_hint.isEmpty():
            # Fallback: nehme das Ende, das näher an der Marker-Position ist
            try:
                vpt = QgsPointXY(self.marker.center())
                d0 = QgsGeometry.fromPointXY(poly[0]).distance(QgsGeometry.fromPointXY(vpt))
                d1 = QgsGeometry.fromPointXY(poly[-1]).distance(QgsGeometry.fromPointXY(vpt))
                return 0 if d0 <= d1 else len(poly)-1
            except Exception:
                return 0
        d0 = lr_geom_hint.distance(QgsGeometry.fromPointXY(poly[0]))
        d1 = lr_geom_hint.distance(QgsGeometry.fromPointXY(poly[-1]))
        return 0 if d0 <= d1 else len(poly)-1

    def _corridor_for_ha(self, he_geom: QgsGeometry, snap_pt: QgsPointXY, lr_geom_hint: QgsGeometry):
        """
        Liefert die Punkte des Korridors der Bestands-HE VOM LR-Ende BIS zum Snap-Punkt.
        he_geom: Geometrie der bestehenden HE (i. d. R. HA→LR gespeichert).
        lr_geom_hint: Geometrie des zugehörigen LR (zur Endpunkt-Wahl).
        """
        parts = self._polyline_parts(he_geom)
        if not parts: return [snap_pt]
        poly = parts[0]

        # 1) Orientierung: vom LR-Ende starten
        lr_end_idx = self._pick_lr_end_index_for_ha(he_geom, lr_geom_hint)
        oriented = poly if lr_end_idx == 0 else list(reversed(poly))

        # 2) Segment bestimmen, das dem Snap am nächsten ist (auf der orientierten Polyline)
        best_i, best_d = 0, float("inf")
        qg = QgsGeometry.fromPointXY(snap_pt)
        for i in range(len(oriented)-1):
            d = QgsGeometry.fromPolylineXY([oriented[i], oriented[i+1]]).distance(qg)
            if d < best_d:
                best_d, best_i = d, i

        # 3) Korridor: vom LR-Ende bis inkl. best_i, dann bis Snap
        corr = oriented[:best_i+1]
        corr.append(snap_pt)
        return corr

    def _draw_preview(self, tail_point):
        self.tmp_rb.reset(QgsWkbTypes.LineGeometry)
        for p in self.points:
            self.tmp_rb.addPoint(p)
        if tail_point is not None:
            self.tmp_rb.addPoint(tail_point)

    # ---------- Events ----------
    def canvasMoveEvent(self, e):
        mappt = self._toMap(e.pos())
        if not self.guided_done and self.snap_ref_geom and not self.snap_ref_geom.isEmpty():
            if QgsWkbTypes.geometryType(self.snap_ref_geom.wkbType()) == QgsWkbTypes.PointGeometry:
                mappt = QgsPointXY(self.snap_ref_geom.asPoint())
                self.tmp_rb.reset()
                self.marker.setCenter(mappt); self.marker.show()
                return

            if self.mode == "lr":
                g = self.parent_lr_geom if (self.parent_lr_geom) else self.snap_ref_geom
                mappt = self._clamp_on_interval(g, mappt)
                # Vorschau: nur Startpunkt→Mauspunkt (kein Korridor im LR-Modus)
                self._draw_preview(mappt)
                self.marker.setCenter(mappt); self.marker.show()
                return

            # mode == "ha": Vorschau entlang Bestands-HE vom LR-Ende bis Snap
            snap_on_he = self._closest_on(self.snap_ref_geom, mappt)
            pts = self._corridor_for_ha(self.snap_ref_geom, snap_on_he, self.parent_lr_geom)
            self.tmp_rb.setToGeometry(QgsGeometry.fromPolylineXY(pts), None)
            self.marker.setCenter(snap_on_he); self.marker.show()
            return

        # nach dem 1. Punkt: freie Vorschau
        self.marker.hide()
        if self.points:
            self._draw_preview(mappt)

    def canvasPressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        mappt = self._toMap(e.pos())

        if not self.guided_done and self.snap_ref_geom and not self.snap_ref_geom.isEmpty():
            if QgsWkbTypes.geometryType(self.snap_ref_geom.wkbType()) == QgsWkbTypes.PointGeometry:
                self.points.append(QgsPointXY(self.snap_ref_geom.asPoint()))
            else:
                if self.mode == "lr":
                    g = self.parent_lr_geom if (self.parent_lr_geom) else self.snap_ref_geom
                    snap_pt = self._clamp_on_interval(g, mappt)
                    if snap_pt is None:
                        self.canvas.scene().addSimpleText("Kein freier Rohrabschnitt verfügbar.")
                        return
                    self.points.append(QgsPointXY(snap_pt))
                else:
                    snap_on_he = self._closest_on(self.snap_ref_geom, mappt)
                    pts = self._corridor_for_ha(self.snap_ref_geom, snap_on_he, self.parent_lr_geom)
                    self.points.extend(pts if pts else [QgsPointXY(snap_on_he)])

            self.guided_done = True
            self.marker.hide()
            self._draw_preview(None)

        else:
            self.points.append(QgsPointXY(mappt))
            self._draw_preview(None)

    def canvasReleaseEvent(self, e):
        if e.button() == Qt.RightButton:
            if len(self.points) >= 2 and self.on_finish:
                self.on_finish(self.points)
                if self.persist_rb and len(self.points) >= 2:
                    self.persist_rb.setToGeometry(QgsGeometry.fromPolylineXY(self.points), None)
            self.deactivate(); self.canvas.unsetMapTool(self)

    def deactivate(self):
        super().deactivate()
        try:
            self.tmp_rb.reset()
            self.marker.hide()
        except Exception:
            pass
        self.points = []

class ClickableRect(QGraphicsRectItem):
    def __init__(self, x, y, width, height, rohrnummer, farb_id, callback, parent=None):
        super().__init__(x, y, width, height, parent)
        self.rohrnummer = rohrnummer  # Speichere die zugehörige Rohrnummer
        self.farb_id = farb_id        # Speichere die zugehörige FARBE-ID
        self.callback = callback      # Übergib die Callback-Funktion

    def mousePressEvent(self, event):
        """Wird ausgelöst, wenn auf das Quadrat geklickt wird."""
        if self.callback:
            self.callback(self.rohrnummer, self.farb_id)  # Rufe die Callback-Funktion mit Rohrnummer und FARBE-ID auf
        super().mousePressEvent(event)

class ClickSelector(QgsMapTool):
    def __init__(self, canvas, layers, callback, iface):
        super().__init__(canvas)
        self.canvas = canvas
        self.layers = layers
        self.callback = callback
        self.iface = iface
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, event):
        from qgis.core import QgsPointXY, QgsGeometry, QgsTolerance

        point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos().x(), event.pos().y())
        closest_feature = None
        closest_layer = None
        closest_dist = float('inf')

        tolerance = QgsTolerance.vertexSearchRadius(self.canvas.mapSettings())

        for layer in self.layers:
            if not layer.isValid():
                continue
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if geom and not geom.isEmpty():
                    dist = geom.distance(QgsGeometry.fromPointXY(QgsPointXY(point)))
                    if dist < closest_dist and dist <= tolerance:
                        closest_dist = dist
                        closest_feature = feat
                        closest_layer = layer

        if closest_feature:
            self.callback(closest_feature, closest_layer)
        else:
            self.iface.messageBar().pushMessage("Hinweis", "Kein passendes Objekt im Toleranzbereich gefunden.", level=Qgis.Warning)

class CustomLineCaptureTool(QgsMapTool):
    """Digitalisiert eine Linie mit Live-Vorschau:
       - 1. Punkt: sichtbar, an Snap-Geometrie geführt (Knoten fix, Rohr entlang Linie)
       - weitere Punkte: frei, mit RubberBand-Vorschau
    """
    def __init__(self, canvas, capture_callback, finalize_callback, snap_geometry=None):
        super().__init__(canvas)
        from qgis.gui import QgsVertexMarker
        self.canvas = canvas
        self.capture_callback = capture_callback
        self.finalize_callback = finalize_callback
        self.points = []
        self.snap_geometry = snap_geometry  # QgsGeometry (Point oder LineString)
        self.preview_marker = QgsVertexMarker(self.canvas)
        self.preview_marker.setIconSize(12)
        self.preview_marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.preview_marker.setPenWidth(2)
        self.preview_marker.hide()
        self.setCursor(Qt.CrossCursor)

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            mappt = self.toMapCoordinates(event.pos())
            # 1. Punkt ggf. auf Snap-Geometrie führen
            if len(self.points) == 0 and self.snap_geometry is not None and not self.snap_geometry.isEmpty():
                if self.snap_geometry.wkbType() == QgsWkbTypes.Point:
                    mappt = self.snap_geometry.asPoint()
                else:
                    mappt = self.snap_geometry.closestSegmentWithContext(mappt)[1]
            self.points.append(QgsPointXY(mappt))
            if self.capture_callback:
                self.capture_callback(mappt)
        elif event.button() == Qt.RightButton:
            # Abschluss
            if self.finalize_callback:
                self.finalize_callback(self.points)
            self.points = []

    def canvasMoveEvent(self, event):
        """Live-Vorschau: zeigt 1. Punkt geführt an, danach normaler Verlauf."""
        try:
            mappt = self.toMapCoordinates(event.pos())
            if len(self.points) == 0:
                # 1. Punkt Vorschau
                if self.snap_geometry and not self.snap_geometry.isEmpty():
                    if self.snap_geometry.wkbType() == QgsWkbTypes.Point:
                        mappt = self.snap_geometry.asPoint()
                    else:
                        mappt = self.snap_geometry.closestSegmentWithContext(mappt)[1]
                self.preview_marker.setCenter(mappt)
                self.preview_marker.show()
            else:
                # Folgepunkte – nur Marker verschieben, RubberBand handled die rufende Klasse
                self.preview_marker.setCenter(mappt)
                self.preview_marker.show()
        except Exception:
            pass

    def canvasReleaseEvent(self, event):
        pass

    def deactivate(self):
        super().deactivate()
        self.points = []
        if self.preview_marker:
            self.preview_marker.hide()

class HauseinfuehrungsVerlegungsTool(QDialog):
    instance = None  # Klassenvariable zur Verwaltung der Instanz

    def get_attribute(self, feature, field):
        from PyQt5.QtCore import QVariant
        value = feature[field]
        if isinstance(value, QVariant):
            return None
        return value

    def __init__(self, iface, parent=None):
        if HauseinfuehrungsVerlegungsTool.instance is not None:
            HauseinfuehrungsVerlegungsTool.instance.raise_()
            HauseinfuehrungsVerlegungsTool.instance.activateWindow()
            return

        super().__init__(parent, Qt.WindowStaysOnTopHint)
        self.iface = iface
        self.ui = Ui_HauseinfuehrungsVerlegungsToolDialogBase()
        self.ui.setupUi(self)

        self.edit_mode = False
        self.selected_ha_id = None
        self.ui.pushButton_select_leerrohr.clicked.connect(self.select_hauseinfuehrung)
        self.ui.pushButton_verlauf_HA.setEnabled(True)

        self.settings = QSettings("SiegeleCo", "ToolBox")
        self.db_details = None
        self.is_connected = False

        self.scene = QGraphicsScene()
        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)

        HauseinfuehrungsVerlegungsTool.instance = self

        self.load_setup_data()

        self.ui.button_box.button(QDialogButtonBox.Reset).clicked.connect(self.formular_initialisieren)
        self.ui.button_box.button(QDialogButtonBox.Cancel).clicked.connect(self.abbrechen_und_schliessen)

        self.ui.pushButton_adresse.clicked.connect(self.adresse_waehlen)
        self.ui.checkBox_aufschlieung.stateChanged.connect(self.aufschliessungspunkt_verwalten)
        self.ui.checkBox_Mehrfachimport.stateChanged.connect(self.handle_checkbox_mehrfachimport)

        self.startpunkt_id = None
        self.verlauf_ids = []
        self.highlights = []
        self.adresspunkt_highlight = None
        self.gewaehlte_rohrnummer = None
        self.direktmodus = False
        self.mehrfachimport_modus = False
        self.gewaehlte_adresse = None
        self.gewaehlte_farb_id = None
        self.ausgewaehltes_rechteck = None

        self.ui.comboBox_Status.clear()
        self.status_dict = {}
        try:
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()
            cur.execute("SELECT id, \"STATUS\" FROM lwl.\"LUT_Status\" ORDER BY id")
            for row in cur.fetchall():
                status_id, status_text = row
                self.ui.comboBox_Status.addItem(status_text)
                self.status_dict[status_text] = status_id
            conn.close()
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Status laden fehlgeschlagen: {e}", level=Qgis.Critical)

        self.ui.pushButton_parentLeerrohr.clicked.connect(self.aktion_parent_leerrohr)
        self.ui.pushButton_verlauf_HA.clicked.connect(self.aktion_verlauf)
        self.ui.pushButton_Abzweigung.clicked.connect(self.aktion_abzweig_von_bestehender_ha)
        self.ui.pushButton_Import.clicked.connect(self.daten_importieren)
        self.ui.pushButton_Datenpruefung.clicked.connect(self.daten_pruefen)
        self.ui.checkBox_direkt.stateChanged.connect(self.handle_checkbox_direkt)
        self.ui.pushButton_verteiler.clicked.connect(self.verteilerkasten_waehlen)

        self.handle_checkbox_direkt(self.ui.checkBox_direkt.checkState())

    def load_setup_data(self):
        """Lädt Datenbankverbindung aus dem aktiven Setup."""
        print("DEBUG: Lade Setup-Daten für Hauseinführung")
        username = self.settings.value("connection_username", "")
        password = base64.b64decode(self.settings.value("connection_password", "").encode()).decode() if self.settings.value("connection_password", "") else ""
        umgebung = self.settings.value("connection_umgebung", "")

        if not username or not password or not umgebung:
            self.iface.messageBar().pushMessage("Fehler", "Keine Setup-Verbindung gefunden. Bitte konfigurieren Sie das Setup.", level=Qgis.Critical)
            QgsMessageLog.logMessage("Keine Setup-Verbindung gefunden.", "Hauseinfuehrung", Qgis.Critical)
            return

        # Datenbankverbindungsparameter setzen
        self.db_details = self.get_database_connection(username, password, umgebung)
        try:
            conn = psycopg2.connect(**self.db_details)
            conn.close()
            self.is_connected = True
            self.ui.pushButton_Import.setEnabled(True)
            QgsMessageLog.logMessage(f"Verbindung zu {umgebung} hergestellt.", "Hauseinfuehrung", Qgis.Info)
        except Exception as e:
            self.is_connected = False
            self.ui.pushButton_Import.setEnabled(False)
            self.iface.messageBar().pushMessage("Fehler", f"Verbindung fehlgeschlagen: {e}", level=Qgis.Critical)
            QgsMessageLog.logMessage(f"Verbindungsfehler zu {umgebung}: {e}", "Hauseinfuehrung", Qgis.Critical)

    def get_database_connection(self, username=None, password=None, umgebung=None):
        """Gibt die Verbindungsinformationen für psycopg2 zurück."""
        if username is None:
            username = self.settings.value("connection_username", "")
        if password is None:
            password = base64.b64decode(self.settings.value("connection_password", "").encode()).decode() if self.settings.value("connection_password", "") else ""
        if umgebung is None:
            umgebung = self.settings.value("connection_umgebung", "Testumgebung")

        if umgebung == "Testumgebung":
            conn_info = {
                "host": "172.30.0.4",
                "port": "5432",
                "dbname": "qwc_services",
                "sslmode": "disable"
            }
        else:  # Produktivumgebung
            conn_info = {
                "host": "172.30.0.3",
                "port": "5432",
                "dbname": "qwc_services",
                "sslmode": "disable"
            }
        return {
            "dbname": conn_info["dbname"],
            "user": username,
            "password": password,
            "host": conn_info["host"],
            "port": conn_info["port"],
            "sslmode": conn_info["sslmode"]
        }

    def select_hauseinfuehrung(self):
        if self.highlights:
            for h in self.highlights:
                h.hide()
            self.highlights.clear()
        self.iface.messageBar().pushMessage("Info", "Wählen Sie eine Hauseinführung auf der Karte.", level=Qgis.Info)
        ha_layer = QgsProject.instance().mapLayersByName("LWL_Hauseinfuehrung")[0]
        if not ha_layer:
            self.iface.messageBar().pushMessage("Fehler", "Layer 'LWL_Hauseinfuehrung' nicht gefunden.", level=Qgis.Critical)
            return

        def on_ha_selected(feature, layer):
            self.selected_ha_id = self.get_attribute(feature, "id")
            self.edit_mode = True
            self.ui.pushButton_Import.setText("Update")
            self.ui.pushButton_verlauf_HA.setEnabled(False)
            self.ui.label_gewaehlte_haueinfuehrung.setText(f"ID: {self.selected_ha_id}")
            
            self.ui.label_Kommentar.setText(self.get_attribute(feature, "KOMMENTAR") or "")
            self.ui.label_Kommentar_2.setText(self.get_attribute(feature, "BESCHREIBUNG") or "")
            self.ui.checkBox_Gefoerdert.setChecked(self.get_attribute(feature, "GEFOERDERT") or False)
            status_id = self.get_attribute(feature, "STATUS")
            if status_id:
                for text, id_ in self.status_dict.items():
                    if id_ == status_id:
                        self.ui.comboBox_Status.setCurrentText(text)
                        break
            verlegt_am = self.get_attribute(feature, "VERLEGT_AM")
            if verlegt_am:
                self.ui.mDateTimeEdit_Strecke.setDateTime(QDateTime(verlegt_am))
            
            self.gewaehlte_adresse = self.get_attribute(feature, "HA_ADRCD_SUBCD")
            self.gewaehlter_adrcd = self.get_attribute(feature, "HA_ADRCD")
            if self.gewaehlte_adresse:
                bev_layer = QgsProject.instance().mapLayersByName("BEV_GEB_PT")[0]
                bev_feat = next((f for f in bev_layer.getFeatures() if f["adrcd_subcd"] == self.gewaehlte_adresse), None)
                if bev_feat:
                    self.ui.label_adresse.setPlainText(f"{bev_feat['strassenname']}, {bev_feat['hnr_adr_zu']}")
            
            self.ui.checkBox_aufschlieung.setChecked(self.gewaehlte_adresse is None)  # Setze Checkbox basierend auf Adresse
            
            self.gewaehlter_verteiler = self.get_attribute(feature, "VKG_LR")
            if self.gewaehlter_verteiler:
                knoten_layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
                vk_feat = next((f for f in knoten_layer.getFeatures() if f["id"] == self.gewaehlter_verteiler), None)
                if vk_feat:
                    self.ui.label_verteiler.setPlainText(f"Ausgewählt: {vk_feat['TYP']} (ID: {self.gewaehlter_verteiler})")
                    self.highlight_geometry(vk_feat.geometry(), knoten_layer, QColor(Qt.red))
            
            self.startpunkt_id = self.get_attribute(feature, "ID_LEERROHR")
            self.abzweigung_id = self.get_attribute(feature, "ID_ABZWEIGUNG")
            is_direkt = (self.get_attribute(feature, "ROHRNUMMER") == 0 and self.get_attribute(feature, "FARBE") == 'direkt')
            self.ui.checkBox_direkt.setChecked(is_direkt)
            if self.startpunkt_id:
                lr_layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
                lr_feat = next((f for f in lr_layer.getFeatures() if f["id"] == self.startpunkt_id), None)
                if lr_feat:
                    self.ui.label_parentLeerrohr.setPlainText(str(self.startpunkt_id))
                    self.ui.label_subtyp.setPlainText(f"SUBTYP: {lr_feat['SUBTYP']}")
                    self.ui.label_farbschema.setPlainText(str(lr_feat['CODIERUNG']))
                    self.ui.label_firma.setPlainText(f"Hersteller: {lr_feat['FIRMA_HERSTELLER']}")
                    self.zeichne_rohre(lr_feat['SUBTYP'], lr_feat['CODIERUNG'], lr_feat['FIRMA_HERSTELLER'])
                    self.highlight_geometry(lr_feat.geometry(), lr_layer, QColor(Qt.blue))
                    rohrnummer = self.get_attribute(feature, "ROHRNUMMER")
                    self.gewaehlte_rohrnummer = int(rohrnummer) if rohrnummer is not None else None
                    self.handle_rect_click(self.gewaehlte_rohrnummer, self.get_attribute(feature, "FARBE"))
            elif self.abzweigung_id:
                abz_layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr_Abzweigung")[0]
                abz_feat = next((f for f in abz_layer.getFeatures() if f["id"] == self.abzweigung_id), None)
                if abz_feat:
                    self.ui.label_parentLeerrohr.setPlainText(f"Abzweigung: {self.abzweigung_id}")
                    self.ui.label_subtyp.setPlainText(f"SUBTYP: {abz_feat['SUBTYP']}")
                    self.ui.label_farbschema.setPlainText(str(abz_feat['CODIERUNG']))
                    self.ui.label_firma.setPlainText(f"Hersteller: {abz_feat['FIRMA_HERSTELLER']}")
                    self.zeichne_rohre(abz_feat['SUBTYP'], abz_feat['CODIERUNG'], abz_feat['FIRMA_HERSTELLER'], is_abzweigung=True)
                    self.highlight_geometry(abz_feat.geometry(), abz_layer, QColor(Qt.blue))
                    rohrnummer = self.get_attribute(feature, "ROHRNUMMER")
                    self.gewaehlte_rohrnummer = int(rohrnummer) if rohrnummer is not None else None
                    self.handle_rect_click(self.gewaehlte_rohrnummer, self.get_attribute(feature, "FARBE"))
            else:
                self.zeichne_rohre(None, None, None)
            
            self.ui.checkBox_Befestigt.setChecked(self.get_attribute(feature, "BEFESTIGT") or False)
            
            highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), layer)
            highlight.setColor(QColor(Qt.yellow))
            highlight.setWidth(3)
            highlight.show()
            self.highlights.append(highlight)
            
            self.ui.pushButton_Import.setEnabled(False)  # Deaktiviere Import

        self.click_selector = ClickSelector(self.iface.mapCanvas(), [ha_layer], on_ha_selected, self.iface)
        self.iface.mapCanvas().setMapTool(self.click_selector)

    def aktion_abzweig_von_leerrohr(self):
        """Erster Punkt gleitet entlang des gewählten Leerrohrs."""
        # Guard
        if not getattr(self, "startpunkt_id", None) and not getattr(self, "abzweigung_id", None):
            self.iface.messageBar().pushMessage("Fehler", "Kein Parent-Leerrohr/Abzweigung gewählt.", level=Qgis.Critical)
            return

        # Geometrie + CRS holen
        if self.abzweigung_id:
            layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr_Abzweigung")[0]
            feat = next((f for f in layer.getFeatures() if f["id"] == self.abzweigung_id), None)
        else:
            layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
            feat = next((f for f in layer.getFeatures() if f["id"] == self.startpunkt_id), None)

        if not feat:
            self.iface.messageBar().pushMessage("Fehler", "Parent-Feature nicht gefunden.", level=Qgis.Critical)
            return

        g = QgsGeometry(feat.geometry())
        # in Karten-CRS transformieren
        tr = QgsCoordinateTransform(layer.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance())
        _ = g.transform(tr)

        # Finish-Callback: speichere resultierende Linie
        def on_finish(points):
            if len(points) < 2:
                self.iface.messageBar().pushMessage("Fehler", "Mindestens zwei Punkte erforderlich.", level=Qgis.Critical); return

            # Falls ein Ziel-Leerrohr gewählt ist: P[0] (LR-Seite) exakt aufs LR snappen
            try:
                lr_id = getattr(self, "startpunkt_id", None)
                if lr_id:
                    lr_layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
                    lr_feat = next((f for f in lr_layer.getFeatures() if f["id"] == lr_id), None)
                    if lr_feat:
                        # in Karten-CRS transformieren
                        from qgis.core import QgsCoordinateTransform, QgsProject, QgsGeometry
                        g_lr = QgsGeometry(lr_feat.geometry())
                        tr = QgsCoordinateTransform(lr_layer.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance())
                        _ = g_lr.transform(tr)
                        # P[0] auf LR projizieren (P[0] ist LR-Ende der übernommenen HA)
                        p0 = points[0]
                        p0s = g_lr.closestSegmentWithContext(p0)[1]
                        points[0] = QgsPointXY(p0s)
            except Exception:
                pass

            self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
            self.result_rb.setToGeometry(self.erfasste_geom, None)
            self.iface.messageBar().pushMessage("Info", "HA-Linie ab bestehender HA erfasst.", level=Qgis.Success)
            self.iface.mapCanvas().unsetMapTool(self.map_tool)

        # Optional: pro Klick noch was tun (z. B. ersten Punkt merken)
        def on_point(p):
            if len(getattr(self, "erfasste_punkte", [])) == 0:
                self.erfasste_punkte = [p]
            else:
                self.erfasste_punkte.append(p)

        self.map_tool = GuidedStartLineTool(self.iface.mapCanvas(), g, on_finish, on_point)
        self.iface.mapCanvas().setMapTool(self.map_tool)
        self.iface.messageBar().pushMessage("Info", "Klicken: Punkte setzen • Rechtsklick: beenden. Erster Punkt gleitet am Leerrohr.", level=Qgis.Info)

    def aktion_abzweig_von_bestehender_ha(self):
        """Abzweig von bestehender HA: Korridor IMMER vom HA-Start (Hausanschluss) bis zum Snap-Punkt."""
        from qgis.core import QgsCoordinateTransform, QgsGeometry, QgsProject

        if not hasattr(self, "result_rb") or self.result_rb is None:
            self.result_rb = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.LineGeometry)
            self.result_rb.setColor(Qt.red); self.result_rb.setWidth(2)
        else:
            self.result_rb.reset()

        ha_layer_list = QgsProject.instance().mapLayersByName("LWL_Hauseinfuehrung")
        if not ha_layer_list:
            self.iface.messageBar().pushMessage("Fehler", "Layer 'LWL_Hauseinfuehrung' nicht gefunden.", level=Qgis.Critical); return
        ha_layer = ha_layer_list[0]

        def on_pick_ha(feature, layer):
            try:
                g = QgsGeometry(feature.geometry())
                tr = QgsCoordinateTransform(layer.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance())
                _ = g.transform(tr)

                def on_finish(points):
                    if len(points) < 2:
                        self.iface.messageBar().pushMessage("Fehler", "Mindestens zwei Punkte erforderlich.", level=Qgis.Critical); return
                    self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
                    self.result_rb.setToGeometry(self.erfasste_geom, None)
                    self.iface.messageBar().pushMessage("Info", "HA-Linie ab bestehender HA erfasst.", level=Qgis.Success)
                    self.iface.mapCanvas().unsetMapTool(self.map_tool)

                # HA-Modus → vom Start (Hausanschluss) loslaufen, VKG-Hinweis nicht nötig
                self.map_tool = GuidedStartLineTool(self.iface.mapCanvas(), g, on_finish, mode="ha", persist_rb=self.result_rb)
                self.iface.mapCanvas().setMapTool(self.map_tool)
                self.iface.messageBar().pushMessage("Info", "Erster Punkt gleitet an der gewählten HA (vom Start). Rechtsklick: beenden.", level=Qgis.Info)
            except Exception as e:
                self.iface.messageBar().pushMessage("Fehler", f"Abzweig-Start fehlgeschlagen: {e}", level=Qgis.Critical)

        self.click_selector = ClickSelector(self.iface.mapCanvas(), [ha_layer], on_pick_ha, self.iface)
        self.iface.mapCanvas().setMapTool(self.click_selector)
        self.iface.messageBar().pushMessage("Info", "Bitte eine bestehende HA zum Andocken wählen.", level=Qgis.Info)

    def handle_checkbox_direkt(self, state):
        """Aktiviert/Deaktiviert den Button zur Auswahl des Parent-Leerrohrs."""
        self.direktmodus = (state == Qt.Checked)
        self.ui.pushButton_parentLeerrohr.setEnabled(not self.direktmodus)

    def handle_checkbox_mehrfachimport(self, state):
        """Verarbeitet den Zustand der Mehrfachimport-Checkbox."""
        self.mehrfachimport_modus = (state == Qt.Checked)
        QgsMessageLog.logMessage(f"DEBUG: Mehrfachimport-Modus = {self.mehrfachimport_modus}", "Hauseinfuehrung", Qgis.Info)

    def verteilerkasten_waehlen(self):
        QgsMessageLog.logMessage("DEBUG: Starte verteilerkasten_waehlen", "Hauseinfuehrung", Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
        if not layer:
            self.iface.messageBar().pushMessage("Fehler", "Layer 'LWL_Knoten' nicht gefunden.", level=Qgis.Critical)
            return

        self.iface.setActiveLayer(layer)
        self.iface.messageBar().pushMessage("Info", "Bitte klicken Sie auf ein Objekt (Verteilerkasten, Ortszentrale oder Schacht), um es auszuwählen.", level=Qgis.Info)

        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())

        def on_vertex_selected(point):
            QgsMessageLog.logMessage("DEBUG: on_vertex_selected aufgerufen", "Hauseinfuehrung", Qgis.Info)
            try:
                pixel_radius = 5
                map_units_per_pixel = self.iface.mapCanvas().mapUnitsPerPixel()
                search_radius = pixel_radius * map_units_per_pixel
                point_geom = QgsGeometry.fromPointXY(point)
                search_rect = point_geom.buffer(search_radius, 1).boundingBox()

                request = QgsFeatureRequest().setFilterRect(search_rect)
                features = [feature for feature in layer.getFeatures(request)]

                for feature in features:
                    if feature["TYP"] in ["Verteilerkasten", "Ortszentrale", "Schacht"]:
                        verteiler_id = feature["id"]

                        self.gewaehlter_verteiler = verteiler_id
                        self.ui.label_verteiler.setPlainText(f"Ausgewählt: {feature['TYP']} (ID: {verteiler_id})")
                        QgsMessageLog.logMessage(f"DEBUG: Verteiler ID={verteiler_id} ausgewählt", "Hauseinfuehrung", Qgis.Info)

                        self.formular_initialisieren_fuer_verteilerwechsel()

                        geom = feature.geometry()
                        self.highlight_geometry(geom, layer)

                        self.ui.pushButton_Import.setEnabled(False)  # Deaktiviere Import
                        self.ui.pushButton_select_leerrohr.setEnabled(False)  # Deaktiviere Auswahl Hauseinführung
                        return

                self.iface.messageBar().pushMessage("Fehler", "Kein gültiges Objekt (Verteilerkasten, Ortszentrale oder Schacht) an dieser Stelle gefunden.", level=Qgis.Info)

            except Exception as e:
                self.iface.messageBar().pushMessage("Fehler", f"Fehler bei der Auswahl: {e}", level=Qgis.Info)

        self.map_tool.canvasClicked.connect(on_vertex_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def formular_initialisieren_fuer_verteilerwechsel(self):
        """Setzt spezifische Felder und die grafische Ansicht zurück."""
        QgsMessageLog.logMessage("DEBUG: Starte formular_initialisieren_fuer_verteilerwechsel", "Hauseinfuehrung", Qgis.Info)
        # Felder im UI zurücksetzen
        self.ui.label_parentLeerrohr.setPlainText("")
        self.ui.label_subtyp.setPlainText("")
        self.ui.label_farbschema.setPlainText("")
        self.ui.label_firma.setPlainText("")

        # Szene initialisieren, falls sie noch nicht existiert
        if not hasattr(self, "scene") or self.scene is None:
            self.scene = QGraphicsScene()

        # Lösche die grafische Ansicht und das ausgewählte Quadrat
        self.scene.clear()
        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)
        self.ausgewaehltes_rechteck = None  # Zurücksetzen des ausgewählten Quadrats

        # Nachricht für den Benutzer
        self.iface.messageBar().pushMessage("Info", "Bitte wählen Sie das Leerrohr erneut, um die Daten zu aktualisieren.", level=Qgis.Info)

    def highlight_geometry(self, geom, layer, color=QColor(Qt.red)):
        """Hebt eine Geometrie hervor."""
        highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
        highlight.setColor(color)
        highlight.setWidth(3)
        highlight.show()
        self.highlights.append(highlight)  # Zu Highlights hinzufügen für Cleanup
        return highlight

    def aktion_parent_leerrohr(self):
        """Wählt ein Parent-Leerrohr oder eine Abzweigung aus."""
        QgsMessageLog.logMessage("DEBUG: Starte aktion_parent_leerrohr", "Hauseinfuehrung", Qgis.Info)
        self.iface.messageBar().pushMessage("Bitte wählen Sie ein Parent Leerrohr oder eine Abzweigung", level=Qgis.Info)

        leerrohr_layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        abzweig_layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr_Abzweigung")[0]

        layers = [leerrohr_layer, abzweig_layer]

        def on_feature_selected(feature, layer):
            QgsMessageLog.logMessage(f"DEBUG: Feature ausgewählt, Layer={layer.name()}", "Hauseinfuehrung", Qgis.Info)
            if self.highlights:
                for h in self.highlights:
                    h.hide()
                self.highlights.clear()

            objekt_id = feature["id"]
            subtyp_id = feature["SUBTYP"]
            farbschema = feature["CODIERUNG"]
            firma = feature["FIRMA_HERSTELLER"]

            if layer.name() == "LWL_Leerrohr":
                self.startpunkt_id = objekt_id
                self.abzweigung_id = None
                self.ui.label_parentLeerrohr.setPlainText(str(objekt_id))
            else:
                self.abzweigung_id = objekt_id
                self.startpunkt_id = None
                self.ui.label_parentLeerrohr.setPlainText(f"Abzweigung: {objekt_id}")

            self.ui.label_farbschema.setPlainText(str(farbschema))
            self.ui.label_subtyp.setPlainText(f"SUBTYP: {subtyp_id}")
            self.ui.label_firma.setPlainText(f"Hersteller: {firma}")

            is_abzweigung = (layer.name() == "LWL_Leerrohr_Abzweigung")
            self.zeichne_rohre(subtyp_id, farbschema, firma, is_abzweigung=is_abzweigung)

            highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), layer)
            highlight.setColor(Qt.red)
            highlight.setWidth(3)
            highlight.show()
            self.highlights.append(highlight)

        self.click_selector = ClickSelector(self.iface.mapCanvas(), layers, on_feature_selected, self.iface)
        self.iface.mapCanvas().setMapTool(self.click_selector)

    def zeichne_rohre(self, subtyp_id, farbschema, firma, is_abzweigung=False):
        """Zeichnet Rohre – aktiv nur, wenn rohrgenau bis zum gewählten VKG verbunden.
        Markiert Rohrnummern auch als belegt, wenn die HE downstream im Netz hängt."""
        # Szene vorbereiten
        if self.ui.graphicsView_Farben_Rohre.scene():
            self.ui.graphicsView_Farben_Rohre.scene().clear()
        else:
            self.ui.graphicsView_Farben_Rohre.setScene(QGraphicsScene())
        self.scene = self.ui.graphicsView_Farben_Rohre.scene()
        self.scene.setSceneRect(0, 0, 491, 200)
        self.gewaehlte_rohrnummer = None
        self.ausgewaehltes_rechteck = None

        # Farben + Rohrnummern
        rohre, subtyp_char, typ = self.lade_farben_und_rohrnummern(subtyp_id)
        if not rohre or subtyp_char is None:
            self.iface.messageBar().pushMessage("Fehler", f"Keine Rohre/Subtyp für {subtyp_id}.", level=Qgis.Critical)
            return

        try:
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()

            # Start-LR (bei Abzweigung: Parent)
            if is_abzweigung:
                cur.execute('SELECT "PARENT_LEERROHR_ID" FROM lwl."LWL_Leerrohr_Abzweigung" WHERE "id" = %s', (self.abzweigung_id,))
                row = cur.fetchone()
                start_lr_id = row[0] if row and row[0] is not None else None
            else:
                start_lr_id = self.startpunkt_id

            if not start_lr_id or not getattr(self, "gewaehlter_verteiler", None):
                conn.close()
                self.iface.messageBar().pushMessage("Fehler", "Start-Leerrohr oder VKG fehlt.", level=Qgis.Critical)
                return
            vkg_id = int(self.gewaehlter_verteiler)

            # ---------- A) LR-Reichweite ----------
            cur.execute("""
                WITH RECURSIVE reach_lr(lr_id) AS (
                    SELECT %s::bigint
                    UNION
                    SELECT CASE WHEN r."ID_LEERROHR_1" = reach_lr.lr_id THEN r."ID_LEERROHR_2"
                                ELSE r."ID_LEERROHR_1" END
                    FROM reach_lr
                    JOIN lwl."LWL_Leerrohr_Leerrohr_rel" r
                    ON r."ID_LEERROHR_1" = reach_lr.lr_id OR r."ID_LEERROHR_2" = reach_lr.lr_id
                )
                SELECT array_agg(DISTINCT lr_id) FROM reach_lr;
            """, (start_lr_id,))
            reach_lr_ids = cur.fetchone()[0] or []

            cur.execute("""
                SELECT lr.id
                FROM lwl."LWL_Leerrohr" lr
                WHERE lr.id = ANY(%s::bigint[]) AND %s = ANY(lr."VKG_LR")
            """, (reach_lr_ids, vkg_id))
            ziel_lr_ids = [r[0] for r in cur.fetchall()]

            cur.execute('SELECT %s = ANY("VKG_LR") FROM lwl."LWL_Leerrohr" WHERE id=%s', (vkg_id, start_lr_id))
            start_has_vkg = cur.fetchone()[0] if cur.rowcount else False

            if not ziel_lr_ids and not start_has_vkg:
                self.belegte_rohre = []
                self._render_rohr_quadrate(rohre, subtyp_char, typ, enable_set=set(), info_hint="Keine LR-Verbindung zum VKG")
                conn.close()
                return

            ziel_lr_all = list(set(ziel_lr_ids + ([start_lr_id] if start_has_vkg else [])))

            # ---------- B) Erreichbare Rohrnummern (Rohrketten bis Ziel-LR) ----------
            cur.execute("""
            WITH RECURSIVE
            start_rohre AS (
            SELECT r.id AS rid, r."ROHRNUMMER" AS rnr
            FROM lwl."LWL_Rohr" r
            WHERE r."ID_LEERROHR" = %s
            ),
            walk(rid, rnr) AS (
            SELECT rid, rnr FROM start_rohre
            UNION
            SELECT CASE WHEN rel."ID_ROHR_1" = w.rid THEN rel."ID_ROHR_2" ELSE rel."ID_ROHR_1" END, w.rnr
            FROM walk w
            JOIN lwl."LWL_Rohr_Rohr_rel" rel
                ON rel."ID_ROHR_1" = w.rid OR rel."ID_ROHR_2" = w.rid
            )
            SELECT DISTINCT w.rnr
            FROM walk w
            JOIN lwl."LWL_Rohr" r2 ON r2.id = w.rid
            WHERE r2."ID_LEERROHR" = ANY(%s::bigint[])
            """, (start_lr_id, ziel_lr_all))
            rohrnummern_mit_vkgpfad = set(n for (n,) in cur.fetchall())

            # ---------- C) Netzweite Belegung (unabhängig von r2.ID_HAUSEINFÜHRUNG) ----------
            # Belegt, wenn eine HE mit diesem VKG auf IRGENDeinem erreichbaren LR dieselbe Rohrnummer hat.
            cur.execute("""
                SELECT DISTINCT ha."ROHRNUMMER"
                FROM lwl."LWL_Hauseinfuehrung" ha
                WHERE ha."VKG_LR" = %s
                AND ha."ID_LEERROHR" = ANY(%s::bigint[])
            """, (vkg_id, reach_lr_ids))
            belegte_rnr_im_netz = {n for (n,) in cur.fetchall()}

            # lokale Belegung (optional zusätzlich)
            if is_abzweigung:
                cur.execute("""SELECT DISTINCT "ROHRNUMMER"
                            FROM lwl."LWL_Hauseinfuehrung"
                            WHERE "ID_ABZWEIGUNG"=%s AND "VKG_LR"=%s""", (self.abzweigung_id, vkg_id))
            else:
                cur.execute("""SELECT DISTINCT "ROHRNUMMER"
                            FROM lwl."LWL_Hauseinfuehrung"
                            WHERE "ID_LEERROHR"=%s AND "VKG_LR"=%s""", (start_lr_id, vkg_id))
            belegte_local = {n for (n,) in cur.fetchall()}

            self.belegte_rohre = sorted(belegte_rnr_im_netz.union(belegte_local))
            conn.close()

        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"zeichne_rohre fehlgeschlagen: {e}", level=Qgis.Critical)
            return

        # Rendering
        self._render_rohr_quadrate(
            rohre, subtyp_char, typ,
            enable_set=rohrnummern_mit_vkgpfad,
            info_hint=None
        )

    def _render_rohr_quadrate(self, rohre, subtyp_char, typ, enable_set, info_hint=None):
        """Nur intern: zeichnet die Kästchen mit deiner bestehenden Optik (grau/X für nicht wählbar)."""
        y_offset = 0; x_offset = 0
        font = QFont(); font.setPointSize(8)
        # Überschrift
        subtyp_text = self.scene.addText(f"{subtyp_char}:")
        subtyp_text.setPos(x_offset, y_offset); subtyp_text.setDefaultTextColor(Qt.black); subtyp_text.setFont(font); subtyp_text.setZValue(1)
        self.scene.addItem(subtyp_text)
        x_offset += subtyp_text.boundingRect().width() + 3
        font.setPointSize(10)
        square_size = {1: 30, 2: 25, 3: 20}.get(typ, 20)
        unique_d = set(d for _, _, d, _, _, _ in rohre); is_mixed = len(unique_d) > 1; max_d = max(unique_d) if unique_d else 0

        for rohr_id, rohr_nummer, durchmesser, farbe, primary_farbcode, secondary_farbcode in rohre:
            ist_belegt = rohr_nummer in getattr(self, "belegte_rohre", [])
            aktiv = rohr_nummer in (enable_set or set())
            ist_gewaehlt = (rohr_nummer == self.gewaehlte_rohrnummer) if self.gewaehlte_rohrnummer else False
            add_adjust = 5 if is_mixed and durchmesser == max_d else 0
            size = square_size + add_adjust
            x = x_offset; y = y_offset + (square_size - size) / 2

            rect = ClickableRect(x, y, size, size, rohr_nummer, rohr_id, self.handle_rect_click if aktiv else None)
            rect.setPen(QPen(Qt.black, 1))

            # diagonal geteilte Füllung
            tri1_pts = [QPointF(x, y), QPointF(x + size, y), QPointF(x, y + size)]
            tri2_pts = [QPointF(x + size, y), QPointF(x + size, y + size), QPointF(x, y + size)]
            t1 = QGraphicsPolygonItem(QPolygonF(tri1_pts), rect)
            t2 = QGraphicsPolygonItem(QPolygonF(tri2_pts), rect)

            if not aktiv:
                # nicht rohrgenau verbunden → grau + Tooltip
                color = QColor("#B0B0B0")
                t1.setBrush(QBrush(color)); t2.setBrush(QBrush(color))
                rect.setPen(QPen(Qt.darkGray, 1))
                rect.setToolTip(info_hint or "Keine Verbindung zum gewählten VKG über Rohrkette.")
            else:
                if ist_belegt and not ist_gewaehlt:
                    color = QColor("#808080"); t1.setBrush(QBrush(color)); t2.setBrush(QBrush(color))
                    rect.setPen(QPen(Qt.red, 2))
                    rect.setToolTip(f"Rohr {rohr_nummer}: bereits für diesen VKG belegt")
                else:
                    if secondary_farbcode:
                        t1.setBrush(QBrush(QColor(primary_farbcode)))
                        t2.setBrush(QBrush(QColor(secondary_farbcode)))
                    else:
                        c = QColor(primary_farbcode); t1.setBrush(QBrush(c)); t2.setBrush(QBrush(c))

            t1.setPen(QPen(Qt.NoPen)); t2.setPen(QPen(Qt.NoPen))
            t1.setZValue(0); t2.setZValue(0)
            self.scene.addItem(rect)

            # Rotes X nur für belegte nicht-gewählte
            if ist_belegt and not ist_gewaehlt:
                l1 = QGraphicsLineItem(x, y, x + size, y + size, rect); l1.setPen(QPen(Qt.red, 2)); l1.setZValue(2); self.scene.addItem(l1)
                l2 = QGraphicsLineItem(x + size, y, x, y + size, rect); l2.setPen(QPen(Qt.red, 2)); l2.setZValue(2); self.scene.addItem(l2)

            # Zahl
            txt = self.scene.addText(str(rohr_nummer))
            cx = x + size/2 - txt.boundingRect().width()/2
            cy = y + size/2 - txt.boundingRect().height()/2
            txt.setPos(cx, cy)
            col = QColor(primary_farbcode if aktiv else "#808080")
            bright = (col.red()*0.299 + col.green()*0.587 + col.blue()*0.114)
            txt.setDefaultTextColor(Qt.black if bright > 128 else Qt.white)
            txt.setFont(font); txt.setZValue(3); self.scene.addItem(txt)

            x_offset += size + 3

        y_offset += square_size + 1
        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)
        self.ui.graphicsView_Farben_Rohre.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.ui.graphicsView_Farben_Rohre.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.iface.messageBar().pushMessage("Info", "Rohre (rohrgenau) angezeigt.", level=Qgis.Success)

    def handle_rect_click(self, rohrnummer, farb_id):
        # Auswahl-Markierung wie gehabt …
        if self.ausgewaehltes_rechteck:
            try:
                ist_belegt = self.ausgewaehltes_rechteck.rohrnummer in getattr(self, "belegte_rohre", [])
                self.ausgewaehltes_rechteck.setPen(QPen(Qt.red if ist_belegt else Qt.black, 2 if ist_belegt else 1))
            except RuntimeError:
                pass
        self.ausgewaehltes_rechteck = None
        for item in self.scene.items():
            if isinstance(item, ClickableRect) and item.rohrnummer == rohrnummer:
                item.setPen(QPen(QColor(Qt.blue), 6))
                self.ausgewaehltes_rechteck = item
                break

        self.gewaehlte_rohrnummer = int(rohrnummer)
        self.gewaehlte_farb_id = farb_id

        # Freies Intervall nur am Start-LR notwendig (Clamping)
        self.freies_intervall = (0.0, 1.0)
        try:
            vkg_id = int(self.gewaehlter_verteiler)
            conn = psycopg2.connect(**self.db_details); cur = conn.cursor()
            # Start-LR (bei Abzweig: Parent)
            if getattr(self, "abzweigung_id", None):
                cur.execute('SELECT "PARENT_LEERROHR_ID" FROM lwl."LWL_Leerrohr_Abzweigung" WHERE "id" = %s', (self.abzweigung_id,))
                row = cur.fetchone(); start_lr_id = row[0] if row else None
            else:
                start_lr_id = self.startpunkt_id
            if not start_lr_id:
                conn.close(); return

            # vorhandene Segmente dieser Rohrnummer am Start-LR
            cur.execute("""
                SELECT "FROM_POS","TO_POS"
                FROM lwl."LWL_Rohr"
                WHERE "ID_LEERROHR"=%s AND "ROHRNUMMER"=%s
                ORDER BY "FROM_POS"
            """, (start_lr_id, self.gewaehlte_rohrnummer))
            segs = [(float(a), float(b)) for a,b in cur.fetchall()]

            # VKG-Seite heuristisch: welcher Endknoten des Start-LR ist der VKG?
            cur.execute('SELECT "VONKNOTEN","NACHKNOTEN" FROM lwl."LWL_Leerrohr" WHERE id=%s', (start_lr_id,))
            vonk, nachk = cur.fetchone() if cur.rowcount else (None, None)
            vkg_seite = 1.0 if (nachk == vkg_id) else 0.0

            if segs:
                froms = [a for a,_ in segs]; tos = [b for _,b in segs]
                if vkg_seite == 0.0:
                    a = 0.0; b = min(froms)
                else:
                    a = max(tos); b = 1.0
                self.freies_intervall = (max(0.0, min(1.0, a)), max(0.0, min(1.0, b)))
            else:
                self.freies_intervall = (0.0, 1.0)

            conn.close()
            self.iface.messageBar().pushMessage("Info", f"Freies Intervall: {self.freies_intervall}", level=Qgis.Info)
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Intervallermittlung fehlgeschlagen: {e}", level=Qgis.Critical)
            self.freies_intervall = (0.0, 1.0)

    def clear_ha_preview(self):
        """Alle temporären Zeichenobjekte/Tools bereinigen."""
        try:
            # RubberBand der erfassten Linie
            if hasattr(self, "rubber_band") and self.rubber_band:
                self.rubber_band.reset()
        except Exception:
            pass

        # evtl. zweite Vorschau (falls du self.result_rb verwendest)
        if hasattr(self, "result_rb") and self.result_rb:
            try:
                self.result_rb.reset()
            except Exception:
                pass
            self.result_rb = None

        # evtl. geführtes Capture-Tool beenden
        if hasattr(self, "map_tool") and self.map_tool:
            try:
                self.iface.mapCanvas().unsetMapTool(self.map_tool)
            except Exception:
                pass
            self.map_tool = None

    def lade_farben_und_rohrnummern(self, subtyp_id):
        """Lädt Farben und Rohrnummern aus LUT_Leerrohr_SubTyp und LUT_Rohr_Beschreibung."""
        print(f"DEBUG: Parsing ROHR_DEFINITION für Subtyp-ID: {subtyp_id}")
        try:
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()
            cur.execute("""
                SELECT ls."ROHR_DEFINITION"::text, ls."ID_CODIERUNG", ls."SUBTYP_char", ls."ID_TYP"
                FROM lwl."LUT_Leerrohr_SubTyp" ls
                WHERE ls."id" = %s
            """, (subtyp_id,))
            result = cur.fetchone()
            if not result:
                raise ValueError(f"Subtyp-ID {subtyp_id} nicht gefunden.")
            rohr_definition, id_codierung, subtyp_char, typ = result
            if not rohr_definition:
                raise ValueError(f"ROHR_DEFINITION leer für {subtyp_id}.")
            rohr_array = json.loads(rohr_definition)
            cur.execute("""
                SELECT "ROHRNUMMER", "FARBE", "FARBCODE"
                FROM lwl."LUT_Rohr_Beschreibung"
                WHERE "ID_SUBTYP" = %s
                ORDER BY "ROHRNUMMER"
            """, (subtyp_id,))
            farben = cur.fetchall()
            rohre = []
            rohr_nummer = 1
            for group in rohr_array:
                anzahl = int(group.get("anzahl", 1))
                durchmesser = int(group.get("durchmesser", 0))
                for _ in range(anzahl):
                    if rohr_nummer <= len(farben):
                        _, farbe, farbcode = farben[rohr_nummer - 1]
                        if '/' in farbcode:
                            primary_farbcode, secondary_farbcode = farbcode.split('/')
                            secondary_farbcode = None if not secondary_farbcode.strip() or secondary_farbcode.strip('#') == '000000' else secondary_farbcode
                        else:
                            primary_farbcode = farbcode
                            secondary_farbcode = None
                        rohre.append((rohr_nummer, rohr_nummer, durchmesser, farbe, primary_farbcode, secondary_farbcode))
                    else:
                        farbe = "grau"
                        primary_farbcode = "#808080"
                        secondary_farbcode = None
                        rohre.append((rohr_nummer, rohr_nummer, durchmesser, farbe, primary_farbcode, secondary_farbcode))
                    rohr_nummer += 1
            conn.close()
            return rohre, subtyp_char, typ
        except Exception as e:
            print(f"DEBUG: Fehler: {e}")
            return [], None, None

    def aktion_verlauf(self):
        """Standard: erster Punkt gleitet sichtbar (LR oder VKG); Korridor wird übernommen."""
        from qgis.core import QgsCoordinateTransform, QgsProject, QgsGeometry

        # persistenter Ergebnis-RubberBand
        if not hasattr(self, "result_rb") or self.result_rb is None:
            self.result_rb = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.LineGeometry)
            self.result_rb.setColor(Qt.red); self.result_rb.setWidth(2)
        else:
            self.result_rb.reset()

        ref_geom = None
        ref_mode = "lr"
        vkg_hint = getattr(self, "gewaehlter_verteiler", None)

        if self.ui.checkBox_direkt.isChecked():
            # Direkt: Punkt am VKG
            kn_layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
            vk = next((f for f in kn_layer.getFeatures() if f["id"] == vkg_hint), None)
            if not vk:
                self.iface.messageBar().pushMessage("Fehler", "Kein Verteilerkasten gewählt.", level=Qgis.Critical); return
            ref_geom = QgsGeometry(vk.geometry())
            tr = QgsCoordinateTransform(kn_layer.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance())
            _ = ref_geom.transform(tr)
            ref_mode = "ha"  # Punkt → egal, wir übernehmen einfach den Punkt
        else:
            # Leerrohr oder Abzweigung wählen
            layer = None; feat = None
            if getattr(self, "abzweigung_id", None):
                layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr_Abzweigung")[0]
                feat = next((f for f in layer.getFeatures() if f["id"] == self.abzweigung_id), None)
            elif getattr(self, "startpunkt_id", None):
                layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
                feat = next((f for f in layer.getFeatures() if f["id"] == self.startpunkt_id), None)
            if not feat:
                self.iface.messageBar().pushMessage("Fehler", "Kein Parent-Leerrohr/Abzweigung gewählt.", level=Qgis.Critical); return
            ref_geom = QgsGeometry(feat.geometry())
            tr = QgsCoordinateTransform(layer.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance())
            _ = ref_geom.transform(tr)
            ref_mode = "lr"

        def on_finish(points):
            if len(points) < 2:
                self.iface.messageBar().pushMessage("Fehler", "Mindestens zwei Punkte erforderlich.", level=Qgis.Critical); return
            self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
            self.result_rb.setToGeometry(self.erfasste_geom, None)
            self.iface.messageBar().pushMessage("Info", "HA-Linie erfasst.", level=Qgis.Success)

        self.map_tool = GuidedStartLineTool(
            self.iface.mapCanvas(),
            ref_geom,
            on_finish,
            mode=ref_mode,
            vkg_hint_id=vkg_hint,            # wichtig: LR vom VKG-Ende starten
            persist_rb=self.result_rb
        )
        self.iface.mapCanvas().setMapTool(self.map_tool)
        self.iface.messageBar().pushMessage("Info", "Klicken: Punkte setzen • Rechtsklick: beenden. Korridor wird übernommen.", level=Qgis.Info)

    def aufschliessungspunkt_verwalten(self, state):
        """Aktiviert/Deaktiviert den Adressauswahlbutton basierend auf der Checkbox."""
        QgsMessageLog.logMessage(f"DEBUG: aufschliessungspunkt_verwalten mit state={state}", "Hauseinfuehrung", Qgis.Info)
        if state == Qt.Checked:
            self.ui.pushButton_adresse.setEnabled(False)
            # label_adresse ist ein QTextEdit
            self.ui.label_adresse.setPlainText("Keine Adresse (Aufschließungspunkt)")
            self.gewaehlte_adresse = None
        else:
            self.ui.pushButton_adresse.setEnabled(True)
            self.ui.label_adresse.setPlainText("")

    def adresse_waehlen(self):
        """Öffnet die Auswahl eines Adresspunkts über den neuen BEV_GEB_PT-Datensatz."""
        QgsMessageLog.logMessage("DEBUG: Starte adresse_waehlen", "Hauseinfuehrung", Qgis.Info)
        self.iface.messageBar().pushMessage("Info", "Bitte wählen Sie einen Adresspunkt auf der Karte aus.", level=Qgis.Info)

        # Neuer Layername
        layername = "BEV_GEB_PT"
        layer = next((lyr for lyr in QgsProject.instance().mapLayers().values() if lyr.name() == layername), None)

        if not layer:
            self.iface.messageBar().pushMessage("Fehler", f"Layer '{layername}' nicht gefunden.", level=Qgis.Critical)
            return

        self.iface.setActiveLayer(layer)
        self.iface.actionSelect().trigger()

        def on_adresspunkt_selected():
            """Callback für die Auswahl eines Adresspunkts."""
            QgsMessageLog.logMessage("DEBUG: on_adresspunkt_selected aufgerufen", "Hauseinfuehrung", Qgis.Info)
            selected_features = layer.selectedFeatures()

            if not selected_features:
                self.iface.messageBar().pushMessage("Info", "Keine Adresse ausgewählt. Bitte wählen Sie einen Punkt aus.", level=Qgis.Info)
                return

            try:
                feature = selected_features[0]

                # Neue Feldnamen
                adresspunkt_id = feature["adrcd_subcd"]
                adrcd = feature["adrcd"]  # Neuer Zugriff auf ADRCD
                strassenname = feature["strassenname"]
                hausnummer = feature["hnr_adr_zu"]

                # label_adresse ist ein QTextEdit
                self.ui.label_adresse.setPlainText(f"{strassenname}, {hausnummer}")

                # Highlight aktivieren
                geom = feature.geometry()
                if hasattr(self, "adresspunkt_highlight") and self.adresspunkt_highlight:
                    self.adresspunkt_highlight.hide()
                self.adresspunkt_highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                self.adresspunkt_highlight.setColor(QColor(Qt.red))
                self.adresspunkt_highlight.setWidth(3)
                self.adresspunkt_highlight.show()

                # Neue IDs merken
                self.gewaehlte_adresse = adresspunkt_id
                self.gewaehlter_adrcd = adrcd  # Speichere ADRCD
                QgsMessageLog.logMessage(f"DEBUG: Adresspunkt ausgewählt, adrcd_subcd={adresspunkt_id}, adrcd={adrcd}", "Hauseinfuehrung", Qgis.Info)

            except KeyError as e:
                self.iface.messageBar().pushMessage(
                    "Fehler", f"Fehlendes Attribut im ausgewählten Adresspunkt: {e}", level=Qgis.Critical
                )

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(on_adresspunkt_selected)

    def pruefungen_durchfuehren(self):
        fehler = []
        if self.ui.comboBox_Status.currentText() == "":
            fehler.append("Bitte wählen Sie einen Status aus.")
        if self.edit_mode:
            if not self.ui.checkBox_aufschlieung.isChecked() and (not hasattr(self, "gewaehlte_adresse") or self.gewaehlte_adresse is None):
                fehler.append("Kein Adresspunkt ausgewählt.")
        else:
            if self.ui.checkBox_direkt.isChecked():
                if not hasattr(self, "gewaehlter_verteiler") or self.gewaehlter_verteiler is None:
                    fehler.append("Kein Verteilerkasten ausgewählt.")
            else:
                if (not hasattr(self, "startpunkt_id") or self.startpunkt_id is None) and (not hasattr(self, "abzweigung_id") or self.abzweigung_id is None):
                    fehler.append("Kein Parent Leerrohr oder Abzweigung ausgewählt.")
                if not hasattr(self, "gewaehlte_rohrnummer") or self.gewaehlte_rohrnummer is None:
                    fehler.append("Keine Rohrnummer ausgewählt.")
            if not hasattr(self, "erfasste_geom") or self.erfasste_geom is None:
                fehler.append("Kein Verlauf der Hauseinführung erfasst.")
            if not self.ui.checkBox_aufschlieung.isChecked() and (not hasattr(self, "gewaehlte_adresse") or self.gewaehlte_adresse is None):
                fehler.append("Kein Adresspunkt ausgewählt.")
        return fehler

    def daten_pruefen(self):
        """Führt Prüfungen durch und zeigt Ergebnisse im Label an."""
        QgsMessageLog.logMessage("DEBUG: Starte daten_pruefen", "Hauseinfuehrung", Qgis.Info)

        fehler = []
        hinweise = []

        # Grundprüfungen
        if self.ui.comboBox_Status.currentText() == "":
            fehler.append("Bitte wählen Sie einen Status aus.")

        if self.edit_mode:
            # Update-Fall
            if not self.ui.checkBox_aufschlieung.isChecked() and (not hasattr(self, "gewaehlte_adresse") or self.gewaehlte_adresse is None):
                fehler.append("Kein Adresspunkt ausgewählt.")
        else:
            # Neu-Anlage
            if self.ui.checkBox_direkt.isChecked():
                if not hasattr(self, "gewaehlter_verteiler") or self.gewaehlter_verteiler is None:
                    fehler.append("Kein Verteilerkasten ausgewählt.")
            else:
                if (not hasattr(self, "startpunkt_id") or self.startpunkt_id is None) and (not hasattr(self, "abzweigung_id") or self.abzweigung_id is None):
                    fehler.append("Kein Parent Leerrohr oder Abzweigung ausgewählt.")
                if not hasattr(self, "gewaehlte_rohrnummer") or self.gewaehlte_rohrnummer is None:
                    fehler.append("Keine Rohrnummer ausgewählt.")
            if not hasattr(self, "erfasste_geom") or self.erfasste_geom is None:
                fehler.append("Kein Verlauf der Hauseinführung erfasst.")
            if not self.ui.checkBox_aufschlieung.isChecked() and (not hasattr(self, "gewaehlte_adresse") or self.gewaehlte_adresse is None):
                fehler.append("Kein Adresspunkt ausgewählt.")

        # Nur wenn Parent & VKG da sind: Logikprüfungen (Belegung/Erreichbarkeit)
        if not self.ui.checkBox_direkt.isChecked() and not fehler:
            try:
                conn = psycopg2.connect(**self.db_details)
                cur = conn.cursor()

                is_abzweigung = hasattr(self, "abzweigung_id") and self.abzweigung_id is not None
                parent_id = self.abzweigung_id if is_abzweigung else self.startpunkt_id
                vkg_id = self.gewaehlter_verteiler

                # 1) Belegung am gleichen VKG verbieten
                if is_abzweigung:
                    cur.execute("""
                        SELECT 1 FROM lwl."LWL_Hauseinfuehrung"
                        WHERE "ID_ABZWEIGUNG" = %s AND "VKG_LR" = %s AND "ROHRNUMMER" = %s
                        LIMIT 1
                    """, (parent_id, vkg_id, self.gewaehlte_rohrnummer))
                else:
                    cur.execute("""
                        SELECT 1 FROM lwl."LWL_Hauseinfuehrung"
                        WHERE "ID_LEERROHR" = %s AND "VKG_LR" = %s AND "ROHRNUMMER" = %s
                        LIMIT 1
                    """, (parent_id, vkg_id, self.gewaehlte_rohrnummer))
                if cur.fetchone():
                    fehler.append(f"Rohrnummer {self.gewaehlte_rohrnummer} ist am gewählten Verteiler bereits durch eine HA belegt.")

                # 2) Erreichbarkeit zum gewählten VKG schnell prüfen (Ende am VKG?)
                #    (ausreichend, bis das Verbinder-Tool die volle Graph-Prüfung liefert)
                if is_abzweigung:
                    cur.execute('SELECT "VONKNOTEN","NACHKNOTEN" FROM lwl."LWL_Leerrohr_Abzweigung" WHERE id=%s', (parent_id,))
                else:
                    cur.execute('SELECT "VONKNOTEN","NACHKNOTEN" FROM lwl."LWL_Leerrohr" WHERE id=%s', (parent_id,))
                row = cur.fetchone()
                if row:
                    vonk, nachk = row
                    if vkg_id not in (vonk, nachk):
                        hinweise.append("Hinweis: Das gewählte Leerrohr endet nicht direkt am gewählten Verteiler. Prüfe Verbindungen im Verbinder-Tool.")
                else:
                    fehler.append("Parent-Objekt konnte nicht gelesen werden.")

                # 3) Zweiter VKG-Fall (gleiche Rohrnummer von der anderen Seite zulassen)
                #    Gibt es am *anderen* VKG bereits eine HA mit derselben Rohrnummer? -> OK, nur Hinweis.
                #    (Trim der Geometrie implementieren wir im nächsten Schritt bei daten_importieren)
                if row:
                    andere_vkg_kandidaten = []
                    # Welche Enden sind Verteiler?
                    cur.execute('SELECT id FROM lwl."LWL_Knoten" WHERE "id" IN (%s,%s) AND "TYP" = %s', (vonk, nachk, 'Verteilerkasten'))
                    end_vkgs = [r[0] for r in cur.fetchall()]
                    for k in end_vkgs:
                        if k != vkg_id:
                            andere_vkg_kandidaten.append(k)

                    if andere_vkg_kandidaten:
                        if is_abzweigung:
                            cur.execute("""
                                SELECT 1 FROM lwl."LWL_Hauseinfuehrung"
                                WHERE "ID_ABZWEIGUNG" = %s AND "VKG_LR" = ANY(%s) AND "ROHRNUMMER" = %s
                                LIMIT 1
                            """, (parent_id, andere_vkg_kandidaten, self.gewaehlte_rohrnummer))
                        else:
                            cur.execute("""
                                SELECT 1 FROM lwl."LWL_Hauseinfuehrung"
                                WHERE "ID_LEERROHR" = %s AND "VKG_LR" = ANY(%s) AND "ROHRNUMMER" = %s
                                LIMIT 1
                            """, (parent_id, andere_vkg_kandidaten, self.gewaehlte_rohrnummer))
                        if cur.fetchone():
                            hinweise.append("Hinweis: Gleiche Rohrnummer ist am anderen Verteiler bereits belegt – diese HA darf bis zum bestehenden virtuellen Knoten geführt werden (Trim erfolgt beim Import).")

                conn.close()
            except Exception as e:
                fehler.append(f"Logikprüfung fehlgeschlagen: {e}")

        # Ausgabe
        if fehler:
            self.ui.label_Pruefung.setPlainText("\n".join(fehler))
            self.ui.label_Pruefung.setStyleSheet("background-color: rgba(255, 0, 0, 0.2); color: black;")
            self.ui.pushButton_Import.setEnabled(False)
        else:
            text = "Alle Prüfungen erfolgreich bestanden."
            if hinweise:
                text += "\n" + "\n".join(hinweise)
            self.ui.label_Pruefung.setPlainText(text)
            self.ui.label_Pruefung.setStyleSheet("background-color: rgba(0, 255, 0, 0.2); color: black;")
            self.ui.pushButton_Import.setEnabled(True)

    def check_available_rohre(self):
        """
        Prüft, ob noch verfügbare Rohre existieren.
        'Verfügbar' = am gewählten VKG nicht durch eine HA belegt.
        (Verbindungen werden hier bewusst NICHT als Belegung behandelt.)
        """
        # Direktmodus: keine Rohrnummern
        if self.ui.checkBox_direkt.isChecked():
            return True

        # Subtyp stabil ermitteln (bevorzugt aus self.subtyp_id_aktiv)
        subtyp_id = getattr(self, "subtyp_id_aktiv", None)
        if subtyp_id is None:
            # Fallback: robust aus Label "SUBTYP: X" parsen
            try:
                txt = self.ui.label_subtyp.toPlainText().strip()
                if txt.upper().startswith("SUBTYP:"):
                    subtyp_id = int(txt.replace("SUBTYP:", "").strip())
            except Exception:
                subtyp_id = None

        if subtyp_id is None:
            self.iface.messageBar().pushMessage("Fehler", "SUBTYP unbekannt – bitte Parent wählen.", level=Qgis.Critical)
            return False

        # VKG muss gesetzt sein, sonst macht die Belegungsprüfung keinen Sinn
        vkg_id = getattr(self, "gewaehlter_verteiler", None)
        if vkg_id is None:
            self.iface.messageBar().pushMessage("Fehler", "Kein Verteiler gewählt.", level=Qgis.Critical)
            return False

        # Parent-ID + Abzweigungs-Flag
        is_abzweigung = bool(getattr(self, "abzweigung_id", None))
        parent_id = self.abzweigung_id if is_abzweigung else getattr(self, "startpunkt_id", None)
        if parent_id is None:
            self.iface.messageBar().pushMessage("Fehler", "Kein Parent-Objekt gewählt.", level=Qgis.Critical)
            return False

        try:
            # 1) Alle möglichen Rohrnummern für den aktiven Subtyp holen
            #    WICHTIG: lade_farben_und_rohrnummern gibt 3 Werte zurück!
            rohre_def, _subtyp_char, _typ = self.lade_farben_und_rohrnummern(int(subtyp_id))
            alle_rohrnummern = [r[1] for r in rohre_def]  # Annahme: (rohr_id, rohr_nummer, ...)

            # 2) Belegte Rohrnummern am gewählten VKG aus HA lesen
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()

            if is_abzweigung:
                cur.execute("""
                    SELECT DISTINCT ha."ROHRNUMMER"
                    FROM "lwl"."LWL_Hauseinfuehrung" ha
                    WHERE ha."ID_ABZWEIGUNG" = %s AND ha."VKG_LR" = %s
                """, (parent_id, vkg_id))
            else:
                cur.execute("""
                    SELECT DISTINCT ha."ROHRNUMMER"
                    FROM "lwl"."LWL_Hauseinfuehrung" ha
                    WHERE ha."ID_LEERROHR" = %s AND ha."VKG_LR" = %s
                """, (parent_id, vkg_id))

            belegte_rohre = {row[0] for row in cur.fetchall() if row[0] is not None}
            conn.close()

            # 3) Verfügbare = alle minus belegte
            verfuegbare = [n for n in alle_rohrnummern if n not in belegte_rohre]
            return len(verfuegbare) > 0

        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Fehler", f"Fehler beim Prüfen der verfügbaren Rohre: {e}", level=Qgis.Critical
            )
            return False

    def daten_importieren(self):
        QgsMessageLog.logMessage("DEBUG: Starte daten_importieren", "Hauseinfuehrung", Qgis.Info)
        self.direktmodus = self.ui.checkBox_direkt.isChecked()
        QgsMessageLog.logMessage(f"DEBUG: Direktmodus laut Checkbox = {self.direktmodus}", "Hauseinfuehrung", Qgis.Info)

        if not self.edit_mode and (not hasattr(self, 'erfasste_geom') or self.erfasste_geom is None):
            self.iface.messageBar().pushMessage(
                "Fehler", "Keine Geometrie erfasst. Bitte zuerst die Linie digitalisieren.", level=Qgis.Critical
            )
            return

        try:
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()

            kommentar = self.ui.label_Kommentar.text() if isinstance(self.ui.label_Kommentar, QLineEdit) else self.ui.label_Kommentar.toPlainText()
            beschreibung = self.ui.label_Kommentar_2.text() if isinstance(self.ui.label_Kommentar_2, QLineEdit) else self.ui.label_Kommentar_2.toPlainText()
            gefoerdert = self.ui.checkBox_Gefoerdert.isChecked()
            status_text = self.ui.comboBox_Status.currentText()
            status = self.status_dict.get(status_text) if status_text else None
            farbe = None
            vkg_lr = self.gewaehlter_verteiler
            adresspunkt_id = None if self.ui.checkBox_aufschlieung.isChecked() else self.gewaehlte_adresse
            adrcd = -1 if self.ui.checkBox_aufschlieung.isChecked() else self.gewaehlter_adrcd  # Setze -1 bei checked
            befestigt = self.ui.checkBox_Befestigt.isChecked()

            verlegt_am = None
            if hasattr(self.ui, "mDateTimeEdit_Strecke"):
                try:
                    verlegt_am = self.ui.mDateTimeEdit_Strecke.dateTime().toString("yyyy-MM-dd")
                    QgsMessageLog.logMessage(f"DEBUG: Datum verlegt_am={verlegt_am}", "Hauseinfuehrung", Qgis.Info)
                except Exception as e:
                    QgsMessageLog.logMessage(f"DEBUG: Fehler beim Abrufen des Datums: {e}", "Hauseinfuehrung", Qgis.Info)
                    verlegt_am = None

            QgsMessageLog.logMessage(f"DEBUG: adresspunkt_id={adresspunkt_id}, adrcd={adrcd}", "Hauseinfuehrung", Qgis.Info)
            QgsMessageLog.logMessage(f"DEBUG: status={status}", "Hauseinfuehrung", Qgis.Info)

            auftraggeber_id = None
            if self.gewaehlter_verteiler:
                cur.execute('SELECT "id_AUFTRAGGEBER" FROM lwl."LWL_Knoten" WHERE id = %s', (self.gewaehlter_verteiler,))
                result = cur.fetchone()
                if result:
                    auftraggeber_id = result[0]
                QgsMessageLog.logMessage(f"DEBUG: auftraggeber_id={auftraggeber_id}", "Hauseinfuehrung", Qgis.Info)

            if self.edit_mode:
                neue_rohrnummer = self.gewaehlte_rohrnummer
                alte_rohrnummer = None
                cur.execute("SELECT \"ROHRNUMMER\" FROM \"lwl\".\"LWL_Hauseinfuehrung\" WHERE \"id\" = %s", (self.selected_ha_id,))
                alte_rohrnummer = cur.fetchone()[0]

                if neue_rohrnummer != alte_rohrnummer:
                    if self.abzweigung_id:
                        query_belegte_rohre = """
                            SELECT DISTINCT ha."ROHRNUMMER"
                            FROM "lwl"."LWL_Hauseinfuehrung" ha
                            WHERE ha."ID_ABZWEIGUNG" = %s
                            AND ha."VKG_LR" = %s
                        """
                        cur.execute(query_belegte_rohre, (self.abzweigung_id, self.gewaehlter_verteiler))
                    else:
                        query_belegte_rohre = """
                            SELECT DISTINCT ha."ROHRNUMMER"
                            FROM "lwl"."LWL_Hauseinfuehrung" ha
                            WHERE ha."ID_LEERROHR" = %s
                            AND ha."VKG_LR" = %s
                        """
                        cur.execute(query_belegte_rohre, (self.startpunkt_id, self.gewaehlter_verteiler))
                    belegte_rohre = [row[0] for row in cur.fetchall()]

                    if neue_rohrnummer in belegte_rohre:
                        if self.abzweigung_id:
                            cur.execute("""
                                SELECT "id" FROM "lwl"."LWL_Hauseinfuehrung"
                                WHERE "ID_ABZWEIGUNG" = %s AND "VKG_LR" = %s AND "ROHRNUMMER" = %s
                            """, (self.abzweigung_id, self.gewaehlter_verteiler, neue_rohrnummer))
                        else:
                            cur.execute("""
                                SELECT "id" FROM "lwl"."LWL_Hauseinfuehrung"
                                WHERE "ID_LEERROHR" = %s AND "VKG_LR" = %s AND "ROHRNUMMER" = %s
                            """, (self.startpunkt_id, self.gewaehlter_verteiler, neue_rohrnummer))
                        anderes_ha_id = cur.fetchone()[0]

                        if anderes_ha_id != self.selected_ha_id:
                            cur.execute("BEGIN")
                            cur.execute("""
                                UPDATE "lwl"."LWL_Hauseinfuehrung"
                                SET "ROHRNUMMER" = NULL
                                WHERE "id" = %s
                            """, (anderes_ha_id,))
                            cur.execute("""
                                UPDATE "lwl"."LWL_Hauseinfuehrung"
                                SET "ROHRNUMMER" = %s
                                WHERE "id" = %s
                            """, (neue_rohrnummer, self.selected_ha_id))
                            cur.execute("""
                                UPDATE "lwl"."LWL_Hauseinfuehrung"
                                SET "ROHRNUMMER" = %s
                                WHERE "id" = %s
                            """, (alte_rohrnummer, anderes_ha_id))
                            cur.execute("COMMIT")
                            self.iface.messageBar().pushMessage("Info", f"Rohrnummern getauscht (ID {anderes_ha_id}).", level=Qgis.Info)

                cur.execute("""
                    UPDATE "lwl"."LWL_Hauseinfuehrung"
                    SET "KOMMENTAR" = %s, "BESCHREIBUNG" = %s, "GEFOERDERT" = %s, "STATUS" = %s,
                        "VERLEGT_AM" = %s, "HA_ADRCD_SUBCD" = %s, "HA_ADRCD" = %s, "ROHRNUMMER" = %s, "BEFESTIGT" = %s
                    WHERE "id" = %s
                """, (kommentar, beschreibung, gefoerdert, status, verlegt_am, adresspunkt_id, adrcd, neue_rohrnummer, befestigt, self.selected_ha_id))
            else:
                if self.direktmodus:
                    rohrnummer = 0
                    farbe = 'direkt'

                    knoten_layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
                    request = QgsFeatureRequest().setFilterExpression(f'"id" = {self.gewaehlter_verteiler}')
                    features = list(knoten_layer.getFeatures(request))
                    if not features or not features[0].geometry() or features[0].geometry().isEmpty():
                        self.iface.messageBar().pushMessage("Fehler", "Verteilerkasten ohne Geometrie.", level=Qgis.Critical)
                        return

                    vkg_feature = features[0]
                    points = self.erfasste_geom.asPolyline()
                    if len(points) < 2:
                        self.iface.messageBar().pushMessage("Fehler", "Die Linie muss mindestens zwei Punkte enthalten.", level=Qgis.Critical)
                        return

                    try:
                        startpunkt = vkg_feature.geometry().asPoint()
                        points[0] = startpunkt
                    except Exception as e:
                        self.iface.messageBar().pushMessage("Fehler", f"Geometrieproblem: {e}", level=Qgis.Critical)
                        return

                    self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
                    geom_wkt = self.erfasste_geom.asWkt()

                    cur.execute("""
                        INSERT INTO "lwl"."LWL_Hauseinfuehrung"
                        (geom, "ID_LEERROHR", "KOMMENTAR", "BESCHREIBUNG", "ROHRNUMMER", "FARBE",
                        "VKG_LR", "HA_ADRCD_SUBCD", "HA_ADRCD", "GEFOERDERT", "ID_KNOTEN", "id_AUFTRAGGEBER", "VERLEGT_AM", "STATUS", "BEFESTIGT")
                        VALUES (ST_SetSRID(ST_GeomFromText(%s), 31254), NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                        geom_wkt,
                        kommentar,
                        beschreibung,
                        rohrnummer,
                        farbe,
                        self.gewaehlter_verteiler,
                        adresspunkt_id,
                        adrcd,
                        gefoerdert,
                        self.gewaehlter_verteiler,
                        auftraggeber_id,
                        verlegt_am,
                        status,
                        befestigt
                    ))

                else:
                    rohrnummer = self.gewaehlte_rohrnummer
                    if hasattr(self, "gewaehlte_farb_id") and self.gewaehlte_farb_id is not None:
                        subtyp_id_text = self.ui.label_subtyp.toPlainText().replace("SUBTYP: ", "")
                        cur.execute(
                            'SELECT "FARBE" FROM lwl."LUT_Farbe_Rohr" WHERE "ROHRNUMMER" = %s AND "ID_CODIERUNG" IN (SELECT "ID_CODIERUNG" FROM lwl."LUT_Leerrohr_SubTyp" WHERE "id" = %s)',
                            (self.gewaehlte_rohrnummer, subtyp_id_text)
                        )
                        result = cur.fetchone()
                        farbe = result[0] if result else None
                        QgsMessageLog.logMessage(f"DEBUG: Farbe für Rohrnummer {self.gewaehlte_rohrnummer}: {farbe}", "Hauseinfuehrung", Qgis.Info)

                    if self.abzweigung_id is not None:
                        abzweig_layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr_Abzweigung")[0]
                        selected_features = [f for f in abzweig_layer.getFeatures() if f["id"] == self.abzweigung_id]
                        if not selected_features:
                            self.iface.messageBar().pushMessage("Fehler", "Abzweigung nicht gefunden.", level=Qgis.Critical)
                            return

                        geom_feature = selected_features[0]
                        leerrohr_geom = geom_feature.geometry()

                        points = self.erfasste_geom.asPolyline()
                        if len(points) == 0:
                            self.iface.messageBar().pushMessage("Fehler", "Erfasste Linie ist leer.", level=Qgis.Critical)
                            return

                        snapped_point = leerrohr_geom.closestSegmentWithContext(points[0])[1]
                        points[0] = QgsPointXY(snapped_point)
                        self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
                        geom_wkt = self.erfasste_geom.asWkt()

                        cur.execute("""
                            INSERT INTO "lwl"."LWL_Hauseinfuehrung"
                            (geom, "ID_ABZWEIGUNG", "KOMMENTAR", "BESCHREIBUNG", "ROHRNUMMER", "FARBE",
                            "VKG_LR", "HA_ADRCD_SUBCD", "HA_ADRCD", "GEFOERDERT", "id_AUFTRAGGEBER", "VERLEGT_AM", "STATUS", "BEFESTIGT")
                            VALUES (ST_SetSRID(ST_GeomFromText(%s), 31254), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                            geom_wkt,
                            self.abzweigung_id,
                            kommentar,
                            beschreibung,
                            rohrnummer,
                            farbe,
                            self.gewaehlter_verteiler,
                            adresspunkt_id,
                            adrcd,
                            gefoerdert,
                            auftraggeber_id,
                            verlegt_am,
                            status,
                            befestigt
                        ))

                    else:
                        leerrohr_layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
                        selected_features = [f for f in leerrohr_layer.getFeatures() if f["id"] == self.startpunkt_id]
                        if not selected_features:
                            self.iface.messageBar().pushMessage("Fehler", "Leerrohr nicht gefunden.", level=Qgis.Critical)
                            return

                        leerrohr_geom = selected_features[0].geometry()
                        points = self.erfasste_geom.asPolyline()
                        if len(points) == 0:
                            self.iface.messageBar().pushMessage("Fehler", "Erfasste Linie ist leer.", level=Qgis.Critical)
                            return

                        snapped_point = leerrohr_geom.closestSegmentWithContext(points[0])[1]
                        points[0] = QgsPointXY(snapped_point)
                        self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
                        geom_wkt = self.erfasste_geom.asWkt()

                        cur.execute("""
                            INSERT INTO "lwl"."LWL_Hauseinfuehrung"
                            (geom, "ID_LEERROHR", "KOMMENTAR", "BESCHREIBUNG", "ROHRNUMMER", "FARBE",
                            "VKG_LR", "HA_ADRCD_SUBCD", "HA_ADRCD", "GEFOERDERT", "id_AUFTRAGGEBER", "VERLEGT_AM", "STATUS", "BEFESTIGT")
                            VALUES (ST_SetSRID(ST_GeomFromText(%s), 31254), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                            geom_wkt,
                            self.startpunkt_id,
                            kommentar,
                            beschreibung,
                            rohrnummer,
                            farbe,
                            self.gewaehlter_verteiler,
                            adresspunkt_id,
                            adrcd,
                            gefoerdert,
                            auftraggeber_id,
                            verlegt_am,
                            status,
                            befestigt
                        ))

            conn.commit()
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)

            layer = QgsProject.instance().mapLayersByName("LWL_Hauseinfuehrung")[0]
            if layer:
                layer.dataProvider().reloadData()
                layer.triggerRepaint()
                self.iface.mapCanvas().refreshAllLayers()
                self.iface.mapCanvas().refresh()

            if self.mehrfachimport_modus:
                if hasattr(self, "rubber_band") and self.rubber_band:
                    self.rubber_band.reset()
                self.rubber_band = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.LineGeometry)
                self.rubber_band.setColor(Qt.red)
                self.rubber_band.setWidth(2)
                self.erfasste_geom = None
                self.erfasste_punkte = []
                self.gewaehlte_rohrnummer = None
                self.gewaehlte_farb_id = None
                if self.ausgewaehltes_rechteck:
                    try:
                        self.ausgewaehltes_rechteck.setPen(QPen(Qt.black, 1))
                    except RuntimeError:
                        pass
                self.ausgewaehltes_rechteck = None

                self.ui.label_adresse.setPlainText("")
                self.gewaehlte_adresse = None
                self.gewaehlter_adrcd = None
                if hasattr(self, "adresspunkt_highlight") and self.adresspunkt_highlight:
                    self.adresspunkt_highlight.hide()
                    self.adresspunkt_highlight = None

                if not self.ui.checkBox_direkt.isChecked():
                    subtyp_id = self.ui.label_subtyp.toPlainText().replace("SUBTYP: ", "")
                    farbschema = self.ui.label_farbschema.toPlainText()
                    firma = self.ui.label_firma.toPlainText().replace("Hersteller: ", "")
                    is_abzweigung = hasattr(self, "abzweigung_id") and self.abzweigung_id is not None
                    self.zeichne_rohre(subtyp_id, farbschema, firma, is_abzweigung=is_abzweigung)

                if not self.check_available_rohre():
                    self.ui.checkBox_Mehrfachimport.setChecked(False)
                    self.mehrfachimport_modus = False
                    self.iface.messageBar().pushMessage(
                        "Info", "Keine weiteren Rohre verfügbar. Mehrfachimport-Modus deaktiviert.", level=Qgis.Info
                    )
                else:
                    self.iface.messageBar().pushMessage(
                        "Info", "Daten importiert. Bitte erfassen Sie die nächste Hauseinführung.", level=Qgis.Success
                    )

            else:
                self.formular_initialisieren()

        except Exception as e:
            conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Fehler beim Importieren: {e}", level=Qgis.Critical)
        finally:
            conn.close()

    def formular_initialisieren(self):
        """Setzt das Formular auf den Ausgangszustand zurück und entfernt Highlights."""
        QgsMessageLog.logMessage("DEBUG: Starte formular_initialisieren", "Hauseinfuehrung", Qgis.Info)
        self.startpunkt_id = None
        self.erfasste_geom = None
        self.gewaehlte_rohrnummer = None
        self.ui.label_parentLeerrohr.setPlainText("")
        self.ui.label_verteiler.setPlainText("")
        self.ui.label_firma.setPlainText("")
        self.ui.label_farbschema.setPlainText("")
        self.ui.label_subtyp.setPlainText("")
        if isinstance(self.ui.label_Kommentar, QLineEdit):
            self.ui.label_Kommentar.setText("")
        else:
            self.ui.label_Kommentar.setPlainText("")
        if isinstance(self.ui.label_Kommentar_2, QLineEdit):
            self.ui.label_Kommentar_2.setText("")
        else:
            self.ui.label_Kommentar_2.setPlainText("")
        self.ui.comboBox_Status.setCurrentIndex(-1)
        # label_adresse ist ein QTextEdit
        self.ui.label_adresse.setPlainText("")

        # Datum zurücksetzen
        if hasattr(self, "mDateTimeEdit_Strecke"):
            self.ui.mDateTimeEdit_Strecke.setDateTime(QDateTime.currentDateTime())

        if self.adresspunkt_highlight:
            self.adresspunkt_highlight.hide()
            self.adresspunkt_highlight = None

        # Entferne grafische Elemente
        if hasattr(self, "scene"):
            self.scene.clear()  # Entferne alle Felder
        if hasattr(self, "rubber_band") and self.rubber_band:
            self.rubber_band.reset()

        # Entferne Highlights
        if self.highlights:
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()

        # Entferne Verteiler-Highlight (Einzelobjekt)
        if hasattr(self, "verteiler_highlight") and self.verteiler_highlight:
            self.verteiler_highlight.hide()
            self.verteiler_highlight = None

        # Entferne roten Rahmen vom ausgewählten Quadrat
        if self.ausgewaehltes_rechteck:
            try:
                self.ausgewaehltes_rechteck.setPen(QPen(Qt.black, 1))
            except RuntimeError:
                pass
        self.ausgewaehltes_rechteck = None

        # Edit-Modus zurücksetzen
        self.edit_mode = False
        self.selected_ha_id = None
        self.ui.pushButton_Import.setText("Import")
        self.ui.pushButton_verlauf_HA.setEnabled(True)
        self.ui.pushButton_select_leerrohr.setEnabled(True)

        # Zeige eine Info-Meldung an
        self.iface.messageBar().pushMessage("Info", "Formular und Highlights wurden zurückgesetzt.", level=Qgis.Info)

    def abbrechen_und_schliessen(self):
        """Formular zurücksetzen, Vorschau entfernen, Fenster schließen."""
        QgsMessageLog.logMessage("DEBUG: Starte abbrechen_und_schliessen", "Hauseinfuehrung", Qgis.Info)
        self.clear_ha_preview()          # <--- neu
        self.formular_initialisieren()   # vorhandene Zurücksetzen-Logik
        self.close()

    def closeEvent(self, event):
        """Wird aufgerufen, wenn das Fenster geschlossen wird."""
        QgsMessageLog.logMessage("DEBUG: Starte closeEvent", "Hauseinfuehrung", Qgis.Info)

        # NEU: zuerst alle temporären Zeichen-/Tool-Objekte entsorgen
        self.clear_ha_preview()  # <--- neu

        # Bestehende Highlights etc. entfernen
        if self.highlights:
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()

        if hasattr(self, "adresspunkt_highlight") and self.adresspunkt_highlight:
            self.adresspunkt_highlight.hide()
            self.adresspunkt_highlight = None

        if self.ausgewaehltes_rechteck:
            try:
                self.ausgewaehltes_rechteck.setPen(QPen(Qt.black, 1))
            except RuntimeError:
                pass
        self.ausgewaehltes_rechteck = None

        HauseinfuehrungsVerlegungsTool.instance = None
        super().closeEvent(event)
