# -*- coding: utf-8 -*-
"""CV PDF Maker - paste your CV JSON, download a clean PDF (Streamlit + WeasyPrint).

Flexible schema: the CV is a `header` plus an ordered list of `sections`, each with its own
title and type (text / entries / skills / list). The old fixed-key format (summary, experience,
education, military_service, ...) is still accepted and converted automatically.
"""
from __future__ import annotations

import ast
import html
import json
import re

import streamlit as st

try:
    from weasyprint import HTML

    WEASY_ERR = None
except Exception as exc:
    HTML = None
    WEASY_ERR = exc

import fitz  # PyMuPDF - rasterizes PDF pages for the preview only

st.set_page_config(page_title="CV PDF Maker", page_icon="📄", layout="wide")


# ==============================================================================
# INPUT PARSING (JSON, or a pasted Python dict like `master_cv_data = {...}`)
# ==============================================================================
def parse_input(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("Nothing to parse - paste your CV JSON first.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as json_err:
        candidate = re.sub(r"^\s*[A-Za-z_]\w*\s*=\s*", "", text)
        try:
            data = ast.literal_eval(candidate)
        except Exception:
            raise ValueError(
                f"Invalid JSON: {json_err.msg} (line {json_err.lineno}, column {json_err.colno})"
            ) from None
    if not isinstance(data, dict):
        raise ValueError("The top level of the CV data must be an object { ... }.")
    return data


def as_list(v) -> list:
    return v if isinstance(v, list) else []


def as_dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def lines_of(v) -> list[str]:
    """str -> [str]; list -> [str, ...]; anything else -> []"""
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [str(x) for x in as_list(v) if str(x).strip()]


# ==============================================================================
# ENTRY STANDARDISATION  (accepts many field aliases)
# ==============================================================================
def std_entry(e: dict) -> dict:
    parts = lines_of(e.get("subtitle_parts"))
    for k in ("subtitle", "institution"):
        parts += lines_of(e.get(k))
    details = lines_of(e.get("details")) + lines_of(e.get("gpa"))

    links = []
    raw_links = ([e["link"]] if e.get("link") else []) + as_list(e.get("links"))
    for l in raw_links:
        if isinstance(l, dict) and l.get("url"):
            links.append({"label": l.get("label") or l["url"], "url": l["url"]})
        elif isinstance(l, str) and l.strip():
            links.append({"label": l, "url": l})

    return {
        "title": str(e.get("title") or e.get("degree") or e.get("name") or ""),
        "date": str(e.get("date") or e.get("dates") or ""),
        "subtitle_parts": parts,
        "overview": str(e.get("overview") or e.get("description") or ""),
        "details": details,
        "links": links,
        "bullets": lines_of(e.get("bullets")),
        "_include": e.get("_include", True) is not False,
    }


def entry_to_json(se: dict) -> dict:
    out = {"title": se["title"]}
    if se["date"]:
        out["date"] = se["date"]
    if se["subtitle_parts"]:
        out["subtitle_parts"] = se["subtitle_parts"]
    if se["overview"]:
        out["overview"] = se["overview"]
    if se["details"]:
        out["details"] = se["details"]
    if len(se["links"]) == 1:
        out["link"] = se["links"][0]
    elif se["links"]:
        out["links"] = se["links"]
    if se["bullets"]:
        out["bullets"] = se["bullets"]
    if not se["_include"]:
        out["_include"] = False
    return out


def raw_entries(section) -> list[dict]:
    items = section if isinstance(section, list) else as_dict(section).get("entries", [])
    return [e for e in as_list(items) if isinstance(e, dict)]


# ==============================================================================
# NORMALISATION: legacy fixed-key format  ->  {"header": {...}, "sections": [...]}
# ==============================================================================
def normalize(data: dict) -> dict:
    header = as_dict(data.get("header"))
    if isinstance(data.get("sections"), list):
        return {"header": header, "sections": [s for s in data["sections"] if isinstance(s, dict)]}

    def conv(items):
        return [entry_to_json(std_entry(e)) for e in items]

    secs: list[dict] = []
    if str(data.get("summary") or "").strip():
        secs.append({"title": "Summary", "type": "text", "content": data["summary"]})
    if raw_entries(data.get("experience")):
        secs.append({"title": "Freelance / Project-Based Professional Experience", "type": "entries",
                     "entries": conv(raw_entries(data["experience"]))})
    pep = data.get("personal_engineering_projects")
    if raw_entries(pep):
        s = {"title": "Personal Engineering Projects", "type": "entries"}
        if as_dict(pep).get("note"):
            s["note"] = pep["note"]
        s["entries"] = conv(raw_entries(pep))
        secs.append(s)
    groups = []
    for key in ("team_capstone_project", "self_paced_training_projects", "academic_graduation_project"):
        if raw_entries(data.get(key)):
            groups.append({"label": as_dict(data.get(key)).get("label", ""),
                           "entries": conv(raw_entries(data[key]))})
    if groups:
        secs.append({"title": "Capstone, Academic & Training Projects", "type": "entries", "groups": groups})
    if raw_entries(data.get("professional_training")):
        secs.append({"title": "Professional Training", "type": "entries",
                     "entries": conv(raw_entries(data["professional_training"]))})
    certs = [c for c in as_list(data.get("certifications")) if isinstance(c, dict) and c.get("name")]
    if certs:
        secs.append({"title": "Certifications", "type": "list", "items": certs})
    if raw_entries(data.get("education")):
        secs.append({"title": "Education", "type": "entries", "entries": conv(raw_entries(data["education"]))})
    ms = as_dict(data.get("military_service"))
    if ms.get("status") or ms.get("date"):
        status = f"({ms['status']}) " if ms.get("status") else ""
        secs.append({"title": "Military Service", "type": "text", "content": f"{status}{ms.get('date', '')}".strip()})
    if as_list(data.get("technical_skills")):
        secs.append({"title": "Technical Skills", "type": "skills", "items": data["technical_skills"]})
    if str(data.get("spoken_languages") or "").strip():
        secs.append({"title": "Spoken Languages", "type": "text", "content": data["spoken_languages"]})
    return {"header": header, "sections": secs}


# ==============================================================================
# HTML / CSS ENGINE
# ==============================================================================
def protect_hyphens(text: str) -> str:
    out = []
    for tok in text.split(" "):
        out.append(tok.replace("-", "&#8209;") if "-" in tok and tok != "-" else tok)
    return " ".join(out)


def esc(text) -> str:
    return protect_hyphens(html.escape(str(text or "")))


CSS_TEMPLATE = """
@page { size: A4 portrait; margin: 0.32in {SIDE} 0.35in {SIDE}; }
@page :first { margin-top: 0.28in; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: {FONT}; color: #1a1a1a; font-size: 9.5pt; line-height: 1.24; hyphens: none; -webkit-hyphens: none; }
a { color: {ACCENT}; text-decoration: underline; text-underline-offset: 1.5pt; text-decoration-thickness: 0.6pt; }
.nowrap-link { white-space: nowrap; }
.header { text-align: center; margin-top: 0; margin-bottom: 9pt; }
.header .name { font-size: 20pt; font-weight: 700; color: {ACCENT}; line-height: 1.15; margin-bottom: 2pt; }
.header .role { font-size: 11pt; font-weight: 700; color: #1a1a1a; line-height: 1.2; margin-bottom: 3pt; }
.header .contact-line { font-size: 8.5pt; color: #595959; line-height: 1.3; }
.header .sep { margin: 0 5pt; color: #595959; }
.section { margin-bottom: 8.5pt; }
.section-heading { font-size: 11pt; font-weight: 700; color: {ACCENT}; text-transform: uppercase; letter-spacing: 0.2pt;
    padding-bottom: 2.5pt; border-bottom: 0.75pt solid #8a94a8; margin-bottom: 5pt; page-break-after: avoid; break-after: avoid; }
.sub-group-label { font-size: 9.5pt; font-weight: 700; color: {ACCENT}; margin-top: 4pt; margin-bottom: 3pt; page-break-after: avoid; break-after: avoid; }
.section-note { font-size: 8.5pt; color: #595959; margin-bottom: 4pt; }
.summary-text, .overview-text { font-size: 9.5pt; color: #1a1a1a; line-height: 1.25; margin-bottom: 2pt; }
.skill-row { font-size: 9pt; line-height: 1.25; margin-bottom: 2pt; color: #1a1a1a; }
.skill-cat { font-weight: 700; }
.entry { margin-bottom: 7pt; }
.entry-header { display: flex; justify-content: space-between; align-items: baseline; page-break-after: avoid; break-after: avoid; }
.entry-title { font-size: 10pt; font-weight: 700; color: #1a1a1a; }
.entry-date { font-size: 8.5pt; color: #595959; white-space: nowrap; margin-left: 10pt; }
.entry-subline { font-size: 8.5pt; color: #595959; line-height: 1.25; margin-top: 1pt; margin-bottom: 2.5pt; page-break-after: avoid; break-after: avoid; }
.entry-subline .sub-sep { margin: 0 4pt; }
.bullets { margin-top: 2pt; }
.bullet-item { font-size: 9.5pt; line-height: 1.24; color: #1a1a1a; margin-bottom: 2pt; padding-left: 14pt; text-indent: -9pt; }
.bullet-glyph { color: #000000; margin-right: 4pt; }
.detail-block { margin-top: 4pt; font-size: 9.5pt; line-height: 1.25; }
"""

FONTS = {
    "Sans-serif (Helvetica / Arial)": '"Helvetica Neue", Helvetica, Arial, "Liberation Sans", sans-serif',
    "Modern (Calibri)": 'Calibri, Carlito, "Segoe UI", "Liberation Sans", sans-serif',
    "Serif (Georgia / Times)": 'Georgia, "Times New Roman", "Liberation Serif", serif',
}


def build_css(accent: str, scale: float, font_stack: str, side_margin: float) -> str:
    css = (CSS_TEMPLATE.replace("{ACCENT}", accent).replace("{FONT}", font_stack)
           .replace("{SIDE}", f"{side_margin:.2f}in"))
    return re.sub(r"(\d+(?:\.\d+)?)pt", lambda m: f"{float(m.group(1)) * scale:.2f}pt", css)


def link_html(label: str, url: str) -> str:
    return f'<a class="nowrap-link" href="{html.escape(url)}">{html.escape(label)}</a>'


def render_entries(items: list[dict], max_bullets: int) -> str:
    sep = '<span class="sub-sep">|</span>'
    blocks = []
    for raw in items:
        e = std_entry(raw)
        if not e["_include"]:
            continue
        parts = [esc(p) for p in e["subtitle_parts"]] + [link_html(l["label"], l["url"]) for l in e["links"]]
        subline = f'<div class="entry-subline">{sep.join(parts)}</div>' if parts else ""
        overview = f'<div class="overview-text">{esc(e["overview"])}</div>' if e["overview"] else ""
        details = "".join(f'<div class="overview-text">{esc(x)}</div>' for x in e["details"])
        bl = e["bullets"][:max_bullets] if max_bullets else e["bullets"]
        bullets = ""
        if bl:
            rows = "".join(f'<div class="bullet-item"><span class="bullet-glyph">•</span>{esc(b)}</div>' for b in bl)
            bullets = f'<div class="bullets">{rows}</div>'
        blocks.append(
            f'<div class="entry"><div class="entry-header">'
            f'<div class="entry-title">{esc(e["title"])}</div>'
            f'<div class="entry-date">{html.escape(e["date"])}</div></div>'
            f"{subline}{overview}{details}{bullets}</div>"
        )
    return "\n".join(blocks)


def infer_type(sec: dict) -> str:
    if "entries" in sec or "groups" in sec:
        return "entries"
    items = sec.get("items")
    if isinstance(items, dict):
        return "skills"
    if isinstance(items, list) and items:
        if all(isinstance(i, dict) and "category" in i for i in items):
            return "skills"
        return "list"
    return "text"


TYPE_ALIASES = {"bullets": "list", "skill": "skills", "education": "entries", "experience": "entries",
                "projects": "entries", "paragraph": "text", "plain": "text"}


def render_section_body(sec: dict, max_bullets: int) -> str:
    typ = str(sec.get("type") or infer_type(sec)).lower()
    typ = TYPE_ALIASES.get(typ, typ)

    if typ == "entries":
        note = f'<div class="section-note">{esc(sec["note"])}</div>' if sec.get("note") else ""
        body = render_entries(as_list(sec.get("entries")), max_bullets)
        for g in as_list(sec.get("groups")):
            g = as_dict(g)
            inner = render_entries(as_list(g.get("entries")), max_bullets)
            if inner:
                label = g.get("label") or g.get("title") or ""
                lab_html = f'<div class="sub-group-label">{html.escape(str(label))}</div>' if label else ""
                body += lab_html + inner
        return note + body if body else ""

    if typ == "skills":
        items = sec.get("items")
        rows = list(items.items()) if isinstance(items, dict) else [
            (s.get("category"), s.get("items")) for s in as_list(items) if isinstance(s, dict)]
        out = []
        for cat, val in rows:
            val = ", ".join(map(str, val)) if isinstance(val, list) else (val or "")
            cat_html = f'<span class="skill-cat">{esc(cat)}:</span> ' if cat else ""
            out.append(f'<div class="skill-row">{cat_html}{esc(val)}</div>')
        return "\n".join(out)

    if typ == "list":
        parts = []
        for it in as_list(sec.get("items")):
            if isinstance(it, dict):
                text = str(it.get("name") or it.get("text") or it.get("title") or "")
                url = it.get("url") or ""
                lab = it.get("label") or url
                parts.append(html.escape(text) + (" " + link_html(lab, url) if url else ""))
            elif str(it).strip():
                parts.append(html.escape(str(it)))
        if not parts:
            return ""
        if sec.get("inline"):
            return f'<div class="detail-block">{" | ".join(parts)}</div>'
        return '<div class="detail-block">' + "<br>".join(
            f'<span class="bullet-glyph">•</span>{p}' for p in parts) + "</div>"

    # text
    content = sec.get("content", sec.get("text", ""))
    paras = lines_of(content)
    return "".join(f'<div class="summary-text">{esc(p)}</div>' for p in paras)


def build_html(norm: dict, css: str, max_bullets: int) -> str:
    sep = '<span class="sep">|</span>'
    h = norm["header"]
    bits = [html.escape(str(h[k])) for k in ("location", "phone") if h.get(k)]
    if h.get("email"):
        e = html.escape(str(h["email"]))
        bits.append(f'<a class="nowrap-link" href="mailto:{e}">{e}</a>')
    bits += [html.escape(x) for x in lines_of(h.get("contact"))]
    links = sep.join(
        link_html(l.get("label") or l["url"], l["url"])
        for l in as_list(h.get("links")) if isinstance(l, dict) and l.get("url")
    )
    body = [
        f'<div class="header"><div class="name">{html.escape(str(h.get("name", "")))}</div>'
        f'<div class="role">{html.escape(str(h.get("title", "")))}</div>'
        f'<div class="contact-line">{sep.join(bits)}</div>'
        + (f'<div class="contact-line">{links}</div>' if links else "") + "</div>"
    ]
    for sec in norm["sections"]:
        if sec.get("_include") is False or sec.get("hidden") is True:
            continue
        inner = render_section_body(sec, max_bullets)
        if inner:
            title = html.escape(str(sec.get("title") or ""))
            heading = f'<div class="section-heading">{title}</div>' if title else ""
            body.append(f'<div class="section">{heading}{inner}</div>')
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><style>{css}</style></head>'
            f'<body>{"".join(body)}</body></html>')


def make_pdf(html_str: str) -> tuple[bytes, int]:
    doc = HTML(string=html_str).render()
    return doc.write_pdf(), len(doc.pages)


def to_pngs(pdf: bytes, dpi: int = 120) -> list[bytes]:
    doc = fitz.open(stream=pdf, filetype="pdf")
    return [p.get_pixmap(dpi=dpi).tobytes("png") for p in doc]


# ==============================================================================
# SAMPLE + SCHEMA REFERENCE
# ==============================================================================
SAMPLE = {
    "header": {
        "name": "Jane Doe", "title": "Software Engineer", "location": "Cairo, Egypt",
        "phone": "0100 000 0000", "email": "jane@example.com",
        "links": [{"label": "github.com/janedoe", "url": "https://github.com/janedoe"}],
    },
    "sections": [
        {"title": "Summary", "type": "text",
         "content": "Backend engineer with experience building REST APIs."},
        {"title": "Experience", "type": "entries", "entries": [
            {"title": "Backend Developer", "date": "Jan. 2025 - Present",
             "subtitle_parts": ["Freelance, Remote", "C#, ASP.NET Core, SQL Server"],
             "overview": "Short overview of the role.",
             "bullets": ["Built secure REST APIs with JWT authentication."]}]},
        {"title": "Projects", "type": "entries", "groups": [
            {"label": "Personal Projects", "entries": [
                {"title": "Bank ATM System", "date": "2026",
                 "subtitle_parts": [".NET 10, PostgreSQL"],
                 "link": {"label": "github.com/janedoe/BankATM", "url": "https://github.com/janedoe/BankATM"},
                 "bullets": ["Modeled a two-phase cash-dispensing workflow."]}]}]},
        {"title": "Education & Military Service", "type": "entries", "entries": [
            {"title": "BSc in Computer Science", "date": "Sep. 2019 - Jun. 2023",
             "subtitle_parts": ["Helwan University"], "details": ["GPA: 3.63/4.0"]},
            {"title": "Military Service", "date": "Jul. 2023 - Dec. 2024",
             "subtitle_parts": ["Completed"]}]},
        {"title": "Certifications", "type": "list", "items": [
            {"name": "Machine Learning Specialization", "url": "https://example.com/cert"}]},
        {"title": "Technical Skills", "type": "skills", "items": [
            {"category": "Languages", "items": "C#, TypeScript, Python"},
            {"category": "Databases", "items": ["SQL Server", "PostgreSQL", "Redis"]}]},
        {"title": "Languages", "type": "list", "inline": True,
         "items": ["Arabic (Native)", "English (Professional)"]},
    ],
}

SCHEMA_HELP = """
**Shape:** `{ "header": {...}, "sections": [ {...}, {...} ] }` - sections render in the order you list them,
with whatever titles you choose. Put anything anywhere (e.g. *Military Service* as an entry inside *Education*).

**Section types** (`type` is optional - it is guessed from the keys):

| type | keys | renders |
|---|---|---|
| `text` | `content` (string or list of paragraphs) | plain paragraphs |
| `entries` | `entries` and/or `groups: [{label, entries}]`, optional `note` | title + date, sub-line, overview, bullets |
| `skills` | `items`: `[{category, items}]` or `{ "Languages": "C#, ..." }` (value may be a string or list) | bold-category rows |
| `list` | `items`: strings or `{name, url}`, optional `"inline": true` | bullet lines or one `|`-separated line |

**Entry fields** (all optional except a title): `title`, `date`, `subtitle_parts` (list) or `subtitle`,
`overview`, `details` (extra lines, e.g. GPA), `link` `{label,url}` or `links` `[...]`, `bullets`.
Aliases: `degree`→title, `institution`→sub-line, `gpa`→detail line, `description`→overview, `dates`→date.

**Hide without deleting:** `"_include": false` on any entry or section (or `"hidden": true` on a section).

**Header:** `name`, `title`, `location`, `phone`, `email`, `links`, and `contact` (list of extra contact strings).

**Old format** (`summary`, `experience`, `education`, `military_service`, `technical_skills`, ...) still works and is
converted automatically - after you generate, copy the converted JSON from the expander and edit it freely.
"""


# ==============================================================================
# UI
# ==============================================================================
st.markdown(
    """
<style>
.block-container { padding-top: 2rem; max-width: 1200px; }
.hero { background: linear-gradient(135deg, #1f3752 0%, #3b6fb6 100%); color: #fff;
        padding: 1.5rem 1.9rem; border-radius: 18px; margin-bottom: 1.2rem; }
.hero h1 { margin: 0; font-size: 1.9rem; color: #fff; padding: 0; }
.hero p { margin: .35rem 0 0; opacity: .88; }
div[data-testid="stMetric"] { background: rgba(128,128,128,.08); border-radius: 14px; padding: .8rem 1rem; }
button[kind="primary"], button[kind="secondary"] { border-radius: 10px; font-weight: 600; }
#MainMenu, footer { visibility: hidden; }
</style>
<div class="hero"><h1>📄 CV PDF Maker</h1>
<p>Paste your CV JSON, hit Generate, download a clean PDF. Sections and titles are fully up to you.</p></div>
""",
    unsafe_allow_html=True,
)

if WEASY_ERR is not None:
    st.error("WeasyPrint could not be loaded - system libraries are missing. "
             "Add a `packages.txt` (see the repo) and reboot the app.\n\n" f"Details: `{WEASY_ERR}`")
    st.stop()

left, right = st.columns([3, 2], gap="large")

with left:
    st.markdown("#### 1 · Paste your CV data")
    if "json_text" not in st.session_state:
        st.session_state["json_text"] = ""
    st.text_area(
        "CV JSON", key="json_text", height=430, label_visibility="collapsed",
        placeholder='{\n  "header": { "name": "...", "title": "..." },\n  "sections": [\n    { "title": "Summary", "content": "..." },\n    { "title": "Experience", "entries": [ ... ] }\n  ]\n}',
    )
    b1, b2, _ = st.columns([1.4, 1, 2.6])
    generate = b1.button("⚡ Generate PDF", type="primary")
    if b2.button("Load sample"):
        st.session_state["json_text"] = json.dumps(SAMPLE, indent=2, ensure_ascii=False)
        st.rerun()
    with st.expander("📘 Schema reference"):
        st.markdown(SCHEMA_HELP)

with right:
    st.markdown("#### 2 · Style (optional)")
    with st.container(border=True):
        accent = st.color_picker("Accent color", "#1f3752")
        font_name = st.selectbox("Font", list(FONTS))
        scale = st.slider("Text size", 0.85, 1.15, 1.0, 0.01, format="%.2fx")
        side_margin = st.slider("Side margins (inches)", 0.30, 0.90, 0.48, 0.02)
        max_bullets = st.slider("Max bullets per entry (0 = all)", 0, 8, 0)

if generate:
    try:
        data = parse_input(st.session_state["json_text"])
        norm = normalize(data)
        if not norm["sections"]:
            raise ValueError("No sections found. Add a \"sections\" list (see the Schema reference).")
        css = build_css(accent, scale, FONTS[font_name], side_margin)
        with st.spinner("Rendering PDF..."):
            pdf, pages = make_pdf(build_html(norm, css, max_bullets))
            pngs = to_pngs(pdf)
        name = re.sub(r"[^A-Za-z0-9]+", "_", str(norm["header"].get("name", ""))).strip("_") or "CV"
        st.session_state["result"] = {"pdf": pdf, "pages": pages, "pngs": pngs, "name": name,
                                      "norm": json.dumps(norm, indent=2, ensure_ascii=False)}
    except Exception as ex:
        st.session_state.pop("result", None)
        st.error(f"Could not generate the PDF: {ex}")

res = st.session_state.get("result")
if res:
    st.divider()
    st.markdown("#### 3 · Download")
    m1, m2, m3 = st.columns([1, 1, 2])
    m1.metric("Pages", res["pages"])
    m2.metric("File size", f'{len(res["pdf"]) / 1024:.0f} KB')
    with m3:
        st.write("")
        st.download_button("⬇️ Download PDF", res["pdf"], file_name=f'{res["name"]}_CV.pdf',
                           mime="application/pdf", type="primary")
    if res["pages"] > 2:
        st.warning("The CV runs over 2 pages - lower the text size or max bullets and generate again.")
    with st.expander("🔁 Your data in the flexible format (copy, edit, paste back)"):
        st.code(res["norm"], language="json")
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        for n, png in enumerate(res["pngs"], 1):
            st.image(png, caption=f'Page {n} of {res["pages"]}', width="stretch")