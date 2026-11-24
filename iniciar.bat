@echo off
rem Arranca la app y la reinicia sola si se cae. Un solo proceso (ver README: "Un solo proceso").
cd /d "%~dp0"
:inicio
python -m uvicorn main:app --host 0.0.0.0 --port 8000
echo.
echo La app se detuvo. Se reinicia en 5 segundos (cerrá esta ventana o Ctrl+C para salir)...
timeout /t 5 >nul
goto inicio
