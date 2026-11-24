"""Servidor Linux: .doc → .docx con LibreOffice (sin Word) y rutas de Windows reubicadas al mudar la carpeta de datos."""
import sys, tempfile, shutil, subprocess
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import nucleo
from docx import Document

# 1) rutas guardadas en Windows → carpeta de datos nueva (Linux)
datos = tmp / "datos"; (datos / "originales" / "2026" / "10").mkdir(parents=True); (datos / "despacho" / "7").mkdir(parents=True)
(datos / "originales" / "2026" / "10" / "7_nota.docx").write_bytes(b"x"); (datos / "despacho" / "7" / "n - despacho.pdf").write_bytes(b"x")
nid = db.crear_nota("nota.docx", db.crear_equipo("E"), r"C:\Users\usuario\Desktop\notas web\dgpdh_web\datos\originales\2026\10\7_nota.docx")
with db._conexion() as con:
    con.execute("UPDATE notas SET ruta_despacho_pdf = ? WHERE id = ?", (r"C:\Users\usuario\Desktop\notas web\dgpdh_web\datos\despacho\7\n - despacho.pdf", nid))
assert db.reubicar_rutas(datos) == 2
n = db.obtener_nota(nid)
assert n["ruta_original"] == str(datos / "originales" / "2026" / "10" / "7_nota.docx") and n["ruta_despacho_pdf"] == str(datos / "despacho" / "7" / "n - despacho.pdf")
assert db.reubicar_rutas(datos) == 0, "una sola vez"
print("rutas OK")

# 2) .doc sin Word: LibreOffice
if shutil.which("soffice") or shutil.which("libreoffice"):
    d = Document(); d.add_paragraph("Al Sr. Director: texto de prueba."); d.save(tmp / "base.docx")
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    subprocess.run([exe, f"-env:UserInstallation={(tmp / 'p').as_uri()}", "--headless", "--convert-to", "doc", "--outdir", str(tmp), str(tmp / "base.docx")], capture_output=True, timeout=180)
    assert (tmp / "base.doc").is_file()
    nucleo.WIN32_DISPONIBLE = False
    salida = nucleo.convertir_doc_a_docx(tmp / "base.doc")
    assert Path(salida) == tmp / "base.docx" or Path(salida).name == "base.docx"
    assert "texto de prueba" in "\n".join(p.text for p in Document(salida).paragraphs)
    print(".doc con LibreOffice OK")
else:
    print("(sin LibreOffice en este equipo: se omite la prueba de .doc)")

# 3) migración: exportar los datos en la PC y verificar el servidor
import zipfile, importlib.util
sys.path.insert(0, str(Path(APP) / "linux"))
import exportar_datos, verificar_servidor
app = tmp / "app"; (app / "datos" / "originales").mkdir(parents=True); (app / "datos" / "respaldos").mkdir(); (app / "static").mkdir(); (app / "linux").mkdir()
shutil.copy(db.RUTA_DB, app / "datos" / "dgpdh.db")
(app / "datos" / "originales" / "a.docx").write_bytes(b"doc"); (app / "datos" / "respaldos" / "grande.db").write_bytes(b"x" * 10)
(app / "datos" / "trabajador.lock").write_bytes(b""); (app / "datos" / "dgpdh.db-wal").write_bytes(b"")
lt = tmp / "lt"; (lt / "LanguageTool-6.4").mkdir(parents=True); (lt / "LanguageTool-6.4" / "languagetool.jar").write_bytes(b"jar")
z = exportar_datos.exportar(tmp / "m.zip", datos=app / "datos", lt=lt, avisar=lambda *_: None)
nombres = zipfile.ZipFile(z).namelist()
assert "datos/dgpdh.db" in nombres and "datos/originales/a.docx" in nombres and "MANIFIESTO.txt" in nombres
assert "languagetool/LanguageTool-6.4/languagetool.jar" in nombres
assert not any(n.startswith("datos/respaldos") or n.endswith(("-wal", "trabajador.lock")) for n in nombres), nombres
assert "SHA-256" in zipfile.ZipFile(z).read("MANIFIESTO.txt").decode()
# servidor: config con rutas de Windows → FALTA; con rutas válidas → sin esas faltas
(app / "linux" / "iniciar.sh").write_bytes(b"#!/bin/bash\r\necho\r\n")
(app / "config.ini").write_text("[firma]\nnombre_firmante = X\narchivo_firma = \\\\fsppn\\e\\firma.PNG\n\n[respaldos]\ncarpeta = C:\\respaldos\n\n[ollama]\nusar_ia = false\n\n[sesiones]\nclave_secreta = abc\n", encoding="utf-8")
verificar_servidor.LINUX = True
inf = verificar_servidor.verificar(app, imprimir=False)
textos = " | ".join(f"{e}:{t}" for e, t, _ in inf.lineas)
assert "FALTA:config.ini: la firma apunta a una ruta de Windows" in textos and "FALTA:config.ini: [respaldos] carpeta es una ruta de Windows" in textos, textos
assert "saltos de línea de Windows" in textos and "Falta static/htmx.min.js" in textos and "IA desactivada" in textos
firma = tmp / "firma.png"; firma.write_bytes(b"png"); resp = tmp / "resp"
(app / "config.ini").write_text(f"[firma]\nnombre_firmante = X\narchivo_firma = {firma}\n\n[respaldos]\ncarpeta = {resp}\n\n[ollama]\nusar_ia = false\n\n[sesiones]\nclave_secreta = abc\n", encoding="utf-8")
(app / "static" / "htmx.min.js").write_text("//")
inf = verificar_servidor.verificar(app, imprimir=False)
textos = " | ".join(f"{e}:{t}" for e, t, _ in inf.lineas)
assert "OK:Firma accesible" in textos and "OK:Respaldos: se puede escribir" in textos and "OK:htmx local" in textos and "OK:Base de datos:" in textos, textos
print("migración: exportar datos y verificar servidor OK")
