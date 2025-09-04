# -*- coding: utf-8 -*-
"""
HauseinfuehrungsVerlegungsTool
Verlegt Hauseinführungen durch Auswahl von Parent Leerrohr, Verlauf und Endpunkten.
"""

from PyQt5.QtCore import Qt, QRectF, QObject, pyqtSignal, QPointF, QSettings, QDateTime
from PyQt5.QtWidgets import QDialog, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsRectItem, QGraphicsPolygonItem, QDialogButtonBox, QLineEdit, QTextEdit, QGraphicsLineItem
from qgis.core import QgsProject, Qgis, QgsFeatureRequest, QgsDataSourceUri, QgsWkbTypes, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsFeature, QgsCoordinateReferenceSystem, QgsMessageLog
from qgis.gui import QgsHighlight, QgsMapToolEmitPoint, QgsRubberBand, QgsMapTool
from PyQt5.QtGui import QColor, QBrush, QFont, QPolygonF, QMouseEvent, QPen
import psycopg2
import base64
import json
from .hauseinfuehrung_verlegen_dialog import Ui_HauseinfuehrungsVerlegungsToolDialogBase

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
        """Zeichnet Quadrate für Rohre, diagonal geteilt mit Farben basierend auf Subtyp."""
        print("DEBUG: Starte zeichne_rohre")
        
        # Lösche die bestehende Szene
        if self.ui.graphicsView_Farben_Rohre.scene():
            self.ui.graphicsView_Farben_Rohre.scene().clear()
        else:
            self.ui.graphicsView_Farben_Rohre.setScene(QGraphicsScene())

        self.scene = self.ui.graphicsView_Farben_Rohre.scene()
        self.scene.setSceneRect(0, 0, 491, 200)
        self.gewaehlte_rohrnummer = None
        self.ausgewaehltes_rechteck = None

        if subtyp_id is None:  # Direktmodus
            font = QFont()
            font.setBold(True)
            font.setPointSize(12)
            text = self.scene.addText("Direkte Hauseinführung")
            text.setPos(10, 10)
            text.setDefaultTextColor(Qt.red)
            text.setFont(font)
            return

        rohre, subtyp_char, typ = self.lade_farben_und_rohrnummern(subtyp_id)
        
        if not rohre or subtyp_char is None:
            self.iface.messageBar().pushMessage(
                "Fehler", f"Keine Rohre oder SUBTYP_char für Subtyp {subtyp_id} gefunden.", level=Qgis.Critical
            )
            return

        try:
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()

            if is_abzweigung:
                id_ = self.abzweigung_id
                table_name = '"lwl"."LWL_Leerrohr_Abzweigung"'
            else:
                id_ = self.startpunkt_id
                table_name = '"lwl"."LWL_Leerrohr"'

            query = f"""
                SELECT "VONKNOTEN", "NACHKNOTEN", "VKG_LR"
                FROM {table_name}
                WHERE "id" = %s
            """
            cur.execute(query, (id_,))
            result = cur.fetchone()

            if not result:
                self.iface.messageBar().pushMessage(
                    "Fehler", f"Das gewählte {'Abzweigung' if is_abzweigung else 'Leerrohr'} wurde nicht gefunden.", level=Qgis.Critical
                )
                conn.close()
                return

            vonknoten, nachknoten, vkg_lr = result

            query_verteiler = """
                SELECT "id"
                FROM "lwl"."LWL_Knoten"
                WHERE "id" IN (%s, %s) AND "TYP" = 'Verteilerkasten'
            """
            cur.execute(query_verteiler, (vonknoten, nachknoten))
            verteiler = cur.fetchall()
            verteiler_ids = [v[0] for v in verteiler]

            if is_abzweigung:
                query_belegte_rohre = """
                    SELECT DISTINCT ha."ROHRNUMMER"
                    FROM "lwl"."LWL_Hauseinfuehrung" ha
                    WHERE ha."ID_ABZWEIGUNG" = %s
                    AND ha."VKG_LR" = %s
                """
            else:
                query_belegte_rohre = """
                    SELECT DISTINCT ha."ROHRNUMMER"
                    FROM "lwl"."LWL_Hauseinfuehrung" ha
                    WHERE ha."ID_LEERROHR" = %s
                    AND ha."VKG_LR" = %s
                """
            cur.execute(query_belegte_rohre, (id_, self.gewaehlter_verteiler))
            belegte_rohre = [row[0] for row in cur.fetchall()]
            self.belegte_rohre = belegte_rohre  # Neu: speichern für handle_rect_click

            self.iface.messageBar().pushMessage(
                "Info", f"Belegte Rohrnummern für Verteiler {self.gewaehlter_verteiler}: {belegte_rohre}", level=Qgis.Info
            )

            conn.close()

        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Fehler", f"Fehler beim Abrufen belegter Rohrnummern: {e}", level=Qgis.Critical
            )
            return

        y_offset = 0
        x_offset = 0
        font = QFont()
        font.setPointSize(8)  # Kleinere Schriftgröße für SUBTYP_char

        # Zeichne SUBTYP_char
        subtyp_text = self.scene.addText(f"{subtyp_char}:")
        subtyp_text.setPos(x_offset, y_offset)
        subtyp_text.setDefaultTextColor(Qt.black)
        subtyp_text.setFont(font)
        subtyp_text.setZValue(1)
        self.scene.addItem(subtyp_text)

        x_offset += subtyp_text.boundingRect().width() + 3

        font.setPointSize(10)  # Schriftgröße für Rohrnummern
        square_size = {1: 30, 2: 25, 3: 20}.get(typ, 20)
        unique_durchmesser = set(durchmesser for _, _, durchmesser, _, _, _ in rohre)
        is_mixed = len(unique_durchmesser) > 1
        max_durchmesser = max(unique_durchmesser) if unique_durchmesser else 0

        for rohr_id, rohr_nummer, durchmesser, farbe, primary_farbcode, secondary_farbcode in rohre:
            ist_belegt = rohr_nummer in self.belegte_rohre
            ist_gewaehlt = (rohr_nummer == self.gewaehlte_rohrnummer) if self.gewaehlte_rohrnummer else False
            add_adjust = 5 if is_mixed and durchmesser == max_durchmesser else 0
            size = square_size + add_adjust
            x = x_offset
            y = y_offset + (square_size - size) / 2

            # Quadrat erstellen
            rect = ClickableRect(x, y, size, size, rohr_nummer, rohr_id, self.handle_rect_click)
            rect.setPen(QPen(Qt.black, 1))

            # Dreiecke für diagonale Teilung
            triangle1_points = [
                QPointF(x, y),
                QPointF(x + size, y),
                QPointF(x, y + size)
            ]
            triangle2_points = [
                QPointF(x + size, y),
                QPointF(x + size, y + size),
                QPointF(x, y + size)
            ]

            triangle1 = QGraphicsPolygonItem(QPolygonF(triangle1_points), rect)
            triangle2 = QGraphicsPolygonItem(QPolygonF(triangle2_points), rect)

            # Farben: Gewähltes immer farbig, andere belegte grau
            if ist_belegt and not ist_gewaehlt:
                color = QColor("#808080")
                triangle1.setBrush(QBrush(color))
                triangle2.setBrush(QBrush(color))
                rect.setPen(QPen(Qt.red, 2))
                rect.setToolTip(f"Rohrnummer {rohr_nummer} bereits belegt")
            else:
                if secondary_farbcode:
                    color1 = QColor(primary_farbcode)
                    color2 = QColor(secondary_farbcode)
                    triangle1.setBrush(QBrush(color1))
                    triangle2.setBrush(QBrush(color2))
                else:
                    color = QColor(primary_farbcode)
                    triangle1.setBrush(QBrush(color))
                    triangle2.setBrush(QBrush(color))

            triangle1.setPen(QPen(Qt.NoPen))
            triangle2.setPen(QPen(Qt.NoPen))
            triangle1.setZValue(0)
            triangle2.setZValue(0)
            self.scene.addItem(rect)

            # Rotes X nur für belegte nicht-gewählte
            if ist_belegt and not ist_gewaehlt:
                line1 = QGraphicsLineItem(x, y, x + size, y + size, rect)
                line1.setPen(QPen(Qt.red, 2))
                line1.setZValue(2)
                self.scene.addItem(line1)
                line2 = QGraphicsLineItem(x + size, y, x, y + size, rect)
                line2.setPen(QPen(Qt.red, 2))
                line2.setZValue(2)
                self.scene.addItem(line2)

            # Text für Rohrnummer
            text = self.scene.addText(str(rohr_nummer))
            text_center_x = x + size / 2 - text.boundingRect().width() / 2
            text_center_y = y + size / 2 - text.boundingRect().height() / 2
            text.setPos(text_center_x, text_center_y)
            color = QColor("#808080" if ist_belegt and not ist_gewaehlt else primary_farbcode)
            brightness = (color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114)
            text.setDefaultTextColor(Qt.black if brightness > 128 else Qt.white)
            text.setFont(font)
            text.setZValue(3)
            self.scene.addItem(text)

            x_offset += size + 3

        y_offset += square_size + 1

        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)
        self.ui.graphicsView_Farben_Rohre.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.ui.graphicsView_Farben_Rohre.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.iface.messageBar().pushMessage("Info", "Rohre erfolgreich angezeigt.", level=Qgis.Success)

    def handle_rect_click(self, rohrnummer, farb_id):
        self.iface.messageBar().pushMessage(
            "Info", f"Quadrat mit Rohrnummer {rohrnummer} wurde angeklickt!", level=Qgis.Info
        )

        if self.ausgewaehltes_rechteck:
            try:
                ist_belegt = self.ausgewaehltes_rechteck.rohrnummer in self.belegte_rohre
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
        """Erfasst die Liniengeometrie der Hauseinführung mit Snap auf Leerrohr oder Verteilerkasten."""
        QgsMessageLog.logMessage("DEBUG: Starte aktion_verlauf", "Hauseinfuehrung", Qgis.Info)
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
            # Standardmodus: Snap an Leerrohr oder Abzweigung
            if self.abzweigung_id:
                layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr_Abzweigung")[0]
                feature = next((f for f in layer.getFeatures() if f["id"] == self.abzweigung_id), None)
                if not feature:
                    self.iface.messageBar().pushMessage(
                        "Fehler", "Die ausgewählte Abzweigung konnte nicht gefunden werden.", level=Qgis.Critical
                    )
                    return
                snap_geometry = feature.geometry()
                layer_crs = layer.crs()
                self.iface.messageBar().pushMessage(
                    "Info", f"Abzweigungs-Geometrie geladen: {snap_geometry.asWkt()}", level=Qgis.Info
                )
            elif self.startpunkt_id:
                layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
                feature = next((f for f in layer.getFeatures() if f["id"] == self.startpunkt_id), None)
                if not feature:
                    self.iface.messageBar().pushMessage(
                        "Fehler", "Das ausgewählte Leerrohr konnte nicht gefunden werden.", level=Qgis.Critical
                    )
                    return
                snap_geometry = feature.geometry()
                layer_crs = layer.crs()
                self.iface.messageBar().pushMessage(
                    "Info", f"Leerrohr-Geometrie geladen: {snap_geometry.asWkt()}", level=Qgis.Info
                )
            else:
                self.iface.messageBar().pushMessage(
                    "Fehler", "Kein Parent Leerrohr oder Abzweigung ausgewählt.", level=Qgis.Critical
                )
                return

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
        fehler = self.pruefungen_durchfuehren()

        if fehler:
            # Fehlermeldungen im Label anzeigen
            self.ui.label_Pruefung.setPlainText("\n".join(fehler))
            self.ui.label_Pruefung.setStyleSheet(
                "background-color: rgba(255, 0, 0, 0.2); color: black;"  # Leichtes Rot im Hintergrund, schwarze Schrift
            )
            self.ui.pushButton_Import.setEnabled(False)  # Import deaktivieren
        else:
            # Erfolgsmeldung anzeigen
            self.ui.label_Pruefung.setPlainText("Alle Prüfungen erfolgreich bestanden.")
            self.ui.label_Pruefung.setStyleSheet(
                "background-color: rgba(0, 255, 0, 0.2); color: black;"  # Leichtes Grün im Hintergrund, schwarze Schrift
            )
            self.ui.pushButton_Import.setEnabled(True)  # Import aktivieren

    def check_available_rohre(self):
        """Prüft, ob noch verfügbare Rohre existieren."""
        if self.ui.checkBox_direkt.isChecked():
            return True  # Im Direktmodus gibt es keine Rohrnummern-Beschränkung
        try:
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()

            is_abzweigung = hasattr(self, "abzweigung_id") and self.abzweigung_id is not None
            id_ = self.abzweigung_id if is_abzweigung else self.startpunkt_id
            table_name = '"lwl"."LWL_Leerrohr_Abzweigung"' if is_abzweigung else '"lwl"."LWL_Leerrohr"'

            query = f"""
                SELECT "VONKNOTEN", "NACHKNOTEN", "VKG_LR"
                FROM {table_name}
                WHERE "id" = %s
            """
            cur.execute(query, (id_,))
            result = cur.fetchone()

            if not result:
                self.iface.messageBar().pushMessage(
                    "Fehler", f"Das gewählte {'Abzweigung' if is_abzweigung else 'Leerrohr'} wurde nicht gefunden.", level=Qgis.Critical
                )
                conn.close()
                return False

            vonknoten, nachknoten, vkg_lr = result

            query_verteiler = """
                SELECT "id"
                FROM "lwl"."LWL_Knoten"
                WHERE "id" IN (%s, %s) AND "TYP" = 'Verteilerkasten'
            """
            cur.execute(query_verteiler, (vonknoten, nachknoten))
            verteiler = cur.fetchall()
            verteiler_ids = [v[0] for v in verteiler]

            if is_abzweigung:
                query_belegte_rohre = """
                    SELECT DISTINCT ha."ROHRNUMMER"
                    FROM "lwl"."LWL_Hauseinfuehrung" ha
                    WHERE ha."ID_ABZWEIGUNG" = %s
                    AND ha."VKG_LR" = %s
                """
            else:
                query_belegte_rohre = """
                    SELECT DISTINCT ha."ROHRNUMMER"
                    FROM "lwl"."LWL_Hauseinfuehrung" ha
                    WHERE ha."ID_LEERROHR" = %s
                    AND ha."VKG_LR" = %s
                """
            cur.execute(query_belegte_rohre, (id_, self.gewaehlter_verteiler))
            belegte_rohre = [row[0] for row in cur.fetchall()]

            # Lade alle möglichen Rohrnummern
            subtyp_id = self.ui.label_subtyp.toPlainText().replace("SUBTYP: ", "")
            rohre, _ = self.lade_farben_und_rohrnummern(subtyp_id)
            alle_rohrnummern = [rohr[1] for rohr in rohre]

            verfügbare_rohre = [num for num in alle_rohrnummern if num not in belegte_rohre]
            conn.close()

            return len(verfügbare_rohre) > 0

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
        """Ruft die Formularinitialisierung auf und schließt das Fenster."""
        QgsMessageLog.logMessage("DEBUG: Starte abbrechen_und_schliessen", "Hauseinfuehrung", Qgis.Info)
        self.formular_initialisieren()  # Formular zurücksetzen
        self.close()

    def closeEvent(self, event):
        """Wird aufgerufen, wenn das Fenster geschlossen wird."""
        QgsMessageLog.logMessage("DEBUG: Starte closeEvent", "Hauseinfuehrung", Qgis.Info)
        # Entferne alle bestehenden Highlights
        if self.highlights:
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()

        # Entferne den Adresspunkt-Highlight, falls vorhanden
        if hasattr(self, "adresspunkt_highlight") and self.adresspunkt_highlight:
            self.adresspunkt_highlight.hide()
            self.adresspunkt_highlight = None

        # Entferne roten Rahmen vom ausgewählten Quadrat
        if self.ausgewaehltes_rechteck:
            try:
                self.ausgewaehltes_rechteck.setPen(QPen(Qt.black, 1))
            except RuntimeError:
                pass
        self.ausgewaehltes_rechteck = None

        # Setze die Klassenvariable zurück, um Mehrfachöffnungen zu verhindern
        HauseinfuehrungsVerlegungsTool.instance = None

        # Rufe die Originalmethode auf
        super().closeEvent(event)