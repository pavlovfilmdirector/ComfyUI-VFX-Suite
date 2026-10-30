import json

lines = open(r"B:\ComfyUI-VFX-Suite\projects\New_Movie_Shot\agent\dialogue.jsonl", "r", encoding="utf-8").readlines()
with open(r"B:\ComfyUI-VFX-Suite\output_cache\agent_dialogue_debug.txt", "w", encoding="utf-8") as out:
    for l in lines[-12:]:
        d = json.loads(l)
        out.write(f"=== {d.get('role').upper()} ===\n")
        out.write(d.get('text', '') + "\n")
        if d.get('action'):
            out.write(f"ACTION: {d.get('action')} {d.get('params')}\n")
        out.write("\n" + "="*50 + "\n\n")

print("Dumped.")
