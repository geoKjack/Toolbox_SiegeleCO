from PyQt5.QtWidgets import QDialog, QMessageBox, QListWidget, QLineEdit, QPushButton, QTableView, QVBoxLayout, QLabel, QSizePolicy, QListWidgetItem
from PyQt5.QtCore import QSettings, Qt, pyqtSignal, QAbstractTableModel, QVariant
from .setup_dialog import Ui_SetupToolDialogBase
import psycopg2
import json
import base64
import os
from qgis.core import Qgis, QgsMessageLog, QgsProject
from qgis.gui import QgsFileWidget
from PyQt5.QtGui import QColor, QFont

class SetupTableModel(QAbstractTableModel):
    def __init__(self, data, headers, parent=None):
        super().__init__(parent)
        self._data = data
        self._headers = headers
        self.parent = parent

    def rowCount(self, parent):
        return len(self._data)

    def columnCount(self, parent):
        return len(self._headers)

    def data(self, index, role):
        if not index.isValid():
            return QVariant()
        if role == Qt.DisplayRole:
            value = self._data[index.row()][index.column()]
            if index.column() == 2:  # Auftraggeber-ID in Bezeichnung umwandeln
                if not self.parent.is_connected:
                    return "Keine Verbindung"
                try:
                    db_params = self.parent.get_database_connection()
                    conn = psycopg2.connect(**db_params)
                    cur = conn.cursor()
                    cur.execute("SELECT \"BEZEICHNUNG\" FROM \"Verwaltung_Intern\".\"Auftraggeber\" WHERE id = %s", (value,))
                    result = cur.fetchone()
                    conn.close()
                    return result[0] if result else "Unbekannt"
                except Exception as e:
                    QgsMessageLog.logMessage(f"Fehler beim Laden des Auftraggebers: {e}", "SetupTool", Qgis.Critical)
                    return "Fehler"
            elif index.column() in [4, 5, 6]:  # Leerrohr-C, Bündel-C, Faser-C
                return ", ".join(str(id) for id in value) if value else ""
            elif index.column() == 8:  # db_connection (als String anzeigen)
                return str(value) if value else ""
            elif index.column() == 9:  # leerrohr_subtyp (als String anzeigen)
                return ", ".join(str(id) for id in value) if value else ""
            return str(value)
        elif role == Qt.BackgroundRole:
            selected_row = self.parent.ui.tableView.currentIndex().row()
            if index.row() == selected_row:
                return QVariant(QColor(200, 200, 200))  # Dunkleres Grau für ausgewählte Zeile
            return QVariant()
        return QVariant()

    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self._headers[section]
        return QVariant()

class SetupTool(QDialog):
    def __init__(self, iface):
        super().__init__(None)
        self.ui = Ui_SetupToolDialogBase()
        self.ui.setupUi(self)
        self.setModal(False)
        self.iface = iface
        self.settings = QSettings("SiegeleCo", "ToolBox")
        self.is_connected = False
        self.current_setup_id = None
        self.current_qgis_project_path = ""
        self.conn = None  # Für offene Datenbankverbindung
        self.data_cache = {}  # Für spätere Cache-Implementierung
        QgsMessageLog.logMessage(f"tableView initialized: {self.ui.tableView is not None}", "SetupTool", Qgis.Info)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.ui.label_Kommentar_5.setEchoMode(QLineEdit.Password)
        style_sheet = """
        QListWidget::item:selected {
            background-color: #90EE90;
            color: black;
        }
        QListWidget::item:!selected {
            background-color: transparent;
        }
        """
        self.ui.listWidget_Firma.setStyleSheet(style_sheet)
        self.ui.listWidget_Leerohr.setStyleSheet(style_sheet)
        self.ui.listWidget_Buendel.setStyleSheet(style_sheet)
        self.ui.listWidget_Faser.setStyleSheet(style_sheet)
        self.ui.listWidget_Eigner.setStyleSheet(style_sheet)
        self.ui.listWidget_Leerohr_SubTyp.setStyleSheet(style_sheet)
        # QgsFileWidget konfigurieren
        try:
            self.ui.mQgsFileWidget.setFilter("QGIS-Projekte (*.qgz)")
            self.ui.mQgsFileWidget.setStorageMode(self.ui.mQgsFileWidget.StorageMode.GetFile)
            self.ui.mQgsFileWidget.fileChanged.connect(self.update_qgis_project_label)
            QgsMessageLog.logMessage("QgsFileWidget erfolgreich initialisiert.", "SetupTool", Qgis.Info)
        except AttributeError as e:
            QgsMessageLog.logMessage(f"Fehler: mQgsFileWidget nicht gefunden: {e}", "SetupTool", Qgis.Critical)
        try:
            self.ui.label_qgz.setText("Kein QGIS-Projekt gewählt")
            QgsMessageLog.logMessage("label_qgz erfolgreich initialisiert.", "SetupTool", Qgis.Info)
        except AttributeError as e:
            QgsMessageLog.logMessage(f"Fehler: label_qgz nicht gefunden: {e}", "SetupTool", Qgis.Critical)
        # Signale verknüpfen
        self.ui.pushButton_Save.clicked.connect(self.save_settings)
        self.ui.button_box.button(self.ui.button_box.Cancel).clicked.connect(self.close)
        self.ui.button_box.button(self.ui.button_box.Reset).clicked.connect(self.reset_settings)
        self.ui.button_box.button(self.ui.button_box.Apply).clicked.connect(self.apply_configuration)
        self.ui.button_box.button(self.ui.button_box.Open).clicked.connect(self.open_configuration)
        self.ui.button_box.button(self.ui.button_box.Discard).clicked.connect(self.delete_configuration)
        self.ui.pushButton_Verbindung.clicked.connect(self.test_connection)
        self.ui.pushButton_Verbindung_Trennen.clicked.connect(self.disconnect_connection)
        self.ui.comboBox_Auftraggeber.currentTextChanged.connect(self.update_auftraggeber_label)
        self.ui.pushButton_Codierung.clicked.connect(self.update_codierung_from_subtyp)
        if self.ui.tableView is not None and self.ui.tableView.selectionModel() is not None:
            self.ui.tableView.selectionModel().selectionChanged.connect(self.update_table_selection)
        self.ui.listWidget_Firma.itemSelectionChanged.connect(self.update_leerrohr_subtyp)
        self.ui.listWidget_Leerohr.itemSelectionChanged.connect(self.update_leerrohr_subtyp)
        # Umgebung befüllen und Farbe setzen
        self.populate_umgebung()
        self.update_setup_label()
        self.update_qgis_project_label()
        # Automatisch laden, wenn gespeicherte Verbindung existiert
        self.load_connection_settings()

    def update_setup_label(self):
        try:
            setup_name = self.settings.value("name", "")
            if setup_name:
                self.ui.label_Setup.setText(f"Setup: {setup_name}")
                self.ui.label_Setup.setStyleSheet("background-color: #90EE90; font-weight: bold;")
            else:
                self.ui.label_Setup.setText("Kein Setup gewählt")
                self.ui.label_Setup.setStyleSheet("background-color: transparent; font-weight: normal;")
        except AttributeError:
            setup_name = self.settings.value("name", "")
            if setup_name:
                self.ui.label_gewaehlter_Auftraggeber.setText(f"Setup: {setup_name}")
                self.ui.label_gewaehlter_Auftraggeber.setStyleSheet("background-color: #90EE90; font-weight: bold; color: black;")
            else:
                self.ui.label_gewaehlter_Auftraggeber.setText("Kein Setup gewählt")
                self.ui.label_gewaehlter_Auftraggeber.setStyleSheet("background-color: transparent; font-weight: normal; color: black;")

    def update_qgis_project_label(self):
        try:
            # Verwende den gespeicherten qgis_project_path oder den aktuellen Setup-Pfad
            project_path = self.current_qgis_project_path or self.settings.value("qgis_project_path", "")
            if project_path and os.path.exists(project_path):
                project_name = os.path.splitext(os.path.basename(project_path))[0]
                self.ui.label_qgz.setText(f" {project_name}")
                self.ui.label_qgz.setStyleSheet("background-color: #90EE90; font-weight: bold;")
            else:
                self.ui.label_qgz.setText("Kein QGIS-Projekt gewählt")
                self.ui.label_qgz.setStyleSheet("background-color: transparent; font-weight: normal;")
        except AttributeError as e:
            QgsMessageLog.logMessage(f"Fehler beim Aktualisieren von label_qgz: {e}", "SetupTool", Qgis.Critical)

    def load_connection_settings(self):
        print("DEBUG: Starte load_connection_settings")
        username = self.settings.value("connection_username", "")
        password = base64.b64decode(self.settings.value("connection_password", "").encode()).decode() if self.settings.value("connection_password", "") else ""
        umgebung = self.settings.value("connection_umgebung", "")
        if username and password and umgebung:
            self.ui.label_Kommentar_3.setText(username)
            self.ui.label_Kommentar_5.setText(password)
            index = self.ui.comboBox_Umgebung.findText(umgebung)
            if index != -1:
                self.ui.comboBox_Umgebung.setCurrentIndex(index)
            # Automatisch verbinden und offen halten
            db_params = self.get_database_connection(username, password, umgebung)
            try:
                self.conn = psycopg2.connect(**db_params)
                self.iface.plugin.conn = self.conn  # Verbindung für andere Tools
                self.is_connected = True
                self.ui.pushButton_Verbindung.setStyleSheet("background-color: #90EE90;")
                self.iface.messageBar().pushMessage("Erfolg", f"Automatische Verbindung zu {umgebung} hergestellt!", level=Qgis.Success)
                # Cache befüllen
                self.data_cache = {
                    "firma": [],
                    "codierung_leerrohr": [],
                    "codierung_buendel": [],
                    "codierung_faser": [],
                    "auftraggeber": [],
                    "eigner": ["Gemeinde", "TIWAG", "TIGAS"],
                    "leerrohr_subtyp": []
                }
                print("DEBUG: Initialisiere data_cache")
                try:
                    cur = self.conn.cursor()
                    # Firma
                    cur.execute("SELECT DISTINCT \"FIRMA\" FROM lwl.\"LUT_Leerrohr_SubTyp\" ORDER BY \"FIRMA\"")
                    result = cur.fetchall()
                    self.data_cache["firma"] = [row[0] for row in result if row[0]]
                    print(f"DEBUG: Firma Cache: {self.data_cache['firma']} (Rows: {len(result)})")
                    # Codierung Leerrohr
                    cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Leerrohr%' ORDER BY \"BEMERKUNG\"")
                    result = cur.fetchall()
                    self.data_cache["codierung_leerrohr"] = [(row[0], row[1], row[2]) for row in result if row[1]]
                    print(f"DEBUG: Leerrohr Codierung Cache: {self.data_cache['codierung_leerrohr']} (Rows: {len(result)})")
                    # Codierung Bündel
                    cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Bündel%' ORDER BY \"BEMERKUNG\"")
                    result = cur.fetchall()
                    self.data_cache["codierung_buendel"] = [(row[0], row[1], row[2]) for row in result if row[1]]
                    print(f"DEBUG: Bündel Codierung Cache: {self.data_cache['codierung_buendel']} (Rows: {len(result)})")
                    # Codierung Faser
                    cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Faser%' ORDER BY \"BEMERKUNG\"")
                    result = cur.fetchall()
                    self.data_cache["codierung_faser"] = [(row[0], row[1], row[2]) for row in result if row[1]]
                    print(f"DEBUG: Faser Codierung Cache: {self.data_cache['codierung_faser']} (Rows: {len(result)})")
                    # Auftraggeber
                    cur.execute("SELECT \"BEZEICHNUNG\" FROM \"Verwaltung_Intern\".\"Auftraggeber\" ORDER BY \"BEZEICHNUNG\"")
                    result = cur.fetchall()
                    self.data_cache["auftraggeber"] = [row[0] for row in result if row[0]]
                    print(f"DEBUG: Auftraggeber Cache: {self.data_cache['auftraggeber']} (Rows: {len(result)})")
                    # Leerrohr-Subtyp
                    cur.execute("""
                    SELECT s.id, s."ID_TYP", s."SUBTYP_char", c."CODIERUNG", c."BEMERKUNG", c."id" AS codierung_id
                    FROM lwl."LUT_Leerrohr_SubTyp" s
                    JOIN lwl."LUT_Codierung" c ON s."ID_CODIERUNG" = c."id"
                    ORDER BY s.id
                    """)
                    result = cur.fetchall()
                    self.data_cache["leerrohr_subtyp"] = [(row[0], row[1], row[2], row[3], row[4], row[5]) for row in result]
                    print(f"DEBUG: Leerrohr-Subtyp Cache: {self.data_cache['leerrohr_subtyp']} (Rows: {len(result)})")
                    if not self.data_cache["leerrohr_subtyp"]:
                        print("DEBUG: WARNUNG: Keine Leerrohr-Subtypen geladen!")
                        QgsMessageLog.logMessage("Kritischer Fehler: Keine Leerrohr-Subtypen geladen!", "SetupTool", Qgis.Critical)
                    cur.close()
                    if not any(self.data_cache[key] for key in self.data_cache if key != "eigner"):
                        print("DEBUG: Kritischer Fehler: Keine Cache-Daten geladen!")
                        QgsMessageLog.logMessage("Kritischer Fehler: Keine Cache-Daten geladen!", "SetupTool", Qgis.Critical)
                        self.iface.messageBar().pushMessage("Fehler", "Keine Daten für Cache geladen. Überprüfen Sie DB-Zugriff!", level=Qgis.Critical)
                    else:
                        print(f"DEBUG: Cache erfolgreich befüllt: {self.data_cache}")
                        QgsMessageLog.logMessage(f"Cache erfolgreich befüllt: {self.data_cache}", "SetupTool", Qgis.Info)
                except Exception as e:
                    print(f"DEBUG: Kritischer Fehler beim Befüllen des Caches: {e}")
                    QgsMessageLog.logMessage(f"Kritischer Fehler beim Befüllen des Caches: {e}", "SetupTool", Qgis.Critical)
                    self.iface.messageBar().pushMessage("Fehler", f"Cache-Befüllung fehlgeschlagen: {e}", level=Qgis.Critical)
                    return  # Beende, wenn Cache fehlschlägt, um UI nicht zu befüllen
                # Befülle UI-Elemente aus Cache
                self.populate_firma()
                self.populate_codierung_leerrohr()
                self.populate_auftraggeber()
                self.populate_codierung_buendel()
                self.populate_codierung_faser()
                self.populate_eigner()
                self.load_configurations()
                self.update_leerrohr_subtyp()
                self.update_setup_label()
                self.update_qgis_project_label()
                # Stelle gespeicherte Auswahl wieder her
                firma = self.settings.value("firma", "").split(", ") if self.settings.value("firma", "") else []
                codierung_leerrohr = self.settings.value("codierung_leerrohr", "").split(", ") if self.settings.value("codierung_leerrohr", "") else []
                codierung_buendel = self.settings.value("codierung_buendel", "").split(", ") if self.settings.value("codierung_buendel", "") else []
                codierung_faser = self.settings.value("codierung_faser", "").split(", ") if self.settings.value("codierung_faser", "") else []
                eigner = self.settings.value("eigner", "").split(", ") if self.settings.value("eigner", "") else []
                auftraggeber = self.settings.value("auftraggeber", "")
                leerrohr_subtyp = [int(x) for x in self.settings.value("leerrohr_subtyp", []) if x] if self.settings.value("leerrohr_subtyp", []) else []
                name = self.settings.value("name", "")
                self.current_qgis_project_path = self.resolve_onedrive_path(self.settings.value("qgis_project_path", ""))
                for item in self.ui.listWidget_Firma.findItems("", Qt.MatchContains):
                    if item.text() in firma:
                        item.setSelected(True)
                for item in self.ui.listWidget_Leerohr.findItems("", Qt.MatchContains):
                    text = item.text()
                    id_start = text.find("(ID: ") + 5
                    id_end = text.find(")", id_start)
                    if id_start > 4 and id_end > id_start:
                        id_value = text[id_start:id_end]
                        if id_value in codierung_leerrohr:
                            item.setSelected(True)
                for item in self.ui.listWidget_Buendel.findItems("", Qt.MatchContains):
                    text = item.text()
                    id_start = text.find("(ID: ") + 5
                    id_end = text.find(")", id_start)
                    if id_start > 4 and id_end > id_start:
                        id_value = text[id_start:id_end]
                        if id_value in codierung_buendel:
                            item.setSelected(True)
                for item in self.ui.listWidget_Faser.findItems("", Qt.MatchContains):
                    text = item.text()
                    id_start = text.find("(ID: ") + 5
                    id_end = text.find(")", id_start)
                    if id_start > 4 and id_end > id_start:
                        id_value = text[id_start:id_end]
                        if id_value in codierung_faser:
                            item.setSelected(True)
                for item in self.ui.listWidget_Eigner.findItems("", Qt.MatchContains):
                    if item.text() in eigner:
                        item.setSelected(True)
                self.ui.comboBox_Auftraggeber.setCurrentText(auftraggeber)
                self.update_auftraggeber_label(auftraggeber)
                if hasattr(self.ui, 'lineEdit_Name'):
                    self.ui.lineEdit_Name.setText(name)
                try:
                    self.ui.mQgsFileWidget.setFilePath(self.current_qgis_project_path)
                except AttributeError as e:
                    QgsMessageLog.logMessage(f"Fehler beim Setzen von mQgsFileWidget: {e}", "SetupTool", Qgis.Critical)
                # Subtypen nachladen
                self.update_leerrohr_subtyp()
                for item in self.ui.listWidget_Leerohr_SubTyp.findItems("", Qt.MatchContains):
                    try:
                        subtyp_id = int(item.text().split(" - ")[0])
                        if subtyp_id in leerrohr_subtyp:
                            item.setSelected(True)
                    except ValueError:
                        continue
            except Exception as e:
                self.is_connected = False
                self.conn = None
                self.ui.pushButton_Verbindung.setStyleSheet("background-color: #FF6347;")
                QgsMessageLog.logMessage(f"Automatischer Verbindungsfehler zu {umgebung}: {e}", "SetupTool", Qgis.Critical)
                self.iface.messageBar().pushMessage("Fehler", f"Automatische Verbindung fehlgeschlagen: {e}", level=Qgis.Critical)
                # UI leer lassen
                self.ui.pushButton_Verbindung.setStyleSheet("background-color: gray;")
                self.ui.label_Kommentar_3.clear()
                self.ui.label_Kommentar_5.clear()
                self.ui.comboBox_Auftraggeber.clear()
                self.ui.listWidget_Firma.clear()
                self.ui.listWidget_Leerohr.clear()
                self.ui.listWidget_Buendel.clear()
                self.ui.listWidget_Faser.clear()
                self.ui.listWidget_Eigner.clear()
                self.ui.listWidget_Leerohr_SubTyp.clear()
                self.ui.tableView.setModel(None)
                try:
                    self.ui.mQgsFileWidget.setFilePath("")
                    self.current_qgis_project_path = ""
                except AttributeError as e:
                    QgsMessageLog.logMessage(f"Fehler beim Zurücksetzen von mQgsFileWidget: {e}", "SetupTool", Qgis.Critical)
                self.update_qgis_project_label()
        else:
            # Keine gespeicherte Verbindung – leer bleiben
            self.ui.pushButton_Verbindung.setStyleSheet("background-color: gray;")
            self.ui.label_Kommentar_3.clear()
            self.ui.label_Kommentar_5.clear()
            self.ui.comboBox_Auftraggeber.clear()
            self.ui.listWidget_Firma.clear()
            self.ui.listWidget_Leerohr.clear()
            self.ui.listWidget_Buendel.clear()
            self.ui.listWidget_Faser.clear()
            self.ui.listWidget_Eigner.clear()
            self.ui.listWidget_Leerohr_SubTyp.clear()
            self.ui.tableView.setModel(None)
            try:
                self.ui.mQgsFileWidget.setFilePath("")
                self.current_qgis_project_path = ""
            except AttributeError as e:
                QgsMessageLog.logMessage(f"Fehler beim Zurücksetzen von mQgsFileWidget: {e}", "SetupTool", Qgis.Critical)
            self.update_qgis_project_label()

    def update_auftraggeber_label(self, text):
        if text:
            self.ui.label_gewaehlter_Auftraggeber.setText(f"Auftraggeber: {text}")
            self.ui.label_gewaehlter_Auftraggeber.setStyleSheet("color: green; font-weight: normal;")
        else:
            self.ui.label_gewaehlter_Auftraggeber.setText("Kein Auftraggeber gewählt")
            self.ui.label_gewaehlter_Auftraggeber.setStyleSheet("color: black; font-weight: normal;")

    def update_table_selection(self):
        self.ui.tableView.viewport().update()

    def update_leerrohr_subtyp(self):
        if not self.is_connected:
            self.ui.listWidget_Leerohr_SubTyp.clear()
            return
        selected_firmen = [self.ui.listWidget_Firma.item(i).text() for i in range(self.ui.listWidget_Firma.count()) if self.ui.listWidget_Firma.item(i).isSelected()]
        selected_leerrohr_ids = []
        for i in range(self.ui.listWidget_Leerohr.count()):
            item = self.ui.listWidget_Leerohr.item(i)
            if item.isSelected():
                text = item.text()
                id_start = text.find("(ID: ") + 5
                id_end = text.find(")", id_start)
                if id_start > 4 and id_end > id_start:
                    id_value = int(text[id_start:id_end])
                    selected_leerrohr_ids.append(id_value)
        current_selected_subtyp_ids = [int(item.text().split(" - ")[0]) for item in self.ui.listWidget_Leerohr_SubTyp.selectedItems()] if self.ui.listWidget_Leerohr_SubTyp.selectedItems() else []
        self.populate_leerrohr_subtyp(selected_firmen, selected_leerrohr_ids, current_selected_subtyp_ids)

    def populate_leerrohr_subtyp(self, selected_firmen, selected_leerrohr_ids, current_selected_subtyp_ids):
        if not self.is_connected:
            self.ui.listWidget_Leerohr_SubTyp.clear()
            self.iface.messageBar().pushMessage("Info", "Bitte stellen Sie eine Verbindung her, um Subtypen zu laden.", level=Qgis.Info)
            return
        try:
            subtyp_items = []
            for subtyp in self.data_cache["leerrohr_subtyp"]:
                subtyp_id, typ_nummer, subtyp_char, codierung, bemerkung, codierung_id = subtyp
                if (not selected_firmen or subtyp[0] in [s[0] for s in self.data_cache["leerrohr_subtyp"] if s[0]]) and \
                   (not selected_leerrohr_ids or codierung_id in selected_leerrohr_ids):
                    codierung_text = f"{codierung} - {bemerkung} (ID: {codierung_id})"
                    subtyp_items.append(f"{subtyp_id} - {typ_nummer} - {subtyp_char} - {codierung_text}")
            self.ui.listWidget_Leerohr_SubTyp.clear()
            self.ui.listWidget_Leerohr_SubTyp.addItems(subtyp_items)
            self.ui.listWidget_Leerohr_SubTyp.setSelectionMode(QListWidget.MultiSelection)
            for i in range(self.ui.listWidget_Leerohr_SubTyp.count()):
                item = self.ui.listWidget_Leerohr_SubTyp.item(i)
                try:
                    subtyp_id = int(item.text().split(" - ")[0])
                    if subtyp_id in current_selected_subtyp_ids:
                        item.setSelected(True)
                except ValueError:
                    continue
            QgsMessageLog.logMessage(f"Loaded subtypen from cache: {subtyp_items}", "SetupTool", Qgis.Info)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Subtypen aus Cache: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Subtypen fehlgeschlagen: {e}", level=Qgis.Critical)

    def update_codierung_from_subtyp(self):
        """Aktualisiert die Codierungen basierend auf den gewählten Leerrohr-Subtypen."""
        if not self.is_connected:
            self.iface.messageBar().pushMessage(
                "Fehler", "Bitte stellen Sie eine Verbindung her, um die Codierungen zu aktualisieren.", level=Qgis.Critical
            )
            return
        # Hole die ausgewählten Subtypen
        selected_subtyp_ids = []
        for item in self.ui.listWidget_Leerohr_SubTyp.selectedItems():
            try:
                subtyp_id = int(item.text().split(" - ")[0])
                selected_subtyp_ids.append(subtyp_id)
            except ValueError:
                continue
        if not selected_subtyp_ids:
            self.iface.messageBar().pushMessage(
                "Info", "Keine Subtypen ausgewählt. Bitte wählen Sie mindestens einen Subtyp aus.", level=Qgis.Info
            )
            return
        # Hole Verbindungsparameter
        username = self.ui.label_Kommentar_3.text()
        password = self.ui.label_Kommentar_5.text()
        umgebung = self.ui.comboBox_Umgebung.currentText()
        db_params = self.get_database_connection(username, password, umgebung)
        try:
            conn = psycopg2.connect(**db_params)
            cur = conn.cursor()
            # Ermittle die Codierungen (ID_CODIERUNG) für die ausgewählten Subtypen
            query = """
            SELECT DISTINCT c."id", c."CODIERUNG", c."BEMERKUNG"
            FROM lwl."LUT_Leerrohr_SubTyp" s
            JOIN lwl."LUT_Codierung" c ON s."ID_CODIERUNG" = c."id"
            WHERE s."id" = ANY(%s)
            ORDER BY c."BEMERKUNG"
            """
            cur.execute(query, (selected_subtyp_ids,))
            codierungen = cur.fetchall()
            if not codierungen:
                self.iface.messageBar().pushMessage(
                    "Info", "Keine Codierungen für die ausgewählten Subtypen gefunden.", level=Qgis.Info
                )
                return
            # Aktualisiere die Codierungsliste
            self.ui.listWidget_Leerohr.clear()
            codierung_items = [f"{row[1]} - {row[2]} (ID: {row[0]})" for row in codierungen]
            self.ui.listWidget_Leerohr.addItems(codierung_items)
            self.ui.listWidget_Leerohr.setSelectionMode(QListWidget.MultiSelection)
            # Wähle alle Codierungen aus, die den Subtypen entsprechen
            for i in range(self.ui.listWidget_Leerohr.count()):
                item = self.ui.listWidget_Leerohr.item(i)
                item.setSelected(True)
            # Aktualisiere die Subtypen-Liste, um die Konsistenz zu wahren
            selected_firmen = [self.ui.listWidget_Firma.item(i).text() for i in range(self.ui.listWidget_Firma.count()) if self.ui.listWidget_Firma.item(i).isSelected()]
            selected_leerrohr_ids = [row[0] for row in codierungen]  # IDs der Codierungen
            self.populate_leerrohr_subtyp(selected_firmen, selected_leerrohr_ids, selected_subtyp_ids)
            self.iface.messageBar().pushMessage(
                "Erfolg", "Codierungen basierend auf Subtypen erfolgreich aktualisiert.", level=Qgis.Success
            )
            QgsMessageLog.logMessage(f"Updated codierungen: {codierung_items}", "SetupTool", Qgis.Info)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Aktualisieren der Codierungen: {e}", "SetupTool", level=Qgis.Critical)
            self.iface.messageBar().pushMessage(
                "Fehler", f"Aktualisieren der Codierungen fehlgeschlagen: {e}", level=Qgis.Critical
            )
        finally:
            if 'conn' in locals():
                cur.close()
                conn.close()

    def get_firma_for_subtyp(self, subtyp_id):
        username = self.ui.label_Kommentar_3.text()
        password = self.ui.label_Kommentar_5.text()
        umgebung = self.ui.comboBox_Umgebung.currentText()
        db_params = self.get_database_connection(username, password, umgebung)
        try:
            conn = psycopg2.connect(**db_params)
            cur = conn.cursor()
            cur.execute("SELECT \"FIRMA\" FROM lwl.\"LUT_Leerrohr_SubTyp\" WHERE id = %s", (subtyp_id,))
            firma = cur.fetchone()
            cur.close()
            conn.close()
            return firma[0] if firma else None
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Firma für Subtyp: {e}", "SetupTool", Qgis.Critical)
            return None

    def validate_inputs(self):
        errors = []
        if not self.ui.label_Kommentar_3.text():
            errors.append("Benutzername fehlt.")
        if not self.ui.label_Kommentar_5.text():
            errors.append("Passwort fehlt.")
        if not self.ui.comboBox_Umgebung.currentText():
            errors.append("Umgebung fehlt.")
        if errors:
            self.iface.messageBar().pushMessage("Fehler", "; ".join(errors), level=Qgis.Critical)
            return False
        return True

    def test_connection(self):
        if not self.validate_inputs():
            return
        username = self.ui.label_Kommentar_3.text()
        password = self.ui.label_Kommentar_5.text()
        umgebung = self.ui.comboBox_Umgebung.currentText()
        db_params = self.get_database_connection(username, password, umgebung)
        try:
            # Verbindung öffnen und offen halten
            if self.conn:
                self.conn.close()
            self.conn = psycopg2.connect(**db_params)
            self.iface.plugin.conn = self.conn  # Verbindung für andere Tools
            self.is_connected = True
            self.ui.pushButton_Verbindung.setStyleSheet("background-color: #90EE90;")
            self.iface.messageBar().pushMessage("Erfolg", f"Verbindung zu {umgebung} hergestellt!", level=Qgis.Success)
            # Speichere Verbindungseinstellungen
            self.settings.setValue("connection_username", username)
            self.settings.setValue("connection_password", base64.b64encode(password.encode()).decode())
            self.settings.setValue("connection_umgebung", umgebung)
            # Daten in Cache laden
            self.data_cache = {
                "firma": [],
                "codierung_leerrohr": [],
                "codierung_buendel": [],
                "codierung_faser": [],
                "auftraggeber": [],
                "eigner": ["Gemeinde", "TIWAG", "TIGAS"],
                "leerrohr_subtyp": []
            }
            try:
                cur = self.conn.cursor()
                # Firma
                cur.execute("SELECT DISTINCT \"FIRMA\" FROM lwl.\"LUT_Leerrohr_SubTyp\" ORDER BY \"FIRMA\"")
                result = cur.fetchall()
                self.data_cache["firma"] = [row[0] for row in result if row[0]]
                QgsMessageLog.logMessage(f"Firma Cache: {self.data_cache['firma']} (Rows: {len(result)})", "SetupTool", Qgis.Info)
                # Codierung Leerrohr
                cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Leerrohr%' ORDER BY \"BEMERKUNG\"")
                result = cur.fetchall()
                self.data_cache["codierung_leerrohr"] = [(row[0], row[1], row[2]) for row in result if row[1]]
                QgsMessageLog.logMessage(f"Leerrohr Codierung Cache: {self.data_cache['codierung_leerrohr']} (Rows: {len(result)})", "SetupTool", Qgis.Info)
                # Codierung Bündel
                cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Bündel%' ORDER BY \"BEMERKUNG\"")
                result = cur.fetchall()
                self.data_cache["codierung_buendel"] = [(row[0], row[1], row[2]) for row in result if row[1]]
                QgsMessageLog.logMessage(f"Bündel Codierung Cache: {self.data_cache['codierung_buendel']} (Rows: {len(result)})", "SetupTool", Qgis.Info)
                # Codierung Faser
                cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Faser%' ORDER BY \"BEMERKUNG\"")
                result = cur.fetchall()
                self.data_cache["codierung_faser"] = [(row[0], row[1], row[2]) for row in result if row[1]]
                QgsMessageLog.logMessage(f"Faser Codierung Cache: {self.data_cache['codierung_faser']} (Rows: {len(result)})", "SetupTool", Qgis.Info)
                # Auftraggeber
                cur.execute("SELECT \"BEZEICHNUNG\" FROM \"Verwaltung_Intern\".\"Auftraggeber\" ORDER BY \"BEZEICHNUNG\"")
                result = cur.fetchall()
                self.data_cache["auftraggeber"] = [row[0] for row in result if row[0]]
                QgsMessageLog.logMessage(f"Auftraggeber Cache: {self.data_cache['auftraggeber']} (Rows: {len(result)})", "SetupTool", Qgis.Info)
                # Leerrohr-Subtyp
                cur.execute("""
                SELECT s.id, s."ID_TYP", s."SUBTYP_char", c."CODIERUNG", c."BEMERKUNG", c."id" AS codierung_id
                FROM lwl."LUT_Leerrohr_SubTyp" s
                JOIN lwl."LUT_Codierung" c ON s."ID_CODIERUNG" = c."id"
                ORDER BY s.id
                """)
                result = cur.fetchall()
                self.data_cache["leerrohr_subtyp"] = [(row[0], row[1], row[2], row[3], row[4], row[5]) for row in result]
                QgsMessageLog.logMessage(f"Leerrohr-Subtyp Cache: {self.data_cache['leerrohr_subtyp']} (Rows: {len(result)})", "SetupTool", Qgis.Info)
                cur.close()
                if not any(self.data_cache[key] for key in self.data_cache if key != "eigner"):
                    QgsMessageLog.logMessage("Kritischer Fehler: Keine Cache-Daten geladen!", "SetupTool", Qgis.Critical)
                    self.iface.messageBar().pushMessage("Fehler", "Keine Daten für Cache geladen. Überprüfen Sie DB-Zugriff!", level=Qgis.Critical)
                else:
                    QgsMessageLog.logMessage(f"Cache erfolgreich befüllt: {self.data_cache}", "SetupTool", Qgis.Info)
            except Exception as e:
                QgsMessageLog.logMessage(f"Fehler bei Cache-Befüllung: {e}", "SetupTool", Qgis.Critical)
                self.iface.messageBar().pushMessage("Fehler", f"Cache-Befüllung fehlgeschlagen: {e}", level=Qgis.Critical)
                self.is_connected = False
                self.conn = None
                self.ui.pushButton_Verbindung.setStyleSheet("background-color: #FF6347;")
                return
            # Befülle UI-Elemente aus Cache
            self.populate_firma()
            self.populate_codierung_leerrohr()
            self.populate_auftraggeber()
            self.populate_codierung_buendel()
            self.populate_codierung_faser()
            self.populate_eigner()
            self.load_configurations()
            self.update_leerrohr_subtyp()
            self.update_setup_label()
            self.update_qgis_project_label()
        except Exception as e:
            self.is_connected = False
            self.conn = None
            self.ui.pushButton_Verbindung.setStyleSheet("background-color: #FF6347;")
            QgsMessageLog.logMessage(f"Verbindungsfehler zu {umgebung}: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Verbindung fehlgeschlagen: {e}", level=Qgis.Critical)

    def populate_firma(self, username=None, password=None, umgebung=None):
        if not self.is_connected:
            self.ui.listWidget_Firma.clear()
            return
        try:
            if "firma" not in self.data_cache:
                raise KeyError("firma")
            self.ui.listWidget_Firma.clear()
            self.ui.listWidget_Firma.addItems(self.data_cache["firma"])
            self.ui.listWidget_Firma.setSelectionMode(QListWidget.MultiSelection)
            QgsMessageLog.logMessage(f"Loaded firms from cache: {self.data_cache['firma']}", "SetupTool", Qgis.Info)
        except KeyError as e:
            QgsMessageLog.logMessage(f"Cache-Schlüssel fehlt: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Firmen aus Cache fehlgeschlagen: Schlüssel {e} nicht gefunden", level=Qgis.Critical)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Firmen aus Cache: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Firmen fehlgeschlagen: {e}", level=Qgis.Critical)

    def populate_codierung_leerrohr(self, username=None, password=None, umgebung=None):
        if not self.is_connected:
            self.ui.listWidget_Leerohr.clear()
            return
        try:
            if "codierung_leerrohr" not in self.data_cache:
                raise KeyError("codierung_leerrohr")
            self.ui.listWidget_Leerohr.clear()
            codierungen = [f"{row[0]} - {row[1]} (ID: {row[2]})" for row in self.data_cache["codierung_leerrohr"]]
            self.ui.listWidget_Leerohr.addItems(codierungen)
            self.ui.listWidget_Leerohr.setSelectionMode(QListWidget.MultiSelection)
            QgsMessageLog.logMessage(f"Loaded leerohr codings from cache: {codierungen}", "SetupTool", Qgis.Info)
        except KeyError as e:
            QgsMessageLog.logMessage(f"Cache-Schlüssel fehlt: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Leerrohr-Codierungen aus Cache fehlgeschlagen: Schlüssel {e} nicht gefunden", level=Qgis.Critical)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Leerrohr-Codierungen aus Cache: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Leerrohr-Codierungen fehlgeschlagen: {e}", level=Qgis.Critical)

    def populate_codierung_buendel(self, username=None, password=None, umgebung=None):
        if not self.is_connected:
            self.ui.listWidget_Buendel.clear()
            return
        try:
            if "codierung_buendel" not in self.data_cache:
                raise KeyError("codierung_buendel")
            self.ui.listWidget_Buendel.clear()
            codierungen = [f"{row[0]} - {row[1]} (ID: {row[2]})" for row in self.data_cache["codierung_buendel"]]
            self.ui.listWidget_Buendel.addItems(codierungen)
            self.ui.listWidget_Buendel.setSelectionMode(QListWidget.MultiSelection)
            QgsMessageLog.logMessage(f"Loaded buendel codings from cache: {codierungen}", "SetupTool", Qgis.Info)
        except KeyError as e:
            QgsMessageLog.logMessage(f"Cache-Schlüssel fehlt: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Bündel-Codierungen aus Cache fehlgeschlagen: Schlüssel {e} nicht gefunden", level=Qgis.Critical)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Bündel-Codierungen aus Cache: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Bündel-Codierungen fehlgeschlagen: {e}", level=Qgis.Critical)

    def populate_codierung_faser(self, username=None, password=None, umgebung=None):
        if not self.is_connected:
            self.ui.listWidget_Faser.clear()
            return
        try:
            if "codierung_faser" not in self.data_cache:
                raise KeyError("codierung_faser")
            self.ui.listWidget_Faser.clear()
            codierungen = [f"{row[0]} - {row[1]} (ID: {row[2]})" for row in self.data_cache["codierung_faser"]]
            self.ui.listWidget_Faser.addItems(codierungen)
            self.ui.listWidget_Faser.setSelectionMode(QListWidget.MultiSelection)
            QgsMessageLog.logMessage(f"Loaded faser codings from cache: {codierungen}", "SetupTool", Qgis.Info)
        except KeyError as e:
            QgsMessageLog.logMessage(f"Cache-Schlüssel fehlt: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Faser-Codierungen aus Cache fehlgeschlagen: Schlüssel {e} nicht gefunden", level=Qgis.Critical)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Faser-Codierungen aus Cache: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Faser-Codierungen fehlgeschlagen: {e}", level=Qgis.Critical)

    def populate_auftraggeber(self, username=None, password=None, umgebung=None):
        if not self.is_connected:
            self.ui.comboBox_Auftraggeber.clear()
            return
        try:
            if "auftraggeber" not in self.data_cache:
                raise KeyError("auftraggeber")
            self.ui.comboBox_Auftraggeber.clear()
            self.ui.comboBox_Auftraggeber.addItems(self.data_cache["auftraggeber"])
            self.update_auftraggeber_label(self.ui.comboBox_Auftraggeber.currentText())
            QgsMessageLog.logMessage(f"Loaded auftraggeber from cache: {self.data_cache['auftraggeber']}", "SetupTool", Qgis.Info)
        except KeyError as e:
            QgsMessageLog.logMessage(f"Cache-Schlüssel fehlt: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Auftraggeber aus Cache fehlgeschlagen: Schlüssel {e} nicht gefunden", level=Qgis.Critical)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Auftraggeber aus Cache: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Auftraggeber fehlgeschlagen: {e}", level=Qgis.Critical)

    def populate_eigner(self):
        if not self.is_connected:
            self.ui.listWidget_Eigner.clear()
            return
        try:
            if "eigner" not in self.data_cache:
                raise KeyError("eigner")
            self.ui.listWidget_Eigner.clear()
            self.ui.listWidget_Eigner.addItems(self.data_cache["eigner"])
            self.ui.listWidget_Eigner.setSelectionMode(QListWidget.MultiSelection)
            QgsMessageLog.logMessage(f"Loaded eigner from cache: {self.data_cache['eigner']}", "SetupTool", Qgis.Info)
        except KeyError as e:
            QgsMessageLog.logMessage(f"Cache-Schlüssel fehlt: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Eigner aus Cache fehlgeschlagen: Schlüssel {e} nicht gefunden", level=Qgis.Critical)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Eigner aus Cache: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Eigner fehlgeschlagen: {e}", level=Qgis.Critical)

    def populate_umgebung(self):
        self.ui.comboBox_Umgebung.addItems(["Testumgebung", "Produktivumgebung"])
        self.ui.comboBox_Umgebung.setCurrentIndex(-1)  # Keine Standardauswahl
        self.update_umgebung_color()
        self.ui.comboBox_Umgebung.currentTextChanged.connect(self.update_umgebung_color)

    def update_umgebung_color(self):
        current_text = self.ui.comboBox_Umgebung.currentText()
        style_sheet = """
        QComboBox QAbstractItemView::item {
            padding: 2px;
        }
        """
        if current_text == "Testumgebung":
            style_sheet += "QComboBox { background-color: #90EE90; }"
        elif current_text == "Produktivumgebung":
            style_sheet += "QComboBox { background-color: #FF6347; }"
        else:
            style_sheet += "QComboBox { background-color: gray; }"  # Neutral für keine Auswahl
        self.ui.comboBox_Umgebung.setStyleSheet(style_sheet)

    def make_path_relative_to_onedrive(self, full_path):
        onedrive_root = os.environ.get('OneDrive')
        if not onedrive_root:
            QgsMessageLog.logMessage("Warnung: OneDrive-Umgebungsvariable nicht gefunden. Speichere absoluten Pfad.", "SetupTool", Qgis.Warning)
            return full_path  # Fallback auf absoluten Pfad
        onedrive_root = os.path.normpath(onedrive_root)
        full_path = os.path.normpath(full_path)
        if full_path.startswith(onedrive_root):
            relative_path = full_path[len(onedrive_root):].lstrip('\\').lstrip('/')
            backslash = '\\'
            return f"OneDrive:{backslash}{relative_path.replace('/', backslash)}"  # Platzhalter "OneDrive:\"
        else:
            return full_path  # Nicht in OneDrive, absoluten Pfad behalten

    def resolve_onedrive_path(self, stored_path):
        if stored_path.startswith("OneDrive:\\"):
            onedrive_root = os.environ.get('OneDrive')
            if not onedrive_root:
                QgsMessageLog.logMessage("Fehler: OneDrive-Umgebungsvariable nicht gefunden. Kann Pfad nicht auflösen.", "SetupTool", Qgis.Critical)
                return stored_path  # Fallback, könnte fehlschlagen
            relative_part = stored_path[len("OneDrive:\\"):]
            return os.path.normpath(os.path.join(onedrive_root, relative_part))
        else:
            return stored_path  # Absoluter Pfad, unverändert

    def save_settings(self):
        """
        Speichert die ausgewählten Einstellungen in der Datenbank und in QSettings, damit andere Tools (z. B. Leerrohr-Tool) sie verwenden können.
        """
        if not self.is_connected:
            self.iface.messageBar().pushMessage("Fehler", "Bitte stellen Sie eine Verbindung her, um Einstellungen zu speichern.", level=Qgis.Critical)
            return
        if not self.validate_inputs():
            return
        username = self.ui.label_Kommentar_3.text()
        password = self.ui.label_Kommentar_5.text()
        umgebung = self.ui.comboBox_Umgebung.currentText()
        db_params = self.get_database_connection(username, password, umgebung)
        try:
            # Hole ausgewählte Firmen
            firma = ", ".join([item.text() for item in self.ui.listWidget_Firma.selectedItems()])
            print(f"DEBUG: Gespeicherte Firma: {firma}")
            # Hole IDs der ausgewählten Leerrohr-Codierungen
            selected_leerrohr_ids = []
            for item in self.ui.listWidget_Leerohr.selectedItems():
                text = item.text()
                id_start = text.find("(ID: ") + 5
                id_end = text.find(")", id_start)
                if id_start > 4 and id_end > id_start:
                    selected_leerrohr_ids.append(int(text[id_start:id_end]))
            print(f"DEBUG: Ausgewählte Leerrohr-Codierungen IDs: {selected_leerrohr_ids}")
            # Hole IDs der ausgewählten Bündel-Codierungen
            selected_buendel_ids = []
            for item in self.ui.listWidget_Buendel.selectedItems():
                text = item.text()
                id_start = text.find("(ID: ") + 5
                id_end = text.find(")", id_start)
                if id_start > 4 and id_end > id_start:
                    selected_buendel_ids.append(int(text[id_start:id_end]))
            print(f"DEBUG: Ausgewählte Bündel-Codierungen IDs: {selected_buendel_ids}")
            # Hole IDs der ausgewählten Faser-Codierungen
            selected_faser_ids = []
            for item in self.ui.listWidget_Faser.selectedItems():
                text = item.text()
                id_start = text.find("(ID: ") + 5
                id_end = text.find(")", id_start)
                if id_start > 4 and id_end > id_start:
                    selected_faser_ids.append(int(text[id_start:id_end]))
            print(f"DEBUG: Ausgewählte Faser-Codierungen IDs: {selected_faser_ids}")
            # Hole Auftraggeber-ID
            auftraggeber = self.ui.comboBox_Auftraggeber.currentText()
            id_auftraggeber = None
            if auftraggeber:
                cur = self.conn.cursor()
                cur.execute("SELECT id FROM \"Verwaltung_Intern\".\"Auftraggeber\" WHERE \"BEZEICHNUNG\" = %s", (auftraggeber,))
                result = cur.fetchone()
                id_auftraggeber = result[0] if result else None
                cur.close()
            print(f"DEBUG: Auftraggeber ID: {id_auftraggeber}")
            # Hole ausgewählte Eigner
            eigner = ", ".join([item.text() for item in self.ui.listWidget_Eigner.selectedItems()])
            print(f"DEBUG: Gespeicherte Eigner: {eigner}")
            # Hole Setup-Name
            name = self.ui.lineEdit_Name.text().strip()
            if not name:
                self.iface.messageBar().pushMessage("Fehler", "Bitte einen Namen für das Setup eingeben.", level=Qgis.Critical)
                return
            print(f"DEBUG: Setup-Name: {name}")
            # Konvertiere DB-Verbindung in JSON-String
            db_connection_str = json.dumps(db_params)
            print(f"DEBUG: DB-Verbindung als JSON: {db_connection_str}")
            # Hole ausgewählte Leerrohr-Subtypen-IDs
            selected_subtyp_ids = []
            for item in self.ui.listWidget_Leerohr_SubTyp.selectedItems():
                try:
                    subtyp_id = int(item.text().split(" - ")[0])
                    selected_subtyp_ids.append(subtyp_id)
                except ValueError:
                    print(f"DEBUG: Ungültiger Subtyp-Text: {item.text()}")
                    continue
            print(f"DEBUG: Ausgewählte Subtyp-IDs: {selected_subtyp_ids}")
            if not selected_subtyp_ids:
                self.iface.messageBar().pushMessage("Warnung", "Keine Subtypen ausgewählt!", level=Qgis.Warning)
            # Hole vollständige Subtyp-Daten aus dem Cache
            leerrohr_subtyp_data = [subtyp for subtyp in self.data_cache.get("leerrohr_subtyp", []) if subtyp[0] in selected_subtyp_ids]
            print(f"DEBUG: Gespeicherte Subtyp-Data: {leerrohr_subtyp_data}")
            # Hole QGIS-Projekt-Pfad
            qgis_project_path = self.ui.mQgsFileWidget.filePath()
            qgis_project_path = self.make_path_relative_to_onedrive(qgis_project_path)
            print(f"DEBUG: QGIS-Projekt-Pfad: {qgis_project_path}")
            # Speichere in der Datenbank
            cur = self.conn.cursor()
            if self.current_setup_id and name == self.settings.value("name", ""):
                # Aktualisiere ein bestehendes Setup
                cur.execute("""
                UPDATE "Verwaltung_Intern"."setup_toolbox"
                SET "name" = %s, "firma" = %s, "codierung_leerrohr" = %s, "codierung_buendel" = %s,
                    "codierung_faser" = %s, "id_auftraggeber" = %s, "eigner" = %s, "db_connection" = %s,
                    "leerrohr_subtyp" = %s, "qgis_proj" = %s
                WHERE id = %s
                """, (name, firma, selected_leerrohr_ids, selected_buendel_ids, selected_faser_ids, id_auftraggeber, eigner, db_connection_str, selected_subtyp_ids, qgis_project_path, self.current_setup_id))
                self.conn.commit()
                print(f"DEBUG: Setup '{name}' aktualisiert mit Subtyp-IDs: {selected_subtyp_ids}")
            else:
                # Erstelle ein neues Setup
                cur.execute("""
                INSERT INTO "Verwaltung_Intern"."setup_toolbox" ("name", "firma", "codierung_leerrohr", "codierung_buendel", "codierung_faser", "id_auftraggeber", "eigner", "db_connection", "leerrohr_subtyp", "qgis_proj")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """, (name, firma, selected_leerrohr_ids, selected_buendel_ids, selected_faser_ids, id_auftraggeber, eigner, db_connection_str, selected_subtyp_ids, qgis_project_path))
                self.current_setup_id = cur.fetchone()[0]
                self.conn.commit()
                print(f"DEBUG: Neues Setup '{name}' mit ID {self.current_setup_id} und Subtyp-IDs {selected_subtyp_ids} gespeichert")
            # Speichere in QSettings
            self.settings.setValue("firma", firma)
            self.settings.setValue("codierung_leerrohr", ", ".join(map(str, selected_leerrohr_ids)))
            self.settings.setValue("codierung_buendel", ", ".join(map(str, selected_buendel_ids)))
            self.settings.setValue("codierung_faser", ", ".join(map(str, selected_faser_ids)))
            self.settings.setValue("auftraggeber", auftraggeber)
            self.settings.setValue("eigner", eigner)
            self.settings.setValue("name", name)
            self.settings.setValue("username", username)
            self.settings.setValue("password", base64.b64encode(password.encode()).decode())
            self.settings.setValue("umgebung", umgebung)
            self.settings.setValue("db_connection", db_connection_str)
            self.settings.setValue("leerrohr_subtyp", selected_subtyp_ids)
            self.settings.setValue("leerrohr_subtyp_data", leerrohr_subtyp_data)
            print(f"DEBUG: QSettings aktualisiert mit leerrohr_subtyp: {selected_subtyp_ids}, leerrohr_subtyp_data: {leerrohr_subtyp_data}")
            # Aktualisiere active_setup im Haupt-Plugin
            self.iface.plugin.active_setup = {
                "name": name,
                "umgebung": umgebung,
                "firma": firma.split(", ") if firma else [],
                "codierung_leerrohr": [str(id) for id in selected_leerrohr_ids],
                "codierung_buendel": [str(id) for id in selected_buendel_ids],
                "codierung_faser": [str(id) for id in selected_faser_ids],
                "eigner": eigner.split(", ") if eigner else [],
                "auftraggeber": auftraggeber,
                "leerrohr_subtyp": selected_subtyp_ids,
                "qgis_project_path": qgis_project_path,
                "db_connection": db_params,  # Dictionary für active_setup
                "leerrohr_subtyp_data": leerrohr_subtyp_data
            }
            print(f"DEBUG: Updated active_setup in main.py: {self.iface.plugin.active_setup}")
            QgsMessageLog.logMessage(f"Updated active_setup in main.py: {self.iface.plugin.active_setup}", "SetupTool", Qgis.Info)
            # Aktualisiere die Konfigurationsliste und die Anzeige
            self.iface.plugin.update_setup_label()
            self.load_configurations()
            self.iface.messageBar().pushMessage("Erfolg", "Einstellungen gespeichert!", level=Qgis.Success)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Speichern: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Speichern fehlgeschlagen: {e}", level=Qgis.Critical)
        finally:
            if 'cur' in locals():
                cur.close()

    def delete_configuration(self):
        if not self.is_connected:
            self.iface.messageBar().pushMessage("Fehler", "Bitte stellen Sie eine Verbindung her, um Konfigurationen zu löschen.", level=Qgis.Critical)
            return
        selected_row = self.ui.tableView.currentIndex().row()
        if selected_row >= 0:
            reply = QMessageBox.question(
                self,
                "Setup löschen",
                "Sind Sie sicher, dass Sie das gewählte Setup löschen wollen?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                self.iface.messageBar().pushMessage("Info", "Löschen abgebrochen.", level=Qgis.Info)
                return
            username = self.ui.label_Kommentar_3.text()
            password = self.ui.label_Kommentar_5.text()
            umgebung = self.ui.comboBox_Umgebung.currentText()
            db_params = self.get_database_connection(username, password, umgebung)
            try:
                conn = psycopg2.connect(**db_params)
                cur = conn.cursor()
                delete_id = self.ui.tableView.model().data(self.ui.tableView.model().index(selected_row, 0), Qt.DisplayRole)
                cur.execute("DELETE FROM \"Verwaltung_Intern\".\"setup_toolbox\" WHERE id = %s", (delete_id,))
                conn.commit()
                if self.current_setup_id == delete_id:
                    self.current_setup_id = None
                    self.current_qgis_project_path = ""
                self.iface.messageBar().pushMessage("Erfolg", "Eintrag gelöscht!", level=Qgis.Success)
                self.load_configurations()
                self.update_setup_label()
                self.update_qgis_project_label()
            except Exception as e:
                QgsMessageLog.logMessage(f"Fehler beim Löschen: {e}", "SetupTool", level=Qgis.Critical)
                self.iface.messageBar().pushMessage("Fehler", f"Löschen fehlgeschlagen: {e}", level=Qgis.Critical)
            finally:
                if 'conn' in locals():
                    cur.close()
                    conn.close()

    def load_configurations(self):
        if not self.is_connected or not self.conn:
            self.ui.tableView.setModel(None)
            self.iface.messageBar().pushMessage("Info", "Bitte stellen Sie eine Verbindung her, um Konfigurationen zu laden.", level=Qgis.Info)
            return
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT id, name, id_auftraggeber, firma, codierung_leerrohr, codierung_buendel, codierung_faser, eigner, leerrohr_subtyp, qgis_proj FROM \"Verwaltung_Intern\".\"setup_toolbox\"")
            data = cur.fetchall()
            headers = ["ID", "Name", "Auftraggeber", "Firma", "Leerrohr-C", "Bündel-C", "Faser-C", "Eigner", "Leerohr-Subtyp"]
            model = SetupTableModel(data, headers, self)
            self.ui.tableView.setModel(model)
            self.ui.tableView.resizeColumnsToContents()
            if self.ui.tableView is not None and self.ui.tableView.selectionModel() is not None:
                self.ui.tableView.selectionModel().selectionChanged.connect(self.update_table_selection)
            cur.close()
            QgsMessageLog.logMessage(f"Loaded configurations: {len(data)} entries", "SetupTool", Qgis.Info)
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Laden der Konfigurationen: {e}", "SetupTool", Qgis.Critical)
            self.ui.tableView.setModel(None)
            self.iface.messageBar().pushMessage("Fehler", f"Laden der Konfigurationen fehlgeschlagen: {e}", level=Qgis.Critical)

    def apply_configuration(self):
        """
        Wendet eine ausgewählte Konfiguration an und setzt sie global in QSettings und active_setup.
        Lädt die Subtypen aus dem Cache und öffnet das gespeicherte QGIS-Projekt.
        """
        # Prüfe, ob eine Datenbankverbindung besteht
        if not self.is_connected:
            self.iface.messageBar().pushMessage("Fehler", "Bitte stellen Sie eine Verbindung her, um eine Konfiguration anzuwenden.", level=Qgis.Critical)
            return
        # Hole die ID der ausgewählten Konfiguration
        selected_row = self.ui.tableView.currentIndex().row()
        if selected_row >= 0:
            setup_id = self.ui.tableView.model().data(self.ui.tableView.model().index(selected_row, 0), Qt.DisplayRole)
        else:
            setup_id = self.current_setup_id
        if not setup_id:
            self.iface.messageBar().pushMessage("Fehler", "Bitte wählen Sie eine Konfiguration aus.", level=Qgis.Critical)
            return
        try:
            cur = self.conn.cursor()
            # Lade die Konfiguration aus der Datenbank
            cur.execute("SELECT firma, codierung_leerrohr, codierung_buendel, codierung_faser, id_auftraggeber, eigner, name, db_connection, leerrohr_subtyp, qgis_proj FROM \"Verwaltung_Intern\".\"setup_toolbox\" WHERE id = %s", (setup_id,))
            config = cur.fetchone()
            if config:
                # Extrahiere die Daten aus der Abfrage
                firma = config[0].split(", ") if config[0] else []
                codierung_leerrohr = [str(id) for id in config[1]] if config[1] else []
                codierung_buendel = [str(id) for id in config[2]] if config[2] else []
                codierung_faser = [str(id) for id in config[3]] if config[3] else []
                id_auftraggeber = config[4]
                eigner = config[5].split(", ") if config[5] else []
                name = config[6]
                db_connection = config[7]  # Direkt als Dict aus jsonb
                leerrohr_subtyp = config[8] if config[8] else []
                qgis_project_path = self.resolve_onedrive_path(config[9])
                # Hole Auftraggeber-Bezeichnung
                cur.execute("SELECT \"BEZEICHNUNG\" FROM \"Verwaltung_Intern\".\"Auftraggeber\" WHERE id = %s", (id_auftraggeber,))
                result = cur.fetchone()
                auftraggeber = result[0] if result else ""
                print(f"DEBUG: Auftraggeber: {auftraggeber}")
                # Hole vollständige Subtyp-Daten aus dem Cache für die ausgewählten IDs
                leerrohr_subtyp_data = [subtyp for subtyp in self.data_cache.get("leerrohr_subtyp", []) if subtyp[0] in leerrohr_subtyp]
                print(f"DEBUG: Gefilterte leerrohr_subtyp_data: {leerrohr_subtyp_data}")
                # Konvertiere db_connection zu einem String für QSettings
                db_connection_str = json.dumps(db_connection) if isinstance(db_connection, dict) else db_connection
                print(f"DEBUG: db_connection als String: {db_connection_str}")
                # Speichere in QSettings
                self.settings.setValue("name", name)
                self.settings.setValue("firma", ", ".join(firma))
                self.settings.setValue("codierung_leerrohr", ", ".join(codierung_leerrohr))
                self.settings.setValue("codierung_buendel", ", ".join(codierung_buendel))
                self.settings.setValue("codierung_faser", ", ".join(codierung_faser))
                self.settings.setValue("auftraggeber", auftraggeber)
                self.settings.setValue("eigner", ", ".join(eigner))
                self.settings.setValue("leerrohr_subtyp", leerrohr_subtyp)
                self.settings.setValue("qgis_project_path", qgis_project_path)
                self.settings.setValue("db_connection", db_connection_str)  # Speichere als String
                self.settings.setValue("leerrohr_subtyp_data", leerrohr_subtyp_data)
                print(f"DEBUG: QSettings gespeichert mit leerrohr_subtyp: {leerrohr_subtyp}")
                # Aktualisiere active_setup im Haupt-Plugin
                self.iface.plugin.active_setup = {
                    "name": name,
                    "umgebung": self.settings.value("umgebung", ""),
                    "firma": firma,
                    "codierung_leerrohr": codierung_leerrohr,
                    "codierung_buendel": codierung_buendel,
                    "codierung_faser": codierung_faser,
                    "eigner": eigner,
                    "auftraggeber": auftraggeber,
                    "leerrohr_subtyp": leerrohr_subtyp,
                    "qgis_project_path": qgis_project_path,
                    "db_connection": db_connection,  # Dict für active_setup
                    "leerrohr_subtyp_data": leerrohr_subtyp_data
                }
                print(f"DEBUG: Updated active_setup: {self.iface.plugin.active_setup}")
                # UI aktualisieren (Listen und Eingabefelder)
                self.ui.lineEdit_Name.setText(name)
                self.ui.mQgsFileWidget.setFilePath(qgis_project_path)
                self.ui.comboBox_Auftraggeber.setCurrentText(auftraggeber)
                for item in self.ui.listWidget_Firma.findItems("", Qt.MatchContains):
                    item.setSelected(item.text() in firma)
                for item in self.ui.listWidget_Leerohr.findItems("", Qt.MatchContains):
                    text = item.text()
                    id_start = text.find("(ID: ") + 5
                    id_end = text.find(")", id_start)
                    if id_start > 4 and id_end > id_start:
                        id_value = text[id_start:id_end]
                        item.setSelected(id_value in codierung_leerrohr)
                for item in self.ui.listWidget_Buendel.findItems("", Qt.MatchContains):
                    text = item.text()
                    id_start = text.find("(ID: ") + 5
                    id_end = text.find(")", id_start)
                    if id_start > 4 and id_end > id_start:
                        id_value = text[id_start:id_end]
                        item.setSelected(id_value in codierung_buendel)
                for item in self.ui.listWidget_Faser.findItems("", Qt.MatchContains):
                    text = item.text()
                    id_start = text.find("(ID: ") + 5
                    id_end = text.find(")", id_start)
                    if id_start > 4 and id_end > id_start:
                        id_value = text[id_start:id_end]
                        item.setSelected(id_value in codierung_faser)
                for item in self.ui.listWidget_Eigner.findItems("", Qt.MatchContains):
                    item.setSelected(item.text() in eigner)
                for item in self.ui.listWidget_Leerohr_SubTyp.findItems("", Qt.MatchContains):
                    try:
                        subtyp_id = int(item.text().split(" - ")[0])
                        item.setSelected(subtyp_id in leerrohr_subtyp)
                    except ValueError:
                        pass
                # Lade das gespeicherte QGIS-Projekt, wenn ein Pfad vorhanden ist
                if qgis_project_path and os.path.exists(qgis_project_path):
                    current_project_path = QgsProject.instance().fileName()
                    if current_project_path and os.path.normpath(current_project_path) == os.path.normpath(qgis_project_path):
                        self.iface.messageBar().pushMessage("Info", f"QGIS-Projekt '{os.path.basename(qgis_project_path)}' ist bereits geladen.", level=Qgis.Info)
                    else:
                        reply = QMessageBox.question(
                            self,
                            "QGIS-Projekt laden",
                            f"Haben Sie Ihr aktuelles Projekt gespeichert? Soll das aktuelle Projekt durch '{os.path.basename(qgis_project_path)}' ersetzt werden?",
                            QMessageBox.Yes | QMessageBox.No,
                            QMessageBox.No
                        )
                        if reply == QMessageBox.Yes:
                            try:
                                QgsProject.instance().read(qgis_project_path)
                                self.iface.messageBar().pushMessage("Erfolg", f"QGIS-Projekt '{os.path.basename(qgis_project_path)}' geladen!", level=Qgis.Success)
                            except Exception as e:
                                QgsMessageLog.logMessage(f"Fehler beim Laden des QGIS-Projekts: {e}", "SetupTool", Qgis.Critical)
                                self.iface.messageBar().pushMessage("Fehler", f"Laden des QGIS-Projekts fehlgeschlagen: {e}", level=Qgis.Critical)
                self.iface.plugin.update_setup_label()
                self.update_qgis_project_label()
                self.iface.messageBar().pushMessage("Erfolg", f"Konfiguration '{name}' angewendet!", level=Qgis.Success)
                QgsMessageLog.logMessage(f"Applied configuration: {name} (ID: {setup_id})", "SetupTool", Qgis.Info)
            else:
                self.iface.messageBar().pushMessage("Fehler", "Konfiguration nicht gefunden.", level=Qgis.Critical)
            cur.close()
        except Exception as e:
            QgsMessageLog.logMessage(f"Fehler beim Anwenden: {e}", "SetupTool", Qgis.Critical)
            self.iface.messageBar().pushMessage("Fehler", f"Anwenden fehlgeschlagen: {e}", level=Qgis.Critical)

    def open_configuration(self):
        if not self.is_connected:
            self.iface.messageBar().pushMessage("Fehler", "Bitte stellen Sie eine Verbindung her, um eine Konfiguration zu öffnen.", level=Qgis.Critical)
            return
        selected_row = self.ui.tableView.currentIndex().row()
        if selected_row >= 0:
            username = self.ui.label_Kommentar_3.text()
            password = self.ui.label_Kommentar_5.text()
            umgebung = self.ui.comboBox_Umgebung.currentText()
            db_params = self.get_database_connection(username, password, umgebung)
            try:
                conn = psycopg2.connect(**db_params)
                cur = conn.cursor()
                cur.execute("SELECT firma, codierung_leerrohr, codierung_buendel, codierung_faser, id_auftraggeber, eigner, name, db_connection, leerrohr_subtyp, id, qgis_proj FROM \"Verwaltung_Intern\".\"setup_toolbox\" WHERE id = %s", (self.ui.tableView.model().data(self.ui.tableView.model().index(selected_row, 0), Qt.DisplayRole),))
                config = cur.fetchone()
                if config:
                    firma = config[0].split(", ") if config[0] else []
                    codierung_leerrohr = [str(id) for id in config[1]] if config[1] else []
                    codierung_buendel = [str(id) for id in config[2]] if config[2] else []
                    codierung_faser = [str(id) for id in config[3]] if config[3] else []
                    id_auftraggeber = config[4]
                    eigner = config[5].split(", ") if config[5] else []
                    name = config[6]
                    db_connection = config[7]
                    leerrohr_subtyp = config[8] if config[8] else []
                    self.current_setup_id = config[9]
                    self.current_qgis_project_path = self.resolve_onedrive_path(config[10])
                    cur.execute("SELECT \"BEZEICHNUNG\" FROM \"Verwaltung_Intern\".\"Auftraggeber\" WHERE id = %s", (id_auftraggeber,))
                    auftraggeber_result = cur.fetchone()
                    auftraggeber = auftraggeber_result[0] if auftraggeber_result else ""
                    self.ui.listWidget_Firma.clear()
                    self.populate_firma(username, password, umgebung)
                    for item in self.ui.listWidget_Firma.findItems("", Qt.MatchContains):
                        if item.text() in firma:
                            item.setSelected(True)
                    self.ui.listWidget_Leerohr.clear()
                    cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Leerrohr%' ORDER BY \"BEMERKUNG\"")
                    leerohr_options = [f"{row[0]} - {row[1]} (ID: {row[2]})" for row in cur.fetchall() if row[1]]
                    self.ui.listWidget_Leerohr.addItems(leerohr_options)
                    for item in self.ui.listWidget_Leerohr.findItems("", Qt.MatchContains):
                        text = item.text()
                        id_start = text.find("(ID: ") + 5
                        id_end = text.find(")", id_start)
                        if id_start > 4 and id_end > id_start:
                            id_value = int(text[id_start:id_end])
                            if str(id_value) in codierung_leerrohr:
                                item.setSelected(True)
                    self.ui.listWidget_Buendel.clear()
                    cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Bündel%' ORDER BY \"BEMERKUNG\"")
                    buendel_options = [f"{row[0]} - {row[1]} (ID: {row[2]})" for row in cur.fetchall() if row[1]]
                    self.ui.listWidget_Buendel.addItems(buendel_options)
                    for item in self.ui.listWidget_Buendel.findItems("", Qt.MatchContains):
                        text = item.text()
                        id_start = text.find("(ID: ") + 5
                        id_end = text.find(")", id_start)
                        if id_start > 4 and id_end > id_start:
                            id_value = int(text[id_start:id_end])
                            if str(id_value) in codierung_buendel:
                                item.setSelected(True)
                    self.ui.listWidget_Faser.clear()
                    cur.execute("SELECT \"CODIERUNG\", \"BEMERKUNG\", \"id\" FROM lwl.\"LUT_Codierung\" WHERE \"ELEMENT\" LIKE '%Faser%' ORDER BY \"BEMERKUNG\"")
                    faser_options = [f"{row[0]} - {row[1]} (ID: {row[2]})" for row in cur.fetchall() if row[1]]
                    self.ui.listWidget_Faser.addItems(faser_options)
                    for item in self.ui.listWidget_Faser.findItems("", Qt.MatchContains):
                        text = item.text()
                        id_start = text.find("(ID: ") + 5
                        id_end = text.find(")", id_start)
                        if id_start > 4 and id_end > id_start:
                            id_value = int(text[id_start:id_end])
                            if str(id_value) in codierung_faser:
                                item.setSelected(True)
                    self.ui.listWidget_Eigner.clear()
                    self.populate_eigner()
                    for item in self.ui.listWidget_Eigner.findItems("", Qt.MatchContains):
                        if item.text() in eigner:
                            item.setSelected(True)
                    self.ui.comboBox_Auftraggeber.setCurrentText(auftraggeber)
                    self.update_auftraggeber_label(auftraggeber)
                    if hasattr(self.ui, 'lineEdit_Name'):
                        self.ui.lineEdit_Name.setText(name)
                    try:
                        self.ui.mQgsFileWidget.setFilePath(self.current_qgis_project_path)
                    except AttributeError as e:
                        QgsMessageLog.logMessage(f"Fehler beim Setzen von mQgsFileWidget: {e}", "SetupTool", Qgis.Critical)
                    self.update_leerrohr_subtyp()
                    for item in self.ui.listWidget_Leerohr_SubTyp.findItems("", Qt.MatchContains):
                        try:
                            subtyp_id = int(item.text().split(" - ")[0])
                            if subtyp_id in leerrohr_subtyp:
                                item.setSelected(True)
                        except ValueError:
                            continue
                    self.update_qgis_project_label()
                    self.iface.messageBar().pushMessage("Erfolg", "Konfiguration geladen!", level=Qgis.Success)
                else:
                    self.iface.messageBar().pushMessage("Fehler", "Keine Konfiguration gefunden.", level=Qgis.Critical)
                conn.close()
            except Exception as e:
                QgsMessageLog.logMessage(f"Fehler beim Öffnen: {e}", "SetupTool", level=Qgis.Critical)
                self.iface.messageBar().pushMessage("Fehler", f"Öffnen fehlgeschlagen: {e}", level=Qgis.Critical)

    def disconnect_connection(self):
        self.is_connected = False
        self.current_setup_id = None
        self.current_qgis_project_path = ""
        self.data_cache = {}  # Cache leeren
        self.ui.pushButton_Verbindung.setStyleSheet("background-color: gray;")
        self.ui.listWidget_Leerohr.clear()
        self.ui.listWidget_Buendel.clear()
        self.ui.listWidget_Faser.clear()
        self.ui.listWidget_Firma.clear()
        self.ui.listWidget_Eigner.clear()
        self.ui.listWidget_Leerohr_SubTyp.clear()
        self.ui.comboBox_Auftraggeber.clear()
        self.ui.tableView.setModel(None)
        self.ui.label_Kommentar_3.clear()
        self.ui.label_Kommentar_5.clear()
        try:
            self.ui.mQgsFileWidget.setFilePath("")
        except AttributeError as e:
            QgsMessageLog.logMessage(f"Fehler beim Zurücksetzen von mQgsFileWidget: {e}", "SetupTool", Qgis.Critical)
        # Entferne Verbindungseinstellungen aus QSettings
        self.settings.remove("connection_username")
        self.settings.remove("connection_password")
        self.settings.remove("connection_umgebung")
        self.settings.remove("firma")
        self.settings.remove("codierung_leerrohr")
        self.settings.remove("codierung_buendel")
        self.settings.remove("codierung_faser")
        self.settings.remove("auftraggeber")
        self.settings.remove("eigner")
        self.settings.remove("name")
        self.settings.remove("leerrohr_subtyp")
        self.settings.remove("qgis_project_path")
        self.settings.remove("db_connection")
        self.settings.remove("leerrohr_subtyp_data")  # Entferne Subtyp-Daten
        if hasattr(self.iface, 'plugin') and hasattr(self.iface.plugin, 'update_setup_label'):
            self.iface.plugin.update_setup_label()
        self.update_setup_label()
        self.update_qgis_project_label()
        self.iface.messageBar().pushMessage("Info", "Verbindung getrennt, Einstellungen und Cache zurückgesetzt!", level=Qgis.Info)

    def reset_settings(self):
        # Verbindungsfelder und -status nicht zurücksetzen
        self.ui.listWidget_Firma.clearSelection()
        self.ui.listWidget_Leerohr.clearSelection()
        self.ui.comboBox_Auftraggeber.setCurrentIndex(-1)
        self.ui.listWidget_Buendel.clearSelection()
        self.ui.listWidget_Faser.clearSelection()
        self.ui.listWidget_Eigner.clearSelection()
        self.ui.listWidget_Leerohr_SubTyp.clearSelection()
        if hasattr(self.ui, 'lineEdit_Name'):
            self.ui.lineEdit_Name.clear()
        try:
            self.ui.mQgsFileWidget.setFilePath("")
            self.current_qgis_project_path = ""
        except AttributeError as e:
            QgsMessageLog.logMessage(f"Fehler beim Zurücksetzen von mQgsFileWidget: {e}", "SetupTool", Qgis.Critical)
        self.update_auftraggeber_label("")
        self.ui.tableView.setModel(None)
        self.current_setup_id = None
        self.update_setup_label()
        self.update_qgis_project_label()
        self.iface.messageBar().pushMessage("Info", "Konfigurationseinstellungen zurückgesetzt!", level=Qgis.Info)

    def get_database_connection(self, username=None, password=None, umgebung=None):
        if umgebung is None:
            umgebung = self.settings.value("connection_umgebung", "Testumgebung")
        if username is None:
            username = self.settings.value("connection_username", "")
        if password is None:
            password = base64.b64decode(self.settings.value("connection_password", "").encode()).decode() if self.settings.value("connection_password", "") else ""
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