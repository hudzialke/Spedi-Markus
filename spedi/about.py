"""Public project attribution, AI disclosure and license summary."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from spedi import __version__
from spedi.i18n import tr

AUTHOR = "Patryk Prośniak (hudzialke)"
PROJECT_URL = "https://github.com/hudzialke/Spedi-Markus"


def show_about(parent=None) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("O programie Spedi"))
    dialog.setMinimumWidth(540)
    layout = QVBoxLayout(dialog)
    text = QLabel(
        f"<h2>Spedi {__version__}</h2>"
        f"<p>{tr('Autor')}: <b>{AUTHOR}</b></p>"
        f"<p>{tr('Program powstał przy wykorzystaniu różnych modeli sztucznej inteligencji (AI).')}</p>"
        f"<p>{tr('Licencja MIT pozwala używać, kopiować, modyfikować i rozpowszechniać program, również komercyjnie, z zachowaniem informacji o autorstwie i licencji.')}</p>"
        f"<p><b>{tr('Program jest udostępniany bez gwarancji. Autor nie ponosi odpowiedzialności za jego działanie ani skutki wykorzystania, w zakresie dopuszczonym przez prawo.')}</b></p>"
        f"<p>{tr('Wyniki wymagają samodzielnej weryfikacji. Program nie zastępuje porady prawnej ani kontroli zgodności z przepisami.')}</p>"
        f"<p>{tr('Parser DDD')}: Syax89 — MIT.</p>"
        f'<p><a href="{PROJECT_URL}">{PROJECT_URL}</a></p>'
    )
    text.setWordWrap(True)
    text.setOpenExternalLinks(True)
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
