# reading_core.py
from pathlib import Path
from bs4 import BeautifulSoup
import pandas as pd

def process_file(input_file: Path, output_folder: Path):
    """
    Process one HTML/XHTML file into cleaned HTML + TXT.
    Return a dict row for the Excel report.
    """
    with open(input_file, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "lxml")

    footer_text = None

    # 1) <aside> feature detection (simple example: div.feature -> aside)
    for cand in soup.find_all("div", class_="feature"):
        aside = soup.new_tag("aside")
        aside.string = cand.get_text()
        cand.replace_with(aside)

    # 2) Heading detection: short ALLCAPS paragraph -> <h2>
    for p in list(soup.find_all("p")):
        t = p.get_text(strip=True)
        if t and t.isupper() and len(t.split()) <= 6:
            h2 = soup.new_tag("h2")
            h2.string = t
            p.replace_with(h2)

    # 3) Paragraph normalization: ensure sentence ending (very light touch)
    for p in soup.find_all("p"):
        txt = p.get_text(strip=True)
        if txt and not txt.endswith((".", "!", "?")):
            p.string = txt + "."

    # 4) Page numbers -> footer
    for div in list(soup.find_all("div", class_="epub-page-number")):
        footer = soup.new_tag("footer")
        hidden_p = soup.new_tag("p", attrs={"class": "text-hidden"})
        footer_text = div.get_text(strip=True)
        hidden_p.string = footer_text
        footer.append(hidden_p)
        div.replace_with(footer)

    # 5) Images -> figure + figcaption (alt text mirrored in hidden caption)
    for img in list(soup.find_all("img")):
        fig = soup.new_tag("figure")
        img.extract()
        fig.append(img)
        figcap = soup.new_tag("figcaption")
        hidden_p = soup.new_tag("p", attrs={"class": "text-hidden"})
        hidden_p.string = img.get("alt", "Image")
        figcap.append(hidden_p)
        fig.append(figcap)
        # Insert figure where the image was
        img.replace_with(fig)

    # 6) Patch all spans: aria-hidden="true"
    for span in soup.find_all("span"):
        span["aria-hidden"] = "true"

    # Save outputs
    output_html = output_folder / f"{input_file.stem}-reading-order.html"
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(str(soup))

    output_txt = output_folder / f"{input_file.stem}-reading-order.txt"
    with open(output_txt, "w", encoding="utf-8") as f:
        f.write(soup.get_text(separator="\n"))

    return {
        "File": input_file.name,
        "Footer Found": "Yes" if footer_text else "No",
        "Page Number": footer_text or "",
        "HTML Output": output_html.name,
        "Text Output": output_txt.name,
    }

def _collect_files(input_folder: Path):
    files = list(input_folder.rglob("*.xhtml")) + list(input_folder.rglob("*.html"))
    # sort for stable order
    files = sorted(files, key=lambda p: str(p).lower())
    return files

def process_folder(input_folder: Path, output_folder: Path, progress_callback=None):
    """
    Process all HTML/XHTML files under input_folder into output_folder.
    Generates an Excel summary file.
    If provided, progress_callback(current, total, stage, filename) will be called.
    """
    files = _collect_files(input_folder)
    total = len(files)
    report_rows = []

    if total == 0:
        # still produce an empty report for consistency
        df = pd.DataFrame(report_rows, columns=["File","Footer Found","Page Number","HTML Output","Text Output"])
        report_path = output_folder / "reading_report.xlsx"
        df.to_excel(report_path, index=False)
        if progress_callback:
            progress_callback(1, 1, "No files found", "")
        return report_path

    for i, fp in enumerate(files, start=1):
        if progress_callback:
            progress_callback(i-1, total, "Processing", fp.name)
        row = process_file(fp, output_folder)
        report_rows.append(row)
        if progress_callback:
            progress_callback(i, total, "Processing", fp.name)

    if progress_callback:
        progress_callback(total, total, "Writing Excel report", "reading_report.xlsx")

    df = pd.DataFrame(report_rows)
    report_path = output_folder / "reading_report.xlsx"
    df.to_excel(report_path, index=False)

    return report_path
