import logging
from qgis.core import QgsProject, QgsDataSourceUri, Qgis, QgsGeometry, QgsFeatureRequest, QgsMessageLog
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QCheckBox
from qgis.PyQt.QtCore import Qt
from .leerrohr_verlegen_dialog import Ui_LeerrohrVerlegungsToolDialogBase
from qgis.PyQt.QtSql import QSqlDatabase, QSqlQuery
from qgis.gui import QgsHighlight
import psycopg2

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class LeerrohrVerlegenTool(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.ui = Ui_LeerrohrVerlegungsToolDialogBase()
        self.ui.setupUi(self)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        # Initialisiere self.selected_verteiler
        self.selected_verteiler = None
        self.selected_parent_leerrohr = None 

        # **Verknüpfe Buttons**
        self.ui.pushButton_verlauf.clicked.connect(self.activate_trasse_selection)
        self.ui.pushButton_Datenpruefung.clicked.connect(self.pruefe_daten)
        self.ui.pushButton_Import.setEnabled(False)
        self.ui.pushButton_Import.clicked.connect(self.importiere_daten)
        self.ui.pushButton_verteiler.clicked.connect(self.select_verteilerkasten)

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
        self.verteiler_highlight = None

        # **Radiobuttons für Verlegungsmodus**
        self.ui.radioButton_Hauptstrang.toggled.connect(self.update_verlegungsmodus)
        self.ui.radioButton_Abzweigung.toggled.connect(self.update_verlegungsmodus)

        # **Dropdown-Verknüpfungen**
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.update_selected_leerrohr_typ)
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.populate_leerrohr_subtypen)
        self.ui.comboBox_leerrohr_typ_2.currentIndexChanged.connect(self.update_selected_leerrohr_subtyp)
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.update_combobox_states)

        # **🚀 NEUE FIXES: Korrekte Reihenfolge für Abhängigkeiten**
        self.ui.comboBox_Firma.currentIndexChanged.connect(self.populate_farbschema)  # ✅ Firma -> Farbschema aktualisieren
        self.ui.comboBox_Farbschema.currentIndexChanged.connect(self.populate_leerrohr_subtypen)  # ✅ Farbschema -> Subtypen aktualisieren
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.populate_firma)

        # **Setze Standardzustand (Firma deaktiviert)**
        self.ui.comboBox_Firma.setEnabled(False)

        # **Direkte Initialisierung**
        self.populate_leerrohr_typen()
        self.populate_gefoerdert_subduct()
        self.populate_farbschema()  # ✅ Lädt jetzt, wenn eine Firma gewählt wird

        # **Erzwinge eine Initialisierung des Verlegungsmodus**
        self.update_verlegungsmodus()
        
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
        
    def update_verlegungsmodus(self):
        """Aktiviert oder deaktiviert Felder je nach Auswahl von Hauptstrang/Abzweigung."""

        if self.ui.radioButton_Hauptstrang.isChecked():
            # ✅ Hauptstrang-Modus → Typ & Subtyp aktivieren, Parent & Knoten deaktivieren
            self.populate_leerrohr_typen()  # ← Typen neu laden!
            self.populate_leerrohr_subtypen()  # ← Subtypen neu laden!

            self.ui.comboBox_leerrohr_typ.setEnabled(True)
            self.ui.comboBox_leerrohr_typ_2.setEnabled(True)

            self.ui.pushButton_Parent_Leerrohr.setEnabled(False)  
            self.ui.pushButton_Knoten_Abzweigung.setEnabled(False)   

            # Attribute aktivieren
            self.ui.comboBox_Verbundnummer.setEnabled(True)
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
            self.ui.comboBox_leerrohr_typ.clear()  # Erst leeren
            self.ui.comboBox_leerrohr_typ.addItem("Deaktiviert")  # Dann Eintrag hinzufügen
            self.ui.comboBox_leerrohr_typ.setEnabled(False)  # Danach deaktivieren
            
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
            self.ui.comboBox_Farbschema.setEnabled(False)
            self.ui.comboBox_Gefoerdert.setEnabled(False)
            self.ui.comboBox_Subduct.setEnabled(False)
            self.ui.label_Kommentar.setEnabled(False)
            self.ui.label_Kommentar_2.setEnabled(False)
            self.ui.mDateTimeEdit_Strecke.setEnabled(False)

            # Falls Parent-Leerrohr gewählt wurde → Werte übernehmen
            if self.selected_parent_leerrohr:
                if "VERBUNDNUMMER" in self.selected_parent_leerrohr:
                    index = self.ui.comboBox_Verbundnummer.findText(str(self.selected_parent_leerrohr["VERBUNDNUMMER"]))
                    if index != -1:
                        self.ui.comboBox_Verbundnummer.setCurrentIndex(index)

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

    def select_verteilerkasten(self):
        """Aktiviert das Map-Tool zum Auswählen eines Verteilerkastens."""
        self.ui.label_gewaehlter_verteiler.clear()  # Label zurücksetzen

        # Aktiviere MapTool zur Auswahl
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.verteilerkasten_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def verteilerkasten_selected(self, point):
        """Wird ausgelöst, wenn ein Punkt auf der Karte ausgewählt wird."""
        layer_name = "LWL_Knoten"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_gewaehlter_verteiler.setText("Layer 'LWL_Knoten' nicht gefunden")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        nearest_feature = None
        nearest_distance = float("inf")
        threshold_distance = 20  # Maximale Entfernung in Metern

        for feature in layer.getFeatures():
            if feature["TYP"] not in ["Verteilerkasten", "Schacht", "Ortszentrale"]:
                continue
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        # Prüfen, ob der nächste Verteilerkasten innerhalb der Schwelle liegt
        if nearest_feature and nearest_distance <= threshold_distance:
            verteiler_id = nearest_feature["id"]  # ID des Verteilers
            self.selected_verteiler = verteiler_id  # Speichere die ID direkt

            self.ui.label_gewaehlter_verteiler.setText(f"Verteilerkasten ID: {verteiler_id}")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightgreen;")
            self.ui.label_Pruefung.setText("")
            self.ui.label_Pruefung.setStyleSheet("background-color: white;")            

            # Highlight für den Verteilerkasten setzen
            if self.verteiler_highlight:
                self.verteiler_highlight.hide()  # Vorheriges Highlight entfernen
            self.verteiler_highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            self.verteiler_highlight.setColor(Qt.red)
            self.verteiler_highlight.setWidth(5)
            self.verteiler_highlight.show()
            self.selected_verteiler = verteiler_id  # Setze die Verteilerkasten-ID
            QgsMessageLog.logMessage(f"self.selected_verteiler nach Setzen: {self.selected_verteiler}", "ToolBox_SiegeleCo", level=Qgis.Info)
            
        else:
            QgsMessageLog.logMessage("Kein Verteilerkasten gefunden oder außerhalb der Schwelle.", "ToolBox_SiegeleCo", level=Qgis.Warning)
            self.ui.label_Pruefung.setText("Kein Verteilerkasten innerhalb von 20 m gefunden")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")

        self.populate_verbundnummer()  # Aufruf nur nach erfolgreicher Verteiler-Auswahl
        # Deaktiviere MapTool
        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

    def update_combobox_states(self):
        """Aktiviert oder deaktiviert comboBox_Verbundnummer und comboBox_Farbschema basierend auf dem ausgewählten TYP."""
        selected_typ = self.ui.comboBox_leerrohr_typ.currentData()  # Holt den Wert aus der aktuellen Auswahl
        if selected_typ == 3:  # Überprüft, ob der TYP 'Multi-Rohr' ist (TYP = 3)
            self.ui.comboBox_Verbundnummer.setEnabled(True)
            self.ui.comboBox_Farbschema.setEnabled(True)
            self.ui.comboBox_Firma.setEnabled(True)
        else:
            self.ui.comboBox_Verbundnummer.setEnabled(False)
            self.ui.comboBox_Farbschema.setEnabled(False)
            self.ui.comboBox_Firma.setEnabled(False) 
            self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)  # Auswahl zurücksetzen
            self.ui.comboBox_Farbschema.setCurrentIndex(-1)    # Auswahl zurücksetzen
            self.ui.comboBox_Firma.setCurrentIndex(-1)

    def populate_leerrohr_typen(self):
        """Füllt die Dropdown-Liste für Leerrohrtypen."""
        db_details = self.get_database_connection()  # Verbindungsdetails abrufen
        conn = psycopg2.connect(
            dbname=db_details["dbname"],
            user=db_details["user"],
            password=db_details["password"],
            host=db_details["host"],
            port=db_details["port"]
        )
        cur = conn.cursor()
        try:
            # Datenbankabfrage ausführen
            cur.execute('SELECT "WERT", "TYP" FROM lwl."LUT_Leerrohr_Typ" WHERE "WERT" IN (1, 2, 3)')
            rows = cur.fetchall()

            # ComboBox leeren und befüllen
            self.ui.comboBox_leerrohr_typ.clear()
            for row in rows:
                wert, typ = row
                self.ui.comboBox_leerrohr_typ.addItem(typ, wert)

            # Standardmäßig keine Auswahl setzen
            self.ui.comboBox_leerrohr_typ.setCurrentIndex(-1)

        except Exception as e:
            self.ui.label_Pruefung.setText(f"Fehler beim Abrufen der Leerrohrtypen: {e}")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
        finally:
            cur.close()
            conn.close()
            
    def update_selected_leerrohr_typ(self):
        """Aktualisiert das Label für den gewählten Typ."""
        if self.ui.comboBox_leerrohr_typ.currentIndex() >= 0:
            typ_text = self.ui.comboBox_leerrohr_typ.currentText()
            self.ui.label_gewaehltes_leerrohr.setText(typ_text)
        else:
            self.ui.label_gewaehltes_leerrohr.clear()

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
            self.ui.label_Pruefung.setText(f"Fehler beim Laden der Subtypen: {e}")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")

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
        """Füllt die Dropdown für 'Verbundnummer' mit Werten von 1 bis 9 und deaktiviert bereits verwendete Nummern."""
        self.ui.comboBox_Verbundnummer.clear()  # Leert die Dropdown-Liste
        QgsMessageLog.logMessage(f"self.selected_verteiler vor Verwendung in populate_verbundnummer: {self.selected_verteiler}", "ToolBox_SiegeleCo", level=Qgis.Info)

        verwendete_nummern = set()
        db_details = self.get_database_connection()
        conn = None
        cur = None

        try:
            if not hasattr(self, 'selected_verteiler') or not self.selected_verteiler:
                self.ui.label_Pruefung.setText("Kein Verteilerkasten ausgewählt.")
                self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
                return

            verteiler_id = self.selected_verteiler  # Direkt die gespeicherte ID verwenden
            QgsMessageLog.logMessage(f"Starte populate_verbundnummer für Verteilerkasten {verteiler_id}", "ToolBox_SiegeleCo", level=Qgis.Info)

            # Verbindung zur Datenbank herstellen
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()

            # Alle bereits verwendeten Verbundnummern abrufen
            cur.execute(f"""
                SELECT DISTINCT "VERBUNDNUMMER"
                FROM lwl."LWL_Leerrohr"
                WHERE "VKG_LR" = %s
            """, (verteiler_id,))
            verwendete_nummern = {int(row[0]) for row in cur.fetchall() if row[0] is not None}
            QgsMessageLog.logMessage(f"Gefundene verwendete Nummern: {verwendete_nummern}", "ToolBox_SiegeleCo", level=Qgis.Info)

            # Alle möglichen Nummern von 1 bis 9 in die Dropdown-Liste einfügen
            for nummer in range(1, 10):
                self.ui.comboBox_Verbundnummer.addItem(str(nummer))
                if nummer in verwendete_nummern:
                    # Nummer ausgrauen, wenn sie bereits verwendet wurde
                    index = self.ui.comboBox_Verbundnummer.count() - 1
                    item = self.ui.comboBox_Verbundnummer.model().item(index)
                    item.setEnabled(False)

            # Standardmäßig die erste verfügbare Nummer auswählen
            freie_nummer = next((n for n in range(1, 10) if n not in verwendete_nummern), None)
            if freie_nummer:
                self.ui.comboBox_Verbundnummer.setCurrentText(str(freie_nummer))
            else:
                self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)

        except Exception as e:
            self.ui.label_Pruefung.setText(f"Fehler beim Abrufen der Verbundnummern: {e}")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
            QgsMessageLog.logMessage(f"Fehler in populate_verbundnummer: {e}", "ToolBox_SiegeleCo", level=Qgis.Critical)

        finally:
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
            self.ui.label_Pruefung.setText(f"Fehler beim Laden der Farbschemata: {e}")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")

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
            self.ui.label_Pruefung.setText(f"Fehler beim Laden der Firmen: {e}")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

        self.ui.comboBox_Firma.blockSignals(False)
        self.populate_farbschema()  # Direkt Farbschema neu laden

    def update_selected_leerrohr_typ(self):
        if self.ui.comboBox_leerrohr_typ.currentIndex() >= 0:
            typ_text = self.ui.comboBox_leerrohr_typ.currentText()
            self.ui.label_gewaehltes_leerrohr.setText(typ_text)
        else:
            self.ui.label_gewaehltes_leerrohr.clear()

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

    def select_trasse(self, point):
        """Wird ausgelöst, wenn ein Punkt auf der Karte ausgewählt wird."""
        layer = QgsProject.instance().mapLayersByName("LWL_Trasse")
        if not layer:
            self.ui.label_Pruefung.setText("Layer 'LWL_Trasse' nicht gefunden")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        nearest_feature = None
        nearest_distance = float("inf")

        for feature in layer.getFeatures():
            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        if nearest_feature:
            trasse_id = nearest_feature["id"]

            if trasse_id not in self.selected_trasse_ids:
                self.selected_trasse_ids.append(trasse_id)

                # Highlight für die Trasse hinzufügen
                highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
                highlight.setColor(Qt.red)  # Setze rote Farbe
                highlight.setWidth(5)
                highlight.show()
                self.trasse_highlights.append(highlight)

                self.ui.label_verlauf.setText(", ".join(map(str, self.selected_trasse_ids)))
                self.ui.label_verlauf.setStyleSheet("background-color: lightgreen;")
            else:
                self.ui.label_Pruefung.setText(f"Trasse {trasse_id} ist bereits ausgewählt.")
                self.ui.label_Pruefung.setStyleSheet("background-color: yellow; color: black;")


    def pruefe_daten(self):
        print("DEBUG: Starte pruefe_daten")
        QgsMessageLog.logMessage(f"self.selected_verteiler in pruefe_daten: {self.selected_verteiler}", "ToolBox_SiegeleCo", level=Qgis.Info)
        self.debug_check()
        """Prüft, ob die ausgewählten Trassen einen durchgängigen Verlauf ohne Lücken und Abzweigungen ergeben und ob alle Pflichtfelder ausgefüllt sind."""
        fehler = []

        # Schritt 1: Prüfe, ob die Pflichtfelder gefüllt sind
        if not self.ui.label_gewaehltes_leerrohr.toPlainText().strip():
            fehler.append("Kein Leerrohr-Typ ausgewählt.")
        if not self.ui.label_gewaehltes_leerrohr_2.toPlainText().strip():
            fehler.append("Kein Leerrohr-Subtyp ausgewählt.")
            
        # Schritt 2: Prüfe, ob Trassen ausgewählt wurden
        if not self.selected_trasse_ids:
            fehler.append("Keine Trassen ausgewählt.")

        # Schritt 3: Sammeln der Knoteninformationen (nur wenn keine Fehler vorliegen)
        if not fehler:
            knoten_dict = {}  # Speichert die Häufigkeit jedes Knotens
            trassen_info = []  # Speichert Trasseninformationen (ID, VON, NACH)

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
                for trasse_id in self.selected_trasse_ids:
                    cur.execute('SELECT "VONKNOTEN", "NACHKNOTEN" FROM lwl."LWL_Trasse" WHERE "id" = %s', (trasse_id,))
                    row = cur.fetchone()
                    if row:
                        vonknoten, nachknoten = row
                        trassen_info.append((trasse_id, vonknoten, nachknoten))

                        # Zähle die Häufigkeit der Knoten
                        for knoten in [vonknoten, nachknoten]:
                            if knoten in knoten_dict:
                                knoten_dict[knoten] += 1
                            else:
                                knoten_dict[knoten] = 1
                    else:
                        fehler.append(f"Fehler beim Abrufen der Knoten für Trasse {trasse_id}.")
                
                # Schritt 4: Validierung der Knotenhäufigkeiten
                if not fehler:
                    startknoten = [knoten for knoten, count in knoten_dict.items() if count == 1]
                    mittel_knoten = [knoten for knoten, count in knoten_dict.items() if count == 2]

                    if len(startknoten) != 2:  # Es muss genau einen Start- und einen Endknoten geben
                        fehler.append("Kein durchgängiger Verlauf: Es gibt nicht genau einen Start- und einen Endknoten.")

                    if any(count > 2 for count in knoten_dict.values()):  # Kein Knoten darf öfter als zweimal vorkommen
                        fehler.append("Kein durchgängiger Verlauf: Es gibt Abzweigungen oder Lücken.")

                # Schritt 5: Reihenfolge der Trassen korrigieren
                geordnete_trassen = []
                if not fehler:
                    geordnete_trassen = self.ordne_trassen(trassen_info)
                if not self.ui.label_gewaehlter_verteiler.toPlainText().strip():
                    fehler.append("Kein Verteilerkasten ausgewählt.")



            except Exception as e:
                fehler.append(f"Datenbankfehler: {e}")
            finally:
                cur.close()
                conn.close()

        # Schritt 6: Ergebnis anzeigen
        if fehler:
            self.ui.label_Pruefung.setText("; ".join(fehler))
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
            self.ui.pushButton_Import.setEnabled(False)
        else:
            self.ui.label_Pruefung.setText("Prüfung erfolgreich. Import möglich.")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightgreen;")
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

            # Prüfen, ob alle Pflichtfelder ausgefüllt sind
            trassen_ids_pg_array = "{" + ",".join(map(str, set(self.selected_trasse_ids))) + "}"  # Doppelte Trassen entfernen
            verbundnummer = self.ui.comboBox_Verbundnummer.currentText().strip() or None
            kommentar = self.ui.label_Kommentar.text().strip() or None
            beschreibung = self.ui.label_Kommentar_2.text().strip() or None
            farbschema = self.ui.comboBox_Farbschema.currentText().strip() or None
            firma_hersteller = self.ui.comboBox_Firma.currentText().strip() or None

            # Subtyp als Nummer aus der ComboBox ermitteln
            subtyp_id = self.ui.comboBox_leerrohr_typ_2.currentData()
            if subtyp_id is None or subtyp_id == -1:
                self.ui.label_Pruefung.setText("❌ Kein gültiger Subtyp ausgewählt!")
                self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
                return

            # Prüfen, ob mindestens eine Trasse ausgewählt wurde
            if not self.selected_trasse_ids:
                self.ui.label_Pruefung.setText("❌ Keine Trassen ausgewählt.")
                self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
                return

            # Sammle die Geometrien aller ausgewählten Trassen
            cur.execute("""
                SELECT "id", ST_AsText("geom")
                FROM lwl."LWL_Trasse"
                WHERE "id" = ANY(%s::bigint[])
            """, (self.selected_trasse_ids,))
            trassen_geometrien = cur.fetchall()

            # Prüfe, ob alle Trassen eine gültige Geometrie haben
            if not trassen_geometrien or len(trassen_geometrien) != len(self.selected_trasse_ids):
                self.ui.label_Pruefung.setText("❌ Fehler: Nicht alle Trassen haben gültige Geometrien.")
                self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
                return

            # Verbinde die Geometrien zu einer einzigen Linie
            geometrien_wkt = ", ".join([f"ST_GeomFromText('{geom[1]}', 31254)" for geom in trassen_geometrien])
            cur.execute(f"SELECT ST_AsText(ST_LineMerge(ST_Union(ARRAY[{geometrien_wkt}])))")
            verbundene_geometrie = cur.fetchone()[0]

            # Erzeuge eine Offset-Linie basierend auf dem ursprünglichen Verlauf
            cur.execute(f"SELECT ST_AsText(ST_OffsetCurve(ST_GeomFromText('{verbundene_geometrie}', 31254), 1.0))")
            offset_geometrie = cur.fetchone()[0]

            # Einfügen der Daten in die Datenbank
            insert_query = """
            INSERT INTO lwl."LWL_Leerrohr" (
                "ID_TRASSE", "TYP", "SUBTYP", "GEFOERDERT", "SUBDUCT", "VERBUNDNUMMER", 
                "KOMMENTAR", "BESCHREIBUNG", "VERLEGT_AM", "FARBSCHEMA", "FIRMA_HERSTELLER", "VKG_LR", "geom"
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 31254))
            """

            cur.execute(insert_query, (
                trassen_ids_pg_array,
                self.ui.comboBox_leerrohr_typ.currentData(),  # TYP
                subtyp_id,                                  # SUBTYP (als Nummer)
                'TRUE' if self.ui.comboBox_Gefoerdert.currentText() == "Ja" else 'FALSE',
                'TRUE' if self.ui.comboBox_Subduct.currentText() == "Ja" else 'FALSE',
                verbundnummer,
                kommentar,
                beschreibung,
                self.ui.mDateTimeEdit_Strecke.date().toString("yyyy-MM-dd"),
                farbschema,
                firma_hersteller,
                self.selected_verteiler,
                offset_geometrie
            ))

            conn.commit()
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)

            # Falls Mehrfachimport aktiv ist, automatische Anpassung der Verbundnummer
            if self.ui.checkBox_clearForm.isChecked():
                if verbundnummer and verbundnummer.isdigit():
                    neue_verbundnummer = int(verbundnummer) + 1
                    if neue_verbundnummer <= 9:
                        self.ui.comboBox_Verbundnummer.setCurrentText(str(neue_verbundnummer))
                    else:
                        self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)
            else:
                self.initialisiere_formular()

        except Exception as e:
            if conn:
                conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Import fehlgeschlagen: {str(e)}", level=Qgis.Critical)
        finally:
            if conn:
                conn.close()
        
        # Karte aktualisieren, damit die Daten sofort sichtbar sind
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
        if layer:
            layer.triggerRepaint()

    def initialisiere_formular(self):
        """Initialisiert das gesamte Formular."""
        self.clear_trasse_selection()

        if self.verteiler_highlight:
            self.verteiler_highlight.hide()
            self.verteiler_highlight = None

        self.ui.label_gewaehlter_verteiler.setText("Kein Verteiler ausgewählt")
        self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
        from PyQt5.QtCore import QDate
        self.ui.mDateTimeEdit_Strecke.setDate(QDate.currentDate())

        # Dropdowns und Checkboxen initialisieren
        self.populate_leerrohr_typen()
        self.populate_leerrohr_subtypen()
        self.populate_gefoerdert_subduct()
        self.populate_verbundnummer()
        self.populate_farbschema()
        self.populate_firma()

        # Umschalten erfolgt nur noch über die RadioButtons:
        self.ui.radioButton_Hauptstrang.setChecked(True)
        
        # Sicherstellen, dass alle ComboBoxen leer oder auf Standard stehen
        self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)
        self.ui.comboBox_Farbschema.setCurrentIndex(-1)
        self.ui.pushButton_Import.setEnabled(False)

    def clear_trasse_selection(self):
                
        # Setze Default-Werte für Label und Felder
        self.ui.label_gewaehlter_verteiler.setText("")
        self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
        
        self.ui.label_Kommentar.setText("")
        self.ui.label_Kommentar_2.setText("")
        
        """Setzt alle Felder und Highlights für Trassen zurück."""
        for highlight in self.trasse_highlights:
            highlight.hide()
            
        # Entferne das Highlight für den Verteilerkasten
        if self.verteiler_highlight:
            self.verteiler_highlight.hide()
            self.verteiler_highlight = None

        self.trasse_highlights.clear()
        self.selected_trasse_ids.clear()

        self.ui.label_verlauf.clear()
        self.ui.label_verlauf.setStyleSheet("background-color: lightcoral;")
        self.ui.label_Pruefung.clear()
        self.ui.label_Pruefung.setStyleSheet("")

        self.ui.comboBox_leerrohr_typ.setCurrentIndex(-1)
        self.ui.comboBox_leerrohr_typ_2.setCurrentIndex(-1)
        self.ui.comboBox_Gefoerdert.setCurrentIndex(-1)
        self.ui.comboBox_Subduct.setCurrentIndex(-1)
        self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)
        self.ui.pushButton_Import.setEnabled(False)

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