from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from markus_dispatcher.core.models import Client, ContactChannel
from markus_dispatcher.data import db
from markus_dispatcher.ui.icons import tool_button


class ClientsPane(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected_id: int | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)

        left = QVBoxLayout()
        title = QLabel(_tr("Klienci"))
        title.setObjectName("SectionLabel")
        left.addWidget(title)
        self.list = QListWidget()
        self.list.setObjectName("ClientsList")
        self.list.setSpacing(2)
        self.list.setStyleSheet("QListWidget#ClientsList::item { border-top: 1px solid #3c423b; border-bottom: 1px solid #3c423b; padding: 6px; }")
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list, stretch=1)
        add_btn = tool_button(_tr("Nowy klient"), "plus")
        add_btn.clicked.connect(self._new)
        left.addWidget(add_btn)
        root.addLayout(left, stretch=1)

        form_host = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit()
        self.email = QLineEdit()
        self.phone = QLineEdit()
        self.channel = QComboBox()
        self.channel.addItem(_tr("WhatsApp + e-mail"), "both")
        self.channel.addItem(_tr("Tylko WhatsApp"), "whatsapp")
        self.channel.addItem(_tr("Tylko e-mail"), "email")
        self.notes = QPlainTextEdit()
        form.addRow(_tr("Firma"), self.name)
        form.addRow("E-mail", self.email)
        form.addRow(_tr("Telefon / WA"), self.phone)
        form.addRow(_tr("Kanał"), self.channel)
        form.addRow(_tr("Notatki"), self.notes)
        form_host.addLayout(form)
        row = QHBoxLayout()
        save_btn = tool_button(_tr("Zapisz klienta"), "save", kind="primary")
        save_btn.clicked.connect(self._save)
        del_btn = tool_button(_tr("Usuń klienta"), "trash")
        del_btn.clicked.connect(self._delete)
        row.addWidget(save_btn)
        row.addWidget(del_btn)
        row.addStretch(1)
        form_host.addLayout(row)
        form_host.addStretch(1)
        root.addLayout(form_host, stretch=2)

        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for client in db.list_clients():
            item = QListWidgetItem(client.name)
            item.setData(256, client.id)  # Qt.UserRole
            self.list.addItem(item)

    def _on_select(self, current: QListWidgetItem | None, _prev: object) -> None:
        if current is None:
            self._selected_id = None
            return
        cid = current.data(256)
        self._selected_id = int(cid) if cid is not None else None
        clients = {c.id: c for c in db.list_clients()}
        client = clients.get(self._selected_id) if self._selected_id else None
        if client is None:
            return
        self._fill(client)

    def _fill(self, client: Client) -> None:
        self.name.setText(client.name)
        self.email.setText(client.email)
        self.phone.setText(client.phone)
        idx = self.channel.findData(client.preferred_channel)
        self.channel.setCurrentIndex(idx if idx >= 0 else 0)
        self.notes.setPlainText(client.notes)

    def _new(self) -> None:
        self._selected_id = None
        self.name.clear()
        self.email.clear()
        self.phone.clear()
        self.channel.setCurrentIndex(0)
        self.notes.clear()
        self.name.setFocus()

    def _save(self) -> None:
        channel = self.channel.currentData()
        assert isinstance(channel, str)
        try:
            client = db.upsert_client(
                client_id=self._selected_id,
                name=self.name.text(),
                email=self.email.text(),
                phone=self.phone.text(),
                preferred_channel=channel,  # type: ignore[arg-type]
                notes=self.notes.toPlainText(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "MarkusDispatcher", str(exc))
            return
        self._selected_id = client.id
        self.reload()

    def _delete(self) -> None:
        if self._selected_id is None:
            return
        db.delete_client(self._selected_id)
        self._new()
        self.reload()
