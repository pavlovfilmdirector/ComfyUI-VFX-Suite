/**
 * Keshiki (景色) VFX AI Studio — 3D Viewport & 3D-to-2D Projection Utilities
 * =======================================================================
 * Modular math & canvas helpers for:
 * 1. Projecting a selected 3D Proxy Cube's World Bounding Box (`THREE.Box3`) onto the
 *    active Shot Camera (or Free Camera) to automatically extract a 512x512 reference
 *    image crop from the current 2D video plate for Hunyuan3D-Omni / TRELLIS.2 generation.
 * 2. Normalizing 3D object scale vectors `(sx, sy, sz)` into integer BBox ratios `[1..100]`
 *    required by Tencent Hunyuan3D-Omni (`--bbox Lx Ly Lz`).
 */

/**
 * Normalize a 3D scale vector into integer ratios [1..100] for Hunyuan3D-Omni BBox conditioning.
 * @param {{x:number, y:number, z:number}} scale
 * @returns {[number, number, number]}
 */
export function normalizeScaleToHunyuanBBox(scale) {
    const sx = Math.max(0.01, Math.abs(Number(scale?.x) || 1));
    const sy = Math.max(0.01, Math.abs(Number(scale?.y) || 1));
    const sz = Math.max(0.01, Math.abs(Number(scale?.z) || 1));
    const maxDim = Math.max(sx, sy, sz, 0.01);
    return [
        Math.max(1, Math.min(100, Math.round((sx / maxDim) * 50))),
        Math.max(1, Math.min(100, Math.round((sy / maxDim) * 50))),
        Math.max(1, Math.min(100, Math.round((sz / maxDim) * 50)))
    ];
}

/**
 * Project a 3D Object's World Bounding Box onto the active camera and crop that region
 * from the current 2D background plate image (or fallback URL).
 *
 * @param {object} params
 * @param {any} params.THREE - Three.js namespace
 * @param {any} params.targetObj - Selected THREE.Object3D (e.g., Proxy Cube)
 * @param {any} params.camera - Active THREE.PerspectiveCamera (Shot Camera or Orbit/UE5 Camera)
 * @param {HTMLImageElement|HTMLCanvasElement|null} params.plateImage - Loaded 2D plate image element
 * @param {string} [params.fallbackUrl] - URL to fetch plate image from if `plateImage` is not ready
 * @param {number} [params.marginRatio=0.20] - Extra context padding around the projected 2D BBox
 * @param {number} [params.outputSize=512] - Output square canvas resolution (px)
 * @returns {Promise<{dataUrl: string, mode: string, bboxPx: {x:number, y:number, w:number, h:number}}>}
 */
export async function projectObject3DBBoxToPlateCrop({
    THREE,
    targetObj,
    camera,
    plateImage,
    fallbackUrl = "/vfx/plate_prep/default/frame/undist/0",
    marginRatio = 0.20,
    outputSize = 512
}) {
    // 1. Ensure we have a valid HTMLImageElement to crop from
    let img = plateImage;
    if (!img || !(img.width > 0 || img.naturalWidth > 0)) {
        img = await new Promise((resolve, reject) => {
            const loaderImg = new Image();
            loaderImg.crossOrigin = "anonymous";
            loaderImg.onload = () => resolve(loaderImg);
            loaderImg.onerror = (err) => reject(new Error("Could not load current 2D plate frame for cropping"));
            loaderImg.src = `${fallbackUrl}${fallbackUrl.includes("?") ? "&" : "?"}t=${Date.now()}`;
        });
    }

    const imgW = img.naturalWidth || img.width || 1280;
    const imgH = img.naturalHeight || img.height || 720;

    let minU = 1.0, minV = 1.0, maxU = 0.0, maxV = 0.0;
    let validProjectedCorners = 0;

    if (THREE && targetObj && camera) {
        targetObj.updateMatrixWorld(true);
        camera.updateMatrixWorld(true);
        camera.updateProjectionMatrix();

        const box = new THREE.Box3().setFromObject(targetObj);
        if (!box.isEmpty()) {
            const corners = [
                new THREE.Vector3(box.min.x, box.min.y, box.min.z),
                new THREE.Vector3(box.min.x, box.min.y, box.max.z),
                new THREE.Vector3(box.min.x, box.max.y, box.min.z),
                new THREE.Vector3(box.min.x, box.max.y, box.max.z),
                new THREE.Vector3(box.max.x, box.min.y, box.min.z),
                new THREE.Vector3(box.max.x, box.min.y, box.max.z),
                new THREE.Vector3(box.max.x, box.max.y, box.min.z),
                new THREE.Vector3(box.max.x, box.max.y, box.max.z)
            ];

            for (const pt of corners) {
                const projected = pt.clone().project(camera);
                // Check that point is in front of the camera near/far clip (z in [-1..1])
                if (projected.z >= -1.0 && projected.z <= 1.05) {
                    const u = (projected.x + 1.0) * 0.5;
                    const v = (1.0 - projected.y) * 0.5;
                    minU = Math.min(minU, u);
                    maxU = Math.max(maxU, u);
                    minV = Math.min(minV, v);
                    maxV = Math.max(maxV, v);
                    validProjectedCorners++;
                }
            }
        }
    }

    let cropX = 0, cropY = 0, cropW = imgW, cropH = imgH;
    let mode = "full_frame";

    // Check if the projected 2D rectangle intersects the image frame with sensible area
    if (
        validProjectedCorners >= 2 &&
        maxU > 0.02 && minU < 0.98 &&
        maxV > 0.02 && minV < 0.98
    ) {
        const spanU = Math.max(0.08, maxU - minU);
        const spanV = Math.max(0.08, maxV - minV);
        const padU = spanU * marginRatio;
        const padV = spanV * marginRatio;

        const u0 = Math.max(0.0, Math.min(0.95, minU - padU));
        const v0 = Math.max(0.0, Math.min(0.95, minV - padV));
        const u1 = Math.min(1.0, Math.max(u0 + 0.05, maxU + padU));
        const v1 = Math.min(1.0, Math.max(v0 + 0.05, maxV + padV));

        cropX = Math.round(u0 * imgW);
        cropY = Math.round(v0 * imgH);
        cropW = Math.max(32, Math.round((u1 - u0) * imgW));
        cropH = Math.max(32, Math.round((v1 - v0) * imgH));
        mode = "projected_3d_bbox";
    } else {
        // Fallback: center square crop (80% of shortest dimension)
        const side = Math.round(Math.min(imgW, imgH) * 0.85);
        cropX = Math.round((imgW - side) * 0.5);
        cropY = Math.round((imgH - side) * 0.5);
        cropW = side;
        cropH = side;
        mode = "center_plate_crop";
    }

    // 2. Render letterboxed/centered into a clean square 512x512 canvas for Hunyuan3D / TRELLIS.2
    const canvas = document.createElement("canvas");
    canvas.width = outputSize;
    canvas.height = outputSize;
    const ctx2d = canvas.getContext("2d");

    ctx2d.fillStyle = "#141416";
    ctx2d.fillRect(0, 0, outputSize, outputSize);

    const scale = Math.min(outputSize / cropW, outputSize / cropH);
    const drawW = Math.round(cropW * scale);
    const drawH = Math.round(cropH * scale);
    const drawX = Math.round((outputSize - drawW) * 0.5);
    const drawY = Math.round((outputSize - drawH) * 0.5);

    ctx2d.drawImage(img, cropX, cropY, cropW, cropH, drawX, drawY, drawW, drawH);

    return {
        dataUrl: canvas.toDataURL("image/png"),
        mode,
        bboxPx: { x: cropX, y: cropY, w: cropW, h: cropH }
    };
}
