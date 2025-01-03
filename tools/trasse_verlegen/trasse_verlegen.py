import logging
from qgis.core import QgsProject, QgsDataSourceUri, Qgis, QgsGeometry, QgsFeatureRequest, QgsMessageLog
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox
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

        # Setze das Fenster immer im Vordergrund
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        # Verbindung für die Buttons
        self.ui.pushButton_verlauf.clicked.connect(self.activate_trasse_selection)
        self.ui.pushButton_Datenpruefung.clicked.connect(self.pruefe_daten)
        self.ui.pushButton_Import.setEnabled(False)  # Standardmäßig deaktiviert
        self.ui.pushButton_Import.clicked.connect(self.importiere_daten)
        self.ui.pushButton_verteiler.clicked.connect(self.select_verteilerkasten)


        # Verbindung für Reset und Cancel in der button_box
        reset_button = self.ui.button_box.button(QDialogButtonBox.Reset)
        cancel_button = self.ui.button_box.button(QDialogButtonBox.Cancel)

        if reset_button:
            reset_button.clicked.connect(self.clear_trasse_selection)
        if cancel_button:
            cancel_button.clicked.connect(self.close_tool)

        # Variablen für Auswahlwerkzeug und Highlights
        self.map_tool = None
        self.selected_trasse_ids = []  # Speichert die IDs der ausgewählten Trassen
        self.trasse_highlights = []    # Speichert die Highlights für die Trassen

        # Verbindung für die Auswahl der Typen und Subtypen
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.update_selected_leerrohr_typ)
        self.ui.comboBox_leerrohr_typ.currentIndexChanged.connect(self.populate_leerrohr_subtypen)
        self.ui.comboBox_leerrohr_typ_2.currentIndexChanged.connect(self.update_selected_leerrohr_subtyp)

        # Direkt beim Start die Dropdowns füllen
        self.populate_leerrohr_typen()
        self.populate_leerrohr_subtypen()
        self.populate_gefoerdert_subduct()  # Neue Methode für Gefoerdert und Subduct
        self.populate_verbundnummer()      # Neue Methode für Verbundnummer
        self.populate_farbschema()

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

    def select_verteilerkasten(self):
        """Aktiviert das Map-Tool zum Auswählen eines Verteilerkastens."""
        # Setze das Label zurück
        self.ui.label_gewaehlter_verteiler.clear()

        # Aktiviere das MapTool zur Auswahl
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self.verteilerkasten_selected)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def verteilerkasten_selected(self, point):
        """Wird ausgelöst, wenn ein Punkt auf der Karte ausgewählt wird."""
        # Layer direkt aus der Datenbank abrufen
        layer_name = "LWL_Knoten"
        layer = QgsProject.instance().mapLayersByName(layer_name)
        if not layer:
            self.ui.label_gewaehlter_verteiler.setText("Layer 'LWL_Knoten' nicht gefunden")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")
            return
        layer = layer[0]

        nearest_feature = None
        nearest_distance = float("inf")

        # Suche den nächstgelegenen Verteilerkasten (mit Filter auf Typ "Verteilerkasten")
        for feature in layer.getFeatures():
            if feature["TYP"] != "Verteilerkasten":  # Filter für Verteilerkasten
                continue

            distance = feature.geometry().distance(QgsGeometry.fromPointXY(point))
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_feature = feature

        if nearest_feature:
            verteiler_id = nearest_feature["id"]  # Feldname für die ID anpassen
            self.ui.label_gewaehlter_verteiler.setText(f"Verteilerkasten ID: {verteiler_id}")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("")  # Hintergrundfarbe zurücksetzen

            # Optional: Highlight des ausgewählten Verteilerkastens
            highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
            highlight.setColor(Qt.red)
            highlight.setWidth(5)
            highlight.show()
            self.trasse_highlights.append(highlight)  # Speichere das Highlight für späteres Entfernen

        else:
            self.ui.label_gewaehlter_verteiler.setText("Kein Verteilerkasten gefunden")
            self.ui.label_gewaehlter_verteiler.setStyleSheet("background-color: lightcoral;")

        # Deaktiviere das MapTool
        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.map_tool = None

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
        """Füllt die Subtypen basierend auf dem ausgewählten Typ."""
        # Hole die ausgewählte Typ-ID aus der ComboBox
        selected_typ = self.ui.comboBox_leerrohr_typ.currentText()  # Der Text des gewählten Typs
        if not selected_typ:
            self.ui.comboBox_leerrohr_typ_2.clear()
            self.ui.comboBox_leerrohr_typ_2.addItem("Bitte Typ wählen")
            return

        # Datenbankverbindung herstellen
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
            # SQL-Abfrage für die Subtypen
            query = 'SELECT "id", "SUBTYP" FROM lwl."LUT_Leerrohr_SubTyp" WHERE "TYP" = %s'
            cur.execute(query, (selected_typ,))
            rows = cur.fetchall()

            # ComboBox leeren und befüllen
            self.ui.comboBox_leerrohr_typ_2.clear()
            if rows:
                for row in rows:
                    subtyp_id, subtyp_name = row
                    self.ui.comboBox_leerrohr_typ_2.addItem(subtyp_name, subtyp_id)
            else:
                self.ui.comboBox_leerrohr_typ_2.addItem("Keine Subtypen verfügbar")

            # Keine Vorauswahl
            self.ui.comboBox_leerrohr_typ_2.setCurrentIndex(-1)

        except Exception as e:
            self.ui.label_Pruefung.setText(f"Fehler beim Laden der Subtypen: {e}")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
        finally:
            cur.close()
            conn.close()

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
        """Füllt die Dropdown für 'Verbundnummer' mit Werten von 1 bis 9, basierend auf dem gewählten Verteilerkasten."""
        self.ui.comboBox_Verbundnummer.clear()  # Leert die Dropdown-Liste

        alle_verfuegbaren_nummern = [str(i) for i in range(1, 10)]  # Standardmäßig alle Nummern
        verwendete_nummern = set()

        db_details = self.get_database_connection()
        conn = None
        cur = None

        try:
            # Verteilerkasten ID aus dem Label extrahieren
            verteiler_id_text = self.ui.label_gewaehlter_verteiler.toPlainText()
            if not verteiler_id_text:
                self.ui.label_Pruefung.setPlainText("Kein Verteilerkasten ausgewählt.")
                return

            verteiler_id = verteiler_id_text.split(":")[1].strip()
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

            # Alle Trassen abrufen, die von diesem Verteilerkasten ausgehen
            cur.execute(f"""
                SELECT ARRAY_AGG("id")
                FROM lwl."LWL_Trasse"
                WHERE "VONKNOTEN" = %s OR "NACHKNOTEN" = %s
            """, (verteiler_id, verteiler_id))
            trassen_ids = cur.fetchone()[0]

            QgsMessageLog.logMessage(f"Gefundene Trassen-IDs: {trassen_ids}", "ToolBox_SiegeleCo", level=Qgis.Info)

            if trassen_ids:
                # Alle bereits verwendeten Verbundnummern abrufen
                cur.execute(f"""
                    SELECT DISTINCT "VERBUNDNUMMER"
                    FROM lwl."LWL_Leerrohr"
                    WHERE "ID_TRASSE" && %s::bigint[]
                """, (trassen_ids,))
                verwendete_nummern = {str(row[0]) for row in cur.fetchall() if row[0] is not None}
                QgsMessageLog.logMessage(f"Gefundene verwendete Nummern: {verwendete_nummern}", "ToolBox_SiegeleCo", level=Qgis.Info)

            # Dropdown-Liste befüllen
            for nummer in alle_verfuegbaren_nummern:
                self.ui.comboBox_Verbundnummer.addItem(nummer)
                if nummer in verwendete_nummern:
                    # Nummer ausgrauen, wenn sie bereits verwendet wurde
                    index = self.ui.comboBox_Verbundnummer.count() - 1
                    item = self.ui.comboBox_Verbundnummer.model().item(index)
                    item.setEnabled(False)

            # Standardmäßig keine Auswahl
            self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)

            # Log: Dropdown-Liste erfolgreich aktualisiert
            QgsMessageLog.logMessage(
                f"Dropdown-Liste aktualisiert: {self.ui.comboBox_Verbundnummer.count()} Einträge",
                "ToolBox_SiegeleCo",
                level=Qgis.Info
            )

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
        """Füllt die ComboBox für Farbschema mit den Werten aus der Tabelle lwl.LUT_Farbe_Codierung."""
        try:
            # Datenbankverbindung herstellen
            db_details = self.get_database_connection()
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()

            # SQL-Abfrage zur Abrufung der Codierung
            query = 'SELECT "CODIERUNG" FROM lwl."LUT_Farbe_Codierung"'
            cur.execute(query)
            rows = cur.fetchall()

            # ComboBox leeren und befüllen
            self.ui.comboBox_Farbschema.clear()
            if rows:
                for row in rows:
                    self.ui.comboBox_Farbschema.addItem(row[0])  # Codierung hinzufügen
            else:
                self.ui.comboBox_Farbschema.addItem("Keine Daten verfügbar")

            # Keine Vorauswahl setzen
            self.ui.comboBox_Farbschema.setCurrentIndex(-1)

        except Exception as e:
            # Fehlerbehandlung für die Benutzeroberfläche
            self.ui.label_Pruefung.setText(f"Fehler beim Laden des Farbschemas.")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    def update_selected_leerrohr_typ(self):
        if self.ui.comboBox_leerrohr_typ.currentIndex() >= 0:
            typ_text = self.ui.comboBox_leerrohr_typ.currentText()
            self.ui.label_gewaehltes_leerrohr.setText(typ_text)
        else:
            self.ui.label_gewaehltes_leerrohr.clear()

    def update_selected_leerrohr_subtyp(self):
        if self.ui.comboBox_leerrohr_typ_2.currentIndex() >= 0:
            subtyp_text = self.ui.comboBox_leerrohr_typ_2.currentText()
            self.ui.label_gewaehltes_leerrohr_2.setText(subtyp_text)
        else:
            self.ui.label_gewaehltes_leerrohr_2.clear()

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

                highlight = QgsHighlight(self.iface.mapCanvas(), nearest_feature.geometry(), layer)
                highlight.setColor(Qt.red)
                highlight.setWidth(5)
                highlight.show()
                self.trasse_highlights.append(highlight)

                self.ui.label_verlauf.setText(", ".join(map(str, self.selected_trasse_ids)))
            else:
                self.ui.label_Pruefung.setText(f"Trasse {trasse_id} ist bereits ausgewählt.")
                self.ui.label_Pruefung.setStyleSheet("background-color: yellow; color: black;")
                
        QgsMessageLog.logMessage(f"Aktualisierte Trassen-IDs: {self.selected_trasse_ids}", "ToolBox_SiegeleCo", level=Qgis.Info)
        self.populate_verbundnummer()

    def pruefe_daten(self):
        print("DEBUG: Starte pruefe_daten")
        self.debug_check()
        """Prüft, ob die ausgewählten Trassen einen durchgängigen Verlauf ohne Lücken und Abzweigungen ergeben und ob alle Pflichtfelder ausgefüllt sind."""
        fehler = []

        # Schritt 1: Prüfe, ob Trassen ausgewählt wurden
        if not self.selected_trasse_ids:
            fehler.append("Keine Trassen ausgewählt.")
        
        # Schritt 2: Prüfe, ob die Pflichtfelder gefüllt sind
        if not self.ui.label_gewaehltes_leerrohr.toPlainText().strip():
            fehler.append("Kein Leerrohr-Typ ausgewählt.")
        if not self.ui.label_gewaehltes_leerrohr_2.toPlainText().strip():
            fehler.append("Kein Leerrohr-Subtyp ausgewählt.")

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
        conn = None  # Verbindung initialisieren
        try:
            # Datenbankverbindungsdetails abrufen
            db_details = self.get_database_connection()  # Verbindungsdetails holen

            # Verbindung herstellen
            conn = psycopg2.connect(
                dbname=db_details["dbname"],
                user=db_details["user"],
                password=db_details["password"],
                host=db_details["host"],
                port=db_details["port"]
            )
            cur = conn.cursor()
            conn.autocommit = False

            # Trassen-IDs konvertieren
            trassen_ids_pg_array = "{" + ",".join(map(str, self.selected_trasse_ids)) + "}"

            # Null-Werte sicherstellen
            verbundnummer = self.ui.comboBox_Verbundnummer.currentText() if self.ui.comboBox_Verbundnummer.currentText() else None
            kommentar = self.ui.label_Kommentar.text().strip() if self.ui.label_Kommentar.text().strip() else None
            beschreibung = self.ui.label_Kommentar_2.text().strip() if self.ui.label_Kommentar_2.text().strip() else None
            farbschema = self.ui.comboBox_Farbschema.currentText() if self.ui.comboBox_Farbschema.currentText() else None

            # SQL-Abfrage
            insert_query = """
            INSERT INTO lwl."LWL_Leerrohr" (
                "ID_TRASSE", "TYP", "SUBTYP", "GEFOERDERT", "SUBDUCT", "VERBUNDNUMMER", 
                "KOMMENTAR", "BESCHREIBUNG", "VERLEGT_AM", "FARBSCHEMA"
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """

            # Query ausführen
            cur.execute(insert_query, (
                trassen_ids_pg_array,  # PostgreSQL-kompatibles Array
                self.ui.comboBox_leerrohr_typ.currentData(),  # Typ
                self.ui.comboBox_leerrohr_typ_2.currentData(),  # Subtyp
                'TRUE' if self.ui.comboBox_Gefoerdert.currentText() == "Ja" else 'FALSE',  # Gefördert
                'TRUE' if self.ui.comboBox_Subduct.currentText() == "Ja" else 'FALSE',  # Subduct
                verbundnummer,  # Verbundnummer
                kommentar,  # Kommentar
                beschreibung,  # Beschreibung
                self.ui.mDateTimeEdit_Strecke.date().toString("yyyy-MM-dd"),  # Verlegt am
                farbschema  # Farbschema
            ))

            # Änderungen speichern
            conn.commit()
            self.iface.messageBar().pushMessage("Erfolg", "Daten erfolgreich importiert.", level=Qgis.Success)
        except Exception as e:
            if conn:
                conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Import fehlgeschlagen: {str(e)}", level=Qgis.Critical)
        finally:
            if conn:
                conn.close()




    def clear_trasse_selection(self):
        """Setzt alle Felder und Highlights zurück."""
        for highlight in self.trasse_highlights:
            highlight.hide()
        self.trasse_highlights.clear()
        self.selected_trasse_ids.clear()

        self.ui.label_verlauf.clear()
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
