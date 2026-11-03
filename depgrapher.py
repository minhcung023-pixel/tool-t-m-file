import os
import re
import json
import hashlib
import sys

PATTERNS = [
    re.compile(r'^\s*import\s+([\w\.]+)'),
    re.compile(r'^\s*from\s+([\w\.]+)\s+import'),
    re.compile(r'^\s*#include\s+["<]([^">]+)[">]'),
    re.compile(r'require\([\'"]([^\'"]+)[\'"]\)'),
]

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def resolve_ref(ref, current_file, root):
    candidates = []
    ref = ref.strip()
    if ref.startswith(".") or "/" in ref or "\\" in ref or ref.endswith((".py", ".h", ".hpp", ".c", ".cpp", ".js")):
        base = os.path.dirname(current_file)
        for ext in ("", ".py", ".h", ".hpp", ".c", ".cpp", ".js", ".ts"):
            candidates.append(os.path.normpath(os.path.join(base, ref + ext)))
    else:
        rel = ref.replace(".", os.sep)
        for ext in ("", ".py", ".h", ".hpp", ".c", ".cpp", ".js", ".ts"):
            candidates.append(os.path.normpath(os.path.join(root, rel + ext)))
            candidates.append(os.path.normpath(os.path.join(root, rel, "__init__" + ext)))
    for c in candidates:
        if os.path.isfile(c):
            return os.path.relpath(c, root)
    return None

def extract_refs(path):
    refs = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                for pat in PATTERNS:
                    m = pat.search(line)
                    if m:
                        refs.append(m.group(1))
    except Exception:
        pass
    return refs

def build_graph(entry, root):
    seen = set()
    edges = {}
    queue = [os.path.relpath(entry, root)]
    while queue:
        cur = queue.pop(0)
        cur_abs = os.path.join(root, cur)
        if cur in seen or not os.path.isfile(cur_abs):
            continue
        seen.add(cur)
        edges[cur] = []
        for ref in extract_refs(cur_abs):
            target = resolve_ref(ref, cur_abs, root)
            if target and target not in seen:
                edges[cur].append(target)
                queue.append(target)
    return seen, edges

def lock(entry, root, manifest="deps.lock.json"):
    files, edges = build_graph(entry, root)
    data = {
        "entry": os.path.relpath(entry, root),
        "files": {f: {"sha256": sha256(os.path.join(root, f)), "size": os.path.getsize(os.path.join(root, f))} for f in sorted(files)},
        "edges": edges,
    }
    with open(os.path.join(root, manifest), "w") as f:
        json.dump(data, f, indent=2)
    print(f"[vex] locked {len(files)} files -> {manifest}")

def verify(root, manifest="deps.lock.json"):
    with open(os.path.join(root, manifest)) as f:
        data = json.load(f)
    bad = []
    for f, meta in data["files"].items():
        p = os.path.join(root, f)
        if not os.path.isfile(p):
            bad.append((f, "missing"))
        elif sha256(p) != meta["sha256"]:
            bad.append((f, "changed"))
    if bad:
        print("[vex] drift detected:")
        for f, why in bad:
            print(f"  {why}: {f}")
        sys.exit(1)
    print("[vex] all files match manifest. clean.")
