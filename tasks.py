"""Definisi task CrewAI untuk generasi SRS dan Red Team audit."""

from crewai import Task


def create_tasks(studi_kasus, ba_agent, auditor_agent):
    """Membuat task BA dan auditor dengan konteks output yang berantai."""
    srs_task = Task(
        description=f"""
Analisis cerita kasus berikut sebagai satu-satunya sumber fakta:

<CERITA_KASUS>
{studi_kasus}
</CERITA_KASUS>

Hasilkan SRS profesional dalam Bahasa Indonesia sesuai struktur pada instruksi agen.

KONTRAK KUALITAS:
1. Lakukan analisis internal secara berurutan: fakta eksplisit, aktor, proses,
   data, aturan bisnis, risiko, ambiguitas, lalu requirement. Jangan tampilkan
   rantai penalaran internal; tampilkan hanya fakta, asumsi tervalidasi, dan
   justifikasi requirement yang ringkas.
2. Jangan menambahkan fakta tanpa bukti. Setiap asumsi atau kebutuhan tersembunyi
   harus ada di tabel **Asumsi yang Perlu Validasi** atau **Clarification Needed**.
3. Setiap FR harus atomik, memiliki kode FR-XX, RBAC, prioritas, Fibonacci Story
   Points (1, 2, 3, 5, 8, 13), dan justifikasi teknis singkat.
4. Setiap FR harus memiliki User Story serta tabel BDD **Given | When | Then**
   yang berisi tepat minimal tiga skenario: Main/Happy Path, Alternative/Edge
   Case, dan Exception/Error Handling. Skenario harus dapat diuji QA.
5. Sertakan skema PostgreSQL 3NF, Data Dictionary dengan kolom **Table Name,
   Field Name, Data Type, Constraints, Business Logic**, dan satu blok Mermaid
   ERD tunggal. Seluruh relasi dan entitas harus berada pada blok yang sama.
6. Identifikasi PII/sensitive data yang benar-benar tersirat atau disebut pada
   cerita kasus. Buat Data Protection Requirement mencakup enkripsi, RBAC,
   retensi, dan risiko UU PDP/GDPR. Bila data sensitif tidak disebut, nyatakan
   sebagai TBD, bukan fakta.
7. Buat Feature-to-API Mapping Matrix:
   | FR ID | HTTP Method | Endpoint Path | Request Payload / Params | Response Status |
   Gunakan RESTful API dan status HTTP yang relevan.
8. Sertakan Definition of Done (DoD) yang memuat kriteria verifikasi implementasi,
   test BDD, security/privacy check, migration, API contract, observability, dan
   dokumentasi.
9. Output harus berupa Markdown terstruktur, tabel penuh, istilah tegas, dan
   tanpa salam/pembuka/penutup generik.
""",
        expected_output=(
            "SRS lengkap berisi fakta dan asumsi terpisah, FR dengan Story Points, "
            "BDD tiga skenario per FR, data dictionary, Mermaid ERD tunggal, "
            "privacy/security requirements, serta Feature-to-API Mapping Matrix."
        ),
        agent=ba_agent,
    )

    audit_task = Task(
        description=f"""
Audit SRS dari task sebelumnya sebagai Red Team. Bandingkan terhadap cerita kasus
asli berikut dan jangan menulis ulang dokumen SRS:

<CERITA_KASUS>
{studi_kasus}
</CERITA_KASUS>

ATURAN AUDIT:
1. Bersikap skeptis. Cari business logic loopholes, edge case, kontrol yang
   hilang, dan kebutuhan tersembunyi; jangan memberi pujian generik.
2. Periksa cakupan fitur, ketidakjelasan narasi, kelayakan implementasi,
   kualitas BDD, 3NF/ERD/data dictionary, dan Feature-to-API Mapping.
3. Periksa PII/sensitive data, RBAC, enkripsi, retensi, audit log, validasi
   input, OWASP Top 10, serta gap UU PDP/GDPR.
4. Wajib buat rubrik objektif dengan skor 1-10:
   | Dimensi | Skor | Bukti / Alasan | Dampak |
   | Completeness Score | 1-10 | ... | ... |
   | Ambiguity Score | 1-10 | 10 sangat jelas; 1 sangat ambigu | ... |
   | Feasibility Score | 1-10 | ... | ... |
5. Wajib buat tabel temuan:
   | ID | Severity | Kategori | Bukti di SRS / Catatan | Celah atau Edge Case | Rekomendasi Terukur |
6. Validasi bahwa setiap FR memiliki Main/Happy Path, Alternative/Edge Case,
   serta Exception/Error Handling. Sebutkan FR yang gagal memenuhi syarat.
7. Output hanya laporan audit dengan heading Markdown tebal, tabel, dan action
   item P0/P1/P2. Tanpa salam, tanpa penutup, tanpa kata-kata generik.
""",
        expected_output=(
            "Laporan Red Team kritis berisi rubrik Completeness/Ambiguity/"
            "Feasibility 1-10, tabel temuan severity, audit privacy UU PDP/GDPR, "
            "validasi BDD/data/API, dan action items P0/P1/P2."
        ),
        agent=auditor_agent,
        context=[srs_task],
    )

    return [srs_task, audit_task]
