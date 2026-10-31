#!/usr/bin/env python3
"""Turn slide decks into Markdown and slide images for slide-lessons.

Reads .pptx/.pptm/.ppsx with the standard library only. Converts .ppt, .pps,
.odp and .key to .pptx with LibreOffice (soffice). Reads slide PDFs with
pdftotext (poppler). Renders slide images with LibreOffice + pdftoppm when
they are installed. Nothing is uploaded; the only network use is downloading
a Google Slides deck shared as "anyone with the link".

  extract_slides.py list <file|folder|google-slides-url> ...
      Print one TSV row per deck: name, format, slides, slides with notes,
      private hint, title, first slide titles. Plus totals. Reads only.

  extract_slides.py extract <file|folder|url> ... --out DIR [--private yes|no] [--no-images]
      Write DIR/text/<slug>.md (one section per slide: title, text, tables,
      chart data, SmartArt text, image alt text, speaker notes),
      DIR/slides/<slug>/NN.png and DIR/meta.json. Prints a JSON summary.

  extract_slides.py private DIR yes|no
      Record whether the decks in DIR are private (meta.json "private").
"""
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "dc": "http://purl.org/dc/elements/1.1/",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
}
R_ID = f"{{{NS['r']}}}id"
OOXML = {".pptx", ".pptm", ".ppsx"}
CONVERT = {".ppt", ".pps", ".odp", ".key"}
PDF = {".pdf"}
GOOGLE = re.compile(r"https://docs\.google\.com/presentation/d/([A-Za-z0-9_-]{20,})(?:[/?#].*)?")
MAX_FILE = 300 * 1024 * 1024        # biggest deck we accept or download
MAX_PART = 50 * 1024 * 1024         # biggest single XML part we read from a deck
MAX_PARTS = 20000                   # zip entries; real decks have a few hundred
SKIP_PH = {"sldNum", "dt", "ftr", "hdr", "sldImg"}
TITLE_PH = {"title", "ctrTitle"}
PRIVATE_WORDS = re.compile(r"\b(confidential|internal only|internal use|proprietary|do not (distribute|share|forward)|nda|restricted|draft)\b", re.I)
TIMEOUT = 300
GENERIC_TITLES = {"presentation", "powerpoint presentation", "untitled", "untitled presentation", "slide 1"}


# ---------- inputs ----------

def expand(inputs):
    """Files, folders (recursive) and Google Slides URLs -> list of sources."""
    found = []
    for raw in inputs:
        if raw.startswith("http://") or raw.startswith("https://"):
            match = GOOGLE.fullmatch(raw)
            if not match:
                sys.exit(f"not a Google Slides link: {raw}")
            found.append({"kind": "google", "id": match.group(1), "name": f"google-{match.group(1)[:10]}", "source": raw})
            continue
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not child.name.startswith((".", "~$")) and kind_of(child):
                    found.append({"kind": kind_of(child), "path": child, "name": child.name, "source": str(child)})
        elif path.is_file():
            if not kind_of(path):
                sys.exit(f"unsupported file type: {path.name} (pptx, ppt, odp, key, pdf)")
            found.append({"kind": kind_of(path), "path": path, "name": path.name, "source": str(path)})
        else:
            sys.exit(f"no such file or folder: {raw}")
    for item in found:
        if "path" in item and item["path"].stat().st_size > MAX_FILE:
            sys.exit(f"{item['name']} is larger than {MAX_FILE // 2**20} MB")
    return found


def kind_of(path):
    suffix = path.suffix.lower()
    if suffix in OOXML:
        return "pptx"
    if suffix in CONVERT:
        return "convert"
    if suffix in PDF:
        return "pdf"
    return None


def download_google(item, workdir):
    url = f"https://docs.google.com/presentation/d/{item['id']}/export/pptx"
    request = urllib.request.Request(url, headers={"User-Agent": "slide-lessons"})
    target = workdir / f"{item['id']}.pptx"
    try:
        with urllib.request.urlopen(request, timeout=120) as response, open(target, "wb") as out:
            if "html" in response.headers.get("Content-Type", ""):
                sys.exit("Google Slides returned a web page, not a deck. Share it as 'Anyone with the link' "
                         "or download it as .pptx and pass the file.")
            size = 0
            while chunk := response.read(1 << 20):
                size += len(chunk)
                if size > MAX_FILE:
                    sys.exit(f"Google Slides deck is larger than {MAX_FILE // 2**20} MB")
                out.write(chunk)
    except urllib.error.HTTPError as err:
        sys.exit(f"Google Slides download failed ({err.code}). Is the deck shared as 'Anyone with the link'?")
    except urllib.error.URLError as err:
        sys.exit(f"Could not reach Google Slides: {err.reason}")
    if not zipfile.is_zipfile(target):
        sys.exit("Google Slides did not return a .pptx file.")
    return target


def soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


def run_soffice(args, workdir):
    """Run LibreOffice headless with a throwaway profile, so an open LibreOffice window doesn't block it."""
    exe = soffice()
    if not exe:
        return False
    profile = (workdir / "lo-profile").as_uri()
    try:
        subprocess.run([exe, f"-env:UserInstallation={profile}", "--headless", "--norestore", *args],
                       cwd=workdir, capture_output=True, timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return False
    return True


def to_pptx(path, workdir):
    if not soffice():
        sys.exit(f"{path.name} needs LibreOffice to convert it. Install it (sudo apt install libreoffice-impress, "
                 "brew install --cask libreoffice, or libreoffice.org), or save the deck as .pptx yourself.")
    out = workdir / "converted"
    out.mkdir(exist_ok=True)
    run_soffice(["--convert-to", "pptx", "--outdir", str(out), str(path)], workdir)
    result = out / (path.stem + ".pptx")
    if not result.exists():
        sys.exit(f"LibreOffice could not convert {path.name}. Newer Keynote files often fail; export to .pptx from Keynote.")
    return result


def local_pptx(item, workdir):
    """Return a .pptx path for an OOXML, convertible or Google source (None for PDFs)."""
    if item["kind"] == "google":
        return download_google(item, workdir)
    if item["kind"] == "convert":
        return to_pptx(item["path"], workdir)
    if item["kind"] == "pptx":
        return item["path"]
    return None


# ---------- pptx reading ----------

class Deck:
    def __init__(self, path):
        try:
            self.zip = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            sys.exit(f"{path.name} is not a valid .pptx file")
        infos = self.zip.infolist()
        if len(infos) > MAX_PARTS:
            sys.exit(f"{path.name} has {len(infos)} parts; refusing (possible zip bomb)")
        self.sizes = {info.filename: info.file_size for info in infos}

    def xml(self, name):
        if name not in self.sizes:
            return None
        if self.sizes[name] > MAX_PART:
            sys.exit(f"part {name} is over {MAX_PART // 2**20} MB uncompressed; refusing (possible zip bomb)")
        data = self.zip.read(name)
        if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            sys.exit(f"part {name} declares a DTD or entities; refusing to parse it")
        return ET.fromstring(data)

    def rels(self, part):
        folder, base = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", base + ".rels"))
        found = {}
        for rel in (root if root is not None else []):
            if rel.get("TargetMode") == "External":
                continue
            target = posixpath.normpath(posixpath.join(folder, rel.get("Target", "")))
            found[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], target.lstrip("/"))
        return found

    def slide_parts(self):
        pres = self.xml("ppt/presentation.xml")
        if pres is None:
            sys.exit("not a PowerPoint deck (no ppt/presentation.xml)")
        rels = self.rels("ppt/presentation.xml")
        return [rels[sid.get(R_ID)][1] for sid in pres.iterfind("p:sldIdLst/p:sldId", NS) if sid.get(R_ID) in rels]

    def core(self):
        root = self.xml("docProps/core.xml")
        if root is None:
            return {}
        pick = lambda tag: (root.findtext(tag, default="", namespaces=NS) or "").strip()
        return {"title": pick("dc:title"), "author": pick("dc:creator")}


def para_text(para):
    bits = []
    for node in para.iter():
        if node.tag == f"{{{NS['a']}}}t" and node.text:
            bits.append(node.text)
        elif node.tag == f"{{{NS['a']}}}br":
            bits.append(" ")
    return re.sub(r"\s+", " ", "".join(bits)).strip()


def body_lines(tx_body):
    lines = []
    for para in tx_body.iterfind("a:p", NS):
        text = para_text(para)
        if text:
            ppr = para.find("a:pPr", NS)
            level = int(ppr.get("lvl", 0)) if ppr is not None else 0
            lines.append((level, text))
    return lines


def cell(text):
    return text.replace("|", "\\|") or " "


def md_table(rows):
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(cell(c) for c in rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows[1:]]
    return out


def points(node):
    values = {}
    if node is None:
        return values
    for pt in node.iter(f"{{{NS['c']}}}pt"):
        values[int(pt.get("idx", 0))] = (pt.findtext("c:v", default="", namespaces=NS) or "").strip()
    return values


def chart_lines(deck, part):
    root = deck.xml(part)
    if root is None:
        return []
    title = " ".join(t.text for t in root.iterfind("c:chart/c:title//a:t", NS) if t.text).strip()
    plot = root.find("c:chart/c:plotArea", NS)
    kinds = [child.tag.split("}")[1] for child in (plot if plot is not None else []) if child.tag.endswith("Chart")]
    series = []
    for ser in root.iter(f"{{{NS['c']}}}ser"):
        name = " ".join(v.text for v in ser.iterfind("c:tx//c:v", NS) if v.text) or f"Series {len(series) + 1}"
        cats = points(ser.find("c:cat", NS)) or points(ser.find("c:xVal", NS))
        vals = points(ser.find("c:val", NS)) or points(ser.find("c:yVal", NS))
        series.append((name, cats, vals))
    head = f"**Chart: {title or 'untitled'}** ({', '.join(kinds) or 'chart'})"
    if not series:
        return [head]
    index = sorted(set().union(*(s[1].keys() | s[2].keys() for s in series)))
    rows = [["category"] + [s[0] for s in series]]
    for i in index:
        label = next((s[1][i] for s in series if i in s[1]), str(i + 1))
        rows.append([label] + [s[2].get(i, "") for s in series])
    return [head, ""] + md_table(rows)


def smartart_lines(deck, rels, frame):
    ids = frame.find(".//dgm:relIds", NS)
    if ids is None:
        return []
    target = rels.get(ids.get(f"{{{NS['r']}}}dm"))
    root = deck.xml(target[1]) if target else None
    if root is None:
        return []
    texts = [para_text(p) for p in root.iter(f"{{{NS['a']}}}p")]
    return ["**Diagram (SmartArt):** " + " → ".join(t for t in texts if t)]


def shape_lines(deck, rels, node, slide):
    """Walk a shape tree in reading order, filling slide['title'] and slide['lines']."""
    for child in node:
        tag = child.tag.split("}")[1]
        if tag == "sp":
            ph = child.find("p:nvSpPr/p:nvPr/p:ph", NS)
            ph_type = ph.get("type", "body") if ph is not None else None
            if ph_type in SKIP_PH:
                continue
            tx = child.find("p:txBody", NS)
            lines = body_lines(tx) if tx is not None else []
            if ph_type in TITLE_PH and not slide["title"]:
                slide["title"] = " ".join(t for _, t in lines)
            else:
                slide["lines"] += ["  " * level + "- " + text for level, text in lines]
        elif tag == "grpSp":
            shape_lines(deck, rels, child, slide)
        elif tag == "pic":
            info = child.find("p:nvPicPr/p:cNvPr", NS)
            alt = (info.get("descr") or "").strip() if info is not None else ""
            media = child.find("p:nvPicPr/p:nvPr/a:videoFile", NS) is not None or \
                any(node.tag.endswith("}media") for node in child.iterfind("p:nvPicPr/p:nvPr//*", NS))
            label = "video" if media else "image"
            slide["images"] += 1
            slide["lines"].append(f"[{label}: {alt}]" if alt else f"[{label}]")
        elif tag == "graphicFrame":
            table = child.find(".//a:tbl", NS)
            chart = child.find(".//c:chart", NS)
            if table is not None:
                rows = [[" ".join(para_text(p) for p in tc.iterfind(".//a:p", NS)).strip() for tc in tr.iterfind("a:tc", NS)]
                        for tr in table.iterfind("a:tr", NS)]
                slide["lines"] += [""] + md_table(rows) + [""]
            elif chart is not None and chart.get(R_ID) in rels:
                slide["lines"] += [""] + chart_lines(deck, rels[chart.get(R_ID)][1]) + [""]
            else:
                slide["lines"] += smartart_lines(deck, rels, child)


def notes_text(deck, part):
    root = deck.xml(part)
    if root is None:
        return ""
    paras = []
    for sp in root.iter(f"{{{NS['p']}}}sp"):
        ph = sp.find("p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is None or ph.get("type", "body") != "body":
            continue
        tx = sp.find("p:txBody", NS)
        if tx is not None:
            paras += [para_text(p) for p in tx.iterfind("a:p", NS)]
    return "\n".join(p for p in paras if p).strip()


def read_pptx(path):
    deck = Deck(path)
    slides = []
    for n, part in enumerate(deck.slide_parts(), 1):
        root = deck.xml(part)
        if root is None:
            continue
        rels = deck.rels(part)
        slide = {"n": n, "title": "", "lines": [], "images": 0, "hidden": root.get("show") == "0", "notes": ""}
        tree = root.find("p:cSld/p:spTree", NS)
        if tree is not None:
            shape_lines(deck, rels, tree, slide)
        notes = next((target for kind, target in rels.values() if kind == "notesSlide"), None)
        if notes:
            slide["notes"] = notes_text(deck, notes)
        slides.append(slide)
    return deck.core(), slides


# ---------- pdf reading ----------

def need(tool, why):
    if not shutil.which(tool):
        sys.exit(f"{why} needs {tool} (poppler-utils): sudo apt install poppler-utils, or brew install poppler")


def read_pdf(path):
    need("pdftotext", "Reading slide PDFs")
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=60).stdout if shutil.which("pdfinfo") else ""
    meta = dict(line.split(":", 1) for line in info.splitlines() if ":" in line)
    text = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
                          capture_output=True, text=True, timeout=TIMEOUT, check=True).stdout
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    slides = []
    for n, page in enumerate(pages, 1):
        lines = [re.sub(r"\s{3,}", "   ", line).strip() for line in page.splitlines()]
        lines = [line for line in lines if line]
        slides.append({"n": n, "title": lines[0] if lines else "", "lines": lines[1:], "images": 0, "hidden": False, "notes": ""})
    core = {"title": meta.get("Title", "").strip(), "author": meta.get("Author", "").strip()}
    return core, slides


# ---------- images ----------

def render(pptx, pdf, slides, dest, workdir):
    """Write dest/NN.png for each slide. Returns a warning string or None."""
    if not shutil.which("pdftoppm"):
        return "no slide images: install poppler-utils (pdftoppm)"
    if pptx is not None:
        if not soffice():
            return "no slide images: install LibreOffice to render .pptx slides"
        out = workdir / "pdf"
        out.mkdir(exist_ok=True)
        options = json.dumps({"ExportHiddenSlides": {"type": "boolean", "value": "true"}})
        run_soffice(["--convert-to", f"pdf:impress_pdf_Export:{options}", "--outdir", str(out), str(pptx)], workdir)
        pdf = out / (Path(pptx).stem + ".pdf")
        if not pdf.exists():
            return "no slide images: LibreOffice could not export the deck to PDF"
    pages = workdir / "pages"
    shutil.rmtree(pages, ignore_errors=True)
    pages.mkdir()
    subprocess.run(["pdftoppm", "-png", "-scale-to", "1280", str(pdf), str(pages / "p")],
                   capture_output=True, timeout=TIMEOUT, check=False)
    files = sorted(pages.glob("p-*.png"), key=lambda f: int(f.stem.split("-")[1]))
    visible = [s["n"] for s in slides if not s["hidden"]]
    if len(files) == len(slides):
        numbers = [s["n"] for s in slides]
    elif len(files) == len(visible):
        numbers = visible
    else:
        return f"no slide images: rendered {len(files)} pages for {len(slides)} slides, can't match them up"
    dest.mkdir(parents=True, exist_ok=True)
    for number, file in zip(numbers, files):
        shutil.move(str(file), dest / f"{number:02d}.png")
    return None


# ---------- output ----------

def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "deck"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def markdown(title, item, core, slides):
    with_notes = sum(1 for s in slides if s["notes"])
    out = [f"# {title}", "", f"Source: {item['name']} · {len(slides)} slides · {with_notes} with speaker notes"
           + (f" · by {core['author']}" if core.get("author") else "") + f" · extracted {date.today().isoformat()}", ""]
    for s in slides:
        out.append(f"## Slide {s['n']}: {s['title'] or '(no title)'}")
        if s["hidden"]:
            out.append("_(hidden slide)_")
        out.append("")
        out += s["lines"] or ["_(no text on this slide)_"]
        if s["notes"]:
            out += ["", "**Speaker notes:**", ""] + ["> " + line for line in s["notes"].splitlines()]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def deck_title(core, slides, item):
    title = core.get("title", "")
    if not title or title.lower() in GENERIC_TITLES:
        title = next((s["title"] for s in slides if s["title"]), "") or Path(item["name"]).stem
    return title


def private_hint(core, slides):
    blob = " ".join([core.get("title", "")] + [s["title"] + " " + " ".join(s["lines"]) + " " + s["notes"] for s in slides])
    hits = sorted({m.group(0).lower() for m in PRIVATE_WORDS.finditer(blob)})
    return ", ".join(hits)


def read_any(item, workdir):
    pptx = local_pptx(item, workdir)
    if pptx is not None:
        core, slides = read_pptx(pptx)
    else:
        core, slides = read_pdf(item["path"])
    return pptx, core, slides


def cmd_list(inputs):
    items = expand(inputs)
    total = notes = 0
    print("name\tformat\tslides\twith_notes\tprivate_hint\ttitle\tfirst_slides")
    for item in items:
        with tempfile.TemporaryDirectory() as tmp:
            if item["kind"] in ("google", "convert"):
                print(f"{item['name']}\t{item['kind']}\t?\t?\t?\t\t(needs {'download' if item['kind'] == 'google' else 'conversion'})")
                continue
            _, core, slides = read_any(item, Path(tmp))
        with_notes = sum(1 for s in slides if s["notes"])
        total, notes = total + len(slides), notes + with_notes
        firsts = " / ".join(s["title"] for s in slides if s["title"])[:160]
        print(f"{item['name']}\t{item['kind']}\t{len(slides)}\t{with_notes}\t{private_hint(core, slides)}\t{deck_title(core, slides, item)}\t{firsts}")
    print(f"# {len(items)} decks, {total} slides known, {notes} with speaker notes", file=sys.stderr)


def load_meta(out_dir):
    path = out_dir / "meta.json"
    return path, (json.loads(path.read_text()) if path.exists() else {"decks": [], "private": None})


def cmd_extract(inputs, opts):
    if not opts["out"]:
        sys.exit("--out DIR is required")
    out_dir = opts["out"]
    items = expand(inputs)
    meta_path, meta = load_meta(out_dir)
    taken = {d["slug"] for d in meta["decks"]}
    written = []
    for item in items:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            pptx, core, slides = read_any(item, workdir)
            title = deck_title(core, slides, item)
            slug = base = slugify(Path(item["name"]).stem if item["kind"] != "google" else title)
            known = next((d for d in meta["decks"] if d["source"] == item["source"]), None)
            if known:
                slug = known["slug"]
            else:
                n = 2
                while slug in taken:
                    slug, n = f"{base}-{n}", n + 1
                taken.add(slug)
            text_path = out_dir / "text" / f"{slug}.md"
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(markdown(title, item, core, slides))
            warning = None
            image_dir = out_dir / "slides" / slug
            if not opts["no_images"]:
                warning = render(pptx, item.get("path") if pptx is None else None, slides, image_dir, workdir)
            images = sorted(p.name for p in image_dir.glob("*.png")) if image_dir.exists() else []
            entry = {
                "slug": slug, "title": title, "author": core.get("author", ""), "source": item["source"],
                "format": Path(item["name"]).suffix.lower().lstrip(".") or item["kind"],
                "sha256": sha256(pptx or item["path"]), "slides": len(slides),
                "hidden": [s["n"] for s in slides if s["hidden"]],
                "with_notes": sum(1 for s in slides if s["notes"]),
                "text": f"text/{slug}.md", "images": f"slides/{slug}/" if images else None,
            }
            meta["decks"] = [d for d in meta["decks"] if d["slug"] != slug] + [entry]
            written.append({"slug": slug, "title": title, "slides": len(slides), "text": str(text_path),
                            "images": len(images), "private_hint": private_hint(core, slides), "warning": warning})
    if opts["private"] is not None:
        meta["private"] = opts["private"]
    meta["extracted"] = date.today().isoformat()
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"written": written, "private": meta.get("private")}, indent=2, ensure_ascii=False))


def cmd_private(folder, value):
    meta_path, meta = load_meta(Path(folder).expanduser())
    if not meta_path.exists():
        sys.exit(f"no meta.json in {folder}")
    meta["private"] = value == "yes"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(f"private = {meta['private']}")


def parse_args(argv):
    opts = {"out": None, "private": None, "no_images": False}
    inputs, i = [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--out":
            opts["out"], i = Path(argv[i + 1]).expanduser(), i + 2
        elif arg == "--private":
            if argv[i + 1] not in ("yes", "no"):
                sys.exit("--private takes yes or no")
            opts["private"], i = argv[i + 1] == "yes", i + 2
        elif arg == "--no-images":
            opts["no_images"], i = True, i + 1
        elif arg == "--":
            i += 1
        else:
            inputs.append(arg)
            i += 1
    return inputs, opts


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    command = sys.argv[1]
    if command == "private" and len(sys.argv) == 4 and sys.argv[3] in ("yes", "no"):
        cmd_private(sys.argv[2], sys.argv[3])
        sys.exit(0)
    args, options = parse_args(sys.argv[2:])
    if command == "list" and args:
        cmd_list(args)
    elif command == "extract" and args:
        cmd_extract(args, options)
    else:
        sys.exit(__doc__)
