# AI-Assisted BA Workspace

Aplikasi web internal yang dirancang untuk mempercepat alur kerja Business Analyst (BA) dalam mengolah catatan/curhatan acak dari klien menjadi dokumen spesifikasi sistem (SRS) yang terstruktur.

> **Metode Pengembangan:** Dibuat menggunakan pendekatan *AI-Assisted Development* untuk mempercepat penulisan kode program dan validasi arsitektur agen AI.

## Fitur Utama
- **Ekstraksi Curhatan Klien:** Mengubah narasi acak menjadi Functional Requirements (FR), BDD User Stories (*Given-When-Then*), dan skema tabel database (3NF).
- **Visualisasi ERD:** Menggenerasi diagram relasi entitas menggunakan Mermaid.js.
- **Red Team Reviewer:** Agen AI kedua yang meninjau potensi celah logika bisnis dan aspek keamanan data (UU PDP).
- **Export & Backlog:** Fitur ekspor backlog ke format CSV Jira.

## Tech Stack
- Python & Streamlit
- CrewAI & Gemini API
- Pandas & Mermaid.js
