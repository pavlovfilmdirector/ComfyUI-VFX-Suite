import { app } from "../../../scripts/app.js";

/**
 * Camera Tracker SfM Frontend Integration
 * ========================================
 * - Sanitizes legacy widget values (prevents NaN from outdated workflows).
 * - Dynamically labels focal_length_mm as [Fixed Lens] vs [Auto Seed].
 * - Ensures intuitive mask mode presentation.
 */

function setupCameraTrackerNode(node) {
    if (!node || !node.widgets) return;

    const wDensity = node.widgets.find(w => w.name === "point_cloud_density");
    const wFocalMode = node.widgets.find(w => w.name === "focal_length_mode");
    const wFocalMm = node.widgets.find(w => w.name === "focal_length_mm" || w.name === "focal_mm");
    const wMaskMode = node.widgets.find(w => w.name === "mask_mode");

    // 0. Sanitize point cloud density
    if (wDensity) {
        const validDensities = ["Draft (2048 pts)", "Standard (4096 pts)", "Dense (8192 pts)", "Ultra (16384 pts)"];
        if (!validDensities.includes(wDensity.value)) {
            wDensity.value = "Draft (2048 pts)";
        }
    }

    // 0.5. Sanitize focal length mode
    if (wFocalMode) {
        const validFocalModes = ["Auto Estimate (Refined)", "Fixed / Known Focal"];
        if (!validFocalModes.includes(wFocalMode.value)) {
            wFocalMode.value = "Auto Estimate (Refined)";
        }
    }

    // 1. Sanitize float widget (eliminate NaN from legacy graph strings like "YOLO Auto")
    if (wFocalMm) {
        if (typeof wFocalMm.value !== "number" || isNaN(wFocalMm.value) || wFocalMm.value <= 0) {
            const parsed = parseFloat(wFocalMm.value);
            wFocalMm.value = (!isNaN(parsed) && parsed > 0) ? parsed : 28.0;
        }
    }

    // 2. Sanitize mask mode widget
    if (wMaskMode) {
        const validModes = [
            "Exclude Masked (Dynamic Objects)",
            "Auto YOLO Detect",
            "Track Subject Only (Inverted)",
            "Bypass Mask (Full Frame)"
        ];
        if (!validModes.includes(wMaskMode.value)) {
            wMaskMode.value = "Exclude Masked (Dynamic Objects)";
        }
    }

    // 3. Dynamic labeling for focal_length_mode
    function updateFocalState() {
        if (!wFocalMode || !wFocalMm) return;
        const isFixed = (wFocalMode.value === "Fixed / Known Focal");
        if (isFixed) {
            wFocalMm.label = "focal_length_mm [Fixed Lens]";
        } else {
            wFocalMm.label = "focal_length_mm [Auto Seed]";
        }
        if (app.graph) app.graph.setDirtyCanvas(true, true);
    }

    if (wFocalMode) {
        const origCallback = wFocalMode.callback;
        wFocalMode.callback = function (v) {
            const res = origCallback ? origCallback.apply(this, arguments) : undefined;
            updateFocalState();
            return res;
        };
        updateFocalState();
    }
}

app.registerExtension({
    name: "VFXSuite.CameraTracker",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "CameraTrackerSfM") {
            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
                setupCameraTrackerNode(this);
                return r;
            };

            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
                setupCameraTrackerNode(this);
                return r;
            };
        }
    },
    async nodeCreated(node) {
        if (node.comfyClass === "CameraTrackerSfM" || node.type === "CameraTrackerSfM") {
            setupCameraTrackerNode(node);
        }
    },
    async loadedGraphNode(node) {
        if (node.comfyClass === "CameraTrackerSfM" || node.type === "CameraTrackerSfM") {
            setupCameraTrackerNode(node);
        }
    },
    async setup(app) {
        if (app.graph && app.graph._nodes) {
            for (const node of app.graph._nodes) {
                if (node.comfyClass === "CameraTrackerSfM" || node.type === "CameraTrackerSfM") {
                    setupCameraTrackerNode(node);
                }
            }
        }
    }
});
