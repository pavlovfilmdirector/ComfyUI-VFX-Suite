import pytest
from conftest import load


def test_load_voice_templates():
    pr = load("process_runner")
    t_a2v = pr.load_template("voice", "ltx25_a2v")
    assert "img" in t_a2v
    assert "aud" in t_a2v
    assert "trim" in t_a2v
    assert t_a2v["aud"]["class_type"] == "LoadAudio"
    assert t_a2v["trim"]["class_type"] == "TrimAudioDuration"

    t_dubit = pr.load_template("voice", "ltx23_dubit")
    assert "vid" in t_dubit
    assert "aenc" in t_dubit


def test_inject_lipsync_params():
    pr = load("process_runner")
    tmpl = pr.load_template("voice", "ltx25_a2v")
    params = {
        "img.image": "face_avatar.png",
        "aud.audio": "voiceover.wav",
        "trim.start_index": 2.0,
        "trim.duration": 3.5,
        "pos.text": "speaking character, looking into camera",
        "empty.length": 85
    }
    injected = pr.inject_params(tmpl, params)
    assert injected["img"]["inputs"]["image"] == "face_avatar.png"
    assert injected["aud"]["inputs"]["audio"] == "voiceover.wav"
    assert injected["trim"]["inputs"]["start_index"] == 2.0
    assert injected["trim"]["inputs"]["duration"] == 3.5
    assert injected["pos"]["inputs"]["text"] == "speaking character, looking into camera"
    assert injected["empty"]["inputs"]["length"] == 85


def test_invalid_voice_template_raises():
    pr = load("process_runner")
    with pytest.raises(pr.ProcessError):
        pr.load_template("voice", "unknown_template_xyz")
