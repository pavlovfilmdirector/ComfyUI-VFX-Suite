# AI Director Prompt Cheat Sheet & Workflow Guide (KESHIKI VFX Studio)
> Reference guide of tested prompt commands and trigger phrases for all studio production pipelines.

This document contains ready-to-use user prompts that the built-in AI Director (and external agents connected via MCP) are specifically trained and calibrated to execute autonomously without syntax errors or missing parameters. You can copy and paste these commands directly or customize them for your project.

---

## 1. End-to-End Concept-to-Video: 2D Reference → 3D Scene → TRELLIS.2 → Camera Orbit → Layout-to-Render

Used when you have one or two concept images (props or an environment) and want the AI Director to autonomously build the 3D scene, generate 3D assets, set up the camera trajectory, and trigger the two-stage distilled video render.

### 📝 Prompt for the Agent:
```text
build a 3d scene add two cubes generate two 3d assets with trellis based on attached references and replace the cubes add a ground plane under models set up a camera that pulls back from a close-up of the girl to a wide full-body shot standing next to the table which is the second model launch layout to render and generate video
```

### ⚡ What the Agent Executes:
1. Switches the viewport dimension to **3D**.
2. Creates proxy bounding boxes for key scene elements (`Cube_Girl`, `Cube_Table`) and adds a floor ground plane.
3. Calculates a cinematic orbit camera trajectory (180° arc, 1.1m lift, 50mm focal length, 49 frames).
4. Launches high-fidelity PBR 3D mesh generation in **TRELLIS.2** for attached reference cards.
5. Hot-swaps the proxy boxes with the generated `.GLB` models directly in the Three.js viewport.
6. Bakes the 3D layout render pass and queues the **LTX-2.5 IC-LoRA Layout-to-Render** two-stage pipeline.

---

## 2. Skeleton Motion & Pose Projection: Prepared 3D Scene → OpenPose → Video Generation

Used when your 3D scene already has a floor, a character prop (`HeroProp_3D`), an animated skeleton (e.g. Capoeira dance animation), and a virtual camera. You want to extract the skeletal animation from the active camera viewpoint and generate a photorealistic video of the character in a new visual style.

### 📝 Prompt for the Agent:
```text
you have a ready 3d scene on the Main Stage level with a floor cube, a 3d human model HeroProp_3D and a Skeleton with an existing camera. do not alter the scene keep the current camera. extract the skeletal pose animation from the skeleton and generate video of a man dancing on a beach next to a stone statue
```

### 💬 Immediate Execution Confirmation (if prompted):
```text
start
```
*(or `launch`, `go ahead`, `continue`, `run` — the agent understands single-word affirmatives and immediately dispatches execution).*

### ⚡ What the Agent Executes:
1. Flags the existing geometry as preserved (`scene_preserved: True`) — **does not wipe or reset** your 3D objects.
2. Projects the 3D skeleton bones through the active camera frustum into a frame-accurate 2D **OpenPose** sequence layer (`Pose from 3D`).
3. Formulates the director visual prompt:
   `Cinematic photorealistic shot of a man dancing energetically on a sandy beach next to a stone statue, golden sunset lighting, subtle rim light, ocean waves, 4k`.
4. Queues the LTX-2.5 video generation pipeline conditioned on the exported pose layer.

---

## 3. Performance Matchmoving & Actor Reskinning

Used when you have live-action footage of an actor and need to capture human body poses and camera motion, assemble a new 3D digital set, and transfer the original choreography onto a new character.

### 📝 Prompt for the Agent (Autonomous Mode):
```text
extract human pose animation and camera tracking from video, build a new 3d set with new environment, transfer the performance onto a new character keeping the exact camera and body motion, reskin character and run render
```

### 📝 Prompt for the Agent (Staged Review Mode with Checkpoints):
```text
run performance matchmove pipeline step by step with pauses for confirmation, visualising studio windows. First extract poses and camera track, then wait for my approval
```
*(On each stage completion, click **[ ✓ Approve & Continue ]** in the chat card or type `approved, continue to next stage`).*

### ⚡ What the Agent Executes:
1. Dispatches frame-by-frame pose extraction (`video_job: pose`).
2. Calculates camera solve using GLOMAP structure-from-motion (`video_job: track_camera`).
3. Switches to 3D and configures the new digital set environment.
4. Spawns an Unreal Engine standard mannequin skeleton and retargets animation.
5. Launches two-stage LTX-2.5 IC-LoRA Matchmove rendering.

---

## 4. Keyframe-Guided Video Generation (First & Last Frame / I2V)

Used when you want to animate a still image or create an interpolated cinematic transition between a start frame and an end frame.

### 📝 Single-Frame Image-to-Video (I2V):
```text
generate video from the selected frame: camera slowly pushes in on the protagonist, cinematic atmospheric lighting, subtle shadows, 4k high quality
```

### 📝 First & Last Frame Camera Travel (FLF2V):
```text
create video from first to last frame: smooth camera pan from left to right transitioning seamlessly between start frame and end frame
```
*(Attach both reference images with `@ref_1` and `@ref_2` or select them in the Storyboard library).*

---

## 5. VFX Compositing: Background Replacement Behind Subject

Used when you need to isolate an actor from live footage, generate a perspective-matched background plate using depth cues, and composite them with realistic edge blending.

### 📝 Prompt for the Agent:
```text
mask the character, extract depth map, generate a new rainy cyberpunk city background plate and composite with light wrap and color harmonization
```

### ⚡ What the Agent Executes:
1. Calls SAM 3.1 `text_mask` with prompt `"person"`.
2. Extracts depth map sequence via `depth` (Depth Anything v2).
3. Synthesizes perspective-consistent background motion with LTX IC-LoRA Union Control.
4. Executes `composite` with `harmonize: 0.4` and `light_wrap: 0.3` for realistic edge integration.

---

## 6. Clean Plate: Dynamic Object & Actor Removal

Used to produce an empty background plate with full camera motion preserved (for inserting CG titles, 3D models, or digital stunts).

### 📝 Prompt for the Agent:
```text
create a clean plate for this video, remove all people and actors from the frame and preserve the original camera movement
```

### ⚡ What the Agent Executes:
* Runs LTX-2.5 IC-LoRA with `clean-plate` weights directly on the camera plate, inpainting the occluded scenery while retaining camera shake and motion blur.

---

## 7. Concept Art Generation & Targeted Retouching (FLUX.3 Klein & Qwen Edit)

### 📝 Generate a New Concept Frame:
```text
paint a storyboard shot: cyberpunk detective in a trenchcoat standing in neon rain in front of a speakeasy bar, rear view, cinematic 35mm anamorphic optics, volumetric lighting
```

### 📝 Masked Subject Replacement (Face Swap / Wardrobe Edit):
```text
on the selected image replace the face inside the mask with the person from attached reference, preserve exact lighting, pores and anatomy
```

---

## 8. 3D Model Inspection & PBR Materials (Details Inspector)

Used to inspect generated 3D meshes on a turntable with live PBR maps (Albedo, Normal, Roughness, Wireframe).

### 📝 Prompt for the Agent:
```text
open 3d mesh details, show the details inspector and pbr texture maps
```

### ⚡ What the Agent Executes:
* Switches tab to `details`, displays the Three.js 360° interactive turntable, and exposes all PBR material texture channels.

---

## 9. Controlling Agent Autonomy Modes

You can dynamically toggle the execution mode in conversation:

| Mode | Command | Behavioral Change |
|---|---|---|
| **Full Autopilot** | `work autonomously, execute all processes without asking for confirmation` | Heavy tasks and video renders trigger automatically without stopping. |
| **Milestone Review** | `work step by step with pauses and approval for each stage` | Displays interactive approval checkpoints (`Awaiting Approval`) before proceeding. |
| **Studio Guide Lookup** | `how do I transfer skeleton pose into a video?` | Queries internal studio knowledge base (`ask_studio_agent`) and outputs exact instructions. |

---

## 💡 Quick Tips & Best Practices

1. **Short Affirmatives:** Once the agent drafts a plan and displays prompt cards, simple words like `start`, `go`, `launch`, `proceed`, or `run` instantly start ComfyUI GPU generation.
2. **Tagging Assets via `@`:** Reference media files directly in chat text (e.g., `@shot_01`, `@Cube_Girl`, `@Hero_Prop_3D`) to auto-attach them as visual conditioning.
3. **Preserving 3D Work:** When you already placed props or cameras manually in Three.js, include phrases like *«scene is already prepared»*, *«do not change the scene»*, or *«keep existing camera»*. The agent detects these guardrails and preserves your geometry intact.
