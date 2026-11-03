import os, re, json, hashlib, tempfile, zipfile, shutil, difflib, time
from flask import Flask, request, render_template_string, send_file, jsonify

app = Flask(__name__)
WORK = tempfile.mkdtemp(prefix="vex_work_")

# ==========================================
# LOGIC QUÉT FILE & SỬA FILE
# ==========================================
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

def read(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

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
    if s is None or e is None: return content
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
    if s is None or e is None: return content
    return "".join(lines[:s]) + new_text + "".join(lines[e+1:])

def patch_by_keyword(content, keyword, new_lines, mode="replace_line"):
    lines = content.splitlines(keepends=True)
    out = []
    for l in lines:
        if keyword in l:
            if mode == "replace_line": out.append(new_lines + "\n")
            elif mode == "append_after": out.append(l); out.append(new_lines + "\n")
            elif mode == "append_before": out.append(new_lines + "\n"); out.append(l)
        else: out.append(l)
    return "".join(out)

def apply_edit(root, cmd):
    path = os.path.join(root, cmd["file"])
    if not os.path.isfile(path): return False
    old = read(path)
    op = cmd["op"]

    if op == "replace_line": new = patch_by_keyword(old, cmd["keyword"], cmd["new_lines"], "replace_line")
    elif op == "insert": new = insert_lines(old, cmd["anchor"], cmd["lines"], cmd.get("after", True))
    elif op == "delete_range": new = delete_range(old, cmd["start"], cmd["end"])
    elif op == "replace_range": new = replace_range(old, cmd["start"], cmd["end"], cmd["new_text"])
    elif op == "patch": new = patch_by_keyword(old, cmd["keyword"], cmd["new_lines"], cmd.get("mode", "replace_line"))
    else: return False

    if new != old:
        write(path, new)
        return True
    return False

# ==========================================
# GIAO DIỆN WEB
# ==========================================
PAGE = """
<!doctype html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>MINH TOOL</title>
    <style>
        :root { --bg: #050505; --main: #0f0; --dim: #005500; --red: #ff3333; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Courier New', Courier, monospace; }
        body {
            background-color: var(--bg); color: var(--main);
            min-height: 100vh; display: flex; flex-direction: column; padding: 15px;
            background-image: linear-gradient(rgba(0, 255, 0, 0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 255, 0, 0.05) 1px, transparent 1px);
            background-size: 30px 30px;
        }
        .header { text-align: center; padding: 20px 10px; border-bottom: 2px solid var(--dim); margin-bottom: 20px; box-shadow: 0 4px 15px rgba(0, 255, 0, 0.1); }
        h1 { font-size: 1.5rem; text-shadow: 0 0 10px var(--main); letter-spacing: 3px; text-transform: uppercase; }
        .blink { animation: blink 1s step-end infinite; }
        @keyframes blink { 50% { opacity: 0; } }
        .container { flex: 1; display: flex; flex-direction: column; gap: 20px; width: 100%; max-width: 800px; margin: 0 auto; }
        .terminal-box { border: 1px solid var(--dim); background: rgba(0, 20, 0, 0.6); padding: 20px 15px 15px 15px; border-radius: 4px; position: relative; }
        .terminal-box::before { content: attr(data-title); position: absolute; top: -10px; left: 10px; background: var(--bg); padding: 0 5px; font-size: 0.8rem; color: var(--dim); }
        input[type="file"] { display: none; }
        .file-label { display: block; width: 100%; padding: 20px; text-align: center; border: 2px dashed var(--main); cursor: pointer; transition: all 0.3s; text-transform: uppercase; font-weight: bold; font-size: 1.1rem; }
        .file-label:active { background: var(--main); color: var(--bg); box-shadow: 0 0 15px var(--main); }
        .file-name { margin-top: 10px; font-size: 0.85rem; color: var(--dim); word-break: break-all; text-align: center; }
        textarea { width: 100%; height: 180px; background: transparent; border: none; color: var(--main); font-size: 0.9rem; resize: vertical; outline: none; }
        textarea::placeholder { color: var(--dim); }
        button { width: 100%; padding: 18px; background: transparent; color: var(--main); border: 2px solid var(--main); font-size: 1.2rem; font-weight: bold; cursor: pointer; text-transform: uppercase; letter-spacing: 2px; transition: 0.2s; box-shadow: 0 0 10px rgba(0, 255, 0, 0.2); margin-top: 10px; }
        button:active { background: var(--main); color: var(--bg); box-shadow: 0 0 20px var(--main); }
        button:disabled { border-color: var(--dim); color: var(--dim); box-shadow: none; }
        .output-box { flex: 1; min-height: 250px; border: 1px solid var(--dim); padding: 15px; font-size: 0.85rem; overflow-y: auto; white-space: pre-wrap; word-wrap: break-word; background: rgba(0, 10, 0, 0.9); color: var(--dim); }
        .error { color: var(--red); text-shadow: 0 0 5px var(--red); }
        .success { color: var(--main); }
    </style>
</head>
<body>
    <div class="header">
        <h1>MINH TOOL<span class="blink">_</span></h1>
        <div style="font-size: 0.8rem; color: var(--dim); margin-top: 5px;">FILE SYSTEM EDITOR</div>
    </div>
    <div class="container">
        <form id="f">
            <div class="terminal-box" data-title="[ 1. TARGET_FILE ]">
                <label for="zip" class="file-label" id="file-label">CHỌN FILE .ZIP</label>
                <input type="file" name="zip" id="zip" accept=".zip" required onchange="updateFileName(this)">
                <div class="file-name" id="file-name">Chưa có file nào được chọn...</div>
            </div>
            <div class="terminal-box" data-title="[ 2. JSON_EDITS ]">
                <textarea name="edits" id="edits" placeholder='[&#10;  {&#10;    "file": "main.py",&#10;    "op": "replace_line",&#10;    "keyword": "old",&#10;    "new_lines": "new"&#10;  }&#10;]'>[]</textarea>
            </div>
            <button type="submit" id="submit-btn">RUN_EXECUTION</button>
        </form>
        <div class="terminal-box" data-title="[ 3. SYSTEM_LOG ]">
            <div class="output-box" id="out">> Hệ thống sẵn sàng. Đang chờ lệnh...</div>
        </div>
    </div>
    <script>
        function updateFileName(input) {
            const fileNameDiv = document.getElementById('file-name');
            const label = document.getElementById('file-label');
            if (input.files && input.files.length > 0) {
                fileNameDiv.textContent = input.files[0].name;
                label.textContent = "ĐÃ CHỌN FILE";
                label.style.color = "var(--bg)"; label.style.background = "var(--main)";
            } else {
                fileNameDiv.textContent = "Chưa có file nào được chọn...";
                label.textContent = "CHỌN FILE .ZIP";
                label.style.color = "var(--main)"; label.style.background = "transparent";
            }
        }
        document.getElementById('f').onsubmit = async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const outDiv = document.getElementById('out');
            const btn = document.getElementById('submit-btn');
            outDiv.innerHTML = "> Khởi chạy tiến trình...<br>> Đang giải nén file...";
            outDiv.className = 'output-box';
            btn.disabled = true; btn.textContent = "PROCESSING...";
            try {
                const r = await fetch('/run', {method:'POST', body:fd});
                const j = await r.json();
                if (j.error) {
                    outDiv.innerHTML = "[!] LỖI: " + j.error;
                    outDiv.classList.add('error');
                } else {
                    outDiv.innerHTML = j.log;
                    outDiv.classList.add('success');
                    if (j.download) {
                        outDiv.innerHTML += "<br><br>> Tải file kết quả...";
                        const a = document.createElement('a');
                        a.href = '/download?token=' + j.download;
                        a.download = 'result.zip'; 
                        document.body.appendChild(a); a.click(); document.body.removeChild(a);
                    }
                }
            } catch (err) {
                outDiv.innerHTML = "[!] LỖI KẾT NỐI: Server đéo phản hồi hoặc sập rồi!";
                outDiv.classList.add('error');
            } finally {
                btn.disabled = false; btn.textContent = "RUN_EXECUTION";
            }
        };
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/run", methods=["POST"])
def run():
    try:
        zf = request.files["zip"]
        edits_raw = request.form.get("edits", "[]")
        work = tempfile.mkdtemp(dir=WORK)
        zpath = os.path.join(work, "in.zip")
        zf.save(zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(work)

        entry = next((f for f in os.listdir(work) if f.endswith((".py",".c",".cpp",".js"))), None)
        if not entry:
            return jsonify({"error": "đéo tìm thấy entry file ở root (cần file .py, .c, .cpp, hoặc .js ở ngoài cùng)"})

        log = [f"[vex] Tìm thấy entry: {entry}"]
        lock(entry, work, "deps.lock.json")
        log.append("[vex] Đã lock dependency.")

        try:
            edits = json.loads(edits_raw)
        except Exception as e:
            return jsonify({"error": f"JSON bị sai cú pháp rồi mày: {e}"})

        for c in edits:
            apply_edit(work, c)
            log.append(f"[vex] Đã xử lý lệnh cho: {c.get('file')}")

        out_path = os.path.join(work, "result.zip")
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(work):
                for f in files:
                    if f == "result.zip" or f == "deps.lock.json": continue
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, work))
                    
        log.append(f"[vex] Xong. Đang nén lại file kết quả.")
        return jsonify({"log": "\n".join(log), "download": os.path.basename(work)})
    except Exception as e:
        return jsonify({"error": repr(e)})

@app.route("/download")
def download():
    token = request.args.get("token")
    p = os.path.join(WORK, token, "result.zip")
    if not os.path.isfile(p):
        return "not found", 404
    return send_file(p, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
