import os
import tempfile
import zipfile
from pathlib import Path
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, flash
from reading_core import process_folder

app = Flask(__name__)
app.secret_key = "secret123"
OUTPUT_FOLDER = "outputs"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Global progress data
progress_data = {"percent": 0, "stage": "Idle", "file": ""}


# ------------------- Progress Callback -------------------
def _update_progress(current, total, stage, filename):
    if total > 0:
        progress_data["percent"] = int((current / total) * 100)
    else:
        progress_data["percent"] = 0
    progress_data["stage"] = stage
    progress_data["file"] = filename


# ------------------- HTML Templates -------------------
UPLOAD_FORM_HTML = """
<!doctype html>
<html>
<head>
  <title>EPUB Processor</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="p-5">
  <h2>Upload EPUB (.zip)</h2>
  <form id="upload-form" method="post" enctype="multipart/form-data">
    <div class="mb-3">
      <input type="file" name="file" accept=".zip" class="form-control" required>
    </div>
    <button id="upload-btn" type="submit" class="btn btn-primary">
      <span id="btn-text">Upload & Process</span>
      <span id="btn-spinner" class="spinner-border spinner-border-sm d-none" role="status" aria-hidden="true"></span>
    </button>
  </form>

  <div class="progress mt-4" style="height:30px;">
    <div id="progress-bar" class="progress-bar progress-bar-striped progress-bar-animated bg-primary"
         role="progressbar" style="width:0%">0%</div>
  </div>
  <p id="progress-text" class="mt-2 text-muted"></p>

  <script>
    // Show spinner & disable button after form submit
    document.getElementById("upload-form").addEventListener("submit", function() {
      const btn = document.getElementById("upload-btn");
      document.getElementById("btn-text").textContent = "Processing...";
      document.getElementById("btn-spinner").classList.remove("d-none");
      btn.disabled = true;
    });

    async function fetchProgress() {
      const res = await fetch("/progress");
      if (!res.ok) return;
      const data = await res.json();
      const bar = document.getElementById("progress-bar");
      const text = document.getElementById("progress-text");

      bar.style.width = data.percent + "%";
      bar.innerText = data.percent + "%";
      text.innerText = data.stage + (data.file ? " → " + data.file : "");

      // ✅ Turn green when complete
      if (data.percent >= 100) {
        bar.classList.remove("progress-bar-striped", "progress-bar-animated", "bg-primary");
        bar.classList.add("bg-success");
      }
    }

    setInterval(fetchProgress, 1000);
  </script>
</body>
</html>
"""

SUCCESS_HTML = """
<!doctype html>
<html>
<head>
  <title>Processing Complete</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="p-5 text-center">
  <h2 class="text-success">✅ Processing Complete!</h2>
  <p>Your EPUB has been processed successfully.</p>
  <a class="btn btn-success btn-lg" href="{{ url_for('download_file', filename=filename) }}">⬇ Download Processed ZIP</a>
</body>
</html>
"""


# ------------------- Routes -------------------
@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file part")
            return redirect(request.url)

        file = request.files["file"]
        if file.filename == "":
            flash("No selected file")
            return redirect(request.url)

        if file and file.filename.lower().endswith(".zip"):
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)

                # Paths
                extract_dir = tmpdir_path / "extracted"
                output_dir = tmpdir_path / "processed"
                extract_dir.mkdir(parents=True, exist_ok=True)
                output_dir.mkdir(parents=True, exist_ok=True)

                # Save uploaded ZIP
                uploaded_zip = tmpdir_path / "uploaded.zip"
                file.save(uploaded_zip)

                # Extract EPUB ZIP
                with zipfile.ZipFile(uploaded_zip, "r") as zip_ref:
                    zip_ref.extractall(extract_dir)

                # Run processing
                try:
                    report_path = process_folder(
                        Path(extract_dir),
                        Path(output_dir),
                        progress_callback=_update_progress,
                        feature_titles=None,
                        h1_candidates=None
                    )
                except Exception as e:
                    return render_template_string(
                        "<h2 style='color:red;'>Processing failed: {{err}}</h2>",
                        err=str(e)
                    )

                # Package final ZIP
                final_zip_path = Path(OUTPUT_FOLDER) / "processed_output.zip"
                with zipfile.ZipFile(final_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                    # Add processed files
                    for root, _, files in os.walk(output_dir):
                        for fname in files:
                            fpath = Path(root) / fname
                            arcname = fpath.relative_to(output_dir)
                            zipf.write(fpath, arcname)

                    # Add Excel report
                    if report_path.exists():
                        zipf.write(report_path, report_path.name)

                return redirect(url_for("success", filename=final_zip_path.name))

        else:
            flash("Please upload a valid EPUB zip file")
            return redirect(request.url)

    return render_template_string(UPLOAD_FORM_HTML)


@app.route("/progress")
def progress():
    return progress_data


@app.route("/success/<filename>")
def success(filename):
    return render_template_string(SUCCESS_HTML, filename=filename)


@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)


# ------------------- Run -------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
