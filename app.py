from pathlib import Path
import os
import shutil
import tempfile
import zipfile
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, flash
from reading_core import process_folder

app = Flask(__name__)
app.secret_key = "secret123"
OUTPUT_FOLDER = "outputs"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Templates inline for simplicity
UPLOAD_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <title>EPUB Processor</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
</head>
<body class="bg-light">
<div class="container py-5">
  <h1 class="mb-4">📚 EPUB Processor</h1>
  <form method="post" action="{{ url_for('upload') }}" enctype="multipart/form-data" class="card p-4 shadow-sm bg-white">
    <div class="mb-3">
      <label class="form-label">Upload EPUB .zip</label>
      <input class="form-control" type="file" name="file" required>
    </div>
    <button class="btn btn-primary" type="submit">Process</button>
  </form>
  {% with messages = get_flashed_messages() %}
    {% if messages %}
      <div class="alert alert-danger mt-3">{{ messages[0] }}</div>
    {% endif %}
  {% endwith %}
</div>
</body>
</html>
"""

PROGRESS_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <title>Processing EPUB</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
  <script>
    function updateProgress() {
      $.get("{{ url_for('progress') }}", function(data) {
        $("#progress-bar").css("width", data.percent + "%");
        $("#progress-bar").text(data.percent + "%");
        if (data.percent < 100) {
          setTimeout(updateProgress, 1000);
        } else {
          window.location.href = "{{ url_for('success') }}";
        }
      });
    }
    $(document).ready(function() {
      updateProgress();
    });
  </script>
</head>
<body class="bg-light">
<div class="container py-5 text-center">
  <h2 class="mb-4">⏳ Processing...</h2>
  <div class="progress" style="height: 30px;">
    <div id="progress-bar" class="progress-bar progress-bar-striped progress-bar-animated bg-success"
         role="progressbar" style="width: 0%">0%</div>
  </div>
</div>
</body>
</html>
"""

SUCCESS_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <title>EPUB Processed</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<div class="container py-5 text-center">
  <h2 class="mb-4">✅ Processing Complete</h2>
  <p>Your EPUB has been patched and packaged. Click below to download.</p>
  <a href="{{ url_for('download_file', filename=zip_name) }}" class="btn btn-success btn-lg">
    ⬇ Download Processed ZIP
  </a>
</div>
</body>
</html>
"""

# Shared state for progress
progress_data = {"current": 0, "total": 1}

@app.route("/", methods=["GET"])
def index():
    return render_template_string(UPLOAD_PAGE)

@app.route("/", methods=["POST"])
def upload():
    if "file" not in request.files:
        flash("No file uploaded")
        return redirect(request.url)

    file = request.files["file"]
    if file.filename == "":
        flash("No selected file")
        return redirect(request.url)

    if not file.filename.lower().endswith(".zip"):
        flash("Please upload a .zip file containing EPUB files")
        return redirect(request.url)

    # Reset progress
    progress_data["current"] = 0
    progress_data["total"] = 1

    # Create temp workspace
    temp_dir = tempfile.mkdtemp()
    input_zip = os.path.join(temp_dir, "input.zip")
    file.save(input_zip)

    extract_dir = os.path.join(temp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    with zipfile.ZipFile(input_zip, "r") as zip_ref:
        zip_ref.extractall(extract_dir)

    output_dir = os.path.join(temp_dir, "processed")
    os.makedirs(output_dir, exist_ok=True)

    # Run processing
    process_folder(
        extract_dir,
        output_dir,
        progress_callback=_update_progress,
        feature_titles=None,   # pass list if you want Excel feature tracking
        h1_candidates=None     # pass list if you want H1 rules applied
    )

    # Package final ZIP
    final_zip_path = os.path.join(OUTPUT_FOLDER, "processed_output.zip")
    if os.path.exists(final_zip_path):
        os.remove(final_zip_path)
    shutil.make_archive(final_zip_path.replace(".zip", ""), "zip", output_dir)

    return render_template_string(PROGRESS_PAGE)

def _update_progress(current, total, stage, filename):
    progress_data["current"] = current
    progress_data["total"] = total

@app.route("/progress")
def progress():
    percent = int((progress_data["current"] / progress_data["total"]) * 100)
    return {"percent": percent}

@app.route("/success")
def success():
    return render_template_string(SUCCESS_PAGE, zip_name="processed_output.zip")

@app.route("/download/<path:filename>")
def download_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
