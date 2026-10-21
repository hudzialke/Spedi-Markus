from __future__ import annotations

from brudnopis_ot.core import paths as brudnopis_paths
from ewidentor.core import paths as ewidentor_paths
from spedi import paths
from spedi.paths import ENV_DATA_DIR, brudnopis_db, data_dir, ewidentor_db


def test_data_dir_honours_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "dane"))
    root = data_dir()
    assert root == tmp_path / "dane"
    assert brudnopis_db().parent == root
    assert ewidentor_db().name == "ewidentor.sqlite"


def test_frozen_data_dir_is_in_writable_user_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        paths,
        "user_data_dir",
        lambda app_name, appauthor=False: str(tmp_path / app_name),
    )

    root = data_dir()

    assert root == tmp_path / "Spedi" / "dane"
    assert root.is_dir()


def test_integrated_reports_and_scenarios_stay_outside_install_dir(
    tmp_path, monkeypatch
) -> None:
    user_data = tmp_path / "Spedi" / "dane"
    monkeypatch.setenv(ENV_DATA_DIR, str(user_data))

    assert brudnopis_paths.reports_dir() == tmp_path / "Spedi" / "raporty"
    assert brudnopis_paths.scenarios_dir() == tmp_path / "Spedi" / "scenariusze"
    assert ewidentor_paths.reports_dir() == tmp_path / "Spedi" / "raporty"
