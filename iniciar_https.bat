@echo off
rem Arranca la app con conexion cifrada (HTTPS) en el puerto 8443. Antes: python https_interno.py (ver README).
cd /d "%~dp0"
if not exist certs\servidor.crt (
  echo Falta el certificado. Corre primero:  python https_interno.py --nombre NOMBRE-DEL-SERVIDOR --ip IP-DEL-SERVIDOR
  pause
  exit /b 1
)
:inicio
python -m uvicorn main:app --host 0.0.0.0 --port 8443 --ssl-keyfile certs\servidor.key --ssl-certfile certs\servidor.crt
echo.
echo La app se detuvo. Se reinicia en 5 segundos (cerra esta ventana o Ctrl+C para salir)...
timeout /t 5 >nul
goto inicio
