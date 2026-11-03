import os, json, tempfile, zipfile
from flask import Flask, request, render_template_string, send_file, jsonify
from depgrapher import lock
from editor import apply_edit

app = Flask(__name__)
WORK = tempfile.mkdtemp(prefix="vex_work_")

PAGE = """
<!doctype html><html><body style="font-family:monospace;background:#111;color:#0f0;padding:20px">
<h2>MINH TOOL - TÌM & SỬA FILE</h2>
<form id="f">
  <input type="file" name="zip" accept=".zip" required><br><br>
  <textarea name="edits" rows="10" cols="60" placeholder='Dán JSON sửa file vào đây (VD: [{"file":"main.py","op":"replace_line","keyword":"old","new_lines":"new"}])'>[]</textarea><br><br>
  <button type="submit">RUN</button>
</form>
<pre id="out"></pre>
<script>
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch('/run', {method:'POST', body:fd});
  const j = await r.json();
  document.getElementById('out').textContent = j.log || j.error;
  if (j.download) {
    const a = document.createElement('a');
    a.href = '/download?token=' + j.download;
    a.download = 'result.zip'; a.click();
  }
};
</script></body></html>
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
            return jsonify({"error": "đéo tìm thấy entry file ở root"})

        log = [f"[vex] entry={entry}"]
        lock(entry, work, "deps.lock.json")
        log.append("[vex] locked")

        try:
            edits = json.loads(edits_raw)
        except Exception as e:
            return jsonify({"error": f"edits.json lỗi: {e}"})

        for c in edits:
            apply_edit(work, c, dry=False)
            log.append(f"[vex] patched {c.get('file')}")

        out_path = os.path.join(work, "result.zip")
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(work):
                for f in files:
                    if f == "result.zip": continue
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, work))

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
