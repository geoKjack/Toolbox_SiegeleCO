# -*- coding: utf-8 -*-
"""
HauseinfuehrungsVerlegungsTool
Verlegt Hauseinführungen durch Auswahl von Parent Leerrohr, Verlauf und Endpunkten.
"""

from qgis.PyQt.QtCore import Qt, QRectF, QObject, pyqtSignal
from qgis.PyQt.QtWidgets import QDialog, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsRectItem
from qgis.core import QgsProject, Qgis, QgsFeatureRequest, QgsDataSourceUri
from qgis.gui import QgsHighlight
from PyQt5.QtGui import QColor, QBrush, QFont
import psycopg2
from .hauseinfuehrung_verlegen_dialog import Ui_HauseinfuehrungsVerlegungsToolDialogBase
from PyQt5.QtCore import QObject, pyqtSignal

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

    def close_database_connection(self):
        """Schließt die Datenbankverbindung."""
        if self.conn:
            self.conn.close()

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
                leerrohr_id = selected_features[0]["id"]
                subtyp_id = selected_features[0]["SUBTYP"]
                farbschema = selected_features[0]["FARBSCHEMA"]  # Direkter Zugriff auf Farbschema
                self.startpunkt_id = leerrohr_id

                # Setze die Labels mit exakten Werten
                self.ui.label_parentLeerrohr.setText(str(leerrohr_id))  # Nur die ID
                self.ui.label_farbschema.setText(farbschema)  # Exakter Farbschema-Wert
                self.ui.label_subtyp.setText(f"SUBTYP: {subtyp_id}")  # Subtyp anzeigen

                # Debug-Ausgabe
                self.iface.messageBar().pushMessage("Info", f"SUBTYP = {subtyp_id}, FARBSCHEMA = {farbschema}", level=Qgis.Info)

                # Zeichne Rohre
                self.zeichne_rohre(subtyp_id, farbschema)

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(onParentLeerrohrSelected)

    def zeichne_rohre(self, subtyp_id, farbschema):
        """Zeichnet klickbare Rechtecke für Rohre basierend auf Subtyp und Farbschema."""
        self.scene = QGraphicsScene()
        self.ui.graphicsView_Farben_Rohre.setScene(self.scene)

        # Daten laden und sortieren
        rohre = sorted(self.lade_farben_und_rohrnummern(subtyp_id, farbschema), key=lambda x: x[0])

        if not rohre:
            self.iface.messageBar().pushMessage("Info", "Keine Rohre zum Zeichnen gefunden.", level=Qgis.Warning)
            return

        x_offset = 10
        y_offset = 10
        rect_width = 30
        rect_height = 30
        spacing = 10
        font_size = 10

        for i, (rohrnummer, farbcode) in enumerate(rohre):
            farbteile = farbcode.split("-")
            x_pos = x_offset + i * (rect_width + spacing)

            # Klickbares Rechteck erstellen
            clickable_rect = ClickableRect(x_pos, y_offset, rect_width, rect_height, rohrnummer, self.handle_rect_click)
            clickable_rect.setBrush(QBrush(QColor(farbteile[0])) if len(farbteile) == 1 else None)
            self.scene.addItem(clickable_rect)

            # Zwei Farben: obere und untere Hälfte
            if len(farbteile) == 2:
                clickable_rect.setBrush(None)
                rect_upper = QGraphicsRectItem(x_pos, y_offset, rect_width, rect_height / 2)
                rect_upper.setBrush(QBrush(QColor(farbteile[0])))
                self.scene.addItem(rect_upper)

                rect_lower = QGraphicsRectItem(x_pos, y_offset + rect_height / 2, rect_width, rect_height / 2)
                rect_lower.setBrush(QBrush(QColor(farbteile[1])))
                self.scene.addItem(rect_lower)

            # HALO und Text hinzufügen
            halo_item = QGraphicsSimpleTextItem(str(rohrnummer))
            halo_item.setFont(QFont("Arial", font_size))
            halo_item.setBrush(QBrush(Qt.white))  # HALO in Weiß
            halo_item.setPos(x_pos + rect_width / 4, y_offset + rect_height / 4)
            halo_item.setZValue(1)  # Hintergrundebene für HALO
            self.scene.addItem(halo_item)

            text_item = QGraphicsSimpleTextItem(str(rohrnummer))
            text_item.setFont(QFont("Arial", font_size))
            text_item.setBrush(QBrush(Qt.black))  # Text in Schwarz
            text_item.setPos(x_pos + rect_width / 4, y_offset + rect_height / 4)
            text_item.setZValue(2)  # Vordergrundebene für Text
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
        """Aktion für die Auswahl des Verlaufs der Hauseinführung."""
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Verlauf der Hauseinführung", level=Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        self.iface.setActiveLayer(layer)
        self.iface.actionSelect().trigger()

        def onVerlaufSelected():
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()

            selected_features = layer.selectedFeatures()
            if selected_features:
                self.verlauf_ids = [feature.id() for feature in selected_features]
                verlauf_text = "; ".join(map(str, self.verlauf_ids))
                self.ui.label_verlauf.setText(f"Verlauf: {verlauf_text}")

                for feature in selected_features:
                    geom = feature.geometry()
                    highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                    highlight.setColor(Qt.blue)
                    highlight.setWidth(3)
                    highlight.show()
                    self.highlights.append(highlight)

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(onVerlaufSelected)

    def get_database_connection(self):
        """Holt die aktuelle Datenbankverbindung."""
        project = QgsProject.instance()
        layers = project.mapLayers().values()
        for layer in layers:
            if layer.name() == "LWL_Leerrohr" and layer.dataProvider().name() == "postgres":
                uri = layer.dataProvider().dataSourceUri()
                return uri
        raise Exception("Keine aktive PostgreSQL-Datenbankverbindung gefunden.")
        
    def daten_importieren(self):
        """Importiert die geprüften Daten in die Datenbank."""
        try:
            cur = self.conn.cursor()
            # Beispiel: Hol dir die Daten aus den UI-Feldern
            kommentar = self.ui.label_Kommentar.text()
            verlauf_ids = self.verlauf_ids  # Vom Verlauf gespeicherte IDs
            startpunkt_id = self.startpunkt_id  # Vom Parent Leerrohr gespeicherte ID

            # Führe die eigentliche Datenbankoperation aus
            for verlauf_id in verlauf_ids:
                query = """
                    INSERT INTO "lwl"."LWL_Hauseinfuehrung" ("STARTPUNKT_ID", "VERLAUF_ID", "KOMMENTAR")
                    VALUES (%s, %s, %s)
                """
                cur.execute(query, (startpunkt_id, verlauf_id, kommentar))

            self.conn.commit()  # Änderungen in der Datenbank bestätigen
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)
        except Exception as e:
            self.conn.rollback()  # Änderungen bei Fehler zurücksetzen
            self.iface.messageBar().pushMessage("Fehler", f"Import fehlgeschlagen: {e}", level=Qgis.Critical)

