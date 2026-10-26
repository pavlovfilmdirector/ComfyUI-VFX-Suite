import { app } from "../../../scripts/app.js";

/**
 * VFX Plate Prep Studio: Interactive Staged Processing Pipeline
 * ==============================================================
 * Independent Step-by-Step execution:
 * 1. Load Video / Sequence & Scrubbing
 * 2. Lens Undistortion (k1, k2) & Distortion Grid Overlay
 * 3. YOLOv8 Dynamic Actor & Prop Masking
 * 4. Mask Thickness (Dilation / Erosion)
 * 5. Manual Mask Refinement Brush (Add / Subtract)
 */

// Inject CSS styles if not yet loaded
if (!document.getElementById("vfx-suite-styles")) {
    const link = document.createElement("link");
    link.id = "vfx-suite-styles";
    link.rel = "stylesheet";
    link.href = "/extensions/ComfyUI-VFX-Suite/css/layout_editor.css";
    document.head.appendChild(link);
}

// Open Interactive Full Studio Modal
async function openStudioModal(node) {
    const nodeId = String(node.id || "default");
    let currentFrame = 0;
    let totalFrames = 1;
    let activeStage = "undist"; // "raw", "undist", "mask", "cutout"
    // Fetch existing metadata for this specific node
    let info = { total_frames: 0, width: 1920, height: 1080, fps: 25, k1: 0, k2: 0, thickness: 20 };
    try {
        const r = await fetch(`/vfx/plate_prep/info?node_id=${nodeId}`);
        if (r.ok) info = await r.json();
    } catch (e) {}

    async function apiPost(endpoint, body = {}) {
        const res = await fetch(`/vfx/plate_prep/${endpoint}?node_id=${nodeId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ node_id: nodeId, ...body })
        });
        if (!res.ok) {
            const errText = await res.text();
            throw new Error(`Ошибка сервера (${res.status}): ${errText}`);
        }
        return await res.json();
    }

                    totalFrames = info.total_frames || 1;
                    currentFrame = 0;

                    // Get values from node widgets if any (legacy compatibility)
                    const wInputPath = node.widgets ? node.widgets.find(w => w.name === "input_path") : null;
                    const wK1 = node.widgets ? node.widgets.find(w => w.name === "k1_distortion") : null;
                    const wK2 = node.widgets ? node.widgets.find(w => w.name === "k2_distortion") : null;
                    const wClasses = node.widgets ? node.widgets.find(w => w.name === "mask_classes") : null;
                    const wDilation = node.widgets ? node.widgets.find(w => w.name === "mask_dilation") : null;

                    const modal = document.createElement("div");
                    modal.className = "vfx-modal-backdrop";

                    // Header
                    const header = document.createElement("div");
                    header.className = "vfx-modal-header";
                    header.innerHTML = `
                        <div class="vfx-header-group">
                            <span class="vfx-header-title">🎞️ Plate Prep Studio — Staged Processing Pipeline</span>
                            <span class="vfx-badge-status" id="hud-res-badge">${info.width}x${info.height} • ${totalFrames} frames</span>
                        </div>
                        <div class="vfx-header-group">
                            <button class="vfx-btn" id="btn-view-raw">Raw Plate</button>
                            <button class="vfx-btn vfx-btn-active" id="btn-view-undist">Undistorted</button>
                            <button class="vfx-btn" id="btn-view-mask">Mask Overlay</button>
                            <button class="vfx-btn" id="btn-view-cutout">Actor Cutout</button>
                            <button class="vfx-btn vfx-btn-primary" id="btn-studio-save">💾 Save & Sync</button>
                            <button class="vfx-btn vfx-btn-danger" id="btn-studio-close">✕ Close</button>
                        </div>
                    `;
                    modal.appendChild(header);

                    // Body: Viewport (Left) + Controls (Right)
                    const body = document.createElement("div");
                    body.className = "vfx-studio-body";

                    // --- Left: Viewport & Canvas ---
                    const viewportPane = document.createElement("div");
                    viewportPane.className = "vfx-studio-viewport-pane";

                    const canvasContainer = document.createElement("div");
                    canvasContainer.className = "vfx-studio-canvas-container";

                    const mainCanvas = document.createElement("canvas");
                    mainCanvas.className = "vfx-studio-main-canvas";
                    mainCanvas.width = 1280;
                    mainCanvas.height = 720;
                    canvasContainer.appendChild(mainCanvas);
                    viewportPane.appendChild(canvasContainer);

                    // Timeline Footer on viewport
                    const timelineFooter = document.createElement("div");
                    timelineFooter.className = "vfx-modal-footer";
                    timelineFooter.style.cssText = "position: relative; width: 100%; box-sizing: border-box;";
                    timelineFooter.innerHTML = `
                        <div class="vfx-timeline-track-container" style="position: relative; width: 100%;">
                            <input type="range" class="vfx-timeline-slider" id="studio-scrubber" min="0" max="${Math.max(0, totalFrames - 1)}" value="0" />
                            <div class="vfx-keyframe-markers" id="studio-timeline-markers"></div>
                        </div>
                        <div class="vfx-timeline-controls">
                            <div class="vfx-key-group">
                                <button class="vfx-btn" id="btn-studio-play">▶ Play</button>
                                <button class="vfx-btn" id="btn-prev-key" title="Предыдущий ключевой кадр (Hotkeys: [ или Ctrl+◀)">⏮ Ключ</button>
                                <button class="vfx-btn" id="btn-prev-frame" title="Предыдущий кадр (◀)">◀ Step</button>
                                <button class="vfx-btn" id="btn-next-frame" title="Следующий кадр (▶)">Step ▶</button>
                                <button class="vfx-btn" id="btn-next-key" title="Следующий ключевой кадр (Hotkeys: ] или Ctrl+▶)">Ключ ⏭</button>
                                <span style="font-family: monospace; font-size: 13px; color: #4f80ff;" id="studio-frame-counter">[ 000 / ${totalFrames} ]</span>
                            </div>
                            <div class="vfx-key-group">
                                <label style="font-size: 11px; color: #ffca28; display: flex; align-items: center; gap: 4px; cursor: pointer;" title="Инвертировать маску вырезания/трекинга">
                                    <input type="checkbox" id="chk-invert-mask" ${info.invert_mask ? "checked" : ""} /> 🔲 Инвертировать маску
                                </label>
                                <label style="font-size: 11px; color: #aaa; display: flex; align-items: center; gap: 4px; cursor: pointer;">
                                    <input type="checkbox" id="chk-grid-overlay" /> Show Distortion Grid
                                </label>
                            </div>
                        </div>
                    `;
                    viewportPane.appendChild(timelineFooter);
                    body.appendChild(viewportPane);

                    // --- Right: Staged Process Controls ---
                    const controlsPane = document.createElement("div");
                    controlsPane.className = "vfx-studio-controls-pane";
                    controlsPane.innerHTML = `
                        <!-- Stage 1: Load Footage -->
                        <div class="vfx-panel-section">
                            <div class="vfx-panel-title">
                                <span>📁 1. Load Video / Sequence</span>
                                <span style="font-size: 10px; color: #888;">Step 1</span>
                            </div>
                            <div class="vfx-panel-row" style="gap: 6px;">
                                <button class="vfx-btn vfx-btn-primary" id="btn-pick-video" style="flex: 1;">🎬 Выбрать видео</button>
                                <button class="vfx-btn vfx-btn-primary" id="btn-pick-folder" style="flex: 1;">📂 Выбрать папку</button>
                            </div>
                            <input type="file" id="file-input-video" accept="video/*,.mp4,.mov,.mkv,.avi,.webm" style="display: none;" />
                            <input type="file" id="file-input-folder" webkitdirectory directory multiple style="display: none;" />
                            <div class="vfx-panel-row" style="margin-top: 4px;">
                                <input type="text" class="vfx-text-input" id="input-footage-path" placeholder="Или путь на диске..." value="${wInputPath ? wInputPath.value : ''}" style="flex: 1;" />
                                <button class="vfx-btn" id="btn-proc-load" style="white-space: nowrap;">Загрузить</button>
                            </div>
                        </div>

                        <!-- Stage 2: Lens Undistortion -->
                        <div class="vfx-panel-section">
                            <div class="vfx-panel-title">
                                <span>🔧 2. Lens Undistortion (k1, k2)</span>
                                <span style="font-size: 10px; color: #888;">Step 2</span>
                            </div>
                            <div class="vfx-panel-row" style="align-items: center;">
                                <span style="width: 32px; min-width: 32px; font-weight: 500;">k1:</span>
                                <input type="range" id="slider-k1" min="-0.6" max="0.6" step="0.01" value="${info.k1 !== undefined ? info.k1 : (wK1 ? wK1.value : 0.0)}" style="flex: 1;" />
                                <span style="font-family: monospace; width: 45px; text-align: right;" id="lbl-k1">0.00</span>
                            </div>
                            <div class="vfx-panel-row" style="align-items: center;">
                                <span style="width: 32px; min-width: 32px; font-weight: 500;">k2:</span>
                                <input type="range" id="slider-k2" min="-0.4" max="0.4" step="0.01" value="${info.k2 !== undefined ? info.k2 : (wK2 ? wK2.value : 0.0)}" style="flex: 1;" />
                                <span style="font-family: monospace; width: 45px; text-align: right;" id="lbl-k2">0.00</span>
                            </div>
                            <div class="vfx-panel-row" style="gap: 6px; margin-top: 4px;">
                                <button class="vfx-btn" id="btn-proc-undistort" style="flex: 2; background: #1e3a5f; color: #90caf9; font-weight: bold;">
                                    ⚡ Применить дисторсию
                                </button>
                                <button class="vfx-btn" id="btn-auto-calibrate" style="flex: 1; background: #2c2545; color: #ce93d8;" title="Авто-определение дисторсии линзы">
                                    ✨ Авто
                                </button>
                                <button class="vfx-btn" id="btn-reset-distortion" style="flex: 1;" title="Сбросить k1, k2 к 0.00">
                                    ↺ Сброс
                                </button>
                            </div>
                        </div>

                        <!-- Stage 3: AI Dynamic Masking & User Upload -->
                        <div class="vfx-panel-section">
                            <div class="vfx-panel-title">
                                <span>🤖 3. AI & User Masking</span>
                                <span style="font-size: 10px; color: #888;">Step 3</span>
                            </div>
                            <div style="font-size: 11px; color: #8a94a8; margin-bottom: 4px; font-weight: 500;">Движок сегментации:</div>
                            <div class="vfx-panel-row">
                                <select id="select-mask-engine" class="vfx-text-input" style="cursor: pointer; font-weight: bold; width: 100%; border-color: #2e4359; background: #131722;">
                                    <option value="sam2" ${(!info.mask_engine || info.mask_engine === 'sam2') ? 'selected' : ''}>🎯 SAM 2.1 (SOTA Контуры & Кино-точность)</option>
                                    <option value="yolo" ${info.mask_engine === 'yolo' ? 'selected' : ''}>⚡ YOLOv8 (Сверхбыстрый драфт / Real-time)</option>
                                </select>
                            </div>
                            <div class="vfx-panel-row" style="margin-top: 4px;">
                                <input type="text" class="vfx-text-input" id="input-mask-classes" value="${info.mask_classes || (wClasses ? wClasses.value : 'person, car, truck, bus, motorcycle')}" title="Classes to track" />
                            </div>
                            <div class="vfx-panel-row">
                                <label style="font-size: 11px; color: #aaa; display: flex; align-items: center; gap: 4px;">
                                    <input type="checkbox" id="chk-detect-props" checked /> Green / Dynamic Props
                                </label>
                            </div>
                            <button class="vfx-btn" id="btn-proc-automask" style="background: #1e3a34; color: #80cbc4; font-weight: bold;">
                                🎯 Сгенерировать авто-маски (SAM 2.1)
                            </button>
                            <button class="vfx-btn" id="btn-proc-corridorkey" style="background: #1b5e20; color: #a5d6a7; font-weight: bold; margin-top: 4px;" title="Уточнить грубую маску нейросетевым кеером CorridorKey с удалением зелени (Despill)">
                                🟩 Уточнить через CorridorKey (Green Screen)
                            </button>
                            <div style="margin-top: 8px; border-top: 1px solid #232738; padding-top: 8px;">
                                <div style="font-size: 11px; color: #8a94a8; margin-bottom: 5px; font-weight: 500;">📥 Загрузка пользовательских масок:</div>
                                <div class="vfx-panel-row" style="gap: 6px;">
                                    <button class="vfx-btn" id="btn-pick-mask-file" style="flex: 1; background: #233044; color: #90caf9;" title="Загрузить видео масок или одиночное изображение">
                                        🎬/🖼️ Файл маски
                                    </button>
                                    <button class="vfx-btn" id="btn-pick-mask-folder" style="flex: 1; background: #233044; color: #90caf9;" title="Загрузить папку с последовательностью масок">
                                        📂 Папка масок
                                    </button>
                                </div>
                                <input type="file" id="file-input-mask-file" accept="video/*,image/*,.mp4,.mov,.mkv,.avi,.webm,.png,.jpg,.jpeg,.webp" style="display: none;" />
                                <input type="file" id="file-input-mask-folder" webkitdirectory directory multiple style="display: none;" />
                            </div>
                            <div class="vfx-panel-row" style="margin-top: 8px;">
                                <label style="font-size: 11px; color: #ffca28; display: flex; align-items: center; gap: 4px; cursor: pointer;" title="Инвертировать маску вырезания/трекинга">
                                    <input type="checkbox" id="chk-invert-mask-panel" ${info.invert_mask ? "checked" : ""} /> 🔲 Инвертировать маску (Invert Matte)
                                </label>
                            </div>
                        </div>

                        <!-- Stage 4: Mask Thickness (Dilate / Erode) -->
                        <div class="vfx-panel-section">
                            <div class="vfx-panel-title">
                                <span>📐 4. Mask Thickness</span>
                                <span style="font-size: 10px; color: #888;">Step 4</span>
                            </div>
                            <div class="vfx-panel-row" style="align-items: center;">
                                <span style="width: 60px; min-width: 60px;">Толщина:</span>
                                <input type="range" id="slider-thickness" min="-50" max="50" step="1" value="${info.thickness !== undefined ? info.thickness : (wDilation ? wDilation.value : 20)}" style="flex: 1;" />
                                <span style="font-family: monospace; width: 45px; text-align: right;" id="lbl-thickness">+20px</span>
                            </div>
                            <button class="vfx-btn" id="btn-proc-thickness" style="background: #423520; color: #ffe082; font-weight: bold;">
                                📐 Применить толщину маски
                            </button>
                        </div>

                        <!-- Stage 5: Manual Brush Refinement & Keyframes -->
                        <div class="vfx-panel-section">
                            <div class="vfx-panel-title">
                                <span>🖌️ 5. Manual Mask & Keyframes</span>
                                <span style="font-size: 10px; color: #81c784; font-weight: bold;" id="hud-auto-badge">⚡ Auto-Save ON</span>
                            </div>
                            <div class="vfx-panel-row" style="gap: 6px;">
                                <button class="vfx-btn vfx-btn-active" id="btn-brush-add" style="flex: 1; border-color: #ff3b5c; color: #ff6b81;">+ Add (Красный)</button>
                                <button class="vfx-btn" id="btn-brush-sub" style="flex: 1; border-color: #00a2ff; color: #64b5f6;">- Erase (Синий)</button>
                                <button class="vfx-btn" id="btn-brush-undo" style="width: 65px;" title="Отменить последний мазок (Ctrl+Z)">↶ Undo</button>
                            </div>
                            <div class="vfx-panel-row" style="align-items: center;">
                                <span style="width: 60px; min-width: 60px;">Кисть:</span>
                                <input type="range" id="slider-brush-size" min="2" max="50" step="1" value="25" style="flex: 1;" />
                                <span style="font-family: monospace; width: 45px; text-align: right;" id="lbl-brush-size">25px</span>
                            </div>
                            <div class="vfx-panel-row" style="gap: 6px;">
                                <button class="vfx-btn" id="btn-proc-paint" style="flex: 2; background: #223b36; color: #80cbc4; font-weight: bold;" title="Маска сохраняется автоматически при отпускании кисти">
                                    ⚡ Авто-сохранение ключа
                                </button>
                                <button class="vfx-btn" id="btn-clear-frame" style="flex: 1; background: #262738; color: #bbb;" title="Стереть ручную маску на текущем кадре">
                                    🗑️ Стереть
                                </button>
                            </div>
                            <div class="vfx-panel-row" style="gap: 6px; margin-top: 4px;">
                                <button class="vfx-btn" id="btn-interpolate-keyframes" style="flex: 1; background: #2d3b2d; color: #81c784; font-weight: bold;" title="Интерполировать маски между ключевыми кадрами">
                                    ⚡ Интерполировать
                                </button>
                                <button class="vfx-btn" id="btn-reset-all-masks" style="flex: 1; background: #382525; color: #e57373;" title="Сбросить все правки к маскам YOLO">
                                    ↺ Сброс всех
                                </button>
                            </div>
                            <div id="hud-keyframes-list" style="font-family: monospace; font-size: 10px; color: #ffb300; margin-top: 4px; overflow-x: auto; white-space: nowrap;">
                                Ключи: []
                            </div>
                        </div>

                        <!-- Status Log Box -->
                        <div class="vfx-panel-section" style="flex: 1;">
                            <div class="vfx-panel-title">
                                <span>Console Status</span>
                            </div>
                            <div id="studio-log-box" style="font-family: monospace; font-size: 11px; color: #aaa; height: 100px; overflow-y: auto; background: #0c0d12; padding: 6px; border-radius: 4px; border: 1px solid #1f202c;">
                                Ready. Load video or adjust undistortion.
                            </div>
                        </div>
                    `;
                    body.appendChild(controlsPane);
                    modal.appendChild(body);
                    document.body.appendChild(modal);

                    // --- State & Rendering Engine ---
                    const ctx = mainCanvas.getContext("2d");
                    const paintCanvas = document.createElement("canvas");
                    paintCanvas.width = mainCanvas.width;
                    paintCanvas.height = mainCanvas.height;
                    const paintCtx = paintCanvas.getContext("2d");

                    const tintCanvas = document.createElement("canvas");
                    const tintCtx = tintCanvas.getContext("2d");

                    let isPlaying = false;
                    let showGrid = false;
                    let brushMode = "add"; // "add" or "subtract"
                    let brushSize = 25;
                    let isPainting = false;
                    const undoStack = [];
                    let keyframesList = Array.isArray(info.keyframes) ? info.keyframes : [];

                    function updateTimelineMarkers() {
                        const track = document.getElementById("studio-timeline-markers");
                        if (!track) return;
                        track.innerHTML = "";
                        if (!totalFrames || totalFrames <= 1 || !keyframesList || keyframesList.length === 0) return;

                        keyframesList.forEach(kf => {
                            const marker = document.createElement("div");
                            marker.className = "vfx-key-diamond";
                            const pct = (kf / (totalFrames - 1)) * 100;
                            marker.style.left = `${pct}%`;
                            marker.title = `Ключевой кадр ${kf} (Клик для перехода)`;
                            marker.onclick = (e) => {
                                e.stopPropagation();
                                const scrubber = document.getElementById("studio-scrubber");
                                if (scrubber) scrubber.value = kf;
                                if (typeof switchFrameSafe === "function") {
                                    switchFrameSafe(kf);
                                }
                            };
                            track.appendChild(marker);
                        });
                    }

                    function updateKeyframeHud() {
                        const countEl = document.getElementById("hud-keyframes-count");
                        const listEl = document.getElementById("hud-keyframes-list");
                        if (countEl) countEl.innerText = `Keys: ${keyframesList.length}`;
                        if (listEl) listEl.innerText = `Ключи: [ ${keyframesList.join(', ')} ]`;
                        updateTimelineMarkers();
                    }
                    updateKeyframeHud();

                    function logMsg(msg) {
                        const box = document.getElementById("studio-log-box");
                        if (box) {
                            const line = document.createElement("div");
                            line.innerText = `[${new Date().toLocaleTimeString()}] ${msg}`;
                            box.appendChild(line);
                            box.scrollTop = box.scrollHeight;
                        }
                    }

                    // --- Render Engine with Zero-Flicker Preloading & Double Buffering ---
                    let renderSeqId = 0;
                    let lastLoadedBaseImg = null;
                    let lastLoadedMaskImg = null;

                    function fetchImage(url) {
                        return new Promise((resolve) => {
                            const img = new Image();
                            img.onload = () => resolve(img);
                            img.onerror = () => resolve(null);
                            img.src = url;
                        });
                    }

                    function drawCheckerboard(targetCtx, w, h) {
                        const size = 16;
                        for (let y = 0; y < h; y += size) {
                            for (let x = 0; x < w; x += size) {
                                targetCtx.fillStyle = ((x / size + y / size) % 2 === 0) ? "#1a1a24" : "#282936";
                                targetCtx.fillRect(x, y, size, size);
                            }
                        }
                    }

                    async function renderViewport() {
                        const thisSeq = ++renderSeqId;
                        const counter = document.getElementById("studio-frame-counter");
                        if (counter) {
                            const isKey = keyframesList.includes(currentFrame);
                            counter.innerText = `[ ${String(currentFrame).padStart(3, '0')} / ${totalFrames} ]${isKey ? ' ★ KEY' : ''}`;
                            counter.style.color = isKey ? '#ffb300' : '#4f80ff';
                        }

                        const reqTime = Date.now();
                        let baseImg = null;
                        let mImg = null;

                        if (activeStage === "cutout") {
                            baseImg = await fetchImage(`/vfx/plate_prep/frame/cutout/${currentFrame}?node_id=${nodeId}&t=${reqTime}`);
                        } else if (activeStage === "mask") {
                            const [b, m] = await Promise.all([
                                fetchImage(`/vfx/plate_prep/frame/undist/${currentFrame}?node_id=${nodeId}&t=${reqTime}`),
                                fetchImage(`/vfx/plate_prep/frame/mask/${currentFrame}?node_id=${nodeId}&t=${reqTime}`)
                            ]);
                            baseImg = b;
                            mImg = m;
                        } else if (activeStage === "undist") {
                            baseImg = await fetchImage(`/vfx/plate_prep/frame/undist/${currentFrame}?node_id=${nodeId}&t=${reqTime}`);
                        } else {
                            baseImg = await fetchImage(`/vfx/plate_prep/frame/raw/${currentFrame}?node_id=${nodeId}&t=${reqTime}`);
                        }

                        // Stale request protection
                        if (thisSeq !== renderSeqId) return;

                        if (!baseImg) return;
                        lastLoadedBaseImg = baseImg;
                        lastLoadedMaskImg = mImg;

                        const w = baseImg.width || 1280;
                        const h = baseImg.height || 720;

                        if (mainCanvas.width !== w || mainCanvas.height !== h) {
                            mainCanvas.width = w;
                            mainCanvas.height = h;
                            paintCanvas.width = w;
                            paintCanvas.height = h;
                        }

                        // Offscreen double buffering
                        const off = document.createElement("canvas");
                        off.width = w;
                        off.height = h;
                        const octx = off.getContext("2d");

                        if (activeStage === "cutout") {
                            drawCheckerboard(octx, w, h);
                            octx.drawImage(baseImg, 0, 0, w, h);
                        } else if (activeStage === "mask") {
                            octx.drawImage(baseImg, 0, 0, w, h);
                            if (mImg) {
                                drawRubyMaskOnTarget(octx, mImg, w, h);
                            }
                        } else {
                            octx.drawImage(baseImg, 0, 0, w, h);
                        }

                        // Overlay active user strokes
                        octx.drawImage(paintCanvas, 0, 0);

                        // Overlay distortion grid if enabled
                        if (showGrid) {
                            drawDistortionGridOnCtx(octx, w, h);
                        }

                        // Atomic copy to screen - NO FLICKER
                        ctx.clearRect(0, 0, w, h);
                        ctx.drawImage(off, 0, 0);
                    }

                    function drawRubyMaskOnTarget(targetCtx, maskImage, w, h) {
                        if (!maskImage) return;
                        tintCanvas.width = w;
                        tintCanvas.height = h;
                        tintCtx.clearRect(0, 0, w, h);
                        tintCtx.drawImage(maskImage, 0, 0, w, h);
                        try {
                            const imgData = tintCtx.getImageData(0, 0, w, h);
                            const d = imgData.data;
                            for (let i = 0; i < d.length; i += 4) {
                                if (d[i] > 20 || d[i + 1] > 20 || d[i + 2] > 20) {
                                    d[i] = 255;
                                    d[i + 1] = 45;
                                    d[i + 2] = 80;
                                    d[i + 3] = 140;
                                } else {
                                    d[i + 3] = 0;
                                }
                            }
                            tintCtx.putImageData(imgData, 0, 0);
                            targetCtx.drawImage(tintCanvas, 0, 0);
                        } catch (e) {
                            console.warn("Mask tint fallback:", e);
                            targetCtx.save();
                            targetCtx.globalAlpha = 0.5;
                            targetCtx.drawImage(maskImage, 0, 0, w, h);
                            targetCtx.restore();
                        }
                    }

                    function redrawCurrentScreen() {
                        if (!lastLoadedBaseImg) return;
                        const w = mainCanvas.width;
                        const h = mainCanvas.height;

                        ctx.clearRect(0, 0, w, h);
                        if (activeStage === "cutout") {
                            drawCheckerboard(ctx, w, h);
                            ctx.drawImage(lastLoadedBaseImg, 0, 0, w, h);
                        } else if (activeStage === "mask") {
                            ctx.drawImage(lastLoadedBaseImg, 0, 0, w, h);
                            if (lastLoadedMaskImg) {
                                drawRubyMaskOnTarget(ctx, lastLoadedMaskImg, w, h);
                            }
                        } else {
                            ctx.drawImage(lastLoadedBaseImg, 0, 0, w, h);
                        }

                        ctx.drawImage(paintCanvas, 0, 0);
                        if (showGrid) drawDistortionGridOnCtx(ctx, w, h);
                    }

                    function drawDistortionGridOnCtx(targetCtx, w, h) {
                        if (!showGrid) return;
                        const k1 = parseFloat(sK1.value) || 0;
                        const k2 = parseFloat(sK2.value) || 0;
                        const cx = w / 2;
                        const cy = h / 2;
                        const normR = Math.sqrt(cx * cx + cy * cy);
                        const step = 45;

                        ctx.save();
                        ctx.strokeStyle = "rgba(79, 128, 255, 0.65)";
                        ctx.lineWidth = 1.2;

                        // Vertical warped lines
                        for (let x = 0; x <= w; x += step) {
                            ctx.beginPath();
                            for (let y = 0; y <= h; y += 12) {
                                const dx = x - cx;
                                const dy = y - cy;
                                const r = Math.sqrt(dx * dx + dy * dy) / normR;
                                const factor = 1 + k1 * (r * r) + k2 * (r * r * r * r);
                                const wx = cx + dx * factor;
                                const wy = cy + dy * factor;
                                if (y === 0) ctx.moveTo(wx, wy);
                                else ctx.lineTo(wx, wy);
                            }
                            ctx.stroke();
                        }

                        // Horizontal warped lines
                        for (let y = 0; y <= h; y += step) {
                            ctx.beginPath();
                            for (let x = 0; x <= w; x += 12) {
                                const dx = x - cx;
                                const dy = y - cy;
                                const r = Math.sqrt(dx * dx + dy * dy) / normR;
                                const factor = 1 + k1 * (r * r) + k2 * (r * r * r * r);
                                const wx = cx + dx * factor;
                                const wy = cy + dy * factor;
                                if (x === 0) ctx.moveTo(wx, wy);
                                else ctx.lineTo(wx, wy);
                            }
                            ctx.stroke();
                        }
                        ctx.restore();
                    }
                    renderViewport();

                    // --- Manual Brush Painting on Canvas with Auto-Save & Undo ---
                    let lastX = null;
                    let lastY = null;
                    let autoSaveTimer = null;
                    let isAutoSaving = false;

                    function pushUndo() {
                        if (undoStack.length >= 10) undoStack.shift();
                        undoStack.push(paintCtx.getImageData(0, 0, paintCanvas.width, paintCanvas.height));
                    }

                    function hasPaintStrokes() {
                        try {
                            const imgData = paintCtx.getImageData(0, 0, paintCanvas.width, paintCanvas.height);
                            const d = imgData.data;
                            for (let i = 3; i < d.length; i += 4) {
                                if (d[i] > 10) return true;
                            }
                        } catch (e) {}
                        return false;
                    }

                    async function commitManualPaint(isAuto = false) {
                        if (isAutoSaving) return;
                        if (!hasPaintStrokes()) return;

                        isAutoSaving = true;
                        const b64 = paintCanvas.toDataURL("image/png");
                        const autoBadge = document.getElementById("hud-auto-badge");
                        if (autoBadge) {
                            autoBadge.innerText = isAuto ? "💾 Сохранение..." : "Сохранение...";
                            autoBadge.style.color = "#ffb300";
                        }

                        try {
                            const data = await apiPost("manual_paint", {
                                frame_idx: currentFrame,
                                mode: brushMode,
                                mask_base64: b64
                            });
                            paintCtx.clearRect(0, 0, paintCanvas.width, paintCanvas.height);
                            undoStack.length = 0;
                            if (data.keyframes) {
                                keyframesList = data.keyframes;
                                updateKeyframeHud();
                            }
                            if (autoBadge) {
                                autoBadge.innerText = "✓ Сохранено";
                                autoBadge.style.color = "#81c784";
                                setTimeout(() => {
                                    if (autoBadge) {
                                        autoBadge.innerText = "⚡ Auto-Save ON";
                                        autoBadge.style.color = "#81c784";
                                    }
                                }, 1500);
                            }
                            logMsg(isAuto ? `⚡ Маска кадра ${currentFrame} автоматически сохранена (Ключ)!` : `Маска кадра ${currentFrame} сохранена как ключевой кадр!`);
                            renderViewport();
                            if (node.updatePreview) node.updatePreview();
                        } catch (e) {
                            logMsg(`Ошибка сохранения маски: ${e.message || e}`);
                            if (autoBadge) {
                                autoBadge.innerText = "⚠ Ошибка";
                                autoBadge.style.color = "#e57373";
                            }
                        } finally {
                            isAutoSaving = false;
                        }
                    }

                    function scheduleAutoSave() {
                        clearTimeout(autoSaveTimer);
                        autoSaveTimer = setTimeout(() => {
                            commitManualPaint(true);
                        }, 350);
                    }

                    mainCanvas.addEventListener("mousedown", (e) => {
                        clearTimeout(autoSaveTimer);
                        isPainting = true;
                        pushUndo();
                        const coords = getCanvasCoords(e);
                        lastX = coords.x;
                        lastY = coords.y;
                        drawPoint(coords.x, coords.y);
                    });

                    window.addEventListener("mouseup", () => {
                        if (isPainting) {
                            isPainting = false;
                            lastX = null;
                            lastY = null;
                            scheduleAutoSave();
                        }
                    });

                    mainCanvas.addEventListener("mousemove", (e) => {
                        if (!isPainting) return;
                        const coords = getCanvasCoords(e);
                        drawSegment(coords.x, coords.y);
                        lastX = coords.x;
                        lastY = coords.y;
                    });

                    function getCanvasCoords(e) {
                        const rect = mainCanvas.getBoundingClientRect();
                        const scaleX = mainCanvas.width / rect.width;
                        const scaleY = mainCanvas.height / rect.height;
                        return {
                            x: (e.clientX - rect.left) * scaleX,
                            y: (e.clientY - rect.top) * scaleY
                        };
                    }

                    function drawPoint(x, y) {
                        const color = brushMode === "add" ? "rgba(255, 59, 92, 0.95)" : "rgba(0, 162, 255, 0.95)";
                        paintCtx.fillStyle = color;
                        paintCtx.beginPath();
                        paintCtx.arc(x, y, brushSize, 0, Math.PI * 2);
                        paintCtx.fill();
                        ctx.drawImage(paintCanvas, 0, 0);
                    }

                    function drawSegment(x, y) {
                        const color = brushMode === "add" ? "rgba(255, 59, 92, 0.95)" : "rgba(0, 162, 255, 0.95)";
                        paintCtx.strokeStyle = color;
                        paintCtx.fillStyle = color;
                        paintCtx.lineWidth = brushSize * 2;
                        paintCtx.lineCap = "round";
                        paintCtx.lineJoin = "round";

                        paintCtx.beginPath();
                        if (lastX !== null && lastY !== null) {
                            paintCtx.moveTo(lastX, lastY);
                            paintCtx.lineTo(x, y);
                            paintCtx.stroke();
                        } else {
                            paintCtx.arc(x, y, brushSize, 0, Math.PI * 2);
                            paintCtx.fill();
                        }

                        ctx.drawImage(paintCanvas, 0, 0);
                    }

                    function doUndo() {
                        clearTimeout(autoSaveTimer);
                        if (undoStack.length > 0) {
                            const last = undoStack.pop();
                            paintCtx.putImageData(last, 0, 0);
                            redrawCurrentScreen();
                            logMsg("Отменено последнее действие кисти.");
                            scheduleAutoSave();
                        }
                    }
                    document.getElementById("btn-brush-undo").onclick = doUndo;
                    // --- Event Handlers: View Modes ---
                    function setViewStage(stage) {
                        activeStage = stage;
                        document.querySelectorAll("#btn-view-raw, #btn-view-undist, #btn-view-mask, #btn-view-cutout").forEach(b => b.classList.remove("vfx-btn-active"));
                        const activeBtn = document.getElementById(`btn-view-${stage}`);
                        if (activeBtn) activeBtn.classList.add("vfx-btn-active");
                        renderViewport();
                    }

                    document.getElementById("btn-view-raw").onclick = () => setViewStage("raw");
                    document.getElementById("btn-view-undist").onclick = () => setViewStage("undist");
                    document.getElementById("btn-view-mask").onclick = () => setViewStage("mask");
                    document.getElementById("btn-view-cutout").onclick = () => setViewStage("cutout");

                    document.getElementById("chk-grid-overlay").onchange = (e) => {
                        showGrid = e.target.checked;
                        renderViewport();
                    };

                    async function switchFrameSafe(newFrame) {
                        clearTimeout(autoSaveTimer);
                        if (hasPaintStrokes()) {
                            await commitManualPaint(true);
                        }
                        currentFrame = newFrame;
                        paintCtx.clearRect(0, 0, paintCanvas.width, paintCanvas.height);
                        undoStack.length = 0;
                        renderViewport();
                    }

                    // Scrubber
                    const scrubber = document.getElementById("studio-scrubber");
                    scrubber.oninput = (e) => {
                        switchFrameSafe(parseInt(e.target.value));
                    };

                    function jumpKeyframe(direction) {
                        if (!keyframesList || keyframesList.length === 0) {
                            logMsg("Нет сохраненных ключевых кадров.");
                            return;
                        }
                        const sortedKeys = [...keyframesList].map(Number).sort((a, b) => a - b);
                        let target = null;
                        if (direction < 0) {
                            const prevKeys = sortedKeys.filter(k => k < currentFrame);
                            target = prevKeys.length > 0 ? prevKeys[prevKeys.length - 1] : sortedKeys[sortedKeys.length - 1];
                        } else {
                            const nextKeys = sortedKeys.filter(k => k > currentFrame);
                            target = nextKeys.length > 0 ? nextKeys[0] : sortedKeys[0];
                        }

                        if (target !== null && target !== currentFrame) {
                            scrubber.value = target;
                            switchFrameSafe(target);
                            logMsg(`Переход к ключевому кадру: ${target}`);
                        } else if (target === currentFrame) {
                            logMsg(`Текущий кадр [${target}] уже является ключевым.`);
                        }
                    }

                    const btnPrevKey = document.getElementById("btn-prev-key");
                    if (btnPrevKey) btnPrevKey.onclick = () => jumpKeyframe(-1);
                    const btnNextKey = document.getElementById("btn-next-key");
                    if (btnNextKey) btnNextKey.onclick = () => jumpKeyframe(1);

                    document.getElementById("btn-prev-frame").onclick = () => {
                        const target = Math.max(0, currentFrame - 1);
                        scrubber.value = target;
                        switchFrameSafe(target);
                    };
                    document.getElementById("btn-next-frame").onclick = () => {
                        const target = Math.min(totalFrames - 1, currentFrame + 1);
                        scrubber.value = target;
                        switchFrameSafe(target);
                    };

                    const btnPlay = document.getElementById("btn-studio-play");
                    btnPlay.onclick = async () => {
                        if (!isPlaying && hasPaintStrokes()) {
                            await commitManualPaint(true);
                        }
                        isPlaying = !isPlaying;
                        btnPlay.innerText = isPlaying ? "⏸ Pause" : "▶ Play";
                    };

                    // Global hotkeys: Ctrl+Z (Undo), [ / ] or Ctrl+Left / Ctrl+Right (Jump Keyframe), Left / Right (Step Frame), Space (Play/Pause)
                    const onKeyDown = (e) => {
                        if (e.target && ((e.target.tagName === "INPUT" && e.target.type === "text") || e.target.tagName === "TEXTAREA")) {
                            return;
                        }
                        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
                            e.preventDefault();
                            doUndo();
                        } else if (e.key === "[" || (e.ctrlKey && e.key === "ArrowLeft")) {
                            e.preventDefault();
                            jumpKeyframe(-1);
                        } else if (e.key === "]" || (e.ctrlKey && e.key === "ArrowRight")) {
                            e.preventDefault();
                            jumpKeyframe(1);
                        } else if (e.key === "ArrowLeft") {
                            e.preventDefault();
                            const target = Math.max(0, currentFrame - 1);
                            scrubber.value = target;
                            switchFrameSafe(target);
                        } else if (e.key === "ArrowRight") {
                            e.preventDefault();
                            const target = Math.min(totalFrames - 1, currentFrame + 1);
                            scrubber.value = target;
                            switchFrameSafe(target);
                        } else if (e.key === " ") {
                            e.preventDefault();
                            btnPlay.click();
                        }
                    };
                    window.addEventListener("keydown", onKeyDown);

                    // Mask Inversion Control
                    async function toggleInvertMask(newVal) {
                        info.invert_mask = Boolean(newVal);
                        const c1 = document.getElementById("chk-invert-mask");
                        const c2 = document.getElementById("chk-invert-mask-panel");
                        if (c1) c1.checked = info.invert_mask;
                        if (c2) c2.checked = info.invert_mask;
                        try {
                            await apiPost("set_invert", { invert: info.invert_mask });
                            logMsg(`Инвертирование маски: ${info.invert_mask ? "ВКЛЮЧЕНО" : "ВЫКЛЮЧЕНО"}`);
                            renderViewport();
                            if (node.updatePreview) node.updatePreview();
                        } catch (e) {
                            logMsg(`Ошибка смены инверсии: ${e.message || e}`);
                        }
                    }

                    const chkInvert = document.getElementById("chk-invert-mask");
                    if (chkInvert) {
                        chkInvert.onchange = (e) => toggleInvertMask(e.target.checked);
                    }
                    const chkInvertPanel = document.getElementById("chk-invert-mask-panel");
                    if (chkInvertPanel) {
                        chkInvertPanel.onchange = (e) => toggleInvertMask(e.target.checked);
                    }

                    // --- Process 1: Load Footage & File Uploads ---
                    const inputVideo = document.getElementById("file-input-video");
                    const inputFolder = document.getElementById("file-input-folder");

                    document.getElementById("btn-pick-video").onclick = () => inputVideo.click();
                    document.getElementById("btn-pick-folder").onclick = () => inputFolder.click();

                    inputVideo.onchange = async (e) => {
                        const file = e.target.files[0];
                        if (!file) return;
                        logMsg(`Загрузка видео: ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)...`);
                        const fd = new FormData();
                        fd.append("node_id", nodeId);
                        fd.append("file", file, file.name);
                        try {
                            const res = await fetch(`/vfx/plate_prep/upload?node_id=${nodeId}`, { method: "POST", body: fd });
                            if (!res.ok) {
                                const errTxt = await res.text();
                                return logMsg(`Ошибка загрузки (${res.status}): ${errTxt}`);
                            }
                            const data = await res.json();
                            if (data.error) return logMsg(`Ошибка загрузки: ${data.error}`);
                            totalFrames = data.total_frames;
                            currentFrame = 0;
                            scrubber.max = Math.max(0, totalFrames - 1);
                            scrubber.value = 0;
                            document.getElementById("hud-res-badge").innerText = `${data.width}x${data.height} • ${totalFrames} кадров`;
                            if (wInputPath) wInputPath.value = data.input_path;
                            document.getElementById("input-footage-path").value = data.input_path;
                            logMsg(`Успешно загружено видео: ${totalFrames} кадров.`);
                            setViewStage("undist");
                            if (node.updatePreview) node.updatePreview();
                        } catch (err) {
                            logMsg(`Сбой загрузки: ${err}`);
                        }
                    };

                    inputFolder.onchange = async (e) => {
                        const files = Array.from(e.target.files).filter(f => f.name.match(/\.(png|jpe?g|webp|tiff?)$/i));
                        if (!files.length) return alert("В выбранной папке не найдено изображений!");
                        logMsg(`Загрузка секвенции из ${files.length} изображений...`);
                        const fd = new FormData();
                        fd.append("node_id", nodeId);
                        for (const f of files) {
                            fd.append("files", f, f.name);
                        }
                        try {
                            const res = await fetch(`/vfx/plate_prep/upload?node_id=${nodeId}`, { method: "POST", body: fd });
                            if (!res.ok) {
                                const errTxt = await res.text();
                                return logMsg(`Ошибка загрузки (${res.status}): ${errTxt}`);
                            }
                            const data = await res.json();
                            if (data.error) return logMsg(`Ошибка загрузки: ${data.error}`);
                            totalFrames = data.total_frames;
                            currentFrame = 0;
                            scrubber.max = Math.max(0, totalFrames - 1);
                            scrubber.value = 0;
                            document.getElementById("hud-res-badge").innerText = `${data.width}x${data.height} • ${totalFrames} кадров`;
                            if (wInputPath) wInputPath.value = data.input_path;
                            document.getElementById("input-footage-path").value = data.input_path;
                            logMsg(`Успешно загружена секвенция: ${totalFrames} кадров.`);
                            setViewStage("undist");
                            if (node.updatePreview) node.updatePreview();
                        } catch (err) {
                            logMsg(`Сбой загрузки: ${err}`);
                        }
                    };

                    document.getElementById("btn-proc-load").onclick = async () => {
                        const path = document.getElementById("input-footage-path").value.trim();
                        if (!path) return alert("Введите путь к видео или папке кадров!");
                        logMsg(`Loading footage from: ${path}...`);
                        try {
                            const data = await apiPost("load", { input_path: path });
                            if (data.error) return logMsg(`Error: ${data.error}`);
                            totalFrames = data.total_frames;
                            currentFrame = 0;
                            scrubber.max = Math.max(0, totalFrames - 1);
                            scrubber.value = 0;
                            document.getElementById("hud-res-badge").innerText = `${data.width}x${data.height} • ${totalFrames} кадров`;
                            logMsg(`Loaded ${totalFrames} frames (${data.width}x${data.height}).`);
                            setViewStage("undist");
                            if (node.updatePreview) node.updatePreview();
                        } catch (e) {
                            logMsg(`Load failed: ${e.message || e}`);
                        }
                    };

                    // --- Process 2: Undistort & Grid ---
                    const sK1 = document.getElementById("slider-k1");
                    const sK2 = document.getElementById("slider-k2");
                    sK1.value = info.k1 !== undefined ? info.k1 : (wK1 ? wK1.value : 0.0);
                    sK2.value = info.k2 !== undefined ? info.k2 : (wK2 ? wK2.value : 0.0);
                    document.getElementById("lbl-k1").innerText = parseFloat(sK1.value).toFixed(2);
                    document.getElementById("lbl-k2").innerText = parseFloat(sK2.value).toFixed(2);

                    sK1.oninput = () => {
                        document.getElementById("lbl-k1").innerText = parseFloat(sK1.value).toFixed(2);
                        if (showGrid) renderViewport();
                    };
                    sK2.oninput = () => {
                        document.getElementById("lbl-k2").innerText = parseFloat(sK2.value).toFixed(2);
                        if (showGrid) renderViewport();
                    };

                    async function applyUndistortionAction(k1, k2) {
                        logMsg(`Applying lens undistortion: k1=${k1.toFixed(3)}, k2=${k2.toFixed(3)}...`);
                        try {
                            const data = await apiPost("undistort", { k1: k1, k2: k2 });
                            if (data.error) return logMsg(`Error: ${data.error}`);
                            logMsg("Дисторсия успешно рассчитана и применена!");
                            setViewStage("undist");
                            if (node.updatePreview) node.updatePreview();
                        } catch (e) {
                            logMsg(`Undistort failed: ${e.message || e}`);
                        }
                    }

                    document.getElementById("btn-proc-undistort").onclick = () => {
                        const k1 = parseFloat(sK1.value);
                        const k2 = parseFloat(sK2.value);
                        applyUndistortionAction(k1, k2);
                    };

                    document.getElementById("btn-reset-distortion").onclick = () => {
                        sK1.value = 0.0;
                        sK2.value = 0.0;
                        document.getElementById("lbl-k1").innerText = "0.00";
                        document.getElementById("lbl-k2").innerText = "0.00";
                        applyUndistortionAction(0.0, 0.0);
                    };

                    document.getElementById("btn-auto-calibrate").onclick = async () => {
                        logMsg("Автоматическое определение кривизны оптики...");
                        try {
                            const data = await apiPost("auto_calibrate", {});
                            if (data.error) return logMsg(`Error: ${data.error}`);
                            sK1.value = data.k1;
                            sK2.value = data.k2;
                            document.getElementById("lbl-k1").innerText = parseFloat(data.k1).toFixed(2);
                            document.getElementById("lbl-k2").innerText = parseFloat(data.k2).toFixed(2);
                            logMsg(`Авто-определено: k1=${data.k1}, k2=${data.k2}`);
                            applyUndistortionAction(data.k1, data.k2);
                        } catch (err) {
                            logMsg(`Авто-калибровка не удалась: ${err.message || err}`);
                        }
                    };

                    // --- Process 3: Auto-Mask (SAM 2.1 / YOLOv8 Dual-Engine) ---
                    const selectMaskEngine = document.getElementById("select-mask-engine");
                    const btnProcAutoMask = document.getElementById("btn-proc-automask");

                    function updateMaskEngineUi() {
                        const engine = selectMaskEngine ? selectMaskEngine.value : "sam2";
                        if (btnProcAutoMask) {
                            if (engine === "sam2") {
                                btnProcAutoMask.innerText = "🎯 Сгенерировать авто-маски (SAM 2.1)";
                                btnProcAutoMask.style.background = "#1e3a34";
                                btnProcAutoMask.style.color = "#80cbc4";
                            } else {
                                btnProcAutoMask.innerText = "⚡ Сгенерировать авто-маски (YOLOv8)";
                                btnProcAutoMask.style.background = "#2b3a2b";
                                btnProcAutoMask.style.color = "#a5d6a7";
                            }
                        }
                    }
                    if (selectMaskEngine) {
                        selectMaskEngine.onchange = updateMaskEngineUi;
                        updateMaskEngineUi();
                    }

                    if (btnProcAutoMask) {
                        btnProcAutoMask.onclick = async () => {
                            const engine = selectMaskEngine ? selectMaskEngine.value : "sam2";
                            const engineLabel = engine === "sam2" ? "SAM 2.1" : "YOLOv8";
                            const classes = document.getElementById("input-mask-classes").value;
                            const props = document.getElementById("chk-detect-props").checked;
                            logMsg(`Запуск авто-маскирования [${engineLabel}] для классов: ${classes}...`);
                            try {
                                const data = await apiPost("auto_mask", {
                                    mask_classes: classes,
                                    detect_props: props,
                                    engine: engine
                                });
                                if (data.error) return logMsg(`Error: ${data.error}`);
                                logMsg(`Авто-маски [${engineLabel}] успешно сгенерированы!`);
                                setViewStage("mask");
                                if (node.updatePreview) node.updatePreview();
                            } catch (e) {
                                logMsg(`Ошибка авто-маскирования: ${e.message || e}`);
                            }
                        };
                    }

                    const btnProcCorridorKey = document.getElementById("btn-proc-corridorkey");
                    if (btnProcCorridorKey) {
                        btnProcCorridorKey.onclick = async () => {
                            if (totalFrames <= 0) return alert("Сначала загрузите видео/секвенцию!");
                            logMsg("🟩 Запуск CorridorKey (GreenFormer)... Нейросетевое извлечение альфы и удаление зелени...");
                            try {
                                const data = await apiPost("corridor_key", {
                                    despill_strength: 1.0,
                                    refiner_strength: 1.0,
                                    auto_despeckle: "On",
                                    despeckle_size: 400
                                });
                                if (data.error) return logMsg(`Ошибка CorridorKey: ${data.error}`);
                                logMsg("🟩 CorridorKey успешно завершен! Высокодетальная альфа (волосы/motion blur) и чистый cutout готовы.");
                                setViewStage("cutout");
                                if (node.updatePreview) node.updatePreview();
                            } catch (e) {
                                logMsg(`Ошибка CorridorKey: ${e.message || e}`);
                            }
                        };
                    }

                    // --- User Mask Upload ---
                    const inputMaskFile = document.getElementById("file-input-mask-file");
                    const inputMaskFolder = document.getElementById("file-input-mask-folder");

                    document.getElementById("btn-pick-mask-file").onclick = () => {
                        if (totalFrames <= 0) return alert("Сначала загрузите шот/видео (Шаг 1), перед загрузкой масок!");
                        inputMaskFile.value = "";
                        inputMaskFile.click();
                    };

                    document.getElementById("btn-pick-mask-folder").onclick = () => {
                        if (totalFrames <= 0) return alert("Сначала загрузите шот/видео (Шаг 1), перед загрузкой масок!");
                        inputMaskFolder.value = "";
                        inputMaskFolder.click();
                    };

                    inputMaskFile.onchange = async (e) => {
                        const file = e.target.files[0];
                        if (!file) return;
                        const isVideo = file.type.startsWith("video/") || file.name.match(/\.(mp4|mov|mkv|avi|webm)$/i);
                        const isImage = file.type.startsWith("image/") || file.name.match(/\.(png|jpe?g|webp|tiff?)$/i);

                        if (!isVideo && !isImage) {
                            return alert("Пожалуйста, выберите видео (.mp4, .mov) или изображение (.png, .jpg) маски.");
                        }

                        const fd = new FormData();
                        fd.append("node_id", nodeId);
                        fd.append("file", file, file.name);

                        if (isVideo) {
                            logMsg(`Загрузка видео масок: ${file.name}...`);
                        } else {
                            const applyAll = confirm(
                                `Применить изображение маски ко всем кадрам шота?\n\n` +
                                `[OK] — Применить ко ВСЕМ кадрам (0..${totalFrames - 1})\n` +
                                `[Отмена] — Сохранить как ключевой кадр ТОЛЬКО для текущего кадра [${currentFrame}]`
                            );
                            fd.append("apply_all", applyAll ? "true" : "false");
                            fd.append("frame_idx", String(currentFrame));
                            logMsg(`Импорт маски: ${file.name} (${applyAll ? "все кадры" : "кадр " + currentFrame})...`);
                        }

                        try {
                            const res = await fetch(`/vfx/plate_prep/upload_mask?node_id=${nodeId}`, { method: "POST", body: fd });
                            if (!res.ok) {
                                const errTxt = await res.text();
                                return logMsg(`Ошибка загрузки маски (${res.status}): ${errTxt}`);
                            }
                            const data = await res.json();
                            if (data.error) return logMsg(`Ошибка: ${data.error}`);
                            logMsg(`Пользовательские маски успешно импортированы!`);
                            if (data.keyframes) {
                                keyframesList = data.keyframes;
                                updateKeyframeHud();
                            }
                            setViewStage("mask");
                            renderViewport();
                            if (node.updatePreview) node.updatePreview();
                        } catch (err) {
                            logMsg(`Сбой загрузки маски: ${err.message || err}`);
                        }
                    };

                    inputMaskFolder.onchange = async (e) => {
                        const files = Array.from(e.target.files).filter(f => f.name.match(/\.(png|jpe?g|webp|tiff?)$/i));
                        if (!files.length) return alert("В выбранной папке не найдено изображений масок!");
                        logMsg(`Загрузка папки масок из ${files.length} изображений...`);
                        const fd = new FormData();
                        fd.append("node_id", nodeId);
                        for (const f of files) {
                            fd.append("files", f, f.name);
                        }
                        try {
                            const res = await fetch(`/vfx/plate_prep/upload_mask?node_id=${nodeId}`, { method: "POST", body: fd });
                            if (!res.ok) {
                                const errTxt = await res.text();
                                return logMsg(`Ошибка загрузки (${res.status}): ${errTxt}`);
                            }
                            const data = await res.json();
                            if (data.error) return logMsg(`Ошибка загрузки: ${data.error}`);
                            logMsg(`Успешно импортирована секвенция масок (${data.frames_count} кадров)!`);
                            setViewStage("mask");
                            renderViewport();
                            if (node.updatePreview) node.updatePreview();
                        } catch (err) {
                            logMsg(`Сбой загрузки: ${err.message || err}`);
                        }
                    };

                    // --- Process 4: Mask Thickness ---
                    const sThick = document.getElementById("slider-thickness");
                    sThick.value = info.thickness !== undefined ? info.thickness : (wDilation ? wDilation.value : 20);
                    const initThick = parseInt(sThick.value);
                    document.getElementById("lbl-thickness").innerText = `${initThick >= 0 ? '+' : ''}${initThick}px`;

                    sThick.oninput = () => {
                        const val = parseInt(sThick.value);
                        document.getElementById("lbl-thickness").innerText = `${val >= 0 ? '+' : ''}${val}px`;
                    };

                    document.getElementById("btn-proc-thickness").onclick = async () => {
                        const thick = parseInt(sThick.value);
                        logMsg(`Adjusting smooth mask thickness: ${thick}px (ellipse/anti-alias)...`);
                        try {
                            const data = await apiPost("thickness", { thickness: thick });
                            if (data.error) return logMsg(`Error: ${data.error}`);
                            logMsg("Сглаженная толщина маски применена!");
                            setViewStage("mask");
                            if (node.updatePreview) node.updatePreview();
                        } catch (e) {
                            logMsg(`Thickness failed: ${e.message || e}`);
                        }
                    };

                    // --- Process 5: Manual Brush Tools & Keyframes ---
                    const btnBrushAdd = document.getElementById("btn-brush-add");
                    const btnBrushSub = document.getElementById("btn-brush-sub");
                    btnBrushAdd.onclick = () => {
                        brushMode = "add";
                        btnBrushAdd.classList.add("vfx-btn-active");
                        btnBrushSub.classList.remove("vfx-btn-active");
                    };
                    btnBrushSub.onclick = () => {
                        brushMode = "subtract";
                        btnBrushSub.classList.add("vfx-btn-active");
                        btnBrushAdd.classList.remove("vfx-btn-active");
                    };

                    const sBrushSize = document.getElementById("slider-brush-size");
                    sBrushSize.oninput = () => {
                        brushSize = parseInt(sBrushSize.value);
                        document.getElementById("lbl-brush-size").innerText = `${brushSize}px`;
                    };

                    document.getElementById("btn-proc-paint").onclick = () => {
                        clearTimeout(autoSaveTimer);
                        commitManualPaint(false);
                    };

                    document.getElementById("btn-clear-frame").onclick = async () => {
                        logMsg(`Стирание ручной маски на кадре ${currentFrame}...`);
                        try {
                            const data = await apiPost("clear_frame", { frame_idx: currentFrame });
                            paintCtx.clearRect(0, 0, paintCanvas.width, paintCanvas.height);
                            undoStack.length = 0;
                            if (data.keyframes) {
                                keyframesList = data.keyframes;
                                updateKeyframeHud();
                            }
                            logMsg(`Маска кадра ${currentFrame} сброшена к исходной.`);
                            renderViewport();
                            if (node.updatePreview) node.updatePreview();
                        } catch (err) {
                            logMsg(`Ошибка очистки кадра: ${err.message || err}`);
                        }
                    };

                    document.getElementById("btn-interpolate-keyframes").onclick = async () => {
                        logMsg("Интерполяция масок между всеми ключевыми кадрами...");
                        try {
                            const data = await apiPost("interpolate_keyframes", {});
                            if (data.status === "need_at_least_2_keyframes") {
                                return alert("Для интерполяции нужно нарисовать маски минимум на двух ключевых кадрах!");
                            }
                            logMsg(`Успешно интерполировано ${data.interpolated_frames} промежуточных кадров!`);
                            renderViewport();
                            if (node.updatePreview) node.updatePreview();
                        } catch (err) {
                            logMsg(`Сбой интерполяции: ${err.message || err}`);
                        }
                    };

                    document.getElementById("btn-reset-all-masks").onclick = async () => {
                        if (!confirm("Сбросить все ручные правки на всех кадрах к авто-маскам YOLO?")) return;
                        logMsg("Сброс всех ручных правок...");
                        try {
                            await apiPost("reset_masks", {});
                            paintCtx.clearRect(0, 0, paintCanvas.width, paintCanvas.height);
                            undoStack.length = 0;
                            keyframesList = [];
                            updateKeyframeHud();
                            logMsg("Все маски сброшены к исходным.");
                            renderViewport();
                            if (node.updatePreview) node.updatePreview();
                        } catch (err) {
                            logMsg(`Сбой сброса масок: ${err.message || err}`);
                        }
                    };

                    // --- Save & Sync to Node ---
                    document.getElementById("btn-studio-save").onclick = () => {
                        if (wInputPath) wInputPath.value = document.getElementById("input-footage-path").value;
                        if (wK1) wK1.value = parseFloat(sK1.value);
                        if (wK2) wK2.value = parseFloat(sK2.value);
                        if (wClasses) wClasses.value = document.getElementById("input-mask-classes").value;
                        if (wDilation) wDilation.value = Math.max(0, parseInt(sThick.value));
                        if (node.updatePreview) node.updatePreview();
                        closeModal();
                    };

                    // Close Modal
                    const closeModal = async () => {
                        window.removeEventListener("keydown", onKeyDown);
                        isPlaying = false;
                        cancelAnimationFrame(studioAnimId);
                        clearTimeout(autoSaveTimer);
                        if (hasPaintStrokes()) {
                            await commitManualPaint(true);
                        }
                        modal.remove();
                        if (node.updatePreview) node.updatePreview();
                    };
                    document.getElementById("btn-studio-close").onclick = closeModal;

                    // Playback Loop
                    let lastPlayTime = performance.now();
                    let studioAnimId;
                    function studioPlayLoop(now) {
                        studioAnimId = requestAnimationFrame(studioPlayLoop);
                        if (isPlaying && totalFrames > 1 && (now - lastPlayTime) > (1000 / (info.fps || 25))) {
                            lastPlayTime = now;
                            currentFrame = (currentFrame + 1) % totalFrames;
                            scrubber.value = currentFrame;
                            renderViewport();
                        }
                    }
                    studioAnimId = requestAnimationFrame(studioPlayLoop);
}

function drawNodePreview(node, ctx) {
    const info = node._vfx_plate_info;
    if (!info || !info.total_frames || info.total_frames <= 0) return;

    const pad = 12;
    const w = node.size[0];
    const h = node.size[1];

    let startY = 65;
    if (node.widgets && node.widgets.length > 0) {
        const lastW = node.widgets[node.widgets.length - 1];
        if (lastW && lastW.last_y !== undefined) {
            startY = lastW.last_y + (lastW.computeSize ? lastW.computeSize(w)[1] : 24) + 8;
        }
    }

    const cardX = pad;
    const cardY = startY;
    const cardW = w - pad * 2;
    const cardH = h - cardY - pad;

    if (cardW < 50 || cardH < 50) return;

    const roundRect = (c, x, y, rw, rh, r) => {
        c.beginPath();
        c.moveTo(x + r, y);
        c.lineTo(x + rw - r, y);
        c.quadraticCurveTo(x + rw, y, x + rw, y + r);
        c.lineTo(x + rw, y + rh - r);
        c.quadraticCurveTo(x + rw, y + rh, x + rw - r, y + rh);
        c.lineTo(x + r, y + rh);
        c.quadraticCurveTo(x, y + rh, x, y + rh - r);
        c.lineTo(x, y + r);
        c.quadraticCurveTo(x, y, x + r, y);
        c.closePath();
    };

    ctx.save();

    // 1. Outer Card Background
    roundRect(ctx, cardX, cardY, cardW, cardH, 8);
    ctx.fillStyle = "#0e1017";
    ctx.fill();
    ctx.strokeStyle = "#25293d";
    ctx.lineWidth = 1.2;
    ctx.stroke();

    // 2. Pill Tabs Bar (Top of Card)
    const activeStage = node._vfx_preview_stage || (info.has_undist ? "undist" : "raw");
    const pills = [
        { id: "undist", label: "Undist" },
        { id: "cutout", label: "Cutout" },
        { id: "mask", label: "Mask" },
        { id: "raw", label: "Raw" }
    ];

    const pillH = 18;
    const pillY = cardY + 7;
    let pillX = cardX + 7;

    node._vfx_pill_bounds = [];

    for (const p of pills) {
        ctx.font = "bold 9px sans-serif";
        const textW = ctx.measureText(p.label).width;
        const pw = textW + 14;
        const isActive = activeStage === p.id;

        roundRect(ctx, pillX, pillY, pw, pillH, 4);
        if (isActive) {
            ctx.fillStyle = p.id === "cutout" ? "#382245" : (p.id === "mask" ? "#421f2d" : "#1a2c47");
            ctx.fill();
            ctx.strokeStyle = p.id === "cutout" ? "#ce93d8" : (p.id === "mask" ? "#f48fb1" : "#64b5f6");
            ctx.lineWidth = 1;
            ctx.stroke();
            ctx.fillStyle = "#ffffff";
        } else {
            ctx.fillStyle = "#161822";
            ctx.fill();
            ctx.strokeStyle = "#272a3b";
            ctx.lineWidth = 1;
            ctx.stroke();
            ctx.fillStyle = "#7c859e";
        }

        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(p.label, pillX + pw / 2, pillY + pillH / 2);

        node._vfx_pill_bounds.push({ id: p.id, x: pillX, y: pillY, w: pw, h: pillH });
        pillX += pw + 4;
    }

    // Right HUD: Res & Frame Count
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.font = "10px monospace";
    ctx.fillStyle = "#637499";
    ctx.fillText(`${info.width}x${info.height} • ${info.total_frames}f`, cardX + cardW - 8, pillY + pillH / 2);

    // 3. Image Viewport Area
    const imgX = cardX + 6;
    const imgY = pillY + pillH + 6;
    const imgW = cardW - 12;
    const imgH = cardH - (imgY - cardY) - 24;

    node._vfx_viewport_bounds = { x: imgX, y: imgY, w: imgW, h: imgH };

    if (imgW > 20 && imgH > 20) {
        ctx.save();
        roundRect(ctx, imgX, imgY, imgW, imgH, 5);
        ctx.clip();

        // Background or Checkerboard for cutout
        if (activeStage === "cutout") {
            const sq = 12;
            for (let cy = imgY; cy < imgY + imgH; cy += sq) {
                for (let cx = imgX; cx < imgX + imgW; cx += sq) {
                    ctx.fillStyle = (((cx - imgX)/sq + (cy - imgY)/sq) % 2 === 0) ? "#141620" : "#202434";
                    ctx.fillRect(cx, cy, sq, sq);
                }
            }
        } else {
            ctx.fillStyle = "#07080c";
            ctx.fillRect(imgX, imgY, imgW, imgH);
        }

        // Draw preview image
        const img = node._vfx_preview_img;
        if (img && img.complete && img.naturalWidth > 0) {
            const scale = Math.min(imgW / img.naturalWidth, imgH / img.naturalHeight);
            const dw = img.naturalWidth * scale;
            const dh = img.naturalHeight * scale;
            const dx = imgX + (imgW - dw) / 2;
            const dy = imgY + (imgH - dh) / 2;
            ctx.drawImage(img, dx, dy, dw, dh);
        } else {
            ctx.fillStyle = "#4a5168";
            ctx.font = "11px sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText("📷 Загрузка кадра...", imgX + imgW / 2, imgY + imgH / 2);
        }

        // Subtle gradient vignette at bottom of image for contrast
        const grad = ctx.createLinearGradient(0, imgY + imgH - 26, 0, imgY + imgH);
        grad.addColorStop(0, "rgba(0, 0, 0, 0)");
        grad.addColorStop(1, "rgba(0, 0, 0, 0.7)");
        ctx.fillStyle = grad;
        ctx.fillRect(imgX, imgY + imgH - 26, imgW, 26);

        // Hover effect overlay
        if (node._vfx_viewport_hover) {
            ctx.fillStyle = "rgba(79, 128, 255, 0.15)";
            ctx.fillRect(imgX, imgY, imgW, imgH);
            ctx.fillStyle = "#ffffff";
            ctx.font = "bold 11px sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText("🔍 Открыть Studio", imgX + imgW / 2, imgY + imgH / 2);
        }

        ctx.restore();

        // Border around image (glows on hover)
        ctx.strokeStyle = node._vfx_viewport_hover ? "#4f80ff" : "#242838";
        ctx.lineWidth = node._vfx_viewport_hover ? 1.5 : 1;
        roundRect(ctx, imgX, imgY, imgW, imgH, 5);
        ctx.stroke();
    }

    // 4. Bottom Footer Strip
    const footerY = cardY + cardH - 12;
    ctx.textBaseline = "middle";

    // Left: file name or status
    ctx.textAlign = "left";
    ctx.font = "10px sans-serif";
    ctx.fillStyle = "#8a94a8";
    let srcText = info.input_path ? info.input_path.split(/[\\/]/).pop() : "Shot loaded";
    if (srcText.length > 25) srcText = srcText.substring(0, 23) + "...";
    ctx.fillText(`🎬 ${srcText}`, cardX + 8, footerY);

    // Keyframe counter badge if any
    const keysCount = Array.isArray(info.keyframes) ? info.keyframes.length : 0;
    if (keysCount > 0) {
        ctx.fillStyle = "#ffb300";
        ctx.font = "bold 10px monospace";
        ctx.fillText(`★ ${keysCount} keys`, cardX + 155, footerY);
    }

    // Right: Clickable studio hint
    ctx.textAlign = "right";
    ctx.font = "10px sans-serif";
    ctx.fillStyle = node._vfx_viewport_hover ? "#6ba0ff" : "#4f80ff";
    ctx.fillText("Клик для Studio ↗", cardX + cardW - 8, footerY);

    ctx.restore();
}

function setupPlatePrepNode(node) {
    if (!node) return;

    // 1. Remove all input slots (images, manual_mask) - all ingestion is internal
    if (node.inputs && node.inputs.length > 0) {
        for (let i = node.inputs.length - 1; i >= 0; i--) {
            node.removeInput(i);
        }
    }

    // 2. Clean up any legacy parameter widgets, leaving only the Studio button
    if (node.widgets && node.widgets.length > 0) {
        node.widgets = node.widgets.filter(w => w.name === "🎞️ Open Plate Prep Studio" || w.name === "open_plate_prep");
    }

    // Ensure button exists
    const hasBtn = node.widgets && node.widgets.some(w => w.name === "🎞️ Open Plate Prep Studio" || w.name === "open_plate_prep");
    if (!hasBtn) {
        node.addWidget("button", "🎞️ Open Plate Prep Studio", null, () => {
            openStudioModal(node);
        });
    }

    if (node._vfx_plate_prep_initialized) {
        if (node.updatePreview) node.updatePreview();
        return;
    }
    node._vfx_plate_prep_initialized = true;

    // Preview state
    node._vfx_preview_stage = "undist";
    node._vfx_preview_img = null;
    node._vfx_plate_info = null;
    node._vfx_viewport_hover = false;

    node.switchPreviewStage = function(stage) {
        this._vfx_preview_stage = stage;
        const nodeId = String(this.id || "default");
        const t = Date.now();
        const img = new Image();
        img.onload = () => {
            this._vfx_preview_img = img;
            if (app.graph) app.graph.setDirtyCanvas(true, true);
        };
        img.onerror = () => {
            if (stage !== "undist") {
                this.switchPreviewStage("undist");
            }
        };
        img.src = `/vfx/plate_prep/frame/${stage}/0?node_id=${nodeId}&t=${t}`;
    };

    node.updatePreview = async function() {
        const nodeId = String(this.id || "default");
        try {
            const res = await fetch(`/vfx/plate_prep/info?node_id=${nodeId}`);
            if (!res.ok) return;
            const info = await res.json();
            this._vfx_plate_info = info;

            if (info.total_frames && info.total_frames > 0) {
                // Expand node vertically for preview card
                if (this.size[1] < 310) {
                    this.setSize([Math.max(360, this.size[0]), 330]);
                }
                const stage = this._vfx_preview_stage || (info.has_undist ? "undist" : "raw");
                const t = Date.now();
                const img = new Image();
                img.onload = () => {
                    this._vfx_preview_img = img;
                    if (app.graph) app.graph.setDirtyCanvas(true, true);
                };
                img.src = `/vfx/plate_prep/frame/${stage}/0?node_id=${nodeId}&t=${t}`;
            } else {
                this._vfx_preview_img = null;
                if (this.size[1] > 100) {
                    this.setSize([Math.max(360, this.size[0]), 85]);
                }
                if (app.graph) app.graph.setDirtyCanvas(true, true);
            }
        } catch (e) {
            console.warn("[VFX Suite] Failed to update node preview:", e);
        }
    };

    // Override onDrawForeground to render preview
    const origOnDrawForeground = node.onDrawForeground;
    node.onDrawForeground = function(ctx, graphCanvas) {
        if (origOnDrawForeground) {
            origOnDrawForeground.apply(this, arguments);
        }
        if (this.flags && this.flags.collapsed) return;
        drawNodePreview(this, ctx);
    };

    // Handle mouse click on preview pills or viewport
    const origOnMouseDown = node.onMouseDown;
    node.onMouseDown = function(e, local_pos, graph_canvas) {
        if (this._vfx_handle_preview_click && this._vfx_handle_preview_click(local_pos)) {
            return true;
        }
        if (origOnMouseDown) {
            return origOnMouseDown.apply(this, arguments);
        }
    };

    // Hover effect on mouse move
    const origOnMouseMove = node.onMouseMove;
    node.onMouseMove = function(e, local_pos, graph_canvas) {
        if (this._vfx_plate_info && this._vfx_plate_info.total_frames > 0) {
            const vp = this._vfx_viewport_bounds;
            const isHover = vp && local_pos[0] >= vp.x && local_pos[0] <= vp.x + vp.w &&
                                   local_pos[1] >= vp.y && local_pos[1] <= vp.y + vp.h;
            if (this._vfx_viewport_hover !== isHover) {
                this._vfx_viewport_hover = isHover;
                if (app.graph) app.graph.setDirtyCanvas(true);
            }
        }
        if (origOnMouseMove) {
            return origOnMouseMove.apply(this, arguments);
        }
    };

    node._vfx_handle_preview_click = function(localPos) {
        if (!this._vfx_plate_info || !this._vfx_plate_info.total_frames) return false;
        const lx = localPos[0];
        const ly = localPos[1];

        // 1. Check pills
        if (this._vfx_pill_bounds) {
            for (const p of this._vfx_pill_bounds) {
                if (lx >= p.x && lx <= p.x + p.w && ly >= p.y && ly <= p.y + p.h) {
                    this.switchPreviewStage(p.id);
                    return true;
                }
            }
        }

        // 2. Check viewport
        const vp = this._vfx_viewport_bounds;
        if (vp && lx >= vp.x && lx <= vp.x + vp.w && ly >= vp.y && ly <= vp.y + vp.h + 24) {
            openStudioModal(this);
            return true;
        }

        return false;
    };

    const origComputeSize = node.computeSize;
    node.computeSize = function(out) {
        let size = origComputeSize ? origComputeSize.apply(this, arguments) : [360, 85];
        if (this._vfx_plate_info && this._vfx_plate_info.total_frames > 0) {
            size[0] = Math.max(size[0], 360);
            size[1] = Math.max(size[1], 330);
        } else {
            size[0] = Math.max(size[0], 360);
            size[1] = 85;
        }
        if (out) {
            out[0] = size[0];
            out[1] = size[1];
        }
        return size;
    };

    // Initial setup and preview fetch
    node.setSize([Math.max(360, node.size[0]), (node._vfx_plate_info && node._vfx_plate_info.total_frames > 0) ? 330 : 85]);
    node.updatePreview();
}

app.registerExtension({
    name: "VFXSuite.PlatePrep",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "VFXPlatePrep") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
                setupPlatePrepNode(this);
                return r;
            };

            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
                setupPlatePrepNode(this);
                return r;
            };
        }
    },
    async nodeCreated(node) {
        if (node.comfyClass === "VFXPlatePrep" || node.type === "VFXPlatePrep") {
            setupPlatePrepNode(node);
        }
    },
    async loadedGraphNode(node) {
        if (node.comfyClass === "VFXPlatePrep" || node.type === "VFXPlatePrep") {
            setupPlatePrepNode(node);
        }
    },
    async setup(app) {
        if (app.graph && app.graph._nodes) {
            for (const node of app.graph._nodes) {
                if (node.comfyClass === "VFXPlatePrep" || node.type === "VFXPlatePrep") {
                    setupPlatePrepNode(node);
                }
            }
        }
    }
});
