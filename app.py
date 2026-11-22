# -*- coding: utf-8 -*-
"""CV PDF Maker - paste your CV JSON, download a clean PDF (Streamlit + WeasyPrint)."""
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
# INPUT PARSING (accepts JSON, or a pasted Python dict like `master_cv_data = {...}`)
# ==============================================================================
def parse_input(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("Nothing to parse - paste your CV JSON first.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as json_err:
        # Fallback: Python-style dict (None/True/False, trailing commas, adjacent string literals)
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


def as_list(v) -> list:
    return v if isinstance(v, list) else []


def as_dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def entries(section) -> list[dict]:
    """A section is either a list of entries or {'label':..., 'entries': [...]}."""
    items = section if isinstance(section, list) else as_dict(section).get("entries", [])
    return [e for e in as_list(items) if isinstance(e, dict) and e.get("_include", True) is not False]


def render_entry_list(items: list[dict], max_bullets: int) -> str:
    sep = '<span class="sub-sep">|</span>'
    blocks = []
    for it in items:
        parts = [esc(p) for p in as_list(it.get("subtitle_parts")) if str(p).strip()]
        lnk = as_dict(it.get("link"))
        if lnk.get("url"):
            label = lnk.get("label") or lnk["url"]
            parts.append(f'<a class="nowrap-link" href="{html.escape(lnk["url"])}">{html.escape(label)}</a>')
        subline = f'<div class="entry-subline">{sep.join(parts)}</div>' if parts else ""
        overview = f'<div class="overview-text">{esc(it["overview"])}</div>' if it.get("overview") else ""
        bl = [b for b in as_list(it.get("bullets")) if str(b).strip()]
        if max_bullets:
            bl = bl[:max_bullets]
        bullets = ""
        if bl:
            rows = "".join(f'<div class="bullet-item"><span class="bullet-glyph">•</span>{esc(b)}</div>' for b in bl)
            bullets = f'<div class="bullets">{rows}</div>'
        blocks.append(
            f'<div class="entry"><div class="entry-header">'
            f'<div class="entry-title">{esc(it.get("title"))}</div>'
            f'<div class="entry-date">{html.escape(str(it.get("date") or ""))}</div></div>'
            f"{subline}{overview}{bullets}</div>"
        )
    return "\n".join(blocks)


def section(heading: str, body: str) -> str:
    return f'<div class="section"><div class="section-heading">{heading}</div>{body}</div>'


def build_html(d: dict, css: str, max_bullets: int) -> str:
    sep = '<span class="sep">|</span>'
    h = as_dict(d.get("header"))
    bits = [html.escape(str(h[k])) for k in ("location", "phone") if h.get(k)]
    if h.get("email"):
        e = html.escape(str(h["email"]))
        bits.append(f'<a class="nowrap-link" href="mailto:{e}">{e}</a>')
    links = sep.join(
        f'<a class="nowrap-link" href="{html.escape(l["url"])}">{html.escape(l.get("label") or l["url"])}</a>'
        for l in as_list(h.get("links")) if isinstance(l, dict) and l.get("url")
    )
    body = [
        f'<div class="header"><div class="name">{html.escape(str(h.get("name", "")))}</div>'
        f'<div class="role">{html.escape(str(h.get("title", "")))}</div>'
        f'<div class="contact-line">{sep.join(bits)}</div>'
        + (f'<div class="contact-line">{links}</div>' if links else "") + "</div>"
    ]

    if str(d.get("summary") or "").strip():
        body.append(section("Summary", f'<div class="summary-text">{esc(d["summary"])}</div>'))

    exp = entries(d.get("experience"))
    if exp:
        body.append(section("Freelance / Project-Based Professional Experience", render_entry_list(exp, max_bullets)))

    pep = entries(d.get("personal_engineering_projects"))
    if pep:
        note = as_dict(d.get("personal_engineering_projects")).get("note")
        note_html = f'<div class="section-note">{esc(note)}</div>' if note else ""
        body.append(section("Personal Engineering Projects", note_html + render_entry_list(pep, max_bullets)))

    inner = ""
    for key in ("team_capstone_project", "self_paced_training_projects", "academic_graduation_project"):
        items = entries(d.get(key))
        if items:
            label = as_dict(d.get(key)).get("label", "")
            inner += f'<div class="sub-group-label">{html.escape(str(label))}</div>' + render_entry_list(items, max_bullets)
    if inner:
        body.append(section("Capstone, Academic &amp; Training Projects", inner))

    pt = entries(d.get("professional_training"))
    if pt:
        body.append(section("Professional Training", render_entry_list(pt, max_bullets)))

    certs = [c for c in as_list(d.get("certifications")) if isinstance(c, dict) and c.get("name")]
    if certs:
        rows = "<br>".join(
            f'<span class="bullet-glyph">•</span>{html.escape(c["name"])} '
            + (f'<a class="nowrap-link" href="{html.escape(c["url"])}">{html.escape(c["url"])}</a>' if c.get("url") else "")
            for c in certs
        )
        body.append(section("Certifications", f'<div class="detail-block">{rows}</div>'))

    edu = [e for e in as_list(d.get("education")) if isinstance(e, dict)]
    if edu:
        rows = "".join(
            f'<div class="entry"><div class="entry-header"><div class="entry-title">{esc(e.get("degree"))}</div>'
            f'<div class="entry-date">{html.escape(str(e.get("date") or ""))}</div></div>'
            f'<div class="entry-subline">{esc(e.get("institution"))}</div>'
            f'<div class="overview-text">{html.escape(str(e.get("gpa") or ""))}</div></div>'
            for e in edu
        )
        body.append(section("Education", rows))

    ms = as_dict(d.get("military_service"))
    if ms.get("status") or ms.get("date"):
        status = f'({html.escape(str(ms["status"]))}) ' if ms.get("status") else ""
        body.append(section("Military Service",
                            f'<div class="detail-block">{status}{html.escape(str(ms.get("date") or ""))}</div>'))

    skills = [s for s in as_list(d.get("technical_skills")) if isinstance(s, dict)]
    if skills:
        rows = "\n".join(
            f'<div class="skill-row"><span class="skill-cat">{esc(s.get("category"))}:</span> {esc(s.get("items"))}</div>'
            for s in skills
        )
        body.append(section("Technical Skills", rows))

    if str(d.get("spoken_languages") or "").strip():
        body.append(section("Spoken Languages", f'<div class="detail-block">{html.escape(d["spoken_languages"])}</div>'))

    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><style>{css}</style></head>'
            f'<body>{"".join(body)}</body></html>')


def make_pdf(html_str: str) -> tuple[bytes, int]:
    doc = HTML(string=html_str).render()
    return doc.write_pdf(), len(doc.pages)


def to_pngs(pdf: bytes, dpi: int = 120) -> list[bytes]:
    doc = fitz.open(stream=pdf, filetype="pdf")
    return [p.get_pixmap(dpi=dpi).tobytes("png") for p in doc]


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
<p>Paste your CV JSON, hit Generate, download a clean one-click PDF.</p></div>
""",
    unsafe_allow_html=True,
)

if WEASY_ERR is not None:
    st.error("WeasyPrint could not be loaded - system libraries are missing. "
             "Add a `packages.txt` (see the repo) and reboot the app.\n\n" f"Details: `{WEASY_ERR}`")
    st.stop()

SAMPLE = {
    "header": {"name": "Jane Doe", "title": "Software Engineer", "location": "Cairo, Egypt",
               "phone": "0100 000 0000", "email": "jane@example.com",
               "links": [{"label": "github.com/janedoe", "url": "https://github.com/janedoe"}]},
    "summary": "Backend engineer with experience building REST APIs.",
    "experience": [{"title": "Backend Developer", "date": "Jan. 2025 - Present",
                    "subtitle_parts": ["Freelance, Remote", "C#, ASP.NET Core, SQL Server"],
                    "overview": None, "link": None,
                    "bullets": ["Built secure REST APIs with JWT authentication."]}],
    "technical_skills": [{"category": "Languages", "items": "C#, TypeScript, Python"}],
    "spoken_languages": "Arabic (Native), English (Professional)",
}

left, right = st.columns([3, 2], gap="large")

with left:
    st.markdown("#### 1 · Paste your CV data")
    if "json_text" not in st.session_state:
        st.session_state["json_text"] = ""
    st.text_area(
        "CV JSON", key="json_text", height=430, label_visibility="collapsed",
        placeholder='{\n  "header": { "name": "...", "title": "..." },\n  "summary": "...",\n  "experience": [ ... ]\n}',
    )
    b1, b2, _ = st.columns([1.4, 1, 2.6])
    generate = b1.button("⚡ Generate PDF", type="primary")
    if b2.button("Load sample"):
        st.session_state["json_text"] = json.dumps(SAMPLE, indent=2, ensure_ascii=False)
        st.rerun()

with right:
    st.markdown("#### 2 · Style (optional)")
    with st.container(border=True):
        accent = st.color_picker("Accent color", "#1f3752")
        font_name = st.selectbox("Font", list(FONTS))
        scale = st.slider("Text size", 0.85, 1.15, 1.0, 0.01, format="%.2fx")
        side_margin = st.slider("Side margins (inches)", 0.30, 0.90, 0.48, 0.02)
        max_bullets = st.slider("Max bullets per entry (0 = all)", 0, 8, 0)
    st.caption("Tip: you can also paste a Python dict like `master_cv_data = {...}` straight from your notebook. "
               "Entries with `\"_include\": false` are skipped.")

if generate:
    try:
        data = parse_input(st.session_state["json_text"])
        css = build_css(accent, scale, FONTS[font_name], side_margin)
        with st.spinner("Rendering PDF..."):
            pdf, pages = make_pdf(build_html(data, css, max_bullets))
            pngs = to_pngs(pdf)
        name = re.sub(r"[^A-Za-z0-9]+", "_", str(as_dict(data.get("header")).get("name", ""))).strip("_") or "CV"
        st.session_state["result"] = {"pdf": pdf, "pages": pages, "pngs": pngs, "name": name}
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
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        for n, png in enumerate(res["pngs"], 1):
            st.image(png, caption=f'Page {n} of {res["pages"]}', width="stretch")
