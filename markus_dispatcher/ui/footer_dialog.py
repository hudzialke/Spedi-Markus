from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from markus_dispatcher.core.instruction import DEFAULT_FOOTER


class FooterDialog(QDialog):
    def __init__(self, current_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Edytuj dopisek"))
        self.setMinimumSize(520, 280)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                _tr("Tekst na końcu instrukcji dla kierowcy. Puste = bez dopisku.")
            )
        )
        self._editor = QPlainTextEdit()
        self._editor.setPlainText(current_text)
        layout.addWidget(self._editor)
        row = QHBoxLayout()
        reset_btn = QPushButton(_tr("Przywróć domyślny"))
        reset_btn.clicked.connect(lambda: self._editor.setPlainText(DEFAULT_FOOTER))
        row.addWidget(reset_btn)
        row.addStretch(1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(_tr("Zapisz"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_tr("Anuluj"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def footer_text(self) -> str:
        return self._editor.toPlainText()
