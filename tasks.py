from crewai import Task

def create_tasks(studi_kasus, ba_agent, auditor_agent):
    srs_task = Task(
        description=f"""
        Analisislah studi kasus berikut dan buatlah dokumen System Requirement Specification (SRS) lengkap berstandar ISO/IEC:
        "{studi_kasus}"
        
        ATURAN MUTLAK DOKUMEN:
        1. Buat struktur SRS standar lengkap:
           - 1. Pendahuluan (Tujuan, Ruang Lingkup)
           - 2. Deskripsi Umum (Arsitektur Sistem, Role & RBAC)
           - 3. Kebutuhan Fungsional (User Stories & Acceptance Criteria)
           - 4. Kebutuhan Non-Fungsional (Performa, Keamanan, Availability)
           - 5. Skema Basis Data PostgreSQL
           - 6. Entity Relationship Diagram (ERD)
           
        2. Pada bagian ERD, WAJIB memasukkan SELURUH sintaks relasi dan struktur tabel ke dalam SATU BLOK KODE MERMAID KETAT seperti ini:
        
        ```mermaid
        erDiagram
            PATIENTS ||--o{{ VISITS : makes
            VISITS ||--o| VITAL_SIGNS : has
            
            PATIENTS {{
                uuid patient_id PK
                string full_name
            }}
            VISITS {{
                uuid visit_id PK
                string status
            }}
        ```
        DILARANG KERAS MEMISAH KODE RELASI DAN STRUKTUR TABEL KE BLOK TERPISAH!
        """,
        expected_output="Dokumen SRS draft lengkap dengan diagram ERD Mermaid utuh dalam satu blok.",
        agent=ba_agent
    )

    audit_task = Task(
        description="""
        Audit dokumen SRS dari Business Analyst yang tersedia pada konteks task.
        Bandingkan langsung terhadap cerita kasus asli. Buat HANYA laporan audit kritis
        sesuai struktur wajib dalam instruksi Anda; jangan menulis ulang SRS.
        """,
        expected_output="Laporan Red Team audit yang memuat skor, temuan, risiko, dan action items terukur.",
        agent=auditor_agent,
        context=[srs_task],
    )

    return [srs_task, audit_task]
