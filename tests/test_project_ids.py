import json
import os

from vfx_nodes import project_manager as pm


def test_folder_path_never_becomes_project_id(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "PROJECTS_ROOT", str(tmp_path / "root"))
    folder = tmp_path / "Films" / "My_Shot"
    data = pm.create_or_open_project("My_Shot", project_id=str(folder), custom_path=str(folder))
    assert data["id"] == "My_Shot"
    assert os.path.isfile(folder / "project.json")
    assert pm.get_project_dir("My_Shot") == os.path.abspath(str(folder))


def test_legacy_path_id_is_repaired_on_open(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "PROJECTS_ROOT", str(tmp_path / "root"))
    folder = tmp_path / "Old"
    folder.mkdir()
    (folder / "project.json").write_text(json.dumps({"id": str(folder).replace("\\", "/"), "name": "Old"}), encoding="utf-8")
    data = pm.create_or_open_project("Old", project_id=str(folder), custom_path=str(folder))
    assert data["id"] == "Old"


def test_list_projects_has_no_duplicates(tmp_path, monkeypatch):
    root = tmp_path / "root"
    monkeypatch.setattr(pm, "PROJECTS_ROOT", str(root))
    pm.create_or_open_project("Alpha", custom_path=str(root))
    ids = [p["id"] for p in pm.list_projects()]
    assert ids.count("Alpha") == 1
