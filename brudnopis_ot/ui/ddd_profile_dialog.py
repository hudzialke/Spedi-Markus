from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from brudnopis_ot.core.ddd_file_loader import DddLoadResult


class DddProfilePickDialog(QDialog):
    """Lista profili po batch-importcie C_*/M_* — wybór osi czasu."""

    def __init__(
        self,
        profiles: list[DddLoadResult],
        *,
        link_hints: tuple[str, ...] = (),
        errors: tuple[str, ...] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Wybierz profil DDD"))
        self.resize(560, 420)
        self._profiles = list(profiles)
        self._selected: DddLoadResult | None = None

        root = QVBoxLayout(self)
        root.addWidget(
            QLabel(
                _tr("Pliki zostały pogrupowane po karcie kierowcy (C_*) "
                "i rejestracji pojazdu (M_*). Wybierz profil do osi:")
            )
        )

        self._list = QListWidget()
        for profile in self._profiles:
            kind = "VU" if profile.source_kind == "vu" else _tr("Karta")
            span = ""
            if profile.date_from and profile.date_to:
                span = (
                    f" · {profile.date_from.strftime('%d.%m.%Y')}–"
                    f"{profile.date_to.strftime('%d.%m.%Y')}"
                )
            text = (
                f"[{kind}] {profile.profile_label} · "
                f"{profile.filename} · {len(profile.segments)} seg.{span}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, profile.profile_key)
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(self._accept_current)
        root.addWidget(self._list, stretch=1)

        notes: list[str] = []
        if link_hints:
            notes.append(_tr("Powiązania karta ↔ VU:"))
            notes.extend(f"• {h}" for h in link_hints)
        if errors:
            if notes:
                notes.append("")
            notes.append(_tr("Pominięte / błędy:"))
            notes.extend(f"• {e}" for e in errors)
        if notes:
            box = QTextEdit()
            box.setReadOnly(True)
            box.setMaximumHeight(120)
            box.setPlainText("\n".join(notes))
            root.addWidget(box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(buttons)
        root.addLayout(row)

        if self._list.count():
            self._list.setCurrentRow(0)

    def selected_profile(self) -> DddLoadResult | None:
        return self._selected

    def _accept_current(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        for profile in self._profiles:
            if profile.profile_key == key:
                self._selected = profile
                self.accept()
                return
