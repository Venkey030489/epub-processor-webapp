# app.py
import os
import uuid
import json
import tempfile
import zipfile
import threading
from pathlib import Path
from flask import Flask, request, render_template_string, send_file, url_for, jsonify
import reading_core  # your processing logic

app = Flask(__name__)

# In-memory job registry for progress tracking
JOBS = {}  # job_id -> {"percent": int, "message": str, "done": bool, "error": str|None, "work_dir": str, "output_zip": str|None}

# ---------- HTML Templates ----------

UPLOAD_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>EPUB Reading Order Tool</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background-color: #f8f9fa; padding-top: 40px; }
    .container { max-width: 640px; }
    .card { border-radius: 16px; box-shadow: 0 4px 10px rgba(0,0,0,0.08); }
  </style>
</head>
<body>
  <div class="container">
    <div class="card p-4">
      <h2 class="text-center mb-3">📘 EPUB Reading Order Tool</h2>
      <p class="text-muted text-center mb-4">Upload a ZIP of your EPUB HTML/XHTML files. We’ll process them and create HTML, TXT, and an Excel report.</p>
      <form action="{{ url_for('start') }}" method="post" enctype="multipart/form-data">
        <div class="mb-3">
          <label class="form-label">ZIP file</label>
          <input class="form-control" type="file" name="file" accept=".zip" required>
        </div>
        <div class="d-grid">
          <button class="btn btn-primary btn-lg" type="submit">Process Files</button>
        </div>
      </form>
      <div class="mt-4 small text-muted">
        <strong>Note:</strong> Large uploads may take a while. Keep this tab open until processing finishes.
      </div>
    </div>
  </div>
</body>
</html>
"""

PROGRESS_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Processing…</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background-color: #f8f9fa; padding-top: 40px; }
    .container { max-width: 640px; }
    .card { border-radius: 16px; box-shadow: 0 4px 10px rgba(0,0,0,0.08); }
  </style>
</head>
<body>
  <div class="container">
    <div class="card p-4">
      <h2 class="mb-3 text-center">⏳ Processing your files…</h2>
      <p id="status-text" class="text-muted text-center mb-3">Starting…</p>
      <div class="progress" role="progressbar" aria-label="Processing progress" aria-valuemin="0" aria-valuemax="100">
        <div id="bar" class="progress-bar progress-bar-striped progress-bar-animated" style="width: 0%">0%</div>
      </div>
      <div id="done-section" class="text-center mt-4" style="display:none;">
        <h3 class="text-success">✅ Processing Complete</h3>
        <p>Your files are ready.</p>
        <a id="download-link" href="#" class="btn btn-success btn-lg">⬇️ Download Results</a>
        <div class="mt-3">
          <a href="{{ url_for('index') }}" class="btn btn-secondary">🔄 Process Another File</a>
        </div>
      </div>
      <div id="error-section" class="alert alert-danger mt-4" style="display:none;"></div>
    </div>
  </div>

  <script>
    const jobId = "{{ job_id }}";
    const bar = document.getElementById('bar');
    const statusText = document.getElementById('status-text');
    const doneSection = document.getElementById('done-section');
    const errorSection = document.getElementById('error-section');
    const downloadLink = document.getElementById('download-link');

    function poll() {
      fetch(`{{ url_for('status', job_id='__JOB__') }}`.replace('__JOB__', jobId), { cache: 'no-cache' })
        .then(r => r.json())
        .then(d => {
          if (d.error) {
            errorSection.style.display = 'block';
            errorSection.textContent = d.error;
            statusText.textContent = 'Failed';
            bar.classList.remove('progress-bar-animated');
            bar.classList.add('bg-danger');
            bar.style.width = '100%';
            bar.textContent = 'Error';
            return;
          }
          const pct = Math.max(0, Math.min(100, d.percent || 0));
          bar.style.width = pct + '%';
          bar.textContent = pct + '%';
          statusText.textContent = d.message || '';
          if (d.done) {
            doneSection.style.display = 'block';
            downloadLink.href = `{{ url_for('download_results', job_id='__JOB__') }}`.replace('__JOB__', jobId);
            bar.classList.remove('progress-bar-animated');
            return;
          }
          setTimeout(poll, 900);
        })
        .catch(err => {
          errorSection.style.display = 'block';
          errorSection.textContent = 'Network error while checking status.';
        });
    }

    poll();
  </script>
</body>
</html>
"""

# ---------- Helpers ----------

def update_progress(job_id: str, percent: int, message: str):
    job = JOBS.get(job_id)
    if not job:
        return
    job["percent"] = int(percent)
    job["message"] = message

def worker(job_id: str, zip_path: str):
    """
    Background worker that extracts the ZIP, runs processing, zips results,
    and updates progress as it goes.
    """
    try:
        job = JOBS[job_id]
        work_dir = job["work_dir"]

        update_progress(job_id, 2, "Extracting upload…")
        input_root = os.path.join(work_dir, "input")
        os.makedirs(input_root, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(input_root)

        output_root = os.path.join(work_dir, "output")
        os.makedirs(output_root, exist_ok=True)

        # Progress map:
        # 5% -> start processing
        # 5-90% -> per-file updates from reading_core.process_folder(...)
        # 92-98% -> making ZIP
        # 100% -> done
        update_progress(job_id, 5, "Scanning files…")

        def progress_cb(current, total, stage, filename):
            # Safeguard
            total = max(total, 1)
            # Map file progress into 5..90%
            base = 5
            span = 85
            pct = base + int((current / total) * span)
            update_progress(job_id, pct, f"{stage}: {filename} ({current}/{total})")

        # Run processing (creates Excel too)
        reading_core.process_folder(Path(input_root), Path(output_root), progress_callback=progress_cb)

        update_progress(job_id, 92, "Packaging results…")
        output_zip = os.path.join(work_dir, "results.zip")
        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in Path(output_root).rglob("*"):
                zf.write(f, f.relative_to(output_root))

        job["output_zip"] = output_zip
        update_progress(job_id, 100, "Complete")
        job["done"] = True

    except Exception as e:
        JOBS[job_id]["error"] = f"Processing failed: {e}"
        JOBS[job_id]["done"] = True
        JOBS[job_id]["percent"] = 100
        JOBS[job_id]["message"] = "Failed"

# ---------- Routes ----------

@app.route("/", methods=["GET"])
def index():
    return render_template_string(UPLOAD_PAGE)

@app.route("/start", methods=["POST"])
def start():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename.lower().endswith(".zip"):
        return "❌ Please upload a valid .zip file.", 400

    # Create a job
    job_id = str(uuid.uuid4())
    work_dir = tempfile.mkdtemp(prefix="epubjob_")
    JOBS[job_id] = {
        "percent": 0,
        "message": "Queued",
        "done": False,
        "error": None,
        "work_dir": work_dir,
        "output_zip": None,
    }

    # Save the uploaded zip to disk
    input_zip = os.path.join(work_dir, "input.zip")
    uploaded.save(input_zip)

    # Start background thread
    t = threading.Thread(target=worker, args=(job_id, input_zip), daemon=True)
    t.start()

    # Show progress UI for this job
    return render_template_string(PROGRESS_PAGE, job_id=job_id)

@app.route("/status/<job_id>", methods=["GET"])
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Invalid job id"}), 404
    return jsonify({
        "percent": job["percent"],
        "message": job["message"],
        "done": job["done"],
        "error": job["error"]
    })

@app.route("/download/<job_id>", methods=["GET"])
def download_results(job_id):
    job = JOBS.get(job_id)
    if not job:
        return "Invalid job id", 404
    output_zip = job.get("output_zip")
    if not output_zip or not os.path.exists(output_zip):
        return "No result available", 404
    return send_file(output_zip, as_attachment=True, download_name="results.zip")

if __name__ == "__main__":
    # For local dev
    app.run(host="0.0.0.0", port=5000, debug=True)
