import os
import re
import json
import shutil
import difflib
import time

BACKUP_DIR = ".vex_backup"

def backup(path, root):
    rel = os.path.relpath(path, root)
    dst = os.path.join(root, BACKUP_DIR, rel + f".{int(time.time())}.bak")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(path, dst)
    return dst

def read(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def show_diff(old, new, path):
    print(f"\n[vex] diff: {path}")
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="before", tofile="after", lineterm=""):
        print(line)

def insert_lines(content, anchor_pattern, lines, after=True):
    rx = re.compile(anchor_pattern)
    out = []
    for l in content.splitlines(keepends=True):
        if rx.search(l):
            if after:
                out.append(l)
                out.extend(x + "\n" for x in lines)
            else:
                out.extend(x + "\n" for x in lines)
                out.append(l)
        else:
            out.append(l)
    return "".join(out)

def delete_range(content, start_pat, end_pat):
    lines = content.splitlines(keepends=True)
    s = e = None
    for i, l in enumerate(lines):
        if s is None and re.search(start_pat, l):
            s = i
        elif s is not None and re.search(end_pat, l):
            e = i
            break
    if s is None or e is None:
        print(f"[vex] đéo tìm thấy range. start={s} end={e}")
        return content
    return "".join(lines[:s] + lines[e+1:])

def replace_range(content, start_pat, end_pat, new_text):
    lines = content.splitlines(keepends=True)
    s = e = None
    for i, l in enumerate(lines):
        if s is None and re.search(start_pat, l):
            s = i
        elif s is not None and re.search(end_pat, l):
            e = i
            break
    if s is None or e is None:
        print(f"[vex] đéo tìm thấy range để thay.")
        return content
    return "".join(lines[:s]) + new_text + "".join(lines[e+1:])

def patch_by_keyword(content, keyword, new_lines, mode="replace_line"):
    lines = content.splitlines(keepends=True)
    out = []
    hit = False
    for l in lines:
        if keyword in l:
            hit = True
            if mode == "replace_line":
                out.append(new_lines + "\n")
            elif mode == "delete_line":
                pass
            elif mode == "append_after":
                out.append(l)
                out.append(new_lines + "\n")
            elif mode == "append_before":
                out.append(new_lines + "\n")
                out.append(l)
        else:
            out.append(l)
    if not hit:
        print(f"[vex] keyword '{keyword}' đéo thấy.")
    return "".join(out)

def apply_edit(root, cmd, dry=False):
    path = os.path.join(root, cmd["file"])
    if not os.path.isfile(path):
        print(f"[vex] file đéo tồn tại: {cmd['file']}")
        return False
    old = read(path)
    op = cmd["op"]

    if op == "replace_line":
        new = patch_by_keyword(old, cmd["keyword"], cmd["new_lines"], "replace_line")
    elif op == "insert":
        new = insert_lines(old, cmd["anchor"], cmd["lines"], cmd.get("after", True))
    elif op == "delete_range":
        new = delete_range(old, cmd["start"], cmd["end"])
    elif op == "replace_range":
        new = replace_range(old, cmd["start"], cmd["end"], cmd["new_text"])
    elif op == "patch":
        new = patch_by_keyword(old, cmd["keyword"], cmd["new_lines"], cmd.get("mode", "replace_line"))
    else:
        print(f"[vex] op lạ: {op}")
        return False

    if new == old:
        print(f"[vex] no-op: {cmd['file']}")
        return False

    show_diff(old, new, cmd["file"])
    if dry:
        print(f"[vex][dry] skip write: {cmd['file']}")
        return True

    bak = backup(path, root)
    write(path, new)
    print(f"[vex] patched {cmd['file']} (backup: {bak})")
    return True
