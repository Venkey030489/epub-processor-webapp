import threading
from pathlib import Path
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, flash
from reading_core import process_folder
import os, tempfile, zipfile, shutil

app = Flask(__name__)
app.secret_key = "secret123"
OUTPUT_FOLDER = "outputs"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

progress_status = {"percent": 0, "done": False, "zip_path": None, "error": None}

def _update_progress(p):
    progress_status["percent"] = p

def background_task(epub_path, job_id):
    try:
        with tempfile.TemporaryDirectory() as extract_dir, tempfile.TemporaryDirectory() as out_dir:
            with zipfile.ZipFile(epub_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            # Run processing
            report_path = process_folder(
                Path(extract_dir),
                Path(out_dir),
                progress_callback=_update_progress,
                feature_titles=None,
                h1_candidates=None
            )

            # Repack EPUB
            patched_epub = os.path.join(out_dir, "patched.epub")
            with zipfile.ZipFile(patched_epub, 'w') as zip_out:
                for foldername, _, filenames in os.walk(extract_dir):
                    for filename in filenames:
                        file_path = os.path.join(foldername, filename)
                        rel_path = os.path.relpath(file_path, extract_dir)
                        zip_out.write(file_path, rel_path)

            # Make final ZIP with everything
            final_zip = os.path.join(OUTPUT_FOLDER, f"{job_id}.zip")
            with zipfile.ZipFile(final_zip, 'w') as zipf:
                zipf.write(patched_epub, "patched.epub")
                for file in os.listdir(out_dir):
                    zipf.write(os.path.join(out_dir, file), file)

            progress_status["done"] = True
            progress_status["zip_path"] = final_zip
    except Exception as e:
        progress_status["error"] = str(e)


@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file uploaded")
            return redirect(request.url)
        file = request.files["file"]
        if not file.filename.endswith(".epub"):
            flash("Please upload an EPUB file")
            return redirect(request.url)

        job_id = os.path.splitext(file.filename)[0]
        epub_path = os.path.join(OUTPUT_FOLDER, file.filename)
        file.save(epub_path)

        # Reset progress
        progress_status.update({"percent": 0, "done": False, "zip_path": None, "error": None})

        # Start background thread
        thread = threading.Thread(target=background_task, args=(epub_path, job_id))
        thread.start()

        return redirect(url_for("progress_page"))

    return render_template_string("""
        <h1>Upload EPUB</h1>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file" required>
            <button type="submit">Upload</button>
        </form>
    """)


@app.route("/progress")
def progress_page():
    if progress_status["error"]:
        return f"❌ Error: {progress_status['error']}"
    if progress_status["done"]:
        return f"""
            ✅ Processing complete!<br>
            <a href="{url_for('download_file', filename=os.path.basename(progress_status['zip_path']))}">Download Results ZIP</a>
        """
    return f"Processing: {progress_status['percent']}%"


@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)
