"""The AI Director: every action it can choose reaches a handler in the studio UI, and help questions are
answered from the user guide."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core_agent"))
import studio_agent as sa  # noqa: E402

JS = open(os.path.join(ROOT, "web", "standalone", "studio_tabs_controller.js"), encoding="utf-8").read()
HANDLED = set(re.findall(r'act\.type === "([a-z_0-9]+)"', JS))

SAMPLE = {"generate_image": {"prompt": "x"}, "generate_video": {"prompt": "x"}, "edit_image": {"prompt": "x"},
          "video_process": {"kind": "depth"}, "dubbing": {"mode": "dub"}, "mesh3d": {"image": "x"},
          "deconstruct": {"passes": "depth"}, "music": {"tags": "piano", "lyrics": ""}, "voice": {"text": "Привет"},
          "show_workflow": {}, "switch_tab": {"tab_id": "mesh_gen"}, "switch_dimension": {"dimension": "3D"},
          "open_details": {"target": "mesh3d"}, "save_pipeline": {"name": "Test Pipe"}, "save_tool": {"name": "Test Tool"}}


@pytest.mark.parametrize("action", sorted(sa.ACTIONS))
def test_every_agent_action_has_a_ui_handler(action):
    acts, _ = sa.to_studio_actions(action, SAMPLE.get(action, {}), {})
    assert acts, f"{action} produces no studio action"
    for a in acts:
        assert a["type"] in HANDLED, f"{action} -> '{a['type']}' has no handler in studio_tabs_controller.js"


def test_every_video_process_kind_is_documented_for_the_agent():
    import importlib
    vl = importlib.import_module("vfx_nodes.video_layers_service")
    src = open(os.path.join(ROOT, "vfx_nodes", "video_layers_service.py"), encoding="utf-8").read()
    kinds = re.findall(r'"([a-z_0-9]+)": _job_', src[src.index("def start_job"):src.index("def start_job") + 2000])
    know = open(os.path.join(ROOT, "core_agent", "knowledge_studio", "studio.md"), encoding="utf-8").read()
    internal = {"track_object", "fill", "mask_grow", "layer_to_video", "detect_all", "undistort"}   # UI-driven helpers
    missing = [k for k in kinds if k not in internal and f"| {k}" not in know and f"{k} |" not in know and f"/ {k}" not in know]
    assert not missing, f"not in core_agent/knowledge_studio/studio.md: {missing}"


@pytest.mark.parametrize("q,expect", [
    ("как сделать дубляж видео", "Дубляж"), ("как заменить фон", "фон"), ("как сделать музыку", "Музыка"),
    ("how do I dub a video", "Dub"), ("как озвучить длинный текст закадровым голосом", "Закадровый"),
    ("как сделать липсинк по фото", "липсинк")])
def test_help_questions_are_answered_from_the_guide(q, expect):
    s = sa.guide_snippets(q)
    assert expect.lower() in s.lower(), s[:400]


def test_clean_plate_action_strips_source_layer():
    acts, _ = sa.to_studio_actions("video_process", {
        "kind": "ltx_ic_lora",
        "params": {
            "lora": "clean-plate",
            "source_layer": "mask_id_player",
            "prompt": "An empty clean plate",
            "name": "clean_plate_scene"
        }
    }, {"active_video": {"id": "vid_123"}})
    assert len(acts) == 1
    assert acts[0]["type"] == "video_job"
    assert acts[0]["video_id"] == "vid_123"
    # clean-plate must NOT have source_layer so it processes the real video plate
    assert "source_layer" not in acts[0]["params"]


def test_dummy_source_layer_resolves_to_actual_mask():
    acts, _ = sa.to_studio_actions("video_process", {
        "kind": "corridor_key",
        "params": {
            "source_layer": "mask_id_player"
        }
    }, {"active_video": {"id": "vid_123", "layers": [{"id": "vl_real_mask", "type": "matte", "name": "Matte"}]}})
    assert len(acts) == 1
    assert acts[0]["params"]["source_layer"] == "vl_real_mask"


def test_create_blank_canvas_action():
    acts, _ = sa.to_studio_actions("create_blank_canvas", {
        "kind": "video",
        "title": "Холст для анимации",
        "width": 1920,
        "height": 1080,
        "fps": 24.0,
        "total_frames": 49,
        "color": "#00ff00"
    }, {})
    assert len(acts) == 1
    assert acts[0]["type"] == "create_blank_canvas"
    assert acts[0]["kind"] == "video"
    assert acts[0]["title"] == "Холст для анимации"
    assert acts[0]["width"] == 1920
    assert acts[0]["height"] == 1080
    assert acts[0]["fps"] == 24.0
    assert acts[0]["total_frames"] == 49
    assert acts[0]["color"] == "#00ff00"


def test_has_imperative_intent():
    assert sa.has_imperative_intent("сделай глубину")
    assert sa.has_imperative_intent("сгенерируй изображение киберпанк города")
    assert sa.has_imperative_intent("запусти генерацию")
    assert sa.has_imperative_intent("давай")
    assert sa.has_imperative_intent("нарисуй дракона")
    assert not sa.has_imperative_intent("какие у нас есть модели для видео?")


def test_imperative_bypasses_confirm(tmp_path):
    # When user uses imperative command, heavy action generates studio_actions immediately
    project_dir = str(tmp_path / "proj")
    os.makedirs(project_dir, exist_ok=True)
    res = sa.plan_turn(
        user_message="сделай арт неонового Токио",
        project_dir=project_dir,
        studio_ctx={"active_image": {"id": "img_1"}},
        agent_config={"provider": "mock"},
        llm_fallback=lambda sys, msgs: 'Я создам этот арт.\n```json\n{"action": "generate_image", "params": {"prompt": "neon Tokyo cyberpunk street"}}\n```'
    )
    assert res["decision"]["confirm"] is False
    assert len(res["studio_actions"]) > 0
    trigger_act = next((a for a in res["studio_actions"] if a["type"] == "trigger_generation"), None)
    assert trigger_act is not None
    assert trigger_act["mode"] == "image"


def test_action_visualization_and_stage_confirmation(tmp_path):
    project_dir = str(tmp_path / "proj")
    os.makedirs(project_dir, exist_ok=True)

    # 1. First turn: User requests work with stage confirmation and action visualization
    turn1 = sa.plan_turn(
        user_message="расставь 3д сцену, сгенерируй меш с подтверждением этапов и визуализируй действия",
        project_dir=project_dir,
        studio_ctx={},
        agent_config={"provider": "mock"}
    )
    assert "stage_workflow" in turn1
    swf1 = turn1["stage_workflow"]
    assert swf1["stage_index"] == 1
    assert swf1["total_stages"] == 3
    assert swf1["awaiting_approval"] is True
    acts1 = [a["type"] for a in turn1["studio_actions"]]
    assert "switch_dimension" in acts1
    assert "setup_3d_layout" in acts1

    # 2. Second turn: Reactive wakeup after Stage 1 approval
    turn2 = sa.plan_turn(
        user_message=swf1["next_instruction"],
        project_dir=project_dir,
        studio_ctx={},
        agent_config={"provider": "mock"}
    )
    assert "stage_workflow" in turn2
    swf2 = turn2["stage_workflow"]
    assert swf2["stage_index"] == 2
    assert swf2["awaiting_approval"] is True
    acts2 = [a["type"] for a in turn2["studio_actions"]]
    assert "switch_tab" in acts2
    assert "mesh3d_job" in acts2

    # 3. Third turn: Reactive wakeup after Stage 2 approval
    turn3 = sa.plan_turn(
        user_message=swf2["next_instruction"],
        project_dir=project_dir,
        studio_ctx={},
        agent_config={"provider": "mock"}
    )
    assert "stage_workflow" in turn3
    swf3 = turn3["stage_workflow"]
    assert swf3["stage_index"] == 3
    assert swf3["awaiting_approval"] is False
    acts3 = [a["type"] for a in turn3["studio_actions"]]
    assert "switch_dimension" in acts3
    assert "run_layout_to_render" in acts3


def test_vfx_agent_service_stage_confirmation():
    import importlib
    vas = importlib.import_module("vfx_nodes.vfx_agent_service")

    # Turn 1
    p1 = vas.generate_director_plan("расставь 3д сцену по этапам с подтверждением и покажи открывая окна")
    assert "stage_workflow" in p1
    assert p1["stage_workflow"]["stage_index"] == 1
    assert p1["stage_workflow"]["awaiting_approval"] is True
    acts1 = [a["type"] for a in p1["studio_actions"]]
    assert "switch_dimension" in acts1
    assert "setup_3d_layout" in acts1

    # Turn 2
    p2 = vas.generate_director_plan(p1["stage_workflow"]["next_instruction"])
    assert "stage_workflow" in p2
    assert p2["stage_workflow"]["stage_index"] == 2
    assert p2["stage_workflow"]["awaiting_approval"] is True
    acts2 = [a["type"] for a in p2["studio_actions"]]
    assert "switch_tab" in acts2
    assert "generate_3d_mesh" in acts2


def test_motion_reskin_and_performance_matchmove_pipeline(tmp_path):
    project_dir = str(tmp_path / "proj")
    os.makedirs(project_dir, exist_ok=True)

    # 1. Direct execution in studio_agent
    res = sa.plan_turn(
        user_message="берем видео снимаем с него анимацию людей в виде поз делаем трекинг далее создаем сцену добавляем трекинг камеру и проецируем на камеру анимацию скелета даем референс персонажа и в новой сцене новый персонаж",
        project_dir=project_dir,
        studio_ctx={"active_video": {"id": "vid_dance_01"}},
        agent_config={"provider": "mock"}
    )
    acts = [a["type"] for a in res["studio_actions"]]
    assert "video_job" in acts
    assert "setup_3d_layout" in acts
    assert "add_character_skeleton" in acts
    assert "project_skeleton_to_pose" in acts
    assert "trigger_generation" in acts

    # 2. Stage-confirmed execution
    res_staged = sa.plan_turn(
        user_message="сними анимацию поз с видео, сделай трекинг и спроецируй скелет на камеру по этапам с подтверждением",
        project_dir=project_dir,
        studio_ctx={"active_video": {"id": "vid_dance_01"}},
        agent_config={"provider": "mock"}
    )
    assert "stage_workflow" in res_staged
    swf = res_staged["stage_workflow"]
    assert swf["stage_index"] == 1
    assert swf["total_stages"] == 4
    assert swf["awaiting_approval"] is True
    staged_acts = [a.get("kind") or a.get("type") for a in res_staged["studio_actions"]]
    assert "pose" in staged_acts
    assert "track_camera" in staged_acts

    # 3. vfx_agent_service plan generation
    import importlib
    vas = importlib.import_module("vfx_nodes.vfx_agent_service")
    p = vas.generate_director_plan(
        "берем видео снимаем с него анимацию людей в виде поз делаем трекинг далее создаем сцену добавляем трекинг камеру и проецируем на камеру анимацию скелета даем референс персонажа и бац у нас в новой сцене новый персонаж",
        agent_config={"provider": "mock"}
    )
    p_acts = [a["type"] for a in p["studio_actions"]]
    assert "video_job" in p_acts
    assert "setup_3d_layout" in p_acts
    assert "add_character_skeleton" in p_acts
    assert "project_skeleton_to_pose" in p_acts
    assert "trigger_generation" in p_acts


def test_intent_actions_and_autonomous_mode(tmp_path):
    import studio_agent as sa
    pdir = str(tmp_path / "proj_intent")
    os.makedirs(pdir, exist_ok=True)
    
    # 1. Autonomous mode
    r1 = sa.plan_turn("делай все процессы без запроса продолжения работай автономно", pdir, {}, {})
    assert "автономный" in r1["response_ru"].lower()
    
    # 2. Details inspector
    r2 = sa.plan_turn("детализация 3д сцены", pdir, {}, {})
    acts2 = [a["type"] for a in r2["studio_actions"]]
    assert "open_details" in acts2
    assert "switch_tab" in acts2
    
    # 3. 3D Scene + Trellis + Camera + Layout-to-Render
    ctx = {
        "attached": [
            {"title": "girl.jpg", "url": "/view/girl.jpg"},
            {"title": "table.jpg", "url": "/view/table.jpg"}
        ]
    }
    msg = ("сделай сцену 3д добавь два куба сгенерируй с помощью треллис два объекта на основе референсов "
           "и замени ими кубы сделай под моделями пол добавь камеру которая с крупного плана девушки "
           "отъезжает на общий план и мы видим девушку в полный рост которая стоит рядом сто столом "
           "это вторая модель запусти генерацию лейаут то рендер создай видео")
    r3 = sa.plan_turn(msg, pdir, ctx, {})
    acts3 = [a["type"] for a in r3["studio_actions"]]
    assert "switch_dimension" in acts3
    assert "setup_3d_layout" in acts3
    assert "mesh3d_job" in acts3
    assert "run_layout_to_render" in acts3
    
    # 4. Go command resumes that workflow
    r4 = sa.plan_turn("запускай", pdir, ctx, {})
    acts4 = [a["type"] for a in r4["studio_actions"]]
    assert "setup_3d_layout" in acts4
    assert "run_layout_to_render" in acts4







def test_ltx_ic_lora_strips_chunk_and_enables_one_pass():
    import core_agent.studio_agent as sa
    acts, extra = sa.to_studio_actions(
        "video_process",
        {"kind": "ltx_ic_lora", "params": {"chunk": 33, "lora": "LTX-2.5/test.safetensors"}},
        {"active_video": {"id": "vid_123"}}
    )
    assert len(acts) == 1
    job_act = acts[0]
    assert job_act["type"] == "video_job"
    assert job_act["kind"] == "ltx_ic_lora"
    assert "chunk" not in job_act["params"], "chunk must be stripped so _one_pass is triggered"


def test_layout_to_render_auto_source_video():
    import core_agent.studio_agent as sa
    acts, extra = sa.to_studio_actions(
        "generate_video",
        {"mode": "layout_to_render", "prompt": "Cinematic test render"},
        {"videos": [{"id": "baked_layout_sequence", "title": "3D Layout Render"}]}
    )
    trig = [a for a in acts if a["type"] == "trigger_generation"][0]
    assert trig["generation_mode"] == "layout_to_render"
    assert trig["video_id"] == "baked_layout_sequence"
    assert trig["source_video"] == "baked_layout_sequence"


def test_97_frames_one_pass_intent(tmp_path):
    import core_agent.studio_agent as sa
    pdir = str(tmp_path)
    
    # 1. Question about 97 vs 33 frames
    r1 = sa.plan_turn("а если он будет не по 33 кадра генерить а целиком 97?", pdir, {}, {})
    assert "97 кадров" in r1["response_ru"]
    assert "one_pass" in r1["response_ru"].lower() or "однопроходн" in r1["response_ru"].lower()
    assert "5.0 гб" in r1["response_ru"].lower() or "5.0 гб vram" in r1["response_ru"].lower()

    # 2. Launch command for 97 frames
    r2 = sa.plan_turn("запусти однопроходный рендер целиком 97 кадров", pdir, {}, {})
    acts = [a["type"] for a in r2["studio_actions"]]
    assert "trigger_generation" in acts
    trig = [a for a in r2["studio_actions"] if a["type"] == "trigger_generation"][0]
    assert trig["generation_mode"] == "layout_to_render"
    assert trig["video_id"] == "baked_layout_sequence"


def test_generic_go_does_not_hijack_stage_workflow(tmp_path):
    import core_agent.studio_agent as sa
    import json
    pdir = str(tmp_path)

    state_file = os.path.join(pdir, 'agent_state.json')
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump({
            'stage_workflow': {
                'active': True,
                'stage_index': 2,
                'awaiting_approval': True,
                'next_instruction': 'Этап 2 утвержден...'
            }
        }, f)

    r = sa.plan_turn('делай', pdir, {}, {'model': 'mock', 'provider': 'mock'})
    assert 'Этап 2 утвержден' not in r['response_ru']
