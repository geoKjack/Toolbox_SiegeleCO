# -*- coding: utf-8 -*-
"""
KabelVerlegungsTool
Verlegt Kabel durch Auswahl von Startknoten, Leerrohren und Endknoten.
"""
from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon, QStandardItemModel, QStandardItem
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsProject, Qgis, QgsDataSourceUri, QgsVectorLayer, QgsFeatureRequest, QgsMessageLog
from qgis.gui import QgsHighlight
from PyQt5.QtWidgets import QDialog
from PyQt5.QtCore import QVariant
import os.path
import psycopg2

from . import resources_rc  # Hier ist der Import für die Ressourcen
from .kabel_verlegen_dialog import KabelVerlegungsToolDialog

class KabelVerlegungsTool:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor."""
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(self.plugin_dir, 'i18n', 'KabelVerlegungsTool_{}.qm'.format(locale))

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr(u'&Kabel Verlegen')
        self.first_start = None

        # Variablen für den ersten Tab (Streckenkabel)
        self.startpunkt_id = None
        self.endpunkt_id = None
        self.verlauf_ids = []  # Liste für mehrere Verlaufseingaben
        self.highlights = []  # Liste für gespeicherte Highlight-Objekte

        # Variablen für den zweiten Tab (Hauseinführungskabel)
        self.startpunkt_id_2 = None
        self.virtueller_knoten_id = None
        self.hausanschluss_id = None
        self.verlauf_ids_2 = []
        self.highlights_2 = []
        self.startpunkt_bezeichnung = None
    
    def closeEvent(self, event):
        """Überschreibt das Schließen des Dialogs über das rote 'X'."""
        self.first_start = True  # Setzt den Zustand zurück, damit der Dialog erneut geöffnet werden kann
        event.accept()  # Schließt das Fenster tatsächlich


    def tr(self, message):
        """Get the translation for a string using Qt translation API."""
        return QCoreApplication.translate('KabelVerlegungsTool', message)

    def add_action(self, icon_path, text, callback, enabled_flag=True, add_to_menu=True, add_to_toolbar=True, status_tip=None, whats_this=None, parent=None):
        """Add a toolbar icon to the toolbar."""
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.iface.addToolBarIcon(action)

        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)
        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""
        icon_path = ':/plugins/kabel_verlegen/icon.png'
        self.add_action(icon_path, text=self.tr(u'Kabel Verlegen'), callback=self.run, parent=self.iface.mainWindow())
        self.first_start = True

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginMenu(self.tr(u'&Kabel Verlegen'), action)
            self.iface.removeToolBarIcon(action)

    def on_close_dialog(self):
        """Schließt den Dialog und setzt die Instanz zurück."""
        if self.dlg:  # Überprüfen, ob der Dialog existiert
            self.dlg.close()  # Schließt den Dialog
            self.dlg = None   # Setzt die Dialoginstanz auf None
            self.first_start = True  # Setzt first_start zurück, um den Dialog neu zu erstellen

    def run(self):
        """Run method that performs all the real work"""
        if self.first_start:
            self.first_start = False
            self.dlg = KabelVerlegungsToolDialog()

            # Setze das Fenster "immer im Vordergrund"
            self.dlg.setWindowFlag(Qt.WindowStaysOnTopHint)

            # Setup der Buttons und Verbindungen von Tab 1
            self.dlg.pushButton_startpunkt.clicked.connect(self.aktion_startknoten)
            self.dlg.pushButton_endpunkt.clicked.connect(self.aktion_endpunkt)
            self.dlg.pushButton_verlauf.clicked.connect(self.aktion_verlauf)
            self.dlg.pushButton_Vorschau.clicked.connect(self.kabelverlauf_erstellen)
            self.dlg.pushButton_Datenpruefung.clicked.connect(self.pruefe_verbindung)  # Korrekte Verknüpfung
            self.dlg.pushButton_Import.clicked.connect(self.daten_importieren)  # Import-Button verknüpfen
            
            # Setup der Buttons und Verbindungen von Tab 2
            self.dlg.pushButton_startpunkt_2.clicked.connect(self.aktion_startknoten_2)
            self.dlg.pushButton_virtueller_knoten.clicked.connect(self.aktion_virtuellerknoten_2)
            self.dlg.pushButton_hausanschluss.clicked.connect(self.aktion_endpunkt_2)
            self.dlg.pushButton_verlauf_2.clicked.connect(self.aktion_verlauf_2)
            self.dlg.pushButton_Vorschau_2.clicked.connect(self.kabelverlauf_erstellen_2)
            self.dlg.pushButton_Datenpruefung_2.clicked.connect(self.pruefe_verbindung_2)  # Korrekte Verknüpfung
            self.dlg.pushButton_Import_2.clicked.connect(self.daten_importieren_2)  # Import-Button verknüpfen

            # Import-Button initial deaktivieren
            self.dlg.pushButton_Import.setEnabled(False)
            self.dlg.pushButton_Import_2.setEnabled(False)
            
            # Kabelauswahl an die Funktion binden
            self.dlg.comboBox_kabel_typ.currentIndexChanged.connect(self.onKabelChanged)
            self.dlg.comboBox_kabel_typ_2.currentIndexChanged.connect(self.onKabelChanged_2)  # Für den zweiten Tab

            # Fülle die Verlegestatus-ComboBox mit den gewünschten Werten Tab 1
            self.dlg.comboBox_Verlegestatus.addItems([
                "Geplant",
                "Eingeblasen - inaktiv",
                "Eingeblasen - aktiv",
                "Defekt"
            ])
            # Fülle die Verlegestatus-ComboBox mit den gewünschten Werten Tab 2
            self.dlg.comboBox_Verlegestatus_2.addItems([
                "Geplant",
                "Eingeblasen - inaktiv",
                "Eingeblasen - aktiv",
                "Defekt"
            ])
            
            # Initialisiere die Werte der ComboBox für Gefördert Tab 1
            self.dlg.comboBox_Gefoerdert.clear()  # Falls vorher schon Werte existieren
            self.dlg.comboBox_Gefoerdert.addItems(["Ja", "Nein"])  # Optionen hinzufügen

            # Initialisiere die Werte der ComboBox für Gefördert Tab 2
            self.dlg.comboBox_Gefoerdert_2.clear()  # Falls vorher schon Werte existieren
            self.dlg.comboBox_Gefoerdert_2.addItems(["Ja", "Nein"])  # Optionen hinzufügen

            # Kabeltypen aus der Datenbank füllen
            self.populate_kabel_typen()
            self.populate_kabel_typen_2()  # Methode für den zweiten Tab

            # Verknüpfe den Reset-Button mit der Reset-Funktion
            self.dlg.button_box.button(self.dlg.button_box.Reset).clicked.connect(self.reset_form)
            self.dlg.button_box_2.button(self.dlg.button_box.Reset).clicked.connect(self.reset_form_2)
            
            # Verknüpfe den Abbrechen-Button mit dem Schließen des Fensters
            self.dlg.button_box.button(self.dlg.button_box.Cancel).clicked.connect(self.on_close_dialog)
            self.dlg.button_box_2.button(self.dlg.button_box.Cancel).clicked.connect(self.on_close_dialog)
            
            # Initialisieren Sie alle ComboBoxen auf leer (Index -1)
            self.dlg.comboBox_kabel_typ.setCurrentIndex(-1)
            self.dlg.comboBox_kabel_typ_2.setCurrentIndex(-1)
            self.dlg.comboBox_Verlegestatus.setCurrentIndex(-1)
            self.dlg.comboBox_Verlegestatus_2.setCurrentIndex(-1)
            self.dlg.comboBox_Gefoerdert.setCurrentIndex(-1)
            self.dlg.comboBox_Gefoerdert_2.setCurrentIndex(-1)
        self.dlg.show()


    def reset_form(self):
        """Setzt das gesamte Formular zurück und entfernt alle Highlights"""
        # Entferne alle Highlights, auch aus der Szene
        for item in self.iface.mapCanvas().scene().items():
            if isinstance(item, QgsHighlight):
                item.hide()  # Entferne alle Highlight-Objekte
        self.highlights.clear()  # Leere die Liste der Highlights

        # Setze das gesamte Formular zurück
        self.dlg.label_startpunkt.clear()  # Startpunkt zurücksetzen
        self.dlg.label_endpunkt.clear()    # Endpunkt zurücksetzen
        self.dlg.label_verlauf.clear()     # Verlauf zurücksetzen
        self.dlg.tableView_Vorschau.setModel(None)  # Vorschau-Tabelle zurücksetzen
        
        # Vorübergehend den Eventhandler trennen, um unerwünschte Updates zu vermeiden
        self.dlg.comboBox_kabel_typ.blockSignals(True)
        self.dlg.comboBox_kabel_typ.setCurrentIndex(-1)  # ComboBox auf keinen Eintrag setzen
        self.dlg.comboBox_kabel_typ.blockSignals(False)
        
        self.dlg.comboBox_Verlegestatus.setCurrentIndex(0)  # Verlegestatus zurücksetzen
        self.dlg.comboBox_Gefoerdert.setCurrentIndex(0)  # Gefördert-Status zurücksetzen
        self.dlg.label_Kommentar.clear()  # Kommentar zurücksetzen
        self.dlg.label_Kommentar_2.clear()  # Kommentar zurücksetzen
        self.dlg.label_Pruefung.clear()  # Prüfungsergebnis zurücksetzen
        self.dlg.label_gewaehltes_kabel.clear()  # Label für gewähltes Kabel zurücksetzen
        self.startpunkt_id = None
        self.endpunkt_id = None
        self.verlauf_ids = []

        # Import-Button deaktivieren
        self.dlg.pushButton_Import.setEnabled(False)
        
    def reset_form_2(self):
        """Setzt das gesamte Formular des zweiten Tabs zurück und entfernt alle Highlights"""
        # Entferne alle Highlights aus dem zweiten Tab, auch aus der Szene
        for item in self.iface.mapCanvas().scene().items():
            if isinstance(item, QgsHighlight):
                item.hide()  # Entferne alle Highlight-Objekte
        self.highlights_2.clear()  # Leere die Liste der Highlights

        # Setze das gesamte Formular von Tab 2 zurück
        self.dlg.label_startpunkt_2.clear()  # Startpunkt zurücksetzen
        self.dlg.label_virtueller_knoten.clear()  # Virtueller Knoten zurücksetzen
        self.dlg.label_hausanschluss.clear()  # Hausanschlusspunkt zurücksetzen
        self.dlg.label_verlauf_2.clear()  # Verlauf zurücksetzen
        self.dlg.tableView_Vorschau_2.setModel(None)  # Vorschau-Tabelle zurücksetzen

        # Vorübergehend den Eventhandler trennen, um unerwünschte Updates zu vermeiden
        self.dlg.comboBox_kabel_typ_2.blockSignals(True)
        self.dlg.comboBox_kabel_typ_2.setCurrentIndex(-1)  # ComboBox auf keinen Eintrag setzen
        self.dlg.comboBox_kabel_typ_2.blockSignals(False)

        self.dlg.comboBox_Verlegestatus_2.setCurrentIndex(0)  # Verlegestatus zurücksetzen
        self.dlg.comboBox_Gefoerdert_2.setCurrentIndex(0)  # Gefördert-Status zurücksetzen
        self.dlg.label_Kommentar_3.clear()  # Kommentar zurücksetzen
        self.dlg.label_Pruefung_2.clear()  # Prüfungsergebnis zurücksetzen
        self.dlg.label_gewaehltes_kabel_2.clear()  # Label für gewähltes Kabel zurücksetzen

        # Setze die Start-, End- und Verlaufsvariablen für den zweiten Tab zurück
        self.startpunkt_id_2 = None
        self.virtueller_knoten_id = None
        self.hausanschluss_id = None
        self.verlauf_ids_2 = []

        # Import-Button deaktivieren
        self.dlg.pushButton_Import_2.setEnabled(False)

    def get_database_connection(self):
        """Holt die aktuelle Datenbankverbindung."""
        project = QgsProject.instance()
        layers = project.mapLayers().values()
    
        for layer in layers:
            if layer.name() == "LWL_Kabel_Typ" and layer.dataProvider().name() == "postgres":
                uri = layer.dataProvider().dataSourceUri()
                return uri
        
        raise Exception("Keine aktive PostgreSQL-Datenbankverbindung gefunden.")

    def populate_kabel_typen(self):
        """Holt die Kabeltypen aus der Datenbank und füllt die ComboBox (Filter: Streckenkabel)."""
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
            # Filter für Streckenkabel
            cur.execute('SELECT "id", "BEZEICHNUNG" FROM "lwl"."LWL_Kabel_Typ" WHERE "TYP" = %s;', ("Streckenkabel",))
            kabel_typen = cur.fetchall()

            for typ in kabel_typen:
                self.dlg.comboBox_kabel_typ.addItem(f"{typ[1]}", typ[0])  # Text und ID hinzufügen

        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", str(e), level=Qgis.Critical)
        finally:
            if conn is not None:
                conn.close()

    def populate_kabel_typen_2(self):
        """Holt die Kabeltypen aus der Datenbank und füllt die ComboBox (Filter: Hauseinführungskabel)."""
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
            # Filter für Hauseinführungskabel
            cur.execute('SELECT "id", "BEZEICHNUNG" FROM "lwl"."LWL_Kabel_Typ" WHERE "TYP" = %s;', ("Hauseinführungskabel",))
            kabel_typen = cur.fetchall()

            for typ in kabel_typen:
                self.dlg.comboBox_kabel_typ_2.addItem(f"{typ[1]}", typ[0])  # Text und ID hinzufügen

        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", str(e), level=Qgis.Critical)
        finally:
            if conn is not None:
                conn.close()

    def onKabelChanged(self):
        """Funktion, um das ausgewählte Kabel im Label nur bei Benutzerinteraktion anzuzeigen"""
        if self.dlg.comboBox_kabel_typ.hasFocus():  # Überprüfen, ob die ComboBox den Fokus hat (vom Benutzer ausgewählt)
            selected_kabel = self.dlg.comboBox_kabel_typ.currentText()
            self.dlg.label_gewaehltes_kabel.setText(f"{selected_kabel}")

    def onKabelChanged_2(self):
        """Funktion, um das ausgewählte Kabel im zweiten Tab anzuzeigen"""
        if self.dlg.comboBox_kabel_typ_2.hasFocus():  # Überprüfen, ob die ComboBox den Fokus hat
            selected_kabel = self.dlg.comboBox_kabel_typ_2.currentText()
            self.dlg.label_gewaehltes_kabel_2.setText(f"{selected_kabel}")

    def get_next_kabel_id(self):
        """Ermittelt die nächste verfügbare Kabel-ID."""
        try:
            # Hole die Datenbankverbindung
            db_uri = self.get_database_connection()
            uri = QgsDataSourceUri(db_uri)

            # Verbinde zur PostgreSQL-Datenbank
            conn = psycopg2.connect(
                dbname=uri.database(),
                user=uri.username(),
                password=uri.password(),
                host=uri.host(),
                port=uri.port()
            )
            cur = conn.cursor()

            # Führe die Abfrage aus, um die maximale KABEL_ID zu erhalten
            cur.execute('SELECT MAX("KABEL_ID") FROM "lwl"."LWL_Kabel_Verlegt";')
            result = cur.fetchone()

            # Wenn keine Kabel-ID existiert, starte mit 1, ansonsten erhöhe um 1
            if result and result[0]:
                return result[0] + 1
            else:
                return 1

        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", str(e), level=Qgis.Critical)
        finally:
            if conn is not None:
                conn.close()

    def aktion_startknoten(self):
        """Aktion für den Startknoten"""
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Startknoten", level=Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]  # Direkte Referenz auf den Knoten-Layer
        self.iface.setActiveLayer(layer)  # Aktiviert den Layer

        self.iface.actionSelect().trigger()  # Aktiviert das Auswahlwerkzeug

        def onStartpunktSelected():
            # Entferne vorherige Highlights
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()

            selected_features = layer.selectedFeatures()
            if selected_features:
                startpunkt_id = selected_features[0].id()
                self.dlg.label_startpunkt.setText(f"Startknoten: {startpunkt_id}")
                self.startpunkt_id = startpunkt_id  # Speichern des Startpunkts

                # Highlight Startpunkt
                geom = selected_features[0].geometry()
                highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                highlight.setColor(Qt.red)  # Farbe der Hervorhebung für Startpunkt
                highlight.setWidth(4)
                highlight.show()
                self.highlights.append(highlight)  # Hinzufügen zur Liste für späteres Entfernen

        try:
            layer.selectionChanged.disconnect()  # Vorherige Verbindungen entfernen
        except TypeError:
            pass

        layer.selectionChanged.connect(onStartpunktSelected)

    def aktion_endpunkt(self):
        """Aktion für den Endpunkt"""
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Endpunkt", level=Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]  # Direkte Referenz auf den Knoten-Layer
        self.iface.setActiveLayer(layer)  # Aktiviert den Layer

        self.iface.actionSelect().trigger()  # Aktiviert das Auswahlwerkzeug

        def onEndpunktSelected():
            # Entferne alle bestehenden Highlights, bevor neue hinzugefügt werden
            for highlight in self.highlights:
                highlight.hide()
            self.highlights.clear()
            
            selected_features = layer.selectedFeatures()
            if selected_features:
                endpunkt_id = selected_features[0].id()
                self.dlg.label_endpunkt.setText(f"Endpunkt: {endpunkt_id}")
                self.endpunkt_id = endpunkt_id  # Speichern des Endpunkts
                
                # Highlight Endpunkt
                geom = selected_features[0].geometry()
                highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                highlight.setColor(Qt.red)  # Farbe der Hervorhebung für Endpunkt
                highlight.setWidth(4)
                highlight.show()
                self.highlights.append(highlight)  # Hinzufügen zur Liste für späteres Entfernen

        try:
            layer.selectionChanged.disconnect()  # Vorherige Verbindungen entfernen
        except TypeError:
            pass

        layer.selectionChanged.connect(onEndpunktSelected)

    def aktion_verlauf(self):
        """Aktion für den Verlauf"""
        # Setze das Verlauf-Label und die Verlaufs-IDs zurück
        self.dlg.label_verlauf.clear()
        self.verlauf_ids.clear()
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Verlauf (Leerrohrfolge)", level=Qgis.Info)
        
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]  # Direkte Referenz auf den Verlauf-Layer
        self.iface.setActiveLayer(layer)  # Aktiviert den Layer

        self.iface.actionSelect().trigger()  # Aktiviert das Auswahlwerkzeug

        def onVerlaufSelected():
            selected_features = layer.selectedFeatures()

            if selected_features:
                for feature in selected_features:
                    verlauf_id = feature["id"]  # Stelle sicher, dass dieses Attribut existiert
                    self.verlauf_ids.append(verlauf_id)

                # Aktualisiere das Label mit den IDs
                verlauf_text = "; ".join(map(str, self.verlauf_ids))  # Semikolon-getrennt
                self.dlg.label_verlauf.setText(f"Verlauf: {verlauf_text}")

                # Highlight-Funktion für die Geometrien
                for feature in selected_features:
                    geom = feature.geometry()
                    highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                    highlight.setColor(Qt.red)  # Farbe der Hervorhebung
                    highlight.setWidth(3)       # Linienstärke der Hervorhebung
                    highlight.show()

                    # Speichern der Highlights für das spätere Entfernen
                    self.highlights.append(highlight)

        try:
            layer.selectionChanged.disconnect()  # Vorherige Verbindungen entfernen
        except TypeError:
            pass

        layer.selectionChanged.connect(onVerlaufSelected)


    def kabelverlauf_erstellen(self):
        """Funktion, um den Kabelverlauf in der Tabellenansicht anzuzeigen."""
        # Zusätzliche Attribute aus den Eingabefeldern holen
        kommentar = self.dlg.label_Kommentar.text()  # Kommentar
        bezeichnung_intern = self.dlg.label_Kommentar_2.text()  # Bezeichnung_intern (Neues Attribut)
        verlegestatus = self.dlg.comboBox_Verlegestatus.currentText()  # Verlegestatus
        gefoerdert = self.dlg.comboBox_Gefoerdert.currentText()  # Gefördert

        # Erstellt eine Liste mit Zeilen, die in die Tabelle eingefügt werden
        kabelverlauf_daten = []
        
        # Startknoten
        kabelverlauf_daten.append([
            'Startknoten', 
            self.startpunkt_id, 
            '',  # Keine Verbindung für den Startknoten
            kommentar,
            bezeichnung_intern,  # Füge das neue Attribut hinzu
            verlegestatus, 
            gefoerdert
        ])
        
        # Verbindung der Leerrohre prüfen und darstellen
        for index, verlauf_id in enumerate(self.verlauf_ids, start=1):
            layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
            feature = next(layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {verlauf_id}')))
            
            von_knoten = feature["VONKNOTEN"]
            nach_knoten = feature["NACHKNOTEN"]
            
            verbindung_text = f"VON: {von_knoten}, NACH: {nach_knoten}"
            
            kabelverlauf_daten.append([
                f'Leerrohr {index}', 
                verlauf_id, 
                verbindung_text, 
                kommentar,
                bezeichnung_intern,  # Füge das neue Attribut hinzu
                verlegestatus, 
                gefoerdert
            ])
        
        # Endknoten
        kabelverlauf_daten.append([
            'Endknoten', 
            self.endpunkt_id, 
            '',  # Keine Verbindung für den Endknoten
            kommentar,
            bezeichnung_intern,  # Füge das neue Attribut hinzu
            verlegestatus, 
            gefoerdert
        ])

        # Erstelle ein Standard-Modell für die Tabelle
        model = QStandardItemModel()

        # Setze die Spaltenüberschriften (mit neuem Attribut)
        model.setHorizontalHeaderLabels(['Attribut', 'Wert', 'Verbindung', 'Kommentar', 'Bezeichnung_intern', 'Verlegestatus', 'Gefördert'])

        # Befülle das Modell mit den Daten
        for row_data in kabelverlauf_daten:
            row = []
            for item in row_data:
                cell = QStandardItem(str(item))
                row.append(cell)
            model.appendRow(row)

        # Setze das Modell in die TableView
        self.dlg.tableView_Vorschau.setModel(model)

    def pruefe_verbindung(self):
        """Prüft, ob die Leerrohre eine durchgehende Verbindung ohne Lücken darstellen."""
        # Überprüfen, ob ein Kabeltyp gewählt wurde
        if self.dlg.comboBox_kabel_typ.currentIndex() == -1:
            self.dlg.label_Pruefung.setText("Kein Kabeltyp ausgewählt.")
            self.dlg.label_Pruefung.setStyleSheet("background-color: lightcoral;")  # Hintergrund auf Rot setzen
            return

        # Überprüfen, ob Start-, Endpunkt oder Verlauf fehlt
        if not self.startpunkt_id or not self.endpunkt_id or not self.verlauf_ids:
            self.dlg.label_Pruefung.setText("Unvollständige Daten.")
            self.dlg.label_Pruefung.setStyleSheet("background-color: lightcoral;")  # Hintergrund auf Rot setzen
            return

        korrekt = True  # Variable korrekt initialisieren
        letzter_knoten = self.startpunkt_id  # Starte mit dem Startknoten

        for verlauf_id in self.verlauf_ids:
            layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
            feature = next(layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {verlauf_id}')))

            von_knoten = feature["VONKNOTEN"]
            nach_knoten = feature["NACHKNOTEN"]

            # Prüfe, ob der letzte Knoten entweder mit dem VON- oder NACH-Knoten übereinstimmt
            if letzter_knoten != von_knoten and letzter_knoten != nach_knoten:
                korrekt = False
                break

            # Setze den neuen letzten Knoten, der entweder der VON- oder NACH-Knoten sein kann
            letzter_knoten = nach_knoten if letzter_knoten == von_knoten else von_knoten

        # Am Ende prüfen, ob der letzte Knoten mit dem Endknoten übereinstimmt
        if korrekt and letzter_knoten == self.endpunkt_id:
            self.dlg.label_Pruefung.setText("Verlauf ist korrekt verbunden. Daten können importiert werden")
            self.dlg.label_Pruefung.setStyleSheet("background-color: lightgreen;")  # Hintergrund auf Grün setzen
            self.dlg.pushButton_Import.setEnabled(True)  # Import-Button aktivieren
        else:
            self.dlg.label_Pruefung.setText("Verlauf ist nicht verbunden. Bitte überprüfen Sie die Auswahl")
            self.dlg.label_Pruefung.setStyleSheet("background-color: lightcoral;")  # Hintergrund auf Rot setzen

    def get_kabeltyp_id(self, kabel_name):
        """Funktion, um die ID des Kabeltyps basierend auf dem Namen abzurufen"""
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
            cur.execute('SELECT id FROM "lwl"."LWL_Kabel_Typ" WHERE "BEZEICHNUNG" = %s;', (kabel_name,))
            kabeltyp_id = cur.fetchone()

            if kabeltyp_id:
                return kabeltyp_id[0]  # Rückgabe der Kabeltyp-ID
            else:
                raise Exception("Kabeltyp-ID nicht gefunden.")

        except Exception as e:
            self.iface.messageBar().pushMessage("Fehler", str(e), level=Qgis.Critical)
        finally:
            if conn is not None:
                conn.close()

        return None

    def daten_importieren(self):
        """Importiert die geprüften Daten in die Datenbank."""
        try:
            # Datum aus dem DateTimeEdit-Feld für Streckenkabel abrufen
            datum_verlegt = self.dlg.mDateTimeEdit_Strecke.dateTime().toString("yyyy-MM-dd HH:mm:ss")

            # Hole die Datenbankverbindung
            db_uri = self.get_database_connection()
            uri = QgsDataSourceUri(db_uri)

            # Verbinde zur PostgreSQL-Datenbank
            conn = psycopg2.connect(
                dbname=uri.database(),
                user=uri.username(),
                password=uri.password(),
                host=uri.host(),
                port=uri.port()
            )
            cur = conn.cursor()

            # Starte die Datenbank-Transaktion
            conn.autocommit = False

            # Kabeltyp-ID basierend auf dem ausgewählten Text abrufen
            kabel_name = self.dlg.comboBox_kabel_typ.currentText()
            kabeltyp_id = self.get_kabeltyp_id(kabel_name)

            # Überprüfen, ob die Kabeltyp-ID abgerufen werden konnte
            if not kabeltyp_id:
                raise Exception("Kabeltyp-ID konnte nicht abgerufen werden.")

            # Bestimme die nächste verfügbare Kabel-ID
            kabel_id = self.get_next_kabel_id()

            # Iteriere durch die Verlaufsliste und füge die Daten ein
            for index, verlauf_id in enumerate(self.verlauf_ids, start=1):
                seg_id = index
                kommentar = self.dlg.label_Kommentar.text()
                bezeichnung_intern = self.dlg.label_Kommentar_2.text()
                verlegestatus = self.dlg.comboBox_Verlegestatus.currentText()
                gefoerdert = self.dlg.comboBox_Gefoerdert.currentText()

                # Leerrohr-Daten
                layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
                feature = next(layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {verlauf_id}')))
                von_knoten = feature["VONKNOTEN"]
                nach_knoten = feature["NACHKNOTEN"]
                trassen_id = feature["ID_Trasse"]

                # SQL-Abfrage mit dem Datum hinzufügen
                insert_query = """
                INSERT INTO "lwl"."LWL_Kabel_Verlegt"
                ("KABEL_ID", "KABELTYP", "ID_LEERROHR", "ID_TRASSE", "VONKNOTEN", "NACHKNOTEN", "SEGMENT_ID", 
                 "KOMMENTAR", "BEZEICHNUNG_INTERN", "VERLEGESTATUS", "STARTKNOTEN", "ENDKNOTEN", "GEFOERDERT", 
                 "TYP", "DATUM_VERLEGT")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """

                # Daten einsetzen
                cur.execute(insert_query, (
                    kabel_id, kabeltyp_id, verlauf_id, trassen_id, von_knoten, nach_knoten, seg_id, kommentar,
                    bezeichnung_intern, verlegestatus, 
                    self.startpunkt_id if index == 1 else None,
                    self.endpunkt_id if index == len(self.verlauf_ids) else None,
                    gefoerdert, "Streckenkabel", datum_verlegt
                ))

            # Transaktion abschließen
            conn.commit()

            # Erfolgsmeldung anzeigen
            self.iface.messageBar().pushMessage("Erfolg", "Kabel wurden erfolgreich importiert.", level=Qgis.Success)
            self.reset_form()

        except Exception as e:
            # Fehlerbehandlung und Rollback der Transaktion
            conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Import fehlgeschlagen: {str(e)}", level=Qgis.Critical)

        finally:
            if conn is not None:
                conn.close()

    def aktion_startknoten_2(self):
        """Aktion für den Startknoten im zweiten Tab"""
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Startpunkt (VKG) der Hauseinführung", level=Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
        self.iface.setActiveLayer(layer)

        self.iface.actionSelect().trigger()

        def onStartpunktSelected():
            # Entferne alle bestehenden Highlights, bevor neue hinzugefügt werden
            for highlight in self.highlights_2:
                highlight.hide()
            self.highlights_2.clear()
            
            selected_features_2 = [feature for feature in layer.selectedFeatures() if feature["TYP"] in ["Ortszentrale", "Verteilerkasten"]]
            
            if selected_features_2:
                startpunkt_id_2 = selected_features_2[0].id()
                self.startpunkt_bezeichnung = selected_features_2[0].attribute("BEZEICHNUNG")  # Setzen der Bezeichnung
                print("Startpunkt-Bezeichnung (gefunden):", self.startpunkt_bezeichnung)

                # Überprüfen, ob die Bezeichnung wirklich vorhanden ist
                if not self.startpunkt_bezeichnung:
                    print("Warnung: Attribut 'BEZEICHNUNG' konnte im Startknoten nicht gefunden werden.")
                    self.startpunkt_bezeichnung = "Unbekannt"

                self.dlg.label_startpunkt_2.setText(f"Startknoten: {startpunkt_id_2}")
                self.startpunkt_id_2 = startpunkt_id_2

                geom = selected_features_2[0].geometry()
                highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                highlight.setColor(Qt.blue)
                highlight.setWidth(4)
                highlight.show()
                self.highlights_2.append(highlight)

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(onStartpunktSelected)

    def aktion_virtuellerknoten_2(self):
        """Aktion für den virtuellen Knoten im zweiten Tab"""
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Virtuellen Knoten am Ende der Hauseinführung (Tab 2)", level=Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Knoten")[0]
        self.iface.setActiveLayer(layer)

        self.iface.actionSelect().trigger()

        def onVirtuellerKnotenSelected():
            # Entferne alle bestehenden Highlights, bevor neue hinzugefügt werden
            for highlight in self.highlights_2:
                highlight.hide()
            self.highlights_2.clear()
            
            # Filtere nur Features mit TYP = "Virtueller Knoten"
            selected_features_2 = [feature for feature in layer.selectedFeatures() if feature["TYP"] == "Virtueller Knoten"]

            if selected_features_2:
                virtueller_knoten_id = selected_features_2[0].id()
                self.dlg.label_virtueller_knoten.setText(f"Virtueller Knoten: {virtueller_knoten_id}")
                self.virtueller_knoten_id = virtueller_knoten_id

                geom = selected_features_2[0].geometry()
                highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                highlight.setColor(Qt.blue)
                highlight.setWidth(4)
                highlight.show()
                self.highlights_2.append(highlight)

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(onVirtuellerKnotenSelected)

    def aktion_endpunkt_2(self):
        """Aktion für den Hausanschlusspunkt im zweiten Tab"""
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Hausanschluss aus (Tab 2)", level=Qgis.Info)
        layer = QgsProject.instance().mapLayersByName("LWL_Hausanschluss")[0]
        self.iface.setActiveLayer(layer)

        self.iface.actionSelect().trigger()

        def onEndpunktSelected():
            # Entferne alle bestehenden Highlights, bevor neue hinzugefügt werden
            for highlight in self.highlights_2:
                highlight.hide()
            self.highlights_2.clear()
            
            selected_features_2 = layer.selectedFeatures()
            if selected_features_2:
                hausanschluss_id = selected_features_2[0].id()
                self.dlg.label_hausanschluss.setText(f"Hausanschluss: {hausanschluss_id}")
                self.endpunkt_id_2 = hausanschluss_id  # Hier setzen wir endpunkt_id_2

                geom = selected_features_2[0].geometry()
                highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                highlight.setColor(Qt.blue)
                highlight.setWidth(4)
                highlight.show()
                self.highlights_2.append(highlight)

        try:
            layer.selectionChanged.disconnect()
        except TypeError:
            pass

        layer.selectionChanged.connect(onEndpunktSelected)

    def aktion_verlauf_2(self):
        """Aktion für den Verlauf im zweiten Tab"""
        # Setze das Verlauf-Label und die Verlaufs-IDs für Tab 2 zurück
        self.dlg.label_verlauf_2.clear()
        self.verlauf_ids_2.clear()
        self.iface.messageBar().pushMessage("Bitte wählen Sie den Verlauf (Leerrohrfolge)", level=Qgis.Info)
        
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]  # Direkte Referenz auf den Verlauf-Layer
        self.iface.setActiveLayer(layer)  # Aktiviert den Layer

        self.iface.actionSelect().trigger()  # Aktiviert das Auswahlwerkzeug

        def onVerlaufSelected():
            selected_features_2 = layer.selectedFeatures()

            if selected_features_2:
                # Füge die neu ausgewählten IDs zur Liste hinzu
                for feature in selected_features_2:
                    verlauf_id = feature["id"]
                    self.verlauf_ids_2.append(verlauf_id)

                # Aktualisiere das Label mit den IDs
                verlauf_text = "; ".join(map(str, self.verlauf_ids_2))  # Semikolon-getrennte IDs anzeigen
                self.dlg.label_verlauf_2.setText(f"Verlauf: {verlauf_text}")

                # Highlight-Funktion für die neuen Geometrien
                for feature in selected_features_2:
                    geom = feature.geometry()
                    highlight = QgsHighlight(self.iface.mapCanvas(), geom, layer)
                    highlight.setColor(Qt.blue)  # Farbe der Hervorhebung
                    highlight.setWidth(3)  # Linienstärke der Hervorhebung
                    highlight.show()

                    # Speichern der neuen Highlights für das spätere Entfernen
                    self.highlights_2.append(highlight)

        try:
            layer.selectionChanged.disconnect()  # Vorherige Verbindungen entfernen
        except TypeError:
            pass

        layer.selectionChanged.connect(onVerlaufSelected)

    def kabelverlauf_erstellen_2(self):
        """Funktion, um den Kabelverlauf in der Tabellenansicht anzuzeigen - Tab 2"""
        # Zusätzliche Attribute aus den Eingabefeldern holen
        kommentar = self.dlg.label_Kommentar_3.text()  # Kommentar
        verlegestatus = self.dlg.comboBox_Verlegestatus_2.currentText()  # Verlegestatus
        gefoerdert = self.dlg.comboBox_Gefoerdert_2.currentText()  # Gefördert

        # Erstellt eine Liste mit Zeilen, die in die Tabelle eingefügt werden
        kabelverlauf_daten = []
        
        # Startknoten
        kabelverlauf_daten.append([
            'Startknoten', 
            self.startpunkt_id_2, 
            '',  # Keine Verbindung für den Startknoten
            kommentar,
            verlegestatus, 
            gefoerdert
        ])
        
        # Verbindung der Leerrohre prüfen und darstellen
        for index, verlauf_id in enumerate(self.verlauf_ids_2, start=1):
            layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
            feature = next(layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {verlauf_id}')))
            
            von_knoten = feature["VONKNOTEN"]
            nach_knoten = feature["NACHKNOTEN"]
            
            verbindung_text = f"VON: {von_knoten}, NACH: {nach_knoten}"
            
            kabelverlauf_daten.append([
                f'Leerrohr {index}', 
                verlauf_id, 
                verbindung_text, 
                kommentar,
                verlegestatus, 
                gefoerdert
            ])
        
        # Hausanschluss
        kabelverlauf_daten.append([
            'Hausanschluss', 
            self.endpunkt_id_2, 
            '',  # Keine Verbindung für den Hausanschluss
            kommentar,
            verlegestatus, 
            gefoerdert
        ])

        # Erstelle ein Standard-Modell für die Tabelle
        model = QStandardItemModel()

        # Setze die Spaltenüberschriften (mit neuem Attribut)
        model.setHorizontalHeaderLabels(['Attribut', 'Wert', 'Verbindung', 'Kommentar', 'Verlegestatus', 'Gefördert'])

        # Befülle das Modell mit den Daten
        for row_data in kabelverlauf_daten:
            row = []
            for item in row_data:
                cell = QStandardItem(str(item))
                row.append(cell)
            model.appendRow(row)

        # Setze das Modell in die TableView
        self.dlg.tableView_Vorschau_2.setModel(model)

    def pruefe_verbindung_2(self):
        """Prüft die Verbindung für den zweiten Tab (Hauseinführung)"""
        if not self.startpunkt_id_2 or not self.virtueller_knoten_id or not self.endpunkt_id_2 or not self.verlauf_ids_2:
            self.dlg.label_Pruefung_2.setText("Unvollständige Daten.")
            self.dlg.label_Pruefung_2.setStyleSheet("background-color: lightcoral;")
            return

        korrekt = True
        letzter_knoten = self.startpunkt_id_2  # Starte mit dem Startpunkt
        layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]

        for idx, verlauf_id in enumerate(self.verlauf_ids_2):
            feature_iter = layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {verlauf_id}'))
            feature = next(feature_iter, None)

            if feature is None:
                QgsMessageLog.logMessage(f"Fehler: Leerrohr mit ID {verlauf_id} nicht gefunden.", level=Qgis.Critical)
                korrekt = False
                break

            leerrohr_typ = feature["TYP"]

            # Überprüfe Typ 3 Leerrohre
            if leerrohr_typ == 3:
                von_knoten = feature["VONKNOTEN"]
                nach_knoten = feature["NACHKNOTEN"]

                if letzter_knoten != von_knoten and letzter_knoten != nach_knoten:
                    QgsMessageLog.logMessage(
                        f"Fehler: Ungültige Verbindung bei Leerrohr ID {verlauf_id}. Letzter Knoten: {letzter_knoten}, VON: {von_knoten}, NACH: {nach_knoten}.", 
                        level=Qgis.Critical)
                    korrekt = False
                    break

                letzter_knoten = nach_knoten if letzter_knoten == von_knoten else von_knoten

            # Überprüfe Typ 4 Leerrohr (Hauseinführung)
            elif leerrohr_typ == 4:
                # Verifiziere, dass die PARENT_LEERROHR_ID des Typ-4-Leerrohrs dem letzten Typ-3-Leerrohr entspricht
                if feature["PARENT_LEERROHR_ID"] != self.verlauf_ids_2[idx - 1]:
                    QgsMessageLog.logMessage(
                        f"Fehler: Die Hauseinführung ist nicht korrekt mit dem letzten Leerrohr ID {self.verlauf_ids_2[idx - 1]} als PARENT_LEERROHR_ID verbunden.",
                        level=Qgis.Critical)
                    korrekt = False
                    break

                hausanschluss_layer = QgsProject.instance().mapLayersByName("LWL_Hausanschluss")[0]
                anschluss_feature_iter = hausanschluss_layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {self.endpunkt_id_2}'))
                anschluss_feature = next(anschluss_feature_iter, None)

                if anschluss_feature and anschluss_feature["ID_KNOTEN"] != self.virtueller_knoten_id:
                    QgsMessageLog.logMessage(
                        f"Fehler: Der Hausanschlusspunkt stimmt nicht mit dem virtuellen Knoten überein.", 
                        level=Qgis.Critical)
                    korrekt = False
                    break

        if korrekt:
            self.dlg.label_Pruefung_2.setText("Verlauf ist korrekt verbunden. Daten können importiert werden")
            self.dlg.label_Pruefung_2.setStyleSheet("background-color: lightgreen;")
            self.dlg.pushButton_Import_2.setEnabled(True)
        else:
            QgsMessageLog.logMessage("Fehler: Verlauf ist nicht korrekt verbunden.", level=Qgis.Critical)
            self.dlg.label_Pruefung_2.setText("Verlauf ist nicht verbunden. Bitte überprüfen Sie die Auswahl")
            self.dlg.label_Pruefung_2.setStyleSheet("background-color: lightcoral;")

    def daten_importieren_2(self):
        """Importiert die geprüften Daten in die Datenbank für Tab 2 (Hauseinführung)."""
        try:
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

            # Kabeltyp-ID für Tab 2
            kabel_name = self.dlg.comboBox_kabel_typ_2.currentText()
            kabeltyp_id = self.get_kabeltyp_id(kabel_name)
            if not kabeltyp_id:
                raise Exception("Kabeltyp-ID konnte nicht abgerufen werden.")

            # Bestimme die nächste verfügbare Kabel-ID
            kabel_id = self.get_next_kabel_id()

            # DATUM_VERLEGT aus mDateTimeEdit_Hauseinfuehrung abrufen
            datum_verlegt = self.dlg.mDateTimeEdit_Hauseinfuehrung.date().toString("yyyy-MM-dd")
            
            # Zusätzliche Attribute aus den Eingabefeldern holen
            kommentar = self.dlg.label_Kommentar_3.text()
            bezeichnung_intern = f"EK {self.startpunkt_bezeichnung}-{self.hausanschluss_id}"
            verlegestatus = self.dlg.comboBox_Verlegestatus_2.currentText()
            gefoerdert = self.dlg.comboBox_Gefoerdert_2.currentText()

            for index, verlauf_id in enumerate(self.verlauf_ids_2):
                seg_id = index + 1
                layer = QgsProject.instance().mapLayersByName("LWL_Leerrohr")[0]
                feature = next(layer.getFeatures(QgsFeatureRequest().setFilterExpression(f'"id" = {verlauf_id}')))

                von_knoten = feature["VONKNOTEN"] if feature["VONKNOTEN"] not in [QVariant(), None] else None
                nach_knoten = feature["NACHKNOTEN"] if feature["NACHKNOTEN"] not in [QVariant(), None] else None
                trassen_id = feature["ID_Trasse"] if feature["ID_Trasse"] not in [QVariant(), None] else None

                insert_query = """
                INSERT INTO "lwl"."LWL_Kabel_Verlegt"
                ("KABEL_ID", "KABELTYP", "DATUM_VERLEGT", "ID_LEERROHR", "ID_TRASSE", "VONKNOTEN", "NACHKNOTEN", 
                 "SEGMENT_ID", "KOMMENTAR", "BEZEICHNUNG_INTERN", "VERLEGESTATUS", "STARTKNOTEN", "GEFOERDERT", 
                 "HAUSANSCHLUSS_ID", "VIRTUELLER_KNOTEN", "TYP")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """

                cur.execute(insert_query, (
                    kabel_id,
                    kabeltyp_id,
                    datum_verlegt,  # Datumswert von mDateTimeEdit_Hauseinfuehrung
                    verlauf_id,
                    trassen_id,
                    von_knoten,
                    nach_knoten,
                    seg_id,
                    kommentar,
                    bezeichnung_intern,
                    verlegestatus,
                    self.startpunkt_id_2 if index == 0 else None,
                    gefoerdert,
                    self.endpunkt_id_2 if index == len(self.verlauf_ids_2) - 1 else None,
                    self.virtueller_knoten_id if index == len(self.verlauf_ids_2) - 1 else None,
                    "Hausanschlusskabel"
                ))

            conn.commit()
            self.iface.messageBar().pushMessage("Erfolg", "Hauseinführung wurde erfolgreich importiert.", level=Qgis.Success)
            self.reset_form_2()

        except Exception as e:
            conn.rollback()
            self.iface.messageBar().pushMessage("Fehler", f"Import fehlgeschlagen: {str(e)}", level=Qgis.Critical)

        finally:
            if conn is not None:
                conn.close()
