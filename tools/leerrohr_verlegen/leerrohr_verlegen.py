import logging
from qgis.core import QgsVectorLayer, QgsFeature, QgsProject, QgsDataSourceUri, Qgis, QgsGeometry, QgsFeatureRequest, QgsMessageLog
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QCheckBox, QMessageBox
from qgis.PyQt.QtCore import Qt, QDate
from .leerrohr_verlegen_dialog import Ui_LeerrohrVerlegungsToolDialogBase
from qgis.PyQt.QtSql import QSqlDatabase, QSqlQuery
from qgis.gui import QgsHighlight
from qgis.PyQt.QtGui import QColor
import psycopg2
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
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        # Variablen für die gewählten Objekte
        self.selected_verteiler = None
        self.selected_verteiler_2 = None
        self.selected_parent_leerrohr = None
        self.selected_subduct_parent = None

        # Map-Tool-Variablen und Trassen-Listen
        self.map_tool = None
        self.selected_trasse_ids = []
        self.selected_trasse_ids_flat = []
        self.trasse_highlights = []
        self.verteiler_highlight_1 = None
        self.verteiler_highlight_2 = None
        self.parent_highlight = None
        self.subduct_highlight = None
        self.route_highlights = []

        # Verknüpfe Buttons mit bestehenden Methoden
        self.ui.pushButton_verteiler.clicked.connect(self.select_verteiler)
        self.ui.pushButton_verteiler_2.clicked.connect(self.select_verteiler_2)
        self.ui.pushButton_Parent_Leerrohr.clicked.connect(self.select_parent_leerrohr)
        self.ui.pushButton_routing.clicked.connect(self.start_routing)
        self.ui.pushButton_subduct.clicked.connect(self.select_subduct_parent)

        self.ui.pushButton_Datenpruefung.clicked.connect(self.pruefe_daten)
        self.ui.pushButton_Import.setEnabled(False)
        self.ui.pushButton_Import.clicked.connect(self.importiere_daten)

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

        # Dropdown-Verknüpfungen
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.update_selected_leerrohr_typ)
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.populate_leerrohr_subtypen)
        self.ui.comboBox_leerrohr_typ_2.currentIndexChanged.connect(self.update_selected_leerrohr_subtyp)
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.update_combobox_states)

        # Korrekte Reihenfolge für Abhängigkeiten
        self.ui.comboBox_Firma.currentIndexChanged.connect(self.populate_farbschema)
        self.ui.comboBox_Farbschema.currentIndexChanged.connect(self.populate_leerrohr_subtypen)
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.populate_firma)

        # Setze Standardzustand (Firma deaktiviert)
        self.ui.comboBox_Firma.setEnabled(False)

        # Direkte Initialisierung
        self.populate_leerrohr_typen()
        self.populate_gefoerdert_subduct()
        self.populate_farbschema()
        self.update_verbundnummer_dropdown()
        self.update_verlegungsmodus()

        # Erzwinge eine Initialisierung des Verlegungsmodus
        self.update_verlegungsmodus()
        
        # Speichert Routen nach path_id für Farben
        self.routes_by_path_id = {}
        
        print(f"DEBUG: Initialer Status von comboBox_Verbundnummer: {self.ui.comboBox_Verbundnummer.currentText()}, Enabled: {self.ui.comboBox_Verbundnummer.isEnabled()}")
        
        QgsMessageLog.logMessage(str(dir(self.ui)), "Leerrohr-Tool", level=Qgis.Info)
        
    def debug_check(self):
        try:
            print("Prüfe Zugriff auf 'label_gewaehlter_verteiler'")
            verteiler_id_text = self.ui.label_gewaehlter_verteiler.toPlainText()  # Für QTextEdit
            print(f"'label_gewaehlter_verteiler' Text: {verteiler_id_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_gewaehlter_verteiler': {e}")
        
        try:
            print("Prüfe Zugriff auf 'label_verlauf'")
            verlauf_text = self.ui.label_verlauf.toPlainText()  # Für QTextEdit
            print(f"'label_verlauf' Text: {verlauf_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_verlauf': {e}")

        try:
            print("Prüfe Zugriff auf 'label_Pruefung'")
            pruefung_text = self.ui.label_Pruefung.toPlainText()  # Für QTextEdit
            print(f"'label_Pruefung' Text: {pruefung_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_Pruefung': {e}")

        try:
            print("Prüfe Zugriff auf 'label_Kommentar'")
            kommentar_text = self.ui.label_Kommentar.text()  # Für QLineEdit
            print(f"'label_Kommentar' Text: {kommentar_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_Kommentar': {e}")

        try:
            print("Prüfe Zugriff auf 'label_Kommentar_2'")
            beschreibung_text = self.ui.label_Kommentar_2.text()  # Für QLineEdit
            print(f"'label_Kommentar_2' Text: {beschreibung_text}")
        except AttributeError as e:
            print(f"Fehler bei 'label_Kommentar_2': {e}")

        print("Debugging abgeschlossen.")

    def get_database_connection(self):
        """Gibt die Verbindungsinformationen für psycopg2 zurück."""
        layers = QgsProject.instance().mapLayers().values()
        for layer in layers:
            if layer.providerType() == 'postgres':
                connection_info = QgsDataSourceUri(layer.source())
                return {
                    "dbname": connection_info.database(),
                    "user": connection_info.username(),
                    "password": connection_info.password(),
                    "host": connection_info.host(),
                    "port": connection_info.port()
                }
        raise Exception("Keine gültige PostgreSQL-Datenbankverbindung gefunden.")
        
    def db_execute(self, query):
        """Führt eine SQL-Abfrage gegen die PostgreSQL-Datenbank aus und gibt das Ergebnis zurück."""
        try:
            db_params = self.get_database_connection()
            print(f"DEBUG: Verbindungsparameter: {db_params}")
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
            if conn:
                conn.close()
            return None
        except Exception as e:
            print(f"DEBUG: Allgemeiner Fehler bei SQL-Query: {e}")
            print(f"DEBUG: Fehlgeschlagene Query: {query}")
            QgsMessageLog.logMessage(f"Allgemeiner Fehler: {e}", "Leerrohr-Tool", level=Qgis.Critical)
            if conn:
                conn.close()
            return None
       
    def update_verlegungsmodus(self):
        """Aktiviert oder deaktiviert Felder je nach Auswahl von Hauptstrang/Abzweigung und aktualisiert Werte."""
        print("DEBUG: Starte update_verlegungsmodus")
        if self.ui.radioButton_Abzweigung.isChecked():
            # Abzweigungs-Modus → Typ & Subtyp deaktivieren, Parent & Start/Ende aktivieren
            self.ui.comboBox_leerrohr_typ.clear()
            self.ui.comboBox_leerrohr_typ.addItem("Deaktiviert")
            self.ui.comboBox_leerrohr_typ.setEnabled(False)
            
            self.ui.comboBox_leerrohr_typ_2.clear()
            self.ui.comboBox_leerrohr_typ_2.addItem("Deaktiviert")
            self.ui.comboBox_leerrohr_typ_2.setEnabled(False)

            self.ui.pushButton_Parent_Leerrohr.setEnabled(True)
            self.ui.label_Parent_Leerrohr.setEnabled(True)
            self.ui.label_Parent_Leerrohr.setStyleSheet("background-color: lightcoral;")

            self.ui.pushButton_verteiler.setText("Startknoten Abzweigung")
            self.ui.pushButton_verteiler_2.setText("Endknoten Abzweigung")

            # Attribute deaktivieren, aber Werte aus Parent-Leerrohr übernehmen
            self.ui.comboBox_Verbundnummer.setEnabled(False)
            print(f"DEBUG: Verbundnummer-Status in Abzweigungs-Modus: {self.ui.comboBox_Verbundnummer.isEnabled()}")
            self.ui.comboBox_Farbschema.setEnabled(False)
            self.ui.checkBox_Foerderung.setEnabled(False)
            self.ui.checkBox_Subduct.setEnabled(False)
            self.ui.pushButton_subduct.setEnabled(False)  # Subduct-Button deaktivieren
            self.ui.label_Subduct.setEnabled(False)       # Subduct-Label deaktivieren
            self.ui.label_Kommentar.setEnabled(False)
            self.ui.label_Kommentar_2.setEnabled(False)
            self.ui.mDateTimeEdit_Strecke.setEnabled(False)

            # Formular initialisieren für Abzweigung
            self.clear_trasse_selection()

            # Falls Parent-Leerrohr gewählt wurde → Werte übernehmen
            if self.selected_parent_leerrohr:
                print(f"DEBUG: Selected Parent-Leerrohr: {self.selected_parent_leerrohr}")
                if "VERBUNDNUMMER" in self.selected_parent_leerrohr:
                    parent_verbundnummer = self.selected_parent_leerrohr["VERBUNDNUMMER"]
                    print(f"DEBUG: VERBUNDNUMMER: {parent_verbundnummer}")
                    if self.ui.comboBox_leerrohr_typ.currentData() == 3:  # Nur Multi-Rohr
                        index = self.ui.comboBox_Verbundnummer.findText(str(parent_verbundnummer))
                        if index != -1:
                            self.ui.comboBox_Verbundnummer.setCurrentIndex(index)
                    else:
                        self.ui.comboBox_Verbundnummer.clear()
                        self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")
                        self.ui.comboBox_Verbundnummer.setCurrentIndex(0)

                if "FARBSCHEMA" in self.selected_parent_leerrohr:
                    farbschema = self.selected_parent_leerrohr["FARBSCHEMA"]
                    print(f"DEBUG: FARBSCHEMA: {farbschema}")
                    index = self.ui.comboBox_Farbschema.findText(farbschema)
                    if index != -1:
                        self.ui.comboBox_Farbschema.setCurrentIndex(index)

                if "GEFOERDERT" in self.selected_parent_leerrohr:
                    gefoerdert = self.selected_parent_leerrohr["GEFOERDERT"]
                    print(f"DEBUG: GEFOERDERT: {gefoerdert}")
                    self.ui.checkBox_Foerderung.setChecked(gefoerdert)

                if "SUBDUCT" in self.selected_parent_leerrohr:
                    subduct = self.selected_parent_leerrohr["SUBDUCT"]
                    print(f"DEBUG: SUBDUCT: {subduct}")
                    self.ui.checkBox_Subduct.setChecked(subduct)

                if "KOMMENTAR" in self.selected_parent_leerrohr:
                    kommentar = self.selected_parent_leerrohr["KOMMENTAR"]
                    print(f"DEBUG: KOMMENTAR: {kommentar}")
                    self.ui.label_Kommentar.setText(str(kommentar or ""))

                if "BESCHREIBUNG" in self.selected_parent_leerrohr:
                    beschreibung = self.selected_parent_leerrohr["BESCHREIBUNG"]
                    print(f"DEBUG: BESCHREIBUNG: {beschreibung}")
                    self.ui.label_Kommentar_2.setText(str(beschreibung or ""))

                # Aktualisiere das Datum mit Fehlerbehandlung
                verlegt_am = self.selected_parent_leerrohr.get("VERLEGT_AM", None)
                print(f"DEBUG: VERLEGT_AM: {verlegt_am}")
                if verlegt_am and isinstance(verlegt_am, (QDate, datetime.date)):
                    self.ui.mDateTimeEdit_Strecke.setDate(verlegt_am)
                elif verlegt_am is None:
                    self.ui.mDateTimeEdit_Strecke.setDate(QDate.currentDate())
                else:
                    try:
                        date_obj = QDate.fromString(str(verlegt_am), "yyyy-MM-dd")
                        self.ui.mDateTimeEdit_Strecke.setDate(date_obj)
                    except ValueError:
                        self.ui.mDateTimeEdit_Strecke.setDate(QDate.currentDate())
        else:
            # Hauptstrang-Modus → Typ & Subtyp aktivieren, Parent & Start/Ende deaktivieren
            self.ui.comboBox_leerrohr_typ.setEnabled(True)
            self.populate_leerrohr_typen()
            self.populate_leerrohr_subtypen()

            self.ui.comboBox_leerrohr_typ_2.setEnabled(True)

            self.ui.pushButton_Parent_Leerrohr.setEnabled(False)
            self.ui.label_Parent_Leerrohr.setEnabled(False)
            self.ui.label_Parent_Leerrohr.setText("Parent-Leerrohr erfassen")
            self.ui.label_Parent_Leerrohr.setStyleSheet("")
            self.selected_parent_leerrohr = None
            print("DEBUG: Parent-Leerrohr zurückgesetzt und gelöscht")
            self.ui.pushButton_verteiler.setText("Startknoten auswählen")
            self.ui.pushButton_verteiler_2.setText("Endknoten auswählen")

            # Attribute aktivieren
            self.ui.comboBox_Verbundnummer.setEnabled(self.ui.comboBox_leerrohr_typ.currentData() == 3)
            print(f"DEBUG: Verbundnummer-Status in Hauptstrang-Modus: {self.ui.comboBox_Verbundnummer.isEnabled()}")
            self.ui.comboBox_Farbschema.setEnabled(True)
            self.ui.checkBox_Foerderung.setEnabled(True)
            self.ui.checkBox_Subduct.setEnabled(True)
            self.ui.pushButton_subduct.setEnabled(self.ui.checkBox_Subduct.isChecked())  # Subduct-Button abhängig von CheckBox
            self.ui.label_Subduct.setEnabled(self.ui.checkBox_Subduct.isChecked())  # Subduct-Label abhängig von CheckBox
            self.ui.label_Kommentar.setEnabled(True)
            self.ui.label_Kommentar_2.setEnabled(True)
            self.ui.mDateTimeEdit_Strecke.setEnabled(True)

            # Formular initialisieren für Hauptstrang
            self.clear_trasse_selection()

            # Firma-ComboBox wird nur aktiviert, wenn update_combobox_states() es erlaubt
            self.update_combobox_states()

            # Aktualisiere Subduct-Status basierend auf der Checkbox
            self.update_subduct_button()
            
    def select_verteiler(self):
        """Aktiviert das Map-Tool zum Auswählen des ersten Knotens (Startknoten oder Start der Abzweigung)."""
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
        threshold_distance = 50 * (map_scale / (39.37 * 96))  # 50 Pixel in Metern basierend auf DPI und Maßstab

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
            self.selected_verteiler = knot_id  # Speichere den gewählten Knoten

            # Validierung: Prüfe, ob der Knoten auf den Trassen des Parent-Leerrohrs liegt
            if self.selected_parent_leerrohr is None or "ID_TRASSE" not in self.selected_parent_leerrohr:
                self.ui.label_gewaehlter_verteiler.setText("Kein Parent-Leerrohr ausgewählt oder Trassen-IDs fehlen")
                self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
                self.selected_verteiler = None
                return

            parent_trasse_ids = self.selected_parent_leerrohr.get("ID_TRASSE", [])
            if parent_trasse_ids is None:
                parent_trasse_ids = []  # Setze auf leere Liste, falls None
            print(f"DEBUG: Parent-Trasse-IDs für Start: {parent_trasse_ids}")
            
            # Formatierung der Trassen-IDs als PostgreSQL-Array-String
            if not parent_trasse_ids:  # Falls leere Liste, melde Fehler
                self.ui.label_gewaehlter_verteiler.setText("Keine Trassen-IDs im Parent-Leerrohr gefunden")
                self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
                self.selected_verteiler = None
                return
                    
            trasse_ids_str = "{" + ",".join(str(int(id)) for id in parent_trasse_ids) + "}"
            print(f"DEBUG: Trasse-IDs-String: {trasse_ids_str}")

            # Prüfe, ob der Knoten auf einer Trasse des Parent-Leerrohrs liegt
            conn = self.get_database_connection()
            with psycopg2.connect(**conn) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT COUNT(*) 
                        FROM lwl."LWL_Trasse" 
                        WHERE id = ANY(%s)
                        AND ("VONKNOTEN" = %s OR "NACHKNOTEN" = %s)
                    """, (trasse_ids_str, knot_id, knot_id))
                    result = cur.fetchone()
                    print(f"DEBUG: Ergebnis aus Datenbank: {result}")

                    if result and result[0] > 0:
                        self.ui.label_gewaehlter_verteiler.setText(f"Startknoten: {knot_id}")
                        self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightgreen;")
                        
                        # Highlighting des Startknotens hinzufügen
                        if hasattr(self, "verteiler_highlight_1") and self.verteiler_highlight_1:
                            self.verteiler_highlight_1.hide()  # Vorheriges Highlight entfernen
                        self.verteiler_highlight_1 = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
                        self.verteiler_highlight_1.setColor(Qt.blue)  # Rot für den Startknoten
                        self.verteiler_highlight_1.setWidth(5)
                        self.verteiler_highlight_1.show()
                        print(f"DEBUG: Startknoten {knot_id} hervorgehoben")

                        # Zusätzliche Validierung: Prüfe, ob der Knoten nicht VKG_LR oder ENDKNOTEN des Parent-Leerrohrs ist
                        parent_vkg_lr = self.selected_parent_leerrohr.get("VKG_LR", None)
                        parent_endknoten = self.selected_parent_leerrohr.get("ENDKNOTEN", None)
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

        # Berechne die Toleranz in Metern, basierend auf 10 Pixeln und dem aktuellen Maßstab
        map_scale = self.iface.mapCanvas().scale()
        dpi = 96  # Standard-DPI (kann angepasst werden, je nach Monitor)
        meters_per_pixel = map_scale / (39.37 * dpi)  # 39.37 = Zoll pro Meter
        threshold_distance = 10 * meters_per_pixel  # 10 Pixel als Toleranz, in Metern umgerechnet

        nearest_feature = None
        nearest_distance = float("inf")

        # Erstelle einen räumlichen Filter (Buffer um den Klickpunkt)
        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)  # 8 Segmente für die Rundung
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        feature_count = 0
        for feature in layer.getFeatures(request):
            feature_count += 1
            if feature["TYP"] not in ["Verteilerkasten", "Schacht", "Ortszentrale"]:
                continue
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        print(f"DEBUG: Anzahl der Features im Filterbereich: {feature_count}")
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
        """Aktiviert das Map-Tool zum Auswählen des zweiten Knotens (Endknoten oder Ende der Abzweigung)."""
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
        layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
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

            # Highlighting des Knotens
            if hasattr(self, "verteiler_2_highlight") and self.verteiler_2_highlight:
                self.verteiler_2_highlight.hide()
            self.verteiler_2_highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.verteiler_2_highlight.setColor(Qt.blue)  # Grün für Ende der Abzweigung
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

        # Berechne die Toleranz in Metern, basierend auf 10 Pixeln und dem aktuellen Maßstab
        map_scale = self.iface.mapCanvas().scale()
        dpi = 96  # Standard-DPI (kann angepasst werden, je nach Monitor)
        meters_per_pixel = map_scale / (39.37 * dpi)  # 39.37 = Zoll pro Meter
        threshold_distance = 10 * meters_per_pixel  # 10 Pixel als Toleranz, in Metern umgerechnet

        nearest_feature = None
        nearest_distance = float("inf")

        # Erstelle einen räumlichen Filter (Buffer um den Klickpunkt)
        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)  # 8 Segmente für die Rundung
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        feature_count = 0
        for feature in layer.getFeatures(request):
            feature_count += 1
            if feature["TYP"] not in ["Verteilerkasten", "Schacht", "Ortszentrale", "Hilfsknoten"]:
                continue
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        print(f"DEBUG: Anzahl der Features im Filterbereich: {feature_count}")
        print(f"DEBUG: Zeit für Knotenauswahl: {time.time() - start_time} Sekunden")

        if nearest_feature and nearest_distance <= threshold_distance:
            verteiler_id = nearest_feature["id"]
            self.selected_verteiler_2 = verteiler_id  

            self.ui.label_gewaehlter_verteiler_2.setText(f"Verteiler/Knoten ID: {verteiler_id}")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightgreen;")

            if self.verteiler_highlight_2:
                self.verteiler_highlight_2.hide()
            self.verteiler_highlight_2 = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.verteiler_highlight_2.setColor(Qt.red)  # Zweiten Knoten in Blau hervorheben
            self.verteiler_highlight_2.setWidth(5)
            self.verteiler_highlight_2.show()

            QgsMessageLog.logMessage(f"Zweiter Knoten gewählt: {self.selected_verteiler_2}", "Leerrohr-Tool", level=Qgis.Info)
        else:
            self.ui.label_gewaehlter_verteiler_2.setText("Kein Knoten innerhalb der Toleranz gefunden")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None
        
    def start_routing(self):
        """Startet das Routing und hebt bis zu 3 berechnete Routen hervor, von denen der Benutzer eine auswählen kann."""
        print(f"DEBUG: Starte Routing – selected_verteiler: {self.selected_verteiler}, selected_verteiler_2: {self.selected_verteiler_2}")

        # Vorherige Routen löschen (falls vorhanden)
        if hasattr(self, "route_highlights") and self.route_highlights:
            for highlight in self.route_highlights:
                highlight.setVisible(False)
            self.route_highlights.clear()

        print(f"DEBUG: Anzahl der Route-Highlights NACH Entfernung: {len(self.route_highlights) if hasattr(self, 'route_highlights') else 'Nicht definiert'}")

        # 1️⃣ Start- und Endknoten aus den gespeicherten Variablen auslesen
        if self.ui.radioButton_Abzweigung.isChecked():
            if not (self.selected_parent_leerrohr and self.selected_verteiler and self.selected_verteiler_2):
                self.ui.label_Status.setText("Bitte wähle Parent-Leerrohr, Start- und Endknoten der Abzweigung aus!")
                self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                print("DEBUG: Status gesetzt: Fehlende Auswahl")
                return
            start_id = self.selected_verteiler
            end_id = self.selected_verteiler_2
            parent_id = self.selected_parent_leerrohr["id"]

            try:
                parent_vkg_lr = self.selected_parent_leerrohr["VKG_LR"]
                parent_endknoten = self.selected_parent_leerrohr.get("ENDKNOTEN", None)
                if start_id == parent_vkg_lr or (parent_endknoten and start_id == parent_endknoten):
                    self.ui.label_Status.setText("Der Startknoten der Abzweigung darf nicht Start- oder Endknoten des Parent-Leerrohrs sein!")
                    self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                    print("DEBUG: Status gesetzt: Startknoten ungültig")
                    return
                trassen_ids = self.selected_parent_leerrohr["ID_TRASSE"]
                conn = self.get_database_connection()
                with psycopg2.connect(**conn) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT EXISTS (
                                SELECT 1 
                                FROM lwl."LWL_Trasse" 
                                WHERE id = ANY(%s) 
                                AND ("VONKNOTEN" = %s OR "NACHKNOTEN" = %s)
                            )
                        """, (trassen_ids, start_id, start_id))
                        if not cur.fetchone()[0]:
                            self.ui.label_Status.setText("Der Startknoten der Abzweigung muss auf einer Trasse des Parent-Leerrohrs liegen!")
                            self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                            print("DEBUG: Status gesetzt: Startknoten nicht auf Parent-Trasse")
                            return
            except Exception as e:
                self.ui.label_Status.setText(f"Fehler bei der Validierung des Startknotens: {e}")
                self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                print(f"DEBUG: Validierungsfehler: {e}")
                return
        else:
            if not (self.selected_verteiler and self.selected_verteiler_2):
                self.ui.label_Status.setText("Bitte wähle Start- und Endknoten aus!")
                self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                print("DEBUG: Status gesetzt: Fehlende Knoten")
                return
            start_id = self.selected_verteiler
            end_id = self.selected_verteiler_2
            parent_id = None

            try:
                conn = self.get_database_connection()
                with psycopg2.connect(**conn) as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT "TYP" 
                            FROM lwl."LWL_Knoten" 
                            WHERE id = %s
                        """, (start_id,))
                        typ = cur.fetchone()
                        if not typ or typ[0] not in ["Verteilerkasten", "Schacht", "Ortszentrale"]:
                            self.ui.label_Status.setText("Der Startknoten des Hauptstrangs muss ein Verteiler, Schacht oder eine Ortszentrale sein!")
                            self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                            print("DEBUG: Status gesetzt: Ungültiger Startknoten-Typ")
                            return
                        cur.execute("""
                            SELECT "TYP" 
                            FROM lwl."LWL_Knoten" 
                            WHERE id = %s
                        """, (end_id,))
                        typ = cur.fetchone()
                        if typ and typ[0] == "Virtueller Knoten":
                            self.ui.label_Status.setText("Der Endknoten des Hauptstrangs darf kein virtueller Knoten sein!")
                            self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                            print("DEBUG: Status gesetzt: Ungültiger Endknoten-Typ")
                            return
            except Exception as e:
                self.ui.label_Status.setText(f"Fehler bei der Validierung der Knoten: {e}")
                self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                print(f"DEBUG: Validierungsfehler: {e}")
                return

        try:
            start_id = int(start_id)
            end_id = int(end_id)
        except ValueError:
            self.ui.label_Status.setText("Knoten-IDs müssen Zahlen sein!")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
            print("DEBUG: Status gesetzt: Ungültige Knoten-IDs")
            return

        # 2️⃣ Routing-SQL-Query mit pgr_ksp für 3 kürzeste Pfade
        if self.ui.radioButton_Abzweigung.isChecked():
            trassen_ids = set(self.selected_parent_leerrohr["ID_TRASSE"])
            print(f"DEBUG: Ursprüngliche Trassen-IDs des Parent-Leerrohrs: {trassen_ids}")

            if not trassen_ids:
                self.ui.label_Status.setText("Keine Trassen-IDs im Parent-Leerrohr gefunden!")
                self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                print("DEBUG: Status gesetzt: Keine Trassen-IDs")
                return

            trassen_ids_str = "{" + ",".join(map(str, trassen_ids)) + "}"
            print(f"DEBUG: PostgreSQL-Array für ausgeschlossene Parent-Trassen-IDs: {trassen_ids_str}")

            sql_query = f"""
                SELECT seq, path_id, edge AS trasse_id
                FROM pgr_ksp(
                    'SELECT id, "VONKNOTEN" AS source, "NACHKNOTEN" AS target, "LAENGE" AS cost 
                     FROM lwl."LWL_Trasse" 
                     WHERE "LAENGE" IS NOT NULL AND "LAENGE" > 0 
                     AND id NOT IN (SELECT unnest(''{trassen_ids_str}''::bigint[]))',
                    %s, %s,
                    3,
                    false
                );
            """
        else:
            sql_query = """
                SELECT seq, path_id, edge AS trasse_id
                FROM pgr_ksp(
                    'SELECT id, "VONKNOTEN" AS source, "NACHKNOTEN" AS target, "LAENGE" AS cost 
                     FROM lwl."LWL_Trasse"',
                    %s, %s,
                    3,
                    false
                );
            """

        # 3️⃣ Query ausführen
        try:
            result = self.db_execute(sql_query % (start_id, end_id))
            print(f"DEBUG: Routing SQL-Abfrage: {sql_query % (start_id, end_id)}")
            print(f"DEBUG: Ergebnis aus Datenbank: {result}")

            if not result or len(result) == 0:
                self.ui.label_Status.setText("Kein Pfad gefunden! Möglicherweise gibt es keine Route ohne Parent-Trassen.")
                self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                print("DEBUG: Status gesetzt: Kein Pfad gefunden")
                return

            # Gruppiere die Ergebnisse nach path_id (für echte alternative Routen)
            routes = {}
            for seq, path_id, trasse_id in result:
                if path_id not in routes:
                    routes[path_id] = []
                if trasse_id is not None and trasse_id != -1:  # Ignoriere -1 (Ende des Pfads)
                    routes[path_id].append(trasse_id)

            # NEU: Setze nur die erste Route als Standard, synchronisiere flat
            self.routes_by_path_id = routes  # Speichere alle Routen für die Auswahl
            self.selected_trasse_ids = list(routes.values())  # Liste von Listen bleibt erhalten
            self.selected_trasse_ids_flat = routes[1]  # Standardmäßig erste Route für den Import
            print(f"DEBUG: Initial ausgewählte Route: {self.selected_trasse_ids_flat}")

            # GEÄNDERT: Highlights für alle Routen, aber flat bleibt bei der Auswahl
            self.highlight_multiple_routes(list(routes.values()))

            # 5️⃣ Aktiviere MapTool zur Routenauswahl, wenn mehr als eine Route existiert
            if len(routes) > 1:
                self.activate_route_selection()
                self.ui.label_Status.setText("Wählen Sie eine Route aus den hervorgehobenen Pfaden!")
            else:
                self.ui.label_Status.setText("Route berechnet – Import möglich!")
            
            self.ui.label_Status.setStyleSheet("background-color: lightgreen; color: black; font-weight: bold; padding: 5px;")
            print("DEBUG: Status gesetzt: Erfolgreiches Routing")

        except Exception as e:
            self.ui.label_Status.setText(f"Datenbankfehler: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
            print(f"DEBUG: Datenbankfehler: {e}")
            return
            
    def highlight_multiple_routes(self, routes):
        """Hebt eine oder mehrere Routen in unterschiedlichen Farben in QGIS hervor."""
        print(f"DEBUG: Anzahl der Routen zum Highlighten: {len(routes)}")

        if hasattr(self, "route_highlights") and self.route_highlights:
            for highlight in self.route_highlights:
                highlight.setVisible(False)
            self.route_highlights.clear()

        self.route_highlights = []

        layer_list = QgsProject.instance().mapLayersByName("LWL_Trasse")
        if not layer_list:
            print("⚠ Fehler: Der Layer 'LWL_Trasse' wurde nicht gefunden!")
            return

        trasse_layer = layer_list[0]

        if len(routes) == 1:  # Nur eine Route (kürzester Pfad)
            color = QColor(255, 0, 0, 150)  # Rot für den kürzesten Pfad
            for trassen_id in routes[0]:
                print(f"DEBUG: Trassen-ID zum Highlighten (Route): {trassen_id}")

                request = QgsFeatureRequest().setFilterExpression(f'"id" = {trassen_id}')
                feature_iter = trasse_layer.getFeatures(request)

                for feature in feature_iter:
                    highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), trasse_layer)
                    highlight.setColor(color)
                    highlight.setWidth(10)
                    highlight.show()
                    self.route_highlights.append(highlight)
        else:  # Mehrere Routen (alternative Pfade)
            colors = [QColor(255, 0, 0, 150), QColor(0, 0, 255, 150), QColor(0, 255, 0, 150)]  # Rot, Blau, Grün
            for i, route in enumerate(routes):
                for trassen_id in route:
                    print(f"DEBUG: Trassen-ID zum Highlighten (Route {i+1}): {trassen_id}")

                    request = QgsFeatureRequest().setFilterExpression(f'"id" = {trassen_id}')
                    feature_iter = trasse_layer.getFeatures(request)

                    for feature in feature_iter:
                        highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), trasse_layer)
                        highlight.setColor(colors[i % len(colors)])  # Wechsle Farben für jede Route
                        highlight.setWidth(10)
                        highlight.show()
                        self.route_highlights.append(highlight)

        print(f"DEBUG: {len(self.route_highlights)} Highlights gesetzt")

    def activate_route_selection(self):
        print("DEBUG: Aktiviere MapTool zur Routenauswahl")

        class RouteSelectionTool(QgsMapToolEmitPoint):
            def __init__(self, tool):
                self.tool = tool
                super().__init__(tool.iface.mapCanvas())
                # Speichere die Gruppierung nach path_id aus den Routenergebnissen
                self.routes_by_path_id = {}
                for path_id, route in enumerate(self.tool.selected_trasse_ids):  # selected_trasse_ids ist Liste von Listen
                    if path_id < len(self.tool.selected_trasse_ids):
                        self.routes_by_path_id[path_id + 1] = self.tool.selected_trasse_ids[path_id]

            def canvasReleaseEvent(self, event):
                point = event.mapPoint()
                layer = QgsProject.instance().mapLayersByName("LWL_Trasse")[0]
                for feature in layer.getFeatures():
                    if feature.geometry().distance(QgsGeometry.fromPointXY(point)) < 20:  # 20 Meter Toleranz
                        trassen_id = feature["id"]
                        # Suche in allen Routen
                        for path_id, route in self.routes_by_path_id.items():
                            if trassen_id in route:
                                # NEU: Setze beide Variablen auf die gewählte Route
                                self.tool.selected_trasse_ids = route
                                self.tool.selected_trasse_ids_flat = route  # Synchronisiere
                                self.tool.highlight_selected_route()
                                self.tool.iface.mapCanvas().unsetMapTool(self)
                                self.tool.ui.label_Status.setText(f"Route {path_id} ausgewählt – Import möglich!")
                                self.tool.ui.label_Status.setStyleSheet("background-color: lightgreen; color: black; font-weight: bold; padding: 5px;")
                                print(f"DEBUG: Gewählte Route: {self.tool.selected_trasse_ids_flat}")
                                
                                # GEÄNDERT: Aktualisiere Verbundnummer nach Auswahl
                                self.tool.populate_verbundnummer()
                                return
                        break
                self.tool.ui.label_Status.setText("Kein gültiger Pfad ausgewählt!")
                self.tool.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")

        self.map_tool = RouteSelectionTool(self)
        self.iface.mapCanvas().setMapTool(self.map_tool)
        
    def highlight_selected_route(self):
        print(f"DEBUG: Hebe ausgewählte Route hervor – selected_trasse_ids: {self.selected_trasse_ids}")

        if hasattr(self, "route_highlights") and self.route_highlights:
            for highlight in self.route_highlights:
                highlight.setVisible(False)
            self.route_highlights.clear()

        self.route_highlights = []

        layer_list = QgsProject.instance().mapLayersByName("LWL_Trasse")
        if not layer_list:
            print("⚠ Fehler: Der Layer 'LWL_Trasse' wurde nicht gefunden!")
            return

        trasse_layer = layer_list[0]

        # Farben basierend auf path_id
        colors = {
            1: QColor(255, 0, 0, 150),  # Rot für path_id 1
            2: QColor(0, 0, 255, 150),  # Blau für path_id 2
            3: QColor(0, 255, 0, 150)   # Grün für path_id 3
        }

        # Finde den path_id der ausgewählten Route
        selected_route = self.selected_trasse_ids
        path_id = None
        for pid, route in self.routes_by_path_id.items():
            if route == selected_route:
                path_id = pid
                break

        if path_id is None:
            path_id = 1  # Fallback auf Rot

        color = colors.get(path_id, QColor(255, 0, 0, 150))  # Standardfarbe Rot

        for trassen_id in selected_route:
            print(f"DEBUG: Trassen-ID zur finalen Hervorhebung: {trassen_id}")

            request = QgsFeatureRequest().setFilterExpression(f'"id" = {trassen_id}')
            feature_iter = trasse_layer.getFeatures(request)

            for feature in feature_iter:
                highlight = QgsHighlight(self.iface.mapCanvas(), feature.geometry(), trasse_layer)
                highlight.setColor(color)
                highlight.setWidth(10)
                highlight.show()
                self.route_highlights.append(highlight)

        print(f"DEBUG: {len(self.route_highlights)} finale Highlights gesetzt")

    def clear_routing(self):
        """Entfernt alle bestehenden Routing-Highlights aus der Karte."""
        print("DEBUG: Methode clear_routing() wurde aufgerufen!")

        if not hasattr(self, "route_highlights"):
            self.route_highlights = []

        print(f"DEBUG: Vor dem Entfernen - Anzahl der Routing-Highlights: {len(self.route_highlights)}")

        for highlight in self.route_highlights:
            highlight.setVisible(False)
            del highlight

        self.route_highlights.clear()
        self.route_highlights = []

        print(f"DEBUG: Nach dem Entfernen - Anzahl der Routing-Highlights: {len(self.route_highlights)}")

    def select_parent_leerrohr(self):
        """Aktiviert das Map-Tool zum Auswählen eines Parent-Leerrohrs aus LWL_Leerrohr."""
        print("DEBUG: Starte Auswahl eines Parent-Leerrohrs")
        self.ui.label_Parent_Leerrohr.clear()  # Label zurücksetzen

        # Falls das MapTool bereits aktiv ist, trennen
        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass

        # Aktiviere MapTool zur Auswahl
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.parent_leerrohr_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def parent_leerrohr_selected(self, point):
        """Speichert das gewählte Parent-Leerrohr und dessen Attribute."""
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

        # Berechne Toleranz in Metern basierend auf 10 Pixeln und Maßstab
        map_scale = self.iface.mapCanvas().scale()
        dpi = 96  # Standard-DPI
        meters_per_pixel = map_scale / (39.37 * dpi)
        threshold_distance = 10 * meters_per_pixel

        nearest_feature = None
        nearest_distance = float("inf")

        # Räumlicher Filter um den Klickpunkt
        buffer = QgsGeometry.fromPointXY(point).buffer(threshold_distance, 8)
        request = QgsFeatureRequest().setFilterRect(buffer.boundingBox())

        feature_count = 0
        for feature in layer.getFeatures(request):
            feature_count += 1
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        print(f"DEBUG: Anzahl gefilterter Features: {feature_count}")
        print(f"DEBUG: Zeit für Auswahl: {time.time() - start_time} Sekunden")

        if nearest_feature and nearest_distance <= threshold_distance:
            leerrohr_id = nearest_feature["id"]
            
            # Initialisiere selected_parent_leerrohr neu, um alte Werte zu vermeiden
            self.selected_parent_leerrohr = {}
            
            # Fülle selected_parent_leerrohr mit den Attributen des Features
            for field in layer.fields():
                field_name = field.name()
                self.selected_parent_leerrohr[field_name] = nearest_feature[field_name] if field_name in nearest_feature else None
            
            # Setze explizit die relevanten Felder, falls sie nicht im Feature vorhanden sind, hole sie aus der Datenbank
            try:
                db_params = self.get_database_connection()
                with psycopg2.connect(**db_params) as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT "ID_TRASSE", "VERLEGT_AM", "VERBUNDNUMMER", "FARBSCHEMA", "GEFOERDERT", "SUBDUCT", "KOMMENTAR", "BESCHREIBUNG", "VONKNOTEN", "NACHKNOTEN", "COUNT" FROM lwl."LWL_Leerrohr" WHERE id = %s', (leerrohr_id,))
                        result = cur.fetchone()
                        if result:
                            self.selected_parent_leerrohr["ID_TRASSE"] = result[0] if result[0] else []
                            self.selected_parent_leerrohr["VERLEGT_AM"] = result[1] if result[1] else None
                            self.selected_parent_leerrohr["VERBUNDNUMMER"] = result[2] if result[2] is not None else None
                            self.selected_parent_leerrohr["FARBSCHEMA"] = result[3] if result[3] is not None else None
                            self.selected_parent_leerrohr["GEFOERDERT"] = result[4] if result[4] is not None else None
                            self.selected_parent_leerrohr["SUBDUCT"] = result[5] if result[5] is not None else None
                            self.selected_parent_leerrohr["KOMMENTAR"] = result[6] if result[6] is not None else None
                            self.selected_parent_leerrohr["BESCHREIBUNG"] = result[7] if result[7] is not None else None
                            self.selected_parent_leerrohr["VONKNOTEN"] = result[8] if result[8] is not None else None
                            self.selected_parent_leerrohr["NACHKNOTEN"] = result[9] if result[9] is not None else None
                            self.selected_parent_leerrohr["COUNT"] = result[10] if result[10] is not None else None
            except psycopg2.Error as e:
                print(f"DEBUG: PostgreSQL-Fehler beim Abrufen von Feldern: {e}")
                # Fallback-Werte, falls die Datenbankabfrage fehlschlägt
                self.selected_parent_leerrohr["ID_TRASSE"] = [] if "ID_TRASSE" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["ID_TRASSE"]
                self.selected_parent_leerrohr["VERLEGT_AM"] = None if "VERLEGT_AM" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["VERLEGT_AM"]
                self.selected_parent_leerrohr["VERBUNDNUMMER"] = None if "VERBUNDNUMMER" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["VERBUNDNUMMER"]
                self.selected_parent_leerrohr["FARBSCHEMA"] = None if "FARBSCHEMA" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["FARBSCHEMA"]
                self.selected_parent_leerrohr["GEFOERDERT"] = None if "GEFOERDERT" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["GEFOERDERT"]
                self.selected_parent_leerrohr["SUBDUCT"] = None if "SUBDUCT" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["SUBDUCT"]
                self.selected_parent_leerrohr["KOMMENTAR"] = None if "KOMMENTAR" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["KOMMENTAR"]
                self.selected_parent_leerrohr["BESCHREIBUNG"] = None if "BESCHREIBUNG" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["BESCHREIBUNG"]
                self.selected_parent_leerrohr["VONKNOTEN"] = None if "VONKNOTEN" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["VONKNOTEN"]
                self.selected_parent_leerrohr["NACHKNOTEN"] = None if "NACHKNOTEN" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["NACHKNOTEN"]
                self.selected_parent_leerrohr["COUNT"] = None if "COUNT" not in self.selected_parent_leerrohr else self.selected_parent_leerrohr["COUNT"]

            self.selected_parent_leerrohr["id"] = leerrohr_id

            print(f"DEBUG: selected_parent_leerrohr vor Zuweisung: {self.selected_parent_leerrohr}")
            print(f"DEBUG: Selected Parent-Leerrohr VERLEGT_AM: {self.selected_parent_leerrohr.get('VERLEGT_AM', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr ID_TRASSE: {self.selected_parent_leerrohr.get('ID_TRASSE', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr VERBUNDNUMMER: {self.selected_parent_leerrohr.get('VERBUNDNUMMER', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr FARBSCHEMA: {self.selected_parent_leerrohr.get('FARBSCHEMA', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr GEFOERDERT: {self.selected_parent_leerrohr.get('GEFOERDERT', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr SUBDUCT: {self.selected_parent_leerrohr.get('SUBDUCT', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr KOMMENTAR: {self.selected_parent_leerrohr.get('KOMMENTAR', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr BESCHREIBUNG: {self.selected_parent_leerrohr.get('BESCHREIBUNG', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr VONKNOTEN: {self.selected_parent_leerrohr.get('VONKNOTEN', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr NACHKNOTEN: {self.selected_parent_leerrohr.get('NACHKNOTEN', 'Nicht vorhanden')}")
            print(f"DEBUG: Selected Parent-Leerrohr COUNT: {self.selected_parent_leerrohr.get('COUNT', 'Nicht vorhanden')}")
            print(f"DEBUG: Full selected_parent_leerrohr: {self.selected_parent_leerrohr}")
            print(f"DEBUG: Feature VERLEGT_AM: {nearest_feature['VERLEGT_AM'] if 'VERLEGT_AM' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature ID_TRASSE: {nearest_feature['ID_TRASSE'] if 'ID_TRASSE' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature VERBUNDNUMMER: {nearest_feature['VERBUNDNUMMER'] if 'VERBUNDNUMMER' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature FARBSCHEMA: {nearest_feature['FARBSCHEMA'] if 'FARBSCHEMA' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature GEFOERDERT: {nearest_feature['GEFOERDERT'] if 'GEFOERDERT' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature SUBDUCT: {nearest_feature['SUBDUCT'] if 'SUBDUCT' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature KOMMENTAR: {nearest_feature['KOMMENTAR'] if 'KOMMENTAR' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature BESCHREIBUNG: {nearest_feature['BESCHREIBUNG'] if 'BESCHREIBUNG' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature VONKNOTEN: {nearest_feature['VONKNOTEN'] if 'VONKNOTEN' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature NACHKNOTEN: {nearest_feature['NACHKNOTEN'] if 'NACHKNOTEN' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature COUNT: {nearest_feature['COUNT'] if 'COUNT' in nearest_feature else 'Nicht vorhanden'}")
            print(f"DEBUG: Feature Felder: {[field.name() for field in layer.fields()]}")

            self.ui.label_Parent_Leerrohr.setText(f"Parent-Leerrohr ID: {leerrohr_id}")
            self.ui.label_Parent_Leerrohr.setStyleSheet("background-color: lightgreen;")

            # Highlighting des gewählten Leerrohrs
            if hasattr(self, "parent_highlight") and self.parent_highlight:
                self.parent_highlight.hide()
            self.parent_highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.parent_highlight.setColor(Qt.yellow)  # Gelb für Parent-Leerrohr
            self.parent_highlight.setWidth(5)
            self.parent_highlight.show()

            QgsMessageLog.logMessage(f"Parent-Leerrohr gewählt: {leerrohr_id}", "Leerrohr-Tool", level=Qgis.Info)
            
            # Attribute übernehmen
            self.update_verlegungsmodus()
        else:
            self.ui.label_Parent_Leerrohr.setText("Kein Leerrohr in Reichweite gefunden")
            self.ui.label_Parent_Leerrohr.setStyleSheet("background-color: lightcoral;")

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None        
        
    def update_combobox_states(self):
        """Aktiviert oder deaktiviert comboBox_Verbundnummer und comboBox_Farbschema basierend auf dem ausgewählten TYP, ohne den Inhalt zu überschreiben."""
        print("DEBUG: Starte update_combobox_states")
        selected_typ = self.ui.comboBox_leerrohr_typ.currentData()
        print(f"DEBUG: Ausgewählter Typ in update_combobox_states: {selected_typ}")

        if selected_typ == 3:  # Überprüft, ob der TYP 'Multi-Rohr' ist (TYP = 3)
            self.ui.comboBox_Verbundnummer.setEnabled(True)
            print(f"DEBUG: Verbundnummer aktiviert, da Typ {selected_typ} ist.")
            self.ui.comboBox_Farbschema.setEnabled(True)
            self.ui.comboBox_Firma.setEnabled(True)
        else:
            self.ui.comboBox_Verbundnummer.setEnabled(False)
            print(f"DEBUG: Verbundnummer deaktiviert, da Typ {selected_typ} nicht 3 ist.")
            # Überschreibe den Inhalt nur, wenn er nicht bereits "Deaktiviert" enthält
            if self.ui.comboBox_Verbundnummer.count() == 0 or self.ui.comboBox_Verbundnummer.currentText() != "Deaktiviert":
                self.ui.comboBox_Verbundnummer.clear()
                self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")
                self.ui.comboBox_Verbundnummer.setCurrentIndex(0)
            self.ui.comboBox_Farbschema.setEnabled(False)
            self.ui.comboBox_Firma.setEnabled(False)

    def populate_leerrohr_typen(self):
        """Lädt alle Leerrohrtypen aus LUT_Leerrohr_Typ in das Dropdown."""
        print("DEBUG: Starte populate_leerrohr_typen")
        self.ui.comboBox_leerrohr_typ.blockSignals(True)
        self.ui.comboBox_leerrohr_typ.clear()
        self.ui.comboBox_leerrohr_typ.setEnabled(True)  # Immer aktiviert

        db_details = self.get_database_connection()
        conn = None
        cur = None

        try:
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()

            print("DEBUG: Führe SQL-Abfrage aus: SELECT \"id\", \"TYP\" FROM lwl.\"LUT_Leerrohr_Typ\" WHERE \"id\" IN (1, 2, 3)")
            cur.execute("SELECT \"id\", \"TYP\" FROM lwl.\"LUT_Leerrohr_Typ\" WHERE \"id\" IN (1, 2, 3)")
            typen = cur.fetchall()
            print(f"DEBUG: Gefundene Leerrohrtypen aus LUT_Leerrohr_Typ: {typen}")

            for typ_id, typ_name in typen:
                print(f"DEBUG: Hinzufügen zu Dropdown – ID: {typ_id}, Typ: {typ_name}")
                self.ui.comboBox_leerrohr_typ.addItem(typ_name, typ_id)  # Setze die ID als data

            # Setze den Standardwert oder behalte den vorherigen Typ
            if self.ui.comboBox_leerrohr_typ.count() > 0:
                self.ui.comboBox_leerrohr_typ.setCurrentIndex(0)  # Standard: Erster Typ
                self.update_selected_leerrohr_typ()  # Aktualisiere sofort

        except Exception as e:
            self.ui.label_Status.setText(f"Fehler beim Laden der Leerrohrtypen: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
            print(f"DEBUG: Fehler bei der Abfrage der Leerrohrtypen: {e}")

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        self.ui.comboBox_leerrohr_typ.blockSignals(False)

    def update_selected_leerrohr_typ(self):
        """Aktualisiert das Label für den gewählten Typ und ruft populate_verbundnummer auf, unabhängig vom Typ."""
        print("DEBUG: Starte update_selected_leerrohr_typ")
        if self.ui.comboBox_leerrohr_typ.currentIndex() >= 0:
            typ_text = self.ui.comboBox_leerrohr_typ.currentText()
            selected_typ = self.ui.comboBox_leerrohr_typ.currentData()
            print(f"DEBUG: Gewählter Typ – Text: {typ_text}, Data: {selected_typ}")
            self.ui.label_gewaehltes_leerrohr.setText(typ_text)
            
            # Rufe populate_verbundnummer immer auf, unabhängig vom Typ (berücksichtigt Codierung indirekt über andere Methoden)
            self.populate_verbundnummer()
        else:
            self.ui.label_gewaehltes_leerrohr.clear()
            print("DEBUG: Kein Typ ausgewählt, Label geleert")

    def populate_leerrohr_subtypen(self):
        """Füllt die ComboBox für Leerrohr-Subtypen basierend auf Typ, Firma und Codierung, mit IDs und Textwerten aus der DB-Struktur."""
        self.ui.comboBox_leerrohr_typ_2.blockSignals(True)
        self.ui.comboBox_leerrohr_typ_2.clear()
        self.ui.comboBox_leerrohr_typ_2.setEnabled(False)
        
        # Label für Subtyp leeren, wenn kein Typ gewählt wurde
        self.ui.label_gewaehltes_leerrohr_2.clear()

        firma = self.ui.comboBox_Firma.currentData()  # Textwert (z. B. "Gabocom")
        typ_id = self.ui.comboBox_leerrohr_typ.currentData()  # Numerische ID_TYP (z. B. 3)
        codierung_id = self.ui.comboBox_Farbschema.currentData()  # Numerische ID_CODIERUNG (z. B. 1)

        # Prüfe, ob alle Werte vorhanden sind
        if not firma or not typ_id or not codierung_id:
            self.ui.comboBox_leerrohr_typ_2.addItem("Bitte Firma, Typ und Codierung wählen")
            self.ui.comboBox_leerrohr_typ_2.blockSignals(False)
            return

        db_details = self.get_database_connection()
        conn = None
        cur = None

        try:
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()

            # Lade alle Subtypen für Typ-ID + Firma-Text + Codierung-ID
            print("DEBUG: Lade Subtypen mit IDs und Text – FIRMA: %s, ID_TYP: %s, ID_CODIERUNG: %s" % (firma, typ_id, codierung_id))
            cur.execute("""
                SELECT "id", "SUBTYP_char", "BEZEICHNUNG"
                FROM lwl."LUT_Leerrohr_SubTyp"
                WHERE "FIRMA" = %s AND "ID_TYP" = %s AND "ID_CODIERUNG" = %s;
            """, (firma, typ_id, codierung_id))

            rows = cur.fetchall()

            if rows:
                self.ui.comboBox_leerrohr_typ_2.setEnabled(True)
                for row in rows:
                    subtyp_id, subtyp_char, bezeichnung = row
                    self.ui.comboBox_leerrohr_typ_2.addItem(f"{subtyp_char} - {bezeichnung}", subtyp_id)

                # Qt-Trick: Stelle sicher, dass der erste Wert wählbar ist
                self.ui.comboBox_leerrohr_typ_2.setCurrentIndex(-1)
                self.ui.comboBox_leerrohr_typ_2.setCurrentIndex(0)

                # Aktualisiere das Label für den gewählten Subtyp (Text anzeigen)
                self.ui.label_gewaehltes_leerrohr_2.setText(self.ui.comboBox_leerrohr_typ_2.currentText())
            else:
                self.ui.comboBox_leerrohr_typ_2.addItem("Keine Subtypen verfügbar")

        except Exception as e:
            self.ui.label_Status.setText(f"Fehler beim Laden der Subtypen: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        self.ui.comboBox_leerrohr_typ_2.blockSignals(False)

    def get_selected_subtyp_id(self):
        """Ruft die ID des ausgewählten Subtyps ab, basierend auf Codierung und Subtypen aus LUT_Leerrohr_SubTyp."""
        print("DEBUG: Starte get_selected_subtyp_id")
        # Prüfe, ob eine Auswahl getroffen wurde
        if self.ui.comboBox_leerrohr_typ_2.currentIndex() == -1:
            raise ValueError("Kein Subtyp ausgewählt.")
        
        # Abrufen der ID des ausgewählten Subtyps
        subtyp_id = self.ui.comboBox_leerrohr_typ_2.currentData()
        print(f"DEBUG: Ausgewählte Subtyp-ID: {subtyp_id}")
        return subtyp_id

    def update_subduct_button(self):
        """Aktiviert oder deaktiviert den Subduct-Button und das Subduct-Label basierend auf der CheckBox, unabhängig von Codierung."""
        print("DEBUG: Starte update_subduct_button")
        is_subduct = self.ui.checkBox_Subduct.isChecked()
        self.ui.pushButton_subduct.setEnabled(is_subduct)
        self.ui.label_Subduct.setEnabled(is_subduct)  # Subduct-Label nur aktiv, wenn CheckBox angeklickt
        self.ui.label_Subduct.setText("Hauptrohr auswählen")
        if is_subduct:
            self.ui.label_Subduct.setStyleSheet("background-color: lightcoral;")
        else:
            self.ui.label_Subduct.setStyleSheet("background-color: ;")  # Oder "" für Standard-QT-Styling
        print(f"DEBUG: Subduct-Button und -Label aktiviert: {is_subduct}")

    def select_subduct_parent(self):
        """Aktiviert das Map-Tool zum Auswählen eines Subduct-Parent-Leerrohrs, unabhängig von Codierung."""
        print("DEBUG: Starte Auswahl eines Subduct-Parent-Leerrohrs")
        self.ui.label_Subduct.clear()  # Neues Label für Subduct zurücksetzen

        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass

        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.subduct_parent_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def subduct_parent_selected(self, point):
        """Speichert das gewählte Subduct-Parent-Leerrohr, unabhängig von Codierung."""
        print("DEBUG: Verarbeite Auswahl des Subduct-Parent-Leerrohrs")
        layer_name = "LWL_Leerrohr"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_Subduct.setText("Layer 'LWL_Leerrohr' nicht gefunden")
            self.ui.label_Subduct.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        map_scale = self.iface.mapCanvas().scale()
        dpi = 96
        meters_per_pixel = map_scale / (39.37 * dpi)
        threshold_distance = 10 * meters_per_pixel

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
            
            # Highlighting des Subduct-Parent-Leerrohrs
            if hasattr(self, "subduct_highlight") and self.subduct_highlight:
                self.subduct_highlight.hide()
            self.subduct_highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.subduct_highlight.setColor(Qt.cyan)  # Cyan für Subduct
            self.subduct_highlight.setWidth(5)
            self.subduct_highlight.show()
            print(f"DEBUG: Subduct-Parent-Leerrohr {leerrohr_id} hervorgehoben")

            QgsMessageLog.logMessage(f"Subduct-Parent-Leerrohr gewählt: {leerrohr_id}", "Leerrohr-Tool", level=Qgis.Info)
        else:
            self.ui.label_Subduct.setText("Kein Leerrohr in Reichweite gefunden")
            self.ui.label_Subduct.setStyleSheet("background-color: lightcoral;")
            self.selected_subduct_parent = None

        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def populate_verbundnummer(self):
        """Setzt die Verbundnummer basierend auf dem ausgewählten Rohrtyp, dem Routing (falls vorhanden), und ermöglicht Auswahl mit flexibler Zählung, berücksichtigt Codierung indirekt über Subtypen."""
        print("DEBUG: Starte populate_verbundnummer")
        self.ui.comboBox_Verbundnummer.clear()

        # 🛑 Überprüfen, ob Multi-Rohr (TYP = 3) oder nicht!
        selected_typ = self.ui.comboBox_leerrohr_typ.currentData()
        print(f"DEBUG: Ausgewählter Leerrohrtyp (currentData): {selected_typ}")
        print(f"DEBUG: Ausgewählter Leerrohrtyp (currentText): {self.ui.comboBox_leerrohr_typ.currentText()}")
        print(f"DEBUG: Ist Multi-Rohr? {selected_typ == 3}")

        if selected_typ != 3:  
            # ❌ Kein Multi-Rohr → Zeige "Deaktiviert" im Dropdown
            print(f"DEBUG: Setze Verbundnummer auf 'Deaktiviert', da Typ {selected_typ} nicht 3 ist.")
            self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")
            self.ui.comboBox_Verbundnummer.setCurrentIndex(0)
            self.ui.comboBox_Verbundnummer.setEnabled(False)
            return  # ⛔ Methode direkt verlassen!

        # ✅ Multi-Rohr → Berechnung aktivieren
        self.ui.comboBox_Verbundnummer.setEnabled(True)

        # Prüfe, ob Routing-Daten vorhanden sind
        if not self.selected_verteiler or not self.selected_trasse_ids_flat:  # Verwende selected_trasse_ids_flat
            # Wenn keine Routing-Daten vorhanden sind, zeige alle Nummern aktiv an
            print("DEBUG: Keine Routing-Daten vorhanden, zeige alle Nummern aktiv an")
            for nummer in range(1, 11):  # Zeige z. B. die Nummern 1–10 aktiv an
                self.ui.comboBox_Verbundnummer.addItem(str(nummer))
            self.ui.comboBox_Verbundnummer.setCurrentText("1")  # Setze Standardwert auf 1
            return

        # Hole die verwendeten Verbundnummern für die aktuelle Route und den Startknoten
        verwendete_nummern = set()
        db_details = self.get_database_connection()
        conn = None
        cur = None

        try:
            print("DEBUG: Verbinde mit Datenbank")
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()

            trassen_ids_str = "{" + ",".join(map(str, set(self.selected_trasse_ids_flat))) + "}"  # Verwende selected_trasse_ids_flat
            print(f"DEBUG: SQL-Abfrage für Verbundnummern: SELECT DISTINCT \"VERBUNDNUMMER\" FROM lwl.\"LWL_Leerrohr\" WHERE \"TYP\" = 3 AND (\"VKG_LR\" = {self.selected_verteiler} OR \"ID_TRASSE\" && {trassen_ids_str}::bigint[]) AND \"VERBUNDNUMMER\" IS NOT NULL")

            cur.execute("""
                SELECT DISTINCT "VERBUNDNUMMER"
                FROM lwl."LWL_Leerrohr"
                WHERE "TYP" = 3 
                AND ("VKG_LR" = %s OR "ID_TRASSE" && %s::bigint[])
                AND "VERBUNDNUMMER" IS NOT NULL
            """, (self.selected_verteiler, trassen_ids_str))

            verwendete_nummern = {int(row[0]) for row in cur.fetchall() if row[0] is not None}
            print(f"DEBUG: Gefundene verwendete Verbundnummern: {verwendete_nummern}")

            # Ermittle die höchste bisher verwendete Nummer, um die Zählung fortzusetzen
            max_nummer = max(verwendete_nummern) if verwendete_nummern else 0
            print(f"DEBUG: Maximale Nummer: {max_nummer}")

            # Befülle die ComboBox mit Verbundnummern (startend bei 1, fortlaufend, deaktiviere bereits genutzte Nummern)
            for nummer in range(1, max_nummer + 11):  # Erlaube 10 zusätzliche Nummern über die höchste hinaus
                self.ui.comboBox_Verbundnummer.addItem(str(nummer))
                if nummer in verwendete_nummern:
                    index = self.ui.comboBox_Verbundnummer.count() - 1
                    item = self.ui.comboBox_Verbundnummer.model().item(index)
                    item.setEnabled(False)
                    print(f"DEBUG: Nummer {nummer} wurde deaktiviert (bereits verwendet).")

            # Automatisch die nächste freie Verbundnummer setzen (die kleinste verfügbare)
            freie_nummer = next((n for n in range(1, max_nummer + 11) if n not in verwendete_nummern), None)
            if freie_nummer:
                self.ui.comboBox_Verbundnummer.setCurrentText(str(freie_nummer))
                print(f"DEBUG: Erste freie Nummer gesetzt: {freie_nummer}")
            else:
                self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)  # Keine freie Nummer gefunden
                print("DEBUG: Keine freie Verbundnummer gefunden.")

        except Exception as e:
            self.ui.label_Status.setText(f"Fehler beim Abrufen der Verbundnummern: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
            print(f"DEBUG: Fehler bei der Datenbankabfrage: {e}")

        finally:
            print("DEBUG: Schließe Datenbankverbindung")
            if cur:
                cur.close()
            if conn:
                conn.close()                  

    def populate_gefoerdert_subduct(self):
        """Setzt die CheckBoxen für 'Gefördert' und 'Subduct' auf Standardwerte, unabhängig von Codierung."""
        print("DEBUG: Starte populate_gefoerdert_subduct")
        self.ui.checkBox_Foerderung.setChecked(False)  # Standard: Nicht gefördert
        self.ui.checkBox_Subduct.setChecked(False)     # Standard: Kein Subduct
        print("DEBUG: CheckBoxen für 'Gefördert' und 'Subduct' auf False gesetzt")

    def populate_farbschema(self):
        """Füllt die ComboBox für Farbschema basierend auf der gewählten Firma und Typ."""
        self.ui.comboBox_Farbschema.blockSignals(True)
        self.ui.comboBox_Farbschema.clear()
        self.ui.comboBox_Farbschema.setEnabled(False)

        firma = self.ui.comboBox_Firma.currentData()  # Textwert (z. B. "Gabocom")
        typ_id = self.ui.comboBox_leerrohr_typ.currentData()  # Numerische ID_TYP (z. B. 3)

        if not firma or not typ_id:
            self.ui.comboBox_Farbschema.addItem("Bitte Firma wählen")
            self.ui.comboBox_Farbschema.blockSignals(False)
            return

        db_details = self.get_database_connection()
        conn = None
        cur = None

        try:
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()

            # Lade NUR die Farbschemata (CODIERUNG) und ihre IDs für die gewählte Firma & Typ
            cur.execute("""
                SELECT "CODIERUNG", "ID_CODIERUNG"
                FROM lwl."LUT_Leerrohr_SubTyp"
                WHERE "FIRMA" = %s AND "ID_TYP" = %s
                GROUP BY "CODIERUNG", "ID_CODIERUNG"
                ORDER BY "CODIERUNG";
            """, (firma, typ_id))

            rows = cur.fetchall()

            if rows:
                self.ui.comboBox_Farbschema.setEnabled(True)
                for codierung, codierung_id in rows:
                    self.ui.comboBox_Farbschema.addItem(codierung, codierung_id)  # Text als sichtbar, ID als data

                # Falls das bisherige Farbschema noch verfügbar ist → beibehalten (Text oder ID prüfen)
                previous_farbschema = self.ui.comboBox_Farbschema.currentData()
                if previous_farbschema in [row[1] for row in rows]:  # Suche nach ID
                    self.ui.comboBox_Farbschema.setCurrentIndex(
                        [row[1] for row in rows].index(previous_farbschema)
                    )
                else:
                    self.ui.comboBox_Farbschema.setCurrentIndex(0)  # Erstes gültiges setzen

            else:
                self.ui.comboBox_Farbschema.addItem("Keine Farbschemata verfügbar")

        except Exception as e:
            self.ui.label_Status.setText(f"Fehler beim Laden der Farbschemata: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        self.ui.comboBox_Farbschema.blockSignals(False)
        self.populate_leerrohr_subtypen()  # Direkt Subtypen neu laden

    def populate_firma(self):
        """Lädt alle Firmen aus LUT_Leerrohr_SubTyp für den ausgewählten Typ, mit Prüfung in LUT_Rohr_Beschreibung."""
        print("DEBUG: Starte populate_firma")
        self.ui.comboBox_Firma.blockSignals(True)
        self.ui.comboBox_Firma.clear()
        self.ui.comboBox_Firma.setEnabled(True)  # Immer aktiviert

        typ_id = self.ui.comboBox_leerrohr_typ.currentData()  # ID_TYP

        if not typ_id:
            self.ui.comboBox_Firma.addItem("Bitte Typ wählen")
            self.ui.comboBox_Firma.blockSignals(False)
            self.ui.label_gewaehltes_leerrohr_2.clear()
            self.ui.label_gewaehltes_leerrohr_2.setStyleSheet("")
            return

        db_details = self.get_database_connection()
        conn = None
        cur = None

        try:
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()

            print("DEBUG: Lade alle Firmen aus LUT_Leerrohr_SubTyp für Typ, mit Prüfung in LUT_Rohr_Beschreibung")
            cur.execute("""
                SELECT DISTINCT ls."FIRMA"
                FROM lwl."LUT_Leerrohr_SubTyp" ls
                JOIN lwl."LUT_Rohr_Beschreibung" rb ON ls."id" = rb."ID_SUBTYP"
                WHERE ls."ID_TYP" = %s
                ORDER BY ls."FIRMA";
            """, (typ_id,))
            firmen = cur.fetchall()
            print(f"DEBUG: Gefundene Firmen: {firmen}")

            if not firmen:
                self.ui.comboBox_Firma.addItem("Keine Firma verfügbar")
                self.ui.comboBox_Firma.blockSignals(False)
                self.ui.label_gewaehltes_leerrohr_2.clear()
                self.ui.label_gewaehltes_leerrohr_2.setStyleSheet("")
                return

            # Fülle das Dropdown mit Firmennamen und setze den Firmennamen als data
            for firma_name, in firmen:
                print(f"DEBUG: Hinzufügen zu Dropdown – Firma: {firma_name}")
                self.ui.comboBox_Firma.addItem(firma_name, firma_name)  # Setze den Firmennamen als data

            # Behalte die vorherige Firma, falls verfügbar
            previous_firma = self.ui.comboBox_Firma.currentData()
            if previous_firma in [f[0] for f in firmen]:
                self.ui.comboBox_Firma.setCurrentIndex(
                    [f[0] for f in firmen].index(previous_firma)
                )
            else:
                self.ui.comboBox_Firma.setCurrentIndex(0)  # Erstes gültiges setzen

        except Exception as e:
            self.ui.label_Status.setText(f"Fehler beim Laden der Firmen: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
            print(f"DEBUG: Fehler bei der Abfrage der Firmen: {e}")

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        self.ui.comboBox_Firma.blockSignals(False)
        self.populate_farbschema()  # Direkt Codierungen neu laden

    def update_selected_leerrohr_subtyp(self):
        """Aktualisiert das Label für den gewählten Subtyp, basierend auf Codierung und Subtypen aus LUT_Leerrohr_SubTyp."""
        print("DEBUG: Starte update_selected_leerrohr_subtyp")
        if self.ui.comboBox_leerrohr_typ_2.currentIndex() >= 0:
            subtyp_text = self.ui.comboBox_leerrohr_typ_2.currentText()
            selected_subtyp = self.ui.comboBox_leerrohr_typ_2.currentData()
            print(f"DEBUG: Gewählter Subtyp – Text: {subtyp_text}, Data: {selected_subtyp}")
            self.ui.label_gewaehltes_leerrohr_2.setText(subtyp_text)
        else:
            self.ui.label_gewaehltes_leerrohr_2.clear()
            print("DEBUG: Kein Subtyp ausgewählt, Label geleert")

    def activate_trasse_selection(self):
        # Setze das Label zurück
        self.ui.label_verlauf.clear()
        
        # Entferne alle bestehenden Highlights
        for highlight in self.trasse_highlights:
            highlight.hide()
        self.trasse_highlights.clear()

        # Leere die Liste der ausgewählten Trassen
        self.selected_trasse_ids.clear()

        # Aktiviere das MapTool zur Trassenauswahl
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.select_trasse)
        self.iface.mapCanvas().setMapTool(self.map_tool)
            
    def update_verbundnummer_dropdown(self):
        """Aktualisiert das Verbundnummer-Dropdown basierend auf dem Leerrohrtyp, unabhängig von Codierung."""
        print("DEBUG: Starte update_verbundnummer_dropdown")
        typ_id = self.ui.comboBox_leerrohr_typ.currentData()  # Holt den aktuellen Typ
        print(f"DEBUG: Ausgewählter Leerrohrtyp (currentData): {typ_id}")

        if typ_id == 3:  # Multi-Rohr → Verbundnummer wählbar
            self.ui.comboBox_Verbundnummer.setEnabled(True)
            self.populate_verbundnummer()  # Verfügbare Nummern abrufen, berücksichtigt Codierung indirekt
            print("DEBUG: Verbundnummer-Dropdown aktiviert für Multi-Rohr (Typ 3)")
        else:
            # Alle anderen Typen → Deaktiviert anzeigen
            self.ui.comboBox_Verbundnummer.clear()
            self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")  
            self.ui.comboBox_Verbundnummer.setCurrentIndex(0)
            self.ui.comboBox_Verbundnummer.setEnabled(False)
            print(f"DEBUG: Verbundnummer-Dropdown deaktiviert für Typ {typ_id}")

    def pruefe_daten(self):
        """Prüft, ob die Pflichtfelder korrekt gefüllt sind und die Daten logisch zusammenpassen, unabhängig von Codierung."""
        print("DEBUG: Starte pruefe_daten")
        fehler = []

        typ_id = self.ui.comboBox_leerrohr_typ.currentData()  # Holt den aktuellen Leerrohr-Typ
        verbundnummer = self.ui.comboBox_Verbundnummer.currentText().strip()
        codierung = self.ui.comboBox_Farbschema.currentText().strip()
        subtyp_id = self.ui.comboBox_leerrohr_typ_2.currentData()

        # DEBUG: Logge grundlegende Informationen zur Nachverfolgung
        print(f"DEBUG: Prüfe Daten – selected_verteiler: {self.selected_verteiler}, selected_verteiler_2: {self.selected_verteiler_2}")
        print(f"DEBUG: Selected Trasse IDs: {self.selected_trasse_ids_flat}")

        # ✅ 1. Pflichtfelder für Hauptrohre und Multi-Rohre unterschiedlich behandeln
        if self.ui.radioButton_Abzweigung.isChecked():
            if not (self.selected_parent_leerrohr and self.selected_verteiler and self.selected_verteiler_2):
                fehler.append("Bitte wähle Parent-Leerrohr, Start- und Endknoten der Abzweigung aus.")
            # Prüfe, ob selected_verteiler auf parent_trasse_ids liegt
            parent_trasse_ids = self.selected_parent_leerrohr["ID_TRASSE"]
            trasse_ids_str = "{" + ",".join(str(int(id)) for id in parent_trasse_ids) + "}"
            sql_query = f"""
                SELECT COUNT(*) 
                FROM lwl."LWL_Trasse" 
                WHERE id = ANY('{trasse_ids_str}'::bigint[])
                AND ("VONKNOTEN" = {self.selected_verteiler} OR "NACHKNOTEN" = {self.selected_verteiler})
            """
            result = self.db_execute(sql_query)
            if not (result and result[0][0] > 0):
                fehler.append("Der Startknoten der Abzweigung liegt nicht auf der Trasse des Parent-Leerrohrs.")
        else:
            if not (self.selected_verteiler and self.selected_verteiler_2):
                fehler.append("Bitte wähle Start- und Endknoten aus.")
        
        # Prüfe Pflichtfelder für Codierung und Subtyp
        if not codierung or codierung == "Keine Codierungen verfügbar" or codierung == "Bitte Firma wählen":
            fehler.append("Bitte wähle eine gültige Codierung aus.")
        
        if not subtyp_id or subtyp_id is None or self.ui.comboBox_leerrohr_typ_2.currentText() in ["Keine Subtypen verfügbar", "Bitte Firma und Codierung wählen"]:
            fehler.append("Bitte wähle einen gültigen Subtyp aus.")

        if typ_id == 3 and (not verbundnummer or not verbundnummer.isdigit()):
            fehler.append("Keine gültige Verbundnummer für Multi-Rohr gewählt.")
        elif typ_id != 3 and verbundnummer != "Deaktiviert":
            fehler.append("Verbundnummer muss für Nicht-Multi-Rohre 0 sein.")

        # Anpassung für Hauptrohre: Verhindere Datenbankfehler bei "Hauptrohr"
        if typ_id != 3:  # Für Hauptrohre/Zubringerrohre (nicht Multi-Rohr)
            verbundnummer = "0"  # Setze Verbundnummer auf 0, um Datenbankfehler zu vermeiden

        # NEU: Prüfung der Trassen und des Endknotens (für beide Modi)
        if hasattr(self, 'selected_trasse_ids_flat') and self.selected_trasse_ids_flat:
            trassen_ids_list = list(set(self.selected_trasse_ids_flat))  # Entferne Duplikate
            conn = None
            cur = None
            try:
                db_details = self.get_database_connection()
                conn = psycopg2.connect(**db_details)
                cur = conn.cursor()

                # 1. Hole alle Knoten der Trassen
                cur.execute("""
                    SELECT "VONKNOTEN", "NACHKNOTEN"
                    FROM lwl."LWL_Trasse"
                    WHERE id = ANY(%s)
                """, (trassen_ids_list,))
                trassen_knoten = cur.fetchall()
                print(f"DEBUG: Trassen-Knoten: {trassen_knoten}")

                # 2. Zähle Knoten-Vorkommen (ähnlich wie Trigger)
                knoten_counts = {}
                for von_knoten, nach_knoten in trassen_knoten:
                    knoten_counts[von_knoten] = knoten_counts.get(von_knoten, 0) + 1
                    knoten_counts[nach_knoten] = knoten_counts.get(nach_knoten, 0) + 1
                print(f"DEBUG: Knoten-Zählung: {knoten_counts}")

                # 3. Prüfe Start- und Endknoten
                start_knoten = self.selected_verteiler
                end_knoten = self.selected_verteiler_2
                
                if start_knoten not in knoten_counts:
                    fehler.append(f"Startknoten {start_knoten} ist nicht mit den ausgewählten Trassen verbunden.")
                if end_knoten not in knoten_counts:
                    fehler.append(f"Endknoten {end_knoten} ist nicht mit den ausgewählten Trassen verbunden.")
                elif knoten_counts[end_knoten] > 1 and end_knoten != start_knoten:
                    fehler.append(f"Endknoten {end_knoten} kommt mehrfach vor und ist kein gültiger Endknoten.")
                
            except Exception as e:
                fehler.append(f"Datenbankfehler bei der Trassenprüfung: {e}")
            finally:
                if cur:
                    cur.close()
                if conn:
                    conn.close()
        else:
            fehler.append("Keine Trassen ausgewählt.")

        # ✅ 2. Prüfe, ob bereits vergebene Verbundnummer gewählt wurde (nur für Multi-Rohr)
        vorhandene_verbundnummern = set()

        try:
            db_details = self.get_database_connection()
            conn = psycopg2.connect(**db_details)
            cur = conn.cursor()

            if self.ui.radioButton_Abzweigung.isChecked():
                if typ_id == 3 and self.selected_parent_leerrohr and "VKG_LR" in self.selected_parent_leerrohr:
                    cur.execute("""
                        SELECT DISTINCT "VERBUNDNUMMER"
                        FROM lwl."LWL_Leerrohr"
                        WHERE "TYP" = 3 
                        AND "VKG_LR" = %s
                        AND "ID_TRASSE" && %s::bigint[];
                    """, (self.selected_parent_leerrohr["VKG_LR"], "{" + ",".join(map(str, set(self.selected_trasse_ids_flat))) + "}"))
                elif typ_id != 3:  # Überspringe die Abfrage für Hauptrohre/Zubringerrohre
                    pass  # Keine Prüfung der vorhandenen Verbundnummern für Nicht-Multi-Rohre
                else:
                    cur.execute("""
                        SELECT DISTINCT "VERBUNDNUMMER"
                        FROM lwl."LWL_Leerrohr"
                        WHERE "TYP" = 3 
                        AND "VKG_LR" = %s
                        AND "ID_TRASSE" && %s::bigint[];
                    """, (self.selected_verteiler, "{" + ",".join(map(str, set(self.selected_trasse_ids_flat))) + "}"))
            else:
                if typ_id == 3:
                    cur.execute("""
                        SELECT DISTINCT "VERBUNDNUMMER"
                        FROM lwl."LWL_Leerrohr"
                        WHERE "TYP" = 3 
                        AND "VKG_LR" = %s
                        AND "ID_TRASSE" && %s::bigint[];
                    """, (self.selected_verteiler, "{" + ",".join(map(str, set(self.selected_trasse_ids_flat))) + "}"))
                elif typ_id != 3:  # Überspringe die Abfrage für Hauptrohre/Zubringerrohre
                    pass  # Keine Prüfung der vorhandenen Verbundnummern für Nicht-Multi-Rohre

            if typ_id == 3:  # Nur für Multi-Rohre die Prüfung durchführen
                vorhandene_verbundnummern = {int(row[0]) for row in cur.fetchall() if row[0] is not None}

                if verbundnummer and verbundnummer.isdigit() and int(verbundnummer) in vorhandene_verbundnummern:
                    fehler.append(f"Verbundnummer {verbundnummer} ist bereits vergeben.")

        except Exception as e:
            fehler.append(f"Datenbankfehler bei der Verbundnummer-Prüfung: {e}")
        finally:
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                conn.close()

        # ✅ 3. Prüfe, ob mindestens eine Trasse ausgewählt wurde
        if not hasattr(self, 'selected_trasse_ids_flat') or not self.selected_trasse_ids_flat:
            fehler.append("Keine Trassen ausgewählt.")

        # ✅ 4. Falls die Prüfung bestanden wurde → Import ermöglichen
        if fehler:
            self.ui.label_Status.setText("; ".join(fehler))
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
            self.ui.pushButton_Import.setEnabled(False)
        else:
            self.ui.label_Status.setText("Prüfung erfolgreich. Import möglich.")
            self.ui.label_Status.setStyleSheet("background-color: lightgreen;")
            self.ui.pushButton_Import.setEnabled(True)

        print(f"DEBUG: Prüfe Daten – selected_trasse_ids_flat: {self.selected_trasse_ids_flat}")

    def importiere_daten(self):
        """Importiert die Daten aus dem Formular in die Tabelle lwl.LWL_Leerrohr oder lwl.LWL_Leerrohr_Abzweigung, berücksichtigt Codierung statt Farbschema."""
        print("DEBUG: Starte importiere_daten")
        conn = None
        try:
            print("DEBUG: Hole Datenbankverbindung")
            db_details = self.get_database_connection()
            conn = psycopg2.connect(**db_details)
            cur = conn.cursor()
            conn.autocommit = False
            print("DEBUG: Datenbankverbindung erfolgreich")

            if self.ui.radioButton_Abzweigung.isChecked():
                print("DEBUG: Abzweigungsmodus aktiviert")
                trassen_ids_pg_array = "{" + ",".join(map(str, self.selected_trasse_ids_flat)) + "}"
                count = self.selected_parent_leerrohr.get("COUNT", 0) or 0
                status = "AKTIV"
                verfuegbare_rohre = self.selected_parent_leerrohr.get("VERFUEGBARE_ROHRE", "{1,2,3}")
                parent_id = self.selected_parent_leerrohr["id"]
                hilfsknoten_id = self.selected_verteiler
                nach_knoten = self.selected_verteiler_2
                print(f"DEBUG: Abzweigung-Daten - Trassen: {trassen_ids_pg_array}, Parent: {parent_id}, Hilfsknoten: {hilfsknoten_id}, Nachknoten: {nach_knoten}")

                cur.execute("""
                    SELECT COUNT(*) FROM lwl."LWL_Leerrohr_Abzweigung" 
                    WHERE "ID_PARENT_LEERROHR" = %s AND "ID_HILFSKNOTEN" = %s AND "NACHKNOTEN" = %s
                """, (parent_id, hilfsknoten_id, nach_knoten))
                exists = cur.fetchone()[0]
                if exists > 0:
                    raise Exception("Diese Abzweigung existiert bereits.")

                insert_query = """
                INSERT INTO lwl."LWL_Leerrohr_Abzweigung" (
                    "ID_PARENT_LEERROHR", "ID_HILFSKNOTEN", "ID_TRASSE", "COUNT", 
                    "VERFUEGBARE_ROHRE", "STATUS"
                ) VALUES (%s, %s, %s::bigint[], %s, %s, %s)
                """
                values = (parent_id, hilfsknoten_id, hilfsknoten_id, nach_knoten, trassen_ids_pg_array, count, verfuegbare_rohre, status)
                cur.execute(insert_query, values)
                print(f"DEBUG: Abzweigung eingefügt, Rows affected: {cur.rowcount}")
            else:
                print("DEBUG: Hauptstrang-Modus aktiviert")
                trassen_ids_pg_array = "{" + ",".join(map(str, set(self.selected_trasse_ids_flat))) + "}"
                verbundnummer = self.ui.comboBox_Verbundnummer.currentText().strip()
                status = "aktiv"
                gefoerdert = self.ui.checkBox_Foerderung.isChecked()
                subduct = self.ui.checkBox_Subduct.isChecked()
                parent_leerrohr_id = self.selected_subduct_parent if subduct else None
                verfuegbare_rohre = "{1,2,3}"
                typ = self.ui.comboBox_leerrohr_typ.currentData()
                codierung = self.ui.comboBox_Farbschema.currentText().strip()  # Geändert von farbschema zu codierung
                subtyp_id = self.ui.comboBox_leerrohr_typ_2.currentData()
                # NEU: VONKNOTEN und NACHKNOTEN direkt vorgeben
                vonknoten = self.selected_verteiler
                nachknoten = self.selected_verteiler_2
                kommentar = self.ui.label_Kommentar.text().strip() or None
                beschreibung = self.ui.label_Kommentar_2.text().strip() or None
                verlegt_am = self.ui.mDateTimeEdit_Strecke.date().toString("yyyy-MM-dd")

                print(f"DEBUG: Hauptstrang-Daten - Trassen: {trassen_ids_pg_array}, Verbundnummer: {verbundnummer}, Typ: {typ}, Codierung: {codierung}, Subtyp: {subtyp_id}, Von: {vonknoten}, Nach: {nachknoten}, Subduct: {subduct}, Kommentar: {kommentar}, Beschreibung: {beschreibung}, Verlegt_am: {verlegt_am}")

                if verbundnummer == "Deaktiviert" or not verbundnummer:
                    verbundnummer = "0" if typ != 3 else None

                # GEÄNDERT: VONKNOTEN und NACHKNOTEN explizit in der INSERT-Abfrage vorgeben, Codierung statt Farbschema
                insert_query = """
                INSERT INTO lwl."LWL_Leerrohr" (
                    "ID_TRASSE", "VERBUNDNUMMER", "VERFUEGBARE_ROHRE", "STATUS", "VKG_LR", 
                    "GEFOERDERT", "SUBDUCT", "PARENT_LEERROHR_ID", "TYP", "FARBSCHEMA", "SUBTYP", 
                    "VONKNOTEN", "NACHKNOTEN", "KOMMENTAR", "BESCHREIBUNG", "VERLEGT_AM"
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                # Korrigiere die Spalte "FARBSCHEMA" zu "CODIERUNG" in der INSERT-Abfrage
                insert_query = insert_query.replace('"FARBSCHEMA"', '"CODIERUNG"')
                values = (
                    trassen_ids_pg_array, verbundnummer, verfuegbare_rohre, status, vonknoten,
                    gefoerdert, subduct, parent_leerrohr_id, typ, codierung, subtyp_id,
                    vonknoten, nachknoten, kommentar, beschreibung, verlegt_am
                )
                cur.execute(insert_query, values)
                print(f"DEBUG: Hauptstrang eingefügt, Rows affected: {cur.rowcount}")

            conn.commit()
            print("DEBUG: Commit erfolgreich")
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)
            self.initialisiere_formular()

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

        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        if layer:
            layer.triggerRepaint()
            print("DEBUG: Layer aktualisiert")

    def initialisiere_formular(self):
        """Setzt das Formular zurück, entfernt vorhandene Highlights, es sei denn, Mehrfachimport ist aktiviert, unabhängig von Codierung."""
        print("DEBUG: Starte initialisiere_formular")

        # Prüfe, ob Mehrfachimport aktiviert ist
        if not self.ui.checkBox_clearForm.isChecked():
            # 1️⃣ Debug-Ausgabe: Vor dem Entfernen der Highlights
            if hasattr(self, "route_highlights"):
                print(f"DEBUG: Anzahl der Highlights VOR Reset: {len(self.route_highlights)}")
            
            # 2️⃣ Alle Variablen und UI-Elemente zurücksetzen
            self.selected_verteiler = None
            self.selected_verteiler_2 = None

            self.ui.label_gewaehlter_verteiler.setText("Verteiler wählen!")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
            self.ui.label_gewaehlter_verteiler_2.setText("Verteiler wählen!")
            self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")

            self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)
            self.ui.pushButton_Import.setEnabled(False)

            # 3️⃣ Routing-Highlights und andere Highlights entfernen
            self.clear_routing()

            # 4️⃣ Entferne Highlights für Verteiler
            if hasattr(self, "verteiler_highlight_1") and self.verteiler_highlight_1:
                self.verteiler_highlight_1.hide()
                self.verteiler_highlight_1 = None

            if hasattr(self, "verteiler_highlight_2") and self.verteiler_highlight_2:
                self.verteiler_highlight_2.hide()
                self.verteiler_highlight_2 = None

            # 5️⃣ Zurücksetzen von Trassen-IDs
            self.selected_trasse_ids = []
            self.selected_trasse_ids_flat = []

            self.ui.label_Status.clear()
            self.ui.label_Status.setStyleSheet("")

            # Korrigierte CheckBoxen statt ComboBoxen
            self.ui.checkBox_Foerderung.setChecked(False)  # Standard: Nicht gefördert
            self.ui.checkBox_Subduct.setChecked(False)     # Standard: Kein Subduct

            # 6️⃣ Debug-Ausgabe: Nach dem Entfernen
            if hasattr(self, "route_highlights"):
                print(f"DEBUG: Anzahl der Highlights NACH Reset: {len(self.route_highlights)}")

            print("DEBUG: Formular wurde erfolgreich zurückgesetzt.")
        else:
            # 1️⃣ Bei aktiviertem Mehrfachimport: Nur Verbundnummer aktualisieren, wenn Multi-Rohr ausgewählt ist
            selected_typ = self.ui.comboBox_leerrohr_typ.currentData()
            if selected_typ == 3:  # Nur für Multi-Rohr
                print("DEBUG: Mehrfachimport aktiviert – aktualisiere Verbundnummer für Multi-Rohr")
                self.populate_verbundnummer()
            else:
                print("DEBUG: Mehrfachimport aktiviert, aber kein Multi-Rohr – keine Änderungen")

            # 2️⃣ Highlights und andere UI-Elemente unverändert lassen
            self.ui.pushButton_Import.setEnabled(True)  # Import bleibt aktiviert für weitere Imports

    def clear_trasse_selection(self):
        """Setzt die Trassenauswahl zurück, entfernt Highlights und initialisiert UI-Elemente, unabhängig von Codierung."""
        print("DEBUG: Starte clear_trasse_selection")
        # Setze Default-Werte für Label und Felder
        self.ui.label_gewaehlter_verteiler.setText("Verteiler wählen!")
        self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
        
        self.ui.label_gewaehlter_verteiler_2.setText("Verteiler wählen!")
        self.ui.label_gewaehlter_verteiler_2.setStyleSheet("background-color: lightcoral;")
        
        self.ui.label_Kommentar.setText("")
        self.ui.label_Kommentar_2.setText("")
        
        self.selected_verteiler = None  # Sicherstellen, dass der Wert zurückgesetzt wird
        self.selected_verteiler_2 = None  # Sicherstellen, dass der Wert zurückgesetzt wird
                            
        # Entferne das Highlight für den Verteilerkasten
        if hasattr(self, "verteiler_highlight_1") and self.verteiler_highlight_1:
            self.verteiler_highlight_1.hide()
            self.verteiler_highlight_1 = None

        if hasattr(self, "verteiler_highlight_2") and self.verteiler_highlight_2:
            self.verteiler_highlight_2.hide()
            self.verteiler_highlight_2 = None

        self.ui.label_Status.clear()
        self.ui.label_Status.setStyleSheet("")

        # Korrigierte CheckBoxen statt ComboBoxen
        self.ui.checkBox_Foerderung.setChecked(False)  # Standard: Nicht gefördert
        self.ui.checkBox_Subduct.setChecked(False)     # Standard: Kein Subduct
        
        self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)
        self.ui.pushButton_Import.setEnabled(False)
        
        # Leere und initialisiere Subduct-Label
        self.ui.label_Subduct.setText("")  # Leert das Label
        self.ui.label_Subduct.setStyleSheet("background-color: ;")  # Setzt Standard-Styling
        
        # 3️⃣ Routing-Highlights entfernen
        self.clear_routing()

        # 4️⃣ Zurücksetzen von Trassen-IDs
        self.selected_trasse_ids = []
        self.selected_trasse_ids_flat = []

        # 5️⃣ Debug-Ausgabe: Nach dem Entfernen
        if hasattr(self, "route_highlights"):
            print(f"DEBUG: Anzahl der Highlights NACH Reset: {len(self.route_highlights)}")

        print("DEBUG: Formular wurde erfolgreich zurückgesetzt.")

    def close_tool(self):
        """Schließt das Tool und löscht alle Highlights, unabhängig von Codierung."""
        print("DEBUG: Schließe Tool und entferne alle Highlights")
        self.clear_trasse_selection()
        if self.map_tool:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
            self.map_tool = None
        
        if hasattr(self, "verteiler_highlight_1") and self.verteiler_highlight_1:
            self.verteiler_highlight_1.hide()
            self.verteiler_highlight_1 = None
            print("DEBUG: Startknoten-Highlight entfernt")
        
        if hasattr(self, "verteiler_2_highlight") and self.verteiler_2_highlight:
            self.verteiler_2_highlight.hide()
            self.verteiler_2_highlight = None
            print("DEBUG: Endknoten-Highlight entfernt")
        
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
        self.close()

    def closeEvent(self, event):
        """Überschreibt das Schließen des Fensters über das rote 'X', unabhängig von Codierung."""
        print("DEBUG: Starte closeEvent")
        self.close_tool()
        event.accept()
        print("DEBUG: Fenster-Schließereignis akzeptiert")