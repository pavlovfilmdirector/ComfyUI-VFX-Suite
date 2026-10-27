"""Voice board: lines -> voice (sample fragment <= 10 s, draft take, ready speech, timing fit),
picture -> LTX-2.5 audio-to-video, clip -> voices mixed in or DubIt."""
import os

import pytest
from conftest import load, make_video, make_wav


@pytest.fixture
def vb(project, fake_comfy, monkeypatch, tmp_path):
    m = load("voice_board_service")
    fake_comfy.patch(monkeypatch, m)
    calls = []

    def fake_start(mode, cfg, project_id=""):
        calls.append((mode, cfg))
        return {"id": f"d{len(calls)}", "status": "running"}

    def fake_wait(jid, parent, lo, hi):
        mode, cfg = calls[-1]
        if mode == "voice":
            d = os.path.join(project.dir, "audio_tts"); os.makedirs(d, exist_ok=True)
            p = make_wav(os.path.join(d, f"cue{len(calls)}.wav"), seconds=1.0)
            return {"status": "done", "result": {"audio": {"path": p, "url": f"/vfx/project_file/{project.pid}/audio_tts/cue{len(calls)}.wav"}}}
        return {"status": "done", "log": ["TRANSCRIBE_RESULT:распознанные слова"]}

    monkeypatch.setattr(m.dub, "start", fake_start)
    monkeypatch.setattr(m, "_wait_dub", fake_wait)
    media = {"/img.png": str(tmp_path / "img.png"), "/clip.mp4": make_video(str(tmp_path / "clip.mp4"), frames=72),
             "/sample.wav": make_wav(str(tmp_path / "sample.wav"), seconds=15), "/draft.wav": make_wav(str(tmp_path / "draft.wav"), seconds=2.0),
             "/speech.wav": make_wav(str(tmp_path / "speech.wav"), seconds=1.5)}
    import cv2, numpy as np
    cv2.imwrite(media["/img.png"], np.full((64, 64, 3), 200, np.uint8))

    def resolve(u):
        if u in media:
            return media[u]
        if u.startswith(f"/vfx/project_file/{project.pid}/"):
            return os.path.join(project.dir, u.split(f"/vfx/project_file/{project.pid}/", 1)[1])
        return ""
    monkeypatch.setattr(m, "resolve_media_path", resolve)
    m._calls = calls
    return m


def job(m):
    m._JOBS["t"] = {"id": "t", "status": "running"}
    return "t"


def board(m, pid, cards):
    return m.save_board(pid, {"cards": cards, "language": "ru"})


def test_ref_window_is_capped_at_10_seconds(vb):
    assert vb._ref_window({"ref_start": 2, "ref_end": 30}, "/x") == (2.0, 12.0)


def test_voice_uses_sample_fragment_and_auto_transcribes_it(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [
        {"id": "q1", "text": "Привет", "ref_audio": "/sample.wav", "ref_start": 1, "ref_end": 20}]}])
    r = vb._voice(job(vb), project.pid, "c1", "q1")
    tr = next(c for c in vb._calls if c[0] == "transcribe")[1]["load_audio"]
    assert (tr["start_time"], tr["end_time"]) == (1.0, 11.0)
    v = next(c for c in vb._calls if c[0] == "voice")[1]["tts_general"]
    assert v["ref_text"] == "распознанные слова" and v["target_text"] == "Привет" and v["ref_end_time"] == 11.0
    assert r["audio"] and vb.load_board(project.pid)["cards"][0]["cues"][0]["audio"] == r["audio"]


def test_typed_fragment_words_skip_transcription(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [
        {"id": "q1", "text": "Привет", "ref_audio": "/sample.wav", "ref_text": "мои слова"}]}])
    vb._voice(job(vb), project.pid, "c1", "q1")
    assert not [c for c in vb._calls if c[0] == "transcribe"]


def test_draft_gives_words_and_timing(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [
        {"id": "q1", "text": "", "ref_audio": "/sample.wav", "ref_text": "x", "draft_audio": "/draft.wav"}]}])
    r = vb._voice(job(vb), project.pid, "c1", "q1")
    assert r["text"] == "распознанные слова"
    assert abs(r["seconds"] - 2.0) < 0.15          # fitted to the draft's length


def test_draft_as_reference_when_no_sample(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [
        {"id": "q1", "text": "Да", "draft_audio": "/draft.wav", "ref_source": "draft", "ref_text": "черновик"}]}])
    vb._voice(job(vb), project.pid, "c1", "q1")
    assert next(c for c in vb._calls if c[0] == "voice")[1]["tts_general"]["speaker_ref"].endswith("draft.wav")


def test_explicit_duration_stretches_the_voice(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [
        {"id": "q1", "text": "Да", "ref_audio": "/sample.wav", "ref_text": "x", "duration": 1.6}]}])
    assert abs(vb._voice(job(vb), project.pid, "c1", "q1")["seconds"] - 1.6) < 0.15


def test_ready_speech_is_used_as_is(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [{"id": "a1", "speech": "/speech.wav", "start": 0}]}])
    r = vb._voice(job(vb), project.pid, "c1", "a1")
    assert r["audio"] == "/speech.wav" and not vb._calls


def test_line_without_any_voice_is_refused(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [{"id": "q1", "text": "Да"}]}])
    with pytest.raises(Exception, match="voice sample"):
        vb._voice(job(vb), project.pid, "c1", "q1")


def test_lipsync_builds_a2v_with_audio_length(vb, project, fake_comfy):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [
        {"id": "q1", "text": "x", "audio": "/speech.wav", "start": 0}]}])
    r = vb._lipsync(job(vb), project.pid, "c1", "", 5)
    p = fake_comfy.runs[-1]["prompt"]
    length = next(n for n in p.values() if n["class_type"] == "EmptyLTXVLatentVideo")["inputs"]["length"]
    assert (length - 1) % 8 == 0 and length >= 1.5 * 24
    assert next(n for n in p.values() if n["class_type"] == "TrimAudioDuration")["inputs"]["duration"] >= 1.5
    assert os.path.isfile(os.path.join(project.dir, "source_video", os.path.basename(r["video"])))


def test_lipsync_places_lines_at_their_start(vb, project, fake_comfy):
    board(vb, project.pid, [{"id": "c1", "kind": "image", "media": "/img.png", "cues": [
        {"id": "q1", "audio": "/speech.wav", "start": 0}, {"id": "q2", "audio": "/speech.wav", "start": 3.0}]}])
    vb._lipsync(job(vb), project.pid, "c1", "", 1)
    trim = next(n for n in fake_comfy.runs[-1]["prompt"].values() if n["class_type"] == "TrimAudioDuration")["inputs"]["duration"]
    assert trim >= 4.5        # second line starts at 3 s and lasts 1.5 s


def test_lipsync_needs_a_picture_card(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "video", "media": "/clip.mp4", "cues": []}])
    with pytest.raises(Exception, match="image"):
        vb._lipsync(job(vb), project.pid, "c1", "", 1)


def test_dubit_prompt_has_the_new_words_in_quotes(vb, project, fake_comfy):
    board(vb, project.pid, [{"id": "c1", "kind": "video", "media": "/clip.mp4", "cues": [{"id": "q1", "text": "Новые слова"}]}])
    r = vb._dubit(job(vb), project.pid, "c1", "A man", 0, 5, 3)
    p = fake_comfy.runs[-1]["prompt"]
    assert 'speaking in Russian saying: "Новые слова"' in r["prompt"]
    length = next(n for n in p.values() if n["class_type"] == "EmptyLTXVLatentVideo")["inputs"]["length"]
    assert (length - 1) % 8 == 0 and length <= 72


def test_dubit_needs_words(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "video", "media": "/clip.mp4", "cues": [{"id": "q1", "text": ""}]}])
    with pytest.raises(Exception, match="new words"):
        vb._dubit(job(vb), project.pid, "c1", "", 0, 5, 1)


def test_mix_lays_voices_onto_the_clip(vb, project):
    board(vb, project.pid, [{"id": "c1", "kind": "video", "media": "/clip.mp4", "cues": [{"id": "q1", "audio": "/speech.wav", "start": 0.5}]}])
    r = vb._mix(job(vb), project.pid, "c1", False)
    out = os.path.join(project.dir, "source_video", os.path.basename(r["video"]))
    assert os.path.isfile(out) and os.path.getsize(out) > 1000


def test_board_save_load_roundtrip_keeps_canvas(vb, project):
    vb.save_board(project.pid, {"cards": [], "canvas": {"nodes": [{"id": "n1", "type": "text"}], "edges": []}})
    assert vb.load_board(project.pid)["canvas"]["nodes"][0]["id"] == "n1"
