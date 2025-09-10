# -*- coding: utf-8 -*-
"""
/***************************************************************************
 TollBoxSiegeleCo
                                 A QGIS plugin
 Eine Toolbox für verschiedene Werkzeuge in QGIS.
 ***************************************************************************/
"""

# Import der Ressourcen
from . import resources_rc
# ---- RC-Shim: macht "import Button_checkbox_rc" global verfügbar ----
import sys
try:
    from .tools.leerrohr_verbinder import Button_checkbox_rc as _rc
    sys.modules['Button_checkbox_rc'] = _rc
except Exception:
    pass
# ---------------------------------------------------------------------

# Import der Hauptklasse des Plugins
from .main import ToolBoxSiegeleCoPlugin

# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load ToollBoxSiegeleCoPlugin class from file main.py."""
    # Lade die ToolBoxSiegeleCoPlugin-Klasse aus main.py
    return ToolBoxSiegeleCoPlugin(iface)
