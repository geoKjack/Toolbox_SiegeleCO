# -*- coding: utf-8 -*-
"""
HauseinfuehrungsVerlegungsTool
Verlegt Hauseinführungen durch Auswahl von Parent Leerrohr, Verlauf und Endpunkten.
"""

from PyQt5.QtCore import Qt, QRectF, QObject, pyqtSignal
from PyQt5.QtWidgets import QDialog, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsRectItem, QGraphicsPolygonItem
from qgis.core import QgsProject, Qgis, QgsFeatureRequest, QgsDataSourceUri, QgsWkbTypes, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsFeature
from qgis.gui import QgsHighlight, QgsMapToolEdit, QgsMapToolEmitPoint, QgsMapToolCapture, QgsRubberBand, QgsMapTool
from PyQt5.QtGui import QColor, QBrush, QFont, QPolygonF, QMouseEvent
import psycopg2
from .hauseinfuehrung_verlegen_dialog import Ui_HauseinfuehrungsVerlegungsToolDialogBase
from PyQt5.QtCore import Qt, QRectF, QObject, pyqtSignal, QPointF

class ClickableRect(QGraphicsRectItem):
    def __init__(self, x, y, width, height, rohrnummer, callback, parent=None):
        super().__init__(parent)
        self.setRect(x, y, width, height)
        self.rohrnummer = rohrnummer  # Speichere die zugehörige Rohrnummer
        self.callback = callback  # Übergib die Callback-Funktion

    def mousePressEvent(self, event):
        """Wird ausgelöst, wenn auf das Rechteck geklickt wird."""
        if self.callback:
            self.callback(self.rohrnummer)  # Rufe die Callback-Funktion mit der Rohrnummer auf
        super().mousePressEvent(event)

class CustomLineCaptureTool(QgsMapTool):
    """Ein benutzerdefiniertes Werkzeug zur Digitalisierung einer Linie."""

    def __init__(self, canvas, capture_callback, finalize_callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.capture_callback = capture_callback
        self.finalize_callback = finalize_callback
        self.points = []

    def canvasPressEvent(self, event):
        """Wird aufgerufen, wenn die Maus gedrückt wird."""
        if event.button() == Qt.LeftButton:
            # Linke Maustaste: Punkt hinzufügen
            point = self.toMapCoordinates(event.pos())
            self.points.append(QgsPointXY(point))
            self.capture_callback(point)
        elif event.button() == Qt.RightButton:
            # Rechte Maustaste: Linie abschließen
            self.finalize_callback(self.points)
            self.points = []  # Punkte zurücksetzen

    def canvasMoveEvent(self, event):
        """Bewegt die Maus auf der Karte (optional, falls notwendig)."""
        pass

    def canvasReleaseEvent(self, event):
        """Wird aufgerufen, wenn die Maus losgelassen wird (optional)."""
        pass

    def deactivate(self):
        """Werkzeug deaktivieren."""
        super().deactivate()
        self.points = []

class HauseinfuehrungsVerlegungsTool(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent, Qt.WindowStaysOnTopHint)
        self.iface = iface
        self.ui = Ui_HauseinfuehrungsVerlegungsToolDialogBase()
        self.ui.setupUi(self)

        # Initialisiere wichtige Variablen
        self.startpunkt_id = None
        self.verlauf_ids = []
        self.highlights = []

        # Datenbankverbindung vorbereiten
        self.db_uri = None
        self.conn = None
        self.init_database_connection()

        # Buttons mit Aktionen verknüpfen
        self.ui.pushButton_parentLeerrohr.clicked.connect(self.aktion_parent_leerrohr)
        self.ui.pushButton_verlauf_HA.clicked.connect(self.aktion_verlauf)
        self.ui.pushButton_Import.clicked.connect(self.daten_importieren)

    def init_database_connection(self):
        """Initialisiert die Datenbankverbindung."""
        try:
            db_uri = self.get_database_connection()
            self.db_uri = QgsDataSourceUri(db_uri)
            self.conn = psycopg2.connect(
                dbname=self.db_uri.database(),
                user=self.db_uri.username(),
                password=self.db_uri.password(),
                host=self.db_uri.host(),
                port=self.db_uri.port()
            )
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Datenbankverbindung konnte nicht hergestellt werden: {e}", level=Qgis.Critical)

    def get_database_connection(self):
        """Holt die aktuelle Datenbankverbindung aus dem Projekt."""
        project = QgsProject.instance()
        layers = project.mapLayers().values()
        for layer in layers:
            # Prüfe, ob ein PostgreSQL-Layer vorhanden ist und ob er den gewünschten Namen hat
            if layer.dataProvider().name() == "postgres" and layer.name() == "LWL_Leerrohr":
                uri = layer.dataProvider().dataSourceUri()
                return uri
        raise Exception("Keine aktive PostgreSQL-Datenbankverbindung gefunden.")

    def aktion_parent_leerrohr(self):
        """Aktion für die Auswahl des Parent Leerrohrs."""
        self.iface.messageBar().pushMessage("Bitte wählen Sie das Parent Leerrohr", level=Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        self.iface.setActiveLayer(layer)
        self.iface.actionSelect().trigger()

        def onParentLeerrohrSelected():
            if self.highlights:
                for highlight in self.highlights:
                    highlight.hide()
                self.highlights.clear()

            selected_features = layer.selectedFeatures()
            if selected_features:
                # Extrahiere die Werte des ausgewählten Leerrohrs
                leerrohr_id = selected_features[0]["id"]
                subtyp_id = selected_features[0]["SUBTYP"]  # ID des Subtyps
                farbschema = selected_features[0]["FARBSCHEMA"]

                # Setze die Parent-ID für spätere Verarbeitung
                self.startpunkt_id = leerrohr_id

                # Lade die Lookup-Tabelle für die Subtypen
                lookup_layer = QgsProject.instance().mapLayersByName("LUT_Leerrohr_SubTyp")[0]
                subtyp_wert = "Unbekannt"  # Fallback-Wert, falls Lookup fehlschlägt

                for feature in lookup_layer.getFeatures():
                    if feature["id"] == subtyp_id:  # Vergleiche SUBTYP-ID
                        subtyp_wert = feature["SUBTYP"]  # Beschreibung holen
                        break

                # Setze die Labels
                self.ui.label_parentLeerrohr.setText(str(leerrohr_id))  # Nur die ID
                self.ui.label_farbschema.setText(str(farbschema))  # Farbschema-Wert
                self.ui.label_subtyp.setText(f"SUBTYP: {subtyp_wert}")  # Beschreibung des Subtyps

                # Debug-Ausgabe
                self.iface.messageBar().pushMessage("Info", f"SUBTYP = {subtyp_wert}, FARBSCHEMA = {farbschema}", level=Qgis.Info)

                # Zeichne die Rohrnummern-Farbcodierungen
                self.zeichne_rohre(subtyp_id, farbschema)

                # Highlight das ausgewählte Feature
                for feature in selected_features:
                    geom = feature.geometry()
                    highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                    highlight.setColor(Qt.red)  # Farbe für das Highlight
                    highlight.setWidth(3)  # Dicke des Highlights
                    highlight.show()
                    self.highlights.append(highlight)

            else:
                self.startpunkt_id = None
                self.iface.messageBar().pushMessage("Fehler", "Kein Leerrohr ausgewählt.", level=Qgis.Critical)

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(onParentLeerrohrSelected)

    def zeichne_rohre(self, subtyp_id, farbschema):
        """Zeichnet klickbare Rechtecke für Rohre basierend auf Subtyp und Farbschema.
           Bereits belegte Rohrnummern werden ausgegraut und deaktiviert.
        """
        self.scene = QGraphicsScene()
        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)

        # Daten laden
        rohre = self.lade_farben_und_rohrnummern(subtyp_id, farbschema)

        if not rohre:
            self.iface.messageBar().pushMessage("Info", "Keine Rohre zum Zeichnen gefunden.", level=Qgis.Warning)
            return

        # Abfrage: Welche Rohrnummern sind bereits belegt?
        try:
            cur = self.conn.cursor()
            query = """
                SELECT "ROHRNUMMER" 
                FROM "lwl"."LWL_Hauseinfuehrung" 
                WHERE "ID_LEERROHR" = %s
            """
            cur.execute(query, (self.startpunkt_id,))
            belegte_rohre = [row[0] for row in cur.fetchall()]
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Fehler beim Abrufen belegter Rohrnummern: {e}", level=Qgis.Critical)
            return

        # Sortiere die Rohrdaten nach Rohrnummer (erste Spalte in der Liste)
        rohre.sort(key=lambda x: x[0])  # Sortiere nach der Rohrnummer

        x_offset = 10
        y_offset = 10
        rect_width = 30
        rect_height = 30
        spacing = 10
        font_size = 10

        for i, (rohrnummer, farbcode) in enumerate(rohre):
            farbteile = farbcode.split("-")
            x_pos = x_offset + i * (rect_width + spacing)

            # Prüfe, ob die Rohrnummer bereits belegt ist
            ist_belegt = rohrnummer in belegte_rohre

            # Klickbares Rechteck erstellen
            if not ist_belegt:
                clickable_rect = ClickableRect(x_pos, y_offset, rect_width, rect_height, rohrnummer, self.handle_rect_click)
            else:
                # Dummy-Rechteck ohne Funktion für belegte Rohrnummern
                clickable_rect = QGraphicsRectItem(x_pos, y_offset, rect_width, rect_height)
                clickable_rect.setToolTip(f"Rohrnummer {rohrnummer} bereits belegt")

            self.scene.addItem(clickable_rect)

            # Einfarbige Rechtecke
            if len(farbteile) == 1:
                color = QColor(farbteile[0])
                if ist_belegt:
                    color.setAlpha(100)  # Reduziert die Deckkraft für belegte Rohrnummern
                clickable_rect.setBrush(QBrush(color))
            elif len(farbteile) == 2:
                # Zweifarbige Rechtecke: Diagonale Teilung
                color1 = QColor(farbteile[0])
                color2 = QColor(farbteile[1])
                if ist_belegt:
                    color1.setAlpha(100)
                    color2.setAlpha(100)

                polygon1 = QGraphicsPolygonItem()
                polygon1.setPolygon(QPolygonF([
                    QPointF(x_pos, y_offset),  # Oben links
                    QPointF(x_pos + rect_width, y_offset),  # Oben rechts
                    QPointF(x_pos, y_offset + rect_height)  # Unten links
                ]))
                polygon1.setBrush(QBrush(color1))
                self.scene.addItem(polygon1)

                polygon2 = QGraphicsPolygonItem()
                polygon2.setPolygon(QPolygonF([
                    QPointF(x_pos + rect_width, y_offset),  # Oben rechts
                    QPointF(x_pos + rect_width, y_offset + rect_height),  # Unten rechts
                    QPointF(x_pos, y_offset + rect_height)  # Unten links
                ]))
                polygon2.setBrush(QBrush(color2))
                self.scene.addItem(polygon2)

            # Halo-Effekt für Text (Rohrnummer)
            halo_text = QGraphicsSimpleTextItem(str(rohrnummer))
            halo_text.setBrush(QBrush(Qt.white))
            halo_text.setFont(QFont("Arial", font_size, QFont.Bold))
            halo_text.setZValue(1)  # Hintergrundebene
            halo_text.setPos(x_pos + rect_width / 4 - 1, y_offset + rect_height / 4 - 1)  # Leichte Verschiebung für den Halo
            self.scene.addItem(halo_text)

            text_item = QGraphicsSimpleTextItem(str(rohrnummer))
            text_item.setBrush(QBrush(Qt.black if not ist_belegt else Qt.gray))
            text_item.setFont(QFont("Arial", font_size, QFont.Bold))
            text_item.setZValue(2)  # Vordere Ebene
            text_item.setPos(x_pos + rect_width / 4, y_offset + rect_height / 4)
            self.scene.addItem(text_item)

        self.scene.update()


    def handle_rect_click(self, rohrnummer):
        """Verarbeitet Klicks auf ein Rechteck."""
        self.iface.messageBar().pushMessage("Info", f"Rechteck mit Rohrnummer {rohrnummer} wurde angeklickt!", level=Qgis.Info)


    def lade_farben_und_rohrnummern(self, subtyp_id, farbschema):
        """Lädt Farben und Rohrnummern aus der Tabelle LUT_Farbe_Rohr."""
        try:
            cur = self.conn.cursor()
            query = """
                SELECT "ROHRNUMMER", "FARBCODE" 
                FROM "lwl"."LUT_Farbe_Rohr" 
                WHERE "SUBTYP" = %s AND "FARBSCHEMA" = %s
            """
            self.iface.messageBar().pushMessage("Info", f"SUBTYP = {subtyp_id}, FARBSCHEMA = {farbschema}", level=Qgis.Info)
            cur.execute(query, (subtyp_id, farbschema))
            result = cur.fetchall()
            if not result:
                self.iface.messageBar().pushMessage("Info", "Keine Rohre gefunden.", level=Qgis.Warning)
            return result
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Farben konnten nicht geladen werden: {e}", level=Qgis.Critical)
            return []

    def aktion_verlauf(self):
        """Aktion zum Erfassen der Liniengeometrie der Hauseinführung mit Snap auf das Parent Leerrohr."""
        if self.startpunkt_id is None:
            self.iface.messageBar().pushMessage(
                "Fehler", "Kein Parent Leerrohr ausgewählt. Bitte zuerst ein Leerrohr auswählen.", level=Qgis.Critical
            )
            return

        self.iface.messageBar().pushMessage(
            "Info", "Bitte digitalisieren Sie die Linie der Hauseinführung (Rechtsklick zum Abschließen).", level=Qgis.Info
        )

        # Initialisiere Variablen
        self.erfasste_punkte = []
        if hasattr(self, "rubber_band") and self.rubber_band:
            self.rubber_band.reset()
        self.rubber_band = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.LineGeometry)
        self.rubber_band.setColor(Qt.red)
        self.rubber_band.setWidth(2)

        # Hole die Geometrie des ausgewählten Leerrohrs
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        selected_features = [f for f in layer.getFeatures() if f["id"] == self.startpunkt_id]

        if not selected_features:
            self.iface.messageBar().pushMessage(
                "Fehler", "Das ausgewählte Leerrohr konnte nicht gefunden werden.", level=Qgis.Critical
            )
            return

        leerrohr_geom = selected_features[0].geometry()

        def point_captured(point):
            """Callback, wenn ein Punkt erfasst wird."""
            if len(self.erfasste_punkte) == 0:
                # Der erste Punkt wird explizit gesnapped
                snapped_point = leerrohr_geom.closestSegmentWithContext(point)[1]
                self.erfasste_punkte.append(QgsPointXY(snapped_point))
                self.iface.messageBar().pushMessage(
                    "Info", f"Startpunkt gesnapped: {snapped_point}.", level=Qgis.Success
                )
            else:
                # Weitere Punkte ohne Snapping hinzufügen
                self.erfasste_punkte.append(QgsPointXY(point))

            self.rubber_band.setToGeometry(QgsGeometry.fromPolylineXY(self.erfasste_punkte), None)

        def finalize_geometry(points):
            """Callback, wenn die Linie abgeschlossen wird (Rechtsklick)."""
            if len(points) < 2:
                self.iface.messageBar().pushMessage(
                    "Fehler", "Mindestens zwei Punkte erforderlich, um eine Linie zu erstellen.", level=Qgis.Critical
                )
                self.erfasste_punkte.clear()
                self.rubber_band.reset()
                return

            # Erstelle die finale Geometrie
            line_geom = QgsGeometry.fromPolylineXY(points)

            # Finalisiere und speichere die Geometrie
            self.erfasste_geom = line_geom
            self.iface.messageBar().pushMessage(
                "Info", "Linie erfolgreich erfasst. Bitte Attribute ausfüllen und auf Import klicken.", level=Qgis.Success
            )
            self.iface.mapCanvas().unsetMapTool(self.map_tool)  # Werkzeug deaktivieren

        # Setze das benutzerdefinierte Werkzeug
        self.map_tool = CustomLineCaptureTool(self.iface.mapCanvas(), point_captured, finalize_geometry)
        self.iface.mapCanvas().setMapTool(self.map_tool)


        
    def daten_importieren(self):
        """Importiert die Geometrie und Attribute in die Datenbank."""
        if not hasattr(self, 'erfasste_geom') or self.erfasste_geom is None:
            self.iface.messageBar().pushMessage(
                "Fehler", "Keine Geometrie erfasst. Bitte zuerst die Linie digitalisieren.", level=Qgis.Critical
            )
            return  # Import abbrechen

        # Hole die Geometrie des ausgewählten Leerrohrs
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        selected_features = [f for f in layer.getFeatures() if f["id"] == self.startpunkt_id]

        if not selected_features:
            self.iface.messageBar().pushMessage(
                "Fehler", "Das ausgewählte Leerrohr konnte nicht gefunden werden.", level=Qgis.Critical
            )
            return

        leerrohr_geom = selected_features[0].geometry()

        try:
            # Snap den Startpunkt erneut auf das Leerrohr
            points = self.erfasste_geom.asPolyline()
            if len(points) > 0:
                snapped_point = leerrohr_geom.closestSegmentWithContext(points[0])[1]
                points[0] = QgsPointXY(snapped_point)
                self.erfasste_geom = QgsGeometry.fromPolylineXY(points)

            cur = self.conn.cursor()
            # Hol die Attribute aus den UI-Feldern
            kommentar = self.ui.label_Kommentar.text()
            startpunkt_id = self.startpunkt_id  # Vom Parent Leerrohr gespeicherte ID
            geom_wkt = self.erfasste_geom.asWkt()  # Geometrie in WKT umwandeln

            # Datenbankabfrage
            query = """
                INSERT INTO "lwl"."LWL_Hauseinfuehrung" (geom, "ID_LEERROHR", "KOMMENTAR", "ROHRNUMMER")
                VALUES (ST_SetSRID(ST_GeomFromText(%s), 31254), %s, %s, 1)
            """
            cur.execute(query, (geom_wkt, startpunkt_id, kommentar))
            self.conn.commit()

            # Erfolgsmeldung
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)

            # Zurücksetzen der Geometrie
            self.erfasste_geom = None

        except Exception as e:
            self.conn.rollback()
            self.iface.messageBar().pushMessage(
                "Fehler", f"Fehler beim Importieren der Daten: {e}", level=Qgis.Critical
            )

            
    def closeEvent(self, event):
        """Wird aufgerufen, wenn das Fenster geschlossen wird."""
        # Entferne alle bestehenden Highlights
        if self.highlights:
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()
        # Rufe die Originalmethode auf
        super().closeEvent(event)