"""
TRELLIS.2 runner for KESHIKI (runs inside the TRELLIS.2 Python environment, cwd = the TRELLIS.2 repo).

  image -> 3D  : python trellis_runner.py --mode generate --image in.png --out out.glb [stage params]
  texture mesh : python trellis_runner.py --mode texture  --image in.png --mesh in.glb --out out.glb

Progress lines: "STATUS_UPDATE:{json}" (same protocol as the dubbing engines).
All settings of the TRELLIS.2 web demo are exposed: resolution, seed, the three samplers
(sparse structure, shape, texture: steps / guidance strength / guidance rescale / rescale_t),
decimation target and texture size.
"""
import argparse
import json
import os
import sys
import time

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("ATTN_BACKEND", "xformers")


def status(p, msg, **details):
    print("STATUS_UPDATE:" + json.dumps({"progress": p, "status": msg, "details": details or None}, ensure_ascii=False), flush=True)


def sampler(args, prefix, defaults):
    return {
        "steps": int(getattr(args, f"{prefix}_steps") or defaults[0]),
        "guidance_strength": float(getattr(args, f"{prefix}_guidance") or defaults[1]),
        "guidance_rescale": float(getattr(args, f"{prefix}_rescale") if getattr(args, f"{prefix}_rescale") is not None else defaults[2]),
        "rescale_t": float(getattr(args, f"{prefix}_rescale_t") or defaults[3]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["generate", "texture"], default="generate")
    ap.add_argument("--image", required=True)
    ap.add_argument("--mesh", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--weights", default="")
    ap.add_argument("--resolution", default="1024", choices=["512", "1024", "1536"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--decimation", type=int, default=500000)
    ap.add_argument("--texture_size", type=int, default=2048)
    ap.add_argument("--no_rembg", action="store_true")
    for pre in ("ss", "shape", "tex"):
        ap.add_argument(f"--{pre}_steps", type=int)
        ap.add_argument(f"--{pre}_guidance", type=float)
        ap.add_argument(f"--{pre}_rescale", type=float)
        ap.add_argument(f"--{pre}_rescale_t", type=float)
    a = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    from PIL import Image
    import torch

    weights = a.weights or os.path.join(os.getcwd(), "weights", "microsoft", "TRELLIS.2-4B")
    if not os.path.isfile(os.path.join(weights, "pipeline.json")):
        weights = "microsoft/TRELLIS.2-4B"
    image = Image.open(a.image)
    t0 = time.time()

    if a.mode == "texture":
        if not a.mesh or not os.path.isfile(a.mesh):
            status(100, "No mesh to texture", error="mesh file missing")
            sys.exit(2)
        import trimesh
        from trellis2.pipelines import Trellis2TexturingPipeline
        status(10, "Loading TRELLIS.2 texturing pipeline…")
        pipe = Trellis2TexturingPipeline.from_pretrained(weights)
        pipe.cuda()
        mesh = trimesh.load(a.mesh)
        if isinstance(mesh, trimesh.Scene):
            mesh = mesh.to_mesh()
        if not a.no_rembg:
            image = pipe.preprocess_image(image)
        status(35, "Painting the texture…")
        out = pipe.run(mesh, image, seed=a.seed, preprocess_image=False,
                       tex_slat_sampler_params=sampler(a, "tex", (12, 1.0, 0.0, 3.0)),
                       resolution=int(a.resolution), texture_size=a.texture_size)
        status(90, "Writing GLB…")
        out.export(a.out, extension_webp=True)
    else:
        import o_voxel
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        status(10, "Loading TRELLIS.2 (4B)…")
        pipe = Trellis2ImageTo3DPipeline.from_pretrained(weights)
        pipe.cuda()
        status(25, "Generating shape and materials…")
        outputs = pipe.run(
            image, seed=a.seed, preprocess_image=not a.no_rembg,
            sparse_structure_sampler_params=sampler(a, "ss", (12, 7.5, 0.7, 5.0)),
            shape_slat_sampler_params=sampler(a, "shape", (12, 7.5, 0.5, 3.0)),
            tex_slat_sampler_params=sampler(a, "tex", (12, 1.0, 0.0, 3.0)),
            pipeline_type={"512": "512", "1024": "1024_cascade", "1536": "1536_cascade"}[a.resolution],
        )
        mesh = outputs[0] if isinstance(outputs, (list, tuple)) else outputs
        if isinstance(mesh, (list, tuple)):
            mesh = mesh[0]
        mesh.simplify(16777216)  # nvdiffrast limit
        status(70, "Remesh, UV unwrap and texture bake…")
        glb = o_voxel.postprocess.to_glb(
            vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs, coords=mesh.coords,
            attr_layout=getattr(mesh, "layout", None) or pipe.pbr_attr_layout,
            voxel_size=mesh.voxel_size,
            aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
            decimation_target=a.decimation, texture_size=a.texture_size,
            remesh=True, remesh_band=1, remesh_project=0, verbose=True)
        status(92, "Writing GLB…")
        glb.export(a.out, extension_webp=True)

    torch.cuda.empty_cache()
    status(100, f"Done in {time.time() - t0:.0f} s", output_glb=a.out)


if __name__ == "__main__":
    main()
