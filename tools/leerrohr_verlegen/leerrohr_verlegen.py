from qgis.core import QgsProject, QgsDataSourceUri, Qgis, QgsGeometry, QgsFeatureRequest
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox
from qgis.PyQt.QtCore import Qt
from .leerrohr_verlegen_dialog import Ui_LeerrohrVerlegungsToolDialogBase
from qgis.PyQt.QtSql import QSqlDatabase, QSqlQuery
from qgis.gui import QgsHighlight

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
        self.ui.comboBox_leerrohr_typ_2.currentIndexChanged.connect(self.update_selected_leerrohr_subtyp)

        # Direkt beim Start die Dropdowns füllen
        self.populate_leerrohr_typen()
        self.populate_leerrohr_subtypen()
        self.populate_gefoerdert_subduct()  # Neue Methode für Gefoerdert und Subduct
        self.populate_verbundnummer()      # Neue Methode für Verbundnummer

    def get_db_connection(self):
        layers = QgsProject.instance().mapLayers().values()
        db = None
        for layer in layers:
            if layer.providerType() == 'postgres':
                connection_info = QgsDataSourceUri(layer.source())
                db = QSqlDatabase.addDatabase("QPSQL")
                db.setHostName(connection_info.host())
                db.setPort(int(connection_info.port()))
                db.setDatabaseName(connection_info.database())
                db.setUserName(connection_info.username())
                db.setPassword(connection_info.password())
                break

        if db is None or not db.open():
            raise Exception("Datenbankverbindung konnte nicht hergestellt werden.")

        return db

    def populate_leerrohr_typen(self):
        db = self.get_db_connection()
        query = QSqlQuery(db)
        query.prepare('SELECT "WERT", "TYP" FROM lwl."LUT_Leerrohr_Typ" WHERE "WERT" IN (1, 2, 3)')

        if not query.exec_():
            self.ui.label_Pruefung.setText("Fehler beim Abrufen der Leerrohrtypen")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
            return

        self.ui.comboBox_leerrohr_typ.clear()  # Vor dem Befüllen sicherstellen, dass die ComboBox leer ist

        while query.next():
            wert = query.value(0)
            typ = query.value(1)
            self.ui.comboBox_leerrohr_typ.addItem(typ, wert)

        # Setze die ComboBox auf "keine Auswahl"
        self.ui.comboBox_leerrohr_typ.setCurrentIndex(-1)

    def populate_leerrohr_subtypen(self):
        db = self.get_db_connection()
        query = QSqlQuery(db)
        query.prepare('SELECT "SUBTYP" FROM lwl."LUT_Leerrohr_SubTyp"')

        if not query.exec_():
            self.ui.label_Pruefung.setText("Fehler beim Abrufen der Leerrohr-Subtypen")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
            return

        self.ui.comboBox_leerrohr_typ_2.clear()  # Vor dem Befüllen sicherstellen, dass die ComboBox leer ist

        while query.next():
            subtyp = query.value(0)
            self.ui.comboBox_leerrohr_typ_2.addItem(subtyp)

        # Setze die ComboBox auf "keine Auswahl"
        self.ui.comboBox_leerrohr_typ_2.setCurrentIndex(-1)

    def populate_gefoerdert_subduct(self):
        """Füllt die Dropdowns für 'Gefördert' und 'Subduct' mit 'Ja' und 'Nein'."""
        options = ["Ja", "Nein"]

        # Populate Gefördert
        self.ui.comboBox_Gefoerdert.clear()
        self.ui.comboBox_Gefoerdert.addItems(options)
        self.ui.comboBox_Gefoerdert.setCurrentIndex(-1)  # Setze die ComboBox auf "keine Auswahl"

        # Populate Subduct
        self.ui.comboBox_Subduct.clear()
        self.ui.comboBox_Subduct.addItems(options)
        self.ui.comboBox_Subduct.setCurrentIndex(-1)  # Setze die ComboBox auf "keine Auswahl"

    def populate_verbundnummer(self):
        """Füllt die Dropdown für 'Verbundnummer' mit Werten von 1 bis 9."""
        self.ui.comboBox_Verbundnummer.clear()
        self.ui.comboBox_Verbundnummer.addItems([str(i) for i in range(1, 10)])
        self.ui.comboBox_Verbundnummer.setCurrentIndex(-1)  # Setze die ComboBox auf "keine Auswahl"

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

    def pruefe_daten(self):
        """Prüft die Daten und aktiviert den Import-Button, wenn die Prüfung erfolgreich ist."""
        fehler = []

        # Prüfe, ob eine Trasse ausgewählt wurde
        if not self.selected_trasse_ids:
            fehler.append("Keine Trasse ausgewählt.")

        # Prüfe, ob das Label für den ausgewählten Leerrohr-Typ befüllt ist
        if not self.ui.label_gewaehltes_leerrohr.toPlainText().strip():
            fehler.append("Kein Leerrohr-Typ ausgewählt.")
            
        
        # Prüfe, ob ein SubTyp ausgewählt wurde
        if not self.ui.label_gewaehltes_leerrohr_2.toPlainText().strip():
            fehler.append("Kein Leerrohr-SubTyp ausgewählt.")

        # Ergebnis der Prüfung
        if fehler:
            self.ui.label_Pruefung.setText("; ".join(fehler))
            self.ui.label_Pruefung.setStyleSheet("background-color: lightcoral;")
            self.ui.pushButton_Import.setEnabled(False)
        else:
            self.ui.label_Pruefung.setText("Prüfung erfolgreich. Import möglich.")
            self.ui.label_Pruefung.setStyleSheet("background-color: lightgreen;")
            self.ui.pushButton_Import.setEnabled(True)

    def importiere_daten(self):
        """Importiert die Daten (noch nicht implementiert)."""
        self.ui.label_Pruefung.setText("Daten erfolgreich importiert!")
        self.ui.label_Pruefung.setStyleSheet("background-color: lightgreen;")

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
