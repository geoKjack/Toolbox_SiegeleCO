# -*- coding: utf-8 -*-
"""
HauseinfuehrungsVerlegungsTool
Verlegt Hauseinführungen durch Auswahl von Parent Leerrohr, Verlauf und Endpunkten.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialog
from qgis.core import QgsProject, Qgis, QgsFeatureRequest
from qgis.gui import QgsHighlight
import psycopg2
from .hauseinfuehrung_verlegen_dialog import Ui_HauseinfuehrungsVerlegungsToolDialogBase


class HauseinfuehrungsVerlegungsTool(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.ui = Ui_HauseinfuehrungsVerlegungsToolDialogBase()
        self.ui.setupUi(self)

        # Initialisiere wichtige Variablen
        self.startpunkt_id = None
        self.verlauf_ids = []
        self.highlights = []

        # Buttons mit Aktionen verknüpfen
        self.ui.pushButton_parentLeerrohr.clicked.connect(self.aktion_parent_leerrohr)
        self.ui.pushButton_verlauf_HA.clicked.connect(self.aktion_verlauf)
        self.ui.pushButton_Import.clicked.connect(self.daten_importieren)

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
                # Hier wird die tatsächliche ID des Leerrohrs abgerufen
                leerrohr_id = selected_features[0]["id"]  # 'id' sollte der tatsächliche Spaltenname der ID sein
                self.startpunkt_id = leerrohr_id
                self.ui.label_parentLeerrohr.setText(f"Parent Leerrohr ID: {leerrohr_id}")

                # Highlight setzen
                geom = selected_features[0].geometry()
                highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                highlight.setColor(Qt.red)
                highlight.setWidth(3)
                highlight.show()
                self.highlights.append(highlight)

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(onParentLeerrohrSelected)


    def aktion_verlauf(self):
        """Aktion für die Auswahl des Verlaufs der Hauseinführung."""
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Verlauf der Hauseinführung", level=Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        self.iface.setActiveLayer(layer)
        self.iface.actionSelect().trigger()

        def onVerlaufSelected():
            # Entferne bisherige Highlights
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()

            selected_features = layer.selectedFeatures()
            if selected_features:
                self.verlauf_ids = [feature.id() for feature in selected_features]
                verlauf_text = "; ".join(map(str, self.verlauf_ids))
                self.ui.label_verlauf.setText(f"Verlauf: {verlauf_text}")

                # Highlights setzen
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

    def daten_importieren(self):
        """Importiert die geprüften Daten in die Datenbank."""
        try:
            # Hole die Datenbankverbindung
            db_uri = self.get_database_connection()
            uri = QgsDataSourceUri(db_uri)

            conn = psycopg2.connect(
                dbname=uri.database(),
                user=uri.username(),
                password=uri.password(),
                host=uri.host(),
                port=uri.port()
            )
            cur = conn.cursor()
            conn.autocommit = False

            # Bestimme die nächste verfügbare ID
            haus_id = self.get_next_haus_id()

            # Zusätzliche Attribute abrufen
            kommentar = self.ui.label_Kommentar.text()
            verbundnummer = self.ui.comboBox_Verbundnummer.currentText()
            gefördert = self.ui.comboBox_Gefoerdert.currentText()

            # Daten importieren
            for idx, verlauf_id in enumerate(self.verlauf_ids, start=1):
                insert_query = """
                INSERT INTO "lwl"."LWL_Hauseinfuehrung"
                ("ID", "VERLAUF_ID", "VERBUNDNUMMER", "GEFÖRDERT", "KOMMENTAR")
                VALUES (%s, %s, %s, %s, %s)
                """
                cur.execute(insert_query, (haus_id, verlauf_id, verbundnummer, gefördert, kommentar))

            conn.commit()
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)
            self.reset_form()

        except Exception as e:
            conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Import fehlgeschlagen: {str(e)}", level=Qgis.Critical)

        finally:
            if conn is not None:
                conn.close()

    def reset_form(self):
        """Setzt das Formular zurück."""
        self.startpunkt_id = None
        self.verlauf_ids = []
        self.ui.label_parentLeerrohr.clear()
        self.ui.label_verlauf.clear()
        for highlight in self.highlights:
            highlight.hide()
        self.highlights.clear()

    def get_database_connection(self):
        """Holt die aktuelle Datenbankverbindung."""
        project = QgsProject.instance()
        layers = project.mapLayers().values()

        for layer in layers:
            if layer.name() == "LWL_Leerrohr" and layer.dataProvider().name() == "postgres":
                uri = layer.dataProvider().dataSourceUri()
                return uri

        raise Exception("Keine aktive PostgreSQL-Datenbankverbindung gefunden.")

    def get_next_haus_id(self):
        """Ermittelt die nächste verfügbare ID für Hauseinführungen."""
        db_uri = self.get_database_connection()
        uri = QgsDataSourceUri(db_uri)

        try:
            conn = psycopg2.connect(
                dbname=uri.database(),
                user=uri.username(),
                password=uri.password(),
                host=uri.host(),
                port=uri.port()
            )
            cur = conn.cursor()
            cur.execute('SELECT MAX("ID") FROM "lwl"."LWL_Hauseinfuehrung";')
            result = cur.fetchone()

            return result[0] + 1 if result and result[0] else 1

        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", str(e), level=Qgis.Critical)

        finally:
            if conn is not None:
                conn.close()
