import sqlite3

DB_NAME = "database.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Tabel Users (Termasuk kolom api_key)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            api_key TEXT DEFAULT ''
        )
    ''')
    
    # Coba alter table jika database lama belum memiliki kolom api_key
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN api_key TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass # Kolom sudah ada
    
    # 2. Tabel History SRS
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS srs_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            judul TEXT NOT NULL,
            studi_kasus TEXT NOT NULL,
            hasil_srs TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # 3. Akun Admin Default
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password, role) VALUES ('admin', 'AdminPro123!', 'admin')")
    
    conn.commit()
    conn.close()

def register_user(username, password):
    """Mendaftarkan akun baru ke database"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO users (username, password, role, api_key) VALUES (?, ?, ?, ?)', 
            (username.strip(), password.strip(), 'user', '')
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def login_user(username, password):
    """Melakukan verifikasi login pengguna"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, username, role, api_key FROM users WHERE username = ? AND password = ?', 
        (username.strip(), password.strip())
    )
    user = cursor.fetchone()
    conn.close()
    return user

def get_user_by_username(username):
    """Mengambil data user berdasarkan username (dipakai saat refresh browser)"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, role, api_key FROM users WHERE username = ?', (username.strip(),))
    user = cursor.fetchone()
    conn.close()
    return user

def update_api_key(user_id, new_key):
    """Menyimpan API Key ke database user secara permanen"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET api_key = ? WHERE id = ?', (new_key, user_id))
    conn.commit()
    conn.close()

def save_srs(user_id, judul, studi_kasus, hasil_srs):
    """Menyimpan dokumen SRS baru dan mengembalikan ID barunya"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO srs_history (user_id, judul, studi_kasus, hasil_srs)
        VALUES (?, ?, ?, ?)
    ''', (user_id, judul, studi_kasus, hasil_srs))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def save_srs_version(user_id, judul, studi_kasus, hasil_srs, version=1, parent_id=None):
    """Menyimpan versi revisi dokumen SRS"""
    return save_srs(user_id, judul, studi_kasus, hasil_srs)

def delete_srs(srs_id, user_id):
    """Menghapus dokumen SRS berdasarkan ID dan User ID"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM srs_history WHERE id = ? AND user_id = ?', (srs_id, user_id))
    conn.commit()
    conn.close()

def get_user_srs_history(user_id):
    """Mengambil riwayat SRS milik user tertentu"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, user_id, judul, studi_kasus, hasil_srs, created_at 
        FROM srs_history 
        WHERE user_id = ? 
        ORDER BY created_at DESC
    ''', (user_id,))
    data = cursor.fetchall()
    conn.close()
    return data

# Alias fungsi untuk kompatibilitas panggilan di app.py
def get_user_history(user_id):
    return get_user_srs_history(user_id)

def get_admin_metrics():
    """Mengambil metrik ringkas untuk admin"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'user'")
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM srs_history')
    total_srs = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT u.username, COUNT(s.id) as total 
        FROM srs_history s 
        JOIN users u ON s.user_id = u.id 
        GROUP BY s.user_id 
        ORDER BY total DESC LIMIT 1
    ''')
    top_user = cursor.fetchone()
    top_user_name = top_user[0] if top_user else "-"
    
    conn.close()
    return total_users, total_srs, top_user_name

def get_daily_srs_stats():
    """Mengambil statistik pembuatan SRS harian"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DATE(created_at) as tgl, COUNT(*) as jumlah
        FROM srs_history
        GROUP BY DATE(created_at)
        ORDER BY tgl ASC
    ''')
    data = cursor.fetchall()
    conn.close()
    return data

def get_all_users_stats():
    """Mengambil statistik jumlah SRS per pengguna"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.id, u.username, COUNT(s.id) as total_srs
        FROM users u
        LEFT JOIN srs_history s ON u.id = s.user_id
        WHERE u.role = 'user'
        GROUP BY u.id
        ORDER BY total_srs DESC
    ''')
    data = cursor.fetchall()
    conn.close()
    return data

def get_all_srs_global():
    """Mengambil seluruh log SRS secara global untuk admin"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.id, u.username, s.judul, s.created_at, s.hasil_srs 
        FROM srs_history s
        JOIN users u ON s.user_id = u.id
        ORDER BY s.created_at DESC
    ''')
    data = cursor.fetchall()
    conn.close()
    return data

def get_advanced_admin_metrics():
    """Mengambil metrik analitik lengkap untuk dashboard admin"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'user'")
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM srs_history')
    total_srs = cursor.fetchone()[0]
    
    cursor.execute('SELECT AVG(LENGTH(hasil_srs)) FROM srs_history')
    avg_length = cursor.fetchone()[0]
    avg_chars = int(avg_length) if avg_length else 0
    
    conn.close()
    return total_users, total_srs, avg_chars