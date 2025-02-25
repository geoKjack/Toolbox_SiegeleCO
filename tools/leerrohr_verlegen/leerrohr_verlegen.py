import logging
from qgis.core import QgsVectorLayer, QgsFeature, QgsProject, QgsDataSourceUri, Qgis, QgsGeometry, QgsFeatureRequest, QgsMessageLog
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QCheckBox, QMessageBox
from qgis.PyQt.QtCore import Qt
from .leerrohr_verlegen_dialog import Ui_LeerrohrVerlegungsToolDialogBase
from qgis.PyQt.QtSql import QSqlDatabase, QSqlQuery
from qgis.gui import QgsHighlight
from qgis.PyQt.QtGui import QColor
import psycopg2

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

        # **Variablen für die gewählten Objekte**
        self.selected_verteiler = None
        self.selected_verteiler_2 = None
        self.selected_parent_leerrohr = None
        self.selected_knoten_abzweigung = None

        # **Verknüpfe Buttons mit bestehenden Methoden**
        self.ui.pushButton_verteiler.clicked.connect(self.select_verteiler)
        self.ui.pushButton_verteiler_2.clicked.connect(self.select_verteiler_2)
        self.ui.pushButton_Parent_Leerrohr.clicked.connect(self.select_parent_leerrohr)
        self.ui.pushButton_Knoten_Abzweigung.clicked.connect(self.select_knoten_abzweigung)
        
        self.ui.pushButton_routing.clicked.connect(self.start_routing)

        self.ui.pushButton_Datenpruefung.clicked.connect(self.pruefe_daten)
        self.ui.pushButton_Import.setEnabled(False)
        self.ui.pushButton_Import.clicked.connect(self.importiere_daten)

        # **Reset & Cancel Buttons**
        reset_button = self.ui.button_box.button(QDialogButtonBox.Reset)
        cancel_button = self.ui.button_box.button(QDialogButtonBox.Cancel)
        if reset_button:
            reset_button.clicked.connect(self.clear_trasse_selection)
        if cancel_button:
            cancel_button.clicked.connect(self.close_tool)

        # **Map-Tool-Variablen**
        self.map_tool = None
        self.selected_trasse_ids = []
        self.trasse_highlights = []
        self.verteiler_highlight_1 = None
        self.verteiler_highlight_2 = None

        # **Radiobuttons für Verlegungsmodus**
        self.ui.radioButton_Hauptstrang.toggled.connect(self.update_verlegungsmodus)
        self.ui.radioButton_Abzweigung.toggled.connect(self.update_verlegungsmodus)

        # **Dropdown-Verknüpfungen**
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.update_selected_leerrohr_typ)
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.populate_leerrohr_subtypen)
        self.ui.comboBox_leerrohr_typ_2.currentIndexChanged.connect(self.update_selected_leerrohr_subtyp)
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.update_combobox_states)

        # **🚀 Korrekte Reihenfolge für Abhängigkeiten**
        self.ui.comboBox_Firma.currentIndexChanged.connect(self.populate_farbschema)  
        self.ui.comboBox_Farbschema.currentIndexChanged.connect(self.populate_leerrohr_subtypen)  
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.populate_firma)

        # **Setze Standardzustand (Firma deaktiviert)**
        self.ui.comboBox_Firma.setEnabled(False)

        # **Direkte Initialisierung**
        self.populate_leerrohr_typen()
        self.populate_gefoerdert_subduct()
        self.populate_farbschema()  
        self.update_verbundnummer_dropdown()  # Ändere auf update_verbundnummer_dropdown, falls vorhanden

        # **Erzwinge eine Initialisierung des Verlegungsmodus**
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
        """Aktiviert oder deaktiviert Felder je nach Auswahl von Hauptstrang/Abzweigung."""
        print("DEBUG: Starte update_verlegungsmodus")
        if self.ui.radioButton_Hauptstrang.isChecked():
            # ✅ Hauptstrang-Modus → Typ & Subtyp aktivieren, Parent & Knoten deaktivieren
            self.populate_leerrohr_typen()
            self.populate_leerrohr_subtypen()

            self.ui.comboBox_leerrohr_typ.setEnabled(True)
            self.ui.comboBox_leerrohr_typ_2.setEnabled(True)

            self.ui.pushButton_Parent_Leerrohr.setEnabled(False)  
            self.ui.pushButton_Knoten_Abzweigung.setEnabled(False)   

            # Attribute aktivieren
            self.ui.comboBox_Verbundnummer.setEnabled(self.ui.comboBox_leerrohr_typ.currentData() == 3)
            print(f"DEBUG: Verbundnummer-Status in Hauptstrang-Modus: {self.ui.comboBox_Verbundnummer.isEnabled()}")
            self.ui.comboBox_Farbschema.setEnabled(True)
            self.ui.comboBox_Gefoerdert.setEnabled(True)
            self.ui.comboBox_Subduct.setEnabled(True)
            self.ui.label_Kommentar.setEnabled(True)
            self.ui.label_Kommentar_2.setEnabled(True)
            self.ui.mDateTimeEdit_Strecke.setEnabled(True)

            # **Firma-ComboBox wird nur aktiviert, wenn update_combobox_states() es erlaubt**
            self.update_combobox_states()

        else:
            # ✅ Abzweigungs-Modus → Typ & Subtyp deaktivieren, Parent & Knoten aktivieren
            self.ui.comboBox_leerrohr_typ.clear()
            self.ui.comboBox_leerrohr_typ.addItem("Deaktiviert")
            self.ui.comboBox_leerrohr_typ.setEnabled(False)
            
            self.ui.comboBox_leerrohr_typ_2.clear()
            self.ui.comboBox_leerrohr_typ_2.addItem("Deaktiviert")
            self.ui.comboBox_leerrohr_typ_2.setEnabled(False)

            self.ui.pushButton_Parent_Leerrohr.setEnabled(True)  
            self.ui.pushButton_Knoten_Abzweigung.setEnabled(True)  

            # **Firma ZWANGSWEISE deaktivieren**
            self.ui.comboBox_Firma.clear()  
            self.ui.comboBox_Firma.setEnabled(False)  

            # **Attribute deaktivieren, aber Werte aus Parent-Leerrohr übernehmen**
            self.ui.comboBox_Verbundnummer.setEnabled(False)
            print(f"DEBUG: Verbundnummer-Status in Abzweigungs-Modus: {self.ui.comboBox_Verbundnummer.isEnabled()}")
            self.ui.comboBox_Farbschema.setEnabled(False)
            self.ui.comboBox_Gefoerdert.setEnabled(False)
            self.ui.comboBox_Subduct.setEnabled(False)
            self.ui.label_Kommentar.setEnabled(False)
            self.ui.label_Kommentar_2.setEnabled(False)
            self.ui.mDateTimeEdit_Strecke.setEnabled(False)

            # Falls Parent-Leerrohr gewählt wurde → Werte übernehmen
            if self.selected_parent_leerrohr:
                if "VERBUNDNUMMER" in self.selected_parent_leerrohr:
                    parent_verbundnummer = self.selected_parent_leerrohr["VERBUNDNUMMER"]
                    if self.ui.comboBox_leerrohr_typ.currentData() == 3:  # Nur Multi-Rohr
                        index = self.ui.comboBox_Verbundnummer.findText(str(parent_verbundnummer))
                        if index != -1:
                            self.ui.comboBox_Verbundnummer.setCurrentIndex(index)
                    else:
                        self.ui.comboBox_Verbundnummer.clear()
                        self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")
                        self.ui.comboBox_Verbundnummer.setCurrentIndex(0)

                if "FARBSCHEMA" in self.selected_parent_leerrohr:
                    index = self.ui.comboBox_Farbschema.findText(self.selected_parent_leerrohr["FARBSCHEMA"])
                    if index != -1:
                        self.ui.comboBox_Farbschema.setCurrentIndex(index)

                if "GEFOERDERT" in self.selected_parent_leerrohr:
                    self.ui.comboBox_Gefoerdert.setCurrentText("Ja" if self.selected_parent_leerrohr["GEFOERDERT"] else "Nein")

                if "SUBDUCT" in self.selected_parent_leerrohr:
                    self.ui.comboBox_Subduct.setCurrentText("Ja" if self.selected_parent_leerrohr["SUBDUCT"] else "Nein")

                if "KOMMENTAR" in self.selected_parent_leerrohr:
                    self.ui.label_Kommentar.setText(self.selected_parent_leerrohr["KOMMENTAR"])

                if "BESCHREIBUNG" in self.selected_parent_leerrohr:
                    self.ui.label_Kommentar_2.setText(self.selected_parent_leerrohr["BESCHREIBUNG"])

                if "VERLEGT_AM" in self.selected_parent_leerrohr:
                    self.ui.mDateTimeEdit_Strecke.setDate(self.selected_parent_leerrohr["VERLEGT_AM"])

    def select_verteiler(self):
        """Aktiviert das Map-Tool zum Auswählen des ersten Verteilers/Knotens."""
        self.ui.label_gewaehlter_verteiler.clear()  # Label zurücksetzen

        # Aktiviere MapTool zur Auswahl
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.verteiler_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

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
        """Aktiviert das Map-Tool zum Auswählen des zweiten Verteilers/Knotens."""
        self.ui.label_gewaehlter_verteiler_2.clear()  # Label zurücksetzen

        # Falls das MapTool bereits verbunden ist, zuerst trennen
        if self.map_tool:
            try:
                self.map_tool.canvasClicked.disconnect()
            except TypeError:
                pass  # Falls nichts verbunden ist, gibt es keinen Fehler

        # Aktiviere MapTool zur Auswahl
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.verteiler_2_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

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
        start_id = self.selected_verteiler  # Startpunkt
        end_id = self.selected_verteiler_2  # Endpunkt

        # Prüfen, ob Werte vorhanden sind
        if not start_id or not end_id:
            self.ui.label_Status.setText("Bitte Start- und Endknoten auswählen!")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
            return

        try:
            start_id = int(start_id)
            end_id = int(end_id)
        except ValueError:
            self.ui.label_Status.setText("Knoten-IDs müssen Zahlen sein!")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
            return

        # 2️⃣ Routing-SQL-Query mit pgr_ksp für 3 kürzeste Pfade
        sql_query = """
            SELECT seq, path_id, edge FROM pgr_ksp(
                'SELECT id, "VONKNOTEN" AS source, "NACHKNOTEN" AS target, "LAENGE" AS cost FROM lwl."LWL_Trasse"',
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
                self.ui.label_Status.setText("Kein Pfad gefunden!")
                self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
                return

            # Gruppiere die Ergebnisse nach path_id (für echte alternative Routen)
            routes = {}
            for seq, path_id, edge in result:
                if path_id not in routes:
                    routes[path_id] = []
                if edge is not None and edge != -1:  # Ignoriere -1 (Ende des Pfads)
                    routes[path_id].append(edge)

            # Speichere die Routen als Liste von Listen für selected_trasse_ids und aktuelle Liste für Kompatibilität
            self.selected_trasse_ids = list(routes.values())  # Liste von Listen: [[44437, 44452], [44438, 44439]]
            self.selected_trasse_ids_flat = []  # Flache Liste für Kompatibilität mit anderen Methoden
            for route in routes.values():
                self.selected_trasse_ids_flat.extend(route)

            # Speichere routes_by_path_id für die spätere Nutzung in highlight_selected_route
            self.routes_by_path_id = routes

            print(f"DEBUG: Gefundene Routen nach path_id: {routes}")
            print(f"DEBUG: Nach Routing – selected_trasse_ids (als Liste von Listen): {self.selected_trasse_ids}")
            print(f"DEBUG: Nach Routing – selected_trasse_ids_flat: {self.selected_trasse_ids_flat}")

            # 4️⃣ Hebe alle Routen hervor (3 Farben)
            self.highlight_multiple_routes(list(routes.values()))

            # 5️⃣ Aktiviere MapTool zur Routenauswahl, wenn mehr als eine Route existiert
            if len(routes) > 1:
                self.activate_route_selection()
                self.ui.label_Status.setText("Wählen Sie eine Route aus den hervorgehobenen Pfaden!")
            else:
                self.ui.label_Status.setText("Route berechnet – Import möglich!")
            
            self.ui.label_Status.setStyleSheet("background-color: lightgreen; color: black; font-weight: bold; padding: 5px;")

        except Exception as e:
            self.ui.label_Status.setText(f"Datenbankfehler: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral; color: white; font-weight: bold; padding: 5px;")
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
                        # Suche in allen Routen (verschachtelte Listen)
                        for path_id, route in self.routes_by_path_id.items():
                            if trassen_id in route:  # Prüfe, ob trassen_id in einer der inneren Listen ist
                                # Setze die gesamte Route für diesen path_id als selected_trasse_ids
                                self.tool.selected_trasse_ids = route
                                self.tool.highlight_selected_route()
                                self.tool.iface.mapCanvas().unsetMapTool(self)
                                self.tool.ui.label_Status.setText(f"Route {path_id} ausgewählt – Import möglich!")
                                self.tool.ui.label_Status.setStyleSheet("background-color: lightgreen; color: black; font-weight: bold; padding: 5px;")
                                
                                # Aktualisiere die Verbundnummer basierend auf der ausgewählten Route
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
        """Dummy-Methode für den Button Parent-Leerrohr"""
        QgsMessageLog.logMessage("Parent-Leerrohr Auswahl gedrückt, aber noch nicht implementiert.", "Leerrohr-Tool", level=Qgis.Warning)
        
    def select_knoten_abzweigung(self):
        """Dummy-Methode für den Button Knoten-Abzweigung"""
        QgsMessageLog.logMessage("Knoten-Abzweigung Auswahl gedrückt, aber noch nicht implementiert.", "Leerrohr-Tool", level=Qgis.Warning)

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
        """Füllt die Dropdown-Liste für Leerrohrtypen mit erweitertem Debugging."""
        print("DEBUG: Starte populate_leerrohr_typen")
        db_details = self.get_database_connection()
        conn = psycopg2.connect(
            dbname=db_details["dbname"],
            user=db_details["user"],
            password=db_details["password"],
            host=db_details["host"],
            port=db_details["port"]
        )
        cur = conn.cursor()
        try:
            print("DEBUG: Führe SQL-Abfrage aus: SELECT \"WERT\", \"TYP\" FROM lwl.\"LUT_Leerrohr_Typ\" WHERE \"WERT\" IN (1, 2, 3)")
            cur.execute('SELECT "WERT", "TYP" FROM lwl."LUT_Leerrohr_Typ" WHERE "WERT" IN (1, 2, 3)')
            rows = cur.fetchall()

            print(f"DEBUG: Gefundene Leerrohrtypen aus LUT_Leerrohr_Typ: {rows}")

            self.ui.comboBox_leerrohr_typ.clear()
            for row in rows:
                wert, typ = row
                print(f"DEBUG: Hinzufügen zu Dropdown – Wert: {wert}, Typ: {typ}")
                self.ui.comboBox_leerrohr_typ.addItem(typ, wert)

            print(f"DEBUG: Aktueller Index nach Befüllen: {self.ui.comboBox_leerrohr_typ.currentIndex()}")
            self.ui.comboBox_leerrohr_typ.setCurrentIndex(-1)

        except Exception as e:
            self.ui.label_Status.setText(f"Fehler beim Abrufen der Leerrohrtypen: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
            print(f"DEBUG: Fehler bei der Abfrage der Leerrohrtypen: {e}")
        finally:
            print("DEBUG: Schließe Datenbankverbindung")
            cur.close()
            conn.close()

    def update_selected_leerrohr_typ(self):
        """Aktualisiert das Label für den gewählten Typ und ruft populate_verbundnummer auf, unabhängig vom Typ."""
        print("DEBUG: Starte update_selected_leerrohr_typ")
        if self.ui.comboBox_leerrohr_typ.currentIndex() >= 0:
            typ_text = self.ui.comboBox_leerrohr_typ.currentText()
            selected_typ = self.ui.comboBox_leerrohr_typ.currentData()
            print(f"DEBUG: Gewählter Typ – Text: {typ_text}, Data: {selected_typ}")
            self.ui.label_gewaehltes_leerrohr.setText(typ_text)
            
            # Rufe populate_verbundnummer immer auf, unabhängig vom Typ
            self.populate_verbundnummer()
        else:
            self.ui.label_gewaehltes_leerrohr.clear()
            print("DEBUG: Kein Typ ausgewählt, Label geleert")

    def populate_leerrohr_subtypen(self):
        """Füllt die ComboBox für Leerrohr-Subtypen basierend auf Typ, Farbschema und Firma."""
        self.ui.comboBox_leerrohr_typ_2.blockSignals(True)
        self.ui.comboBox_leerrohr_typ_2.clear()
        self.ui.comboBox_leerrohr_typ_2.setEnabled(False)
        
        # 🚨 NEUER FIX: Label für Subtyp sofort leeren, wenn kein Typ gewählt wurde
        self.ui.label_gewaehltes_leerrohr_2.clear()

        typ_id = self.ui.comboBox_leerrohr_typ.currentData()
        farbschema = self.ui.comboBox_Farbschema.currentText().strip()
        firma = self.ui.comboBox_Firma.currentText().strip()

        # 🚨 Falls kein Typ gewählt ist, brich die Methode sofort ab!
        if not typ_id:
            self.ui.comboBox_leerrohr_typ_2.addItem("Bitte zuerst einen Typ wählen")
            self.ui.comboBox_leerrohr_typ_2.blockSignals(False)
            return

        if not farbschema or not firma:
            self.ui.comboBox_leerrohr_typ_2.addItem("Bitte Farbschema wählen")
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

            # Lade alle Subtypen für Typ + Firma + Farbschema
            cur.execute("""
                SELECT "id", "SUBTYP_char"
                FROM lwl."LUT_Leerrohr_SubTyp"
                WHERE "FARBSCHEMA" = %s AND "FIRMA" = %s AND "ID_TYP" = %s;
            """, (farbschema, firma, typ_id))

            rows = cur.fetchall()

            if rows:
                self.ui.comboBox_leerrohr_typ_2.setEnabled(True)
                for row in rows:
                    self.ui.comboBox_leerrohr_typ_2.addItem(row[1], row[0])

                # 🚀 Qt-Trick: Damit der erste Wert immer wählbar ist
                self.ui.comboBox_leerrohr_typ_2.setCurrentIndex(-1)
                self.ui.comboBox_leerrohr_typ_2.setCurrentIndex(0)

                # 🚀 Direkt das Label für den gewählten Subtyp aktualisieren
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

    def update_selected_leerrohr_subtyp(self):
        """Aktualisiert das Label für den gewählten Subtyp."""
        subtyp_text = self.ui.comboBox_leerrohr_typ_2.currentText()
        if subtyp_text and self.ui.comboBox_leerrohr_typ_2.currentIndex() >= 0:
            self.ui.label_gewaehltes_leerrohr_2.setText(subtyp_text)
        else:
            self.ui.label_gewaehltes_leerrohr_2.clear()

    def get_selected_subtyp_id(self):
        # Prüfe, ob eine Auswahl getroffen wurde
        if self.ui.comboBox_leerrohr_typ_2.currentIndex() == -1:
            raise ValueError("Kein Subtyp ausgewählt.")
        
        # Abrufen der ID des ausgewählten Subtyps
        subtyp_id = self.ui.comboBox_leerrohr_typ_2.currentData()
        return subtyp_id

    def populate_gefoerdert_subduct(self):
        """Füllt die Dropdowns für 'Gefördert' und 'Subduct' mit 'Ja' und 'Nein'."""
        options = ["Ja", "Nein"]

        # Populate Gefördert
        self.ui.comboBox_Gefoerdert.clear()
        self.ui.comboBox_Gefoerdert.addItems(options)
        self.ui.comboBox_Gefoerdert.setCurrentText("Nein")  # Setze die ComboBox auf "keine Auswahl"

        # Populate Subduct
        self.ui.comboBox_Subduct.clear()
        self.ui.comboBox_Subduct.addItems(options)
        self.ui.comboBox_Subduct.setCurrentText("Nein")  # Setze die ComboBox auf "keine Auswahl"

    def populate_verbundnummer(self):
        """Setzt die Verbundnummer basierend auf dem ausgewählten Rohrtyp, dem Routing (falls vorhanden), und ermöglicht Auswahl mit flexibler Zählung."""
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
        if not self.selected_verteiler or not self.selected_trasse_ids:
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

            trassen_ids_str = "{" + ",".join(map(str, set(self.selected_trasse_ids))) + "}"
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
                
    def populate_farbschema(self):
        """Füllt die ComboBox für Farbschema basierend auf der gewählten Firma und Typ."""
        self.ui.comboBox_Farbschema.blockSignals(True)
        self.ui.comboBox_Farbschema.clear()
        self.ui.comboBox_Farbschema.setEnabled(False)

        firma = self.ui.comboBox_Firma.currentText().strip()
        typ_id = self.ui.comboBox_leerrohr_typ.currentData()

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

            # Lade NUR die Farbschemata für die gewählte Firma & Typ
            cur.execute("""
                SELECT DISTINCT "FARBSCHEMA"
                FROM lwl."LUT_Leerrohr_SubTyp"
                WHERE "FIRMA" = %s AND "ID_TYP" = %s
                ORDER BY "FARBSCHEMA";
            """, (firma, typ_id))

            rows = cur.fetchall()

            if rows:
                self.ui.comboBox_Farbschema.setEnabled(True)
                self.ui.comboBox_Farbschema.addItems([row[0] for row in rows])

                # Falls das bisherige Farbschema noch verfügbar ist → beibehalten
                previous_farbschema = self.ui.comboBox_Farbschema.currentText()
                if previous_farbschema in [row[0] for row in rows]:
                    self.ui.comboBox_Farbschema.setCurrentText(previous_farbschema)
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
        """Füllt die ComboBox für Firma basierend auf dem gewählten Leerrohrtyp."""
        self.ui.comboBox_Firma.blockSignals(True)
        self.ui.comboBox_Firma.clear()
        self.ui.comboBox_Firma.setEnabled(False)

        typ_id = self.ui.comboBox_leerrohr_typ.currentData()

        if not typ_id:
            self.ui.comboBox_Firma.addItem("Bitte Typ wählen")
            self.ui.comboBox_Firma.blockSignals(False)
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

            # Lade alle Firmen für den gewählten Typ
            cur.execute("""
                SELECT DISTINCT "FIRMA"
                FROM lwl."LUT_Leerrohr_SubTyp"
                WHERE "ID_TYP" = %s
                ORDER BY "FIRMA";
            """, (typ_id,))

            rows = cur.fetchall()

            if rows:
                self.ui.comboBox_Firma.setEnabled(True)
                self.ui.comboBox_Firma.addItems([row[0] for row in rows])

                # Falls nur eine Firma verfügbar ist → direkt setzen
                if len(rows) == 1:
                    self.ui.comboBox_Firma.setCurrentIndex(0)

            else:
                self.ui.comboBox_Firma.addItem("Keine Firma verfügbar")

        except Exception as e:
            self.ui.label_Status.setText(f"Fehler beim Laden der Firmen: {e}")
            self.ui.label_Status.setStyleSheet("background-color: lightcoral;")

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        self.ui.comboBox_Firma.blockSignals(False)
        self.populate_farbschema()  # Direkt Farbschema neu laden

    def update_selected_leerrohr_subtyp(self):
        """Aktualisiert das Label für den gewählten Subtyp, ohne das Farbschema zu überschreiben."""
        subtyp_text = self.ui.comboBox_leerrohr_typ_2.currentText()

        if subtyp_text and self.ui.comboBox_leerrohr_typ_2.currentIndex() >= 0:
            self.ui.label_gewaehltes_leerrohr_2.setText(subtyp_text)

            # 🚨 Entferne den automatischen Aufruf von populate_farbschema()
            # Die Farbschemata dürfen nicht neu geladen werden, wenn nur der Subtyp wechselt.
        else:
            self.ui.label_gewaehltes_leerrohr_2.clear()
            self.ui.comboBox_Farbschema.clear()
            self.ui.comboBox_Farbschema.addItem("Bitte Subtyp wählen")

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
        """Aktualisiert das Verbundnummer-Dropdown basierend auf dem Leerrohrtyp."""
        typ_id = self.ui.comboBox_leerrohr_typ.currentData()  # Holt den aktuellen Typ

        if typ_id == 3:  # Multi-Rohr → Verbundnummer wählbar
            self.ui.comboBox_Verbundnummer.setEnabled(True)
            self.populate_verbundnummer()  # Verfügbare Nummern abrufen
        else:
            # Alle anderen Typen → Deaktiviert anzeigen
            self.ui.comboBox_Verbundnummer.clear()
            self.ui.comboBox_Verbundnummer.addItem("Deaktiviert")  
            self.ui.comboBox_Verbundnummer.setCurrentIndex(0)
            self.ui.comboBox_Verbundnummer.setEnabled(False)

    def pruefe_daten(self):
        """Prüft, ob die Pflichtfelder korrekt gefüllt sind und die Daten logisch zusammenpassen."""
        fehler = []

        typ_id = self.ui.comboBox_leerrohr_typ.currentData()  # Holt den aktuellen Leerrohr-Typ
        verbundnummer = self.ui.comboBox_Verbundnummer.currentText().strip()

        # ✅ 1. Pflichtfelder für Hauptrohre und Multi-Rohre unterschiedlich behandeln
        if typ_id == 3 and (not verbundnummer or not verbundnummer.isdigit()):
            fehler.append("Keine gültige Verbundnummer für Multi-Rohr gewählt.")
        elif typ_id != 3 and verbundnummer != "Deaktiviert":
            fehler.append("Verbundnummer muss für Nicht-Multi-Rohre 0 sein.")

        # ✅ 2. Prüfe, ob bereits vergebene Verbundnummer gewählt wurde (nur für Multi-Rohr)
        vorhandene_verbundnummern = set()

        try:
            db_details = self.get_database_connection()
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()

            if typ_id == 3:
                cur.execute("""
                    SELECT DISTINCT "VERBUNDNUMMER"
                    FROM lwl."LWL_Leerrohr"
                    WHERE "TYP" = 3 
                    AND "VKG_LR" = %s
                    AND "ID_TRASSE" && %s::bigint[];
                """, (self.selected_verteiler, "{" + ",".join(map(str, set(self.selected_trasse_ids))) + "}"))

                vorhandene_verbundnummern = {int(row[0]) for row in cur.fetchall() if row[0] is not None}

                if verbundnummer and int(verbundnummer) in vorhandene_verbundnummern:
                    fehler.append(f"Verbundnummer {verbundnummer} ist bereits vergeben.")

        except Exception as e:
            fehler.append(f"Datenbankfehler bei der Verbundnummer-Prüfung: {e}")
        finally:
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                conn.close()

        # ✅ 3. Prüfe, ob mindestens eine Trasse ausgewählt wurde
        if not self.selected_trasse_ids:
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

    def ordne_trassen(self, trassen_info):
        """Ordnet die Trassen basierend auf den Knoteninformationen und dem gewählten Verteilerkasten."""
        if not trassen_info or not self.ui.label_gewaehlter_verteiler.toPlainText().strip():
            return trassen_info

        verteiler_id = int(self.ui.label_gewaehlter_verteiler.toPlainText().split(":")[1].strip())

        # Finde die Trasse, die vom Verteilerkasten startet
        start_trasse = None
        for i, (trasse_id, vonknoten, nachknoten) in enumerate(trassen_info):
            if vonknoten == verteiler_id or nachknoten == verteiler_id:
                start_trasse = trassen_info.pop(i)
                # Falls nötig, Richtung anpassen
                if start_trasse[1] != verteiler_id:
                    start_trasse = (start_trasse[0], start_trasse[2], start_trasse[1])
                break

        if not start_trasse:
            # Falls keine passende Trasse gefunden wurde, bleibt die Reihenfolge unverändert
            return trassen_info

        # Reihenfolge anpassen
        geordnete_trassen = [start_trasse]
        while trassen_info:
            letzte_trasse = geordnete_trassen[-1]
            letzte_knoten = letzte_trasse[2]  # NACH-Knoten

            for i, trasse in enumerate(trassen_info):
                if trasse[1] == letzte_knoten:
                    geordnete_trassen.append(trassen_info.pop(i))
                    break
                elif trasse[2] == letzte_knoten:
                    geordnete_trassen.append((trasse[0], trasse[2], trasse[1]))
                    trassen_info.pop(i)
                    break
        return geordnete_trassen

    def importiere_daten(self):
        """Importiert die Daten aus dem Formular in die Tabelle lwl.LWL_Leerrohr."""
        conn = None
        try:
            db_details = self.get_database_connection()
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()
            conn.autocommit = False

            # 🔹 Daten aus UI-Elementen abrufen
            trassen_ids_pg_array = "{" + ",".join(map(str, set(self.selected_trasse_ids))) + "}"
            verbundnummer = self.ui.comboBox_Verbundnummer.currentText().strip()
            kommentar = self.ui.label_Kommentar.text().strip() or None
            beschreibung = self.ui.label_Kommentar_2.text().strip() or None
            farbschema = self.ui.comboBox_Farbschema.currentText().strip() or None
            firma_hersteller = self.ui.comboBox_Firma.currentText().strip() or None

            # ❌ Falls "Deaktiviert" oder leer, setze Verbundnummer auf 0 für Nicht-Multi-Rohr, sonst behalte den Wert
            if verbundnummer == "Deaktiviert" or not verbundnummer:
                verbundnummer = "0" if self.ui.comboBox_leerrohr_typ.currentData() != 3 else None
            elif not verbundnummer.isdigit():
                raise ValueError(f"Ungültige Verbundnummer: {verbundnummer}")

            # 🔹 Sammle Geometrien aller Trassen
            cur.execute("""
                SELECT "id", ST_AsText("geom")
                FROM lwl."LWL_Trasse"
                WHERE "id" = ANY(%s::bigint[])
            """, (self.selected_trasse_ids,))
            trassen_geometrien = cur.fetchall()

            # 🔹 Falls keine gültigen Geometrien → Fehler
            if not trassen_geometrien or len(trassen_geometrien) != len(self.selected_trasse_ids):
                self.ui.label_Status.setText("❌ Fehler: Nicht alle Trassen haben gültige Geometrien.")
                self.ui.label_Status.setStyleSheet("background-color: lightcoral;")
                return

            # 🔹 Verbinde Geometrien zu einer einzigen Linie
            geometrien_wkt = ", ".join([f"ST_GeomFromText('{geom[1]}', 31254)" for geom in trassen_geometrien])
            cur.execute(f"SELECT ST_AsText(ST_LineMerge(ST_Union(ARRAY[{geometrien_wkt}])))")
            verbundene_geometrie = cur.fetchone()[0]

            # 🔹 Einfügen der Daten in die Datenbank
            insert_query = """
            INSERT INTO lwl."LWL_Leerrohr" (
                "ID_TRASSE", "TYP", "SUBTYP", "GEFOERDERT", "SUBDUCT", "VERBUNDNUMMER", 
                "KOMMENTAR", "BESCHREIBUNG", "VERLEGT_AM", "FARBSCHEMA", "FIRMA_HERSTELLER", "VKG_LR", "geom"
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 31254))
            """

            cur.execute(insert_query, (
                trassen_ids_pg_array,
                self.ui.comboBox_leerrohr_typ.currentData(),
                self.ui.comboBox_leerrohr_typ_2.currentData(),
                'TRUE' if self.ui.comboBox_Gefoerdert.currentText() == "Ja" else 'FALSE',
                'TRUE' if self.ui.comboBox_Subduct.currentText() == "Ja" else 'FALSE',
                verbundnummer,  # 🔹 Falls None oder "Deaktiviert" → 0 für Nicht-Multi-Rohr
                kommentar,
                beschreibung,
                self.ui.mDateTimeEdit_Strecke.date().toString("yyyy-MM-dd"),
                farbschema,
                firma_hersteller,
                self.selected_verteiler,
                verbundene_geometrie
            ))

            conn.commit()
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)

            # 🔹 Formular zurücksetzen basierend auf Mehrfachimport-Checkbox
            self.initialisiere_formular()

        except Exception as e:
            if conn:
                conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Import fehlgeschlagen: {str(e)}", level=Qgis.Critical)

        finally:
            if conn:
                conn.close()

        # 🔹 Karte aktualisieren, damit neue Daten sichtbar sind
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        if layer:
            layer.triggerRepaint()

    def initialisiere_formular(self):
        """Setzt das Formular zurück, entfernt vorhandene Highlights, es sei denn, Mehrfachimport ist aktiviert."""
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

            self.ui.label_Status.clear()
            self.ui.label_Status.setStyleSheet("")

            self.ui.comboBox_Gefoerdert.setCurrentIndex(-1)
            self.ui.comboBox_Subduct.setCurrentIndex(-1)

            # 5️⃣ Debug-Ausgabe: Nach dem Entfernen
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

        self.ui.comboBox_Gefoerdert.setCurrentIndex(-1)
        self.ui.comboBox_Subduct.setCurrentIndex(-1)
        self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)
        self.ui.pushButton_Import.setEnabled(False)
        
        # 3️⃣ Routing-Highlights entfernen
        self.clear_routing()

        # 4️⃣ Debug-Ausgabe: Nach dem Entfernen
        if hasattr(self, "route_highlights"):
            print(f"DEBUG: Anzahl der Highlights NACH Reset: {len(self.route_highlights)}")

        print("DEBUG: Formular wurde erfolgreich zurückgesetzt.")

    def close_tool(self):
        """Schließt das Tool und löscht alle Highlights."""
        self.clear_trasse_selection()
        if self.map_tool:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)
            self.map_tool = None
        self.close()

    def closeEvent(self, event):
        """Überschreibt das Schließen des Fensters über das rote 'X'."""
        self.close_tool()
        event.accept()