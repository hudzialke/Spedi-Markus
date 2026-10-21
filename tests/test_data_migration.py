from pathlib import Path

from spedi import migrate


def test_installed_readonly_databases_are_migrated_to_user_data(
    tmp_path: Path, monkeypatch
) -> None:
    install_root = tmp_path / "Program Files" / "Spedi"
    installed_data = install_root / "dane"
    installed_data.mkdir(parents=True)
    (installed_data / "ddd_profiles.sqlite").write_bytes(b"brudnopis")
    (installed_data / "ewidentor.sqlite").write_bytes(b"ewidentor")

    user_data = tmp_path / "AppData" / "Local" / "Spedi" / "dane"
    monkeypatch.setattr(migrate, "app_root", lambda: install_root)
    monkeypatch.setattr(
        migrate, "local_app_data", lambda: tmp_path / "AppData" / "Local"
    )
    monkeypatch.setattr(migrate, "data_dir", lambda: user_data)
    monkeypatch.setattr(
        migrate, "brudnopis_db", lambda: user_data / "ddd_profiles.sqlite"
    )
    monkeypatch.setattr(
        migrate, "ewidentor_db", lambda: user_data / "ewidentor.sqlite"
    )

    notes = migrate.migrate_legacy_databases()

    assert (user_data / "ddd_profiles.sqlite").read_bytes() == b"brudnopis"
    assert (user_data / "ewidentor.sqlite").read_bytes() == b"ewidentor"
    assert len(notes) == 2
