import pytest

from vfx_nodes import kimodo_service as ks


def test_kimodo_not_configured(monkeypatch):
    monkeypatch.setattr(ks, "local_setting", lambda k, d="": "")
    st = ks.kimodo_status()
    assert st["available"] is False and "kimodo_root" in st["hint"]
    with pytest.raises(ValueError):
        ks.start_motion("p", "a person waves", 3)


def test_kimodo_python_found_from_root(tmp_path, monkeypatch):
    py = tmp_path / "venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(ks, "local_setting", lambda k, d="": str(tmp_path) if k == "kimodo_root" else "")
    st = ks.kimodo_status()
    assert st["available"] is True and st["python"] == str(py)


def test_empty_prompt_rejected():
    with pytest.raises(ValueError):
        ks.start_motion("p", "   ", 3)
