// ComfyUI-VFX-Suite: 3D Layout Editor Node with Dual Viewport, Outliner & Transform Inspector
import { app } from "/scripts/app.js";

let threePromise = null;
function loadDependencies() {
    if (threePromise) return threePromise;

    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/extensions/ComfyUI-VFX-Suite/css/layout_editor.css";
    document.head.appendChild(link);

    const loadScript = (src) => {
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = src;
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    };

    threePromise = loadScript("/extensions/ComfyUI-VFX-Suite/lib/three.min.js")
        .then(() => loadScript("/extensions/ComfyUI-VFX-Suite/lib/fflate.min.js"))
        .then(() => Promise.all([
            loadScript("/extensions/ComfyUI-VFX-Suite/lib/OrbitControls.js"),
            loadScript("/extensions/ComfyUI-VFX-Suite/lib/TransformControls.js"),
            loadScript("/extensions/ComfyUI-VFX-Suite/lib/OBJLoader.js"),
            loadScript("/extensions/ComfyUI-VFX-Suite/lib/GLTFLoader.js"),
            loadScript("/extensions/ComfyUI-VFX-Suite/lib/FBXLoader.js"),
        ]))
        .then(() => window.THREE);

    return threePromise;
}

app.registerExtension({
    name: "VFXSuite.3DLayoutEditor",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "LayoutEditor3D") {
            const setupLayoutNode = function (node) {
                if (!node || node._vfx_layout_initialized) return;
                node._vfx_layout_initialized = true;

                // Sleek compact node dimensions matching Plate Prep
                node.setSize([380, 260]);

                // Internal scene data
                let sceneData = [];
                try {
                    const jsonW = node.widgets && node.widgets.find(w => w.name === "layout_json");
                    if (jsonW && jsonW.value) sceneData = JSON.parse(jsonW.value);
                } catch (e) {
                    sceneData = [];
                }

                let totalFrames = 87;

                // Hide ugly code textarea and parameter clutter from the node face
                if (node.widgets) {
                    node.widgets.forEach(w => {
                        if (w.name === "workflow_gate") {
                            const validGates = ["Auto (Pause if Layout Empty)", "Interactive (Always Pause for Staging)", "Direct Render (Queue)"];
                            if (!w.value || !validGates.includes(w.value)) {
                                w.value = "Auto (Pause if Layout Empty)";
                            }
                        }
                        if (["layout_json", "render_mode", "camera_smoothing", "workflow_gate"].includes(w.name)) {
                            w.type = "hidden";
                            w.computeSize = () => [0, -4];
                        }
                    });
                }

                // Native LiteGraph button widget on top
                const hasBtn = node.widgets && node.widgets.some(w => w.name === "⛶ Open Fullscreen 3D Viewport");
                if (!hasBtn) {
                    node.addWidget("button", "⛶ Open Fullscreen 3D Viewport", null, () => {
                        openFullscreenViewport();
                    });
                }

                // Sync scene data frame count
                fetch("/vfx/scene_data").then(r => r.json()).then(data => {
                    if (data.camera && data.camera.matrices && data.camera.matrices.length > 0) {
                        totalFrames = data.camera.matrices.length;
                    }
                    if (app.graph) app.graph.setDirtyCanvas(true, true);
                }).catch(() => {});

                // Load initial plate cutout preview thumbnail
                node._vfx_preview_img = null;
                node.updatePreview = function () {
                    const t = Date.now();
                    const img = new Image();
                    img.onload = () => {
                        node._vfx_preview_img = img;
                        if (app.graph) app.graph.setDirtyCanvas(true, true);
                    };
                    img.src = `/vfx/actor_cutout/0?t=${t}`;
                };
                node.updatePreview();

                // Native LiteGraph Canvas Drawing (Guaranteed never to overflow node bounds)
                node.onDrawForeground = function (ctx) {
                    if (this.flags && this.flags.collapsed) return;

                    const w = this.size[0];
                    const h = this.size[1];
                    const cardX = 12;
                    const cardY = 56;
                    const cardW = w - 24;
                    const cardH = h - 68;

                    if (cardW <= 20 || cardH <= 20) return;

                    ctx.save();

                    // Card container border & background
                    ctx.fillStyle = "#0c0d13";
                    ctx.strokeStyle = "#25283c";
                    ctx.lineWidth = 1;
                    ctx.beginPath();
                    ctx.roundRect(cardX, cardY, cardW, cardH, [6]);
                    ctx.fill();
                    ctx.stroke();

                    // Clip to card
                    ctx.save();
                    ctx.beginPath();
                    ctx.roundRect(cardX, cardY, cardW, cardH, [6]);
                    ctx.clip();

                    // Draw Preview Image
                    if (this._vfx_preview_img && this._vfx_preview_img.complete && this._vfx_preview_img.naturalWidth > 0) {
                        ctx.drawImage(this._vfx_preview_img, cardX, cardY, cardW, cardH);
                    } else {
                        // Sleek perspective grid floor
                        ctx.fillStyle = "#090a10";
                        ctx.fillRect(cardX, cardY, cardW, cardH);
                        ctx.strokeStyle = "rgba(79, 128, 255, 0.16)";
                        ctx.lineWidth = 1;
                        const horizon = cardY + cardH * 0.42;
                        const vanishX = cardX + cardW * 0.5;

                        for (let i = -5; i <= 5; i++) {
                            ctx.beginPath();
                            ctx.moveTo(vanishX + i * (cardW * 0.10), horizon);
                            ctx.lineTo(vanishX + i * (cardW * 0.38), cardY + cardH);
                            ctx.stroke();
                        }
                        for (let j = 1; j <= 6; j++) {
                            const y = horizon + (cardH * 0.58) * Math.pow(j / 6, 2);
                            ctx.beginPath();
                            ctx.moveTo(cardX, y);
                            ctx.lineTo(cardX + cardW, y);
                            ctx.stroke();
                        }

                        // Perspective Camera icon
                        ctx.fillStyle = "#4f80ff";
                        ctx.font = "bold 22px sans-serif";
                        ctx.textAlign = "center";
                        ctx.textBaseline = "middle";
                        ctx.fillText("🎥", cardX + cardW / 2, cardY + cardH * 0.38);
                    }

                    // Bottom info vignette overlay
                    const grad = ctx.createLinearGradient(cardX, cardY + cardH - 46, cardX, cardY + cardH);
                    grad.addColorStop(0, "rgba(8, 9, 14, 0.0)");
                    grad.addColorStop(1, "rgba(8, 9, 14, 0.92)");
                    ctx.fillStyle = grad;
                    ctx.fillRect(cardX, cardY + cardH - 46, cardW, 46);

                    // Badge text
                    const objCount = sceneData ? sceneData.length : 0;
                    ctx.fillStyle = "#79a8ff";
                    ctx.font = "bold 11px -apple-system, BlinkMacSystemFont, monospace";
                    ctx.textAlign = "left";
                    ctx.textBaseline = "bottom";
                    ctx.fillText(`📐 3D STUDIO • ${totalFrames} КАДРОВ`, cardX + 10, cardY + cardH - 12);

                    ctx.fillStyle = "#aaa";
                    ctx.textAlign = "right";
                    ctx.fillText(`${objCount} ПРОКСИ`, cardX + cardW - 10, cardY + cardH - 12);

                    ctx.restore();
                    ctx.restore();
                };

                // Click on preview card directly opens Fullscreen Studio
                const origMouseDown = node.onMouseDown;
                node.onMouseDown = function (e, localPos) {
                    if (localPos && localPos[1] > 54 && localPos[1] < this.size[1] - 12) {
                        openFullscreenViewport();
                        return true;
                    }
                    return origMouseDown ? origMouseDown.apply(this, arguments) : undefined;
                };

                function syncJsonWidget() {
                    const jsonW = node.widgets && node.widgets.find(w => w.name === "layout_json");
                    if (jsonW) {
                        jsonW.value = JSON.stringify(sceneData, null, 2);
                    }
                }

                // --- 2. FULLSCREEN 3D VIEWPORT MODAL ---
                async function openFullscreenViewport() {
                    await loadDependencies();
                    const THREE = window.THREE;

                    let currentFrame = 0;
                    let isPlaying = false;
                    let autoKeyEnabled = true;
                    let selectedObject = null;
                    const selectedObjects = new Set();
                    let currentInterp = "bezier";
                    let isShotCamActive = false;

                    // Camera Aspect Ratio state (default 16:9, updated by scene_data)
                    let plateWidth = 1920;
                    let plateHeight = 1080;
                    let plateAspect = 16.0 / 9.0;

                    // Create Modal
                    const modal = document.createElement("div");
                    modal.className = "vfx-modal-backdrop";

                    // Header
                    const header = document.createElement("div");
                    header.className = "vfx-modal-header";
                    header.innerHTML = `
                        <div class="vfx-header-group">
                            <span class="vfx-header-title">📐 VFX 3D Layout Studio</span>
                            <span style="font-size: 11px; color: #79a8ff; font-family: monospace;" id="hdr-scene-status">Active Workspace • GLOMAP Solved</span>
                            <span class="vfx-render-badge-pill vfx-badge-draft" id="hdr-render-badge">○ Draft (Unbaked)</span>
                        </div>
                        <div class="vfx-header-group">
                            <button class="vfx-btn" id="btn-undo-top" style="opacity: 0.5; cursor: default;" title="Undo (Ctrl+Z) • До 10 шагов истории">↩ Undo</button>
                            <button class="vfx-btn vfx-btn-render-primary" id="btn-render-layout-top" title="Render layout inside node with live progress">🎬 Render Layout</button>
                            <button class="vfx-btn" id="btn-queue-render-top" style="background: #2e7d32; color: #fff; font-weight: bold; border: 1px solid #4caf50;" title="Bake layout and queue ComfyUI generation">🚀 Proceed / Run Pipeline</button>
                            <button class="vfx-btn vfx-btn-danger" id="btn-close-modal">✕ Close</button>
                        </div>
                    `;
                    modal.appendChild(header);

                    // Main Workspace (Full-height 3D Left + Control/Monitor Right)
                    const workspace = document.createElement("div");
                    workspace.className = "vfx-layout-workspace";
                    modal.appendChild(workspace);

                    // Full-viewport Canvas Host
                    const canvasHost = document.createElement("div");
                    canvasHost.className = "vfx-viewport-canvas-host";
                    workspace.appendChild(canvasHost);

                    // Left Viewport Overlay (HUD, Badges)
                    const leftPaneOverlay = document.createElement("div");
                    leftPaneOverlay.className = "vfx-viewport-left-overlay";
                    leftPaneOverlay.innerHTML = `
                        <div class="vfx-viewport-badge-left">🌐 3D PERSPECTIVE (Orbit / FlyCam)</div>
                        <div class="vfx-3pt-picker-bar" id="vfx-3pt-picker-bar" style="display: none;">
                            <div class="vfx-3pt-status" id="vfx-3pt-status">🎯 Click 3 points in Point Cloud (0 / 3)</div>
                            <div class="vfx-3pt-actions" id="vfx-3pt-actions" style="display: none;">
                                <button class="vfx-btn vfx-btn-primary" id="btn-3pt-confirm" title="Create permanent Reference Plane in scene">📐 Create Plane</button>
                                <button class="vfx-btn" id="btn-3pt-flip" title="Flip plane normal (180°)">🔄 Flip Normal</button>
                                <button class="vfx-btn" id="btn-3pt-align-grid" title="Align Scene Ground Grid to this Plane">🎯 Align Grid</button>
                                <button class="vfx-btn" id="btn-3pt-repick" title="Clear points and pick again">↺ Re-pick</button>
                            </div>
                            <button class="vfx-btn" id="btn-3pt-cancel" style="padding: 2px 8px; font-size: 11px;" title="Exit 3-Point tool (Esc)">✕ Close</button>
                        </div>
                        <div class="vfx-viewport-overlay">
                            <div class="vfx-overlay-badge" id="hud-status">Mode: Select | Space: World | Left: 3D Perspective</div>
                            <div class="vfx-overlay-badge">HOTKEYS: Shift+Click (Multi-select) | Q/W/E/R (Tools) | ~ (World/Local) | C (Cam) | ←/→ (Step) | [/] (Key Jump) | Space (Play) | K (Key) | Del</div>
                        </div>
                    `;
                    workspace.appendChild(leftPaneOverlay);

                    // Right Column (Controls Top + Camera Monitor Bottom)
                    const splitterV = document.createElement("div");
                    splitterV.className = "vfx-splitter-v";
                    splitterV.id = "splitter-studio-v";
                    splitterV.title = "Drag horizontally to resize 3D viewport";
                    workspace.appendChild(splitterV);

                    const studioRightCol = document.createElement("div");
                    studioRightCol.className = "vfx-studio-right-column";
                    studioRightCol.innerHTML = `
                        <!-- Top Half: Controls, Outliner, Transform Inspector, Environment, Pipeline -->
                        <div class="vfx-controls-top-pane" id="controls-top-pane">
                            <!-- 1. Tools & Primitives -->
                            <div class="vfx-sidebar-header" id="hdr-tools">
                                <span><span class="vfx-collapse-arrow">▼</span>🛠️ Tools & Primitives</span>
                            </div>
                            <div class="vfx-section-content" id="body-tools">
                                <div class="vfx-toolbar-grid">
                                    <button class="vfx-btn" id="btn-add-cube" title="Add Box / Куб">+ Box</button>
                                    <button class="vfx-btn" id="btn-add-sphere" title="Add Sphere / Шар">+ Sphere</button>
                                    <button class="vfx-btn" id="btn-add-cylinder" title="Add Cylinder / Цилиндр">+ Cylinder</button>
                                    <button class="vfx-btn" id="btn-add-cone" title="Add Cone / Конус">+ Cone</button>
                                </div>
                                <div class="vfx-toolbar-grid" style="margin-top: 3px;">
                                    <button class="vfx-btn" id="btn-add-torus" title="Add Torus / Тор">+ Torus</button>
                                    <button class="vfx-btn" id="btn-add-plane" title="Add Floor Plane / Плоскость">+ Floor</button>
                                    <button class="vfx-btn" id="btn-import-mesh" style="grid-column: span 2;" title="Import 3D Model (GLB/GLTF/OBJ/FBX)">📁 Import 3D</button>
                                    <input type="file" id="file-mesh-input" accept=".glb,.gltf,.obj,.fbx" style="display:none;" />
                                </div>
                                <div style="margin-top: 4px; padding: 0 12px; display: flex; flex-direction: column; gap: 4px;">
                                    <button class="vfx-btn" id="btn-add-markers" style="background: #3e2723; border: 1px solid #ff9800; color: #ffb74d; font-size: 11px; padding: 4px 8px; font-weight: 500;" title="Add 3D Calibration Markers Array (51x51x10 spheres for LTX IC-LoRA)">
                                        📍 3D Markers Grid
                                    </button>
                                    <button class="vfx-btn" id="btn-tool-3pt-plane" style="flex: 1; background: #132238; border: 1px solid #1e88e5; color: #64b5f6; font-size: 11px; padding: 4px 8px; font-weight: 500;" title="Pick 3 points in Point Cloud to preview and create aligned Reference Plane">
                                        🎯 3-Point Plane Tool
                                    </button>
                                </div>
                                <div class="vfx-toolbar-grid" style="margin-top: 4px;">
                                    <button class="vfx-btn" id="btn-tool-select" title="Select (Q)">Select (Q)</button>
                                    <button class="vfx-btn vfx-btn-active" id="btn-tool-translate" title="Move (W)">Move (W)</button>
                                    <button class="vfx-btn" id="btn-tool-rotate" title="Rotate (E)">Rotate (E)</button>
                                    <button class="vfx-btn" id="btn-tool-scale" title="Scale (R)">Scale (R)</button>
                                </div>
                                <div style="padding: 4px 12px; display: flex; gap: 4px;">
                                    <button class="vfx-btn" id="btn-coord-space" style="flex: 1;" title="Space: World/Local (~)">Space: World (~)</button>
                                    <button class="vfx-btn" id="btn-cam-view" style="flex: 1;" title="Toggle Camera View on Left (C)">🎥 Shot Cam (C)</button>
                                </div>
                            </div>

                            <!-- 2. Scene Outliner -->
                            <div class="vfx-sidebar-header" id="hdr-outliner">
                                <span><span class="vfx-collapse-arrow">▼</span>📋 Scene Outliner</span>
                                <span id="outliner-count" style="font-size: 11px; color: #888;">0 objects</span>
                            </div>
                            <div class="vfx-section-content" id="body-outliner">
                                <div class="vfx-outliner-list" id="outliner-list"></div>
                            </div>

                            <!-- 3. Transform Inspector -->
                            <div class="vfx-sidebar-header" id="hdr-inspector">
                                <span><span class="vfx-collapse-arrow">▼</span>📐 Transform Inspector</span>
                                <span id="inspector-target" style="font-size: 11px; color: #ffb300;">None</span>
                            </div>
                            <div class="vfx-section-content" id="body-inspector">
                                <div class="vfx-transform-section" id="transform-inspector">
                                    <div style="display: flex; gap: 4px; margin-bottom: 6px;">
                                        <button class="vfx-btn" id="btn-duplicate-obj" style="flex: 1; font-size: 11px; padding: 3px 6px;" title="Duplicate Object (Ctrl+D)">📋 Duplicate</button>
                                        <button class="vfx-btn" id="btn-copy-xform" style="flex: 1; font-size: 11px; padding: 3px 6px;" title="Copy Transforms (Ctrl+C)">Copy Xform</button>
                                        <button class="vfx-btn" id="btn-paste-xform" style="flex: 1; font-size: 11px; padding: 3px 6px;" title="Paste Transforms (Ctrl+V)">Paste Xform</button>
                                        <button class="vfx-btn" id="btn-reset-xform" style="font-size: 11px; padding: 3px 6px;" title="Reset to Origin (Pos=0, Rot=0, Scale=1)">↺ Reset</button>
                                    </div>
                                    <div class="vfx-transform-row">
                                        <span class="vfx-transform-label">Pos (m):</span>
                                        <div class="vfx-num-input-group">
                                            <span class="vfx-axis-tag vfx-axis-x">X</span>
                                            <input type="number" step="0.05" class="vfx-num-input" id="num-pos-x" value="0.00" />
                                            <span class="vfx-axis-tag vfx-axis-y">Y</span>
                                            <input type="number" step="0.05" class="vfx-num-input" id="num-pos-y" value="0.00" />
                                            <span class="vfx-axis-tag vfx-axis-z">Z</span>
                                            <input type="number" step="0.05" class="vfx-num-input" id="num-pos-z" value="0.00" />
                                        </div>
                                    </div>
                                    <div class="vfx-transform-row">
                                        <span class="vfx-transform-label">Rot (°):</span>
                                        <div class="vfx-num-input-group">
                                            <span class="vfx-axis-tag vfx-axis-x">X</span>
                                            <input type="number" step="1.0" class="vfx-num-input" id="num-rot-x" value="0.0" />
                                            <span class="vfx-axis-tag vfx-axis-y">Y</span>
                                            <input type="number" step="1.0" class="vfx-num-input" id="num-rot-y" value="0.0" />
                                            <span class="vfx-axis-tag vfx-axis-z">Z</span>
                                            <input type="number" step="1.0" class="vfx-num-input" id="num-rot-z" value="0.0" />
                                        </div>
                                    </div>
                                    <div class="vfx-transform-row">
                                        <span class="vfx-transform-label">Scale:</span>
                                        <div class="vfx-num-input-group">
                                            <span class="vfx-axis-tag vfx-axis-x">X</span>
                                            <input type="number" step="0.05" class="vfx-num-input" id="num-scale-x" value="1.00" />
                                            <span class="vfx-axis-tag vfx-axis-y">Y</span>
                                            <input type="number" step="0.05" class="vfx-num-input" id="num-scale-y" value="1.00" />
                                            <span class="vfx-axis-tag vfx-axis-z">Z</span>
                                            <input type="number" step="0.05" class="vfx-num-input" id="num-scale-z" value="1.00" />
                                        </div>
                                    </div>
                                    <div class="vfx-transform-row" style="margin-top: 4px;">
                                        <span class="vfx-transform-label">Obj Color:</span>
                                        <input type="color" id="sidebar-color-picker" value="#cccccc" style="width: 22px; height: 22px; border: 1px solid #444; border-radius: 4px; background: none; cursor: pointer; padding: 0;" title="Selected Object Color" />
                                        <button class="vfx-btn" id="sidebar-swatch-clay" style="padding: 2px 6px; font-size: 10px; background: #cccccc; color: #111;">Clay</button>
                                        <button class="vfx-btn" id="sidebar-swatch-red" style="padding: 2px 7px; background: #e53935; border: none;"></button>
                                        <button class="vfx-btn" id="sidebar-swatch-green" style="padding: 2px 7px; background: #43a047; border: none;"></button>
                                        <button class="vfx-btn" id="sidebar-swatch-blue" style="padding: 2px 7px; background: #1e88e5; border: none;"></button>
                                        <button class="vfx-btn" id="sidebar-swatch-gold" style="padding: 2px 7px; background: #fdd835; border: none;"></button>
                                    </div>
                                    <div class="vfx-transform-row" style="margin-top: 4px;">
                                        <span class="vfx-transform-label">Sky Color:</span>
                                        <input type="color" id="sidebar-sky-picker" value="#0a0a0d" style="width: 22px; height: 22px; border: 1px solid #444; border-radius: 4px; background: none; cursor: pointer; padding: 0;" title="Sky / Environment Background Color" />
                                        <button class="vfx-btn" id="sidebar-sky-dark" style="padding: 2px 6px; font-size: 10px; background: #0a0a0d; color: #fff; border: 1px solid #333;" title="Cinema Dark (#0a0a0d)">Dark</button>
                                        <button class="vfx-btn" id="sidebar-sky-slate" style="padding: 2px 7px; background: #181b24; border: none;" title="Slate Studio (#181b24)"></button>
                                        <button class="vfx-btn" id="sidebar-sky-blue" style="padding: 2px 7px; background: #1e3a5f; border: none;" title="Deep Blue Sky (#1e3a5f)"></button>
                                        <button class="vfx-btn" id="sidebar-sky-day" style="padding: 2px 7px; background: #4a75a0; border: none;" title="Daylight Sky (#4a75a0)"></button>
                                        <button class="vfx-btn" id="sidebar-sky-clay" style="padding: 2px 7px; background: #4a4542; border: none;" title="Warm Clay (#4a4542)"></button>
                                    </div>
                                </div>
                            </div>

                            <!-- 4. Environment & Stage Background -->
                            <div class="vfx-sidebar-header" id="hdr-env">
                                <span><span class="vfx-collapse-arrow">▼</span>🎨 Environment & Stage</span>
                            </div>
                            <div class="vfx-section-content" id="body-env" style="padding: 8px 12px; display: flex; flex-direction: column; gap: 8px;">
                                <div style="display: flex; align-items: center; justify-content: space-between;">
                                    <span style="font-size: 11px; color: #aaa;">Stage Color:</span>
                                    <div style="display: flex; align-items: center; gap: 5px;">
                                        <input type="color" id="env-bg-color-picker" value="#0a0a0d" style="width: 22px; height: 22px; border: 1px solid #444; border-radius: 4px; background: none; cursor: pointer; padding: 0;" title="Custom Environment Color" />
                                        <button class="vfx-env-swatch" id="env-swatch-dark" style="background: #0a0a0d;" title="Cinema Dark (#0a0a0d)"></button>
                                        <button class="vfx-env-swatch" id="env-swatch-slate" style="background: #181b24;" title="Slate Studio (#181b24)"></button>
                                        <button class="vfx-env-swatch" id="env-swatch-gray" style="background: #2d303d;" title="Medium Gray (#2d303d)"></button>
                                        <button class="vfx-env-swatch" id="env-swatch-clay" style="background: #4a4542;" title="Warm Clay (#4a4542)"></button>
                                        <button class="vfx-env-swatch" id="env-swatch-light" style="background: #727788;" title="Light Gray (#727788)"></button>
                                    </div>
                                </div>
                                <div style="display: flex; align-items: center; justify-content: space-between; font-size: 11px;">
                                    <span style="color: #aaa;">Grid Contrast:</span>
                                    <button class="vfx-btn" id="btn-toggle-grid" style="font-size: 10px; padding: 2px 8px;">Grid: Visible</button>
                                </div>
                                <div style="display: flex; align-items: center; justify-content: space-between; font-size: 11px;">
                                    <span style="color: #aaa;">Cam Inertia:</span>
                                    <button class="vfx-btn" id="btn-toggle-damping" style="font-size: 10px; padding: 2px 8px;" title="Camera inertia/damping when stopping">Inertia: OFF (Fixed)</button>
                                </div>
                                <div style="display: flex; align-items: center; justify-content: space-between; font-size: 11px;">
                                    <span style="color: #aaa;">Cloud Pts:</span>
                                    <div style="display: flex; align-items: center; gap: 4px;">
                                        <span style="color: #888; font-size: 10px;">Size:</span>
                                        <input type="range" id="slider-cloud-size" min="0.01" max="0.35" step="0.01" value="0.08" style="width: 48px;" title="Point cloud particle size" />
                                        <span id="lbl-cloud-size" style="font-family: monospace; font-size: 10px; color: #4f80ff; min-width: 24px;">0.08</span>
                                        <span style="color: #888; font-size: 10px; margin-left: 2px;">Alpha:</span>
                                        <input type="range" id="slider-cloud-alpha" min="0.0" max="1.0" step="0.05" value="0.90" style="width: 48px;" title="Point cloud transparency" />
                                        <span id="lbl-cloud-alpha" style="font-family: monospace; font-size: 10px; color: #4f80ff; min-width: 26px;">90%</span>
                                    </div>
                                </div>
                            </div>

                            <!-- 5. Actor Cutout Billboard Controls -->
                            <div class="vfx-sidebar-header" id="hdr-actor">
                                <span><span class="vfx-collapse-arrow">▼</span>👤 Actor Cutout</span>
                            </div>
                            <div class="vfx-section-content" id="body-actor" style="padding: 8px 12px; display: flex; flex-direction: column; gap: 6px;">
                                <div class="vfx-panel-row">
                                    <button class="vfx-btn vfx-btn-active" id="btn-toggle-actor" style="font-size: 11px; padding: 3px 8px;">👤 Actor: ON</button>
                                    <div style="display: flex; align-items: center; gap: 4px; font-size: 11px; color: #aaa;">
                                        <span>Dist:</span>
                                        <input type="range" id="slider-actor-dist" min="0.5" max="15.0" step="0.1" value="3.0" style="width: 50px;" title="Actor distance in 3D (m)" />
                                        <span>Alpha:</span>
                                        <input type="range" id="slider-actor-alpha" min="0.1" max="1.0" step="0.05" value="0.85" style="width: 45px;" title="Actor opacity" />
                                    </div>
                                </div>
                            </div>

                            <!-- 6. In-Node Render & Pipeline Execution -->
                            <div class="vfx-sidebar-header" id="hdr-pipeline">
                                <span><span class="vfx-collapse-arrow">▼</span>🎬 Render & Pipeline Execution</span>
                            </div>
                            <div class="vfx-section-content" id="body-pipeline" style="padding: 8px 12px; display: flex; flex-direction: column; gap: 6px;">
                                <div class="vfx-panel-row">
                                    <span style="color: #aaa; font-size: 11px;">Render Mode:</span>
                                    <select class="vfx-interp-select" id="sidebar-render-mode" style="font-size: 11px; padding: 3px 6px;">
                                        <option value="Clay (Lambertian Studio)">Clay Studio</option>
                                        <option value="Semantic Color (Color Blocking)">Semantic Color</option>
                                        <option value="Flat Color Segmentation">Flat Color</option>
                                        <option value="Depth Map">Depth Map</option>
                                        <option value="Surface Normals">Surface Normals</option>
                                        <option value="Layout Mask">Layout Mask</option>
                                    </select>
                                </div>
                                <div class="vfx-panel-row">
                                    <span style="color: #aaa; font-size: 11px;">Resolution:</span>
                                    <select class="vfx-interp-select" id="sidebar-render-res" style="font-size: 11px; padding: 3px 6px;">
                                        <option value="1.0">Native Match (100%)</option>
                                        <option value="0.5">Half Scale (50% Draft)</option>
                                        <option value="720p">720p HD</option>
                                    </select>
                                </div>
                                <div class="vfx-panel-row">
                                    <span style="color: #aaa; font-size: 11px;">Workflow Gate:</span>
                                    <select class="vfx-interp-select" id="sidebar-workflow-gate" style="font-size: 11px; padding: 3px 6px;">
                                        <option value="Auto (Pause if Layout Empty)">Auto (Pause if Empty)</option>
                                        <option value="Interactive (Always Pause for Staging)">Always Pause</option>
                                        <option value="Direct Render (Queue)">Direct Render</option>
                                    </select>
                                </div>
                                <div class="vfx-panel-row">
                                    <span style="color: #aaa; font-size: 11px;">Cam Smooth:</span>
                                    <input type="range" id="sidebar-cam-smoothing" min="0" max="1" step="0.05" value="0" style="width: 80px;" />
                                </div>

                                <button class="vfx-btn vfx-btn-render-primary" id="btn-start-layout-render" style="margin-top: 4px; padding: 7px 10px; font-size: 12px; width: 100%;">🎬 Render Layout Inside Node</button>

                                <!-- Render Progress Card -->
                                <div class="vfx-render-progress-card" id="render-progress-card" style="display: none;">
                                    <div class="vfx-render-progress-meta">
                                        <span id="render-progress-label" style="color: #ff9800; font-weight: bold;">Rendering...</span>
                                        <button class="vfx-btn vfx-btn-danger" id="btn-cancel-render" style="font-size: 9px; padding: 1px 5px;">Cancel</button>
                                    </div>
                                    <div class="vfx-render-progress-track">
                                        <div class="vfx-render-progress-fill" id="render-progress-fill"></div>
                                    </div>
                                </div>

                                <!-- Post-Render Review Card -->
                                <div id="render-review-card" style="display: none; flex-direction: column; gap: 6px; margin-top: 4px; background: rgba(0, 229, 255, 0.05); border: 1px solid rgba(0, 229, 255, 0.25); border-radius: 6px; padding: 8px 10px;">
                                    <div style="font-size: 11px; color: #4caf50; font-weight: bold; display: flex; align-items: center; gap: 4px;">
                                        <span>✅ Render Complete & Baked</span>
                                    </div>
                                    <div style="font-size: 10px; color: #aaa;">Review playback in camera monitor before continuing pipeline:</div>
                                    <div style="display: flex; gap: 4px;">
                                        <button class="vfx-btn" id="btn-review-toggle-playback" style="flex: 1; font-size: 11px; padding: 4px 6px;">▶ Play Baked</button>
                                        <button class="vfx-btn" id="btn-re-render" style="font-size: 11px; padding: 4px 6px;" title="Re-render layout sequence">↺ Re-render</button>
                                    </div>
                                </div>
                            </div>

                            <div style="padding: 10px 12px; margin-top: auto; display: flex; flex-direction: column; gap: 6px;">
                                <button class="vfx-btn" id="btn-queue-render" style="background: #2e7d32; color: #fff; font-weight: bold; border: 1px solid #4caf50; padding: 8px 12px;" title="Bake layout and proceed with ComfyUI pipeline">🚀 Proceed / Run Pipeline</button>
                            </div>
                        </div>

                        <!-- Horizontal Splitter between Controls and Camera Monitor -->
                        <div class="vfx-splitter-h" id="splitter-studio-h" title="Drag vertically to resize camera monitor"></div>

                        <!-- Bottom Half: Camera Monitor with Film Gate & Matte Bars (Каше) -->
                        <div class="vfx-camera-monitor-pane" id="pane-cam-monitor">
                            <div class="vfx-monitor-header">
                                <span class="vfx-monitor-title">🎥 Shot Camera Monitor</span>
                                <div style="display: flex; align-items: center; gap: 6px;">
                                    <div class="vfx-monitor-pill-group">
                                        <button class="vfx-monitor-pill-btn vfx-pill-active" id="btn-mon-mode-3d" title="Interactive 3D Staging View">3D View</button>
                                        <button class="vfx-monitor-pill-btn" id="btn-mon-mode-baked" title="Baked Render Review Playback">🎬 Baked View</button>
                                    </div>
                                    <button class="vfx-icon-btn" id="btn-frame-shot" style="font-size: 10px; color: #00e5ff; padding: 2px 6px; border: 1px solid rgba(0,229,255,0.4);" title="Frame Selected / Scene in View (F)">🔍 Frame (F)</button>
                                    <span class="vfx-monitor-badge" id="monitor-aspect-badge">16:9 • FILM GATE</span>
                                </div>
                            </div>
                            <div class="vfx-monitor-frame-container" id="monitor-frame-box">
                                <div class="vfx-film-gate-box" id="film-gate-box">
                                    <img id="baked-plate-underlay" class="vfx-plate-compare-layer" style="display: none; opacity: 0;" />
                                    <img id="baked-render-preview-img" class="vfx-baked-render-layer" style="display: none;" />
                                    <div class="vfx-gate-corner vfx-corner-tl"></div>
                                    <div class="vfx-gate-corner vfx-corner-tr"></div>
                                    <div class="vfx-gate-corner vfx-corner-bl"></div>
                                    <div class="vfx-gate-corner vfx-corner-br"></div>
                                    <div class="vfx-gate-safe"></div>
                                    <div class="vfx-gate-crosshair"></div>
                                </div>
                            </div>
                            <div class="vfx-monitor-footer-bar">
                                <div style="display: flex; align-items: center; gap: 6px;">
                                    <span>🔒 Safe Area: 90%</span>
                                    <div id="monitor-compare-wrap" style="display: none; align-items: center; gap: 4px; margin-left: 8px;">
                                        <span style="color: #aaa;">Plate Blend:</span>
                                        <input type="range" id="slider-baked-blend" min="0" max="1" step="0.05" value="0" style="width: 55px;" title="Blend plate cutout with rendered layout" />
                                        <span id="lbl-baked-blend" style="color: #00e5ff; min-width: 26px;">0%</span>
                                    </div>
                                </div>
                                <span id="monitor-lens-info">Focal: 35mm • Sensor: 36×24mm</span>
                            </div>
                        </div>
                    `;
                    workspace.appendChild(studioRightCol);

                    // Footer / Timeline
                    const footer = document.createElement("div");
                    footer.className = "vfx-modal-footer";
                    footer.innerHTML = `
                        <div class="vfx-timeline-track-container">
                            <input type="range" class="vfx-timeline-slider" id="timeline-slider" min="0" max="${totalFrames - 1}" value="${currentFrame}" />
                            <div class="vfx-keyframe-markers" id="key-markers-track"></div>
                        </div>
                        <div class="vfx-timeline-controls">
                            <div class="vfx-key-group">
                                <button class="vfx-btn" id="btn-first-frame" title="First Frame (Home)" style="padding: 3px 6px;">|◀</button>
                                <button class="vfx-btn" id="btn-prev-key" title="Previous Keyframe ([)" style="padding: 3px 8px; font-weight: 500;">⏮ Key</button>
                                <button class="vfx-btn" id="btn-prev-frame" title="Step -1 Frame (←)" style="padding: 3px 8px;">⏴</button>
                                <button class="vfx-btn" id="btn-play-pause" title="Play / Pause (Space)" style="min-width: 72px;">▶ Play</button>
                                <button class="vfx-btn" id="btn-next-frame" title="Step +1 Frame (→)" style="padding: 3px 8px;">⏵</button>
                                <button class="vfx-btn" id="btn-next-key" title="Next Keyframe (])" style="padding: 3px 8px; font-weight: 500;">Key ⏭</button>
                                <button class="vfx-btn" id="btn-last-frame" title="Last Frame (End)" style="padding: 3px 6px;">▶|</button>
                                <span style="font-family: monospace; font-size: 13px; color: #4f80ff; margin-left: 6px;" id="hud-frame-label">Frame: [ ${String(currentFrame).padStart(3, '0')} / ${totalFrames} ]</span>
                            </div>
                            <div class="vfx-key-group">
                                <button class="vfx-btn vfx-btn-active" id="btn-autokey">● Auto-Key: ON</button>
                                <button class="vfx-btn" id="btn-set-key">+ Key (K)</button>
                                <button class="vfx-btn" id="btn-del-key">- Del Key</button>
                                <select class="vfx-interp-select" id="sel-interp">
                                    <option value="bezier">Bezier (Smooth)</option>
                                    <option value="linear">Linear</option>
                                    <option value="step">Step (Constant)</option>
                                </select>
                                <button class="vfx-btn" id="btn-ease-in">Ease In</button>
                                <button class="vfx-btn" id="btn-ease-out">Ease Out</button>
                                <button class="vfx-btn" id="btn-ease-inout">Ease In-Out</button>
                            </div>
                        </div>
                    `;
                    modal.appendChild(footer);
                    document.body.appendChild(modal);

                    const filmGateBox = modal.querySelector("#film-gate-box");
                    const monitorFrameBox = modal.querySelector("#monitor-frame-box");

                    // --- 3. THREE.JS SCENE SETUP ---
                    const width = canvasHost.clientWidth || window.innerWidth;
                    const height = canvasHost.clientHeight || (window.innerHeight - 100);

                    const scene = new THREE.Scene();
                    scene.background = new THREE.Color(0x0a0a0d);

                    // Grid floor (Z=0 plane)
                    const gridHelper = new THREE.GridHelper(50, 50, 0x4f80ff, 0x222230);
                    gridHelper.rotation.x = Math.PI / 2;
                    scene.add(gridHelper);

                    // Lighting
                    const ambientLight = new THREE.AmbientLight(0xffffff, 0.45);
                    scene.add(ambientLight);

                    const keyLight = new THREE.DirectionalLight(0xffffff, 0.85);
                    keyLight.position.set(5, 10, 8);
                    scene.add(keyLight);

                    const fillLight = new THREE.DirectionalLight(0x8899bb, 0.35);
                    fillLight.position.set(-8, -4, 5);
                    scene.add(fillLight);

                    // Cameras
                    const flyCamera = new THREE.PerspectiveCamera(50, width / height, 0.1, 1000);
                    flyCamera.position.set(0, -6, 3);
                    flyCamera.up.set(0, 0, 1);
                    flyCamera.lookAt(0, 0, 0);

                    const shotCamera = new THREE.PerspectiveCamera(50, plateAspect, 0.1, 1000);
                    shotCamera.position.set(0, -5, 2);
                    shotCamera.up.set(0, 0, 1);
                    shotCamera.lookAt(0, 0, 0.5);

                    const camFrustumHelper = new THREE.CameraHelper(shotCamera);
                    scene.add(camFrustumHelper);

                    // 3D Camera proxy model for Perspective view
                    const camGroup = new THREE.Group();
                    const camBoxGeom = new THREE.BoxGeometry(0.24, 0.16, 0.32);
                    const camMat = new THREE.MeshStandardMaterial({ color: 0x222638, roughness: 0.35, metalness: 0.7 });
                    const camBox = new THREE.Mesh(camBoxGeom, camMat);
                    const lensGeom = new THREE.ConeGeometry(0.12, 0.22, 16);
                    lensGeom.rotateX(-Math.PI / 2);
                    lensGeom.translate(0, 0, -0.22);
                    const lensMat = new THREE.MeshStandardMaterial({ color: 0x00f0ff, roughness: 0.2, metalness: 0.85 });
                    const camLens = new THREE.Mesh(lensGeom, lensMat);
                    camGroup.add(camBox);
                    camGroup.add(camLens);
                    scene.add(camGroup);
                    const camBodyMesh = camGroup;

                    let activeCamera = flyCamera;

                    // --- SCENE DATA: POINT CLOUD & SOLVED CAMERA ---
                    let cameraMatrices = [];
                    const textureLoader = new THREE.TextureLoader();

                    // Camera Trajectory Path Line
                    let cameraPathLine = null;
                    function buildCameraTrajectoryPath() {
                        if (cameraPathLine) {
                            scene.remove(cameraPathLine);
                            cameraPathLine.geometry.dispose();
                            cameraPathLine = null;
                        }
                        if (!cameraMatrices || cameraMatrices.length < 2) return;

                        const pathPts = [];
                        for (let i = 0; i < cameraMatrices.length; i++) {
                            const raw = cameraMatrices[i];
                            pathPts.push(new THREE.Vector3(raw[0][3], raw[1][3], raw[2][3]));
                        }

                        const pathGeom = new THREE.BufferGeometry().setFromPoints(pathPts);
                        const pathMat = new THREE.LineBasicMaterial({ color: 0x00f0ff, linewidth: 2 });
                        cameraPathLine = new THREE.Line(pathGeom, pathMat);

                        const keyPtsGeom = new THREE.BufferGeometry().setFromPoints(pathPts);
                        const keyPtsMat = new THREE.PointsMaterial({ color: 0xffaa00, size: 0.08 });
                        const keyPtsMesh = new THREE.Points(keyPtsGeom, keyPtsMat);
                        cameraPathLine.add(keyPtsMesh);

                        scene.add(cameraPathLine);
                    }

                    // Actor Cutout Billboard state
                    let isActorProjectionVisible = true;
                    let actorDistance = 3.0;
                    let actorOpacity = 0.85;

                    const actorMaterial = new THREE.MeshBasicMaterial({
                        transparent: true,
                        opacity: actorOpacity,
                        depthWrite: false,
                        side: THREE.DoubleSide
                    });
                    const actorPlaneGeom = new THREE.PlaneGeometry(1, 1);
                    const actorBillboard = new THREE.Mesh(actorPlaneGeom, actorMaterial);
                    actorBillboard.renderOrder = 999;
                    scene.add(actorBillboard);

                    function updateActorTexture(frameIdx) {
                        if (!isActorProjectionVisible) {
                            actorBillboard.visible = false;
                            return;
                        }
                        actorBillboard.visible = true;
                        textureLoader.load(`/vfx/actor_cutout/${frameIdx}`, (tex) => {
                            tex.minFilter = THREE.LinearFilter;
                            tex.magFilter = THREE.LinearFilter;
                            actorMaterial.map = tex;
                            actorMaterial.needsUpdate = true;
                        }, undefined, () => {});
                    }

                    function updateActorBillboardTransform() {
                        if (!isActorProjectionVisible) return;
                        const cam = shotCamera;
                        const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(cam.quaternion);
                        actorBillboard.position.copy(cam.position).addScaledVector(forward, actorDistance);
                        actorBillboard.quaternion.copy(cam.quaternion);

                        const vFovRad = THREE.MathUtils.degToRad(cam.fov);
                        const planeHeight = 2.0 * actorDistance * Math.tan(vFovRad / 2.0);
                        const planeWidth = planeHeight * plateAspect;
                        actorBillboard.scale.set(planeWidth, planeHeight, 1);
                    }

                    function updateShotCameraPose(frameIdx) {
                        if (cameraMatrices.length > 0) {
                            const idx = Math.min(frameIdx, cameraMatrices.length - 1);
                            const rawMat = cameraMatrices[idx];
                            const flat = [].concat(...rawMat);
                            const mat4 = new THREE.Matrix4().set(...flat);
                            mat4.decompose(shotCamera.position, shotCamera.quaternion, shotCamera.scale);
                            shotCamera.updateMatrixWorld(true);
                            camFrustumHelper.update();
                            if (camBodyMesh) {
                                camBodyMesh.position.copy(shotCamera.position);
                                camBodyMesh.quaternion.copy(shotCamera.quaternion);
                            }
                        }
                    }

                    // Load GLOMAP Point Cloud and Solved Camera from Server
                    let cloudMesh = null;
                    let pointCloudMaterial = null;
                    let cloudPointSize = 0.08;
                    let cloudPointAlpha = 0.90;
                    try {
                        fetch("/vfx/scene_data").then(r => r.json()).then(data => {
                            if (data.points && data.points.length > 0) {
                                const pts = data.points;
                                const cols = data.colors || [];
                                const ptGeom = new THREE.BufferGeometry();
                                const posArr = new Float32Array(pts.length * 3);
                                const colArr = new Float32Array(pts.length * 3);

                                for (let i = 0; i < pts.length; i++) {
                                    posArr[i * 3] = pts[i][0];
                                    posArr[i * 3 + 1] = pts[i][1];
                                    posArr[i * 3 + 2] = pts[i][2];

                                    const c = cols[i];
                                    if (c && (c[0] > 0.05 || c[1] > 0.05 || c[2] > 0.05)) {
                                        const mult = (c[0] > 1.0 || c[1] > 1.0 || c[2] > 1.0) ? (1.0 / 255.0) : 1.0;
                                        colArr[i * 3] = c[0] * mult;
                                        colArr[i * 3 + 1] = c[1] * mult;
                                        colArr[i * 3 + 2] = c[2] * mult;
                                    } else {
                                        const normZ = Math.max(0, Math.min(1, (pts[i][2] + 1.0) / 3.0));
                                        colArr[i * 3] = 0.20 + 0.50 * normZ;
                                        colArr[i * 3 + 1] = 0.70 + 0.25 * normZ;
                                        colArr[i * 3 + 2] = 0.95;
                                    }
                                }
                                ptGeom.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
                                ptGeom.setAttribute('color', new THREE.BufferAttribute(colArr, 3));
                                ptGeom.computeBoundingSphere();
                                pointCloudMaterial = new THREE.PointsMaterial({
                                    size: cloudPointSize,
                                    sizeAttenuation: true,
                                    vertexColors: true,
                                    transparent: true,
                                    opacity: cloudPointAlpha
                                });
                                cloudMesh = new THREE.Points(ptGeom, pointCloudMaterial);
                                scene.add(cloudMesh);
                            }

                            if (data.camera) {
                                if (data.camera.width && data.camera.height) {
                                    plateWidth = data.camera.width;
                                    plateHeight = data.camera.height;
                                    plateAspect = plateWidth / plateHeight;
                                    if (filmGateBox) {
                                        filmGateBox.style.aspectRatio = `${plateWidth} / ${plateHeight}`;
                                    }
                                    const badge = document.getElementById("monitor-aspect-badge");
                                    if (badge) {
                                        badge.innerText = `${plateWidth}×${plateHeight} • ${(plateAspect).toFixed(2)}:1`;
                                    }
                                }

                                if (data.camera.matrices && data.camera.matrices.length > 0) {
                                    cameraMatrices = data.camera.matrices;
                                    totalFrames = Math.max(totalFrames, cameraMatrices.length);
                                    timelineSlider.max = totalFrames - 1;
                                    hudFrameLabel.innerText = `Frame: [ ${String(currentFrame).padStart(3, '0')} / ${totalFrames} ]`;
                                    if (data.camera.focal_mm) {
                                        const focal = data.camera.focal_mm;
                                        const sensorW = data.camera.sensor_width_mm || 36.0;
                                        const fovRad = 2.0 * Math.atan(sensorW / (2.0 * focal));
                                        shotCamera.fov = THREE.MathUtils.radToDeg(fovRad);
                                        shotCamera.updateProjectionMatrix();

                                        const lensInfo = document.getElementById("monitor-lens-info");
                                        if (lensInfo) {
                                            lensInfo.innerText = `Focal: ${Math.round(focal)}mm • Sensor: 36×24mm`;
                                        }
                                    }
                                    buildCameraTrajectoryPath();
                                    updateShotCameraPose(currentFrame);

                                    // Auto-frame FlyCam to view scene nicely
                                    const firstCam = cameraMatrices[0];
                                    const camPos = new THREE.Vector3(firstCam[0][3], firstCam[1][3], firstCam[2][3]);
                                    let targetLook = new THREE.Vector3(0, 2, 0);
                                    if (cloudMesh && cloudMesh.geometry.boundingSphere) {
                                        targetLook.copy(cloudMesh.geometry.boundingSphere.center);
                                    }
                                    flyCamera.position.set(camPos.x - 2.5, camPos.y - 4.5, camPos.z + 2.5);
                                    flyCamera.lookAt(targetLook);
                                    orbitControls.target.copy(targetLook);
                                    orbitControls.update();
                                }
                            }

                            updateActorTexture(currentFrame);
                            updateActorBillboardTransform();
                        }).catch(e => console.log("[VFX Suite] Scene data load note:", e));
                    } catch (e) {}

                    // Renderer
                    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
                    renderer.setSize(width, height);
                    renderer.setPixelRatio(window.devicePixelRatio || 1);
                    canvasHost.appendChild(renderer.domElement);

                    // Controls
                    const orbitControls = new THREE.OrbitControls(flyCamera, renderer.domElement);
                    orbitControls.enableDamping = false;
                    orbitControls.dampingFactor = 0;
                    orbitControls.screenSpacePanning = true;
                    orbitControls.minPolarAngle = 0.001;
                    orbitControls.maxPolarAngle = Math.PI - 0.001;

                    const transformControls = new THREE.TransformControls(flyCamera, renderer.domElement);
                    transformControls.size = 1.0;
                    transformControls.setMode("translate");
                    scene.add(transformControls);
                    // Multi-selection transform proxy group
                    const transformProxyGroup = new THREE.Group();
                    scene.add(transformProxyGroup);

                    // 3-Point Plane Calibration Tool Setup
                    let isPicking3Points = false;
                    const picked3Points = [];
                    const pickedMarkersGroup = new THREE.Group();
                    scene.add(pickedMarkersGroup);

                    let triangleLineMesh = null;
                    const planePreviewGroup = new THREE.Group();
                    planePreviewGroup.visible = false;
                    scene.add(planePreviewGroup);

                    let previewCenter = new THREE.Vector3();
                    let previewNormal = new THREE.Vector3(0, 0, 1);
                    let previewQuat = new THREE.Quaternion();
                    let previewSize = 6.0;
                    let isNormalFlipped = false;

                    // Hover snapping indicator marker on Point Cloud (pinpoint <= 0.01m)
                    let snappedVertex = null;
                    let snappedPointIndex = -1;

                    const hoverMarker = new THREE.Group();
                    const hoverDot = new THREE.Mesh(
                        new THREE.SphereGeometry(0.005, 12, 12),
                        new THREE.MeshBasicMaterial({ color: 0x00ffff, transparent: true, opacity: 0.95 })
                    );
                    const hoverRingGeom = new THREE.RingGeometry(0.012, 0.016, 24);
                    const hoverRingMat = new THREE.MeshBasicMaterial({ color: 0x00ffff, side: THREE.DoubleSide, transparent: true, opacity: 0.85, depthTest: false });
                    const hoverRing = new THREE.Mesh(hoverRingGeom, hoverRingMat);
                    hoverMarker.add(hoverDot);
                    hoverMarker.add(hoverRing);
                    hoverMarker.visible = false;
                    scene.add(hoverMarker);

                    let wasGizmoDragging = false;
                    let gizmoInitialStates = new Map();

                    // CRITICAL FIX: Override _getPointer so TransformControls calculates raycast coordinates
                    // relative to our scissored viewports (Left 3D Area or Right Film Gate) rather than the whole screen!
                    transformControls._getPointer = function (event) {
                        const pointer = event.changedTouches ? event.changedTouches[0] : event;
                        const { cam, ndc } = getPointerTarget(pointer);
                        if (cam && transformControls.camera !== cam) {
                            transformControls.camera = cam;
                        }
                        if (ndc) {
                            return {
                                x: ndc.x,
                                y: ndc.y,
                                button: event.button !== undefined ? event.button : 0
                            };
                        }
                        return {
                            x: -999,
                            y: -999,
                            button: event.button !== undefined ? event.button : 0
                        };
                    };

                    transformControls.addEventListener("dragging-changed", (e) => {
                        orbitControls.enabled = !e.value;
                        if (e.value) {
                            wasGizmoDragging = true;
                            pushUndoState(selectedObjects.size > 1 ? "Transform Multi-Objects" : "Gizmo Transform");

                            // Cache initial transforms of all selected objects when dragging begins
                            gizmoInitialStates.clear();
                            transformProxyGroup.updateMatrixWorld(true);
                            const proxyInverse = transformProxyGroup.matrixWorld.clone().invert();

                            selectedObjects.forEach(obj => {
                                obj.updateMatrixWorld(true);
                                const localToProxy = obj.matrixWorld.clone().premultiply(proxyInverse);
                                gizmoInitialStates.set(obj, {
                                    relMatrix: localToProxy,
                                    initPos: obj.position.clone(),
                                    initRot: obj.rotation.clone(),
                                    initScale: obj.scale.clone()
                                });
                            });
                        } else {
                            setTimeout(() => { wasGizmoDragging = false; }, 120);
                            gizmoInitialStates.clear();
                        }
                    });

                    transformControls.addEventListener("change", () => {
                        if (selectedObjects.size > 1 && transformControls.object === transformProxyGroup) {
                            transformProxyGroup.updateMatrixWorld(true);
                            selectedObjects.forEach(obj => {
                                const st = gizmoInitialStates.get(obj);
                                if (st) {
                                    const newWorldMat = st.relMatrix.clone().premultiply(transformProxyGroup.matrixWorld);
                                    newWorldMat.decompose(obj.position, obj.quaternion, obj.scale);
                                    obj.rotation.setFromQuaternion(obj.quaternion);

                                    if (autoKeyEnabled) {
                                        recordKeyframe(obj, currentFrame);
                                    }
                                }
                            });
                            syncSceneData();
                            updateTransformInspector();
                            updateSelectionBoxes();
                        } else if (selectedObject) {
                            if (autoKeyEnabled) {
                                recordKeyframe(selectedObject, currentFrame);
                            } else {
                                syncSceneData();
                            }
                            updateTransformInspector();
                            updateSelectionBoxes();
                        }
                    });

                    // Three.js Objects management
                    const threeObjectsMap = new Map();
                    // Fully matte diffuse clay material without specular reflections
                    const clayMaterial = new THREE.MeshStandardMaterial({
                        color: 0xcccccc,
                        roughness: 1.0,
                        metalness: 0.0,
                    });

                    // Add Primitives with Pivot at Bottom Center (Z=0)
                    function createPrimitiveMesh(type, id, color = "#cccccc") {
                        let geom;
                        if (type === "cylinder") {
                            geom = new THREE.CylinderGeometry(0.5, 0.5, 2.0, 32);
                            geom.rotateX(Math.PI / 2);
                            geom.translate(0, 0, 1.0); // Pivot at bottom circular base (Z=0)
                        } else if (type === "sphere") {
                            geom = new THREE.SphereGeometry(0.6, 32, 24);
                            geom.translate(0, 0, 0.6); // Pivot at bottom pole (Z=0)
                        } else if (type === "cone") {
                            geom = new THREE.ConeGeometry(0.6, 1.6, 32);
                            geom.rotateX(Math.PI / 2);
                            geom.translate(0, 0, 0.8); // Pivot at flat base (Z=0)
                        } else if (type === "torus") {
                            geom = new THREE.TorusGeometry(0.65, 0.22, 24, 48);
                            geom.translate(0, 0, 0.22); // Pivot at bottom contact level (Z=0)
                        } else if (type === "plane") {
                            geom = new THREE.PlaneGeometry(6, 6);
                        } else {
                            geom = new THREE.BoxGeometry(1.0, 1.0, 1.0);
                            geom.translate(0, 0, 0.5); // Pivot at bottom center (Z=0)
                        }
                        const mat = clayMaterial.clone();
                        mat.color.set(color);
                        const mesh = new THREE.Mesh(geom, mat);
                        mesh.userData = { id: id || Date.now(), type: type, name: `${type}_${threeObjectsMap.size + 1}`, color: color, keyframes: [] };
                        return mesh;
                    }

                    // Smart Spawn in front of Camera at ground level (Z=0)
                    function getSpawnPosition() {
                        if (cameraMatrices.length > 0 && shotCamera) {
                            const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(shotCamera.quaternion);
                            const target = shotCamera.position.clone().addScaledVector(forward, 2.6);
                            return [Math.round(target.x * 100) / 100, Math.round(target.y * 100) / 100, 0.0];
                        }
                        return [0.0, 0.0, 0.0];
                    }

                    // Selection Highlight Boxes (supports multi-selection)
                    const selectionBoxesMap = new Map();
                    function updateSelectionBoxes() {
                        // Remove boxes for objects no longer selected
                        selectionBoxesMap.forEach((box, id) => {
                            if (!selectedObjects.has(threeObjectsMap.get(id))) {
                                scene.remove(box);
                                if (box.geometry) box.geometry.dispose();
                                selectionBoxesMap.delete(id);
                            }
                        });
                        // Add or update box for each selected object
                        selectedObjects.forEach(m => {
                            if (!m) return;
                            const id = m.userData.id;
                            let box = selectionBoxesMap.get(id);
                            if (!box) {
                                box = new THREE.BoxHelper(m, 0x00f0ff);
                                box.material.depthTest = false;
                                box.material.transparent = true;
                                box.material.opacity = 0.95;
                                box.renderOrder = 998;
                                scene.add(box);
                                selectionBoxesMap.set(id, box);
                            }
                            box.setFromObject(m);
                            box.visible = m.visible !== false;
                        });
                    }

                    // Restore saved objects
                    sceneData.forEach(item => {
                        const m = createPrimitiveMesh(item.type, item.id, item.color);
                        if (item.name) m.userData.name = item.name;
                        m.position.fromArray(item.position);
                        m.rotation.fromArray(item.rotation);
                        m.scale.fromArray(item.scale);
                        m.userData.keyframes = item.keyframes || [];
                        scene.add(m);
                        threeObjectsMap.set(m.userData.id, m);
                    });

                    // Auto-select first object or create default reference cube in front of camera
                    if (threeObjectsMap.size === 0) {
                        const defaultBox = createPrimitiveMesh("cube", Date.now(), "#cccccc");
                        const spawn = getSpawnPosition();
                        defaultBox.position.fromArray(spawn);
                        scene.add(defaultBox);
                        threeObjectsMap.set(defaultBox.userData.id, defaultBox);
                        selectObject(defaultBox);
                        syncSceneData();
                    } else if (!selectedObject) {
                        selectObject(threeObjectsMap.values().next().value);
                    }

                    // Flycam (WASD + RMB)
                    let isRmbDown = false;
                    const keysPressed = {};
                    let flySpeed = 5.0;
                    let flyYaw = 0;
                    let flyPitch = 0;

                    // Pointer routing for Dual Viewport (Left 3D vs Right Film Gate)
                    function getPointerTarget(e) {
                        const hostRect = canvasHost.getBoundingClientRect();
                        const gateRect = filmGateBox ? filmGateBox.getBoundingClientRect() : null;
                        const rightColRect = studioRightCol.getBoundingClientRect();
                        const leftW = Math.max(10, rightColRect.left - hostRect.left);

                        const mouseX = e.clientX;
                        const mouseY = e.clientY;

                        const ndc = new THREE.Vector2();

                        // Inside film gate (Shot Camera Monitor)?
                        if (gateRect && mouseX >= gateRect.left && mouseX <= gateRect.right &&
                            mouseY >= gateRect.top && mouseY <= gateRect.bottom) {
                            ndc.x = ((mouseX - gateRect.left) / gateRect.width) * 2 - 1;
                            ndc.y = -((mouseY - gateRect.top) / gateRect.height) * 2 + 1;
                            return { cam: shotCamera, ndc, isGate: true };
                        }

                        // Inside left 3D Viewport?
                        if (mouseX < rightColRect.left) {
                            ndc.x = ((mouseX - hostRect.left) / leftW) * 2 - 1;
                            ndc.y = -((mouseY - hostRect.top) / hostRect.height) * 2 + 1;
                            return { cam: activeCamera, ndc, isGate: false };
                        }

                        return { cam: null, ndc: null, isGate: false };
                    }

                    canvasHost.addEventListener("pointerdown", (e) => {
                        const { cam, isGate } = getPointerTarget(e);
                        if (cam && transformControls.camera !== cam) {
                            transformControls.camera = cam;
                        }

                        // Disable OrbitControls if clicking inside camera film gate or on gizmo handles
                        if (isGate || transformControls.axis !== null) {
                            orbitControls.enabled = false;
                        }

                        if (e.button === 2) { // RMB Flycam
                            if (cam === flyCamera) {
                                isRmbDown = true;
                                orbitControls.enabled = false;
                                // Calculate actual heading and elevation from flyCamera's current orientation
                                const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(flyCamera.quaternion);
                                flyPitch = Math.asin(Math.max(-0.999, Math.min(0.999, fwd.z)));
                                flyYaw = Math.atan2(fwd.y, fwd.x);
                            }
                        }
                    });

                    window.addEventListener("pointerup", (e) => {
                        if (e.button === 2) {
                            if (isRmbDown) {
                                isRmbDown = false;
                                const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(flyCamera.quaternion);
                                orbitControls.target.copy(flyCamera.position).addScaledVector(forward, 4.0);
                                orbitControls.update();
                            }
                        }
                        if (!transformControls.dragging) {
                            orbitControls.enabled = true;
                        }
                    });

                    canvasHost.addEventListener("contextmenu", (e) => e.preventDefault());

                    canvasHost.addEventListener("wheel", (e) => {
                        if (isRmbDown) {
                            e.preventDefault();
                            flySpeed = Math.max(0.5, Math.min(25.0, flySpeed * (e.deltaY < 0 ? 1.2 : 0.8)));
                        }
                    }, { passive: false });

                    // Hotkeys
                    window.addEventListener("keydown", (e) => {
                        keysPressed[e.code] = true;

                        if (!isRmbDown && e.target.tagName !== "INPUT" && e.target.tagName !== "SELECT") {
                            // Escape: cancel 3-point plane picking if active
                            if (e.key === "Escape") {
                                if (isPicking3Points) {
                                    e.preventDefault();
                                    exit3PointPicker();
                                    return;
                                }
                            }

                            // Ctrl+Z: Undo
                            if ((e.ctrlKey || e.metaKey) && (e.code === "KeyZ" || e.key === "z" || e.key === "Z" || e.key === "я" || e.key === "Я")) {
                                e.preventDefault();
                                undo();
                                return;
                            }
                            // Ctrl+D: Duplicate Object
                            if ((e.ctrlKey || e.metaKey) && (e.code === "KeyD" || e.key === "d" || e.key === "D" || e.key === "в" || e.key === "В")) {
                                e.preventDefault();
                                duplicateSelectedObject();
                                return;
                            }
                            // Ctrl+C: Copy Transforms
                            if ((e.ctrlKey || e.metaKey) && (e.code === "KeyC" || e.key === "c" || e.key === "C" || e.key === "с" || e.key === "С")) {
                                e.preventDefault();
                                copySelectedTransforms();
                                return;
                            }
                            // Ctrl+V: Paste Transforms
                            if ((e.ctrlKey || e.metaKey) && (e.code === "KeyV" || e.key === "v" || e.key === "V" || e.key === "м" || e.key === "М")) {
                                e.preventDefault();
                                pasteTransformsToSelected();
                                return;
                            }

                            // Tilde / Backquote: Toggle World vs Local Space (~ or Russian ё / Ё)
                            if (e.code === "Backquote" || e.key === "~" || e.key === "`" || e.key === "ё" || e.key === "Ё") {
                                e.preventDefault();
                                toggleCoordSpace();
                            }
                            // C: Toggle Camera View on Left Viewport (EN C / c or RU С / с)
                            if (e.code === "KeyC" || e.key === "c" || e.key === "C" || e.key === "с" || e.key === "С") {
                                e.preventDefault();
                                toggleCameraView();
                            }
                            // F: Frame Selected Object / Scene Center (EN F / f or RU А / а)
                            if (e.code === "KeyF" || e.key === "f" || e.key === "F" || e.key === "а" || e.key === "А") {
                                e.preventDefault();
                                frameSelectedObject();
                            }
                            // Standard DCC Hotkeys (EN & RU layouts)
                            if (e.code === "KeyQ" || e.key === "q" || e.key === "Q" || e.key === "й" || e.key === "Й") setToolMode("select");
                            if (e.code === "KeyW" || e.key === "w" || e.key === "W" || e.key === "ц" || e.key === "Ц") setToolMode("translate");
                            if (e.code === "KeyE" || e.key === "e" || e.key === "E" || e.key === "у" || e.key === "У") setToolMode("rotate");
                            if (e.code === "KeyR" || e.key === "r" || e.key === "R" || e.key === "к" || e.key === "К") setToolMode("scale");
                            if (e.code === "KeyK" || e.key === "k" || e.key === "K" || e.key === "л" || e.key === "Л") {
                                if (selectedObject) recordKeyframe(selectedObject, currentFrame);
                            }
                            if (e.key === "Delete" || e.key === "Backspace") {
                                deleteSelectedObject();
                            }
                            if (e.code === "Space") {
                                e.preventDefault();
                                toggleTimelinePlay();
                            }
                            if (e.code === "ArrowLeft") {
                                e.preventDefault();
                                stepFrame(-1);
                            }
                            if (e.code === "ArrowRight") {
                                e.preventDefault();
                                stepFrame(1);
                            }
                            if (e.code === "BracketLeft" || e.key === "[" || e.key === "х" || e.key === "Х") {
                                e.preventDefault();
                                jumpKeyframe(-1);
                            }
                            if (e.code === "BracketRight" || e.key === "]" || e.key === "ъ" || e.key === "Ъ") {
                                e.preventDefault();
                                jumpKeyframe(1);
                            }
                            if (e.code === "Home") {
                                e.preventDefault();
                                setTimelineFrame(0);
                            }
                            if (e.code === "End") {
                                e.preventDefault();
                                setTimelineFrame(totalFrames - 1);
                            }
                        }
                    });

                    window.addEventListener("keyup", (e) => {
                        keysPressed[e.code] = false;
                    });

                    // Mouse look for Flycam, Gizmo hover, and 3-Point Snapping detection
                    canvasHost.addEventListener("pointermove", (e) => {
                        const { cam, ndc, isGate } = getPointerTarget(e);
                        if (cam && transformControls.camera !== cam) {
                            transformControls.camera = cam;
                        }

                        // 3-Point Snapping Hover Indicator: True magnetic snapping to Point Cloud vertices
                        if (isPicking3Points) {
                            if (cam === flyCamera && cloudMesh && !isRmbDown && ndc) {
                                const posAttr = cloudMesh.geometry ? cloudMesh.geometry.getAttribute('position') : null;
                                const hostRect = canvasHost.getBoundingClientRect();
                                const rightColRect = studioRightCol.getBoundingClientRect();
                                const vpWidth = Math.max(10, rightColRect.left - hostRect.left);
                                const vpHeight = Math.max(10, hostRect.height);

                                raycaster.setFromCamera(ndc, cam);
                                const camDist = flyCamera.position.distanceTo(orbitControls.target || new THREE.Vector3(0,0,0));
                                raycaster.params.Points.threshold = Math.max(0.18, camDist * 0.04);
                                const hits = raycaster.intersectObject(cloudMesh);

                                let bestVertex = null;
                                let bestIdx = -1;
                                let minPixelDist = Infinity;

                                if (hits.length > 0 && posAttr) {
                                    // Test top candidates from raycaster for screen-space proximity
                                    const candidateLimit = Math.min(hits.length, 25);
                                    for (let i = 0; i < candidateLimit; i++) {
                                        const hIdx = hits[i].index;
                                        if (hIdx === undefined || hIdx === null) continue;

                                        const v = new THREE.Vector3().fromBufferAttribute(posAttr, hIdx);
                                        cloudMesh.localToWorld(v);

                                        // Project vertex to screen space
                                        const proj = v.clone().project(cam);
                                        // Ignore vertices behind camera
                                        if (proj.z > 1.0) continue;

                                        const dx = (proj.x - ndc.x) * (vpWidth * 0.5);
                                        const dy = (proj.y - ndc.y) * (vpHeight * 0.5);
                                        const pDist = Math.sqrt(dx * dx + dy * dy);

                                        if (pDist < minPixelDist) {
                                            minPixelDist = pDist;
                                            bestVertex = v;
                                            bestIdx = hIdx;
                                        }
                                    }
                                }

                                // Snap if closest vertex is within 38 screen pixels
                                if (bestVertex && minPixelDist <= 38) {
                                    snappedVertex = bestVertex;
                                    snappedPointIndex = bestIdx;
                                    hoverMarker.position.copy(bestVertex);
                                    hoverMarker.quaternion.copy(cam.quaternion); // Face camera
                                    hoverMarker.visible = true;
                                    renderer.domElement.style.cursor = 'crosshair';

                                    const statusEl = document.getElementById("vfx-3pt-status");
                                    if (statusEl && picked3Points.length < 3) {
                                        statusEl.innerText = `🧲 Snapped to Vertex #${bestIdx} • Click to pick (${picked3Points.length} / 3)`;
                                    }
                                } else {
                                    snappedVertex = null;
                                    snappedPointIndex = -1;
                                    hoverMarker.visible = false;
                                    renderer.domElement.style.cursor = 'default';

                                    const statusEl = document.getElementById("vfx-3pt-status");
                                    if (statusEl && picked3Points.length < 3) {
                                        statusEl.innerText = `🎯 Hover near points to snap (${picked3Points.length} / 3)`;
                                    }
                                }
                            } else {
                                snappedVertex = null;
                                snappedPointIndex = -1;
                                hoverMarker.visible = false;
                            }
                        } else if (hoverMarker && hoverMarker.visible) {
                            snappedVertex = null;
                            snappedPointIndex = -1;
                            hoverMarker.visible = false;
                        }

                        if (isGate) {
                            orbitControls.enabled = false;
                        } else if (transformControls.axis !== null || transformControls.dragging) {
                            orbitControls.enabled = false;
                            renderer.domElement.style.cursor = 'pointer';
                        } else if (!isRmbDown && !isPicking3Points) {
                            orbitControls.enabled = true;
                            renderer.domElement.style.cursor = 'default';
                        }

                        if (isRmbDown && cam === flyCamera) {
                            const movementX = e.movementX || 0;
                            const movementY = e.movementY || 0;
                            flyYaw -= movementX * 0.003;
                            flyPitch -= movementY * 0.003;
                            // Allow looking all the way up into the sky (+89 deg) and down into ground (-89 deg)
                            flyPitch = Math.max(-Math.PI * 0.495, Math.min(Math.PI * 0.495, flyPitch));

                            const cp = Math.cos(flyPitch);
                            const forward = new THREE.Vector3(
                                Math.cos(flyYaw) * cp,
                                Math.sin(flyYaw) * cp,
                                Math.sin(flyPitch)
                            );
                            flyCamera.lookAt(flyCamera.position.clone().add(forward));
                            orbitControls.target.copy(flyCamera.position).addScaledVector(forward, 4.0);
                        }
                    });

                    function updateFlycam(dt) {
                        if (!isRmbDown) return;
                        const moveDist = flySpeed * dt;
                        const cp = Math.cos(flyPitch);
                        const forward = new THREE.Vector3(
                            Math.cos(flyYaw) * cp,
                            Math.sin(flyYaw) * cp,
                            Math.sin(flyPitch)
                        );
                        // Clean lateral vector in ground plane (Forward x Up)
                        const right = new THREE.Vector3(Math.sin(flyYaw), -Math.cos(flyYaw), 0);
                        const up = new THREE.Vector3(0, 0, 1);

                        if (keysPressed["KeyW"]) flyCamera.position.addScaledVector(forward, moveDist);
                        if (keysPressed["KeyS"]) flyCamera.position.addScaledVector(forward, -moveDist);
                        if (keysPressed["KeyD"]) flyCamera.position.addScaledVector(right, moveDist);
                        if (keysPressed["KeyA"]) flyCamera.position.addScaledVector(right, -moveDist);
                        if (keysPressed["KeyE"]) flyCamera.position.addScaledVector(up, moveDist);
                        if (keysPressed["KeyQ"]) flyCamera.position.addScaledVector(up, -moveDist);

                        orbitControls.target.copy(flyCamera.position).addScaledVector(forward, 4.0);
                    }

                    // Raycasting for selection in both viewports (Single click or Shift/Ctrl multi-select)
                    const raycaster = new THREE.Raycaster();

                    canvasHost.addEventListener("click", (e) => {
                        if (isRmbDown || wasGizmoDragging) return;
                        const { cam, ndc } = getPointerTarget(e);
                        if (!cam || !ndc) return;

                        // 3-Point Calibration Click Handler
                        if (isPicking3Points) {
                            if (e.button !== 0) return;
                            if (picked3Points.length >= 3) return;
                            if (cam !== flyCamera || !cloudMesh) return;

                            if (snappedVertex) {
                                addPickedPoint(snappedVertex.clone(), snappedPointIndex);
                            } else {
                                // Fallback: raycast nearest point if user clicks precisely on a point
                                raycaster.setFromCamera(ndc, cam);
                                const camDist = flyCamera.position.distanceTo(orbitControls.target || new THREE.Vector3(0,0,0));
                                raycaster.params.Points.threshold = Math.max(0.18, camDist * 0.04);
                                const hits = raycaster.intersectObject(cloudMesh);
                                if (hits.length > 0) {
                                    const posAttr = cloudMesh.geometry ? cloudMesh.geometry.getAttribute('position') : null;
                                    const hIdx = hits[0].index;
                                    if (posAttr && hIdx !== undefined && hIdx !== null) {
                                        const exactV = new THREE.Vector3().fromBufferAttribute(posAttr, hIdx);
                                        cloudMesh.localToWorld(exactV);
                                        addPickedPoint(exactV, hIdx);
                                    } else {
                                        addPickedPoint(hits[0].point.clone());
                                    }
                                }
                            }
                            return;
                        }

                        transformControls.camera = cam;

                        raycaster.setFromCamera(ndc, cam);
                        const meshes = Array.from(threeObjectsMap.values());
                        const intersects = raycaster.intersectObjects(meshes, true);

                        const isMultiModifier = (e.shiftKey || e.ctrlKey || e.metaKey);

                        if (intersects.length > 0) {
                            let rootMesh = intersects[0].object;
                            while (rootMesh.parent && !threeObjectsMap.has(rootMesh.userData.id) && rootMesh.parent !== scene) {
                                rootMesh = rootMesh.parent;
                            }

                            if (isMultiModifier) {
                                toggleObjectSelection(rootMesh);
                            } else {
                                selectObject(rootMesh);
                            }
                        } else if (!isMultiModifier) {
                            // Click on empty space clears selection
                            selectObject(null);
                        }
                    });

                    function toggleObjectSelection(mesh) {
                        if (!mesh) return;
                        if (selectedObjects.has(mesh)) {
                            selectedObjects.delete(mesh);
                            if (selectedObject === mesh) {
                                selectedObject = selectedObjects.size > 0 ? selectedObjects.values().next().value : null;
                            }
                        } else {
                            selectedObjects.add(mesh);
                            selectedObject = mesh;
                        }
                        refreshSelectionState();
                    }

                    function selectObject(mesh, additive = false) {
                        if (!additive) {
                            selectedObjects.clear();
                        }
                        if (mesh) {
                            selectedObjects.add(mesh);
                            selectedObject = mesh;
                        } else if (!additive) {
                            selectedObject = null;
                        }
                        refreshSelectionState();
                    }

                    function refreshSelectionState() {
                        // Reset emissive glow on all objects
                        threeObjectsMap.forEach(m => {
                            if (m.material && m.material.emissive && m.userData._origEmissive !== undefined) {
                                m.material.emissive.setHex(m.userData._origEmissive);
                            }
                        });

                        // Set emissive glow on selected objects
                        selectedObjects.forEach(m => {
                            if (m.material && m.material.emissive) {
                                if (m.userData._origEmissive === undefined) {
                                    m.userData._origEmissive = m.material.emissive.getHex();
                                }
                                m.material.emissive.setHex(0x182436);
                            }
                        });

                        if (selectedObjects.size === 0) {
                            transformControls.detach();
                            const hud = document.getElementById("hud-status");
                            if (hud) hud.innerText = `Mode: Select | Space: ${transformControls.space.toUpperCase()} | Object: None`;
                        } else if (selectedObjects.size === 1) {
                            const single = selectedObjects.values().next().value;
                            selectedObject = single;
                            if (transformControls.getMode() !== "select") {
                                transformControls.attach(single);
                            }
                            const hud = document.getElementById("hud-status");
                            if (hud) hud.innerText = `Mode: ${transformControls.getMode().toUpperCase()} | Space: ${transformControls.space.toUpperCase()} | Object: ${single.userData.name || single.userData.type}`;
                        } else {
                            // Multiple objects selected: compute centroid & attach transformProxyGroup
                            let centroid = new THREE.Vector3(0, 0, 0);
                            selectedObjects.forEach(m => centroid.add(m.position));
                            centroid.divideScalar(selectedObjects.size);

                            transformProxyGroup.position.copy(centroid);
                            transformProxyGroup.rotation.set(0, 0, 0);
                            transformProxyGroup.scale.set(1, 1, 1);
                            transformProxyGroup.updateMatrixWorld(true);

                            if (transformControls.getMode() !== "select") {
                                transformControls.attach(transformProxyGroup);
                            }
                            const hud = document.getElementById("hud-status");
                            if (hud) hud.innerText = `Mode: ${transformControls.getMode().toUpperCase()} | Space: ${transformControls.space.toUpperCase()} | Objects: ${selectedObjects.size} selected`;
                        }

                        updateSelectionBoxes();
                        updateKeyMarkers();
                        updateOutliner();
                        updateTransformInspector();
                    }

                    function setToolMode(mode) {
                        if (mode === "select") {
                            transformControls.detach();
                        } else {
                            if (selectedObjects.size > 1) {
                                let centroid = new THREE.Vector3(0, 0, 0);
                                selectedObjects.forEach(m => centroid.add(m.position));
                                centroid.divideScalar(selectedObjects.size);
                                transformProxyGroup.position.copy(centroid);
                                transformProxyGroup.rotation.set(0, 0, 0);
                                transformProxyGroup.scale.set(1, 1, 1);
                                transformProxyGroup.updateMatrixWorld(true);
                                transformControls.attach(transformProxyGroup);
                            } else if (selectedObject) {
                                transformControls.attach(selectedObject);
                            }
                            transformControls.setMode(mode);
                        }
                        document.querySelectorAll("#btn-tool-select, #btn-tool-translate, #btn-tool-rotate, #btn-tool-scale").forEach(b => b.classList.remove("vfx-btn-active"));
                        const btn = document.getElementById(`btn-tool-${mode}`);
                        if (btn) btn.classList.add("vfx-btn-active");
                        const hud = document.getElementById("hud-status");
                        if (hud) {
                            let objName = "None";
                            if (selectedObjects.size > 1) objName = `${selectedObjects.size} objects`;
                            else if (selectedObject) objName = selectedObject.userData.name || selectedObject.userData.type;
                            hud.innerText = `Mode: ${mode.toUpperCase()} | Space: ${transformControls.space.toUpperCase()} | Object: ${objName}`;
                        }
                    }

                    function toggleCoordSpace() {
                        const cur = transformControls.space;
                        const next = cur === "world" ? "local" : "world";
                        transformControls.setSpace(next);
                        const btn = document.getElementById("btn-coord-space");
                        if (btn) btn.innerText = `Space: ${next.charAt(0).toUpperCase() + next.slice(1)} (~)`;
                        const hud = document.getElementById("hud-status");
                        if (hud) {
                            const objName = selectedObject ? (selectedObject.userData.name || selectedObject.userData.type) : "None";
                            hud.innerText = `Mode: ${transformControls.getMode().toUpperCase()} | Space: ${next.toUpperCase()} | Object: ${objName}`;
                        }
                    }

                    function toggleCameraView() {
                        isShotCamActive = !isShotCamActive;
                        const btn = document.getElementById("btn-cam-view");
                        if (isShotCamActive) {
                            activeCamera = shotCamera;
                            if (btn) {
                                btn.innerText = "🎥 Free Orbit (C)";
                                btn.classList.add("vfx-btn-active");
                            }
                            orbitControls.enabled = false;
                        } else {
                            activeCamera = flyCamera;
                            if (btn) {
                                btn.innerText = "🎥 Shot Cam (C)";
                                btn.classList.remove("vfx-btn-active");
                            }
                            orbitControls.enabled = true;
                        }
                        transformControls.camera = activeCamera;
                    }

                    // --- 5. HISTORY (UNDO), DUPLICATION & TRANSFORM CLIPBOARD ---
                    const undoStack = [];
                    const MAX_UNDO_STEPS = 10;
                    let transformClipboard = null;

                    function pushUndoState(actionName = "Action") {
                        const snapshot = Array.from(threeObjectsMap.values()).map(m => ({
                            id: m.userData.id,
                            name: m.userData.name || m.userData.type,
                            type: m.userData.type,
                            color: m.userData.color || "#cccccc",
                            position: [m.position.x, m.position.y, m.position.z],
                            rotation: [m.rotation.x, m.rotation.y, m.rotation.z],
                            scale: [m.scale.x, m.scale.y, m.scale.z],
                            keyframes: JSON.parse(JSON.stringify(m.userData.keyframes || []))
                        }));

                        undoStack.push({
                            name: actionName,
                            selectedId: selectedObject ? selectedObject.userData.id : null,
                            snapshot: JSON.stringify(snapshot)
                        });

                        if (undoStack.length > MAX_UNDO_STEPS) {
                            undoStack.shift();
                        }
                        updateUndoButton();
                    }

                    function updateUndoButton() {
                        const btn = document.getElementById("btn-undo-top");
                        if (btn) {
                            if (undoStack.length > 0) {
                                btn.innerText = `↩ Undo (${undoStack.length})`;
                                btn.style.opacity = "1";
                                btn.style.cursor = "pointer";
                            } else {
                                btn.innerText = "↩ Undo";
                                btn.style.opacity = "0.5";
                                btn.style.cursor = "default";
                            }
                        }
                    }

                    function undo() {
                        if (undoStack.length === 0) return;
                        const state = undoStack.pop();
                        updateUndoButton();
                        if (!state || !state.snapshot) return;

                        try {
                            const items = JSON.parse(state.snapshot);

                            threeObjectsMap.forEach(mesh => {
                                scene.remove(mesh);
                                if (mesh.geometry) mesh.geometry.dispose();
                            });
                            threeObjectsMap.clear();

                            items.forEach(item => {
                                const mesh = createPrimitiveMesh(item.type, item.id, item.color);
                                mesh.userData.name = item.name;
                                mesh.position.fromArray(item.position);
                                mesh.rotation.fromArray(item.rotation);
                                mesh.scale.fromArray(item.scale);
                                mesh.userData.keyframes = item.keyframes || [];
                                scene.add(mesh);
                                threeObjectsMap.set(mesh.userData.id, mesh);
                            });

                            let toSelect = null;
                            if (state.selectedId && threeObjectsMap.has(state.selectedId)) {
                                toSelect = threeObjectsMap.get(state.selectedId);
                            } else if (threeObjectsMap.size > 0) {
                                toSelect = threeObjectsMap.values().next().value;
                            }
                            selectObject(toSelect);

                            threeObjectsMap.forEach(obj => evaluateAnimation(obj, currentFrame));
                            syncSceneData();
                            updateOutliner();
                            updateTransformInspector();
                            updateKeyMarkers();
                        } catch (err) {
                            console.warn("[VFX Suite] Failed to restore undo snapshot:", err);
                        }
                    }

                    function deleteSelectedObject() {
                        const targets = selectedObjects.size > 0 ? Array.from(selectedObjects) : (selectedObject ? [selectedObject] : []);
                        if (targets.length === 0) return;
                        pushUndoState(targets.length > 1 ? "Delete Multi-Objects" : "Delete Object");

                        targets.forEach(obj => {
                            scene.remove(obj);
                            threeObjectsMap.delete(obj.userData.id);
                            sceneData = sceneData.filter(item => item.id !== obj.userData.id);
                            const box = selectionBoxesMap.get(obj.userData.id);
                            if (box) {
                                scene.remove(box);
                                if (box.geometry) box.geometry.dispose();
                                selectionBoxesMap.delete(obj.userData.id);
                            }
                        });

                        selectedObjects.clear();
                        selectedObject = null;
                        refreshSelectionState();
                        syncSceneData();
                        updateOutliner();
                    }

                    function duplicateSelectedObject() {
                        const targets = selectedObjects.size > 0 ? Array.from(selectedObjects) : (selectedObject ? [selectedObject] : []);
                        if (targets.length === 0) return;
                        pushUndoState(targets.length > 1 ? "Duplicate Multi-Objects" : "Duplicate Object");

                        const newMeshes = [];
                        targets.forEach((orig, idx) => {
                            const origType = orig.userData.type || "cube";
                            const newId = Date.now() + idx;
                            const newName = `${orig.userData.name || origType}_copy`;
                            const newColor = orig.userData.color || "#cccccc";

                            let newMesh;
                            if (["cube", "sphere", "cylinder", "cone", "torus", "plane"].includes(origType)) {
                                newMesh = createPrimitiveMesh(origType, newId, newColor);
                            } else {
                                newMesh = orig.clone(true);
                                newMesh.userData = {
                                    id: newId,
                                    type: origType,
                                    name: newName,
                                    color: newColor,
                                    keyframes: []
                                };
                            }

                            newMesh.userData.id = newId;
                            newMesh.userData.name = newName;
                            newMesh.userData.color = newColor;

                            newMesh.position.copy(orig.position);
                            newMesh.rotation.copy(orig.rotation);
                            newMesh.scale.copy(orig.scale);

                            if (orig.userData.keyframes && orig.userData.keyframes.length > 0) {
                                newMesh.userData.keyframes = orig.userData.keyframes.map(k => ({
                                    frame: k.frame,
                                    position: [...k.position],
                                    rotation: [...k.rotation],
                                    scale: [...k.scale],
                                    interp: k.interp || "bezier"
                                }));
                            } else {
                                newMesh.userData.keyframes = [];
                            }

                            scene.add(newMesh);
                            threeObjectsMap.set(newId, newMesh);
                            newMeshes.push(newMesh);
                        });

                        selectedObjects.clear();
                        newMeshes.forEach(m => selectedObjects.add(m));
                        selectedObject = newMeshes[newMeshes.length - 1];
                        refreshSelectionState();
                        syncSceneData();
                        updateOutliner();
                    }

                    function copySelectedTransforms() {
                        if (!selectedObject) return;
                        transformClipboard = {
                            position: [selectedObject.position.x, selectedObject.position.y, selectedObject.position.z],
                            rotation: [selectedObject.rotation.x, selectedObject.rotation.y, selectedObject.rotation.z],
                            scale: [selectedObject.scale.x, selectedObject.scale.y, selectedObject.scale.z]
                        };
                        const btnPaste = document.getElementById("btn-paste-xform");
                        if (btnPaste) {
                            btnPaste.classList.add("vfx-btn-active");
                            btnPaste.title = `Paste [Pos: ${transformClipboard.position.map(v => v.toFixed(2)).join(", ")}]`;
                        }
                    }

                    function pasteTransformsToSelected() {
                        if (!selectedObject || !transformClipboard) return;
                        pushUndoState("Paste Transforms");

                        selectedObject.position.set(...transformClipboard.position);
                        selectedObject.rotation.set(...transformClipboard.rotation);
                        selectedObject.scale.set(...transformClipboard.scale);

                        if (autoKeyEnabled) {
                            recordKeyframe(selectedObject, currentFrame);
                        } else {
                            syncSceneData();
                        }
                        updateTransformInspector();
                    }

                    function resetSelectedTransforms() {
                        const targets = selectedObjects.size > 0 ? Array.from(selectedObjects) : (selectedObject ? [selectedObject] : []);
                        if (targets.length === 0) return;
                        pushUndoState(targets.length > 1 ? "Reset Multi-Transforms" : "Reset Transforms");

                        targets.forEach(obj => {
                            obj.position.set(0, 0, 0);
                            obj.rotation.set(0, 0, 0);
                            obj.scale.set(1, 1, 1);
                            if (autoKeyEnabled) {
                                recordKeyframe(obj, currentFrame);
                            }
                        });

                        if (!autoKeyEnabled) {
                            syncSceneData();
                        }
                        refreshSelectionState();
                        updateTransformInspector();
                    }

                    // --- 5. OUTLINER & TRANSFORM INSPECTOR ---
                    function updateOutliner() {
                        const list = document.getElementById("outliner-list");
                        if (!list) return;
                        list.innerHTML = "";

                        const countEl = document.getElementById("outliner-count");
                        if (countEl) countEl.innerText = `${threeObjectsMap.size} objects`;

                        threeObjectsMap.forEach((mesh) => {
                            const item = document.createElement("div");
                            const isMeshSelected = selectedObjects.has(mesh) || (selectedObject === mesh);
                            item.className = "vfx-outliner-item" + (isMeshSelected ? " vfx-outliner-item-active" : "");

                            let typeIcon = "📦";
                            if (mesh.userData.type === "sphere") typeIcon = "⚪";
                            else if (mesh.userData.type === "cone") typeIcon = "▲";
                            else if (mesh.userData.type === "torus") typeIcon = "⭕";
                            else if (mesh.userData.type === "cylinder") typeIcon = "🛢️";
                            else if (mesh.userData.type === "plane") typeIcon = "📐";
                            else if (mesh.userData.type && mesh.userData.type.includes(".")) typeIcon = "📁";

                            const label = document.createElement("span");
                            label.innerText = `${typeIcon} ${mesh.userData.name || mesh.userData.type}`;
                            label.style.flex = "1";
                            label.onclick = (e) => {
                                if (e.shiftKey || e.ctrlKey || e.metaKey) {
                                    toggleObjectSelection(mesh);
                                } else {
                                    selectObject(mesh);
                                }
                            };

                            const actions = document.createElement("div");
                            actions.className = "vfx-outliner-actions";

                            const btnVis = document.createElement("button");
                            btnVis.className = "vfx-icon-btn";
                            btnVis.innerText = mesh.visible ? "👁️" : "🚫";
                            btnVis.title = "Toggle Visibility";
                            btnVis.onclick = (e) => {
                                e.stopPropagation();
                                mesh.visible = !mesh.visible;
                                btnVis.innerText = mesh.visible ? "👁️" : "🚫";
                            };

                            const btnDel = document.createElement("button");
                            btnDel.className = "vfx-icon-btn";
                            btnDel.innerText = "🗑️";
                            btnDel.title = "Delete Object";
                            btnDel.onclick = (e) => {
                                e.stopPropagation();
                                selectObject(mesh);
                                deleteSelectedObject();
                            };

                            actions.appendChild(btnVis);
                            actions.appendChild(btnDel);
                            item.appendChild(label);
                            item.appendChild(actions);
                            list.appendChild(item);
                        });
                    }

                    function updateTransformInspector() {
                        const targetEl = document.getElementById("inspector-target");
                        if (!selectedObject) {
                            if (targetEl) targetEl.innerText = "None";
                            return;
                        }
                        if (targetEl) targetEl.innerText = selectedObject.userData.name || selectedObject.userData.type || "Object";

                        const posX = document.getElementById("num-pos-x");
                        const posY = document.getElementById("num-pos-y");
                        const posZ = document.getElementById("num-pos-z");
                        if (posX) posX.value = selectedObject.position.x.toFixed(2);
                        if (posY) posY.value = selectedObject.position.y.toFixed(2);
                        if (posZ) posZ.value = selectedObject.position.z.toFixed(2);

                        const rotX = document.getElementById("num-rot-x");
                        const rotY = document.getElementById("num-rot-y");
                        const rotZ = document.getElementById("num-rot-z");
                        if (rotX) rotX.value = THREE.MathUtils.radToDeg(selectedObject.rotation.x).toFixed(1);
                        if (rotY) rotY.value = THREE.MathUtils.radToDeg(selectedObject.rotation.y).toFixed(1);
                        if (rotZ) rotZ.value = THREE.MathUtils.radToDeg(selectedObject.rotation.z).toFixed(1);

                        const scX = document.getElementById("num-scale-x");
                        const scY = document.getElementById("num-scale-y");
                        const scZ = document.getElementById("num-scale-z");
                        if (scX) scX.value = selectedObject.scale.x.toFixed(2);
                        if (scY) scY.value = selectedObject.scale.y.toFixed(2);
                        if (scZ) scZ.value = selectedObject.scale.z.toFixed(2);

                        const colPick = document.getElementById("sidebar-color-picker");
                        if (colPick && selectedObject.userData.color) {
                            colPick.value = selectedObject.userData.color;
                        }
                    }

                    function bindTransformInputs() {
                        const onInput = () => {
                            if (!selectedObject) return;
                            const px = parseFloat(document.getElementById("num-pos-x").value) || 0;
                            const py = parseFloat(document.getElementById("num-pos-y").value) || 0;
                            const pz = parseFloat(document.getElementById("num-pos-z").value) || 0;
                            selectedObject.position.set(px, py, pz);

                            const rx = THREE.MathUtils.degToRad(parseFloat(document.getElementById("num-rot-x").value) || 0);
                            const ry = THREE.MathUtils.degToRad(parseFloat(document.getElementById("num-rot-y").value) || 0);
                            const rz = THREE.MathUtils.degToRad(parseFloat(document.getElementById("num-rot-z").value) || 0);
                            selectedObject.rotation.set(rx, ry, rz);

                            const sx = parseFloat(document.getElementById("num-scale-x").value) || 1;
                            const sy = parseFloat(document.getElementById("num-scale-y").value) || 1;
                            const sz = parseFloat(document.getElementById("num-scale-z").value) || 1;
                            selectedObject.scale.set(sx, sy, sz);

                            if (autoKeyEnabled) {
                                recordKeyframe(selectedObject, currentFrame);
                            } else {
                                syncSceneData();
                            }
                        };

                        ["num-pos-x", "num-pos-y", "num-pos-z", "num-rot-x", "num-rot-y", "num-rot-z", "num-scale-x", "num-scale-y", "num-scale-z"].forEach(id => {
                            const el = document.getElementById(id);
                            if (el) {
                                el.onfocus = () => pushUndoState("Manual Edit");
                                el.oninput = onInput;
                            }
                        });
                    }
                    bindTransformInputs();

                    // --- 6. KEYFRAMING & ANIMATION ---
                    function recordKeyframe(obj, frame) {
                        if (!obj) return;
                        const key = {
                            frame: frame,
                            position: [obj.position.x, obj.position.y, obj.position.z],
                            rotation: [obj.rotation.x, obj.rotation.y, obj.rotation.z],
                            scale: [obj.scale.x, obj.scale.y, obj.scale.z],
                            interp: currentInterp
                        };

                        if (!obj.userData.keyframes) obj.userData.keyframes = [];
                        const existingIdx = obj.userData.keyframes.findIndex(k => k.frame === frame);
                        if (existingIdx >= 0) {
                            obj.userData.keyframes[existingIdx] = key;
                        } else {
                            obj.userData.keyframes.push(key);
                            obj.userData.keyframes.sort((a, b) => a.frame - b.frame);
                        }
                        updateKeyMarkers();
                        syncSceneData();
                    }

                    function deleteKeyframe(obj, frame) {
                        if (!obj || !obj.userData.keyframes) return;
                        obj.userData.keyframes = obj.userData.keyframes.filter(k => k.frame !== frame);
                        updateKeyMarkers();
                        syncSceneData();
                    }

                    // Draggable Keyframe Diamonds along timeline
                    function updateKeyMarkers() {
                        const track = document.getElementById("key-markers-track");
                        if (!track) return;
                        track.innerHTML = "";
                        if (!selectedObject || !selectedObject.userData.keyframes) return;

                        selectedObject.userData.keyframes.forEach((k) => {
                            const marker = document.createElement("div");
                            marker.className = "vfx-key-diamond";
                            const pct = (k.frame / (totalFrames - 1)) * 100;
                            marker.style.left = `${pct}%`;
                            marker.title = `Keyframe ${k.frame} (Drag horizontally to move)`;

                            marker.onmousedown = (e) => {
                                e.stopPropagation();
                                e.preventDefault();
                                marker.classList.add("vfx-key-diamond-dragging");

                                const trackRect = track.getBoundingClientRect();

                                const onMouseMove = (moveEvt) => {
                                    const clickX = moveEvt.clientX - trackRect.left;
                                    const relPct = Math.max(0, Math.min(1, clickX / trackRect.width));
                                    const newFrame = Math.round(relPct * (totalFrames - 1));

                                    k.frame = newFrame;
                                    marker.style.left = `${(newFrame / (totalFrames - 1)) * 100}%`;
                                    marker.title = `Keyframe ${newFrame}`;

                                    currentFrame = newFrame;
                                    const timelineSlider = document.getElementById("timeline-slider");
                                    if (timelineSlider) timelineSlider.value = currentFrame;
                                    const hud = document.getElementById("hud-frame-label");
                                    if (hud) hud.innerText = `Frame: [ ${String(currentFrame).padStart(3, '0')} / ${totalFrames} ]`;

                                    evaluateAnimation(selectedObject, currentFrame);
                                    updateShotCameraPose(currentFrame);
                                    updateActorTexture(currentFrame);
                                    updateActorBillboardTransform();
                                    updateTransformInspector();
                                };

                                const onMouseUp = () => {
                                    marker.classList.remove("vfx-key-diamond-dragging");
                                    window.removeEventListener("mousemove", onMouseMove);
                                    window.removeEventListener("mouseup", onMouseUp);

                                    selectedObject.userData.keyframes.sort((a, b) => a.frame - b.frame);
                                    syncSceneData();
                                    updateKeyMarkers();
                                };

                                window.addEventListener("mousemove", onMouseMove);
                                window.addEventListener("mouseup", onMouseUp);
                            };

                            track.appendChild(marker);
                        });
                    }

                    function evaluateAnimation(obj, frame) {
                        const keys = obj.userData.keyframes;
                        if (!keys || keys.length === 0) return;

                        if (frame <= keys[0].frame) {
                            obj.position.fromArray(keys[0].position);
                            obj.rotation.set(...keys[0].rotation);
                            obj.scale.fromArray(keys[0].scale);
                            return;
                        }
                        if (frame >= keys[keys.length - 1].frame) {
                            const last = keys[keys.length - 1];
                            obj.position.fromArray(last.position);
                            obj.rotation.set(...last.rotation);
                            obj.scale.fromArray(last.scale);
                            return;
                        }

                        let k0 = keys[0];
                        let k1 = keys[keys.length - 1];
                        for (let i = 0; i < keys.length - 1; i++) {
                            if (keys[i].frame <= frame && frame <= keys[i + 1].frame) {
                                k0 = keys[i];
                                k1 = keys[i + 1];
                                break;
                            }
                        }

                        const span = Math.max(1, k1.frame - k0.frame);
                        let t = (frame - k0.frame) / span;

                        if (k0.interp === "step") {
                            t = 0;
                        } else if (k0.interp === "bezier") {
                            t = t * t * (3 - 2 * t);
                        }

                        obj.position.x = k0.position[0] + (k1.position[0] - k0.position[0]) * t;
                        obj.position.y = k0.position[1] + (k1.position[1] - k0.position[1]) * t;
                        obj.position.z = k0.position[2] + (k1.position[2] - k0.position[2]) * t;
                    }

                    function syncSceneData() {
                        sceneData = Array.from(threeObjectsMap.values()).map(m => ({
                            id: m.userData.id,
                            name: m.userData.name || m.userData.type,
                            type: m.userData.type,
                            color: m.userData.color || "#cccccc",
                            position: [m.position.x, m.position.y, m.position.z],
                            rotation: [m.rotation.x, m.rotation.y, m.rotation.z],
                            scale: [m.scale.x, m.scale.y, m.scale.z],
                            keyframes: m.userData.keyframes || []
                        }));
                        syncJsonWidget();
                    }

                    // --- 7. UI LISTENERS & ACTIONS ---
                    function applySelectedColor(hex) {
                        const targets = selectedObjects.size > 0 ? Array.from(selectedObjects) : (selectedObject ? [selectedObject] : []);
                        if (targets.length === 0) return;
                        pushUndoState(targets.length > 1 ? "Change Multi-Colors" : "Change Color");
                        targets.forEach(obj => {
                            if (obj.material && obj.material.color) {
                                obj.material.color.set(hex);
                            }
                            obj.userData.color = hex;
                        });
                        const picker = document.getElementById("sidebar-color-picker");
                        if (picker) picker.value = hex;
                        syncSceneData();
                    }

                    const sidebarColorPicker = document.getElementById("sidebar-color-picker");
                    if (sidebarColorPicker) sidebarColorPicker.oninput = (e) => applySelectedColor(e.target.value);
                    const swatchClay = document.getElementById("sidebar-swatch-clay");
                    if (swatchClay) swatchClay.onclick = () => applySelectedColor("#cccccc");
                    const swatchRed = document.getElementById("sidebar-swatch-red");
                    if (swatchRed) swatchRed.onclick = () => applySelectedColor("#e53935");
                    const swatchGreen = document.getElementById("sidebar-swatch-green");
                    if (swatchGreen) swatchGreen.onclick = () => applySelectedColor("#43a047");
                    const swatchBlue = document.getElementById("sidebar-swatch-blue");
                    if (swatchBlue) swatchBlue.onclick = () => applySelectedColor("#1e88e5");
                    const swatchGold = document.getElementById("sidebar-swatch-gold");
                    if (swatchGold) swatchGold.onclick = () => applySelectedColor("#fdd835");

                    document.getElementById("btn-add-cube").onclick = () => {
                        pushUndoState("Add Box");
                        const m = createPrimitiveMesh("cube", Date.now());
                        m.position.fromArray(getSpawnPosition());
                        scene.add(m);
                        threeObjectsMap.set(m.userData.id, m);
                        selectObject(m);
                        syncSceneData();
                    };
                    document.getElementById("btn-add-sphere").onclick = () => {
                        pushUndoState("Add Sphere");
                        const m = createPrimitiveMesh("sphere", Date.now());
                        m.position.fromArray(getSpawnPosition());
                        scene.add(m);
                        threeObjectsMap.set(m.userData.id, m);
                        selectObject(m);
                        syncSceneData();
                    };
                    document.getElementById("btn-add-cylinder").onclick = () => {
                        pushUndoState("Add Cylinder");
                        const m = createPrimitiveMesh("cylinder", Date.now());
                        m.position.fromArray(getSpawnPosition());
                        scene.add(m);
                        threeObjectsMap.set(m.userData.id, m);
                        selectObject(m);
                        syncSceneData();
                    };
                    document.getElementById("btn-add-cone").onclick = () => {
                        pushUndoState("Add Cone");
                        const m = createPrimitiveMesh("cone", Date.now());
                        m.position.fromArray(getSpawnPosition());
                        scene.add(m);
                        threeObjectsMap.set(m.userData.id, m);
                        selectObject(m);
                        syncSceneData();
                    };
                    document.getElementById("btn-add-torus").onclick = () => {
                        pushUndoState("Add Torus");
                        const m = createPrimitiveMesh("torus", Date.now());
                        m.position.fromArray(getSpawnPosition());
                        scene.add(m);
                        threeObjectsMap.set(m.userData.id, m);
                        selectObject(m);
                        syncSceneData();
                    };
                    document.getElementById("btn-add-plane").onclick = () => {
                        pushUndoState("Add Floor");
                        const m = createPrimitiveMesh("plane", Date.now());
                        m.position.fromArray(getSpawnPosition());
                        scene.add(m);
                        threeObjectsMap.set(m.userData.id, m);
                        selectObject(m);
                        syncSceneData();
                    };

                    document.getElementById("btn-add-markers").onclick = () => {
                        const existing = scene.getObjectByName("LTX_Calibration_Markers_3");
                        if (existing) {
                            existing.visible = !existing.visible;
                            logMsg(`3D Markers Grid (Markers_3.blend) visibility: ${existing.visible ? "ON" : "OFF"}`);
                            return;
                        }

                        // Exact match with Markers_3.blend:
                        // Sphere base radius 0.2m, 51x51x10 array = 26,010 spheres
                        // Spacing: X: 2.0m, Y: 2.0m, Z: 1.6m (Z-up), offset: X=-30, Y=-30, Z=0
                        const geo = new THREE.SphereGeometry(0.2, 8, 8);
                        const mat = new THREE.MeshStandardMaterial({
                            color: 0xffaa00,
                            roughness: 0.35,
                            metalness: 0.2,
                            emissive: 0x331500
                        });
                        const totalCount = 51 * 51 * 10;
                        const instancedMesh = new THREE.InstancedMesh(geo, mat, totalCount);
                        instancedMesh.name = "LTX_Calibration_Markers_3";

                        const dummy = new THREE.Object3D();
                        let idx = 0;
                        for (let z = 0; z < 10; z++) {
                            for (let y = 0; y < 51; y++) {
                                for (let x = 0; x < 51; x++) {
                                    dummy.position.set(-30.0 + x * 2.0, -30.0 + y * 2.0, z * 1.6);
                                    dummy.updateMatrix();
                                    instancedMesh.setMatrixAt(idx++, dummy.matrix);
                                }
                            }
                        }
                        instancedMesh.instanceMatrix.needsUpdate = true;
                        scene.add(instancedMesh);
                        logMsg("📍 Added 26,010 3D Calibration Markers (Markers_3.blend) to scene!");
                    };

                    // -------------------------------------------------------------
                    // 3-Point Plane Calibration & Alignment Tool
                    // -------------------------------------------------------------
                    function clear3PointSelection() {
                        picked3Points.length = 0;
                        snappedVertex = null;
                        snappedPointIndex = -1;
                        while (pickedMarkersGroup.children.length > 0) {
                            const child = pickedMarkersGroup.children[0];
                            pickedMarkersGroup.remove(child);
                            if (child.geometry) child.geometry.dispose();
                            if (child.material) {
                                if (Array.isArray(child.material)) child.material.forEach(m => m.dispose());
                                else child.material.dispose();
                            }
                        }
                        triangleLineMesh = null;
                        while (planePreviewGroup.children.length > 0) {
                            const child = planePreviewGroup.children[0];
                            planePreviewGroup.remove(child);
                            if (child.geometry) child.geometry.dispose();
                            if (child.material) {
                                if (Array.isArray(child.material)) child.material.forEach(m => m.dispose());
                                else child.material.dispose();
                            }
                        }
                        planePreviewGroup.visible = false;
                        isNormalFlipped = false;
                        hoverMarker.visible = false;

                        const statusEl = document.getElementById("vfx-3pt-status");
                        if (statusEl) statusEl.innerText = "🎯 Hover near points to snap (0 / 3)";
                        const actionsEl = document.getElementById("vfx-3pt-actions");
                        if (actionsEl) actionsEl.style.display = "none";
                    }

                    function build3PointPlanePreview() {
                        while (planePreviewGroup.children.length > 0) {
                            const child = planePreviewGroup.children[0];
                            planePreviewGroup.remove(child);
                            if (child.geometry) child.geometry.dispose();
                            if (child.material) {
                                if (Array.isArray(child.material)) child.material.forEach(m => m.dispose());
                                else child.material.dispose();
                            }
                        }

                        if (picked3Points.length < 3) return;

                        const p1 = picked3Points[0];
                        const p2 = picked3Points[1];
                        const p3 = picked3Points[2];

                        previewCenter.copy(p1).add(p2).add(p3).divideScalar(3);

                        const v1 = new THREE.Vector3().subVectors(p2, p1);
                        const v2 = new THREE.Vector3().subVectors(p3, p1);
                        previewNormal.crossVectors(v1, v2).normalize();

                        if (previewNormal.lengthSq() < 0.0001) {
                            previewNormal.set(0, 0, 1);
                        }

                        // Default orientation: align normal towards +Z (upward) unless flipped
                        if (!isNormalFlipped) {
                            if (previewNormal.z < 0) {
                                previewNormal.negate();
                            }
                        } else {
                            if (previewNormal.z > 0) {
                                previewNormal.negate();
                            }
                        }

                        const axisX = v1.clone().normalize();
                        if (axisX.lengthSq() < 0.0001) axisX.set(1, 0, 0);
                        const axisY = new THREE.Vector3().crossVectors(previewNormal, axisX).normalize();

                        const rotMatrix = new THREE.Matrix4().makeBasis(axisX, axisY, previewNormal);
                        previewQuat.setFromRotationMatrix(rotMatrix);

                        const d12 = p1.distanceTo(p2);
                        const d13 = p1.distanceTo(p3);
                        const d23 = p2.distanceTo(p3);
                        const maxSpan = Math.max(d12, d13, d23, 1.5);
                        previewSize = Math.max(4.0, Math.ceil(maxSpan * 2.2));

                        planePreviewGroup.position.copy(previewCenter);
                        planePreviewGroup.quaternion.copy(previewQuat);

                        // 1. Ghost Translucent Plane
                        const planeGeom = new THREE.PlaneGeometry(previewSize, previewSize, 10, 10);
                        const planeMat = new THREE.MeshBasicMaterial({
                            color: 0x00d4ff,
                            transparent: true,
                            opacity: 0.28,
                            side: THREE.DoubleSide,
                            depthWrite: false
                        });
                        const planeMesh = new THREE.Mesh(planeGeom, planeMat);
                        planePreviewGroup.add(planeMesh);

                        // 2. Glowing Wireframe Grid Overlay
                        const wireGeom = new THREE.WireframeGeometry(new THREE.PlaneGeometry(previewSize, previewSize, 10, 10));
                        const wireMat = new THREE.LineBasicMaterial({
                            color: 0x00ffff,
                            transparent: true,
                            opacity: 0.65
                        });
                        const wireMesh = new THREE.LineSegments(wireGeom, wireMat);
                        planePreviewGroup.add(wireMesh);

                        // 3. Normal Arrow Helper (+Z in local space)
                        const arrowLen = Math.max(0.8, previewSize * 0.25);
                        const arrowHelper = new THREE.ArrowHelper(
                            new THREE.Vector3(0, 0, 1),
                            new THREE.Vector3(0, 0, 0),
                            arrowLen,
                            0xffb300,
                            arrowLen * 0.25,
                            arrowLen * 0.12
                        );
                        planePreviewGroup.add(arrowHelper);

                        // 4. Center Anchor Sphere (pinpoint <= 0.01m)
                        const originGeom = new THREE.SphereGeometry(0.005, 12, 12);
                        const originMat = new THREE.MeshBasicMaterial({ color: 0xffb300 });
                        const originMesh = new THREE.Mesh(originGeom, originMat);
                        planePreviewGroup.add(originMesh);

                        planePreviewGroup.visible = true;
                    }

                    function addPickedPoint(pt, vertexIdx = -1) {
                        if (picked3Points.length >= 3) return;

                        // Prevent selecting the exact same point twice (distance < 0.005m)
                        for (let i = 0; i < picked3Points.length; i++) {
                            if (picked3Points[i].distanceTo(pt) < 0.005) {
                                const statusEl = document.getElementById("vfx-3pt-status");
                                if (statusEl) statusEl.innerText = `⚠️ Point already chosen! Pick a different vertex.`;
                                return;
                            }
                        }

                        picked3Points.push(pt);

                        const colors = [0xff3355, 0x33ff66, 0x3399ff];
                        const idx = picked3Points.length - 1;

                        // Pinpoint Solid Sphere Marker (<= 0.01m)
                        const markerMat = new THREE.MeshBasicMaterial({ color: colors[idx % 3] });
                        const markerGeom = new THREE.SphereGeometry(0.005, 12, 12);
                        const markerMesh = new THREE.Mesh(markerGeom, markerMat);
                        markerMesh.position.copy(pt);
                        pickedMarkersGroup.add(markerMesh);

                        if (picked3Points.length === 2) {
                            if (triangleLineMesh) pickedMarkersGroup.remove(triangleLineMesh);
                            const lineGeom = new THREE.BufferGeometry().setFromPoints([picked3Points[0], picked3Points[1]]);
                            const lineMat = new THREE.LineBasicMaterial({ color: 0xffbb00, linewidth: 2 });
                            triangleLineMesh = new THREE.Line(lineGeom, lineMat);
                            pickedMarkersGroup.add(triangleLineMesh);

                            const statusEl = document.getElementById("vfx-3pt-status");
                            if (statusEl) statusEl.innerText = "🎯 Point 2 set! Hover to snap 3rd point (2 / 3)";
                        } else if (picked3Points.length === 3) {
                            if (triangleLineMesh) pickedMarkersGroup.remove(triangleLineMesh);
                            const lineGeom = new THREE.BufferGeometry().setFromPoints([
                                picked3Points[0], picked3Points[1], picked3Points[2], picked3Points[0]
                            ]);
                            const lineMat = new THREE.LineBasicMaterial({ color: 0x00e5ff, linewidth: 2 });
                            triangleLineMesh = new THREE.Line(lineGeom, lineMat);
                            pickedMarkersGroup.add(triangleLineMesh);

                            build3PointPlanePreview();

                            const statusEl = document.getElementById("vfx-3pt-status");
                            if (statusEl) statusEl.innerText = "✅ 3 Points Selected! Previewing Plane:";
                            const actionsEl = document.getElementById("vfx-3pt-actions");
                            if (actionsEl) actionsEl.style.display = "flex";
                        } else {
                            const statusEl = document.getElementById("vfx-3pt-status");
                            if (statusEl) statusEl.innerText = "🎯 Point 1 set! Hover to snap 2nd point (1 / 3)";
                        }
                    }

                    function flipNormal() {
                        isNormalFlipped = !isNormalFlipped;
                        build3PointPlanePreview();
                    }

                    function createPermanentPlaneFrom3Points() {
                        if (picked3Points.length < 3) return;
                        pushUndoState("Create 3-Pt Ref Plane");

                        const newId = Date.now();
                        const planeMesh = createPrimitiveMesh("plane", newId, "#2979ff");
                        planeMesh.position.copy(previewCenter);
                        planeMesh.quaternion.copy(previewQuat);
                        const s = previewSize / 6.0;
                        planeMesh.scale.set(s, s, 1.0);
                        planeMesh.userData.name = `Ref_Plane_${threeObjectsMap.size + 1}`;

                        scene.add(planeMesh);
                        threeObjectsMap.set(planeMesh.userData.id, planeMesh);

                        exit3PointPicker();
                        selectObject(planeMesh);
                        syncSceneData();
                    }

                    function alignGridTo3Points() {
                        if (picked3Points.length < 3) return;
                        gridHelper.position.copy(previewCenter);
                        gridHelper.quaternion.copy(previewQuat);
                        gridHelper.rotateX(Math.PI / 2);

                        const hud = document.getElementById("hud-status");
                        if (hud) hud.innerText = "🎯 Stage Ground Grid aligned to 3-point plane orientation!";
                    }

                    function enter3PointPicker() {
                        if (!cloudMesh || !cloudMesh.geometry) {
                            alert("Point Cloud is not loaded in scene. Please ensure tracking solve generated 3D points.");
                            return;
                        }
                        isPicking3Points = true;
                        clear3PointSelection();
                        transformControls.detach();
                        selectObject(null);
                        const btn = document.getElementById("btn-tool-3pt-plane");
                        if (btn) btn.classList.add("vfx-btn-active");
                        const bar = document.getElementById("vfx-3pt-picker-bar");
                        if (bar) bar.style.display = "flex";
                    }

                    function exit3PointPicker() {
                        isPicking3Points = false;
                        clear3PointSelection();
                        const btn = document.getElementById("btn-tool-3pt-plane");
                        if (btn) btn.classList.remove("vfx-btn-active");
                        const bar = document.getElementById("vfx-3pt-picker-bar");
                        if (bar) bar.style.display = "none";
                        hoverMarker.visible = false;
                        renderer.domElement.style.cursor = 'default';
                    }

                    const btnTool3Pt = document.getElementById("btn-tool-3pt-plane");
                    if (btnTool3Pt) {
                        btnTool3Pt.onclick = () => {
                            if (isPicking3Points) exit3PointPicker();
                            else enter3PointPicker();
                        };
                    }

                    const btn3PtConfirm = document.getElementById("btn-3pt-confirm");
                    if (btn3PtConfirm) btn3PtConfirm.onclick = createPermanentPlaneFrom3Points;

                    const btn3PtFlip = document.getElementById("btn-3pt-flip");
                    if (btn3PtFlip) btn3PtFlip.onclick = flipNormal;

                    const btn3PtAlignGrid = document.getElementById("btn-3pt-align-grid");
                    if (btn3PtAlignGrid) btn3PtAlignGrid.onclick = alignGridTo3Points;

                    const btn3PtRepick = document.getElementById("btn-3pt-repick");
                    if (btn3PtRepick) btn3PtRepick.onclick = clear3PointSelection;

                    const btn3PtCancel = document.getElementById("btn-3pt-cancel");
                    if (btn3PtCancel) btn3PtCancel.onclick = exit3PointPicker;

                    // Import 3D Asset
                    const fileMeshInput = document.getElementById("file-mesh-input");
                    document.getElementById("btn-import-mesh").onclick = () => fileMeshInput.click();
                    fileMeshInput.onchange = (e) => {
                        const file = e.target.files[0];
                        if (!file) return;
                        const url = URL.createObjectURL(file);
                        const ext = file.name.split('.').pop().toLowerCase();

                        if (ext === "glb" || ext === "gltf") {
                            const loader = new THREE.GLTFLoader();
                            loader.load(url, (gltf) => {
                                const model = gltf.scene;
                                model.userData = { id: Date.now(), type: file.name, name: file.name, keyframes: [] };
                                scene.add(model);
                                threeObjectsMap.set(model.userData.id, model);
                                selectObject(model);
                                syncSceneData();
                            });
                        } else if (ext === "fbx") {
                            const loader = new THREE.FBXLoader();
                            loader.load(url, (fbx) => {
                                fbx.userData = { id: Date.now(), type: file.name, name: file.name, keyframes: [] };
                                fbx.scale.set(0.01, 0.01, 0.01);
                                scene.add(fbx);
                                threeObjectsMap.set(fbx.userData.id, fbx);
                                selectObject(fbx);
                                syncSceneData();
                            });
                        } else if (ext === "obj") {
                            const loader = new THREE.OBJLoader();
                            loader.load(url, (obj) => {
                                obj.userData = { id: Date.now(), type: file.name, name: file.name, keyframes: [] };
                                scene.add(obj);
                                threeObjectsMap.set(obj.userData.id, obj);
                                selectObject(obj);
                                syncSceneData();
                            });
                        }
                    };

                    // Auto-Key toggle
                    const btnAutoKey = document.getElementById("btn-autokey");
                    btnAutoKey.onclick = () => {
                        autoKeyEnabled = !autoKeyEnabled;
                        btnAutoKey.className = autoKeyEnabled ? "vfx-btn vfx-btn-active" : "vfx-btn";
                        btnAutoKey.innerText = autoKeyEnabled ? "● Auto-Key: ON" : "○ Auto-Key: OFF";
                    };

                    document.getElementById("btn-set-key").onclick = () => {
                        if (selectedObjects.size > 0) {
                            selectedObjects.forEach(obj => recordKeyframe(obj, currentFrame));
                        } else if (selectedObject) {
                            recordKeyframe(selectedObject, currentFrame);
                        }
                    };
                    document.getElementById("btn-del-key").onclick = () => {
                        if (selectedObjects.size > 0) {
                            selectedObjects.forEach(obj => deleteKeyframe(obj, currentFrame));
                        } else if (selectedObject) {
                            deleteKeyframe(selectedObject, currentFrame);
                        }
                    };

                    // Interpolation & Easing
                    const selInterp = document.getElementById("sel-interp");
                    selInterp.onchange = (e) => { currentInterp = e.target.value; };
                    document.getElementById("btn-ease-in").onclick = () => {
                        currentInterp = "bezier";
                        selInterp.value = "bezier";
                        const targets = selectedObjects.size > 0 ? Array.from(selectedObjects) : (selectedObject ? [selectedObject] : []);
                        targets.forEach(obj => recordKeyframe(obj, currentFrame));
                    };
                    document.getElementById("btn-ease-out").onclick = () => {
                        currentInterp = "bezier";
                        selInterp.value = "bezier";
                        const targets = selectedObjects.size > 0 ? Array.from(selectedObjects) : (selectedObject ? [selectedObject] : []);
                        targets.forEach(obj => recordKeyframe(obj, currentFrame));
                    };
                    document.getElementById("btn-ease-inout").onclick = () => {
                        currentInterp = "bezier";
                        selInterp.value = "bezier";
                        const targets = selectedObjects.size > 0 ? Array.from(selectedObjects) : (selectedObject ? [selectedObject] : []);
                        targets.forEach(obj => recordKeyframe(obj, currentFrame));
                    };

                    // Timeline Scrubber & Navigation Engine
                    const timelineSlider = document.getElementById("timeline-slider");
                    const hudFrameLabel = document.getElementById("hud-frame-label");

                    function setTimelineFrame(f) {
                        currentFrame = Math.max(0, Math.min(totalFrames - 1, f));
                        if (timelineSlider) timelineSlider.value = currentFrame;
                        if (hudFrameLabel) hudFrameLabel.innerText = `Frame: [ ${String(currentFrame).padStart(3, '0')} / ${totalFrames} ]`;
                        threeObjectsMap.forEach(obj => evaluateAnimation(obj, currentFrame));
                        updateShotCameraPose(currentFrame);
                        updateActorTexture(currentFrame);
                        updateActorBillboardTransform();
                        updateTransformInspector();
                        if (typeof updateBakedReviewFrame === "function") updateBakedReviewFrame(currentFrame);
                    }

                    function stepFrame(delta) {
                        setTimelineFrame(currentFrame + delta);
                    }

                    function getKeyframeList() {
                        const keySet = new Set();
                        if (selectedObject && selectedObject.userData && selectedObject.userData.keyframes && selectedObject.userData.keyframes.length > 0) {
                            selectedObject.userData.keyframes.forEach(k => keySet.add(k.frame));
                        } else {
                            threeObjectsMap.forEach(obj => {
                                if (obj.userData && obj.userData.keyframes) {
                                    obj.userData.keyframes.forEach(k => keySet.add(k.frame));
                                }
                            });
                        }
                        return Array.from(keySet).sort((a, b) => a - b);
                    }

                    function jumpKeyframe(direction) {
                        const keys = getKeyframeList();
                        if (keys.length === 0) return;
                        if (direction < 0) {
                            const prevKeys = keys.filter(f => f < currentFrame);
                            if (prevKeys.length > 0) {
                                setTimelineFrame(prevKeys[prevKeys.length - 1]);
                            } else {
                                setTimelineFrame(keys[0]);
                            }
                        } else {
                            const nextKeys = keys.filter(f => f > currentFrame);
                            if (nextKeys.length > 0) {
                                setTimelineFrame(nextKeys[0]);
                            } else {
                                setTimelineFrame(keys[keys.length - 1]);
                            }
                        }
                    }

                    timelineSlider.oninput = (e) => {
                        setTimelineFrame(parseInt(e.target.value));
                    };

                    const btnFirstFrame = document.getElementById("btn-first-frame");
                    if (btnFirstFrame) btnFirstFrame.onclick = () => setTimelineFrame(0);

                    const btnLastFrame = document.getElementById("btn-last-frame");
                    if (btnLastFrame) btnLastFrame.onclick = () => setTimelineFrame(totalFrames - 1);

                    const btnPrevFrame = document.getElementById("btn-prev-frame");
                    if (btnPrevFrame) btnPrevFrame.onclick = () => stepFrame(-1);

                    const btnNextFrame = document.getElementById("btn-next-frame");
                    if (btnNextFrame) btnNextFrame.onclick = () => stepFrame(1);

                    const btnPrevKey = document.getElementById("btn-prev-key");
                    if (btnPrevKey) btnPrevKey.onclick = () => jumpKeyframe(-1);

                    const btnNextKey = document.getElementById("btn-next-key");
                    if (btnNextKey) btnNextKey.onclick = () => jumpKeyframe(1);

                    const btnPlayPause = document.getElementById("btn-play-pause");
                    function toggleTimelinePlay() {
                        isPlaying = !isPlaying;
                        btnPlayPause.innerText = isPlaying ? "⏸ Pause" : "▶ Play";
                    }
                    btnPlayPause.onclick = toggleTimelinePlay;

                    // Viewport Mode Buttons
                    document.getElementById("btn-coord-space").onclick = toggleCoordSpace;
                    document.getElementById("btn-cam-view").onclick = toggleCameraView;

                    // Tool mode buttons
                    document.getElementById("btn-tool-select").onclick = () => setToolMode("select");
                    document.getElementById("btn-tool-translate").onclick = () => setToolMode("translate");
                    document.getElementById("btn-tool-rotate").onclick = () => setToolMode("rotate");
                    document.getElementById("btn-tool-scale").onclick = () => setToolMode("scale");

                    // Transform & Object Utility Buttons
                    const btnDuplicateObj = document.getElementById("btn-duplicate-obj");
                    if (btnDuplicateObj) btnDuplicateObj.onclick = duplicateSelectedObject;

                    const btnCopyXform = document.getElementById("btn-copy-xform");
                    if (btnCopyXform) btnCopyXform.onclick = copySelectedTransforms;

                    const btnPasteXform = document.getElementById("btn-paste-xform");
                    if (btnPasteXform) btnPasteXform.onclick = pasteTransformsToSelected;

                    const btnResetXform = document.getElementById("btn-reset-xform");
                    if (btnResetXform) btnResetXform.onclick = resetSelectedTransforms;

                    const btnUndoTop = document.getElementById("btn-undo-top");
                    if (btnUndoTop) btnUndoTop.onclick = undo;

                    // Actor Cutout Billboard Controls
                    const btnToggleActor = document.getElementById("btn-toggle-actor");
                    if (btnToggleActor) {
                        btnToggleActor.onclick = () => {
                            isActorProjectionVisible = !isActorProjectionVisible;
                            btnToggleActor.className = isActorProjectionVisible ? "vfx-btn vfx-btn-active" : "vfx-btn";
                            btnToggleActor.innerText = isActorProjectionVisible ? "Actor: ON" : "Actor: OFF";
                            actorBillboard.visible = isActorProjectionVisible;
                            if (isActorProjectionVisible) {
                                updateActorTexture(currentFrame);
                                updateActorBillboardTransform();
                            }
                        };
                    }

                    const sliderActorDist = document.getElementById("slider-actor-dist");
                    if (sliderActorDist) {
                        sliderActorDist.oninput = (e) => {
                            actorDistance = parseFloat(e.target.value);
                            updateActorBillboardTransform();
                        };
                    }

                    const sliderActorAlpha = document.getElementById("slider-actor-alpha");
                    if (sliderActorAlpha) {
                        sliderActorAlpha.oninput = (e) => {
                            actorOpacity = parseFloat(e.target.value);
                            actorMaterial.opacity = actorOpacity;
                        };
                    }

                    // Sidebar Pipeline Settings Binding
                    const selRenderMode = document.getElementById("sidebar-render-mode");
                    const selWorkflowGate = document.getElementById("sidebar-workflow-gate");
                    const sliderSmoothing = document.getElementById("sidebar-cam-smoothing");

                    const wRenderMode = node.widgets && node.widgets.find(w => w.name === "render_mode");
                    const wWorkflowGate = node.widgets && node.widgets.find(w => w.name === "workflow_gate");
                    const wSmoothing = node.widgets && node.widgets.find(w => w.name === "camera_smoothing");

                    if (wRenderMode && selRenderMode) {
                        selRenderMode.value = wRenderMode.value;
                        selRenderMode.onchange = () => { wRenderMode.value = selRenderMode.value; };
                    }
                    if (wWorkflowGate && selWorkflowGate) {
                        selWorkflowGate.value = wWorkflowGate.value;
                        selWorkflowGate.onchange = () => { wWorkflowGate.value = selWorkflowGate.value; };
                    }
                    if (wSmoothing && sliderSmoothing) {
                        sliderSmoothing.value = wSmoothing.value;
                        sliderSmoothing.oninput = () => { wSmoothing.value = parseFloat(sliderSmoothing.value); };
                    }

                    // --- 7. HARDWARE-ACCELERATED IN-NODE WEBGL RENDER & REVIEW PIPELINE ---
                    let bakedRenderFrames = [];
                    let isRenderComplete = false;
                    let isRenderingSequence = false;
                    let renderCancelRequested = false;
                    let activeMonitorMode = "3d";
                    let bakedPlateBlend = 0.0;

                    const hdrRenderBadge = document.getElementById("hdr-render-badge");
                    const btnRenderTop = document.getElementById("btn-render-layout-top");
                    const btnStartRenderSide = document.getElementById("btn-start-layout-render");
                    const cardProgress = document.getElementById("render-progress-card");
                    const lblProgress = document.getElementById("render-progress-label");
                    const fillProgress = document.getElementById("render-progress-fill");
                    const btnCancelRender = document.getElementById("btn-cancel-render");
                    const cardReview = document.getElementById("render-review-card");
                    const btnReviewPlay = document.getElementById("btn-review-toggle-playback");
                    const btnReRender = document.getElementById("btn-re-render");

                    const btnMonMode3D = document.getElementById("btn-mon-mode-3d");
                    const btnMonModeBaked = document.getElementById("btn-mon-mode-baked");
                    const bakedImg = document.getElementById("baked-render-preview-img");
                    const underlayImg = document.getElementById("baked-plate-underlay");
                    const compareWrap = document.getElementById("monitor-compare-wrap");
                    const sliderBakedBlend = document.getElementById("slider-baked-blend");
                    const lblBakedBlend = document.getElementById("lbl-baked-blend");

                    const btnQueueTop = document.getElementById("btn-queue-render-top");
                    const btnQueueSide = document.getElementById("btn-queue-render");

                    function setMonitorMode(mode) {
                        activeMonitorMode = mode;
                        if (mode === "baked") {
                            if (bakedRenderFrames.length === 0) {
                                startInNodeRender();
                                return;
                            }
                            if (btnMonMode3D) btnMonMode3D.classList.remove("vfx-pill-active");
                            if (btnMonModeBaked) btnMonModeBaked.classList.add("vfx-pill-active");
                            if (bakedImg) {
                                bakedImg.style.display = "block";
                                bakedImg.src = bakedRenderFrames[currentFrame] || "";
                            }
                            if (compareWrap) compareWrap.style.display = "flex";
                            if (underlayImg) {
                                underlayImg.style.display = (bakedPlateBlend > 0.01) ? "block" : "none";
                                underlayImg.style.opacity = bakedPlateBlend;
                                underlayImg.src = `/vfx/actor_cutout/${currentFrame}`;
                            }
                        } else {
                            if (btnMonModeBaked) btnMonModeBaked.classList.remove("vfx-pill-active");
                            if (btnMonMode3D) btnMonMode3D.classList.add("vfx-pill-active");
                            if (bakedImg) bakedImg.style.display = "none";
                            if (underlayImg) underlayImg.style.display = "none";
                            if (compareWrap) compareWrap.style.display = "none";
                        }
                    }

                    if (btnMonMode3D) btnMonMode3D.onclick = () => setMonitorMode("3d");
                    if (btnMonModeBaked) btnMonModeBaked.onclick = () => setMonitorMode("baked");

                    if (sliderBakedBlend) {
                        sliderBakedBlend.oninput = (e) => {
                            bakedPlateBlend = parseFloat(e.target.value);
                            if (lblBakedBlend) lblBakedBlend.innerText = `${Math.round(bakedPlateBlend * 100)}%`;
                            if (underlayImg) {
                                underlayImg.style.display = (bakedPlateBlend > 0.01) ? "block" : "none";
                                underlayImg.style.opacity = bakedPlateBlend;
                                underlayImg.src = `/vfx/actor_cutout/${currentFrame}`;
                            }
                        };
                    }

                    function updateBakedReviewFrame(f) {
                        if (activeMonitorMode === "baked") {
                            if (bakedImg && bakedRenderFrames[f]) {
                                bakedImg.src = bakedRenderFrames[f];
                            }
                            if (underlayImg && bakedPlateBlend > 0.01) {
                                underlayImg.src = `/vfx/actor_cutout/${f}`;
                            }
                        }
                    }

                    async function uploadBakedFrames(frames) {
                        try {
                            const batchSize = 15;
                            for (let i = 0; i < frames.length; i += batchSize) {
                                const batch = [];
                                for (let j = i; j < Math.min(i + batchSize, frames.length); j++) {
                                    batch.push({ frame: j, image: frames[j] });
                                }
                                await fetch("/vfx/save_layout_batch", {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({
                                        frames: batch,
                                        manifest: sceneData,
                                        total_frames: totalFrames
                                    })
                                });
                            }
                            console.log("[ComfyUI-VFX-Suite] Successfully saved baked layout frames to backend cache");
                        } catch (e) {
                            console.error("[ComfyUI-VFX-Suite] Error saving baked layout frames:", e);
                        }
                    }

                    async function startInNodeRender() {
                        if (isRenderingSequence) return;
                        isRenderingSequence = true;
                        renderCancelRequested = false;

                        // UI Setup
                        if (cardProgress) cardProgress.style.display = "flex";
                        if (cardReview) cardReview.style.display = "none";
                        if (btnStartRenderSide) btnStartRenderSide.style.display = "none";
                        if (fillProgress) fillProgress.style.width = "0%";
                        if (lblProgress) lblProgress.innerText = "Render: preparing buffer...";
                        if (hdrRenderBadge) {
                            hdrRenderBadge.className = "vfx-render-badge-pill";
                            hdrRenderBadge.style.background = "rgba(255, 87, 34, 0.2)";
                            hdrRenderBadge.style.color = "#ff5722";
                            hdrRenderBadge.style.border = "1px solid #ff5722";
                            hdrRenderBadge.innerText = "⏳ Rendering...";
                        }

                        // Switch monitor to live 3D view so user sees frames baking in real time
                        setMonitorMode("3d");

                        // Determine Resolution
                        let rW = 1280;
                        let rH = Math.round(1280 / plateAspect);
                        const resSelect = document.getElementById("sidebar-render-res");
                        const resVal = resSelect ? resSelect.value : "1.0";
                        if (resVal === "1.0" && plateWidth > 0 && plateHeight > 0) {
                            rW = plateWidth;
                            rH = plateHeight;
                        } else if (resVal === "0.5" && plateWidth > 0 && plateHeight > 0) {
                            rW = Math.round(plateWidth * 0.5);
                            rH = Math.round(plateHeight * 0.5);
                        } else if (resVal === "720p") {
                            rW = 1280;
                            rH = 720;
                        }

                        // Offscreen isolated WebGL Renderer
                        const offCanvas = document.createElement("canvas");
                        offCanvas.width = rW;
                        offCanvas.height = rH;
                        const offRenderer = new THREE.WebGLRenderer({
                            canvas: offCanvas,
                            antialias: true,
                            preserveDrawingBuffer: true,
                            alpha: true
                        });
                        offRenderer.setSize(rW, rH, false);

                        // Save helper visibility
                        const wasFrustumVis = camFrustumHelper ? camFrustumHelper.visible : false;
                        const wasBodyVis = camBodyMesh ? camBodyMesh.visible : false;
                        const wasGridVis = gridHelper ? gridHelper.visible : false;
                        const wasPathVis = cameraPathLine ? cameraPathLine.visible : false;

                        if (camFrustumHelper) camFrustumHelper.visible = false;
                        if (camBodyMesh) camBodyMesh.visible = false;
                        selectionBoxesMap.forEach(box => { box.visible = false; });
                        if (gridHelper) gridHelper.visible = false;
                        if (cameraPathLine) cameraPathLine.visible = false;
                        transformControls.detach();

                        // Render Mode Shading Materials setup if needed
                        const curRenderMode = (document.getElementById("sidebar-render-mode")?.value) || "Clay (Lambertian Studio)";
                        const origMaterials = new Map();
                        if (curRenderMode === "Layout Mask") {
                            scene.background = new THREE.Color(0x000000);
                            threeObjectsMap.forEach((mesh, id) => {
                                origMaterials.set(id, mesh.material);
                                mesh.material = new THREE.MeshBasicMaterial({ color: 0xffffff });
                            });
                        } else if (curRenderMode === "Surface Normals") {
                            threeObjectsMap.forEach((mesh, id) => {
                                origMaterials.set(id, mesh.material);
                                mesh.material = new THREE.MeshNormalMaterial();
                            });
                        } else if (curRenderMode === "Depth Map") {
                            threeObjectsMap.forEach((mesh, id) => {
                                origMaterials.set(id, mesh.material);
                                mesh.material = new THREE.MeshDepthMaterial();
                            });
                        }

                        shotCamera.aspect = rW / rH;
                        shotCamera.updateProjectionMatrix();

                        const newFrames = new Array(totalFrames);
                        const startTime = performance.now();

                        for (let f = 0; f < totalFrames; f++) {
                            if (renderCancelRequested) break;

                            currentFrame = f;
                            timelineSlider.value = f;
                            hudFrameLabel.innerText = `Frame: [ ${String(f).padStart(3, '0')} / ${totalFrames} ]`;

                            threeObjectsMap.forEach(obj => evaluateAnimation(obj, f));
                            updateShotCameraPose(f);
                            updateActorTexture(f);
                            updateActorBillboardTransform();

                            offRenderer.render(scene, shotCamera);
                            const frameData = offCanvas.toDataURL("image/png");
                            newFrames[f] = frameData;

                            const pct = Math.round(((f + 1) / totalFrames) * 100);
                            const elapsedSec = (performance.now() - startTime) / 1000.0;
                            const fps = (f + 1) / Math.max(0.01, elapsedSec);
                            const remainingSec = Math.max(0, (totalFrames - (f + 1)) / Math.max(0.1, fps)).toFixed(1);

                            if (fillProgress) fillProgress.style.width = `${pct}%`;
                            if (lblProgress) lblProgress.innerText = `Render: frame ${f + 1} / ${totalFrames} (${pct}%) • ~${remainingSec}s`;

                            // Yield to browser event loop
                            await new Promise(r => setTimeout(r, 0));
                        }

                        // Restore materials
                        if (origMaterials.size > 0) {
                            threeObjectsMap.forEach((mesh, id) => {
                                if (origMaterials.has(id)) mesh.material = origMaterials.get(id);
                            });
                            setEnvironmentColor(currentEnvColor);
                        }

                        // Restore helpers
                        if (camFrustumHelper) camFrustumHelper.visible = wasFrustumVis;
                        if (camBodyMesh) camBodyMesh.visible = wasBodyVis;
                        if (gridHelper) gridHelper.visible = isGridVisible;
                        if (cameraPathLine) cameraPathLine.visible = wasPathVis;
                        if (selectedObject) selectObject(selectedObject);
                        updateSelectionBoxes();

                        offRenderer.dispose();

                        if (renderCancelRequested) {
                            isRenderingSequence = false;
                            if (lblProgress) lblProgress.innerText = "Render cancelled by user";
                            setTimeout(() => {
                                if (cardProgress) cardProgress.style.display = "none";
                                if (btnStartRenderSide) btnStartRenderSide.style.display = "block";
                            }, 1200);
                            if (hdrRenderBadge) {
                                hdrRenderBadge.className = "vfx-render-badge-pill vfx-badge-draft";
                                hdrRenderBadge.style = "";
                                hdrRenderBadge.innerText = "○ Draft (Unbaked)";
                            }
                            return;
                        }

                        // Success Complete
                        bakedRenderFrames = newFrames;
                        isRenderComplete = true;
                        isRenderingSequence = false;

                        if (cardProgress) cardProgress.style.display = "none";
                        if (cardReview) cardReview.style.display = "flex";
                        if (btnStartRenderSide) btnStartRenderSide.style.display = "none";

                        if (hdrRenderBadge) {
                            hdrRenderBadge.className = "vfx-render-badge-pill vfx-badge-ready";
                            hdrRenderBadge.style = "";
                            hdrRenderBadge.innerText = `✅ Baked (${totalFrames} f)`;
                        }

                        if (btnQueueTop) {
                            btnQueueTop.classList.add("vfx-btn-proceed-ready");
                            btnQueueTop.innerText = "🚀 Proceed / Run Pipeline (Ready)";
                        }
                        if (btnQueueSide) {
                            btnQueueSide.classList.add("vfx-btn-proceed-ready");
                            btnQueueSide.innerText = "🚀 Proceed / Run Pipeline (Ready)";
                        }

                        // Switch camera monitor to Baked View for immediate visual review!
                        setMonitorMode("baked");

                        // Auto-start review playback
                        isPlaying = true;
                        btnPlayPause.innerText = "⏸ Pause";

                        // Update node thumbnail on canvas
                        if (bakedRenderFrames.length > 0 && bakedRenderFrames[0]) {
                            const img = new Image();
                            img.onload = () => {
                                node._vfx_preview_img = img;
                                if (app.graph) app.graph.setDirtyCanvas(true, true);
                            };
                            img.src = bakedRenderFrames[0];
                        }

                        // Background upload to server cache
                        uploadBakedFrames(bakedRenderFrames);
                    }

                    if (btnRenderTop) btnRenderTop.onclick = startInNodeRender;
                    if (btnStartRenderSide) btnStartRenderSide.onclick = startInNodeRender;
                    if (btnReRender) btnReRender.onclick = startInNodeRender;
                    if (btnCancelRender) btnCancelRender.onclick = () => { renderCancelRequested = true; };

                    if (btnReviewPlay) {
                        btnReviewPlay.onclick = () => {
                            if (activeMonitorMode !== "baked") setMonitorMode("baked");
                            toggleTimelinePlay();
                        };
                    }

                    // Save & Queue Render directly from 3D Viewport
                    const onQueueRender = async () => {
                        if (!isRenderComplete && bakedRenderFrames.length === 0) {
                            const doRender = confirm("Layout render has not been executed yet.\nRun quick render now before launching the pipeline?");
                            if (doRender) {
                                await startInNodeRender();
                            }
                        }
                        syncSceneData();
                        if (wWorkflowGate && wWorkflowGate.value === "Interactive (Always Pause for Staging)") {
                            wWorkflowGate.value = "Direct Render (Queue)";
                        }
                        closeModal();
                        if (app && typeof app.queuePrompt === "function") {
                            app.queuePrompt(0);
                        }
                    };

                    if (btnQueueTop) btnQueueTop.onclick = onQueueRender;
                    if (btnQueueSide) btnQueueSide.onclick = onQueueRender;


                    // Environment Color & Stage Presets
                    let currentEnvColor = "#0a0a0d";
                    function setEnvironmentColor(hex) {
                        currentEnvColor = hex;
                        scene.background = new THREE.Color(hex);
                        const p1 = document.getElementById("env-bg-color-picker");
                        if (p1) p1.value = hex;
                        const p2 = document.getElementById("sidebar-sky-picker");
                        if (p2) p2.value = hex;

                        // Adapt grid brightness for high contrast against background
                        const col = new THREE.Color(hex);
                        const lum = col.r * 0.299 + col.g * 0.587 + col.b * 0.114;
                        if (gridHelper) {
                            if (lum > 0.35) {
                                gridHelper.material.color.setHex(0x1a2a44);
                            } else {
                                gridHelper.material.color.setHex(0x4f80ff);
                            }
                        }
                    }

                    // Inspector Sky Color row listeners
                    const sideSkyPicker = document.getElementById("sidebar-sky-picker");
                    if (sideSkyPicker) sideSkyPicker.oninput = (e) => setEnvironmentColor(e.target.value);
                    const sideSkyDark = document.getElementById("sidebar-sky-dark");
                    if (sideSkyDark) sideSkyDark.onclick = () => setEnvironmentColor("#0a0a0d");
                    const sideSkySlate = document.getElementById("sidebar-sky-slate");
                    if (sideSkySlate) sideSkySlate.onclick = () => setEnvironmentColor("#181b24");
                    const sideSkyBlue = document.getElementById("sidebar-sky-blue");
                    if (sideSkyBlue) sideSkyBlue.onclick = () => setEnvironmentColor("#1e3a5f");
                    const sideSkyDay = document.getElementById("sidebar-sky-day");
                    if (sideSkyDay) sideSkyDay.onclick = () => setEnvironmentColor("#4a75a0");
                    const sideSkyClay = document.getElementById("sidebar-sky-clay");
                    if (sideSkyClay) sideSkyClay.onclick = () => setEnvironmentColor("#4a4542");

                    // Environment Section listeners
                    const envBgPicker = document.getElementById("env-bg-color-picker");
                    if (envBgPicker) envBgPicker.oninput = (e) => setEnvironmentColor(e.target.value);
                    const swatchDark = document.getElementById("env-swatch-dark");
                    if (swatchDark) swatchDark.onclick = () => setEnvironmentColor("#0a0a0d");
                    const swatchSlate = document.getElementById("env-swatch-slate");
                    if (swatchSlate) swatchSlate.onclick = () => setEnvironmentColor("#181b24");
                    const swatchGray = document.getElementById("env-swatch-gray");
                    if (swatchGray) swatchGray.onclick = () => setEnvironmentColor("#2d303d");
                    const swatchClayStage = document.getElementById("env-swatch-clay");
                    if (swatchClayStage) swatchClayStage.onclick = () => setEnvironmentColor("#4a4542");
                    const swatchLight = document.getElementById("env-swatch-light");
                    if (swatchLight) swatchLight.onclick = () => setEnvironmentColor("#727788");

                    setEnvironmentColor(currentEnvColor);

                    let isGridVisible = true;
                    const btnToggleGrid = document.getElementById("btn-toggle-grid");
                    if (btnToggleGrid) {
                        btnToggleGrid.onclick = () => {
                            isGridVisible = !isGridVisible;
                            if (gridHelper) gridHelper.visible = isGridVisible;
                            btnToggleGrid.innerText = isGridVisible ? "Grid: Visible" : "Grid: Hidden";
                            btnToggleGrid.className = isGridVisible ? "vfx-btn" : "vfx-btn vfx-btn-active";
                        };
                    }

                    let isDampingEnabled = false;
                    const btnToggleDamping = document.getElementById("btn-toggle-damping");
                    if (btnToggleDamping) {
                        btnToggleDamping.onclick = () => {
                            isDampingEnabled = !isDampingEnabled;
                            orbitControls.enableDamping = isDampingEnabled;
                            orbitControls.dampingFactor = isDampingEnabled ? 0.05 : 0;
                            btnToggleDamping.innerText = isDampingEnabled ? "Inertia: ON" : "Inertia: OFF (Fixed)";
                            btnToggleDamping.className = isDampingEnabled ? "vfx-btn vfx-btn-active" : "vfx-btn";
                        };
                    }

                    // Point Cloud Particle Size & Alpha Sliders
                    const sliderCloudSize = document.getElementById("slider-cloud-size");
                    const lblCloudSize = document.getElementById("lbl-cloud-size");
                    if (sliderCloudSize) {
                        sliderCloudSize.oninput = (e) => {
                            cloudPointSize = parseFloat(e.target.value);
                            if (lblCloudSize) lblCloudSize.innerText = cloudPointSize.toFixed(2);
                            if (pointCloudMaterial) {
                                pointCloudMaterial.size = cloudPointSize;
                                pointCloudMaterial.needsUpdate = true;
                            }
                        };
                    }

                    const sliderCloudAlpha = document.getElementById("slider-cloud-alpha");
                    const lblCloudAlpha = document.getElementById("lbl-cloud-alpha");
                    if (sliderCloudAlpha) {
                        sliderCloudAlpha.oninput = (e) => {
                            cloudPointAlpha = parseFloat(e.target.value);
                            if (lblCloudAlpha) lblCloudAlpha.innerText = `${Math.round(cloudPointAlpha * 100)}%`;
                            if (pointCloudMaterial) {
                                pointCloudMaterial.opacity = cloudPointAlpha;
                                pointCloudMaterial.visible = cloudPointAlpha > 0.01;
                                pointCloudMaterial.needsUpdate = true;
                            }
                            if (cloudMesh) {
                                cloudMesh.visible = cloudPointAlpha > 0.01;
                            }
                        };
                    }

                    // Framing Target in View
                    function frameSelectedObject() {
                        let targetPos = new THREE.Vector3(0, 0, 0);
                        if (selectedObject) {
                            targetPos.copy(selectedObject.position);
                        } else if (cloudMesh && cloudMesh.geometry.boundingSphere) {
                            targetPos.copy(cloudMesh.geometry.boundingSphere.center);
                        } else if (cameraMatrices.length > 0) {
                            const raw = cameraMatrices[0];
                            targetPos.set(raw[0][3], raw[1][3] + 2.5, raw[2][3]);
                        }
                        const offset = new THREE.Vector3(2.5, -4.0, 2.0);
                        flyCamera.position.copy(targetPos).add(offset);
                        flyCamera.lookAt(targetPos);
                        orbitControls.target.copy(targetPos);
                        orbitControls.update();
                    }

                    const btnFrameShot = document.getElementById("btn-frame-shot");
                    if (btnFrameShot) btnFrameShot.onclick = frameSelectedObject;

                    // Collapsible Accordion Sections (▼ / ▶)
                    function setupCollapsible(hdrId, bodyId) {
                        const hdr = document.getElementById(hdrId);
                        const body = document.getElementById(bodyId);
                        if (!hdr || !body) return;
                        const arrow = hdr.querySelector(".vfx-collapse-arrow");
                        hdr.addEventListener("click", () => {
                            const isCollapsed = body.classList.toggle("vfx-section-collapsed");
                            if (arrow) arrow.textContent = isCollapsed ? "▶" : "▼";
                        });
                    }

                    setupCollapsible("hdr-tools", "body-tools");
                    setupCollapsible("hdr-outliner", "body-outliner");
                    setupCollapsible("hdr-inspector", "body-inspector");
                    setupCollapsible("hdr-env", "body-env");
                    setupCollapsible("hdr-actor", "body-actor");
                    setupCollapsible("hdr-pipeline", "body-pipeline");

                    // Resizable Studio Splitters
                    let isDraggingSplitterV = false;
                    splitterV.addEventListener("mousedown", (e) => {
                        e.preventDefault();
                        isDraggingSplitterV = true;
                        splitterV.classList.add("vfx-splitter-active");
                        document.body.style.cursor = "col-resize";
                        document.body.style.userSelect = "none";
                    });

                    const splitterH = document.getElementById("splitter-studio-h");
                    const controlsTopPane = document.getElementById("controls-top-pane");
                    const paneCamMonitor = document.getElementById("pane-cam-monitor");
                    let isDraggingSplitterH = false;

                    if (splitterH && controlsTopPane && paneCamMonitor) {
                        splitterH.addEventListener("mousedown", (e) => {
                            e.preventDefault();
                            isDraggingSplitterH = true;
                            splitterH.classList.add("vfx-splitter-active");
                            document.body.style.cursor = "row-resize";
                            document.body.style.userSelect = "none";
                        });
                    }

                    const onSplitterMouseMove = (e) => {
                        if (isDraggingSplitterV) {
                            const hostRect = canvasHost.getBoundingClientRect();
                            const newW = Math.max(260, Math.min(hostRect.width - 200, hostRect.right - e.clientX));
                            studioRightCol.style.width = `${newW}px`;
                        }
                        if (isDraggingSplitterH && paneCamMonitor && controlsTopPane) {
                            const colRect = studioRightCol.getBoundingClientRect();
                            const newMonH = Math.max(140, Math.min(colRect.height - 140, colRect.bottom - e.clientY));
                            paneCamMonitor.style.height = `${newMonH}px`;
                            paneCamMonitor.style.flex = "none";
                            controlsTopPane.style.flex = "1";
                        }
                    };

                    const onSplitterMouseUp = () => {
                        if (isDraggingSplitterV) {
                            isDraggingSplitterV = false;
                            splitterV.classList.remove("vfx-splitter-active");
                        }
                        if (isDraggingSplitterH && splitterH) {
                            isDraggingSplitterH = false;
                            splitterH.classList.remove("vfx-splitter-active");
                        }
                        document.body.style.cursor = "";
                        document.body.style.userSelect = "";
                    };

                    window.addEventListener("mousemove", onSplitterMouseMove);
                    window.addEventListener("mouseup", onSplitterMouseUp);

                    // Close Modal
                    const closeModal = () => {
                        cancelAnimationFrame(animId);
                        window.removeEventListener("resize", handleResize);
                        window.removeEventListener("mousemove", onSplitterMouseMove);
                        window.removeEventListener("mouseup", onSplitterMouseUp);
                        // Save latest camera view snapshot as node preview
                        try {
                            if (camBodyMesh) camBodyMesh.visible = false;
                            if (camFrustumHelper) camFrustumHelper.visible = false;
                            if (cameraPathLine) cameraPathLine.visible = false;
                            renderer.render(scene, shotCamera);
                            const thumbUrl = renderer.domElement.toDataURL("image/jpeg", 0.75);
                            const img = new Image();
                            img.onload = () => {
                                node._vfx_preview_img = img;
                                if (app.graph) app.graph.setDirtyCanvas(true, true);
                            };
                            img.src = thumbUrl;
                        } catch (e) {}

                        modal.remove();
                    };
                    document.getElementById("btn-close-modal").onclick = closeModal;

                    const handleResize = () => {
                        const w = canvasHost.clientWidth || window.innerWidth;
                        const h = canvasHost.clientHeight || (window.innerHeight - 100);
                        renderer.setSize(w, h, false);
                    };
                    window.addEventListener("resize", handleResize);

                    // Initialize Outliner
                    updateOutliner();

                    // --- 8. MAIN RENDER LOOP (LEFT 3D PERSPECTIVE + RIGHT LETTERBOXED CAMERA MONITOR) ---
                    let lastTime = performance.now();
                    let animId;

                    function animate(now) {
                        animId = requestAnimationFrame(animate);
                        const dt = Math.min(0.1, (now - lastTime) / 1000.0);
                        lastTime = now;

                        updateFlycam(dt);

                        if (isPlaying) {
                            currentFrame = (currentFrame + 1) % totalFrames;
                            timelineSlider.value = currentFrame;
                            hudFrameLabel.innerText = `Frame: [ ${String(currentFrame).padStart(3, '0')} / ${totalFrames} ]`;
                            threeObjectsMap.forEach(obj => evaluateAnimation(obj, currentFrame));
                            updateShotCameraPose(currentFrame);
                            updateActorTexture(currentFrame);
                            updateActorBillboardTransform();
                            updateTransformInspector();
                            if (typeof updateBakedReviewFrame === "function") updateBakedReviewFrame(currentFrame);
                        }

                        if (!isRmbDown && activeCamera === flyCamera) {
                            orbitControls.update();
                        }

                        updateSelectionBoxes();

                        // Auto-fit Film Gate within Monitor Frame container to preserve aspect ratio (Letterbox/Pillarbox)
                        if (filmGateBox && monitorFrameBox) {
                            const contW = monitorFrameBox.clientWidth - 16;
                            const contH = monitorFrameBox.clientHeight - 16;
                            if (contW > 20 && contH > 20) {
                                let fitW = contW;
                                let fitH = fitW / plateAspect;
                                if (fitH > contH) {
                                    fitH = contH;
                                    fitW = fitH * plateAspect;
                                }
                                filmGateBox.style.width = `${Math.floor(fitW)}px`;
                                filmGateBox.style.height = `${Math.floor(fitH)}px`;
                            }
                        }

                        // Geometry coordinates for Viewports
                        const w = canvasHost.clientWidth || window.innerWidth;
                        const h = canvasHost.clientHeight || window.innerHeight;

                        if (w > 0 && h > 0 && (renderer.domElement.width !== Math.floor(w * (window.devicePixelRatio || 1)) || renderer.domElement.height !== Math.floor(h * (window.devicePixelRatio || 1)))) {
                            renderer.setSize(w, h, false);
                        }

                        const hostRect = canvasHost.getBoundingClientRect();
                        const rightColRect = studioRightCol.getBoundingClientRect();
                        const gateRect = filmGateBox ? filmGateBox.getBoundingClientRect() : null;
                        const monRect = monitorFrameBox ? monitorFrameBox.getBoundingClientRect() : null;

                        const leftW = Math.max(10, Math.round(rightColRect.left - hostRect.left));

                        renderer.setScissorTest(true);

                        // 1. Clear monitor pane area to deep cinema black matte (#040507)
                        if (monRect && monRect.width > 10 && monRect.height > 10) {
                            const monX = Math.round(monRect.left - hostRect.left);
                            const monY = Math.round(hostRect.bottom - monRect.bottom);
                            const monW = Math.round(monRect.width);
                            const monH = Math.round(monRect.height);

                            renderer.setViewport(monX, monY, monW, monH);
                            renderer.setScissor(monX, monY, monW, monH);
                            renderer.setClearColor(0x040507, 1.0);
                            renderer.clear();
                        }

                        // 2. Render Left Viewport: 3D Perspective (Orbit / FlyCam)
                        renderer.setViewport(0, 0, leftW, h);
                        renderer.setScissor(0, 0, leftW, h);
                        activeCamera.aspect = leftW / h;
                        activeCamera.updateProjectionMatrix();

                        const isViewingShotOnLeft = (activeCamera === shotCamera);
                        if (camBodyMesh) camBodyMesh.visible = !isViewingShotOnLeft;
                        if (camFrustumHelper) camFrustumHelper.visible = !isViewingShotOnLeft;
                        if (cameraPathLine) cameraPathLine.visible = !isViewingShotOnLeft;
                        if (gridHelper) gridHelper.visible = isGridVisible;

                        renderer.render(scene, activeCamera);

                        // 3. Render Right Bottom Viewport: Shot Camera inside Film Gate (Exact Aspect Ratio, No Stretch!)
                        if (activeMonitorMode === "3d" && gateRect && gateRect.width > 10 && gateRect.height > 10) {
                            const gateX = Math.round(gateRect.left - hostRect.left);
                            const gateY = Math.round(hostRect.bottom - gateRect.bottom);
                            const gateW = Math.round(gateRect.width);
                            const gateH = Math.round(gateRect.height);

                            renderer.setViewport(gateX, gateY, gateW, gateH);
                            renderer.setScissor(gateX, gateY, gateW, gateH);

                            if (camBodyMesh) camBodyMesh.visible = false;
                            if (camFrustumHelper) camFrustumHelper.visible = false;
                            if (cameraPathLine) cameraPathLine.visible = false;

                            shotCamera.aspect = gateW / gateH;
                            shotCamera.updateProjectionMatrix();

                            renderer.render(scene, shotCamera);
                        }

                        renderer.setScissorTest(false);
                    }
                    animId = requestAnimationFrame(animate);
                }
            };

            nodeType.prototype.setupLayoutNode = setupLayoutNode;

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
                setupLayoutNode(this);
                return r;
            };

            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
                setupLayoutNode(this);
                return r;
            };
        }
    },
    async nodeCreated(node) {
        if (node.comfyClass === "LayoutEditor3D" || node.type === "LayoutEditor3D") {
            if (node.setupLayoutNode) node.setupLayoutNode(node);
        }
    },
    async loadedGraphNode(node) {
        if (node.comfyClass === "LayoutEditor3D" || node.type === "LayoutEditor3D") {
            if (node.setupLayoutNode) node.setupLayoutNode(node);
        }
    },
    async setup(app) {
        if (app.graph && app.graph._nodes) {
            for (const node of app.graph._nodes) {
                if (node.comfyClass === "LayoutEditor3D" || node.type === "LayoutEditor3D") {
                    if (node.setupLayoutNode) node.setupLayoutNode(node);
                }
            }
        }

        // Expose helper for KESHIKI Studio iframe or standalone bridge to serialize current ComfyUI graph
        window.__keshikiSerializeComfyWorkflow = async () => {
            const rawGraph = app.graph ? app.graph.serialize() : {};
            let apiPrompt = {};
            try {
                if (typeof app.graphToPrompt === "function") {
                    const p = await app.graphToPrompt();
                    apiPrompt = p.output || p || {};
                }
            } catch (e) {}
            return { graph: rawGraph, api_prompt: apiPrompt };
        };

        // If user opened standard ComfyUI (/) in the main window, provide a 1-click button to return to KESHIKI Studio or bind workflow
        if (window.self === window.top && !document.getElementById("keshiki-return-studio-bar")) {
            const bar = document.createElement("div");
            bar.id = "keshiki-return-studio-bar";
            bar.style.cssText = `
                position: fixed;
                bottom: 16px;
                left: 50%;
                transform: translateX(-50%);
                z-index: 99999;
                background: rgba(15, 15, 18, 0.94);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 8px;
                padding: 6px 10px;
                display: flex;
                align-items: center;
                gap: 8px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.8);
                backdrop-filter: blur(10px);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            `;
            bar.innerHTML = `
                <button id="btn-keshiki-back-to-studio" style="background: #ffffff; color: #09090b; border: none; border-radius: 5px; padding: 5px 12px; font-size: 11.5px; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 6px;">
                    🎬 Back to KESHIKI Studio
                </button>
                <div style="width: 1px; height: 18px; background: rgba(255,255,255,0.15);"></div>
                <select id="sel-keshiki-bind-proc" style="background: #18181b; color: #e4e4e7; border: 1px solid rgba(255,255,255,0.18); border-radius: 4px; padding: 4px 8px; font-size: 11px;">
                    <option value="storyboard">Storyboard (FLUX.1)</option>
                    <option value="character">Characters & LoRA</option>
                    <option value="location">Locations & Scenes</option>
                    <option value="tts_stage1">Voice & TTS</option>
                    <option value="video_gen" selected>Primary Video (LTX-2.5)</option>
                    <option value="mesh_gen">3D Mesh Generation</option>
                    <option value="deconstruct">Pass Deconstruction</option>
                    <option value="composite">Final Composite</option>
                    <option value="music">Music Generation</option>
                </select>
                <button id="btn-keshiki-bind-active-graph" style="background: rgba(59, 130, 246, 0.2); color: #93c5fd; border: 1px solid rgba(59, 130, 246, 0.45); border-radius: 5px; padding: 5px 10px; font-size: 11px; font-weight: 600; cursor: pointer;">
                    📌 Bind Workflow to Process
                </button>
            `;
            document.body.appendChild(bar);

            bar.querySelector("#btn-keshiki-back-to-studio").onclick = () => {
                window.location.href = "/vfx/studio";
            };
            bar.querySelector("#btn-keshiki-bind-active-graph").onclick = async () => {
                const procId = bar.querySelector("#sel-keshiki-bind-proc").value;
                const btn = bar.querySelector("#btn-keshiki-bind-active-graph");
                btn.innerText = "⏳ Saving...";
                try {
                    const wfData = await window.__keshikiSerializeComfyWorkflow();
                    const res = await fetch("/vfx/settings/bind_workflow", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            process_id: procId,
                            workflow_name: `ComfyUI_Active_${procId}.json`,
                            workflow_json: wfData
                        })
                    });
                    const data = await res.json();
                    btn.innerText = `✓ Bound to [${procId}]!`;
                    setTimeout(() => { btn.innerText = "📌 Bind Workflow to Process"; }, 2500);
                } catch (e) {
                    btn.innerText = `Error: ${e.message}`;
                }
            };
        }
    }
});
