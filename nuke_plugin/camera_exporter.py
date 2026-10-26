"""
Camera & Scene Exporter for Foundry Nuke
========================================
Exports camera trajectories solved by GLOMAP SfM into:
1. Nuke native .chan (Frame, Tx, Ty, Tz, Rx, Ry, Rz, Fov)
2. JSON Camera Manifest (World Matrices, Intrinsic params)
"""

import os
import math
import json

def rotation_matrix_to_euler_xyz_deg(R):
    """
    Decomposes 3x3 rotation matrix into XYZ Euler angles in degrees (Nuke / DCC convention).
    R = Rz * Ry * Rx
    """
    sy = math.sqrt(R[0][0] * R[0][0] + R[1][0] * R[1][0])
    singular = sy < 1e-6

    if not singular:
        x = math.atan2(R[2][1], R[2][2])
        y = math.atan2(-R[2][0], sy)
        z = math.atan2(R[1][0], R[0][0])
    else:
        x = math.atan2(-R[1][2], R[1][1])
        y = math.atan2(-R[2][0], sy)
        z = 0.0

    return math.degrees(x), math.degrees(y), math.degrees(z)


def export_chan_file(file_path, matrices, focal_mm=35.0, sensor_width_mm=36.0, start_frame=1):
    """
    Writes Nuke standard .chan file:
    Frame | Tx | Ty | Tz | Rx | Ry | Rz | Vertical_Fov
    """
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    
    # Calculate vertical / horizontal FOV
    fov_deg = 2.0 * math.degrees(math.atan((sensor_width_mm / 2.0) / max(0.1, focal_mm)))

    with open(file_path, "w", encoding="utf-8") as f:
        for i, mat in enumerate(matrices):
            frame_num = start_frame + i
            tx = mat[0][3]
            ty = mat[1][3]
            tz = mat[2][3]

            R = [
                [mat[0][0], mat[0][1], mat[0][2]],
                [mat[1][0], mat[1][1], mat[1][2]],
                [mat[2][0], mat[2][1], mat[2][2]]
            ]
            rx, ry, rz = rotation_matrix_to_euler_xyz_deg(R)

            # Standard format: frame tx ty tz rx ry rz fov
            f.write(f"{frame_num}\t{tx:.6f}\t{ty:.6f}\t{tz:.6f}\t{rx:.6f}\t{ry:.6f}\t{rz:.6f}\t{fov_deg:.4f}\n")

    return file_path


def export_camera_json(file_path, camera_data):
    """Saves full camera trajectory manifest in JSON format."""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(camera_data, f, indent=2)
    return file_path
