"""Aplikasi Streamlit untuk AI Systems Analyst Pro."""

import io
import json
import os
import re
import uuid
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from crewai import Crew, Process, Task
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

import database as db
from agents import create_agents
from tasks import create_tasks


GEMINI_MODEL_NAME = "gemini-3.6-flash"
RESULT_DELIMITER = "\n\n---\n# RED TEAM AUDIT REPORT\n\n"
FENCE = chr(96) * 3
MERMAID_PATTERN = re.compile(
    re.escape(FENCE) + r"mermaid\s*\n?(.*?)" + re.escape(FENCE),
    re.IGNORECASE | re.DOTALL,
)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")

st.set_page_config(
    page_title="AI Systems Analyst Pro",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)


def load_css() -> None:
    """Memuat stylesheet lokal bila tersedia."""
    css_file = Path(__file__).with_name("style.css")
    if css_file.exists():
        st.markdown(
            f"<style>{css_file.read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True,
        )


def initialize_session() -> None:
    """Menyiapkan state utama aplikasi."""
    defaults = {
        "user": None,
        "active_page": "SRS Studio",
        "selected_srs": None,
        "last_srs_result": "",
        "last_audit_result": "",
        "last_project_title": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def split_stored_result(result: str) -> tuple[str, str]:
    """Memisahkan SRS dan audit dari hasil yang tersimpan."""
    if RESULT_DELIMITER in result:
        return tuple(result.split(RESULT_DELIMITER, maxsplit=1))
    return result.strip(), "Laporan audit belum tersedia untuk dokumen lama ini."


def get_task_output(task) -> str:
    """Mengambil keluaran raw dari task CrewAI."""
    output = getattr(task, "output", None)
    return "" if output is None else str(getattr(output, "raw", output)).strip()


def extract_mermaid_blocks(markdown_text: str) -> list[tuple[str, str]]:
    """Memecah Markdown menjadi teks reguler dan kode Mermaid."""
    blocks, cursor = [], 0
    for match in MERMAID_PATTERN.finditer(markdown_text):
        if match.start() > cursor:
            blocks.append(("markdown", markdown_text[cursor:match.start()]))
        blocks.append(("mermaid", match.group(1).strip()))
        cursor = match.end()
    if cursor < len(markdown_text):
        blocks.append(("markdown", markdown_text[cursor:]))
    return blocks or [("markdown", markdown_text)]


def normalize_mermaid_erd(mermaid_code: str) -> str:
    """Memperbaiki pemisah baris ERD umum dari keluaran LLM sebelum dirender."""
    cleaned_code = mermaid_code.strip()
    cleaned_code = re.sub(r"^```mermaid\s*", "", cleaned_code, flags=re.I)
    cleaned_code = cleaned_code.replace("```", "").strip()
    if not cleaned_code.startswith("erDiagram"):
        cleaned_code = f"erDiagram\n{cleaned_code}"

    # LLM kadang menyatukan penutup blok dengan deklarasi entitas berikutnya:
    # `field_name } products {` harus menjadi dua baris berbeda.
    cleaned_code = re.sub(
        r"}\s*(?=[A-Za-z_][A-Za-z0-9_]*\s*\{)",
        "}\n",
        cleaned_code,
    )
    cleaned_code = re.sub(
        r"(?<!\n)([A-Z][A-Z0-9_]*)\s*\{",
        r"\n\1 {",
        cleaned_code,
    )

    # Mermaid ERD hanya menerima key PK/FK/UK. Detail PostgreSQL seperti INDEX,
    # NOT NULL, DEFAULT, dan CHECK harus tetap berada di Data Dictionary, bukan ERD.
    normalized_lines = []
    is_inside_entity = False
    for line in cleaned_code.splitlines():
        stripped_line = line.strip()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\{$", stripped_line):
            is_inside_entity = True
            normalized_lines.append(stripped_line)
            continue
        if stripped_line == "}":
            is_inside_entity = False
            normalized_lines.append(stripped_line)
            continue
        if is_inside_entity and stripped_line:
            attribute_match = re.match(
                r"^([A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?)\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+.*)?$",
                stripped_line,
            )
            if attribute_match:
                data_type, field_name = attribute_match.groups()
                valid_keys = re.findall(r"\b(PK|FK|UK)\b", stripped_line, re.I)
                key_suffix = f" {valid_keys[0].upper()}" if valid_keys else ""
                normalized_lines.append(f"{data_type} {field_name}{key_suffix}")
                continue
        normalized_lines.append(stripped_line)

    cleaned_code = "\n".join(normalized_lines)
    cleaned_code = re.sub(r"\n{3,}", "\n\n", cleaned_code)
    return cleaned_code.strip()


def sanitize_mermaid_flowchart(mermaid_code: str) -> str:
    """Membersihkan flowchart Mermaid tanpa merusak simbol panah atau node."""
    cleaned_code = mermaid_code.strip()
    cleaned_code = re.sub(
        r"^" + re.escape(FENCE) + r"(?:mermaid)?\s*",
        "",
        cleaned_code,
        flags=re.I,
    )
    cleaned_code = cleaned_code.replace(FENCE, "").strip()
    if not re.match(r"^graph\s+(TD|LR)\b", cleaned_code, re.I):
        cleaned_code = f"graph TD\n{cleaned_code}"

    def clean_node_label(match: re.Match) -> str:
        label = match.group(1)
        label = re.sub(r"""["'{}\[\]()]""", "", label)
        label = re.sub(r"\s+", " ", label).strip()
        return f"[{label}]"

    cleaned_code = re.sub(r"\[([^\]]*)\]", clean_node_label, cleaned_code)
    cleaned_code = cleaned_code.replace('"', "").replace("'", "")
    cleaned_code = re.sub(r"\n{3,}", "\n\n", cleaned_code)
    return cleaned_code.strip()


def extract_process_flowcharts(markdown_text: str) -> dict[str, str]:
    """Mengekstrak diagram As-Is dan To-Be dari section proses bisnis."""
    sections = {}
    heading_pattern = re.compile(
        r"(?ims)^#{1,6}\s*(?:\d+(?:\.\d+)*\.?\s*)?"
        r"(?:AS[-\s]?IS|TO[-\s]?BE)[^\n]*$"
    )
    matches = list(heading_pattern.finditer(markdown_text))
    for index, match in enumerate(matches):
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown_text)
        heading = match.group(0).upper().replace(" ", "")
        flow_type = "as_is" if "AS-IS" in heading or "ASIS" in heading else "to_be"
        mermaid_match = MERMAID_PATTERN.search(markdown_text, match.end(), section_end)
        if mermaid_match:
            sections[flow_type] = sanitize_mermaid_flowchart(mermaid_match.group(1))

    flow_blocks = [
        sanitize_mermaid_flowchart(match.group(1))
        for match in MERMAID_PATTERN.finditer(markdown_text)
        if re.match(r"^\s*graph\s+(TD|LR)\b", match.group(1), re.I)
    ]
    if "as_is" not in sections and flow_blocks:
        sections["as_is"] = flow_blocks[0]
    if "to_be" not in sections and len(flow_blocks) > 1:
        sections["to_be"] = flow_blocks[1]
    return sections


def render_mermaid_diagram(mermaid_code: str) -> None:
    """Merender Mermaid.js ke SVG di dalam Streamlit."""
    diagram_id = f"mermaid-{uuid.uuid4().hex}"
    normalized_code = (
        normalize_mermaid_erd(mermaid_code)
        if mermaid_code.lstrip().lower().startswith("erdiagram")
        else sanitize_mermaid_flowchart(mermaid_code)
    )
    component_html = f"""
    <div id="{diagram_id}" class="mermaid-host"></div>
    <script type="module">
      import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
      const source = {json.dumps(normalized_code)};
      const host = document.getElementById("{diagram_id}");
      mermaid.initialize({{
        startOnLoad: false, theme: "neutral", securityLevel: "strict",
        er: {{ useMaxWidth: true }}
      }});
      try {{
        const isValid = await mermaid.parse(source, {{ suppressErrors: true }});
        if (!isValid) {{
          throw new Error("Sintaks Mermaid tidak valid setelah sanitasi otomatis.");
        }}
        const rendered = await mermaid.render("{diagram_id}-svg", source);
        host.innerHTML = rendered.svg;
      }} catch (error) {{
        host.textContent = "Diagram Mermaid tidak dapat dirender: " + error.message;
        host.style.color = "#b42318";
      }}
    </script>
    <style>
      body {{ margin: 0; background: transparent; }}
      .mermaid-host {{ overflow-x: auto; padding: 16px 4px; text-align: center; }}
      .mermaid-host svg {{ max-width: 100%; height: auto; }}
    </style>
    """
    try:
        components.html(component_html, height=440, scrolling=True)
    except Exception as error:
        st.warning(f"Renderer Mermaid tidak tersedia: {error}")
        st.code(normalized_code, language="mermaid")


def render_process_mapping(markdown_text: str) -> None:
    """Menampilkan perbandingan visual proses As-Is dan To-Be."""
    flowcharts = extract_process_flowcharts(markdown_text)
    st.markdown("### Analisis Proses Bisnis (BPMN)")
    st.caption(
        "Perbandingan alur manual saat ini dan alur target terotomatisasi. "
        "Kode Mermaid tersedia bila diagram gagal dirender."
    )
    as_is_column, to_be_column = st.columns(2)
    definitions = [
        (as_is_column, "as_is", "As-Is — Proses Saat Ini", "Diagram As-Is belum dihasilkan."),
        (to_be_column, "to_be", "To-Be — Proses Target", "Diagram To-Be belum dihasilkan."),
    ]
    for column, key, title, empty_message in definitions:
        with column:
            st.markdown(f"#### {title}")
            diagram_code = flowcharts.get(key)
            if not diagram_code:
                st.info(empty_message)
                continue
            render_mermaid_diagram(diagram_code)
            with st.expander(f"Lihat kode Mermaid {title}"):
                st.code(diagram_code, language="mermaid")


def render_srs_markdown(markdown_text: str) -> None:
    """Menampilkan Markdown dan mengganti kode Mermaid menjadi diagram."""
    for block_type, content in extract_mermaid_blocks(markdown_text):
        if block_type == "mermaid":
            st.caption("Entity Relationship Diagram")
            render_mermaid_diagram(content)
            with st.expander("Lihat kode Mermaid"):
                st.code(normalize_mermaid_erd(content), language="mermaid")
        elif content.strip():
            st.markdown(content)


def is_table_separator(line: str) -> bool:
    """Memeriksa baris pemisah tabel Markdown."""
    cells = [
        cell.strip().replace(":", "").replace("-", "")
        for cell in line.strip().strip("|").split("|")
    ]
    return bool(cells) and all(not cell for cell in cells)


def add_markdown_to_docx(document: Document, markdown_text: str) -> None:
    """Mengonversi heading, tabel, daftar, dan paragraf Markdown ke DOCX."""
    lines, index, in_code_block = markdown_text.splitlines(), 0, False
    while index < len(lines):
        line = lines[index].rstrip()
        if line.strip().startswith(FENCE):
            in_code_block = not in_code_block
            index += 1
            continue
        if in_code_block or not line.strip():
            index += 1
            continue

        heading = HEADING_PATTERN.match(line)
        if heading:
            document.add_heading(heading.group(2).strip(), min(len(heading.group(1)), 4))
            index += 1
            continue
        if line.lstrip().startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            rows = [
                [cell.strip() for cell in row.strip().strip("|").split("|")]
                for row in table_lines
                if not is_table_separator(row)
            ]
            if rows:
                table = document.add_table(
                    rows=0,
                    cols=max(len(row) for row in rows),
                )
                table.style = "Table Grid"
                for row_index, row in enumerate(rows):
                    cells = table.add_row().cells
                    for column, value in enumerate(row):
                        cells[column].text = value
                        if row_index == 0:
                            for run in cells[column].paragraphs[0].runs:
                                run.bold = True
            continue

        bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
        numbered = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if bullet:
            document.add_paragraph(bullet.group(1), style="List Bullet")
        elif numbered:
            document.add_paragraph(numbered.group(1), style="List Number")
        else:
            clean_line = re.sub(r"[*_]", "", line).replace(chr(96), "")
            document.add_paragraph(clean_line)
        index += 1


def create_srs_docx(title: str, srs_text: str, audit_text: str = "") -> bytes:
    """Membuat DOCX lengkap dari SRS beserta lampiran audit Red Team."""
    document = Document()
    section = document.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.65)
    section.left_margin = section.right_margin = Inches(0.7)
    document.styles["Normal"].font.name = "Aptos"
    document.styles["Normal"].font.size = Pt(10)
    heading = document.add_heading(title, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = document.add_paragraph("Software Requirements Specification")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph()
    add_markdown_to_docx(document, srs_text)
    if audit_text.strip():
        document.add_page_break()
        document.add_heading("Lampiran A — Laporan Red Team Audit", level=1)
        add_markdown_to_docx(document, audit_text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def sanitize_filename(value: str) -> str:
    """Membuat nama berkas aman untuk Windows."""
    return re.sub(r'[<>:"/\\\\|?*]+', "_", value).strip() or "SRS"


def get_active_results() -> tuple[str, str, str]:
    """Mengambil hasil SRS/audit yang tersimpan lintas halaman."""
    selected = st.session_state.selected_srs
    if selected:
        return (
            selected["judul"],
            selected["srs_text"],
            selected["audit_text"],
        )
    return (
        st.session_state.last_project_title or "Untitled SRS",
        st.session_state.last_srs_result,
        st.session_state.last_audit_result,
    )


def extract_fr_dataframe(srs_text: str) -> pd.DataFrame:
    """Mengekstrak Functional Requirements untuk CSV Jira dan analitik."""
    rows = []
    for line in srs_text.splitlines():
        if not re.match(r"^\|\s*FR-\d+", line, re.IGNORECASE):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 5:
            rows.append(
                {
                    "FR ID": cells[0],
                    "Summary": cells[1],
                    "Description": cells[2],
                    "Role": cells[3],
                    "Priority": cells[4],
                }
            )
    if not rows:
        for fr_id in sorted(set(re.findall(r"\bFR-\d+\b", srs_text, re.IGNORECASE))):
            rows.append(
                {
                    "FR ID": fr_id.upper(),
                    "Summary": "Functional Requirement",
                    "Description": "",
                    "Role": "",
                    "Priority": "Medium",
                }
            )
    return pd.DataFrame(
        rows,
        columns=["FR ID", "Summary", "Description", "Role", "Priority"],
    )


def extract_bdd_feature(title: str, srs_text: str) -> str:
    """Menghasilkan file Cucumber .feature dari baris Gherkin pada SRS."""
    gherkin_lines = []
    for line in srs_text.splitlines():
        clean_line = re.sub(r"^\s*[-*]\s*", "", line).strip()
        clean_line = clean_line.replace("**", "")
        if re.match(r"^(Scenario|Given|When|Then|And)\s*:?\s+", clean_line, re.I):
            clean_line = re.sub(r"^(Scenario|Given|When|Then|And)\s*:\s*", r"\1 ", clean_line, flags=re.I)
            gherkin_lines.append(clean_line)
    body = "\n".join(gherkin_lines)
    return f"Feature: {title}\n\n{body or '  # Acceptance Criteria Gherkin belum ditemukan.'}\n"


def calculate_priority_counts(fr_dataframe: pd.DataFrame) -> pd.DataFrame:
    """Menghitung jumlah FR berdasarkan prioritas."""
    priorities = ["High", "Medium", "Low"]
    normalized = fr_dataframe["Priority"].astype(str).str.lower()
    counts = {
        "High": int(normalized.str.contains(r"high|tinggi", regex=True).sum()),
        "Medium": int(normalized.str.contains(r"medium|med|sedang", regex=True).sum()),
        "Low": int(normalized.str.contains(r"low|rendah", regex=True).sum()),
    }
    return pd.DataFrame({"Priority": priorities, "Jumlah FR": [counts[item] for item in priorities]}).set_index("Priority")


def render_priority_chart(priority_counts: pd.DataFrame) -> None:
    """Menampilkan chart prioritas horizontal dengan gaya minimalis."""
    colors = {"High": "#e76f6f", "Medium": "#d9a441", "Low": "#5b9bbd"}
    labels = priority_counts.index.tolist()
    values = priority_counts["Jumlah FR"].tolist()
    figure = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            marker_color=[colors.get(label, "#2563eb") for label in labels],
            text=values,
            textposition="outside",
            hovertemplate="%{x}: %{y} FR<extra></extra>",
            width=0.52,
        )
    )
    figure.update_layout(
        height=330,
        margin={"l": 12, "r": 12, "t": 18, "b": 12},
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        showlegend=False,
        font={"family": "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif", "color": "#37352F"},
    )
    figure.update_xaxes(
        categoryorder="array",
        categoryarray=["High", "Medium", "Low"],
        showgrid=False,
        tickangle=0,
        fixedrange=True,
        title=None,
    )
    figure.update_yaxes(
        showgrid=False,
        showline=False,
        zeroline=False,
        rangemode="tozero",
        fixedrange=True,
        title=None,
    )
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})


def calculate_risk_counts(audit_text: str) -> dict[str, int]:
    """Menghitung indikasi risiko dari laporan auditor."""
    return {
        "Critical / High": len(re.findall(r"\b(critical|high|kritis|tinggi)\b", audit_text, re.I)),
        "Medium": len(re.findall(r"\b(medium|med|sedang)\b", audit_text, re.I)),
        "Low": len(re.findall(r"\b(low|rendah)\b", audit_text, re.I)),
    }


def render_risk_gap_matrix(audit_text: str) -> None:
    """Menampilkan kartu ringkasan Risk & Gap Matrix."""
    risk_counts = calculate_risk_counts(audit_text)
    columns = st.columns(3)
    styles = ["risk-critical", "risk-medium", "risk-low"]
    for column, (label, count), style_name in zip(columns, risk_counts.items(), styles):
        with column:
            st.markdown(
                f"<div class='risk-card {style_name}'><span>{label}</span><strong>{count}</strong><small>Temuan terindikasi</small></div>",
                unsafe_allow_html=True,
            )


def extract_target_tables(srs_text: str) -> list[str]:
    """Mengambil kandidat nama tabel dari ERD atau tabel data dictionary."""
    erd_tables = re.findall(r"(?m)^\s*([A-Z][A-Z0-9_]+)\s*\{", srs_text)
    dictionary_tables = re.findall(r"(?m)^\|\s*([a-z][a-z0-9_]*)\s*\|", srs_text)
    tables = erd_tables + dictionary_tables
    return list(dict.fromkeys(table.lower() for table in tables)) or ["TBD"]


def build_rtm_dataframe(srs_text: str) -> pd.DataFrame:
    """Membuat Requirements Traceability Matrix secara otomatis."""
    fr_dataframe = extract_fr_dataframe(srs_text)
    tables = extract_target_tables(srs_text)
    objective = "Business goal belum teridentifikasi"
    objective_match = re.search(r"\*?\*?Objective:?\*?\*?\s*(.+)", srs_text, re.I)
    if objective_match:
        objective = objective_match.group(1).strip()
    rows = []
    for index, fr_id in enumerate(fr_dataframe["FR ID"].tolist()):
        rows.append(
            {
                "Business Goal": objective,
                "FR ID": fr_id,
                "Target Table": tables[index % len(tables)],
                "Test Case Status": "Pending QA",
            }
        )
    return pd.DataFrame(rows)


def display_document(data: dict) -> None:
    """Menampilkan SRS, audit, dan tombol ekspor dalam tab terpisah."""
    st.markdown(f"## {data['judul']}")
    st.caption("Dokumen SRS dan audit Red Team berbasis CrewAI.")
    srs_tab, process_tab, audit_tab, export_tab = st.tabs(
        [
            "📘 SRS Document",
            "🔄 Analisis Proses Bisnis (BPMN)",
            "🛡️ Red Team Audit Report",
            "📤 Export",
        ]
    )
    with srs_tab:
        render_srs_markdown(data["srs_text"])
    with process_tab:
        render_process_mapping(data["srs_text"])
    with audit_tab:
        st.markdown("### Risk & Gap Matrix")
        render_risk_gap_matrix(data["audit_text"])
        st.divider()
        render_srs_markdown(data["audit_text"])
    with export_tab:
        st.download_button(
            "📄 Download SRS as Word Document",
            data=create_srs_docx(data["judul"], data["srs_text"], data["audit_text"]),
            file_name=f"{sanitize_filename(data['judul'])}_SRS.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
        )
        st.download_button(
            "⬇️ Download SRS as Markdown",
            data=data["srs_text"],
            file_name=f"{sanitize_filename(data['judul'])}_SRS.md",
            mime="text/markdown",
        )
        st.caption("Berkas Word memuat SRS lengkap, DoD bila tersedia, dan lampiran audit Red Team.")

    render_revision_box(data)


def run_revision(data: dict, revision_instruction: str) -> None:
    """Memperbarui SRS aktif tanpa mengulang ekstraksi business case dari nol."""
    api_key = st.session_state.user.get("api_key", "").strip()
    if not api_key:
        raise ValueError("Gemini API key belum dikonfigurasi.")

    os.environ["GEMINI_API_KEY"] = api_key
    with st.spinner("Business Analyst memperbarui SRS dan Red Team mengaudit revisinya..."):
        ba_agent, auditor_agent = create_agents(GEMINI_MODEL_NAME)
        revision_task = Task(
            description=f"""
Perbarui dokumen SRS yang ada berdasarkan instruksi revisi pengguna.

ATURAN KERJA:
1. Ini revisi incremental. Jangan mengulang ekstraksi atau mengarang ulang fakta
   dari business case awal.
2. Pertahankan artefak SRS yang masih relevan, lalu perbarui hanya bagian yang
   terdampak: FR, BDD, data dictionary, ERD, API mapping, privacy/security,
   dan Definition of Done.
3. Jika instruksi ambigu atau bertentangan dengan SRS lama, catat sebagai asumsi
   atau pertanyaan klarifikasi; jangan menganggapnya fakta.
4. Keluarkan SRS lengkap hasil revisi sesuai struktur baku.

<BUSINESS_CASE_ASLI>
{data["studi_kasus"]}
</BUSINESS_CASE_ASLI>

<SRS_LAMA>
{data["srs_text"]}
</SRS_LAMA>

<INSTRUKSI_REVISI_PENGGUNA>
{revision_instruction}
</INSTRUKSI_REVISI_PENGGUNA>
""",
            expected_output="SRS lengkap hasil revisi incremental yang dapat diuji.",
            agent=ba_agent,
        )
        revision_audit_task = Task(
            description=f"""
Audit SRS hasil revisi pada konteks task. Fokus pada dampak instruksi pengguna:

<INSTRUKSI_REVISI_PENGGUNA>
{revision_instruction}
</INSTRUKSI_REVISI_PENGGUNA>

Jangan menulis ulang SRS. Pastikan perubahan tidak menimbulkan regression pada
business logic, BDD, database, API mapping, PII, RBAC, UU PDP/GDPR, atau
Definition of Done. Gunakan format Red Team audit, skor objektif, severity,
dan action item P0/P1/P2.
""",
            expected_output="Laporan audit Red Team untuk SRS hasil revisi.",
            agent=auditor_agent,
            context=[revision_task],
        )
        crew = Crew(
            agents=[ba_agent, auditor_agent],
            tasks=[revision_task, revision_audit_task],
            process=Process.sequential,
            verbose=False,
        )
        crew_output = crew.kickoff()

    revised_srs = get_task_output(revision_task) or str(crew_output)
    revised_audit = get_task_output(revision_audit_task) or "Laporan audit revisi tidak tersedia."
    revised_title = f"{data['judul']} — Revisi"
    stored_result = f"{revised_srs}{RESULT_DELIMITER}{revised_audit}"
    document_id = db.save_srs_version(
        st.session_state.user["id"],
        revised_title,
        data["studi_kasus"],
        stored_result,
    )
    st.session_state.selected_srs = {
        "id": document_id,
        "judul": revised_title,
        "studi_kasus": data["studi_kasus"],
        "srs_text": revised_srs,
        "audit_text": revised_audit,
    }
    st.session_state.last_srs_result = revised_srs
    st.session_state.last_audit_result = revised_audit
    st.session_state.last_project_title = revised_title


def render_revision_box(data: dict) -> None:
    """Menyediakan input Human-in-the-Loop setelah hasil analisis."""
    st.divider()
    st.markdown("### Revisi & Refine")
    st.caption(
        "Masukkan perubahan spesifik. SRS aktif diperbarui dan diaudit ulang tanpa "
        "mengekstrak business case dari awal."
    )
    instruction = st.chat_input(
        "Contoh: Tambahkan approval manager untuk pembatalan pesanan di atas Rp5 juta.",
        key=f"revision_instruction_{data['id']}",
    )
    if instruction:
        try:
            run_revision(data, instruction.strip())
            st.rerun()
        except Exception as error:
            st.error(f"Revisi gagal dijalankan: {error}")


def select_history_document(item: tuple) -> None:
    """Membuka dokumen riwayat pada workspace."""
    document_id, _, title, business_case, stored_result, _ = item
    srs_text, audit_text = split_stored_result(stored_result)
    st.session_state.selected_srs = {
        "id": document_id,
        "judul": title,
        "studi_kasus": business_case,
        "srs_text": srs_text,
        "audit_text": audit_text,
    }
    st.session_state.last_srs_result = srs_text
    st.session_state.last_audit_result = audit_text
    st.session_state.last_project_title = title
    st.session_state.active_page = "SRS Studio"
    st.rerun()


def render_sidebar() -> None:
    """Membuat sidebar minimalis bergaya Notion."""
    user = st.session_state.user
    with st.sidebar:
        st.markdown("<div class='sidebar-brand'><span>⚡</span> AI Systems Analyst</div>", unsafe_allow_html=True)
        st.caption(f"Workspace · {user['username']}")
        st.markdown("<div class='sidebar-label'>WORKSPACE</div>", unsafe_allow_html=True)
        if st.button("✨ SRS Studio", use_container_width=True):
            st.session_state.active_page = "SRS Studio"
            st.session_state.selected_srs = None
            st.rerun()
        if st.button("🕰️ Riwayat Proyek", use_container_width=True):
            st.session_state.active_page = "Riwayat Proyek"
            st.rerun()
        st.divider()
        st.markdown("<div class='sidebar-label'>ANALYTICS &amp; TOOLS</div>", unsafe_allow_html=True)
        if st.button("📈 Dashboard Metrik", use_container_width=True):
            st.session_state.active_page = "Dashboard Metrik"
            st.rerun()
        if st.button("📥 Export Center (Jira & Word)", use_container_width=True):
            st.session_state.active_page = "Export Center"
            st.rerun()
        if st.button("🧭 Requirements Traceability Matrix", use_container_width=True):
            st.session_state.active_page = "RTM"
            st.rerun()
        st.divider()
        st.markdown("<div class='sidebar-label'>PENGATURAN</div>", unsafe_allow_html=True)
        if st.button("🔑 Konfigurasi API", use_container_width=True):
            st.session_state.active_page = "Konfigurasi API"
            st.rerun()
        st.divider()
        if st.button("Keluar", use_container_width=True):
            st.session_state.user = None
            st.session_state.selected_srs = None
            st.rerun()


def render_authentication() -> None:
    """Menampilkan form login dan registrasi."""
    st.markdown("<div class='auth-shell'>", unsafe_allow_html=True)
    st.title("AI Systems Analyst Pro")
    st.caption("Enterprise SRS Generator & Red Team Auditor")
    login_tab, register_tab = st.tabs(["Masuk", "Daftar"])
    with login_tab:
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Masuk ke workspace", type="primary", use_container_width=True):
            user_data = db.login_user(username, password)
            if user_data:
                st.session_state.user = {
                    "id": user_data[0],
                    "username": user_data[1],
                    "role": user_data[2],
                    "api_key": user_data[3] or "",
                }
                st.rerun()
            st.error("Username atau password tidak valid.")
    with register_tab:
        username = st.text_input("Username baru", key="register_username")
        password = st.text_input("Password baru", type="password", key="register_password")
        if st.button("Buat akun", type="primary", use_container_width=True):
            if not username.strip() or not password:
                st.warning("Username dan password wajib diisi.")
            elif db.register_user(username, password):
                st.success("Akun berhasil dibuat. Silakan masuk.")
            else:
                st.error("Username tersebut sudah digunakan.")
    st.markdown("</div>", unsafe_allow_html=True)


def run_analysis(title: str, business_case: str, api_key: str) -> None:
    """Menjalankan BA dan auditor lalu menyimpan keluaran terpisah."""
    os.environ["GEMINI_API_KEY"] = api_key
    with st.spinner("Business Analyst dan Red Team Auditor sedang menyusun dokumen..."):
        ba_agent, auditor_agent = create_agents(GEMINI_MODEL_NAME)
        tasks = create_tasks(business_case, ba_agent, auditor_agent)
        crew = Crew(
            agents=[ba_agent, auditor_agent],
            tasks=tasks,
            process=Process.sequential,
            verbose=False,
        )
        crew_output = crew.kickoff()
    srs_text = get_task_output(tasks[0]) or str(crew_output)
    audit_text = get_task_output(tasks[1]) or "Laporan audit tidak tersedia."
    stored = f"{srs_text}{RESULT_DELIMITER}{audit_text}"
    document_id = db.save_srs(st.session_state.user["id"], title, business_case, stored)
    st.session_state.selected_srs = {
        "id": document_id,
        "judul": title,
        "studi_kasus": business_case,
        "srs_text": srs_text,
        "audit_text": audit_text,
    }
    st.session_state.last_srs_result = srs_text
    st.session_state.last_audit_result = audit_text
    st.session_state.last_project_title = title


def render_generator() -> None:
    """Merender form pembangkitan SRS atau hasil dokumen aktif."""
    if st.session_state.selected_srs:
        if st.button("← Kembali ke form SRS"):
            st.session_state.selected_srs = None
            st.rerun()
        display_document(st.session_state.selected_srs)
        return
    st.markdown("<div class='notion-cover'></div>", unsafe_allow_html=True)
    st.markdown("<div class='page-icon'>📝</div>", unsafe_allow_html=True)
    st.title("SRS Studio")
    st.caption("Ubah kebutuhan bisnis mentah menjadi SRS dan laporan audit Red Team.")
    st.markdown("<div class='model-badge'>Model: Gemini 3.6 Flash</div>", unsafe_allow_html=True)
    with st.form("generate_srs_form"):
        title = st.text_input("Nama proyek", placeholder="Contoh: Sistem Informasi Apotek")
        business_case = st.text_area(
            "Business case dan kebutuhan awal",
            height=260,
            placeholder="Jelaskan masalah bisnis, pengguna, alur kerja, data, dan batasan sistem.",
        )
        submitted = st.form_submit_button("🚀 Jalankan Analisis Multi-Agent", type="primary")
    if submitted:
        api_key = st.session_state.user.get("api_key", "").strip()
        if not api_key:
            st.error("Simpan Gemini API key melalui sidebar sebelum menjalankan analisis.")
        elif not title.strip() or not business_case.strip():
            st.warning("Nama proyek dan business case wajib diisi.")
        else:
            try:
                run_analysis(title.strip(), business_case.strip(), api_key)
                st.rerun()
            except Exception as error:
                st.error(f"Analisis gagal dijalankan: {error}")


def render_history() -> None:
    """Menampilkan riwayat SRS pemilik akun."""
    st.title("Riwayat Dokumen")
    st.caption("Dokumen SRS yang dibuat pada akun ini.")
    history = db.get_user_history(st.session_state.user["id"])
    if not history:
        st.info("Belum ada dokumen yang tersimpan.")
        return
    for item in history:
        document_id, _, title, _, _, created_at = item
        with st.container(border=True):
            left_column, right_column = st.columns([5, 1])
            with left_column:
                st.markdown(f"#### {title}")
                st.caption(f"Dibuat: {created_at}")
            with right_column:
                if st.button("Buka", key=f"open_{document_id}", use_container_width=True):
                    select_history_document(item)


def render_analytics() -> None:
    """Menampilkan metrik FR dan estimasi usaha proyek."""
    title, srs_text, _ = get_active_results()
    st.title("Dashboard Metrik")
    st.caption("Analitik kebutuhan fungsional dan estimasi delivery berbasis SRS aktif.")
    if not srs_text:
        st.info("Buat atau buka dokumen SRS terlebih dahulu.")
        return
    fr_dataframe = extract_fr_dataframe(srs_text)
    priority_counts = calculate_priority_counts(fr_dataframe)
    high = int(priority_counts.loc["High", "Jumlah FR"])
    medium = int(priority_counts.loc["Medium", "Jumlah FR"])
    low = int(priority_counts.loc["Low", "Jumlah FR"])
    total_fr = len(fr_dataframe)
    story_points = max(total_fr * 3, 1)
    sprint_weeks = max(2, (story_points + 19) // 20 * 2)
    man_hours = story_points * 8

    st.markdown(f"### {title}")
    metric_columns = st.columns(4)
    metric_columns[0].metric("Total FR", total_fr)
    metric_columns[1].metric("Story Points", story_points)
    metric_columns[2].metric("Estimasi Sprint", f"{sprint_weeks} minggu")
    metric_columns[3].metric("Estimasi Man-Hours", f"{man_hours:,} jam")
    st.markdown("### Distribusi Prioritas Functional Requirements")
    render_priority_chart(priority_counts)
    st.dataframe(fr_dataframe, use_container_width=True, hide_index=True)


def render_export_center() -> None:
    """Menyediakan ekspor Jira CSV, Word, dan BDD Feature."""
    title, srs_text, audit_text = get_active_results()
    st.title("Export Center")
    st.caption("Ekspor artefak analisis ke format siap pakai.")
    if not srs_text:
        st.info("Buat atau buka dokumen SRS terlebih dahulu.")
        return
    fr_dataframe = extract_fr_dataframe(srs_text)
    jira_dataframe = fr_dataframe.copy()
    jira_dataframe.insert(0, "Issue Type", "Story")
    jira_dataframe["Status"] = "To Do"
    bdd_feature = extract_bdd_feature(title, srs_text)

    export_tab, bdd_tab = st.tabs(["📥 Jira & Word", "🧪 BDD Feature Exporter"])
    with export_tab:
        first_column, second_column = st.columns(2)
        with first_column:
            st.download_button(
                "📥 Unduh SRS sebagai CSV Jira Backlog",
                data=jira_dataframe.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{sanitize_filename(title)}_jira_backlog.csv",
                mime="text/csv",
                type="primary",
                use_container_width=True,
            )
        with second_column:
            st.download_button(
                "📄 Unduh SRS sebagai Microsoft Word",
                data=create_srs_docx(title, srs_text, audit_text),
                file_name=f"{sanitize_filename(title)}_SRS.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                use_container_width=True,
            )
        st.dataframe(jira_dataframe, use_container_width=True, hide_index=True)
    with bdd_tab:
        st.caption("Acceptance Criteria diekstrak menjadi sintaks Cucumber/Gherkin.")
        st.code(bdd_feature, language="gherkin")
        st.download_button(
            "🧪 Unduh Cucumber Feature (.feature)",
            data=bdd_feature.encode("utf-8"),
            file_name=f"{sanitize_filename(title)}.feature",
            mime="text/plain",
            type="primary",
        )


def render_rtm() -> None:
    """Menampilkan Requirements Traceability Matrix."""
    _, srs_text, _ = get_active_results()
    st.title("Requirements Traceability Matrix")
    st.caption("Pemetaan Business Goal, requirement, tabel target, dan status QA.")
    if not srs_text:
        st.info("Buat atau buka dokumen SRS terlebih dahulu.")
        return
    rtm_dataframe = build_rtm_dataframe(srs_text)
    st.dataframe(rtm_dataframe, use_container_width=True, hide_index=True)
    st.download_button(
        "Unduh RTM sebagai CSV",
        data=rtm_dataframe.to_csv(index=False).encode("utf-8-sig"),
        file_name="requirements_traceability_matrix.csv",
        mime="text/csv",
    )


def render_api_settings() -> None:
    """Menampilkan konfigurasi API secara terpisah dari sidebar."""
    st.title("Konfigurasi API")
    st.caption("API key disimpan per pengguna pada database lokal.")
    api_key = st.text_input(
        "Gemini API key",
        value=st.session_state.user.get("api_key", ""),
        type="password",
    )
    st.markdown(f"Model CrewAI aktif: **{GEMINI_MODEL_NAME}**")
    if st.button("Simpan Konfigurasi", type="primary"):
        db.update_api_key(st.session_state.user["id"], api_key.strip())
        st.session_state.user["api_key"] = api_key.strip()
        st.success("Konfigurasi API berhasil disimpan.")


def main() -> None:
    """Titik masuk aplikasi Streamlit."""
    db.init_db()
    load_css()
    initialize_session()
    if st.session_state.user is None:
        render_authentication()
        return
    render_sidebar()
    if st.session_state.active_page == "Riwayat Proyek":
        render_history()
    elif st.session_state.active_page == "Dashboard Metrik":
        render_analytics()
    elif st.session_state.active_page == "Export Center":
        render_export_center()
    elif st.session_state.active_page == "RTM":
        render_rtm()
    elif st.session_state.active_page == "Konfigurasi API":
        render_api_settings()
    else:
        render_generator()


if __name__ == "__main__":
    main()
