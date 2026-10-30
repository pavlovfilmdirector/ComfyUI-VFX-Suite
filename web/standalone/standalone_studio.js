// Keshiki (景色) 3D VFX Studio — Autonomous Cinema & Generative AI Studio
// Dual Viewport, 6DoF Cinema Camera Navigation, 3-Point Snapping, Keyframing & Baking
import { initStudioArchitecture } from "/vfx/studio/studio_tabs_controller.js?v=2.6.0";
import { projectObject3DBBoxToPlateCrop } from "/vfx/studio/modules/ue5_viewport_utils.js";

export async function initStandaloneLayoutStudio() {
    function uiText(enStr, ruStr) {
        if (typeof window !== "undefined" && typeof window.__vfxUiText === "function") {
            return window.__vfxUiText(enStr, ruStr);
        }
        try {
            const p = JSON.parse(localStorage.getItem("vfx_studio_prefs") || "{}");
            if (p.translateInterface && p.language === "ru") return ruStr || enStr;
        } catch (_) {}
        return enStr;
    }

    function tipText(enStr, ruStr) {
        if (typeof window !== "undefined" && typeof window.__vfxTipText === "function") {
            return window.__vfxTipText(enStr, ruStr);
        }
        try {
            const p = JSON.parse(localStorage.getItem("vfx_studio_prefs") || "{}");
            if (p.translateTooltips && p.language === "ru") return ruStr || enStr;
        } catch (_) {}
        return enStr || ruStr;
    }

    function tipAttr(titleEn, titleRu, descEn = "", descRu = "", apiOrShortcut = "") {
        const safe = (s) => String(s || "").replace(/"/g, "&quot;");
        return `data-tip-t-en="${safe(titleEn)}" data-tip-t-ru="${safe(titleRu || titleEn)}" data-tip-d-en="${safe(descEn)}" data-tip-d-ru="${safe(descRu)}" data-tip-api="${safe(apiOrShortcut)}"`;
    }

    function setTip(el, titleEn, titleRu, descEn = "", descRu = "") {
        if (!el) return;
        el.setAttribute("data-tip-t-en", titleEn || "");
        el.setAttribute("data-tip-t-ru", titleRu || titleEn || "");
        if (descEn || descRu) {
            el.setAttribute("data-tip-d-en", descEn || "");
            el.setAttribute("data-tip-d-ru", descRu || descEn || "");
        }
        el.removeAttribute("title");
    }

    const THREE = window.THREE;
    if (!THREE) {
        console.error("[VFX Studio] Three.js is not loaded on window.THREE");
        return;
    }

    const appRoot = document.getElementById("studio-app-root") || document.body;
    appRoot.innerHTML = "";

    let currentFrame = 0;
    let totalFrames = 87;
    let projectFPS = parseFloat(localStorage.getItem("vfx_project_fps") || "24.0");

    function setProjectFrameRate(fps) {
        const val = Math.max(1.0, Math.min(120.0, parseFloat(fps) || 24.0));
        projectFPS = Math.round(val * 100) / 100;
        localStorage.setItem("vfx_project_fps", String(projectFPS));
        const fpsLabel = document.getElementById("st-fps-label");
        if (fpsLabel) fpsLabel.innerText = `${projectFPS.toFixed(1)} FPS`;
        if (window.currentProject) window.currentProject.fps = projectFPS;
        return projectFPS;
    }

    function setProjectTotalFrames(count) {
        const val = Math.max(1, Math.min(10000, parseInt(count, 10) || 87));
        totalFrames = val;
        currentFrame = Math.min(currentFrame, totalFrames - 1);
        const slider = document.getElementById("timeline-slider");
        if (slider) {
            slider.max = Math.max(0, totalFrames - 1);
            slider.value = currentFrame;
        }
        const hud = document.getElementById("hud-frame-label");
        if (hud) {
            hud.innerText = `Frame: [ ${String(currentFrame).padStart(3, '0')} / ${String(totalFrames).padStart(3, '0')} ]`;
        }
        const lblTotal = document.getElementById("lbl-total-frames");
        if (lblTotal) lblTotal.innerText = `/${totalFrames}`;
        const scrub = document.getElementById("scrub-timeline");
        if (scrub) scrub.max = Math.max(0, totalFrames - 1);
        if (window.currentProject) window.currentProject.total_frames = totalFrames;
        const topFrames = document.getElementById("inp-top-frames");
        if (topFrames && document.activeElement !== topFrames && String(topFrames.value) !== String(totalFrames)) {
            topFrames.value = totalFrames;
            topFrames.dispatchEvent(new Event("input"));
        }
        if (typeof updateKeyframeMarkers === "function") updateKeyframeMarkers();
        if (typeof updateTimelineActiveContextBar === "function") updateTimelineActiveContextBar();
        return totalFrames;
    }
    window.__vfxSetProjectFPS = setProjectFrameRate;
    window.__vfxSetTotalFrames = setProjectTotalFrames;
    window.__vfxGetProjectFPS = () => projectFPS;
    window.__vfxGetTotalFrames = () => totalFrames;

    let isPlaying = false;
    let autoKeyEnabled = true;
    let selectedObject = null;
    const selectedObjects = new Set();
    let currentInterp = "bezier";
    let isShotCamActive = false;
    let isGridVisible = localStorage.getItem("vfx_3d_grid_visible") !== "false";
    let sceneData = [];

    // Camera Aspect Ratio state (default 16:9, updated by scene_data)
    let plateWidth = 1280;
    let plateHeight = 720;
    let plateAspect = 16.0 / 9.0;

    function setProjectResolution(w, h) {
        const width = Math.max(128, Math.min(8192, parseInt(w, 10) || 1280));
        const height = Math.max(128, Math.min(8192, parseInt(h, 10) || 720));
        plateWidth = width;
        plateHeight = height;
        plateAspect = plateWidth / plateHeight;

        const gateBox = document.getElementById("film-gate-box");
        if (gateBox) {
            gateBox.style.aspectRatio = `${plateWidth} / ${plateHeight}`;
        }
        const badge = document.getElementById("monitor-aspect-badge");
        if (badge) {
            badge.innerText = `${plateWidth}×${plateHeight} • ${(plateAspect).toFixed(2)}:1`;
        }
        if (window.currentProject) {
            window.currentProject.width = plateWidth;
            window.currentProject.height = plateHeight;
            window.currentProject.aspect_ratio = (plateAspect).toFixed(2);
        }
        if (typeof shotCamera !== "undefined" && shotCamera) {
            shotCamera.aspect = plateAspect;
            shotCamera.updateProjectionMatrix();
        }
        return { width: plateWidth, height: plateHeight, aspect: plateAspect };
    }
    window.__vfxSetProjectResolution = setProjectResolution;
    window.__vfxGetProjectResolution = () => ({ width: plateWidth, height: plateHeight, aspect: plateAspect });

    // Create Modal Backdrop as root container
    const container = document.createElement("div");
    container.className = "vfx-modal-backdrop";
    container.style.position = "relative";
    container.style.width = "100vw";
    container.style.height = "100vh";

    function logMsg(msg, detail = "") {
        if (typeof window.vfxLog === "function") {
            window.vfxLog("SYSTEM", msg, detail);
        } else {
            console.log("[Keshiki VFX Studio]", msg, detail);
        }
    }

    // 1. Header (Google Stitch Minimalist 34px Bar)
    const header = document.createElement("div");
    header.className = "vfx-modal-header";
    header.innerHTML = `
        <div class="vfx-header-group" style="display: flex; align-items: center; gap: 10px;">
            <div id="vfx-hdr-logo-wrapper" style="display: flex; align-items: center; gap: 8px; padding-right: 12px; border-right: 1px solid rgba(255,255,255,0.08); height: 24px; cursor: pointer;" ${tipAttr("Keshiki Logo — Click to switch style (Bonsai / Branches / Stars)", "Логотип Keshiki — нажмите для смены стиля (Бонсай / Ветви / Звёзды)")}>
                <img id="hdr-keshiki-logo-icon" src="/vfx/studio/keshiki_mark.png" alt="Keshiki Logo" style="height: 22px; width: auto; object-fit: contain; filter: drop-shadow(0 0 6px rgba(255,255,255,0.25)); transition: transform 0.15s ease;" />
                <span class="vfx-header-title" style="font-weight: 600; font-size: 11.5px; letter-spacing: 0.02em; color: #fff;">
                    KESHIKI
                </span>
                <span class="vfx-badge-tech-preview" style="font-size: 9px; padding: 1.5px 6px; border-radius: 3px; background: rgba(56, 189, 248, 0.14); border: 1px solid rgba(56, 189, 248, 0.42); color: #38bdf8; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;">Tech Preview v0.1</span>
            </div>
            <span style="font-size: 10px; color: rgba(255,255,255,0.45); font-family: 'JetBrains Mono', monospace;" id="hdr-scene-status">Keshiki & ComfyUI</span>
            <span class="vfx-render-badge-pill vfx-badge-draft" id="hdr-render-badge" style="font-size: 9.5px; padding: 1px 6px; border-radius: 4px; background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.65); border: 1px solid rgba(255,255,255,0.08);">Draft</span>
        </div>
        <div class="vfx-header-group" style="display: flex; align-items: center; gap: 6px;">
            <button class="vfx-btn" id="btn-switch-comfyui-mode" style="padding: 3px 9px; font-size: 10.5px; cursor: pointer; background: rgba(59, 130, 246, 0.14); border: 1px solid rgba(59, 130, 246, 0.45); color: #93c5fd; font-weight: 600; display: inline-flex; align-items: center; gap: 5px;" ${tipAttr('Switch to standard ComfyUI node mode and bind custom workflows', 'Переключиться в обычный нодовый режим ComfyUI и привязать кастомный воркфлоу к процессу')}>
                <i data-lucide="workflow" style="width: 12px; height: 12px;"></i><span>⇄ ComfyUI</span>
            </button>
            <div id="vfx-progress-chip" style="display: none; align-items: center; gap: 8px; padding: 2px 9px; border: 1px solid rgba(56, 189, 248, 0.45); background: rgba(15, 23, 42, 0.9); border-radius: 5px; cursor: pointer; font-size: 10px; color: #e4e4e7; position: relative; box-shadow: 0 0 12px rgba(56, 189, 248, 0.18);" ${tipAttr('Generation progress — click for details', 'Прогресс генерации — клик для подробностей')}>
                <div style="width: 100px; height: 5px; background: rgba(255,255,255,0.1); border-radius: 999px; overflow: hidden;"><div id="vfx-progress-fill" style="height: 100%; width: 0%; background: linear-gradient(90deg, #38bdf8, #818cf8, #c084fc); border-radius: 999px; transition: width 0.35s ease;"></div></div>
                <span id="vfx-progress-text" style="font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 600; color: #38bdf8; white-space: nowrap; max-width: 240px; overflow: hidden; text-overflow: ellipsis;"></span>
            </div>
            <button class="vfx-btn" id="btn-stop-all-jobs" style="display: none; padding: 3px 8px; font-size: 10px; font-weight: 600; align-items: center; gap: 4px; background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.45); color: #fca5a5; border-radius: 4px; cursor: pointer;" ${tipAttr('Stop running generation and clear queue', 'Остановить текущую генерацию и очистить очередь')}>
                <i data-lucide="square" style="width: 10px; height: 10px; fill: currentColor;"></i>
                <span>${uiText("Stop", "Стоп")}</span>
            </button>
            <button class="vfx-btn" id="btn-restart-server" style="padding: 3px 6px; display: inline-flex; align-items: center; gap: 4px;" ${tipAttr('Restart ComfyUI server (applies code changes). Running jobs will be interrupted.', 'Перезапустить сервер ComfyUI (применить изменения кода). Запущенные задачи прервутся.')}>
                <i data-lucide="rotate-cw" style="width: 12px; height: 12px;"></i><span id="lbl-restart-server" style="font-size: 10px; display: none;"></span>
            </button>
            <button class="vfx-btn" id="btn-fullscreen-toggle" style="padding: 3px 6px;" ${tipAttr("Fullscreen", "Во весь экран")}>
                <i data-lucide="maximize-2" style="width: 12px; height: 12px;"></i>
            </button>
        </div>
    `;
    container.appendChild(header);

    const logoImg = header.querySelector("#hdr-keshiki-logo-icon");
    if (logoImg) {
        logoImg.src = `/vfx/studio/keshiki_mark.png?v=${Date.now()}`;
    }

    // Set inverted favicon (black emblem on white) for browser tab
    let favEl = document.querySelector("link[rel*='icon']");
    if (!favEl) {
        favEl = document.createElement("link");
        favEl.rel = "icon";
        document.head.appendChild(favEl);
    }
    favEl.href = `/vfx/studio/favicon.png?v=${Date.now()}`;

    // 2. Main Workspace
    const workspace = document.createElement("div");
    workspace.className = "vfx-layout-workspace";
    workspace.style.display = "flex";
    workspace.style.flex = "1";
    workspace.style.position = "relative";
    workspace.style.overflow = "hidden";
    container.appendChild(workspace);

    // Full-viewport Canvas Host (background WebGL canvas)
    const canvasHost = document.createElement("div");
    canvasHost.className = "vfx-viewport-canvas-host";
    workspace.appendChild(canvasHost);

    // Left Viewport Overlay (HUD, Badges)
    const leftPaneOverlay = document.createElement("div");
    leftPaneOverlay.className = "vfx-viewport-left-overlay";
    leftPaneOverlay.innerHTML = `
        <div class="vfx-viewport-badge-left" id="vfx-viewport-badge-title">🌐 3D PERSPECTIVE (Orbit / FlyCam)</div>
        <div id="vfx-viewport-axis-widget" style="position: absolute; bottom: 60px; right: 16px; width: 78px; height: 78px; z-index: 15; pointer-events: auto; user-select: none;">
            <canvas id="vfx-axis-compass-canvas" width="78" height="78" style="display: block; cursor: pointer;" ${tipAttr('3D Orientation Compass (Click axes to align view)', '3D-компас ориентации (Нажмите на ось для перехода к виду)')}></canvas>
        </div>
        <div class="vfx-3pt-picker-bar" id="vfx-3pt-picker-bar" style="display: none;">
            <div class="vfx-3pt-status" id="vfx-3pt-status">🎯 Click 3 points in Point Cloud (0 / 3)</div>
            <div class="vfx-3pt-actions" id="vfx-3pt-actions" style="display: none; gap: 4px;">
                <button class="vfx-btn vfx-btn-primary" id="btn-3pt-confirm" ${tipAttr("Create permanent Reference Plane in scene", "Создать опорную плоскость в сцене")}>📐 Create Plane</button>
                <button class="vfx-btn" id="btn-3pt-flip" ${tipAttr("Flip plane normal (180°)", "Развернуть нормаль плоскости (180°)")}>🔄 Flip Normal</button>
                <button class="vfx-btn" id="btn-3pt-align-grid" ${tipAttr("Align Scene Ground Grid to this Plane", "Выровнять сетку пола по этой плоскости")}>🎯 Align Grid</button>
                <button class="vfx-btn" id="btn-3pt-repick" ${tipAttr("Clear points and pick again", "Сбросить точки и выбрать заново")}>↺ Re-pick</button>
            </div>
            <button class="vfx-btn" id="btn-3pt-cancel" style="padding: 2px 8px; font-size: 11px;" ${tipAttr("Exit 3-Point tool (Esc)", "Выйти из инструмента 3 точек (Esc)")}>✕ Close</button>
        </div>
        <div class="vfx-viewport-overlay">
            <div class="vfx-overlay-badge" id="hud-status">Mode: Select | Space: World | Left: 3D Perspective</div>
            <div class="vfx-overlay-badge">HOTKEYS: Shift+Click (Multi-select) | Q/W/E/R (Tools) | ~ (World/Local) | C (Cam) | ←/→ (Step) | [/] (Key Jump) | Space (Play) | K (Key) | Del</div>
        </div>
    `;
    workspace.appendChild(leftPaneOverlay);

    // Splitter Vertical
    const splitterV = document.createElement("div");
    splitterV.className = "vfx-splitter-v";
    splitterV.id = "splitter-studio-v";
    setTip(splitterV, "Drag horizontally to resize 3D viewport", "Потяните по горизонтали для изменения ширины 3D-вьюпорта");
    workspace.appendChild(splitterV);

    // Right Column (Controls Top + Camera Monitor Bottom)
    const studioRightCol = document.createElement("div");
    studioRightCol.className = "vfx-studio-right-column";
    studioRightCol.innerHTML = `
        <!-- Top Half: Controls, Outliner, Transform Inspector, Environment -->
        <div class="vfx-controls-top-pane" id="controls-top-pane">
            <!-- 0. AI Director (Gemma 12B) -->
            <div class="vfx-sidebar-header" id="hdr-director" style="background: rgba(255,255,255,0.04); color: #ffffff;">
                <span><span class="vfx-collapse-arrow">▼</span>${uiText("🎬 🤖 AI Director (Gemma 12B)", "🎬 🤖 AI Режиссер (Gemma 12B)")}</span>
            </div>
            <div class="vfx-section-content" id="body-director" style="padding: 8px 12px; display: flex; flex-direction: column; gap: 6px;">
                <div id="director-chat-box" style="max-height: 140px; overflow-y: auto; background: #141416; border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; padding: 6px; font-size: 11px; color: #e1e1e4; line-height: 1.4;">
                    <div style="color: rgba(255,255,255,0.45); font-style: italic;">${uiText("Describe the shot you want to create (e.g. 'night cyberpunk, rain, slow camera dolly into character'). I will generate a prompt and configure the camera and scene!", "Напишите, какой шот вы хотите получить (например: 'ночной киберпанк, дождь, наезд камеры на персонажа'). Я сгенерирую кинематографичный промпт для LTX-Video и настрою камеру со сценой!")}</div>
                </div>
                <div style="display: flex; gap: 4px;">
                    <input type="text" id="input-director-chat" placeholder="${uiText('Describe shot in natural language...', 'Опишите шот на естественном языке...')}" style="flex: 1; background: #18181b; border: 1px solid rgba(255,255,255,0.12); border-radius: 4px; color: #fff; padding: 4px 6px; font-size: 11px;" />
                    <button class="vfx-btn" id="btn-send-director" style="background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); color: #fff; font-weight: bold; font-size: 11px; padding: 4px 10px;">▶</button>
                </div>
                <div id="director-prompt-preview" style="display: none; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; padding: 6px; font-size: 10px; color: #e1e1e4;">
                    <div style="font-weight: bold; margin-bottom: 2px; color: #ffffff;">LTX-Video Prompt:</div>
                    <div id="director-prompt-text" style="font-family: monospace; max-height: 70px; overflow-y: auto; margin-bottom: 4px;"></div>
                    <button class="vfx-btn" id="btn-copy-director-prompt" style="font-size: 10px; padding: 2px 6px; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); color: #fff;">📋 ${uiText("Copy Prompt", "Скопировать")}</button>
                </div>
            </div>

            <!-- 0.5. Voice & Lip-Sync Studio (TTS) -->
            <div class="vfx-sidebar-header" id="hdr-voice" style="background: rgba(255,255,255,0.04); color: #ffffff;">
                <span><span class="vfx-collapse-arrow">▼</span>🎙️ Audio & Lip-Sync</span>
            </div>
            <div class="vfx-section-content" id="body-voice" style="padding: 8px 12px; display: flex; flex-direction: column; gap: 6px;">
                <textarea id="input-tts-text" rows="2" placeholder="Dialogue line..." style="width: 100%; box-sizing: border-box; background: #18181b; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; color: #fff; padding: 4px 6px; font-size: 11px; resize: vertical;"></textarea>
                <div style="display: flex; gap: 6px; align-items: center;">
                    <select id="select-tts-voice" class="vfx-interp-select" style="flex: 1; font-size: 11px;">
                        <option value="dmitry">Dmitry (RU Neural)</option>
                        <option value="svetlana">Svetlana (RU Neural)</option>
                        <option value="christopher">Christopher (EN Cinema)</option>
                        <option value="jenny">Jenny (EN Clear)</option>
                        <option value="guy">Guy (EN Dramatic)</option>
                    </select>
                    <label style="font-size: 10px; color: rgba(255,255,255,0.65); display: flex; align-items: center; gap: 3px; cursor: pointer;">
                        <input type="checkbox" id="chk-tts-translate" /> 🌐 Translate
                    </label>
                </div>
                <button class="vfx-btn" id="btn-generate-speech" style="background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); color: #fff; font-weight: bold; font-size: 11px; padding: 4px 8px;">
                    🎙️ Synthesize & Lip-Sync
                </button>
                <div id="voice-player-card" style="display: none; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; padding: 6px; font-size: 10px; color: #e1e1e4;">
                    <audio id="audio-preview-player" controls style="width: 100%; height: 28px; margin-bottom: 4px;"></audio>
                    <div id="voice-info-label" style="font-size: 10px; color: rgba(255,255,255,0.5);">Duration: 0.0s (0 frames)</div>
                </div>
            </div>

            <!-- 1. Scene Outliner (Moved to top as requested) -->
            <div class="vfx-sidebar-header" id="hdr-outliner" style="cursor: pointer; flex: none;">
                <span style="display:flex; align-items:center; gap:5px;"><span class="vfx-collapse-arrow">▼</span><i data-lucide="layers" style="width:11px; height:11px;"></i><span>Scene Outliner</span></span>
                <span id="outliner-count" style="font-size: 10px; color: rgba(255,255,255,0.4); font-family: 'JetBrains Mono', monospace;">0 objects</span>
            </div>
            <div class="vfx-section-content" id="body-outliner" style="display: flex; flex-direction: column; overflow: hidden; padding: 0; min-height: 48px; flex: none;">
                <div class="vfx-outliner-list" id="outliner-list" style="flex: 1; overflow-y: auto; max-height: none !important;"></div>
            </div>
            <div class="vfx-splitter-h" id="splitter-outliner-h" ${tipAttr("Drag to resize Outliner", "Потяните для изменения высоты списка объектов (Outliner)")} style="height: 5px; flex: none; cursor: ns-resize; background: rgba(255,255,255,0.06); border-top: 1px solid rgba(255,255,255,0.08); border-bottom: 1px solid rgba(0,0,0,0.4); margin-bottom: 4px;"></div>

            <!-- Selected Object Transform Inspector (Directly under Scene Outliner) -->
            <div class="vfx-sidebar-header" id="hdr-inspector" style="cursor: pointer; flex: none;">
                <span style="display:flex; align-items:center; gap:5px; min-width:0; overflow:hidden;">
                    <span class="vfx-collapse-arrow">▼</span>
                    <i data-lucide="move-3d" style="width:11px; height:11px; color:#38bdf8; flex-shrink:0;"></i>
                    <span>${uiText("Transform", "Трансформация")}</span>
                    <span id="inspector-target" style="font-size: 9.5px; color: #38bdf8; font-weight: 500; max-width: 95px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">• ${uiText("None", "Нет")}</span>
                </span>
                <div style="display:flex; gap:3px; align-items:center; flex-shrink:0;">
                    <button class="vfx-btn" id="btn-sidebar-snap-floor" ${tipAttr("Snap Bottom to Floor (Z=0)", "Поставить на пол (Z=0)")} style="padding:1px 5px; font-size:9px;">Z=0</button>
                    <button class="vfx-btn" id="btn-duplicate-obj" ${tipAttr("Duplicate Object (Ctrl+D)", "Дублировать объект (Ctrl+D)")} style="padding:1px 5px; font-size:9px;">Dup</button>
                    <button class="vfx-btn" id="btn-reset-xform" ${tipAttr("Reset All Transform", "Сбросить все координаты")} style="padding:1px 5px; font-size:9px;">↺</button>
                </div>
            </div>
            <div class="vfx-section-content" id="body-inspector" style="padding: 6px 8px; display: flex; flex-direction: column; gap: 5px; flex: none;">
                <div class="ue-transform-grouped-card" id="transform-inspector">
                    <!-- Location -->
                    <div class="ue5-xf-row">
                        <div class="ue5-xf-label-pill" ${tipAttr("Location (X, Y, Z)", "Координаты положения (X, Y, Z)")}>
                            <span>${uiText("Location", "Позиция")}</span>
                        </div>
                        <div class="ue5-xf-inputs">
                            <div class="ue-striped-input-wrap ue5-input x" ${tipAttr('Location X (drag to scrub)', 'Позиция X (перетащите для изменения)')}>
                                <span class="ue5-input-bar x"></span>
                                <input type="number" step="0.05" class="ue-vec-inp" id="num-pos-x" value="0.00" />
                            </div>
                            <div class="ue-striped-input-wrap ue5-input y" ${tipAttr('Location Y (drag to scrub)', 'Позиция Y (перетащите для изменения)')}>
                                <span class="ue5-input-bar y"></span>
                                <input type="number" step="0.05" class="ue-vec-inp" id="num-pos-y" value="0.00" />
                            </div>
                            <div class="ue-striped-input-wrap ue5-input z" ${tipAttr('Location Z (drag to scrub)', 'Позиция Z (перетащите для изменения)')}>
                                <span class="ue5-input-bar z"></span>
                                <input type="number" step="0.05" class="ue-vec-inp" id="num-pos-z" value="0.00" />
                            </div>
                        </div>
                        <button class="vfx-btn ue5-reset-btn" id="btn-sidebar-reset-pos" ${tipAttr("Reset Location to 0", "Сбросить позицию в 0")}>↺</button>
                    </div>

                    <!-- Rotation -->
                    <div class="ue5-xf-row">
                        <div class="ue5-xf-label-pill" ${tipAttr("Rotation (Roll, Pitch, Yaw)", "Углы поворота (°)")}>
                            <span>${uiText("Rotation", "Вращение")}</span>
                        </div>
                        <div class="ue5-xf-inputs">
                            <div class="ue-striped-input-wrap ue5-input x" ${tipAttr('Rotation X (drag to scrub)', 'Вращение X (перетащите для изменения)')}>
                                <span class="ue5-input-bar x"></span>
                                <input type="number" step="1.0" class="ue-vec-inp" id="num-rot-x" value="0.0" />
                                <span class="ue-unit-suffix">°</span>
                            </div>
                            <div class="ue-striped-input-wrap ue5-input y" ${tipAttr('Rotation Y (drag to scrub)', 'Вращение Y (перетащите для изменения)')}>
                                <span class="ue5-input-bar y"></span>
                                <input type="number" step="1.0" class="ue-vec-inp" id="num-rot-y" value="0.0" />
                                <span class="ue-unit-suffix">°</span>
                            </div>
                            <div class="ue-striped-input-wrap ue5-input z" ${tipAttr('Rotation Z (drag to scrub)', 'Вращение Z (перетащите для изменения)')}>
                                <span class="ue5-input-bar z"></span>
                                <input type="number" step="1.0" class="ue-vec-inp" id="num-rot-z" value="0.0" />
                                <span class="ue-unit-suffix">°</span>
                            </div>
                        </div>
                        <button class="vfx-btn ue5-reset-btn" id="btn-sidebar-reset-rot" ${tipAttr("Reset Rotation to 0°", "Сбросить поворот в 0°")}>↺</button>
                    </div>

                    <!-- Scale -->
                    <div class="ue5-xf-row">
                        <div class="ue5-xf-scale-col">
                            <div class="ue5-xf-label-pill ue5-scale-pill" ${tipAttr("Scale (X, Y, Z)", "Масштаб объекта (X, Y, Z)")}>
                                <span>${uiText("Scale", "Масштаб")}</span>
                            </div>
                            <button class="vfx-btn ue-btn-lock-scale active" id="btn-sidebar-lock-scale" ${tipAttr("Lock Proportional Scaling", "Связать пропорции масштаба")}>
                                <i data-lucide="lock" style="width:10px; height:10px;"></i>
                            </button>
                        </div>
                        <div class="ue5-xf-inputs">
                            <div class="ue-striped-input-wrap ue5-input x" ${tipAttr('Scale X (drag to scrub)', 'Масштаб X (перетащите для изменения)')}>
                                <span class="ue5-input-bar x"></span>
                                <input type="number" step="0.05" min="0.01" class="ue-vec-inp" id="num-scale-x" value="1.00" />
                            </div>
                            <div class="ue-striped-input-wrap ue5-input y" ${tipAttr('Scale Y (drag to scrub)', 'Масштаб Y (перетащите для изменения)')}>
                                <span class="ue5-input-bar y"></span>
                                <input type="number" step="0.05" min="0.01" class="ue-vec-inp" id="num-scale-y" value="1.00" />
                            </div>
                            <div class="ue-striped-input-wrap ue5-input z" ${tipAttr('Scale Z (drag to scrub)', 'Масштаб Z (перетащите для изменения)')}>
                                <span class="ue5-input-bar z"></span>
                                <input type="number" step="0.05" min="0.01" class="ue-vec-inp" id="num-scale-z" value="1.00" />
                            </div>
                        </div>
                        <button class="vfx-btn ue5-reset-btn" id="btn-sidebar-reset-scale" ${tipAttr("Reset Scale to 1.0", "Сбросить масштаб в 1.0")}>↺</button>
                    </div>
                </div>

                <!-- Quick Pivot & Copy/Paste Row -->
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 4px; margin-top: 1px;">
                    <span style="font-size: 9.5px; color: rgba(255,255,255,0.5);">${uiText("Pivot:", "Пивот:")}</span>
                    <div style="display: flex; gap: 3px; flex: 1;">
                        <button class="vfx-btn btn-sidebar-pivot" data-pivot="bottom" style="flex:1; padding:2px 4px; font-size:9px;" ${tipAttr("Snap Pivot to Bottom", "Пивот в основание (Bottom)")}>${uiText("Bottom", "Низ")}</button>
                        <button class="vfx-btn btn-sidebar-pivot" data-pivot="center" style="flex:1; padding:2px 4px; font-size:9px;" ${tipAttr("Snap Pivot to Center", "Пивот в центр (Center)")}>${uiText("Center", "Центр")}</button>
                        <button class="vfx-btn btn-sidebar-pivot" data-pivot="top" style="flex:1; padding:2px 4px; font-size:9px;" ${tipAttr("Snap Pivot to Top", "Пивот в верхнюю грань (Top)")}>${uiText("Top", "Верх")}</button>
                    </div>
                    <button class="vfx-btn" id="btn-copy-xform" style="padding: 2px 5px; font-size: 9px;" ${tipAttr("Copy Transforms (Ctrl+C)", "Копировать трансформацию (Ctrl+C)")}>Copy</button>
                    <button class="vfx-btn" id="btn-paste-xform" style="padding: 2px 5px; font-size: 9px;" ${tipAttr("Paste Transforms (Ctrl+V)", "Вставить трансформацию (Ctrl+V)")}>Paste</button>
                </div>
            </div>

            <!-- Viewport Camera Views (Top, Bottom, Front, Back, Left, Right, Ortho/Persp) -->
            <div class="vfx-sidebar-header" id="hdr-views" style="cursor: pointer; flex: none;">
                <span style="display:flex; align-items:center; gap:5px;"><span class="vfx-collapse-arrow">▼</span><i data-lucide="video" style="width:11px; height:11px;"></i><span>${uiText("Viewport Views", "Виды камеры")}</span></span>
                <span id="lbl-active-view-name" style="font-size: 9.5px; color: #38bdf8; font-family: 'JetBrains Mono', monospace; font-weight: 600;">PERSP</span>
            </div>
            <div class="vfx-section-content" id="body-views" style="padding: 6px 8px; display: flex; flex-direction: column; gap: 4px; flex: none;">
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;">
                    <button class="vfx-btn" id="btn-view-top" ${tipAttr('Top View (Num 7)', 'Вид сверху (Сверху, Num 7)')} style="padding: 3px 2px; font-size: 10px; font-weight: 500; display: flex; align-items: center; justify-content: center; gap: 2px;">
                        <span>${uiText("Top", "Верх")}</span>
                    </button>
                    <button class="vfx-btn" id="btn-view-front" ${tipAttr('Front View (Num 1)', 'Вид спереди (Спереди, Num 1)')} style="padding: 3px 2px; font-size: 10px; font-weight: 500; display: flex; align-items: center; justify-content: center; gap: 2px;">
                        <span>${uiText("Front", "Перед")}</span>
                    </button>
                    <button class="vfx-btn" id="btn-view-right" ${tipAttr('Right View (Num 3)', 'Вид справа (Справа, Num 3)')} style="padding: 3px 2px; font-size: 10px; font-weight: 500; display: flex; align-items: center; justify-content: center; gap: 2px;">
                        <span>${uiText("Right", "Право")}</span>
                    </button>
                    <button class="vfx-btn" id="btn-view-bottom" ${tipAttr('Bottom View (Ctrl+Num 7)', 'Вид снизу (Снизу, Ctrl+Num 7)')} style="padding: 3px 2px; font-size: 10px; font-weight: 500; display: flex; align-items: center; justify-content: center; gap: 2px;">
                        <span>${uiText("Bottom", "Низ")}</span>
                    </button>
                    <button class="vfx-btn" id="btn-view-back" ${tipAttr('Back View / Reverse (Ctrl+Num 1)', 'Вид сзади / Обратка (Ctrl+Num 1)')} style="padding: 3px 2px; font-size: 10px; font-weight: 500; display: flex; align-items: center; justify-content: center; gap: 2px;">
                        <span>${uiText("Back", "Зад")}</span>
                    </button>
                    <button class="vfx-btn" id="btn-view-left" ${tipAttr('Left View (Ctrl+Num 3)', 'Вид слева (Слева, Ctrl+Num 3)')} style="padding: 3px 2px; font-size: 10px; font-weight: 500; display: flex; align-items: center; justify-content: center; gap: 2px;">
                        <span>${uiText("Left", "Лево")}</span>
                    </button>
                </div>
                <div style="display: flex; gap: 4px; margin-top: 2px;">
                    <button class="vfx-btn" id="btn-toggle-ortho" ${tipAttr('Toggle Perspective / Orthographic View (Num 5)', 'Переключить Перспектива / Ортографический вид (Num 5)')} style="flex: 1; padding: 3px 6px; font-size: 10px; font-weight: 600; display: flex; align-items: center; justify-content: center; gap: 4px; color: #38bdf8; border-color: rgba(56, 189, 248, 0.3);">
                        <i data-lucide="box" style="width: 11px; height: 11px;"></i>
                        <span id="lbl-toggle-ortho-text">${uiText("Persp", "Перспектива")}</span>
                    </button>
                    <button class="vfx-btn" id="btn-view-focus" ${tipAttr('Frame / Focus Selected Object (F)', 'Фокус на выделенном объекте (F)')} style="padding: 3px 7px; font-size: 10px; display: flex; align-items: center; gap: 3px;">
                        <i data-lucide="focus" style="width: 11px; height: 11px;"></i>
                        <span>F</span>
                    </button>
                </div>
            </div>

            <!-- 3D Cursor Section (Under Viewport Views and Above 3D Tools) -->
            <div class="vfx-sidebar-header" id="hdr-cursor3d" style="cursor: pointer; flex: none;">
                <span style="display:flex; align-items:center; gap:5px;">
                    <span class="vfx-collapse-arrow">▼</span>
                    <i data-lucide="target" style="width:11px; height:11px; color:#f43f5e;"></i>
                    <span>${uiText("3D Cursor", "3D-Курсор")}</span>
                </span>
                <button class="vfx-btn ue-btn-cursor-origin" id="sidebar-btn-cursor-origin-quick"
                    ${tipAttr('Reset 3D Cursor to Origin (Shift+C)', 'Сбросить 3D-курсор в центр мира (0, 0, 0) (Shift+C)')}
                    style="padding:1px 5px; font-size:9px; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.15); color:rgba(255,255,255,0.8); border-radius:3px; cursor:pointer;">
                    (0,0,0)
                </button>
            </div>
            <div class="vfx-section-content" id="body-cursor3d" style="padding: 6px 8px; display: flex; flex-direction: column; gap: 5px; flex: none;">
                <!-- Cursor Coordinates X, Y, Z -->
                <div style="display:flex; flex-direction:column; gap:3px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-size:9.5px; color:rgba(255,255,255,0.5);">${uiText("Cursor Coordinates (Shift+RMB):", "Координаты курсора (Shift+ПКМ):")}</span>
                    </div>
                    <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:4px;">
                        <div class="ue-striped-input-wrap ue5-input x" ${tipAttr('Cursor X (drag to scrub)', 'Позиция 3D-курсора X (перетащите для изменения)')}>
                            <span class="ue5-input-bar x"></span>
                            <input type="number" step="0.1" class="ue-vec-inp ue-cursor-inp-x" data-vec="cursor" data-axis="x" id="sidebar-cursor-x" value="0.00" />
                        </div>
                        <div class="ue-striped-input-wrap ue5-input y" ${tipAttr('Cursor Y (drag to scrub)', 'Позиция 3D-курсора Y (перетащите для изменения)')}>
                            <span class="ue5-input-bar y"></span>
                            <input type="number" step="0.1" class="ue-vec-inp ue-cursor-inp-y" data-vec="cursor" data-axis="y" id="sidebar-cursor-y" value="0.00" />
                        </div>
                        <div class="ue-striped-input-wrap ue5-input z" ${tipAttr('Cursor Z (drag to scrub)', 'Позиция 3D-курсора Z (перетащите для изменения)')}>
                            <span class="ue5-input-bar z"></span>
                            <input type="number" step="0.1" class="ue-vec-inp ue-cursor-inp-z" data-vec="cursor" data-axis="z" id="sidebar-cursor-z" value="0.00" />
                        </div>
                    </div>
                </div>

                <!-- Cursor Scale & Thickness -->
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:6px; margin-top:2px;">
                    <div style="display:flex; flex-direction:column; gap:2px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:9.5px; color:rgba(255,255,255,0.5);">${uiText("Scale:", "Масштаб:")}</span>
                            <input type="number" step="0.05" min="0.05" max="5.0" class="ue-vec-inp ue-cursor-inp-scale" data-vec="cursor" data-axis="scale" id="sidebar-cursor-scale" value="1.00" style="width:46px; text-align:right; font-size:9.5px; padding:1px 4px; background:rgba(0,0,0,0.35); border:1px solid rgba(255,255,255,0.12); border-radius:3px; color:#e4e4e7;" ${tipAttr('3D Cursor Scale', 'Масштаб 3D-курсора')} />
                        </div>
                        <input type="range" min="0.1" max="3.0" step="0.05" class="ue-cursor-slider-scale" id="sidebar-cursor-scale-slider" value="1.00" style="width:100%; accent-color:#f43f5e; height:4px; cursor:pointer;" ${tipAttr('3D Cursor Scale', 'Масштаб 3D-курсора')} />
                    </div>
                    <div style="display:flex; flex-direction:column; gap:2px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span style="font-size:9.5px; color:rgba(255,255,255,0.5);">${uiText("Thickness:", "Толщина:")}</span>
                            <input type="number" step="0.1" min="0.1" max="5.0" class="ue-vec-inp ue-cursor-inp-thickness" data-vec="cursor" data-axis="thickness" id="sidebar-cursor-thickness" value="1.00" style="width:46px; text-align:right; font-size:9.5px; padding:1px 4px; background:rgba(0,0,0,0.35); border:1px solid rgba(255,255,255,0.12); border-radius:3px; color:#e4e4e7;" ${tipAttr('3D Cursor Line Thickness', 'Толщина линий 3D-курсора')} />
                        </div>
                        <input type="range" min="0.2" max="4.0" step="0.05" class="ue-cursor-slider-thickness" id="sidebar-cursor-thickness-slider" value="1.00" style="width:100%; accent-color:#38bdf8; height:4px; cursor:pointer;" ${tipAttr('3D Cursor Line Thickness', 'Толщина линий 3D-курсора')} />
                    </div>
                </div>

                <!-- Cursor Actions: Cursor -> Sel, Sel -> Cursor, (0,0,0) -->
                <div style="display:grid; grid-template-columns: 1fr 1fr 50px; gap:4px; margin-top:2px;">
                    <button class="vfx-btn ue-btn-cursor-to-sel" id="sidebar-btn-cursor-to-sel"
                        ${tipAttr('Snap 3D Cursor to Selected Object (Shift+S)', 'Переместить 3D-курсор к выбранному объекту (Shift+S)')}
                        style="padding:4px 6px; font-size:10px; display:flex; align-items:center; justify-content:center; gap:4px; background:rgba(244,63,94,0.12); border:1px solid rgba(244,63,94,0.35); color:#f43f5e; border-radius:3px; cursor:pointer;">
                        <i data-lucide="target" style="width:11px; height:11px;"></i>
                        <span>${uiText("Cursor → Sel", "Курсор к объекту")}</span>
                    </button>
                    <button class="vfx-btn ue-btn-sel-to-cursor" id="sidebar-btn-sel-to-cursor"
                        ${tipAttr('Snap Selected Object to 3D Cursor', 'Переместить выбранный объект к 3D-курсору')}
                        style="padding:4px 6px; font-size:10px; display:flex; align-items:center; justify-content:center; gap:4px; background:rgba(56,189,248,0.12); border:1px solid rgba(56,189,248,0.35); color:#38bdf8; border-radius:3px; cursor:pointer;">
                        <i data-lucide="move" style="width:11px; height:11px;"></i>
                        <span>${uiText("Sel → Cursor", "Объект к курсору")}</span>
                    </button>
                    <button class="vfx-btn ue-btn-cursor-origin" id="sidebar-btn-cursor-origin"
                        ${tipAttr('Reset 3D Cursor to Origin (Shift+C)', 'Сбросить 3D-курсор в центр мира (0, 0, 0) (Shift+C)')}
                        style="padding:4px 6px; font-size:10px; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.15); color:rgba(255,255,255,0.85); border-radius:3px; display:flex; align-items:center; justify-content:center; cursor:pointer;">
                        (0,0,0)
                    </button>
                </div>
            </div>

            <!-- 2. Tools & Primitives -->
            <div class="vfx-sidebar-header" id="hdr-tools">
                <span><span class="vfx-collapse-arrow">▼</span>${uiText("🛠️ Tools & Primitives", "🛠️ Инструменты и Примитивы")}</span>
            </div>
            <div class="vfx-section-content" id="body-tools" style="padding: 6px 10px; display: flex; flex-direction: column; gap: 6px;">
                <!-- Camera & Scene Quick Actions (moved from top 3D bar) -->
                <div style="display: flex; align-items: center; gap: 4px; flex-wrap: wrap;">
                    <button class="vfx-btn" id="btn-sidebar-3d-orbit-cam" ${tipAttr('Toggle Orbit / Shot Camera (C)', 'Переключить свободную Орбиту / Камеру шота (C)')} style="height: 24px; padding: 0 7px; font-size: 10px; display: inline-flex; align-items: center; gap: 4px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.12); color: #ffffff; border-radius: 4px;">
                        <i data-lucide="video" style="width:11px; height:11px;"></i><span>${uiText("Orbit ⇄ Cam", "Орбита ⇄ Камера")}</span>
                    </button>
                    <div style="position: relative; display: inline-flex; align-items: center;">
                        <button class="vfx-btn" id="btn-sidebar-3d-add-cam" ${tipAttr('Add Default Animated 3D Camera', 'Добавить дефолтную анимированную 3D-камеру')} style="height: 24px; padding: 0 7px; font-size: 10px; display: inline-flex; align-items: center; gap: 4px; background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.4); color: #fbbf24; border-radius: 4px;">
                            <i data-lucide="camera" style="width:11px; height:11px;"></i><span>${uiText("+ Default Cam ▾", "+ Дефолт Камера ▾")}</span>
                        </button>
                        <div id="menu-sidebar-3d-cam-presets" style="display: none; position: absolute; top: calc(100% + 4px); left: 0; background: #141418; border: 1px solid rgba(255,255,255,0.18); border-radius: 6px; padding: 4px; z-index: 1000; box-shadow: 0 8px 24px rgba(0,0,0,0.85); min-width: 175px; flex-direction: column; gap: 2px;">
                            <button class="vfx-btn btn-sidebar-cam-preset" data-preset="dolly_in" style="width:100%; text-align:left; padding:5px 8px; font-size:10px; border:none; background:transparent; display:flex; align-items:center; gap:6px;">🎬 <span>${uiText("Dolly In (Push)", "Наезд камеры")}</span></button>
                            <button class="vfx-btn btn-sidebar-cam-preset" data-preset="truck_right" style="width:100%; text-align:left; padding:5px 8px; font-size:10px; border:none; background:transparent; display:flex; align-items:center; gap:6px;">↔️ <span>${uiText("Truck Left ➔ Right", "Проезд вправо")}</span></button>
                            <button class="vfx-btn btn-sidebar-cam-preset" data-preset="orbit_arc" style="width:100%; text-align:left; padding:5px 8px; font-size:10px; border:none; background:transparent; display:flex; align-items:center; gap:6px;">🔄 <span>${uiText("Orbit Arc (72°)", "Облет дугой (72°)")}</span></button>
                            <button class="vfx-btn btn-sidebar-cam-preset" data-preset="orbit_half" style="width:100%; text-align:left; padding:5px 8px; font-size:10px; border:none; background:transparent; display:flex; align-items:center; gap:6px;">🌓 <span>${uiText("Half Orbit (180°)", "Полуоблет (180°)")}</span></button>
                            <button class="vfx-btn btn-sidebar-cam-preset" data-preset="from_view" style="width:100%; text-align:left; padding:5px 8px; font-size:10px; border:none; background:transparent; display:flex; align-items:center; gap:6px;">👁️ <span>${uiText("From Current View", "С текущего ракурса")}</span></button>
                        </div>
                    </div>
                    <button class="vfx-btn" id="btn-sidebar-3d-keyframe-cam" ${tipAttr('Record current camera transform as a keyframe at current frame', 'Зафиксировать текущее положение камеры в текущем кадре таймлинии')} style="height: 24px; padding: 0 7px; font-size: 10px; display: inline-flex; align-items: center; gap: 4px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.15); color: #ffffff; border-radius: 4px;">
                        <i data-lucide="key" style="width:11px; height:11px; color:#f59e0b;"></i><span>${uiText("Keyframe Cam", "Ключ Камеры")}</span>
                    </button>
                    <button class="vfx-btn" id="btn-sidebar-3d-save-scene" ${tipAttr('Save 3D Scene / Level Snapshot', 'Сохранить 3D-сцену / слепок уровня')} style="height: 24px; padding: 0 7px; font-size: 10px; display: inline-flex; align-items: center; gap: 4px; background: rgba(245, 158, 11, 0.08); border: 1px dashed rgba(245, 158, 11, 0.45); color: #fde68a; border-radius: 4px;">
                        <i data-lucide="bookmark" style="width:11px; height:11px; color:#fbbf24;"></i><span>${uiText("+ Save 3D", "+ Сохр. 3D")}</span>
                    </button>
                </div>
                <div style="display: flex; flex-direction: column; gap: 4px; padding: 4px; background: rgba(255,255,255,0.02); border-radius: 4px; border: 1px solid rgba(255,255,255,0.06); box-sizing: border-box;">
                    <!-- Layer 1: Primitives -->
                    <div style="display: flex; align-items: center; gap: 4px;">
                        <button class="vfx-btn" id="btn-add-cube" ${tipAttr('Add Box', 'Добавить куб')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center;"><i data-lucide="box" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-add-sphere" ${tipAttr('Add Sphere', 'Добавить сферу')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center;"><i data-lucide="circle" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-add-cylinder" ${tipAttr('Add Cylinder', 'Добавить цилиндр')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center;"><i data-lucide="cylinder" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-add-cone" ${tipAttr('Add Cone', 'Добавить конус')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center;"><i data-lucide="cone" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-add-torus" ${tipAttr('Add Torus', 'Добавить тор')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center;"><i data-lucide="disc" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-add-plane" ${tipAttr('Add Floor Plane', 'Добавить плоскость')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center;"><i data-lucide="square" style="width:13px; height:13px;"></i></button>
                    </div>

                    <!-- Layer 2: Camera, Sun, Point Light, 3-Point Plane, 3D Cursor -->
                    <div style="display: flex; align-items: center; gap: 4px;">
                        <button class="vfx-btn" id="btn-add-camera" ${tipAttr('Add Free 3D Camera', 'Добавить 3D-камеру')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#00f0ff;"><i data-lucide="camera" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-add-sun-light" ${tipAttr('Add Sun Light (Intensity, Color, Hard/Soft Angle)', 'Добавить Солнце (Сила, Цвет, Угол: жёсткий/мягкий свет)')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#fbbf24;"><i data-lucide="sun" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-add-point-light" ${tipAttr('Add Light Source (Point ⇄ Area)', 'Добавить источник света (Точечный Point ⇄ Площадной Area)')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#fde047;"><i data-lucide="lightbulb" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-tool-3pt-plane" ${tipAttr('Pick 3 points in Point Cloud to create aligned Reference Plane', 'Выбрать 3 точки в облаке для построения плоскости')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#38bdf8;"><i data-lucide="crosshair" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-tool-3d-cursor" ${tipAttr('3D Cursor (Toggle ON/OFF) • Shift+RMB to place in 3D', '3D-курсор (Вкл/Выкл) • Shift+ПКМ для размещения в 3D')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#f43f5e;"><i data-lucide="target" style="width:13px; height:13px;"></i></button>
                    </div>

                    <!-- Layer 3: Character & Animation Pipeline in sequential order -->
                    <div style="display: flex; align-items: center; gap: 4px;">
                        <button class="vfx-btn" id="btn-add-skeleton" ${tipAttr('Add a skeleton without a mesh (UE mannequin bones) — animations from Mixamo / UE are retargeted onto it', 'Добавить скелет без меша (кости манекена UE) — на него ретаргетятся анимации Mixamo / UE')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#f59e0b;"><i data-lucide="user-plus" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-import-anim" ${tipAttr('Character animation (FBX/GLB: Mixamo, UE) for the selected character — retargeted automatically', 'Анимация персонажа (FBX/GLB: Mixamo, UE) для выбранного персонажа — ретаргет автоматически')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#f59e0b;"><i data-lucide="person-standing" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-toggle-bones" ${tipAttr('Show / hide the skeleton of the selected character', 'Показать / скрыть кости выбранного персонажа')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#f59e0b;"><i data-lucide="bone" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-pose-from-3d" ${tipAttr('Characters -> 2D pose layer (OpenPose) on the tracked video, through the shot camera', 'Персонажи на 2D-слой позы (OpenPose) на трекнутом видео через камеру шота')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#22d3ee;"><i data-lucide="image" style="width:13px; height:13px;"></i></button>
                        <button class="vfx-btn" id="btn-render-from-3d" ${tipAttr('Characters rendered through the shot camera -> RGB layer on the tracked video', 'Рендер персонажей через камеру шота -> RGB-слой на видео')} style="width:26px; height:26px; min-width:26px; padding:0; display:inline-flex; align-items:center; justify-content:center; color:#22d3ee;"><i data-lucide="box" style="width:13px; height:13px;"></i></button>

                        <input type="file" id="file-mesh-input" accept=".glb,.gltf,.obj,.fbx" style="display:none;" />
                        <input type="file" id="file-anim-input" accept=".fbx,.glb,.gltf" style="display:none;" />
                        <select id="sel-anim-clip" class="vfx-interp-select" ${tipAttr('Animation clip from the file', 'Клип анимации из файла')} style="display:none; max-width:140px; height:24px; font-size:10px; padding:0 4px;"></select>
                        <button class="vfx-btn" id="btn-toggle-hips-motion" ${tipAttr('Toggle Hips Translation / Root Motion', 'Вкл/выкл перемещение таза (Root Motion)')} style="display:none; height:24px; font-size:10px; padding:0 6px; color:#f59e0b;">Hips: ON</button>
                        <button class="vfx-btn" id="btn-bake-root-motion" ${tipAttr('Bake Hips translation into Object Pivot keyframes', 'Запечь смещение таза в пивот объекта')} style="display:none; height:24px; font-size:10px; padding:0 6px; color:#38bdf8;">📌 Root to Pivot</button>
                    </div>
                </div>
                <!-- Quick Object Color Bar (Always visible under Primitives in 3D Tools) -->
                <div id="tools-obj-color-bar" style="display: flex; align-items: center; justify-content: space-between; gap: 5px; margin-top: 5px; padding: 4px 6px; background: rgba(255,255,255,0.025); border-radius: 4px; border: 1px solid rgba(255,255,255,0.07); flex-wrap: wrap;">
                    <span style="font-size: 10px; color: rgba(255,255,255,0.65); font-weight: 500; display: inline-flex; align-items: center; gap: 4px;">
                        🎨 <span>${uiText("Object Color:", "Цвет объекта:")}</span>
                    </span>
                    <div style="display: flex; align-items: center; gap: 3px; flex-wrap: wrap;">
                        <input type="color" id="sidebar-color-picker" value="#cccccc" style="width: 22px; height: 20px; border: 1px solid rgba(255,255,255,0.25); border-radius: 3px; background: none; cursor: pointer; padding: 0;" ${tipAttr('Choose Selected Object Color', 'Выбрать цвет выделенного 3D-объекта')} />
                        <button class="vfx-btn" id="sidebar-swatch-clay" ${tipAttr("Clay (#cccccc)", "Глина (#cccccc)")} style="padding: 1px 5px; height: 18px; font-size: 9.5px; background: #cccccc; color: #111; font-weight: 600; border: none; border-radius: 3px;">Clay</button>
                        <button class="vfx-btn sidebar-quick-color-swatch" data-col="#ffffff" ${tipAttr("White (#ffffff)", "Белый (#ffffff)")} style="width: 16px; height: 18px; padding: 0; background: #ffffff; border: 1px solid rgba(0,0,0,0.3); border-radius: 3px;"></button>
                        <button class="vfx-btn" id="sidebar-swatch-red" ${tipAttr("Red (#e53935)", "Красный (#e53935)")} style="width: 16px; height: 18px; padding: 0; background: #e53935; border: none; border-radius: 3px;"></button>
                        <button class="vfx-btn sidebar-quick-color-swatch" data-col="#fb8c00" ${tipAttr("Orange (#fb8c00)", "Оранжевый (#fb8c00)")} style="width: 16px; height: 18px; padding: 0; background: #fb8c00; border: none; border-radius: 3px;"></button>
                        <button class="vfx-btn" id="sidebar-swatch-gold" ${tipAttr("Gold (#fdd835)", "Золотой (#fdd835)")} style="width: 16px; height: 18px; padding: 0; background: #fdd835; border: none; border-radius: 3px;"></button>
                        <button class="vfx-btn" id="sidebar-swatch-green" ${tipAttr("Green (#43a047)", "Зелёный (#43a047)")} style="width: 16px; height: 18px; padding: 0; background: #43a047; border: none; border-radius: 3px;"></button>
                        <button class="vfx-btn sidebar-quick-color-swatch" data-col="#00acc1" ${tipAttr("Cyan (#00acc1)", "Бирюзовый (#00acc1)")} style="width: 16px; height: 18px; padding: 0; background: #00acc1; border: none; border-radius: 3px;"></button>
                        <button class="vfx-btn" id="sidebar-swatch-blue" ${tipAttr("Blue (#1e88e5)", "Синий (#1e88e5)")} style="width: 16px; height: 18px; padding: 0; background: #1e88e5; border: none; border-radius: 3px;"></button>
                        <button class="vfx-btn sidebar-quick-color-swatch" data-col="#8e24aa" ${tipAttr("Purple (#8e24aa)", "Фиолетовый (#8e24aa)")} style="width: 16px; height: 18px; padding: 0; background: #8e24aa; border: none; border-radius: 3px;"></button>
                        <button class="vfx-btn sidebar-quick-color-swatch" data-col="#374151" ${tipAttr("Dark Slate (#374151)", "Тёмно-серый (#374151)")} style="width: 16px; height: 18px; padding: 0; background: #374151; border: 1px solid rgba(255,255,255,0.15); border-radius: 3px;"></button>
                    </div>
                </div>
                <!-- Dedicated Light Source Controls (shown in #body-tools when Sun / Point / Area light is selected) -->
                <div id="sidebar-light-inspector" style="display: none; flex-direction: column; gap: 5px; margin-top: 6px; padding: 6px 8px; background: rgba(251, 191, 36, 0.05); border: 1px solid rgba(251, 191, 36, 0.28); border-radius: 4px;">
                    <div style="display: flex; align-items: center; justify-content: space-between; font-size: 10.5px; color: #fbbf24; font-weight: 600;">
                        <span>☀️ ${uiText("Light Source", "Источник света")}</span>
                        <div style="display: flex; gap: 3px;">
                            <button class="vfx-btn sidebar-light-mode-btn" data-lmode="sun" style="padding: 1px 6px; font-size: 9.5px;">☀️ Sun</button>
                            <button class="vfx-btn sidebar-light-mode-btn" data-lmode="point" style="padding: 1px 6px; font-size: 9.5px;">💡 Point</button>
                            <button class="vfx-btn sidebar-light-mode-btn" data-lmode="area" style="padding: 1px 6px; font-size: 9.5px;">▭ Area</button>
                        </div>
                    </div>
                    <div class="vfx-transform-row">
                        <span class="vfx-transform-label">${uiText("Intensity:", "Сила:")}</span>
                        <input type="range" id="sidebar-light-intensity-slider" min="0" max="15" step="0.1" value="2.5" style="flex: 1; accent-color: #fbbf24; cursor: pointer;" />
                        <input type="number" id="sidebar-light-intensity-num" min="0" max="50" step="0.1" value="2.5" class="vfx-num-input" style="width: 48px;" />
                    </div>
                    <div class="vfx-transform-row">
                        <span class="vfx-transform-label" ${tipAttr('0° = Hard crisp shadows, 45° = Soft diffused light', '0° = Жёсткий свет (чёткие тени), 45° = Мягкий рассеянный свет')}>${uiText("Angle (°):", "Угол (°):")}</span>
                        <input type="range" id="sidebar-light-angle-slider" min="0" max="45" step="1" value="8" style="flex: 1; accent-color: #38bdf8; cursor: pointer;" />
                        <input type="number" id="sidebar-light-angle-num" min="0" max="45" step="1" value="8" class="vfx-num-input" style="width: 48px;" />
                    </div>
                    <div id="sidebar-light-range-row" class="vfx-transform-row" style="display: none;">
                        <span class="vfx-transform-label">${uiText("Range (m):", "Дальность:")}</span>
                        <input type="range" id="sidebar-light-range-slider" min="1" max="60" step="0.5" value="20" style="flex: 1; accent-color: #fde047; cursor: pointer;" />
                        <input type="number" id="sidebar-light-range-num" min="1" max="120" step="0.5" value="20" class="vfx-num-input" style="width: 48px;" />
                    </div>
                    <div id="sidebar-light-area-row" class="vfx-transform-row" style="display: none;">
                        <span class="vfx-transform-label">${uiText("Area W×H:", "Размер W×H:")}</span>
                        <div class="vfx-num-input-group">
                            <span class="vfx-axis-tag vfx-axis-x">W</span>
                            <input type="number" id="sidebar-light-area-w" min="0.2" max="15" step="0.1" value="2.0" class="vfx-num-input" />
                            <span class="vfx-axis-tag vfx-axis-y">H</span>
                            <input type="number" id="sidebar-light-area-h" min="0.2" max="15" step="0.1" value="1.2" class="vfx-num-input" />
                        </div>
                    </div>
                </div>
                <!-- Hidden legacy proxy elements for hotkeys and left dock shortcuts -->
                <div style="display: none !important;" aria-hidden="true">
                    <button id="btn-import-mesh" style="display: none !important;"></button>
                    <button id="btn-tool-select" style="display: none !important;"></button>
                    <button id="btn-tool-translate" style="display: none !important;"></button>
                    <button id="btn-tool-rotate" style="display: none !important;"></button>
                    <button id="btn-tool-scale" style="display: none !important;"></button>
                    <button id="btn-coord-space" style="display: none !important;"></button>
                    <button id="btn-cam-view" style="display: none !important;"></button>
                </div>
            </div>



            <!-- 4. Environment & Stage -->
            <div class="vfx-sidebar-header" id="hdr-env">
                <span style="display:flex; align-items:center; gap:5px;"><span class="vfx-collapse-arrow">▼</span><i data-lucide="sliders" style="width:11px; height:11px;"></i><span>Environment & Stage</span></span>
            </div>
            <div class="vfx-section-content" id="body-env" style="padding: 6px 10px; display: flex; flex-direction: column; gap: 6px;">
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 10.5px;" ${tipAttr("Viewport exposure (tone mapping). Double-click to reset", "Экспозиция вьюпорта (tone mapping). Двойной клик — сброс")}>
                    <span style="color: rgba(255,255,255,0.5);">${uiText("Exposure", "Экспозиция")}</span>
                    <input type="range" id="slider-viewport-exposure" min="0.2" max="2.5" step="0.05" value="1.0" style="flex: 1; accent-color:#fbbf24;" />
                    <span id="lbl-viewport-exposure" style="font-family: 'JetBrains Mono', monospace; font-size: 9.5px; color: #fff; min-width: 30px; text-align: right;">1.00</span>
                </div>
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 10.5px;" ${tipAttr("Built-in studio light. Auto-dims when you add your own Sun/Point/Area lights. Double-click to reset", "Встроенный студийный свет. Автоматически приглушается, когда в сцене есть свои источники. Двойной клик — сброс")}>
                    <span style="color: rgba(255,255,255,0.5);">${uiText("Studio Fill", "Студ. заливка")}</span>
                    <input type="range" id="slider-studio-fill" min="0" max="2" step="0.05" value="1.0" style="flex: 1; accent-color:#38bdf8;" />
                    <span id="lbl-studio-fill" style="font-family: 'JetBrains Mono', monospace; font-size: 9.5px; color: #fff; min-width: 30px; text-align: right;">1.00</span>
                </div>
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <span style="font-size: 10.5px; color: rgba(255,255,255,0.5);">Stage Color</span>
                    <div style="display: flex; align-items: center; gap: 4px;">
                        <input type="color" id="env-bg-color-picker" value="#0a0a0d" style="width: 20px; height: 20px; border: 1px solid rgba(255,255,255,0.12); border-radius: 3px; background: none; cursor: pointer; padding: 0;" />
                        <button class="vfx-env-swatch" id="env-swatch-dark" style="background: #0a0a0d;" ${tipAttr("Cinema Dark", "Тёмный кинозал")}></button>
                        <button class="vfx-env-swatch" id="env-swatch-slate" style="background: #141416;" ${tipAttr("Carbon Studio", "Карбоновая студия")}></button>
                        <button class="vfx-env-swatch" id="env-swatch-gray" style="background: #222226;" ${tipAttr("Neutral Zinc", "Нейтральный серый")}></button>
                    </div>
                </div>
                <div style="display: flex; align-items: center; justify-content: space-between; font-size: 10.5px;">
                    <span style="color: rgba(255,255,255,0.5);">Ground Grid</span>
                    <div style="display: flex; align-items: center; gap: 5px;">
                        <input type="color" id="grid-color-picker" value="#4f80ff" ${tipAttr('Grid Color', 'Цвет сетки')} style="width: 20px; height: 20px; border: 1px solid rgba(255,255,255,0.12); border-radius: 3px; background: none; cursor: pointer; padding: 0;" />
                        <button class="vfx-btn" id="btn-toggle-grid" style="font-size: 10px; padding: 2px 7px;">Grid: Visible</button>
                    </div>
                </div>
                <div style="display: flex; align-items: center; justify-content: space-between; font-size: 10.5px;">
                    <span style="color: rgba(255,255,255,0.5);">LiDAR Cloud</span>
                    <div style="display: flex; align-items: center; gap: 4px;">
                        <span style="color: rgba(255,255,255,0.4); font-size: 9.5px;">Size</span>
                        <input type="range" id="slider-cloud-size" min="0.01" max="1.50" step="0.01" value="0.04" style="width: 44px; accent-color:#ffffff;" />
                        <span id="lbl-cloud-size" style="font-family: 'JetBrains Mono', monospace; font-size: 9.5px; color: #ffffff; min-width: 24px;">0.04</span>
                        <span style="color: rgba(255,255,255,0.4); font-size: 9.5px; margin-left: 2px;">Alpha</span>
                        <input type="range" id="slider-cloud-alpha" min="0.0" max="1.0" step="0.05" value="0.90" style="width: 44px; accent-color:#ffffff;" />
                        <span id="lbl-cloud-alpha" style="font-family: 'JetBrains Mono', monospace; font-size: 9.5px; color: #ffffff; min-width: 24px;">90%</span>
                    </div>
                </div>
            </div>

            <!-- 5. Actor Cutout Billboard -->
            <div class="vfx-sidebar-header" id="hdr-actor">
                <span style="display:flex; align-items:center; gap:5px;"><span class="vfx-collapse-arrow">▼</span><i data-lucide="user-check" style="width:11px; height:11px;"></i><span>Actor Cutout Plane</span></span>
            </div>
            <div class="vfx-section-content" id="body-actor" style="padding: 6px 10px; display: flex; flex-direction: column; gap: 6px;">
                <div class="vfx-panel-row" style="display: flex; align-items: center; justify-content: space-between;">
                    <button class="vfx-btn" id="btn-toggle-actor" style="font-size: 10px; padding: 2px 8px;">Actor: OFF</button>
                    <div style="display: flex; align-items: center; gap: 4px; font-size: 10px; color: rgba(255,255,255,0.5);">
                        <span>Dist</span>
                        <input type="range" id="slider-actor-dist" min="0.5" max="15.0" step="0.1" value="3.0" style="width: 48px; accent-color:#ffffff;" />
                        <span>Alpha</span>
                        <input type="range" id="slider-actor-alpha" min="0.1" max="1.0" step="0.05" value="0.85" style="width: 44px; accent-color:#ffffff;" />
                    </div>
                </div>
                <!-- Custom Actor Media Dropzone / Slot (Manual Addition Only) -->
                <div id="actor-cutout-dropzone" style="border: 1px dashed rgba(255,255,255,0.22); border-radius: 6px; padding: 7px 6px; text-align: center; background: rgba(0,0,0,0.25); cursor: pointer; transition: border-color 0.15s, background-color 0.15s; display: flex; flex-direction: column; align-items: center; gap: 4px;">
                    <input type="file" id="input-actor-cutout" accept="image/*,video/*,.mp4,.webm,.mov,.png,.jpg,.jpeg,.webp" style="display: none;" />
                    <div id="actor-cutout-empty" style="display: flex; flex-direction: column; align-items: center; gap: 3px; pointer-events: none;">
                        <i data-lucide="video" style="width: 18px; height: 18px; color: rgba(255,255,255,0.4);"></i>
                        <span style="font-size: 10px; color: rgba(255,255,255,0.6);">${uiText('Drop Video or Image here', 'Перетащите видео или изображение сюда')}</span>
                        <span style="font-size: 9px; color: rgba(255,255,255,0.35);">${uiText('or click to browse file', 'или кликните для выбора')}</span>
                    </div>
                    <div id="actor-cutout-preview-box" style="display: none; width: 100%; align-items: center; gap: 8px;">
                        <div style="width: 44px; height: 44px; border-radius: 4px; overflow: hidden; background: repeating-conic-gradient(#333 0% 25%, #222 0% 50%) 50% / 10px 10px; border: 1px solid rgba(255,255,255,0.15); flex-shrink: 0; display: flex; align-items: center; justify-content: center; position: relative;">
                            <img id="actor-cutout-thumb" src="" alt="Actor" style="max-width: 100%; max-height: 100%; object-fit: contain; display: none;" />
                            <video id="actor-cutout-thumb-video" src="" muted playsinline autoplay loop style="max-width: 100%; max-height: 100%; object-fit: contain; display: none;"></video>
                        </div>
                        <div style="flex: 1; min-width: 0; text-align: left; display: flex; flex-direction: column; gap: 2px;">
                            <div style="display: flex; align-items: center; gap: 4px; overflow: hidden;">
                                <span id="actor-cutout-badge" style="display: none; font-size: 8px; font-weight: 700; padding: 1px 4px; border-radius: 3px; font-family: monospace;"></span>
                                <span id="actor-cutout-label" style="font-size: 10px; color: #fff; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></span>
                            </div>
                            <div style="display: flex; align-items: center; gap: 4px; margin-top: 2px;">
                                <button class="vfx-btn" id="btn-actor-rembg" ${tipAttr('Remove background with BiRefNet AI', 'Удалить фон через BiRefNet')} style="font-size: 9.5px; padding: 1px 6px; color: #38bdf8;">✂️ ${uiText('Remove BG', 'Удалить фон')}</button>
                                <button class="vfx-btn" id="btn-actor-reset" ${tipAttr('Remove custom video / image from plane', 'Удалить видео / изображение с плейна')} style="font-size: 9.5px; padding: 1px 6px; color: #f87171;">✕</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>


        </div>

        <!-- Horizontal Splitter -->
        <div class="vfx-splitter-h" id="splitter-studio-h" ${tipAttr('Drag vertically to resize monitor', 'Потяните по вертикали для изменения размера монитора')}></div>

        <!-- Bottom Half: Monitor -->
        <div class="vfx-camera-monitor-pane" id="pane-cam-monitor">
            <div class="vfx-monitor-header">
                <span class="vfx-monitor-title" style="display:inline-flex; align-items:center; gap:5px;">
                    <i data-lucide="video" style="width:11px; height:11px;"></i>
                    <span>${uiText('MONITOR', 'МОНИТОР')}</span>
                </span>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <div class="vfx-monitor-pill-group">
                        <button class="vfx-monitor-pill-btn" id="btn-mon-render-camera" ${tipAttr('Render 3D camera sequence and add result to Gallery', 'Отрендерить секвенцию 3D-камеры и добавить результат в галерею')} style="color: #38bdf8;">🎬 ${uiText('Render Camera', 'Рендер камеры')}</button>
                        <button class="vfx-monitor-pill-btn vfx-pill-active" id="btn-mon-preview-toggle" ${tipAttr('Toggle real-time 3D camera preview', 'Вкл/выкл трансляцию камеры')}>● Preview ON</button>
                    </div>
                    <span class="vfx-monitor-badge" id="monitor-aspect-badge">16:9 • FILM GATE</span>
                </div>
            </div>
            <div class="vfx-monitor-frame-container" id="monitor-frame-box">
                <div class="vfx-film-gate-box" id="film-gate-box">
                    <canvas id="monitor-3d-canvas" style="position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; pointer-events: none; z-index: 1;"></canvas>

                    <img id="monitor-empty-watermark" src="/vfx/studio/keshiki_watermark_full.png" class="vfx-empty-watermark-card-logo" style="z-index: 0;" />
                    <div class="vfx-gate-corner vfx-corner-tl" style="z-index: 4;"></div>
                    <div class="vfx-gate-corner vfx-corner-tr" style="z-index: 4;"></div>
                    <div class="vfx-gate-corner vfx-corner-bl" style="z-index: 4;"></div>
                    <div class="vfx-gate-corner vfx-corner-br" style="z-index: 4;"></div>
                    <div class="vfx-gate-safe" style="z-index: 4;"></div>
                    <div class="vfx-gate-crosshair" style="z-index: 4;"></div>
                    <div id="render-progress-card" style="display: none; position: absolute; left: 10px; right: 10px; bottom: 10px; z-index: 10; background: rgba(9, 10, 16, 0.88); border: 1px solid rgba(56, 189, 248, 0.45); border-radius: 6px; padding: 6px 10px; flex-direction: column; gap: 4px; pointer-events: none;">
                        <div id="render-progress-label" style="font-size: 10px; font-weight: 600; color: #38bdf8; font-family: monospace;">Rendering...</div>
                        <div style="width: 100%; height: 4px; background: rgba(255,255,255,0.12); border-radius: 2px; overflow: hidden;">
                            <div id="render-progress-fill" style="width: 0%; height: 100%; background: #38bdf8; transition: width 0.08s linear;"></div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="vfx-monitor-footer-bar">
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span>Safe Area: 90%</span>

                </div>
                <span id="monitor-lens-info">Focal: 35mm • Sensor: 36×24mm</span>
            </div>
        </div>
    `;
    workspace.appendChild(studioRightCol);

    // 3. Footer / Timeline (Google Stitch Minimalist Bar)
    const footer = document.createElement("div");
    footer.className = "vfx-modal-footer";
    footer.innerHTML = `
        <div class="vfx-timeline-track-container" id="timeline-track-container" style="position: relative;">
            <input type="range" class="vfx-timeline-slider" id="timeline-slider" min="0" max="${totalFrames - 1}" value="${currentFrame}" />
            <div class="vfx-keyframe-markers" id="key-markers-track"></div>
            <div id="timeline-marquee-box" style="position: absolute; display: none; background: rgba(56, 189, 248, 0.22); border: 1px solid #38bdf8; border-radius: 2px; pointer-events: none; z-index: 10;"></div>
        </div>
        <div class="vfx-timeline-controls">
            <div class="vfx-key-group" style="overflow-x: auto; max-width: 65%;">
                <button class="vfx-btn" id="btn-first-frame" ${tipAttr("First Frame (|◀)", "Первый кадр (|◀)")} style="padding: 2px 6px;">|◀</button>
                <button class="vfx-btn" id="btn-prev-key" ${tipAttr("Previous Keyframe ([)", "Предыдущий ключевой кадр ([)")} style="padding: 2px 7px;">⏮ Key</button>
                <button class="vfx-btn" id="btn-prev-frame" ${tipAttr("Step -1 Frame (←)", "На 1 кадр назад (←)")} style="padding: 2px 7px;">⏴</button>
                <button class="vfx-btn" id="btn-play-pause" ${tipAttr("Play / Pause (Space)", "Воспроизведение / Пауза (Пробел)")} style="min-width: 64px; background:#ffffff; color:#09090b; border-color:#ffffff; font-weight:600;">▶ Play</button>
                <button class="vfx-btn" id="btn-next-frame" ${tipAttr("Step +1 Frame (→)", "На 1 кадр вперёд (→)")} style="padding: 2px 7px;">⏵</button>
                <button class="vfx-btn" id="btn-next-key" ${tipAttr("Next Keyframe (])", "Следующий ключевой кадр (])")} style="padding: 2px 7px;">Key ⏭</button>
                <button class="vfx-btn" id="btn-last-frame" ${tipAttr("Last Frame (▶|)", "Последний кадр (▶|)")} style="padding: 2px 6px;">▶|</button>
                <span style="font-family: 'JetBrains Mono', monospace; font-size: 10.5px; color: rgba(255,255,255,0.75); margin-left: 6px; white-space: nowrap; display: inline-flex; align-items: center; gap: 3px;" id="hud-frame-wrap">
                    <span>Frame:</span>
                    <input type="number" id="inp-current-frame" min="0" max="${totalFrames - 1}" value="${currentFrame}"
                        ${tipAttr('Set current frame manually (Enter)', 'Вбить текущий кадр вручную (Enter)')}
                        style="width: 46px; background: #0c0c10; color: #00f0ff; border: 1px solid rgba(0,240,255,0.35); border-radius: 3px; padding: 1px 4px; font-family: 'JetBrains Mono', monospace; font-size: 10.5px; font-weight: 600; text-align: center; outline: none;" />
                    <span id="hud-frame-label">/ ${String(totalFrames).padStart(3, '0')}</span>
                </span>
                <div id="vfx-timeline-active-context-bar" style="display: inline-flex; align-items: center; gap: 4px; margin-left: 8px;"></div>
            </div>
            <div class="vfx-key-group">
                <button class="vfx-btn" id="btn-context-key" ${tipAttr('Bind character, location, storyboard or action to frame (K)', 'Привязать персонажа, локацию, раскадровку или действие к кадру (K)')} style="padding: 2px 8px; font-weight: 500; background: rgba(255,255,255,0.08); border-color: rgba(255,255,255,0.25);">🔗 + Key Context (K)</button>
                <button class="vfx-btn" id="btn-set-key" style="padding: 2px 7px;" ${tipAttr('Add 3D Transform Keyframe', 'Ключ 3D трансформации')}>+ 3D Key</button>
                <button class="vfx-btn" id="btn-del-key" style="padding: 2px 7px;" ${tipAttr('Delete keys on current frame or selected keys (Del)', 'Удалить ключи на текущем кадре или все выделенные ключи (Del)')}>- Del Key</button>
                <button class="vfx-btn" id="btn-del-all-keys" style="padding: 2px 7px; color:#f87171; border-color:rgba(239,68,68,0.35);" ${tipAttr('Delete ALL keys for selected object or camera', 'Удалить ВСЕ ключи выбранного объекта или камеры')}>${uiText('🗑 All Keys', '🗑 Все ключи')}</button>
                <button class="vfx-btn" id="btn-timeline-script" ${tipAttr('Compile chronological prompt script for LTX-Video and AI Director', 'Скомпилировать хронологический скрипт промптов для LTX-Video и ИИ-Режиссера')} style="padding: 2px 8px; background: rgba(255,255,255,0.06); border-color: rgba(255,255,255,0.18);">📜 Script / Prompt</button>
                <select class="vfx-interp-select" id="sel-interp">
                    <option value="bezier">Bezier</option>
                    <option value="linear">Linear</option>
                    <option value="step">Step</option>
                </select>
            </div>
        </div>
    `;
    // Footer / Timeline is now passed to initStudioArchitecture and mounted in the Preview Zone ending at splitterV

    // 4. Status Bar (Bottom Studio Frame & System Telemetry)
    const statusBar = document.createElement("div");
    statusBar.className = "vfx-status-bar";
    statusBar.id = "vfx-studio-status-bar";
    statusBar.innerHTML = `
        <div class="vfx-status-bar-group">
            <div class="vfx-status-pill" id="st-pill-engine" ${tipAttr("ComfyUI & Keshiki Backend Engine Status", "Статус движка ComfyUI и бэкенда Keshiki")}>
                <span class="vfx-status-dot-green"></span>
                <span style="font-weight: 600; color: #ffffff;">KESHIKI CORE</span>
                <span style="color: rgba(255,255,255,0.4);">READY</span>
            </div>
            <div class="vfx-status-sep"></div>
            <div class="vfx-status-pill" id="st-pill-mode" ${tipAttr("Active Workspace Dimension", "Активный режим рабочего пространства (1D / 2D / 3D)")}>
                <i data-lucide="box" style="width: 11px; height: 11px; stroke-width: 1.5; color: rgba(255,255,255,0.65);"></i>
                <span id="st-mode-label" style="font-weight: 500; color: #ffffff;">3D Perspective</span>
            </div>
            <div class="vfx-status-pill" id="st-pill-fps" style="cursor: pointer;" ${tipAttr("Project Frame Rate & Duration Settings (Click to configure)", "Частота кадров (FPS) и длительность проекта (нажмите для настройки)")}>
                <i data-lucide="gauge" style="width: 11px; height: 11px; stroke-width: 1.5; color: rgba(255,255,255,0.65);"></i>
                <span id="st-fps-label" style="font-weight: 500; color: #ffffff;">24.0 FPS</span>
            </div>
            <div class="vfx-status-pill" id="st-pill-cam" ${tipAttr("Shot Camera & Projection Settings", "Камера шота и параметры проекции")}>
                <i data-lucide="camera" style="width: 11px; height: 11px; stroke-width: 1.5; color: rgba(255,255,255,0.65);"></i>
                <span id="st-cam-label">ShotCam: 35mm</span>
            </div>
            <div class="vfx-status-pill" id="st-pill-project" ${tipAttr("Current Active Project Sequence", "Текущий активный проект")}>
                <i data-lucide="folder" style="width: 11px; height: 11px; stroke-width: 1.5; color: rgba(255,255,255,0.65);"></i>
                <span id="st-project-label">Cyberpunk_Rooftop_Seq04</span>
            </div>
        </div>

        <div class="vfx-status-bar-group" style="flex: 1; justify-content: center; overflow: hidden; padding: 0 8px;">
            <div id="st-telemetry-cluster" style="display: flex; align-items: center; justify-content: center; gap: 12px; font-size: 9.5px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; user-select: none;">
                <!-- CPU -->
                <div class="vfx-telem-item" id="telem-cpu" style="display: flex; align-items: center; gap: 4px; cursor: help;" title="CPU Utilization">
                    <span style="color: rgba(255,255,255,0.45); font-weight: 500;">CPU</span>
                    <canvas id="telem-canvas-cpu" width="34" height="13" style="border-radius: 2px; background: rgba(255,255,255,0.04);"></canvas>
                    <span id="telem-val-cpu" style="color: #67e8f9; font-weight: 600; min-width: 25px;">--%</span>
                </div>
                <!-- RAM -->
                <div class="vfx-telem-item" id="telem-ram" style="display: flex; align-items: center; gap: 4px; cursor: help;" title="RAM Utilization">
                    <span style="color: rgba(255,255,255,0.45); font-weight: 500;">RAM</span>
                    <canvas id="telem-canvas-ram" width="34" height="13" style="border-radius: 2px; background: rgba(255,255,255,0.04);"></canvas>
                    <span id="telem-val-ram" style="color: #c084fc; font-weight: 600; min-width: 25px;">--%</span>
                </div>
                <!-- GPU -->
                <div class="vfx-telem-item" id="telem-gpu" style="display: flex; align-items: center; gap: 4px; cursor: help;" title="GPU / VRAM Utilization">
                    <span style="color: rgba(255,255,255,0.45); font-weight: 500;">GPU</span>
                    <canvas id="telem-canvas-gpu" width="34" height="13" style="border-radius: 2px; background: rgba(255,255,255,0.04);"></canvas>
                    <span id="telem-val-gpu" style="color: #34d399; font-weight: 600; min-width: 25px;">--%</span>
                </div>
                <!-- DISK -->
                <div class="vfx-telem-item" id="telem-disk" style="display: flex; align-items: center; gap: 4px; cursor: help;" title="Active Disk I/O (Studio & System)">
                    <span style="color: rgba(255,255,255,0.45); font-weight: 500;">DISK</span>
                    <canvas id="telem-canvas-disk" width="34" height="13" style="border-radius: 2px; background: rgba(255,255,255,0.04);"></canvas>
                    <span id="telem-val-disk" style="color: #fbbf24; font-weight: 600; min-width: 32px;">-- MB/s</span>
                </div>
            </div>
        </div>

        <div class="vfx-status-bar-group">
            <div class="vfx-status-pill" id="st-pill-selection" ${tipAttr("Selected Actor / Proxy Object", "Выбранный объект сцены")}>
                <i data-lucide="crosshair" style="width: 11px; height: 11px; stroke-width: 1.5; color: rgba(255,255,255,0.65);"></i>
                <span id="st-selected-label">No Selection</span>
            </div>
            <div class="vfx-status-pill" id="st-pill-actors" ${tipAttr("Total Scene Actors", "Всего объектов в сцене")}>
                <i data-lucide="layers" style="width: 11px; height: 11px; stroke-width: 1.5; color: rgba(255,255,255,0.65);"></i>
                <span id="st-actors-count">0 Actors</span>
            </div>
            <div class="vfx-status-sep"></div>
            <div class="vfx-status-pill" id="st-pill-vram" ${tipAttr("GPU VRAM Allocation", "Использование видеопамяти GPU (VRAM)")}>
                <i data-lucide="cpu" style="width: 11px; height: 11px; stroke-width: 1.5; color: rgba(255,255,255,0.65);"></i>
                <span>VRAM: <strong style="color: #ffffff; font-weight: 500;">18.4</strong>/24G</span>
            </div>
            <div class="vfx-status-pill" id="st-btn-terminal" style="cursor: pointer;" ${tipAttr("Toggle Debug Terminal & Logs", "Открыть / скрыть терминал отладки и логи")}>
                <i data-lucide="terminal" style="width: 11px; height: 11px; stroke-width: 1.5; color: rgba(255,255,255,0.65);"></i>
                <span>Console</span>
                <span id="st-err-badge" style="display: none; background: #ffffff; color: #000000; border-radius: 3px; padding: 0 3px; font-size: 8px; font-weight: 700;">0</span>
            </div>
        </div>
    `;
    container.appendChild(statusBar);
    appRoot.appendChild(container);

    function updateStatusBarTelemetry(patch = {}) {
        if (patch.selected !== undefined) {
            const selEl = statusBar.querySelector("#st-selected-label");
            if (selEl) {
                if (!patch.selected) {
                    selEl.innerText = "No Selection";
                    selEl.style.color = "rgba(255,255,255,0.55)";
                } else {
                    const name = patch.selected.userData?.name || patch.selected.name || "Actor";
                    const type = (patch.selected.userData?.type || "mesh").toUpperCase();
                    selEl.innerText = `${name} [${type}]`;
                    selEl.style.color = "#ffffff";
                }
            }
        }
        if (patch.mode !== undefined) {
            const mEl = statusBar.querySelector("#st-mode-label");
            if (mEl) mEl.innerText = patch.mode;
        }
        if (patch.actorsCount !== undefined) {
            const aEl = statusBar.querySelector("#st-actors-count");
            if (aEl) aEl.innerText = `${patch.actorsCount} Actors`;
        }
        if (patch.cam !== undefined) {
            const cEl = statusBar.querySelector("#st-cam-label");
            if (cEl) cEl.innerText = patch.cam;
        }
        if (patch.message !== undefined) {
            const hEl = statusBar.querySelector("#st-hint-text");
            if (hEl) {
                hEl.innerHTML = `<i data-lucide="info" style="width: 11px; height: 11px; stroke-width: 1.5; color: #ffffff;"></i><span style="color: #ffffff;">${patch.message}</span>`;
                if (window.lucide) window.lucide.createIcons();
            }
        }
        if (patch.errors !== undefined) {
            const bEl = statusBar.querySelector("#st-err-badge");
            if (bEl) {
                if (patch.errors > 0) {
                    bEl.style.display = "inline-block";
                    bEl.innerText = patch.errors > 9 ? "9+" : String(patch.errors);
                } else {
                    bEl.style.display = "none";
                }
            }
        }
    }
    window.__vfxUpdateStatusBar = updateStatusBarTelemetry;

    const stBtnTerm = statusBar.querySelector("#st-btn-terminal");
    if (stBtnTerm) {
        stBtnTerm.onclick = () => {
            if (typeof window.__vfxSwitchStudioTab === "function") {
                window.__vfxSwitchStudioTab("terminal");
            }
        };
    }

    const stPillVram = statusBar.querySelector("#st-pill-vram");
    if (stPillVram) {
        stPillVram.style.cursor = "pointer";
        setTip(stPillVram, "Purge VRAM: click to instantly unload all models from GPU", "Очистить VRAM: кликните для мгновенной выгрузки всех моделей из GPU");
        stPillVram.onclick = async () => {
            const vramSpan = stPillVram.querySelector("span");
            const oldHtml = vramSpan ? vramSpan.innerHTML : "";
            if (vramSpan) vramSpan.innerHTML = `VRAM: <em>${uiText("Purging...", "Очистка...")}</em>`;
            try {
                const res = await fetch("/vfx/unload_models", { method: "POST" });
                const d = await res.json();
                if (vramSpan) {
                    const freeGb = d.free_gb || 0;
                    const totGb = d.total_gb || 24;
                    const usedGb = Math.max(0, (totGb - freeGb).toFixed(1));
                    vramSpan.innerHTML = `VRAM: <strong style="color: #10b981; font-weight: 600;">${usedGb}</strong>/${totGb}G`;
                }
                if (typeof window.showToastNotification === "function") {
                    window.showToastNotification(d.message || uiText("VRAM successfully purged!", "VRAM успешно освобождена!"));
                }
            } catch (err) {
                if (vramSpan) vramSpan.innerHTML = oldHtml;
            }
        };

        // Live Centered System Telemetry Graph (CPU, RAM, GPU, DISK)
        const telemHistory = {
            cpu: [0, 0, 0, 0, 0],
            ram: [0, 0, 0, 0, 0],
            gpu: [0, 0, 0, 0, 0],
            disk: [0, 0, 0, 0, 0]
        };

        function drawSparkline(canvas, history, strokeColor, maxVal = 100) {
            if (!canvas) return;
            const ctx = canvas.getContext("2d");
            if (!ctx) return;
            const w = canvas.width;
            const h = canvas.height;
            ctx.clearRect(0, 0, w, h);
            if (!history || history.length < 2) return;

            ctx.beginPath();
            for (let i = 0; i < history.length; i++) {
                const x = (i / (history.length - 1)) * (w - 2) + 1;
                const norm = Math.max(0, Math.min(1, history[i] / maxVal));
                const y = h - (norm * (h - 3)) - 1.5;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.strokeStyle = strokeColor;
            ctx.lineWidth = 1.2;
            ctx.lineJoin = "round";
            ctx.stroke();

            // Gradient fill under sparkline
            ctx.lineTo(w - 1, h);
            ctx.lineTo(1, h);
            ctx.closePath();
            ctx.fillStyle = strokeColor.includes("#") ? `${strokeColor}22` : "rgba(255,255,255,0.08)";
            ctx.fill();
        }

        async function pollSystemMetrics() {
            try {
                const res = await fetch("/vfx/system_metrics");
                if (!res.ok) return;
                const m = await res.json();

                // 1. CPU
                const cpuVal = Math.round(m.cpu_pct || 0);
                telemHistory.cpu.push(cpuVal);
                if (telemHistory.cpu.length === 1) telemHistory.cpu.unshift(cpuVal);
                if (telemHistory.cpu.length > 16) telemHistory.cpu.shift();
                const elCpuVal = statusBar.querySelector("#telem-val-cpu");
                if (elCpuVal) elCpuVal.innerText = `${cpuVal}%`;
                const cCpu = statusBar.querySelector("#telem-canvas-cpu");
                drawSparkline(cCpu, telemHistory.cpu, cpuVal > 85 ? "#f87171" : "#67e8f9", 100);
                const elCpu = statusBar.querySelector("#telem-cpu");
                if (elCpu) {
                    elCpu.title = `CPU: ${cpuVal}% (Studio App: ${m.app_cpu_pct || 0}%)\nCores: ${m.cpu_count || 16}`;
                }

                // 2. RAM
                const ramVal = Math.round(m.ram_pct || 0);
                telemHistory.ram.push(ramVal);
                if (telemHistory.ram.length === 1) telemHistory.ram.unshift(ramVal);
                if (telemHistory.ram.length > 16) telemHistory.ram.shift();
                const elRamVal = statusBar.querySelector("#telem-val-ram");
                if (elRamVal) elRamVal.innerText = `${ramVal}%`;
                const cRam = statusBar.querySelector("#telem-canvas-ram");
                drawSparkline(cRam, telemHistory.ram, ramVal > 85 ? "#f87171" : "#c084fc", 100);
                const elRam = statusBar.querySelector("#telem-ram");
                if (elRam) {
                    elRam.title = `RAM: ${m.ram_used_gb || 0} / ${m.ram_total_gb || 0} GB (${ramVal}%)\nStudio App RAM: ${m.app_ram_gb || 0} GB`;
                }

                // 3. GPU / VRAM
                const gpuVal = Math.round(m.gpu_util || m.vram_pct || 0);
                telemHistory.gpu.push(gpuVal);
                if (telemHistory.gpu.length === 1) telemHistory.gpu.unshift(gpuVal);
                if (telemHistory.gpu.length > 16) telemHistory.gpu.shift();
                const elGpuVal = statusBar.querySelector("#telem-val-gpu");
                if (elGpuVal) elGpuVal.innerText = `${gpuVal}%`;
                const cGpu = statusBar.querySelector("#telem-canvas-gpu");
                drawSparkline(cGpu, telemHistory.gpu, gpuVal > 85 ? "#f87171" : "#34d399", 100);
                const elGpu = statusBar.querySelector("#telem-gpu");
                if (elGpu) {
                    elGpu.title = `GPU: ${m.gpu_name || 'GPU'} (${gpuVal}%)\nVRAM: ${m.vram_used_gb || 0} / ${m.vram_total_gb || 24} GB (${m.vram_pct || 0}%)`;
                }

                // 4. DISK
                const ioTotal = Math.round(((m.io_read_mb_s || 0) + (m.io_write_mb_s || 0)) * 10) / 10;
                telemHistory.disk.push(ioTotal);
                if (telemHistory.disk.length === 1) telemHistory.disk.unshift(ioTotal);
                if (telemHistory.disk.length > 16) telemHistory.disk.shift();
                const elDiskVal = statusBar.querySelector("#telem-val-disk");
                if (elDiskVal) elDiskVal.innerText = `${ioTotal} MB/s`;
                const cDisk = statusBar.querySelector("#telem-canvas-disk");
                const maxDisk = Math.max(20, ...telemHistory.disk);
                drawSparkline(cDisk, telemHistory.disk, ioTotal > 50 ? "#f87171" : "#fbbf24", maxDisk);
                const elDisk = statusBar.querySelector("#telem-disk");
                if (elDisk) {
                    elDisk.title = `Disk Drive [${m.disk_drive || 'App'}]: ${m.disk_free_gb || 0} GB free (${m.disk_pct || 0}% used)\nActive I/O: Read ${m.io_read_mb_s || 0} MB/s, Write ${m.io_write_mb_s || 0} MB/s`;
                }

                // Update VRAM pill in right section as well
                const vramSpan = stPillVram.querySelector("span");
                if (vramSpan && !vramSpan.innerHTML.includes("Purging") && !vramSpan.innerHTML.includes("Очистка")) {
                    const totGb = m.vram_total_gb || 24;
                    const usedGb = m.vram_used_gb || 0;
                    const col = usedGb > 20 ? "#f87171" : (usedGb > 14 ? "#fbbf24" : "#ffffff");
                    vramSpan.innerHTML = `VRAM: <strong style="color: ${col}; font-weight: 500;">${usedGb}</strong>/${totGb}G`;
                }
            } catch (_) {}
        }

        // Poll every 2.5s for snappy live graphs without system overhead
        setTimeout(pollSystemMetrics, 400);
        setInterval(pollSystemMetrics, 2500);
    }

    const filmGateBox = container.querySelector("#film-gate-box");
    const monitorFrameBox = container.querySelector("#monitor-frame-box");
    const timelineSlider = footer.querySelector("#timeline-slider");
    const inpCurrentFrame = footer.querySelector("#inp-current-frame");
    const hudFrameLabel = footer.querySelector("#hud-frame-label");
    const keyMarkersTrack = footer.querySelector("#key-markers-track");
    const btnPlayPause = footer.querySelector("#btn-play-pause");
    let monitorNeedsRender = true;
let lastMonitorRenderTime = 0;

    // Live generation progress: header chip + popover with step bar, ETA and the sampler preview.
    (() => {
        const chip = document.getElementById("vfx-progress-chip");
        if (!chip) return;
        const fill = document.getElementById("vfx-progress-fill");
        const text = document.getElementById("vfx-progress-text");
        const pop = document.createElement("div");
        pop.id = "vfx-progress-popover";
        pop.style.cssText = "display: none; position: fixed; z-index: 99999; width: 340px; background: rgba(16,16,20,0.97); border: 1px solid #334155; border-radius: 8px; box-shadow: 0 16px 40px rgba(0,0,0,0.6); padding: 10px; font-size: 11px; color: #e4e4e7;";
        document.body.appendChild(pop);
        let open = false, lastPreviewAt = 0, lastData = null;
        const fmt = (sec) => {
            if (sec == null || !isFinite(sec)) return "";
            sec = Math.max(0, Math.round(sec));
            return sec >= 60 ? `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}` : `${sec}s`;
        };
        const esc = (t) => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
        chip.onclick = (e) => {
            e.stopPropagation();
            open = !open;
            if (open) {
                const r = chip.getBoundingClientRect();
                pop.style.top = (r.bottom + 6) + "px";
                pop.style.left = Math.max(8, Math.min(window.innerWidth - 350, r.left - 120)) + "px";
            }
            pop.style.display = open ? "block" : "none";
            render(lastData);
        };
        document.addEventListener("mousedown", (e) => {
            if (open && !pop.contains(e.target) && !chip.contains(e.target)) { open = false; pop.style.display = "none"; }
        });
        let stopRequestedAt = 0;
        let inactiveStreak = 0;
        window.__notifyVfxStopRequested = () => {
            stopRequestedAt = Date.now();
            inactiveStreak = 99;
        };
        function render(d) {
            if (!d) return;
            if (Date.now() - stopRequestedAt < 3500) {
                chip.style.display = "none";
                const stopBtn = document.getElementById("btn-stop-all-jobs");
                if (stopBtn) stopBtn.style.display = "none";
                if (open) { open = false; pop.style.display = "none"; }
                return;
            }
            let r = (d.running && d.running.status === "running") ? d.running : null;
            // Guard against completed samplers remaining stuck in snapshot
            if (r && r.value && r.max && r.value >= r.max && r.eta === 0 && r.elapsed > 15) {
                r = null;
            }
            const vj = (d.video_jobs || [])[0];
            const mj = (d.mesh_jobs || [])[0];
            const liveGenActive = Boolean(window.__activeLiveGen && window.__activeLiveGen.active);
            const activeRaw = Boolean(r || vj || mj || liveGenActive);

            if (activeRaw) {
                inactiveStreak = 0;
            } else {
                inactiveStreak++;
            }

            // If live generation explicitly ended, reset streak immediately
            if (window.__activeLiveGen && window.__activeLiveGen.active === false && !r && !vj) {
                inactiveStreak = 99;
            }

            // Debounce / latch: don't flicker off on momentary 1-tick pause between ComfyUI nodes
            const active = activeRaw || (inactiveStreak < 3 && chip.style.display === "inline-flex");

            chip.style.display = active ? "inline-flex" : "none";
            const stopBtn = document.getElementById("btn-stop-all-jobs");
            if (stopBtn) stopBtn.style.display = active ? "inline-flex" : "none";
            if (!active) { if (open) { open = false; pop.style.display = "none"; } return; }
            let pct, label;
            if (r) {
                pct = r.percent;
                const step = r.max ? ` ${r.value}/${r.max}` : "";
                label = `${Math.round(pct)}% · ${r.node_title || r.label || "…"}${step}${r.eta != null ? " · ~" + fmt(r.eta) : ""}`;
            } else if (mj) {
                pct = mj.progress || 0;
                label = `${Math.round(pct)}% · ${mj.kind} · ${mj.message || ""}`;
            } else if (vj) {
                pct = vj.progress || 0;
                label = `${Math.round(pct)}% · ${vj.kind} · ${vj.message || ""}`;
            } else if (liveGenActive) {
                pct = window.__activeLiveGen.pct || 15;
                const st = (window.__activeLiveGen.stageTxt || "").replace(/^\[\d+\/\d+\]\s*/, '');
                label = `${Math.round(pct)}% · ${st ? st.slice(0, 26) : (window.__activeLiveGen.isVideo ? "LTX-2.5 Video" : "FLUX.3 Klein")}`;
            }
            if (pct != null) {
                fill.style.width = `${Math.max(2, Math.min(100, pct))}%`;
            }
            if (label != null) {
                text.textContent = label + (d.pending ? ` (+${d.pending})` : "");
            }
            if (!open) return;
            let html = "";
            if (r) {
                html += `<div style="display:flex; justify-content:space-between; margin-bottom:6px;"><b>${esc(r.label || uiText("ComfyUI run", "Запуск ComfyUI"))}</b><span style="opacity:.6">${fmt(r.elapsed)}</span></div>`;
                html += `<div style="opacity:.8; margin-bottom:4px;">${esc(r.node_title || "…")}</div>`;
                html += `<div style="height:6px; background:rgba(255,255,255,.08); border-radius:3px; overflow:hidden; margin-bottom:3px;"><div style="height:100%; width:${r.step_percent}%; background:#22d3ee;"></div></div>`;
                html += `<div style="display:flex; justify-content:space-between; opacity:.65; margin-bottom:6px;"><span>${uiText("Step", "Шаг")} ${r.value}/${r.max || "–"}</span><span>${uiText("Nodes", "Ноды")} ${r.done_nodes}/${Math.max(1, r.total_nodes - r.cached_nodes)}</span><span>${r.eta != null ? uiText("ETA ", "Осталось ") + fmt(r.eta) : ""}</span></div>`;
                html += r.has_preview
                    ? `<img src="/vfx/process/preview/${encodeURIComponent(r.prompt_id)}?t=${lastPreviewAt}" style="width:100%; border-radius:4px; background:#000; display:block;">`
                    : `<div style="opacity:.5; font-size:10px; padding:8px 0;">${uiText("Preview appears while the sampler runs.", "Превью появится, когда пойдёт сэмплер.")}</div>`;
            }
            (d.mesh_jobs || []).forEach(j => {
                html += `<div style="margin-top:8px; border-top:1px solid #27272a; padding-top:6px;"><b>${esc(j.kind)}</b> · ${Math.round(j.progress || 0)}% <span style="opacity:.65">${esc(j.message || "")}</span></div>`;
            });
            (d.video_jobs || []).forEach(j => {
                html += `<div style="margin-top:8px; border-top:1px solid #27272a; padding-top:6px;"><b>${esc(j.kind)}</b> · ${Math.round(j.progress || 0)}% <span style="opacity:.65">${esc(j.message || "")}</span></div>`;
            });
            if (d.pending) html += `<div style="margin-top:6px; opacity:.65">${uiText("Queued", "В очереди")}: ${d.pending}</div>`;
            pop.innerHTML = html;
        }
        async function tick() {
            let delay = 3000;
            try {
                const d = await (await fetch("/vfx/process/progress")).json();
                lastData = d;
                if (d.running || (d.video_jobs || []).length || (d.mesh_jobs || []).length || (window.__activeLiveGen && window.__activeLiveGen.active)) {
                    delay = 1000;
                    if (Date.now() - lastPreviewAt > 1500) lastPreviewAt = Date.now();
                }
                render(d);
                if (typeof window.__vfxOnProcessProgress === "function") {
                    try { window.__vfxOnProcessProgress(d); } catch (_) {}
                }
            } catch (_) { delay = 5000; }
            setTimeout(tick, delay);
        }
        tick();
    })();

    // Fullscreen Toggle
    const stopAllBtn = document.getElementById("btn-stop-all-jobs");
    if (stopAllBtn) {
        stopAllBtn.onclick = async () => {
            if (typeof window.__notifyVfxStopRequested === "function") {
                window.__notifyVfxStopRequested();
            }
            stopAllBtn.disabled = true;
            stopAllBtn.style.display = "none";
            const chip = document.getElementById("vfx-progress-chip");
            if (chip) chip.style.display = "none";
            const pop = document.getElementById("vfx-progress-popover");
            if (pop) pop.style.display = "none";
            try {
                await fetch("/vfx/mesh3d/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
            } catch (_) {}
            try {
                await fetch("/interrupt", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
                await fetch("/queue", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clear: true }) }).catch(() => {});
                await fetch("/vfx/process/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ all: true }) }).catch(() => {});
                await fetch("/vfx/agent/unload_vram", { method: "POST" }).catch(() => {});
                await fetch("/vfx/unload_models", { method: "POST" }).catch(() => {});
                if (typeof window.__vfxCancelActiveGeneration === "function") {
                    window.__vfxCancelActiveGeneration();
                }
                if (typeof window.showToastNotification === "function") window.showToastNotification(uiText("Stopped: queue cleared, VRAM purged", "Остановлено: очередь очищена, VRAM освобождена"));
            } catch (_) {}
            stopAllBtn.disabled = false;
        };
    }

    // Restart server: first click arms (3 s), second click restarts, then the page waits for the server and reloads.
    (() => {
        const btn = document.getElementById("btn-restart-server");
        const lbl = document.getElementById("lbl-restart-server");
        if (!btn) return;
        let armed = false, armTimer = null;
        const setLabel = (t) => { lbl.textContent = t; lbl.style.display = t ? "inline" : "none"; };
        btn.onclick = async () => {
            if (!armed) {
                armed = true;
                setLabel(uiText("Click again to restart", "Нажмите ещё раз"));
                btn.style.borderColor = "rgba(248,113,113,0.6)";
                armTimer = setTimeout(() => { armed = false; setLabel(""); btn.style.borderColor = ""; }, 3000);
                return;
            }
            clearTimeout(armTimer);
            btn.disabled = true;
            setLabel(uiText("Restarting…", "Перезапуск…"));
            try { await fetch("/vfx/system/restart", { method: "POST" }); } catch (_) {}
            const started = Date.now();
            await new Promise(r => setTimeout(r, 2500));
            while (Date.now() - started < 180000) {
                try {
                    const r = await fetch("/vfx/system/ping", { cache: "no-store" });
                    if (r.ok) { location.reload(); return; }
                } catch (_) {}
                await new Promise(r => setTimeout(r, 1500));
            }
            btn.disabled = false;
            setLabel(uiText("Server did not come back — check the ComfyUI console", "Сервер не поднялся — проверьте консоль ComfyUI"));
        };
    })();

    window.__vfxPurgeVRAM = async (showToast = true) => {
        if (showToast && typeof window.showToastNotification === "function") {
            window.showToastNotification(uiText("⚡ Purging GPU VRAM & unloading models...", "⚡ Очистка видеопамяти и выгрузка моделей..."));
        }
        try {
            await Promise.allSettled([
                fetch("/vfx/agent/unload_vram", { method: "POST" }),
                fetch("/vfx/unload_models", { method: "POST" }),
                fetch("/free", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ unload_models: true, free_memory: true }) })
            ]);
            if (showToast && typeof window.showToastNotification === "function") {
                window.showToastNotification(uiText("⚡ GPU VRAM successfully purged and defragmented!", "⚡ Видеопамять успешно очищена и дефрагментирована!"));
            }
        } catch (e) {
            if (showToast && typeof window.showToastNotification === "function") {
                window.showToastNotification(uiText("Failed to purge VRAM: " + e, "Не удалось очистить VRAM: " + e), true);
            }
        }
    };

    const btnHdrPurge = document.getElementById("btn-hdr-purge-vram");
    if (btnHdrPurge) {
        btnHdrPurge.onclick = () => window.__vfxPurgeVRAM();
    }

    document.getElementById("btn-fullscreen-toggle").onclick = () => {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(() => {});
        } else {
            document.exitFullscreen().catch(() => {});
        }
    };

    // --- THREE.JS SCENE SETUP ---
    const width = canvasHost.clientWidth || window.innerWidth;
    const height = canvasHost.clientHeight || (window.innerHeight - 100);

    const scene = new THREE.Scene();
    let envBgColor = localStorage.getItem("vfx_3d_env_bg_color") || "#0a0a0d";
    scene.background = new THREE.Color(envBgColor);

    // Grid floor (Z=0 plane) with customizable color and visibility persistence
    let gridColorHex = localStorage.getItem("vfx_3d_grid_color") || "#4f80ff";

    function createGridHelper(hex) {
        const col = new THREE.Color(hex);
        const gh = new THREE.GridHelper(50, 50, col, col.clone().multiplyScalar(0.45));
        gh.rotation.x = Math.PI / 2;
        gh.visible = isGridVisible;
        return gh;
    }
    let gridHelper = createGridHelper(gridColorHex);
    scene.add(gridHelper);

    // 3D Ground Axis Lines (Red = X, Green = Y) with smooth radial fade towards infinity
    function createGroundAxesHelper() {
        const grp = new THREE.Group();
        grp.name = "vfx_ground_axes";

        const fadeAxisVertexShader = `
            varying vec3 vWorldPos;
            void main() {
                vWorldPos = (modelMatrix * vec4(position, 1.0)).xyz;
                gl_Position = projectionMatrix * viewMatrix * vec4(vWorldPos, 1.0);
            }
        `;

        const fadeAxisFragmentShader = `
            uniform vec3 uColor;
            uniform float uInnerRadius;
            uniform float uOuterRadius;
            varying vec3 vWorldPos;

            void main() {
                float dist = length(vWorldPos.xy);
                float alpha = smoothstep(uOuterRadius, uInnerRadius, dist);
                if (alpha <= 0.002) discard;
                gl_FragColor = vec4(uColor, alpha * 0.9);
            }
        `;

        function createFadeLineGeometry(isX) {
            const pts = [];
            const extent = 500;
            const step = 20;
            for (let t = -extent; t <= extent; t += step) {
                if (isX) {
                    pts.push(new THREE.Vector3(t, 0, 0.002));
                } else {
                    pts.push(new THREE.Vector3(0, t, 0.002));
                }
            }
            return new THREE.BufferGeometry().setFromPoints(pts);
        }

        const matX = new THREE.ShaderMaterial({
            vertexShader: fadeAxisVertexShader,
            fragmentShader: fadeAxisFragmentShader,
            uniforms: {
                uColor: { value: new THREE.Color(0xef4444) },
                uInnerRadius: { value: 25.0 },
                uOuterRadius: { value: 220.0 }
            },
            transparent: true,
            depthWrite: false
        });
        const lineX = new THREE.Line(createFadeLineGeometry(true), matX);
        lineX.frustumCulled = false;
        grp.add(lineX);

        const matY = new THREE.ShaderMaterial({
            vertexShader: fadeAxisVertexShader,
            fragmentShader: fadeAxisFragmentShader,
            uniforms: {
                uColor: { value: new THREE.Color(0x10b981) },
                uInnerRadius: { value: 25.0 },
                uOuterRadius: { value: 220.0 }
            },
            transparent: true,
            depthWrite: false
        });
        const lineY = new THREE.Line(createFadeLineGeometry(false), matY);
        lineY.frustumCulled = false;
        grp.add(lineY);

        return grp;
    }
    let groundAxesHelper = createGroundAxesHelper();
    scene.add(groundAxesHelper);

    // =========================================================================
    // 3D CURSOR (Blender-style 3D Reticle & Spawn Anchor with Scale & Thickness)
    // =========================================================================
    let cursor3DPosition = new THREE.Vector3(0, 0, 0);
    let cursor3DScale = 1.0;
    let cursor3DThickness = 1.0;
    try {
        const savedCursor = JSON.parse(localStorage.getItem("vfx_3d_cursor"));
        if (savedCursor && typeof savedCursor.x === "number") {
            cursor3DPosition.set(savedCursor.x, savedCursor.y, savedCursor.z);
        }
        if (savedCursor && typeof savedCursor.scale === "number" && !isNaN(savedCursor.scale)) {
            cursor3DScale = Math.max(0.05, Math.min(10.0, savedCursor.scale));
        }
        if (savedCursor && typeof savedCursor.thickness === "number" && !isNaN(savedCursor.thickness)) {
            cursor3DThickness = Math.max(0.1, Math.min(10.0, savedCursor.thickness));
        }
    } catch (_) {}

    let cursor3DVisible = false;
    let is3DCursorToolActive = false;

    const CURSOR_BASE_RING_RADIUS = 0.16;
    const CURSOR_BASE_CROSS_LEN = 0.24;
    const CURSOR_RING_SEGS = 64;

    function create3DCursorMesh() {
        const grp = new THREE.Group();
        grp.name = "VFX_3D_Cursor_Reticle";
        grp.renderOrder = 99999;

        // 1. Billboarded Ring Holder (children[0] rotates to face activeCamera)
        const ringHolder = new THREE.Group();
        ringHolder.renderOrder = 99999;

        // 1a. Crisp 1px core line ring
        const ringPos = [];
        const ringColors = [];
        for (let i = 0; i <= CURSOR_RING_SEGS; i++) {
            const theta = (i / CURSOR_RING_SEGS) * Math.PI * 2;
            ringPos.push(Math.cos(theta) * CURSOR_BASE_RING_RADIUS, Math.sin(theta) * CURSOR_BASE_RING_RADIUS, 0);
            const isRed = Math.floor(i / (CURSOR_RING_SEGS / 8)) % 2 === 0;
            if (isRed) {
                ringColors.push(0.96, 0.22, 0.22);
            } else {
                ringColors.push(1.0, 1.0, 1.0);
            }
        }
        const ringGeom = new THREE.BufferGeometry();
        ringGeom.setAttribute("position", new THREE.Float32BufferAttribute(ringPos, 3));
        ringGeom.setAttribute("color", new THREE.Float32BufferAttribute(ringColors, 3));
        const ringLine = new THREE.Line(ringGeom, new THREE.LineBasicMaterial({
            vertexColors: true,
            depthTest: false,
            depthWrite: false,
            transparent: true
        }));
        ringLine.renderOrder = 99999;
        ringHolder.add(ringLine);

        // 1b. Variable-thickness segmented ribbon mesh for the ring (works on Windows WebGL/ANGLE)
        const bandPositions = new Float32Array(CURSOR_RING_SEGS * 6 * 3);
        const bandColors = new Float32Array(CURSOR_RING_SEGS * 6 * 3);
        for (let i = 0; i < CURSOR_RING_SEGS; i++) {
            const isRed = Math.floor(i / (CURSOR_RING_SEGS / 8)) % 2 === 0;
            const r = isRed ? 0.96 : 1.0;
            const g = isRed ? 0.22 : 1.0;
            const b = isRed ? 0.22 : 1.0;
            for (let v = 0; v < 6; v++) {
                const idx = (i * 6 + v) * 3;
                bandColors[idx] = r;
                bandColors[idx + 1] = g;
                bandColors[idx + 2] = b;
            }
        }
        const bandGeom = new THREE.BufferGeometry();
        bandGeom.setAttribute("position", new THREE.BufferAttribute(bandPositions, 3));
        bandGeom.setAttribute("color", new THREE.BufferAttribute(bandColors, 3));
        const bandMesh = new THREE.Mesh(bandGeom, new THREE.MeshBasicMaterial({
            vertexColors: true,
            side: THREE.DoubleSide,
            depthTest: false,
            depthWrite: false,
            transparent: true,
            opacity: 0.95
        }));
        bandMesh.renderOrder = 99999;
        ringHolder.add(bandMesh);
        grp.add(ringHolder);

        // 2. 3D Axis Crosshair Ticks (Red X, Green Y, Blue Z)
        const crossHolder = new THREE.Group();
        crossHolder.renderOrder = 99999;

        const crossGeom = new THREE.BufferGeometry();
        const crossPositions = [
            -CURSOR_BASE_CROSS_LEN, 0, 0,  CURSOR_BASE_CROSS_LEN, 0, 0,
            0, -CURSOR_BASE_CROSS_LEN, 0,  0, CURSOR_BASE_CROSS_LEN, 0,
            0, 0, -CURSOR_BASE_CROSS_LEN,  0, 0, CURSOR_BASE_CROSS_LEN
        ];
        const crossColors = [
            1.0, 0.25, 0.25,  1.0, 0.25, 0.25,
            0.15, 0.85, 0.25,  0.15, 0.85, 0.25,
            0.2, 0.65, 1.0,   0.2, 0.65, 1.0
        ];
        crossGeom.setAttribute("position", new THREE.Float32BufferAttribute(crossPositions, 3));
        crossGeom.setAttribute("color", new THREE.Float32BufferAttribute(crossColors, 3));
        const crossLines = new THREE.LineSegments(crossGeom, new THREE.LineBasicMaterial({
            vertexColors: true,
            depthTest: false,
            depthWrite: false,
            transparent: true
        }));
        crossLines.renderOrder = 99999;
        crossHolder.add(crossLines);

        const unitBox = new THREE.BoxGeometry(1, 1, 1);
        const makeBar = (colHex) => {
            const m = new THREE.Mesh(unitBox, new THREE.MeshBasicMaterial({
                color: colHex,
                depthTest: false,
                depthWrite: false,
                transparent: true,
                opacity: 0.92
            }));
            m.renderOrder = 99999;
            return m;
        };
        const xBar = makeBar(0xff4040);
        const yBar = makeBar(0x26d940);
        const zBar = makeBar(0x33a6ff);
        crossHolder.add(xBar, yBar, zBar);
        grp.add(crossHolder);

        // 3. Center Target Dot
        const dotGeom = new THREE.SphereGeometry(1, 10, 10);
        const dotMat = new THREE.MeshBasicMaterial({
            color: 0xffffff,
            depthTest: false,
            depthWrite: false,
            transparent: true
        });
        const dotMesh = new THREE.Mesh(dotGeom, dotMat);
        dotMesh.renderOrder = 99999;
        grp.add(dotMesh);

        grp.userData.__cursorParts = { bandGeom, xBar, yBar, zBar, dotMesh };
        grp.position.copy(cursor3DPosition);
        grp.visible = cursor3DVisible;
        return grp;
    }

    const cursor3DGroup = create3DCursorMesh();
    scene.add(cursor3DGroup);

    function apply3DCursorStyle() {
        if (!cursor3DGroup) return;
        cursor3DGroup.scale.setScalar(Math.max(0.05, cursor3DScale));
        const parts = cursor3DGroup.userData.__cursorParts;
        if (!parts) return;

        const halfBand = Math.max(0.0004, 0.0042 * cursor3DThickness);
        const rIn = Math.max(0.004, CURSOR_BASE_RING_RADIUS - halfBand);
        const rOut = CURSOR_BASE_RING_RADIUS + halfBand;
        const posAttr = parts.bandGeom.getAttribute("position");
        const arr = posAttr.array;
        for (let i = 0; i < CURSOR_RING_SEGS; i++) {
            const t0 = (i / CURSOR_RING_SEGS) * Math.PI * 2;
            const t1 = ((i + 1) / CURSOR_RING_SEGS) * Math.PI * 2;
            const c0 = Math.cos(t0), s0 = Math.sin(t0);
            const c1 = Math.cos(t1), s1 = Math.sin(t1);
            const base = i * 18;
            // Tri 1
            arr[base]     = c0 * rIn;  arr[base + 1] = s0 * rIn;  arr[base + 2] = 0;
            arr[base + 3] = c0 * rOut; arr[base + 4] = s0 * rOut; arr[base + 5] = 0;
            arr[base + 6] = c1 * rOut; arr[base + 7] = s1 * rOut; arr[base + 8] = 0;
            // Tri 2
            arr[base + 9]  = c0 * rIn;  arr[base + 10] = s0 * rIn;  arr[base + 11] = 0;
            arr[base + 12] = c1 * rOut; arr[base + 13] = s1 * rOut; arr[base + 14] = 0;
            arr[base + 15] = c1 * rIn;  arr[base + 16] = s1 * rIn;  arr[base + 17] = 0;
        }
        posAttr.needsUpdate = true;

        const barThick = Math.max(0.0006, 0.005 * cursor3DThickness);
        const fullCross = CURSOR_BASE_CROSS_LEN * 2;
        parts.xBar.scale.set(fullCross, barThick, barThick);
        parts.yBar.scale.set(barThick, fullCross, barThick);
        parts.zBar.scale.set(barThick, barThick, fullCross);

        const dotR = Math.max(0.003, 0.011 * (0.55 + 0.45 * cursor3DThickness));
        parts.dotMesh.scale.setScalar(dotR);
    }

    apply3DCursorStyle();

    function update3DCursorMesh() {
        if (!cursor3DGroup) return;
        cursor3DGroup.position.copy(cursor3DPosition);
        cursor3DGroup.visible = cursor3DVisible;
        apply3DCursorStyle();
        try {
            localStorage.setItem("vfx_3d_cursor", JSON.stringify({
                x: Number(cursor3DPosition.x.toFixed(4)),
                y: Number(cursor3DPosition.y.toFixed(4)),
                z: Number(cursor3DPosition.z.toFixed(4)),
                scale: Number(cursor3DScale.toFixed(3)),
                thickness: Number(cursor3DThickness.toFixed(3))
            }));
        } catch (_) {}
        if (typeof window.__vfxOn3DCursorChanged === "function") {
            window.__vfxOn3DCursorChanged(cursor3DPosition, cursor3DScale, cursor3DThickness);
        }
        notifyDetailsAndOutliner();
        monitorNeedsRender = true;
        renderStudioViewports();
    }

    function set3DCursorPosition(x, y, z) {
        cursor3DPosition.set(Number(x) || 0, Number(y) || 0, Number(z) || 0);
        cursor3DVisible = true;
        if (cursor3DGroup) cursor3DGroup.visible = true;
        if (typeof update3DCursorUI === "function") update3DCursorUI();
        update3DCursorMesh();
    }

    function set3DCursorScale(s) {
        const val = parseFloat(s);
        if (!isNaN(val)) {
            cursor3DScale = Math.max(0.05, Math.min(10.0, val));
            update3DCursorMesh();
        }
    }

    function set3DCursorThickness(t) {
        const val = parseFloat(t);
        if (!isNaN(val)) {
            cursor3DThickness = Math.max(0.1, Math.min(10.0, val));
            update3DCursorMesh();
        }
    }

    function snapCursorToSelected() {
        if (!selectedObject) {
            logMsg("Cannot snap 3D cursor: no object selected.");
            return false;
        }
        cursor3DPosition.copy(selectedObject.position);
        update3DCursorMesh();
        logMsg(`3D Cursor snapped to ${selectedObject.userData?.name || 'Selected'} [${cursor3DPosition.x.toFixed(2)}, ${cursor3DPosition.y.toFixed(2)}, ${cursor3DPosition.z.toFixed(2)}]`);
        return true;
    }

    function snapSelectedToCursor() {
        if (!selectedObject) {
            logMsg("Cannot snap object: no object selected.");
            return false;
        }
        pushUndoState("Snap Selected to 3D Cursor");
        selectedObject.position.copy(cursor3DPosition);
        selectedObject.updateMatrixWorld(true);
        if (selectedObject === shotCamera) {
            syncShotCameraVisuals(true);
        } else if (selectedObject.userData?.camera) {
            selectedObject.userData.camera.updateMatrixWorld(true);
        }
        updateTransformInspector(false);
        notifyDetailsAndOutliner();
        monitorNeedsRender = true;
        renderStudioViewports();
        logMsg(`${selectedObject.userData?.name || 'Selected'} snapped to 3D Cursor [${cursor3DPosition.x.toFixed(2)}, ${cursor3DPosition.y.toFixed(2)}, ${cursor3DPosition.z.toFixed(2)}]`);
        return true;
    }

    function snapCursorToOrigin() {
        cursor3DPosition.set(0, 0, 0);
        update3DCursorMesh();
        logMsg("3D Cursor reset to world origin (0, 0, 0)");
        return true;
    }

    window.__vfx3DCursor = {
        get: () => cursor3DPosition.clone(),
        set: set3DCursorPosition,
        getScale: () => cursor3DScale,
        setScale: set3DCursorScale,
        getThickness: () => cursor3DThickness,
        setThickness: set3DCursorThickness,
        snapCursorToSelected,
        snapSelectedToCursor,
        snapCursorToOrigin,
        toggleVisible: (v) => {
            cursor3DVisible = v !== undefined ? !!v : !cursor3DVisible;
            update3DCursorMesh();
            return cursor3DVisible;
        }
    };

    // Lighting (Balanced Studio PBR Base + Hemisphere Fill so metallic/rough GLB textures are never black)
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.35);
    scene.add(ambientLight);

    const hemiLight = new THREE.HemisphereLight(0xffffff, 0x2a3042, 0.55);
    hemiLight.position.set(0, 0, 15);
    scene.add(hemiLight);

    const keyLight = new THREE.DirectionalLight(0xffffff, 1.1);
    keyLight.position.set(5, -7, 9);
    keyLight.castShadow = true;
    if (keyLight.shadow) {
        keyLight.shadow.mapSize.width = 1024;
        keyLight.shadow.mapSize.height = 1024;
        keyLight.shadow.bias = -0.0005;
        keyLight.shadow.radius = 2.5;
    }
    scene.add(keyLight);

    const fillLight = new THREE.DirectionalLight(0x9ab0d6, 0.35);
    fillLight.position.set(-8, 6, 5);
    scene.add(fillLight);

    // Default studio rig auto-dims when the user adds own Sun / Point / Area lights,
    // so user lights are not fighting a constant baseline (prevents floor blow-out).
    const DEFAULT_RIG_BASE = { amb: 0.35, hemi: 0.55, key: 1.1, fill: 0.35 };
    let studioFillLevel = 1.0;   // user multiplier for default rig (Environment & Stage)
    let viewportExposure = 1.0;  // tone-mapping exposure
    try {
        const savedLook = JSON.parse(localStorage.getItem("vfx_viewport_look") || "{}");
        if (typeof savedLook.fill === "number") studioFillLevel = Math.max(0, Math.min(2, savedLook.fill));
        if (typeof savedLook.exposure === "number") viewportExposure = Math.max(0.1, Math.min(3, savedLook.exposure));
    } catch (_) {}
    let lastUserLightCount = -1;
    let lastRigFill = -1;
    function updateDefaultRigForUserLights(force = false) {
        let n = 0;
        try { threeObjectsMap.forEach(o => { if (o && o.userData && o.userData.type === "light" && o.visible !== false) n++; }); } catch (_) {}
        if (!force && n === lastUserLightCount && lastRigFill === studioFillLevel) return;
        lastUserLightCount = n;
        lastRigFill = studioFillLevel;
        const k = (n > 0 ? 0.22 : 1.0) * studioFillLevel;
        ambientLight.intensity = DEFAULT_RIG_BASE.amb * k;
        hemiLight.intensity = DEFAULT_RIG_BASE.hemi * k;
        keyLight.intensity = n > 0 ? 0 : DEFAULT_RIG_BASE.key * studioFillLevel;
        keyLight.castShadow = n === 0;
        fillLight.intensity = DEFAULT_RIG_BASE.fill * k;
    }
    window.__vfxViewportLook = {
        setExposure: (v) => { viewportExposure = Math.max(0.1, Math.min(3, Number(v) || 1)); applyViewportLook(); },
        setStudioFill: (v) => { studioFillLevel = Math.max(0, Math.min(2, Number(v))); applyViewportLook(); },
        get: () => ({ exposure: viewportExposure, fill: studioFillLevel })
    };
    function applyViewportLook() {
        try { localStorage.setItem("vfx_viewport_look", JSON.stringify({ exposure: viewportExposure, fill: studioFillLevel })); } catch (_) {}
        if (typeof renderer !== "undefined" && renderer) renderer.toneMappingExposure = viewportExposure;
        if (typeof monitorRenderer !== "undefined" && monitorRenderer) monitorRenderer.toneMappingExposure = viewportExposure;
        updateDefaultRigForUserLights(true);
        try { monitorNeedsRender = true; renderStudioViewports(); } catch (_) {}
    }

    // Cameras
    const flyCamera = new THREE.PerspectiveCamera(50, width / height, 0.1, 1000);
    flyCamera.position.set(0, -6, 3);
    flyCamera.up.set(0, 0, 1);
    flyCamera.lookAt(0, 0, 0);

    const shotCamera = new THREE.PerspectiveCamera(50, plateAspect, 0.1, 1000);
    shotCamera.position.set(0, -5, 2);
    shotCamera.up.set(0, 0, 1);
    {
        const initMat = new THREE.Matrix4().lookAt(shotCamera.position, new THREE.Vector3(0, 0, 0.5), new THREE.Vector3(0, 0, 1));
        shotCamera.quaternion.setFromRotationMatrix(initMat);
    }
    scene.add(shotCamera);

    const camFrustumHelper = new THREE.CameraHelper(shotCamera);
    camFrustumHelper.visible = false;
    scene.add(camFrustumHelper);

    // Symbolic wireframe 3D Camera locator for Perspective view (zero solid mesh, view rays + symbol only)
    function createCameraSymbolLocator(isShotCam = false) {
        const camGroup = new THREE.Group();
        camGroup.userData.isCameraProxy = true;
        if (isShotCam) camGroup.userData.isShotCamProxy = true;

        // Wireframe symbolic camera outline + UP indicator triangle (-Z is view direction, +Z is UP)
        const pts = [
            // Symbolic body frame (compact wireframe rectangle)
            -0.14, -0.09,  0.14,   0.14, -0.09,  0.14,
             0.14, -0.09,  0.14,   0.14,  0.09,  0.14,
             0.14,  0.09,  0.14,  -0.14,  0.09,  0.14,
            -0.14,  0.09,  0.14,  -0.14, -0.09,  0.14,

            -0.14, -0.09, -0.06,   0.14, -0.09, -0.06,
             0.14, -0.09, -0.06,   0.14,  0.09, -0.06,
             0.14,  0.09, -0.06,  -0.14,  0.09, -0.06,
            -0.14,  0.09, -0.06,  -0.14, -0.09, -0.06,

            -0.14, -0.09,  0.14,  -0.14, -0.09, -0.06,
             0.14, -0.09,  0.14,   0.14, -0.09, -0.06,
             0.14,  0.09,  0.14,   0.14,  0.09, -0.06,
            -0.14,  0.09,  0.14,  -0.14,  0.09, -0.06,

            // Front lens cone wireframe (-Z direction)
            -0.08, -0.06, -0.06,  -0.13, -0.10, -0.22,
             0.08, -0.06, -0.06,   0.13, -0.10, -0.22,
             0.08,  0.06, -0.06,   0.13,  0.10, -0.22,
            -0.08,  0.06, -0.06,  -0.13,  0.10, -0.22,

            -0.13, -0.10, -0.22,   0.13, -0.10, -0.22,
             0.13, -0.10, -0.22,   0.13,  0.10, -0.22,
             0.13,  0.10, -0.22,  -0.13,  0.10, -0.22,
            -0.13,  0.10, -0.22,  -0.13, -0.10, -0.22,

            // Top "UP" direction triangle symbol (+Y in camera local space)
            -0.09,  0.12, -0.06,   0.09,  0.12, -0.06,
             0.09,  0.12, -0.06,   0.00,  0.22, -0.14,
             0.00,  0.22, -0.14,  -0.09,  0.12, -0.06,

            // Central sight ray (-Z optical axis)
             0.00,  0.00,  0.00,   0.00,  0.00, -0.42
        ];

        const lineGeom = new THREE.BufferGeometry();
        lineGeom.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
        const lineMat = new THREE.LineBasicMaterial({ color: 0x00f0ff, transparent: true, opacity: 0.95 });
        const wireSymbol = new THREE.LineSegments(lineGeom, lineMat);
        wireSymbol.name = "cam_wire_symbol";
        wireSymbol.userData.isCameraProxy = true;
        if (isShotCam) wireSymbol.userData.isShotCamProxy = true;
        camGroup.add(wireSymbol);

        // 100% invisible raycast hit-sphere so clicking the wireframe symbol in 3D viewport selects the camera
        const hitGeom = new THREE.SphereGeometry(0.24, 8, 8);
        const hitMat = new THREE.MeshBasicMaterial({
            color: 0x00f0ff,
            transparent: true,
            opacity: 0.0,
            depthWrite: false
        });
        const hitProxy = new THREE.Mesh(hitGeom, hitMat);
        hitProxy.userData.isCameraProxy = true;
        if (isShotCam) hitProxy.userData.isShotCamProxy = true;
        camGroup.add(hitProxy);

        return camGroup;
    }

    const camGroup = createCameraSymbolLocator(true);
    camGroup.position.copy(shotCamera.position);
    camGroup.quaternion.copy(shotCamera.quaternion);
    camGroup.visible = true;
    scene.add(camGroup);
    const camBodyMesh = camGroup;

    shotCamera.userData = {
        id: "cam_shot",
        name: "Shot Camera",
        type: "camera",
        color: "#00f0ff",
        camera: shotCamera,
        cameraHelper: camFrustumHelper,
        keyframes: [],
        focalMm: 35,
        sensorPreset: "full_frame",
        sensorWidthMm: 36.0,
        sensorHeightMm: 24.0,
        imageWidth: 1920,
        imageHeight: 1080,
        apertureFStop: 2.8,
        focusDistanceM: 3.5,
        shutterAngleDeg: 180,
        anamorphicSqueeze: 1.0,
        distortionMode: "off",
        distortionK1: 0.0,
        distortionK2: 0.0,
        frustumRayLength: 3.5,
        frustumRayOpacity: 0.75
    };

    let isShotCameraVisible = true;
    let lastActiveFreeCamGroup = null;
    let activeCamera = flyCamera;

    function updateCameraFrustumHelper(camActor) {
        if (!camActor || !camActor.userData) return;
        const ud = camActor.userData;
        const cam = camActor === shotCamera ? shotCamera : ud.camera;
        const helper = camActor === shotCamera ? camFrustumHelper : ud.cameraHelper;
        if (!cam || !helper) return;

        const rayLen = Math.max(0.4, Math.min(60.0, Number(ud.frustumRayLength) || 3.5));
        const rayOpacity = Math.max(0.0, Math.min(1.0, ud.frustumRayOpacity !== undefined ? Number(ud.frustumRayOpacity) : 0.75));

        // Temporarily set cam.far to rayLen so THREE.CameraHelper builds rays of the exact requested length,
        // then restore cam.far = 1000 so Shot Camera Monitor still renders far geometry!
        const savedFar = cam.far;
        cam.far = rayLen;
        cam.updateProjectionMatrix();
        helper.update();
        cam.far = Math.max(500, savedFar || 1000);
        cam.updateProjectionMatrix();

        if (helper.material) {
            helper.material.transparent = true;
            helper.material.opacity = rayOpacity;
            helper.material.depthWrite = false;
            helper.material.needsUpdate = true;
        }
        helper.visible = (camActor.visible !== false) && rayOpacity > 0.01 && (camActor !== shotCamera || (!isShotCameraDeleted && isShotCameraVisible && activeCamera !== shotCamera));

        // Also update the central sight line length on the camera symbol if present
        const proxyRoot = camActor === shotCamera ? camBodyMesh : camActor;
        const wireSym = proxyRoot ? proxyRoot.getObjectByName("cam_wire_symbol") : null;
        if (wireSym && wireSym.geometry) {
            const posAttr = wireSym.geometry.getAttribute("position");
            if (posAttr && posAttr.count >= 2) {
                posAttr.setZ(posAttr.count - 1, -Math.min(rayLen, Math.max(0.42, rayLen * 0.45)));
                posAttr.needsUpdate = true;
            }
        }
        monitorNeedsRender = true;
    }

    function syncShotCameraVisuals(updateTrajectoryFrame = false) {
        shotCamera.matrixAutoUpdate = true;
        shotCamera.updateMatrix();
        shotCamera.updateMatrixWorld(true);
        if (camBodyMesh) {
            camBodyMesh.position.copy(shotCamera.position);
            camBodyMesh.quaternion.copy(shotCamera.quaternion);
            camBodyMesh.updateMatrixWorld(true);
        }
        if (updateTrajectoryFrame) {
            monitorNeedsRender = true;
        }
        if (updateTrajectoryFrame && Array.isArray(cameraMatrices) && cameraMatrices.length > 0) {
            const f = Math.max(0, Math.min(cameraMatrices.length - 1, currentFrame));
            const e = shotCamera.matrixWorld.elements;
            cameraMatrices[f] = [
                [e[0], e[4], e[8], shotCamera.position.x],
                [e[1], e[5], e[9], shotCamera.position.y],
                [e[2], e[6], e[10], shotCamera.position.z],
                [0, 0, 0, 1]
            ];
        }
    }

    // Camera trajectory & Scene Data (with Cubic Bezier user guide handles, Linear & Step)
    let cameraMatrices = [];
    const textureLoader = new THREE.TextureLoader();
    let cameraPathLine = null;

    function evalCubicBezier1D(t, p0, p1, p2, p3) {
        const mt = 1.0 - t;
        return mt * mt * mt * p0 + 3.0 * mt * mt * t * p1 + 3.0 * mt * t * t * p2 + t * t * t * p3;
    }

    function evalCubicBezierEasing(u, x1 = 0.333, y1 = 0.0, x2 = 0.667, y2 = 1.0) {
        if (u <= 0) return 0;
        if (u >= 1) return 1;
        const cx1 = Math.max(0.0, Math.min(1.0, Number(x1) || 0.333));
        const cx2 = Math.max(0.0, Math.min(1.0, Number(x2) || 0.667));
        // Solve x(t) = u via Newton-Raphson / binary search (6 iterations is <1e-5 error)
        let lo = 0.0, hi = 1.0, t = u;
        for (let iter = 0; iter < 8; iter++) {
            const xEst = evalCubicBezier1D(t, 0.0, cx1, cx2, 1.0);
            const err = xEst - u;
            if (Math.abs(err) < 1e-4) break;
            if (err < 0) lo = t;
            else hi = t;
            t = 0.5 * (lo + hi);
        }
        return evalCubicBezier1D(t, 0.0, Number(y1) || 0.0, Number(y2) ?? 1.0, 1.0);
    }

    function computeSegment3DBezierControls(sortedKeys, i) {
        const k0 = sortedKeys[i];
        const k1 = sortedKeys[i + 1];
        const p0 = k0.position;
        const p1 = k1.position;
        const prevP = i > 0 ? sortedKeys[i - 1].position : p0;
        const nextP = (i + 2 < sortedKeys.length) ? sortedKeys[i + 2].position : p1;

        const segDist = Math.max(0.001, p0.distanceTo(p1));
        // Default unstretched Catmull-Rom / auto tangent vectors (1/3 of chord)
        const defDir0 = new THREE.Vector3().subVectors(p1, prevP);
        if (defDir0.lengthSq() > 1e-6) defDir0.normalize();
        const defDir1 = new THREE.Vector3().subVectors(nextP, p0);
        if (defDir1.lengthSq() > 1e-6) defDir1.normalize();

        const outScale = k0.tangentOutScale !== undefined ? Number(k0.tangentOutScale) : 1.0;
        const inScale = k1.tangentInScale !== undefined ? Number(k1.tangentInScale) : 1.0;

        const tanOut = defDir0.multiplyScalar((segDist / 3.0) * outScale);
        const tanIn = defDir1.multiplyScalar(-(segDist / 3.0) * inScale);

        if (Array.isArray(k0.tangentOutOffset) && k0.tangentOutOffset.length === 3) {
            tanOut.x += Number(k0.tangentOutOffset[0]) || 0;
            tanOut.y += Number(k0.tangentOutOffset[1]) || 0;
            tanOut.z += Number(k0.tangentOutOffset[2]) || 0;
        }
        if (Array.isArray(k1.tangentInOffset) && k1.tangentInOffset.length === 3) {
            tanIn.x += Number(k1.tangentInOffset[0]) || 0;
            tanIn.y += Number(k1.tangentInOffset[1]) || 0;
            tanIn.z += Number(k1.tangentInOffset[2]) || 0;
        }

        const c0 = p0.clone().add(tanOut);
        const c1 = p1.clone().add(tanIn);
        return { p0, c0, c1, p1 };
    }

    function eval3DCubicBezierPoint(p0, c0, c1, p1, t, target = new THREE.Vector3()) {
        const mt = 1.0 - t;
        const w0 = mt * mt * mt;
        const w1 = 3.0 * mt * mt * t;
        const w2 = 3.0 * mt * t * t;
        const w3 = t * t * t;
        target.set(
            w0 * p0.x + w1 * c0.x + w2 * c1.x + w3 * p1.x,
            w0 * p0.y + w1 * c0.y + w2 * c1.y + w3 * p1.y,
            w0 * p0.z + w1 * c0.z + w2 * c1.z + w3 * p1.z
        );
        return target;
    }

    let active3DBezierHandles = [];

    function buildCameraTrajectoryPath() {
        if (cameraPathLine) {
            scene.remove(cameraPathLine);
            cameraPathLine.geometry.dispose();
            cameraPathLine = null;
        }
        active3DBezierHandles = [];
        let pathPts = [];
        let keyPts = [];
        let handleLinesPts = [];
        let handleTipPts = [];

        if (cameraMatrices && cameraMatrices.length >= 2) {
            for (let i = 0; i < cameraMatrices.length; i++) {
                const raw = cameraMatrices[i];
                pathPts.push(new THREE.Vector3(raw[0][3], raw[1][3], raw[2][3]));
            }
            keyPts = pathPts;
        } else {
            const camActor = (selectedObject && Array.isArray(selectedObject.userData?.keyframes) && selectedObject.userData.keyframes.length >= 2)
                ? selectedObject
                : getActiveMonitorCameraActor();
            const keys = (camActor && camActor.userData && Array.isArray(camActor.userData.keyframes)) ? camActor.userData.keyframes : [];
            if (keys.length >= 2) {
                const sorted = [...keys].sort((a, b) => a.frame - b.frame);
                keyPts = sorted.map(k => k.position.clone());
                for (let i = 0; i < sorted.length - 1; i++) {
                    const k0 = sorted[i];
                    const k1 = sorted[i + 1];
                    const segInterp = k0.interp || currentInterp || "bezier";
                    const steps = Math.max(4, Math.min(60, k1.frame - k0.frame));
                    const { p0, c0, c1, p1 } = computeSegment3DBezierControls(sorted, i);

                    if (segInterp === "bezier") {
                        handleLinesPts.push(p0.clone(), c0.clone(), p1.clone(), c1.clone());
                        handleTipPts.push(c0.clone(), c1.clone());
                        active3DBezierHandles.push(
                            { actor: camActor, keyObj: k0, role: "out", anchorPos: p0.clone(), tipPos: c0.clone(), segIdx: i },
                            { actor: camActor, keyObj: k1, role: "in", anchorPos: p1.clone(), tipPos: c1.clone(), segIdx: i }
                        );
                    }

                    for (let s = 0; s < steps; s++) {
                        const u = s / steps;
                        if (segInterp === "step") {
                            pathPts.push(p0.clone());
                        } else if (segInterp === "linear") {
                            pathPts.push(new THREE.Vector3().lerpVectors(p0, p1, u));
                        } else {
                            const hOut = k0.handleOut || { dx: 0.333, dy: 0.333 };
                            const hIn = k1.handleIn || { dx: -0.333, dy: -0.333 };
                            const isExplicitStop = (k0.bezierMode === "ease_stop" || k1.bezierMode === "ease_stop");
                            const isFlatLegacy = !isExplicitStop && (Math.abs(Number(hOut.dy) || 0) < 1e-4 && Math.abs(Number(hIn.dy) || 0) < 1e-4);
                            const isContDiag = (
                                Math.abs((Number(hOut.dy) || 0) - (Number(hOut.dx) || 0.333)) < 0.03 &&
                                Math.abs((Number(hIn.dy) || 0) - (Number(hIn.dx) || -0.333)) < 0.03
                            );
                            const tSample = (!isExplicitStop && (isFlatLegacy || isContDiag)) ? u : evalCubicBezierEasing(u, hOut.dx, hOut.dy, 1.0 + hIn.dx, 1.0 + hIn.dy);
                            pathPts.push(eval3DCubicBezierPoint(p0, c0, c1, p1, tSample));
                        }
                    }
                }
                pathPts.push(sorted[sorted.length - 1].position.clone());
            }
        }

        if (pathPts.length < 2) return;

        const pathGeom = new THREE.BufferGeometry().setFromPoints(pathPts);
        const pathMat = new THREE.LineBasicMaterial({ color: 0x00f0ff, linewidth: 2 });
        cameraPathLine = new THREE.Line(pathGeom, pathMat);
        cameraPathLine.userData.isCameraProxy = true;

        const keyPtsGeom = new THREE.BufferGeometry().setFromPoints(keyPts);
        const keyPtsMat = new THREE.PointsMaterial({ color: 0xffaa00, size: 7.5, sizeAttenuation: false });
        const keyPtsMesh = new THREE.Points(keyPtsGeom, keyPtsMat);
        keyPtsMesh.userData.isCameraProxy = true;
        cameraPathLine.add(keyPtsMesh);

        if (handleLinesPts.length >= 2) {
            const hGeom = new THREE.BufferGeometry().setFromPoints(handleLinesPts);
            const hMat = new THREE.LineBasicMaterial({ color: 0xf59e0b, transparent: true, opacity: 0.85 });
            const hLines = new THREE.LineSegments(hGeom, hMat);
            hLines.userData.isCameraProxy = true;
            cameraPathLine.add(hLines);

            const hTipGeom = new THREE.BufferGeometry().setFromPoints(handleTipPts);
            const hTipMat = new THREE.PointsMaterial({ color: 0xfde047, size: 6.0, sizeAttenuation: false });
            const hTips = new THREE.Points(hTipGeom, hTipMat);
            hTips.userData.isCameraProxy = true;
            cameraPathLine.add(hTips);
        }

        scene.add(cameraPathLine);
        monitorNeedsRender = true;
    }

    // Actor Cutout Billboard (Manual Addition Only - No Cache Fallback)
    let isActorProjectionVisible = false;
    let actorDistance = 3.0;
    let actorOpacity = 0.85;
    let customActorCutoutUrl = null;
    let customActorCutoutName = "";
    let customActorCutoutAspect = null;
    let customActorCutoutTex = null;
    let customActorCutoutVideo = null;
    let customActorCutoutCanvas = null;
    let customActorCutoutMediaType = "none"; // "image" | "video" | "none"
    let pendingActorSeekTime = null;
    let isBakingLayout = false;

    const actorMaterial = new THREE.MeshBasicMaterial({
        transparent: true,
        opacity: actorOpacity,
        depthWrite: false,
        side: THREE.DoubleSide
    });
    const actorPlaneGeom = new THREE.PlaneGeometry(1, 1);
    const actorBillboard = new THREE.Mesh(actorPlaneGeom, actorMaterial);
    actorBillboard.visible = false;
    actorBillboard.renderOrder = 999;
    scene.add(actorBillboard);

    function isVideoMedia(url = "", name = "") {
        const s = (name + " " + url).toLowerCase();
        return /\.(mp4|webm|mov|mkv|m4v|ogv)(\?|$)/i.test(s) || url.startsWith("data:video/") || url.startsWith("blob:");
    }

    function syncActorVideoCanvas() {
        if (!customActorCutoutVideo || !customActorCutoutCanvas || !customActorCutoutTex) return false;
        const vid = customActorCutoutVideo;
        if (vid.readyState < 2 || !vid.videoWidth || !vid.videoHeight) return false;
        if (customActorCutoutCanvas.width !== vid.videoWidth || customActorCutoutCanvas.height !== vid.videoHeight) {
            customActorCutoutCanvas.width = vid.videoWidth;
            customActorCutoutCanvas.height = vid.videoHeight;
        }
        const ctx = customActorCutoutCanvas.getContext("2d");
        if (!ctx) return false;
        ctx.clearRect(0, 0, customActorCutoutCanvas.width, customActorCutoutCanvas.height);
        ctx.drawImage(vid, 0, 0, customActorCutoutCanvas.width, customActorCutoutCanvas.height);
        customActorCutoutTex.needsUpdate = true;
        actorMaterial.needsUpdate = true;
        return true;
    }

    function setCustomActorCutout(url, name = "", forceType = null) {
        if (!url) return;

        // Clean up previous video if any
        if (customActorCutoutVideo) {
            try {
                customActorCutoutVideo.pause();
                customActorCutoutVideo.removeAttribute("src");
                customActorCutoutVideo.load();
            } catch (_) {}
            customActorCutoutVideo = null;
        }
        if (customActorCutoutTex) {
            try { customActorCutoutTex.dispose(); } catch (_) {}
            customActorCutoutTex = null;
        }
        customActorCutoutCanvas = null;
        pendingActorSeekTime = null;

        const isVid = forceType ? (forceType === "video") : isVideoMedia(url, name);
        customActorCutoutMediaType = isVid ? "video" : "image";
        customActorCutoutUrl = url;
        customActorCutoutName = name || (isVid ? "custom_video.mp4" : "actor_cutout.png");

        isActorProjectionVisible = true;
        actorBillboard.visible = true;
        const btnToggleActor = document.getElementById("btn-toggle-actor");
        if (btnToggleActor) {
            btnToggleActor.className = "vfx-btn vfx-btn-active";
            btnToggleActor.innerText = `👤 Actor: ON`;
        }

        if (isVid) {
            const vid = document.createElement("video");
            vid.crossOrigin = "anonymous";
            vid.playsInline = true;
            vid.muted = true;
            vid.loop = true;
            vid.autoplay = false;
            vid.preload = "auto";
            vid.src = url;

            customActorCutoutVideo = vid;
            const cvs = document.createElement("canvas");
            cvs.width = 1920;
            cvs.height = 1080;
            customActorCutoutCanvas = cvs;

            const vTex = new THREE.CanvasTexture(cvs);
            vTex.minFilter = THREE.LinearFilter;
            vTex.magFilter = THREE.LinearFilter;
            vTex.format = THREE.RGBAFormat;
            customActorCutoutTex = vTex;
            actorMaterial.map = vTex;
            actorMaterial.needsUpdate = true;

            const onFrameDecoded = () => {
                if (customActorCutoutVideo !== vid) return;
                if (syncActorVideoCanvas()) {
                    monitorNeedsRender = true;
                    if (!isBakingLayout) {
                        renderStudioViewports();
                    }
                }
                if (!isBakingLayout && pendingActorSeekTime !== null && !vid.seeking) {
                    const nextT = pendingActorSeekTime;
                    pendingActorSeekTime = null;
                    if (Math.abs(vid.currentTime - nextT) > 0.002) {
                        try { vid.currentTime = nextT; } catch (_) {}
                    }
                }
            };

            const onMeta = () => {
                if (vid.videoWidth && vid.videoHeight) {
                    customActorCutoutAspect = vid.videoWidth / vid.videoHeight;
                    cvs.width = vid.videoWidth;
                    cvs.height = vid.videoHeight;
                    updateActorBillboardTransform();
                    onFrameDecoded();
                }
            };
            vid.addEventListener("loadedmetadata", onMeta);
            vid.addEventListener("loadeddata", onFrameDecoded);
            vid.addEventListener("seeked", onFrameDecoded);
            vid.addEventListener("timeupdate", onFrameDecoded);
            vid.load();

            const fps = projectFPS || 25;
            try { vid.currentTime = currentFrame / fps; } catch (_) {}

            updateActorCutoutUI();
            updateActorBillboardTransform();
            renderStudioViewports();
        } else {
            textureLoader.load(url, (tex) => {
                tex.minFilter = THREE.LinearFilter;
                tex.magFilter = THREE.LinearFilter;
                customActorCutoutTex = tex;
                if (tex.image && tex.image.naturalWidth && tex.image.naturalHeight) {
                    customActorCutoutAspect = tex.image.naturalWidth / tex.image.naturalHeight;
                }
                actorMaterial.map = tex;
                actorMaterial.needsUpdate = true;
                updateActorCutoutUI();
                updateActorBillboardTransform();
                renderStudioViewports();
            }, undefined, (err) => {
                logMsg(`Actor Cutout load error: ${err && err.message ? err.message : 'failed'}`);
            });
            updateActorCutoutUI();
        }

        const pid = (window.currentProject && window.currentProject.id) || "";
        if (pid) {
            try {
                localStorage.setItem(`vfx_actor_cutout_${pid}`, JSON.stringify({
                    url: customActorCutoutUrl,
                    name: customActorCutoutName,
                    aspect: customActorCutoutAspect,
                    mediaType: customActorCutoutMediaType
                }));
            } catch (_) {}
        }
    }

    function clearCustomActorCutout() {
        if (customActorCutoutVideo) {
            try {
                customActorCutoutVideo.pause();
                customActorCutoutVideo.removeAttribute("src");
                customActorCutoutVideo.load();
            } catch (_) {}
            customActorCutoutVideo = null;
        }
        if (customActorCutoutTex) {
            try { customActorCutoutTex.dispose(); } catch (_) {}
            customActorCutoutTex = null;
        }
        customActorCutoutUrl = null;
        customActorCutoutName = "";
        customActorCutoutAspect = null;
        customActorCutoutMediaType = "none";
        actorMaterial.map = null;
        actorMaterial.needsUpdate = true;
        actorBillboard.visible = false;
        updateActorCutoutUI();
        renderStudioViewports();
        const pid = (window.currentProject && window.currentProject.id) || "";
        if (pid) {
            try { localStorage.removeItem(`vfx_actor_cutout_${pid}`); } catch (_) {}
        }
    }

    function updateActorCutoutUI() {
        const dropEmpty = document.getElementById("actor-cutout-empty");
        const previewBox = document.getElementById("actor-cutout-preview-box");
        const thumbImg = document.getElementById("actor-cutout-thumb");
        const thumbVid = document.getElementById("actor-cutout-thumb-video");
        const label = document.getElementById("actor-cutout-label");
        const badge = document.getElementById("actor-cutout-badge");
        const btnRembg = document.getElementById("btn-actor-rembg");

        if (customActorCutoutUrl) {
            if (dropEmpty) dropEmpty.style.display = "none";
            if (previewBox) previewBox.style.display = "flex";
            if (label) label.innerText = customActorCutoutName || (customActorCutoutMediaType === "video" ? "custom_video.mp4" : "actor.png");
            
            if (customActorCutoutMediaType === "video") {
                if (thumbImg) thumbImg.style.display = "none";
                if (thumbVid) {
                    thumbVid.style.display = "block";
                    if (thumbVid.src !== customActorCutoutUrl) thumbVid.src = customActorCutoutUrl;
                }
                if (badge) {
                    badge.innerText = "VIDEO";
                    badge.style.display = "inline-block";
                    badge.style.background = "rgba(56,189,248,0.2)";
                    badge.style.color = "#38bdf8";
                }
                if (btnRembg) btnRembg.style.display = "none";
            } else {
                if (thumbVid) {
                    thumbVid.style.display = "none";
                    thumbVid.src = "";
                }
                if (thumbImg) {
                    thumbImg.style.display = "block";
                    thumbImg.src = customActorCutoutUrl;
                }
                if (badge) {
                    badge.innerText = "IMAGE";
                    badge.style.display = "inline-block";
                    badge.style.background = "rgba(16,185,129,0.2)";
                    badge.style.color = "#34d399";
                }
                if (btnRembg) btnRembg.style.display = "inline-flex";
            }
        } else {
            if (dropEmpty) dropEmpty.style.display = "flex";
            if (previewBox) previewBox.style.display = "none";
            if (thumbImg) { thumbImg.style.display = "none"; thumbImg.src = ""; }
            if (thumbVid) { thumbVid.style.display = "none"; thumbVid.src = ""; }
            if (label) label.innerText = "";
            if (badge) badge.style.display = "none";
        }
    }
    window.__vfxSetCustomActorCutout = setCustomActorCutout;
    window.__vfxClearCustomActorCutout = clearCustomActorCutout;

    function updateActorTexture(frameIdx) {
        if (!isActorProjectionVisible) {
            actorBillboard.visible = false;
            return;
        }
        // Strict manual addition only: if user hasn't added custom media, do NOT show and do NOT load from cache!
        if (!customActorCutoutTex) {
            actorBillboard.visible = false;
            return;
        }
        actorBillboard.visible = true;
        if (actorMaterial.map !== customActorCutoutTex) {
            actorMaterial.map = customActorCutoutTex;
            actorMaterial.needsUpdate = true;
        }

        if (customActorCutoutMediaType === "video" && customActorCutoutVideo) {
            if (!customActorCutoutVideo.paused && !isPlaying) {
                try { customActorCutoutVideo.pause(); } catch (_) {}
            }
            const fps = (typeof projectFPS === "number" && projectFPS > 0) ? projectFPS : 25;
            const rawTime = frameIdx / fps;
            const dur = (customActorCutoutVideo.duration && isFinite(customActorCutoutVideo.duration) && customActorCutoutVideo.duration > 0)
                ? customActorCutoutVideo.duration
                : 0;
            const targetTime = dur > 0 ? Math.min(Math.max(0, dur - 0.004), rawTime % dur) : rawTime;
            if (Math.abs(customActorCutoutVideo.currentTime - targetTime) > 0.002) {
                if (customActorCutoutVideo.seeking) {
                    pendingActorSeekTime = targetTime;
                } else {
                    pendingActorSeekTime = null;
                    try { customActorCutoutVideo.currentTime = targetTime; } catch (_) {}
                }
            } else {
                syncActorVideoCanvas();
            }
        }
    }

    async function updateActorTextureAsync(frameIdx, timeoutMs = 750) {
        if (!isActorProjectionVisible || !customActorCutoutTex) {
            actorBillboard.visible = false;
            return;
        }
        actorBillboard.visible = true;
        if (actorMaterial.map !== customActorCutoutTex) {
            actorMaterial.map = customActorCutoutTex;
            actorMaterial.needsUpdate = true;
        }
        if (customActorCutoutMediaType !== "video" || !customActorCutoutVideo) {
            return;
        }
        const vid = customActorCutoutVideo;
        if (!vid.paused) {
            try { vid.pause(); } catch (_) {}
        }
        pendingActorSeekTime = null;

        // Wait for video metadata if still loading
        if (vid.readyState < 1) {
            await new Promise(resolve => {
                let done = false;
                const finish = () => {
                    if (done) return;
                    done = true;
                    vid.removeEventListener("loadedmetadata", finish);
                    vid.removeEventListener("loadeddata", finish);
                    vid.removeEventListener("error", finish);
                    resolve();
                };
                vid.addEventListener("loadedmetadata", finish, { once: true });
                vid.addEventListener("loadeddata", finish, { once: true });
                vid.addEventListener("error", finish, { once: true });
                setTimeout(finish, timeoutMs);
            });
        }

        const fps = (typeof projectFPS === "number" && projectFPS > 0) ? projectFPS : 25;
        const rawTime = frameIdx / fps;
        const dur = (vid.duration && isFinite(vid.duration) && vid.duration > 0) ? vid.duration : 0;
        const targetTime = dur > 0 ? Math.min(Math.max(0, dur - 0.004), rawTime % dur) : rawTime;

        if (Math.abs(vid.currentTime - targetTime) > 0.002 || vid.seeking) {
            await new Promise(resolve => {
                let done = false;
                const finish = () => {
                    if (done) return;
                    done = true;
                    vid.removeEventListener("seeked", finish);
                    vid.removeEventListener("error", finish);
                    resolve();
                };
                vid.addEventListener("seeked", finish, { once: true });
                vid.addEventListener("error", finish, { once: true });
                setTimeout(finish, timeoutMs);
                try {
                    vid.currentTime = targetTime;
                } catch (_) {
                    finish();
                }
            });
        }

        // Wait a short decode settle timeout so the VP9/H264 hardware frame buffer is 100% ready
        await new Promise(r => setTimeout(r, 65));
        syncActorVideoCanvas();
    }

    function updateActorBillboardTransform() {
        if (!isActorProjectionVisible || !customActorCutoutTex) return;
        const refCam = (!isShotCameraDeleted && shotCamera) ? shotCamera : (getActiveMonitorCamera() || shotCamera);
        refCam.updateMatrixWorld(true);
        const dir = new THREE.Vector3();
        refCam.getWorldDirection(dir);
        actorBillboard.position.copy(refCam.position).addScaledVector(dir, actorDistance);
        actorBillboard.quaternion.copy(refCam.quaternion);

        const vFovRad = THREE.MathUtils.degToRad(refCam.fov);
        const planeHeight = 2.0 * actorDistance * Math.tan(vFovRad / 2.0);
        const aspect = (customActorCutoutAspect && customActorCutoutAspect > 0) ? customActorCutoutAspect : plateAspect;
        const planeWidth = planeHeight * aspect;
        actorBillboard.scale.set(planeWidth, planeHeight, 1);
        actorBillboard.updateMatrixWorld(true);
    }

    function updateShotCameraPose(frameIdx) {
        if (!cameraMatrices || frameIdx >= cameraMatrices.length) {
            syncShotCameraVisuals(false);
            return;
        }
        const raw = cameraMatrices[frameIdx];
        if (!raw) return;

        const m = new THREE.Matrix4();
        m.set(
            raw[0][0], raw[0][1], raw[0][2], raw[0][3],
            raw[1][0], raw[1][1], raw[1][2], raw[1][3],
            raw[2][0], raw[2][1], raw[2][2], raw[2][3],
            raw[3][0], raw[3][1], raw[3][2], raw[3][3]
        );

        shotCamera.matrixAutoUpdate = true;
        m.decompose(shotCamera.position, shotCamera.quaternion, shotCamera.scale);
        shotCamera.scale.set(1, 1, 1);
        syncShotCameraVisuals(false);
    }

    function createDefaultCameraTrajectory(preset = "dolly_in", opts = {}) {
        isShotCameraDeleted = false;
        isShotCameraVisible = true;
        try { localStorage.removeItem("vfx_3d_shot_cam_deleted"); } catch (_) {}
        const tFrames = opts.frames || totalFrames || 87;
        const mats = [];

        let target = new THREE.Vector3(0, 0, 0.5);
        if (cloudMesh && cloudMesh.geometry && cloudMesh.geometry.boundingSphere) {
            target.copy(cloudMesh.geometry.boundingSphere.center);
        }
        if (preset === "orbit_half") {   // around the selected (or first) character, at its chest height
            const ch = (selectedObject && selectedObject.userData.type === "character") ? selectedObject
                : [...threeObjectsMap.values()].find(o => o.userData.type === "character" && o.visible !== false);
            if (ch) target.set(ch.position.x, ch.position.y, (opts.height ?? 0.95));
            else target.z = opts.height ?? 0.95;
        }
        if (opts.target) target.fromArray(opts.target);
        // orbit_half follows the character's pelvis (animations with root motion walk away from a fixed centre)
        let followPath = null;
        if (preset === "orbit_half" && opts.follow !== false) {
            const ch = (opts.subject && threeObjectsMap.get(opts.subject)) || ((selectedObject && selectedObject.userData.type === "character") ? selectedObject
                : [...threeObjectsMap.values()].find(o => o.userData.type === "character" && o.visible !== false));
            const rig = ch && characterRigs.get(ch.userData.id);
            const pelvis = rig && (rigJointLookup(rig).get("pelvis"));
            if (pelvis) {
                const keep = currentFrame; followPath = [];
                for (let f = 0; f < tFrames; f++) { currentFrame = f; updateCharactersAtFrame(); followPath.push(pelvis.getWorldPosition(new THREE.Vector3())); }
                currentFrame = keep; updateCharactersAtFrame();
                // smooth so the camera does not shake with every step
                const k = Math.max(1, Math.round(tFrames / 16));
                followPath = followPath.map((_, i) => { const a = followPath.slice(Math.max(0, i - k), i + k + 1); return a.reduce((s, v) => s.add(v), new THREE.Vector3()).multiplyScalar(1 / a.length); });
            }
        }

        let startPos = new THREE.Vector3();
        let endPos = new THREE.Vector3();

        if (preset === "dolly_in") {
            startPos.set(target.x, target.y - 6.5, target.z + 2.2);
            endPos.set(target.x, target.y - 3.2, target.z + 1.2);
        } else if (preset === "truck_right") {
            startPos.set(target.x - 3.5, target.y - 5.0, target.z + 1.5);
            endPos.set(target.x + 3.5, target.y - 5.0, target.z + 1.5);
        } else if (preset === "orbit_arc") {
            // Computed per frame
        } else if (preset === "from_view") {
            startPos.copy(flyCamera.position);
            const fwd = new THREE.Vector3();
            flyCamera.getWorldDirection(fwd);
            endPos.copy(startPos).addScaledVector(fwd, 2.5);
            target.copy(startPos).addScaledVector(fwd, 6.0);
        } else {
            startPos.set(target.x, target.y - 6.0, target.z + 2.0);
            endPos.set(target.x, target.y - 3.0, target.z + 1.2);
        }

        for (let f = 0; f < tFrames; f++) {
            const alpha = tFrames > 1 ? f / (tFrames - 1) : 0;
            const smooth = alpha * alpha * (3 - 2 * alpha);

            let pos = new THREE.Vector3();
            let lookTarget = target.clone();
            if (followPath) { lookTarget.set(followPath[f].x, followPath[f].y, target.z); }

            if (preset === "orbit_arc") {
                const angle = -Math.PI / 2 + (smooth - 0.5) * (Math.PI / 2.5);
                const r = 5.5;
                pos.set(target.x + Math.cos(angle) * r, target.y + Math.sin(angle) * r, target.z + 1.8);
            } else if (preset === "orbit_half") {
                // 180 deg around the subject: from its left side, through the front (-Y), to its right side
                const arc = THREE.MathUtils.degToRad(opts.arcDeg ?? 180);
                const mid = THREE.MathUtils.degToRad(opts.centerDeg ?? -90);   // -90: in front of the subject (-Y)
                const angle = mid - arc / 2 + smooth * arc;
                const r = opts.radius || 3.4;
                pos.set(lookTarget.x + Math.cos(angle) * r, lookTarget.y + Math.sin(angle) * r, lookTarget.z + (opts.lift ?? 0.25));
            } else {
                pos.lerpVectors(startPos, endPos, smooth);
            }

            const m = new THREE.Matrix4();
            m.lookAt(pos, lookTarget, new THREE.Vector3(0, 0, 1));
            m.setPosition(pos);

            const e = m.elements;
            const raw = [
                [e[0], e[4], e[8], pos.x],
                [e[1], e[5], e[9], pos.y],
                [e[2], e[6], e[10], pos.z],
                [0, 0, 0, 1]
            ];
            mats.push(raw);
        }

        if (opts.focalMm) {   // lens for this shot (36 mm-wide sensor, vertical fov follows the frame aspect)
            const asp = opts.aspect || plateAspect || (16 / 9);
            shotCamera.fov = THREE.MathUtils.radToDeg(2 * Math.atan((36 / asp) / (2 * opts.focalMm)));
            shotCamera.updateProjectionMatrix();
        }
        cameraMatrices = mats;
        buildCameraTrajectoryPath();
        updateShotCameraPose(currentFrame);
        if (camBodyMesh) camBodyMesh.visible = (activeCamera !== shotCamera);
        if (camFrustumHelper) {
            camFrustumHelper.visible = (activeCamera !== shotCamera);
            camFrustumHelper.update();
        }
        if (cameraPathLine) cameraPathLine.visible = (activeCamera !== shotCamera);

        updateOutliner();
        notifyDetailsAndOutliner();

        return mats;
    }

    function set3DTransformKeyframe(targetObj = null, frameIdx = currentFrame, silent = false) {
        let obj = targetObj || selectedObject;
        if (!obj) {
            obj = getActiveMonitorCameraActor();
        }
        if (!obj) {
            obj = addFreeCamera("Camera_1", true);
        }
        if (obj === shotCamera && isShotCameraDeleted) {
            isShotCameraDeleted = false;
            isShotCameraVisible = true;
            try { localStorage.removeItem("vfx_3d_shot_cam_deleted"); } catch (_) {}
        }

        ensureActorUserData(obj);
        const f = Math.max(0, Math.min((totalFrames || 87) - 1, Math.round(Number(frameIdx) || 0)));

        pushUndoState("Set Keyframe");
        let keys = Array.isArray(obj.userData.keyframes) ? obj.userData.keyframes : [];
        const existingKey = keys.find(k => Number(k.frame) === f);
        keys = keys.filter(k => Number(k.frame) !== f);
        keys.push({
            frame: f,
            position: obj.position.clone(),
            quaternion: obj.quaternion.clone(),
            scale: obj.scale.clone(),
            focalMm: obj.userData.focalMm,
            interp: existingKey?.interp || currentInterp || "bezier",
            bezierMode: existingKey?.bezierMode || "user",
            handleOut: existingKey?.handleOut ? { ...existingKey.handleOut } : { dx: 0.333, dy: 0.333 },
            handleIn: existingKey?.handleIn ? { ...existingKey.handleIn } : { dx: -0.333, dy: -0.333 },
            tangentOutScale: existingKey?.tangentOutScale !== undefined ? existingKey.tangentOutScale : 1.0,
            tangentInScale: existingKey?.tangentInScale !== undefined ? existingKey.tangentInScale : 1.0,
            tangentOutOffset: existingKey?.tangentOutOffset ? [...existingKey.tangentOutOffset] : [0, 0, 0],
            tangentInOffset: existingKey?.tangentInOffset ? [...existingKey.tangentInOffset] : [0, 0, 0]
        });
        keys.sort((a, b) => a.frame - b.frame);
        obj.userData.keyframes = keys;

        buildCameraTrajectoryPath();
        updateKeyframeMarkers();
        syncSceneData();
        notifyDetailsAndOutliner();
        monitorNeedsRender = true;
        renderStudioViewports();

        if (!silent) {
            logMsg(`🔑 3D Keyframe saved for [${obj.userData.name || "Camera"}] at frame ${f} (Total keys: ${keys.length})`);
            if (typeof window.__vfxToast === "function") {
                window.__vfxToast(uiText(`🔑 3D Keyframe [${obj.userData.name || "Camera"}] saved at frame ${f}`, `🔑 3D Ключ [${obj.userData.name || "Camera"}] сохранён на кадре ${f}`));
            }
        }
        return f;
    }

    function setCameraKeyframe(frameIdx = currentFrame) {
        let camActor = (selectedObject && selectedObject.userData?.type === "camera")
            ? selectedObject
            : getActiveMonitorCameraActor();
        if (!camActor) {
            camActor = addFreeCamera("Camera_1", true);
        }
        return set3DTransformKeyframe(camActor, frameIdx, false);
    }

    // Load Scene Data & Adaptive Point Cloud Material (4K / 1080p Adaptive)
    let cloudMesh = null;
    let pointCloudMaterial = null;
    let cloudPointSize = parseFloat(localStorage.getItem("vfx_ue_point_size") || "0.065");
    let cloudPointAlpha = parseFloat(localStorage.getItem("vfx_3d_point_alpha") || "0.90");
    let flySpeedMultiplier = parseFloat(localStorage.getItem("vfx_ue_fly_speed") || "1.0");

    let isLidarDeleted = localStorage.getItem("vfx_3d_lidar_deleted") === "true";
    let isShotCameraDeleted = localStorage.getItem("vfx_3d_shot_cam_deleted") === "true";

    // Generate crisp circular LiDAR disc sprite for high-resolution point clouds
    function createAdaptiveLidarPointTexture() {
        const sz = 64;
        const cnv = document.createElement("canvas");
        cnv.width = sz;
        cnv.height = sz;
        const ctx2d = cnv.getContext("2d");
        const half = sz / 2;
        const grad = ctx2d.createRadialGradient(half - 4, half - 4, 2, half, half, half - 1);
        grad.addColorStop(0.0, "rgba(255, 255, 255, 1.0)");
        grad.addColorStop(0.68, "rgba(245, 248, 255, 0.96)");
        grad.addColorStop(0.88, "rgba(200, 210, 230, 0.55)");
        grad.addColorStop(1.0, "rgba(0, 0, 0, 0.0)");
        ctx2d.fillStyle = grad;
        ctx2d.beginPath();
        ctx2d.arc(half, half, half - 1, 0, Math.PI * 2);
        ctx2d.fill();
        const tex = new THREE.CanvasTexture(cnv);
        tex.needsUpdate = true;
        return tex;
    }
    const lidarDiscTexture = createAdaptiveLidarPointTexture();

    // Update effective point size across 1080p and 4K monitors (and sync all scene point clouds)
    function updateAdaptivePointCloudSize(viewportCssHeight) {
        const refHeight = 960.0;
        const h = Math.max(320, viewportCssHeight || (canvasHost ? canvasHost.clientHeight : 0) || window.innerHeight || refHeight);
        // Compensate when moving between 1080p and 4K viewports so points stay crisp and uniform
        const screenScale = Math.pow(refHeight / h, 0.35);
        const effSize = Math.max(0.005, cloudPointSize * screenScale);
        if (pointCloudMaterial) {
            pointCloudMaterial.size = effSize;
            pointCloudMaterial.needsUpdate = true;
        }
        if (scene) {
            scene.traverse((obj) => {
                if (obj && obj.isPoints && obj.material) {
                    obj.material.size = effSize;
                    obj.material.needsUpdate = true;
                }
            });
        }
    }

    // Cinema Free Camera State (Z-up Yaw & Pitch in radians)
    let camYaw = Math.PI * 0.5;   // Looking along +Y by default
    let camPitch = -0.35;         // Looking slightly down
    const camPivotTarget = new THREE.Vector3(0, 0, 0);

    function syncYawPitchFromCamera() {
        const dir = new THREE.Vector3();
        flyCamera.getWorldDirection(dir);
        dir.normalize();
        camPitch = Math.asin(Math.max(-0.995, Math.min(0.995, dir.z)));
        camYaw = Math.atan2(dir.y, dir.x);
    }

    function applyYawPitchToCamera() {
        const maxPitch = THREE.MathUtils.degToRad(89.0);
        camPitch = Math.max(-maxPitch, Math.min(maxPitch, camPitch));
        const cosP = Math.cos(camPitch);
        const dirX = cosP * Math.cos(camYaw);
        const dirY = cosP * Math.sin(camYaw);
        const dirZ = Math.sin(camPitch);
        flyCamera.up.set(0, 0, 1);
        flyCamera.lookAt(
            flyCamera.position.x + dirX,
            flyCamera.position.y + dirY,
            flyCamera.position.z + dirZ
        );
    }

    function saveFlyCameraPose() {
        try {
            const pose = {
                pos: [flyCamera.position.x, flyCamera.position.y, flyCamera.position.z],
                yaw: camYaw,
                pitch: camPitch,
                pivot: [camPivotTarget.x, camPivotTarget.y, camPivotTarget.z]
            };
            localStorage.setItem("vfx_3d_fly_cam_pose", JSON.stringify(pose));
        } catch (_) {}
    }

    function restoreFlyCameraPose() {
        try {
            const raw = localStorage.getItem("vfx_3d_fly_cam_pose");
            if (raw) {
                const pose = JSON.parse(raw);
                if (pose.pos && Array.isArray(pose.pos)) {
                    flyCamera.position.set(pose.pos[0], pose.pos[1], pose.pos[2]);
                }
                if (pose.yaw !== undefined) camYaw = pose.yaw;
                if (pose.pitch !== undefined) camPitch = pose.pitch;
                if (pose.pivot && Array.isArray(pose.pivot)) {
                    camPivotTarget.set(pose.pivot[0], pose.pivot[1], pose.pivot[2]);
                }
                applyYawPitchToCamera();
            }
        } catch (_) {}
    }

    function focusCameraOnObject(targetObj) {
        let center = new THREE.Vector3(0, 0, 0.5);
        let radius = 2.5;
        if (targetObj) {
            const box = new THREE.Box3().setFromObject(targetObj);
            if (!box.isEmpty()) {
                const sphere = new THREE.Sphere();
                box.getBoundingSphere(sphere);
                center.copy(sphere.center);
                radius = Math.max(1.2, sphere.radius * 2.4);
            } else {
                center.copy(targetObj.position);
            }
        } else if (cloudMesh && cloudMesh.geometry && cloudMesh.geometry.boundingSphere) {
            center.copy(cloudMesh.geometry.boundingSphere.center);
            radius = Math.max(2.5, cloudMesh.geometry.boundingSphere.radius * 0.65);
        }
        const dir = new THREE.Vector3();
        flyCamera.getWorldDirection(dir);
        dir.normalize();
        flyCamera.position.copy(center).addScaledVector(dir, -radius);
        camPivotTarget.copy(center);
        flyCamera.lookAt(center);
        syncYawPitchFromCamera();

        if (isOrthoMode) {
            orthoFrustumSize = radius * 2.2;
            orthoCamera.position.copy(center).addScaledVector(dir, -radius * 1.5);
            orthoCamera.lookAt(center);
            updateOrthoCameraFrustum();
        }
    }

    // ==========================================
    // Viewport Camera Views & Orthographic Mode
    // ==========================================
    const orthoCamera = new THREE.OrthographicCamera(-5, 5, 5, -5, 0.05, 3000);
    orthoCamera.position.set(0, -6, 3);
    orthoCamera.up.set(0, 0, 1);
    orthoCamera.lookAt(0, 0, 0);

    let isOrthoMode = false;
    let currentActiveView = "Persp";
    let orthoFrustumSize = 6.0;

    function updateOrthoCameraFrustum(vSize) {
        if (vSize !== undefined && vSize > 0) orthoFrustumSize = vSize;
        const w = getMainViewportWidth();
        const h = (canvasHost ? canvasHost.clientHeight : 0) || window.innerHeight || 800;
        const aspect = Math.max(0.1, w / Math.max(1, h));
        const halfH = orthoFrustumSize * 0.5;
        const halfW = halfH * aspect;
        orthoCamera.left = -halfW;
        orthoCamera.right = halfW;
        orthoCamera.top = halfH;
        orthoCamera.bottom = -halfH;
        orthoCamera.updateProjectionMatrix();
    }

    function updateActiveViewLabel(viewStr) {
        const badge = document.getElementById("vfx-viewport-badge-title");
        const orthoBtnText = document.getElementById("lbl-toggle-ortho-text");
        const orthoBtn = document.getElementById("btn-toggle-ortho");
        
        if (orthoBtnText) {
            orthoBtnText.textContent = isOrthoMode ? uiText("Ortho", "Орто") : uiText("Persp", "Перспектива");
        }
        if (orthoBtn) {
            if (isOrthoMode) {
                orthoBtn.style.borderColor = "#10b981";
                orthoBtn.style.color = "#10b981";
                orthoBtn.style.background = "rgba(16, 185, 129, 0.15)";
            } else {
                orthoBtn.style.borderColor = "rgba(56, 189, 248, 0.3)";
                orthoBtn.style.color = "#38bdf8";
                orthoBtn.style.background = "";
            }
        }

        if (badge) {
            const modeTxt = isOrthoMode ? "ORTHOGRAPHIC" : "PERSPECTIVE";
            badge.textContent = `📐 3D ${viewStr.toUpperCase()} (${modeTxt})`;
        }

        // Highlight preset button
        const btnIds = ["top", "front", "right", "bottom", "back", "left"];
        btnIds.forEach(id => {
            const btn = document.getElementById(`btn-view-${id}`);
            if (!btn) return;
            if (viewStr.toLowerCase() === id) {
                btn.style.background = "rgba(56, 189, 248, 0.25)";
                btn.style.borderColor = "#38bdf8";
                btn.style.color = "#38bdf8";
            } else {
                btn.style.background = "";
                btn.style.borderColor = "";
                btn.style.color = "";
            }
        });
    }

    function setViewportView(viewName, forceOrtho = null) {
        const center = selectedObject ? selectedObject.position.clone() : camPivotTarget.clone();
        let dist = Math.max(3.0, flyCamera.position.distanceTo(center));
        if (isNaN(dist) || dist <= 0.1) dist = 6.0;

        if (forceOrtho !== null) {
            isOrthoMode = forceOrtho;
        } else if (viewName !== "Persp") {
            isOrthoMode = true; // Preset views default to clean orthographic representation
        }

        currentActiveView = viewName;
        orthoFrustumSize = dist * 1.25;

        // In Keshiki 3D: Z is UP, +Y is Depth/Back, -Y is Front, +X is Right, -X is Left
        const newCamPos = center.clone();
        const newUp = new THREE.Vector3(0, 0, 1);
        const newLook = center.clone();

        switch (viewName) {
            case "Top":
                newCamPos.set(center.x, center.y, center.z + dist);
                newUp.set(0, 1, 0); // Looking straight down -Z, +Y is UP
                break;
            case "Bottom":
                newCamPos.set(center.x, center.y, center.z - dist);
                newUp.set(0, -1, 0); // Looking straight up +Z, -Y is UP
                break;
            case "Front":
                newCamPos.set(center.x, center.y - dist, center.z);
                newUp.set(0, 0, 1); // Looking along +Y, +Z is UP
                break;
            case "Back":
                newCamPos.set(center.x, center.y + dist, center.z);
                newUp.set(0, 0, 1); // Looking along -Y, +Z is UP
                break;
            case "Left":
                newCamPos.set(center.x - dist, center.y, center.z);
                newUp.set(0, 0, 1); // Looking along +X, +Z is UP
                break;
            case "Right":
                newCamPos.set(center.x + dist, center.y, center.z);
                newUp.set(0, 0, 1); // Looking along -X, +Z is UP
                break;
            case "Persp":
            default:
                isOrthoMode = false;
                currentActiveView = "Persp";
                break;
        }

        if (viewName !== "Persp") {
            // Update orthographic camera
            orthoCamera.position.copy(newCamPos);
            orthoCamera.up.copy(newUp);
            orthoCamera.lookAt(newLook);
            updateOrthoCameraFrustum();

            // Also update flyCamera so switching to Persp maintains identical angle
            flyCamera.position.copy(newCamPos);
            flyCamera.up.copy(newUp);
            flyCamera.lookAt(newLook);
            syncYawPitchFromCamera();
        }

        if (isOrthoMode) {
            activeCamera = orthoCamera;
            if (transformControls) transformControls.camera = orthoCamera;
        } else {
            activeCamera = flyCamera;
            if (transformControls) transformControls.camera = flyCamera;
        }

        updateActiveViewLabel(currentActiveView);
        monitorNeedsRender = true;
        renderStudioViewports();
    }

    function toggleOrthoMode() {
        isOrthoMode = !isOrthoMode;
        const center = selectedObject ? selectedObject.position.clone() : camPivotTarget.clone();
        let dist = Math.max(1.0, flyCamera.position.distanceTo(center));

        if (isOrthoMode) {
            orthoFrustumSize = dist * 1.25;
            orthoCamera.position.copy(flyCamera.position);
            orthoCamera.quaternion.copy(flyCamera.quaternion);
            orthoCamera.up.copy(flyCamera.up);
            updateOrthoCameraFrustum();
            activeCamera = orthoCamera;
            if (transformControls) transformControls.camera = orthoCamera;
            currentActiveView = currentActiveView === "Persp" ? "User Ortho" : currentActiveView;
        } else {
            flyCamera.position.copy(orthoCamera.position);
            flyCamera.quaternion.copy(orthoCamera.quaternion);
            flyCamera.up.copy(orthoCamera.up);
            syncYawPitchFromCamera();
            activeCamera = flyCamera;
            if (transformControls) transformControls.camera = flyCamera;
            currentActiveView = "Persp";
        }

        updateActiveViewLabel(currentActiveView);
        monitorNeedsRender = true;
        renderStudioViewports();
    }

    // 3D Axis Compass HUD Widget (projects X, Y, Z directions live)
    function renderAxisCompass() {
        const compassCanvas = document.getElementById("vfx-axis-compass-canvas");
        if (!compassCanvas) return;
        const ctx = compassCanvas.getContext("2d");
        if (!ctx) return;

        const w = compassCanvas.width;
        const h = compassCanvas.height;
        ctx.clearRect(0, 0, w, h);

        const cx = w * 0.5;
        const cy = h * 0.5;
        const radius = 25.0;

        // Subtle dark translucent background disc
        ctx.beginPath();
        ctx.arc(cx, cy, 33, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(15, 23, 42, 0.55)";
        ctx.fill();
        ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
        ctx.lineWidth = 1;
        ctx.stroke();

        const axes = [
            { id: "X", dir: new THREE.Vector3(1, 0, 0), color: "#ef4444", label: "X", isPos: true },
            { id: "Y", dir: new THREE.Vector3(0, 1, 0), color: "#10b981", label: "Y", isPos: true },
            { id: "Z", dir: new THREE.Vector3(0, 0, 1), color: "#38bdf8", label: "Z", isPos: true },
            { id: "-X", dir: new THREE.Vector3(-1, 0, 0), color: "#f87171", label: "", isPos: false },
            { id: "-Y", dir: new THREE.Vector3(0, -1, 0), color: "#34d399", label: "", isPos: false },
            { id: "-Z", dir: new THREE.Vector3(0, 0, -1), color: "#7dd3fc", label: "", isPos: false }
        ];

        // Transform axes by camera view rotation
        const camMat = new THREE.Matrix4().extractRotation(activeCamera.matrixWorldInverse);
        axes.forEach(axis => {
            const v = axis.dir.clone().applyMatrix4(camMat);
            axis.screenX = cx + v.x * radius;
            axis.screenY = cy - v.y * radius;
            axis.depth = v.z;
        });

        // Depth sort: render furthest axes first
        axes.sort((a, b) => a.depth - b.depth);

        axes.forEach(axis => {
            if (axis.isPos) {
                // Line from center
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.lineTo(axis.screenX, axis.screenY);
                ctx.strokeStyle = axis.color;
                ctx.lineWidth = 2.2;
                ctx.stroke();

                // Axis bubble
                ctx.beginPath();
                ctx.arc(axis.screenX, axis.screenY, 7.5, 0, Math.PI * 2);
                ctx.fillStyle = axis.color;
                ctx.fill();
                ctx.strokeStyle = "#ffffff";
                ctx.lineWidth = 1.2;
                ctx.stroke();

                // Label
                ctx.fillStyle = "#ffffff";
                ctx.font = "bold 9px sans-serif";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                ctx.fillText(axis.label, axis.screenX, axis.screenY + 0.5);
            } else {
                // Negative axis dotted trace + pip
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.lineTo(axis.screenX, axis.screenY);
                ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
                ctx.lineWidth = 1.0;
                ctx.setLineDash([2, 2]);
                ctx.stroke();
                ctx.setLineDash([]);

                ctx.beginPath();
                ctx.arc(axis.screenX, axis.screenY, 3.2, 0, Math.PI * 2);
                ctx.fillStyle = "rgba(255, 255, 255, 0.4)";
                ctx.fill();
            }
        });
    }

    function initAxisCompassWidget() {
        const compassCanvas = document.getElementById("vfx-axis-compass-canvas");
        if (!compassCanvas) return;
        compassCanvas.addEventListener("click", (e) => {
            const rect = compassCanvas.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const clickY = e.clientY - rect.top;
            const cx = compassCanvas.width * 0.5;
            const cy = compassCanvas.height * 0.5;
            const radius = 25.0;

            const camMat = new THREE.Matrix4().extractRotation(activeCamera.matrixWorldInverse);
            const targets = [
                { dir: new THREE.Vector3(0, 0, 1), view: "Top" },
                { dir: new THREE.Vector3(0, 0, -1), view: "Bottom" },
                { dir: new THREE.Vector3(0, -1, 0), view: "Front" },
                { dir: new THREE.Vector3(0, 1, 0), view: "Back" },
                { dir: new THREE.Vector3(1, 0, 0), view: "Right" },
                { dir: new THREE.Vector3(-1, 0, 0), view: "Left" }
            ];

            let bestTarget = null;
            let minDist = 14.0;
            targets.forEach(t => {
                const v = t.dir.clone().applyMatrix4(camMat);
                const sx = cx + v.x * radius;
                const sy = cy - v.y * radius;
                const d = Math.hypot(clickX - sx, clickY - sy);
                if (d < minDist) {
                    minDist = d;
                    bestTarget = t;
                }
            });

            if (bestTarget) {
                setViewportView(bestTarget.view);
            } else {
                const distCenter = Math.hypot(clickX - cx, clickY - cy);
                if (distCenter < 12.0) {
                    toggleOrthoMode();
                }
            }
        });
    }

    function initViewportViewButtons() {
        const bindBtn = (id, fn) => {
            const btn = document.getElementById(id);
            if (btn) btn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); fn(); };
        };
        bindBtn("btn-view-top", () => setViewportView("Top"));
        bindBtn("btn-view-front", () => setViewportView("Front"));
        bindBtn("btn-view-right", () => setViewportView("Right"));
        bindBtn("btn-view-bottom", () => setViewportView("Bottom"));
        bindBtn("btn-view-back", () => setViewportView("Back"));
        bindBtn("btn-view-left", () => setViewportView("Left"));
        bindBtn("btn-toggle-ortho", () => toggleOrthoMode());
        bindBtn("btn-view-focus", () => focusCameraOnObject(selectedObject));
        initAxisCompassWidget();
        updateActiveViewLabel("Persp");
    }

    async function loadSceneData(targetProj = null, options = {}) {
        try {
            const pid = targetProj
                ? (targetProj.id || targetProj)
                : ((window.currentProject && window.currentProject.id) || (typeof localStorage !== "undefined" ? localStorage.getItem("vfx_last_project") : "") || "New_Movie_Shot");
            const url = pid ? `/vfx/scene_data?project_id=${encodeURIComponent(pid)}` : "/vfx/scene_data";
            const r = await fetch(url);
            const data = await r.json();

            // Always clear old point cloud first
            if (cloudMesh) {
                scene.remove(cloudMesh);
                cloudMesh.geometry.dispose();
                cloudMesh = null;
            }

            let pts = data.points;
            let cols = data.colors || [];
            isLidarDeleted = localStorage.getItem("vfx_3d_lidar_deleted") === "true";
            isShotCameraDeleted = localStorage.getItem("vfx_3d_shot_cam_deleted") === "true";

            if (isLidarDeleted) {
                // User explicitly deleted point cloud; do not synthesize default points
                pts = [];
                cols = [];
            } else if (!pts || pts.length === 0) {
                // Synthesize subtle default LiDAR room/ground scan reference points so point cloud is visible and scalable
                pts = [];
                cols = [];
                const numPts = 700;
                for (let i = 0; i < numPts; i++) {
                    const rx = (Math.random() - 0.5) * 8.0;
                    const ry = (Math.random() - 0.5) * 8.0;
                    const rz = Math.random() < 0.65 ? (Math.random() * 0.1) : (Math.random() * 2.2);
                    pts.push([rx, ry, rz]);
                    const normZ = Math.max(0, Math.min(1, rz / 2.2));
                    cols.push([0.20 + 0.50 * normZ, 0.70 + 0.25 * normZ, 0.95]);
                }
            }

            if (pts && pts.length > 0) {
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
                    map: lidarDiscTexture,
                    alphaTest: 0.08,
                    sizeAttenuation: true,
                    vertexColors: true,
                    transparent: true,
                    opacity: cloudPointAlpha
                });
                updateAdaptivePointCloudSize(canvasHost.clientHeight);
                cloudMesh = new THREE.Points(ptGeom, pointCloudMaterial);
                cloudMesh.userData = { id: "obj_pointcloud", name: "Scene Point Cloud", type: "pointcloud" };
                scene.add(cloudMesh);
            }

            // Always clear old camera trajectory line first
            if (cameraPathLine) {
                scene.remove(cameraPathLine);
                cameraPathLine.geometry.dispose();
                cameraPathLine = null;
            }

            window.__vfxSceneSource = { video: data.source_video || "", startFrame: (data.camera && data.camera.start_frame) || 0 };
            if (data.camera && data.camera.matrices && data.camera.matrices.length > 0 && !isShotCameraDeleted) {
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

                cameraMatrices = data.camera.matrices;
                totalFrames = Math.max(1, cameraMatrices.length);
                timelineSlider.max = Math.max(0, totalFrames - 1);
                hudFrameLabel.innerText = `Frame: [ ${String(currentFrame).padStart(3, '0')} / ${totalFrames} ]`;
                if (data.camera.focal_mm) {
                    const focal = data.camera.focal_mm;
                    const sensorW = data.camera.sensor_width_mm || 36.0;
                    // focal_mm is fx/width*36 (horizontal); three.js wants the VERTICAL fov: atan(h / 2fy)
                    const gateAspect = (data.camera.width && data.camera.height) ? data.camera.width / data.camera.height : plateAspect;
                    const fovRad = 2.0 * Math.atan((sensorW / gateAspect) / (2.0 * focal));
                    shotCamera.fov = THREE.MathUtils.radToDeg(fovRad);
                    shotCamera.updateProjectionMatrix();

                    const lensInfo = document.getElementById("monitor-lens-info");
                    if (lensInfo) {
                        lensInfo.innerText = `Focal: ${Math.round(focal)}mm • Sensor: 36×24mm`;
                    }
                }
                buildCameraTrajectoryPath();
                updateShotCameraPose(currentFrame);

                // Auto-frame FlyCam
                const firstCam = cameraMatrices[0];
                const camPos = new THREE.Vector3(firstCam[0][3], firstCam[1][3], firstCam[2][3]);
                let targetLook = new THREE.Vector3(0, 2, 0);
                if (cloudMesh && cloudMesh.geometry.boundingSphere) {
                    targetLook.copy(cloudMesh.geometry.boundingSphere.center);
                }
                flyCamera.position.set(camPos.x - 2.5, camPos.y - 4.5, camPos.z + 2.5);
                flyCamera.up.set(0, 0, 1);
                flyCamera.lookAt(targetLook);
                camPivotTarget.copy(targetLook);
                syncYawPitchFromCamera();
            } else {
                cameraMatrices = [];
                if (camBodyMesh) camBodyMesh.visible = false;
                if (camFrustumHelper) camFrustumHelper.visible = false;
                if (cameraPathLine) cameraPathLine.visible = false;
                totalFrames = 87;
                currentFrame = 0;
                timelineSlider.max = 86;
                timelineSlider.value = 0;
                hudFrameLabel.innerText = "Frame: [ 000 / 087 ]";
            }

            updateActorTexture(currentFrame);
            updateActorBillboardTransform();
            if (pid) {
                try {
                    const savedCutout = JSON.parse(localStorage.getItem(`vfx_actor_cutout_${pid}`) || "null");
                    if (savedCutout && savedCutout.url) {
                        setCustomActorCutout(savedCutout.url, savedCutout.name, savedCutout.mediaType);
                    }
                } catch (_) {}
            }

            // Load existing saved layout manifest for this project (never leak global cache across projects)
            if (!options.keepExistingActors || threeObjectsMap.size === 0) {
                try {
                    try { localStorage.removeItem("vfx_3d_manifest_cache"); } catch (_) {}
                    const mUrl = pid ? `/vfx/layout_manifest?project_id=${encodeURIComponent(pid)}` : "/vfx/layout_manifest";
                    const mRes = await fetch(mUrl);
                    const mData = await mRes.json();
                    let listToLoad = [];
                    if (Array.isArray(mData) && mData.length > 0) {
                        listToLoad = mData.filter(it => !(it && it.type === "character" && /SK_Desert_soldier/i.test(String(it.characterUrl || ""))));
                    } else if (pid) {
                        const cached = localStorage.getItem(`vfx_3d_manifest_cache_${pid}`);
                        if (cached) {
                            try { listToLoad = JSON.parse(cached) || []; } catch (_) {}
                        }
                    }
                    // Restore dedicated sun settings if available
                    if (pid) {
                        try {
                            const savedSun = JSON.parse(localStorage.getItem(`vfx_3d_sun_${pid}`) || "null");
                            if (savedSun) {
                                let lObj = listToLoad.find(it => it && it.type === "light");
                                if (lObj) {
                                    Object.assign(lObj, savedSun);
                                } else {
                                    listToLoad.push({
                                        id: `light_sun_${pid}`,
                                        name: "Sun_1",
                                        type: "light",
                                        ...savedSun
                                    });
                                }
                            }
                        } catch (_) {}
                    }
                    loadManifestObjects(listToLoad);
                } catch (e) {
                    loadManifestObjects([]);
                }
            }

            // Restore persistent camera pose if previously positioned
            restoreFlyCameraPose();

            // Sync timeline events and status bar project label
            if (typeof window.__vfxReloadProjectTimelineEvents === "function") {
                window.__vfxReloadProjectTimelineEvents(pid);
            }
            const stLabel = document.getElementById("st-project-label");
            if (stLabel) {
                const pName = targetProj?.name || window.currentProject?.name || "Empty Project";
                stLabel.textContent = pName;
            }

        } catch (e) {
            console.log("[VFX Studio] Scene data load note:", e);
        }
    }

    // 3D Viewport Render Quality (Optimized for 4K / High-DPI screens)
    let viewport3DQuality = localStorage.getItem("vfx_3d_render_quality") || "auto";

    function calculateOptimalDpr(w, h) {
        const rawDpr = window.devicePixelRatio || 1.0;
        const safeW = Math.max(1, w);
        const safeH = Math.max(1, h);

        if (viewport3DQuality === "ultra") {
            // Allows up to DPR 2.0 for extreme high-end GPUs (e.g. RTX 4090)
            return Math.min(rawDpr, 2.0);
        }
        if (viewport3DQuality === "native") {
            // Exact 1:1 pixel mapping (1 buffer pixel = 1 CSS pixel)
            return 1.0;
        }
        if (viewport3DQuality === "performance") {
            // Fast 60 FPS: clamp buffer to maximum 1920x1080 (~2.07M pixels)
            const maxPixels = 1920 * 1080;
            const currentPixels = safeW * safeH;
            if (currentPixels * rawDpr * rawDpr > maxPixels) {
                return Math.max(0.65, Math.min(1.0, Math.sqrt(maxPixels / currentPixels)));
            }
            return Math.min(rawDpr, 1.0);
        }

        // Default: "auto" (Balanced 4K / High-DPI 60 FPS)
        // On 4K monitors (e.g. 3840x2160 or 2560x1440), physical pixel density is so high
        // that rendering at ~2560x1440 buffer (3.68M pixels) looks razor sharp with zero pixelation,
        // while reducing WebGL fragment shading cost by up to 500-800%!
        const maxBufferPixels = 2560 * 1440; // 3.6864 Megapixels
        const area = safeW * safeH;
        let targetDpr = Math.min(rawDpr, 1.25);
        if (area * targetDpr * targetDpr > maxBufferPixels) {
            targetDpr = Math.sqrt(maxBufferPixels / area);
        }
        return Math.max(0.75, Math.min(targetDpr, 1.25));
    }

    window.__vfxSet3DQuality = (mode) => {
        viewport3DQuality = mode || "auto";
        try { localStorage.setItem("vfx_3d_render_quality", viewport3DQuality); } catch (_) {}
        currentDpr = 0;
        renderStudioViewports();
    };

    // WebGL Renderer (Dynamic Multi-Monitor 4K / 1080p DPI support)
    let currentDpr = calculateOptimalDpr(width, height);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
    renderer.setPixelRatio(currentDpr);
    renderer.setSize(width, height, false);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    if (THREE.sRGBEncoding !== undefined) renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = viewportExposure;
    renderer.domElement.className = "vfx-viewport-canvas";
    canvasHost.appendChild(renderer.domElement);

    // Dedicated WebGL Renderer for Shot Camera Monitor Viewport (Film Gate)
    const monitorCanvas = document.getElementById("monitor-3d-canvas");
    let monitorRenderer = null;
    if (monitorCanvas) {
        try {
            monitorRenderer = new THREE.WebGLRenderer({
                canvas: monitorCanvas,
                antialias: true,
                alpha: false,
                powerPreference: "high-performance",
                preserveDrawingBuffer: true
            });
            monitorRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.0));
            monitorRenderer.setClearColor(0x0a0a0d, 1.0);
            monitorRenderer.shadowMap.enabled = true;
            monitorRenderer.shadowMap.type = THREE.PCFSoftShadowMap;
            if (THREE.sRGBEncoding !== undefined) monitorRenderer.outputEncoding = THREE.sRGBEncoding;
            monitorRenderer.toneMapping = THREE.ACESFilmicToneMapping;
            monitorRenderer.toneMappingExposure = viewportExposure;
        } catch (e) {
            console.warn("Failed to create monitorRenderer:", e);
        }
    }

    // Keep OrbitControls reference disabled so legacy code never crashes, while our UE5 Controller handles navigation
    const orbitControls = new THREE.OrbitControls(flyCamera, renderer.domElement);
    orbitControls.enabled = false;
    syncYawPitchFromCamera();

    // Transform Controls
    const transformControls = new THREE.TransformControls(flyCamera, renderer.domElement);
    transformControls.size = 1.0;
    transformControls.setMode("translate");
    scene.add(transformControls);

    const transformProxyGroup = new THREE.Group();
    scene.add(transformProxyGroup);

    // =========================================================================
    // KESHIKI 6DoF CAMERA NAVIGATION CONTROLLER
    // - Hold RMB (ПКМ) + Move Mouse: Free-Look 360° (Yaw & Pitch in place) + WASD/QE Fly
    // - Hold RMB (ПКМ) + Mouse Wheel: Adjust WASD Fly Speed
    // - Hold MMB (СКМ) or Shift+RMB or LMB+RMB: Screen-Space Pan
    // - Alt + LMB (ЛКМ): Orbit around Selected Object / Pivot
    // - Hold LMB (ЛКМ) on empty space: Look-Yaw + Walk Forward/Back
    // - Click LMB (ЛКМ): Select 3D Actor / Gizmo without spinning camera
    // - Key 'F': Focus Camera on Selected Actor (or Point Cloud)
    // =========================================================================
    let ueMouseMode = null; // "rmb_look" | "mmb_pan" | "alt_orbit" | "lmb_walk" | null
    let lastMouseX = 0;
    let lastMouseY = 0;
    let isRmbHeld = false;

    renderer.domElement.addEventListener("contextmenu", (e) => {
        e.preventDefault();
    });

    renderer.domElement.addEventListener("pointerdown", (e) => {
        if (activeCamera !== flyCamera && activeCamera !== orthoCamera) return;
        const target = getPointerTarget(e);
        if (!target.cam || target.isShotCam) return;
        if (transformControls.dragging) return;

        lastMouseX = e.clientX;
        lastMouseY = e.clientY;

        if (e.button === 2) {
            isRmbHeld = true;
            ueMouseMode = e.shiftKey ? "mmb_pan" : "rmb_look";
            renderer.domElement.style.cursor = ueMouseMode === "mmb_pan" ? "move" : "crosshair";
            try { renderer.domElement.setPointerCapture(e.pointerId); } catch (_) {}
        } else if (e.button === 1) {
            ueMouseMode = "mmb_pan";
            renderer.domElement.style.cursor = "move";
            try { renderer.domElement.setPointerCapture(e.pointerId); } catch (_) {}
        } else if (e.button === 0) {
            if (e.altKey) {
                ueMouseMode = "alt_orbit";
                if (selectedObject) {
                    camPivotTarget.copy(selectedObject.position);
                }
                renderer.domElement.style.cursor = "grabbing";
            } else if (active3DBezierHandles.length > 0 && cameraPathLine && cameraPathLine.visible) {
                const canvasRect = cachedCanvasRect || renderer.domElement.getBoundingClientRect();
                const vpW = getMainViewportWidth(canvasRect.width);
                const vpH = canvasRect.height;
                let pickedHandle = null;
                let bestDistPx = 14;
                const projTmp = new THREE.Vector3();
                for (const hItem of active3DBezierHandles) {
                    projTmp.copy(hItem.tipPos).project(activeCamera);
                    if (projTmp.z < -1 || projTmp.z > 1) continue;
                    const sx = canvasRect.left + (projTmp.x * 0.5 + 0.5) * vpW;
                    const sy = canvasRect.top + (-projTmp.y * 0.5 + 0.5) * vpH;
                    const dPx = Math.hypot(e.clientX - sx, e.clientY - sy);
                    if (dPx < bestDistPx) {
                        bestDistPx = dPx;
                        pickedHandle = hItem;
                    }
                }
                if (pickedHandle) {
                    e.preventDefault();
                    e.stopPropagation();
                    let hasPushedUndo = false;
                    const camNormal = new THREE.Vector3();
                    activeCamera.getWorldDirection(camNormal);
                    const dragPlane = new THREE.Plane().setFromNormalAndCoplanarPoint(camNormal, pickedHandle.tipPos);
                    const raycaster3D = new THREE.Raycaster();
                    const hitPt = new THREE.Vector3();

                    const onMoveHandle3D = (mvEv) => {
                        if (!hasPushedUndo) {
                            pushUndoState("Adjust 3D Bezier Handle");
                            hasPushedUndo = true;
                        }
                        if (mvEv.shiftKey) {
                            // Shift held: lock handle to default unstretched smooth continuous state
                            if (pickedHandle.role === "out") {
                                pickedHandle.keyObj.tangentOutScale = 1.0;
                                pickedHandle.keyObj.tangentOutOffset = [0, 0, 0];
                                pickedHandle.keyObj.handleOut = { dx: 0.333, dy: 0.333 };
                            } else {
                                pickedHandle.keyObj.tangentInScale = 1.0;
                                pickedHandle.keyObj.tangentInOffset = [0, 0, 0];
                                pickedHandle.keyObj.handleIn = { dx: -0.333, dy: -0.333 };
                            }
                        } else {
                            // User mode (default): stretch 3D Bezier guide handle freely in 3D space
                            const ptTarget = getPointerTarget(mvEv);
                            if (ptTarget.ndc) {
                                raycaster3D.setFromCamera(ptTarget.ndc, activeCamera);
                                if (raycaster3D.ray.intersectPlane(dragPlane, hitPt)) {
                                    // Compute default unstretched tip position first
                                    const sortedKeys = [...(pickedHandle.actor.userData.keyframes || [])].sort((a, b) => a.frame - b.frame);
                                    const origScaleOut = pickedHandle.keyObj.tangentOutScale;
                                    const origScaleIn = pickedHandle.keyObj.tangentInScale;
                                    const origOffOut = pickedHandle.keyObj.tangentOutOffset;
                                    const origOffIn = pickedHandle.keyObj.tangentInOffset;
                                    pickedHandle.keyObj.tangentOutScale = 1.0;
                                    pickedHandle.keyObj.tangentInScale = 1.0;
                                    pickedHandle.keyObj.tangentOutOffset = [0, 0, 0];
                                    pickedHandle.keyObj.tangentInOffset = [0, 0, 0];
                                    const defSeg = computeSegment3DBezierControls(sortedKeys, pickedHandle.segIdx);
                                    pickedHandle.keyObj.tangentOutScale = origScaleOut;
                                    pickedHandle.keyObj.tangentInScale = origScaleIn;
                                    pickedHandle.keyObj.tangentOutOffset = origOffOut;
                                    pickedHandle.keyObj.tangentInOffset = origOffIn;

                                    const defTip = pickedHandle.role === "out" ? defSeg.c0 : defSeg.c1;
                                    const offsetVec = new THREE.Vector3().subVectors(hitPt, defTip);
                                    const defLen = Math.max(0.01, pickedHandle.anchorPos.distanceTo(defTip));
                                    const newLen = pickedHandle.anchorPos.distanceTo(hitPt);
                                    const stretchRatio = Math.max(0.15, Math.min(3.5, newLen / defLen));
                                    const stretch = 0.333 * stretchRatio;
                                    if (pickedHandle.role === "out") {
                                        pickedHandle.keyObj.tangentOutScale = 1.0;
                                        pickedHandle.keyObj.tangentOutOffset = [offsetVec.x, offsetVec.y, offsetVec.z];
                                        pickedHandle.keyObj.handleOut = { dx: Math.max(0.05, Math.min(0.95, stretch)), dy: Math.max(0.05, Math.min(0.95, stretch)) };
                                    } else {
                                        pickedHandle.keyObj.tangentInScale = 1.0;
                                        pickedHandle.keyObj.tangentInOffset = [offsetVec.x, offsetVec.y, offsetVec.z];
                                        pickedHandle.keyObj.handleIn = { dx: -Math.max(0.05, Math.min(0.95, stretch)), dy: -Math.max(0.05, Math.min(0.95, stretch)) };
                                    }
                                }
                            }
                        }
                        buildCameraTrajectoryPath();
                        evaluateAnimation();
                        monitorNeedsRender = true;
                        renderStudioViewports();
                    };

                    const onUpHandle3D = () => {
                        window.removeEventListener("pointermove", onMoveHandle3D);
                        window.removeEventListener("pointerup", onUpHandle3D);
                        syncSceneData();
                        notifyDetailsAndOutliner();
                    };
                    window.addEventListener("pointermove", onMoveHandle3D);
                    window.addEventListener("pointerup", onUpHandle3D);
                    return;
                }
            }
        }
    });

    window.addEventListener("pointermove", (e) => {
        if (!ueMouseMode || (activeCamera !== flyCamera && activeCamera !== orthoCamera) || transformControls.dragging) return;
        const dx = e.clientX - lastMouseX;
        const dy = e.clientY - lastMouseY;
        lastMouseX = e.clientX;
        lastMouseY = e.clientY;
        if (dx === 0 && dy === 0) return;

        const lookSens = 0.0032;

        if (ueMouseMode === "rmb_look") {
            if (isOrthoMode) {
                isOrthoMode = false;
                activeCamera = flyCamera;
                if (transformControls) transformControls.camera = flyCamera;
                currentActiveView = "Persp";
                updateActiveViewLabel("Persp");
            }
            // RMB Free-Look: rotate camera in place (Yaw around Z, Pitch up/down)
            camYaw -= dx * lookSens;
            camPitch -= dy * lookSens;
            applyYawPitchToCamera();
        } else if (ueMouseMode === "mmb_pan") {
            // MMB Pan: move camera right/left and up/down in camera plane
            const cam = activeCamera;
            const forward = new THREE.Vector3();
            cam.getWorldDirection(forward);
            const right = new THREE.Vector3().crossVectors(forward, cam.up).normalize();
            const upLocal = new THREE.Vector3().crossVectors(right, forward).normalize();
            const panSpeed = isOrthoMode
                ? (orthoFrustumSize / Math.max(1, canvasHost ? canvasHost.clientHeight : 800))
                : (0.0085 * Math.max(0.3, flySpeedMultiplier));
            cam.position.addScaledVector(right, -dx * panSpeed);
            cam.position.addScaledVector(upLocal, dy * panSpeed);
            camPivotTarget.addScaledVector(right, -dx * panSpeed);
            camPivotTarget.addScaledVector(upLocal, dy * panSpeed);
            if (!isOrthoMode) {
                applyYawPitchToCamera();
            }
            monitorNeedsRender = true;
            renderStudioViewports();
        } else if (ueMouseMode === "alt_orbit") {
            if (isOrthoMode) {
                isOrthoMode = false;
                activeCamera = flyCamera;
                if (transformControls) transformControls.camera = flyCamera;
                currentActiveView = "Persp";
                updateActiveViewLabel("Persp");
            }
            // Alt + LMB: Orbit around selected object / pivot
            const offset = new THREE.Vector3().subVectors(flyCamera.position, camPivotTarget);
            const radius = Math.max(0.3, offset.length());
            camYaw -= dx * lookSens * 1.15;
            camPitch -= dy * lookSens * 1.15;
            const maxPitch = THREE.MathUtils.degToRad(89.0);
            camPitch = Math.max(-maxPitch, Math.min(maxPitch, camPitch));
            const cosP = Math.cos(camPitch);
            const lookDir = new THREE.Vector3(
                cosP * Math.cos(camYaw),
                cosP * Math.sin(camYaw),
                Math.sin(camPitch)
            );
            flyCamera.position.copy(camPivotTarget).addScaledVector(lookDir, -radius);
            applyYawPitchToCamera();
        }
    });

    window.addEventListener("pointerup", (e) => {
        if (e.button === 2) {
            isRmbHeld = false;
            wasdKeys.forward = wasdKeys.backward = wasdKeys.left = wasdKeys.right = wasdKeys.up = wasdKeys.down = wasdKeys.boost = false;
        }
        if (ueMouseMode) {
            ueMouseMode = null;
            renderer.domElement.style.cursor = "default";
            try { renderer.domElement.releasePointerCapture(e.pointerId); } catch (_) {}
            saveFlyCameraPose();
        }
    });

    renderer.domElement.addEventListener("wheel", (e) => {
        if (activeCamera !== flyCamera && activeCamera !== orthoCamera) return;
        const target = getPointerTarget(e);
        if (!target.cam || target.isShotCam) return;
        e.preventDefault();

        if (isOrthoMode) {
            const factor = e.deltaY < 0 ? 0.88 : (1 / 0.88);
            orthoFrustumSize = Math.max(0.2, Math.min(500.0, orthoFrustumSize * factor));
            updateOrthoCameraFrustum();
            monitorNeedsRender = true;
            renderStudioViewports();
            return;
        }

        if (isRmbHeld || ueMouseMode === "rmb_look") {
            // RMB + Scroll Wheel: dynamically adjust WASD Fly Speed
            const factor = e.deltaY < 0 ? 1.18 : (1 / 1.18);
            flySpeedMultiplier = Math.max(0.05, Math.min(8.0, Number((flySpeedMultiplier * factor).toFixed(2))));
            localStorage.setItem("vfx_ue_fly_speed", String(flySpeedMultiplier));
            if (typeof window.__vfxOnUeHudChanged === "function") {
                window.__vfxOnUeHudChanged({ flySpeedMultiplier, cloudPointSize });
            }
            return;
        }

        // Standard Scroll Wheel: smooth Dolly along view direction
        const dir = new THREE.Vector3();
        flyCamera.getWorldDirection(dir);
        const dollyStep = (e.deltaY < 0 ? 1 : -1) * 0.55 * Math.max(0.25, flySpeedMultiplier);
        flyCamera.position.addScaledVector(dir, dollyStep);
        applyYawPitchToCamera();
    }, { passive: false });

    // =========================================================================
    // 3D VIEWPORT DRAG & DROP HANDLER
    // Allows dragging 3D meshes (TRELLIS.2 / GLB), scenes, and models from the
    // top gallery strip directly into the 3D viewport!
    // - Dropping onto an existing Actor (e.g. Proxy Cube): replaces it in-place
    // - Dropping onto empty space / floor: places new 3D model at cursor location
    // - Dropping an image onto an Actor: applies image as texture
    // =========================================================================
    const handle3DViewportDragOver = (e) => {
        e.preventDefault();
        if (e.dataTransfer) {
            e.dataTransfer.dropEffect = "copy";
        }
        if (renderer && renderer.domElement) {
            renderer.domElement.style.outline = "2px dashed rgba(255, 255, 255, 0.45)";
            renderer.domElement.style.outlineOffset = "-2px";
        }
    };

    const handle3DViewportDragLeave = (e) => {
        if (renderer && renderer.domElement) {
            renderer.domElement.style.outline = "";
            renderer.domElement.style.outlineOffset = "";
        }
    };

    const handle3DViewportDrop = async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (renderer && renderer.domElement) {
            renderer.domElement.style.outline = "";
            renderer.domElement.style.outlineOffset = "";
        }

        const raw = e.dataTransfer.getData("application/vfx-media-item") || e.dataTransfer.getData("application/vfx-ref");
        let payload = null;
        if (raw) {
            try { payload = JSON.parse(raw); } catch (_) {}
        }

        const rect = renderer.domElement.getBoundingClientRect();
        const mouseX = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        const mouseY = -((e.clientY - rect.top) / rect.height) * 2 + 1;

        const raycaster = new THREE.Raycaster();
        const cam = activeCamera || flyCamera;
        raycaster.setFromCamera(new THREE.Vector2(mouseX, mouseY), cam);

        // Raycast against existing 3D scene objects
        const interactable = [...threeObjectsMap.values()].filter(o => o.visible !== false);
        const hits = raycaster.intersectObjects(interactable, true);

        let hitActor = null;
        let planeHitPoint = null;
        if (hits.length > 0) {
            for (const h of hits) {
                if (h.object && (h.object.isLine || h.object.isLineSegments || h.object.name === "__vfx_proxy_cage")) {
                    continue;
                }
                let cur = h.object;
                let foundActor = null;
                while (cur && cur !== scene) {
                    if (cur.userData && cur.userData.id && threeObjectsMap.has(cur.userData.id)) {
                        foundActor = cur;
                        break;
                    }
                    cur = cur.parent;
                }
                if (foundActor) {
                    const fType = foundActor.userData.type || "";
                    const isFloorPlane = fType === "plane" || /^ground$/i.test(foundActor.userData.name || "");
                    if (isFloorPlane) {
                        if (!planeHitPoint) planeHitPoint = h.point.clone();
                        continue;
                    }
                    if (fType === "camera") {
                        continue;
                    }
                    hitActor = foundActor;
                    break;
                }
            }
        }

        const existingReplaceableActors = [...threeObjectsMap.values()].filter(o => {
            const t = o?.userData?.type || "";
            const isFloor = t === "plane" || /^ground$/i.test(o?.userData?.name || "");
            return o && o.visible !== false && !isFloor && t !== "camera";
        });
        const targetActorForModal = hitActor
            || (selectedObject && existingReplaceableActors.includes(selectedObject) ? selectedObject : null)
            || (existingReplaceableActors.length > 0 ? existingReplaceableActors[existingReplaceableActors.length - 1] : null);

        const computeDropGroundPosition = () => {
            let pos = null;
            if (planeHitPoint) {
                pos = new THREE.Vector3(planeHitPoint.x, planeHitPoint.y, Math.max(0.0, planeHitPoint.z) + 0.6);
            } else {
                const groundPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
                const dropPos = new THREE.Vector3();
                const hitG = raycaster.ray.intersectPlane(groundPlane, dropPos);
                if (!hitG) {
                    if (targetActorForModal) {
                        pos = new THREE.Vector3(targetActorForModal.position.x + 1.6, targetActorForModal.position.y, Math.max(0.6, targetActorForModal.position.z));
                    } else {
                        pos = new THREE.Vector3(0, 0, 0.6);
                    }
                } else {
                    pos = new THREE.Vector3(dropPos.x, dropPos.y, Math.max(0.0, dropPos.z) + 0.6);
                }
            }
            // Ensure new placed model doesn't sit on the exact same XY spot as an existing model
            for (const ex of existingReplaceableActors) {
                const dx = pos.x - ex.position.x;
                const dy = pos.y - ex.position.y;
                if (Math.hypot(dx, dy) < 0.75) {
                    pos.x = ex.position.x + 1.6;
                }
            }
            return pos;
        };

        const showDrop3DChoiceModal = ({ meshTitle, oldName, onPlaceNew, onReplace }) => {
            const oldModal = document.getElementById("vfx-drop-3d-choice-modal");
            if (oldModal) oldModal.remove();

            const modal = document.createElement("div");
            modal.id = "vfx-drop-3d-choice-modal";
            modal.style.cssText = `
                position: fixed;
                inset: 0;
                z-index: 100050;
                background: rgba(0, 0, 0, 0.75);
                backdrop-filter: blur(8px);
                -webkit-backdrop-filter: blur(8px);
                display: flex;
                align-items: center;
                justify-content: center;
                user-select: none;
                padding: 16px;
                box-sizing: border-box;
            `;
            modal.innerHTML = `
                <div style="background: #141418; border: 1px solid rgba(255, 255, 255, 0.16); border-radius: 8px; width: 420px; max-width: 95vw; box-shadow: 0 24px 64px rgba(0,0,0,0.9); overflow: hidden; display: flex; flex-direction: column;">
                    <div style="padding: 12px 16px; background: rgba(255, 255, 255, 0.04); border-bottom: 1px solid rgba(255, 255, 255, 0.08); display: flex; align-items: center; justify-content: space-between;">
                        <span style="font-weight: 600; font-size: 12px; color: #ffffff; display: flex; align-items: center; gap: 7px;">
                            <i data-lucide="box" style="width: 14px; height: 14px; color: #38bdf8;"></i>
                            <span>${uiText("Place or Replace 3D Model", "Поместить или заменить 3D-модель")}</span>
                        </span>
                        <button class="vfx-btn" id="btn-drop3d-close" style="background: transparent; border: none; color: rgba(255,255,255,0.5); font-size: 13px; cursor: pointer; padding: 2px 6px;">✕</button>
                    </div>
                    <div style="padding: 16px; font-size: 12px; color: rgba(255, 255, 255, 0.88); line-height: 1.5;">
                        ${uiText(
                            `The cursor is over existing object <b>"${oldName}"</b>.<br>What would you like to do with <b>"${meshTitle}"</b>?`,
                            `Под курсором находится объект <b>«${oldName}»</b>.<br>Что сделать с перетаскиваемой моделью <b>«${meshTitle}»</b>?`
                        )}
                    </div>
                    <div style="padding: 10px 16px; background: rgba(0, 0, 0, 0.3); border-top: 1px solid rgba(255, 255, 255, 0.08); display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap;">
                        <button class="vfx-btn" id="btn-drop3d-cancel" style="padding: 6px 12px; font-size: 11px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.14); color: rgba(255,255,255,0.8); border-radius: 4px; cursor: pointer;">
                            ${uiText("Cancel", "Отмена")}
                        </button>
                        <button class="vfx-btn" id="btn-drop3d-replace" style="padding: 6px 14px; font-size: 11px; font-weight: 600; background: rgba(245, 158, 11, 0.18); border: 1px solid rgba(245, 158, 11, 0.55); color: #fcd34d; border-radius: 4px; cursor: pointer;">
                            🎯 ${uiText(`Replace "${oldName}"`, `Заменить «${oldName}»`)}
                        </button>
                        <button class="vfx-btn vfx-btn-primary" id="btn-drop3d-place" style="padding: 6px 14px; font-size: 11px; font-weight: 600; background: rgba(16, 185, 129, 0.25); border: 1px solid rgba(16, 185, 129, 0.65); color: #6ee7b7; border-radius: 4px; cursor: pointer;">
                            ➕ ${uiText("Place on Level", "Поместить рядом")}
                        </button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            if (window.lucide) { try { window.lucide.createIcons(); } catch (_) {} }

            const close = () => modal.remove();
            modal.querySelector("#btn-drop3d-close").onclick = close;
            modal.querySelector("#btn-drop3d-cancel").onclick = close;
            modal.onclick = (ev) => { if (ev.target === modal) close(); };
            modal.querySelector("#btn-drop3d-place").onclick = async () => {
                close();
                if (typeof onPlaceNew === "function") await onPlaceNew();
            };
            modal.querySelector("#btn-drop3d-replace").onclick = async () => {
                close();
                if (typeof onReplace === "function") await onReplace();
            };
        };

        // Case 1: 3D generated mesh or 3D scene from gallery strip
        const glbUrl = payload ? (payload.glbUrl || payload.url || (payload.filename ? `/vfx/mesh3d/file/${payload.filename}` : "")) : "";
        const is3D = payload && (
            payload.mediaType === "3d-scene" ||
            payload.type === "3d-scene" ||
            payload.mode === "mesh" ||
            /\.(glb|gltf|obj|fbx)(\?|$)/i.test(glbUrl) ||
            /\.(glb|gltf|obj|fbx)(\?|$)/i.test(payload.filename || "")
        );

        if (is3D && glbUrl) {
            const meshTitle = payload.title || payload.filename || "3D_Mesh";
            const spawnNewAtDrop = async () => {
                await replaceObjectWithGLB("__spawn_new__", glbUrl, {
                    name: meshTitle,
                    glbFilename: payload.filename || meshTitle,
                    engineUsed: payload.engine || "TRELLIS.2",
                    textures: payload.textures || {},
                    fitMode: "native"
                });
                if (typeof window.showToastNotification === "function") {
                    window.showToastNotification(uiText(
                        `3D model "${meshTitle}" placed into scene!`,
                        `3D модель "${meshTitle}" добавлена в сцену!`
                    ));
                }
                renderStudioViewports();
            };

            if (targetActorForModal) {
                const targetId = targetActorForModal.userData.id;
                const oldName = targetActorForModal.userData.name || targetId;
                showDrop3DChoiceModal({
                    meshTitle,
                    oldName,
                    onPlaceNew: spawnNewAtDrop,
                    onReplace: async () => {
                        await replaceObjectWithGLB(targetId, glbUrl, {
                            name: meshTitle,
                            glbFilename: payload.filename || meshTitle,
                            engineUsed: payload.engine || "TRELLIS.2",
                            textures: payload.textures || {}
                        });
                        if (typeof window.showToastNotification === "function") {
                            window.showToastNotification(uiText(
                                `Replaced "${oldName}" with 3D model "${meshTitle}"!`,
                                `Объект "${oldName}" заменен на 3D модель "${meshTitle}"!`
                            ));
                        }
                        renderStudioViewports();
                    }
                });
            } else {
                await spawnNewAtDrop();
            }
            return;
        }

        // Case 2: Local 3D files dragged from OS file explorer
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            const file = e.dataTransfer.files[0];
            if (/\.(glb|gltf|obj)$/i.test(file.name)) {
                try {
                    if (typeof uploadToProject === "function") {
                        const uploadedUrl = await uploadToProject(file);
                        const cleanName = file.name.replace(/\.[^/.]+$/, "");
                        const spawnExplorerFileNew = async () => {
                            await replaceObjectWithGLB("__spawn_new__", uploadedUrl, {
                                name: cleanName,
                                glbFilename: file.name,
                                fitMode: "native"
                            });
                            if (typeof window.showToastNotification === "function") {
                                window.showToastNotification(uiText(`Imported 3D model "${file.name}"!`, `3D модель "${file.name}" загружена в сцену!`));
                            }
                            renderStudioViewports();
                        };
                        if (targetActorForModal) {
                            const targetId = targetActorForModal.userData.id;
                            const oldName = targetActorForModal.userData.name || targetId;
                            showDrop3DChoiceModal({
                                meshTitle: cleanName,
                                oldName,
                                onPlaceNew: spawnExplorerFileNew,
                                onReplace: async () => {
                                    await replaceObjectWithGLB(targetId, uploadedUrl, { name: cleanName, glbFilename: file.name });
                                    if (typeof window.showToastNotification === "function") {
                                        window.showToastNotification(uiText(`Replaced "${oldName}" with "${file.name}"!`, `Объект "${oldName}" заменен на "${file.name}"!`));
                                    }
                                    renderStudioViewports();
                                }
                            });
                        } else {
                            await spawnExplorerFileNew();
                        }
                    }
                } catch (err) {
                    if (typeof window.showToastNotification === "function") window.showToastNotification(`❌ ${err.message}`, true);
                }
                return;
            }
        }

        // Case 3: Dropped image or video onto a 3D Actor or Actor Cutout Billboard
        const isMedia = payload && (payload.mediaType === "1d-image" || payload.type === "1d-image" || payload.type === "video" || payload.mediaType === "video" || /\.(png|jpe?g|webp|mp4|webm|mov|mkv)(\?|$)/i.test(payload.url || ""));
        if (isMedia) {
            const mUrl = payload.url || payload.fallbackUrl;
            if (mUrl) {
                const isVid = payload.type === "video" || payload.mediaType === "video" || isVideoMedia(mUrl, payload.filename || payload.title);
                if (hitActor === actorBillboard || (isActorProjectionVisible && !hitActor)) {
                    setCustomActorCutout(mUrl, payload.filename || payload.title || (isVid ? "video.mp4" : "actor.png"), isVid ? "video" : "image");
                    if (typeof window.showToastNotification === "function") {
                        window.showToastNotification(uiText(`Actor Cutout ${isVid ? 'video' : 'image'} set!`, `Кастомный ${isVid ? 'видео' : 'изображение'}-эктор установлен!`));
                    }
                    renderStudioViewports();
                    return;
                }
                if (hitActor && typeof applyTextureToObject === "function") {
                    applyTextureToObject(hitActor.userData.id, mUrl, true);
                    if (typeof window.showToastNotification === "function") {
                        window.showToastNotification(uiText(
                            `Texture applied to "${hitActor.userData.name || hitActor.userData.id}"!`,
                            `Текстура наложена на "${hitActor.userData.name || hitActor.userData.id}"!`
                        ));
                    }
                    renderStudioViewports();
                    return;
                }
            }
        }
    };

    renderer.domElement.addEventListener("dragover", handle3DViewportDragOver);
    renderer.domElement.addEventListener("dragleave", handle3DViewportDragLeave);
    renderer.domElement.addEventListener("drop", handle3DViewportDrop);

    if (canvasHost) {
        canvasHost.addEventListener("dragover", handle3DViewportDragOver);
        canvasHost.addEventListener("dragleave", handle3DViewportDragLeave);
        canvasHost.addEventListener("drop", handle3DViewportDrop);
    }

    // Cached coordinate resolution for scissored viewports (prevents per-frame / per-pointermove DOM reflow)
    let cachedCanvasRect = null;
    let cachedRightColRect = null;
    let cachedGateRect = null;

    function refreshViewportRectsCache(force = false) {
        if (!force && cachedCanvasRect && cachedCanvasRect.width > 0 && cachedGateRect && cachedGateRect.width > 0) return;
        cachedCanvasRect = renderer.domElement.getBoundingClientRect();
        cachedRightColRect = (studioRightCol && studioRightCol.offsetParent !== null) ? studioRightCol.getBoundingClientRect() : null;
        cachedGateRect = (filmGateBox && filmGateBox.offsetParent !== null) ? filmGateBox.getBoundingClientRect() : null;
    }

    function invalidateViewportRectsCache() {
        cachedCanvasRect = null;
        cachedRightColRect = null;
        cachedGateRect = null;
    }
    window.__vfxInvalidateViewportCache = invalidateViewportRectsCache;

    window.addEventListener("resize", () => {
        invalidateViewportRectsCache();
        monitorNeedsRender = true;
    });

    function getMainViewportWidth(fullW) {
        const total = fullW || (canvasHost ? canvasHost.clientWidth : (renderer ? renderer.domElement.clientWidth : window.innerWidth));
        if (!studioRightCol || studioRightCol.offsetParent === null || studioRightCol.offsetWidth <= 0) {
            return total;
        }
        const rightW = studioRightCol.offsetWidth;
        return Math.max(10, Math.min(total, total - rightW));
    }

    function getPointerTarget(e) {
        refreshViewportRectsCache(false);
        const gateRect = cachedGateRect;

        const clientX = e.clientX;
        const clientY = e.clientY;

        if (gateRect && gateRect.width > 0 && clientX >= gateRect.left && clientX <= gateRect.right && clientY >= gateRect.top && clientY <= gateRect.bottom) {
            const ndcX = ((clientX - gateRect.left) / gateRect.width) * 2 - 1;
            const ndcY = -(((clientY - gateRect.top) / gateRect.height) * 2 - 1);
            return { cam: shotCamera, ndc: new THREE.Vector2(ndcX, ndcY), isShotCam: true };
        }

        const canvasRect = cachedCanvasRect || renderer.domElement.getBoundingClientRect();
        const vpW = getMainViewportWidth(canvasRect.width);
        if (vpW > 0 && clientX >= canvasRect.left && clientX <= canvasRect.left + vpW && clientY >= canvasRect.top && clientY <= canvasRect.bottom) {
            const ndcX = ((clientX - canvasRect.left) / vpW) * 2 - 1;
            const ndcY = -(((clientY - canvasRect.top) / canvasRect.height) * 2 - 1);
            return { cam: activeCamera, ndc: new THREE.Vector2(ndcX, ndcY), isShotCam: false };
        }

        return { cam: null, ndc: null, isShotCam: false };
    }

    transformControls._getPointer = function (event) {
        // When user is actively flying/rotating/panning the viewport camera, skip gizmo hover raycasting completely!
        if (ueMouseMode !== null || isRmbHeld) {
            return { x: -999, y: -999, button: event.button !== undefined ? event.button : 0 };
        }
        const pointer = event.changedTouches ? event.changedTouches[0] : event;
        const { cam, ndc } = getPointerTarget(pointer);
        if (cam && transformControls.camera !== cam) {
            transformControls.camera = cam;
        }
        if (ndc) {
            return { x: ndc.x, y: ndc.y, button: event.button !== undefined ? event.button : 0 };
        }
        return { x: -999, y: -999, button: event.button !== undefined ? event.button : 0 };
    };

    let lastInspectorThrottleTs = 0;

    transformControls.addEventListener("dragging-changed", (e) => {
        if (e.value) {
            ueMouseMode = null;
            const targetObj = transformControls.object;
            if (targetObj && targetObj.userData && targetObj.userData.isBaked) {
                if (typeof window.__vfxShowConfirmModal === "function") {
                    window.__vfxShowConfirmModal({
                        title: uiText("Unbake Camera Trajectory?", "Распечь траекторию камеры?"),
                        message: uiText("The camera trajectory is currently baked from 3D tracking data. Modifying it will unbake the motion path into editable keyframes. Do you want to proceed?", "Траектория камеры запечена из трекинга. Изменение преобразует путь в редактируемые ключевые кадры. Продолжить?"),
                        icon: "unlock",
                        iconColor: "#eab308",
                        confirmText: uiText("Unbake & Edit", "Распечь и редактировать"),
                        confirmBg: "#ca8a04",
                        confirmBorder: "#eab308",
                        onConfirm: () => {
                            targetObj.userData.isBaked = false;
                            pushUndoState("Unbake Camera Trajectory");
                            logMsg("Camera trajectory unbaked into editable keyframes.");
                        }
                    });
                }
            } else {
                pushUndoState("Transform Object");
            }
        } else {
            const targetObj = transformControls.object;
            if (targetObj && autoKeyEnabled && Array.isArray(targetObj.userData?.keyframes) && targetObj.userData.keyframes.length > 0) {
                set3DTransformKeyframe(targetObj, currentFrame, true);
            } else if (targetObj && (targetObj.userData?.type === "camera" || targetObj === shotCamera) && Array.isArray(targetObj.userData?.keyframes) && targetObj.userData.keyframes.length >= 2) {
                buildCameraTrajectoryPath();
            }
            syncSceneData();
            updateTransformInspector(true);
            monitorNeedsRender = true;
        }
    });

    transformControls.addEventListener("objectChange", () => {
        const obj = transformControls.object;
        if (obj) {
            obj.updateMatrixWorld(true);
            if (obj === shotCamera) {
                syncShotCameraVisuals(true);
            } else if (obj.userData && obj.userData.camera) {
                obj.userData.camera.updateMatrixWorld(true);
                if (obj.userData.cameraHelper) {
                    obj.userData.cameraHelper.matrixWorld.copy(obj.userData.camera.matrixWorld);
                }
            }
            monitorNeedsRender = true;
            const now = performance.now();
            if (now - lastInspectorThrottleTs > 40) {
                lastInspectorThrottleTs = now;
                updateTransformInspector(false);
            }
        }
    });

    // Object Management & Details Inspector Sync
    const threeObjectsMap = new Map();
    let nextObjId = 1;
    // ids restored from a saved scene ("obj_1") must never be handed out again to new objects
    function freshObjId(wanted) {
        if (wanted) {
            const n = /^obj_(\d+)$/.exec(wanted);
            if (n) nextObjId = Math.max(nextObjId, Number(n[1]) + 1);
            return wanted;
        }
        let id;
        do { id = `obj_${nextObjId++}`; } while (threeObjectsMap.has(id));
        return id;
    }

    function getActiveMonitorCameraActor() {
        if (selectedObject && selectedObject.userData && selectedObject.userData.type === "camera" && selectedObject.userData.camera) {
            if (selectedObject === shotCamera) {
                return !isShotCameraDeleted ? shotCamera : null;
            }
            return selectedObject;
        }
        if (lastActiveFreeCamGroup && threeObjectsMap.has(lastActiveFreeCamGroup.userData?.id)) {
            return lastActiveFreeCamGroup;
        }
        for (const obj of threeObjectsMap.values()) {
            if (obj.userData && obj.userData.type === "camera" && obj.userData.camera) {
                lastActiveFreeCamGroup = obj;
                return obj;
            }
        }
        if (!isShotCameraDeleted) {
            return shotCamera;
        }
        return null;
    }

    function getActiveMonitorCamera() {
        const actor = getActiveMonitorCameraActor();
        if (!actor) return null;
        return actor === shotCamera ? shotCamera : (actor.userData?.camera || null);
    }

    function applyCameraOptics(camActor, updateHud = true) {
        if (!camActor || !camActor.userData || camActor.userData.type !== "camera") return;
        const ud = camActor.userData;
        const cam = camActor === shotCamera ? shotCamera : ud.camera;
        if (!cam) return;

        const focalMm = Math.max(8, Math.min(300, Number(ud.focalMm) || 35));
        const sensorW = Math.max(4, Math.min(100, Number(ud.sensorWidthMm) || 36.0));
        const sensorH = Math.max(3, Math.min(80, Number(ud.sensorHeightMm) || 24.0));
        const imgW = Math.max(256, Math.min(8192, Math.round(Number(ud.imageWidth) || 1920)));
        const imgH = Math.max(256, Math.min(8192, Math.round(Number(ud.imageHeight) || 1080)));
        const squeeze = Math.max(1.0, Math.min(2.5, Number(ud.anamorphicSqueeze) || 1.0));
        const fStop = Math.max(0.7, Math.min(64, Number(ud.apertureFStop) || 2.8));
        const focusDist = Math.max(0.1, Math.min(1000, Number(ud.focusDistanceM) || 3.5));

        ud.focalMm = focalMm;
        ud.sensorWidthMm = sensorW;
        ud.sensorHeightMm = sensorH;
        ud.imageWidth = imgW;
        ud.imageHeight = imgH;
        ud.anamorphicSqueeze = squeeze;
        ud.apertureFStop = fStop;
        ud.focusDistanceM = focusDist;

        // Vertical FOV: the sensor WIDTH is the reference (as in the tracker: focal_mm = fx / width * 36),
        // the height that is actually exposed follows the image aspect
        const exposedH = sensorW / Math.max(0.1, imgW / imgH);
        ud.sensorHeightMm = +exposedH.toFixed(2);
        const vFovDeg = 2.0 * Math.atan(exposedH / (2.0 * focalMm)) * (180.0 / Math.PI);
        cam.fov = vFovDeg;
        const aspect = (imgW / imgH) * squeeze;
        cam.aspect = aspect;
        cam.updateProjectionMatrix();

        updateCameraFrustumHelper(camActor);
        monitorNeedsRender = true;

        // If this camera is the active monitor camera, sync plateAspect & monitor HUD
        const activeMonActor = getActiveMonitorCameraActor();
        if (updateHud && activeMonActor === camActor) {
            plateAspect = aspect;
            const lensInfo = document.getElementById("monitor-lens-info");
            if (lensInfo) {
                const distMode = ud.distortionMode || "off";
                const distTag = distMode !== "off" ? ` • Dist(${distMode === "apply" ? "+" : "−"}): k1=${Number(ud.distortionK1 || 0).toFixed(2)}` : "";
                lensInfo.innerText = `${Math.round(focalMm)}mm • f/${fStop.toFixed(1)} • ${sensorW.toFixed(1)}×${sensorH.toFixed(1)}mm • ${imgW}×${imgH}${distTag}`;
            }
            const aspectBadge = document.getElementById("monitor-aspect-badge");
            if (aspectBadge) {
                aspectBadge.innerText = `${imgW}×${imgH} (${aspect.toFixed(2)}:1)`;
            }
            const stCam = document.getElementById("st-cam-label");
            if (stCam) {
                stCam.innerText = `${ud.name || "Cam"}: ${Math.round(focalMm)}mm f/${fStop.toFixed(1)}`;
            }
        }
    }

    function ensureActorUserData(obj, defaults = {}) {
        const ud = obj.userData || (obj.userData = {});
        if (!ud.id) ud.id = freshObjId(defaults.id);
        if (!ud.name) ud.name = defaults.name || "Actor";
        if (!ud.type) ud.type = defaults.type || "box";
        if (!Array.isArray(ud.keyframes)) ud.keyframes = defaults.keyframes || [];
        if (ud.description === undefined) ud.description = defaults.description || "";
        if (ud.passRole === undefined) ud.passRole = defaults.passRole || "layout_depth";
        if (ud.glbUrl === undefined) ud.glbUrl = defaults.glbUrl || "";
        if (ud.meshEngine === undefined) ud.meshEngine = defaults.meshEngine || "";
        if (ud.assignedImage === undefined) ud.assignedImage = defaults.assignedImage || "";
        if (ud.assignedCharacter === undefined) ud.assignedCharacter = defaults.assignedCharacter || "";
        if (ud.assignedAudio === undefined) ud.assignedAudio = defaults.assignedAudio || "";
        if (ud.fitMode === undefined) ud.fitMode = defaults.fitMode || (ud.type === "glb" ? "native" : "stretch");
        if (ud.pivotOffset === undefined && Array.isArray(defaults.pivotOffset) && defaults.pivotOffset.length === 3) {
            ud.pivotOffset = [
                Number(defaults.pivotOffset[0]) || 0,
                Number(defaults.pivotOffset[1]) || 0,
                Number(defaults.pivotOffset[2]) || 0
            ];
        }
        if (ud.color === undefined) {
            ud.color = (obj.material && obj.material.color) ? `#${obj.material.color.getHexString()}` : (defaults.color || "#cccccc");
        }
        if (defaults.camera !== undefined) ud.camera = defaults.camera;
        if (defaults.cameraHelper !== undefined) ud.cameraHelper = defaults.cameraHelper;
        if (defaults.isBaked !== undefined) ud.isBaked = defaults.isBaked;
        if (ud.type === "character") Object.keys(defaults).forEach(k => { if (ud[k] === undefined) ud[k] = defaults[k]; });

        if (ud.type === "camera") {
            if (ud.focalMm === undefined) ud.focalMm = defaults.focalMm ?? 35;
            if (ud.sensorPreset === undefined) ud.sensorPreset = defaults.sensorPreset ?? "full_frame";
            if (ud.sensorWidthMm === undefined) ud.sensorWidthMm = defaults.sensorWidthMm ?? 36.0;
            if (ud.sensorHeightMm === undefined) ud.sensorHeightMm = defaults.sensorHeightMm ?? 24.0;
            if (ud.imageWidth === undefined) ud.imageWidth = defaults.imageWidth ?? (plateWidth || 1920);
            if (ud.imageHeight === undefined) ud.imageHeight = defaults.imageHeight ?? (plateHeight || 1080);
            if (ud.apertureFStop === undefined) ud.apertureFStop = defaults.apertureFStop ?? 2.8;
            if (ud.focusDistanceM === undefined) ud.focusDistanceM = defaults.focusDistanceM ?? 3.5;
            if (ud.shutterAngleDeg === undefined) ud.shutterAngleDeg = defaults.shutterAngleDeg ?? 180;
            if (ud.anamorphicSqueeze === undefined) ud.anamorphicSqueeze = defaults.anamorphicSqueeze ?? 1.0;
            if (ud.distortionMode === undefined) ud.distortionMode = defaults.distortionMode ?? "off";
            if (ud.distortionK1 === undefined) ud.distortionK1 = defaults.distortionK1 ?? 0.0;
            if (ud.distortionK2 === undefined) ud.distortionK2 = defaults.distortionK2 ?? 0.0;
            if (ud.frustumRayLength === undefined) ud.frustumRayLength = defaults.frustumRayLength ?? 3.5;
            if (ud.frustumRayOpacity === undefined) ud.frustumRayOpacity = defaults.frustumRayOpacity ?? 0.75;
        }
        return ud;
    }

    function notifyDetailsAndOutliner() {
        if (typeof window.__vfxOnOutlinerChanged === "function") {
            window.__vfxOnOutlinerChanged(Array.from(threeObjectsMap.values()), selectedObject);
        }
        if (typeof window.__vfxOnSelectedObjectChanged === "function") {
            window.__vfxOnSelectedObjectChanged(selectedObject);
        }
        updateStatusBarTelemetry({
            selected: selectedObject,
            actorsCount: threeObjectsMap.size
        });
    }

    function registerObject(mesh, name = "Object", type = "mesh", extraMeta = {}) {
        const id = freshObjId(extraMeta.id);
        ensureActorUserData(mesh, { id, name, type, ...extraMeta });
        scene.add(mesh);
        threeObjectsMap.set(id, mesh);
        selectObject(mesh);
        updateOutliner();
        syncSceneData();
        notifyDetailsAndOutliner();
        return mesh;
    }

    const DISTINCT_ACTOR_COLORS = [
        "#00f0ff", // Vibrant Cyan
        "#f43f5e", // Rose Red
        "#10b981", // Emerald Green
        "#a855f7", // Purple / Violet
        "#f59e0b", // Amber / Orange
        "#3b82f6", // Royal Blue
        "#ec4899", // Pink
        "#14b8a6", // Teal
        "#eab308", // Golden Yellow
        "#06b6d4"  // Sky Blue
    ];

    const isNeutralColor = (c) => {
        if (!c) return true;
        const low = String(c).trim().toLowerCase();
        return low === "#ffffff" || low === "#fff" || low === "#cccccc" || low === "#ccc" || low === "#888888" || low === "#888" || low === "#aaaaaa" || low === "#aaa";
    };

    function getSelectedActorColors() {
        const activeActors = [];
        if (selectedObject) activeActors.push(selectedObject);
        selectedObjects.forEach(obj => {
            if (obj && !activeActors.includes(obj)) activeActors.push(obj);
        });

        // Stable ordering by ID/UUID so colors NEVER swap between objects when switching active key
        activeActors.sort((a, b) => String(a.userData?.id || a.uuid).localeCompare(String(b.userData?.id || b.uuid)));

        const actorColorMap = new Map();
        const usedColors = new Set();

        activeActors.forEach((actor, idx) => {
            let col = actor.userData?.__vfxColor;
            if (!col || isNeutralColor(col) || usedColors.has(col)) {
                if (actor.userData?.type === "camera" || actor === shotCamera) {
                    col = !usedColors.has("#ff9100") ? "#ff9100" : (!usedColors.has("#00f0ff") ? "#00f0ff" : DISTINCT_ACTOR_COLORS[idx % DISTINCT_ACTOR_COLORS.length]);
                } else {
                    col = DISTINCT_ACTOR_COLORS.find(c => !usedColors.has(c)) || DISTINCT_ACTOR_COLORS[idx % DISTINCT_ACTOR_COLORS.length];
                }
                actor.userData = actor.userData || {};
                actor.userData.__vfxColor = col;
            }
            usedColors.add(col);
            actorColorMap.set(actor, col);
        });
        return { activeActors, actorColorMap };
    }

    const selectionBoxHelpers = new Map();
    let highlightedKeyframeFrame = null;
    let highlightedKeyframeActors = new Set();
    let hoveredKeyframeFrame = null;
    let hoveredKeyframeActors = new Set();

    // Everything an object leaves in the scene besides itself (outline box, camera frustum, skeleton lines)
    function purgeObjectArtifacts(obj) {
        if (!obj) return;
        const h = selectionBoxHelpers.get(obj);
        if (h) { scene.remove(h); try { h.geometry && h.geometry.dispose(); h.material && h.material.dispose(); } catch (_) {} selectionBoxHelpers.delete(obj); }
        if (obj.userData && obj.userData.cameraHelper) scene.remove(obj.userData.cameraHelper);
        const id = obj.userData && obj.userData.id;
        try { if (id && characterRigs.has(id)) { scene.remove(characterRigs.get(id).helper); characterRigs.delete(id); } } catch (_) { /* rigs not initialised yet */ }
    }

    function updateSelectionHelpers() {
        if (!scene) return;
        const { activeActors, actorColorMap } = getSelectedActorColors();
        const activeSet = new Set(activeActors);

        // Remove helpers for actors no longer active
        selectionBoxHelpers.forEach((helper, actor) => {
            if (!activeSet.has(actor)) {
                scene.remove(helper);
                if (helper.geometry) helper.geometry.dispose();
                if (helper.material) helper.material.dispose();
                selectionBoxHelpers.delete(actor);
            }
        });

        if (activeActors.length === 0 || !window.__vfxShowBounds) {
            selectionBoxHelpers.forEach((helper) => {
                scene.remove(helper);
                if (helper.geometry) helper.geometry.dispose();
                if (helper.material) helper.material.dispose();
            });
            selectionBoxHelpers.clear();
            return;
        }

        const effectiveHighlighted = hoveredKeyframeActors.size > 0 ? hoveredKeyframeActors : highlightedKeyframeActors;
        const hasAnyHighlight = effectiveHighlighted.size > 0;

        activeActors.forEach(actor => {
            if (!actor || actor.visible === false) return;
            const colHex = actorColorMap.get(actor) || "#00f0ff";
            const threeCol = new THREE.Color(colHex);

            let helper = selectionBoxHelpers.get(actor);
            if (!helper) {
                helper = new THREE.BoxHelper(actor, threeCol);
                helper.material.depthTest = false;
                helper.material.transparent = true;
                helper.renderOrder = 998;
                scene.add(helper);
                selectionBoxHelpers.set(actor, helper);
            } else {
                helper.update();
                helper.material.color.copy(threeCol);
            }

            const isHighlighted = effectiveHighlighted.has(actor);

            if (isHighlighted) {
                // Stand out prominently when its keyframe is selected or hovered!
                helper.material.opacity = 1.0;
                helper.material.linewidth = 3;
                helper.visible = true;
            } else if (hasAnyHighlight) {
                // Dim down non-highlighted selected actors so the key's owner is immediately obvious!
                helper.material.opacity = 0.2;
                helper.material.linewidth = 1;
                helper.visible = true;
            } else {
                // Normal multi-object selection outline
                helper.material.opacity = (activeActors.length > 1) ? 0.75 : 0.6;
                helper.material.linewidth = 1.5;
                helper.visible = true;
            }
        });
    }

    window.__vfxSelectShotCamera = () => selectObject(shotCamera);
    function selectObject(obj, isMulti = false) {
        setTimeout(() => { if (typeof window.__vfxRefreshAnimClips === "function") window.__vfxRefreshAnimClips(); }, 0);
        if (isMulti && obj) {
            if (selectedObjects.has(obj)) {
                selectedObjects.delete(obj);
                if (selectedObject === obj) {
                    selectedObject = Array.from(selectedObjects)[selectedObjects.size - 1] || null;
                }
            } else {
                selectedObjects.add(obj);
                selectedObject = obj;
            }
        } else {
            selectedObject = obj || null;
            selectedObjects.clear();
            if (obj) {
                selectedObjects.add(obj);
            }
        }

        // Validate that highlighted actors are still within activeActors
        if (highlightedKeyframeActors.size > 0) {
            const hasAny = Array.from(highlightedKeyframeActors).some(a => selectedObjects.has(a) || selectedObject === a);
            if (!hasAny) {
                highlightedKeyframeActors.clear();
                highlightedKeyframeFrame = null;
            }
        }

        if (selectedObject) {
            if (selectedObject === shotCamera) {
                lastActiveFreeCamGroup = null;
            } else if (selectedObject.userData && selectedObject.userData.type === "camera") {
                lastActiveFreeCamGroup = selectedObject;
            }
            transformControls.attach(selectedObject);
        } else {
            transformControls.detach();
        }
        updateSelectionHelpers();
        updateOutliner();
        updateTransformInspector(true);
        updateKeyframeMarkers();
        const monCamActor = getActiveMonitorCameraActor();
        if (monCamActor) {
            applyCameraOptics(monCamActor, true);
        }
        renderStudioViewports();
    }

    function deleteShotCamera() {
        pushUndoState("Delete Shot Camera");
        isShotCameraDeleted = true;
        try { localStorage.setItem("vfx_3d_shot_cam_deleted", "true"); } catch (_) {}
        transformControls.detach();
        if (camBodyMesh) {
            camBodyMesh.visible = false;
        }
        if (camFrustumHelper) {
            camFrustumHelper.visible = false;
        }
        if (cameraPathLine) {
            scene.remove(cameraPathLine);
            cameraPathLine = null;
        }
        cameraMatrices = [];
        activeCamera = flyCamera;
        isShotCamActive = false;
        orbitControls.enabled = false;
        if (selectedObject === shotCamera || (selectedObject && selectedObject.userData && selectedObject.userData.id === "cam_shot")) {
            selectedObject = null;
            selectedObjects.clear();
        }
        updateOutliner();
        updateTransformInspector(true);
        syncSceneData();
        renderStudioViewports();
        if (typeof window.__vfxToast === "function") {
            window.__vfxToast("Shot Camera deleted");
        }
    }

    function deletePointCloud() {
        if (!cloudMesh) return;
        pushUndoState("Delete LiDAR Cloud");
        isLidarDeleted = true;
        try { localStorage.setItem("vfx_3d_lidar_deleted", "true"); } catch (_) {}
        transformControls.detach();
        scene.remove(cloudMesh);
        if (cloudMesh.geometry) {
            try { cloudMesh.geometry.dispose(); } catch (_) {}
        }
        if (cloudMesh.material) {
            try {
                if (Array.isArray(cloudMesh.material)) cloudMesh.material.forEach(m => m.dispose());
                else cloudMesh.material.dispose();
            } catch (_) {}
        }
        cloudMesh = null;
        if (selectedObject === cloudMesh) {
            selectedObject = null;
            selectedObjects.clear();
        }
        updateOutliner();
        updateTransformInspector(true);
        syncSceneData();
        renderStudioViewports();
        if (typeof window.__vfxToast === "function") {
            window.__vfxToast("LiDAR Point Cloud deleted");
        }
    }

    function deleteSelectedObjects() {
        // 1. Check if Shot Camera is selected
        if (selectedObject === shotCamera || (selectedObject && selectedObject.userData && selectedObject.userData.id === "cam_shot") || selectedObjects.has(shotCamera)) {
            deleteShotCamera();
            return;
        }

        // 2. Check if Point Cloud is selected
        if (cloudMesh && (selectedObject === cloudMesh || selectedObjects.has(cloudMesh))) {
            deletePointCloud();
            return;
        }

        if (selectedObjects.size === 0 && !selectedObject) return;
        pushUndoState("Delete Object");
        let deletedAnyFreeCamera = false;
        if (selectedObject && selectedObject.userData && threeObjectsMap.has(selectedObject.userData.id)) selectedObjects.add(selectedObject);
        selectedObjects.forEach(obj => {
            transformControls.detach();
            purgeObjectArtifacts(obj);
            if (obj.userData && obj.userData.type === "camera") {
                deletedAnyFreeCamera = true;
                if (lastActiveFreeCamGroup === obj) lastActiveFreeCamGroup = null;
            }
            if (obj.userData && obj.userData.cameraHelper) {
                scene.remove(obj.userData.cameraHelper);
            }
            if (obj.userData && obj.userData.camera && activeCamera === obj.userData.camera) {
                activeCamera = flyCamera;
                isShotCamActive = false;
                orbitControls.enabled = false;
            }
            scene.remove(obj);
            if (obj.geometry) {
                try { obj.geometry.dispose(); } catch (_) {}
            }
            if (obj.material) {
                try {
                    if (Array.isArray(obj.material)) obj.material.forEach(m => m.dispose());
                    else obj.material.dispose();
                } catch (_) {}
            }
            threeObjectsMap.delete(obj.userData.id);
        });
        // If user deleted a Free Camera and there is no tracked trajectory on Shot Camera,
        // ensure the untracked Shot Camera doesn't stay behind as a phantom camera.
        if (deletedAnyFreeCamera && (!cameraMatrices || cameraMatrices.length === 0)) {
            const hasRemainingFreeCam = Array.from(threeObjectsMap.values()).some(o => o.userData?.type === "camera");
            if (!hasRemainingFreeCam) {
                isShotCameraDeleted = true;
                try { localStorage.setItem("vfx_3d_shot_cam_deleted", "true"); } catch (_) {}
                if (camBodyMesh) camBodyMesh.visible = false;
                if (camFrustumHelper) camFrustumHelper.visible = false;
            }
        }
        selectedObjects.clear();
        selectedObject = null;
        updateSelectionHelpers();
        updateOutliner();
        updateTransformInspector(true);
        syncSceneData();
        renderStudioViewports();
    }

    // 3D Viewport Raycasting (Click to Select Actors & 3-Point Plane Picking)
    const raycaster = new THREE.Raycaster();
    raycaster.params.Points.threshold = 0.25;
    let pointerDownPos = { x: 0, y: 0 };
    let is3PtPlaneMode = false;
    let picked3Pts = [];
    const picked3PtMarkers = [];

    function clear3PtMarkers() {
        picked3PtMarkers.forEach(m => scene.remove(m));
        picked3PtMarkers.length = 0;
        picked3Pts = [];
    }

    renderer.domElement.addEventListener("pointerdown", (e) => {
        pointerDownPos = { x: e.clientX, y: e.clientY };
    });

    renderer.domElement.addEventListener("pointerup", (e) => {
        const dist = Math.hypot(e.clientX - pointerDownPos.x, e.clientY - pointerDownPos.y);
        if (dist > 5 || transformControls.dragging) return;

        const target = getPointerTarget(e);
        if (!target.cam || !target.ndc) return;

        raycaster.setFromCamera(target.ndc, target.cam);

        // Blender-style 3D Cursor Placement: Shift + RMB (or clicking with 3D Cursor tool)
        if ((e.button === 2 && e.shiftKey) || (e.button === 0 && is3DCursorToolActive)) {
            let placed = false;
            // 1. Raycast scene objects
            const sceneActors = Array.from(threeObjectsMap.values());
            if (sceneActors.length > 0) {
                const hits = raycaster.intersectObjects(sceneActors, true);
                if (hits.length > 0) {
                    set3DCursorPosition(hits[0].point.x, hits[0].point.y, hits[0].point.z);
                    logMsg(`3D Cursor placed on surface of ${hits[0].object.name || 'Object'}: [${hits[0].point.x.toFixed(2)}, ${hits[0].point.y.toFixed(2)}, ${hits[0].point.z.toFixed(2)}]`);
                    placed = true;
                }
            }
            // 2. Raycast point cloud if available
            if (!placed && cloudMesh) {
                const hits = raycaster.intersectObject(cloudMesh, false);
                if (hits.length > 0) {
                    set3DCursorPosition(hits[0].point.x, hits[0].point.y, hits[0].point.z);
                    logMsg(`3D Cursor placed on Point Cloud: [${hits[0].point.x.toFixed(2)}, ${hits[0].point.y.toFixed(2)}, ${hits[0].point.z.toFixed(2)}]`);
                    placed = true;
                }
            }
            // 3. Fallback to Ground Plane (Z=0 in Z-up)
            if (!placed) {
                const groundPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
                const groundHit = new THREE.Vector3();
                if (raycaster.ray.intersectPlane(groundPlane, groundHit)) {
                    set3DCursorPosition(groundHit.x, groundHit.y, groundHit.z);
                    logMsg(`3D Cursor placed on Ground Grid (Z=0): [${groundHit.x.toFixed(2)}, ${groundHit.y.toFixed(2)}, ${groundHit.z.toFixed(2)}]`);
                    placed = true;
                }
            }
            return;
        }

        if (e.button !== 0) return;

        if (is3PtPlaneMode && cloudMesh) {
            const hits = raycaster.intersectObject(cloudMesh, false);
            if (hits.length > 0) {
                const pt = hits[0].point.clone();
                picked3Pts.push(pt);
                const sphere = new THREE.Mesh(
                    new THREE.SphereGeometry(0.08, 12, 12),
                    new THREE.MeshBasicMaterial({ color: 0xffffff })
                );
                sphere.position.copy(pt);
                scene.add(sphere);
                picked3PtMarkers.push(sphere);

                const st = document.getElementById("vfx-3pt-status");
                if (st) st.innerText = `Picked (${picked3Pts.length} / 3) points`;
                if (picked3Pts.length === 3) {
                    const act = document.getElementById("vfx-3pt-actions");
                    if (act) act.style.display = "flex";
                }
            }
            return;
        }

        const sceneActors = Array.from(threeObjectsMap.values());
        if (!isShotCameraDeleted && isShotCameraVisible && camBodyMesh && camBodyMesh.visible) {
            sceneActors.push(camBodyMesh);
        }
        let hitObj = null;
        if (sceneActors.length > 0) {
            const intersects = raycaster.intersectObjects(sceneActors, true);
            if (intersects.length > 0) {
                let candidate = intersects[0].object;
                while (candidate && candidate.parent && candidate.parent !== scene) {
                    if (candidate === camBodyMesh || candidate.userData?.isShotCamProxy) break;
                    if (threeObjectsMap.has(candidate.userData?.id)) break;
                    candidate = candidate.parent;
                }
                if (candidate === camBodyMesh || candidate?.userData?.isShotCamProxy) {
                    hitObj = shotCamera;
                } else if (candidate && threeObjectsMap.has(candidate.userData?.id)) {
                    hitObj = candidate;
                }
            }
        }
        if (hitObj) {
            selectObject(hitObj, e.shiftKey || e.ctrlKey);
            logMsg(`Selected 3D Actor: ${hitObj.userData?.name || "Shot Camera"} (${hitObj.userData?.type || "camera"})`);
        } else {
            // Clicked in empty space -> Deselect unless multi-selecting
            if (!e.shiftKey && !e.ctrlKey) {
                selectObject(null);
            }
        }
    });

    // 3-Point Plane Tool Wiring
    const btn3PtTool = document.getElementById("btn-tool-3pt-plane");
    const bar3Pt = document.getElementById("vfx-3pt-picker-bar");
    if (btn3PtTool && bar3Pt) {
        btn3PtTool.onclick = () => {
            is3PtPlaneMode = !is3PtPlaneMode;
            bar3Pt.style.display = is3PtPlaneMode ? "flex" : "none";
            clear3PtMarkers();
            const st = document.getElementById("vfx-3pt-status");
            if (st) st.innerText = "Click 3 points in Point Cloud (0 / 3)";
            const act = document.getElementById("vfx-3pt-actions");
            if (act) act.style.display = "none";
        };
        const btnCancel3Pt = document.getElementById("btn-3pt-cancel");
        if (btnCancel3Pt) {
            btnCancel3Pt.onclick = () => {
                is3PtPlaneMode = false;
                bar3Pt.style.display = "none";
                clear3PtMarkers();
            };
        }
        const btnRepick3Pt = document.getElementById("btn-3pt-repick");
        if (btnRepick3Pt) {
            btnRepick3Pt.onclick = () => {
                clear3PtMarkers();
                const st = document.getElementById("vfx-3pt-status");
                if (st) st.innerText = "Click 3 points in Point Cloud (0 / 3)";
                const act = document.getElementById("vfx-3pt-actions");
                if (act) act.style.display = "none";
            };
        }
        const btnConfirm3Pt = document.getElementById("btn-3pt-confirm");
        if (btnConfirm3Pt) {
            btnConfirm3Pt.onclick = () => {
                if (picked3Pts.length < 3) return;
                pushUndoState("Create 3-Point Plane");
                const [p1, p2, p3] = picked3Pts;
                const center = new THREE.Vector3().addVectors(p1, p2).add(p3).divideScalar(3);
                const v1 = new THREE.Vector3().subVectors(p2, p1).normalize();
                const v2 = new THREE.Vector3().subVectors(p3, p1);
                const normal = new THREE.Vector3().crossVectors(v1, v2).normalize();
                if (normal.z < 0) normal.negate();

                const planeMesh = new THREE.Mesh(
                    new THREE.PlaneGeometry(3, 3),
                    new THREE.MeshStandardMaterial({ color: 0x888888, side: THREE.DoubleSide, roughness: 0.6 })
                );
                planeMesh.position.copy(center);
                const upVec = new THREE.Vector3(0, 0, 1);
                planeMesh.quaternion.setFromUnitVectors(upVec, normal);
                registerObject(planeMesh, `Plane3Pt_${nextObjId}`, "plane");

                is3PtPlaneMode = false;
                bar3Pt.style.display = "none";
                clear3PtMarkers();
                logMsg("Created aligned Reference Plane from 3 Point Cloud points");
            };
        }
    }

    // Outliner UI
    const outlinerList = document.getElementById("outliner-list");
    const outlinerCount = document.getElementById("outliner-count");

    function updateOutliner() {
        const ol = document.getElementById("outliner-list") || outlinerList;
        if (!ol) return;
        ol.innerHTML = "";
        const totalCount = (isShotCameraDeleted ? 0 : 1) + (cloudMesh ? 1 : 0) + threeObjectsMap.size;
        const oc = document.getElementById("outliner-count") || outlinerCount;
        if (oc) oc.innerText = `${totalCount} objects`;

        const { activeActors, actorColorMap } = getSelectedActorColors();
        const effectiveHighlighted = hoveredKeyframeActors.size > 0 ? hoveredKeyframeActors : highlightedKeyframeActors;
        const effectiveKeyFrame = (hoveredKeyframeFrame !== null) ? hoveredKeyframeFrame : highlightedKeyframeFrame;

        // 1. Shot Camera Item
        if (!isShotCameraDeleted) {
            const isShotCamSel = selectedObjects.has(shotCamera) || (selectedObject === shotCamera || (selectedObject && selectedObject.userData && selectedObject.userData.id === "cam_shot"));
            const shotCamColor = isShotCamSel ? (actorColorMap.get(shotCamera) || "#00f0ff") : null;
            const isKeyHighlighted = isShotCamSel && effectiveHighlighted.has(shotCamera);

            const shotCamItem = document.createElement("div");
            shotCamItem.className = "vfx-outliner-item" + (isShotCamSel ? " vfx-outliner-item-active selected" : "");
            shotCamItem.style.cssText = "display: flex; align-items: center; padding: 4px 8px; cursor: pointer; border-radius: 4px; gap: 6px; user-select: none; transition: all 0.15s ease;";
            if (shotCamColor && activeActors.length > 1) {
                shotCamItem.style.borderLeft = `3px solid ${shotCamColor}`;
            }
            if (isKeyHighlighted) {
                shotCamItem.style.border = `1.5px solid ${shotCamColor}`;
                shotCamItem.style.boxShadow = `0 0 10px ${shotCamColor}55, inset 0 0 8px ${shotCamColor}22`;
                shotCamItem.style.background = "rgba(255,255,255,0.12)";
            }
            const isCamProxyVis = isShotCameraVisible;
            shotCamItem.innerHTML = `
                ${shotCamColor && activeActors.length > 1 ? `<span style="width:7px; height:7px; border-radius:50%; background:${shotCamColor}; box-shadow:0 0 6px ${shotCamColor}; flex-shrink:0;"></span>` : ''}
                <span class="vfx-outliner-icon" style="font-size:12px;">🎥</span>
                <span class="vfx-outliner-name" style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:500;">Shot Camera</span>
                ${isKeyHighlighted && effectiveKeyFrame !== null ? `<span style="font-size:8px; font-weight:700; color:#fff; background:${shotCamColor}; padding:1px 4px; border-radius:3px;">KEY F${effectiveKeyFrame}</span>` : ''}
                <span style="font-size:9px; color:rgba(255,255,255,0.4); font-family:monospace;">${cameraMatrices && cameraMatrices.length > 0 ? 'TRACKED' : 'CINEMA'}</span>
                <span class="vfx-outliner-nuke" ${tipAttr("Send Camera to Nuke & Deconstruct outputs", "Отправить камеру в Nuke и выходы деконструкции")} style="cursor:pointer; font-size:11px; padding:0 2px; opacity:${(window.__vfxDeconstructManager && window.__vfxDeconstructManager.isItemAdded('shot_camera')) ? '1.0' : '0.4'};">🧩</span>
                <span class="vfx-outliner-eye" ${tipAttr("Toggle Camera Visibility (Hide/Show)", "Показать / скрыть камеру во вьюпорте")} style="cursor:pointer; opacity:${isCamProxyVis ? '1.0' : '0.35'}; font-size:11px;">👁️</span>
                <span class="vfx-outliner-del" ${tipAttr("Delete / Reset Shot Camera", "Удалить / сбросить камеру шота")} style="cursor:pointer; opacity:0.6; font-size:11px; padding:0 2px;">✕</span>
            `;
            shotCamItem.onclick = (e) => {
                if (e.target.classList.contains("vfx-outliner-nuke")) {
                    const curPid = (typeof currentProject !== "undefined" ? currentProject?.id : "New_Movie_Shot") || "sandbox_playground";
                    if (window.__vfxDeconstructManager) {
                        window.__vfxDeconstructManager.toggleItem(curPid, {
                            id: "shot_camera",
                            source_id: "shot_camera",
                            source_type: "camera_3d",
                            name: "Shot Camera",
                            alias: "cam_shot01",
                            data_type: "VFX_CAMERA",
                            meta: { fov: shotCamera.fov }
                        }).then(() => updateOutliner());
                    }
                    return;
                }
                if (e.target.classList.contains("vfx-outliner-eye")) {
                    isShotCameraVisible = !isShotCameraVisible;
                    if (camBodyMesh) camBodyMesh.visible = isShotCameraVisible;
                    if (camFrustumHelper) camFrustumHelper.visible = isShotCameraVisible;
                    if (cameraPathLine) cameraPathLine.visible = isShotCameraVisible;
                    updateOutliner();
                    renderStudioViewports();
                    return;
                }
                if (e.target.classList.contains("vfx-outliner-del")) {
                    deleteShotCamera();
                    return;
                }
                selectObject(shotCamera, e.shiftKey || e.ctrlKey);
            };
            ol.appendChild(shotCamItem);
        }

        // 2. LiDAR Point Cloud Item
        if (cloudMesh) {
            const isCloudSel = selectedObjects.has(cloudMesh) || (selectedObject === cloudMesh);
            const cloudColor = isCloudSel ? (actorColorMap.get(cloudMesh) || "#10b981") : null;
            const isKeyHighlighted = isCloudSel && effectiveHighlighted.has(cloudMesh);

            const ptItem = document.createElement("div");
            ptItem.className = "vfx-outliner-item" + (isCloudSel ? " vfx-outliner-item-active selected" : "");
            ptItem.style.cssText = "display: flex; align-items: center; padding: 4px 8px; cursor: pointer; border-radius: 4px; gap: 6px; user-select: none; transition: all 0.15s ease;";
            if (cloudColor && activeActors.length > 1) {
                ptItem.style.borderLeft = `3px solid ${cloudColor}`;
            }
            if (isKeyHighlighted) {
                ptItem.style.border = `1.5px solid ${cloudColor}`;
                ptItem.style.boxShadow = `0 0 10px ${cloudColor}55, inset 0 0 8px ${cloudColor}22`;
                ptItem.style.background = "rgba(255,255,255,0.12)";
            }
            ptItem.innerHTML = `
                ${cloudColor && activeActors.length > 1 ? `<span style="width:7px; height:7px; border-radius:50%; background:${cloudColor}; box-shadow:0 0 6px ${cloudColor}; flex-shrink:0;"></span>` : ''}
                <span class="vfx-outliner-icon" style="font-size:12px;">☁️</span>
                <span class="vfx-outliner-name" style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:500;">LiDAR Point Cloud</span>
                ${isKeyHighlighted && effectiveKeyFrame !== null ? `<span style="font-size:8px; font-weight:700; color:#fff; background:${cloudColor}; padding:1px 4px; border-radius:3px;">KEY F${effectiveKeyFrame}</span>` : ''}
                <span style="font-size:9px; color:rgba(255,255,255,0.4); font-family:monospace;">LIDAR</span>
                <span class="vfx-outliner-eye" ${tipAttr("Toggle Point Cloud Visibility", "Показать / скрыть облако точек трекинга")} style="cursor:pointer; opacity:${cloudMesh.visible !== false ? '1.0' : '0.35'}; font-size:11px;">👁️</span>
                <span class="vfx-outliner-del" ${tipAttr("Delete Point Cloud", "Удалить облако точек")} style="cursor:pointer; opacity:0.6; font-size:11px; padding:0 2px;">✕</span>
            `;
            ptItem.onclick = (e) => {
                if (e.target.classList.contains("vfx-outliner-eye")) {
                    cloudMesh.visible = !cloudMesh.visible;
                    updateOutliner();
                    renderStudioViewports();
                    return;
                }
                if (e.target.classList.contains("vfx-outliner-del")) {
                    deletePointCloud();
                    return;
                }
                selectObject(cloudMesh, e.shiftKey || e.ctrlKey);
            };
            ol.appendChild(ptItem);
        }

        // 3. Registered 3D Primitives, Free Cameras & Meshes
        threeObjectsMap.forEach((obj) => {
            const isObjSel = selectedObjects.has(obj);
            const objColor = isObjSel ? (actorColorMap.get(obj) || "#00f0ff") : null;
            const isKeyHighlighted = isObjSel && effectiveHighlighted.has(obj);

            const item = document.createElement("div");
            item.className = "vfx-outliner-item" + (isObjSel ? " vfx-outliner-item-active selected" : "");
            item.style.cssText = "display: flex; align-items: center; padding: 4px 8px; cursor: pointer; border-radius: 4px; gap: 6px; user-select: none; transition: all 0.15s ease;";
            if (objColor && activeActors.length > 1) {
                item.style.borderLeft = `3px solid ${objColor}`;
            }
            if (isKeyHighlighted) {
                item.style.border = `1.5px solid ${objColor}`;
                item.style.boxShadow = `0 0 10px ${objColor}55, inset 0 0 8px ${objColor}22`;
                item.style.background = "rgba(255,255,255,0.12)";
            }
            const iconChar = obj.userData.type === "camera" ? "📹" : (obj.userData.type === "light" ? (obj.userData.lightSubType === "sun" ? "☀️" : (obj.userData.lightSubType === "area" ? "▭" : "💡")) : (obj.userData.type === "glb" ? "🧊" : (obj.userData.type === "plane" ? "📐" : (obj.userData.type === "splat" ? "✨" : "📦"))));
            item.innerHTML = `
                ${objColor && activeActors.length > 1 ? `<span style="width:7px; height:7px; border-radius:50%; background:${objColor}; box-shadow:0 0 6px ${objColor}; flex-shrink:0;"></span>` : ''}
                <span class="vfx-outliner-icon" style="font-size:12px;">${iconChar}</span>
                <span class="vfx-outliner-name" style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:500;">${obj.userData.name}</span>
                ${isKeyHighlighted && effectiveKeyFrame !== null ? `<span style="font-size:8px; font-weight:700; color:#fff; background:${objColor}; padding:1px 4px; border-radius:3px;">KEY F${effectiveKeyFrame}</span>` : ''}
                <span style="font-size:9px; color:rgba(255,255,255,0.4); font-family:monospace;">${obj.userData.type === "light" ? String(obj.userData.lightSubType || "sun").toUpperCase() : obj.userData.type.toUpperCase()}</span>
                <span class="vfx-outliner-eye" ${tipAttr("Toggle Visibility", "Показать / скрыть объект")} style="cursor:pointer; opacity:${obj.visible !== false ? '1.0' : '0.35'}; font-size:11px;">👁️</span>
                <span class="vfx-outliner-del" ${tipAttr("Delete Object (Del)", "Удалить объект (Del)")} style="cursor:pointer; opacity:0.6; font-size:11px; padding:0 2px;">✕</span>
            `;
            item.onclick = (e) => {
                if (e.target.classList.contains("vfx-outliner-eye")) {
                    obj.visible = (obj.visible === false);
                    if (obj.userData.cameraHelper) obj.userData.cameraHelper.visible = obj.visible;
                    updateOutliner();
                    renderStudioViewports();
                    return;
                }
                if (e.target.classList.contains("vfx-outliner-del")) {
                    selectObject(obj);
                    deleteSelectedObjects();
                    return;
                }
                selectObject(obj, e.shiftKey || e.ctrlKey);
            };
            ol.appendChild(item);
        });
    }

    // Transform Inspector UI
    const inspectorTarget = document.getElementById("inspector-target");
    const numPosX = document.getElementById("num-pos-x");
    const numPosY = document.getElementById("num-pos-y");
    const numPosZ = document.getElementById("num-pos-z");
    const numRotX = document.getElementById("num-rot-x");
    const numRotY = document.getElementById("num-rot-y");
    const numRotZ = document.getElementById("num-rot-z");
    const numScaleX = document.getElementById("num-scale-x");
    const numScaleY = document.getElementById("num-scale-y");
    const numScaleZ = document.getElementById("num-scale-z");
    const colorPicker = document.getElementById("sidebar-color-picker");

    let sidebarScaleLocked = true;
    function updateTransformInspector(rebuildDetailsDom = false) {
        if (!selectedObject) {
            if (inspectorTarget) inspectorTarget.innerText = "• " + uiText("None", "Нет");
            [numPosX, numPosY, numPosZ].forEach(el => { if (el && document.activeElement !== el) el.value = "0.00"; });
            [numRotX, numRotY, numRotZ].forEach(el => { if (el && document.activeElement !== el) el.value = "0.0"; });
            [numScaleX, numScaleY, numScaleZ].forEach(el => { if (el && document.activeElement !== el) el.value = "1.00"; });
            const lightInsp = document.getElementById("sidebar-light-inspector");
            if (lightInsp) lightInsp.style.display = "none";
            if (rebuildDetailsDom) notifyDetailsAndOutliner();
            return;
        }
        if (inspectorTarget) inspectorTarget.innerText = "• " + (selectedObject.userData.name || "Object");
        const px = selectedObject.position.x.toFixed(2);
        const py = selectedObject.position.y.toFixed(2);
        const pz = selectedObject.position.z.toFixed(2);
        const rx = THREE.MathUtils.radToDeg(selectedObject.rotation.x).toFixed(1);
        const ry = THREE.MathUtils.radToDeg(selectedObject.rotation.y).toFixed(1);
        const rz = THREE.MathUtils.radToDeg(selectedObject.rotation.z).toFixed(1);
        const sx = selectedObject.scale.x.toFixed(2);
        const sy = selectedObject.scale.y.toFixed(2);
        const sz = selectedObject.scale.z.toFixed(2);

        if (numPosX && document.activeElement !== numPosX) numPosX.value = px;
        if (numPosY && document.activeElement !== numPosY) numPosY.value = py;
        if (numPosZ && document.activeElement !== numPosZ) numPosZ.value = pz;

        if (numRotX && document.activeElement !== numRotX) numRotX.value = rx;
        if (numRotY && document.activeElement !== numRotY) numRotY.value = ry;
        if (numRotZ && document.activeElement !== numRotZ) numRotZ.value = rz;

        if (numScaleX && document.activeElement !== numScaleX) numScaleX.value = sx;
        if (numScaleY && document.activeElement !== numScaleY) numScaleY.value = sy;
        if (numScaleZ && document.activeElement !== numScaleZ) numScaleZ.value = sz;

        if (colorPicker) {
            if (selectedObject.userData && selectedObject.userData.color) {
                colorPicker.value = selectedObject.userData.color;
            } else if (selectedObject.material && selectedObject.material.color) {
                colorPicker.value = `#${selectedObject.material.color.getHexString()}`;
            }
        }
        const lightInsp = document.getElementById("sidebar-light-inspector");
        if (lightInsp) {
            if (selectedObject && selectedObject.userData && selectedObject.userData.type === "light") {
                lightInsp.style.display = "flex";
                const sub = selectedObject.userData.lightSubType || "sun";
                const inten = Number(selectedObject.userData.lightIntensity ?? 2.5);
                const ang = Number(selectedObject.userData.lightAngleDeg ?? 8);
                const rng = Number(selectedObject.userData.lightRange ?? 20);
                const aw = Number(selectedObject.userData.areaWidth ?? 2.0);
                const ah = Number(selectedObject.userData.areaHeight ?? 1.2);
                lightInsp.querySelectorAll(".sidebar-light-mode-btn").forEach(b => {
                    const active = b.dataset.lmode === sub;
                    b.style.background = active ? "rgba(251, 191, 36, 0.25)" : "rgba(255,255,255,0.04)";
                    b.style.borderColor = active ? "#fbbf24" : "rgba(255,255,255,0.12)";
                    b.style.color = active ? "#fde68a" : "#d4d4d8";
                });
                const slInt = document.getElementById("sidebar-light-intensity-slider");
                const nmInt = document.getElementById("sidebar-light-intensity-num");
                if (slInt && document.activeElement !== slInt) slInt.value = String(inten);
                if (nmInt && document.activeElement !== nmInt) nmInt.value = inten.toFixed(1);
                const slAng = document.getElementById("sidebar-light-angle-slider");
                const nmAng = document.getElementById("sidebar-light-angle-num");
                if (slAng && document.activeElement !== slAng) slAng.value = String(ang);
                if (nmAng && document.activeElement !== nmAng) nmAng.value = String(Math.round(ang));
                const rngRow = document.getElementById("sidebar-light-range-row");
                if (rngRow) rngRow.style.display = (sub === "point" || sub === "area") ? "flex" : "none";
                const slRng = document.getElementById("sidebar-light-range-slider");
                const nmRng = document.getElementById("sidebar-light-range-num");
                if (slRng && document.activeElement !== slRng) slRng.value = String(rng);
                if (nmRng && document.activeElement !== nmRng) nmRng.value = rng.toFixed(1);
                const areaRow = document.getElementById("sidebar-light-area-row");
                if (areaRow) areaRow.style.display = (sub === "area") ? "flex" : "none";
                const inpAw = document.getElementById("sidebar-light-area-w");
                const inpAh = document.getElementById("sidebar-light-area-h");
                if (inpAw && document.activeElement !== inpAw) inpAw.value = aw.toFixed(1);
                if (inpAh && document.activeElement !== inpAh) inpAh.value = ah.toFixed(1);
            } else {
                lightInsp.style.display = "none";
            }
        }

        // Update UE5 Details panel transform inputs in-place without rebuilding DOM (prevents focus loss & drag lag)
        document.querySelectorAll('.ue-vec-inp[data-vec="pos"][data-axis="x"]').forEach(el => { if (document.activeElement !== el) el.value = px; });
        document.querySelectorAll('.ue-vec-inp[data-vec="pos"][data-axis="y"]').forEach(el => { if (document.activeElement !== el) el.value = py; });
        document.querySelectorAll('.ue-vec-inp[data-vec="pos"][data-axis="z"]').forEach(el => { if (document.activeElement !== el) el.value = pz; });
        document.querySelectorAll('.ue-vec-inp[data-vec="rot"][data-axis="x"]').forEach(el => { if (document.activeElement !== el) el.value = rx; });
        document.querySelectorAll('.ue-vec-inp[data-vec="rot"][data-axis="y"]').forEach(el => { if (document.activeElement !== el) el.value = ry; });
        document.querySelectorAll('.ue-vec-inp[data-vec="rot"][data-axis="z"]').forEach(el => { if (document.activeElement !== el) el.value = rz; });
        document.querySelectorAll('.ue-vec-inp[data-vec="scl"][data-axis="x"]').forEach(el => { if (document.activeElement !== el) el.value = sx; });
        document.querySelectorAll('.ue-vec-inp[data-vec="scl"][data-axis="y"]').forEach(el => { if (document.activeElement !== el) el.value = sy; });
        document.querySelectorAll('.ue-vec-inp[data-vec="scl"][data-axis="z"]').forEach(el => { if (document.activeElement !== el) el.value = sz; });

        if (rebuildDetailsDom) {
            notifyDetailsAndOutliner();
        } else {
            updateStatusBarTelemetry({
                selected: selectedObject,
                actorsCount: threeObjectsMap.size
            });
        }
    }

    function onInspectorChange(changedEl) {
        if (!selectedObject) return;
        if (sidebarScaleLocked && (changedEl === numScaleX || changedEl === numScaleY || changedEl === numScaleZ)) {
            const axis = changedEl === numScaleX ? "x" : (changedEl === numScaleY ? "y" : "z");
            const oldVal = selectedObject.scale[axis] || 1;
            const newVal = Math.max(0.01, parseFloat(changedEl.value) || 1);
            if (Math.abs(oldVal) > 0.0001) {
                const ratio = newVal / oldVal;
                if (changedEl !== numScaleX && numScaleX) numScaleX.value = Math.max(0.01, (selectedObject.scale.x || 1) * ratio).toFixed(2);
                if (changedEl !== numScaleY && numScaleY) numScaleY.value = Math.max(0.01, (selectedObject.scale.y || 1) * ratio).toFixed(2);
                if (changedEl !== numScaleZ && numScaleZ) numScaleZ.value = Math.max(0.01, (selectedObject.scale.z || 1) * ratio).toFixed(2);
            }
        }
        selectedObject.position.set(parseFloat(numPosX.value) || 0, parseFloat(numPosY.value) || 0, parseFloat(numPosZ.value) || 0);
        selectedObject.rotation.set(
            THREE.MathUtils.degToRad(parseFloat(numRotX.value) || 0),
            THREE.MathUtils.degToRad(parseFloat(numRotY.value) || 0),
            THREE.MathUtils.degToRad(parseFloat(numRotZ.value) || 0)
        );
        selectedObject.scale.set(
            Math.max(0.01, parseFloat(numScaleX.value) || 1),
            Math.max(0.01, parseFloat(numScaleY.value) || 1),
            Math.max(0.01, parseFloat(numScaleZ.value) || 1)
        );
        selectedObject.updateMatrixWorld(true);
        if (selectedObject === shotCamera) {
            syncShotCameraVisuals(true);
        } else if (selectedObject.userData && selectedObject.userData.cameraHelper) {
            if (selectedObject.userData.camera) selectedObject.userData.camera.updateMatrixWorld(true);
            selectedObject.userData.cameraHelper.update();
        }
        if (autoKeyEnabled && Array.isArray(selectedObject.userData?.keyframes) && selectedObject.userData.keyframes.length > 0) {
            set3DTransformKeyframe(selectedObject, currentFrame, true);
        }
        updateTransformInspector(false);
        syncSceneData();
        renderStudioViewports();
    }

    [numPosX, numPosY, numPosZ, numRotX, numRotY, numRotZ, numScaleX, numScaleY, numScaleZ].forEach(inp => {
        if (!inp) return;
        inp.addEventListener("focus", () => { if (selectedObject) pushUndoState("Inspector Transform"); });
        inp.addEventListener("input", () => onInspectorChange(inp));
        inp.addEventListener("change", () => onInspectorChange(inp));
    });

    // Wire row resets, scale lock, snap floor, pivot buttons, and UE5 drag-scrubbing in 3D sidebar
    const btnSidebarLockScale = document.getElementById("btn-sidebar-lock-scale");
    if (btnSidebarLockScale) {
        btnSidebarLockScale.onclick = () => {
            sidebarScaleLocked = !sidebarScaleLocked;
            btnSidebarLockScale.classList.toggle("active", sidebarScaleLocked);
            btnSidebarLockScale.innerHTML = `<i data-lucide="${sidebarScaleLocked ? 'lock' : 'unlock'}" style="width:10px; height:10px;"></i>`;
            if (window.lucide) try { window.lucide.createIcons({ nodes: [btnSidebarLockScale] }); } catch (_) {}
        };
    }
    const btnSidebarResetPos = document.getElementById("btn-sidebar-reset-pos");
    if (btnSidebarResetPos) {
        btnSidebarResetPos.onclick = () => {
            if (!selectedObject) return;
            pushUndoState("Reset Position");
            const isNativeGlb = selectedObject.userData?.type === "glb" && selectedObject.userData?.fitMode === "native";
            selectedObject.position.set(0, 0, isNativeGlb ? 0.0 : 0.5);
            selectedObject.updateMatrixWorld(true);
            updateTransformInspector(true);
            syncSceneData();
            renderStudioViewports();
        };
    }
    const btnSidebarResetRot = document.getElementById("btn-sidebar-reset-rot");
    if (btnSidebarResetRot) {
        btnSidebarResetRot.onclick = () => {
            if (!selectedObject) return;
            pushUndoState("Reset Rotation");
            selectedObject.rotation.set(0, 0, 0);
            selectedObject.updateMatrixWorld(true);
            updateTransformInspector(true);
            syncSceneData();
            renderStudioViewports();
        };
    }
    const btnSidebarResetScale = document.getElementById("btn-sidebar-reset-scale");
    if (btnSidebarResetScale) {
        btnSidebarResetScale.onclick = () => {
            if (!selectedObject) return;
            pushUndoState("Reset Scale");
            selectedObject.scale.set(1, 1, 1);
            selectedObject.updateMatrixWorld(true);
            updateTransformInspector(true);
            syncSceneData();
            renderStudioViewports();
        };
    }
    const btnSidebarSnapFloor = document.getElementById("btn-sidebar-snap-floor");
    if (btnSidebarSnapFloor) {
        btnSidebarSnapFloor.onclick = () => snapSelectedObjectToFloor();
    }
    document.querySelectorAll(".btn-sidebar-pivot").forEach(btn => {
        btn.onclick = () => {
            if (selectedObject && typeof applyObjectPivot === "function") {
                applyObjectPivot(selectedObject, btn.dataset.pivot || "bottom");
            }
        };
    });

    // Enable horizontal drag-scrubbing on all .ue-striped-input-wrap inside #body-inspector and #body-cursor3d
    document.querySelectorAll("#body-inspector .ue-striped-input-wrap, #body-cursor3d .ue-striped-input-wrap").forEach(wrap => {
        const inp = wrap.querySelector("input[type='number']");
        if (!inp) return;
        wrap.style.cursor = "ew-resize";
        wrap.addEventListener("pointerdown", (e) => {
            if (e.target === inp && document.activeElement === inp) return;
            if (e.button !== 0) return;
            const startX = e.clientX;
            const startVal = parseFloat(inp.value) || 0;
            const isRot = inp.id.includes("rot");
            const isScale = inp.id.includes("scale");
            const step = isRot ? 1.0 : 0.05;
            let pushedUndo = false;
            const onMove = (ev) => {
                const dx = ev.clientX - startX;
                if (Math.abs(dx) >= 2) {
                    if (!pushedUndo && selectedObject && !inp.id.includes("cursor")) {
                        pushUndoState("Scrub Transform");
                        pushedUndo = true;
                    }
                    const mult = ev.shiftKey ? 0.2 : (ev.ctrlKey ? 5.0 : 1.0);
                    let next = startVal + dx * step * mult;
                    if (isScale) next = Math.max(0.01, next);
                    inp.value = isRot ? next.toFixed(1) : next.toFixed(2);
                    inp.dispatchEvent(new Event("input", { bubbles: true }));
                }
            };
            const onUp = () => {
                window.removeEventListener("pointermove", onMove);
                window.removeEventListener("pointerup", onUp);
            };
            window.addEventListener("pointermove", onMove);
            window.addEventListener("pointerup", onUp);
        });
    });

    function setActorColor(obj, hexColor) {
        if (!obj) return;
        obj.userData.color = hexColor;
        if (obj.userData.type === "light") {
            updateLightActor(obj, { color: hexColor });
            return;
        }
        obj.traverse((child) => {
            if (child.isMesh && child.material && child.material.color) {
                child.material.color.setStyle(hexColor);
            }
        });
        syncSceneData();
        notifyDetailsAndOutliner();
        monitorNeedsRender = true;
        renderStudioViewports();
    }

    if (colorPicker) {
        colorPicker.addEventListener("input", (e) => {
            if (selectedObject) setActorColor(selectedObject, e.target.value);
        });
    }

    // Swatch buttons
    const swatchMap = [
        ["sidebar-swatch-clay", "#cccccc"],
        ["sidebar-swatch-red", "#e53935"],
        ["sidebar-swatch-green", "#43a047"],
        ["sidebar-swatch-blue", "#1e88e5"],
        ["sidebar-swatch-gold", "#fdd835"]
    ];
    swatchMap.forEach(([id, hex]) => {
        const el = document.getElementById(id);
        if (el) el.onclick = () => { if (selectedObject) setActorColor(selectedObject, hex); };
    });
    document.querySelectorAll(".sidebar-quick-color-swatch").forEach(btn => {
        btn.onclick = () => {
            const hex = btn.dataset.col;
            if (hex && selectedObject) setActorColor(selectedObject, hex);
        };
    });

    // Primitives Creation & Proxy-to-3D Replacement
    const defMat = (hex = 0xcccccc, isPlane = false) => new THREE.MeshStandardMaterial({
        color: hex,
        roughness: isPlane ? 0.9 : 0.4,
        metalness: 0.1,
        side: isPlane ? THREE.DoubleSide : THREE.FrontSide
    });

    function createPrimitiveGeometry(type) {
        if (type === "sphere") return new THREE.SphereGeometry(0.5, 32, 24);
        if (type === "cylinder") {
            const g = new THREE.CylinderGeometry(0.5, 0.5, 1, 32);
            g.rotateX(Math.PI / 2);
            return g;
        }
        if (type === "cone") {
            const g = new THREE.ConeGeometry(0.5, 1, 32);
            g.rotateX(Math.PI / 2);
            return g;
        }
        if (type === "torus") return new THREE.TorusGeometry(0.5, 0.2, 16, 32);
        if (type === "plane") return new THREE.PlaneGeometry(3, 3);
        return new THREE.BoxGeometry(1, 1, 1);
    }

    // =========================================================================
    // PIVOT POINT TRANSFORMATION (Unreal Engine DCC Style)
    // =========================================================================
    function applyObjectPivot(targetObj, mode = "center", customDelta = null) {
        let obj = targetObj;
        if (!obj || typeof obj !== "object" || !obj.isObject3D) {
            obj = (typeof targetObj === "string" && threeObjectsMap.get(targetObj)) || selectedObject;
        }
        if (!obj) {
            logMsg("No object selected to adjust pivot.");
            return false;
        }

        let box = new THREE.Box3();
        let hasChildMeshes = false;
        if (obj.children && obj.children.length > 0) {
            obj.traverse((ch) => {
                if (ch !== obj && ch.isMesh && ch.geometry) hasChildMeshes = true;
            });
        }
        const isSingleMesh = (obj instanceof THREE.Mesh) && obj.geometry && !hasChildMeshes;

        if (isSingleMesh) {
            if (!obj.geometry.boundingBox) obj.geometry.computeBoundingBox();
            box.copy(obj.geometry.boundingBox);
        } else {
            obj.updateMatrixWorld(true);
            const invObjWorld = new THREE.Matrix4().copy(obj.matrixWorld).invert();
            const relMat = new THREE.Matrix4();
            const tmpVec = new THREE.Vector3();
            box.makeEmpty();
            obj.traverse((child) => {
                if (!child || child === obj) return;
                if (child.name === "__vfx_proxy_cage" || child.isLine || child.isLineSegments || child.isHelper) return;
                if (child.isMesh && child.geometry) {
                    relMat.multiplyMatrices(invObjWorld, child.matrixWorld);
                    const posAttr = child.geometry.attributes && child.geometry.attributes.position;
                    if (posAttr && posAttr.count > 0 && posAttr.count <= 300000) {
                        for (let i = 0; i < posAttr.count; i++) {
                            tmpVec.fromBufferAttribute(posAttr, i).applyMatrix4(relMat);
                            box.expandByPoint(tmpVec);
                        }
                    } else {
                        if (!child.geometry.boundingBox) child.geometry.computeBoundingBox();
                        if (child.geometry.boundingBox) {
                            const childBox = child.geometry.boundingBox.clone().applyMatrix4(relMat);
                            box.union(childBox);
                        }
                    }
                }
            });
            if (box.isEmpty()) {
                box.set(new THREE.Vector3(-0.5, -0.5, -0.5), new THREE.Vector3(0.5, 0.5, 0.5));
            }
        }

        const center = new THREE.Vector3();
        box.getCenter(center);

        let delta = new THREE.Vector3(0, 0, 0);

        if (customDelta) {
            delta.set(Number(customDelta.x) || 0, Number(customDelta.y) || 0, Number(customDelta.z) || 0);
        } else if (mode === "center") {
            delta.copy(center);
        } else if (mode === "bottom") {
            // Z-min in Z-up
            delta.set(center.x, center.y, box.min.z);
        } else if (mode === "top") {
            // Z-max in Z-up
            delta.set(center.x, center.y, box.max.z);
        } else if (mode === "left") {
            // X-min
            delta.set(box.min.x, center.y, center.z);
        } else if (mode === "right") {
            // X-max
            delta.set(box.max.x, center.y, center.z);
        } else if (mode === "front") {
            // Y-max in Z-up
            delta.set(center.x, box.max.y, center.z);
        } else if (mode === "back") {
            // Y-min in Z-up
            delta.set(center.x, box.min.y, center.z);
        } else if (mode === "cursor") {
            // Snap Pivot to 3D Cursor in local mesh coordinates!
            obj.updateMatrixWorld(true);
            const invMat = new THREE.Matrix4().copy(obj.matrixWorld).invert();
            const localCursor = cursor3DPosition.clone().applyMatrix4(invMat);
            delta.copy(localCursor);
        }

        if (delta.lengthSq() < 1e-6) {
            logMsg(`Object pivot is already at target position (${mode}).`);
            return true;
        }

        pushUndoState(`Adjust Pivot (${mode})`);

        // Translate geometry vertices or direct children by -delta
        if (isSingleMesh) {
            obj.geometry.translate(-delta.x, -delta.y, -delta.z);
            obj.geometry.computeBoundingBox();
            obj.geometry.computeBoundingSphere();
            if (obj.children && obj.children.length > 0) {
                obj.children.forEach(child => {
                    child.position.sub(delta);
                    child.updateMatrixWorld(true);
                });
            }
        } else {
            obj.children.forEach(child => {
                child.position.sub(delta);
                child.updateMatrixWorld(true);
            });
        }

        // Compensate obj.position so the object does not move in world space!
        const deltaWorld = delta.clone().multiply(obj.scale).applyQuaternion(obj.quaternion);
        obj.position.add(deltaWorld);
        obj.updateMatrixWorld(true);

        // Compensate any existing keyframes so scrubbing timeline preserves world pose
        if (obj.userData && Array.isArray(obj.userData.keyframes) && obj.userData.keyframes.length > 0) {
            obj.userData.keyframes.forEach(kf => {
                if (kf && kf.position) {
                    const kfScale = kf.scale || obj.scale;
                    const kfQuat = kf.quaternion || obj.quaternion;
                    const kfDeltaWorld = delta.clone().multiply(kfScale).applyQuaternion(kfQuat);
                    kf.position.add(kfDeltaWorld);
                }
            });
        }

        // Record cumulative pivot offset in userData for export/persistence
        obj.userData.pivotOffset = obj.userData.pivotOffset || [0, 0, 0];
        obj.userData.pivotOffset[0] += delta.x;
        obj.userData.pivotOffset[1] += delta.y;
        obj.userData.pivotOffset[2] += delta.z;

        if (transformControls.object === obj) {
            transformControls.attach(obj);
        }
        updateSelectionHelpers();
        updateTransformInspector(false);
        updateOutliner();
        notifyDetailsAndOutliner();
        syncSceneData();
        monitorNeedsRender = true;
        renderStudioViewports();

        logMsg(`✓ Pivot updated (${mode}): baked offset [${delta.x.toFixed(3)}, ${delta.y.toFixed(3)}, ${delta.z.toFixed(3)}]`);
        return true;
    }

    window.__vfxApplyObjectPivot = applyObjectPivot;
    window.__vfxRefreshStudioLanguage = () => {
        try { updateOutliner(); } catch (_) {}
        try { updateKeyframeMarkers(); } catch (_) {}
        try { updateTimelineActiveContextBar(); } catch (_) {}
    };

    // =========================================================================
    // CHARACTERS: skinned FBX/GLB (UE mannequin, Mixamo…) with a skeleton and animation clips.
    // A clip from another rig (Mixamo -> UE mannequin) is retargeted bone by bone, rest-pose aware.
    // Animation follows the timeline frame (frame / fps), so it scrubs and renders with the shot.
    // =========================================================================
    const characterRigs = new Map(); // object id -> { root, skel helper, bones map, mixer, action, retarget }
    const MIXAMO_TO_UE = {
        Hips: "pelvis", Spine: "spine_01", Spine1: "spine_02", Spine2: "spine_03", Neck: "neck_01", Head: "head",
        LeftShoulder: "clavicle_l", LeftArm: "upperarm_l", LeftForeArm: "lowerarm_l", LeftHand: "hand_l",
        RightShoulder: "clavicle_r", RightArm: "upperarm_r", RightForeArm: "lowerarm_r", RightHand: "hand_r",
        LeftUpLeg: "thigh_l", LeftLeg: "calf_l", LeftFoot: "foot_l", LeftToeBase: "ball_l",
        RightUpLeg: "thigh_r", RightLeg: "calf_r", RightFoot: "foot_r", RightToeBase: "ball_r"
    };
    ["Left", "Right"].forEach(side => {
        const s = side === "Left" ? "l" : "r";
        [["Thumb", "thumb"], ["Index", "index"], ["Middle", "middle"], ["Ring", "ring"], ["Pinky", "pinky"]].forEach(([mx, ue]) => {
            for (let i = 1; i <= 3; i++) MIXAMO_TO_UE[`${side}Hand${mx}${i}`] = `${ue}_0${i}_${s}`;
        });
    });
    const canonBone = (n) => String(n || "").replace(/^.*[:|]/, "").replace(/^mixamorig\d*_?/i, "");

    // Any known source skeleton -> UE mannequin bone name (the studio's common skeleton).
    // Mixamo via the table above; 3ds Max Biped ("Bip001 L UpperArm"); Rigify / Blender / Unity
    // libraries ("upper_arm.L", "spine.001"); Character Creator ("CC_Base_L_Upperarm").
    const FINGER_UE = ["thumb", "index", "middle", "ring", "pinky"];
    function toUEBone(name) {
        // loaders sanitize names: spaces -> "_", dots and colons dropped ("upper_arm.L" -> "upper_armL")
        const n = canonBone(name);
        if (MIXAMO_TO_UE[n]) return MIXAMO_TO_UE[n];
        let m = /^Bip0*1[_\s]+(?:([LR])[_\s]+)?(.+)$/i.exec(n);          // 3ds Max Biped
        if (m) {
            const s = m[1] ? m[1].toLowerCase() : "", part = m[2].replace(/[_\s]+/g, "").toLowerCase();
            const fixed = { pelvis: "pelvis", spine: "spine_01", spine1: "spine_02", spine2: "spine_03", neck: "neck_01", head: "head" }[part];
            if (fixed) return fixed;
            const limb = { clavicle: "clavicle", upperarm: "upperarm", forearm: "lowerarm", hand: "hand", thigh: "thigh", calf: "calf", foot: "foot", toe0: "ball" }[part];
            if (limb && s) return `${limb}_${s}`;
            const f = /^finger(\d)(\d?)$/.exec(part);
            if (f && s) return `${FINGER_UE[+f[1]]}_0${(+(f[2] || 0)) + 1}_${s}`;
            return "";
        }
        m = /^CC_Base_(?:([LR])_)?(.+)$/i.exec(n);                         // Character Creator
        if (m) {
            const s = m[1] ? m[1].toLowerCase() : "", part = m[2].toLowerCase();
            const fixed = { pelvis: "pelvis", waist: "spine_01", spine01: "spine_02", spine02: "spine_03", necktwist01: "neck_01", head: "head" }[part];
            if (fixed) return fixed;
            const limb = { clavicle: "clavicle", upperarm: "upperarm", forearm: "lowerarm", hand: "hand", thigh: "thigh", calf: "calf", foot: "foot", toebase: "ball" }[part];
            if (limb && s) return `${limb}_${s}`;
            return "";
        }
        const r = n.replace(/^(?:DEF|ORG)[-_]/i, "");                       // Rigify / Blender / Unity libraries
        m = /^(?:f_)?(thumb|index|middle|ring|pinky)\.?0?(\d)\.?([LR])$/i.exec(r);
        if (m) return `${m[1].toLowerCase()}_0${m[2]}_${m[3].toLowerCase()}`;
        m = /^(shoulder|upper_arm|upperarm|forearm|hand|thigh|shin|calf|foot|toe)\.?([LR])$/i.exec(r);
        if (m) return `${{ shoulder: "clavicle", upper_arm: "upperarm", upperarm: "upperarm", forearm: "lowerarm", hand: "hand", thigh: "thigh", shin: "calf", calf: "calf", foot: "foot", toe: "ball" }[m[1].toLowerCase()]}_${m[2].toLowerCase()}`;
        m = /^spine\.?(\d{3})$/i.exec(r);
        if (m) return +m[1] >= 1 && +m[1] <= 3 ? `spine_0${+m[1]}` : "";
        return { hips: "pelvis", pelvis: "pelvis", neck: "neck_01", head: "head" }[r.toLowerCase()] || "";
    }

    function collectBones(root) {
        const map = new Map();
        // FBX files often nest several nodes with the same bone name (UE exports: a transform node holding a
        // zero-offset copy). Keep the outermost one: it carries the transform and is what an AnimationMixer binds.
        root.traverse(o => { if (o.isBone) { const n = canonBone(o.name); if (!map.has(n)) map.set(n, o); } });
        return map;
    }

    // Built-in KESHIKI mannequin: UE-mannequin bone names, T-pose, 180 cm, Y-up and centimetres like an FBX,
    // facing +Z (its left side is +X). No mesh - small joint spheres make it visible and clickable.
    const MANNEQUIN_URL = "builtin:mannequin";
    function buildMannequinSkeleton() {
        const J = {   // name: [parent, x, y, z] in cm (absolute rest positions)
            root: [null, 0, 0, 0], pelvis: ["root", 0, 96, 0],
            spine_01: ["pelvis", 0, 106, 0], spine_02: ["spine_01", 0, 118, 0], spine_03: ["spine_02", 0, 132, 0],
            neck_01: ["spine_03", 0, 150, 0], head: ["neck_01", 0, 160, 2], head_end: ["head", 0, 178, 2]
        };
        [["l", 1], ["r", -1]].forEach(([s, k]) => {
            Object.assign(J, {
                [`clavicle_${s}`]: ["spine_03", 3 * k, 145, 0], [`upperarm_${s}`]: [`clavicle_${s}`, 17 * k, 145, -2],
                [`lowerarm_${s}`]: [`upperarm_${s}`, 45 * k, 145, -2], [`hand_${s}`]: [`lowerarm_${s}`, 70 * k, 145, -2],
                [`middle_01_${s}`]: [`hand_${s}`, 79 * k, 145, -2], [`middle_03_${s}`]: [`middle_01_${s}`, 88 * k, 145, -2],
                [`thumb_01_${s}`]: [`hand_${s}`, 73 * k, 143, 2], [`thumb_03_${s}`]: [`thumb_01_${s}`, 78 * k, 142, 6],
                [`thigh_${s}`]: ["pelvis", 9 * k, 92, 0], [`calf_${s}`]: [`thigh_${s}`, 9 * k, 50, 0],
                [`foot_${s}`]: [`calf_${s}`, 9 * k, 8, -2], [`ball_${s}`]: [`foot_${s}`, 9 * k, 1, 12], [`toe_end_${s}`]: [`ball_${s}`, 9 * k, 1, 19]
            });
        });
        const root = new THREE.Group(); root.name = "KESHIKI_Mannequin";
        const bones = {};
        const jointGeo = new THREE.SphereGeometry(1.6, 10, 8);
        const jointMat = new THREE.MeshStandardMaterial({ color: 0xf59e0b, emissive: 0x3a2400, roughness: 0.5 });
        Object.entries(J).forEach(([name, [parent, x, y, z]]) => {
            const b = new THREE.Bone(); b.name = name;
            const pa = parent ? J[parent] : [null, 0, 0, 0];
            b.position.set(x - pa[1], y - pa[2], z - pa[3]);
            (parent ? bones[parent] : root).add(b);
            bones[name] = b;
            if (name !== "root") { const m = new THREE.Mesh(jointGeo, jointMat); m.name = `${name}_joint`; b.add(m); }
        });
        root.updateMatrixWorld(true);
        return root;
    }

    function loadAnyModel(url, name) {
        if (url === MANNEQUIN_URL) return Promise.resolve({ root: buildMannequinSkeleton(), clips: [] });
        const ext = (String(name || url).split("?")[0].split(".").pop() || "").toLowerCase();
        return new Promise((resolve, reject) => {
            if (ext === "fbx") {
                if (!THREE.FBXLoader) return reject(new Error("THREE.FBXLoader is not available"));
                new THREE.FBXLoader().load(url, (o) => resolve({ root: o, clips: o.animations || [] }), undefined, reject);
            } else {
                new THREE.GLTFLoader().load(url, (g) => resolve({ root: g.scene || g.scenes[0], clips: g.animations || [] }), undefined, reject);
            }
        });
    }

    async function uploadToProject(file) {
        const pid = (window.currentProject && window.currentProject.id) || "sandbox_playground";
        const fd = new FormData(); fd.append("file", file, file.name);
        const r = await fetch(`/vfx/projects/${encodeURIComponent(pid)}/upload_media`, { method: "POST", body: fd }).then(x => x.json());
        if (!r.uploaded || !r.uploaded.length) throw new Error(r.error || "upload failed");
        return r.uploaded[0].url;
    }

    // Wraps a model as a studio object: Y-up -> Z-up, centimetres -> metres, feet on the ground at the 3D cursor.
    async function addCharacter(url, displayName, meta = {}, recordUndo = true) {
        const expectedGen = meta._loadGen !== undefined ? meta._loadGen : manifestLoadGen;
        const { root, clips } = await loadAnyModel(url, meta.fileName || url);
        if (expectedGen !== manifestLoadGen) {
            return null;
        }
        if (recordUndo) pushUndoState("Add Character");
        const zUp = new THREE.Group(); zUp.rotation.x = Math.PI / 2; zUp.add(root);
        const wrap = new THREE.Group(); wrap.add(zUp);
        zUp.updateMatrixWorld(true);
        const box = new THREE.Box3().setFromObject(zUp); const size = new THREE.Vector3(); box.getSize(size);
        const h = Math.max(size.z, 0.001);
        const k = meta.unitScale || (url === MANNEQUIN_URL ? 0.01 : (h > 20 ? 0.01 : (h < 0.2 ? 100 : 1))); // UE / Mixamo FBX are in centimetres
        zUp.scale.setScalar(k);
        zUp.updateMatrixWorld(true);
        const box2 = new THREE.Box3().setFromObject(zUp);
        zUp.position.z = -box2.min.z;
        const skeletonOnly = url === MANNEQUIN_URL;
        root.traverse(o => { if (o.isMesh && !skeletonOnly) { o.castShadow = true; o.frustumCulled = false; if (o.material && !Array.isArray(o.material) && o.material.color && !o.material.map) o.material.color.set("#9aa3ad"); } });
        const helper = new THREE.SkeletonHelper(root);
        helper.material.depthTest = false; helper.material.linewidth = 2;
        scene.add(helper);
        helper.visible = meta.showBones !== false;
        if (meta.position) wrap.position.fromArray(meta.position); else wrap.position.copy(cursor3DPosition);
        if (meta.rotation) wrap.rotation.fromArray(meta.rotation);
        if (meta.scale) wrap.scale.fromArray(meta.scale);
        wrap.updateMatrixWorld(true);
        const obj = registerObject(wrap, displayName || "Character", "character", {
            ...(meta.id ? { id: meta.id } : {}), characterUrl: url, fileName: meta.fileName || "", animUrl: meta.animUrl || "", animName: meta.animName || "",
            animOffset: meta.animOffset || 0, animSpeed: meta.animSpeed || 1, showBones: skeletonOnly ? true : meta.showBones !== false, unitScale: k,
            skeletonOnly, color: skeletonOnly ? "#f59e0b" : "#9aa3ad", keyframes: meta.keyframes || []
        });
        const rig = { id: obj.userData.id, obj, root, zUp, helper, bones: collectBones(root), height: (box2.max.z - box2.min.z) / k, ownClips: clips, mixer: new THREE.AnimationMixer(root), action: null, retarget: null };
        wrap.updateMatrixWorld(true);
        root.updateMatrixWorld(true);
        rig.rest = new Map(); rig.bones.forEach((b, n) => rig.rest.set(n, { q: b.quaternion.clone(), p: b.position.clone() }));
        characterRigs.set(obj.userData.id, rig);
        if (meta.animUrl) await setCharacterAnimation(obj, meta.animUrl, meta.animName, false).catch(e => logMsg(`Animation: ${e.message}`));
        else if (clips.length) playClipOn(obj, clips[0], null);
        logMsg(`✓ Character «${displayName}»: ${rig.bones.size} bones${clips.length ? `, ${clips.length} clip(s)` : ""}`);
        updateCharactersAtFrame();
        return obj;
    }

    function playClipOn(obj, clip, retarget) {
        const rig = characterRigs.get(obj.userData.id); if (!rig) return;
        rig.mixer.stopAllAction();
        rig.retarget = retarget;
        rig.action = null;
        if (!retarget) { rig.action = rig.mixer.clipAction(clip); rig.action.play(); }
        obj.userData.animName = clip.name || obj.userData.animName || "";
        obj.userData.animDuration = clip.duration;
        updateCharactersAtFrame();
    }

    // Retarget: source pose drives target bones through world-space deltas from each rig's rest pose, plus a
    // per-bone correction that turns the target's rest (UE A-pose) into the source's rest (Mixamo T-pose).
    function buildRetarget(srcRoot, srcClip, rig) {
        const src = { root: srcRoot, bones: collectBones(srcRoot), mixer: new THREE.AnimationMixer(srcRoot) };
        const pivot = new THREE.Group(); pivot.rotation.x = Math.PI / 2; pivot.add(srcRoot); pivot.updateMatrixWorld(true);
        const pairs = [];
        const tgtNames = new Set(rig.bones.keys());
        src.bones.forEach((sb, sn) => {
            const tn = tgtNames.has(sn) ? sn : (toUEBone(sb.name) || MIXAMO_TO_UE[sn]);
            const tb = tn && rig.bones.get(tn);
            if (tb) pairs.push({ sn, tn, sb, tb });
        });
        if (pairs.length < 6) throw new Error(`only ${pairs.length} bones match — unknown skeleton`);
        // rest poses (before the clip plays): world quaternions + directions to the first matched child
        // restore the rest pose on the mapped nodes only (FBX may nest same-name copies that must stay at identity)
        rig.bones.forEach((b, n) => { const r = rig.rest.get(n); if (r) { b.quaternion.copy(r.q); b.position.copy(r.p); } });
        if (rig.obj) rig.obj.updateMatrixWorld(true);
        rig.zUp.updateMatrixWorld(true);
        const wq = (o) => o.getWorldQuaternion(new THREE.Quaternion());
        const wp = (o) => o.getWorldPosition(new THREE.Vector3());
        // nearest matched bone below this one (it may sit under duplicate same-name nodes, so search descendants)
        const childDir = (bone, list) => {
            const queue = [...bone.children];
            while (queue.length) {
                const c = queue.shift();
                if (list.has(c)) { const d = wp(c).sub(wp(bone)); return d.lengthSq() > 1e-12 ? d.normalize() : null; }
                queue.push(...c.children);
            }
            return null;
        };
        const srcSet = new Set(pairs.map(p => p.sb)), tgtSet = new Set(pairs.map(p => p.tb));
        pairs.forEach(p => {
            p.sRest = wq(p.sb);
            p.tRest = wq(p.tb);
            const ds = childDir(p.sb, srcSet), dt = childDir(p.tb, tgtSet);
            p.corr = (ds && dt) ? new THREE.Quaternion().setFromUnitVectors(dt, ds) : new THREE.Quaternion();
        });
        const hip = pairs.find(p => p.tn === "pelvis" || /^(hips|pelvis)$/i.test(p.sn)) || pairs[0];
        const srcH = (() => { const b = new THREE.Box3(); src.bones.forEach(x => b.expandByPoint(wp(x))); return Math.max(0.001, b.max.z - b.min.z); })();
        const tgtH = (() => { const b = new THREE.Box3(); rig.bones.forEach(x => b.expandByPoint(wp(x))); return Math.max(0.001, b.max.z - b.min.z); })();
        // order parents before children so parent world rotations are final when a child is solved
        const depth = (o) => { let d = 0; while (o.parent) { d++; o = o.parent; } return d; };
        pairs.sort((a, b) => depth(a.tb) - depth(b.tb));
        const action = src.mixer.clipAction(srcClip); action.play();
        return { src, pivot, pairs, hip, hipSrcRest: wp(hip.sb), ratio: tgtH / srcH, action, clip: srcClip };
    }

    function applyRetarget(rig, t) {
        const R = rig.retarget;
        R.src.mixer.setTime(t);
        R.pivot.updateMatrixWorld(true);
        if (rig.obj) rig.obj.updateMatrixWorld(true);
        rig.zUp.updateMatrixWorld(true);
        const tmpQ = new THREE.Quaternion(), parentQ = new THREE.Quaternion();
        R.pairs.forEach(p => {
            const sNow = p.sb.getWorldQuaternion(tmpQ.clone());
            // Source rotation delta from rest
            const sDelta = sNow.multiply(p.sRest.clone().invert());
            p.tb.parent.getWorldQuaternion(parentQ);
            // Target world rotation = delta applied through corr onto target rest orientation
            const world = sDelta.multiply(p.corr).multiply(p.tRest);
            p.tb.quaternion.copy(parentQ.invert().multiply(world));
            p.tb.updateMatrixWorld(true);
        });

        // Root motion of the hips: calculate delta in world space and apply in local parent space
        // Correct unit scaling: parent (root) has scale 0.01 (centimetres), so world delta must be divided by parentWorldScale!
        const charObj = rig.obj || (selectedObject && selectedObject.userData.id === rig.id ? selectedObject : null);
        if (charObj && charObj.userData && charObj.userData.enableHipsMotion === false) {
            const rest = (rig.rest && rig.rest.get(R.hip.tn)) || { p: R.hip.tb.position };
            R.hip.tb.position.copy(rest.p);
            R.hip.tb.updateMatrixWorld(true);
        } else {
            const sHip = R.hip.sb.getWorldPosition(new THREE.Vector3());
            const sHipDelta = sHip.sub(R.hipSrcRest).multiplyScalar(R.ratio);
            if (charObj && charObj.userData && charObj.userData.bakeRootMode === "pivot") {
                sHipDelta.x = 0;
                sHipDelta.y = 0;
            }
            const rest = (rig.rest && rig.rest.get(R.hip.tn)) || { p: R.hip.tb.position };
            const localRest = rest.p.clone();
            const parentRotInv = R.hip.tb.parent.getWorldQuaternion(new THREE.Quaternion()).invert();
            const parentWorldScale = R.hip.tb.parent.getWorldScale(new THREE.Vector3());
            const localDelta = sHipDelta.applyQuaternion(parentRotInv).divide(parentWorldScale);
            R.hip.tb.position.copy(localRest.add(localDelta));
            R.hip.tb.updateMatrixWorld(true);
        }
    }

    async function setCharacterAnimation(obj, url, clipName, recordUndo = true) {
        const rig = characterRigs.get(obj.userData.id); if (!rig) throw new Error("not a character");
        const { root, clips } = await loadAnyModel(url, url);
        if (!clips.length) throw new Error("the file has no animation");
        // asked-for clip, else the longest one (files often start with a 0-length bind/T-pose take)
        const clip = clips.find(c => c.name === clipName) || clips.reduce((a, c) => (c.duration > a.duration ? c : a), clips[0]);
        rig.animClips = clips.filter(c => c.duration > 0).map(c => ({ name: c.name, duration: c.duration }));
        if (recordUndo) pushUndoState("Set Character Animation");
        obj.userData.animUrl = url;
        // same skeleton (bone names match) -> play the clip directly; otherwise retarget
        const trackBones = new Set(clip.tracks.map(tr => canonBone(tr.name.split(".")[0])));
        const direct = [...trackBones].filter(n => rig.bones.has(n)).length >= Math.max(6, trackBones.size * 0.7);
        if (direct) {
            const renamed = clip.clone();
            renamed.tracks.forEach(tr => { const [b, ...rest] = tr.name.split("."); const tb = rig.bones.get(canonBone(b)); if (tb) tr.name = [tb.name, ...rest].join("."); });
            playClipOn(obj, renamed, null);
        } else {
            playClipOn(obj, clip, buildRetarget(root, clip, rig));
        }
        logMsg(`✓ Animation «${clip.name}» on «${obj.userData.name}» (${direct ? "same skeleton" : "retargeted"}, ${clip.duration.toFixed(2)} s)`);
        refreshAnimClipSelect();
        return clip;
    }

    // ---- 3D characters -> OpenPose (COCO-18) keypoints through the shot camera, per frame ----------------
    function rigJointLookup(rig) {
        if (rig._ue) return rig._ue;
        const m = new Map();
        rig.bones.forEach((b, n) => { if (!m.has(n)) m.set(n, b); });
        rig.bones.forEach((b) => { const ue = toUEBone(b.name); if (ue && !m.has(ue)) m.set(ue, b); });
        rig._ue = m;
        return m;
    }
    function characterOpenPose3D(rig) {
        const J = rigJointLookup(rig);
        const P = (n) => { const b = J.get(n); return b ? b.getWorldPosition(new THREE.Vector3()) : null; };
        const ls = P("upperarm_l"), rs = P("upperarm_r"), head = P("head");
        if (!ls || !rs || !head) return null;
        const neck = ls.clone().add(rs).multiplyScalar(0.5);
        const side = ls.clone().sub(rs); const shoulderW = Math.max(side.length(), 1e-4); side.normalize();
        const up = head.clone().sub(P("neck_01") || neck).normalize();
        const fwd = new THREE.Vector3().crossVectors(side, up).normalize();
        const k = shoulderW / 0.34;                            // face proportions follow the body size
        const hc = head.clone().addScaledVector(up, 0.09 * k);
        const at = (f, u, sd) => hc.clone().addScaledVector(fwd, f * k).addScaledVector(up, u * k).addScaledVector(side, sd * k);
        return [at(0.095, -0.01, 0), neck, rs, P("lowerarm_r"), P("hand_r"), ls, P("lowerarm_l"), P("hand_l"),
            P("thigh_r"), P("calf_r"), P("foot_r"), P("thigh_l"), P("calf_l"), P("foot_l"),
            at(0.075, 0.025, -0.032), at(0.075, 0.025, 0.032), at(-0.005, 0.0, -0.075), at(-0.005, 0.0, 0.075)];
    }
    async function exportPoseLayerFrom3D(opts = {}) {
        const cardProgress = document.getElementById("render-progress-card");
        const fillProgress = document.getElementById("render-progress-fill");
        const lblProgress = document.getElementById("render-progress-label");
        if (cardProgress) cardProgress.style.display = "flex";
        if (fillProgress) fillProgress.style.width = "10%";
        if (lblProgress) lblProgress.innerText = uiText("Projecting 3D OpenPose...", "Проекция 3D OpenPose...");

        try {
            const isSyntheticVidId = (id) => !id || id === "baked_layout_sequence" || id === "live_video_render" || String(id).startsWith("vid_3d_");
            const src = { ...(window.__vfxSceneSource || {}), ...(opts.video ? { video: opts.video, startFrame: opts.startFrame || 0 } : {}) };
            if (isSyntheticVidId(src.video)) src.video = null;
            const activeVid = typeof window.__vfxGetActiveVideoId === "function" ? window.__vfxGetActiveVideoId() : null;
            if (!src.video && !isSyntheticVidId(activeVid)) {
                src.video = activeVid;
            }
            if (!src.video) {
                const vidList = (typeof mediaVideos2D !== "undefined" && Array.isArray(mediaVideos2D)) ? mediaVideos2D : [];
                const existingVid = vidList.find(v => v && v.id && !isSyntheticVidId(v.id) && !v.hasAlpha && v.badge !== "ALPHA" && v.badge !== "BAKED")
                    || vidList.find(v => v && v.id && !isSyntheticVidId(v.id));
                if (existingVid) {
                    src.video = existingVid.id;
                } else {
                    const autoVidId = "vid_3d_stage";
                    const autoVid = {
                        id: autoVidId,
                        title: "3D Stage Pose Sequence",
                        badge: "3D STAGE",
                        type: "html5",
                        width: opts.width || plateWidth || 1280,
                        height: opts.height || plateHeight || 720,
                        fps: projectFPS || 25,
                        total_frames: opts.frames || totalFrames || 49
                    };
                    if (Array.isArray(mediaVideos2D)) mediaVideos2D.unshift(autoVid);
                    src.video = autoVidId;
                    if (typeof activeVideoId !== "undefined") activeVideoId = autoVidId;
                }
            }
            const chars = [...threeObjectsMap.values()].filter(o => o.userData.type === "character" && o.visible !== false && characterRigs.has(o.userData.id)
                && (!opts.only || opts.only.includes(o.userData.id)));
            if (!chars.length) {
                throw new Error(uiText("Add a character or skeleton first (👤+)", "Сначала добавьте скелет или персонажа (👤+)"));
            }

            const candidateCams = [
                (!isShotCameraDeleted && cameraMatrices && cameraMatrices.length) ? shotCamera : null,
                getActiveMonitorCamera(),
                shotCamera,
                flyCamera
            ].filter((c, idx, arr) => c && arr.indexOf(c) === idx);
            if (!candidateCams.length) {
                throw new Error(uiText("No camera in 3D scene", "В 3D сцене нет камеры"));
            }

            const keepFrame = currentFrame;
            let frames = [], visible = 0, dbg = null;
            const outW = opts.width || plateWidth || 1920, outH = opts.height || plateHeight || 1080;
            const nFrames = opts.frames || totalFrames || 49;

            for (const cam of candidateCams) {
                const keepAspect = cam.aspect;
                frames = [];
                visible = 0;
                dbg = null;
                try {
                    cam.aspect = outW / outH;
                    cam.updateProjectionMatrix();
                    for (let f = 0; f < nFrames; f++) {
                        currentFrame = f;
                        if (cameraMatrices && cameraMatrices.length) updateShotCameraPose(f);
                        evaluateAnimation();
                        scene.updateMatrixWorld(true);
                        try { updateCharactersAtFrame(); } catch (_) {}
                        scene.updateMatrixWorld(true);
                        if (cam.parent) cam.parent.updateMatrixWorld(true);
                        cam.updateMatrixWorld(true);
                        const people = [];
                        chars.forEach(o => {
                            const pts = characterOpenPose3D(characterRigs.get(o.userData.id)); if (!pts) return;
                            people.push(pts.map(v => {
                                if (!v) return null;
                                const n = v.clone().project(cam);
                                if (!dbg) dbg = { cam: cam.getWorldPosition(new THREE.Vector3()).toArray().map(x => +x.toFixed(2)), joint: v.toArray().map(x => +x.toFixed(2)), ndc: n.toArray().map(x => +x.toFixed(2)) };
                                if (n.z < -1 || n.z > 1) return null;    // behind the camera / clipped
                                visible++;
                                return [+((n.x + 1) / 2).toFixed(5), +((1 - n.y) / 2).toFixed(5)];
                            }));
                        });
                        frames.push({ frame: (src.startFrame || 0) + f, people });
                        if (f % 10 === 0) {
                            const pct = 10 + Math.round(((f + 1) / Math.max(1, nFrames)) * 45);
                            if (fillProgress) fillProgress.style.width = pct + "%";
                            if (lblProgress) lblProgress.innerText = `${uiText("Projecting 3D OpenPose", "Проекция 3D OpenPose")} ${f + 1}/${nFrames}...`;
                            await new Promise(r => setTimeout(r, 0));
                        }
                    }
                } finally {
                    cam.aspect = keepAspect;
                    cam.updateProjectionMatrix();
                    currentFrame = keepFrame;
                    onFrameChanged();
                }
                if (visible > 0) break;
            }

            if (opts.dryRun) {
                return { visible, frames: frames.length, debug: dbg, keypoints: frames };
            }
            if (!visible) {
                throw new Error(uiText("Characters are not in the camera view — place them in front of the camera", "Персонажи не попадают в кадр камеры — разместите их перед камерой"));
            }

            if (fillProgress) fillProgress.style.width = "60%";
            if (lblProgress) lblProgress.innerText = uiText("Rendering OpenPose frames...", "Отрисовка кадров OpenPose...");
            const pid = (window.currentProject && window.currentProject.id) || (typeof localStorage !== "undefined" ? localStorage.getItem("vfx_last_project") : "") || "sandbox_playground";
            const payload = { name: `Pose from 3D (${chars.map(o => o.userData.name).join(", ")})`.slice(0, 80), width: outW, height: outH, frames };
            const r = await fetch(`/vfx/video/${encodeURIComponent(pid)}/${encodeURIComponent(src.video)}/pose_from_3d`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).then(x => x.json());
            if (r.error) {
                throw new Error(r.error);
            }
            if (activeVid && activeVid !== src.video && !isSyntheticVidId(activeVid)) {
                try {
                    await fetch(`/vfx/video/${encodeURIComponent(pid)}/${encodeURIComponent(activeVid)}/pose_from_3d`, {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(payload)
                    });
                } catch (_) {}
            }
            if (fillProgress) fillProgress.style.width = "100%";
            if (lblProgress) lblProgress.innerText = uiText("✅ OpenPose layer ready!", "✅ Слой позы OpenPose готов!");
            if (typeof window.__vfxOn3DLayerExported === "function") {
                try {
                    await window.__vfxOn3DLayerExported({ videoId: src.video, activeVid, layer: r.layer, frames: r.frames, kind: "pose" });
                } catch (_) {}
            }
            logMsg(`✓ Pose layer «${r.layer.name}»: ${r.frames} frames on video ${src.video}`);
            return r;
        } finally {
            setTimeout(() => { if (cardProgress) cardProgress.style.display = "none"; }, 1200);
        }
    }
    window.__vfxPoseFrom3D = exportPoseLayerFrom3D;

    // ---- 3D characters rendered through the shot camera -> an RGB layer (layout-to-render input, first-frame guide) ----
    async function exportRenderLayerFrom3D(opts = {}) {
        const cardProgress = document.getElementById("render-progress-card");
        const fillProgress = document.getElementById("render-progress-fill");
        const lblProgress = document.getElementById("render-progress-label");
        if (cardProgress) cardProgress.style.display = "flex";
        if (fillProgress) fillProgress.style.width = "5%";
        if (lblProgress) lblProgress.innerText = uiText("Rendering 3D pass...", "Рендер 3D-слоя через камеру...");

        const isSyntheticVidId = (id) => !id || id === "baked_layout_sequence" || id === "live_video_render" || String(id).startsWith("vid_3d_");
        const src = { ...(window.__vfxSceneSource || {}), ...(opts.video ? { video: opts.video, startFrame: opts.startFrame || 0 } : {}) };
        if (isSyntheticVidId(src.video)) src.video = null;
        const activeVid = typeof window.__vfxGetActiveVideoId === "function" ? window.__vfxGetActiveVideoId() : null;
        if (!src.video && !isSyntheticVidId(activeVid)) {
            src.video = activeVid;
        }
        if (!src.video) {
            const vidList = (typeof mediaVideos2D !== "undefined" && Array.isArray(mediaVideos2D)) ? mediaVideos2D : [];
            const existingVid = vidList.find(v => v && v.id && !isSyntheticVidId(v.id) && !v.hasAlpha && v.badge !== "ALPHA" && v.badge !== "BAKED")
                || vidList.find(v => v && v.id && !isSyntheticVidId(v.id));
            if (existingVid) src.video = existingVid.id;
        }
        if (!src.video) {
            if (cardProgress) cardProgress.style.display = "none";
            throw new Error(uiText("No target video in project", "В проекте нет целевого видео"));
        }
        const cam = (!isShotCameraDeleted && cameraMatrices && cameraMatrices.length) ? shotCamera : (getActiveMonitorCamera() || shotCamera);
        if (!cam) {
            if (cardProgress) cardProgress.style.display = "none";
            throw new Error(uiText("No camera in 3D scene", "В 3D сцене нет камеры"));
        }
        let outW = opts.width || plateWidth || 1920, outH = opts.height || plateHeight || 1080;
        if (Math.max(outW, outH) > 1920) {
            const sc = 1920 / Math.max(outW, outH);
            outW = Math.max(2, Math.round(outW * sc) & ~1);
            outH = Math.max(2, Math.round(outH * sc) & ~1);
        }
        // what goes into the pass: characters (clay) and/or the primitive set dressing (own colours)
        const withChars = opts.characters !== false;
        const PROP_TYPES = new Set(["box", "sphere", "cylinder", "cone", "torus", "plane", "mesh", "glb"]);
        const chars = !withChars ? [] : [...threeObjectsMap.values()].filter(o => o.userData.type === "character" && characterRigs.has(o.userData.id)
            && (!opts.only || opts.only.includes(o.userData.id)));
        const includeProps = opts.props || chars.length === 0;
        const props = !includeProps ? [] : [...threeObjectsMap.values()].filter(o => PROP_TYPES.has(o.userData.type) && o.visible !== false);
        if (!chars.length && !props.length) {
            if (cardProgress) cardProgress.style.display = "none";
            throw new Error(uiText("Nothing to render (add a character or 3D object)", "Нечего рендерить (добавьте персонажа или 3D объект)"));
        }
        const r = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
        r.setPixelRatio(1); r.setSize(outW, outH, false);
        // only the chosen characters and the lights; helpers, grid, cloud, other objects hidden
        const keepVis = new Map();
        const charSet = new Set([...chars, ...props]);
        const skelOnlyHelpers = new Set(chars.filter(o => o.userData?.skeletonOnly).map(o => characterRigs.get(o.userData.id)?.helper).filter(Boolean));
        scene.children.forEach(o => {
            keepVis.set(o, o.visible);
            const isUserLight = o.userData?.type === "light" && o.visible !== false;
            if (isUserLight && o.userData?.lightGizmo) {
                keepVis.set(o.userData.lightGizmo, o.userData.lightGizmo.visible);
                o.userData.lightGizmo.visible = false;
            }
            if (!(o.isLight || isUserLight || charSet.has(o) || skelOnlyHelpers.has(o))) o.visible = false;
        });
        const helpersToHide = chars.filter(o => !o.userData?.skeletonOnly).map(o => characterRigs.get(o.userData.id)?.helper).filter(Boolean);
        const keepBg = scene.background, keepFrame = currentFrame, keepAspect = cam.aspect;
        scene.background = new THREE.Color(opts.background || "#d4d4d4");
        const hemi = new THREE.HemisphereLight(0xffffff, 0x666666, 0.75); scene.add(hemi);
        const key = new THREE.DirectionalLight(0xffffff, 0.8); key.position.set(3, -4, 6); scene.add(key);
        // clay look: imported characters often miss their textures (black); a layout pass wants clean shading anyway
        const keepOverride = scene.overrideMaterial;
        const hasSkelOnly = chars.some(o => o.userData?.skeletonOnly);
        if (opts.material !== "original" && !props.length && !hasSkelOnly) {
            const clay = new THREE.MeshLambertMaterial({ color: 0x8c8c8c });
            clay.skinning = true;   // older three.js: without it skinned meshes render in their bind pose
            scene.overrideMaterial = clay;
        }
        const nFrames = opts.frames || totalFrames || 49;
        let layerId = null, lastLayer = null, sent = 0, batch = [];
        const layerName = opts.name || `3D render (${[...chars, ...props].map(o => o.userData.name).join(", ")})`.slice(0, 80);
        const pid = (window.currentProject && window.currentProject.id) || (typeof localStorage !== "undefined" ? localStorage.getItem("vfx_last_project") : "") || "sandbox_playground";
        const flush = async () => {
            if (!batch.length) return;
            const res = await fetch(`/vfx/video/${encodeURIComponent(pid)}/${encodeURIComponent(src.video)}/layer_frames`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ layer_id: layerId, name: layerName, type: "rgb", frames: batch })
            }).then(x => x.json());
            if (res.error) throw new Error(res.error);
            layerId = res.layer.id; lastLayer = res.layer; sent += res.frames; batch = [];
        };
        isBakingLayout = true;
        try {
            helpersToHide.forEach(h => { h.visible = false; });
            skelOnlyHelpers.forEach(h => { h.visible = true; });
            cam.aspect = outW / outH; cam.updateProjectionMatrix();
            for (let f = 0; f < nFrames; f++) {
                currentFrame = f;
                if (cameraMatrices && cameraMatrices.length) updateShotCameraPose(f);
                evaluateAnimation();
                scene.updateMatrixWorld(true);
                try { updateCharactersAtFrame(); } catch (_) {}
                scene.children.forEach(o => {
                    const isUserLight = o.userData?.type === "light" && o.visible !== false;
                    if (isUserLight && o.userData?.lightGizmo) o.userData.lightGizmo.visible = false;
                    if (!(o.isLight || isUserLight || charSet.has(o) || skelOnlyHelpers.has(o))) o.visible = false;
                });
                helpersToHide.forEach(h => { h.visible = false; });
                skelOnlyHelpers.forEach(h => { h.visible = true; });
                scene.updateMatrixWorld(true);
                if (cam.parent) cam.parent.updateMatrixWorld(true);
                cam.updateMatrixWorld(true);
                r.render(scene, cam);
                batch.push({ frame: (src.startFrame || 0) + f, data: r.domElement.toDataURL("image/jpeg", 0.92) });
                if (fillProgress) fillProgress.style.width = Math.round(((f + 1) / nFrames) * 95) + "%";
                if (lblProgress) lblProgress.innerText = `${uiText("Rendering 3D frame", "Рендер 3D кадра")} ${f + 1}/${nFrames}...`;
                if (batch.length >= 12) await flush();
            }
            await flush();
            if (fillProgress) fillProgress.style.width = "100%";
            if (lblProgress) lblProgress.innerText = uiText("✅ 3D Render layer ready!", "✅ 3D-слой рендера готов!");
        } finally {
            isBakingLayout = false;
            scene.remove(hemi); scene.remove(key);
            scene.overrideMaterial = keepOverride;
            scene.background = keepBg;
            keepVis.forEach((v, o) => { o.visible = v; });
            cam.aspect = keepAspect; cam.updateProjectionMatrix();
            currentFrame = keepFrame; onFrameChanged();
            setTimeout(() => { if (cardProgress) cardProgress.style.display = "none"; }, 1200);
            r.dispose();
        }
        if (typeof window.__vfxOn3DLayerExported === "function") {
            try {
                await window.__vfxOn3DLayerExported({ videoId: src.video, activeVid, layer: lastLayer || { id: layerId, name: layerName, type: "rgb" }, frames: sent, kind: "rgb" });
            } catch (_) {}
        }
        logMsg(`✓ 3D render layer: ${sent} frames on video ${src.video}`);
        return { layer_id: layerId, layer: lastLayer, frames: sent };
    }
    window.__vfxRenderFrom3D = exportRenderLayerFrom3D;
    window.__vfxCreateCameraPreset = (preset, opts) => createDefaultCameraTrajectory(preset, opts || {});
    window.__vfxReplaceObjectWithGLB = (targetId, glbUrl, metaPatch = {}) => replaceObjectWithGLB(targetId, glbUrl, metaPatch);
    // scripted set dressing (agent / tests): a primitive with position, scale, rotation (deg) and colour
    window.__vfxAddProp = (type = "box", o = {}) => {
        const m = new THREE.Mesh(createPrimitiveGeometry(type), defMat(o.color != null ? new THREE.Color(o.color).getHex() : 0xcccccc, type === "plane"));
        if (o.position) m.position.fromArray(o.position);
        if (o.scale) m.scale.fromArray(o.scale);
        if (o.rotation) m.rotation.set(...o.rotation.map(d => THREE.MathUtils.degToRad(d)));
        registerObject(m, o.name || `${type}_${nextObjId}`, type, o.color ? { color: o.color } : {});
        if (o.color && typeof setActorColor === "function") setActorColor(m, o.color);
        if (o.roughness != null) m.material.roughness = o.roughness;
        return m.userData.id;
    };
    window.__vfxSetPropLook = (id, o = {}) => {
        const obj = threeObjectsMap.get(id); if (!obj) return false;
        if (o.color) setActorColor(obj, o.color);
        obj.traverse(c => { if (c.isMesh && c.material) { if (o.roughness != null) c.material.roughness = o.roughness; if (o.metalness != null) c.material.metalness = o.metalness; c.material.needsUpdate = true; } });
        if (o.visible != null) obj.visible = !!o.visible;
        syncSceneData(); return true;
    };

    // files with many takes (animation libraries): pick the clip for the selected character
    function refreshAnimClipSelect() {
        const sel = document.getElementById("sel-anim-clip"); if (!sel) return;
        const btnHips = document.getElementById("btn-toggle-hips-motion");
        const btnBake = document.getElementById("btn-bake-root-motion");
        const obj = selectedObject && selectedObject.userData && selectedObject.userData.type === "character" ? selectedObject : null;
        const rig = obj && characterRigs.get(obj.userData.id);
        const hasAnim = Boolean(obj && (obj.userData.animUrl || (rig && (rig.retarget || rig.action))));
        
        if (btnHips) {
            btnHips.style.display = hasAnim ? "" : "none";
            const hipsOn = obj ? obj.userData.enableHipsMotion !== false : true;
            btnHips.innerText = `Hips: ${hipsOn ? "ON" : "LOCK"}`;
            btnHips.className = hipsOn ? "vfx-btn vfx-btn-active" : "vfx-btn";
        }
        if (btnBake) {
            btnBake.style.display = (hasAnim && rig && rig.retarget) ? "" : "none";
            const isBaked = obj && obj.userData && obj.userData.bakeRootMode === "pivot";
            btnBake.innerText = isBaked ? "📌 Pivot: ON" : "📌 Root to Pivot";
            btnBake.className = isBaked ? "vfx-btn vfx-btn-active" : "vfx-btn";
        }

        const list = (rig && rig.animClips) || [];
        if (list.length < 2) { sel.style.display = "none"; return; }
        sel.innerHTML = "";
        list.forEach(c => { const o = document.createElement("option"); o.value = c.name; o.textContent = `${c.name} (${c.duration.toFixed(1)} s)`; if (c.name === obj.userData.animName) o.selected = true; sel.appendChild(o); });
        sel.style.display = "";
        sel.onchange = () => setCharacterAnimation(obj, obj.userData.animUrl, sel.value)
            .then(() => { syncSceneData(); renderStudioViewports(); })
            .catch(e => logMsg(`Animation: ${e.message}`));
    }
    window.__vfxRefreshAnimClips = refreshAnimClipSelect;

    function updateCharactersAtFrame() {
        if (!characterRigs.size) return;
        characterRigs.forEach((rig, id) => {
            const obj = threeObjectsMap.get(id);
            if (!obj) { scene.remove(rig.helper); characterRigs.delete(id); return; }
            rig.obj = obj;
            rig.id = id;
            rig.helper.visible = obj.visible !== false && obj.userData.showBones !== false;
            const dur = obj.userData.animDuration || 0;
            if (!dur) return;
            let t = (currentFrame - (obj.userData.animOffset || 0)) / (projectFPS || 24) * (obj.userData.animSpeed || 1);
            t = ((t % dur) + dur) % dur; // loop
            if (rig.retarget) applyRetarget(rig, t);
            else rig.mixer.setTime(t);
        });
    }
    window.__vfxCharacters3D = {
        addUrl: (url, name) => addCharacter(url, name || "Character", { fileName: url }),
        addSkeleton: (name) => addCharacter(MANNEQUIN_URL, name || "Skeleton", { fileName: "KESHIKI mannequin" }),
        animateUrl: async (url, targetId, clipName) => {
            const byId = targetId ? threeObjectsMap.get(targetId) : null;
            const obj = byId || (selectedObject && selectedObject.userData.type === "character" ? selectedObject : [...threeObjectsMap.values()].reverse().find(o => o.userData.type === "character"));
            if (!obj) throw new Error("add a character first");
            await setCharacterAnimation(obj, url, clipName); syncSceneData(); renderStudioViewports(); return obj;
        },
        add: async (file) => { const url = await uploadToProject(file); return addCharacter(url, file.name.replace(/\.[^/.]+$/, ""), { fileName: file.name }); },
        animate: async (file) => {
            const obj = selectedObject && selectedObject.userData.type === "character" ? selectedObject : [...threeObjectsMap.values()].reverse().find(o => o.userData.type === "character");
            if (!obj) throw new Error("add a character first");
            const url = await uploadToProject(file);
            await setCharacterAnimation(obj, url);
            syncSceneData(); renderStudioViewports();
            return obj;
        },
        toggleBones: () => { const o = selectedObject; if (o && o.userData.type === "character") { o.userData.showBones = o.userData.showBones === false; updateCharactersAtFrame(); renderStudioViewports(); } },
        rigs: characterRigs
    };

    const loadedGlbSceneCache = new Map();

    // Spawn a 3D GLB model with its native transforms, or replace a selected Cube/Primitive in-place when requested
    function replaceObjectWithGLB(targetId, glbUrl, metaPatch = {}, recordUndo = true) {
        return new Promise((resolve, reject) => {
            if (!THREE.GLTFLoader) {
                const err = "THREE.GLTFLoader is not available";
                logMsg(`ERROR: ${err}`);
                return reject(new Error(err));
            }

            let targetObj = (targetId && targetId !== "__spawn_new__" && threeObjectsMap.get(targetId)) || (targetId === "__spawn_new__" ? null : selectedObject);
            const isReplacingExisting = Boolean(targetObj);
            const actorTitle = metaPatch.name || metaPatch.actorName || metaPatch.glbFilename || `Mesh3D_${nextObjId}`;

            // Record Undo BEFORE creating/registering the placeholder so the Undo snapshot does NOT contain a phantom placeholder!
            if (recordUndo) {
                pushUndoState(isReplacingExisting ? "Replace Object with 3D GLB" : "Add 3D GLB to Scene");
            }

            if (!targetObj) {
                const placeholder = new THREE.Group();
                if (Array.isArray(metaPatch.position) && metaPatch.position.length === 3) {
                    placeholder.position.fromArray(metaPatch.position);
                } else {
                    placeholder.position.set(0, 0, 0);
                }
                if (Array.isArray(metaPatch.rotation) && metaPatch.rotation.length >= 3) {
                    placeholder.rotation.set(metaPatch.rotation[0] || 0, metaPatch.rotation[1] || 0, metaPatch.rotation[2] || 0);
                } else {
                    placeholder.rotation.set(0, 0, 0);
                }
                if (Array.isArray(metaPatch.scale) && metaPatch.scale.length === 3) {
                    placeholder.scale.fromArray(metaPatch.scale);
                } else {
                    placeholder.scale.set(1, 1, 1);
                }
                targetObj = registerObject(placeholder, actorTitle, "glb", {
                    ...metaPatch,
                    glbUrl: glbUrl,
                    fitMode: metaPatch.fitMode || "native"
                });
            }

            const myLoadGen = manifestLoadGen;
            const savedId = targetObj.userData.id;
            const savedPos = targetObj.position.clone();
            const savedQuat = targetObj.quaternion.clone();
            const savedScale = targetObj.scale.clone();
            const resolvedFitMode = metaPatch.fitMode
                || (!isReplacingExisting ? "native" : (targetObj.userData.type === "glb" ? (targetObj.userData.fitMode || "native") : (targetObj.userData.fitMode || "stretch")));
            const savedMeta = {
                ...targetObj.userData,
                ...metaPatch,
                name: metaPatch.name || metaPatch.actorName || targetObj.userData.name || actorTitle,
                id: savedId,
                type: "glb",
                glbUrl: glbUrl,
                fitMode: resolvedFitMode
            };

            const mountGlbFromPrototype = (protoScene) => {
                // Abort if Undo/Redo or another manifest load invalidated this request
                if (myLoadGen !== manifestLoadGen || threeObjectsMap.get(savedId) !== targetObj) {
                    purgeObjectArtifacts(targetObj);
                    scene.remove(targetObj);
                    return resolve(null);
                }

                const wrapperGroup = new THREE.Group();
                const zUpPivot = new THREE.Group();
                const modelRoot = protoScene.clone(true);

                // Clone & sanitize PBR materials so TRELLIS.2 / HY3D GLBs with metallicFactor=1.0 are never black without HDRI
                const sanitizeGlbMaterial = (mat) => {
                    if (!mat) return mat;
                    let m = mat;
                    if (m.isMeshBasicMaterial) {
                        m = new THREE.MeshStandardMaterial({
                            color: m.color ? m.color.clone() : new THREE.Color(0xffffff),
                            map: m.map || null,
                            roughness: 0.45,
                            metalness: 0.15
                        });
                    } else if (typeof m.clone === "function") {
                        m = m.clone();
                    }
                    if (typeof m.metalness === "number" && m.metalness > 0.35) {
                        m.metalness = 0.15;
                    }
                    if (typeof m.roughness === "number" && m.roughness > 0.85) {
                        m.roughness = 0.55;
                    }
                    if (m.map && m.color && (m.color.r + m.color.g + m.color.b) < 0.15) {
                        m.color.setHex(0xffffff);
                    }
                    if (m.map && THREE.sRGBEncoding && m.map.encoding !== THREE.sRGBEncoding) {
                        m.map.encoding = THREE.sRGBEncoding;
                        m.map.needsUpdate = true;
                    }
                    m.needsUpdate = true;
                    return m;
                };
                modelRoot.traverse((child) => {
                    if (child.isMesh) {
                        child.castShadow = true;
                        child.receiveShadow = true;
                        if (child.material) {
                            if (Array.isArray(child.material)) {
                                child.material = child.material.map(sanitizeGlbMaterial);
                            } else {
                                child.material = sanitizeGlbMaterial(child.material);
                            }
                        }
                    }
                });

                // Convert standard Y-up GLTF to our Z-up studio coordinate system
                zUpPivot.rotation.x = Math.PI / 2;
                zUpPivot.add(modelRoot);
                zUpPivot.updateMatrixWorld(true);

                const bbox = new THREE.Box3().setFromObject(zUpPivot);
                const size = new THREE.Vector3();
                const center = new THREE.Vector3();
                bbox.getSize(size);
                bbox.getCenter(center);

                const sx = Math.max(size.x, 0.001);
                const sy = Math.max(size.y, 0.001);
                const sz = Math.max(size.z, 0.001);

                const normHolder = new THREE.Group();
                normHolder.add(zUpPivot);

                if (savedMeta.fitMode === "native") {
                    zUpPivot.position.set(0, 0, 0);
                    normHolder.scale.set(1, 1, 1);
                } else if (savedMeta.fitMode === "uniform") {
                    zUpPivot.position.set(-center.x, -center.y, -center.z);
                    const maxDim = Math.max(sx, sy, sz);
                    normHolder.scale.setScalar(1.0 / maxDim);
                } else {
                    zUpPivot.position.set(-center.x, -center.y, -center.z);
                    normHolder.scale.set(1.0 / sx, 1.0 / sy, 1.0 / sz);
                }
                wrapperGroup.add(normHolder);

                if (Array.isArray(savedMeta.pivotOffset) && savedMeta.pivotOffset.length === 3) {
                    const po = new THREE.Vector3(
                        Number(savedMeta.pivotOffset[0]) || 0,
                        Number(savedMeta.pivotOffset[1]) || 0,
                        Number(savedMeta.pivotOffset[2]) || 0
                    );
                    if (po.lengthSq() > 1e-8) {
                        wrapperGroup.children.forEach(ch => {
                            ch.position.sub(po);
                            ch.updateMatrixWorld(true);
                        });
                    }
                }

                // Use live transform from targetObj in case it was moved/evaluated before load completed
                wrapperGroup.position.copy(targetObj.position || savedPos);
                wrapperGroup.quaternion.copy(targetObj.quaternion || savedQuat);
                wrapperGroup.scale.copy(targetObj.scale || savedScale);

                ensureActorUserData(wrapperGroup, savedMeta);

                const wasSelected = (selectedObject === targetObj) || selectedObjects.has(targetObj);

                // Remove targetObj and any duplicate/orphan in scene.children with the same ID
                if (transformControls.object === targetObj) {
                    transformControls.detach();
                }
                purgeObjectArtifacts(targetObj);
                scene.remove(targetObj);
                scene.children.slice().forEach(ch => {
                    if (ch && ch.userData && ch.userData.id === savedId) {
                        purgeObjectArtifacts(ch);
                        scene.remove(ch);
                    }
                });

                scene.add(wrapperGroup);
                threeObjectsMap.set(savedId, wrapperGroup);

                if (savedMeta.assignedImage) {
                    applyTextureToObject(savedId, savedMeta.assignedImage, false);
                }

                if (recordUndo || wasSelected) {
                    selectObject(wrapperGroup);
                } else {
                    updateSelectionHelpers();
                    updateOutliner();
                    notifyDetailsAndOutliner();
                    renderStudioViewports();
                }
                syncSceneData();
                logMsg(`Loaded 3D GLB [${savedMeta.name}] at Pos(${wrapperGroup.position.x.toFixed(2)}, ${wrapperGroup.position.y.toFixed(2)}, ${wrapperGroup.position.z.toFixed(2)})`);
                resolve(wrapperGroup);
            };

            if (loadedGlbSceneCache.has(glbUrl)) {
                mountGlbFromPrototype(loadedGlbSceneCache.get(glbUrl));
                return;
            }

            const loader = new THREE.GLTFLoader();
            loader.load(
                glbUrl,
                (gltf) => {
                    const rawRoot = gltf.scene || (gltf.scenes && gltf.scenes[0]);
                    if (rawRoot) {
                        loadedGlbSceneCache.set(glbUrl, rawRoot);
                    }
                    mountGlbFromPrototype(rawRoot);
                },
                undefined,
                (err) => {
                    logMsg(`Failed to load GLB (${glbUrl}): ${err?.message || err}`);
                    reject(err);
                }
            );
        });
    }

    async function parseSplatPLY(url, maxPoints = 25000) {
        try {
            const resp = await fetch(url);
            if (!resp.ok) return null;
            const buffer = await resp.arrayBuffer();
            const headerText = new TextDecoder("ascii").decode(new Uint8Array(buffer, 0, Math.min(buffer.byteLength, 4096)));
            const endHeaderIdx = headerText.indexOf("end_header\n");
            if (endHeaderIdx === -1) return null;
            const header = headerText.substring(0, endHeaderIdx);
            const match = header.match(/element vertex (\d+)/);
            const numVertices = match ? parseInt(match[1], 10) : 0;
            if (numVertices <= 0) return null;

            const dataOffset = endHeaderIdx + "end_header\n".length;
            const isStandard3DGS = header.includes("f_dc_0");
            const isRgbUchar = header.includes("uchar red") || header.includes("uint8 red");

            // Calculate vertex byte stride
            const lines = header.split("\n");
            let stride = 0;
            for (const line of lines) {
                const trimmed = line.trim();
                if (trimmed.startsWith("property ")) {
                    const parts = trimmed.split(/\s+/);
                    const type = parts[1];
                    if (type === "float" || type === "int" || type === "uint") stride += 4;
                    else if (type === "double") stride += 8;
                    else if (type === "uchar" || type === "uint8" || type === "char" || type === "int8") stride += 1;
                    else if (type === "short" || type === "ushort") stride += 2;
                }
            }
            if (stride === 0) stride = 62 * 4;

            const count = Math.min(numVertices, maxPoints);
            const step = Math.max(1, Math.floor(numVertices / count));
            const actualCount = Math.floor(numVertices / step);

            const positions = new Float32Array(actualCount * 3);
            const colors = new Float32Array(actualCount * 3);
            const dataView = new DataView(buffer, dataOffset);

            let outIdx = 0;
            for (let i = 0; i < numVertices && outIdx < actualCount; i += step) {
                const vOffset = i * stride;
                if (vOffset + 12 > dataView.byteLength) break;
                const x = dataView.getFloat32(vOffset, true);
                const y = dataView.getFloat32(vOffset + 4, true);
                const z = dataView.getFloat32(vOffset + 8, true);

                positions[outIdx * 3] = x;
                positions[outIdx * 3 + 1] = y;
                positions[outIdx * 3 + 2] = z;

                if (isStandard3DGS && vOffset + 36 <= dataView.byteLength) {
                    const c0 = 0.28209479177387814;
                    const r = Math.max(0, Math.min(1, 0.5 + c0 * dataView.getFloat32(vOffset + 24, true)));
                    const g = Math.max(0, Math.min(1, 0.5 + c0 * dataView.getFloat32(vOffset + 28, true)));
                    const b = Math.max(0, Math.min(1, 0.5 + c0 * dataView.getFloat32(vOffset + 32, true)));
                    colors[outIdx * 3] = r;
                    colors[outIdx * 3 + 1] = g;
                    colors[outIdx * 3 + 2] = b;
                } else if (isRgbUchar && vOffset + 15 <= dataView.byteLength) {
                    colors[outIdx * 3] = dataView.getUint8(vOffset + 12) / 255.0;
                    colors[outIdx * 3 + 1] = dataView.getUint8(vOffset + 13) / 255.0;
                    colors[outIdx * 3 + 2] = dataView.getUint8(vOffset + 14) / 255.0;
                } else {
                    colors[outIdx * 3] = 0.22;
                    colors[outIdx * 3 + 1] = 0.74;
                    colors[outIdx * 3 + 2] = 0.97;
                }
                outIdx++;
            }
            return { positions, colors, count: outIdx };
        } catch (e) {
            console.warn("Could not parse PLY buffer:", e);
            return null;
        }
    }

    function generateSyntheticSplatBuffer(count = 10000, scale = [1.0, 1.0, 1.0]) {
        const positions = new Float32Array(count * 3);
        const colors = new Float32Array(count * 3);
        const sx = scale[0] || 1.0;
        const sy = scale[1] || 1.0;
        const sz = scale[2] || 1.0;
        for (let i = 0; i < count; i++) {
            const u = Math.random();
            const v = Math.random();
            const theta = u * 2.0 * Math.PI;
            const phi = Math.acos(2.0 * v - 1.0);
            const r = Math.cbrt(Math.random()) * 0.9;

            const x = r * Math.sin(phi) * Math.cos(theta) * sx;
            const y = r * Math.sin(phi) * Math.sin(theta) * sy;
            const z = r * Math.cos(phi) * sz;

            positions[i * 3] = x;
            positions[i * 3 + 1] = y;
            positions[i * 3 + 2] = z;

            const normZ = (z / Math.max(0.1, sz) + 1) * 0.5;
            colors[i * 3] = Math.max(0, Math.min(1, 0.2 + 0.6 * normZ));
            colors[i * 3 + 1] = Math.max(0, Math.min(1, 0.5 + 0.4 * (1 - normZ)));
            colors[i * 3 + 2] = 0.95;
        }
        return { positions, colors, count };
    }

    async function addSplatActor(splatData = {}) {
        pushUndoState("Add 3D Gaussian Splat Actor");
        const actorId = splatData.id || `splat_${Date.now()}`;
        const actorName = splatData.actor_name || splatData.title || `Gaussian_Splat_${nextObjId++}`;
        const splatGroup = new THREE.Group();

        // Position exactly at 3D Cursor
        if (cursor3DPosition && cursor3DPosition.clone) {
            splatGroup.position.copy(cursor3DPosition);
        } else {
            splatGroup.position.set(0, 0, 0);
        }

        const scaleArr = Array.isArray(splatData.scale) && splatData.scale.length === 3 ? splatData.scale : [1.0, 1.0, 1.0];
        splatGroup.scale.set(scaleArr[0], scaleArr[1], scaleArr[2]);

        let parsed = null;
        if (splatData.ply_url) {
            parsed = await parseSplatPLY(splatData.ply_url);
        }
        if (!parsed || parsed.count === 0) {
            parsed = generateSyntheticSplatBuffer(splatData.points_count || 10000, scaleArr);
        }

        const ptGeom = new THREE.BufferGeometry();
        ptGeom.setAttribute("position", new THREE.BufferAttribute(parsed.positions, 3));
        ptGeom.setAttribute("color", new THREE.BufferAttribute(parsed.colors, 3));
        ptGeom.computeBoundingSphere();

        const ptMat = new THREE.PointsMaterial({
            size: 0.045,
            map: lidarDiscTexture,
            alphaTest: 0.05,
            sizeAttenuation: true,
            vertexColors: true,
            transparent: true,
            opacity: 0.90
        });
        const ptsMesh = new THREE.Points(ptGeom, ptMat);
        splatGroup.add(ptsMesh);

        registerObject(splatGroup, actorName, "splat", {
            id: actorId,
            splatUrl: splatData.ply_url || splatData.url || "",
            pointsCount: parsed.count,
            engine: splatData.engine || "3DGS",
            actorName: actorName
        });

        logMsg(`[3DGS] Added Gaussian Splat actor «${actorName}» (${parsed.count} points) at 3D cursor [${cursor3DPosition.x.toFixed(2)}, ${cursor3DPosition.y.toFixed(2)}, ${cursor3DPosition.z.toFixed(2)}]`);
        if (typeof showToastNotification === "function") {
            showToastNotification(uiText(`Added 3DGS «${actorName}» at 3D-cursor`, `3DGS «${actorName}» добавлен в сцену по 3D-курсору`));
        }
        return splatGroup;
    }
    window.__vfxAddSplatActor = addSplatActor;

    function swapObjectPrimitive(targetId, primType, recordUndo = true) {
        const targetObj = (targetId && threeObjectsMap.get(targetId)) || selectedObject;
        if (!targetObj) return null;
        if (recordUndo) pushUndoState(`Swap Mesh to ${primType}`);

        const savedId = targetObj.userData.id;
        const savedPos = targetObj.position.clone();
        const savedQuat = targetObj.quaternion.clone();
        const savedScale = targetObj.scale.clone();
        const savedMeta = {
            ...targetObj.userData,
            type: primType,
            glbUrl: ""
        };

        const geom = createPrimitiveGeometry(primType);
        const mat = defMat(savedMeta.color || 0xcccccc, primType === "plane");
        const newMesh = new THREE.Mesh(geom, mat);
        newMesh.position.copy(savedPos);
        newMesh.quaternion.copy(savedQuat);
        newMesh.scale.copy(savedScale);
        ensureActorUserData(newMesh, savedMeta);

        transformControls.detach();
        scene.remove(targetObj);
        scene.add(newMesh);
        threeObjectsMap.set(savedId, newMesh);

        if (savedMeta.assignedImage) {
            applyTextureToObject(savedId, savedMeta.assignedImage, false);
        }

        selectObject(newMesh);
        syncSceneData();
        return newMesh;
    }

    function applyTextureToObject(targetId, imageUrl, recordUndo = true) {
        const targetObj = (targetId && threeObjectsMap.get(targetId)) || selectedObject;
        if (!targetObj) return;
        if (recordUndo) pushUndoState("Apply Texture to Actor");

        targetObj.userData.assignedImage = imageUrl || "";
        if (!imageUrl) {
            targetObj.traverse((child) => {
                if (child.isMesh && child.material) {
                    child.material.map = null;
                    child.material.needsUpdate = true;
                }
            });
            syncSceneData();
            notifyDetailsAndOutliner();
            return;
        }

        textureLoader.load(imageUrl, (tex) => {
            tex.colorSpace = THREE.SRGBColorSpace || undefined;
            targetObj.traverse((child) => {
                if (child.isMesh && child.material) {
                    child.material.map = tex;
                    if (child.material.color) child.material.color.setHex(0xffffff);
                    child.material.needsUpdate = true;
                }
            });
            syncSceneData();
            notifyDetailsAndOutliner();
            logMsg(`Applied texture to ${targetObj.userData.name}`);
        });
    }

    function duplicateSelectedObject() {
        if (!selectedObject) return null;
        pushUndoState("Duplicate Object");
        const src = selectedObject;
        const newName = `${src.userData.name}_Copy`;
        const extraProps = {
            ...src.userData,
            id: undefined,
            name: newName,
            keyframes: Array.isArray(src.userData.keyframes) ? JSON.parse(JSON.stringify(src.userData.keyframes)) : []
        };
        if (src.userData.type === "light") {
            const newLight = addSceneLight(src.userData.lightSubType || "sun", {
                ...extraProps,
                position: [src.position.x + 0.5, src.position.y + 0.5, src.position.z],
                rotation: [src.rotation.x, src.rotation.y, src.rotation.z],
                scale: [src.scale.x, src.scale.y, src.scale.z]
            }, false);
            return newLight;
        }
        if (src.userData.type === "glb" && src.userData.glbUrl) {
            const tempGrp = new THREE.Group();
            tempGrp.position.copy(src.position).add(new THREE.Vector3(0.4, 0.4, 0));
            tempGrp.quaternion.copy(src.quaternion);
            tempGrp.scale.copy(src.scale);
            const reg = registerObject(tempGrp, newName, "glb", extraProps);
            replaceObjectWithGLB(reg.userData.id, src.userData.glbUrl, { name: newName, fitMode: src.userData.fitMode || "native" }, false);
            return reg;
        }
        const cloneMesh = new THREE.Mesh(createPrimitiveGeometry(src.userData.type), defMat(src.userData.color || 0xcccccc, src.userData.type === "plane"));
        cloneMesh.position.copy(src.position).add(new THREE.Vector3(0.4, 0.4, 0));
        cloneMesh.quaternion.copy(src.quaternion);
        cloneMesh.scale.copy(src.scale);
        return registerObject(cloneMesh, newName, src.userData.type, extraProps);
    }

    function resetSelectedObjectTransform() {
        if (!selectedObject) return;
        pushUndoState("Reset Transform");
        const isNativeGlb = selectedObject.userData?.type === "glb" && selectedObject.userData?.fitMode === "native";
        selectedObject.position.set(0, 0, isNativeGlb ? 0.0 : 0.5);
        selectedObject.rotation.set(0, 0, 0);
        selectedObject.scale.set(1, 1, 1);
        updateTransformInspector();
        syncSceneData();
    }

    function snapSelectedObjectToFloor() {
        if (!selectedObject) return;
        pushUndoState("Snap Actor to Floor Z=0");
        const halfZ = selectedObject.userData.type === "plane" ? 0.0 : (Math.abs(selectedObject.scale.z) * 0.5);
        selectedObject.position.z = halfZ;
        updateTransformInspector();
        syncSceneData();
    }

    const btnDup = document.getElementById("btn-duplicate-obj");
    if (btnDup) btnDup.onclick = duplicateSelectedObject;
    const btnResetXf = document.getElementById("btn-reset-xform");
    if (btnResetXf) btnResetXf.onclick = resetSelectedObjectTransform;

    const btnCamView = document.getElementById("btn-cam-view");
    const btnSidebarOrbitCam = document.getElementById("btn-sidebar-3d-orbit-cam");
    if (btnCamView) {
        btnCamView.onclick = () => {
            const monCam = getActiveMonitorCamera();
            if (!monCam && !isShotCamActive) {
                logMsg("No active camera in scene to view through.");
                if (typeof window.__vfxToast === "function") window.__vfxToast(uiText("No camera in scene to view through", "Нет камеры в сцене для просмотра"));
                return;
            }
            isShotCamActive = !isShotCamActive;
            activeCamera = (isShotCamActive && monCam) ? monCam : flyCamera;
            orbitControls.enabled = false;
            btnCamView.className = isShotCamActive ? "vfx-btn vfx-btn-active" : "vfx-btn";
            if (btnSidebarOrbitCam) {
                btnSidebarOrbitCam.style.borderColor = isShotCamActive ? "rgba(56,189,248,0.65)" : "rgba(255,255,255,0.12)";
                btnSidebarOrbitCam.style.color = isShotCamActive ? "#7dd3fc" : "#d4d4d8";
            }
            logMsg(`Viewport Camera switched to: ${isShotCamActive ? "Shot Camera" : "Free Orbit / FlyCam"}`);
        };
    }
    if (btnSidebarOrbitCam) {
        btnSidebarOrbitCam.onclick = () => {
            if (btnCamView) btnCamView.click();
        };
    }

    const btnSidebarAddCam = document.getElementById("btn-sidebar-3d-add-cam");
    const menuSidebarCamPresets = document.getElementById("menu-sidebar-3d-cam-presets");
    if (btnSidebarAddCam && menuSidebarCamPresets) {
        btnSidebarAddCam.onclick = (e) => {
            e.stopPropagation();
            const isOpen = menuSidebarCamPresets.style.display === "flex";
            menuSidebarCamPresets.style.display = isOpen ? "none" : "flex";
        };
        menuSidebarCamPresets.querySelectorAll(".btn-sidebar-cam-preset").forEach(item => {
            item.onclick = (e) => {
                e.stopPropagation();
                menuSidebarCamPresets.style.display = "none";
                const preset = item.dataset.preset || "orbit";
                if (typeof createDefaultCameraTrajectory === "function") {
                    createDefaultCameraTrajectory(preset);
                }
            };
        });
        document.addEventListener("click", (ev) => {
            if (menuSidebarCamPresets.style.display === "flex" && !menuSidebarCamPresets.contains(ev.target) && ev.target !== btnSidebarAddCam) {
                menuSidebarCamPresets.style.display = "none";
            }
        });
    }

    const btnSidebarKfCam = document.getElementById("btn-sidebar-3d-keyframe-cam");
    if (btnSidebarKfCam) {
        btnSidebarKfCam.onclick = () => {
            if (typeof setCameraKeyframe === "function") {
                setCameraKeyframe(currentFrame);
                if (typeof window.__vfxToast === "function") {
                    window.__vfxToast(uiText(`Camera keyframe recorded at frame ${currentFrame}`, `Ключ камеры записан на кадре ${currentFrame}`));
                }
            }
        };
    }

    const btnSidebarSaveScene = document.getElementById("btn-sidebar-3d-save-scene");
    if (btnSidebarSaveScene) {
        btnSidebarSaveScene.onclick = async () => {
            if (typeof window.__vfxSaveLevelSnapshot === "function") {
                await window.__vfxSaveLevelSnapshot();
            } else {
                await saveManifestToServer();
                if (typeof window.__vfxToast === "function") {
                    window.__vfxToast(uiText("3D Scene saved", "3D сцена сохранена"));
                }
            }
        };
    }

    const btnImportMesh = document.getElementById("btn-import-mesh");
    const fileMeshInput = document.getElementById("file-mesh-input");
    if (btnImportMesh && fileMeshInput) {
        btnImportMesh.onclick = () => fileMeshInput.click();
        fileMeshInput.onchange = (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            if (/\.fbx$/i.test(file.name)) {
                // FBX = a character with a skeleton (UE mannequin, Mixamo…): kept in the project, not a blob
                window.__vfxCharacters3D.add(file).then(o => { if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Character "${o.userData.name}" added`, `Персонаж «${o.userData.name}» добавлен`)); })
                    .catch(err => { logMsg(`ERROR: character import: ${err.message}`); if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Import error: ${err.message}`, `Ошибка импорта: ${err.message}`)); });
                fileMeshInput.value = "";
                return;
            }
            const blobUrl = URL.createObjectURL(file);
            const cleanName = file.name.replace(/\.[^/.]+$/, "");
            replaceObjectWithGLB("__spawn_new__", blobUrl, { name: cleanName, meshEngine: "Imported GLB", fitMode: "native" });
            fileMeshInput.value = "";
        };
    }

    (() => {
        if (typeof window.__vfxToast !== "function") {
            window.__vfxToast = (msg, isErr) => {
                if (typeof window.showToastNotification === "function") window.showToastNotification(msg, !!isErr);
            };
        }
        const b = document.getElementById("btn-import-anim"), inp = document.getElementById("file-anim-input"), bones = document.getElementById("btn-toggle-bones");
        if (b && inp) {
            b.onclick = () => inp.click();
            inp.onchange = (e) => {
                const file = e.target.files?.[0]; inp.value = "";
                if (!file) return;
                window.__vfxCharacters3D.animate(file)
                    .then(o => { if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Animation applied to "${o.userData.name}"`, `Анимация на «${o.userData.name}»`), false); })
                    .catch(err => { logMsg(`ERROR: animation: ${err.message}`); if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Animation error: ${err.message}`, `Анимация: ${err.message}`), true); });
            };
        }
        if (bones) bones.onclick = () => window.__vfxCharacters3D.toggleBones();
        const pb = document.getElementById("btn-pose-from-3d");
        if (pb) pb.onclick = () => {
            pb.disabled = true;
            exportPoseLayerFrom3D()
                .then(r => { if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Pose layer created: ${r.frames} frames (added to Gallery & 2D Video)`, `Слой позы создан: ${r.frames} кадров (добавлен в Галерею и 2D Видео)`), false); })
                .catch(err => { logMsg(`ERROR: pose from 3D: ${err.message}`); if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Pose from 3D: ${err.message}`, `Поза из 3D: ${err.message}`), true); })
                .finally(() => { pb.disabled = false; });
        };
        const rb = document.getElementById("btn-render-from-3d");
        if (rb) rb.onclick = () => {
            rb.disabled = true;
            exportRenderLayerFrom3D()
                .then(r => { if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Render layer created: ${r.frames} frames (added to Gallery & 2D Video)`, `Слой рендера создан: ${r.frames} кадров (добавлен в Галерею и 2D Видео)`), false); })
                .catch(err => { logMsg(`ERROR: render from 3D: ${err.message}`); if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Render from 3D: ${err.message}`, `Рендер из 3D: ${err.message}`), true); })
                .finally(() => { rb.disabled = false; });
        };
        const eb = document.getElementById("btn-render-env-from-3d");
        if (eb) eb.onclick = () => {
            eb.disabled = true;
            exportRenderLayerFrom3D({ characters: false, props: true, background: "#bcd4e6", name: "3D layout: environment" })
                .then(r => { if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Environment layer created: ${r.frames} frames — open video in 2D tab`, `Слой окружения создан: ${r.frames} кадров — откройте видео во вкладке 2D`), false); })
                .catch(err => { logMsg(`ERROR: environment render: ${err.message}`); if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Environment render: ${err.message}`, `Рендер окружения: ${err.message}`), true); })
                .finally(() => { eb.disabled = false; });
        };
        const sk = document.getElementById("btn-add-skeleton");
        if (sk) sk.onclick = () => window.__vfxCharacters3D.addSkeleton()
            .then(o => { if (typeof window.__vfxToast === "function") window.__vfxToast(uiText(`Skeleton "${o.userData.name}" added — you can now assign animation`, `Скелет «${o.userData.name}» добавлен — теперь можно назначить анимацию`), false); })
            .catch(err => logMsg(`ERROR: skeleton: ${err.message}`));
    })();

    let nextCamId = 1;

    // Orient a camera group with CAMERA convention (-Z toward target, Z-up world)
    function aimCameraGroup(group, target) {
        const m = new THREE.Matrix4().lookAt(group.position, target, new THREE.Vector3(0, 0, 1));
        group.quaternion.setFromRotationMatrix(m);
        group.updateMatrixWorld(true);
    }

    function addFreeCamera(name, recordUndo = true) {
        if (recordUndo) pushUndoState("Add Free Camera");
        // If there is no tracked trajectory on Shot Camera, retire the untracked default Shot Camera
        // so there is no duplicate/phantom camera left behind.
        if (!cameraMatrices || cameraMatrices.length === 0) {
            if (camBodyMesh) camBodyMesh.visible = false;
            if (camFrustumHelper) camFrustumHelper.visible = false;
        }

        const camName = name || `Camera_${nextCamId++}`;
        const camGroup = createCameraSymbolLocator(false);

        // Perspective camera
        const fov = 37.8; // 35mm on 36x24mm Full Frame sensor
        const cam = new THREE.PerspectiveCamera(fov, plateAspect, 0.1, 1000);
        camGroup.add(cam);

        const helper = new THREE.CameraHelper(cam);
        helper.visible = true;
        scene.add(helper);

        // Position camera in front of cursor using Z-up camera convention (-Z forward)
        camGroup.up.set(0, 0, 1);
        camGroup.position.set(cursor3DPosition.x, cursor3DPosition.y - 3.5, cursor3DPosition.z + 1.0);
        aimCameraGroup(camGroup, cursor3DPosition.clone());

        const ud = {
            id: `cam_free_${Date.now()}_${Math.floor(Math.random()*1000)}`,
            name: camName,
            type: "camera",
            color: "#00f0ff",
            camera: cam,
            cameraHelper: helper,
            keyframes: [],
            isBaked: false,
            focalMm: 35,
            sensorPreset: "full_frame",
            sensorWidthMm: 36.0,
            sensorHeightMm: 24.0,
            imageWidth: plateWidth || 1920,
            imageHeight: plateHeight || 1080,
            apertureFStop: 2.8,
            focusDistanceM: 3.5,
            shutterAngleDeg: 180,
            anamorphicSqueeze: 1.0,
            distortionMode: "off",
            distortionK1: 0.0,
            distortionK2: 0.0,
            frustumRayLength: 3.5,
            frustumRayOpacity: 0.75
        };

        lastActiveFreeCamGroup = camGroup;
        const reg = registerObject(camGroup, camName, "camera", ud);
        applyCameraOptics(reg, true);
        updateCameraFrustumHelper(reg);
        monitorNeedsRender = true;
        selectObject(reg);
        logMsg(`Created Free Camera: ${camName}`);
        return reg;
    }

    // =========================================================================
    // 3D SCENE LIGHT SOURCES: SUN (☀️) & POINT ⇄ AREA (💡/▭)
    // =========================================================================
    let nextLightId = 1;

    function rebuildLightInternals(lightGroup) {
        if (!lightGroup || !lightGroup.userData) return;
        const ud = lightGroup.userData;
        const subType = ud.lightSubType || "sun";
        const intensity = Math.max(0, Number(ud.lightIntensity ?? (subType === "sun" ? 2.5 : 3.5)));
        const hexColor = ud.color || (subType === "sun" ? "#fff7ed" : "#ffffff");
        const threeCol = new THREE.Color(hexColor);
        const angleDeg = Math.max(0, Math.min(45, Number(ud.lightAngleDeg ?? (subType === "sun" ? 8 : (subType === "area" ? 25 : 15)))));
        const range = Math.max(1, Number(ud.lightRange ?? 20));
        const areaW = Math.max(0.2, Number(ud.areaWidth ?? 2.0));
        const areaH = Math.max(0.2, Number(ud.areaHeight ?? 1.2));
        const softness = angleDeg / 45.0; // 0 = hard light, 1 = soft diffused light

        // Clear previous internal emitter & gizmo children
        lightGroup.children.slice().forEach(ch => {
            lightGroup.remove(ch);
            if (ch.traverse) {
                ch.traverse(node => {
                    try {
                        if (node.geometry) node.geometry.dispose();
                        if (node.material) {
                            if (Array.isArray(node.material)) node.material.forEach(m => m.dispose());
                            else node.material.dispose();
                        }
                    } catch (_) {}
                });
            }
        });

        const emitterGroup = new THREE.Group();
        emitterGroup.name = "__vfx_light_emitters";
        lightGroup.add(emitterGroup);

        const gizmoGroup = new THREE.Group();
        gizmoGroup.name = "__vfx_light_gizmo";
        gizmoGroup.userData.isLightGizmo = true;
        lightGroup.add(gizmoGroup);
        ud.lightGizmo = gizmoGroup;

        // Invisible hit sphere so clicking the light in the 3D viewport is effortless
        const hitGeo = new THREE.SphereGeometry(0.38, 12, 10);
        const hitMat = new THREE.MeshBasicMaterial({ color: threeCol, transparent: true, opacity: 0.0, depthWrite: false });
        const hitMesh = new THREE.Mesh(hitGeo, hitMat);
        hitMesh.name = "__vfx_light_hit";
        gizmoGroup.add(hitMesh);

        const lineMat = new THREE.LineBasicMaterial({ color: threeCol, transparent: true, opacity: 0.92 });
        const softRayMat = new THREE.LineBasicMaterial({ color: threeCol, transparent: true, opacity: 0.55 });
        const coreMat = new THREE.MeshBasicMaterial({ color: threeCol });

        if (subType === "sun") {
            // Directional Sun Light aimed along local -Z
            const targetObj = new THREE.Object3D();
            targetObj.position.set(0, 0, -5);
            emitterGroup.add(targetObj);

            const dirLight = new THREE.DirectionalLight(threeCol, intensity);
            dirLight.position.set(0, 0, 0);
            dirLight.target = targetObj;
            dirLight.castShadow = ud.castShadows !== false;
            if (dirLight.shadow) {
                dirLight.shadow.mapSize.width = 1024;
                dirLight.shadow.mapSize.height = 1024;
                dirLight.shadow.camera.near = 0.2;
                dirLight.shadow.camera.far = 45;
                const d = 10;
                dirLight.shadow.camera.left = -d;
                dirLight.shadow.camera.right = d;
                dirLight.shadow.camera.top = d;
                dirLight.shadow.camera.bottom = -d;
                dirLight.shadow.bias = -0.0005;
                // Angle controls shadow penumbra softness (0° = razor sharp 1.0, 45° = wide soft 14.0)
                dirLight.shadow.radius = 1.0 + softness * 13.0;
            }
            emitterGroup.add(dirLight);

            // Soft sky wrap fill proportional to Angle (simulates larger solar disk / overcast diffusion)
            if (softness > 0.02) {
                const wrapHemi = new THREE.HemisphereLight(threeCol, 0x1e2433, intensity * softness * 0.16);
                emitterGroup.add(wrapHemi);
            }

            // Sun Gizmo: glowing core + solar ring + 8 corona spikes + directional beam cone with spread = lightAngleDeg
            const coreSphere = new THREE.Mesh(new THREE.SphereGeometry(0.14, 16, 12), coreMat);
            gizmoGroup.add(coreSphere);

            const ringPts = [];
            const ringR = 0.28;
            for (let i = 0; i <= 32; i++) {
                const a = (i / 32) * Math.PI * 2;
                ringPts.push(new THREE.Vector3(Math.cos(a) * ringR, Math.sin(a) * ringR, 0));
            }
            gizmoGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(ringPts), lineMat));

            const spikePts = [];
            for (let i = 0; i < 8; i++) {
                const a = (i / 8) * Math.PI * 2;
                spikePts.push(
                    new THREE.Vector3(Math.cos(a) * (ringR + 0.04), Math.sin(a) * (ringR + 0.04), 0),
                    new THREE.Vector3(Math.cos(a) * (ringR + 0.14), Math.sin(a) * (ringR + 0.14), 0)
                );
            }
            // Central aim arrow along -Z
            const beamLen = 2.2;
            spikePts.push(
                new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -beamLen),
                new THREE.Vector3(0, 0, -beamLen), new THREE.Vector3(0.08, 0, -beamLen + 0.2),
                new THREE.Vector3(0, 0, -beamLen), new THREE.Vector3(-0.08, 0, -beamLen + 0.2)
            );
            gizmoGroup.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(spikePts), lineMat));

            // Softness / Angle cone rays along -Z (spreads wider as Angle increases from 0° hard to 45° soft)
            const spreadRad = THREE.MathUtils.degToRad(angleDeg);
            const endR = ringR + Math.sin(spreadRad) * beamLen * 0.85;
            const conePts = [];
            for (let i = 0; i < 8; i++) {
                const a = (i / 8) * Math.PI * 2;
                conePts.push(
                    new THREE.Vector3(Math.cos(a) * ringR * 0.7, Math.sin(a) * ringR * 0.7, 0),
                    new THREE.Vector3(Math.cos(a) * endR, Math.sin(a) * endR, -beamLen)
                );
            }
            const endRingPts = [];
            for (let i = 0; i <= 24; i++) {
                const a = (i / 24) * Math.PI * 2;
                endRingPts.push(new THREE.Vector3(Math.cos(a) * endR, Math.sin(a) * endR, -beamLen));
            }
            gizmoGroup.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(conePts), softRayMat));
            gizmoGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(endRingPts), softRayMat));
        } else if (subType === "area") {
            // Area Panel Light (W x H rectangle emitting along local -Z with soft multi-point array + directional fill)
            const decay = 1.6 - softness * 0.6;
            const centerPt = new THREE.PointLight(threeCol, intensity * 0.5, range, decay);
            centerPt.castShadow = ud.castShadows !== false;
            if (centerPt.shadow) {
                centerPt.shadow.bias = -0.0006;
                centerPt.shadow.radius = 2.5 + softness * 12.0;
            }
            emitterGroup.add(centerPt);

            // 4 corner sub-emitters across the Area panel for true soft area wrap illumination on PBR meshes
            const hw = areaW * 0.5;
            const hh = areaH * 0.5;
            const cornerOffsets = [
                [-hw * 0.65, -hh * 0.65, -0.05],
                [ hw * 0.65, -hh * 0.65, -0.05],
                [ hw * 0.65,  hh * 0.65, -0.05],
                [-hw * 0.65,  hh * 0.65, -0.05]
            ];
            cornerOffsets.forEach(([ox, oy, oz]) => {
                const subPt = new THREE.PointLight(threeCol, intensity * 0.18, range, decay);
                subPt.position.set(ox, oy, oz);
                emitterGroup.add(subPt);
            });

            // Forward directional bounce along -Z
            const targetObj = new THREE.Object3D();
            targetObj.position.set(0, 0, -4);
            emitterGroup.add(targetObj);
            const areaDir = new THREE.DirectionalLight(threeCol, intensity * 0.35);
            areaDir.target = targetObj;
            emitterGroup.add(areaDir);

            // Area Panel Gizmo: rectangular softbox frame + inner cross + emission rays
            const rectPts = [
                new THREE.Vector3(-hw, -hh, 0), new THREE.Vector3(hw, -hh, 0),
                new THREE.Vector3(hw, -hh, 0),  new THREE.Vector3(hw, hh, 0),
                new THREE.Vector3(hw, hh, 0),   new THREE.Vector3(-hw, hh, 0),
                new THREE.Vector3(-hw, hh, 0),  new THREE.Vector3(-hw, -hh, 0),
                new THREE.Vector3(-hw, 0, 0),   new THREE.Vector3(hw, 0, 0),
                new THREE.Vector3(0, -hh, 0),   new THREE.Vector3(0, hh, 0)
            ];
            gizmoGroup.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(rectPts), lineMat));

            // Semi-transparent glowing diffuser plane in viewport
            const panelGeo = new THREE.PlaneGeometry(areaW, areaH);
            const panelMat = new THREE.MeshBasicMaterial({ color: threeCol, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false });
            gizmoGroup.add(new THREE.Mesh(panelGeo, panelMat));

            // Soft spread rays from corners & center along -Z
            const spread = Math.tan(THREE.MathUtils.degToRad(angleDeg)) * 1.1;
            const rayLen = 1.25;
            const rayPts = [
                new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -rayLen),
                new THREE.Vector3(-hw, -hh, 0), new THREE.Vector3(-hw - spread * 0.5, -hh - spread * 0.5, -rayLen),
                new THREE.Vector3( hw, -hh, 0), new THREE.Vector3( hw + spread * 0.5, -hh - spread * 0.5, -rayLen),
                new THREE.Vector3( hw,  hh, 0), new THREE.Vector3( hw + spread * 0.5,  hh + spread * 0.5, -rayLen),
                new THREE.Vector3(-hw,  hh, 0), new THREE.Vector3(-hw - spread * 0.5,  hh + spread * 0.5, -rayLen)
            ];
            gizmoGroup.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(rayPts), softRayMat));
        } else {
            // Point Light (Omni-directional bulb with adjustable hard/soft angle radius)
            const decay = 1.8 - softness * 0.75;
            const ptLight = new THREE.PointLight(threeCol, intensity, range, decay);
            ptLight.castShadow = ud.castShadows !== false;
            if (ptLight.shadow) {
                ptLight.shadow.bias = -0.0005;
                ptLight.shadow.radius = 1.2 + softness * 12.0;
            }
            emitterGroup.add(ptLight);

            if (softness > 0.1) {
                const ambFill = new THREE.HemisphereLight(threeCol, 0x181c26, intensity * softness * 0.22);
                emitterGroup.add(ambFill);
            }

            // Point Light Gizmo: core bulb + 3 orthogonal rings whose outer halo scales with softness angle
            const bulbR = 0.11 + softness * 0.12;
            gizmoGroup.add(new THREE.Mesh(new THREE.SphereGeometry(bulbR, 14, 12), coreMat));

            const haloR = 0.22 + softness * 0.24;
            const circle1 = [], circle2 = [], circle3 = [];
            for (let i = 0; i <= 24; i++) {
                const a = (i / 24) * Math.PI * 2;
                const c = Math.cos(a) * haloR;
                const s = Math.sin(a) * haloR;
                circle1.push(new THREE.Vector3(c, s, 0));
                circle2.push(new THREE.Vector3(c, 0, s));
                circle3.push(new THREE.Vector3(0, c, s));
            }
            gizmoGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(circle1), lineMat));
            gizmoGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(circle2), softRayMat));
            gizmoGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(circle3), softRayMat));
        }
    }

    function updateLightActor(targetObj, patch = {}, recordUndo = false) {
        const obj = targetObj || selectedObject;
        if (!obj || !obj.userData || obj.userData.type !== "light") return false;
        if (recordUndo) pushUndoState("Update Light Settings");
        if (patch.lightSubType !== undefined) obj.userData.lightSubType = patch.lightSubType;
        if (patch.lightIntensity !== undefined) obj.userData.lightIntensity = Math.max(0, Number(patch.lightIntensity));
        if (patch.color !== undefined) obj.userData.color = patch.color;
        if (patch.lightAngleDeg !== undefined) obj.userData.lightAngleDeg = Math.max(0, Math.min(45, Number(patch.lightAngleDeg)));
        if (patch.lightRange !== undefined) obj.userData.lightRange = Math.max(1, Number(patch.lightRange));
        if (patch.areaWidth !== undefined) obj.userData.areaWidth = Math.max(0.2, Number(patch.areaWidth));
        if (patch.areaHeight !== undefined) obj.userData.areaHeight = Math.max(0.2, Number(patch.areaHeight));
        if (patch.castShadows !== undefined) obj.userData.castShadows = !!patch.castShadows;

        rebuildLightInternals(obj);
        obj.updateMatrixWorld(true);
        updateSelectionHelpers();
        updateOutliner();
        updateTransformInspector(false);
        syncSceneData();
        // Immediately persist sun to localStorage for instant reload immunity
        const pidLight = (window.currentProject && window.currentProject.id) || "";
        if (pidLight) {
            try {
                localStorage.setItem(`vfx_3d_sun_${pidLight}`, JSON.stringify({
                    lightSubType: obj.userData.lightSubType || "sun",
                    lightIntensity: obj.userData.lightIntensity,
                    color: obj.userData.color,
                    lightAngleDeg: obj.userData.lightAngleDeg,
                    lightRange: obj.userData.lightRange,
                    areaWidth: obj.userData.areaWidth,
                    areaHeight: obj.userData.areaHeight,
                    castShadows: obj.userData.castShadows,
                    position: [obj.position.x, obj.position.y, obj.position.z],
                    rotation: [obj.rotation.x, obj.rotation.y, obj.rotation.z]
                }));
            } catch (_) {}
        }
        notifyDetailsAndOutliner();
        monitorNeedsRender = true;
        renderStudioViewports();
        return true;
    }

    function addSceneLight(subType = "sun", options = {}, recordUndo = true) {
        const normSub = (subType === "area" || subType === "point") ? subType : "sun";
        if (recordUndo) pushUndoState(`Add ${normSub === "sun" ? "Sun" : (normSub === "area" ? "Area" : "Point")} Light`);

        const defaultName = options.name || (normSub === "sun" ? `Sun_${nextLightId++}` : (normSub === "area" ? `AreaLight_${nextLightId++}` : `PointLight_${nextLightId++}`));
        const lightGroup = new THREE.Group();
        lightGroup.up.set(0, 0, 1);

        if (Array.isArray(options.position) && options.position.length === 3) {
            lightGroup.position.fromArray(options.position);
        } else if (normSub === "sun") {
            lightGroup.position.set(cursor3DPosition.x + 2.2, cursor3DPosition.y - 2.6, cursor3DPosition.z + 3.8);
            aimCameraGroup(lightGroup, cursor3DPosition.clone());
        } else {
            lightGroup.position.set(cursor3DPosition.x + 1.0, cursor3DPosition.y - 1.4, cursor3DPosition.z + 2.0);
            aimCameraGroup(lightGroup, cursor3DPosition.clone());
        }

        if (Array.isArray(options.rotation) && options.rotation.length >= 3) {
            lightGroup.rotation.set(options.rotation[0] || 0, options.rotation[1] || 0, options.rotation[2] || 0);
        }
        if (Array.isArray(options.scale) && options.scale.length === 3) {
            lightGroup.scale.fromArray(options.scale);
        }

        const ud = {
            id: options.id || `light_${normSub}_${Date.now()}_${Math.floor(Math.random() * 1000)}`,
            name: defaultName,
            type: "light",
            lightSubType: normSub,
            color: options.color || (normSub === "sun" ? "#fff7ed" : "#ffffff"),
            lightIntensity: options.lightIntensity !== undefined ? Number(options.lightIntensity) : (normSub === "sun" ? 2.5 : (normSub === "area" ? 4.0 : 3.5)),
            lightAngleDeg: options.lightAngleDeg !== undefined ? Number(options.lightAngleDeg) : (normSub === "sun" ? 8 : (normSub === "area" ? 25 : 15)),
            lightRange: options.lightRange !== undefined ? Number(options.lightRange) : 20,
            areaWidth: options.areaWidth !== undefined ? Number(options.areaWidth) : 2.0,
            areaHeight: options.areaHeight !== undefined ? Number(options.areaHeight) : 1.2,
            castShadows: options.castShadows !== false,
            keyframes: options.keyframes || []
        };

        ensureActorUserData(lightGroup, ud);
        rebuildLightInternals(lightGroup);
        const reg = registerObject(lightGroup, defaultName, "light", ud);
        monitorNeedsRender = true;
        renderStudioViewports();
        logMsg(`Created 3D Light: ${defaultName} (${normSub.toUpperCase()})`);
        return reg;
    }

    window.__vfxAddLight = (subType = "sun", opts = {}) => addSceneLight(subType, opts, true);
    window.__vfxUpdateLight = (patch = {}, targetId = null) => {
        const obj = targetId ? threeObjectsMap.get(targetId) : selectedObject;
        return updateLightActor(obj, patch, true);
    };

    initViewportViewButtons();

    // 3D Cursor controls (under Viewport Views and above 3D Tools)
    const sidebarBtnCursorOriginQuick = document.getElementById("sidebar-btn-cursor-origin-quick");
    if (sidebarBtnCursorOriginQuick) sidebarBtnCursorOriginQuick.onclick = () => snapCursorToOrigin();

    const sidebarBtnCursorOrigin = document.getElementById("sidebar-btn-cursor-origin");
    if (sidebarBtnCursorOrigin) sidebarBtnCursorOrigin.onclick = () => snapCursorToOrigin();

    const sidebarBtnCursorToSel = document.getElementById("sidebar-btn-cursor-to-sel");
    if (sidebarBtnCursorToSel) sidebarBtnCursorToSel.onclick = () => snapCursorToSelected();

    const sidebarBtnSelToCursor = document.getElementById("sidebar-btn-sel-to-cursor");
    if (sidebarBtnSelToCursor) sidebarBtnSelToCursor.onclick = () => snapSelectedToCursor();

    const sidebarCursorX = document.getElementById("sidebar-cursor-x");
    const sidebarCursorY = document.getElementById("sidebar-cursor-y");
    const sidebarCursorZ = document.getElementById("sidebar-cursor-z");

    const updateSidebarCursorFromInps = () => {
        const cx = parseFloat(sidebarCursorX?.value || "0") || 0;
        const cy = parseFloat(sidebarCursorY?.value || "0") || 0;
        const cz = parseFloat(sidebarCursorZ?.value || "0") || 0;
        set3DCursorPosition(cx, cy, cz);
    };

    if (sidebarCursorX) {
        sidebarCursorX.oninput = updateSidebarCursorFromInps;
        sidebarCursorX.onchange = updateSidebarCursorFromInps;
    }
    if (sidebarCursorY) {
        sidebarCursorY.oninput = updateSidebarCursorFromInps;
        sidebarCursorY.onchange = updateSidebarCursorFromInps;
    }
    if (sidebarCursorZ) {
        sidebarCursorZ.oninput = updateSidebarCursorFromInps;
        sidebarCursorZ.onchange = updateSidebarCursorFromInps;
    }
    if (sidebarCursorX) sidebarCursorX.value = cursor3DPosition.x.toFixed(2);
    if (sidebarCursorY) sidebarCursorY.value = cursor3DPosition.y.toFixed(2);
    if (sidebarCursorZ) sidebarCursorZ.value = cursor3DPosition.z.toFixed(2);

    const sidebarCursorScale = document.getElementById("sidebar-cursor-scale");
    const sidebarCursorScaleSlider = document.getElementById("sidebar-cursor-scale-slider");
    const sidebarCursorThickness = document.getElementById("sidebar-cursor-thickness");
    const sidebarCursorThicknessSlider = document.getElementById("sidebar-cursor-thickness-slider");

    if (sidebarCursorScale) sidebarCursorScale.value = cursor3DScale.toFixed(2);
    if (sidebarCursorScaleSlider) sidebarCursorScaleSlider.value = String(cursor3DScale);
    if (sidebarCursorThickness) sidebarCursorThickness.value = cursor3DThickness.toFixed(2);
    if (sidebarCursorThicknessSlider) sidebarCursorThicknessSlider.value = String(cursor3DThickness);

    const onScaleInput = (v) => {
        const num = parseFloat(v);
        if (!isNaN(num)) {
            if (sidebarCursorScale && document.activeElement !== sidebarCursorScale) sidebarCursorScale.value = num.toFixed(2);
            if (sidebarCursorScaleSlider && document.activeElement !== sidebarCursorScaleSlider) sidebarCursorScaleSlider.value = String(num);
            set3DCursorScale(num);
        }
    };
    const onThicknessInput = (v) => {
        const num = parseFloat(v);
        if (!isNaN(num)) {
            if (sidebarCursorThickness && document.activeElement !== sidebarCursorThickness) sidebarCursorThickness.value = num.toFixed(2);
            if (sidebarCursorThicknessSlider && document.activeElement !== sidebarCursorThicknessSlider) sidebarCursorThicknessSlider.value = String(num);
            set3DCursorThickness(num);
        }
    };

    if (sidebarCursorScale) {
        sidebarCursorScale.oninput = (e) => onScaleInput(e.target.value);
        sidebarCursorScale.onchange = (e) => onScaleInput(e.target.value);
    }
    if (sidebarCursorScaleSlider) {
        sidebarCursorScaleSlider.oninput = (e) => onScaleInput(e.target.value);
        sidebarCursorScaleSlider.onchange = (e) => onScaleInput(e.target.value);
    }
    if (sidebarCursorThickness) {
        sidebarCursorThickness.oninput = (e) => onThicknessInput(e.target.value);
        sidebarCursorThickness.onchange = (e) => onThicknessInput(e.target.value);
    }
    if (sidebarCursorThicknessSlider) {
        sidebarCursorThicknessSlider.oninput = (e) => onThicknessInput(e.target.value);
        sidebarCursorThicknessSlider.onchange = (e) => onThicknessInput(e.target.value);
    }

    const btnAddCam = document.getElementById("btn-add-camera");
    if (btnAddCam) {
        btnAddCam.onclick = () => addFreeCamera();
    }
    const btnAddSunLight = document.getElementById("btn-add-sun-light");
    if (btnAddSunLight) {
        btnAddSunLight.onclick = (e) => {
            const existingSun = Array.from(threeObjectsMap.values()).find(o => o.userData?.type === "light" && (o.userData?.lightSubType === "sun" || (o.userData?.name || "").startsWith("Sun")));
            if (existingSun && !e.shiftKey) {
                selectObject(existingSun);
                if (typeof window.__vfxToast === "function") window.__vfxToast(uiText("Selected existing Sun Light", "Выбрано существующее Солнце"));
                return;
            }
            addSceneLight("sun");
        };
    }
    const btnAddPointLight = document.getElementById("btn-add-point-light");
    if (btnAddPointLight) {
        btnAddPointLight.onclick = () => addSceneLight("point");
    }
    // Wire sidebar light inspector controls
    document.querySelectorAll(".sidebar-light-mode-btn").forEach(btn => {
        btn.onclick = () => {
            if (selectedObject && selectedObject.userData?.type === "light") {
                updateLightActor(selectedObject, { lightSubType: btn.dataset.lmode || "sun" }, true);
            }
        };
    });
    const bindLightPair = (sliderId, numId, propKey) => {
        const sl = document.getElementById(sliderId);
        const nm = document.getElementById(numId);
        if (sl) {
            sl.oninput = (e) => {
                const v = parseFloat(e.target.value);
                if (nm) nm.value = String(v);
                if (selectedObject && selectedObject.userData?.type === "light") {
                    updateLightActor(selectedObject, { [propKey]: v }, false);
                }
            };
        }
        if (nm) {
            nm.oninput = (e) => {
                const v = parseFloat(e.target.value);
                if (!isNaN(v)) {
                    if (sl) sl.value = String(v);
                    if (selectedObject && selectedObject.userData?.type === "light") {
                        updateLightActor(selectedObject, { [propKey]: v }, false);
                    }
                }
            };
        }
    };
    bindLightPair("sidebar-light-intensity-slider", "sidebar-light-intensity-num", "lightIntensity");
    bindLightPair("sidebar-light-angle-slider", "sidebar-light-angle-num", "lightAngleDeg");
    bindLightPair("sidebar-light-range-slider", "sidebar-light-range-num", "lightRange");
    const inpAreaW = document.getElementById("sidebar-light-area-w");
    const inpAreaH = document.getElementById("sidebar-light-area-h");
    if (inpAreaW) {
        inpAreaW.oninput = (e) => {
            const v = parseFloat(e.target.value);
            if (!isNaN(v) && selectedObject?.userData?.type === "light") updateLightActor(selectedObject, { areaWidth: v }, false);
        };
    }
    if (inpAreaH) {
        inpAreaH.oninput = (e) => {
            const v = parseFloat(e.target.value);
            if (!isNaN(v) && selectedObject?.userData?.type === "light") updateLightActor(selectedObject, { areaHeight: v }, false);
        };
    }

    const sliderExposure = document.getElementById("slider-viewport-exposure");
    const lblExposure = document.getElementById("lbl-viewport-exposure");
    const sliderStudioFill = document.getElementById("slider-studio-fill");
    const lblStudioFill = document.getElementById("lbl-studio-fill");
    if (sliderExposure) {
        sliderExposure.value = String(viewportExposure);
        if (lblExposure) lblExposure.textContent = viewportExposure.toFixed(2);
        sliderExposure.oninput = (e) => {
            const v = parseFloat(e.target.value) || 1;
            if (lblExposure) lblExposure.textContent = v.toFixed(2);
            window.__vfxViewportLook.setExposure(v);
        };
        sliderExposure.ondblclick = () => { sliderExposure.value = "1"; sliderExposure.oninput({ target: sliderExposure }); };
    }
    if (sliderStudioFill) {
        sliderStudioFill.value = String(studioFillLevel);
        if (lblStudioFill) lblStudioFill.textContent = studioFillLevel.toFixed(2);
        sliderStudioFill.oninput = (e) => {
            const v = parseFloat(e.target.value);
            if (lblStudioFill) lblStudioFill.textContent = (isNaN(v) ? 1 : v).toFixed(2);
            window.__vfxViewportLook.setStudioFill(isNaN(v) ? 1 : v);
        };
        sliderStudioFill.ondblclick = () => { sliderStudioFill.value = "1"; sliderStudioFill.oninput({ target: sliderStudioFill }); };
    }

    function update3DCursorUI() {
        const btn = document.getElementById("btn-tool-3d-cursor");
        if (!btn) return;
        btn.style.background = cursor3DVisible ? "rgba(244, 63, 94, 0.25)" : "transparent";
        btn.style.borderColor = cursor3DVisible ? "#f43f5e" : "transparent";
        btn.style.boxShadow = cursor3DVisible ? "0 0 8px rgba(244, 63, 94, 0.45)" : "none";
        btn.style.color = cursor3DVisible ? "#f43f5e" : "rgba(255,255,255,0.65)";
        if (typeof renderer !== "undefined" && renderer?.domElement) {
            renderer.domElement.style.cursor = is3DCursorToolActive ? "crosshair" : "default";
        }
    }
    window.__vfxUpdate3DCursorUI = update3DCursorUI;

    const btnTool3DCursor = document.getElementById("btn-tool-3d-cursor");
    if (btnTool3DCursor) {
        update3DCursorUI();
        btnTool3DCursor.onclick = () => {
            is3DCursorToolActive = !is3DCursorToolActive;
            cursor3DVisible = is3DCursorToolActive;
            if (cursor3DGroup) cursor3DGroup.visible = cursor3DVisible;
            update3DCursorUI();
            logMsg(cursor3DVisible ? "3D-курсор: ВКЛ (кликните в сцене или Shift+ПКМ для перемещения)" : "3D-курсор: ВЫКЛ");
            if (typeof renderStudioViewports === "function") renderStudioViewports();
        };
    }

    document.getElementById("btn-add-cube").onclick = () => {
        pushUndoState("Add Box");
        const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), defMat());
        m.position.copy(cursor3DPosition);
        registerObject(m, `Box_${nextObjId}`, "box");
    };

    document.getElementById("btn-add-sphere").onclick = () => {
        pushUndoState("Add Sphere");
        const m = new THREE.Mesh(new THREE.SphereGeometry(0.5, 32, 24), defMat());
        m.position.copy(cursor3DPosition);
        registerObject(m, `Sphere_${nextObjId}`, "sphere");
    };

    document.getElementById("btn-add-cylinder").onclick = () => {
        pushUndoState("Add Cylinder");
        const m = new THREE.Mesh(createPrimitiveGeometry("cylinder"), defMat());
        m.position.copy(cursor3DPosition);
        registerObject(m, `Cylinder_${nextObjId}`, "cylinder");
    };

    document.getElementById("btn-add-cone").onclick = () => {
        pushUndoState("Add Cone");
        const m = new THREE.Mesh(createPrimitiveGeometry("cone"), defMat());
        m.position.copy(cursor3DPosition);
        registerObject(m, `Cone_${nextObjId}`, "cone");
    };

    document.getElementById("btn-add-torus").onclick = () => {
        pushUndoState("Add Torus");
        const m = new THREE.Mesh(new THREE.TorusGeometry(0.5, 0.2, 16, 32), defMat());
        m.position.copy(cursor3DPosition);
        registerObject(m, `Torus_${nextObjId}`, "torus");
    };

    document.getElementById("btn-add-plane").onclick = () => {
        pushUndoState("Add Floor");
        const m = new THREE.Mesh(new THREE.PlaneGeometry(3, 3), defMat(0x888888, true));
        m.position.copy(cursor3DPosition);
        registerObject(m, `Floor_${nextObjId}`, "plane");
    };

    const btnAddMarkers = document.getElementById("btn-add-markers");
    if (btnAddMarkers) btnAddMarkers.onclick = () => {
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

    // --- AI Director Agent (Gemma 12B) Handlers ---
    const chatBox = document.getElementById("director-chat-box");
    const chatInput = document.getElementById("input-director-chat");
    const btnSendChat = document.getElementById("btn-send-director");
    const promptPreview = document.getElementById("director-prompt-preview");
    const promptText = document.getElementById("director-prompt-text");
    const btnCopyPrompt = document.getElementById("btn-copy-director-prompt");

    async function sendDirectorMessage() {
        if (!chatInput) return;
        const text = chatInput.value.trim();
        if (!text) return;
        chatInput.value = "";

        const userDiv = document.createElement("div");
        userDiv.style.margin = "4px 0";
        userDiv.style.textAlign = "right";
        userDiv.innerHTML = `<span style="background: #283593; color: #fff; padding: 3px 8px; border-radius: 8px; display: inline-block;">${text}</span>`;
        chatBox.appendChild(userDiv);
        chatBox.scrollTop = chatBox.scrollHeight;

        const typingDiv = document.createElement("div");
        typingDiv.style.color = "#7986cb";
        typingDiv.style.fontStyle = "italic";
        typingDiv.innerText = uiText("🎬 AI Director is planning the shot...", "🎬 Режиссер Gemma 12B обдумывает шот...");
        chatBox.appendChild(typingDiv);
        chatBox.scrollTop = chatBox.scrollHeight;

        try {
            const resp = await fetch("/vfx/agent/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text, planner: "legacy" })
            });
            const plan = await resp.json();
            if (typingDiv.parentNode) chatBox.removeChild(typingDiv);

            const dirDiv = document.createElement("div");
            dirDiv.style.margin = "4px 0";
            dirDiv.style.textAlign = "left";
            const replyText = (uiText("EN", "RU") === "RU" ? plan.response_ru : plan.response_en) || plan.response_ru || plan.response || "Shot planned!";
            dirDiv.innerHTML = `<div style="background: #141724; border: 1px solid #303f9f; color: #e8eaf6; padding: 6px; border-radius: 6px; white-space: pre-wrap;">${replyText}</div>`;
            chatBox.appendChild(dirDiv);
            chatBox.scrollTop = chatBox.scrollHeight;

            if (plan.ltx_prompt) {
                promptPreview.style.display = "block";
                promptText.innerText = plan.ltx_prompt;
            }

            if (plan.camera && plan.camera.add_markers_grid) {
                const existing = scene.getObjectByName("LTX_Calibration_Markers_3");
                if (!existing) {
                    document.getElementById("btn-add-markers").click();
                }
            }
            logMsg(`🎬 Director applied settings: Lens ${plan.camera?.focal_mm || 35}mm, Markers: ${plan.camera?.add_markers_grid ? "ON" : "OFF"}`);
        } catch (e) {
            if (typingDiv.parentNode) chatBox.removeChild(typingDiv);
            const errDiv = document.createElement("div");
            errDiv.style.color = "#ff8a80";
            errDiv.innerText = uiText(`Agent error: ${e.message}`, `Ошибка агента: ${e.message}`);
            chatBox.appendChild(errDiv);
        }
    }

    if (btnSendChat) btnSendChat.onclick = sendDirectorMessage;
    if (chatInput) {
        chatInput.onkeydown = (e) => {
            if (e.key === "Enter") sendDirectorMessage();
        };
    }
    if (btnCopyPrompt) {
        btnCopyPrompt.onclick = () => {
            navigator.clipboard.writeText(promptText.innerText);
            btnCopyPrompt.innerText = uiText("Copied!", "Скопировано!");
            setTimeout(() => { btnCopyPrompt.innerText = uiText("Copy Prompt", "Скопировать"); }, 2000);
        };
    }

    // --- Voice & Lip-Sync Studio (TTS) Handlers ---
    const inputTtsText = document.getElementById("input-tts-text");
    const selectTtsVoice = document.getElementById("select-tts-voice");
    const chkTtsTranslate = document.getElementById("chk-tts-translate");
    const btnGenerateSpeech = document.getElementById("btn-generate-speech");
    const voicePlayerCard = document.getElementById("voice-player-card");
    const audioPreviewPlayer = document.getElementById("audio-preview-player");
    const voiceInfoLabel = document.getElementById("voice-info-label");

    if (btnGenerateSpeech) {
        btnGenerateSpeech.onclick = async () => {
            const text = inputTtsText.value.trim();
            if (!text) return alert(uiText("Enter dialogue line for speech synthesis!", "Введите текст реплики для озвучки!"));

            btnGenerateSpeech.innerText = uiText("Synthesizing speech...", "Генерация речи...");
            btnGenerateSpeech.disabled = true;

            try {
                const resp = await fetch("/vfx/audio/tts", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        text: text,
                        voice: selectTtsVoice.value,
                        auto_translate: chkTtsTranslate.checked,
                        fps: 24.0
                    })
                });
                const res = await resp.json();
                btnGenerateSpeech.innerText = uiText("Synthesize & Lip-Sync", "Сгенерировать голос и липсинг");
                btnGenerateSpeech.disabled = false;

                if (res.error) return alert(uiText("Voice synthesis error: ", "Ошибка озвучки: ") + res.error);

                voicePlayerCard.style.display = "block";
                audioPreviewPlayer.src = `/vfx/audio/current?t=${Date.now()}`;
                audioPreviewPlayer.play();

                voiceInfoLabel.innerText = uiText(`Duration: ${res.duration_sec}s (${res.total_frames} frames @ 24fps)`, `Длительность: ${res.duration_sec} сек (${res.total_frames} кадров @ 24fps)`);
                logMsg(`[TTS] Speech generated (${res.duration_sec}s, ${res.total_frames} frames). WAV exported for LTX Audio-VAE & Lip-Sync!`);

                if (res.total_frames && res.total_frames > 0) {
                    totalFrames = Math.max(totalFrames, res.total_frames);
                    const scrub = document.getElementById("scrub-timeline");
                    if (scrub) scrub.max = totalFrames - 1;
                    const lblTotal = document.getElementById("lbl-total-frames");
                    if (lblTotal) lblTotal.innerText = `/${totalFrames}`;
                }
            } catch (e) {
                btnGenerateSpeech.innerText = uiText("Synthesize & Lip-Sync", "Сгенерировать голос и липсинг");
                btnGenerateSpeech.disabled = false;
                alert(uiText("Speech synthesis error: " + e.message, "Ошибка синтеза речи: " + e.message));
            }
        };
    }

    // Tools & Modes
    document.getElementById("btn-tool-select").onclick = () => { transformControls.detach(); };
    document.getElementById("btn-tool-translate").onclick = () => {
        transformControls.setMode("translate");
        if (selectedObject) transformControls.attach(selectedObject);
    };
    document.getElementById("btn-tool-rotate").onclick = () => {
        transformControls.setMode("rotate");
        if (selectedObject) transformControls.attach(selectedObject);
    };
    document.getElementById("btn-tool-scale").onclick = () => {
        transformControls.setMode("scale");
        if (selectedObject) transformControls.attach(selectedObject);
    };
    document.getElementById("btn-coord-space").onclick = (e) => {
        const isWorld = transformControls.space === "world";
        const nextSpace = isWorld ? "local" : "world";
        transformControls.setSpace(nextSpace);
        e.target.innerText = `Space: ${nextSpace === "local" ? "Local" : "World"} (~)`;
        const hudStatus = document.getElementById("hud-status");
        if (hudStatus) {
            hudStatus.innerText = `GIZMO: ${transformControls.mode.toUpperCase()} (${nextSpace.toUpperCase()})`;
        }
        if (typeof window.__vfxOnCoordSpaceChanged === "function") {
            window.__vfxOnCoordSpaceChanged(nextSpace);
        }
    };

    // Environment & Grid
    const btnToggleGrid = document.getElementById("btn-toggle-grid");
    if (btnToggleGrid) {
        btnToggleGrid.innerText = `Grid: ${isGridVisible ? "Visible" : "Hidden"}`;
        btnToggleGrid.onclick = () => {
            isGridVisible = !isGridVisible;
            if (gridHelper) gridHelper.visible = isGridVisible;
            localStorage.setItem("vfx_3d_grid_visible", String(isGridVisible));
            btnToggleGrid.innerText = `Grid: ${isGridVisible ? "Visible" : "Hidden"}`;
        };
    }

    const gridColorPicker = document.getElementById("grid-color-picker");
    if (gridColorPicker) {
        gridColorPicker.value = gridColorHex;
        gridColorPicker.oninput = (e) => {
            const hex = e.target.value;
            localStorage.setItem("vfx_3d_grid_color", hex);
            if (gridHelper) scene.remove(gridHelper);
            gridColorHex = hex;
            gridHelper = createGridHelper(hex);
            scene.add(gridHelper);
            monitorNeedsRender = true;
            try { renderStudioViewports(); } catch (_) {}
        };
    }

    const envPicker = document.getElementById("env-bg-color-picker");
    if (envPicker) {
        envPicker.value = envBgColor;
        envPicker.oninput = (e) => {
            const hex = e.target.value;
            localStorage.setItem("vfx_3d_env_bg_color", hex);
            envBgColor = hex;
            scene.background = new THREE.Color(hex);
            monitorNeedsRender = true;
            try { renderStudioViewports(); } catch (_) {}
        };
    }

    const setEnvBgColor = (hex) => {
        localStorage.setItem("vfx_3d_env_bg_color", hex);
        envBgColor = hex;
        scene.background = new THREE.Color(hex);
        if (envPicker) envPicker.value = hex;
        monitorNeedsRender = true;
        try { renderStudioViewports(); } catch (_) {}
    };
    const envDark = document.getElementById("env-swatch-dark");
    if (envDark) envDark.onclick = () => setEnvBgColor("#0a0a0d");
    const envSlate = document.getElementById("env-swatch-slate");
    if (envSlate) envSlate.onclick = () => setEnvBgColor("#141416");
    const envGray = document.getElementById("env-swatch-gray");
    if (envGray) envGray.onclick = () => setEnvBgColor("#222226");

    // LiDAR Cloud Size & Alpha Realtime Handlers
    const sliderCloudSize = document.getElementById("slider-cloud-size");
    const lblCloudSize = document.getElementById("lbl-cloud-size");
    if (sliderCloudSize) {
        sliderCloudSize.value = String(cloudPointSize);
        if (lblCloudSize) lblCloudSize.innerText = cloudPointSize.toFixed(2);
        sliderCloudSize.oninput = (e) => {
            const sz = parseFloat(e.target.value) || 0.04;
            cloudPointSize = Math.max(0.005, Math.min(3.0, sz));
            localStorage.setItem("vfx_ue_point_size", String(cloudPointSize));
            if (lblCloudSize) lblCloudSize.innerText = cloudPointSize.toFixed(2);
            updateAdaptivePointCloudSize(canvasHost.clientHeight);
            if (typeof window.__vfxOnUeHudChanged === "function") {
                window.__vfxOnUeHudChanged({ pointSize: cloudPointSize });
            }
        };
    }

    const sliderCloudAlpha = document.getElementById("slider-cloud-alpha");
    const lblCloudAlpha = document.getElementById("lbl-cloud-alpha");
    if (sliderCloudAlpha) {
        sliderCloudAlpha.value = String(cloudPointAlpha);
        if (lblCloudAlpha) lblCloudAlpha.innerText = `${Math.round(cloudPointAlpha * 100)}%`;
        sliderCloudAlpha.oninput = (e) => {
            cloudPointAlpha = parseFloat(e.target.value);
            if (lblCloudAlpha) lblCloudAlpha.innerText = `${Math.round(cloudPointAlpha * 100)}%`;
            if (pointCloudMaterial) {
                pointCloudMaterial.opacity = cloudPointAlpha;
                pointCloudMaterial.needsUpdate = true;
            }
            if (cloudMesh) {
                cloudMesh.visible = cloudPointAlpha > 0.01;
            }
            updateOutliner();
        };
    }

    // Camera Monitor Realtime Preview Toggle
    let isMonitorPreviewEnabled = true;
    const btnMonPreviewToggle = document.getElementById("btn-mon-preview-toggle");
    if (btnMonPreviewToggle) {
        btnMonPreviewToggle.onclick = () => {
            isMonitorPreviewEnabled = !isMonitorPreviewEnabled;
            btnMonPreviewToggle.className = isMonitorPreviewEnabled ? "vfx-monitor-pill-btn vfx-pill-active" : "vfx-monitor-pill-btn";
            btnMonPreviewToggle.innerText = isMonitorPreviewEnabled ? "● Preview ON" : "○ Preview OFF";
            const monCnv = document.getElementById("monitor-3d-canvas");
            const emptyWm = document.getElementById("monitor-empty-watermark");
            if (!isMonitorPreviewEnabled) {
                if (monCnv) monCnv.style.display = "none";
                if (emptyWm) emptyWm.style.display = "block";
            } else {
                if (monCnv) monCnv.style.display = "block";
                if (emptyWm) emptyWm.style.display = "none";
            }
        };
    }

    // Camera Monitor: Live 3D View (stale Baked View overlay disabled)
    window.__vfxUpdateBakedMonitorFrame = () => {};
    window.__vfxIsBakedViewActive = () => false;

    // Actor Cutout Toggles & Custom Dropzone
    const btnToggleActor = document.getElementById("btn-toggle-actor");
    if (btnToggleActor) {
        btnToggleActor.onclick = () => {
            isActorProjectionVisible = !isActorProjectionVisible;
            actorBillboard.visible = isActorProjectionVisible;
            btnToggleActor.className = isActorProjectionVisible ? "vfx-btn vfx-btn-active" : "vfx-btn";
            btnToggleActor.innerText = `👤 Actor: ${isActorProjectionVisible ? "ON" : "OFF"}`;
            if (isActorProjectionVisible && !customActorCutoutTex) {
                updateActorTexture(currentFrame);
            }
            updateActorBillboardTransform();
            renderStudioViewports();
        };
    }

    const sliderActorDist = document.getElementById("slider-actor-dist");
    if (sliderActorDist) {
        sliderActorDist.oninput = (e) => {
            actorDistance = parseFloat(e.target.value);
            updateActorBillboardTransform();
            renderStudioViewports();
        };
    }

    const sliderActorAlpha = document.getElementById("slider-actor-alpha");
    if (sliderActorAlpha) {
        sliderActorAlpha.oninput = (e) => {
            actorOpacity = parseFloat(e.target.value);
            actorMaterial.opacity = actorOpacity;
            renderStudioViewports();
        };
    }

    const dropzoneActor = document.getElementById("actor-cutout-dropzone");
    const inputActorCutout = document.getElementById("input-actor-cutout");
    const btnActorRembg = document.getElementById("btn-actor-rembg");
    const btnActorReset = document.getElementById("btn-actor-reset");

    if (dropzoneActor && inputActorCutout) {
        dropzoneActor.onclick = (e) => {
            if (e.target && e.target.closest("button")) return;
            inputActorCutout.click();
        };
        inputActorCutout.onchange = async (e) => {
            const file = e.target.files && e.target.files[0];
            if (!file) return;
            const isVid = file.type.startsWith("video/") || /\.(mp4|webm|mov|mkv|m4v)$/i.test(file.name);
            try {
                if (typeof uploadToProject === "function") {
                    const upUrl = await uploadToProject(file);
                    setCustomActorCutout(upUrl, file.name, isVid ? "video" : "image");
                } else {
                    const objUrl = URL.createObjectURL(file);
                    setCustomActorCutout(objUrl, file.name, isVid ? "video" : "image");
                }
            } catch (err) {
                const objUrl = URL.createObjectURL(file);
                setCustomActorCutout(objUrl, file.name, isVid ? "video" : "image");
            }
        };

        dropzoneActor.ondragover = (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzoneActor.style.borderColor = "#38bdf8";
            dropzoneActor.style.backgroundColor = "rgba(56, 189, 248, 0.15)";
        };
        dropzoneActor.ondragleave = (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzoneActor.style.borderColor = "rgba(255,255,255,0.22)";
            dropzoneActor.style.backgroundColor = "rgba(0,0,0,0.25)";
        };
        dropzoneActor.ondrop = async (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzoneActor.style.borderColor = "rgba(255,255,255,0.22)";
            dropzoneActor.style.backgroundColor = "rgba(0,0,0,0.25)";

            // 1. Check application/vfx-media-item or application/vfx-ref from top strip / gallery
            const rawItem = e.dataTransfer.getData("application/vfx-media-item") || e.dataTransfer.getData("application/vfx-ref");
            if (rawItem) {
                try {
                    const item = JSON.parse(rawItem);
                    const url = item.cutout_url || item.url || item.thumbnailUrl;
                    const isVid = item.type === "video" || item.mediaType === "video" || isVideoMedia(url, item.name || item.filename);
                    if (url) {
                        setCustomActorCutout(url, item.name || item.filename || (isVid ? "video.mp4" : "actor.png"), isVid ? "video" : "image");
                        if (typeof window.showToastNotification === "function") {
                            window.showToastNotification(uiText(`Actor set from "${item.name || 'gallery'}"`, `Кастомный эктор установлен из галереи`));
                        }
                        return;
                    }
                } catch (_) {}
            }

            // 2. Check local files (both video and image!)
            if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                const file = e.dataTransfer.files[0];
                const isVid = file.type.startsWith("video/") || /\.(mp4|webm|mov|mkv|m4v)$/i.test(file.name);
                const isImg = file.type.startsWith("image/") || /\.(png|jpe?g|webp|bmp|gif)$/i.test(file.name);
                if (isVid || isImg) {
                    try {
                        if (typeof uploadToProject === "function") {
                            const upUrl = await uploadToProject(file);
                            setCustomActorCutout(upUrl, file.name, isVid ? "video" : "image");
                        } else {
                            const objUrl = URL.createObjectURL(file);
                            setCustomActorCutout(objUrl, file.name, isVid ? "video" : "image");
                        }
                    } catch (_) {
                        const objUrl = URL.createObjectURL(file);
                        setCustomActorCutout(objUrl, file.name, isVid ? "video" : "image");
                    }
                    if (typeof window.showToastNotification === "function") {
                        window.showToastNotification(uiText(`Actor set: "${file.name}"`, `Эктор установлен: "${file.name}"`));
                    }
                    return;
                }
            }

            // 3. Fallback: text / URL
            const textUrl = e.dataTransfer.getData("text/plain");
            if (textUrl && /\.(png|jpe?g|webp|mp4|webm|mov)(\?|$)/i.test(textUrl)) {
                const trimmed = textUrl.trim();
                const isVid = /\.(mp4|webm|mov)(\?|$)/i.test(trimmed);
                setCustomActorCutout(trimmed, isVid ? "video.mp4" : "actor.png", isVid ? "video" : "image");
            }
        };
    }

    if (btnActorRembg) {
        btnActorRembg.onclick = async (e) => {
            e.stopPropagation();
            if (!customActorCutoutUrl) return;
            btnActorRembg.disabled = true;
            btnActorRembg.innerText = "⏳ Rembg...";
            try {
                const res = await fetch("/vfx/mesh3d/rembg", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ image: customActorCutoutUrl })
                }).then(r => r.json());
                if (res && res.cutout_url) {
                    setCustomActorCutout(res.cutout_url, (customActorCutoutName ? "cutout_" + customActorCutoutName : "cutout.png"));
                    if (typeof window.showToastNotification === "function") {
                        window.showToastNotification(uiText("Background removed via BiRefNet!", "Фон успешно удален через BiRefNet!"));
                    }
                } else {
                    throw new Error(res.error || "Rembg failed");
                }
            } catch (err) {
                logMsg(`Rembg error: ${err.message}`);
                if (typeof window.showToastNotification === "function") {
                    window.showToastNotification(`❌ Rembg: ${err.message}`, true);
                }
            } finally {
                btnActorRembg.disabled = false;
                btnActorRembg.innerText = `✂️ ${uiText('Remove BG', 'Удалить фон')}`;
            }
        };
    }

    if (btnActorReset) {
        btnActorReset.onclick = (e) => {
            e.stopPropagation();
            clearCustomActorCutout();
            if (typeof window.showToastNotification === "function") {
                window.showToastNotification(uiText("Reset to video cutout plane", "Сброшено к вырезке из видео"));
            }
        };
    }

    // Animation Hips & Root Motion buttons wiring
    const btnToggleHipsMotion = document.getElementById("btn-toggle-hips-motion");
    if (btnToggleHipsMotion) {
        btnToggleHipsMotion.onclick = () => {
            const obj = selectedObject && selectedObject.userData && selectedObject.userData.type === "character" ? selectedObject : null;
            if (!obj) return;
            const currentVal = obj.userData.enableHipsMotion !== false;
            obj.userData.enableHipsMotion = !currentVal;
            refreshAnimClipSelect();
            updateCharactersAtFrame();
            syncSceneData();
            renderStudioViewports();
            logMsg(`Hips motion ${obj.userData.enableHipsMotion ? "enabled" : "locked"}`);
        };
    }

    const btnBakeRootMotion = document.getElementById("btn-bake-root-motion");
    if (btnBakeRootMotion) {
        btnBakeRootMotion.onclick = () => {
            const obj = selectedObject && selectedObject.userData && selectedObject.userData.type === "character" ? selectedObject : null;
            const rig = obj && characterRigs.get(obj.userData.id);
            if (!obj || !rig || !rig.retarget) {
                logMsg("Select a character with retargeted animation to bake root motion");
                return;
            }
            if (obj.userData.bakeRootMode === "pivot") {
                // Toggle off / clear pivot baking
                pushUndoState("Unbake Root Motion");
                obj.userData.bakeRootMode = "hips";
                refreshAnimClipSelect();
                updateCharactersAtFrame();
                syncSceneData();
                renderStudioViewports();
                logMsg(`Hips motion returned to character skeleton`);
                return;
            }
            pushUndoState("Bake Root Motion to Pivot");
            const R = rig.retarget;
            const baseObjPos = obj.position.clone();
            const dur = obj.userData.animDuration || 1;
            const fps = projectFPS || 24;
            const speed = obj.userData.animSpeed || 1;
            const offset = obj.userData.animOffset || 0;
            
            // Calculate displacement at t = 0 as base
            R.src.mixer.setTime(0);
            R.pivot.updateMatrixWorld(true);
            const baseSHip = R.hip.sb.getWorldPosition(new THREE.Vector3());
            const baseWorldDelta = baseSHip.sub(R.hipSrcRest).multiplyScalar(R.ratio);

            if (!Array.isArray(obj.userData.keyframes)) obj.userData.keyframes = [];
            
            for (let f = 0; f < totalFrames; f++) {
                let t = (f - offset) / fps * speed;
                t = ((t % dur) + dur) % dur;
                R.src.mixer.setTime(t);
                R.pivot.updateMatrixWorld(true);
                const curSHip = R.hip.sb.getWorldPosition(new THREE.Vector3());
                const curWorldDelta = curSHip.sub(R.hipSrcRest).multiplyScalar(R.ratio);
                
                const dx = curWorldDelta.x - baseWorldDelta.x;
                const dy = curWorldDelta.y - baseWorldDelta.y;
                const framePos = baseObjPos.clone().add(new THREE.Vector3(dx, dy, 0));
                
                let kf = obj.userData.keyframes.find(k => Number(k.frame) === f);
                if (!kf) {
                    kf = {
                        frame: f,
                        position: framePos,
                        quaternion: obj.quaternion.clone(),
                        scale: obj.scale.clone(),
                        interp: "linear"
                    };
                    obj.userData.keyframes.push(kf);
                } else {
                    kf.position = framePos;
                }
            }
            obj.userData.keyframes.sort((a, b) => a.frame - b.frame);
            obj.userData.bakeRootMode = "pivot";
            refreshAnimClipSelect();
            syncSceneData();
            evaluateAnimation();
            updateCharactersAtFrame();
            renderStudioViewports();
            logMsg(`✓ Root motion baked to ${totalFrames} keyframes on «${obj.userData.name}» pivot`);
        };
    }

    // Timeline Controls & Playback (Synchronized with 2D Video Player & 3D Monitor)
    function togglePlay() {
        const is2DActive = document.getElementById("vfx-2d-media-stage") !== null;
        if (is2DActive && typeof window.__vfxToggle2DPlayback === "function") {
            window.__vfxToggle2DPlayback();
            return;
        }
        isPlaying = !isPlaying;
        if (btnPlayPause) {
            btnPlayPause.innerText = isPlaying ? "⏸ Pause" : "▶ Play";
        }
        if (customActorCutoutMediaType === "video" && customActorCutoutVideo) {
            if (!customActorCutoutVideo.paused) customActorCutoutVideo.pause();
        }
        if (typeof window.__vfxOnExternalPlayState === "function") {
            window.__vfxOnExternalPlayState(isPlaying);
        }
    }
    if (btnPlayPause) {
        btnPlayPause.onclick = togglePlay;
    }

    const selInterp = footer.querySelector("#sel-interp") || document.getElementById("sel-interp");
    function applyInterpModeToActorKeys(interpMode, onlyCurrentFrame = false) {
        currentInterp = interpMode || "bezier";
        if (selInterp && selInterp.value !== currentInterp) {
            selInterp.value = currentInterp;
        }
        const targetObj = selectedObject || getActiveMonitorCameraActor();
        if (targetObj && Array.isArray(targetObj.userData?.keyframes) && targetObj.userData.keyframes.length > 0) {
            pushUndoState("Change Keyframe Interpolation");
            const exactKey = targetObj.userData.keyframes.find(k => Number(k.frame) === currentFrame);
            if (onlyCurrentFrame && exactKey) {
                exactKey.interp = currentInterp;
                if (currentInterp === "bezier") exactKey.bezierMode = "user";
            } else {
                targetObj.userData.keyframes.forEach(k => {
                    k.interp = currentInterp;
                    if (currentInterp === "bezier") k.bezierMode = "user";
                });
            }
            syncSceneData();
        }
        buildCameraTrajectoryPath();
        evaluateAnimation();
        notifyDetailsAndOutliner();
        monitorNeedsRender = true;
        renderStudioViewports();
    }
    if (selInterp) {
        selInterp.value = currentInterp;
        selInterp.onchange = (e) => {
            applyInterpModeToActorKeys(e.target.value || "bezier", false);
        };
    }

    if (inpCurrentFrame) {
        const commitManualFrameInput = () => {
            const parsed = parseInt(inpCurrentFrame.value, 10);
            if (!isNaN(parsed)) {
                currentFrame = Math.max(0, Math.min(Math.max(0, totalFrames - 1), parsed));
                onFrameChanged();
            } else {
                inpCurrentFrame.value = String(currentFrame);
            }
        };
        inpCurrentFrame.onchange = commitManualFrameInput;
        inpCurrentFrame.onkeydown = (e) => {
            e.stopPropagation();
            if (e.key === "Enter") {
                commitManualFrameInput();
                inpCurrentFrame.blur();
            } else if (e.key === "Escape") {
                inpCurrentFrame.value = String(currentFrame);
                inpCurrentFrame.blur();
            }
        };
    }

    const btnPrevFrame = footer.querySelector("#btn-prev-frame") || document.getElementById("btn-prev-frame");
    if (btnPrevFrame) {
        btnPrevFrame.onclick = () => {
            currentFrame = (currentFrame - 1 + totalFrames) % totalFrames;
            onFrameChanged();
        };
    }

    const btnNextFrame = footer.querySelector("#btn-next-frame") || document.getElementById("btn-next-frame");
    if (btnNextFrame) {
        btnNextFrame.onclick = () => {
            currentFrame = (currentFrame + 1) % totalFrames;
            onFrameChanged();
        };
    }

    const btnFirstFrame = footer.querySelector("#btn-first-frame") || document.getElementById("btn-first-frame");
    if (btnFirstFrame) {
        btnFirstFrame.onclick = () => {
            currentFrame = 0;
            onFrameChanged();
        };
    }

    const btnLastFrame = footer.querySelector("#btn-last-frame") || document.getElementById("btn-last-frame");
    if (btnLastFrame) {
        btnLastFrame.onclick = () => {
            currentFrame = totalFrames - 1;
            onFrameChanged();
        };
    }

    if (timelineSlider) {
        timelineSlider.oninput = (e) => {
            currentFrame = parseInt(e.target.value);
            onFrameChanged();
        };
    }

    // Timeline Context Events Store (Persisted per project in localStorage)
    function getProjectTimelineEvents(pid) {
        const id = pid || (window.currentProject ? window.currentProject.id : null);
        if (!id) return [];
        try {
            const raw = localStorage.getItem(`vfx_proj_${id}_timeline_events`);
            if (raw) {
                const arr = JSON.parse(raw);
                if (Array.isArray(arr)) return arr;
            }
        } catch (e) {}
        return [];
    }

    let timelineContextEvents = getProjectTimelineEvents();

    window.__vfxTimelineEvents = timelineContextEvents;
    window.__vfxGetTimelineEvents = () => timelineContextEvents;
    window.__vfxSaveTimelineEvents = (events) => {
        timelineContextEvents = events || [];
        window.__vfxTimelineEvents = timelineContextEvents;
        const pid = window.currentProject ? window.currentProject.id : null;
        if (pid) {
            try {
                localStorage.setItem(`vfx_proj_${pid}_timeline_events`, JSON.stringify(timelineContextEvents));
            } catch (e) {}
        }
        updateKeyframeMarkers();
        updateTimelineActiveContextBar();
    };

    window.__vfxReloadProjectTimelineEvents = (pid) => {
        timelineContextEvents = getProjectTimelineEvents(pid);
        window.__vfxTimelineEvents = timelineContextEvents;
        updateKeyframeMarkers();
        updateTimelineActiveContextBar();
    };

    window.__vfxAddTimelineEvent = (event) => {
        if (!event.id) event.id = "ctx_" + Date.now() + "_" + Math.floor(Math.random() * 1000);
        event.frame = Math.max(0, Math.min(totalFrames - 1, Number(event.frame) || 0));
        // Replace if identical type & target at exact same frame, otherwise push
        const existingIdx = timelineContextEvents.findIndex(e => e.frame === event.frame && e.type === event.type && e.targetId === event.targetId);
        if (existingIdx >= 0) {
            timelineContextEvents[existingIdx] = event;
        } else {
            timelineContextEvents.push(event);
        }
        timelineContextEvents.sort((a, b) => a.frame - b.frame);
        window.__vfxSaveTimelineEvents(timelineContextEvents);
        if (typeof window.__vfxToast === "function") {
            window.__vfxToast(`📌 Keyframed "${event.targetName}" at Frame ${event.frame}`);
        }
        return event;
    };

    window.__vfxRemoveTimelineEvent = (id) => {
        timelineContextEvents = timelineContextEvents.filter(e => e.id !== id);
        window.__vfxSaveTimelineEvents(timelineContextEvents);
    };

    window.__vfxCompileTimelinePromptScript = () => {
        if (!timelineContextEvents || timelineContextEvents.length === 0) {
            return "No keyframed objects or events registered on timeline.";
        }
        const sorted = [...timelineContextEvents].sort((a, b) => a.frame - b.frame);
        const frameGroups = new Map();
        sorted.forEach(ev => {
            const f = Number(ev.frame);
            if (!frameGroups.has(f)) frameGroups.set(f, []);
            frameGroups.get(f).push(ev);
        });

        const frames = Array.from(frameGroups.keys()).sort((a, b) => a - b);
        const lines = [];
        lines.push(`### 🎬 KESHIKI VFX TIMELINE SCRIPT (Total Frames: ${totalFrames})`);
        lines.push(`Temporal sequence of keyframed characters, locations, shots, and director cues:\n`);

        frames.forEach((f, idx) => {
            const nextF = idx < frames.length - 1 ? frames[idx + 1] - 1 : totalFrames - 1;
            const rangeStr = nextF > f ? `Frames [${String(f).padStart(3, '0')} - ${String(nextF).padStart(3, '0')}]` : `Frame [${String(f).padStart(3, '0')}]`;
            const evs = frameGroups.get(f);
            
            const typeLabels = { character: '👤 Character', location: '📍 Location', storyboard: '🎬 Shot', '3d': '🧊 3D Object', action: '⚡ Action / Cue', camera: '🎥 Camera' };
            
            lines.push(`**${rangeStr}**:`);
            evs.forEach(ev => {
                const kind = typeLabels[ev.type] || ev.type;
                const trig = ev.trigger_word ? ` (Trigger: \`${ev.trigger_word}\`)` : '';
                const note = ev.prompt_note ? ` — *${ev.prompt_note}*` : '';
                lines.push(`  • ${kind}: **${ev.targetName}**${trig}${note}`);
            });
            lines.push("");
        });

        return lines.join("\n");
    };

    function getAllKeyframeNumbers() {
        const frameSet = new Set();
        timelineContextEvents.forEach(ev => frameSet.add(Number(ev.frame)));
        const activeActors = new Set();
        if (selectedObject) activeActors.add(selectedObject);
        selectedObjects.forEach(obj => { if (obj) activeActors.add(obj); });

        activeActors.forEach(actor => {
            if (actor && actor.userData && Array.isArray(actor.userData.keyframes)) {
                actor.userData.keyframes.forEach(k => frameSet.add(Number(k.frame)));
            }
        });
        return Array.from(frameSet).sort((a, b) => a - b);
    }

    function evaluateObjectKeyframes(obj) {
        if (!obj || !obj.userData) return;
        const keys = obj.userData.keyframes || [];
        if (keys.length === 0) return;

        const applyAnimatedFocalIfChanged = (newFocal) => {
            if (newFocal !== undefined && obj.userData.type === "camera") {
                if (Math.abs((Number(obj.userData.focalMm) || 35) - Number(newFocal)) > 0.01) {
                    obj.userData.focalMm = Number(newFocal);
                    applyCameraOptics(obj, false);
                }
            }
        };

        const exact = keys.find(k => Number(k.frame) === currentFrame);
        if (exact) {
            obj.position.copy(exact.position);
            obj.quaternion.copy(exact.quaternion);
            obj.scale.copy(exact.scale);
            applyAnimatedFocalIfChanged(exact.focalMm);
        } else if (keys.length > 1) {
            let prev = null;
            let next = null;
            let prevIdx = -1;
            for (let i = 0; i < keys.length; i++) {
                const k = keys[i];
                if (k.frame <= currentFrame) {
                    if (!prev || k.frame > prev.frame) {
                        prev = k;
                        prevIdx = i;
                    }
                }
                if (k.frame >= currentFrame) {
                    if (!next || k.frame < next.frame) next = k;
                }
            }
            if (prev && next && prev !== next) {
                const rawT = (currentFrame - prev.frame) / Math.max(1, next.frame - prev.frame);
                const segInterp = prev.interp || currentInterp || "bezier";
                if (segInterp === "step") {
                    obj.position.copy(prev.position);
                    obj.quaternion.copy(prev.quaternion);
                    obj.scale.copy(prev.scale);
                    applyAnimatedFocalIfChanged(prev.focalMm);
                } else if (segInterp === "bezier") {
                    const hOut = prev.handleOut || { dx: 0.333, dy: 0.333 };
                    const hIn = next.handleIn || { dx: -0.333, dy: -0.333 };
                    const isExplicitStop = (prev.bezierMode === "ease_stop" || next.bezierMode === "ease_stop");
                    const isFlatLegacy = !isExplicitStop && (Math.abs(Number(hOut.dy) || 0) < 1e-4 && Math.abs(Number(hIn.dy) || 0) < 1e-4);
                    const isContDiag = (
                        Math.abs((Number(hOut.dy) || 0) - (Number(hOut.dx) || 0.333)) < 0.03 &&
                        Math.abs((Number(hIn.dy) || 0) - (Number(hIn.dx) || -0.333)) < 0.03
                    );
                    // Smooth pass-through without stopping:
                    // If handles are default or legacy flat, progress flows with continuous velocity (t = rawT),
                    // allowing the 3D Bezier curve (Catmull-Rom tangents) to pass through smoothly without freezing!
                    const easedT = (!isExplicitStop && (isFlatLegacy || isContDiag)) ? rawT : evalCubicBezierEasing(rawT, hOut.dx, hOut.dy, 1.0 + hIn.dx, 1.0 + hIn.dy);
                    const { p0, c0, c1, p1 } = computeSegment3DBezierControls(keys, Math.max(0, prevIdx));
                    eval3DCubicBezierPoint(p0, c0, c1, p1, easedT, obj.position);
                    obj.quaternion.slerpQuaternions(prev.quaternion, next.quaternion, easedT);
                    obj.scale.lerpVectors(prev.scale, next.scale, easedT);
                    if (prev.focalMm !== undefined && next.focalMm !== undefined) {
                        applyAnimatedFocalIfChanged(THREE.MathUtils.lerp(prev.focalMm, next.focalMm, easedT));
                    }
                } else {
                    // Linear
                    obj.position.lerpVectors(prev.position, next.position, rawT);
                    obj.quaternion.slerpQuaternions(prev.quaternion, next.quaternion, rawT);
                    obj.scale.lerpVectors(prev.scale, next.scale, rawT);
                    if (prev.focalMm !== undefined && next.focalMm !== undefined) {
                        applyAnimatedFocalIfChanged(THREE.MathUtils.lerp(prev.focalMm, next.focalMm, rawT));
                    }
                }
            } else if (prev) {
                obj.position.copy(prev.position);
                obj.quaternion.copy(prev.quaternion);
                obj.scale.copy(prev.scale);
                applyAnimatedFocalIfChanged(prev.focalMm);
            } else if (next) {
                obj.position.copy(next.position);
                obj.quaternion.copy(next.quaternion);
                obj.scale.copy(next.scale);
                applyAnimatedFocalIfChanged(next.focalMm);
            }
        }

        obj.updateMatrixWorld(true);
        if (obj === shotCamera) {
            syncShotCameraVisuals(false);
        } else if (obj.userData.type === "camera") {
            if (obj.userData.camera) obj.userData.camera.updateMatrixWorld(true);
            if (obj.userData.cameraHelper && obj.userData.camera) {
                obj.userData.cameraHelper.matrixWorld.copy(obj.userData.camera.matrixWorld);
            }
        }
    }

    function evaluateAnimation() {
        threeObjectsMap.forEach(obj => {
            evaluateObjectKeyframes(obj);
        });
        if (!isShotCameraDeleted && (!cameraMatrices || cameraMatrices.length === 0)) {
            evaluateObjectKeyframes(shotCamera);
        }
        monitorNeedsRender = true;
    }

    function onFrameChanged() {
        timelineSlider.value = currentFrame;
        if (inpCurrentFrame && document.activeElement !== inpCurrentFrame) {
            inpCurrentFrame.value = String(currentFrame);
        }
        hudFrameLabel.innerText = `Frame: [ ${String(currentFrame).padStart(3, '0')} / ${totalFrames} ]`;
        if (cameraMatrices && cameraMatrices.length > 0) {
            updateShotCameraPose(currentFrame);
        }
        evaluateAnimation();
        try { updateCharactersAtFrame(); } catch (e) { console.warn("characters:", e); }
        updateActorTexture(currentFrame);
        updateActorBillboardTransform();
        updateTransformInspector(false);
        if (isBakedViewActive) updateBakedMonitorFrame(currentFrame);
        updateTimelineActiveContextBar();
        monitorNeedsRender = true;
        renderStudioViewports();
        if (typeof window.__vfxOnExternalFrameChange === "function") {
            window.__vfxOnExternalFrameChange(currentFrame, totalFrames);
        }
    }

    function updateTimelineActiveContextBar() {
        const bar = footer.querySelector("#vfx-timeline-active-context-bar");
        if (!bar) return;
        bar.innerHTML = "";

        const currentEvents = timelineContextEvents.filter(e => Number(e.frame) === currentFrame);
        if (currentEvents.length === 0) {
            const pastEvents = timelineContextEvents.filter(e => Number(e.frame) <= currentFrame).sort((a,b) => b.frame - a.frame);
            const activeChar = pastEvents.find(e => e.type === "character");
            const activeLoc = pastEvents.find(e => e.type === "location");
            if (activeChar || activeLoc) {
                const sub = document.createElement("span");
                sub.style.cssText = "font-size: 10px; color: rgba(255,255,255,0.4); display: flex; align-items: center; gap: 4px; white-space: nowrap;";
                sub.innerHTML = `${activeChar ? '👤 ' + activeChar.targetName : ''} ${activeLoc ? '📍 ' + activeLoc.targetName : ''}`;
                bar.appendChild(sub);
            }
            return;
        }

        currentEvents.forEach(ev => {
            const chip = document.createElement("div");
            chip.className = "vfx-context-chip";
            chip.style.cssText = `
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.25);
                color: #ffffff;
                font-size: 10px;
                padding: 1px 6px;
                border-radius: 3px;
                display: flex;
                align-items: center;
                gap: 4px;
                cursor: pointer;
                white-space: nowrap;
            `;
            const icons = { character: '👤', location: '📍', storyboard: '🎬', '3d': '🧊', action: '⚡' };
            chip.innerHTML = `<span>${icons[ev.type] || '•'} ${ev.targetName}</span><span style="opacity: 0.6; font-size: 9px; margin-left: 2px;" ${tipAttr('Remove binding', 'Удалить привязку')}>✕</span>`;
            setTip(chip, `${ev.type.toUpperCase()}: ${ev.targetName}\n${ev.prompt_note || ''}\n(Click to edit, ✕ to delete)`, `${ev.type.toUpperCase()}: ${ev.targetName}\n${ev.prompt_note || ''}\n(Нажмите для редактирования, ✕ для удаления)`);
            chip.onclick = (e) => {
                if (e.target.innerText === "✕") {
                    e.stopPropagation();
                    window.__vfxRemoveTimelineEvent(ev.id);
                } else {
                    if (typeof window.__vfxOpenContextKeyModal === "function") {
                        window.__vfxOpenContextKeyModal(currentFrame, null, ev);
                    }
                }
            };
            bar.appendChild(chip);
        });
    }

    // Keyframe Navigation Controls
    const btnPrevKey = footer.querySelector("#btn-prev-key") || document.getElementById("btn-prev-key");
    if (btnPrevKey) {
        btnPrevKey.onclick = () => {
            const all = getAllKeyframeNumbers();
            if (all.length === 0) return;
            const prev = all.filter(f => f < currentFrame).pop();
            currentFrame = (prev !== undefined) ? prev : all[all.length - 1];
            onFrameChanged();
        };
    }

    const btnNextKey = footer.querySelector("#btn-next-key") || document.getElementById("btn-next-key");
    if (btnNextKey) {
        btnNextKey.onclick = () => {
            const all = getAllKeyframeNumbers();
            if (all.length === 0) return;
            const next = all.find(f => f > currentFrame);
            currentFrame = (next !== undefined) ? next : all[0];
            onFrameChanged();
        };
    }

    // Context Key Dialog & Script Trigger
    const btnContextKey = footer.querySelector("#btn-context-key") || document.getElementById("btn-context-key");
    if (btnContextKey) {
        btnContextKey.onclick = () => {
            if (typeof window.__vfxOpenContextKeyModal === "function") {
                window.__vfxOpenContextKeyModal(currentFrame, selectedObject);
            }
        };
    }

    const btnTimelineScript = footer.querySelector("#btn-timeline-script") || document.getElementById("btn-timeline-script");
    if (btnTimelineScript) {
        btnTimelineScript.onclick = () => {
            if (typeof window.__vfxOpenTimelineScriptModal === "function") {
                window.__vfxOpenTimelineScriptModal();
            }
        };
    }

    // Keyframe Management (3D + Context)
    const btnSetKey = footer.querySelector("#btn-set-key") || document.getElementById("btn-set-key");
    if (btnSetKey) {
        btnSetKey.onclick = () => {
            const is3DViewportVisible = canvasHost && canvasHost.style.display !== "none";
            const activeActors = new Set();
            if (selectedObject) activeActors.add(selectedObject);
            selectedObjects.forEach(obj => { if (obj) activeActors.add(obj); });

            if (activeActors.size > 0) {
                activeActors.forEach(act => {
                    set3DTransformKeyframe(act, currentFrame, false);
                });
            } else if (is3DViewportVisible && getActiveMonitorCameraActor()) {
                setCameraKeyframe(currentFrame);
            } else {
                if (typeof window.__vfxOpenContextKeyModal === "function") {
                    window.__vfxOpenContextKeyModal(currentFrame, null);
                }
            }
        };
    }

    function delete3DKeyframeAtFrame(targetObj, frameIdx = currentFrame) {
        const obj = targetObj || selectedObject || getActiveMonitorCameraActor();
        if (!obj || !Array.isArray(obj.userData?.keyframes)) return false;
        pushUndoState("Delete Keyframe");
        const lenBefore = obj.userData.keyframes.length;
        obj.userData.keyframes = obj.userData.keyframes.filter(k => k.frame !== frameIdx);
        const changed = obj.userData.keyframes.length !== lenBefore;
        if (changed) {
            updateKeyframeMarkers();
            if (obj.userData.type === "camera" || obj === shotCamera) {
                buildCameraTrajectoryPath();
            }
            syncSceneData();
            notifyDetailsAndOutliner();
            monitorNeedsRender = true;
            renderStudioViewports();
        }
        return changed;
    }

    function move3DKeyframeAtFrame(targetObj, oldFrame, newFrame) {
        const clampedNew = Math.max(0, Math.min(Math.max(0, totalFrames - 1), Math.round(Number(newFrame) || 0)));
        const oldF = Math.round(Number(oldFrame) || 0);
        if (clampedNew === oldF) return false;
        pushUndoState("Move Keyframe");

        const moveInActor = (actor) => {
            if (!actor || !Array.isArray(actor.userData?.keyframes)) return false;
            const key = actor.userData.keyframes.find(k => Number(k.frame) === oldF);
            if (!key) return false;
            // Remove any existing key at clampedNew except key itself
            actor.userData.keyframes = actor.userData.keyframes.filter(k => k === key || Number(k.frame) !== clampedNew);
            key.frame = clampedNew;
            actor.userData.keyframes.sort((a, b) => a.frame - b.frame);
            return true;
        };

        let moved = false;
        if (targetObj) {
            moved = moveInActor(targetObj);
        } else {
            if (selectedObject && moveInActor(selectedObject)) moved = true;
            const camAct = getActiveMonitorCameraActor();
            if (!moved && camAct && moveInActor(camAct)) moved = true;
            if (!moved) {
                threeObjectsMap.forEach(obj => {
                    if (moveInActor(obj)) moved = true;
                });
            }
        }

        if (moved) {
            updateKeyframeMarkers();
            buildCameraTrajectoryPath();
            evaluateAnimation();
            syncSceneData();
            notifyDetailsAndOutliner();
            monitorNeedsRender = true;
            renderStudioViewports();
        }
        return moved;
    }

    function update3DKeyframeBezier(targetObj, frameIdx, patch = {}, recordUndo = false) {
        const obj = targetObj || selectedObject || getActiveMonitorCameraActor();
        if (!obj || !Array.isArray(obj.userData?.keyframes)) return false;
        const key = obj.userData.keyframes.find(k => Number(k.frame) === Number(frameIdx));
        if (!key) return false;
        if (recordUndo) {
            pushUndoState("Edit Bezier Curve");
        }
        if (patch.interp !== undefined) {
            key.interp = patch.interp;
            currentInterp = patch.interp;
            if (selInterp) selInterp.value = currentInterp;
        }
        if (patch.bezierMode !== undefined) key.bezierMode = patch.bezierMode;
        if (patch.handleOut) key.handleOut = { dx: Number(patch.handleOut.dx) ?? 0.333, dy: patch.handleOut.dy !== undefined ? Number(patch.handleOut.dy) : 0.333 };
        if (patch.handleIn) key.handleIn = { dx: Number(patch.handleIn.dx) ?? -0.333, dy: patch.handleIn.dy !== undefined ? Number(patch.handleIn.dy) : -0.333 };
        if (patch.tangentOutScale !== undefined) key.tangentOutScale = Number(patch.tangentOutScale);
        if (patch.tangentInScale !== undefined) key.tangentInScale = Number(patch.tangentInScale);
        if (patch.tangentOutOffset) key.tangentOutOffset = [...patch.tangentOutOffset];
        if (patch.tangentInOffset) key.tangentInOffset = [...patch.tangentInOffset];
        buildCameraTrajectoryPath();
        evaluateAnimation();
        syncSceneData();
        monitorNeedsRender = true;
        renderStudioViewports();
        return true;
    }

    const selectedKeyframeFrames = new Set();

    const btnDelKey = footer.querySelector("#btn-del-key") || document.getElementById("btn-del-key");
    if (btnDelKey) {
        btnDelKey.onclick = () => {
            let deletedSomething = false;
            const targetActors = new Set();
            if (selectedObject) targetActors.add(selectedObject);
            selectedObjects.forEach(o => { if (o) targetActors.add(o); });
            if (targetActors.size === 0) {
                const mon = getActiveMonitorCameraActor();
                if (mon) targetActors.add(mon);
            }

            if (selectedKeyframeFrames.size > 0) {
                pushUndoState("Delete Selected Keyframes");
                targetActors.forEach(targetObj => {
                    if (targetObj && Array.isArray(targetObj.userData?.keyframes)) {
                        const beforeLen = targetObj.userData.keyframes.length;
                        targetObj.userData.keyframes = targetObj.userData.keyframes.filter(k => !selectedKeyframeFrames.has(Number(k.frame)));
                        if (targetObj.userData.keyframes.length !== beforeLen) deletedSomething = true;
                    }
                });
                const ctxBefore = timelineContextEvents.length;
                timelineContextEvents = timelineContextEvents.filter(e => !selectedKeyframeFrames.has(Number(e.frame)));
                if (timelineContextEvents.length !== ctxBefore) {
                    deletedSomething = true;
                    window.__vfxSaveTimelineEvents(timelineContextEvents);
                }
                selectedKeyframeFrames.clear();
            } else {
                targetActors.forEach(targetObj => {
                    if (delete3DKeyframeAtFrame(targetObj, currentFrame)) deletedSomething = true;
                });
                const contextBefore = timelineContextEvents.length;
                timelineContextEvents = timelineContextEvents.filter(e => Number(e.frame) !== currentFrame);
                if (timelineContextEvents.length !== contextBefore) {
                    deletedSomething = true;
                    window.__vfxSaveTimelineEvents(timelineContextEvents);
                }
            }
            updateKeyframeMarkers();
            updateTimelineActiveContextBar();
            buildCameraTrajectoryPath();
            evaluateAnimation();
            syncSceneData();
            notifyDetailsAndOutliner();
            monitorNeedsRender = true;
            renderStudioViewports();
            if (deletedSomething && typeof window.__vfxToast === "function") {
                window.__vfxToast(uiText("Keys deleted", "Удалены ключи"));
            }
        };
    }

    const btnDelAllKeys = footer.querySelector("#btn-del-all-keys") || document.getElementById("btn-del-all-keys");
    if (btnDelAllKeys) {
        btnDelAllKeys.onclick = () => {
            const targetActors = new Set();
            if (selectedObject) targetActors.add(selectedObject);
            selectedObjects.forEach(o => { if (o) targetActors.add(o); });
            if (targetActors.size === 0) {
                const mon = getActiveMonitorCameraActor();
                if (mon) targetActors.add(mon);
            }
            const hasAnyKeys = Array.from(targetActors).some(act => Array.isArray(act.userData?.keyframes) && act.userData.keyframes.length > 0);
            if (!hasAnyKeys) {
                if (typeof window.__vfxToast === "function") {
                    window.__vfxToast(uiText("No keys on selected object(s) or camera", "Нет ключей у выбранного объекта(ов) или камеры"));
                }
                return;
            }
            const doDeleteAll = () => {
                pushUndoState("Delete All Keyframes");
                targetActors.forEach(targetObj => {
                    if (targetObj && targetObj.userData) {
                        targetObj.userData.keyframes = [];
                    }
                });
                selectedKeyframeFrames.clear();
                updateKeyframeMarkers();
                updateTimelineActiveContextBar();
                buildCameraTrajectoryPath();
                evaluateAnimation();
                syncSceneData();
                notifyDetailsAndOutliner();
                monitorNeedsRender = true;
                renderStudioViewports();
                if (typeof window.__vfxToast === "function") {
                    window.__vfxToast(uiText("All keys deleted for selected object(s)", "Все ключи удалены для выбранных объектов"));
                }
            };

            if (typeof window.__vfxShowConfirmModal === "function") {
                const names = Array.from(targetActors).map(a => a.userData?.name || "Object").join(", ");
                window.__vfxShowConfirmModal({
                    title: uiText("Delete All Keys?", "Удалить все ключи?"),
                    message: uiText(`Delete all keyframes for ${targetActors.size > 1 ? targetActors.size + ' objects (' + names + ')' : '"' + names + '"'}?\n(Action can be undone with Ctrl+Z)`, `Удалить все ключевые кадры для ${targetActors.size > 1 ? targetActors.size + ' объектов (' + names + ')' : 'объекта "' + names + '"'}?\n(Действие можно отменить через Ctrl+Z)`),
                    icon: "trash-2",
                    iconColor: "#ef4444",
                    confirmText: uiText("Delete All", "Удалить все"),
                    confirmBg: "#991b1b",
                    confirmBorder: "#ef4444",
                    onConfirm: doDeleteAll
                });
            } else {
                doDeleteAll();
            }
        };
    }

    // Timeline Marquee Box Selection
    const timelineContainer = footer.querySelector("#timeline-track-container");
    const marqueeBox = footer.querySelector("#timeline-marquee-box");
    if (timelineContainer && marqueeBox) {
        timelineContainer.addEventListener("pointerdown", (e) => {
            if (e.button !== 0) return;
            if (e.target.classList && e.target.classList.contains("vfx-key-marker")) return;

            const rect = timelineContainer.getBoundingClientRect();
            const maxF = Math.max(1, totalFrames - 1);
            const thumbPad = 7;
            const usableW = Math.max(1, rect.width - thumbPad * 2);
            if (!e.shiftKey) {
                // Direct timeline scrubbing across whole track
                const setScrubFrame = (clientX) => {
                    const cRect = timelineContainer.getBoundingClientRect();
                    const ratio = Math.max(0, Math.min(1, (clientX - cRect.left - thumbPad) / usableW));
                    const f = Math.round(ratio * maxF);
                    currentFrame = Math.max(0, Math.min(maxF, f));
                    if (timelineSlider) timelineSlider.value = currentFrame;
                    onFrameChanged();
                };
                setScrubFrame(e.clientX);

                const onPointerMoveScrub = (ev) => {
                    setScrubFrame(ev.clientX);
                };
                const onPointerUpScrub = () => {
                    window.removeEventListener("pointermove", onPointerMoveScrub);
                    window.removeEventListener("pointerup", onPointerUpScrub);
                };
                window.addEventListener("pointermove", onPointerMoveScrub);
                window.addEventListener("pointerup", onPointerUpScrub);
                return;
            }

            const startX = e.clientX - rect.left;
            let isMarquee = false;

            const onPointerMoveMarquee = (ev) => {
                const curX = ev.clientX - rect.left;
                const dist = Math.abs(curX - startX);
                if (dist > 5) {
                    isMarquee = true;
                    const minX = Math.max(0, Math.min(startX, curX));
                    const maxX = Math.min(rect.width, Math.max(startX, curX));
                    marqueeBox.style.display = "block";
                    marqueeBox.style.left = `${minX}px`;
                    marqueeBox.style.width = `${maxX - minX}px`;
                    marqueeBox.style.top = "0px";
                    marqueeBox.style.height = "100%";

                    const minRatio = Math.max(0, Math.min(1, (minX - thumbPad) / usableW));
                    const maxRatio = Math.max(0, Math.min(1, (maxX - thumbPad) / usableW));
                    const startFrameRange = Math.floor(minRatio * maxF);
                    const endFrameRange = Math.ceil(maxRatio * maxF);

                    if (!ev.shiftKey) selectedKeyframeFrames.clear();

                    const targetObj = selectedObject || getActiveMonitorCameraActor();
                    if (targetObj && Array.isArray(targetObj.userData?.keyframes)) {
                        targetObj.userData.keyframes.forEach(k => {
                            const kf = Number(k.frame);
                            if (kf >= startFrameRange && kf <= endFrameRange) {
                                selectedKeyframeFrames.add(kf);
                            }
                        });
                    }
                    timelineContextEvents.forEach(cev => {
                        const cf = Number(cev.frame);
                        if (cf >= startFrameRange && cf <= endFrameRange) {
                            selectedKeyframeFrames.add(cf);
                        }
                    });

                    updateKeyframeMarkers();
                }
            };

            const onPointerUpMarquee = () => {
                window.removeEventListener("pointermove", onPointerMoveMarquee);
                window.removeEventListener("pointerup", onPointerUpMarquee);
                marqueeBox.style.display = "none";
                if (!isMarquee && !e.shiftKey) {
                    highlightedKeyframeFrame = null;
                    highlightedKeyframeActors.clear();
                    hoveredKeyframeFrame = null;
                    hoveredKeyframeActors.clear();
                    updateSelectionHelpers();
                    updateOutliner();
                    if (selectedKeyframeFrames.size > 0) {
                        selectedKeyframeFrames.clear();
                        updateKeyframeMarkers();
                    }
                    renderStudioViewports();
                }
            };

            window.addEventListener("pointermove", onPointerMoveMarquee);
            window.addEventListener("pointerup", onPointerUpMarquee);
        });
    }

    function bindKeyframeMarkerInteraction(mark, initialFrame, is3DKey, targetActors = []) {
        if (is3DKey && targetActors.length > 0) {
            mark.onpointerenter = () => {
                hoveredKeyframeFrame = initialFrame;
                hoveredKeyframeActors = new Set(targetActors);
                updateSelectionHelpers();
                updateOutliner();
                renderStudioViewports();
            };
            mark.onpointerleave = () => {
                if (hoveredKeyframeFrame === initialFrame) {
                    hoveredKeyframeFrame = null;
                    hoveredKeyframeActors.clear();
                    updateSelectionHelpers();
                    updateOutliner();
                    renderStudioViewports();
                }
            };
        }

        mark.onpointerdown = (e) => {
            if (e.button !== 0) return;
            const trackEl = timelineSlider || keyMarkersTrack;
            const rect = trackEl.getBoundingClientRect();
            const maxF = Math.max(1, totalFrames - 1);
            const thumbPad = 7;
            const usableW = Math.max(1, rect.width - thumbPad * 2);
            const playheadX = rect.left + thumbPad + (currentFrame / maxF) * usableW;
            const distToPlayheadPx = Math.abs(e.clientX - playheadX);

            // Rule requested by user:
            // "если рядом ключ и курсор то цепляется курсор а ключ цепляется только если курсор не рядом"
            // If the playhead cursor is nearby (<= 16px), grab and drag the PLAYHEAD CURSOR!
            if (distToPlayheadPx <= 16 && !e.altKey) {
                e.preventDefault();
                e.stopPropagation();
                const updatePlayheadFromClientX = (clientX) => {
                    const ratio = Math.max(0, Math.min(1, (clientX - (rect.left + thumbPad)) / usableW));
                    const nextF = Math.round(ratio * maxF);
                    if (nextF !== currentFrame) {
                        currentFrame = nextF;
                        onFrameChanged();
                    }
                };
                updatePlayheadFromClientX(e.clientX);
                const onMovePlayhead = (ev) => updatePlayheadFromClientX(ev.clientX);
                const onUpPlayhead = () => {
                    window.removeEventListener("pointermove", onMovePlayhead);
                    window.removeEventListener("pointerup", onUpPlayhead);
                };
                window.addEventListener("pointermove", onMovePlayhead);
                window.addEventListener("pointerup", onUpPlayhead);
                return;
            }

            // Otherwise (playhead cursor is NOT nearby, dist > 16px): grab and drag the KEYFRAME!
            e.preventDefault();
            e.stopPropagation();
            const startX = e.clientX;
            let dragged = false;
            let curKeyFrame = initialFrame;
            let hasRecordedDragUndo = false;

            const onMoveKey = (ev) => {
                if (!dragged && Math.abs(ev.clientX - startX) < 3) return;
                dragged = true;
                if (!hasRecordedDragUndo) {
                    pushUndoState("Move Keyframe(s)");
                    hasRecordedDragUndo = true;
                }
                const ratio = Math.max(0, Math.min(1, (ev.clientX - (rect.left + thumbPad)) / usableW));
                const nextF = Math.max(0, Math.min(maxF, Math.round(ratio * maxF)));
                if (nextF !== curKeyFrame) {
                    const deltaF = nextF - curKeyFrame;
                    if (is3DKey) {
                        const actorsToMove = targetActors.length > 0 ? targetActors : [selectedObject || getActiveMonitorCameraActor()];
                        if (selectedKeyframeFrames.has(initialFrame) && selectedKeyframeFrames.size > 1) {
                            const sortedKeys = Array.from(selectedKeyframeFrames).sort((a, b) => deltaF > 0 ? b - a : a - b);
                            const updatedSet = new Set();
                            sortedKeys.forEach(oldF => {
                                const clampedF = Math.max(0, Math.min(maxF, oldF + deltaF));
                                actorsToMove.forEach(act => {
                                    if (!act || !Array.isArray(act.userData?.keyframes)) return;
                                    const k = act.userData.keyframes.find(item => Number(item.frame) === oldF);
                                    if (k) {
                                        act.userData.keyframes = act.userData.keyframes.filter(item => item === k || Number(item.frame) !== clampedF);
                                        k.frame = clampedF;
                                        act.userData.keyframes.sort((a, b) => a.frame - b.frame);
                                    }
                                });
                                updatedSet.add(clampedF);
                            });
                            selectedKeyframeFrames.clear();
                            updatedSet.forEach(f => selectedKeyframeFrames.add(f));
                        } else {
                            actorsToMove.forEach(act => {
                                if (!act || !Array.isArray(act.userData?.keyframes)) return;
                                const k = act.userData.keyframes.find(item => Number(item.frame) === curKeyFrame);
                                if (k) {
                                    act.userData.keyframes = act.userData.keyframes.filter(item => item === k || Number(item.frame) !== nextF);
                                    k.frame = nextF;
                                    act.userData.keyframes.sort((a, b) => a.frame - b.frame);
                                }
                            });
                        }
                        curKeyFrame = nextF;
                        buildCameraTrajectoryPath();
                        evaluateAnimation();
                        monitorNeedsRender = true;
                        renderStudioViewports();
                    } else {
                        timelineContextEvents.forEach(ctxEv => {
                            if (Number(ctxEv.frame) === curKeyFrame) {
                                ctxEv.frame = nextF;
                            }
                        });
                        curKeyFrame = nextF;
                    }
                    updateKeyframeMarkers();
                }
            };

            const onUpKey = () => {
                window.removeEventListener("pointermove", onMoveKey);
                window.removeEventListener("pointerup", onUpKey);
                if (dragged) {
                    if (is3DKey) {
                        syncSceneData();
                        updateKeyframeMarkers();
                        notifyDetailsAndOutliner();
                    } else {
                        window.__vfxSaveTimelineEvents(timelineContextEvents);
                    }
                } else {
                    if (is3DKey && targetActors.length > 0) {
                        highlightedKeyframeFrame = initialFrame;
                        highlightedKeyframeActors = new Set(targetActors);
                        if (targetActors.length === 1 && targetActors[0]) {
                            selectedObject = targetActors[0];
                            transformControls.attach(selectedObject);
                            updateTransformInspector(true);
                        }
                        updateSelectionHelpers();
                        updateKeyframeMarkers();
                        updateOutliner();
                        renderStudioViewports();
                    }

                    if (e.shiftKey) {
                        if (selectedKeyframeFrames.has(initialFrame)) {
                            selectedKeyframeFrames.delete(initialFrame);
                        } else {
                            selectedKeyframeFrames.add(initialFrame);
                        }
                        updateKeyframeMarkers();
                    } else {
                        selectedKeyframeFrames.clear();
                        selectedKeyframeFrames.add(initialFrame);
                        currentFrame = initialFrame;
                        onFrameChanged();
                        updateKeyframeMarkers();
                    }
                }
            };

            window.addEventListener("pointermove", onMoveKey);
            window.addEventListener("pointerup", onUpKey);
        };
    }

    function getAllSceneActorsWithKeyframes() {
        const actors = new Set();
        if (!isShotCameraDeleted && shotCamera && Array.isArray(shotCamera.userData?.keyframes) && shotCamera.userData.keyframes.length > 0) {
            actors.add(shotCamera);
        }
        for (const obj of threeObjectsMap.values()) {
            if (obj && Array.isArray(obj.userData?.keyframes) && obj.userData.keyframes.length > 0) {
                actors.add(obj);
            }
        }
        if (scene) {
            scene.traverse(node => {
                if (node && node.userData && Array.isArray(node.userData.keyframes) && node.userData.keyframes.length > 0) {
                    let root = node;
                    while (root.parent && root.parent !== scene) {
                        if (root === camBodyMesh || root.userData?.isShotCamProxy || root.userData?.id === "cam_shot") {
                            root = shotCamera;
                            break;
                        }
                        if (threeObjectsMap.has(root.userData?.id) || root === shotCamera) break;
                        root = root.parent;
                    }
                    if (root === camBodyMesh || root.userData?.isShotCamProxy || root.userData?.id === "cam_shot") {
                        root = shotCamera;
                    }
                    actors.add(root);
                }
            });
        }
        return Array.from(actors);
    }

    function updateKeyframeMarkers() {
        if (!keyMarkersTrack) return;
        keyMarkersTrack.innerHTML = "";

        // 1. Gather all scene actors that have keyframes (for global density and phantom keys)
        const allActorsWithKeys = getAllSceneActorsWithKeyframes();
        const allObjectsKeyMap = new Map();
        allActorsWithKeys.forEach(actor => {
            if (!actor || !Array.isArray(actor.userData?.keyframes)) return;
            const label = actor.userData?.name || actor.name || "Object";
            actor.userData.keyframes.forEach(k => {
                const f = Number(k.frame);
                if (isNaN(f)) return;
                if (!allObjectsKeyMap.has(f)) {
                    allObjectsKeyMap.set(f, { frame: f, names: [], actors: [] });
                }
                const entry = allObjectsKeyMap.get(f);
                if (!entry.actors.includes(actor)) {
                    entry.actors.push(actor);
                    entry.names.push(label);
                }
            });
        });

        // 2. Gather active/selected actors and their color map
        const { activeActors, actorColorMap } = getSelectedActorColors();
        const effectiveHighlighted = hoveredKeyframeActors.size > 0 ? hoveredKeyframeActors : highlightedKeyframeActors;
        const effectiveKeyFrame = (hoveredKeyframeFrame !== null) ? hoveredKeyframeFrame : highlightedKeyframeFrame;

        const activeKeyMap = new Map();
        if (activeActors.length > 0) {
            activeActors.forEach(actor => {
                if (!actor || !Array.isArray(actor.userData?.keyframes)) return;
                const col = actorColorMap.get(actor) || "#00f0ff";
                const label = actor.userData?.name || actor.name || "Object";
                actor.userData.keyframes.forEach(k => {
                    const f = Number(k.frame);
                    if (isNaN(f)) return;
                    if (!activeKeyMap.has(f)) {
                        activeKeyMap.set(f, { frame: f, names: [], actors: [], colors: [] });
                    }
                    const entry = activeKeyMap.get(f);
                    if (!entry.actors.includes(actor)) {
                        entry.actors.push(actor);
                        entry.names.push(label);
                        entry.colors.push(col);
                    }
                });
            });
        }

        // 3. Render Phantom Keys FIRST (Behind the timeline, transparent/ghosted, showing rhythm and density)
        // Rule: "мы должны видеть на таймлинии фантомы ключей всех объектов чтобы понимать плотность и расположение
        // но они должны быть прозрачными и как бы за таймлинией"
        allObjectsKeyMap.forEach((entry, f) => {
            if (activeKeyMap.has(f)) return; // Rendered on top in active pass

            const mark = document.createElement("div");
            mark.className = "vfx-key-marker vfx-key-phantom";
            mark.setAttribute("data-frame", f);
            mark.style.cssText = `
                position: absolute;
                top: 50%;
                left: ${(f / Math.max(1, totalFrames - 1)) * 100}%;
                transform: translate(-50%, -50%) rotate(45deg);
                width: 7px;
                height: 7px;
                background: rgba(255, 255, 255, 0.16);
                border: 1px solid rgba(255, 255, 255, 0.32);
                border-radius: 1px;
                box-shadow: none;
                opacity: 0.35;
                z-index: 2;
                cursor: pointer;
                transition: opacity 0.12s ease, transform 0.12s ease, background 0.12s ease;
            `;

            mark.onpointerenter = () => {
                mark.style.opacity = "0.9";
                mark.style.background = "rgba(255, 255, 255, 0.45)";
                mark.style.transform = "translate(-50%, -50%) rotate(45deg) scale(1.3)";
            };
            mark.onpointerleave = () => {
                mark.style.opacity = "0.35";
                mark.style.background = "rgba(255, 255, 255, 0.16)";
                mark.style.transform = "translate(-50%, -50%) rotate(45deg)";
            };

            const actorsList = entry.names.map(n => `● ${n}`).join("\n");
            mark.title = tipText(
                `Phantom Keyframe [Frame ${f}] (${entry.actors.length} object${entry.actors.length > 1 ? 's' : ''}):\n${actorsList}\n• Click to jump & select object`,
                `Фантомный ключ [Кадр ${f}] (${entry.actors.length} объ.${entry.actors.length > 1 ? 'екта' : 'ект'}):\n${actorsList}\n• Нажмите для перехода и выбора объекта`
            );

            // On click on phantom key: jump to frame and select target object so key becomes active!
            mark.onpointerdown = (e) => {
                if (e.button !== 0) return;
                e.preventDefault();
                e.stopPropagation();

                if (entry.actors.length > 0 && entry.actors[0]) {
                    if (e.shiftKey) {
                        if (!selectedObjects.includes(entry.actors[0])) {
                            selectedObjects.push(entry.actors[0]);
                        }
                    } else {
                        selectObject(entry.actors[0]);
                    }
                }

                currentFrame = f;
                if (timelineSlider) timelineSlider.value = f;
                onFrameChanged();
                updateKeyframeMarkers();
            };

            keyMarkersTrack.appendChild(mark);
        });

        // 4. Render Active Selected Keys (On top of timeline, opaque, colored, glowing, interactive)
        // Rule: "а выделенны объект его ключ уже поверх таймлинии непрозрачный и цветной"
        if (activeKeyMap.size > 0) {
            activeKeyMap.forEach((entry, f) => {
                const mark = document.createElement("div");
                mark.className = "vfx-key-marker vfx-key-3d";
                mark.setAttribute("data-frame", f);
                mark.style.left = `${(f / Math.max(1, totalFrames - 1)) * 100}%`;
                mark.style.cursor = "ew-resize";

                let bgStyle = entry.colors[0];
                if (entry.colors.length === 2) {
                    bgStyle = `linear-gradient(135deg, ${entry.colors[0]} 50%, ${entry.colors[1]} 50%)`;
                } else if (entry.colors.length > 2) {
                    const step = 100 / entry.colors.length;
                    const stops = entry.colors.map((c, i) => `${c} ${(i * step).toFixed(1)}% ${((i + 1) * step).toFixed(1)}%`).join(", ");
                    bgStyle = `linear-gradient(to right, ${stops})`;
                }

                const isFrameHighlighted = (f === effectiveKeyFrame);
                const isFrameSelected = selectedKeyframeFrames.has(f);

                mark.style.width = "9px";
                mark.style.height = "9px";
                mark.style.borderRadius = "1.5px";
                mark.style.opacity = "1.0";

                if (isFrameHighlighted || isFrameSelected) {
                    mark.style.background = bgStyle;
                    mark.style.boxShadow = `0 0 12px ${entry.colors[0]}, 0 0 6px #ffffff`;
                    mark.style.outline = "2px solid #ffffff";
                    mark.style.transform = "translate(-50%, -50%) rotate(45deg) scale(1.35)";
                    mark.style.zIndex = "12";
                } else {
                    mark.style.background = bgStyle;
                    mark.style.boxShadow = `0 0 7px ${entry.colors[0]}, 0 1px 4px rgba(0,0,0,0.8)`;
                    mark.style.border = "1.5px solid #ffffff";
                    mark.style.transform = "translate(-50%, -50%) rotate(45deg)";
                    mark.style.zIndex = "8";
                }

                // Floating chip badge when highlighted or individually selected
                if (isFrameHighlighted || (isFrameSelected && selectedKeyframeFrames.size === 1)) {
                    const badge = document.createElement("div");
                    badge.className = "vfx-key-actor-chip";
                    badge.style.cssText = `
                        position: absolute;
                        bottom: 18px;
                        left: 50%;
                        transform: translateX(-50%) rotate(-45deg);
                        background: rgba(13, 17, 23, 0.95);
                        border: 1px solid ${entry.colors[0]};
                        border-radius: 4px;
                        padding: 1px 6px;
                        font-size: 9.5px;
                        font-family: 'JetBrains Mono', monospace;
                        white-space: nowrap;
                        pointer-events: none;
                        z-index: 25;
                        display: flex;
                        align-items: center;
                        gap: 4px;
                        box-shadow: 0 4px 12px rgba(0,0,0,0.85), 0 0 8px ${entry.colors[0]}55;
                    `;
                    const dots = entry.actors.map((act, i) => `<span style="color:${entry.colors[i]}; font-size:11px;">●</span><span style="color:#ffffff;">${entry.names[i]}</span>`).join(" ");
                    badge.innerHTML = dots;
                    mark.appendChild(badge);
                }

                const actorsDesc = entry.actors.map((act, i) => `● ${entry.names[i]}`).join("\n");
                const otherActors = (allObjectsKeyMap.get(f)?.actors || []).filter(a => !entry.actors.includes(a));
                const otherDesc = otherActors.length > 0 ? `\n${tipText("Other objects at this frame:", "Другие объекты на этом кадре:")}\n` + otherActors.map(a => `○ ${a.userData?.name || a.name || "Object"}`).join("\n") : "";

                mark.title = tipText(
                    `3D Keyframe [Frame ${f}]:\n${actorsDesc}${otherDesc}\n• Drag key to move\n• Click to highlight owner & jump to frame`,
                    `3D Ключ [Кадр ${f}]:\n${actorsDesc}${otherDesc}\n• Потяните ключ для перемещения\n• Нажмите для подсветки объекта и перехода к кадру`
                );
                bindKeyframeMarkerInteraction(mark, f, true, entry.actors);
                keyMarkersTrack.appendChild(mark);
            });
        }

        // 2. Gather all context events grouped by frame
        const contextFrameMap = new Map();
        timelineContextEvents.forEach(ev => {
            const f = Number(ev.frame);
            if (!contextFrameMap.has(f)) contextFrameMap.set(f, []);
            contextFrameMap.get(f).push(ev);
        });

        contextFrameMap.forEach((events, f) => {
            const mark = document.createElement("div");
            mark.className = "vfx-key-marker vfx-key-context";
            mark.style.left = `${(f / Math.max(1, totalFrames - 1)) * 100}%`;
            mark.style.cursor = "ew-resize";
            mark.setAttribute("data-frame", f);
            
            const types = events.map(e => e.type);
            let iconText = "◆";
            if (types.includes("character")) iconText = "👤";
            else if (types.includes("location")) iconText = "📍";
            else if (types.includes("storyboard")) iconText = "🎬";
            else if (types.includes("action")) iconText = "⚡";
            mark.innerText = events.length > 1 ? `${iconText}${events.length}` : iconText;

            const desc = events.map(e => `[${e.type.toUpperCase()}] ${e.targetName}${e.prompt_note ? ': "' + e.prompt_note + '"' : ''}`).join("\n");
            mark.title = tipText(
                `Frame [${String(f).padStart(3, '0')}]:\n${desc}\n• Drag key to move\n• Click to jump`,
                `Frame [${String(f).padStart(3, '0')}]:\n${desc}\n• Потяните ключ для перемещения\n• Нажмите для перехода`
            );
            bindKeyframeMarkerInteraction(mark, f, false, []);
            keyMarkersTrack.appendChild(mark);
        });
    }

    // Initialize keyframe markers and context bar on load
    setTimeout(() => {
        updateKeyframeMarkers();
        updateTimelineActiveContextBar();
    }, 100);

    // WASD + Q/E 3D Camera Movement State + Viewport Shortcuts (Physical Key Codes: works in both EN & RU layouts)
    const wasdKeys = { forward: false, backward: false, left: false, right: false, up: false, down: false, boost: false };

    window.addEventListener("keydown", (e) => {
        const tag = e.target && e.target.tagName ? e.target.tagName.toUpperCase() : "";
        const code = e.code || "";
        const k = (e.key || "").toLowerCase();
        const isUndoKey = (code === "KeyZ" || k === "z" || k === "я" || code === "KeyY" || k === "y" || k === "н") && (e.ctrlKey || e.metaKey);
        // a slider / checkbox / color well keeps focus after you touch it (e.g. brush size): undo must still reach the canvas
        const nonText = tag === "INPUT" && /^(range|checkbox|radio|color|button)$/i.test(e.target.type || "");
        if ((tag === "INPUT" && !(nonText && isUndoKey)) || tag === "SELECT" || tag === "TEXTAREA" || e.target?.isContentEditable) return;
        if (nonText && isUndoKey) { try { e.target.blur(); } catch (_) {} }

        if ((code === "KeyZ" || k === "z" || k === "я") && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            if (e.shiftKey) {
                if (typeof window.__vfxHandleGlobalRedo === "function") {
                    window.__vfxHandleGlobalRedo();
                    return;
                }
            }
            if (typeof window.__vfxHandleGlobalUndo === "function") {
                const handled = window.__vfxHandleGlobalUndo();
                if (handled) return;
            }
            popUndoState();
            return;
        }

        if ((code === "KeyY" || k === "y" || k === "н") && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            if (typeof window.__vfxHandleGlobalRedo === "function") {
                window.__vfxHandleGlobalRedo();
                return;
            }
        }

        // Numpad Camera Views & Ortho Toggle (Industry Standard DCC)
        if (code === "Numpad7") {
            e.preventDefault();
            setViewportView(e.ctrlKey ? "Bottom" : "Top");
            return;
        }
        if (code === "Numpad1") {
            e.preventDefault();
            setViewportView(e.ctrlKey ? "Back" : "Front");
            return;
        }
        if (code === "Numpad3") {
            e.preventDefault();
            setViewportView(e.ctrlKey ? "Left" : "Right");
            return;
        }
        if (code === "Numpad5") {
            e.preventDefault();
            toggleOrthoMode();
            return;
        }

        // Blender 3D Cursor Hotkeys: Shift+S (Snap) and Shift+C (Center Cursor)
        if (e.shiftKey && !e.ctrlKey && !e.metaKey && !isRmbHeld) {
            if (code === "KeyS" || k === "s" || k === "ы") {
                e.preventDefault();
                if (selectedObject) {
                    snapSelectedToCursor();
                } else {
                    snapCursorToOrigin();
                }
                return;
            }
            if (code === "KeyC" || k === "c" || k === "с") {
                e.preventDefault();
                snapCursorToOrigin();
                return;
            }
        }

        if (e.ctrlKey || e.metaKey) return;

        // Transform tool shortcuts when RMB is NOT held: 1/2/3/4 or Q/W/E/R
        if (!isRmbHeld) {
            if (code === "Digit1" || code === "KeyQ" || k === "q" || k === "й") { transformControls.detach(); return; }
            if (code === "Digit2" || code === "KeyW" || k === "w" || k === "ц") { transformControls.setMode("translate"); if (selectedObject) transformControls.attach(selectedObject); return; }
            if (code === "Digit3" || code === "KeyE" || k === "e" || k === "у") { transformControls.setMode("rotate"); if (selectedObject) transformControls.attach(selectedObject); return; }
            if (code === "Digit4" || code === "KeyR" || k === "r" || k === "к") { transformControls.setMode("scale"); if (selectedObject) transformControls.attach(selectedObject); return; }
            if (code === "Backquote" || k === "`" || k === "~" || k === "ё") {
                const btnSpace = document.getElementById("btn-coord-space");
                if (btnSpace) btnSpace.click();
                return;
            }
        }

        // 'F' Key: Focus Camera on Selected Object (or Point Cloud if none selected)
        if (code === "KeyF" && !e.altKey && !isRmbHeld) {
            e.preventDefault();
            focusCameraOnObject(selectedObject);
            return;
        }

        // WASD + Q/E 3D Free-Fly Navigation ONLY while holding Right Mouse Button (isRmbHeld)
        // Prevents accidental camera drift when pressing keys without RMB held!
        if (isRmbHeld) {
            if (code === "KeyW" || k === "w" || k === "ц") { wasdKeys.forward = true; }
            if (code === "KeyS" || k === "s" || k === "ы") { wasdKeys.backward = true; }
            if (code === "KeyA" || k === "a" || k === "ф") { wasdKeys.left = true; }
            if (code === "KeyD" || k === "d" || k === "в") { wasdKeys.right = true; }
            if (code === "KeyE" || k === "e" || k === "у") { wasdKeys.up = true; }
            if (code === "KeyQ" || k === "q" || k === "й") { wasdKeys.down = true; }
            if (e.key === "Shift" || code === "ShiftLeft" || code === "ShiftRight") { wasdKeys.boost = true; }
        }

        // 2D player owns Space while it is on screen (otherwise both players toggled and nothing happened)
        const btn2DPlay = document.getElementById("btn-2d-play");
        if (code === "Space" && btn2DPlay && btn2DPlay.offsetParent !== null && typeof window.__vfxToggle2DPlayback === "function") {
            e.preventDefault();
            window.__vfxToggle2DPlayback();
            return;
        }
        if (code === "Space") {
            e.preventDefault();
            if (isRmbHeld) {
                wasdKeys.up = true;
            } else {
                togglePlay();
            }
        }
        if (code === "KeyK" || k === "k" || k === "л") {
            e.preventDefault();
            const is3DViewportVisible = canvasHost && canvasHost.style.display !== "none";
            if (selectedObject) {
                set3DTransformKeyframe(selectedObject, currentFrame, false);
                return;
            }
            if (is3DViewportVisible && getActiveMonitorCameraActor()) {
                setCameraKeyframe(currentFrame);
                return;
            }
            const btnCtx = footer.querySelector("#btn-context-key");
            if (btnCtx) btnCtx.click();
            return;
        }
        if (code === "BracketLeft" || k === "[" || k === "х") {
            e.preventDefault();
            if (btnPrevKey) btnPrevKey.click();
            return;
        }
        if (code === "BracketRight" || k === "]" || k === "ъ") {
            e.preventDefault();
            if (btnNextKey) btnNextKey.click();
            return;
        }
        if (e.key === "Delete" || code === "Delete" || e.key === "Backspace") {
            if (selectedKeyframeFrames.size > 0) {
                e.preventDefault();
                const btnDel = footer.querySelector("#btn-del-key");
                if (btnDel) btnDel.click();
                return;
            }
            if (e.key === "Delete" || code === "Delete") deleteSelectedObjects();
        }
        // the 2D player has its own timeline (video length + in/out): leave the arrows to it
        const vis2D = document.getElementById("btn-2d-play");
        if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && vis2D && vis2D.offsetParent !== null) return;
        if (e.key === "ArrowLeft") { currentFrame = (currentFrame - 1 + totalFrames) % totalFrames; onFrameChanged(); }
        if (e.key === "ArrowRight") { currentFrame = (currentFrame + 1) % totalFrames; onFrameChanged(); }
    });

    window.addEventListener("keyup", (e) => {
        const code = e.code || "";
        const k = (e.key || "").toLowerCase();
        if (code === "KeyW" || k === "w" || k === "ц") wasdKeys.forward = false;
        if (code === "KeyS" || k === "s" || k === "ы") wasdKeys.backward = false;
        if (code === "KeyA" || k === "a" || k === "ф") wasdKeys.left = false;
        if (code === "KeyD" || k === "d" || k === "в") wasdKeys.right = false;
        if (code === "KeyE" || k === "e" || k === "у" || code === "Space") wasdKeys.up = false;
        if (code === "KeyQ" || k === "q" || k === "й") wasdKeys.down = false;
        if (e.key === "Shift" || code === "ShiftLeft" || code === "ShiftRight") wasdKeys.boost = false;
    });

    window.addEventListener("blur", () => {
        isRmbHeld = false;
        ueMouseMode = null;
        wasdKeys.forward = wasdKeys.backward = wasdKeys.left = wasdKeys.right = wasdKeys.up = wasdKeys.down = wasdKeys.boost = false;
    });

    // Undo / Redo - Full 3D Studio State (Objects, Shot Camera, Keyframes, Bezier Tangents, Optics, Events)
    const undoStack = [];
    const redoStack = [];
    const MAX_3D_UNDO = 40;

    function capture3DSnapshot(label = "Action") {
        return {
            label,
            currentFrame,
            selectedId: selectedObject ? (selectedObject === shotCamera ? "cam_shot" : (selectedObject.userData?.id || null)) : null,
            manifest: JSON.parse(JSON.stringify(exportManifest())),
            lidarDeleted: !!isLidarDeleted,
            trackedCamera: (cameraMatrices && cameraMatrices.length) ? true : false,
            shotCamera: {
                position: [shotCamera.position.x, shotCamera.position.y, shotCamera.position.z],
                quaternion: [shotCamera.quaternion.x, shotCamera.quaternion.y, shotCamera.quaternion.z, shotCamera.quaternion.w],
                scale: [shotCamera.scale.x, shotCamera.scale.y, shotCamera.scale.z],
                userData: {
                    focalMm: shotCamera.userData.focalMm ?? 35,
                    sensorPreset: shotCamera.userData.sensorPreset || "full_frame",
                    sensorWidthMm: shotCamera.userData.sensorWidthMm ?? 36.0,
                    sensorHeightMm: shotCamera.userData.sensorHeightMm ?? 24.0,
                    imageWidth: shotCamera.userData.imageWidth ?? 1920,
                    imageHeight: shotCamera.userData.imageHeight ?? 1080,
                    apertureFStop: shotCamera.userData.apertureFStop ?? 2.8,
                    focusDistanceM: shotCamera.userData.focusDistanceM ?? 3.5,
                    shutterAngleDeg: shotCamera.userData.shutterAngleDeg ?? 180,
                    anamorphicSqueeze: shotCamera.userData.anamorphicSqueeze ?? 1.0,
                    distortionMode: shotCamera.userData.distortionMode || "off",
                    distortionK1: shotCamera.userData.distortionK1 ?? 0.0,
                    distortionK2: shotCamera.userData.distortionK2 ?? 0.0,
                    frustumRayLength: shotCamera.userData.frustumRayLength ?? 3.5,
                    frustumRayOpacity: shotCamera.userData.frustumRayOpacity ?? 0.75,
                    keyframes: (shotCamera.userData.keyframes || []).map(k => ({
                        frame: k.frame,
                        position: [k.position.x, k.position.y, k.position.z],
                        rotation: [k.quaternion.x, k.quaternion.y, k.quaternion.z, k.quaternion.w],
                        scale: [k.scale.x, k.scale.y, k.scale.z],
                        focalMm: k.focalMm !== undefined ? Number(k.focalMm) : undefined,
                        interp: k.interp || "bezier",
                        bezierMode: k.bezierMode || "user",
                        handleOut: k.handleOut ? { dx: Number(k.handleOut.dx) ?? 0.333, dy: k.handleOut.dy !== undefined ? Number(k.handleOut.dy) : 0.333 } : { dx: 0.333, dy: 0.333 },
                        handleIn: k.handleIn ? { dx: Number(k.handleIn.dx) ?? -0.333, dy: k.handleIn.dy !== undefined ? Number(k.handleIn.dy) : -0.333 } : { dx: -0.333, dy: -0.333 },
                        tangentOutScale: k.tangentOutScale !== undefined ? Number(k.tangentOutScale) : 1.0,
                        tangentInScale: k.tangentInScale !== undefined ? Number(k.tangentInScale) : 1.0,
                        tangentOutOffset: Array.isArray(k.tangentOutOffset) ? [...k.tangentOutOffset] : [0, 0, 0],
                        tangentInOffset: Array.isArray(k.tangentInOffset) ? [...k.tangentInOffset] : [0, 0, 0]
                    }))
                },
                isDeleted: !!isShotCameraDeleted,
                isVisible: !!isShotCameraVisible
            },
            timelineContextEvents: (typeof timelineContextEvents !== "undefined" && Array.isArray(timelineContextEvents))
                ? JSON.parse(JSON.stringify(timelineContextEvents))
                : []
        };
    }

    function restore3DSnapshot(snap) {
        if (!snap) return;
        try {
            // 1. Restore manifest objects
            if (snap.manifest) {
                loadManifestObjects(snap.manifest);
            }
            // 2. Restore shotCamera
            if (snap.shotCamera) {
                const sc = snap.shotCamera;
                if (sc.position) shotCamera.position.fromArray(sc.position);
                if (sc.quaternion) shotCamera.quaternion.fromArray(sc.quaternion);
                if (sc.scale) shotCamera.scale.fromArray(sc.scale);
                if (sc.userData) {
                    Object.assign(shotCamera.userData, sc.userData);
                    if (Array.isArray(sc.userData.keyframes)) {
                        shotCamera.userData.keyframes = sc.userData.keyframes.map(k => ({
                            frame: k.frame,
                            position: new THREE.Vector3().fromArray(k.position || [0, 0, 0]),
                            quaternion: new THREE.Quaternion().fromArray(k.rotation || [0, 0, 0, 1]),
                            scale: new THREE.Vector3().fromArray(k.scale || [1, 1, 1]),
                            focalMm: k.focalMm !== undefined ? Number(k.focalMm) : undefined,
                            interp: k.interp || "bezier",
                            bezierMode: k.bezierMode || "user",
                            handleOut: k.handleOut ? { dx: Number(k.handleOut.dx) ?? 0.333, dy: k.handleOut.dy !== undefined ? Number(k.handleOut.dy) : 0.333 } : { dx: 0.333, dy: 0.333 },
                            handleIn: k.handleIn ? { dx: Number(k.handleIn.dx) ?? -0.333, dy: k.handleIn.dy !== undefined ? Number(k.handleIn.dy) : -0.333 } : { dx: -0.333, dy: -0.333 },
                            tangentOutScale: k.tangentOutScale !== undefined ? Number(k.tangentOutScale) : 1.0,
                            tangentInScale: k.tangentInScale !== undefined ? Number(k.tangentInScale) : 1.0,
                            tangentOutOffset: Array.isArray(k.tangentOutOffset) ? [...k.tangentOutOffset] : [0, 0, 0],
                            tangentInOffset: Array.isArray(k.tangentInOffset) ? [...k.tangentInOffset] : [0, 0, 0]
                        }));
                    }
                }
                isShotCameraDeleted = !!sc.isDeleted;
                isShotCameraVisible = !!sc.isVisible;
                if (isShotCameraDeleted) {
                    localStorage.setItem("vfx_3d_shot_cam_deleted", "true");
                } else {
                    localStorage.removeItem("vfx_3d_shot_cam_deleted");
                }
                if (camBodyMesh) {
                    camBodyMesh.visible = !isShotCameraDeleted && isShotCameraVisible && activeCamera !== shotCamera;
                }
                if (cameraPathLine) {
                    cameraPathLine.visible = !isShotCameraDeleted && isShotCameraVisible;
                }
                applyCameraOptics(shotCamera, false);
                updateCameraFrustumHelper(shotCamera);
            }
            // 3. Restore timelineContextEvents
            if (Array.isArray(snap.timelineContextEvents) && typeof timelineContextEvents !== "undefined") {
                timelineContextEvents.length = 0;
                timelineContextEvents.push(...snap.timelineContextEvents);
            }
            // 4. Restore selected object
            if (snap.selectedId) {
                if (snap.selectedId === "cam_shot") {
                    selectObject(shotCamera);
                } else {
                    const found = threeObjectsMap.get(snap.selectedId);
                    if (found) selectObject(found);
                }
            } else {
                selectObject(null);
            }
            // 5. Restore Frame
            if (typeof snap.currentFrame === "number") {
                currentFrame = Math.max(0, Math.min(totalFrames - 1, snap.currentFrame));
                if (timelineSlider) timelineSlider.value = currentFrame;
                if (timelineFrameInput) timelineFrameInput.value = currentFrame;
            }

            buildCameraTrajectoryPath();
            evaluateAnimation();
            updateKeyframeMarkers();
            renderTimelineKeyframes();
            updateSelectionHelpers();
            updateOutliner();
            notifyDetailsAndOutliner();
            syncSceneData();
            monitorNeedsRender = true;
            renderStudioViewports();
            logMsg(`Undo/Redo: ${snap.label || 'Action'}`);
        } catch (err) {
            console.error("Failed to restore 3D snapshot:", err);
        }
    }

    function pushUndoState(label = "Action") {
        try {
            const snap = capture3DSnapshot(label);
            undoStack.push(snap);
            if (undoStack.length > MAX_3D_UNDO) undoStack.shift();
            redoStack.length = 0; // Clear redo tree on new change
        } catch (e) {
            console.warn("pushUndoState failed:", e);
        }
    }

    function popUndoState() {
        if (undoStack.length === 0) return false;
        try {
            const currentSnap = capture3DSnapshot("Redo State");
            redoStack.push(currentSnap);
            if (redoStack.length > MAX_3D_UNDO) redoStack.shift();

            const prevSnap = undoStack.pop();
            restore3DSnapshot(prevSnap);
            // the point cloud and the tracked camera path are not part of the manifest: bring them back from the scene file
            const needCloud = prevSnap.lidarDeleted === false && isLidarDeleted;
            const needCam = prevSnap.trackedCamera && !(cameraMatrices && cameraMatrices.length);
            if (needCloud || needCam) {
                if (needCloud) { isLidarDeleted = false; try { localStorage.removeItem("vfx_3d_lidar_deleted"); } catch (_) {} }
                if (needCam) { isShotCameraDeleted = false; try { localStorage.removeItem("vfx_3d_shot_cam_deleted"); } catch (_) {} }
                loadSceneData().catch(() => {});
            }
            return true;
        } catch (e) {
            console.error("popUndoState error:", e);
            return false;
        }
    }

    function popRedoState() {
        if (redoStack.length === 0) return false;
        try {
            const currentSnap = capture3DSnapshot("Undo State");
            undoStack.push(currentSnap);
            if (undoStack.length > MAX_3D_UNDO) undoStack.shift();

            const nextSnap = redoStack.pop();
            restore3DSnapshot(nextSnap);
            return true;
        } catch (e) {
            console.error("popRedoState error:", e);
            return false;
        }
    }

    const btnUndoTop = document.getElementById("btn-undo-top");
    if (btnUndoTop) btnUndoTop.onclick = popUndoState;
    window.__vfxPopUndo = popUndoState;
    window.__vfxStudioUndo = popUndoState;
    window.__vfxStudioRedo = popRedoState;
    window.__vfxStudioRecordUndo = pushUndoState;

    // Manifest Sync & Save
    const pendingCharacterItems = new Map(); // characters still loading (or whose file failed): never dropped from the saved scene
    function exportManifest() {
        const pending = [...pendingCharacterItems.values()].filter(it => !threeObjectsMap.has(it.id));
        return pending.concat(exportManifestLive());
    }
    function exportManifestLive() {
        return Array.from(threeObjectsMap.values()).map(obj => ({
            id: obj.userData.id,
            name: obj.userData.name,
            type: obj.userData.type,
            position: [obj.position.x, obj.position.y, obj.position.z],
            rotation: [obj.rotation.x, obj.rotation.y, obj.rotation.z],
            scale: [obj.scale.x, obj.scale.y, obj.scale.z],
            color: obj.userData.color || (obj.material && obj.material.color ? `#${obj.material.color.getHexString()}` : "#cccccc"),
            description: obj.userData.description || "",
            passRole: obj.userData.passRole || "layout_depth",
            glbUrl: obj.userData.glbUrl || "",
            characterUrl: obj.userData.characterUrl || undefined,
            fileName: obj.userData.fileName || undefined,
            animUrl: obj.userData.animUrl || undefined,
            animName: obj.userData.animName || undefined,
            animOffset: obj.userData.type === "character" ? (obj.userData.animOffset || 0) : undefined,
            animSpeed: obj.userData.type === "character" ? (obj.userData.animSpeed || 1) : undefined,
            showBones: obj.userData.type === "character" ? obj.userData.showBones !== false : undefined,
            meshEngine: obj.userData.meshEngine || "",
            assignedImage: obj.userData.assignedImage || "",
            assignedCharacter: obj.userData.assignedCharacter || "",
            assignedAudio: obj.userData.assignedAudio || "",
            fitMode: obj.userData.fitMode || (obj.userData.type === "glb" ? "native" : "stretch"),
            pivotOffset: Array.isArray(obj.userData.pivotOffset) ? [...obj.userData.pivotOffset] : undefined,
            camConv: obj.userData.type === "camera" ? true : undefined,
            focalMm: obj.userData.type === "camera" ? (obj.userData.focalMm ?? 35) : undefined,
            sensorPreset: obj.userData.type === "camera" ? (obj.userData.sensorPreset || "full_frame") : undefined,
            sensorWidthMm: obj.userData.type === "camera" ? (obj.userData.sensorWidthMm ?? 36.0) : undefined,
            sensorHeightMm: obj.userData.type === "camera" ? (obj.userData.sensorHeightMm ?? 24.0) : undefined,
            imageWidth: obj.userData.type === "camera" ? (obj.userData.imageWidth ?? 1920) : undefined,
            imageHeight: obj.userData.type === "camera" ? (obj.userData.imageHeight ?? 1080) : undefined,
            apertureFStop: obj.userData.type === "camera" ? (obj.userData.apertureFStop ?? 2.8) : undefined,
            focusDistanceM: obj.userData.type === "camera" ? (obj.userData.focusDistanceM ?? 3.5) : undefined,
            shutterAngleDeg: obj.userData.type === "camera" ? (obj.userData.shutterAngleDeg ?? 180) : undefined,
            anamorphicSqueeze: obj.userData.type === "camera" ? (obj.userData.anamorphicSqueeze ?? 1.0) : undefined,
            distortionMode: obj.userData.type === "camera" ? (obj.userData.distortionMode || "off") : undefined,
            distortionK1: obj.userData.type === "camera" ? (obj.userData.distortionK1 ?? 0.0) : undefined,
            distortionK2: obj.userData.type === "camera" ? (obj.userData.distortionK2 ?? 0.0) : undefined,
            frustumRayLength: obj.userData.type === "camera" ? (obj.userData.frustumRayLength ?? 3.5) : undefined,
            frustumRayOpacity: obj.userData.type === "camera" ? (obj.userData.frustumRayOpacity ?? 0.75) : undefined,
            lightSubType: obj.userData.type === "light" ? (obj.userData.lightSubType || "sun") : undefined,
            lightIntensity: obj.userData.type === "light" ? (obj.userData.lightIntensity ?? 2.5) : undefined,
            lightAngleDeg: obj.userData.type === "light" ? (obj.userData.lightAngleDeg ?? 8) : undefined,
            lightRange: obj.userData.type === "light" ? (obj.userData.lightRange ?? 20) : undefined,
            areaWidth: obj.userData.type === "light" ? (obj.userData.areaWidth ?? 2.0) : undefined,
            areaHeight: obj.userData.type === "light" ? (obj.userData.areaHeight ?? 1.2) : undefined,
            castShadows: obj.userData.type === "light" ? (obj.userData.castShadows !== false) : undefined,
            keyframes: (obj.userData.keyframes || []).map(k => ({
                frame: k.frame,
                position: [k.position.x, k.position.y, k.position.z],
                rotation: [k.quaternion.x, k.quaternion.y, k.quaternion.z, k.quaternion.w],
                scale: [k.scale.x, k.scale.y, k.scale.z],
                focalMm: k.focalMm !== undefined ? k.focalMm : undefined,
                interp: k.interp || "bezier",
                bezierMode: k.bezierMode || "user",
                handleOut: k.handleOut ? { dx: k.handleOut.dx, dy: k.handleOut.dy } : { dx: 0.333, dy: 0.333 },
                handleIn: k.handleIn ? { dx: k.handleIn.dx, dy: k.handleIn.dy } : { dx: -0.333, dy: -0.333 },
                tangentOutScale: k.tangentOutScale !== undefined ? k.tangentOutScale : 1.0,
                tangentInScale: k.tangentInScale !== undefined ? k.tangentInScale : 1.0,
                tangentOutOffset: Array.isArray(k.tangentOutOffset) ? [...k.tangentOutOffset] : [0, 0, 0],
                tangentInOffset: Array.isArray(k.tangentInOffset) ? [...k.tangentInOffset] : [0, 0, 0]
            }))
        }));
    }

    let manifestServerSaveTimer = null;
    function syncSceneData() {
        sceneData = exportManifest();
        const pid = (window.currentProject && window.currentProject.id) || "";
        try {
            if (pid) {
                localStorage.setItem(`vfx_3d_manifest_cache_${pid}`, JSON.stringify(sceneData));
                const liveSun = Array.from(threeObjectsMap.values()).find(o => o.userData?.type === 'light');
                if (liveSun) {
                    localStorage.setItem(`vfx_3d_sun_${pid}`, JSON.stringify({
                        lightSubType: liveSun.userData.lightSubType || 'sun',
                        lightIntensity: liveSun.userData.lightIntensity,
                        color: liveSun.userData.color,
                        lightAngleDeg: liveSun.userData.lightAngleDeg,
                        lightRange: liveSun.userData.lightRange,
                        areaWidth: liveSun.userData.areaWidth,
                        areaHeight: liveSun.userData.areaHeight,
                        castShadows: liveSun.userData.castShadows,
                        position: [liveSun.position.x, liveSun.position.y, liveSun.position.z],
                        rotation: [liveSun.rotation.x, liveSun.rotation.y, liveSun.rotation.z]
                    }));
                }
            }
        } catch (_) {}
        if (typeof window.__vfxSyncActiveLevelData === "function") {
            try { window.__vfxSyncActiveLevelData(); } catch (_) {}
        }
        if (manifestServerSaveTimer) clearTimeout(manifestServerSaveTimer);
        manifestServerSaveTimer = setTimeout(() => {
            fetch("/vfx/save_layout_batch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    frames: [],
                    manifest: sceneData,
                    total_frames: totalFrames,
                    project_id: pid
                })
            }).catch(() => {});
        }, 500);
    }

    async function saveManifestToServer() {
        syncSceneData();
        try {
            const res = await fetch("/vfx/save_layout_batch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    frames: [],
                    manifest: sceneData,
                    total_frames: totalFrames,
                    project_id: (window.currentProject && window.currentProject.id) || ""
                })
            });
            const data = await res.json();
            if (data.status === "ok") {
                const btn = document.getElementById("btn-save-manifest");
                if (btn) {
                    const origHtml = btn.innerHTML;
                    btn.innerText = "Saved";
                    setTimeout(() => { btn.innerHTML = origHtml; if (window.lucide) window.lucide.createIcons(); }, 1800);
                }
            }
        } catch (e) {
            alert(`Failed saving manifest: ${e}`);
        }
    }

    const btnSaveManifest = document.getElementById("btn-save-manifest");
    if (btnSaveManifest) btnSaveManifest.onclick = saveManifestToServer;
    window.__vfxSaveManifest = saveManifestToServer;

    let manifestLoadGen = 0;
    function loadManifestObjects(items) {
        const myGen = ++manifestLoadGen;
        pendingCharacterItems.clear();
        threeObjectsMap.forEach(obj => {
            purgeObjectArtifacts(obj);
            scene.remove(obj);
        });
        threeObjectsMap.clear();
        transformControls.detach();
        selectedObject = null;
        selectedObjects.clear();

        try {
            selectionBoxHelpers.forEach(h => {
                scene.remove(h);
                try { h.geometry && h.geometry.dispose(); h.material && h.material.dispose(); } catch (_) {}
            });
            selectionBoxHelpers.clear();
            characterRigs.forEach(r => scene.remove(r.helper));
            characterRigs.clear();
            // Sweep any orphaned actor meshes, groups, or helpers from cancelled async loads
            const systemSceneNodes = new Set([
                gridHelper, groundAxesHelper, cursor3DGroup,
                ambientLight, hemiLight, keyLight, fillLight,
                shotCamera, camFrustumHelper, camBodyMesh,
                cameraPathLine, actorBillboard, cloudMesh,
                transformControls, transformProxyGroup
            ]);
            scene.children.slice().forEach(ch => {
                if (!ch || systemSceneNodes.has(ch) || ch.isLight) return;
                if (ch.userData?.id || ch.isSkeletonHelper || ch.type === "SkeletonHelper" || ch.type === "BoxHelper" || ch.type === "CameraHelper") {
                    purgeObjectArtifacts(ch);
                    scene.remove(ch);
                }
            });
        } catch (_) {}
        try { localStorage.removeItem("vfx_3d_manifest_cache"); } catch (_) {}
        const cleanItems = (Array.isArray(items) ? items : []).filter(it => {
            if (!it) return false;
            if (it.type === "glb" && !it.glbUrl) return false;
            if (it.type === "character" && !it.characterUrl) return false;
            if (it.type === "character" && /desert[_\s]*soldier/i.test(String(it.name || "") + " " + String(it.characterUrl || "") + " " + String(it.fileName || ""))) {
                return false;
            }
            return true;
        });
        cleanItems.forEach(item => {
            if (item.type === "character" && item.characterUrl) {
                if (item.id) pendingCharacterItems.set(item.id, item);
                addCharacter(item.characterUrl, item.name, { ...item, _loadGen: myGen, keyframes: (item.keyframes || []).map(k => ({
                    frame: k.frame, position: new THREE.Vector3().fromArray(k.position || [0, 0, 0]),
                    quaternion: new THREE.Quaternion().fromArray(k.rotation || [0, 0, 0, 1]), scale: new THREE.Vector3().fromArray(k.scale || [1, 1, 1]),
                    interp: k.interp || "bezier" })) }, false).then(() => { pendingCharacterItems.delete(item.id); }).catch(e => logMsg(`Character «${item.name}»: ${e.message}`));
                return;
            }
            if (item.type === "camera") {
                const camGroup = addFreeCamera(item.name || "Camera", false);
                if (item.id) {
                    threeObjectsMap.delete(camGroup.userData.id);
                    camGroup.userData.id = item.id;
                    threeObjectsMap.set(item.id, camGroup);
                }
                if (item.position) camGroup.position.fromArray(item.position);
                if (item.rotation) camGroup.rotation.fromArray(item.rotation);
                if (item.scale) camGroup.scale.fromArray(item.scale);
                if (item.rotation && !item.camConv) {
                    // Legacy cameras were aimed with object convention (+Z forward): re-aim
                    // along their old +Z so the frustum points the intended way.
                    camGroup.updateMatrixWorld(true);
                    const oldFwd = new THREE.Vector3(0, 0, 1).applyQuaternion(camGroup.quaternion);
                    aimCameraGroup(camGroup, camGroup.position.clone().add(oldFwd));
                }
                camGroup.updateMatrixWorld(true);
                if (camGroup.userData.camera) camGroup.userData.camera.updateMatrixWorld(true);
                if (item.color) camGroup.userData.color = item.color;
                if (item.focalMm !== undefined) camGroup.userData.focalMm = item.focalMm;
                if (item.sensorPreset !== undefined) camGroup.userData.sensorPreset = item.sensorPreset;
                if (item.sensorWidthMm !== undefined) camGroup.userData.sensorWidthMm = item.sensorWidthMm;
                if (item.sensorHeightMm !== undefined) camGroup.userData.sensorHeightMm = item.sensorHeightMm;
                if (item.imageWidth !== undefined) camGroup.userData.imageWidth = item.imageWidth;
                if (item.imageHeight !== undefined) camGroup.userData.imageHeight = item.imageHeight;
                if (item.apertureFStop !== undefined) camGroup.userData.apertureFStop = item.apertureFStop;
                if (item.focusDistanceM !== undefined) camGroup.userData.focusDistanceM = item.focusDistanceM;
                if (item.shutterAngleDeg !== undefined) camGroup.userData.shutterAngleDeg = item.shutterAngleDeg;
                if (item.anamorphicSqueeze !== undefined) camGroup.userData.anamorphicSqueeze = item.anamorphicSqueeze;
                if (item.distortionMode !== undefined) camGroup.userData.distortionMode = item.distortionMode;
                if (item.distortionK1 !== undefined) camGroup.userData.distortionK1 = item.distortionK1;
                if (item.distortionK2 !== undefined) camGroup.userData.distortionK2 = item.distortionK2;
                if (item.frustumRayLength !== undefined) camGroup.userData.frustumRayLength = item.frustumRayLength;
                if (item.frustumRayOpacity !== undefined) camGroup.userData.frustumRayOpacity = item.frustumRayOpacity;
                applyCameraOptics(camGroup, false);
                updateCameraFrustumHelper(camGroup);
                if (item.keyframes) {
                    camGroup.userData.keyframes = (item.keyframes || []).map(k => ({
                        frame: k.frame,
                        position: new THREE.Vector3().fromArray(k.position || [0, 0, 0]),
                        quaternion: new THREE.Quaternion().fromArray(k.rotation || [0, 0, 0, 1]),
                        scale: new THREE.Vector3().fromArray(k.scale || [1, 1, 1]),
                        focalMm: k.focalMm !== undefined ? Number(k.focalMm) : undefined,
                        interp: k.interp || "bezier",
                        bezierMode: k.bezierMode || "user",
                        handleOut: k.handleOut ? { dx: Number(k.handleOut.dx) ?? 0.333, dy: k.handleOut.dy !== undefined ? Number(k.handleOut.dy) : 0.333 } : { dx: 0.333, dy: 0.333 },
                        handleIn: k.handleIn ? { dx: Number(k.handleIn.dx) ?? -0.333, dy: k.handleIn.dy !== undefined ? Number(k.handleIn.dy) : -0.333 } : { dx: -0.333, dy: -0.333 },
                        tangentOutScale: k.tangentOutScale !== undefined ? Number(k.tangentOutScale) : 1.0,
                        tangentInScale: k.tangentInScale !== undefined ? Number(k.tangentInScale) : 1.0,
                        tangentOutOffset: Array.isArray(k.tangentOutOffset) ? [...k.tangentOutOffset] : [0, 0, 0],
                        tangentInOffset: Array.isArray(k.tangentInOffset) ? [...k.tangentInOffset] : [0, 0, 0]
                    }));
                }
                return;
            }
            if (item.type === "light") {
                const lightObj = addSceneLight(item.lightSubType || "sun", {
                    ...item,
                    keyframes: (item.keyframes || []).map(k => ({
                        frame: k.frame,
                        position: new THREE.Vector3().fromArray(k.position || [0, 0, 0]),
                        quaternion: new THREE.Quaternion().fromArray(k.rotation || [0, 0, 0, 1]),
                        scale: new THREE.Vector3().fromArray(k.scale || [1, 1, 1]),
                        interp: k.interp || "bezier",
                        bezierMode: k.bezierMode || "user",
                        handleOut: k.handleOut ? { dx: Number(k.handleOut.dx) ?? 0.333, dy: k.handleOut.dy !== undefined ? Number(k.handleOut.dy) : 0.333 } : { dx: 0.333, dy: 0.333 },
                        handleIn: k.handleIn ? { dx: Number(k.handleIn.dx) ?? -0.333, dy: k.handleIn.dy !== undefined ? Number(k.handleIn.dy) : -0.333 } : { dx: -0.333, dy: -0.333 },
                        tangentOutScale: k.tangentOutScale !== undefined ? Number(k.tangentOutScale) : 1.0,
                        tangentInScale: k.tangentInScale !== undefined ? Number(k.tangentInScale) : 1.0,
                        tangentOutOffset: Array.isArray(k.tangentOutOffset) ? [...k.tangentOutOffset] : [0, 0, 0],
                        tangentInOffset: Array.isArray(k.tangentInOffset) ? [...k.tangentInOffset] : [0, 0, 0]
                    }))
                }, false);
                if (item.id && lightObj) {
                    threeObjectsMap.delete(lightObj.userData.id);
                    lightObj.userData.id = item.id;
                    threeObjectsMap.set(item.id, lightObj);
                }
                return;
            }

            let m;
            if (item.type === "glb") {
                m = new THREE.Group();
            } else {
                const primType = item.type || "box";
                const geom = createPrimitiveGeometry(primType);
                if (Array.isArray(item.pivotOffset) && item.pivotOffset.length === 3) {
                    const px = Number(item.pivotOffset[0]) || 0;
                    const py = Number(item.pivotOffset[1]) || 0;
                    const pz = Number(item.pivotOffset[2]) || 0;
                    if (px * px + py * py + pz * pz > 1e-8) {
                        geom.translate(-px, -py, -pz);
                        geom.computeBoundingBox();
                        geom.computeBoundingSphere();
                    }
                }
                const mat = defMat(item.color ? new THREE.Color(item.color) : 0xcccccc, primType === "plane");
                m = new THREE.Mesh(geom, mat);
            }
            if (item.position) m.position.fromArray(item.position);
            if (item.rotation) m.rotation.fromArray(item.rotation);
            if (item.scale) m.scale.fromArray(item.scale);

            ensureActorUserData(m, {
                id: freshObjId(item.id),
                name: item.name || "Object",
                type: item.type || "box",
                color: item.color || "#cccccc",
                description: item.description || "",
                passRole: item.passRole || "layout_depth",
                glbUrl: item.glbUrl || "",
                meshEngine: item.meshEngine || "",
                assignedImage: item.assignedImage || "",
                assignedCharacter: item.assignedCharacter || "",
                assignedAudio: item.assignedAudio || "",
                fitMode: item.fitMode || (item.type === "glb" ? "native" : "stretch"),
                pivotOffset: Array.isArray(item.pivotOffset) ? [...item.pivotOffset] : undefined,
                keyframes: (item.keyframes || []).map(k => ({
                    frame: k.frame,
                    position: new THREE.Vector3().fromArray(k.position || [0, 0, 0]),
                    quaternion: new THREE.Quaternion().fromArray(k.rotation || [0, 0, 0, 1]),
                    scale: new THREE.Vector3().fromArray(k.scale || [1, 1, 1]),
                    interp: k.interp || "bezier",
                    bezierMode: k.bezierMode || "user",
                    handleOut: k.handleOut ? { dx: Number(k.handleOut.dx) ?? 0.333, dy: Number(k.handleOut.dy) ?? 0 } : { dx: 0.333, dy: 0.0 },
                    handleIn: k.handleIn ? { dx: Number(k.handleIn.dx) ?? -0.333, dy: Number(k.handleIn.dy) ?? 0 } : { dx: -0.333, dy: 0.0 },
                    tangentOutScale: k.tangentOutScale !== undefined ? Number(k.tangentOutScale) : 1.0,
                    tangentInScale: k.tangentInScale !== undefined ? Number(k.tangentInScale) : 1.0,
                    tangentOutOffset: Array.isArray(k.tangentOutOffset) ? [...k.tangentOutOffset] : [0, 0, 0],
                    tangentInOffset: Array.isArray(k.tangentInOffset) ? [...k.tangentInOffset] : [0, 0, 0]
                }))
            });

            scene.add(m);
            threeObjectsMap.set(m.userData.id, m);

            if (item.type === "glb" && item.glbUrl) {
                replaceObjectWithGLB(m.userData.id, item.glbUrl, item, false).catch(() => {});
            } else if (item.assignedImage) {
                applyTextureToObject(m.userData.id, item.assignedImage, false);
            }
        });
        buildCameraTrajectoryPath();
        updateKeyframeMarkers();
        evaluateAnimation();
        updateOutliner();
        notifyDetailsAndOutliner();
    }

    // Export .chan Trajectory
    const getChanUrl = () => {
        const pid = window.currentProject ? (window.currentProject.id || window.currentProject) : "";
        return pid ? `/vfx/export_chan?project_id=${encodeURIComponent(pid)}` : "/vfx/export_chan";
    };
    const btnExportChanTop = document.getElementById("btn-export-chan-top");
    if (btnExportChanTop) {
        btnExportChanTop.onclick = () => window.open(getChanUrl(), "_blank");
    }
    window.__vfxExportChan = () => window.open(getChanUrl(), "_blank");

    // Baking Layout Sequence
    async function bakeLayoutSequence() {
        const cardProgress = document.getElementById("render-progress-card");
        const fillProgress = document.getElementById("render-progress-fill");
        const lblProgress = document.getElementById("render-progress-label");
        const btnMonRender = document.getElementById("btn-mon-render-camera");
        const origBtnText = btnMonRender ? btnMonRender.innerHTML : "";
        if (btnMonRender) {
            btnMonRender.disabled = true;
            btnMonRender.style.opacity = "0.75";
        }

        if (cardProgress) cardProgress.style.display = "flex";
        const frames = [];

        const passSelect = document.getElementById("sidebar-render-pass");
        const passMode = passSelect ? passSelect.value : "shaded";

        let overrideMat = null;
        if (passMode === "depth") {
            overrideMat = new THREE.MeshDepthMaterial();
        } else if (passMode === "wireframe") {
            overrideMat = new THREE.MeshBasicMaterial({ wireframe: true, color: 0xffffff });
        }

        const restoreFrame = currentFrame;

        // Hide gizmos, camera helpers, selection boxes and grid so they don't burn into baked render
        const prevTcVis = transformControls ? transformControls.visible : false;
        if (transformControls) transformControls.visible = false;
        const hiddenNodes = [];
        if (Array.isArray(selectionBoxHelpers)) {
            selectionBoxHelpers.forEach(h => {
                if (h && h.visible) {
                    hiddenNodes.push(h);
                    h.visible = false;
                }
            });
        }
        if (camFrustumHelper && camFrustumHelper.visible) {
            hiddenNodes.push(camFrustumHelper);
            camFrustumHelper.visible = false;
        }
        if (camBodyMesh && camBodyMesh.visible) {
            hiddenNodes.push(camBodyMesh);
            camBodyMesh.visible = false;
        }
        if (cameraPathLine && cameraPathLine.visible) {
            hiddenNodes.push(cameraPathLine);
            cameraPathLine.visible = false;
        }
        if (gridHelper && gridHelper.visible) {
            hiddenNodes.push(gridHelper);
            gridHelper.visible = false;
        }
        if (cursor3DGroup && cursor3DGroup.visible) {
            hiddenNodes.push(cursor3DGroup);
            cursor3DGroup.visible = false;
        }
        if (typeof threeObjectsMap !== "undefined" && threeObjectsMap.forEach) {
            threeObjectsMap.forEach(obj => {
                if (obj.userData?.cameraHelper && obj.userData.cameraHelper.visible) {
                    hiddenNodes.push(obj.userData.cameraHelper);
                    obj.userData.cameraHelper.visible = false;
                }
                if (obj.userData?.isCameraProxy && obj.visible) {
                    hiddenNodes.push(obj);
                    obj.visible = false;
                }
                if (obj.userData?.lightGizmo && obj.userData.lightGizmo.visible) {
                    hiddenNodes.push(obj.userData.lightGizmo);
                    obj.userData.lightGizmo.visible = false;
                }
            });
        }
        if (typeof characterRigs !== "undefined" && characterRigs.forEach) {
            characterRigs.forEach(rig => {
                if (rig && rig.helper && rig.helper.visible) {
                    hiddenNodes.push(rig.helper);
                    rig.helper.visible = false;
                }
            });
        }

        const targetCamActor = getActiveMonitorCameraActor();
        const cam = getActiveMonitorCamera() || ((!isShotCameraDeleted && shotCamera) ? shotCamera : null);
        if (!cam) {
            console.warn("[VFX Studio] No camera available to bake layout");
            if (cardProgress) cardProgress.style.display = "none";
            if (btnMonRender) {
                btnMonRender.disabled = false;
                btnMonRender.style.opacity = "1";
                btnMonRender.innerHTML = origBtnText;
            }
            return;
        }

        // Determine exact project/camera render resolution (capped to 1280 max edge so 4K shots render & upload fast without 200MB JSON payload stalls)
        let outW = Math.max(256, Math.round(Number(targetCamActor?.userData?.imageWidth) || plateWidth || 1280));
        let outH = Math.max(144, Math.round(Number(targetCamActor?.userData?.imageHeight) || plateHeight || 720));
        const maxBakeDim = 1280;
        if (Math.max(outW, outH) > maxBakeDim) {
            const sc = maxBakeDim / Math.max(outW, outH);
            outW = Math.max(256, Math.round(outW * sc));
            outH = Math.max(144, Math.round(outH * sc));
        }
        if (outW % 2 !== 0) outW += 1;
        if (outH % 2 !== 0) outH += 1;

        const keepAspect = cam.aspect;
        cam.aspect = outW / outH;
        cam.updateProjectionMatrix();

        // Dedicated offscreen WebGL renderer ensures full 16:9 canvas without editor sidebar letterboxing/cropping
        let bakeRenderer = null;
        isBakingLayout = true;
        try {
            bakeRenderer = new THREE.WebGLRenderer({
                antialias: true,
                preserveDrawingBuffer: true,
                alpha: false,
                powerPreference: "high-performance"
            });
            bakeRenderer.setPixelRatio(1);
            bakeRenderer.setSize(outW, outH, false);
            bakeRenderer.shadowMap.enabled = true;
            bakeRenderer.shadowMap.type = THREE.PCFSoftShadowMap;
            if (THREE.sRGBEncoding !== undefined) bakeRenderer.outputEncoding = THREE.sRGBEncoding;
            bakeRenderer.toneMapping = THREE.ACESFilmicToneMapping;
            bakeRenderer.toneMappingExposure = viewportExposure;

            for (let f = 0; f < totalFrames; f++) {
                currentFrame = f;
                if (cameraMatrices && cameraMatrices.length) {
                    updateShotCameraPose(f);
                }
                evaluateAnimation();
                try { updateCharactersAtFrame(); } catch (_) {}

                // Wait for Actor Cutout video plane frame seek & decode before rendering camera frame
                await updateActorTextureAsync(f, 750);
                updateActorBillboardTransform();

                // Re-enforce hidden helpers synchronously right before render (in case rAF ran during await)
                if (transformControls) transformControls.visible = false;
                if (camFrustumHelper) camFrustumHelper.visible = false;
                if (camBodyMesh) camBodyMesh.visible = false;
                if (cameraPathLine) cameraPathLine.visible = false;
                if (gridHelper) gridHelper.visible = false;
                if (groundAxesHelper) groundAxesHelper.visible = false;
                if (cursor3DGroup) cursor3DGroup.visible = false;
                if (Array.isArray(selectionBoxHelpers)) {
                    selectionBoxHelpers.forEach(h => { if (h) h.visible = false; });
                }
                if (typeof threeObjectsMap !== "undefined" && threeObjectsMap.forEach) {
                    threeObjectsMap.forEach(obj => {
                        if (obj.userData?.cameraHelper) obj.userData.cameraHelper.visible = false;
                        if (obj.userData?.isCameraProxy) obj.visible = false;
                        if (obj.userData?.lightGizmo) obj.userData.lightGizmo.visible = false;
                    });
                }
                if (typeof characterRigs !== "undefined" && characterRigs.forEach) {
                    characterRigs.forEach(rig => {
                        if (rig && rig.helper) {
                            const showB = rig.obj ? (rig.obj.visible !== false && rig.obj.userData?.showBones !== false) : false;
                            rig.helper.visible = showB;
                        }
                    });
                }

                cam.aspect = outW / outH;
                cam.updateProjectionMatrix();
                scene.updateMatrixWorld(true);
                if (cam.parent) cam.parent.updateMatrixWorld(true);
                cam.updateMatrixWorld(true);

                if (overrideMat) scene.overrideMaterial = overrideMat;
                bakeRenderer.render(scene, cam);
                if (overrideMat) scene.overrideMaterial = null;

                const b64 = bakeRenderer.domElement.toDataURL("image/png");
                frames.push({ frame: f, image: b64 });

                const pct = Math.round(((f + 1) / totalFrames) * 80);
                if (fillProgress) fillProgress.style.width = pct + "%";
                if (lblProgress) lblProgress.innerText = uiText("Rendering frame ", "Рендер кадра ") + (f + 1) + " / " + totalFrames + "...";
                if (btnMonRender) btnMonRender.innerText = `⏳ ${f + 1}/${totalFrames}`;
                if (f % 4 === 0) await new Promise(r => setTimeout(r, 0));
            }

            // Upload in safe chunks of 30 frames to avoid 413 Payload Too Large or V8 JSON stringify freezes
            const pid = (window.currentProject && (window.currentProject.id || window.currentProject.name)) || localStorage.getItem("vfx_last_project") || "New_Movie_Shot";
            const fpsVal = (typeof projectFPS === "number" && projectFPS > 0) ? projectFPS : 24;
            const CHUNK_SIZE = 30;
            const manifestObj = exportManifest();
            for (let startIdx = 0; startIdx < frames.length; startIdx += CHUNK_SIZE) {
                const chunk = frames.slice(startIdx, startIdx + CHUNK_SIZE);
                const isFirst = startIdx === 0;
                const isLast = (startIdx + CHUNK_SIZE) >= frames.length;
                const doneCount = Math.min(frames.length, startIdx + chunk.length);
                const savePct = 80 + Math.round((doneCount / Math.max(1, frames.length)) * 20);
                if (fillProgress) fillProgress.style.width = savePct + "%";
                if (lblProgress) {
                    lblProgress.innerText = isLast
                        ? uiText("Compiling MP4 & adding to Gallery...", "Сборка MP4 и добавление в галерею...")
                        : uiText(`Saving frames ${doneCount}/${frames.length}...`, `Сохранение кадров ${doneCount}/${frames.length}...`);
                }
                if (btnMonRender) btnMonRender.innerText = isLast ? "💾 MP4..." : `💾 ${doneCount}/${frames.length}`;
                const resp = await fetch("/vfx/save_layout_batch", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        frames: chunk,
                        manifest: isLast ? manifestObj : null,
                        total_frames: totalFrames,
                        fps: fpsVal,
                        project_id: pid,
                        clear_old: isFirst,
                        compile_mp4: isLast
                    })
                });
                if (!resp.ok) {
                    const errTxt = await resp.text().catch(() => resp.statusText);
                    throw new Error(`save_layout_batch failed (${resp.status}): ${errTxt}`);
                }
            }
            lastBakeTimestamp = Date.now();
            if (isBakedViewActive) {
                updateBakedMonitorFrame(restoreFrame);
            }
            if (lblProgress) lblProgress.innerText = uiText("✅ Added to Gallery!", "✅ Добавлено в галерею!");
            const badge = document.getElementById("hdr-render-badge");
            if (badge) {
                badge.innerText = "✅ Baked";
                badge.className = "vfx-render-badge-pill vfx-badge-ready";
            }
            if (typeof window.__vfxAddBakedLayoutToGallery === "function") {
                await window.__vfxAddBakedLayoutToGallery({
                    totalFrames: totalFrames,
                    fps: fpsVal,
                    bakeTime: lastBakeTimestamp
                });
            }
            return {
                status: "ok",
                saved_count: frames.length,
                total_frames: totalFrames
            };
        } catch (e) {
            console.error("[VFX Studio] Error baking layout:", e);
            throw e;
        } finally {
            isBakingLayout = false;
            cam.aspect = keepAspect;
            cam.updateProjectionMatrix();
            if (transformControls) transformControls.visible = prevTcVis;
            hiddenNodes.forEach(n => { if (n) n.visible = true; });
            currentFrame = restoreFrame;
            onFrameChanged();
            if (btnMonRender) {
                btnMonRender.disabled = false;
                btnMonRender.style.opacity = "1";
                btnMonRender.innerHTML = origBtnText || `🎬 ${uiText('Render Camera', 'Рендер камеры')}`;
            }
            setTimeout(() => { if (cardProgress) cardProgress.style.display = "none"; }, 1500);
            if (bakeRenderer) {
                try {
                    const gl = bakeRenderer.getContext();
                    bakeRenderer.dispose();
                    if (gl && typeof gl.getExtension === "function") {
                        const loseContext = gl.getExtension("WEBGL_lose_context");
                        if (loseContext) loseContext.loseContext();
                    }
                } catch (_) {}
            }
        }
    }

    const btnRenderLayoutTop = document.getElementById("btn-render-layout-top");
    if (btnRenderLayoutTop) btnRenderLayoutTop.onclick = bakeLayoutSequence;
    const btnMonRenderCamera = document.getElementById("btn-mon-render-camera");
    if (btnMonRenderCamera) btnMonRenderCamera.onclick = bakeLayoutSequence;
    window.__vfxBakeLayout = bakeLayoutSequence;

    // Expose 3D Scene context for AI Director and studio context builder
    window.__vfxGet3DSceneContext = () => {
        const objs = [];
        threeObjectsMap.forEach(o => {
            if (!o || !o.userData) return;
            objs.push({
                id: o.userData.id,
                name: o.userData.name || o.name || "Object",
                type: o.userData.type || (characterRigs.has(o.userData.id) ? "skeleton" : (o.userData.isGLB ? "mesh" : "prop")),
                visible: o.visible !== false
            });
        });
        const activeCam = getActiveMonitorCamera();
        const activeCamActor = getActiveMonitorCameraActor();
        return {
            level: (typeof activeScene3DId !== "undefined" && typeof mediaScenes3D !== "undefined")
                ? ((mediaScenes3D.find(s => s.id === activeScene3DId) || {}).title || "Level 1: Main Stage")
                : "Level 1: Main Stage",
            camera: {
                name: (activeCamActor && (activeCamActor.userData?.name || activeCamActor.name)) || (shotCamera && shotCamera.userData?.name) || "клинплейт",
                frames: totalFrames,
                currentFrame: currentFrame
            },
            objects: objs,
            objectCount: objs.length,
            hasSkeleton: objs.some(o => o.type === "skeleton" || o.type === "character" || characterRigs.has(o.id)),
            hasMesh: objs.some(o => o.type === "mesh" || o.type === "glb"),
            hasCamera: !isShotCameraDeleted && !!shotCamera
        };
    };

    const btnSideRender = document.getElementById("btn-start-layout-render-side");
    if (btnSideRender) btnSideRender.onclick = bakeLayoutSequence;

    // Master Cocktail Pipeline Execution (All components orchestrated in 1 click)
    async function executeMasterCocktail() {
        let modal = document.getElementById("vfx-cocktail-modal");
        if (!modal) {
            modal = document.createElement("div");
            modal.id = "vfx-cocktail-modal";
            modal.style.position = "fixed";
            modal.style.inset = "0";
            modal.style.background = "rgba(10, 10, 16, 0.88)";
            modal.style.backdropFilter = "blur(8px)";
            modal.style.display = "flex";
            modal.style.alignItems = "center";
            modal.style.justifyContent = "center";
            modal.style.zIndex = "999999";
            modal.innerHTML = `
                <div style="background: #141724; border: 1px solid #7b1fa2; border-radius: 12px; width: 560px; max-width: 90vw; padding: 20px; box-shadow: 0 0 35px rgba(123, 31, 162, 0.45); color: #fff; font-family: sans-serif;">
                    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 12px; margin-bottom: 16px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <i data-lucide="layers" style="width: 20px; height: 20px; color: #f48fb1;"></i>
                            <span style="font-weight: bold; font-size: 16px; color: #f48fb1;">${uiText("Run Master Pipeline", "Запуск Единого Коктейля (Master Pipeline)")}</span>
                        </div>
                        <button id="btn-close-cocktail-modal" class="vfx-btn" style="padding: 2px 8px; font-size: 12px;">✕</button>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 10px; font-size: 13px;" id="cocktail-steps-list">
                        <div id="step-ai" style="display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 6px; background: rgba(255,255,255,0.03);">
                            <i data-lucide="bot" style="width: 16px; height: 16px; color: #a78bfa;"></i>
                            <div style="flex: 1;">
                                <div style="font-weight: bold;">${uiText("1. AI Director (Gemma 12B)", "1. AI Режиссер (Gemma 12B)")}</div>
                                <div style="font-size: 11px; color: #aaa;" id="lbl-step-ai">${uiText("Cinematic prompt and camera parameters setup", "Кинематографичный промпт и настройка параметров камеры")}</div>
                            </div>
                            <span id="badge-step-ai" style="font-size: 11px; color: rgba(255,255,255,0.5);">${uiText("Pending", "Ожидание")}</span>
                        </div>

                        <div id="step-tts" style="display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 6px; background: rgba(255,255,255,0.03);">
                            <i data-lucide="mic" style="width: 16px; height: 16px; color: rgba(255,255,255,0.7);"></i>
                            <div style="flex: 1;">
                                <div style="font-weight: bold;">${uiText("2. Voice & Lip-Sync (Neural TTS)", "2. Нейроозвучка & Липсинг (Neural TTS)")}</div>
                                <div style="font-size: 11px; color: #aaa;" id="lbl-step-tts">${uiText("Audio synthesis and frame timing calculation", "Синтез аудио и расчет хронометража кадров")}</div>
                            </div>
                            <span id="badge-step-tts" style="font-size: 11px; color: rgba(255,255,255,0.5);">${uiText("Pending", "Ожидание")}</span>
                        </div>

                        <div id="step-key" style="display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 6px; background: rgba(255,255,255,0.03);">
                            <i data-lucide="scissors" style="width: 16px; height: 16px; color: rgba(255,255,255,0.7);"></i>
                            <div style="flex: 1;">
                                <div style="font-weight: bold;">${uiText("3. CorridorKey & SAM 2.1", "3. CorridorKey & SAM 2.1")}</div>
                                <div style="font-size: 11px; color: #aaa;" id="lbl-step-key">${uiText("Green screen keying, despill and fine edge preservation", "Нейрокеинг хромакея, деспилл и сохранение тонких краев")}</div>
                            </div>
                            <span id="badge-step-key" style="font-size: 11px; color: rgba(255,255,255,0.5);">${uiText("Pending", "Ожидание")}</span>
                        </div>

                        <div id="step-layout" style="display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 6px; background: rgba(255,255,255,0.03);">
                            <i data-lucide="box" style="width: 16px; height: 16px; color: rgba(255,255,255,0.7);"></i>
                            <div style="flex: 1;">
                                <div style="font-weight: bold;">${uiText("4. 3D Studio & Calibration Markers", "4. 3D Студия & Маркеры")}</div>
                                <div style="font-size: 11px; color: #aaa;" id="lbl-step-layout">${uiText("Hardware WebGL baking of 26,010 spheres and Depth map", "Аппаратный WebGL бэйкинг сетки 26,010 сфер и Depth-карты")}</div>
                            </div>
                            <span id="badge-step-layout" style="font-size: 11px; color: rgba(255,255,255,0.5);">${uiText("Pending", "Ожидание")}</span>
                        </div>

                        <div id="step-dit" style="display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 6px; background: rgba(255,255,255,0.03);">
                            <i data-lucide="film" style="width: 16px; height: 16px; color: rgba(255,255,255,0.7);"></i>
                            <div style="flex: 1;">
                                <div style="font-weight: bold;">${uiText("5. LTX-Video 2.3/2.5 DiT + IC-LoRA Union", "5. LTX-Video 2.3/2.5 DiT + IC-LoRA Union")}</div>
                                <div style="font-size: 11px; color: #aaa;" id="lbl-step-dit">${uiText("8-step diffusion generation of photorealistic video", "8-шаговая диффузионная генерация фотореалистичного мира")}</div>
                            </div>
                            <span id="badge-step-dit" style="font-size: 11px; color: rgba(255,255,255,0.5);">${uiText("Pending", "Ожидание")}</span>
                        </div>

                        <div id="step-composite" style="display: flex; align-items: center; gap: 10px; padding: 8px; border-radius: 6px; background: rgba(255,255,255,0.03);">
                            <i data-lucide="sparkles" style="width: 16px; height: 16px; color: rgba(255,255,255,0.7);"></i>
                            <div style="flex: 1;">
                                <div style="font-weight: bold;">${uiText("6. VFX Harmonizer & Master Composite", "6. VFX Harmonizer & Мастер-Сведение")}</div>
                                <div style="font-size: 11px; color: #aaa;" id="lbl-step-composite">${uiText("Reinhard tone mapping, Light Wrap and 3D pass integration", "Reinhard tone mapping, Light Wrap и сведение 3D-пассов")}</div>
                            </div>
                            <span id="badge-step-composite" style="font-size: 11px; color: rgba(255,255,255,0.5);">${uiText("Pending", "Ожидание")}</span>
                        </div>
                    </div>

                    <div style="margin-top: 18px; display: flex; justify-content: flex-end; gap: 8px;">
                        <button id="btn-cocktail-start-confirm" class="vfx-btn" style="background: #ffffff; color: #000000; font-weight: bold; padding: 8px 16px; border: 1px solid #ffffff; border-radius: 4px; cursor: pointer;">${uiText("Start Pipeline", "Запустить процесс")}</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            if (window.lucide) window.lucide.createIcons();

            document.getElementById("btn-close-cocktail-modal").onclick = () => {
                modal.style.display = "none";
            };
        }

        modal.style.display = "flex";

        const btnConfirm = document.getElementById("btn-cocktail-start-confirm");
        btnConfirm.disabled = false;
        btnConfirm.innerText = uiText("Start Pipeline", "Запустить процесс");

        btnConfirm.onclick = async () => {
            btnConfirm.disabled = true;
            btnConfirm.innerText = uiText("Running...", "Выполняется...");

            const badgeAi = document.getElementById("badge-step-ai");
            const badgeTts = document.getElementById("badge-step-tts");
            const badgeKey = document.getElementById("badge-step-key");
            const badgeLayout = document.getElementById("badge-step-layout");
            const badgeDit = document.getElementById("badge-step-dit");
            const badgeComp = document.getElementById("badge-step-composite");

            const dirPromptElem = document.getElementById("director-prompt-text");
            const dirPrompt = dirPromptElem ? dirPromptElem.innerText.trim() : "";
            const ttsInput = document.getElementById("input-tts-text");
            const ttsText = ttsInput ? ttsInput.value.trim() : "";
            const voiceSelect = document.getElementById("select-tts-voice");
            const voiceVal = voiceSelect ? voiceSelect.value : "dmitry";
            const chkTrans = document.getElementById("chk-tts-translate");
            const isTranslate = chkTrans ? chkTrans.checked : false;

            const passSelect = document.getElementById("sidebar-render-pass");
            const passMode = passSelect ? passSelect.value : "depth";
            const loraSelect = document.getElementById("sidebar-iclora-model");
            const loraModel = loraSelect ? loraSelect.value : "union";

            badgeAi.innerText = uiText("Ready", "Готово");
            badgeAi.style.color = "#ffffff";

            badgeTts.innerText = ttsText ? uiText("Synthesizing...", "Синтезируется...") : uiText("Cached / Skipped", "Кэшировано / Пропуск");
            badgeTts.style.color = "rgba(255,255,255,0.7)";

            badgeKey.innerText = uiText("Ready for node", "Готово к ноде");
            badgeKey.style.color = "#ffffff";

            badgeLayout.innerText = uiText("WebGL Baking...", "Бэйкинг WebGL...");
            badgeLayout.style.color = "rgba(255,255,255,0.7)";
            try {
                await bakeLayoutSequence();
                badgeLayout.innerText = uiText("Baked", "Забэйкано");
                badgeLayout.style.color = "#ffffff";
            } catch (e) {
                badgeLayout.innerText = uiText(`Error: ${e}`, `Ошибка: ${e}`);
            }

            badgeDit.innerText = uiText("Sending to ComfyUI...", "Отправка в ComfyUI...");
            badgeDit.style.color = "rgba(255,255,255,0.7)";

            try {
                const res = await fetch("/vfx/pipeline/execute_cocktail", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        director_prompt: dirPrompt,
                        audio_text: ttsText,
                        audio_voice: voiceVal,
                        translate: isTranslate,
                        pass_mode: passMode,
                        iclora_model: loraModel,
                        auto_bake: true
                    })
                });
                const payload = await res.json();
                if (payload.status === "running" || payload.status === "queued" || payload.status === "ready_for_comfy") {
                    badgeDit.innerText = uiText(`In ComfyUI Queue (ID: ${payload.prompt_id ? payload.prompt_id.slice(0, 8) : 'OK'})`, `В очереди ComfyUI (ID: ${payload.prompt_id ? payload.prompt_id.slice(0, 8) : 'OK'})`);
                    badgeDit.style.color = "#ffffff";
                    if (badgeComp) {
                        badgeComp.innerText = uiText("Ready for composite", "Готово к сведению");
                        badgeComp.style.color = "#ffffff";
                    }
                    btnConfirm.innerText = uiText("Pipeline Started!", "Коктейль успешно запущен!");
                } else {
                    badgeDit.innerText = `${payload.status || uiText('Started', 'Запущено')}`;
                    if (badgeComp) badgeComp.innerText = uiText("Ready", "Готово");
                    btnConfirm.innerText = uiText("Completed", "Завершено");
                }
            } catch (err) {
                badgeDit.innerText = uiText(`Error: ${err}`, `Ошибка: ${err}`);
                badgeDit.style.color = "#ff5252";
                btnConfirm.disabled = false;
                btnConfirm.innerText = uiText("Retry", "Повторить");
            }
        };
    }

    const btnCocktailMaster = document.getElementById("btn-master-cocktail");
    if (btnCocktailMaster) btnCocktailMaster.onclick = executeMasterCocktail;

    // Dual Viewport Animation Loop (with 4K / 1080p Multi-Monitor DPI & UE5 Free-Fly Navigation)
    const wasdForwardVec = new THREE.Vector3();
    const wasdRightVec = new THREE.Vector3();
    const wasdMoveDelta = new THREE.Vector3();

    let lastPlayStepTime = 0;
    let lastViewportCssH = 0;

    // WebGL Post-Processing for Real-Time Lens Distortion (Apply / Compensate) in Shot Camera Monitor
    let monitorDistortRT = null;
    let monitorDistortScene = null;
    let monitorDistortCam = null;
    let monitorDistortMat = null;

    function ensureMonitorDistortionPipeline(pixelW, pixelH) {
        if (!monitorDistortRT) {
            monitorDistortRT = new THREE.WebGLRenderTarget(Math.max(16, pixelW), Math.max(16, pixelH), {
                minFilter: THREE.LinearFilter,
                magFilter: THREE.LinearFilter,
                format: THREE.RGBAFormat
            });
            monitorDistortScene = new THREE.Scene();
            monitorDistortCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
            monitorDistortMat = new THREE.ShaderMaterial({
                uniforms: {
                    tDiffuse: { value: monitorDistortRT.texture },
                    k1: { value: 0.0 },
                    k2: { value: 0.0 },
                    aspect: { value: 1.7778 },
                    squeeze: { value: 1.0 }
                },
                vertexShader: `
                    varying vec2 vUv;
                    void main() {
                        vUv = uv;
                        gl_Position = vec4(position.xy, 0.0, 1.0);
                    }
                `,
                fragmentShader: `
                    uniform sampler2D tDiffuse;
                    uniform float k1;
                    uniform float k2;
                    uniform float aspect;
                    uniform float squeeze;
                    varying vec2 vUv;
                    void main() {
                        vec2 p = (vUv - 0.5) * 2.0;
                        p.x *= aspect / max(0.25, squeeze);
                        float r2 = dot(p, p);
                        float r4 = r2 * r2;
                        float factor = 1.0 + k1 * r2 + k2 * r4;
                        vec2 dp = p * factor;
                        dp.x /= (aspect / max(0.25, squeeze));
                        vec2 sampleUv = dp * 0.5 + 0.5;
                        if (sampleUv.x < 0.0 || sampleUv.x > 1.0 || sampleUv.y < 0.0 || sampleUv.y > 1.0) {
                            gl_FragColor = vec4(0.03, 0.035, 0.05, 1.0);
                        } else {
                            gl_FragColor = texture2D(tDiffuse, sampleUv);
                        }
                    }
                `,
                depthTest: false,
                depthWrite: false
            });
            const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), monitorDistortMat);
            monitorDistortScene.add(quad);
        } else if (monitorDistortRT.width !== pixelW || monitorDistortRT.height !== pixelH) {
            monitorDistortRT.setSize(Math.max(16, pixelW), Math.max(16, pixelH));
        }
    }

    let lastFilmGateFitW = 0;
    let lastFilmGateFitH = 0;
    let lastMonitorGateW = 320;
    let lastMonitorGateH = 180;
    let lastAnimFrameTs = 0;

    function renderStudioViewports() {
        // Skip 3D render completely if the 3D viewport canvas is hidden or detached (e.g. user is in 1D/2D tab)
        if (!canvasHost || canvasHost.style.display === "none" || canvasHost.style.visibility === "hidden" || canvasHost.clientWidth === 0) {
            return;
        }

        // Fit Film Gate (only write DOM style when size actually changes — zero per-frame layout reflow!)
        if (filmGateBox && monitorFrameBox && ueMouseMode === null) {
            const contW = monitorFrameBox.clientWidth - 16;
            const contH = monitorFrameBox.clientHeight - 16;
            if (contW > 20 && contH > 20) {
                const activeMonActor = getActiveMonitorCameraActor();
                let monAspect = plateAspect;
                let monW = plateWidth;
                let monH = plateHeight;
                if (activeMonActor && activeMonActor.userData) {
                    const wVal = Number(activeMonActor.userData.imageWidth);
                    const hVal = Number(activeMonActor.userData.imageHeight);
                    if (wVal > 0 && hVal > 0) {
                        monW = wVal;
                        monH = hVal;
                        const sq = Number(activeMonActor.userData.anamorphicSqueeze) || 1.0;
                        monAspect = (wVal / hVal) * sq;
                    } else if (activeMonActor.userData.camera && activeMonActor.userData.camera.aspect > 0) {
                        monAspect = activeMonActor.userData.camera.aspect;
                    }
                }
                let fitW = contW;
                let fitH = fitW / monAspect;
                if (fitH > contH) {
                    fitH = contH;
                    fitW = fitH * monAspect;
                }
                const targetW = Math.floor(fitW);
                const targetH = Math.floor(fitH);
                if (targetW !== lastFilmGateFitW || targetH !== lastFilmGateFitH) {
                    lastFilmGateFitW = targetW;
                    lastFilmGateFitH = targetH;
                    filmGateBox.style.width = `${targetW}px`;
                    filmGateBox.style.height = `${targetH}px`;
                    lastMonitorGateW = targetW;
                    lastMonitorGateH = targetH;
                    monitorNeedsRender = true;
                }
                const aspectBadge = document.getElementById("monitor-aspect-badge");
                if (aspectBadge) {
                    aspectBadge.innerText = `${monW}×${monH} (${monAspect.toFixed(2)}:1)`;
                }
            }
        }

        const w = canvasHost.clientWidth || window.innerWidth;
        const h = canvasHost.clientHeight || window.innerHeight;
        const optimalDpr = calculateOptimalDpr(w, h);

        if (Math.abs(optimalDpr - currentDpr) > 0.01) {
            currentDpr = optimalDpr;
            renderer.setPixelRatio(currentDpr);
            renderer.setSize(w, h, false);
            updateAdaptivePointCloudSize(h);
            refreshViewportRectsCache();
            monitorNeedsRender = true;
        } else if (w > 0 && h > 0 && (renderer.domElement.width !== Math.floor(w * currentDpr) || renderer.domElement.height !== Math.floor(h * currentDpr))) {
            renderer.setSize(w, h, false);
            updateAdaptivePointCloudSize(h);
            refreshViewportRectsCache();
            monitorNeedsRender = true;
        } else if (Math.abs(h - lastViewportCssH) > 4) {
            lastViewportCssH = h;
            updateAdaptivePointCloudSize(h);
        }

        const leftW = getMainViewportWidth(w);

        // 1. Render Left Viewport: 3D Perspective / Ortho
        renderer.setViewport(0, 0, leftW, h);
        if (isOrthoMode) {
            updateOrthoCameraFrustum();
        } else {
            activeCamera.aspect = leftW / Math.max(1, h);
            activeCamera.updateProjectionMatrix();
        }

        const hasCameraTrack = (Array.isArray(cameraMatrices) && cameraMatrices.length > 0) || Boolean(cameraPathLine);
        const showShotCamInVp = !isShotCameraDeleted && isShotCameraVisible && (activeCamera !== shotCamera);
        if (!isShotCameraDeleted) {
            syncShotCameraVisuals(false);
        }
        if (camBodyMesh) camBodyMesh.visible = showShotCamInVp;
        if (camFrustumHelper) camFrustumHelper.visible = showShotCamInVp;
        if (cameraPathLine) cameraPathLine.visible = hasCameraTrack && (activeCamera !== shotCamera);
        if (gridHelper) gridHelper.visible = isGridVisible;
        if (groundAxesHelper) groundAxesHelper.visible = isGridVisible;

        threeObjectsMap.forEach(obj => {
            if (obj.userData && obj.userData.cameraHelper) {
                obj.updateMatrixWorld(true);
                if (obj.userData.camera) {
                    obj.userData.camera.updateMatrixWorld(true);
                    obj.userData.cameraHelper.matrixWorld.copy(obj.userData.camera.matrixWorld);
                }
            }
        });

        updateDefaultRigForUserLights();

        // Update bounding box selection & keyframe highlight helpers
        selectionBoxHelpers.forEach((helper, actor) => {
            if (helper && actor && helper.visible) {
                helper.update();
            }
        });

        // Align 3D Cursor ring with active camera view so it always appears circular
        if (cursor3DGroup && cursor3DGroup.visible && cursor3DGroup.children.length > 0) {
            cursor3DGroup.children[0].quaternion.copy(activeCamera.quaternion);
        }

        renderer.render(scene, activeCamera);
        renderAxisCompass();

        // 2. Render Right Bottom Viewport: Shot Camera Film Gate
        const emptyWatermark = document.getElementById("monitor-empty-watermark");
        const paneMon = document.getElementById("pane-cam-monitor");
        const targetCam = getActiveMonitorCamera();
        if (targetCam && monitorRenderer && monitorCanvas && filmGateBox && isMonitorPreviewEnabled && paneMon && paneMon.style.display !== "none") {
            const gw = lastMonitorGateW || filmGateBox.clientWidth;
            const gh = lastMonitorGateH || filmGateBox.clientHeight;
            if (gw > 10 && gh > 10) {
                if (emptyWatermark && emptyWatermark.style.display !== "none") emptyWatermark.style.display = "none";
                if (monitorCanvas.style.display !== "block") monitorCanvas.style.display = "block";

                const nowMs = performance.now();
                // Responsive monitor refresh: render immediately if explicitly flagged or playing,
                // or throttle to ~30 FPS (33ms) so Orbit manipulation, environment, and materials never freeze!
                if (!monitorNeedsRender && !isPlaying && (nowMs - lastMonitorRenderTime < 33)) {
                    return;
                }
                lastMonitorRenderTime = nowMs;
                monitorNeedsRender = false;

                const expectedW = Math.round(gw * monitorRenderer.getPixelRatio());
                const expectedH = Math.round(gh * monitorRenderer.getPixelRatio());
                if (monitorCanvas.width !== expectedW || monitorCanvas.height !== expectedH) {
                    monitorRenderer.setSize(gw, gh, false);
                }

                // Ensure the target camera's world matrix is 100% up-to-date before rendering
                if (targetCam.parent) targetCam.parent.updateMatrixWorld(true);
                targetCam.updateMatrixWorld(true);

                // Temporarily hide transform gizmo, ALL camera helpers, selection outlines, and ALL camera proxy meshes
                // so a camera mesh or frustum is NEVER visible inside the Shot Camera Monitor!
                const prevTcVis = transformControls ? transformControls.visible : false;
                if (transformControls) transformControls.visible = false;

                const hiddenNodes = [];
                selectionBoxHelpers.forEach(h => {
                    if (h && h.visible) {
                        hiddenNodes.push(h);
                        h.visible = false;
                    }
                });
                if (camFrustumHelper && camFrustumHelper.visible) {
                    hiddenNodes.push(camFrustumHelper);
                    camFrustumHelper.visible = false;
                }
                if (camBodyMesh && camBodyMesh.visible) {
                    hiddenNodes.push(camBodyMesh);
                    camBodyMesh.visible = false;
                }
                threeObjectsMap.forEach(obj => {
                    if (obj.userData?.cameraHelper && obj.userData.cameraHelper.visible) {
                        hiddenNodes.push(obj.userData.cameraHelper);
                        obj.userData.cameraHelper.visible = false;
                    }
                    if (obj.userData?.isCameraProxy && obj.visible) {
                        hiddenNodes.push(obj);
                        obj.visible = false;
                    }
                    if (obj.userData?.lightGizmo && obj.userData.lightGizmo.visible) {
                        hiddenNodes.push(obj.userData.lightGizmo);
                        obj.userData.lightGizmo.visible = false;
                    }
                });
                if (cursor3DGroup && cursor3DGroup.visible) {
                    hiddenNodes.push(cursor3DGroup);
                    cursor3DGroup.visible = false;
                }

                const prevBodyVis = camBodyMesh ? camBodyMesh.visible : false;
                const prevFrustumVis = camFrustumHelper ? camFrustumHelper.visible : false;
                const prevPathVis = cameraPathLine ? cameraPathLine.visible : false;
                if (camBodyMesh) camBodyMesh.visible = false;
                if (camFrustumHelper) camFrustumHelper.visible = false;
                if (cameraPathLine) cameraPathLine.visible = false;

                targetCam.aspect = gw / gh;
                targetCam.updateProjectionMatrix();

                const targetCamActor = getActiveMonitorCameraActor();
                const camUd = targetCamActor?.userData || {};
                const distMode = camUd.distortionMode || "off";
                const rawK1 = Number(camUd.distortionK1) || 0;
                const rawK2 = Number(camUd.distortionK2) || 0;
                const squeeze = Number(camUd.anamorphicSqueeze) || 1.0;
                const hasDistortion = distMode !== "off" && (Math.abs(rawK1) > 0.0005 || Math.abs(rawK2) > 0.0005 || Math.abs(squeeze - 1.0) > 0.01);

                if (hasDistortion) {
                    ensureMonitorDistortionPipeline(expectedW, expectedH);
                    const sign = distMode === "compensate" ? -1.0 : 1.0;
                    monitorDistortMat.uniforms.tDiffuse.value = monitorDistortRT.texture;
                    monitorDistortMat.uniforms.k1.value = sign * rawK1;
                    monitorDistortMat.uniforms.k2.value = sign * rawK2;
                    monitorDistortMat.uniforms.aspect.value = gw / gh;
                    monitorDistortMat.uniforms.squeeze.value = squeeze;

                    monitorRenderer.setRenderTarget(monitorDistortRT);
                    monitorRenderer.render(scene, targetCam);
                    monitorRenderer.setRenderTarget(null);
                    monitorRenderer.render(monitorDistortScene, monitorDistortCam);
                } else {
                    monitorRenderer.setRenderTarget(null);
                    monitorRenderer.render(scene, targetCam);
                }

                // Restore visibility for main perspective viewport
                if (transformControls) transformControls.visible = prevTcVis;
                hiddenNodes.forEach(n => { n.visible = true; });
                if (camBodyMesh) camBodyMesh.visible = prevBodyVis;
                if (camFrustumHelper) camFrustumHelper.visible = prevFrustumVis;
                if (cameraPathLine) cameraPathLine.visible = prevPathVis;
            }
        } else {
            if (monitorCanvas && monitorCanvas.style.display !== "none") {
                monitorCanvas.style.display = "none";
            }
            if (emptyWatermark && emptyWatermark.style.display !== "block") {
                emptyWatermark.style.display = "block";
            }
        }
    }

    function animate(nowTs) {
        requestAnimationFrame(animate);
        const now = nowTs || performance.now();
        const dtMs = lastAnimFrameTs > 0 ? Math.min(50, Math.max(4, now - lastAnimFrameTs)) : 16.667;
        lastAnimFrameTs = now;

        if (customActorCutoutMediaType === "video" && customActorCutoutTex && customActorCutoutVideo) {
            if (isPlaying) {
                customActorCutoutTex.needsUpdate = true;
                monitorNeedsRender = true;
            }
        }
        if (isPlaying && !isBakingLayout) {
            const is2DActive = document.getElementById("vfx-2d-media-stage") !== null;
            if (is2DActive) {
                isPlaying = false;
                if (btnPlayPause) btnPlayPause.innerText = "▶ Play";
            } else {
                const playInterval = 1000.0 / Math.max(1.0, projectFPS);
                const actorSeeking = isActorProjectionVisible && customActorCutoutMediaType === "video" && customActorCutoutVideo && customActorCutoutVideo.seeking;
                const waitForActorDecode = actorSeeking && (now - lastPlayStepTime < 350);
                if (!waitForActorDecode && (now - lastPlayStepTime >= playInterval)) {
                    lastPlayStepTime = now;
                    currentFrame = (currentFrame + 1) % totalFrames;
                    onFrameChanged();
                }
            }
        }

        // Apply 6DoF WASD + Q/E Free-Fly Navigation to flyCamera ONLY when RMB is held
        if (!isRmbHeld) {
            wasdKeys.forward = wasdKeys.backward = wasdKeys.left = wasdKeys.right = wasdKeys.up = wasdKeys.down = wasdKeys.boost = false;
        } else if (activeCamera === flyCamera && (wasdKeys.forward || wasdKeys.backward || wasdKeys.left || wasdKeys.right || wasdKeys.up || wasdKeys.down)) {
            const dtScale = dtMs / 16.667;
            const baseSpeed = 0.085 * flySpeedMultiplier * dtScale;
            const speed = wasdKeys.boost ? (baseSpeed * 2.8) : baseSpeed;
            flyCamera.up.set(0, 0, 1);
            flyCamera.getWorldDirection(wasdForwardVec);
            wasdForwardVec.normalize();
            wasdRightVec.crossVectors(wasdForwardVec, flyCamera.up).normalize();

            wasdMoveDelta.set(0, 0, 0);
            if (wasdKeys.forward) wasdMoveDelta.addScaledVector(wasdForwardVec, speed);
            if (wasdKeys.backward) wasdMoveDelta.addScaledVector(wasdForwardVec, -speed);
            if (wasdKeys.right) wasdMoveDelta.addScaledVector(wasdRightVec, speed);
            if (wasdKeys.left) wasdMoveDelta.addScaledVector(wasdRightVec, -speed);
            if (wasdKeys.up) wasdMoveDelta.z += speed;
            if (wasdKeys.down) wasdMoveDelta.z -= speed;

            flyCamera.position.add(wasdMoveDelta);
            camPivotTarget.add(wasdMoveDelta);
            applyYawPitchToCamera();
        }

        renderStudioViewports();
    }

    // Initialize Studio Architecture, Google Stitch Minimalist UI, Details Inspector & 3D Mesh Replacement
    const controlsTopPane = document.getElementById("controls-top-pane");
    const splitterH = document.getElementById("splitter-studio-h");
    const paneCamMonitor = document.getElementById("pane-cam-monitor");
    if (controlsTopPane && splitterH && paneCamMonitor) {
        initStudioArchitecture({
            workspace,
            canvasHost,
            leftPaneOverlay,
            splitterV,
            studioRightCol,
            controlsTopPane,
            paneCamMonitor,
            splitterH,
            header,
            footer,
            getTotalFrames: () => totalFrames,
            getCurrentFrame: () => currentFrame,
            setCurrentFrame: (f) => {
                currentFrame = Math.max(0, Math.min(totalFrames - 1, f));
                onFrameChanged();
            },
            onTogglePlay: togglePlay,
            getIsPlaying: () => isPlaying,
            onUndo3D: popUndoState,
            onRedo3D: popRedoState,
            recordUndo: (label) => pushUndoState(label),
            invalidateViewportRectsCache: invalidateViewportRectsCache,
            onToggleShotCam: () => {
                const btnC = document.getElementById("btn-cam-view");
                if (btnC) btnC.click();
                return activeCamera === shotCamera;
            },
            onSetPointCloudVisible: (vis) => {
                if (cloudMesh) cloudMesh.visible = vis;
            },
            getPointCloudSize: () => cloudPointSize,
            onSetPointCloudSize: (sz) => {
                cloudPointSize = Math.max(0.005, Math.min(3.0, parseFloat(sz) || 0.065));
                localStorage.setItem("vfx_ue_point_size", String(cloudPointSize));
                const sl = document.getElementById("slider-cloud-size");
                const lbl = document.getElementById("lbl-cloud-size");
                if (sl) sl.value = String(cloudPointSize);
                if (lbl) lbl.innerText = cloudPointSize.toFixed(2);
                updateAdaptivePointCloudSize(canvasHost.clientHeight);
            },
            getFlySpeed: () => flySpeedMultiplier,
            onSetFlySpeed: (spd) => {
                flySpeedMultiplier = Math.max(0.05, Math.min(8.0, parseFloat(spd) || 1.0));
                localStorage.setItem("vfx_ue_fly_speed", String(flySpeedMultiplier));
            },
            onFocusSelectedObject: () => focusCameraOnObject(selectedObject),
            onExportManifest: exportManifest,
            onLoadManifest: loadManifestObjects,
            onReloadSceneData: loadSceneData,
            onTrigger3DMarkers: () => {
                const btnM = document.getElementById("btn-add-markers");
                if (btnM) btnM.click();
            },
            onSetFocalLength: (focalMm) => {
                const camActor = getActiveMonitorCameraActor() || shotCamera;
                if (camActor && camActor.userData) {
                    camActor.userData.focalMm = Number(focalMm) || 35;
                    applyCameraOptics(camActor, true);
                } else {
                    const fovDeg = 2 * Math.atan(24 / (2 * focalMm)) * (180 / Math.PI);
                    shotCamera.fov = fovDeg;
                    shotCamera.updateProjectionMatrix();
                }
            },
            onApplyCameraOptics: (camActor) => applyCameraOptics(camActor || selectedObject || getActiveMonitorCameraActor(), true),
            onSet3DKeyframe: (f) => set3DTransformKeyframe(selectedObject || getActiveMonitorCameraActor(), f ?? currentFrame, false),
            onDelete3DKeyframe: (f) => delete3DKeyframeAtFrame(selectedObject || getActiveMonitorCameraActor(), f ?? currentFrame),
            onMove3DKeyframe: (oldF, newF, targetObj) => move3DKeyframeAtFrame(targetObj || selectedObject || getActiveMonitorCameraActor(), oldF, newF),
            onUpdate3DKeyframeBezier: (frameIdx, patch, targetObj) => update3DKeyframeBezier(targetObj || selectedObject || getActiveMonitorCameraActor(), frameIdx, patch),
            getCurrentInterp: () => currentInterp,
            setCurrentInterp: (mode, onlyCurrentFrame = false) => applyInterpModeToActorKeys(mode, onlyCurrentFrame),
            getCoordSpace: () => transformControls.space,
            onBakeLayout: bakeLayoutSequence,
            onSaveScene3D: saveManifestToServer,
            onExportChan: () => window.open(getChanUrl(), "_blank"),
            onAddDefaultCamera: (preset) => createDefaultCameraTrajectory(preset),
            onSetCameraKeyframe: (f) => setCameraKeyframe(f),
            getHasCameraTrack: () => (Array.isArray(cameraMatrices) && cameraMatrices.length > 0) || Boolean(getActiveMonitorCamera()),
            getProjectFPS: () => projectFPS,
            setProjectFPS: (fps) => setProjectFrameRate(fps),
            setTotalFrames: (n) => setProjectTotalFrames(n),
            getPlateWidth: () => plateWidth,
            getPlateHeight: () => plateHeight,
            setProjectResolution: (w, h) => setProjectResolution(w, h),
            onExecuteCocktail: executeMasterCocktail,
            // Details Inspector & 3D Proxy-to-Mesh Replacement Hooks
            getSelectedObject: () => selectedObject,
            getSceneObjects: () => Array.from(threeObjectsMap.values()).map(obj => ({
                id: obj.userData.id,
                name: obj.userData.name,
                type: obj.userData.type,
                color: obj.userData.color,
                userData: obj.userData,
                position: obj.position,
                rotation: obj.rotation,
                scale: obj.scale
            })),
            onSelectObjectById: (id, isMulti = false) => {
                if (!id) {
                    selectObject(null);
                    return;
                }
                const key = String(id);
                const obj = threeObjectsMap.get(key) || threeObjectsMap.get(Number(id)) || Array.from(threeObjectsMap.values()).find(o => String(o.userData?.id) === key);
                selectObject(obj || null, isMulti);
            },
            onAddFreeCamera: (nameOverride = "") => addFreeCamera(nameOverride),
            onAddLight: (subType = "sun", opts = {}) => addSceneLight(subType, opts, true),
            onUpdateLightProps: (patch = {}, recordUndo = false) => updateLightActor(selectedObject, patch, recordUndo),
            onSpawnPrimitive: (primType = "box", nameOverride = "") => {
                const normType = String(primType).toLowerCase();
                if (normType.includes("cam")) {
                    return addFreeCamera(nameOverride);
                }
                if (normType === "sun" || normType === "light_sun") {
                    return addSceneLight("sun", { name: nameOverride || undefined }, true);
                }
                if (normType === "point" || normType === "light_point" || normType === "light") {
                    return addSceneLight("point", { name: nameOverride || undefined }, true);
                }
                if (normType === "area" || normType === "light_area") {
                    return addSceneLight("area", { name: nameOverride || undefined }, true);
                }
                const isPlane = normType.includes("plane") || normType.includes("floor");
                const finalType = isPlane ? "plane" : normType;
                pushUndoState(`Add ${finalType}`);
                const geom = createPrimitiveGeometry(finalType);
                const mat = defMat(finalType === "plane" ? 0x888888 : 0xcccccc, finalType === "plane");
                const m = new THREE.Mesh(geom, mat);
                m.position.copy(cursor3DPosition);
                return registerObject(m, nameOverride || `${finalType.charAt(0).toUpperCase() + finalType.slice(1)}_${nextObjId}`, finalType);
            },
            onUpdateSelectedObjectTransform: (arg1, rotDegArr, scaleArr) => {
                if (!selectedObject) return;
                if (arg1 && typeof arg1 === "object" && !Array.isArray(arg1)) {
                    const { position, rotationDeg, scale } = arg1;
                    if (position) {
                        selectedObject.position.set(Number(position.x) || 0, Number(position.y) || 0, Number(position.z) || 0);
                    }
                    if (rotationDeg) {
                        selectedObject.rotation.set(
                            THREE.MathUtils.degToRad(Number(rotationDeg.x) || 0),
                            THREE.MathUtils.degToRad(Number(rotationDeg.y) || 0),
                            THREE.MathUtils.degToRad(Number(rotationDeg.z) || 0)
                        );
                    }
                    if (scale) {
                        selectedObject.scale.set(
                            Math.max(0.01, Number(scale.x) || 1),
                            Math.max(0.01, Number(scale.y) || 1),
                            Math.max(0.01, Number(scale.z) || 1)
                        );
                    }
                } else {
                    const posArr = arg1;
                    if (Array.isArray(posArr) && posArr.length === 3) {
                        selectedObject.position.set(posArr[0], posArr[1], posArr[2]);
                    }
                    if (Array.isArray(rotDegArr) && rotDegArr.length === 3) {
                        selectedObject.rotation.set(
                            THREE.MathUtils.degToRad(rotDegArr[0]),
                            THREE.MathUtils.degToRad(rotDegArr[1]),
                            THREE.MathUtils.degToRad(rotDegArr[2])
                        );
                    }
                    if (Array.isArray(scaleArr) && scaleArr.length === 3) {
                        selectedObject.scale.set(
                            Math.max(0.01, scaleArr[0]),
                            Math.max(0.01, scaleArr[1]),
                            Math.max(0.01, scaleArr[2])
                        );
                    }
                }
                selectedObject.updateMatrixWorld(true);
                if (selectedObject === shotCamera) {
                    syncShotCameraVisuals(true);
                } else if (selectedObject.userData && selectedObject.userData.camera) {
                    selectedObject.userData.camera.updateMatrixWorld(true);
                    if (selectedObject.userData.cameraHelper) {
                        selectedObject.userData.cameraHelper.matrixWorld.copy(selectedObject.userData.camera.matrixWorld);
                    }
                }
                if (autoKeyEnabled && Array.isArray(selectedObject.userData?.keyframes) && selectedObject.userData.keyframes.length > 0) {
                    set3DTransformKeyframe(selectedObject, currentFrame, true);
                }
                updateTransformInspector(false);
                syncSceneData();
                monitorNeedsRender = true;
                renderStudioViewports();
            },
            onUpdateSelectedObjectMeta: (metaPatch = {}) => {
                if (!selectedObject) return;
                ensureActorUserData(selectedObject);
                if (metaPatch.colorHex && !metaPatch.color) {
                    metaPatch.color = metaPatch.colorHex;
                }
                if (metaPatch.referenceImageUrl !== undefined) {
                    metaPatch.assignedImage = metaPatch.referenceImageUrl;
                }
                if (metaPatch.assignedCharacterId !== undefined) {
                    metaPatch.assignedCharacter = metaPatch.assignedCharacterId;
                }
                if (metaPatch.assignedVoiceId !== undefined) {
                    metaPatch.assignedAudio = metaPatch.assignedVoiceId;
                }
                Object.assign(selectedObject.userData, metaPatch);
                if (metaPatch.color) {
                    setActorColor(selectedObject, metaPatch.color);
                }
                if (selectedObject.userData.type === "camera") {
                    applyCameraOptics(selectedObject, true);
                }
                if (selectedObject.userData.type === "light") {
                    rebuildLightInternals(selectedObject);
                }
                updateOutliner();
                selectedObject.updateMatrixWorld(true);
                if (selectedObject === shotCamera) {
                    syncShotCameraVisuals(true);
                } else if (selectedObject.userData && selectedObject.userData.camera) {
                    selectedObject.userData.camera.updateMatrixWorld(true);
                    updateCameraFrustumHelper(selectedObject);
                }
                updateTransformInspector(false);
                syncSceneData();
                monitorNeedsRender = true;
                renderStudioViewports();
            },
            onReplaceObjectWithGLB: (targetId, glbUrl, metaPatch = {}) => {
                if (metaPatch.stretchToBox !== undefined) {
                    metaPatch.fitMode = metaPatch.stretchToBox ? "stretch" : "uniform";
                }
                return replaceObjectWithGLB(targetId, glbUrl, metaPatch);
            },
            onSwapObjectPrimitive: (targetId, primType) => {
                const normType = String(primType).toLowerCase().includes("plane") || String(primType).toLowerCase().includes("floor")
                    ? "plane"
                    : String(primType).toLowerCase();
                return swapObjectPrimitive(targetId, normType);
            },
            onApplyTextureToObject: applyTextureToObject,
            onDeleteSelectedObject: deleteSelectedObjects,
            onDuplicateSelectedObject: duplicateSelectedObject,
            onResetSelectedObjectTransform: resetSelectedObjectTransform,
            onSnapSelectedObjectToFloor: snapSelectedObjectToFloor,
            onTriggerImportMeshDialog: () => {
                const inp = document.getElementById("file-mesh-input");
                if (inp) inp.click();
            },
            onCaptureSelectedObjectPlateCrop: async () => {
                const activeCam = viewThroughShotCam ? shotCamera : camera;
                const plateImg = (bgTexture && bgTexture.image) ? bgTexture.image : null;
                const fallbackUrl = `/vfx/plate_prep/default/frame/undist/${currentFrame}`;
                return await projectObject3DBBoxToPlateCrop({
                    THREE,
                    targetObj: selectedObject,
                    camera: activeCam,
                    plateImage: plateImg,
                    fallbackUrl,
                    marginRatio: 0.22,
                    outputSize: 512
                });
            },
            // 3D Cursor & Pivot Integration
            get3DCursorPosition: () => cursor3DPosition.clone(),
            set3DCursorPosition: (x, y, z) => set3DCursorPosition(x, y, z),
            snapCursorToSelected: () => snapCursorToSelected(),
            snapSelectedToCursor: () => snapSelectedToCursor(),
            snapCursorToOrigin: () => snapCursorToOrigin(),
            onApplyObjectPivot: (targetObj, mode, customDelta) => applyObjectPivot(targetObj, mode, customDelta),
            addSplatActor: (splatData) => addSplatActor(splatData)
        });
    }

    if (window.lucide) {
        try { window.lucide.createIcons(); } catch (_) {}
    }

    // Start everything
    await loadSceneData();
    animate();
}
