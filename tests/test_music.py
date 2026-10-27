"""Music (ACE-Step 1.5): the song settings reach the right nodes, the result lands in the project library."""
import json
import os

import pytest
from conftest import load


@pytest.fixture
def music(project, fake_comfy, monkeypatch):
    m = load("music_service")
    fake_comfy.patch(monkeypatch, m)
    return m


def test_song_settings_reach_the_workflow(music, project, fake_comfy):
    tr = music._run_one(project.pid, {"tags": "dark ambient, strings", "lyrics": "[verse]\nla la", "bpm": 90, "duration": 30,
                                      "keyscale": "A minor", "timesignature": "3", "language": "ru", "steps": 8}, 7, "t1")
    p = fake_comfy.runs[-1]["prompt"]
    song = next(n for n in p.values() if n["class_type"] == "TextEncodeAceStepAudio1.5")["inputs"]
    assert song["tags"] == "dark ambient, strings" and song["lyrics"].startswith("[verse]")
    assert (song["bpm"], song["duration"], song["keyscale"], song["timesignature"], song["language"]) == (90, 30.0, "A minor", "3", "ru")
    assert next(n for n in p.values() if n["class_type"] == "EmptyAceStep1.5LatentAudio")["inputs"]["seconds"] == 30.0
    assert next(n for n in p.values() if n["class_type"] == "KSampler")["inputs"]["seed"] == 7
    assert os.path.isfile(os.path.join(project.dir, "music", tr["file"]))
    meta = json.load(open(os.path.join(project.dir, "music", tr["file"][:-4] + ".json"), encoding="utf-8"))
    assert meta["bpm"] == 90 and meta["seed"] == 7


def test_empty_lyrics_mean_instrumental_and_bad_key_falls_back(music, project, fake_comfy):
    music._run_one(project.pid, {"tags": "piano", "lyrics": "", "keyscale": "H dur"}, 1, "t2")
    song = next(n for n in fake_comfy.runs[-1]["prompt"].values() if n["class_type"] == "TextEncodeAceStepAudio1.5")["inputs"]
    assert song["lyrics"] == "[instrumental]" and song["keyscale"] == "E minor"


def test_length_is_clamped(music, project, fake_comfy):
    music._run_one(project.pid, {"tags": "x", "duration": 3}, 1, "t3")
    assert next(n for n in fake_comfy.runs[-1]["prompt"].values() if n["class_type"] == "EmptyAceStep1.5LatentAudio")["inputs"]["seconds"] == 10.0


def test_library_lists_tracks_newest_first(music, project):
    d = os.path.join(project.dir, "music"); os.makedirs(d, exist_ok=True)
    for n in ("20260101_000000_1.mp3", "20260102_000000_2.mp3"):
        open(os.path.join(d, n), "wb").write(b"x")
    files = [t["file"] for t in music.list_tracks(project.pid)]
    assert files == ["20260102_000000_2.mp3", "20260101_000000_1.mp3"]


def test_start_refuses_without_models(music, project, comfy_dirs):
    with pytest.raises(Exception, match="not installed"):
        music.start(project.pid, {"tags": "x"})


def test_start_needs_style_or_lyrics(music, project, comfy_dirs):
    for kind, files in music.MODEL_FILES.items():
        comfy_dirs["files"][kind] = list(files)
    with pytest.raises(Exception, match="style|lyrics"):
        music.start(project.pid, {"tags": "", "lyrics": ""})
