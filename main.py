from PyQt5.QtWidgets import QAction, QLabel
from qgis.PyQt.QtGui import QIcon
from qgis.core import Qgis
from PyQt5.QtCore import Qt, QSettings
from . import resources
from .tools.leerrohr_verlegen.leerrohr_verlegen import LeerrohrVerlegenTool
from .tools.hauseinfuehrung_verlegen.hauseinfuehrung_verlegen import HauseinfuehrungsVerlegungsTool
import logging

import sys, sip
sys.path.append(r'C:\Users\marce\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\ToolBox_SiegeleCo')

try:
    from tools.leerrohr_verlegen.leerrohr_verlegen import LeerrohrErfassenTool
    print("LeerrohrErfassenTool erfolgreich importiert")
except ImportError as e:
    print("Fehler beim Import von LeerrohrErfassenTool:", e)

try:
    from .tools.kabel_verlegen.kabel_verlegen import KabelVerlegungsTool
    print("KabelVerlegungsTool erfolgreich importiert")
except ImportError as e:
    print("Fehler beim Import von KabelVerlegungsTool:", e)

class ToolBoxSiegeleCoPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.kabel_tool = None
        self.leerrohr_tool = None
        self.hausanschluss_tool = None
        self.setup_label = None
        self.settings = QSettings("SiegeleCo", "ToolBox")
        self.active_setup = {}  # Dict für aktives Setup
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        self.logger.info("ToolBoxSiegeleCoPlugin initialisiert")

    def initGui(self):
        self.toolbar = self.iface.addToolBar("Toolbox SiegeleCo")
        
        self.setup_label = QLabel("Aktiv: Kein Setup gewählt")
        self.setup_label.setStyleSheet("color: black; padding: 5px; font-weight: bold;")
        self.toolbar.addWidget(self.setup_label)
        
        self.add_toolbar_action("Setup Tool", self.run_setup_tool, ":/plugins/ToolBox_SiegeleCo/icons/setup_Toolbox.png")
        self.add_toolbar_action("Leerrohr Verlegen/Verwalten Tool", self.run_leerrohr_erfassen, ":/plugins/ToolBox_SiegeleCo/icons/icon_leerrohr_verlegen_tool.png")
        self.add_toolbar_action("Leerrohr Verbinden", self.run_leerrohrverbinden_tool, ":/plugins/ToolBox_SiegeleCo/icons/icon_leerohr_verbinden_tool.png")
        self.add_toolbar_action("Hausanschluss Tool", self.run_hausanschluss_verlegen, ":/plugins/ToolBox_SiegeleCo/icons/icon_hausanschluesse.png")
        self.add_toolbar_action("Kabel Verlegen Tool", self.run_kabel_verlegen, ":/plugins/ToolBox_SiegeleCo/icons/icon_kabel_verlegen.png")
        self.add_toolbar_action("Spleiss Tool", self.run_spleisstool, ":/plugins/ToolBox_SiegeleCo/icons/icon_spleiss_tool.png")

    def add_toolbar_action(self, name, function, icon_path):
        icon = QIcon(icon_path)
        if icon.isNull():
            print(f"Fehler: Icon {icon_path} konnte nicht geladen werden")
        else:
            print(f"Icon {icon_path} erfolgreich geladen")
        action = QAction(icon, name, self.iface.mainWindow())
        action.triggered.connect(function)
        self.toolbar.addAction(action)

    def update_setup_label(self):
        setup_name = self.settings.value("name", "Kein Setup aktiv")
        umgebung = self.settings.value("umgebung", None)
        self.setup_label.setText(f"Aktiv: {setup_name}")
        if umgebung == "Produktivumgebung":
            self.setup_label.setStyleSheet("color: red; padding: 5px; font-weight: bold;")
        elif umgebung == "Testumgebung":
            self.setup_label.setStyleSheet("color: green; padding: 5px; font-weight: bold;")
        else:
            self.setup_label.setStyleSheet("color: grey; padding: 5px; font-weight: bold;")
        # Aktualisiere active_setup
        self.active_setup = {
            "name": setup_name,
            "umgebung": umgebung,
            "firma": self.settings.value("firma", "").split(", ") if self.settings.value("firma", "") else [],
            "codierung_leerrohr": self.settings.value("codierung_leerrohr", "").split(", ") if self.settings.value("codierung_leerrohr", "") else [],
            "codierung_buendel": self.settings.value("codierung_buendel", "").split(", ") if self.settings.value("codierung_buendel", "") else [],
            "codierung_faser": self.settings.value("codierung_faser", "").split(", ") if self.settings.value("codierung_faser", "") else [],
            "eigner": self.settings.value("eigner", "").split(", ") if self.settings.value("eigner", "") else [],
            "auftraggeber": self.settings.value("auftraggeber", ""),
            "leerohr_subtyp": [int(x) for x in self.settings.value("leerohr_subtyp", []) if x] if self.settings.value("leerohr_subtyp", []) else [],
            "qgis_project_path": self.settings.value("qgis_project_path", ""),
            "db_connection": self.settings.value("db_connection", "")
        }
        self.logger.info(f"Aktives Setup aktualisiert: {self.active_setup}")

    def run_setup_tool(self):
        from .tools.setup_Toolbox.setup_tool import SetupTool
        setup = SetupTool(self.iface)
        setup.exec_()
        self.update_setup_label()


    def run_leerrohrverbinden_tool(self):
        if not self.settings.value("name"):
            self.iface.messageBar().pushMessage("Fehler", "Bitte wählen Sie zuerst ein Setup im Setup-Tool aus!", level=Qgis.Critical)
            return
        self.iface.messageBar().pushMessage("Leerrohr Verwalten Tool aktiviert", level=Qgis.Info)

    def run_kabel_verlegen(self):
        if not self.settings.value("name"):
            self.iface.messageBar().pushMessage("Fehler", "Bitte wählen Sie zuerst ein Setup im Setup-Tool aus!", level=Qgis.Critical)
            return
        self.iface.messageBar().pushMessage("Kabel Verlegen Tool aktiviert", level=Qgis.Info)
        if not self.kabel_tool:
            self.kabel_tool = KabelVerlegungsTool(self.iface)
        self.kabel_tool.run()

    def run_spleisstool(self):
        if not self.settings.value("name"):
            self.iface.messageBar().pushMessage("Fehler", "Bitte wählen Sie zuerst ein Setup im Setup-Tool aus!", level=Qgis.Critical)
            return
        self.iface.messageBar().pushMessage("Spleiss-Tool aktiviert", level=Qgis.Info)

    def run_leerrohr_erfassen(self):
        if not self.settings.value("name"):
            self.iface.messageBar().pushMessage("Fehler", "Bitte wählen Sie zuerst ein Setup im Setup-Tool aus!", level=Qgis.Critical)
            return
        self.iface.messageBar().pushMessage("Leerrohr Erfassen aktiviert", level=Qgis.Info)
        if self.leerrohr_tool and not sip.isdeleted(self.leerrohr_tool):
            self.leerrohr_tool.close()
        self.leerrohr_tool = LeerrohrVerlegenTool(self.iface)
        self.leerrohr_tool.setAttribute(Qt.WA_DeleteOnClose)
        self.leerrohr_tool.show()

    def run_hausanschluss_verlegen(self):
        if not self.settings.value("name"):
            self.iface.messageBar().pushMessage("Fehler", "Bitte wählen Sie zuerst ein Setup im Setup-Tool aus!", level=Qgis.Critical)
            return
        if HauseinfuehrungsVerlegungsTool.instance is not None:
            HauseinfuehrungsVerlegungsTool.instance.raise_()
            HauseinfuehrungsVerlegungsTool.instance.activateWindow()
            return
        self.test_dialog = HauseinfuehrungsVerlegungsTool(self.iface)
        self.test_dialog.show()

    def unload(self):
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar = None
        # Reset QSettings bei Beenden
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
        self.settings.remove("leerohr_subtyp")
        self.settings.remove("qgis_project_path")
        self.settings.remove("db_connection")
        self.active_setup = {}  # Reset Setup-Dict
        self.logger.info("Plugin entladen, Einstellungen zurückgesetzt")