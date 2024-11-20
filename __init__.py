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

# Import der Hauptklasse des Plugins
from .main import TollBoxSiegeleCoPlugin

# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load TollBoxSiegeleCoPlugin class from file main.py."""
    # Lade die TollBoxSiegeleCoPlugin-Klasse aus main.py
    return TollBoxSiegeleCoPlugin(iface)
