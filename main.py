from PyQt5.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.core import Qgis
from . import resources_rc
from .tools.leerrohr_verlegen.leerrohr_verlegen import LeerrohrVerlegenTool

import sys
sys.path.append(r'C:\Users\marce\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\ToolBox_SiegeleCo')

# Importiere die Tools, falls sie separate Module haben
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

class TollBoxSiegeleCoPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.kabel_tool = None  # Füge ein Attribut für das Kabel-Verlegen-Tool hinzu
        self.leerrohr_tool = None  # Füge ein Attribut für das Leerrohr-Erfassen-Tool hinzu

    def initGui(self):
        # Erstelle eine neue Symbolleiste für die Toolbox
        self.toolbar = self.iface.addToolBar("Toolbox SiegeleCo")
        
        # Erstellt Actions für jede Funktion und fügt sie zur Toolbar hinzu
        self.add_toolbar_action("Split Tool", self.run_split_tool, ":/plugins/ToolBox_SiegeleCo/icons/icon_split_tool.png")
        self.add_toolbar_action("Kundendaten Tool", self.run_kundendaten_tool, ":/plugins/ToolBox_SiegeleCo/icons/icon_kundendaten_tool.png")
        self.add_toolbar_action("Kabel Verlegen Tool", self.run_kabel_verlegen, ":/plugins/ToolBox_SiegeleCo/icons/icon_kabel_verlegen.png")
        self.add_toolbar_action("Trasse Verwalten Tool", self.run_trasse_verwalten, ":/plugins/ToolBox_SiegeleCo/icons/icon_trasse_verwalten_tool.png")
        self.add_toolbar_action("Leerrohr Verwalten Tool", self.run_leerrohr_erfassen, ":/plugins/ToolBox_SiegeleCo/icons/icon_leerrohr_verwalten_tool.png")
        self.add_toolbar_action("Hausanschluss Tool", self.run_other_tool, ":/plugins/ToolBox_SiegeleCo/icons/icon_hausanschluesse.png")

    def add_toolbar_action(self, name, function, icon_path):
        # Hilfsfunktion zum Erstellen und Hinzufügen einer Schaltfläche zur Toolbar
        icon = QIcon(icon_path)
        action = QAction(icon, name, self.iface.mainWindow())
        action.triggered.connect(function)
        self.toolbar.addAction(action)

    def run_split_tool(self):
        self.iface.messageBar().pushMessage("Split Tool aktiviert", level=Qgis.Info)

    def run_kundendaten_tool(self):
        self.iface.messageBar().pushMessage("Kundendaten Tool aktiviert", level=Qgis.Info)

    def run_kabel_verlegen(self):
        self.iface.messageBar().pushMessage("Kabel Verlegen Tool aktiviert", level=Qgis.Info)
        if not self.kabel_tool:
            self.kabel_tool = KabelVerlegungsTool(self.iface)
        self.kabel_tool.run()

    def run_trasse_verwalten(self):
        self.iface.messageBar().pushMessage("Trasse Verwalten Tool aktiviert", level=Qgis.Info)

    def run_leerrohr_erfassen(self):
        self.iface.messageBar().pushMessage("Leerrohr Erfassen aktiviert", level=Qgis.Info)
        self.test_dialog = LeerrohrVerlegenTool(self.iface)  # Übergibt iface korrekt
        self.test_dialog.show()  # Zeigt den Dialog an

    def run_other_tool(self):
        self.iface.messageBar().pushMessage("Hausanschluss Tool aktiviert", level=Qgis.Info)

    def unload(self):
        # Entferne die Symbolleiste bei Deaktivierung des Plugins
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar = None
