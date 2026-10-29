from pathlib import Path
from types import SimpleNamespace

from markus_dispatcher.ui import main_window


class FakeMessageBox:
    class Icon:
        Information = object()

    class ButtonRole:
        ActionRole = object()

    class StandardButton:
        Close = object()

    shown = []

    def __init__(self, parent):
        self.parent = parent
        self.text = ""
        self.open_folder_button = None
        self.action_label = None
        self.clicked = None
        self.__class__.shown.append(self)

    def setIcon(self, icon):
        self.icon = icon

    def setWindowTitle(self, title):
        self.title = title

    def setText(self, text):
        self.text = text

    def addButton(self, *args):
        if len(args) == 2:
            self.action_label = args[0]
            self.open_folder_button = object()
            return self.open_folder_button
        return object()

    def exec(self):
        self.clicked = self.open_folder_button

    def clickedButton(self):
        return self.clicked

    @staticmethod
    def critical(*args):
        raise AssertionError(f"Unexpected error dialog: {args}")

    @staticmethod
    def warning(*args):
        raise AssertionError(f"Unexpected warning dialog: {args}")


def test_export_history_dialog_can_open_export_folder(tmp_path: Path, monkeypatch) -> None:
    export_path = tmp_path / "eksporty" / "MarkusDispatcher-historia-zlecen.xlsx"
    result = SimpleNamespace(
        path=export_path,
        job_count=12,
        verification_count=2,
    )
    opened = []
    FakeMessageBox.shown.clear()

    monkeypatch.setattr(main_window, "export_history", lambda: result)
    monkeypatch.setattr(main_window, "run_background", lambda parent, label, operation: operation(None))
    monkeypatch.setattr(main_window, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(
        main_window,
        "QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened.append(url.toLocalFile()) or True),
    )

    main_window.MainPanel._export_history(object())

    assert len(opened) == 1
    assert Path(opened[0]) == export_path.parent
    assert len(FakeMessageBox.shown) == 1
    assert FakeMessageBox.shown[0].action_label == "Otwórz folder"
    assert str(export_path) in FakeMessageBox.shown[0].text
