# -*- coding: utf-8 -*-
"""
HauseinfuehrungsVerlegungsTool
Verlegt Hauseinführungen durch Auswahl von Parent Leerrohr, Verlauf und Endpunkten.
"""

from PyQt5.QtCore import Qt, QRectF, QObject, pyqtSignal
from PyQt5.QtWidgets import QDialog, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsRectItem, QGraphicsPolygonItem, QDialogButtonBox, QLineEdit, QTextEdit
from qgis.core import QgsProject, Qgis, QgsFeatureRequest, QgsDataSourceUri, QgsWkbTypes, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsFeature, QgsCoordinateReferenceSystem, QgsMessageLog
from qgis.gui import QgsHighlight, QgsMapToolEdit, QgsMapToolEmitPoint, QgsMapToolCapture, QgsRubberBand, QgsMapTool
from PyQt5.QtGui import QColor, QBrush, QFont, QPolygonF, QMouseEvent, QPen
import psycopg2
from .hauseinfuehrung_verlegen_dialog import Ui_HauseinfuehrungsVerlegungsToolDialogBase
from PyQt5.QtCore import Qt, QRectF, QObject, pyqtSignal, QPointF

class ClickableRect(QGraphicsRectItem):
    def __init__(self, x, y, width, height, rohrnummer, farb_id, callback, parent=None):
        super().__init__(parent)
        self.setRect(x, y, width, height)
        self.rohrnummer = rohrnummer  # Speichere die zugehörige Rohrnummer
        self.farb_id = farb_id        # Speichere die zugehörige FARBE-ID
        self.callback = callback      # Übergib die Callback-Funktion

    def mousePressEvent(self, event):
        """Wird ausgelöst, wenn auf das Rechteck geklickt wird."""
        if self.callback:
            self.callback(self.rohrnummer, self.farb_id)  # Rufe die Callback-Funktion mit Rohrnummer und FARBE-ID auf
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

        # Initialisiere die Szene für die grafische Ansicht
        self.scene = QGraphicsScene()
        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)

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
        self.direktmodus = False
        self.gewaehlte_adresse = None  # Gewählte Adresse ist immer mit NON vorhanden

        # Datenbankverbindung vorbereiten
        self.db_uri = None
        self.conn = None
        self.init_database_connection()

        # Variable für das markierte Rechteck
        self.ausgewähltes_rohr_rect = None

        # ComboBox Gefördert mit JA und NEIN befüllen
        self.ui.comboBox_Gefoerdert.addItem("")  # Leerer Eintrag als erste Option
        self.ui.comboBox_Gefoerdert.addItems(["JA", "NEIN"])

        # Buttons mit Aktionen verknüpfen
        self.ui.pushButton_parentLeerrohr.clicked.connect(self.aktion_parent_leerrohr)
        self.ui.pushButton_verlauf_HA.clicked.connect(self.aktion_verlauf)
        self.ui.pushButton_Import.clicked.connect(self.daten_importieren)
        self.ui.pushButton_Datenpruefung.clicked.connect(self.daten_pruefen)
        self.ui.checkBox_direkt.stateChanged.connect(self.handle_checkbox_direkt)
        self.ui.pushButton_verteiler.clicked.connect(self.verteilerkasten_waehlen)

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

        QgsMessageLog.logMessage("Suche nach PostgreSQL-Layern...", "DB-Debug", level=Qgis.Info)

        for layer in layers:
            layer_name = layer.name()
            provider_name = layer.dataProvider().name()
            uri = layer.dataProvider().dataSourceUri()

            QgsMessageLog.logMessage(f"Gefundener Layer: {layer_name} - Provider: {provider_name}", "DB-Debug", level=Qgis.Info)
            QgsMessageLog.logMessage(f"Datasource URI: {uri}", "DB-Debug", level=Qgis.Info)

            if provider_name == "postgres":
                QgsMessageLog.logMessage(f"PostgreSQL-Layer gefunden: {layer_name}", "DB-Debug", level=Qgis.Info)

            if provider_name == "postgres" and ("LWL_Leerrohr" in layer_name or "LWL.LWL_Leerrohr" in uri):
                QgsMessageLog.logMessage(f"Datenbankverbindung gefunden: {uri}", "DB-Debug", level=Qgis.Info)
                return uri

        QgsMessageLog.logMessage("Keine PostgreSQL-Verbindung gefunden!", "DB-Debug", level=Qgis.Critical)
        raise Exception("Keine aktive PostgreSQL-Datenbankverbindung gefunden.")


    def handle_checkbox_direkt(self, state):
        """Aktiviert/Deaktiviert den Button zur Auswahl des Parent-Leerrohrs."""
        self.direktmodus = (state == Qt.Checked)
        self.ui.pushButton_parentLeerrohr.setEnabled(not self.direktmodus)
        
    def verteilerkasten_waehlen(self):
        """Ermöglicht die Auswahl eines Verteilerkastens aus der Karte mit visuellem Auswahlwerkzeug."""
        layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
        if not layer:
            self.iface.messageBar().pushMessage("Fehler", "Layer 'LWL_Knoten' nicht gefunden.", level=Qgis.Critical)
            return

        # Aktiviere den Layer
        self.iface.setActiveLayer(layer)
        self.iface.messageBar().pushMessage("Info", "Bitte klicken Sie auf einen Verteilerkasten, um ihn auszuwählen.", level=Qgis.Info)

        # Auswahlwerkzeug vorbereiten
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())

        def on_vertex_selected(point):
            """Callback-Funktion bei Auswahl eines Punkts."""
            try:
                # Erstelle einen kleinen Puffer um den Punkt
                pixel_radius = 5  # Suchradius in Pixeln
                map_units_per_pixel = self.iface.mapCanvas().mapUnitsPerPixel()
                search_radius = pixel_radius * map_units_per_pixel  # Umrechnung in Kartenkoordinaten
                point_geom = QgsGeometry.fromPointXY(point)
                search_rect = point_geom.buffer(search_radius, 1).boundingBox()

                # Suche nach Features im Pufferbereich
                request = QgsFeatureRequest().setFilterRect(search_rect)
                features = [feature for feature in layer.getFeatures(request)]

                for feature in features:
                    if feature["TYP"] == "Verteilerkasten":
                        verteiler_id = feature["id"]

                        # Aktualisiere UI und speichere die Auswahl
                        self.gewaehlter_verteiler = verteiler_id
                        self.ui.label_verteiler.setText(f"Verteilerkasten ID: {verteiler_id}")

                        # Felder und Ansicht zurücksetzen
                        self.formular_initialisieren_fuer_verteilerwechsel()

                        # Heben Sie das Feature hervor
                        geom = feature.geometry()
                        self.highlight_geometry(geom, layer)
                        return

                self.iface.messageBar().pushMessage("Fehler", "Kein Verteilerkasten an dieser Stelle gefunden.", level=Qgis.Info)

            except Exception as e:
                self.iface.messageBar().pushMessage("Fehler", f"Fehler bei der Verteilerkasten-Auswahl: {e}", level=Qgis.Info)

        self.map_tool.canvasClicked.connect(on_vertex_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def formular_initialisieren_fuer_verteilerwechsel(self):
        """Setzt spezifische Felder und die grafische Ansicht zurück."""
        # Felder im UI zurücksetzen
        self.ui.label_parentLeerrohr.clear()
        self.ui.label_subtyp.clear()
        self.ui.label_farbschema.clear()
        self.ui.label_firma.clear()

        # Szene initialisieren, falls sie noch nicht existiert
        if not hasattr(self, "scene") or self.scene is None:
            self.scene = QGraphicsScene()

        # Lösche die grafische Ansicht und das ausgewählte Rechteck
        self.scene.clear()
        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)
        self.ausgewaehltes_rechteck = None  # Zurücksetzen des ausgewählten Rechtecks

        # Nachricht für den Benutzer
        self.iface.messageBar().pushMessage("Info", "Bitte wählen Sie das Leerrohr erneut, um die Daten zu aktualisieren.", level=Qgis.Info)



    def highlight_geometry(self, geom, layer):
        """Hebt eine Geometrie hervor."""
        if hasattr(self, "verteiler_highlight") and self.verteiler_highlight:
            self.verteiler_highlight.hide()

        self.verteiler_highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
        self.verteiler_highlight.setColor(QColor(Qt.red))
        self.verteiler_highlight.setWidth(3)
        self.verteiler_highlight.show()

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
                subtyp_id = selected_features[0]["SUBTYP"]
                farbschema = selected_features[0]["FARBSCHEMA"]
                firma = selected_features[0]["FIRMA_HERSTELLER"]  # Hersteller aus dem Feature ablesen

                # Speichere die Informationen
                self.startpunkt_id = leerrohr_id
                self.firma = firma

                # Setze die Labels
                self.ui.label_parentLeerrohr.setText(str(leerrohr_id))
                self.ui.label_farbschema.setText(str(farbschema))
                self.ui.label_subtyp.setText(f"SUBTYP: {subtyp_id}")
                self.ui.label_firma.setText(f"Hersteller: {firma}")  # Hersteller im UI anzeigen

                # Zeichne die Rohre basierend auf den neuen Kriterien
                self.zeichne_rohre(subtyp_id, farbschema, firma)

                # Highlight das ausgewählte Feature
                for feature in selected_features:
                    geom = feature.geometry()
                    highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                    highlight.setColor(Qt.red)
                    highlight.setWidth(3)
                    highlight.show()
                    self.highlights.append(highlight)
            else:
                self.startpunkt_id = None

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(onParentLeerrohrSelected)
        
    def zeichne_rohre(self, subtyp_id, farbschema, firma):
        """Zeichnet klickbare Rechtecke für Rohre basierend auf Subtyp und Farbschema.
           Berücksichtigt die Logik für spezifische Leerrohre und ihre Richtung.
        """
        self.scene = QGraphicsScene()
        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)

        # Zurücksetzen der ausgewählten Rohrnummer und des ausgewählten Rechtecks
        self.gewaehlte_rohrnummer = None
        self.ausgewaehltes_rechteck = None

        # Daten laden
        rohre = self.lade_farben_und_rohrnummern(subtyp_id, farbschema, firma)

        if not rohre:
            self.iface.messageBar().pushMessage("Info", "Keine Rohre zum Zeichnen gefunden.", level=Qgis.Warning)
            return

        # Abfrage: Welche Rohrnummern sind bereits belegt?
        try:
            cur = self.conn.cursor()

            # Prüfen, ob das Leerrohr an beiden Enden an Verteilern angeschlossen ist
            query = """
                SELECT 
                    "VONKNOTEN", "NACHKNOTEN", "VKG_LR"
                FROM 
                    "lwl"."LWL_Leerrohr"
                WHERE 
                    "id" = %s
            """
            cur.execute(query, (self.startpunkt_id,))
            result = cur.fetchone()

            if not result:
                self.iface.messageBar().pushMessage("Fehler", "Das gewählte Leerrohr wurde nicht gefunden.", level=Qgis.Critical)
                return

            vonknoten, nachknoten, vkg_lr = result

            # Prüfen, ob beide Enden an einem Verteiler angeschlossen sind
            query_verteiler = """
                SELECT "id"
                FROM "lwl"."LWL_Knoten"
                WHERE "id" IN (%s, %s) AND "TYP" = 'Verteilerkasten'
            """
            cur.execute(query_verteiler, (vonknoten, nachknoten))
            verteiler = cur.fetchall()
            verteiler_ids = [v[0] for v in verteiler]

            # Abfrage aller belegten Rohrnummern für das gewählte Leerrohr (und relevante Verteiler)
            query_belegte_rohre = """
                SELECT DISTINCT ha."ROHRNUMMER"
                FROM "lwl"."LWL_Hauseinfuehrung" ha
                WHERE ha."ID_LEERROHR" = %s
                  AND ha."VKG_LR" = %s
            """
            cur.execute(query_belegte_rohre, (self.startpunkt_id, self.gewaehlter_verteiler))
            belegte_rohre = [row[0] for row in cur.fetchall()]

            # Debugging: Anzeige der belegten Rohrnummern
            self.iface.messageBar().pushMessage(
                "Info", f"Belegte Rohrnummern für Verteiler {self.gewaehlter_verteiler}: {belegte_rohre}", level=Qgis.Info
            )

        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Fehler beim Abrufen belegter Rohrnummern: {e}", level=Qgis.Critical)
            return


        # Zeichne die Rohre basierend auf den verfügbaren Daten
        x_offset = 10
        y_offset = 10
        rect_width = 30
        rect_height = 30
        spacing = 10
        font_size = 10

        for i, (rohrnummer, farbcode, farb_id) in enumerate(rohre):
            farbteile = farbcode.split("-")
            x_pos = x_offset + i * (rect_width + spacing)

            # Prüfe, ob die Rohrnummer bereits belegt ist
            ist_belegt = rohrnummer in belegte_rohre

            # Klickbares Rechteck erstellen
            if not ist_belegt:
                clickable_rect = ClickableRect(x_pos, y_offset, rect_width, rect_height, rohrnummer, farb_id, self.handle_rect_click)
            else:
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
                    QPointF(x_pos, y_offset),
                    QPointF(x_pos + rect_width, y_offset),
                    QPointF(x_pos, y_offset + rect_height)
                ]))
                polygon1.setBrush(QBrush(color1))
                self.scene.addItem(polygon1)

                polygon2 = QGraphicsPolygonItem()
                polygon2.setPolygon(QPolygonF([
                    QPointF(x_pos + rect_width, y_offset),
                    QPointF(x_pos + rect_width, y_offset + rect_height),
                    QPointF(x_pos, y_offset + rect_height)
                ]))
                polygon2.setBrush(QBrush(color2))
                self.scene.addItem(polygon2)

            # Halo-Effekt für Text (Rohrnummer)
            halo_text = QGraphicsSimpleTextItem(str(rohrnummer))
            halo_text.setBrush(QBrush(Qt.white))
            halo_text.setFont(QFont("Arial", font_size, QFont.Bold))
            halo_text.setZValue(1)
            halo_text.setPos(x_pos + rect_width / 4 - 1, y_offset + rect_height / 4 - 1)
            self.scene.addItem(halo_text)

            # Rohrnummer als Text hinzufügen
            text_item = QGraphicsSimpleTextItem(str(rohrnummer))
            text_item.setBrush(QBrush(Qt.black if not ist_belegt else Qt.gray))
            text_item.setFont(QFont("Arial", font_size, QFont.Bold))
            text_item.setZValue(2)
            text_item.setPos(x_pos + rect_width / 4, y_offset + rect_height / 4)
            self.scene.addItem(text_item)

        self.scene.update()

    def handle_rect_click(self, rohrnummer, farb_id):
        """Verarbeitet Klicks auf ein Rechteck."""
        self.iface.messageBar().pushMessage(
            "Info", f"Rechteck mit Rohrnummer {rohrnummer} wurde angeklickt!", level=Qgis.Info
        )

        # Entferne den roten Rahmen vom zuvor ausgewählten Rechteck
        if hasattr(self, "ausgewaehltes_rechteck") and self.ausgewaehltes_rechteck:
            try:
                # Stelle den ursprünglichen schwarzen Rahmen wieder her
                self.ausgewaehltes_rechteck.setPen(QPen(Qt.black))
            except RuntimeError:
                # Falls das Objekt gelöscht wurde, ignorieren
                self.ausgewaehltes_rechteck = None

        # Suche das neue ausgewählte Rechteck und setze einen roten Rahmen
        for item in self.scene.items():
            if isinstance(item, ClickableRect) and item.rohrnummer == rohrnummer:
                item.setPen(QPen(QColor(Qt.red), 3))  # Setze roten Rahmen
                self.ausgewaehltes_rechteck = item  # Aktualisiere das ausgewählte Rechteck
                break

        # Speichere die gewählte Rohrnummer und die zugehörige Farb-ID
        self.gewaehlte_rohrnummer = rohrnummer
        self.gewaehlte_farb_id = farb_id  # Speichere die ID der FARBE

    def lade_farben_und_rohrnummern(self, subtyp_id, farbschema, firma):
        """Lädt Farben und Rohrnummern aus der Tabelle LUT_Rohr_Beschreibung basierend auf Subtyp, Farbschema und Hersteller."""
        try:
            cur = self.conn.cursor()
            query = """
                SELECT "ROHRNUMMER", "FARBCODE", "id"
                FROM "lwl"."LUT_Rohr_Beschreibung"
                WHERE "ID_SUBTYP" = %s AND "FARBSCHEMA" = %s AND "FIRMA" = %s
                ORDER BY "ROHRNUMMER" ASC
            """
            cur.execute(query, (subtyp_id, farbschema, firma))
            result = cur.fetchall()
            if not result:
                self.iface.messageBar().pushMessage("Info", "Keine Rohre gefunden.", level=Qgis.Warning)
            return result
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Farben konnten nicht geladen werden: {e}", level=Qgis.Critical)
            return []

    def aktion_verlauf(self):
        """Erfasst die Liniengeometrie der Hauseinführung mit Snap auf Leerrohr oder Verteilerkasten."""
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

        # Entscheide, welche Geometrie für das Snapping verwendet wird
        snap_geometry = None
        layer_crs = None
        if self.ui.checkBox_direkt.isChecked():
            # Direktmodus: Snap an Verteilerkasten
            if not self.gewaehlter_verteiler:
                self.iface.messageBar().pushMessage(
                    "Fehler", "Kein Verteilerkasten ausgewählt. Bitte wählen Sie einen Verteilerkasten aus.", level=Qgis.Critical
                )
                return

            layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
            verteiler_feature = next((f for f in layer.getFeatures() if f["id"] == self.gewaehlter_verteiler), None)
            if not verteiler_feature:
                self.iface.messageBar().pushMessage(
                    "Fehler", "Der ausgewählte Verteilerkasten konnte nicht gefunden werden.", level=Qgis.Critical
                )
                return
            snap_geometry = verteiler_feature.geometry()
            layer_crs = layer.crs()
            self.iface.messageBar().pushMessage(
                "Info", f"Verteilerkasten-Geometrie geladen: {snap_geometry.asWkt()}", level=Qgis.Info
            )
        else:
            # Standardmodus: Snap an Leerrohr
            layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
            leerrohr_feature = next((f for f in layer.getFeatures() if f["id"] == self.startpunkt_id), None)
            if not leerrohr_feature:
                self.iface.messageBar().pushMessage(
                    "Fehler", "Das ausgewählte Leerrohr konnte nicht gefunden werden.", level=Qgis.Critical
                )
                return
            snap_geometry = leerrohr_feature.geometry()
            layer_crs = layer.crs()
            self.iface.messageBar().pushMessage(
                "Info", f"Leerrohr-Geometrie geladen: {snap_geometry.asWkt()}", level=Qgis.Info
            )

        if not snap_geometry:
            self.iface.messageBar().pushMessage(
                "Fehler", "Snap-Geometrie konnte nicht geladen werden.", level=Qgis.Critical
            )
            return

        # Prüfe den Geometrietyp (Linie oder Punkt)
        geometry_type = snap_geometry.wkbType()

        # Prüfe, ob die Geometrie gültig ist und transformiere sie bei Bedarf
        project_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if layer_crs != project_crs:
            self.iface.messageBar().pushMessage(
                "Info", f"Transformiere Geometrie von {layer_crs.authid()} nach {project_crs.authid()}", level=Qgis.Info
            )
            transform = QgsCoordinateTransform(layer_crs, project_crs, QgsProject.instance())
            snap_geometry = snap_geometry.transform(transform)

        # Callback für Punkt-Erfassung
        def point_captured(point):
            """Wird aufgerufen, wenn ein Punkt erfasst wird."""
            try:
                if len(self.erfasste_punkte) == 0 and snap_geometry:
                    # Unterscheide nach Geometrietyp
                    if geometry_type == QgsWkbTypes.Point:
                        snapped_point = snap_geometry.asPoint()  # Für Punkte: exakte Koordinate
                    else:
                        snapped_point = snap_geometry.closestSegmentWithContext(point)[1]  # Für Linien: nächster Punkt
                    self.iface.messageBar().pushMessage(
                        "Info", f"Erster Punkt gesnapped: {snapped_point}.", level=Qgis.Success
                    )
                    self.erfasste_punkte.append(QgsPointXY(snapped_point))
                else:
                    # Füge nachfolgende Punkte ohne Snapping hinzu
                    self.erfasste_punkte.append(QgsPointXY(point))
                # Aktualisiere die Rubberband-Geometrie
                self.rubber_band.setToGeometry(QgsGeometry.fromPolylineXY(self.erfasste_punkte), None)
            except Exception as e:
                self.iface.messageBar().pushMessage(
                    "Fehler", f"Fehler beim Snapping: {e}", level=Qgis.Critical
                )

        # Callback für Abschluss der Geometrie
        def finalize_geometry(points):
            """Wird aufgerufen, wenn die Linie abgeschlossen wird (Rechtsklick)."""
            if len(points) < 2:
                self.iface.messageBar().pushMessage("Fehler", "Mindestens zwei Punkte erforderlich.", level=Qgis.Critical)
                return
            self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
            self.iface.messageBar().pushMessage("Info", "Linie erfolgreich erfasst.", level=Qgis.Success)
            self.iface.mapCanvas().unsetMapTool(self.map_tool)

        # Setze das benutzerdefinierte Werkzeug
        self.map_tool = CustomLineCaptureTool(self.iface.mapCanvas(), point_captured, finalize_geometry)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def aufschliessungspunkt_verwalten(self, state):
        """Aktiviert/Deaktiviert den Adressauswahlbutton basierend auf der Checkbox."""
        if state == Qt.Checked:
            self.ui.pushButton_adresse.setEnabled(False)
            self.ui.label_adresse.setPlainText("Keine Adresse (Aufschließungspunkt)")
            self.gewaehlte_adresse = None  # <-- Hier die Adresse zurücksetzen!
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

            # Prüfen, ob eine Auswahl getroffen wurde
            if not selected_features:
                self.iface.messageBar().pushMessage("Info", "Keine Adresse ausgewählt. Bitte wählen Sie einen Punkt aus.", level=Qgis.Info)
                return

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

        gefoerdert_text = self.ui.comboBox_Gefoerdert.currentText()

        if gefoerdert_text == "":
            fehler.append("Bitte wählen Sie einen Wert für 'Gefördert' aus.")

        if self.ui.checkBox_direkt.isChecked():
            # Prüfungen für den Direktmodus
            if not hasattr(self, "gewaehlter_verteiler") or self.gewaehlter_verteiler is None:
                fehler.append("Kein Verteilerkasten ausgewählt.")

            # Keine Prüfung der Rohrnummer im Direktmodus
        else:
            # Prüfungen für den Nicht-Direktmodus
            if not hasattr(self, "startpunkt_id") or self.startpunkt_id is None:
                fehler.append("Kein Parent Leerrohr ausgewählt.")
            if not hasattr(self, "gewaehlte_rohrnummer") or self.gewaehlte_rohrnummer is None:
                fehler.append("Keine Rohrnummer ausgewählt.")

        # Gemeinsame Prüfungen für beide Modi
        if not hasattr(self, "erfasste_geom") or self.erfasste_geom is None:
            fehler.append("Kein Verlauf der Hauseinführung erfasst.")
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

        try:
            cur = self.conn.cursor()

            # Hol die Attribute aus den UI-Feldern
            kommentar = self.ui.label_Kommentar.text()
            beschreibung = self.ui.label_Kommentar_2.text()
            geom_wkt = self.erfasste_geom.asWkt()
            rohrnummer = self.gewaehlte_rohrnummer
            vkg_lr = self.gewaehlter_verteiler
            farbe = None
            gefoerdert_text = self.ui.comboBox_Gefoerdert.currentText()  # Wert der ComboBox abgreifen
            gefoerdert = True if gefoerdert_text == "JA" else False

            # Adress-ID setzen (Platzhalter für Aufschließungspunkt oder tatsächlicher ADRKEY)
            if self.ui.checkBox_aufschlieung.isChecked():
                adresspunkt_id = -1  # Platzhalter für Aufschließungspunkt
            else:
                adresspunkt_id = self.gewaehlte_adresse  # Normaler ADRKEY

            # Prüfen, ob der Direktmodus aktiv ist
            if self.direktmodus:
                # Setze die Werte für direkte Hauseinführungen
                rohrnummer = 0  # Direktmodus: Rohrnummer ist immer 0
                farbe = 'direkt'  # Direktmodus: Farbe ist immer 'direkt'

                # Geometrie anpassen, falls Direktmodus
                if not hasattr(self, "gewaehlter_verteiler") or self.gewaehlter_verteiler is None:
                    self.iface.messageBar().pushMessage(
                        "Fehler", "Kein Verteilerkasten ausgewählt.", level=Qgis.Critical
                    )
                    return

                points = self.erfasste_geom.asPolyline()
                if len(points) > 1:
                    start_point_geom = QgsProject.instance().mapLayersByName("LWL_Knoten")[0].getFeature(vkg_lr).geometry()
                    points[0] = start_point_geom.asPoint()  # Setze den Startpunkt auf den Knoten
                    self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
                    geom_wkt = self.erfasste_geom.asWkt()

            else:
                # Logik für normale Hauseinführungen (über Leerrohre)
                if hasattr(self, "gewaehlte_farb_id") and self.gewaehlte_farb_id is not None:
                    cur.execute(
                        """
                        SELECT "FARBE"
                        FROM "lwl"."LUT_Rohr_Beschreibung"
                        WHERE "id" = %s
                        """,
                        (self.gewaehlte_farb_id,)
                    )
                    result = cur.fetchone()
                    farbe = result[0] if result else None
                    if not farbe:
                        self.iface.messageBar().pushMessage(
                            "Warnung", "Keine FARBE für die gewählte Farb-ID gefunden.", level=Qgis.Warning
                        )

                layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
                selected_features = [f for f in layer.getFeatures() if f["id"] == self.startpunkt_id]
                if not selected_features:
                    self.iface.messageBar().pushMessage(
                        "Fehler", "Das ausgewählte Leerrohr konnte nicht gefunden werden.", level=Qgis.Critical
                    )
                    return

                leerrohr_geom = selected_features[0].geometry()

                # Snap den Startpunkt erneut auf das Leerrohr
                points = self.erfasste_geom.asPolyline()
                if len(points) > 0:
                    snapped_point = leerrohr_geom.closestSegmentWithContext(points[0])[1]
                    points[0] = QgsPointXY(snapped_point)
                    self.erfasste_geom = QgsGeometry.fromPolylineXY(points)
                    geom_wkt = self.erfasste_geom.asWkt()

            # Datenbankabfrage für den Import
            query = """
                INSERT INTO "lwl"."LWL_Hauseinfuehrung" (geom, "ID_LEERROHR", "KOMMENTAR", "BESCHREIBUNG", "ROHRNUMMER", "FARBE", "VKG_LR", "HA_ADRKEY", "GEFOERDERT")
                VALUES (ST_SetSRID(ST_GeomFromText(%s), 31254), %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cur.execute(query, (geom_wkt, self.startpunkt_id, kommentar, beschreibung, rohrnummer, farbe, vkg_lr, adresspunkt_id, gefoerdert))
            self.conn.commit()

            # Erfolgsmeldung
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)

            # Karte automatisch refreshen, damit die Daten sofort sichtbar sind
            layer = QgsProject.instance().mapLayersByName("LWL_Hauseinfuehrung")[0]
            if layer:
                layer.triggerRepaint()

            # Formular zurücksetzen
            self.formular_initialisieren()

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
        self.ui.label_verteiler.setText("")    
        self.ui.label_firma.setText("")    
        self.ui.label_farbschema.setText("")
        self.ui.label_subtyp.setText("")
        self.ui.label_Kommentar.setText("")
        self.ui.comboBox_Gefoerdert.setCurrentIndex(-1)
        
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

        # Entferne Verteiler-Highlight (Einzelobjekt)
        if hasattr(self, "verteiler_highlight") and self.verteiler_highlight:
            self.verteiler_highlight.hide()
            self.verteiler_highlight = None

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