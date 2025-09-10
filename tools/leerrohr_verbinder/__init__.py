# __init__.py im Ordner: .../ToolBox_SiegeleCo/tools/leerrohr_verbinder

# 1) Paket initialisieren (falls du hier schon Code hast, lass ihn stehen; füge das hier nur oben ein)
#    -> sorgt dafür, dass ein absoluter Import "import Button_checkbox_rc"
#       immer ein bereits geladenes Paketmodul bekommt.

from . import Button_checkbox_rc as _rc  # Button_checkbox_rc.py liegt im selben Ordner wie dieses __init__.py
import sys as _sys
# unter dem globalen Namen bereitstellen, damit "import Button_checkbox_rc" in der generierten UI funktioniert
_sys.modules.setdefault("Button_checkbox_rc", _rc)
