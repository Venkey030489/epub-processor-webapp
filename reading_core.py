# reading_core.py
import re
import shutil
from pathlib import Path
from typing import Any, List, Optional, Dict, Callable, Tuple
from html import escape as html_escape
import html as ihtml

from bs4 import BeautifulSoup, NavigableString, Comment

import pandas as pd

# ----------------- Helpers -----------------

def _is_ignorable_media_node(node: Any) -> bool:
    if isinstance(node, Comment):
        return True
    if isinstance(node, NavigableString):
        return not str(node).strip()
    if not hasattr(node, "name") or not node.name:
        return True
    t = (node.name or "").lower()
    if t in ("figure", "img", "picture", "svg", "br", "hr"):
        return True
    if t in ("div", "section", "span"):
        if node.find(["figure", "img", "picture", "svg"]) and not node.find("p"):
            return True
    return False

# ---------- Paragraph & aside merging ----------

def merge_outside_p_after_aside(html_fragment: str) -> str:
    soup = BeautifulSoup(html_fragment, 'html.parser')

    def ends_with_dot(el: Any) -> bool:
        txt = el.get_text(strip=True)
        return bool(txt) and txt.endswith(".")

    for aside in soup.find_all("aside"):
        p_tags = aside.find_all("p")
        last_p = p_tags[-1] if p_tags else None
        if not last_p:
            continue

        while not ends_with_dot(last_p):
            sib = aside.next_sibling
            while sib and _is_ignorable_media_node(sib):
                sib = sib.next_sibling
            if sib and getattr(sib, "name", "").lower() == "p":
                current_text = last_p.get_text()
                if current_text and not current_text.endswith((" ", "\n", "\t")):
                    last_p.append(NavigableString(" "))
                for child in list(sib.contents):
                    last_p.append(child)
                sib.extract()
            else:
                break

    return str(soup)


def ensure_paragraphs_end_with_dot(html_fragment: str) -> str:
    soup = BeautifulSoup(html_fragment, 'html.parser')

    def ends_with_dot(el: Any) -> bool:
        txt = el.get_text(strip=True)
        return bool(txt) and txt.endswith(".")

    def first_word_has_uppercase(el: Any) -> bool:
        txt = el.get_text(" ", strip=True)
        if not txt:
            return False
        trimmed = re.sub(r'^[\s\W_]+', '', txt, flags=re.UNICODE)
        if not trimmed:
            return False
        m = re.match(r'^([^\s]+)', trimmed, flags=re.UNICODE)
        if not m:
            return False
        first_word = m.group(1)
        return any(ch.isupper() for ch in first_word)

    for p in soup.find_all("p"):
        if not p.parent:
            continue
        while not ends_with_dot(p):
            sib = p.next_sibling
            while sib and _is_ignorable_media_node(sib):
                sib = sib.next_sibling
            if not sib or getattr(sib, "name", "").lower() != "p" or sib.parent is not p.parent:
                break
            if first_word_has_uppercase(sib):
                break
            current_text = p.get_text()
            if current_text and not current_text.endswith((" ", "\n", "\t")):
                p.append(NavigableString(" "))
            for child in list(sib.contents):
                p.append(child)
            sib.decompose()

    return str(soup)


# ---------- List detection (converted from original) ----------

# Patterns
num_pat = re.compile(r'^\s*(\d{1,3})[.)]\s+(.*)$')
letter_pat = re.compile(r'^\s*([A-Z])[.)]\s+(.*)$')
bullet_chars = r"\-\*\u2013\u2014\u2022\u2023\u25E6\u2219\u00B7\u2027\u25AA\u25CF\u25CB\u25A0"
bullet_pat = re.compile(r'^\s*(?:[' + bullet_chars + r'])\s+(.*)$')
inline_letter_split_pat = re.compile(r'(?:^|\s)([A-Z])[.)]\s+')

def convert_paragraphs_to_lists(html_fragment: str, min_items: int = 2) -> str:
    soup = BeautifulSoup(html_fragment, 'html.parser')

    def split_inline_letter_opts(text: str):
        matches = list(inline_letter_split_pat.finditer(text))
        if not matches:
            return text.strip(), []
        q_text = text[:matches[0].start()].strip()
        options = []
        for idx, m in enumerate(matches):
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            opt = text[start:end].strip()
            if opt:
                options.append(opt)
        return q_text, options

    def is_p(el: Any) -> bool:
        return hasattr(el, "name") and el.name == "p"

    def process_parent_for_numbered_with_nested(parent):
        children = list(parent.children)
        i = 0
        while i < len(children):
            el = children[i]
            if not is_p(el):
                i += 1
                continue

            txt = el.get_text(" ", strip=True)
            m = num_pat.match(txt)
            if not m:
                i += 1
                continue

            items = []
            consumed = []
            pos = i
            while pos < len(children):
                node = children[pos]
                if not is_p(node):
                    break
                t = node.get_text(" ", strip=True)
                mnum = num_pat.match(t)
                if not mnum:
                    break

                q_full = mnum.group(2)
                q_text, inline_opts = split_inline_letter_opts(q_full)

                opt_nodes = []
                opt_texts = []
                k = pos + 1
                while k < len(children):
                    nodek = children[k]
                    if not is_p(nodek):
                        break
                    tk = nodek.get_text(" ", strip=True)
                    if num_pat.match(tk):
                        break  # next numbered item
                    mletter = letter_pat.match(tk)
                    if mletter:
                        opt_nodes.append(nodek)
                        opt_texts.append(mletter.group(2))
                        k += 1
                        continue
                    break

                items.append({
                    "p_node": node,
                    "q_text": q_text,
                    "inline_opts": inline_opts,
                    "opt_nodes": opt_nodes,
                    "opt_texts": opt_texts,
                })
                consumed.append(node)
                consumed.extend(opt_nodes)

                if k < len(children) and is_p(children[k]) and num_pat.match(children[k].get_text(" ", strip=True)):
                    pos = k
                    continue
                else:
                    pos = k
                    break

            if len(items) >= min_items:
                new_ol = soup.new_tag("ol")
                new_ol["class"] = ["text-hidden"]
                for it in items:
                    li = soup.new_tag("li")
                    li.append(NavigableString(it["q_text"]))
                    all_opts = list(it["inline_opts"]) + list(it["opt_texts"])
                    if all_opts:
                        inner = soup.new_tag("ol")
                        inner["type"] = "A"
                        for opt in all_opts:
                            li2 = soup.new_tag("li")
                            li2.string = opt
                            inner.append(li2)
                        li.append(inner)
                    new_ol.append(li)

                el.insert_before(new_ol)
                for n in consumed:
                    n.extract()

                children = list(parent.children)
                try:
                    i = children.index(new_ol) + 1
                except ValueError:
                    i += 1
            else:
                i += 1

    def process_parent_simple_runs(parent):
        children = list(parent.children)
        i = 0
        while i < len(children):
            el = children[i]
            if not is_p(el):
                i += 1
                continue

            t = el.get_text(" ", strip=True)
            list_type = None
            is_letter_run = False
            first_text = None

            mnum = num_pat.match(t)
            mbullet = bullet_pat.match(t)
            mletter = letter_pat.match(t)

            if mnum:
                list_type = "ol"
                first_text = mnum.group(2)
            elif mbullet:
                list_type = "ul"
                first_text = mbullet.group(1)
            elif mletter:
                list_type = "ol"
                is_letter_run = True
                first_text = mletter.group(2)

            if not list_type:
                i += 1
                continue

            run = [(el, first_text)]
            j = i + 1
            while j < len(children):
                el2 = children[j]
                if not is_p(el2):
                    break
                t2 = el2.get_text(" ", strip=True)
                if list_type == "ol" and not is_letter_run:
                    m2 = num_pat.match(t2)
                    if not m2:
                        break
                    run.append((el2, m2.group(2)))
                elif list_type == "ol" and is_letter_run:
                    l2 = letter_pat.match(t2)
                    if not l2:
                        break
                    run.append((el2, l2.group(2)))
                else:
                    b2 = bullet_pat.match(t2)
                    if not b2:
                        break
                    run.append((el2, b2.group(1)))
                j += 1

            if len(run) >= min_items:
                new_list = soup.new_tag(list_type)
                new_list["class"] = ["text-hidden"]
                if list_type == "ol" and is_letter_run:
                    new_list["type"] = "A"
                for _, item_text in run:
                    li = soup.new_tag("li")
                    li.string = item_text
                    new_list.append(li)
                run[0][0].insert_before(new_list)
                for p_el, _ in run:
                    p_el.extract()
                children = list(parent.children)
                try:
                    i = children.index(new_list) + 1
                except ValueError:
                    i += 1
            else:
                i += 1

    for parent in [soup] + list(soup.find_all(True)):
        process_parent_for_numbered_with_nested(parent)
        process_parent_simple_runs(parent)

    return str(soup)


# ---------- Page number move to footer ----------

def move_page_number_to_footer(html_fragment: str) -> Tuple[str, str, Optional[str]]:
    soup = BeautifulSoup(html_fragment, 'html.parser')
    page_num: Optional[str] = None

    pn_divs = soup.find_all("div", class_="epub-page-number")
    if pn_divs:
        text = pn_divs[-1].get_text(" ", strip=True)
        nums = re.findall(r"(\d+)", text)
        if nums:
            page_num = nums[-1].lstrip("0") or "0"
        for d in pn_divs:
            d.decompose()

    for p in reversed(soup.find_all("p")):
        t = p.get_text(strip=True)
        if t.isdigit():
            sib = p.next_sibling
            trailing = True
            while sib:
                if hasattr(sib, "name") and sib.name:
                    if sib.name == "div" and "epub-page-number" in (sib.get("class") or []):
                        sib = sib.next_sibling
                        continue
                    if _is_ignorable_media_node(sib):
                        sib = sib.next_sibling
                        continue
                    trailing = False
                    break
                else:
                    if str(sib).strip() == "":
                        sib = sib.next_sibling
                        continue
                    trailing = False
                    break
            if trailing:
                if page_num is None:
                    page_num = t.lstrip("0") or "0"
                p.decompose()
                break

    all_p = soup.find_all("p")
    if all_p:
        last_p = all_p[-1]
        raw = last_p.get_text()
        m = re.search(r"\s(\d{1,6})\s*$", raw)
        if m:
            digits = m.group(1)
            new_text = re.sub(r"\s\d{1,6}\s*$", "", raw)
            last_p.clear()
            last_p.append(NavigableString(new_text))
            if page_num is None:
                page_num = digits.lstrip("0") or "0"

    footer_html = ""
    if page_num is not None:
        try:
            norm = str(int(page_num))
        except Exception:
            norm = page_num
        footer = BeautifulSoup("", "html.parser").new_tag("footer", role="contentinfo")
        p_tag = BeautifulSoup("", "html.parser").new_tag("p", **{"class": "text-hidden"})
        p_tag.string = f"Page Number {norm}"
        footer.append(p_tag)
        footer_html = str(footer)

    return str(soup), footer_html, page_num


# ---------- Text recompute from HTML fragment ----------

def recompute_text_from_html_fragment(html_fragment: str) -> str:
    soup = BeautifulSoup(html_fragment, 'html.parser')
    lines: List[str] = []

    def clean(t: str) -> str:
        return re.sub(r"\s+", " ", (t or "").strip())

    def text_without_nested_lists(li_tag) -> str:
        parts: List[str] = []
        for c in li_tag.contents:
            if isinstance(c, NavigableString):
                parts.append(str(c))
            else:
                name = getattr(c, "name", "")
                if name in ("ol", "ul", "table"):
                    continue
                parts.append(c.get_text(" ", strip=True))
        return clean(" ".join(parts))

    def dump_list(list_tag, indent: int = 0):
        is_ol = list_tag.name == "ol"
        ol_type = (list_tag.get("type") or "").upper() if is_ol else ""
        try:
            start = int(list_tag.get("start") or 1)
        except Exception:
            start = 1
        idx = start
        for li in list_tag.find_all("li", recursive=False):
            main_text = text_without_nested_lists(li)
            if is_ol:
                if ol_type == "A":
                    marker = f"{chr(ord('A') + idx - 1)}. "
                elif ol_type == "a":
                    marker = f"{chr(ord('a') + idx - 1)}. "
                else:
                    marker = f"{idx}. "
            else:
                marker = "- "
            if main_text:
                lines.append((" " * indent) + marker + main_text)
            for nested in li.find_all(["ol", "ul"], recursive=False):
                dump_list(nested, indent + 2)
            idx += 1

    def dump_table(table_tag, indent: int = 0):
        for tr in table_tag.find_all("tr", recursive=True):
            cells = []
            for cell in tr.find_all(["th", "td"], recursive=False):
                cells.append(clean(cell.get_text(" ", strip=True)))
            if any(cells):
                lines.append((" " * indent) + " | ".join(cells))

    for node in list(soup.contents):
        if isinstance(node, NavigableString) or isinstance(node, Comment):
            continue
        name = getattr(node, "name", "").lower()
        if not name:
            continue

        if name == "p":
            t = clean(node.get_text(" ", strip=True))
            if t:
                lines.append(t)

        elif name in ("ol", "ul"):
            dump_list(node, indent=0)

        elif name == "aside":
            p = node.find("p")
            if p:
                t = clean(p.get_text(" ", strip=True))
                if t:
                    lines.append(t)

        elif name == "table":
            dump_table(node, indent=0)

    return "\n\n".join(lines)


# ---------- H1 injection ----------

def inject_h1_for_runtime_match(fragment: str, candidates: List[str]) -> Tuple[str, Optional[str]]:
    soup = BeautifulSoup(fragment, 'html.parser')

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip()).lower()

    if not candidates:
        return str(soup), None

    h2s = soup.find_all("h2")
    cand_norms = [norm(c) for c in candidates]

    for h2 in h2s:
        h2_text = h2.get_text().strip()
        if norm(h2_text) in cand_norms:
            chosen = h2_text
            h2.decompose()
            return str(soup), chosen

    return str(soup), None


# ---------- insertion fragment & patching ----------

def build_insertion_fragment(hgroup_html: str, structured_content: str, footer_html: str) -> str:
    return f"<main role=\"main\">\n{hgroup_html}{structured_content}\n</main>\n{footer_html}"

def patch_source_file(original_path: Path, insertion_fragment_html: str, insert_after_id: str = "parent-p1", backup: bool = False) -> Tuple[bool, str]:
    """
    Safely insert insertion_fragment_html inside element with id=insert_after_id (as first children).
    If anchor not found, append to body. Sets aria-hidden="true" on all spans.
    Returns (patched_bool, note).
    """
    try:
        try:
            content = original_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = original_path.read_text(encoding="cp1252", errors="ignore")

        soup = BeautifulSoup(content, "html.parser")
        anchor = soup.find(id=insert_after_id)

        frag = BeautifulSoup(insertion_fragment_html, "html.parser")
        nodes = [n for n in list(frag.contents) if not (isinstance(n, NavigableString) and not str(n).strip())]

        if not nodes:
            return False, "Insertion fragment is empty"

        if anchor:
            # insert reversed so the first node becomes first child
            for node in reversed(nodes):
                anchor.insert(0, node)
            where_note = f"Inserted inside id='{insert_after_id}' (as first children)."
        else:
            target = soup.body or soup
            for node in nodes:
                target.append(node)
            where_note = f"Anchor id='{insert_after_id}' not found; appended to <body>."

        # Set aria-hidden="true" on all spans
        span_nodes = soup.find_all("span")
        total_spans = len(span_nodes)
        updated_spans = 0
        for sp in span_nodes:
            if sp.get("aria-hidden") != "true":
                sp["aria-hidden"] = "true"
                updated_spans += 1

        if backup:
            shutil.copy2(original_path, original_path.with_suffix(original_path.suffix + ".bak"))
        original_path.write_text(str(soup), encoding="utf-8")
        note = f"{where_note} Set aria-hidden='true' on {updated_spans}/{total_spans} <span> elements."
        return True, note

    except Exception as e:
        return False, f"Patch error: {type(e).__name__}: {e}"


# ---------- Table detection utilities (copied/adapted) ----------

NUM_RE = re.compile(r'[-+]?\d*\.?\d+')
NUM_LABEL_RE = re.compile(r'^\s*(\d{1,3})\s*(?:[.)])?\s*$')

def _num(val):
    m = NUM_RE.search(val or "")
    return float(m.group()) if m else None

def tokens_attr(tag, name):
    val = tag.get(name)
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return val.split()
    return []

def tidy_text(s):
    return " ".join((s or "").split())

def build_html_table(header, rows):
    out = []
    out.append("<table border='1' cellspacing='0' cellpadding='4'>")
    if header:
        out.append("  <thead><tr>" + "".join(f"<th>{ihtml.escape(h)}</th>" for h in header) + "</tr></thead>")
    out.append("  <tbody>")
    for r in rows:
        out.append("    <tr>" + "".join(f"<td>{ihtml.escape(c)}</td>" for c in r) + "</tr>")
    out.append("  </tbody>")
    out.append("</table>")
    return "\n".join(out)

def build_html_ol(items: List[str], start: Optional[int] = None, ol_type: Optional[str] = None) -> str:
    attrs = ["class='text-hidden'"]
    if start is not None and start != 1:
        attrs.append(f"start='{start}'")
    if ol_type:
        attrs.append(f"type='{ol_type}'")
    out = [f"<ol {' '.join(attrs)}>"]
    for it in items:
        out.append(f"  <li>{ihtml.escape(tidy_text(it))}</li>")
    out.append("</ol>")
    return "\n".join(out)

# For brevity the following table detection helpers are included exactly as needed by _build_ptable_stream_from_css
def extract_positions_from_css(css_text):
    import cssutils
    cssutils.log.setLevel(40)
    sheet = cssutils.parseString(css_text)
    positions = {}

    def handle_rule(rule):
        if getattr(rule, 'type', None) == rule.STYLE_RULE:
            left = top = bottom = None
            for prop in rule.style:
                n = prop.name.lower()
                if n == 'left':
                    left = _num(prop.value)
                elif n == 'top':
                    top = _num(prop.value)
                elif n == 'bottom':
                    bottom = _num(prop.value)
            if left is not None and (top is not None or bottom is not None):
                y_vis = top if top is not None else -bottom
                for sel in rule.selectorText.split(','):
                    s = sel.strip()
                    key = None
                    if s.startswith('#'):
                        key = s[1:]
                    elif s.startswith('.'):
                        key = s[1:]
                    else:
                        mid = re.search(r'#([A-Za-z0-9_-]+)', s)
                        mcl = re.search(r'\.([A-Za-z0-9_-]+)', s)
                        if mid:
                            key = mid.group(1)
                        elif mcl:
                            key = mcl.group(1)
                    if key:
                        positions[key] = (y_vis, left)

        if hasattr(rule, 'cssRules'):
            for r in rule.cssRules:
                handle_rule(r)

    for r in sheet.cssRules:
        handle_rule(r)
    return positions

def collect_tokens(html_str, css_text, allow_classes=None, parser="lxml"):
    pos = extract_positions_from_css(css_text)
    soup = BeautifulSoup(html_str, parser)
    toks = []
    for sp in soup.find_all('span'):
        txt = sp.get_text(strip=True)
        if not txt:
            continue
        classes = tokens_attr(sp, 'class')
        style_class = next((c for c in classes if isinstance(c, str) and c.startswith('styleid')), None)
        if allow_classes is not None and style_class not in allow_classes:
            continue
        key = sp.get('id')
        if key not in pos:
            for c in classes:
                if c in pos:
                    key = c
                    break
        if key not in pos:
            continue
        y, x = pos[key]
        toks.append({'y': y, 'x': x, 'cls': style_class, 'text': txt})
    toks.sort(key=lambda t: (t['y'], t['x']))
    for i, t in enumerate(toks):
        t['idx'] = i
    return toks

def group_by_y(tokens, y_tol=8.0):
    lines, cur = [], None
    for t in tokens:
        if cur is None or abs(t['y'] - cur['y']) > y_tol:
            cur = {'y': t['y'], 'items': [t]}
            lines.append(cur)
        else:
            cur['items'].append(t)
    for ln in lines:
        ln['items'].sort(key=lambda z: z['x'])
    return lines

def x_span(tokens):
    xs = [t['x'] for t in tokens]
    return (min(xs), max(xs), (max(xs) - min(xs)) if xs else 0.0)

# -- simplified detectors reusing original logic (only what's necessary) --
def detect_comparison_table(tokens, header_cls='styleid4', label_cls='styleid4', value_cls='styleid5', y_tol=8.0, min_rows=3):
    # (copied/adapted from original, returns dict or None)
    allow = {header_cls, label_cls, value_cls}
    toks = [t for t in tokens if t['cls'] in allow]
    if not toks:
        return None
    _, _, span = x_span(toks)
    if span <= 0:
        return None

    hdr_tokens = [t for t in toks if t['cls'] == header_cls]
    hdr_lines = [ln for ln in group_by_y(sorted(hdr_tokens, key=lambda z:(z['y'], z['x'])), y_tol=y_tol) if len(ln['items']) >= 3]
    if not hdr_lines:
        return None

    header_line = hdr_lines[0]
    items = sorted(header_line['items'], key=lambda z: z['x'])[:3]
    anchors = [c['x'] for c in items]
    header_texts = [tidy_text(c['text']) for c in items]
    used = {t['idx'] for t in items}

    gaps = [anchors[1]-anchors[0], anchors[2]-anchors[1]]
    if min(gaps) < 0.10 * span:
        return None

    lab_toks = [t for t in toks if t['cls'] == label_cls and t['y'] > header_line['y']]
    label_lines = group_by_y(lab_toks, y_tol=y_tol)
    if len(label_lines) < min_rows:
        return None

    def mid(a, b): return (a + b) / 2.0

    rows = []
    nonempty_both = 0
    for i, ln in enumerate(label_lines):
        y_label = ln['y']
        y_prev = label_lines[i-1]['y'] if i > 0 else y_label - 1e6
        y_next = label_lines[i+1]['y'] if i+1 < len(label_lines) else y_label + 1e6
        band_lo, band_hi = mid(y_prev, y_label), mid(y_label, y_next)

        band = [t for t in toks if band_lo <= t['y'] <= band_hi and t['y'] != header_line['y']]
        cols = {0: [], 1: [], 2: []}
        for t in band:
            j = min(range(3), key=lambda k: abs(t['x'] - anchors[k]))
            cols[j].append(t)

        def merge(ts, allowed_cls=None):
            keep = [z for z in ts if (allowed_cls is None or z['cls'] in allowed_cls)]
            keep.sort(key=lambda z: (z['y'], z['x']))
            for z in keep: used.add(z['idx'])
            return tidy_text(" ".join(z['text'] for z in keep))

        label = merge(cols[0], allowed_cls={label_cls})
        col1  = merge(cols[1], allowed_cls={value_cls})
        col2  = merge(cols[2], allowed_cls={value_cls})

        if any([label, col1, col2]):
            rows.append([label, col1, col2])
            if col1 and col2:
                nonempty_both += 1

    if len(rows) < min_rows or (nonempty_both / len(rows) < 0.6):
        return None

    ymin = min(tokens[i]['y'] for i in used) if used else None
    ymax = max(tokens[i]['y'] for i in used) if used else None
    html_table = build_html_table(header_texts, rows)
    return {'kind':'comparison_3col','header':header_texts,'rows':rows,'html':html_table,'used_idx':used,'ymin':ymin,'ymax':ymax}

def detect_fact_table(tokens, label_cls='styleid3', value_cls='styleid4', y_tol=6.0, lower_fudge=0.30, min_rows=3):
    allow = {label_cls, value_cls}
    toks = [t for t in tokens if t['cls'] in allow]
    if not toks:
        return None
    _, _, span = x_span(toks)
    if span <= 0:
        return None

    lines = group_by_y(toks, y_tol=y_tol)
    label_lines = []
    for ln in lines:
        labs = [it for it in ln['items'] if it['cls'] == label_cls]
        if labs:
            text = tidy_text(" ".join(x['text'] for x in sorted(labs, key=lambda z: z['x'])))
            lx = median([x['x'] for x in labs]) if labs else 0
            if text:
                label_lines.append({'y': ln['y'], 'text': text, 'x': lx, 'items': labs})
    if len(label_lines) < min_rows:
        return None

    spread = max(l['x'] for l in label_lines) - min(l['x'] for l in label_lines)
    if spread > 0.07 * span:
        return None

    def mid(a,b): return (a+b)/2.0

    used = set()
    rows = []
    value_x_all = []
    with_value = 0

    for k, lab in enumerate(label_lines):
        y_label = lab['y']
        for it in lab['items']:
            used.add(it['idx'])
        y_prev = label_lines[k-1]['y'] if k > 0 else None
        y_next = label_lines[k+1]['y'] if k+1 < len(label_lines) else None
        upper = mid(y_prev, y_label) if y_prev is not None else y_label - 1e6
        lower = mid(y_label, y_next) if y_next is not None else y_label + 1e6
        if y_next is not None:
            gap = y_next - y_label
            lower = lower + lower_fudge * max(0, gap)

        vals = []
        for ln in lines:
            if upper <= ln['y'] <= lower:
                vs = [it for it in ln['items'] if it['cls'] == value_cls]
                if vs:
                    vals.append(vs)
        text_segments = []
        for vs in vals:
            for it in vs:
                used.add(it['idx'])
                value_x_all.append(it['x'])
            seg = tidy_text(" ".join(it['text'] for it in sorted(vs, key=lambda z:z['x'])))
            if seg:
                text_segments.append(seg)

        value = tidy_text(" ".join(text_segments))
        if value:
            with_value += 1
        rows.append([lab['text'], value])

    if len(rows) < min_rows or (with_value / len(rows) < 0.6):
        return None
    if value_x_all:
        label_x_med = median(l['x'] for l in label_lines)
        value_x_med = median(value_x_all)
        if value_x_med - label_x_med < 0.15 * span:
            return None

    ymin = min(tokens[i]['y'] for i in used) if used else None
    ymax = max(tokens[i]['y'] for i in used) if used else None
    html_table = build_html_table(header=None, rows=rows)
    return {'kind':'fact_2col','header':None,'rows':rows,'html':html_table,'used_idx':used,'ymin':ymin,'ymax':ymax}

def resolve_overlaps(blocks):
    blocks = [b for b in blocks if b]
    if not blocks:
        return []
    blocks.sort(key=lambda b: len(b['used_idx']), reverse=True)
    chosen = []
    used_all = set()
    for b in blocks:
        overlap = len(b['used_idx'] & used_all) / max(1, len(b['used_idx']))
        if overlap < 0.3:
            chosen.append(b)
            used_all |= b['used_idx']
    return chosen

def render_page_with_tables(html_str, css_text, parser="lxml"):
    tokens_all = collect_tokens(html_str, css_text, allow_classes=None, parser=parser)
    if not tokens_all:
        return "<html><body><!-- no tokens --></body></html>"

    blocks = []
    try:
        blocks.append(detect_comparison_table(tokens_all))
    except Exception:
        pass
    try:
        blocks.append(detect_fact_table(tokens_all))
    except Exception:
        pass
    blocks = resolve_overlaps(blocks)

    # Convert fact_2col blocks that are numbered to <ol>
    def maybe_fact_to_ol(block):
        if not block or block.get('kind') != 'fact_2col':
            return None
        rows = block.get('rows') or []
        pairs = []
        for lab, val in rows:
            val = tidy_text(val)
            if not val:
                return None
            m = NUM_LABEL_RE.match(lab or "")
            if not m:
                return None
            pairs.append((int(m.group(1)), val))
        if len(pairs) < 3:
            return None
        start = pairs[0][0]
        for idx, (n, _) in enumerate(pairs):
            if n != start + idx:
                return None
        items = [v for _, v in pairs]
        return build_html_ol(items, start=start)

    for b in blocks:
        html_ol = maybe_fact_to_ol(b)
        if html_ol:
            b['html'] = html_ol
            b['kind'] = 'ol_from_fact'

    used = set()
    for b in blocks:
        used |= b['used_idx']

    non_table_tokens = [t for t in tokens_all if t['idx'] not in used]
    lines = group_by_y(non_table_tokens, y_tol=8.0)

    items = []
    for ln in lines:
        text = tidy_text(" ".join(x['text'] for x in ln['items']))
        if text:
            items.append({'y': ln['y'], 'html': f"<p>{ihtml.escape(text)}</p>"})
    for b in blocks:
        ymid = (b['ymin'] + b['ymax'])/2.0 if (b['ymin'] is not None and b['ymax'] is not None) else min(t['y'] for t in tokens_all)
        items.append({'y': ymid, 'html': b['html']})

    items.sort(key=lambda x: x['y'])
    out = ["<html><body>"]
    for it in items:
        out.append(it['html'])
    out.append("</body></html>")
    return "\n".join(out)

def read_linked_css(html_path: str, soup: BeautifulSoup) -> str:
    base = Path(html_path).parent
    css_texts = []
    for link in soup.find_all("link", rel=True):
        rels = tokens_attr(link, 'rel')
        if "stylesheet" in rels:
            href = link.get("href")
            if not href:
                continue
            css_path = (base / href).resolve()
            if css_path.exists():
                try:
                    css_texts.append(css_path.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    pass
    return "\n\n".join(css_texts)

def _read_inline_css_from_style_tags(soup: BeautifulSoup) -> str:
    css_chunks = []
    for st in soup.find_all("style"):
        try:
            txt = st.get_text() or ""
            if txt.strip():
                css_chunks.append(txt)
        except Exception:
            pass
    return "\n\n".join(css_chunks)

def _table_to_ol_if_numbered(tb: Any) -> Optional[str]:
    rows = []
    for tr in tb.find_all('tr'):
        cells = tr.find_all(['th', 'td'], recursive=False)
        if not cells:
            continue
        if any(c.name == 'th' for c in cells):
            continue
        if len(cells) < 2:
            continue
        left = tidy_text(cells[0].get_text(" ", strip=True))
        right = tidy_text(" ".join(c.get_text(" ", strip=True) for c in cells[1:]))
        if not right:
            continue
        m = NUM_LABEL_RE.match(left or "")
        if not m:
            return None
        rows.append((int(m.group(1)), right))
    if len(rows) < 3:
        return None
    start = rows[0][0]
    for idx, (n, _) in enumerate(rows):
        if n != start + idx:
            return None
    items = [r[1] for r in rows]
    return build_html_ol(items, start=start)

def _build_ptable_stream_from_dom(html_str: str) -> str:
    s = BeautifulSoup(html_str, 'html.parser')
    body = s.body or s
    parts: List[str] = []

    def sanitize_table(tb):
        clone = BeautifulSoup(str(tb), 'html.parser').table
        if clone:
            cls = clone.get('class') or []
            if 'text-hidden' not in cls:
                cls.append('text-hidden')
            clone['class'] = cls
            for attr in ('border', 'cellspacing', 'cellpadding', 'style', 'width', 'height'):
                if attr in clone.attrs:
                    del clone[attr]
            for el in clone.find_all(True, attrs={'style': True}):
                del el['style']
            return str(clone)
        return ""

    for tb in (body.find_all('table') or []):
        ol_html = _table_to_ol_if_numbered(tb)
        if ol_html:
            parts.append(ol_html)
            continue
        sanitized = sanitize_table(tb)
        if sanitized:
            parts.append(sanitized)

    return "\n".join(parts)


def _build_ptable_stream_from_css(input_path: Path) -> str:
    try:
        html_str = input_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return ""
    parser = "lxml"
    soup = BeautifulSoup(html_str, parser)
    css_ext = ""
    try:
        css_ext = read_linked_css(str(input_path), soup) or ""
    except Exception:
        pass
    css_inline = _read_inline_css_from_style_tags(soup)
    css_text = "\n\n".join(x for x in (css_ext, css_inline) if x.strip())
    try:
        page_html = render_page_with_tables(html_str, css_text, parser=parser)
    except Exception:
        return ""
    bs = BeautifulSoup(page_html, 'html.parser')
    for p in bs.find_all('p'):
        cls = p.get('class') or []
        if 'text-hidden' not in cls:
            cls.append('text-hidden')
        p['class'] = cls
    for tb in bs.find_all('table'):
        cls = tb.get('class') or []
        if 'text-hidden' not in cls:
            cls.append('text-hidden')
        tb['class'] = cls
        for attr in ('border','cellspacing','cellpadding','style','width','height'):
            if attr in tb.attrs:
                del tb[attr]
        for el in tb.find_all(True, attrs={'style': True}):
            del el['style']
    body = bs.body or bs
    return "".join(str(child) for child in body.children if getattr(child, 'name', None))


# ---------- Structured DOM mimic (no Playwright) ----------

def extract_structured_dom_from_html(html_src: str, feature_titles: Optional[List[str]] = None) -> str:
    """
    Simplified DOM extraction to mimic the original Playwright extraction:
    - Harvest <h2>, <p>, <img> from a container (#PageContainer or body).
    - If a <h2> or <p> matches feature_titles exactly (case-insensitive), treat as a feature and create an <aside>.
    - Short paragraphs with heading-like classes become <h2>.
    """
    feature_titles = feature_titles or []
    ft_norm = {re.sub(r"\s+", " ", (t or "").strip()).lower() for t in feature_titles if t and t.strip()}
    soup = BeautifulSoup(html_src, 'html.parser')
    container = soup.select_one('#PageContainer') or soup.select_one('.PageContainer') or soup.body or soup
    result_parts: List[str] = []

    def flatten_paragraph(p):
        return re.sub(r'\s+', ' ', (p.get_text(" ", strip=True) or "").strip())

    # Walk top-down, collecting structured elements
    for node in container.descendants:
        if isinstance(node, Comment) or isinstance(node, NavigableString):
            continue
        if not getattr(node, "name", None):
            continue
        tag = node.name.lower()
        text = flatten_paragraph(node)
        if tag in ("h2", "p"):
            # feature check: exact match to feature_titles
            if text and text.lower() in ft_norm:
                title = text.strip()
                # attempt to capture next sibling <p> text if present
                next_p = None
                sibling = node.next_sibling
                while sibling and (isinstance(sibling, NavigableString) and not str(sibling).strip()):
                    sibling = sibling.next_sibling
                if sibling and getattr(sibling, "name", "").lower() == "p":
                    next_p = flatten_paragraph(sibling)
                # compose aside
                aid = f"feat-{abs(hash(title)) % (10**8)}"
                phtml = next_p or ""
                aside_html = f'<aside aria-labelledby="{aid}"><h2 class="text-hidden" id="{aid}">{html_escape(title)}</h2><p class="text-hidden">{html_escape(phtml)}</p></aside>'
                result_parts.append(aside_html)
                continue

            if tag == "h2":
                if text:
                    result_parts.append(f'<h2 class="text-hidden">{html_escape(text)}</h2>')
                continue

            # p handling: short and heading-like -> h2
            if tag == "p":
                is_short = len(text) < 50
                has_heading_class = False
                classes = (node.get("class") or [])
                if any(c for c in classes if isinstance(c, str) and c.lower() in ("heading", "title")):
                    has_heading_class = True
                if is_short and has_heading_class:
                    if text:
                        result_parts.append(f'<h2 class="text-hidden">{html_escape(text)}</h2>')
                else:
                    if text:
                        result_parts.append(f'<p class="text-hidden">{html_escape(text)}</p>')
                continue

        if tag == "img":
            # build figure with alt copied into figcaption
            src = node.get("src") or ""
            alt = node.get("alt") or ""
            if src.strip():
                fig = f'<figure><img src="{html_escape(src)}" alt="{html_escape(alt)}"/><figcaption><p class="text-hidden">{html_escape(alt)}</p></figcaption></figure>'
                result_parts.append(fig)
            continue

    return "".join(result_parts)


# ---------- High-level process_file & process_folder ----------

def process_file(
    input_file: Path,
    output_folder: Path,
    feature_titles: Optional[List[str]] = None,
    h1_candidates: Optional[List[str]] = None,
    patch_enabled: bool = True,
    backup_enabled: bool = False
) -> Dict[str, Any]:
    """
    Process one HTML/XHTML file:
      - produce patched HTML (in-place if patch_enabled via patch_source_file),
      - produce a cleaned reading-order HTML and TXT into output_folder
      - return metadata dict used in Excel report
    """
    feature_titles = feature_titles or []
    h1_candidates = h1_candidates or []
    footer_text = None

    try:
        html_src = input_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        html_src = ""

    # 1) Extract structured DOM snippet (mimic Playwright extraction)
    structured_dom = extract_structured_dom_from_html(html_src, feature_titles=feature_titles)

    # 2) Build table stream using DOM or CSS
    dom_has_table = bool(BeautifulSoup(html_src or "", 'html.parser').find('table'))
    use_dom_tables_only = False
    if dom_has_table:
        ptable_stream = _build_ptable_stream_from_dom(html_src)
        use_dom_tables_only = True
    else:
        # fallback to css-based detection
        ptable_stream = _build_ptable_stream_from_css(input_file)

    # 3) Merge streams similar to original
    dom_soup = BeautifulSoup(structured_dom, 'html.parser')
    top_level_kept: List[str] = []
    for child in list(dom_soup.contents):
        if getattr(child, 'name', None) is None:
            continue
        tag = child.name.lower()
        if tag == 'p' and not use_dom_tables_only:
            # in original logic they removed p only when using CSS path; we keep p if we used DOM tables
            child.extract()
        else:
            top_level_kept.append(str(child))

    merged_stream = ""
    if ptable_stream.strip():
        merged_stream = "\n".join(top_level_kept + [ptable_stream])
    else:
        merged_stream = structured_dom

    # 4) Post-process: merges, lists, paragraphs end punctuation, footer
    merged_stream = merge_outside_p_after_aside(merged_stream)
    merged_stream = convert_paragraphs_to_lists(merged_stream)
    merged_stream = ensure_paragraphs_end_with_dot(merged_stream)
    merged_stream, footer_html, page_num = move_page_number_to_footer(merged_stream)
    footer_text = page_num or None

    # 5) Optional H1 injection
    merged_stream, chosen_h1 = inject_h1_for_runtime_match(merged_stream, h1_candidates)
    text_content = recompute_text_from_html_fragment(merged_stream)

    hgroup_html = ""
    if chosen_h1:
        hgroup_html = f"<hgroup>\n<h1 class=\"text-hidden\">{html_escape(chosen_h1)}</h1>\n</hgroup>\n"

    # 6) Compose final preview HTML (cleaned)
    styles = """
.text-hidd {
  position: absolute !important;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  border: 0;
  white-space: nowrap;
}
figure { margin: 20px auto; text-align: center; }
figure img { max-width: 100%; height: auto; display: inline-block; border: 1px solid #ddd; border-radius: 4px; }
figcaption p.text-hidden { margin: 0; }
aside { display: block; border: 1px solid #cfd8dc; border-radius: 6px; padding: 16px; margin: 24px 0; background: #f6fbff; }
"""

    page_title = BeautifulSoup(html_src, 'html.parser').title.string if BeautifulSoup(html_src, 'html.parser').title else input_file.name
    result_html = f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<title>EPUB Reading Order - {page_title}</title>
<style>{styles}</style>
</head>
<body>
<main role="main">
{hgroup_html}{merged_stream}
</main>
{footer_html}
</body>
</html>"""

    # 7) Write reading-order outputs
    output_folder.mkdir(parents=True, exist_ok=True)
    output_html = output_folder / f"{input_file.stem}-reading-order.html"
    output_txt = output_folder / f"{input_file.stem}-reading-order.txt"
    try:
        output_html.write_text(result_html, encoding='utf-8')
        output_txt.write_text(text_content, encoding='utf-8')
    except Exception:
        # fallback to latin-1 if some exotic encoding
        output_html.write_text(result_html, encoding='latin-1', errors='ignore')
        output_txt.write_text(text_content, encoding='latin-1', errors='ignore')

    # 8) Patch original source if requested (safe insertion)
    patched = False
    patch_note = ""
    if patch_enabled:
        insertion_fragment = build_insertion_fragment(hgroup_html, merged_stream, footer_html)
        patched, patch_note = patch_source_file(
            original_path=input_file,
            insertion_fragment_html=insertion_fragment,
            insert_after_id="parent-p1",
            backup=backup_enabled
        )

    # 9) Return report row info
    return {
        "input_file": str(input_file),
        "output_html": str(output_html),
        "output_txt": str(output_txt),
        "has_footer": bool(footer_html.strip()),
        "page_number": page_num or "",
        "patched": patched,
        "patch_note": patch_note
    }


def _collect_files(input_folder: Path) -> List[Path]:
    files = list(input_folder.rglob("*.xhtml")) + list(input_folder.rglob("*.html"))
    return sorted(files, key=lambda p: str(p).lower())


def process_folder(
    input_folder: Path,
    output_folder: Path,
    progress_callback: Optional[Callable[[int, int, str, str], None]] = None,
    feature_titles: Optional[List[str]] = None,
    h1_candidates: Optional[List[str]] = None,
    patch_enabled: bool = True,
    backup_enabled: bool = False,
    insert_after_id: str = "parent-p1"
) -> Optional[Path]:
    """
    Process all HTML/XHTML files inside input_folder, write outputs into output_folder.
    Returns path to Excel report (or None if no files).
    progress_callback(current, total, stage, filename) optional.
    """
    feature_titles = feature_titles or []
    h1_candidates = h1_candidates or []

    files = _collect_files(input_folder)
    total = len(files)
    rows: List[Dict[str, Any]] = []

    if total == 0:
        return None

    for idx, fp in enumerate(files, start=1):
        if progress_callback:
            try:
                progress_callback(idx-1, total, "Starting", fp.name)
            except Exception:
                pass
        info = process_file(
            input_file=fp,
            output_folder=output_folder,
            feature_titles=feature_titles,
            h1_candidates=h1_candidates,
            patch_enabled=patch_enabled,
            backup_enabled=backup_enabled
        )
        rows.append(info)
        if progress_callback:
            try:
                progress_callback(idx, total, "Processed", fp.name)
            except Exception:
                pass

    # Write Excel report
    try:
        df = pd.DataFrame([{
            "File": Path(r["input_file"]).name,
            "HasFooter": "Yes" if r["has_footer"] else "No",
            "PageNumber": r["page_number"],
            "HTMLPath": Path(r["output_html"]).name,
            "TXTPath": Path(r["output_txt"]).name,
            "Patched": "Yes" if r.get("patched") else "No",
            "PatchNote": r.get("patch_note","")
        } for r in rows])
        report_path = output_folder / "reading_report.xlsx"
        df.to_excel(report_path, index=False)
    except Exception:
        report_path = None

    return report_path
