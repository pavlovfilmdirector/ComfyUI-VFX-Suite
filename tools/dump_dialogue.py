import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = sys.argv[1] if len(sys.argv) > 1 else "New_Movie_Shot"

lines = open(os.path.join(ROOT, "projects", PROJECT, "agent", "dialogue.jsonl"), "r", encoding="utf-8").readlines()
with open(os.path.join(ROOT, "output_cache", "agent_dialogue_debug.txt"), "w", encoding="utf-8") as out:
    for l in lines[-12:]:
        d = json.loads(l)
        out.write(f"=== {d.get('role').upper()} ===\n")
        out.write(d.get('text', '') + "\n")
        if d.get('action'):
            out.write(f"ACTION: {d.get('action')} {d.get('params')}\n")
        out.write("\n" + "="*50 + "\n\n")

print("Dumped.")
