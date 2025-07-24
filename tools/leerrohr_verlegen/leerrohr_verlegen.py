import re  # Hinzufügen des Imports für reguläre Ausdrücke
import logging
from qgis.core import QgsVectorLayer, QgsFeature, QgsProject, QgsDataSourceUri, Qgis, QgsGeometry, QgsFeatureRequest, QgsMessageLog, QgsProviderRegistry
from qgis.gui import QgsMapToolEmitPoint, QgsHighlight
from qgis.PyQt.QtCore import Qt, QDate, QSettings, QPointF, QTimer
from qgis.PyQt.QtGui import QColor, QBrush, QPen, QFont, QPolygonF, QTextOption
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QCheckBox, QMessageBox, QGraphicsScene, QGraphicsEllipseItem, QListWidget, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsLineItem, QAbstractItemView, QGraphicsTextItem, QGraphicsItem, QListWidgetItem
from .leerrohr_verlegen_dialog import Ui_LeerrohrVerlegungsToolDialogBase
import psycopg2
import json
import base64
import datetime

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class LeerrohrVerlegenTool(QDialog):
    def __init__(self, iface, parent=None):
        print("DEBUG: Tool initialisiert")
        super().__init__(parent)
        self.iface = iface
        self.ui = Ui_LeerrohrVerlegungsToolDialogBase()
        self.ui.setupUi(self)
        self.ui.listWidget_Leerrohr.setSelectionMode(QAbstractItemView.SingleSelection)
        self.ui.listWidget_Leerrohr.itemClicked.connect(self.handle_leerrohr_selection_from_list)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        # Variablen für die gewählten Objekte
        self.selected_verteiler = None
        self.selected_zwischenknoten = None
        self.selected_verteiler_2 = None
        self.selected_parent_leerrohr = None
        self.selected_subduct_parent = None
        self.selected_vkg_lr = None

        # Map-Tool-Variablen und Trassen-Listen
        self.map_tool = None
        self.selected_trasse_ids = []
        self.selected_trasse_ids_flat = []
        self.trasse_highlights = []
        self.verteiler_highlight_1 = None
        self.verteiler_highlight_2 = None
        self.parent_highlight = None
        self.subduct_highlight = None
        self.zwischenknoten_highlight = None
        self.selected_leerrohr = None
        self.route_highlights = []
        self.leerrohr_highlight = None

        # Setup-Settings initialisieren
        self.settings = QSettings("SiegeleCo", "ToolBox")
        self.db_details = None
        self.is_connected = False
        self.conn = None
        self.cur = None

        # Persistente Verbindung aus Setup-Tool übernehmen
        if hasattr(self.iface, 'plugin') and hasattr(self.iface.plugin, 'conn') and self.iface.plugin.conn:
            self.conn = self.iface.plugin.conn
            self.cur = self.conn.cursor()
            self.is_connected = True
            print("DEBUG: Persistente DB-Verbindung aus Setup-Tool übernommen")
            self.db_details = self.get_database_connection()
        else:
            print("DEBUG: Keine persistente Verbindung aus Setup-Tool verfügbar")
            self.iface.messageBar().pushMessage("Fehler", "Keine DB-Verbindung. Bitte Setup öffnen.", level=Qgis.Critical)

        # Neue Ergänzung: Dictionary für Subtyp-Quantitäten
        self.subtyp_quantities = {}  # subtyp_id -> quantity (int, Default: 1)

        # Neue Ergänzung: Persistente Scene für Subtyp-Auswahl
        self.subtyp_scene = QGraphicsScene()
        self.ui.graphicsView_Auswahl_Subtyp.setScene(self.subtyp_scene)
        self.ui.graphicsView_Auswahl_Subtyp.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.ui.graphicsView_Auswahl_Subtyp.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Lade Setup-Daten
        self.load_setup_data()

        # Verknüpfe Buttons mit bestehenden Methoden
        self.ui.pushButton_verteiler.clicked.connect(self.select_verteiler)
        self.ui.pushButton_verteiler_2.clicked.connect(self.select_verteiler_2)
        self.ui.pushButton_Parent_Leerrohr.clicked.connect(self.select_parent_leerrohr)
        self.ui.pushButton_routing.clicked.connect(self.start_routing)
        self.ui.pushButton_subduct.clicked.connect(self.select_subduct_parent)
        self.ui.pushButton_Datenpruefung.clicked.connect(self.pruefe_daten)
        self.ui.pushButton_Import.setEnabled(self.is_connected)
        self.ui.pushButton_Import.clicked.connect(self.importiere_daten)
        self.ui.pushButton_zwischenknoten.clicked.connect(self.select_zwischenknoten)
        self.ui.pushButton_select_leerrohr.clicked.connect(self.select_leerrohr)
        self.ui.pushButton_update_leerrohr.clicked.connect(self.update_leerrohr)

        # Reset & Cancel Buttons
        reset_button = self.ui.button_box.button(QDialogButtonBox.Reset)
        cancel_button = self.ui.button_box.button(QDialogButtonBox.Cancel)
        if reset_button:
            reset_button.clicked.connect(self.clear_trasse_selection)
        if cancel_button:
            cancel_button.clicked.connect(self.close_tool)

        # Radiobuttons für Verlegungsmodus
        self.ui.radioButton_Hauptstrang.toggled.connect(self.update_verlegungsmodus)
        self.ui.radioButton_Abzweigung.toggled.connect(self.update_verlegungsmodus)

        # CheckBoxen für Förderung und Subduct
        self.ui.checkBox_Foerderung.toggled.connect(self.update_combobox_states)
        self.ui.checkBox_Subduct.toggled.connect(self.update_subduct_button)

        # ListWidget-Verknüpfungen für Subtypen
        self.ui.listWidget_Zubringerrohr.itemSelectionChanged.connect(self.handle_subtyp_selection)
        self.ui.listWidget_Hauptrohr.itemSelectionChanged.connect(self.handle_subtyp_selection)
        self.ui.listWidget_Multirohr.itemSelectionChanged.connect(self.handle_subtyp_selection)

        # Setze Standardzustand
        self.populate_gefoerdert_subduct()
        self.update_verlegungsmodus()

        # Speichert Routen nach path_id für Farben
        self.routes_by_path_id = {}

        print(f"DEBUG: Initialer Status von Verbundnummer: {self.ui.comboBox_Verbundnummer.currentText()}, Enabled: {self.ui.comboBox_Verbundnummer.isEnabled()}")
        QgsMessageLog.logMessage(str(dir(self.ui)), "Leerrohr-Tool", level=Qgis.Info)

    class DuplicateButtonItem(QGraphicsTextItem):
        def __init__(self, subtyp_id, parent_tool):
            super().__init__("+")  # Text: '+' als Button-Symbol
            self.subtyp_id = subtyp_id
            self.parent_tool = parent_tool  # Referenz auf LeerrohrVerlegenTool
            self.setFont(QFont("Arial", 12, QFont.Bold))
            self.setDefaultTextColor(Qt.blue)  # Blau für Klickbarkeit
            self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsFocusable)
            self.setCursor(Qt.PointingHandCursor)  # Hand-Cursor für Button-Feeling
            self.setToolTip("Klicken, um diesen Subtyp zu duplizieren")

        def mousePressEvent(self, event):
            if event.button() == Qt.LeftButton:
                # Erhöhe Quantity
                if self.subtyp_id in self.parent_tool.subtyp_quantities:
                    self.parent_tool.subtyp_quantities[self.subtyp_id] += 1
                else:
                    self.parent_tool.subtyp_quantities[self.subtyp_id] = 2  # Start bei 2, da 1 schon da ist
                print(f"DEBUG: Subtyp {self.subtyp_id} dupliziert – neue Anzahl: {self.parent_tool.subtyp_quantities[self.subtyp_id]}")
                
                # Asynchroner Update-Aufruf, um Crash zu vermeiden
                QTimer.singleShot(0, self.parent_tool.update_selected_leerrohr_subtyp)
            super().mousePressEvent(event)

    def load_setup_data(self):
        """Lädt Subtypen aus self.iface.plugin.active_setup und befüllt ListWidgets."""
        print(f"DEBUG: Lade Subtypen aus active_setup: leerrohr_subtyp = {self.iface.plugin.active_setup.get('leerrohr_subtyp', [])}, leerrohr_subtyp_data = {self.iface.plugin.active_setup.get('leerrohr_subtyp_data', [])}")
        if not hasattr(self.iface, 'plugin') or not hasattr(self.iface.plugin, 'active_setup') or not self.iface.plugin.active_setup:
            self.iface.messageBar().pushMessage("Fehler", "Kein aktives Setup gefunden. Bitte konfigurieren Sie das Setup.", level=Qgis.Critical)
            QgsMessageLog.logMessage("Kein aktives Setup in iface.plugin.active_setup", "Leerrohr-Tool", Qgis.Critical)
            return

        active_setup = self.iface.plugin.active_setup
        leerrohr_subtyp_ids = active_setup.get("leerrohr_subtyp", [])
        if not leerrohr_subtyp_ids:
            self.iface.messageBar().pushMessage("Info", "Keine Subtypen im Setup gefunden.", level=Qgis.Info)
            QgsMessageLog.logMessage("Keine Subtypen in active_setup", "Leerrohr-Tool", Qgis.Info)
            return

        leerrohr_subtyp_data = active_setup.get("leerrohr_subtyp_data", [])
        if not leerrohr_subtyp_data:
            self.iface.messageBar().pushMessage("Fehler", "Keine Subtyp-Daten in active_setup verfügbar.", level=Qgis.Critical)
            QgsMessageLog.logMessage("Keine leerrohr_subtyp_data in active_setup", "Leerrohr-Tool", Qgis.Critical)
            return

        try:
            self.ui.listWidget_Zubringerrohr.clear()
            self.ui.listWidget_Hauptrohr.clear()
            self.ui.listWidget_Multirohr.clear()
            for subtyp in leerrohr_subtyp_data:
                subtyp_id, typ_nummer, subtyp_char, codierung, bemerkung, codierung_id = subtyp
                if subtyp_id not in leerrohr_subtyp_ids:
                    continue
                item_text = f"{subtyp_id} - {typ_nummer} - {subtyp_char} - {codierung} - {bemerkung} (ID: {codierung_id})"
                if typ_nummer == 1:
                    self.ui.listWidget_Zubringerrohr.addItem(item_text)
                elif typ_nummer == 2:
                    self.ui.listWidget_Hauptrohr.addItem(item_text)
                elif typ_nummer == 3:
                    self.ui.listWidget_Multirohr.addItem(item_text)
            self.ui.listWidget_Zubringerrohr.setSelectionMode(QListWidget.MultiSelection)
            self.ui.listWidget_Hauptrohr.setSelectionMode(QListWidget.MultiSelection)
            self.ui.listWidget_Multirohr.setSelectionMode(QListWidget.MultiSelection)
            for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
                for i in range(list_widget.count()):
                    item = list_widget.item(i)
                    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            QgsMessageLog.logMessage(f"Geladene Subtypen: {len(leerrohr_subtyp_ids)} Einträge", "Leerrohr-Tool", Qgis.Info)
        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Subtypen fehlgeschlagen: {str(e)}", level=Qgis.Critical)
            QgsMessageLog.logMessage(f"Fehler Subtypen: {str(e)}", "Leerrohr-Tool", Qgis.Critical)

    def debug_check(self):
        """Prüft den Zugriff auf UI-Elemente für Debugging-Zwecke."""
        try:
            print("Prüfe Zugriff auf 'label_gewaehlter_verteiler'")
            verteiler_id_text = self.ui.label_gewaehlter_verteiler.text()
            print(f"'label_gewaehlter_verteiler' Text: {verteiler_id_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_gewaehlter_verteiler': {e}")

        try:
            print("Prüfe Zugriff auf 'label_verlauf'")
            verlauf_text = self.ui.label_verlauf.text()
            print(f"'label_verlauf' Text: {verlauf_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_verlauf': {e}")

        try:
            print("Prüfe Zugriff auf 'label_Pruefung'")
            pruefung_text = self.ui.label_Pruefung.text()
            print(f"'label_Pruefung' Text: {pruefung_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_Pruefung': {e}")

        try:
            print("Prüfe Zugriff auf 'label_Kommentar'")
            kommentar_text = self.ui.label_Kommentar.text()
            print(f"'label_Kommentar' Text: {kommentar_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_Kommentar': {e}")

        try:
            print("Prüfe Zugriff auf 'label_Kommentar_2'")
            beschreibung_text = self.ui.label_Kommentar_2.text()
            print(f"'label_Kommentar_2' Text: {beschreibung_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_Kommentar_2': {e}")

        print("Debugging abgeschlossen.")

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

    def db_execute(self, query):
        """Führt eine SQL-Abfrage gegen die PostgreSQL-Datenbank aus und gibt das Ergebnis zurück."""
        try:
            db_params = self.get_database_connection()
            conn = psycopg2.connect(**db_params)
            cur = conn.cursor()
            print(f"DEBUG: Führe Query aus: {query}")
            cur.execute(query)
            result = cur.fetchall()
            print(f"DEBUG: Ergebnis aus Datenbank: {result}")
            conn.close()
            return result
        except psycopg2.Error as e:
            print(f"DEBUG: PostgreSQL-Fehler bei SQL-Query: {e}")
            print(f"DEBUG: Fehlgeschlagene Query: {query}")
            QgsMessageLog.logMessage(f"PostgreSQL-Fehler: {e}", "Leerrohr-Tool", level=Qgis.Critical)
            if 'conn' in locals():
                conn.close()
            return None
        except Exception as e:
            print(f"DEBUG: Allgemeiner Fehler bei SQL-Query: {e}")
            print(f"DEBUG: Fehlgeschlagene Query: {query}")
            QgsMessageLog.logMessage(f"Allgemeiner Fehler: {e}", "Leerrohr-Tool", level=Qgis.Critical)
            if 'conn' in locals():
                conn.close()
            return None

    def handle_leerrohr_selection_from_list(self, item):
        """Handhabt die Auswahl eines Leerrohrs aus dem ListWidget."""
        import time
        start_time = time.time()
        if item:
            selected_feature = item.data(Qt.UserRole)
            if selected_feature:
                is_abzweigung = self.ui.radioButton_Abzweigung.isChecked()
                layer_name = "LWL_Leerrohr_Abzweigung" if is_abzweigung else "LWL_Leerrohr"
                layer = QgsProject.instance().mapLayersByName(layer_name)
                if layer:
                    layer = layer[0]
                else:
                    print(f"DEBUG: Layer {layer_name} nicht gefunden in Handler")
                    return
                self.process_selected_leerrohr(selected_feature, is_abzweigung, layer)
                # Optional: ListWidget nach Auswahl leeren
                self.ui.listWidget_Leerrohr.clear()
        print(f"DEBUG: Zeit für List-Auswahl-Handler: {time.time() - start_time:.2f} Sekunden")

    def handle_subtyp_selection(self):
        """Handhabt die Auswahl eines Subtyps, indem andere Subtypen abgewählt werden."""
        print("DEBUG: Starte handle_subtyp_selection")
        if self.ui.radioButton_Abzweigung.isChecked() or self.selected_leerrohr:
            # Im Abzweigungs- oder Update-Modus: Nur ein Subtyp erlaubt
            sender = self.sender()  # ListWidget, das das Signal ausgelöst hat
            selected_items = sender.selectedItems()
            if selected_items:
                # Wähle nur das zuletzt ausgewählte Item und deaktiviere andere
                selected_item = selected_items[-1]
                for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
                    for i in range(list_widget.count()):
                        item = list_widget.item(i)
                        if list_widget != sender or item != selected_item:
                            item.setSelected(False)
        self.update_selected_leerrohr_subtyp()

    def update_verlegungsmodus(self):
        """Aktiviert oder deaktiviert Felder je nach Auswahl von Hauptstrang/Abzweigung."""
        print("DEBUG: Starte update_verlegungsmodus")
        if self.ui.radioButton_Abzweigung.isChecked():
            # Einfachauswahl für ListWidgets im Abzweigungsmodus
            self.ui.listWidget_Zubringerrohr.setSelectionMode(QAbstractItemView.SingleSelection)
            self.ui.listWidget_Hauptrohr.setSelectionMode(QAbstractItemView.SingleSelection)
            self.ui.listWidget_Multirohr.setSelectionMode(QAbstractItemView.SingleSelection)
            self.ui.listWidget_Zubringerrohr.setEnabled(True)
            self.ui.listWidget_Hauptrohr.setEnabled(True)
            self.ui.listWidget_Multirohr.setEnabled(True)
            self.ui.pushButton_Parent_Leerrohr.setEnabled(True)
            self.ui.label_Parent_Leerrohr.setEnabled(True)
            if self.selected_parent_leerrohr:
                self.ui.label_Parent_Leerrohr.setStyleSheet("background-color: lightgreen;")
            else:
                self.ui.label_Parent_Leerrohr.setStyleSheet("background-color: lightcoral;")
            self.ui.pushButton_verteiler.setText("Startknoten Abzweigung")
            self.ui.pushButton_verteiler_2.setText("Endknoten Abzweigung")
            self.ui.pushButton_select_leerrohr.setText("Abzweigung Bestand")
            self.ui.comboBox_Verbundnummer.setEnabled(False)
            self.ui.checkBox_Foerderung.setEnabled(False)
            self.ui.checkBox_Subduct.setEnabled(False)
            self.ui.pushButton_subduct.setEnabled(False)
            self.ui.label_Subduct.setEnabled(False)
            self.ui.label_Kommentar.setEnabled(False)
            self.ui.label_Kommentar_2.setEnabled(False)
            self.ui.mDateTimeEdit_Strecke.setEnabled(False)
            # Deaktiviere Zwischenknoten-Button und Label
            self.ui.pushButton_zwischenknoten.setEnabled(False)
            self.ui.label_gewaehlter_zwischenknoten.setEnabled(False)
            self.ui.label_gewaehlter_zwischenknoten.setText("Zwischenknoten nicht verfügbar")
            self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: gray;")
            if self.selected_zwischenknoten:
                self.selected_zwischenknoten = None
                if self.zwischenknoten_highlight:
                    self.zwischenknoten_highlight.hide()
                    self.zwischenknoten_highlight = None
            self.clear_trasse_selection()
        else:
            # MultiSelection im Hauptstrangmodus (Import), SingleSelection im Update-Modus
            selection_mode = QAbstractItemView.SingleSelection if self.selected_leerrohr else QAbstractItemView.MultiSelection
            self.ui.listWidget_Zubringerrohr.setSelectionMode(selection_mode)
            self.ui.listWidget_Hauptrohr.setSelectionMode(selection_mode)
            self.ui.listWidget_Multirohr.setSelectionMode(selection_mode)
            self.ui.listWidget_Zubringerrohr.setEnabled(True)
            self.ui.listWidget_Hauptrohr.setEnabled(True)
            self.ui.listWidget_Multirohr.setEnabled(True)
            self.ui.pushButton_Parent_Leerrohr.setEnabled(False)
            self.ui.label_Parent_Leerrohr.setEnabled(False)
            self.ui.label_Parent_Leerrohr.setText("Parent-Leerrohr erfassen")
            self.ui.label_Parent_Leerrohr.setStyleSheet("")
            self.selected_parent_leerrohr = None
            self.ui.pushButton_verteiler.setText("Startknoten auswählen")
            self.ui.pushButton_verteiler_2.setText("Endknoten auswählen")
            self.ui.pushButton_select_leerrohr.setText("Leerrohr Bestand")
            self.ui.comboBox_Verbundnummer.setEnabled(True)
            self.ui.checkBox_Foerderung.setEnabled(True)
            self.ui.checkBox_Subduct.setEnabled(True)
            self.ui.pushButton_subduct.setEnabled(self.ui.checkBox_Subduct.isChecked())
            self.ui.label_Subduct.setEnabled(self.ui.checkBox_Subduct.isChecked())
            self.ui.label_Kommentar.setEnabled(True)
            self.ui.label_Kommentar_2.setEnabled(True)
            self.ui.mDateTimeEdit_Strecke.setEnabled(True)
            # Aktiviere Zwischenknoten-Button und Label
            self.ui.pushButton_zwischenknoten.setEnabled(True)
            self.ui.label_gewaehlter_zwischenknoten.setEnabled(True)
            self.ui.label_gewaehlter_zwischenknoten.setText("Zwischenknoten wählen (optional)")
            self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: gray;")
            self.clear_trasse_selection()
            self.update_combobox_states()
            self.update_subduct_button()
        
    def select_verteiler(self):
        """Aktiviert das Map-Tool zum Auswählen des ersten Knotens."""
        print("DEBUG: Starte Auswahl des ersten Knotens")
        if self.ui.radioButton_Abzweigung.isChecked():
            self.ui.label_gewaehlter_verteiler.setText("Wählen Sie den Start der Abzweigung")
        else:
            self.ui.label_gewaehlter_verteiler.setText("Wählen Sie den Startknoten")
        self.ui.label_gewaehlter_verteiler.clear()
        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        if self.ui.radioButton_Abzweigung.isChecked():
            self.map_tool.canvasClicked.connect(self.abzweigung_start_selected)
        else:
            self.map_tool.canvasClicked.connect(self.verteiler_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def abzweigung_start_selected(self, point):
        """Speichert den gewählten Startknoten der Abzweigung und validiert ihn."""
        print("DEBUG: Starte Auswahl des ersten Knotens")
        layer_name = "LWL_Knoten"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_gewaehlter_verteiler.setText("Layer 'LWL_Knoten' nicht gefunden")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        map_scale = self.iface.mapCanvas().scale()
        threshold_distance = 50 * (map_scale / (39.37 * 96))

        nearest_feature = None
        nearest_distance = float("inf")

        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        for feature in layer.getFeatures(request):
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        if nearest_feature and nearest_distance <= threshold_distance:
            knot_id = nearest_feature["id"]
            self.selected_verteiler = knot_id

            if self.ui.radioButton_Abzweigung.isChecked():
                if not self.selected_parent_leerrohr or "ID_TRASSE" not in self.selected_parent_leerrohr:
                    self.ui.label_gewaehlter_verteiler.setText("Kein Parent-Leerrohr ausgewählt oder Trassen-IDs fehlen")
                    self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
                    self.selected_verteiler = None
                    return

                parent_trasse_ids = self.selected_parent_leerrohr.get("ID_TRASSE", [])
                if not parent_trasse_ids:
                    self.ui.label_gewaehlter_verteiler.setText("Keine Trassen-IDs im Parent-Leerrohr gefunden")
                    self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
                    self.selected_verteiler = None
                    return

                trasse_ids_str = "{" + ",".join(str(int(id)) for id in parent_trasse_ids) + "}"
                try:
                    with psycopg2.connect(**self.db_details) as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                SELECT COUNT(*) 
                                FROM lwl."LWL_Trasse" 
                                WHERE id = ANY(%s)
                                AND ("VONKNOTEN" = %s OR "NACHKNOTEN" = %s)
                            """, (trasse_ids_str, knot_id, knot_id))
                            result = cur.fetchone()
                            if result and result[0] > 0:
                                self.ui.label_gewaehlter_verteiler.setText(f"Startknoten: {knot_id}")
                                self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightgreen;")
                                if hasattr(self, "verteiler_highlight_1") and self.verteiler_highlight_1:
                                    self.verteiler_highlight_1.hide()
                                self.verteiler_highlight_1 = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
                                self.verteiler_highlight_1.setColor(Qt.blue)
                                self.verteiler_highlight_1.setWidth(5)
                                self.verteiler_highlight_1.show()
                                parent_vkg_lr = self.selected_parent_leerrohr.get("VKG_LR", None)
                                parent_endknoten = self.selected_parent_leerrohr.get("NACHKNOTEN", None)
                                if knot_id == parent_vkg_lr or (parent_endknoten and knot_id == parent_endknoten):
                                    self.ui.label_gewaehlter_verteiler.setText("Der Startknoten der Abzweigung darf nicht Start- oder Endknoten des Parent-Leerrohrs sein!")
                                    self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
                                    self.selected_verteiler = None
                                    if self.verteiler_highlight_1:
                                        self.verteiler_highlight_1.hide()
                                        self.verteiler_highlight_1 = None
                                    return
                            else:
                                self.ui.label_gewaehlter_verteiler.setText("Kein Knoten auf Trasse gefunden")
                                self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
                                self.selected_verteiler = None
                                return
                except Exception as e:
                    self.ui.label_gewaehlter_verteiler.setText(f"Datenbankfehler: {e}")
                    self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
                    self.selected_verteiler = None
                    return
        else:
            self.ui.label_gewaehlter_verteiler.setText("Kein Knoten in Reichweite gefunden")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
            self.selected_verteiler = None

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def verteiler_selected(self, point):
        """Speichert den gewählten ersten Verteiler/Knoten in `selected_verteiler`."""
        import time
        start_time = time.time()

        print("DEBUG: Starte Knotenauswahl (Verteiler 1)")
        layer_name = "LWL_Knoten"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_gewaehlter_verteiler.setText("Layer 'LWL_Knoten' nicht gefunden")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        map_scale = self.iface.mapCanvas().scale()
        threshold_distance = 10 * (map_scale / (39.37 * 96))

        nearest_feature = None
        nearest_distance = float("inf")

        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        for feature in layer.getFeatures(request):
            if feature["TYP"] not in ["Verteilerkasten", "Schacht", "Ortszentrale"]:
                continue
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        print(f"DEBUG: Anzahl der Features im Filterbereich: {sum(1 for _ in layer.getFeatures(request))}")
        print(f"DEBUG: Zeit für Knotenauswahl: {time.time() - start_time} Sekunden")

        if nearest_feature and nearest_distance <= threshold_distance:
            verteiler_id = nearest_feature["id"]
            self.selected_verteiler = verteiler_id
            self.ui.label_gewaehlter_verteiler.setText(f"Verteiler/Knoten ID: {verteiler_id}")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightgreen;")
            if self.verteiler_highlight_1:
                self.verteiler_highlight_1.hide()
            self.verteiler_highlight_1 = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.verteiler_highlight_1.setColor(Qt.red)
            self.verteiler_highlight_1.setWidth(5)
            self.verteiler_highlight_1.show()
            QgsMessageLog.logMessage(f"Erster Knoten gewählt: {self.selected_verteiler}", "Leerrohr-Tool", level=Qgis.Info)
        else:
            self.ui.label_gewaehlter_verteiler.setText("Kein Knoten innerhalb der Toleranz gefunden")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def select_verteiler_2(self):
        """Aktiviert das Map-Tool zum Auswählen des zweiten Knotens."""
        print("DEBUG: Starte Auswahl des zweiten Knotens")
        if self.ui.radioButton_Abzweigung.isChecked():
            self.ui.label_gewaehlter_verteiler_2.setText("Wählen Sie das Ende der Abzweigung")
        else:
            self.ui.label_gewaehlter_verteiler_2.setText("Wählen Sie den Endknoten")
        self.ui.label_gewaehlter_verteiler_2.clear()
        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        if self.ui.radioButton_Abzweigung.isChecked():
            self.map_tool.canvasClicked.connect(self.abzweigung_end_selected)
        else:
            self.map_tool.canvasClicked.connect(self.verteiler_2_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def abzweigung_end_selected(self, point):
        """Speichert den Endknoten der Abzweigung."""
        layer = QgsProject.instance().mapLayersByName("LWL_Knoten")
        if not layer:
            self.ui.label_gewaehlter_verteiler_2.setText("Layer 'LWL_Knoten' nicht gefunden")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]
        map_scale = self.iface.mapCanvas().scale()
        threshold_distance = 50 * (map_scale / (39.37 * 96))

        nearest_feature = None
        nearest_distance = float("inf")
        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        for feature in layer.getFeatures(request):
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        if nearest_feature:
            self.selected_verteiler_2 = nearest_feature["id"]
            self.ui.label_gewaehlter_verteiler_2.setText(f"Ende Abzweigung ID: {self.selected_verteiler_2}")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightgreen;")
            if hasattr(self, "verteiler_2_highlight") and self.verteiler_2_highlight:
                self.verteiler_2_highlight.hide()
            self.verteiler_2_highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.verteiler_2_highlight.setColor(Qt.blue)
            self.verteiler_2_highlight.setWidth(5)
            self.verteiler_2_highlight.show()
            QgsMessageLog.logMessage(f"Ende der Abzweigung gewählt: {self.selected_verteiler_2}", "Leerrohr-Tool", level=Qgis.Info)
        else:
            self.ui.label_gewaehlter_verteiler_2.setText("Kein Knoten in Reichweite gefunden")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def verteiler_2_selected(self, point):
        """Speichert den gewählten zweiten Verteiler/Knoten in `selected_verteiler_2`."""
        import time
        start_time = time.time()

        print("DEBUG: Starte Knotenauswahl (Verteiler 2)")
        layer_name = "LWL_Knoten"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_gewaehlter_verteiler_2.setText("Layer 'LWL_Knoten' nicht gefunden")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        map_scale = self.iface.mapCanvas().scale()
        threshold_distance = 10 * (map_scale / (39.37 * 96))

        nearest_feature = None
        nearest_distance = float("inf")

        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        for feature in layer.getFeatures(request):
            if feature["TYP"] not in ["Verteilerkasten", "Schacht", "Ortszentrale", "Hilfsknoten"]:
                continue
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        print(f"DEBUG: Anzahl der Features im Filterbereich: {sum(1 for _ in layer.getFeatures(request))}")
        print(f"DEBUG: Zeit für Knotenauswahl: {time.time() - start_time} Sekunden")

        if nearest_feature and nearest_distance <= threshold_distance:
            verteiler_id = nearest_feature["id"]
            self.selected_verteiler_2 = verteiler_id
            self.ui.label_gewaehlter_verteiler_2.setText(f"Verteiler/Knoten ID: {verteiler_id}")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightgreen;")
            if self.verteiler_highlight_2:
                self.verteiler_highlight_2.hide()
            self.verteiler_highlight_2 = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.verteiler_highlight_2.setColor(Qt.red)
            self.verteiler_highlight_2.setWidth(5)
            self.verteiler_highlight_2.show()
            QgsMessageLog.logMessage(f"Zweiter Knoten gewählt: {self.selected_verteiler_2}", "Leerrohr-Tool", level=Qgis.Info)
        else:
            self.ui.label_gewaehlter_verteiler_2.setText("Kein Knoten innerhalb der Toleranz gefunden")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def select_parent_leerrohr(self):
        """Aktiviert das Map-Tool zum Auswählen eines Parent-Leerrohrs."""
        print("DEBUG: Starte Auswahl eines Parent-Leerrohrs")
        self.ui.label_Parent_Leerrohr.clear()
        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.parent_leerrohr_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def parent_leerrohr_selected(self, point):
        """Speichert das gewählte Parent-Leerrohr."""
        import time
        start_time = time.time()

        print("DEBUG: Verarbeite Auswahl des Parent-Leerrohrs")
        layer_name = "LWL_Leerrohr"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_Parent_Leerrohr.setText("Layer 'LWL_Leerrohr' nicht gefunden")
            self.ui.label_Parent_Leerrohr.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        map_scale = self.iface.mapCanvas().scale()
        threshold_distance = 10 * (map_scale / (39.37 * 96))

        nearest_feature = None
        nearest_distance = float("inf")

        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        for feature in layer.getFeatures(request):
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        if nearest_feature and nearest_distance <= threshold_distance:
            leerrohr_id = nearest_feature["id"]
            self.selected_parent_leerrohr = {
                "id": leerrohr_id,
                "ID_TRASSE": nearest_feature["ID_TRASSE"],
                "VKG_LR": nearest_feature["VKG_LR"],
                "NACHKNOTEN": nearest_feature["NACHKNOTEN"],
                "COUNT": nearest_feature["COUNT"],
                "VERFUEGBARE_ROHRE": nearest_feature["VERFUEGBARE_ROHRE"],
                "SUBTYP": nearest_feature["SUBTYP"]
            }
            self.ui.label_Parent_Leerrohr.setText(f"Parent-Leerrohr ID: {leerrohr_id}")
            self.ui.label_Parent_Leerrohr.setStyleSheet("background-color: lightgreen;")
            if hasattr(self, "parent_highlight") and self.parent_highlight:
                self.parent_highlight.hide()
            self.parent_highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.parent_highlight.setColor(Qt.green)
            self.parent_highlight.setWidth(5)
            self.parent_highlight.show()
            QgsMessageLog.logMessage(f"Parent-Leerrohr gewählt: {leerrohr_id}", "Leerrohr-Tool", level=Qgis.Info)
            # Aktualisiere die Subtyp-Anzeige sofort
            self.update_selected_leerrohr_subtyp()
        else:
            self.ui.label_Parent_Leerrohr.setText("Kein Leerrohr in Reichweite gefunden")
            self.ui.label_Parent_Leerrohr.setStyleSheet("background-color: lightcoral;")
            self.selected_parent_leerrohr = None

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def select_zwischenknoten(self):
        """Aktiviert das Map-Tool zum Auswählen eines Zwischenknotens."""
        print("DEBUG: Starte Auswahl des Zwischenknotens")
        self.ui.label_gewaehlter_zwischenknoten.setText("Wählen Sie den Zwischenknoten")
        self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: gray;")
        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.zwischenknoten_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def zwischenknoten_selected(self, point):
        """Speichert den gewählten Zwischenknoten und validiert ihn."""
        print("DEBUG: Verarbeite Auswahl des Zwischenknotens")
        layer_name = "LWL_Knoten"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_gewaehlter_zwischenknoten.setText("Layer 'LWL_Knoten' nicht gefunden")
            self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        map_scale = self.iface.mapCanvas().scale()
        threshold_distance = 10 * (map_scale / (39.37 * 96))

        nearest_feature = None
        nearest_distance = float("inf")

        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        for feature in layer.getFeatures(request):
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        if nearest_feature and nearest_distance <= threshold_distance:
            zwischenknoten_id = nearest_feature["id"]
            # Prüfen, ob der Zwischenknoten weder Start- noch Endknoten ist
            if zwischenknoten_id == self.selected_verteiler or zwischenknoten_id == self.selected_verteiler_2:
                self.ui.label_gewaehlter_zwischenknoten.setText("Zwischenknoten darf nicht Start- oder Endknoten sein!")
                self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: lightcoral;")
                self.selected_zwischenknoten = None
                if self.zwischenknoten_highlight:
                    self.zwischenknoten_highlight.hide()
                    self.zwischenknoten_highlight = None
                return
            self.selected_zwischenknoten = zwischenknoten_id
            self.ui.label_gewaehlter_zwischenknoten.setText(f"Zwischenknoten ID: {zwischenknoten_id}")
            self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: lightgreen;")
            if self.zwischenknoten_highlight:
                self.zwischenknoten_highlight.hide()
            self.zwischenknoten_highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.zwischenknoten_highlight.setColor(Qt.yellow)  # Gelb für Zwischenknoten
            self.zwischenknoten_highlight.setWidth(5)
            self.zwischenknoten_highlight.show()
            QgsMessageLog.logMessage(f"Zwischenknoten gewählt: {zwischenknoten_id}", "Leerrohr-Tool", level=Qgis.Info)
        else:
            self.ui.label_gewaehlter_zwischenknoten.setText("Kein Knoten in Reichweite gefunden")
            self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: lightcoral;")
            self.selected_zwischenknoten = None

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def select_leerrohr(self):
        """Aktiviert das Map-Tool zum Auswählen eines bestehenden Leerrohrs oder einer Abzweigung."""
        print("DEBUG: Starte Auswahl eines bestehenden Leerrohrs/Abzweigung")
        if self.ui.radioButton_Abzweigung.isChecked():
            self.ui.label_gewaehltes_leerrohr.setText("Abzweigung auswählen")
        else:
            self.ui.label_gewaehltes_leerrohr.setText("Leerrohr auswählen")
        self.ui.label_gewaehltes_leerrohr.setStyleSheet("background-color: lightcoral;")
        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.leerrohr_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def leerrohr_selected(self, point):
        """Speichert das gewählte Leerrohr oder die Abzweigung und hebt ihre Knoten hervor."""
        print("DEBUG: Verarbeite Auswahl von Leerrohr/Abzweigung")
        is_abzweigung = self.ui.radioButton_Abzweigung.isChecked()
        layer_name = "LWL_Leerrohr_Abzweigung" if is_abzweigung else "LWL_Leerrohr"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_gewaehltes_leerrohr.setText(f"Layer '{layer_name}' nicht gefunden")
            self.ui.label_gewaehltes_leerrohr.setStyleSheet("background-color: gray;")
            print(f"DEBUG: Layer {layer_name} nicht gefunden")
            self.ui.pushButton_update_leerrohr.setEnabled(False)
            # Setze ListWidgets zurück auf MultiSelection
            for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
                list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
                for i in range(list_widget.count()):
                    item = list_widget.item(i)
                    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    item.setSelected(False)
            self.ui.listWidget_Leerrohr.clear()  # Neues Widget leeren
            return
        layer = layer[0]
        print(f"DEBUG: Layer {layer_name} erfolgreich geladen")

        map_scale = self.iface.mapCanvas().scale()
        threshold_distance = 4 * (map_scale / (39.37 * 96))  # Skalierter Radius: Bei Zoom 1:1000

        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        candidates = []  # Liste aller Kandidaten im Radius
        for feature in layer.getFeatures(request):
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance <= threshold_distance:
                candidates.append(feature)

        # ListWidget leeren vor Befüllung
        self.ui.listWidget_Leerrohr.clear()

        if not candidates:
            self.ui.label_gewaehltes_leerrohr.setText("Kein Leerrohr in Reichweite gefunden")
            self.ui.label_gewaehltes_leerrohr.setStyleSheet("background-color: lightcoral;")
            return

        if len(candidates) == 1:
            # Nur eines: Direkt verarbeiten
            nearest_feature = candidates[0]
            self.process_selected_leerrohr(nearest_feature, is_abzweigung, layer)  # layer übergeben
            # Optional: Ausprägung auch im ListWidget anzeigen (als einzelnes Item)
            item_text = self.get_leerrohr_details_text(nearest_feature)
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, nearest_feature)  # Feature speichern (falls nötig)
            self.ui.listWidget_Leerrohr.addItem(item)
        else:
            # Mehrere: Liste befüllen, Hinweis im Label
            for feature in candidates:
                item_text = self.get_leerrohr_details_text(feature)
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, feature)  # Feature für Handler speichern
                self.ui.listWidget_Leerrohr.addItem(item)
            self.ui.label_gewaehltes_leerrohr.setText("Bitte in der Liste das korrekte Leerrohr wählen")
            self.ui.label_gewaehltes_leerrohr.setStyleSheet("background-color: yellow;")  # Signalisiere Auswahl

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def process_selected_leerrohr(self, feature, is_abzweigung, layer):
        """Verarbeitet ein ausgewähltes Leerrohr-Feature und updated UI-Elemente."""
        import time
        start_time = time.time()
        leerrohr_id = feature.attribute("id")
        fields = feature.fields()
        if is_abzweigung:
            self.selected_leerrohr = {
                "id": leerrohr_id,
                "ID_TRASSE": feature.attribute("ID_TRASSE"),
                "VONKNOTEN": feature.attribute("VONKNOTEN"),
                "NACHKNOTEN": feature.attribute("NACHKNOTEN"),
                "TYP": feature.attribute("TYP"),
                "SUBTYP": feature.attribute("SUBTYP"),
                "CODIERUNG": feature.attribute("CODIERUNG"),
                "VERBUNDNUMMER": None,
                "GEFOERDERT": False,
                "SUBDUCT": False,
                "PARENT_LEERROHR_ID": feature.attribute("ID_PARENT_LEERROHR"),
                "FIRMA_HERSTELLER": None,
                "KOMMENTAR": None,
                "BESCHREIBUNG": None,
                "VERLEGT_AM": None,
                "COUNT": feature.attribute("COUNT"),
                "STATUS": feature.attribute("STATUS"),
                "VKG_LR": feature.attribute("VKG_LR")
            }
            self.ui.label_gewaehltes_leerrohr.setText(f"Abzweigung ID: {leerrohr_id} (ausgewählt)")
        else:
            self.selected_leerrohr = {
                "id": leerrohr_id,
                "ID_TRASSE": feature.attribute("ID_TRASSE"),
                "VONKNOTEN": feature.attribute("VONKNOTEN"),
                "NACHKNOTEN": feature.attribute("NACHKNOTEN"),
                "TYP": feature.attribute("TYP"),
                "SUBTYP": feature.attribute("SUBTYP"),
                "CODIERUNG": feature.attribute("CODIERUNG"),
                "VERBUNDNUMMER": feature.attribute("VERBUNDNUMMER"),
                "GEFOERDERT": feature.attribute("GEFOERDERT") if fields.indexFromName("GEFOERDERT") != -1 else False,
                "SUBDUCT": feature.attribute("SUBDUCT") if fields.indexFromName("SUBDUCT") != -1 else False,
                "PARENT_LEERROHR_ID": feature.attribute("PARENT_LEERROHR_ID"),
                "FIRMA_HERSTELLER": feature.attribute("FIRMA_HERSTELLER"),
                "KOMMENTAR": feature.attribute("KOMMENTAR"),
                "BESCHREIBUNG": feature.attribute("BESCHREIBUNG"),
                "VERLEGT_AM": feature.attribute("VERLEGT_AM"),
                "COUNT": feature.attribute("COUNT"),
                "STATUS": feature.attribute("STATUS"),
                "VKG_LR": feature.attribute("VKG_LR")
            }
            self.ui.label_gewaehltes_leerrohr.setText(f"Leerrohr ID: {leerrohr_id} (ausgewählt)")

        self.selected_verteiler = feature.attribute("VONKNOTEN")
        self.selected_verteiler_2 = feature.attribute("NACHKNOTEN")
        self.ui.label_gewaehlter_verteiler.setText(f"Startknoten: {self.selected_verteiler}")
        self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightgreen;")
        self.ui.label_gewaehlter_verteiler_2.setText(f"Endknoten: {self.selected_verteiler_2}")
        self.ui.label_gewaehltes_leerrohr.setStyleSheet("background-color: lightgreen;")
        self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightgreen;")
        print(f"DEBUG: Basis-Setup Zeit: {time.time() - start_time:.2f} Sekunden")

        # Layer refresh
        layer.dataProvider().reloadData()
        print("DEBUG: Layer refreshed")

        # DB-Abfragen
        db_start = time.time()
        count_db = feature.attribute("COUNT") or 0
        status_db = feature.attribute("STATUS") or 1
        verbundnummer_db = str(feature.attribute("VERBUNDNUMMER") or "0") if not is_abzweigung else None
        typ_db = feature.attribute("TYP") or None
        try:
            print(f"DEBUG: DB-Abfrage für ID: {leerrohr_id}, Layer-COUNT: {count_db}")

            if self.cur:
                if is_abzweigung:
                    self.cur.execute("""
                        SELECT "TYP", "COUNT", "STATUS"
                        FROM lwl."LWL_Leerrohr_Abzweigung"
                        WHERE "id" = %s
                    """, (leerrohr_id,))
                    result = self.cur.fetchone()
                    if result:
                        typ_db, count_db_temp, status_db = result
                        count_db = int(count_db_temp or count_db)
                        status_db = int(status_db or status_db)
                        print(f"DEBUG: DB Abzweigung - COUNT: {count_db}")
                else:
                    self.cur.execute("""
                        SELECT "TYP", "VERBUNDNUMMER", "COUNT", "STATUS"
                        FROM lwl."LWL_Leerrohr"
                        WHERE "id" = %s
                    """, (leerrohr_id,))
                    result = self.cur.fetchone()
                    if result:
                        typ_db, verbundnummer_db_temp, count_db_temp, status_db = result
                        verbundnummer_db = str(verbundnummer_db_temp or verbundnummer_db)
                        count_db = int(count_db_temp or count_db)
                        status_db = int(status_db or status_db)
                        print(f"DEBUG: DB Leerrohr - COUNT: {count_db}, Verbund: {verbundnummer_db}")
        except Exception as e:
            print(f"DEBUG: DB-Error: {e} – Verwende Layer-Fallback")

        # ComboBox-Ver bundnummer
        self.ui.comboBox_Verbundnummer.clear()
        if not is_abzweigung and typ_db == 3:
            self.ui.comboBox_Verbundnummer.setEnabled(True)
            # Dein Füll-Code für verwendete_nummern
            self.ui.comboBox_Verbundnummer.setCurrentText(verbundnummer_db)
        else:
            self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")
            self.ui.comboBox_Verbundnummer.setCurrentText("Deaktiviert")
            self.ui.comboBox_Verbundnummer.setEnabled(False)

        # ComboBox-Count
        self.ui.comboBox_Countwert.clear()
        for count_value in range(16):
            self.ui.comboBox_Countwert.addItem(str(count_value))
        self.ui.comboBox_Countwert.setCurrentText(str(count_db))
        self.ui.comboBox_Countwert.setEnabled(True)
        print(f"DEBUG: COUNT gesetzt: {count_db}")

        # Status
        self.populate_status(status_db)

        print(f"DEBUG: DB-Zeit: {time.time() - db_start:.2f} Sekunden")

        # UI-Updates
        self.ui.checkBox_Foerderung.setChecked(self.selected_leerrohr.get("GEFOERDERT", False))
        self.ui.checkBox_Subduct.setChecked(self.selected_leerrohr.get("SUBDUCT", False))
        self.ui.label_Kommentar.setText(self.selected_leerrohr.get("KOMMENTAR", "") or "")
        self.ui.label_Kommentar_2.setText(self.selected_leerrohr.get("BESCHREIBUNG", "") or "")
        try:
            verlegt_am = self.selected_leerrohr.get("VERLEGT_AM")
            if verlegt_am:
                if isinstance(verlegt_am, QDate):
                    self.ui.mDateTimeEdit_Strecke.setDate(verlegt_am)
                else:
                    self.ui.mDateTimeEdit_Strecke.setDate(QDate.fromString(str(verlegt_am), "yyyy-MM-dd"))
            else:
                self.ui.mDateTimeEdit_Strecke.setDate(QDate.currentDate())
        except Exception as e:
            print(f"DEBUG: Datum-Error: {e}")
            self.ui.mDateTimeEdit_Strecke.setDate(QDate.currentDate())

        # Subtyp-Auswahl
        for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
            list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                if int(item.text().split(" - ")[0]) == self.selected_leerrohr["SUBTYP"]:
                    item.setSelected(True)
                else:
                    item.setSelected(False)

        # Subtyp-Anzeige
        self.update_selected_leerrohr_subtyp()

        # Highlights
        layer.dataProvider().reloadData()
        if self.leerrohr_highlight:
            self.leerrohr_highlight.hide()
        self.leerrohr_highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), layer)
        self.leerrohr_highlight.setColor(Qt.magenta)
        self.leerrohr_highlight.setWidth(5)
        self.leerrohr_highlight.show()
        self.iface.mapCanvas().redrawAllLayers()

        # Knoten-Highlights
        knot_layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
        knot_layer.dataProvider().reloadData()
        request_start = QgsFeatureRequest().setFilterExpression(f'"id" = {self.selected_verteiler}')
        start_feature = next(knot_layer.getFeatures(request_start), None)
        if start_feature:
            if self.verteiler_highlight_1:
                self.verteiler_highlight_1.hide()
            self.verteiler_highlight_1 = QgsHighlight(self.iface.mapCanvas(), start_feature.geometry(), knot_layer)
            self.verteiler_highlight_1.setColor(Qt.red)
            self.verteiler_highlight_1.setWidth(5)
            self.verteiler_highlight_1.show()

        request_end = QgsFeatureRequest().setFilterExpression(f'"id" = {self.selected_verteiler_2}')
        end_feature = next(knot_layer.getFeatures(request_end), None)
        if end_feature:
            if self.verteiler_highlight_2:
                self.verteiler_highlight_2.hide()
            self.verteiler_highlight_2 = QgsHighlight(self.iface.mapCanvas(), end_feature.geometry(), knot_layer)
            self.verteiler_highlight_2.setColor(Qt.red)
            self.verteiler_highlight_2.setWidth(5)
            self.verteiler_highlight_2.show()

        self.iface.mapCanvas().redrawAllLayers()

        # KEIN Setzen von Update-Button hier – nur in pruefe_daten
        self.ui.pushButton_update_leerrohr.setEnabled(False)
        self.ui.pushButton_Import.setEnabled(False)
        print("DEBUG: Buttons deaktiviert – Update nur nach Prüfung")

    def get_leerrohr_details_text(self, feature):
        """Erzeugt den Text für das ListWidget-Item aus dem Feature."""
        fields = feature.fields()
        leerrohr_id = feature.attribute("id") or "N/A"
        typ = feature.attribute("TYP") or "N/A"
        subtyp = feature.attribute("SUBTYP") or "N/A"
        codierung = feature.attribute("CODIERUNG") or "N/A"
        verbundnummer = feature.attribute("VERBUNDNUMMER") if fields.indexFromName("VERBUNDNUMMER") != -1 else "N/A"
        count = feature.attribute("COUNT") or "N/A"
        status = feature.attribute("STATUS") or "N/A"
        return f"ID: {leerrohr_id} - TYP: {typ} - SUBTYP: {subtyp} - Code: {codierung} - V: {verbundnummer} - COUNT: {count} - STATUS: {status}"
    
    def populate_status(self, current_status_id=None):
        """Füllt das comboBox_Status mit Werten aus LUT_Status und setzt den aktuellen Status."""
        print("DEBUG: Starte populate_status")
        self.ui.comboBox_Status.clear()
        try:
            with psycopg2.connect(**self.db_details) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT "id", "STATUS"
                        FROM lwl."LUT_Status"
                        ORDER BY "id"
                    """)
                    status_options = cur.fetchall()
                    for status_id, status_text in status_options:
                        self.ui.comboBox_Status.addItem(status_text, status_id)
                    if current_status_id is not None:
                        index = self.ui.comboBox_Status.findData(current_status_id)
                        if index != -1:
                            self.ui.comboBox_Status.setCurrentIndex(index)
                            print(f"DEBUG: STATUS gesetzt auf: {status_text} (ID: {current_status_id})")
                        else:
                            print(f"DEBUG: Kein passender STATUS für ID {current_status_id} gefunden, setze auf ersten Wert")
                            self.ui.comboBox_Status.setCurrentIndex(0)
                    else:
                        self.ui.comboBox_Status.setCurrentIndex(0)  # Fallback auf ersten Wert
        except Exception as e:
            print(f"DEBUG: Fehler beim Laden der Status-Werte: {e}")
            self.ui.comboBox_Status.addItem("Fehler beim Laden")
            self.ui.comboBox_Status.setCurrentIndex(0)

    def start_routing(self):
        """Startet das Routing und hebt bis zu 3 berechnete Routen hervor, berücksichtigt optional einen Zwischenknoten im Hauptstrangmodus."""
        print(f"DEBUG: Starte Routing – selected_verteiler: {self.selected_verteiler}, selected_zwischenknoten: {self.selected_zwischenknoten}, selected_verteiler_2: {self.selected_verteiler_2}")
        
        # Lösche bestehende Highlights
        if self.route_highlights:
            for highlight in self.route_highlights:
                highlight.setVisible(False)
            self.route_highlights.clear()
        self.selected_trasse_ids = []
        self.selected_trasse_ids_flat = []
        self.routes_by_path_id = {}

        is_abzweigung = self.ui.radioButton_Abzweigung.isChecked()
        if is_abzweigung:
            if not (self.selected_parent_leerrohr and self.selected_verteiler and self.selected_verteiler_2):
                self._set_status("Bitte wähle Parent-Leerrohr, Start- und Endknoten der Abzweigung aus!", error=True)
                return
            start_id = self.selected_verteiler
            end_id = self.selected_verteiler_2
            parent_id = self.selected_parent_leerrohr["id"]
            parent_vkg_lr = self.selected_parent_leerrohr.get("VKG_LR", self.selected_parent_leerrohr.get("VONKNOTEN"))
            parent_endknoten = self.selected_parent_leerrohr.get("NACHKNOTEN")
            if start_id in (parent_vkg_lr, parent_endknoten):
                self._set_status("Der Startknoten der Abzweigung darf nicht Start- oder Endknoten des Parent-Leerrohrs sein!", error=True)
                return
            trassen_ids = self.selected_parent_leerrohr["ID_TRASSE"]
            try:
                with psycopg2.connect(**self.db_details) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT COUNT(*) 
                            FROM lwl."LWL_Trasse" 
                            WHERE id = ANY(%s)
                            AND ("VONKNOTEN" = %s OR "NACHKNOTEN" = %s)
                        """, (trassen_ids, start_id, start_id))
                        if not cur.fetchone()[0]:
                            self._set_status("Der Startknoten der Abzweigung muss auf einer Trasse des Parent-Leerrohrs liegen!", error=True)
                            return
                        # Validierung der Knoten-Typen
                        cur.execute("SELECT \"TYP\" FROM lwl.\"LWL_Knoten\" WHERE id = %s", (start_id,))
                        typ = cur.fetchone()
                        if not typ or typ[0] not in ["Verteilerkasten", "Schacht", "Ortszentrale"]:
                            self._set_status("Der Startknoten der Abzweigung muss ein Verteiler, Schacht oder eine Ortszentrale sein!", error=True)
                            return
                        cur.execute("SELECT \"TYP\" FROM lwl.\"LWL_Knoten\" WHERE id = %s", (end_id,))
                        typ = cur.fetchone()
                        if typ and typ[0] == "Virtueller Knoten":
                            self._set_status("Der Endknoten der Abzweigung darf kein virtueller Knoten sein!", error=True)
                            return
            except Exception as e:
                self._set_status(f"Fehler bei der Validierung des Startknotens: {e}", error=True)
                return
        else:
            if not (self.selected_verteiler and self.selected_verteiler_2):
                self._set_status("Bitte wähle Start- und Endknoten aus!", error=True)
                return
            start_id = self.selected_verteiler
            end_id = self.selected_verteiler_2
            zwischenknoten_id = self.selected_zwischenknoten
            # Prüfen, ob Zwischenknoten weder Start- noch Endknoten ist (nur im Hauptstrangmodus)
            if zwischenknoten_id:
                if zwischenknoten_id == start_id or zwischenknoten_id == end_id:
                    self._set_status("Zwischenknoten darf nicht Start- oder Endknoten sein!", error=True)
                    self.selected_zwischenknoten = None
                    self.ui.label_gewaehlter_zwischenknoten.setText("Zwischenknoten ungültig!")
                    self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: lightcoral;")
                    if self.zwischenknoten_highlight:
                        self.zwischenknoten_highlight.hide()
                        self.zwischenknoten_highlight = None
                    return
            try:
                with psycopg2.connect(**self.db_details) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT \"TYP\" FROM lwl.\"LWL_Knoten\" WHERE id = %s", (start_id,))
                        typ = cur.fetchone()
                        if not typ or typ[0] not in ["Verteilerkasten", "Schacht", "Ortszentrale"]:
                            self._set_status("Der Startknoten des Hauptstrangs muss ein Verteiler, Schacht oder eine Ortszentrale sein!", error=True)
                            return
                        cur.execute("SELECT \"TYP\" FROM lwl.\"LWL_Knoten\" WHERE id = %s", (end_id,))
                        typ = cur.fetchone()
                        if typ and typ[0] == "Virtueller Knoten":
                            self._set_status("Der Endknoten des Hauptstrangs darf kein virtueller Knoten sein!", error=True)
                            return
                        if zwischenknoten_id:
                            cur.execute("SELECT \"TYP\" FROM lwl.\"LWL_Knoten\" WHERE id = %s", (zwischenknoten_id,))
                            typ = cur.fetchone()
                            if not typ:
                                self._set_status("Zwischenknoten nicht gefunden!", error=True)
                                return
            except Exception as e:
                self._set_status(f"Fehler bei der Validierung der Knoten: {e}", error=True)
                return

        try:
            start_id = int(start_id)
            end_id = int(end_id)
            # Konvertiere zwischenknoten_id nur im Hauptstrangmodus
            if not is_abzweigung and zwischenknoten_id:
                zwischenknoten_id = int(zwischenknoten_id)
        except ValueError:
            self._set_status("Knoten-IDs müssen Zahlen sein!", error=True)
            return

        routes = {}
        if is_abzweigung:
            trassen_ids = list(self.selected_parent_leerrohr["ID_TRASSE"])
            sql = """
                SELECT seq, path_id, edge AS trasse_id
                FROM pgr_ksp(
                    'SELECT id, "VONKNOTEN" AS source, "NACHKNOTEN" AS target, "LAENGE" AS cost 
                    FROM lwl."LWL_Trasse" 
                    WHERE "LAENGE" IS NOT NULL AND "LAENGE" > 0 
                    AND id != ALL(%s)',
                    %s, %s,
                    3,
                    false
                )
            """
            params = (trassen_ids, start_id, end_id)
            try:
                with psycopg2.connect(**self.db_details) as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, params)
                        result = cur.fetchall()
            except Exception as e:
                self._set_status(f"Datenbankfehler: {e}", error=True)
                return
            for seq, path_id, trasse_id in result:
                if path_id not in routes:
                    routes[path_id] = []
                if trasse_id is not None and trasse_id != -1:
                    routes[path_id].append(trasse_id)
        else:
            if zwischenknoten_id:
                # Routing in zwei Schritten: Start -> Zwischenknoten, Zwischenknoten -> Ende
                routes_step1 = {}
                routes_step2 = {}
                sql = """
                    SELECT seq, path_id, edge AS trasse_id
                    FROM pgr_ksp(
                        'SELECT id, "VONKNOTEN" AS source, "NACHKNOTEN" AS target, "LAENGE" AS cost 
                        FROM lwl."LWL_Trasse"',
                        %s, %s,
                        3,
                        false
                    )
                """
                # Schritt 1: Start -> Zwischenknoten
                try:
                    with psycopg2.connect(**self.db_details) as conn:
                        with conn.cursor() as cur:
                            cur.execute(sql, (start_id, zwischenknoten_id))
                            result = cur.fetchall()
                    for seq, path_id, trasse_id in result:
                        if path_id not in routes_step1:
                            routes_step1[path_id] = []
                        if trasse_id is not None and trasse_id != -1:
                            routes_step1[path_id].append(trasse_id)
                except Exception as e:
                    self._set_status(f"Datenbankfehler (Start -> Zwischenknoten): {e}", error=True)
                    return
                # Schritt 2: Zwischenknoten -> Ende
                try:
                    with psycopg2.connect(**self.db_details) as conn:
                        with conn.cursor() as cur:
                            cur.execute(sql, (zwischenknoten_id, end_id))
                            result = cur.fetchall()
                    for seq, path_id, trasse_id in result:
                        if path_id not in routes_step2:
                            routes_step2[path_id] = []
                        if trasse_id is not None and trasse_id != -1:
                            routes_step2[path_id].append(trasse_id)
                except Exception as e:
                    self._set_status(f"Datenbankfehler (Zwischenknoten -> Ende): {e}", error=True)
                    return
                # Kombiniere die Routen
                for path_id1 in routes_step1:
                    for path_id2 in routes_step2:
                        combined_path_id = len(routes) + 1
                        if combined_path_id <= 3:  # Begrenze auf 3 kombinierte Routen
                            routes[combined_path_id] = routes_step1[path_id1] + routes_step2[path_id2]
            else:
                # Standard-Routing ohne Zwischenknoten
                sql = """
                    SELECT seq, path_id, edge AS trasse_id
                    FROM pgr_ksp(
                        'SELECT id, "VONKNOTEN" AS source, "NACHKNOTEN" AS target, "LAENGE" AS cost 
                        FROM lwl."LWL_Trasse"',
                        %s, %s,
                        3,
                        false
                    )
                """
                params = (start_id, end_id)
                try:
                    with psycopg2.connect(**self.db_details) as conn:
                        with conn.cursor() as cur:
                            cur.execute(sql, params)
                            result = cur.fetchall()
                    for seq, path_id, trasse_id in result:
                        if path_id not in routes:
                            routes[path_id] = []
                        if trasse_id is not None and trasse_id != -1:
                            routes[path_id].append(trasse_id)
                except Exception as e:
                    self._set_status(f"Datenbankfehler: {e}", error=True)
                    return

        if not routes:
            self._set_status("Kein Pfad gefunden! Möglicherweise gibt es keine Route.", error=True)
            return

        self.routes_by_path_id = routes
        self.selected_trasse_ids = list(routes.values())
        self.selected_trasse_ids_flat = routes.get(1, [])  # Standardmäßig erste Route wählen
        self.highlight_multiple_routes(list(routes.values()))

        self.update_route_view()

        if len(routes) > 1:
            self._set_status("Wählen Sie eine Route aus den hervorgehobenen Pfaden oder im Route-View!")
        else:
            self._set_status("Route berechnet – Import möglich!")

    def _set_status(self, text, error=False):
        """Setzt den Status-Text und die Farbe des Status-Labels."""
        color = "lightcoral" if error else "lightgreen"
        text_color = "white" if error else "black"
        self.ui.label_Status.setText(text)
        self.ui.label_Status.setStyleSheet(f"background-color: {color}; color: {text_color}; font-weight: bold; padding: 5px;")
        print(f"DEBUG: Status gesetzt: {text}")

    def highlight_multiple_routes(self, routes):
        """Hebt eine oder mehrere Routen in unterschiedlichen Farben in QGIS hervor."""
        print(f"DEBUG: Anzahl der Routen zum Highlighten: {len(routes)}")
        if self.route_highlights:
            for highlight in self.route_highlights:
                highlight.setVisible(False)
            self.route_highlights.clear()

        layer_list = QgsProject.instance().mapLayersByName("LWL_Trasse")
        if not layer_list:
            print("⚠ Fehler: Der Layer 'LWL_Trasse' wurde nicht gefunden!")
            return

        trasse_layer = layer_list[0]
        if len(routes) == 1:
            color = QColor(255, 0, 0, 150)
            for trassen_id in routes[0]:
                request = QgsFeatureRequest().setFilterExpression(f'"id" = {trassen_id}')
                for feature in trasse_layer.getFeatures(request):
                    highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), trasse_layer)
                    highlight.setColor(color)
                    highlight.setWidth(10)
                    highlight.show()
                    self.route_highlights.append(highlight)
        else:
            colors = [QColor(255, 0, 0, 150), QColor(0, 0, 255, 150), QColor(0, 255, 0, 150)]
            for i, route in enumerate(routes):
                for trassen_id in route:
                    request = QgsFeatureRequest().setFilterExpression(f'"id" = {trassen_id}')
                    for feature in trasse_layer.getFeatures(request):
                        highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), trasse_layer)
                        highlight.setColor(colors[i % len(colors)])
                        highlight.setWidth(10)
                        highlight.show()
                        self.route_highlights.append(highlight)

        print(f"DEBUG: {len(self.route_highlights)} Highlights gesetzt")

    def activate_route_selection(self):
        """Aktiviert das MapTool zur Routenauswahl."""
        print("DEBUG: Aktiviere MapTool zur Routenauswahl")

        class RouteSelectionTool(QgsMapToolEmitPoint):
            def __init__(self, tool):
                self.tool = tool
                super().__init__(tool.iface.mapCanvas())
                self.routes_by_path_id = tool.routes_by_path_id

            def canvasReleaseEvent(self, event):
                point = event.mapPoint()
                layer = QgsProject.instance().mapLayersByName("LWL_Trasse")[0]
                for feature in layer.getFeatures():
                    if feature.geometry().distance(QgsGeometry.fromPointXY(point)) < 1:
                        trassen_id = feature["id"]
                        for path_id, route in self.routes_by_path_id.items():
                            if trassen_id in route:
                                self.tool.selected_trasse_ids = [route]
                                self.tool.selected_trasse_ids_flat = route
                                self.tool.highlight_selected_route()
                                self.tool.iface.mapCanvas().unsetMapTool(self)
                                self.tool.ui.label_Status.setText(f"Route {path_id} ausgewählt – Import möglich!")
                                self.tool.ui.label_Status.setStyleSheet("background-color: lightgreen; color: black; font-weight: bold; padding: 5px;")
                                print(f"DEBUG: Gewählte Route: {self.tool.selected_trasse_ids_flat}")
                                self.tool.update_route_view_selection()
                                return
                        break
                self.tool.ui.label_Status.setText("Kein gültiger Pfad ausgewählt!")
                self.tool.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")

        self.map_tool = RouteSelectionTool(self)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def highlight_selected_route(self):
        """Hebt die ausgewählte Route hervor."""
        print(f"DEBUG: Hebe ausgewählte Route hervor – selected_trasse_ids: {self.selected_trasse_ids}")
        if self.route_highlights:
            for highlight in self.route_highlights:
                highlight.setVisible(False)
            self.route_highlights.clear()

        layer_list = QgsProject.instance().mapLayersByName("LWL_Trasse")
        if not layer_list:
            print("⚠ Fehler: Der Layer 'LWL_Trasse' wurde nicht gefunden!")
            return

        trasse_layer = layer_list[0]
        colors = {1: QColor(255, 0, 0, 150), 2: QColor(0, 0, 255, 150), 3: QColor(0, 255, 0, 150)}
        path_id = None
        for pid, route in self.routes_by_path_id.items():
            if route == self.selected_trasse_ids[0]:
                path_id = pid
                break
        if path_id is None:
            path_id = 1
        color = colors.get(path_id, QColor(255, 0, 0, 150))

        for trassen_id in self.selected_trasse_ids[0]:
            request = QgsFeatureRequest().setFilterExpression(f'"id" = {trassen_id}')
            for feature in trasse_layer.getFeatures(request):
                highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), trasse_layer)
                highlight.setColor(color)
                highlight.setWidth(10)
                highlight.show()
                self.route_highlights.append(highlight)

        self.update_route_view_selection()

    def update_route_view(self):
        """Aktualisiert die Darstellung der Routen im graphicsView_Auswahl_Route."""
        print("DEBUG: Starte update_route_view")
        
        # Lösche die bestehende Szene im graphicsView_Auswahl_Route
        if self.ui.graphicsView_Auswahl_Route.scene():
            self.ui.graphicsView_Auswahl_Route.scene().clear()
        else:
            self.ui.graphicsView_Auswahl_Route.setScene(QGraphicsScene())

        scene = self.ui.graphicsView_Auswahl_Route.scene()
        # Setze die Szenegröße auf die maximale Breite für 6 Routen + Abstände
        scene.setSceneRect(0, 0, 241, 109)  # Anpassung für initialen 15px Offset

        x_offset = 0  # Startpunkt des ersten Kästchens
        rect_size = 98  # Quadratische Größe (109*109 Pixel)
        colors = [QColor(255, 0, 0), QColor(0, 0, 255), QColor(0, 255, 0)]  # Vollfarben für bis zu 3 Routen

        self.route_rects = {}  # Speichert die Vierecke mit ihren path_ids

        for index, (path_id, route) in enumerate(self.routes_by_path_id.items()):
            if path_id <= len(colors):  # Begrenze auf 6 Routen
                color = colors[path_id - 1]
                # Setze x_offset für das erste Kästchen auf 0, für weitere auf Basis des vorherigen
                if index == 0:
                    current_x_offset = 11
                else:
                    current_x_offset = x_offset + rect_size + 12  # 5px Abstand nach dem ersten
                rect = QGraphicsRectItem(current_x_offset, 5, rect_size, rect_size)  # Quadratisch: 30x30
                rect.setBrush(QBrush(color))
                rect.setPen(QPen(color, 0))  # 1px Rahmen in gleicher Farbe
                rect.setZValue(1)
                scene.addItem(rect)

                # Füge Text mit der Route-Nummer hinzu
                text = scene.addText(f"{path_id}")
                text.setPos(current_x_offset + (rect_size - text.boundingRect().width()) / 2 -4, -8 + (rect_size - text.boundingRect().height()) / 2)
                text.setDefaultTextColor(Qt.black)  # Kontrastierende Farbe für Lesbarkeit
                text.setZValue(2)
                font = QFont()
                font.setPointSize(24)  # Passe die Größe hier an
                text.setFont(font)
                scene.addItem(text)

                # Mache das Viereck klickbar
                rect.setAcceptHoverEvents(True)
                rect.setFlag(QGraphicsRectItem.ItemIsSelectable, True)
                rect.path_id = path_id
                rect.mousePressEvent = lambda event, p_id=path_id: self.route_rect_clicked(p_id)

                self.route_rects[path_id] = rect
                x_offset = current_x_offset  # Aktualisiere x_offset für die nächste Iteration

        self.ui.graphicsView_Auswahl_Route.setScene(scene)
        self.ui.graphicsView_Auswahl_Route.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # Kein horizontales Scrollen
        self.ui.graphicsView_Auswahl_Route.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # Keine vertikale Scrollbar nötig
        self.update_route_view_selection()  # Aktualisiere die Auswahl sofort

    def route_rect_clicked(self, path_id):
        """Wird aufgerufen, wenn ein Route-Viereck geklickt wird."""
        print(f"DEBUG: Route {path_id} im Route-View geklickt")
        self.selected_trasse_ids = [self.routes_by_path_id[path_id]]
        self.selected_trasse_ids_flat = self.routes_by_path_id[path_id]
        self.highlight_selected_route()
        self.update_route_view_selection()

    def update_route_view_selection(self):
        """Aktualisiert die Darstellung im Route-View basierend auf der ausgewählten Route."""
        print("DEBUG: Starte update_route_view_selection")
        selected_path_id = None
        for pid, route in self.routes_by_path_id.items():
            if route == self.selected_trasse_ids[0]:
                selected_path_id = pid
                break

        for path_id, rect in self.route_rects.items():
            if path_id == selected_path_id:
                # Behalte die volle Farbe und den Rahmen für die gewählte Route
                original_brush = rect.brush()
                original_color = original_brush.color()
                rect.setBrush(QBrush(original_color))
                rect.setPen(QPen(original_color, 4))
            else:
                # Schwäche die Farbe der nicht gewählten Routen ab
                original_brush = rect.brush()
                grayed_color = original_brush.color()
                grayed_color.setAlpha(100)  # 50% Transparenz für abgeschwächte Darstellung
                rect.setBrush(QBrush(grayed_color))
                rect.setPen(QPen(Qt.gray, 1))  # Grauer Rahmen für nicht gewählte Routen

    def populate_verbundnummer(self):
        """Setzt die Verbundnummer basierend auf den ausgewählten Subtypen."""
        print("DEBUG: Starte populate_verbundnummer")
        self.ui.comboBox_Verbundnummer.clear()
        selected_subtyp_ids = []
        is_multirohr = False
        for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
            for item in list_widget.selectedItems():
                try:
                    subtyp_id = int(item.text().split(" - ")[0])
                    typ = int(item.text().split(" - ")[1])
                    selected_subtyp_ids.append((subtyp_id, typ))
                    if typ == 3:
                        is_multirohr = True
                except ValueError:
                    continue

        if not is_multirohr:
            self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")
            self.ui.comboBox_Verbundnummer.setCurrentIndex(0)
            self.ui.comboBox_Verbundnummer.setEnabled(False)
            print("DEBUG: Kein Multirohr ausgewählt, Verbundnummer auf 'Deaktiviert' gesetzt")
            return

        self.ui.comboBox_Verbundnummer.setEnabled(True)
        try:
            with psycopg2.connect(**self.db_details) as conn:
                with conn.cursor() as cur:
                    # Wenn ein Leerrohr ausgewählt ist, dessen Verbundnummer berücksichtigen
                    exclude_id = self.selected_leerrohr["id"] if self.selected_leerrohr else None
                    verbundnummer_db = str(self.selected_leerrohr["VERBUNDNUMMER"]) if self.selected_leerrohr and self.selected_leerrohr["VERBUNDNUMMER"] is not None else None
                    print(f"DEBUG: Ausgewähltes Leerrohr ID: {exclude_id}, Verbundnummer: {verbundnummer_db}")

                    # Ermittle verwendete Verbundnummern basierend auf VKG_LR (Startknoten)
                    startknoten = self.selected_verteiler if self.selected_verteiler else None
                    if startknoten:
                        if exclude_id:
                            cur.execute("""
                                SELECT DISTINCT "VERBUNDNUMMER"
                                FROM lwl."LWL_Leerrohr"
                                WHERE "TYP" = 3 
                                AND "VKG_LR" = %s
                                AND "VERBUNDNUMMER" IS NOT NULL
                                AND "id" != %s
                            """, (startknoten, exclude_id))
                        else:
                            cur.execute("""
                                SELECT DISTINCT "VERBUNDNUMMER"
                                FROM lwl."LWL_Leerrohr"
                                WHERE "TYP" = 3 
                                AND "VKG_LR" = %s
                                AND "VERBUNDNUMMER" IS NOT NULL
                            """, (startknoten,))
                    else:
                        # Fallback, wenn kein Startknoten ausgewählt ist
                        if exclude_id:
                            cur.execute("""
                                SELECT DISTINCT "VERBUNDNUMMER"
                                FROM lwl."LWL_Leerrohr"
                                WHERE "TYP" = 3 
                                AND "VERBUNDNUMMER" IS NOT NULL
                                AND "id" != %s
                            """, (exclude_id,))
                        else:
                            cur.execute("""
                                SELECT DISTINCT "VERBUNDNUMMER"
                                FROM lwl."LWL_Leerrohr"
                                WHERE "TYP" = 3 
                                AND "VERBUNDNUMMER" IS NOT NULL
                            """)

                    verwendete_nummern = {int(row[0]) for row in cur.fetchall() if row[0] is not None}
                    max_nummer = max(verwendete_nummern, default=0)
                    print(f"DEBUG: Verwendete Verbundnummern: {verwendete_nummern}, Max Nummer: {max_nummer}")

                    # Fülle das Dropdown mit verfügbaren Verbundnummern
                    freie_nummern = []
                    for nummer in range(1, max_nummer + 11):
                        self.ui.comboBox_Verbundnummer.addItem(str(nummer))
                        if nummer in verwendete_nummern:
                            index = self.ui.comboBox_Verbundnummer.count() - 1
                            self.ui.comboBox_Verbundnummer.model().item(index).setEnabled(False)
                        else:
                            freie_nummern.append(nummer)

                    # Setze die Verbundnummer basierend auf dem Kontext
                    if self.selected_leerrohr and verbundnummer_db and verbundnummer_db.isdigit():
                        # Für ausgewählte Leerrohre: Setze die aktuelle Verbundnummer
                        self.ui.comboBox_Verbundnummer.setCurrentText(verbundnummer_db)
                        print(f"DEBUG: Verbundnummer für ausgewähltes Leerrohr gesetzt: {verbundnummer_db}")
                    else:
                        # Für neuen Import: Wähle die erste freie Verbundnummer
                        freie_nummer = freie_nummern[0] if freie_nummern else max_nummer + 1
                        self.ui.comboBox_Verbundnummer.setCurrentText(str(freie_nummer))
                        print(f"DEBUG: Erste freie Verbundnummer für Import gesetzt: {freie_nummer}")

                    # Bei parallelem Import: Stelle sicher, dass nachfolgende Multirohre die nächsten freien Nummern erhalten
                    if len([t for _, t in selected_subtyp_ids if t == 3]) > 1:
                        print(f"DEBUG: Paralleler Import von {len([t for _, t in selected_subtyp_ids if t == 3])} Multirohren")
                        for i, (subtyp_id, typ) in enumerate(selected_subtyp_ids):
                            if typ == 3 and i > 0:  # Für nachfolgende Multirohre
                                next_freie_nummer = next((n for n in freie_nummern if n > int(self.ui.comboBox_Verbundnummer.currentText())), max_nummer + i + 1)
                                print(f"DEBUG: Nächste freie Verbundnummer für Multirohr {i+1}: {next_freie_nummer}")
                                # Hinweis: Die Zuweisung erfolgt in importiere_daten, hier nur Logik vorbereiten

                    print(f"DEBUG: Verfügbare Verbundnummern in comboBox: {[self.ui.comboBox_Verbundnummer.itemText(i) for i in range(self.ui.comboBox_Verbundnummer.count())]}")
        except Exception as e:
            self.ui.label_Status.setText(f"Fehler beim Abrufen der Verbundnummern: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
            print(f"DEBUG: Fehler beim Abrufen der Verbundnummern: {e}")

    def populate_gefoerdert_subduct(self):
        """Setzt die CheckBoxen für 'Gefördert' und 'Subduct' auf Standardwerte."""
        print("DEBUG: Starte populate_gefoerdert_subduct")
        self.ui.checkBox_Foerderung.setChecked(False)
        self.ui.checkBox_Subduct.setChecked(False)

    def update_subduct_button(self):
        """Aktiviert oder deaktiviert den Subduct-Button und das Subduct-Label."""
        print("DEBUG: Starte update_subduct_button")
        is_subduct = self.ui.checkBox_Subduct.isChecked()
        self.ui.pushButton_subduct.setEnabled(is_subduct)
        self.ui.label_Subduct.setEnabled(is_subduct)
        self.ui.label_Subduct.setText("Hauptrohr auswählen")
        if is_subduct:
            self.ui.label_Subduct.setStyleSheet("background-color: lightcoral;")
        else:
            self.ui.label_Subduct.setStyleSheet("")

    def select_subduct_parent(self):
        """Aktiviert das Map-Tool zum Auswählen eines Subduct-Parent-Leerrohrs."""
        print("DEBUG: Starte Auswahl eines Subduct-Parent-Leerrohrs")
        self.ui.label_Subduct.clear()
        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.subduct_parent_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def subduct_parent_selected(self, point):
        """Speichert das gewählte Subduct-Parent-Leerrohr."""
        print("DEBUG: Verarbeite Auswahl des Subduct-Parent-Leerrohrs")
        layer_name = "LWL_Leerrohr"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_Subduct.setText("Layer 'LWL_Leerrohr' nicht gefunden")
            self.ui.label_Subduct.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        map_scale = self.iface.mapCanvas().scale()
        threshold_distance = 10 * (map_scale / (39.37 * 96))

        nearest_feature = None
        nearest_distance = float("inf")

        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        for feature in layer.getFeatures(request):
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        if nearest_feature and nearest_distance <= threshold_distance:
            leerrohr_id = nearest_feature["id"]
            self.selected_subduct_parent = leerrohr_id
            self.ui.label_Subduct.setText(f"Subduct-Leerrohr ID: {leerrohr_id}")
            self.ui.label_Subduct.setStyleSheet("background-color: lightgreen;")
            if hasattr(self, "subduct_highlight") and self.subduct_highlight:
                self.subduct_highlight.hide()
            self.subduct_highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), layer)
            self.subduct_highlight.setColor(Qt.cyan)
            self.subduct_highlight.setWidth(5)
            self.subduct_highlight.show()
            QgsMessageLog.logMessage(f"Subduct-Parent-Leerrohr gewählt: {leerrohr_id}", "Leerrohr-Tool", level=Qgis.Info)
        else:
            self.ui.label_Subduct.setText("Kein Leerrohr in Reichweite gefunden")
            self.ui.label_Subduct.setStyleSheet("background-color: lightcoral;")
            self.selected_subduct_parent = None

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def update_selected_leerrohr_subtyp(self):
        """Aktualisiert die Subtyp-Anzeige in der GraphicsView, inklusive Duplizieren-Button."""
        print("DEBUG: Starte update_selected_leerrohr_subtyp")
        self.subtyp_scene.clear()  # Szene leeren
        selected_subtyp_ids = []
        belegte_rohre = set()  # Für belegte Rohre, falls nötig

        for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
            for item in list_widget.selectedItems():
                try:
                    subtyp_id = int(item.text().split(" - ")[0])
                    selected_subtyp_ids.append(subtyp_id)
                except ValueError:
                    continue

        if not selected_subtyp_ids:
            self.ui.label_Status.setText("Kein Subtyp ausgewählt.")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
            return

        y_offset = 0
        square_size = 20
        font = QFont("Arial", 12, QFont.Bold)
        for subtyp_id in selected_subtyp_ids:
            rohre, subtyp_char, typ = self.parse_rohr_definition(subtyp_id)
            if not rohre:
                print(f"DEBUG: Keine Rohr-Definition für Subtyp {subtyp_id}")
                continue
            quantity = self.subtyp_quantities.get(subtyp_id, 1)
            for q in range(quantity):
                subtyp_text = self.subtyp_scene.addText(f"{subtyp_char} (Typ {typ})" if q == 0 else f"{subtyp_char} (Duplikat {q})")
                subtyp_text.setPos(0, y_offset)
                subtyp_text.setDefaultTextColor(Qt.black)
                subtyp_text.setFont(font)
                y_offset += subtyp_text.boundingRect().height() + 5
                x_offset = 0

                font.setPointSize(10)
                unique_durchmesser = set(durchmesser for _, _, durchmesser, _, _, _ in rohre)
                is_mixed = len(unique_durchmesser) > 1
                max_durchmesser = max(unique_durchmesser) if unique_durchmesser else 0
                for rohr_id, rohr_nummer, durchmesser, farbe, primary_farbcode, secondary_farbcode in rohre:
                    ist_belegt = rohr_nummer in belegte_rohre and self.selected_leerrohr
                    add_adjust = 5 if is_mixed and durchmesser == max_durchmesser else 0
                    size = square_size + add_adjust
                    x = x_offset
                    y = y_offset + (square_size - size) / 2

                    rect = QGraphicsRectItem(x, y, size, size)
                    rect.setPen(QPen(Qt.red if ist_belegt else Qt.black, 2 if ist_belegt else 1))
                    if ist_belegt:
                        rect.setToolTip(f"Rohrnummer {rohr_nummer} bereits belegt")

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

                    if ist_belegt:
                        color = QColor("#808080")
                        triangle1.setBrush(QBrush(color))
                        triangle2.setBrush(QBrush(color))
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
                    self.subtyp_scene.addItem(rect)

                    if ist_belegt:
                        line1 = QGraphicsLineItem(x, y, x + size, y + size, rect)
                        line1.setPen(QPen(Qt.red, 2))
                        line1.setZValue(2)
                        self.subtyp_scene.addItem(line1)
                        line2 = QGraphicsLineItem(x + size, y, x, y + size, rect)
                        line2.setPen(QPen(Qt.red, 2))
                        line2.setZValue(2)
                        self.subtyp_scene.addItem(line2)

                    text = self.subtyp_scene.addText(str(rohr_nummer))
                    text_center_x = x + size / 2 - text.boundingRect().width() / 2
                    text_center_y = y + size / 2 - text.boundingRect().height() / 2
                    text.setPos(text_center_x, text_center_y)
                    color = QColor("#808080" if ist_belegt else primary_farbcode)
                    brightness = (color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114)
                    text.setDefaultTextColor(Qt.black if brightness > 128 else Qt.white)
                    text.setFont(font)
                    text.setZValue(3)
                    self.subtyp_scene.addItem(text)

                    x_offset += size + 1

                y_offset += square_size + 5

                # Duplizieren-Button hinzufügen (nur im Hauptstrang-Modus)
                if not (self.ui.radioButton_Abzweigung.isChecked() or self.selected_leerrohr):
                    button = self.DuplicateButtonItem(subtyp_id, self)
                    button.setPos(x_offset + 10, y_offset - square_size - 10)
                    self.subtyp_scene.addItem(button)
                    print(f"DEBUG: Duplizieren-Button für Subtyp {subtyp_id} hinzugefügt")

        self.subtyp_scene.setSceneRect(0, 0, 491, y_offset + 20)
        self.ui.graphicsView_Auswahl_Subtyp.setScene(self.subtyp_scene)
        self.ui.label_Status.setText("Subtypen erfolgreich angezeigt.")
        self.ui.label_Status.setStyleSheet("background-color: lightgreen;")
        self.update_combobox_states()

    def parse_rohr_definition(self, subtyp_id):
        print(f"DEBUG: Parsing ROHR_DEFINITION für Subtyp-ID: {subtyp_id}")
        try:
            with psycopg2.connect(**self.db_details) as conn:
                with conn.cursor() as cur:
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
                        durchmesser = int(group.get("durchmesser", 0))  # Hole Durchmesser pro Gruppe
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
                                # Fallback grau
                                farbe = "grau"
                                primary_farbcode = "#808080"
                                secondary_farbcode = None
                                rohre.append((rohr_nummer, rohr_nummer, durchmesser, farbe, primary_farbcode, secondary_farbcode))
                            rohr_nummer += 1
                    return rohre, subtyp_char, typ
        except Exception as e:
            print(f"DEBUG: Fehler: {e}")
            return [], None, None

    def update_combobox_states(self):
        """Aktiviert oder deaktiviert comboBox_Verbundnummer, comboBox_Countwert und comboBox_Status basierend auf den Subtypen und dem Modus."""
        print("DEBUG: Starte update_combobox_states")
        is_multirohr = False
        for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
            for item in list_widget.selectedItems():
                try:
                    typ = int(item.text().split(" - ")[1])
                    if typ == 3:
                        is_multirohr = True
                        break
                except ValueError:
                    continue
                if is_multirohr:
                    break

        if is_multirohr:
            self.ui.comboBox_Verbundnummer.setEnabled(True)
            self.populate_verbundnummer()
        else:
            self.ui.comboBox_Verbundnummer.clear()
            self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")
            self.ui.comboBox_Verbundnummer.setCurrentIndex(0)
            self.ui.comboBox_Verbundnummer.setEnabled(False)

        # Aktiviere comboBox_Status immer, deaktiviere comboBox_Countwert beim Import
        if self.selected_leerrohr:  # Update-Modus
            self.ui.comboBox_Countwert.setEnabled(True)
            self.ui.comboBox_Status.setEnabled(True)
            self.populate_status(self.selected_leerrohr.get("STATUS"))
        else:  # Import-Modus
            self.ui.comboBox_Countwert.setEnabled(False)  # Deaktiviert, da Trigger die Vergabe übernimmt
            self.ui.comboBox_Status.setEnabled(True)  # Aktiviert für manuellen Status beim Import
            self.populate_status()  # Setze auf ersten Wert als Fallback

    def pruefe_daten(self):
        """Prüft, ob die Pflichtfelder korrekt gefüllt sind."""
        print("DEBUG: Starte pruefe_daten")
        fehler = []

        selected_subtyp_ids = []
        is_multirohr = False
        multirohr_quantities = 0
        for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
            for item in list_widget.selectedItems():
                try:
                    subtyp_id = int(item.text().split(" - ")[0])
                    typ = int(item.text().split(" - ")[1])
                    selected_subtyp_ids.append((subtyp_id, typ))
                    if typ == 3:
                        is_multirohr = True
                        quantity = self.subtyp_quantities.get(subtyp_id, 1)
                        multirohr_quantities += quantity  # Summe für Prüfung
                except ValueError:
                    continue

        verbundnummer = self.ui.comboBox_Verbundnummer.currentText().strip()
        if self.ui.radioButton_Abzweigung.isChecked():
            if not (self.selected_parent_leerrohr and self.selected_verteiler and self.selected_verteiler_2):
                fehler.append("Bitte wähle Parent-Leerrohr, Start- und Endknoten der Abzweigung aus.")
            parent_trasse_ids = self.selected_parent_leerrohr.get("ID_TRASSE", []) if self.selected_parent_leerrohr else []
            if parent_trasse_ids:
                trasse_ids_str = "{" + ",".join(str(int(id)) for id in parent_trasse_ids) + "}"
                try:
                    with psycopg2.connect(**self.db_details) as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                SELECT COUNT(*) 
                                FROM lwl."LWL_Trasse" 
                                WHERE id = ANY(%s)
                                AND ("VONKNOTEN" = %s OR "NACHKNOTEN" = %s)
                            """, (trasse_ids_str, self.selected_verteiler, self.selected_verteiler))
                            if not cur.fetchone()[0]:
                                fehler.append("Der Startknoten der Abzweigung liegt nicht auf der Trasse des Parent-Leerrohrs.")
                except Exception as e:
                    fehler.append(f"Datenbankfehler bei der Trassenprüfung: {e}")
        else:
            if not (self.selected_verteiler and self.selected_verteiler_2):
                fehler.append("Bitte wähle Start- und Endknoten aus.")
            if self.selected_zwischenknoten and (self.selected_zwischenknoten == self.selected_verteiler or self.selected_zwischenknoten == self.selected_verteiler_2):
                fehler.append("Zwischenknoten darf nicht Start- oder Endknoten sein.")

        if not selected_subtyp_ids:
            fehler.append("Bitte wähle mindestens einen Subtyp aus.")

        if is_multirohr and (not verbundnummer or not verbundnummer.isdigit()):
            fehler.append("Keine gültige Verbundnummer für Multi-Rohr gewählt.")
        elif not is_multirohr and verbundnummer != "Deaktiviert":
            fehler.append("Verbundnummer muss für Nicht-Multi-Rohre deaktiviert sein.")

        # Prüfe Trassen nur, wenn kein Leerrohr ausgewählt ist (Import-Modus)
        if not self.selected_leerrohr and not self.selected_trasse_ids_flat:
            fehler.append("Keine Trassen ausgewählt.")
        elif self.selected_trasse_ids_flat:
            trassen_ids_list = list(set(self.selected_trasse_ids_flat))
            try:
                with psycopg2.connect(**self.db_details) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT "VONKNOTEN", "NACHKNOTEN"
                            FROM lwl."LWL_Trasse"
                            WHERE id = ANY(%s)
                        """, (trassen_ids_list,))
                        trassen_knoten = cur.fetchall()
                        knoten_counts = {}
                        for von_knoten, nach_knoten in trassen_knoten:
                            knoten_counts[von_knoten] = knoten_counts.get(von_knoten, 0) + 1
                            knoten_counts[nach_knoten] = knoten_counts.get(nach_knoten, 0) + 1
                        start_knoten = self.selected_verteiler
                        end_knoten = self.selected_verteiler_2
                        if start_knoten not in knoten_counts:
                            fehler.append(f"Startknoten {start_knoten} ist nicht mit den ausgewählten Trassen verbunden.")
                        if end_knoten not in knoten_counts:
                            fehler.append(f"Endknoten {end_knoten} ist nicht mit den ausgewählten Trassen verbunden.")
                        elif knoten_counts[end_knoten] > 1 and end_knoten != start_knoten:
                            fehler.append(f"Endknoten {end_knoten} kommt mehrfach vor und ist kein gültiger Endknoten.")
                        if self.selected_zwischenknoten and self.selected_zwischenknoten not in knoten_counts:
                            fehler.append(f"Zwischenknoten {self.selected_zwischenknoten} ist nicht mit den ausgewählten Trassen verbunden.")
            except Exception as e:
                fehler.append(f"Datenbankfehler bei der Trassenprüfung: {e}")

        if is_multirohr:
            vorhandene_verbundnummern = set()
            try:
                with psycopg2.connect(**self.db_details) as conn:
                    with conn.cursor() as cur:
                        # Prüfe Verbundnummern basierend auf VKG_LR (Startknoten)
                        exclude_id = self.selected_leerrohr["id"] if self.selected_leerrohr else None
                        if exclude_id:
                            cur.execute("""
                                SELECT DISTINCT "VERBUNDNUMMER"
                                FROM lwl."LWL_Leerrohr"
                                WHERE "TYP" = 3 
                                AND "VKG_LR" = %s
                                AND "VERBUNDNUMMER" IS NOT NULL
                                AND "id" != %s
                            """, (self.selected_verteiler, exclude_id))
                        else:
                            cur.execute("""
                                SELECT DISTINCT "VERBUNDNUMMER"
                                FROM lwl."LWL_Leerrohr"
                                WHERE "TYP" = 3 
                                AND "VKG_LR" = %s
                                AND "VERBUNDNUMMER" IS NOT NULL
                            """, (self.selected_verteiler,))
                        vorhandene_verbundnummern = {int(row[0]) for row in cur.fetchall() if row[0] is not None}
                        if verbundnummer and verbundnummer.isdigit() and int(verbundnummer) in vorhandene_verbundnummern:
                            fehler.append(f"Verbundnummer {verbundnummer} ist bereits vergeben.")
                        # Neue Ergänzung: Prüfe, ob genug freie Verbundnummern für Duplikate vorhanden
                        max_nummer = max(vorhandene_verbundnummern, default=0)
                        freie_nummern_count = len([n for n in range(1, max_nummer + 11) if n not in vorhandene_verbundnummern])
                        if multirohr_quantities > freie_nummern_count:
                            fehler.append(f"Nicht genug freie Verbundnummern für {multirohr_quantities} Multirohr-Instanzen (verfügbar: {freie_nummern_count}).")
            except Exception as e:
                fehler.append(f"Datenbankfehler bei der Verbundnummer-Prüfung: {e}")

        if fehler:
            self.ui.label_Status.setText("; ".join(fehler))
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
            self.ui.pushButton_Import.setEnabled(False)
            self.ui.pushButton_update_leerrohr.setEnabled(False)
        else:
            self.ui.label_Status.setText("Prüfung erfolgreich.")
            self.ui.label_Status.setStyleSheet("background-color: lightgreen;")
            # Aktiviere nur den relevanten Button
            if self.selected_leerrohr:
                self.ui.pushButton_Import.setEnabled(False)
                self.ui.pushButton_update_leerrohr.setEnabled(True)
                print("DEBUG: Update-Button aktiviert, Import-Button deaktiviert")
            else:
                self.ui.pushButton_Import.setEnabled(True)
                self.ui.pushButton_update_leerrohr.setEnabled(False)
                print("DEBUG: Import-Button aktiviert, Update-Button deaktiviert")

    def importiere_daten(self):
        """Importiert die Daten aus dem Formular in die Tabelle lwl.LWL_Leerrohr oder lwl.LWL_Leerrohr_Abzweigung."""
        print("DEBUG: Starte importiere_daten")
        conn = None
        try:
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()
            conn.autocommit = False

            selected_subtyp_ids = []
            for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
                print(f"DEBUG: Prüfe ListWidget: {list_widget.objectName()}")
                for item in list_widget.selectedItems():
                    try:
                        item_text = item.text()
                        print(f"DEBUG: Verarbeite ListWidget-Eintrag: '{item_text}'")
                        parts = item_text.split(" - ")
                        if len(parts) < 5:  # Mindestens 5 Teile erforderlich (ID, Typ, Subtyp, Codierung, Bemerkung)
                            print(f"DEBUG: Ungültiges Format, zu wenige Teile in: '{item_text}'")
                            continue
                        subtyp_id = int(parts[0].strip())
                        typ = int(parts[1].strip())
                        subtyp_char = parts[2].strip()
                        codierung = parts[3].strip()
                        # Suche nach ID_CODIERUNG im letzten Teil oder mit regulärem Ausdruck
                        id_codierung = None
                        if len(parts) >= 6 and "(ID: " in parts[5]:
                            id_codierung = int(parts[5].split("(ID: ")[1].rstrip(")"))
                        elif "(ID: " in item_text:
                            id_codierung_match = re.search(r'\(ID: (\d+)\)', item_text)
                            if id_codierung_match:
                                id_codierung = int(id_codierung_match.group(1))
                        if id_codierung is None:
                            print(f"DEBUG: Keine gültige ID_CODIERUNG in: '{item_text}'")
                            continue
                        selected_subtyp_ids.append((subtyp_id, typ, codierung, id_codierung))
                        print(f"DEBUG: Subtyp hinzugefügt - ID: {subtyp_id}, Typ: {typ}, Codierung: {codierung}, ID_CODIERUNG: {id_codierung}")
                    except (ValueError, IndexError) as e:
                        print(f"DEBUG: Fehler beim Parsen von Subtyp-Daten: {e}, Eintrag: '{item_text}'")
                        continue

            if not selected_subtyp_ids:
                raise Exception("Keine gültigen Subtypen ausgewählt. Überprüfen Sie die Auswahl und das Format der ListWidget-Einträge.")

            # Überprüfe, ob erforderliche Variablen definiert sind
            print(f"DEBUG: selected_verteiler: {self.selected_verteiler}, selected_verteiler_2: {self.selected_verteiler_2}")
            if not self.selected_verteiler or not self.selected_verteiler_2:
                raise Exception("Start- oder Endknoten nicht ausgewählt.")

            # Hole COUNT und STATUS aus den Dropdowns
            count_value = 0  # Fallback-Wert, da COUNT beim Import deaktiviert ist und Trigger übernimmt
            status_id = self.ui.comboBox_Status.currentData()  # Holt die ID des ausgewählten Status

            # Ermittle freie Verbundnummern für parallelen Import
            verwendete_nummern = set()
            freie_nummern = []
            if any(typ == 3 for _, typ, _, _ in selected_subtyp_ids):
                cur.execute("""
                    SELECT DISTINCT "VERBUNDNUMMER"
                    FROM lwl."LWL_Leerrohr"
                    WHERE "TYP" = 3 
                    AND "VKG_LR" = %s
                    AND "VERBUNDNUMMER" IS NOT NULL
                """, (self.selected_verteiler,))
                verwendete_nummern = {int(row[0]) for row in cur.fetchall() if row[0] is not None}
                max_nummer = max(verwendete_nummern, default=0)
                freie_nummern = [n for n in range(1, max_nummer + 11) if n not in verwendete_nummern]
                print(f"DEBUG: Freie Verbundnummern für Import: {freie_nummern}")

            if self.ui.radioButton_Abzweigung.isChecked():
                print("DEBUG: Abzweigungsmodus aktiviert")
                trassen_ids_pg_array = "{" + ",".join(map(str, self.selected_trasse_ids_flat)) + "}"
                count = self.selected_parent_leerrohr.get("COUNT", 0) or 0
                status = status_id if status_id is not None else self.selected_parent_leerrohr.get("STATUS", 1)  # Nutze Dropdown oder Fallback
                verfuegbare_rohre = self.selected_parent_leerrohr.get("VERFUEGBARE_ROHRE", "{1,2,3}")
                parent_id = self.selected_parent_leerrohr["id"]
                hilfsknoten_id = self.selected_verteiler
                nach_knoten = self.selected_verteiler_2
                for subtyp_id, typ, codierung, id_codierung in selected_subtyp_ids:
                    # Im Abzweigungs-Modus: Quantity=1 (keine Duplizierung)
                    cur.execute(""" 
                        SELECT COUNT(*) FROM lwl."LWL_Leerrohr_Abzweigung" 
                        WHERE "ID_PARENT_LEERROHR" = %s AND "ID_HILFSKNOTEN" = %s AND "NACHKNOTEN" = %s
                    """, (parent_id, hilfsknoten_id, nach_knoten))
                    exists = cur.fetchone()[0]
                    if exists > 0:
                        raise Exception("Diese Abzweigung existiert bereits.")
                    insert_query = """
                        INSERT INTO lwl."LWL_Leerrohr_Abzweigung" (
                            "ID_PARENT_LEERROHR", "ID_HILFSKNOTEN", "ID_TRASSE", "COUNT", "STATUS", 
                            "VERFUEGBARE_ROHRE", "TYP", "CODIERUNG", "ID_CODIERUNG", "SUBTYP", "VKG_LR", "VONKNOTEN", "NACHKNOTEN"
                        ) VALUES (%s, %s, %s::bigint[], %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    values = (
                        parent_id, hilfsknoten_id, trassen_ids_pg_array, count_value, status,
                        verfuegbare_rohre, typ, codierung, id_codierung, subtyp_id, self.selected_verteiler, hilfsknoten_id, nach_knoten
                    )
                    cur.execute(insert_query, values)
                    print(f"DEBUG: Abzweigung eingefügt, Rows affected: {cur.rowcount}, COUNT: {count_value}, STATUS: {status}, ID_CODIERUNG: {id_codierung}")
            else:
                print("DEBUG: Hauptstrang-Modus aktiviert")
                # ID_TRASSE_NEU korrekt aufbauen
                id_trasse_jsonb = None
                if self.selected_trasse_ids_flat:
                    trasse_list = []
                    current_knoten = self.selected_verteiler
                    for i, tid in enumerate(self.selected_trasse_ids_flat):
                        cur.execute("""
                            SELECT "VONKNOTEN", "NACHKNOTEN"
                            FROM lwl."LWL_Trasse"
                            WHERE id = %s
                        """, (tid,))
                        von, nach = cur.fetchone()
                        reverse = False
                        if von != current_knoten:
                            if nach == current_knoten:
                                reverse = True  # Flip, wenn rückwärts anknüpft
                            else:
                                raise Exception(f"Trasse {tid} knüpft nicht an {current_knoten} an!")
                        current_knoten = nach if not reverse else von  # Nächster Knoten
                        trasse_list.append({
                            "index": i + 1,
                            "id": tid,
                            "reverse": reverse
                        })
                    id_trasse_jsonb = json.dumps(trasse_list)
                    print(f"DEBUG: ID_TRASSE_NEU gebaut: {id_trasse_jsonb}")
                trassen_ids_pg_array = "{" + ",".join(map(str, set(self.selected_trasse_ids_flat))) + "}" if self.selected_trasse_ids_flat else None
                trassen_ids_pg_array = "{" + ",".join(map(str, set(self.selected_trasse_ids_flat))) + "}" if self.selected_trasse_ids_flat else None
                status = status_id if status_id is not None else 1  # Nutze Dropdown oder Fallback
                gefoerdert = self.ui.checkBox_Foerderung.isChecked()
                subduct = self.ui.checkBox_Subduct.isChecked()
                parent_leerrohr_id = self.selected_subduct_parent if subduct else None
                firma_hersteller = self.settings.value("firma", "").split(", ")[0] or None
                vonknoten = self.selected_verteiler
                nachknoten = self.selected_verteiler_2
                kommentar = self.ui.label_Kommentar.text().strip() or None
                beschreibung = self.ui.label_Kommentar_2.text().strip() or None
                verlegt_am = self.ui.mDateTimeEdit_Strecke.date().toString("yyyy-MM-dd")

                multirohr_count = sum(1 for _, typ, _, _ in selected_subtyp_ids if typ == 3)
                current_verbundnummer = int(self.ui.comboBox_Verbundnummer.currentText()) if self.ui.comboBox_Verbundnummer.currentText().isdigit() else freie_nummern[0] if freie_nummern else 1
                for i, (subtyp_id, typ, codierung, id_codierung) in enumerate(selected_subtyp_ids):
                    quantity = self.subtyp_quantities.get(subtyp_id, 1)  # Default 1
                    print(f"DEBUG: Importiere Subtyp {subtyp_id} {quantity}-mal")
                    cur.execute("""
                        SELECT SUM((rohr->>'anzahl')::int) AS rohr_anzahl
                        FROM lwl."LUT_Leerrohr_SubTyp" t,
                        LATERAL jsonb_array_elements(t."ROHR_DEFINITION") AS rohr
                        WHERE t."id" = %s
                    """, (subtyp_id,))
                    result = cur.fetchone()
                    rohr_anzahl = int(result[0]) if result and result[0] else 1
                    verfuegbare_rohre = (
                        "{" + ",".join(map(str, range(1, rohr_anzahl + 1))) + "}" if rohr_anzahl > 1 else None
                    )
                    for q in range(quantity):
                        # Für Hauptrohre (TYP=2) Verbundnummer auf 0 setzen
                        verbundnummer_final = "0" if typ != 3 else str(current_verbundnummer)
                        insert_query = """
                            INSERT INTO lwl."LWL_Leerrohr" (
                                "ID_TRASSE", "ID_TRASSE_NEU", "VERBUNDNUMMER", "VERFUEGBARE_ROHRE", "STATUS", "COUNT", "VKG_LR", 
                                "GEFOERDERT", "SUBDUCT", "PARENT_LEERROHR_ID", "TYP", "CODIERUNG", "ID_CODIERUNG", "SUBTYP", 
                                "FIRMA_HERSTELLER", "VONKNOTEN", "NACHKNOTEN", "KOMMENTAR", "BESCHREIBUNG", "VERLEGT_AM"
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                        values = (
                            trassen_ids_pg_array or '{}', id_trasse_jsonb or '{}', verbundnummer_final, verfuegbare_rohre, status, count_value, vonknoten,
                            gefoerdert, subduct, parent_leerrohr_id, typ, codierung, id_codierung, subtyp_id,
                            firma_hersteller, vonknoten, nachknoten, kommentar, beschreibung, verlegt_am
                        )
                        cur.execute(insert_query, values)
                        print(f"DEBUG: Instanz {q+1}/{quantity} von Subtyp {subtyp_id} eingefügt, Rows affected: {cur.rowcount}, Verbundnummer: {verbundnummer_final}, COUNT: {count_value}, STATUS: {status}, ID_CODIERUNG: {id_codierung}")
                        # Für nachfolgende Multirohre die nächste freie Verbundnummer wählen
                        if typ == 3 and multirohr_count > 1:
                            current_verbundnummer = next((n for n in freie_nummern if n > current_verbundnummer), current_verbundnummer + 1)
                            freie_nummern = [n for n in freie_nummern if n != current_verbundnummer]  # Entferne genutzte
                            print(f"DEBUG: Nächste Verbundnummer für Multirohr {i+1}: {current_verbundnummer}")

            conn.commit()
            print("DEBUG: Commit erfolgreich")
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)
            self.initialisiere_formular()
            # Initialisiere graphicsView_Auswahl_Route
            self.clear_routing()
            self.routes_by_path_id = {}
            self.update_route_view()
            print("DEBUG: graphicsView_Auswahl_Route nach Import initialisiert")

        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Datenbankfehler: {str(e)}", level=Qgis.Critical)
            print(f"DEBUG: Datenbankfehler: {str(e)}")
        except Exception as e:
            if conn:
                conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Allgemeiner Fehler: {str(e)}", level=Qgis.Critical)
            print(f"DEBUG: Allgemeiner Fehler: {str(e)}")
        finally:
            if conn:
                conn.close()
                print("DEBUG: Verbindung geschlossen")

        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")
        if layer:
            layer[0].triggerRepaint()
            print("DEBUG: Layer aktualisiert")

    def update_leerrohr(self):
        """Aktualisiert die Daten des ausgewählten Leerrohrs in der Tabelle lwl.LWL_Leerrohr."""
        print("DEBUG: Starte update_leerrohr")
        if not self.selected_leerrohr:
            self.iface.messageBar().pushMessage("Fehler", "Kein Leerrohr ausgewählt.", level=Qgis.Critical)
            return

        conn = None
        try:
            conn = psycopg2.connect(**self.db_details)
            cur = conn.cursor()
            conn.autocommit = False

            selected_subtyp_ids = []
            for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
                for item in list_widget.selectedItems():
                    try:
                        subtyp_id = int(item.text().split(" - ")[0])
                        typ = int(item.text().split(" - ")[1])
                        selected_subtyp_ids.append((subtyp_id, typ))
                    except ValueError:
                        continue

            if not selected_subtyp_ids:
                raise Exception("Keine Subtypen ausgewählt.")

            id_trasse_jsonb = None
            trassen_ids_pg_array = None
            geom_wkt = None
            if self.selected_trasse_ids_flat:
                id_trasse_jsonb = None
                if self.selected_trasse_ids_flat:
                    trasse_list = []
                    num_trassen = len(self.selected_trasse_ids_flat)
                    for i, tid in enumerate(self.selected_trasse_ids_flat):
                        reverse = False  # Default: Vorwärts
                        # Beispiel-Logik für Sackstich: Setze reverse=true für Rückwege (ab der Hälfte der Liste)
                        # Passe das an deine echte Routing-Logik an (z. B. prüfe Knoten-Richtung oder Pfad-Richtung)
                        if i >= num_trassen // 2:  # Einfaches Beispiel: Rückweg ab Mitte (für symmetrische Sackstiche)
                            reverse = True
                        trasse_list.append({
                            "index": i + 1,
                            "id": tid,
                            "reverse": reverse
                        })
                    id_trasse_jsonb = json.dumps(trasse_list)
                    print(f"DEBUG: Erweitertes ID_TRASSE_NEU mit reverse-Flag: {id_trasse_jsonb}")
                trassen_ids_pg_array = "{" + ",".join(map(str, set(self.selected_trasse_ids_flat))) + "}"
                cur.execute("""
                    SELECT ST_AsText(ST_Union(geom))
                    FROM lwl."LWL_Trasse"
                    WHERE id = ANY(%s::bigint[])
                """, (trassen_ids_pg_array,))
                geom_wkt = cur.fetchone()[0] if cur.rowcount > 0 else None
                print(f"DEBUG: Geometrie WKT: {geom_wkt}")
                if not geom_wkt:
                    raise Exception("Keine gültige Geometrie für die ausgewählten Trassen gefunden.")
            else:
                print("DEBUG: Kein neues Routing durchgeführt, Geometrie bleibt unverändert")

            verbundnummer = self.ui.comboBox_Verbundnummer.currentText().strip()
            count_value = int(self.ui.comboBox_Countwert.currentText())
            status_id = self.ui.comboBox_Status.currentData()  # Holt die ID des ausgewählten Status
            status = status_id if status_id is not None else 1  # Fallback auf 1, falls kein Status ausgewählt
            gefoerdert = self.ui.checkBox_Foerderung.isChecked()
            subduct = self.ui.checkBox_Subduct.isChecked()
            parent_leerrohr_id = self.selected_subduct_parent if subduct else None
            vonknoten = self.selected_verteiler
            nachknoten = self.selected_verteiler_2
            kommentar = self.ui.label_Kommentar.text().strip() or None
            beschreibung = self.ui.label_Kommentar_2.text().strip() or None
            verlegt_am = self.ui.mDateTimeEdit_Strecke.date().toString("yyyy-MM-dd")

            for subtyp_id, typ in selected_subtyp_ids:
                cur.execute("""
                    SELECT SUM((rohr->>'anzahl')::int) AS rohr_anzahl
                    FROM lwl."LUT_Leerrohr_SubTyp" t,
                    LATERAL jsonb_array_elements(t."ROHR_DEFINITION") AS rohr
                    WHERE t."id" = %s
                """, (subtyp_id,))
                result = cur.fetchone()
                rohr_anzahl = int(result[0]) if result and result[0] else 1
                verfuegbare_rohre = (
                    "{" + ",".join(map(str, range(1, rohr_anzahl + 1))) + "}" if rohr_anzahl > 1 else None
                )
                verbundnummer_final = "0" if typ != 3 or not verbundnummer.isdigit() else verbundnummer

                if self.selected_trasse_ids_flat:
                    update_query = """
                        UPDATE lwl."LWL_Leerrohr"
                        SET
                            "ID_TRASSE" = %s,
                            "ID_TRASSE_NEU" = %s::jsonb,
                            "VERBUNDNUMMER" = %s,
                            "VERFUEGBARE_ROHRE" = %s,
                            "STATUS" = %s,
                            "COUNT" = %s,
                            "VKG_LR" = %s,
                            "GEFOERDERT" = %s,
                            "SUBDUCT" = %s,
                            "PARENT_LEERROHR_ID" = %s,
                            "TYP" = %s,
                            "SUBTYP" = %s,
                            "VONKNOTEN" = %s,
                            "NACHKNOTEN" = %s,
                            "KOMMENTAR" = %s,
                            "BESCHREIBUNG" = %s,
                            "VERLEGT_AM" = %s,
                            "geom" = ST_GeomFromText(%s)
                        WHERE "id" = %s
                    """
                    values = (
                        trassen_ids_pg_array, id_trasse_jsonb, verbundnummer_final, verfuegbare_rohre, status, count_value, vonknoten,
                        gefoerdert, subduct, parent_leerrohr_id, typ, subtyp_id,
                        vonknoten, nachknoten, kommentar, beschreibung, verlegt_am,
                        geom_wkt, self.selected_leerrohr["id"]
                    )
                    print(f"DEBUG: Update-Query mit Geometrie: {update_query}")
                    print(f"DEBUG: Values mit Geometrie: {values}")
                else:
                    update_query = """
                        UPDATE lwl."LWL_Leerrohr"
                        SET
                            "VERBUNDNUMMER" = %s,
                            "VERFUEGBARE_ROHRE" = %s,
                            "STATUS" = %s,
                            "COUNT" = %s,
                            "VKG_LR" = %s,
                            "GEFOERDERT" = %s,
                            "SUBDUCT" = %s,
                            "PARENT_LEERROHR_ID" = %s,
                            "TYP" = %s,
                            "SUBTYP" = %s,
                            "VONKNOTEN" = %s,
                            "NACHKNOTEN" = %s,
                            "KOMMENTAR" = %s,
                            "BESCHREIBUNG" = %s,
                            "VERLEGT_AM" = %s
                        WHERE "id" = %s
                    """
                    values = (
                        verbundnummer_final, verfuegbare_rohre, status, count_value, vonknoten,
                        gefoerdert, subduct, parent_leerrohr_id, typ, subtyp_id,
                        vonknoten, nachknoten, kommentar, beschreibung, verlegt_am,
                        self.selected_leerrohr["id"]
                    )
                    print(f"DEBUG: Update-Query ohne Geometrie: {update_query}")
                    print(f"DEBUG: Values ohne Geometrie: {values}")

                cur.execute(update_query, values)
                rows_affected = cur.rowcount
                print(f"DEBUG: Leerrohr aktualisiert, Rows affected: {rows_affected}, COUNT: {count_value}, STATUS: {status}")

                if rows_affected == 0:
                    print("DEBUG: WARNUNG: Keine Zeilen aktualisiert – prüfen Sie WHERE-Bedingung oder Datenbankzugriff!")

            conn.commit()
            print("DEBUG: Commit erfolgreich")
            self.iface.messageBar().pushMessage("Erfolg", "Leerrohr erfolgreich aktualisiert.", level=Qgis.Success)
            self.initialisiere_formular()
            # Initialisiere graphicsView_Auswahl_Route
            self.clear_routing()
            self.routes_by_path_id = {}  # Setze Routen zurück
            self.update_route_view()
            print("DEBUG: graphicsView_Auswahl_Route nach Update initialisiert")

            layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")
            if layer:
                layer[0].dataProvider().forceReload()  # Erzwinge das erneute Laden der Daten aus der Datenbank
                layer[0].triggerRepaint()
                QgsProject.instance().reloadAllLayers()  # Zusätzliche Sicherstellung, dass alle Layer aktualisiert werden
                self.iface.mapCanvas().refresh()
                print("DEBUG: Layer LWL_Leerrohr aktualisiert")
                # Debug: Lade die aktualisierten Daten direkt aus der Datenbank
                try:
                    cur.execute("""
                        SELECT "VERBUNDNUMMER", "COUNT", "STATUS", "VONKNOTEN", "NACHKNOTEN"
                        FROM lwl."LWL_Leerrohr"
                        WHERE "id" = %s
                    """, (self.selected_leerrohr["id"],))
                    result = cur.fetchone()
                    if result:
                        updated_verbundnummer, updated_count, updated_status, updated_vonknoten, updated_nachknoten = result
                        print(f"DEBUG: Aktualisierte Werte aus Datenbank - Verbundnummer: {updated_verbundnummer}, COUNT: {updated_count}, STATUS: {updated_status}, VONKNOTEN: {updated_vonknoten}, NACHKNOTEN: {updated_nachknoten}")
                except Exception as e:
                    print(f"DEBUG: Fehler beim Laden der aktualisierten Werte: {e}")

        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Datenbankfehler: {str(e)}", level=Qgis.Critical)
            print(f"DEBUG: Datenbankfehler: {str(e)}")
        except Exception as e:
            if conn:
                conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Allgemeiner Fehler: {str(e)}", level=Qgis.Critical)
            print(f"DEBUG: Allgemeiner Fehler: {str(e)}")
        finally:
            if conn:
                conn.close()
                print("DEBUG: Verbindung geschlossen")

    def initialisiere_formular(self):
        """Setzt das Formular zurück, entfernt vorhandene Highlights, es sei denn, Mehrfachimport ist aktiviert."""
        print("DEBUG: Starte initialisiere_formular")
        if not hasattr(self.ui, 'checkBox_clearForm') or not self.ui.checkBox_clearForm.isChecked():
            if hasattr(self, "route_highlights"):
                print(f"DEBUG: Anzahl der Highlights VOR Reset: {len(self.route_highlights)}")
            self.selected_verteiler = None
            self.selected_verteiler_2 = None
            self.selected_zwischenknoten = None
            self.selected_leerrohr = None
            self.ui.label_gewaehlter_verteiler.setText("Verteiler wählen!")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
            self.ui.label_gewaehlter_verteiler_2.setText("Verteiler wählen!")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")
            self.ui.label_gewaehlter_zwischenknoten.setText("Zwischenknoten wählen (optional)")
            self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: gray;")
            self.ui.label_gewaehltes_leerrohr.setText("Leerrohr auswählen (optional)")
            self.ui.label_gewaehltes_leerrohr.setStyleSheet("background-color: gray;")
            self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)
            self.ui.pushButton_Import.setEnabled(False)
            self.ui.pushButton_update_leerrohr.setEnabled(False)
            # Setze listWidgets zurück auf MultiSelection
            for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
                list_widget.setSelectionMode(QListWidget.MultiSelection)
                for i in range(list_widget.count()):
                    item = list_widget.item(i)
                    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    item.setSelected(False)
            self.clear_routing()
            if hasattr(self, "verteiler_highlight_1") and self.verteiler_highlight_1:
                self.verteiler_highlight_1.hide()
                self.verteiler_highlight_1 = None
            if hasattr(self, "verteiler_highlight_2") and self.verteiler_highlight_2:
                self.verteiler_highlight_2.hide()
                self.verteiler_highlight_2 = None
            if hasattr(self, "zwischenknoten_highlight") and self.zwischenknoten_highlight:
                self.zwischenknoten_highlight.hide()
                self.zwischenknoten_highlight = None
            if hasattr(self, "leerrohr_highlight") and self.leerrohr_highlight:
                self.leerrohr_highlight.hide()
                self.leerrohr_highlight = None
            self.selected_trasse_ids = []
            self.selected_trasse_ids_flat = []
            self.ui.label_Status.clear()
            self.ui.label_Status.setStyleSheet("")
            self.ui.checkBox_Foerderung.setChecked(False)
            self.ui.checkBox_Subduct.setChecked(False)
            self.ui.comboBox_Countwert.setEnabled(False)  # Deaktiviert beim Zurücksetzen
            self.ui.comboBox_Status.setEnabled(True)  # Aktiviert beim Zurücksetzen für Import
            self.populate_status()  # Setze auf ersten Wert als Fallback
            if hasattr(self, "route_highlights"):
                print(f"DEBUG: Anzahl der Highlights NACH Reset: {len(self.route_highlights)}")
            print("DEBUG: Formular wurde erfolgreich zurückgesetzt.")
            # Neue Ergänzung: Reset Quantities
            self.subtyp_quantities.clear()
        else:
            is_multirohr = False
            for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
                for item in list_widget.selectedItems():
                    try:
                        typ = int(item.text().split(" - ")[1])
                        if typ == 3:
                            is_multirohr = True
                            break
                    except ValueError:
                        continue
                    if is_multirohr:
                        break
            if is_multirohr:
                print("DEBUG: Mehrfachimport aktiviert – aktualisiere Verbundnummer für Multi-Rohr")
                self.populate_verbundnummer()
            else:
                print("DEBUG: Mehrfachimport aktiviert, aber kein Multi-Rohr – keine Änderungen")
            self.ui.pushButton_Import.setEnabled(True)
            self.ui.listWidget_Leerrohr.clear()
            # Neue Ergänzung: Reset Quantities auch hier, falls nötig – aber bei Mehrfachimport behalten wir sie optional
            # self.subtyp_quantities.clear()  # Kommentiere aus, wenn du bei Mehrfachimport behalten möchtest

    def clear_trasse_selection(self):
        """Setzt die Trassenauswahl zurück."""
        print("DEBUG: Starte clear_trasse_selection")
        self.ui.label_gewaehlter_verteiler.setText("Verteiler wählen!")
        self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
        self.ui.label_gewaehlter_verteiler_2.setText("Verteiler wählen!")
        self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")
        self.ui.label_gewaehlter_zwischenknoten.setText("Zwischenknoten wählen (optional)")
        self.ui.label_gewaehlter_zwischenknoten.setStyleSheet("background-color: gray;")
        self.ui.label_gewaehltes_leerrohr.setText("Leerrohr auswählen (optional)")
        self.ui.label_gewaehltes_leerrohr.setStyleSheet("background-color: gray;")
        self.ui.label_Kommentar.setText("")
        self.ui.label_Kommentar_2.setText("")
        self.selected_verteiler = None
        self.selected_verteiler_2 = None
        self.selected_zwischenknoten = None
        self.selected_leerrohr = None
        if not self.selected_parent_leerrohr and not self.ui.radioButton_Abzweigung.isChecked():
            print("DEBUG: Kein Parent-Leerrohr und nicht im Abzweigungsmodus – Label zurücksetzen")
            self.ui.label_Parent_Leerrohr.setText("Parent-Leerrohr erfassen")
            self.ui.label_Parent_Leerrohr.setStyleSheet("")
        self.ui.label_Status.clear()
        self.ui.label_Status.setStyleSheet("")
        self.ui.checkBox_Foerderung.setChecked(False)
        self.ui.checkBox_Subduct.setChecked(False)
        self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)
        self.ui.pushButton_Import.setEnabled(False)
        self.ui.pushButton_update_leerrohr.setEnabled(False)
        # Setze listWidgets zurück auf MultiSelection
        for list_widget in [self.ui.listWidget_Zubringerrohr, self.ui.listWidget_Hauptrohr, self.ui.listWidget_Multirohr]:
            list_widget.setSelectionMode(QListWidget.MultiSelection)
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setSelected(False)
        self.clear_routing()
        self.selected_trasse_ids = []
        self.selected_trasse_ids_flat = []
        print(f"DEBUG: Anzahl der Highlights NACH Reset: {len(self.route_highlights)}")
        # Neue Ergänzung: Reset Quantities
        self.subtyp_quantities.clear()
        self.ui.listWidget_Leerrohr.clear()

    def clear_routing(self):
        """Entfernt alle Routing-Highlights und bereitet graphicsView_Auswahl_Route vor."""
        print("DEBUG: Starte clear_routing")
        if hasattr(self, "route_highlights") and self.route_highlights:
            for highlight in self.route_highlights:
                highlight.hide()
            self.route_highlights.clear()
            print(f"DEBUG: Alle Routing-Highlights entfernt: {len(self.route_highlights)}")
        # Setze die Szene im graphicsView_Auswahl_Route zurück
        if self.ui.graphicsView_Auswahl_Route.scene():
            self.ui.graphicsView_Auswahl_Route.scene().clear()
        else:
            self.ui.graphicsView_Auswahl_Route.setScene(QGraphicsScene())
        print("DEBUG: graphicsView_Auswahl_Route Szene zurückgesetzt")

    def close_tool(self):
        """Schließt das Tool und löscht alle Highlights."""
        print("DEBUG: Schließe Tool und entferne alle Highlights")
        self.clear_trasse_selection()
        if self.map_tool:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
            self.map_tool = None
        if hasattr(self, "verteiler_highlight_1") and self.verteiler_highlight_1:
            self.verteiler_highlight_1.hide()
            self.verteiler_highlight_1 = None
            print("DEBUG: Startknoten-Highlight entfernt")
        if hasattr(self, "verteiler_highlight_2") and self.verteiler_highlight_2:
            self.verteiler_highlight_2.hide()
            self.verteiler_highlight_2 = None
            print("DEBUG: Endknoten-Highlight entfernt")
        if hasattr(self, "zwischenknoten_highlight") and self.zwischenknoten_highlight:
            self.zwischenknoten_highlight.hide()
            self.zwischenknoten_highlight = None
            print("DEBUG: Zwischenknoten-Highlight entfernt")
        if hasattr(self, "leerrohr_highlight") and self.leerrohr_highlight:
            self.leerrohr_highlight.hide()
            self.leerrohr_highlight = None
            print("DEBUG: Leerrohr-Highlight entfernt")
        if hasattr(self, "parent_highlight") and self.parent_highlight:
            self.parent_highlight.hide()
            self.parent_highlight = None
            print("DEBUG: Parent-Leerrohr-Highlight entfernt")
        if hasattr(self, "subduct_highlight") and self.subduct_highlight:
            self.subduct_highlight.hide()
            self.subduct_highlight = None
            print("DEBUG: Subduct-Parent-Highlight entfernt")
        if hasattr(self, "route_highlights") and self.route_highlights:
            for highlight in self.route_highlights:
                highlight.hide()
            self.route_highlights.clear()
            print("DEBUG: Alle Routing-Highlights entfernt")
        self.selected_trasse_ids = []
        self.selected_trasse_ids_flat = []
        print("DEBUG: Verbindung bleibt offen für andere Tools")
        self.close()

    def closeEvent(self, event):
        """Überschreibt das Schließen des Fensters über das rote 'X'."""
        print("DEBUG: Starte closeEvent")
        self.close_tool()
        event.accept()
        print("DEBUG: Fenster-Schließereignis akzeptiert")