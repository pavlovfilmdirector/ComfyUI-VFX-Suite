"""Voice-over: paragraphs, cache — only changed paragraphs are voiced again; the track joins them all."""
import os

import pytest
from conftest import load, make_wav


@pytest.fixture
def narr(project, monkeypatch, tmp_path):
    n = load("narration_service")
    vb = load("voice_board_service")
    calls = []

    def fake_start(mode, cfg, project_id=""):
        calls.append((mode, cfg))
        return {"id": f"d{len(calls)}", "status": "running"}

    monkeypatch.setattr(n.dub, "start", fake_start)
    monkeypatch.setattr(vb, "_wait_dub", lambda jid, parent, lo, hi: fake_last(calls))
    monkeypatch.setattr(n, "_wait_dub", lambda jid, parent, lo, hi: fake_last(calls))

    def fake_last(c):
        mode, cfg = c[-1]
        if mode == "voice":
            p = make_wav(str(tmp_path / f"v{len(c)}.wav"), seconds=0.5)
            return {"status": "done", "result": {"audio": {"path": p}}}
        if mode == "transcribe":
            return {"status": "done", "log": ["TRANSCRIBE_RESULT:слова фрагмента"]}
        return {"status": "done", "details": {"processed_text": cfg.get("text", "").upper()}}

    ref = make_wav(str(tmp_path / "ref.wav"), seconds=14)
    monkeypatch.setattr(n, "resolve_media_path", lambda u: ref if u else "")
    n.save_doc(project.pid, {"text": "Первый абзац.\n\nВторой абзац.\n\nТретий.", "ref_audio": "/ref.wav", "language": "ru"})
    n._calls = calls
    return n


def run(n, pid, only=None):
    vb = load("voice_board_service")
    jid = "job_test"
    vb._JOBS[jid] = {"id": jid, "status": "running"}
    return n._generate(jid, pid, list(only or []))


def test_paragraphs_split_on_empty_lines(narr):
    assert narr.paragraphs("a\nb\n\n\nc\n\n") == ["a b", "c"]


def test_first_generate_voices_every_paragraph_and_joins(narr, project):
    r = run(narr, project.pid)
    voices = [c for c in narr._calls if c[0] == "voice"]
    assert len(voices) == 3 and r["voiced"] == 3 and r["cached"] == 0
    assert os.path.isfile(os.path.join(project.dir, "audio_tts", os.path.basename(r["audio"])))
    assert r["seconds"] > 1.5   # 3 x 0.5 s + pauses


def test_reference_fragment_is_capped_at_10_s_and_transcribed(narr, project):
    run(narr, project.pid)
    tr = next(c for c in narr._calls if c[0] == "transcribe")[1]["load_audio"]
    assert tr["end_time"] - tr["start_time"] <= 10.0
    v = next(c for c in narr._calls if c[0] == "voice")[1]
    assert v["tts_general"]["ref_text"] == "слова фрагмента" and v["tts_general"]["ref_end_time"] <= 10.0


def test_only_the_edited_paragraph_is_voiced_again(narr, project):
    run(narr, project.pid)
    narr._calls.clear()
    narr.save_doc(project.pid, {"text": "Первый абзац.\n\nВторой абзац, исправленный.\n\nТретий."})
    r = run(narr, project.pid)
    voices = [c for c in narr._calls if c[0] == "voice"]
    assert len(voices) == 1 and voices[0][1]["tts_general"]["target_text"] == "Второй абзац, исправленный."
    assert r["cached"] == 2


def test_retake_of_one_paragraph_gets_a_new_seed(narr, project):
    run(narr, project.pid)
    seed0 = next(c for c in narr._calls if c[0] == "voice")[1]["f5_tts"]["seed"]
    narr._calls.clear()
    r = run(narr, project.pid, only=[1])
    voices = [c for c in narr._calls if c[0] == "voice"]
    assert len(voices) == 1 and voices[0][1]["f5_tts"]["seed"] != seed0 and r["cached"] == 2


def test_voice_change_invalidates_the_cache(narr, project):
    run(narr, project.pid)
    narr._calls.clear()
    narr.save_doc(project.pid, {"ref_start": 2.0})
    run(narr, project.pid)
    assert len([c for c in narr._calls if c[0] == "voice"]) == 3


def test_status_marks_voiced_paragraphs(narr, project):
    run(narr, project.pid)
    narr.save_doc(project.pid, {"text": "Первый абзац.\n\nНовый."})
    parts = narr.status_parts(project.pid)["parts"]
    assert parts[0]["audio"] and parts[1]["audio"] is None


def test_cache_is_server_owned(narr, project):
    run(narr, project.pid)
    narr.save_doc(project.pid, {"cache": {}, "text": "Первый абзац."})
    assert narr.load_doc(project.pid)["cache"]
