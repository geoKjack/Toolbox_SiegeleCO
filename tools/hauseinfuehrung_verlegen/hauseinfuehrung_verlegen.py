# -*- coding: utf-8 -*-
"""
HauseinfuehrungsVerlegungsTool
Verlegt Hauseinführungen durch Auswahl von Parent Leerrohr, Verlauf und Endpunkten.
"""

from PyQt5.QtCore import Qt, QRectF, QObject, pyqtSignal
from PyQt5.QtWidgets import QDialog, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsRectItem, QGraphicsPolygonItem, QDialogButtonBox
from qgis.core import QgsProject, Qgis, QgsFeatureRequest, QgsDataSourceUri, QgsWkbTypes, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsFeature
from qgis.gui import QgsHighlight, QgsMapToolEdit, QgsMapToolEmitPoint, QgsMapToolCapture, QgsRubberBand, QgsMapTool
from PyQt5.QtGui import QColor, QBrush, QFont, QPolygonF, QMouseEvent, QPen
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
    instance = None  # Klassenvariable zur Verwaltung der Instanz
    
    def __init__(self, iface, parent=None):
        # Prüfen, ob bereits eine Instanz existiert
        if HauseinfuehrungsVerlegungsTool.instance is not None:
            HauseinfuehrungsVerlegungsTool.instance.raise_()
            HauseinfuehrungsVerlegungsTool.instance.activateWindow()
            return  # Verhindere, dass eine neue Instanz erstellt wird

        super().__init__(parent, Qt.WindowStaysOnTopHint)
        self.iface = iface
        self.ui = Ui_HauseinfuehrungsVerlegungsToolDialogBase()
        self.ui.setupUi(self)

        # Setze die aktuelle Instanz
        HauseinfuehrungsVerlegungsTool.instance = self

        # Verknüpfe den Reset-Button und Cancel-Button
        self.ui.button_box.button(QDialogButtonBox.Reset).clicked.connect(self.formular_initialisieren)
        self.ui.button_box.button(QDialogButtonBox.Cancel).clicked.connect(self.abbrechen_und_schliessen)

        # Verbindung zu Buttons und CheckBox
        self.ui.pushButton_adresse.clicked.connect(self.adresse_waehlen)
        self.ui.checkBox_aufschlieung.stateChanged.connect(self.aufschliessungspunkt_verwalten)

        # Initialisiere wichtige Variablen
        self.startpunkt_id = None
        self.verlauf_ids = []
        self.highlights = []
        self.adresspunkt_highlight = None
        self.gewaehlte_rohrnummer = None  # Speichere die gewählte Rohrnummer

        # Datenbankverbindung vorbereiten
        self.db_uri = None
        self.conn = None
        self.init_database_connection()

        # Variable für das markierte Rechteck
        self.ausgewähltes_rohr_rect = None

        # Buttons mit Aktionen verknüpfen
        self.ui.pushButton_parentLeerrohr.clicked.connect(self.aktion_parent_leerrohr)
        self.ui.pushButton_verlauf_HA.clicked.connect(self.aktion_verlauf)
        self.ui.pushButton_Import.clicked.connect(self.daten_importieren)
        self.ui.pushButton_Datenpruefung.clicked.connect(self.daten_pruefen)



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

        # Zurücksetzen der ausgewählten Rohrnummer und des ausgewählten Rechtecks
        self.gewaehlte_rohrnummer = None
        self.ausgewaehltes_rechteck = None

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
        self.iface.messageBar().pushMessage(
            "Info", f"Rechteck mit Rohrnummer {rohrnummer} wurde angeklickt!", level=Qgis.Info
        )

        # Entferne den roten Rahmen vom zuvor ausgewählten Rechteck
        if hasattr(self, "ausgewaehltes_rechteck") and self.ausgewaehltes_rechteck:
            # Stelle den ursprünglichen schwarzen Rahmen wieder her
            self.ausgewaehltes_rechteck.setPen(QPen(Qt.black))

        # Suche das neue ausgewählte Rechteck und setze einen roten Rahmen
        for item in self.scene.items():
            if isinstance(item, ClickableRect) and item.rohrnummer == rohrnummer:
                item.setPen(QPen(QColor(Qt.red), 3))  # Setze roten Rahmen
                self.ausgewaehltes_rechteck = item  # Aktualisiere das ausgewählte Rechteck
                break

        # Speichere die gewählte Rohrnummer
        self.gewaehlte_rohrnummer = rohrnummer


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

    def aufschliessungspunkt_verwalten(self, state):
        """Aktiviert/Deaktiviert den Adressauswahlbutton basierend auf der Checkbox."""
        if state == Qt.Checked:
            self.ui.pushButton_adresse.setEnabled(False)
            self.ui.label_adresse.setPlainText("Keine Adresse (Aufschließungspunkt)")
        else:
            self.ui.pushButton_adresse.setEnabled(True)
            self.ui.label_adresse.clear()

    def adresse_waehlen(self):
        """Öffnet die Auswahl eines Adresspunkts."""
        self.iface.messageBar().pushMessage("Info", "Bitte wählen Sie einen Adresspunkt auf der Karte aus.", level=Qgis.Info)
        
        layer = QgsProject.instance().mapLayersByName("Adressen")[0]  # Schicht "Adressen" wählen
        self.iface.setActiveLayer(layer)
        self.iface.actionSelect().trigger()

        def on_adresspunkt_selected():
            """Wird ausgeführt, wenn ein Adresspunkt ausgewählt wird."""
            layer = QgsProject.instance().mapLayersByName("Adressen")[0]  # Hole den Adressen-Layer
            selected_features = layer.selectedFeatures()

            try:
                # Hole die relevanten Werte aus dem Feature
                adresspunkt_id = selected_features[0]["ADRKEY"]  # Nutze ADRKEY
                sname = selected_features[0]["SNAME"]
                hnr = selected_features[0]["HNR"]

                # Zeige die Adresse im Label an
                self.ui.label_adresse.setPlainText(f"{sname}, {hnr}")

                # Highlight den Adresspunkt
                geom = selected_features[0].geometry()
                if hasattr(self, "adresspunkt_highlight") and self.adresspunkt_highlight:
                    self.adresspunkt_highlight.hide()
                self.adresspunkt_highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                self.adresspunkt_highlight.setColor(QColor(Qt.red))
                self.adresspunkt_highlight.setWidth(3)
                self.adresspunkt_highlight.show()

                # Speichere den gewählten Adresspunkt für spätere Verwendung
                self.gewaehlte_adresse = adresspunkt_id

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
        """Führt alle notwendigen Prüfungen durch und gibt eine Liste von Fehlermeldungen zurück."""
        fehler = []

        # Prüfung: Startpunkt des Leerrohrs ausgewählt
        if not hasattr(self, "startpunkt_id") or self.startpunkt_id is None:
            fehler.append("Kein Parent Leerrohr ausgewählt.")

        # Prüfung: Verlauf erfasst
        if not hasattr(self, "erfasste_geom") or self.erfasste_geom is None:
            fehler.append("Kein Verlauf der Hauseinführung erfasst.")

        # Prüfung: Rohrnummer ausgewählt
        if not hasattr(self, "gewaehlte_rohrnummer") or self.gewaehlte_rohrnummer is None:
            fehler.append("Keine Rohrnummer ausgewählt.")

        # Prüfung: Adresspunkt nur erforderlich, wenn Checkbox nicht aktiviert ist
        if not self.ui.checkBox_aufschlieung.isChecked():
            if not hasattr(self, "gewaehlte_adresse") or self.gewaehlte_adresse is None:
                fehler.append("Kein Adresspunkt ausgewählt, obwohl Aufschließungspunkt nicht gesetzt ist.")

        return fehler

    def daten_pruefen(self):
        """Führt Prüfungen durch und zeigt Ergebnisse im Label an."""
        fehler = self.pruefungen_durchfuehren()

        if fehler:
            # Fehlermeldungen im Label anzeigen
            self.ui.label_Pruefung.setText("\n".join(fehler))
            self.ui.label_Pruefung.setStyleSheet(
                "background-color: rgba(255, 0, 0, 0.2); color: black;"  # Leichtes Rot im Hintergrund, schwarze Schrift
            )
            self.ui.pushButton_Import.setEnabled(False)  # Import deaktivieren
        else:
            # Erfolgsmeldung anzeigen
            self.ui.label_Pruefung.setText("Alle Prüfungen erfolgreich bestanden.")
            self.ui.label_Pruefung.setStyleSheet(
                "background-color: rgba(0, 255, 0, 0.2); color: black;"  # Leichtes Grün im Hintergrund, schwarze Schrift
            )
            self.ui.pushButton_Import.setEnabled(True)  # Import aktivieren


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

            # Prüfen, ob eine Rohrnummer ausgewählt wurde
            rohrnummer = getattr(self, "gewaehlte_rohrnummer", None)
            if rohrnummer is None:
                self.iface.messageBar().pushMessage(
                    "Fehler", "Keine Rohrnummer ausgewählt. Bitte wählen Sie eine Rohrnummer aus.", level=Qgis.Critical
                )
                return

            # Farbwert aus der LUT_Farbe_Rohr-Tabelle abrufen
            farbschema = self.ui.label_farbschema.toPlainText().strip()
            subtyp_char = self.ui.label_subtyp.toPlainText().replace("SUBTYP:", "").strip() # Verwende SUBTYP_CHAR direkt aus der GUI

            if not subtyp_char:
                self.iface.messageBar().pushMessage(
                    "Fehler", "SUBTYP_CHAR fehlt oder ist ungültig. Bitte überprüfen Sie die Eingabe.", level=Qgis.Critical
                )
                return

            cur.execute(
                """
                SELECT "FARBE" 
                FROM "lwl"."LUT_Farbe_Rohr" 
                WHERE "ROHRNUMMER" = %s AND "FARBSCHEMA" = %s AND "SUBTYP_char" = %s
                """,
                (rohrnummer, farbschema, subtyp_char)
            )
            result = cur.fetchone()
            farbe = result[0] if result else None

            if farbe is None:
                self.iface.messageBar().pushMessage(
                    "Warnung", f"Kein Farbwert für Rohrnummer {rohrnummer}, Farbschema {farbschema} und SUBTYP_CHAR {subtyp_char} gefunden.", level=Qgis.Warning
                )

            # Wert für ADRKEY prüfen
            adrkey = getattr(self, "gewaehlte_adresse", None)

            # Setze die ADRKEY-Variable in der aktuellen Session
            if adrkey is not None:
                cur.execute("SET LOCAL myapp.current_adrkey = %s;", (adrkey,))

            # Datenbankabfrage für den Import
            query = """
                INSERT INTO "lwl"."LWL_Hauseinfuehrung" (geom, "ID_LEERROHR", "KOMMENTAR", "ROHRNUMMER", "FARBE")
                VALUES (ST_SetSRID(ST_GeomFromText(%s), 31254), %s, %s, %s, %s)
            """
            cur.execute(query, (geom_wkt, startpunkt_id, kommentar, rohrnummer, farbe))
            self.conn.commit()

            # Erfolgsmeldung
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)

            # Formular zurücksetzen
            self.formular_initialisieren()

            # Nach dem Import den Import-Button deaktivieren
            self.ui.pushButton_Import.setEnabled(False)
            self.ui.label_Pruefung.setText("")
            self.ui.label_Pruefung.setStyleSheet("")  # Hintergrundfarbe entfernen

        except Exception as e:
            self.conn.rollback()
            self.iface.messageBar().pushMessage(
                "Fehler", f"Fehler beim Importieren der Daten: {e}", level=Qgis.Critical
            )

    def formular_initialisieren(self):
        """Setzt das Formular auf den Ausgangszustand zurück und entfernt Highlights."""
        self.startpunkt_id = None
        self.erfasste_geom = None
        self.gewaehlte_rohrnummer = None
        self.ui.label_parentLeerrohr.setText("")
        self.ui.label_farbschema.setText("")
        self.ui.label_subtyp.setText("")
        self.ui.label_Kommentar.setText("")
        
        self.ui.label_adresse.clear()  # Adresse zurücksetzen
        if self.adresspunkt_highlight:
            self.adresspunkt_highlight.hide()
            self.adresspunkt_highlight = None
        
        # Entferne grafische Elemente
        if hasattr(self, "scene"):
            self.scene.clear()  # Entferne alle grafischen Elemente
        if hasattr(self, "rubber_band") and self.rubber_band:
            self.rubber_band.reset()
        
        # Entferne Highlights
        if self.highlights:
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()

        # Zeige eine Info-Meldung an
        self.iface.messageBar().pushMessage("Info", "Formular und Highlights wurden zurückgesetzt.", level=Qgis.Info)

    def abbrechen_und_schliessen(self):
        """Ruft die Formularinitialisierung auf und schließt das Fenster."""
        self.formular_initialisieren()  # Formular zurücksetzen
        self.close()  # Fenster schließen
      
    def closeEvent(self, event):
        """Wird aufgerufen, wenn das Fenster geschlossen wird."""
        # Entferne alle bestehenden Highlights
        if self.highlights:
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()

        # Entferne den Adresspunkt-Highlight, falls vorhanden
        if hasattr(self, "adresspunkt_highlight") and self.adresspunkt_highlight:
            self.adresspunkt_highlight.hide()
            self.adresspunkt_highlight = None

        # Setze die Klassenvariable zurück, um Mehrfachöffnungen zu verhindern
        HauseinfuehrungsVerlegungsTool.instance = None

        # Rufe die Originalmethode auf
        super().closeEvent(event)
