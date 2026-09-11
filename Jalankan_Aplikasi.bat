@echo off
cd /d "D:\Agent AI"

:: Matikan proses python lama agar port tidak terkunci
taskkill /F /IM python.exe >nul 2>&1

:: Jalankan Portal Publik & Admin tanpa auto-open bawaan Streamlit
start "Portal Publik" cmd /k ".\venv\Scripts\activate && py -m streamlit run app.py --server.port 8501 --server.headless true"
start "Portal Admin" cmd /k ".\venv\Scripts\activate && py -m streamlit run admin_app.py --server.port 8502 --server.headless true"

:: Tunggu 3 detik agar server siap, lalu buka persis 2 tab di browser
timeout /t 3 /nobreak >nul
start http://localhost:8501
start http://localhost:8502