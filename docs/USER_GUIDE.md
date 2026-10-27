# KESHIKI — User Guide

KESHIKI (景色) is a VFX studio inside ComfyUI: footage, masks, 3D scenes, frame and video generation (FLUX.3 Klein, LTX-2.5), compositing, dubbing and 3D assets. Everything runs locally on open-weights models.

The studio's AI Director (the agent) knows this guide: ask it "how do I …?" and it answers with these steps and offers to run them for you. A Russian version: [USER_GUIDE_RU.md](USER_GUIDE_RU.md).

## Quick start

1. Run `run_keshiki_studio.bat` (or start ComfyUI and open `http://127.0.0.1:8188/vfx/studio/`).
2. Pick or create a project at the top left.
3. Drop videos or images into the Asset Gallery at the top. Videos open in the Video view, images in Image.
4. In Video, first press **Prepare Plate** — the clip is split into frames. Every video process needs it.
5. Then use the tools next to the video (masks, maps, generation) or ask the agent (bottom right).

## Views and tabs

### Image
Layers, masks, poses, FLUX.3 Klein generation: from text, editing by masks and references, pose transfer.

**Drawings → generation.** Draw on the frame with the brush, then in Generate choose what each paint layer is:
- **notes = instructions**: arrows, circles, words ("a lamp here", "make it red") tell Klein what to change and never show in the result;
- **sketch → real objects**: rough strokes become real things in the scene's style;
- **collage → blend in**: the Collage button (yellow "image +" icon) pastes a project image as its own layer; move it with V, trim it with the eraser; Generate blends it in (light, shadows, perspective).
Each layer can carry a short note.

**Layer transform.** Move tool (V): the active layer gets a box. Corners scale (free; Shift = proportional), edge handles stretch one axis, the yellow handle on top rotates (Shift = 15° steps), inside the box moves. Scale and rotation work around the box centre and are always rendered from the original pixels, so the layer does not get blurry.

**Selection transform.** Make a selection (lasso, rectangle, ellipse) and press V: the box wraps the selection and the same handles move, rotate and scale the selected part of the active layer together with the selection. If the active layer is empty inside the selection, a piece of the picture itself is lifted onto a new "Selection copy" layer, handy for collages from parts of the frame.

### Video
The clip and its layers: masks, depth, pose, generated clips, composites. Layers can be hidden, reordered, faded, and masks viewed in black and white.

### 3D — scene
Characters with animation (FBX, Mixamo, built-in mannequin skeleton), primitives (box, cylinder, sphere…), cameras and camera moves (orbit, dolly), camera tracking from footage. From 3D you send a pose layer and a render layer (clay characters or the environment) to the Video clip.

### Tabs on the left
Storyboard · Characters · Locations · **Voice (dubbing)** · Video · **3D meshes (TRELLIS.2)** · Deconstruction · Composite · Music · Settings.

## Video processes (Video view)

### Masks
- **SAM 3.1: mask by text** — type what to mask (`person`, `red car`, `dog`); it is found and tracked through the clip. The easiest way.
- **SAM2: click & track** — click the object on a frame.
- **YOLO / YOLO + SAM2** — all moving objects (people, cars) in one layer.
- **CorridorKey** — clean matte and foreground from a draft mask.
- **LTX Alpha** — matte without a mask (heavy 22B model).

### Maps
- **Depth** — Depth Anything V2 (default, best relief), **Depth Anything 3** (sharper edges) or **Apple Depth Pro** (clean silhouettes, steady over time, ~1 s/frame). **Background only**: the character is cut out of the depth by its mask and filled from the set, so depth drives the set and the camera while pose drives the character.
- **Pose** — DWPose skeleton per frame.
- **Canny** edges.

### Camera
- **Camera tracking (COLMAP + GLOMAP)** — recovers the camera for 3D. Mask the moving objects first (YOLO), then track.
- **Lens** — undistortion.

### Generation (the Generate button of the clip)
LTX-2.5 + IC-LoRA: pick a mode (layout-to-render, union-control, clean-plate, refine-details, restore, upscaler…), a control layer and a prompt. You can also:
- **set the first frame** — the character's look (see below);
- **relight the first frame**: once the frame exists, a "Relight" block sets the key light direction, height, strength and colour (Qwen Edit, ~20 s);
- **add a second control** — a second IC-LoRA with its own layer (e.g. layout-to-render on a render + pose).

Clips up to 97 frames (~4 s) are generated in one pass — no seams, ~3–4 minutes on 24 GB. Longer clips go in 97-frame passes that overlap by 16 frames and are cross-faded. With a second control (two IC-LoRAs) a pass is 49 frames on 24 GB.

### Composite (in the matte tool group)
A subject cut by its mask from one source over another background:
- **Subject from** — the original footage or a generated layer.
- **Subject mask** — usually a SAM 3.1 mask.
- **Background** — a generated layer or a clean plate.
- **Harmonize** (0.4) pulls the subject's colour and light toward the background; **Light wrap** (0.3) lets the background's light bleed over the subject's edges.
- **Then refine** — an LTX refine-details pass over the composite: smoother edges and details, ~10 min per 100 frames. It barely changes the light; colour and light matching is what Harmonize does.

## Recipes

### New background, same person
1. **Prepare Plate**.
2. **SAM 3.1** → `person`: the person's mask.
3. **Depth** — it carries the camera motion.
4. First frame of the new background: open frame 0 in Image and ask Klein to "replace the background with …, keep the person unchanged" (or use your own picture).
5. **Generate** → union-control, control layer = depth, first frame = step 4, prompt = the new scene. You get the new background with the same camera move.
6. **Composite**: subject = original footage, mask = step 2, background = step 5, harmonize 0.4.
7. Optionally tick "Then refine".

### New character, same background
1. **Prepare Plate**.
2. **Generate** → clean-plate with "An empty clean plate of the exact same location … with no people": the background without the person.
3. **Pose** (DWPose).
4. First frame of the new character: in Generate tick "Set the first frame", choose the character picture, the scene layer (original footage) and the pose layer → "Generate the frame". Not happy → "Another variant".
5. **Generate** → union-control on the pose, with that first frame.
6. **SAM 3.1** on the generated layer: the new character's mask.
7. **Composite**: subject = step 5, mask = step 6, background = the clean plate from step 2.

### A character driven by 3D animation
1. In 3D add a character or a skeleton (orange "person +" button) and load an animation (FBX / Mixamo — retargeted automatically).
2. Block out the set with primitives (a bench from boxes, a tree from a cylinder and a sphere…) and pick a camera (e.g. orbit).
3. Send to Video with the 3D panel buttons: **Pose from 3D** (cyan "image" icon) and **Environment render** (green "trees" icon). **Render from 3D** ("box" icon) renders the characters themselves in grey clay — the input for layout-to-render when the character must follow the 3D model's shape.
4. In Generate: "Set the first frame" → character + environment layer + pose layer + "what the shapes become".
5. **Generate** → layout-to-render on the environment + second control union-control on the pose (influence 0.8).

### Dub a video
**Voice** tab → "Dub a video": the video file (the Video button takes the open clip), languages, translator (a local LLM via Ollama), voice engine (F5-TTS). By default the original speaker's voice is cloned; you can give your own sample. Stages: analysis (Whisper, voice separation) → translation → text preparation → synthesis → timing and final mix. You can resume from any stage. The result is saved to the project's `audio_tts` folder.

### Join clips (editing without generation)
In the Video view file strip press **Join**: click the clips in play order, set the dissolve (overlap frames) and press Join. Each clip's end fades into the next one's start. The result is a new project video. Picture only for now, no sound.

### Music (ACE-Step 1.5)
**Music** tab: style (genre, mood, instruments, voice - click the ready tags), lyrics with structure ([verse], [chorus]… - the buttons insert a tag; empty = instrumental), length, BPM, key, meter, lyrics language, number of variants → Generate. Tracks collect in the project library (`music` folder): play them, add them to the project audio or re-run with the same settings. Models (~15 GB): `.\install.bat --download-models music`.

### Voice board (voice + lip-sync)
**Voice** tab → **Voice board**: a free node canvas, like ComfyUI. The top buttons add nodes: **Picture**, **Clip** (from the project), **Audio** (from the project, a file or a microphone take), **Text**. Drag nodes by the header; wheel = zoom, drag the background = pan, ⤢ = show all. To connect, drag from a node's right dot to another node; click a wire to remove it.
- **Text → Picture/Clip**: a line of speech with a start (when it sounds) and a length (the voice is stretched or squeezed to fit). Voice = synthesise it (F5-TTS).
- **Audio → Text**: the voice for that line. Audio roles:
  - *voice sample*: the clone copies timbre and delivery (intonation, pace, emotion) of a fragment up to 10 s (from–to, ▶); it needs the fragment's exact words: type them or press Recognise;
  - *draft take*: your own reading of the line; it gives the timing and, when the text is empty, the words; without a sample the voice is cloned from the draft itself.
- **Audio → Picture/Clip**: the default sample for all lines, or *ready speech* (used as is, with its own start).
- **Picture** → Lip-sync video (LTX-2.5 audio-to-video, the lips follow the voice, the picture is the first frame, up to 20 s).
- **Clip** → Lay the voices onto the clip (its own sound can stay, quieter), or **DubIt** Re-voice + lips (LTX-2.3: the speaker says the texts' words with new lips and voice; one speaker, up to 8 s, words in the target language about as long as the original).
"To project videos" adds the result to the files.

### Voice-over (long text)
**Voice** tab → **Voice-over**. Paste the text; separate paragraphs with an empty line. On the right: the voice sample (project or file), a fragment up to 10 s, its exact words (Recognise), language, pause between paragraphs, speed. **Prepare text**: ё, numbers as words and stress marks (Russian); stress can be fixed by hand. **Generate** voices only new and changed paragraphs, the rest come from the cache, and everything is joined into one track. Don't like a paragraph? Edit it and Generate again, or press ↻ on it for a new take of just that one. The track goes to the project audio with one button.

### Clone a voice / narrate a text
**Voice** tab → "Clone a voice" (sample + text) or "Narrate text" (long text, Russian stress marks).

### 3D model from an image (TRELLIS.2)
**3D meshes** tab → TRELLIS.2 → "Image → 3D model": reference (characters / shots or from disk), resolution, polygons, texture size → "Generate". The model lands in the library and in the 3D scene (as a new object or replacing the selected cube). "Texture a mesh" paints an existing library mesh from the image.

### Train your own LoRA
See [LORA_TRAINING.md](LORA_TRAINING.md) (Linux/WSL2, VRAM notes, configs tested on 24 GB).

## Talking to the agent

- Say what you want in plain words: "mask the girl", "replace the background with a winter street", "show me how to dub this".
- The agent sees the open clip, its layers and the project files. It picks the right layer itself; if one is missing it offers to make it.
- Heavy jobs (LTX, tracking, relight, dubbing, TRELLIS) are described first, then it asks "shall I run it?". Answer "yes", "go", "ok".
- Multi-step tasks are done one step at a time; the agent tells you which layer each step produced. You can stop and adjust at any step.
- Ask "how do I …?" and the agent answers from this guide and offers to do the steps for you.
- The agent runs on a local model through Ollama (e.g. qwen or gemma). Bigger models understand complex requests better.

## Troubleshooting

- **"Prepare the plate first" / no frames** — press Prepare Plate.
- **The first video generation is slow** — LTX 22B takes 2–3 minutes to load, later runs are faster. Don't start two heavy jobs at once.
- **Seams in the video** — keep shots up to 97 frames (one pass), or overlap 16 for longer ones.
- **The first frame copies the reference instead of the pose** — use the first frame with a separate pose layer (skeleton as its own image), not a skeleton drawn over the scene.
- **The subject's light doesn't match the background** — harmonize 0.4–0.5 in Composite.
- **The mask grabbed the wrong thing** — make the SAM 3.1 text more specific (`woman in red dress`) or click the object with SAM2.
- **Dubbing says "not set up"** — set `dubbing_python` and `dubbing_models` in `keshiki_local.json` (see INSTALL.md).
- **The agent doesn't answer** — is Ollama running and does it have a model?
- **No cloud services in the menus** — by design: KESHIKI runs locally. Cloud opt-in: `KESHIKI_ALLOW_CLOUD=1`.
- **24 GB VRAM** — everything is tuned for it. If memory runs out, close other GPU apps and don't keep LTX and Qwen loaded at the same time.
- **Two IC-LoRAs at once (e.g. pose + depth)** — on 24 GB keep it to 49 frames per run (~1.5 min). At 97 frames the model doesn't fit, generation takes 30+ minutes and decoding can stall.
- **Full regeneration from the skeleton** (new background + the original skeleton) — the face drifts over time, more on dynamic shots. If the face matters, swap the background by compositing (the "New background" recipe), which keeps the original person.
- **Depth and faces** — no depth map (V2, DA3, Depth Pro) holds a face: the head wanders and the features drift from the original. Rule: if the character must stay recognisable, generate from the pose only, without depth. Use depth only when the person doesn't matter (extras, an unknown person) or for the background and camera move.
