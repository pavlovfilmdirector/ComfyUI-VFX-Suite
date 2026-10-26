"""
Nuke VFX Bridge Plugin - ComfyUI Client
=======================================
Communicates with headless ComfyUI running locally (or remote server)
to trigger SfM camera tracking, dynamic masking, and fetch solved 3D scene data.
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
import time

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"

class ComfyVFXClient:
    def __init__(self, base_url=DEFAULT_COMFY_URL):
        self.base_url = base_url.rstrip("/")

    def is_alive(self):
        """Check if ComfyUI server is reachable."""
        try:
            req = urllib.request.Request(f"{self.base_url}/system_stats")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_scene_data(self):
        """Fetch solved camera trajectory and point cloud from /vfx/scene_data."""
        url = f"{self.base_url}/vfx/scene_data"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)

    def get_plate_info(self, node_id="default"):
        """Fetch plate preparation info including distortion k1, k2 from /vfx/plate_prep/{node_id}/info."""
        try:
            url = f"{self.base_url}/vfx/plate_prep/{node_id}/info"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {}

    def trigger_tracking_solve(self, prompt_workflow):
        """Queue prompt workflow via ComfyUI /prompt endpoint."""
        url = f"{self.base_url}/prompt"
        payload = json.dumps({"prompt": prompt_workflow}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            return res_data.get("prompt_id")

    def wait_for_completion(self, prompt_id, timeout_sec=300, poll_callback=None):
        """Poll ComfyUI /history endpoint until prompt_id is done."""
        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            try:
                url = f"{self.base_url}/history/{prompt_id}"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if prompt_id in data:
                        return data[prompt_id]
            except Exception:
                pass

            if poll_callback:
                poll_callback(int(time.time() - start_time))
            time.sleep(1.0)
        raise TimeoutError(f"ComfyUI tracking execution timed out after {timeout_sec}s.")
