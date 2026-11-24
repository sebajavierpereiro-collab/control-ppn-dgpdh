"""Despacho: número de nota y de expediente + PDF.

Mesa de Entrada carga, por documento, el número de nota y el de expediente.
El sistema los inserta en una COPIA del documento firmado (el firmado
original queda intacto) y genera el PDF. Se puede corregir y regenerar
hasta que se despacha.

`obtener_numero_nota()` es el único punto por donde se decide el número de
una nota: hoy lo escribe una persona; cuando exista una fuente automática
(por ejemplo el sistema de mesa de entrada), solo cambia esta función.
"""
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from docx import Document

# --- validación -----------------------------------------------------
_NUMERO_OK = re.compile(r"^[0-9A-Za-zÁÉÍÓÚÑáéíóúñ][0-9A-Za-zÁÉÍÓÚÑáéíóúñ\-/\.\s]{0,38}$")
MAX_LARGO = 40


def normalizar_numero(valor):
    return re.sub(r"\s+", " ", (valor or "").strip())


def validar_numero(valor, etiqueta):
    """Devuelve (numero_normalizado, error|None)."""
    n = normalizar_numero(valor)
    if not n:
        return n, f"Falta el {etiqueta}."
    if not _NUMERO_OK.match(n):
        return n, f"El {etiqueta} tiene caracteres no válidos (solo letras, números, - / . y espacios; hasta {MAX_LARGO})."
    return n, None


def obtener_numero_nota(nota, ingresado=None):
    """Punto único para obtener el número de una nota (ver docstring)."""
    return normalizar_numero(ingresado)


# --- ubicación de las líneas en la plantilla ------------------------
_ETIQUETA = re.compile(
    r"^\s*(?P<etq>nota|expte\.?|expediente|ex\.?)\s*(?:n[°ºo]\.?|nro\.?|n[úu]mero)\s*[:\-]?\s*(?P<resto>.*)$",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"^[\s_\[\]\.\-xX\u2026]*$")
_NUMERO_CORTO = re.compile(r"^[0-9A-Za-z\-/\.]{1,25}$")
ZONA_ENCABEZADO = 20  # solo se buscan en los primeros párrafos no vacíos


def _tipo_etiqueta(etq):
    return "nota" if etq.lower().startswith("nota") else "expte"


def _parrafos_zona(doc):
    vistos = 0
    for p in doc.paragraphs:
        if p.text.strip():
            vistos += 1
            if vistos > ZONA_ENCABEZADO:
                break
            yield p
    for t in doc.tables[:3]:
        for fila in t.rows[:6]:
            for celda in fila.cells:
                for p in celda.paragraphs:
                    if p.text.strip():
                        yield p


def localizar_lineas(doc):
    """{'nota': parrafo|None, 'expte': parrafo|None, 'prefijo': {...}}"""
    res = {"nota": None, "expte": None, "etiqueta": {}}
    for p in _parrafos_zona(doc):
        m = _ETIQUETA.match(p.text)
        if not m:
            continue
        resto = m.group("resto").strip()
        if not (_PLACEHOLDER.match(resto) or _NUMERO_CORTO.match(resto)):
            continue  # es una oración que casualmente empieza con "Nota N°..."
        tipo = _tipo_etiqueta(m.group("etq"))
        if res[tipo] is None:
            res[tipo] = p
            res["etiqueta"][tipo] = p.text[: m.start("resto")].rstrip()
    return res


def _escribir_linea(parrafo, etiqueta, numero):
    # conserva el formato del primer run
    texto = f"{etiqueta} {numero}".strip()
    if not parrafo.runs:
        parrafo.add_run(texto)
        return
    parrafo.runs[0].text = texto
    for r in parrafo.runs[1:]:
        r.text = ""


def _insertar_arriba(doc, texto):
    primero = doc.paragraphs[0] if doc.paragraphs else None
    if primero is None:
        doc.add_paragraph(texto)
        return
    nuevo = primero.insert_paragraph_before(texto)
    return nuevo


def completar_documento(ruta_firmada, ruta_salida, nro_nota, nro_expte):
    """Copia el firmado en `ruta_salida` con los números insertados. Devuelve
    la lista de avisos (líneas que no se encontraron y se agregaron arriba)."""
    doc = Document(str(ruta_firmada))
    lineas = localizar_lineas(doc)
    avisos = []
    pedidos = (("nota", "Nota N°", nro_nota), ("expte", "Expte. N°", nro_expte))
    faltantes = []
    for tipo, etq_defecto, numero in pedidos:
        if not numero:
            continue
        p = lineas[tipo]
        if p is not None:
            _escribir_linea(p, lineas["etiqueta"].get(tipo, etq_defecto), numero)
        else:
            faltantes.append(f"{etq_defecto} {numero}")
            avisos.append(
                f"No encontré la línea «{etq_defecto}» en el documento: la agregué arriba de todo."
            )
    for texto in reversed(faltantes):
        _insertar_arriba(doc, texto)
    Path(ruta_salida).parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(ruta_salida))
    return avisos


# --- PDF ------------------------------------------------------------
def _soffice():
    for nombre in ("soffice", "libreoffice"):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    for c in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if Path(c).is_file():
            return c
    return None


def guardar_como_word(doc, destino, formato):
    """Guarda un documento abierto de Word en otro formato. Las versiones nuevas de Word (365/2016+) ya no exponen
    «SaveAs» al acceso dinámico de Python (el error se ve como «Open.SaveAs»): se usa ExportAsFixedFormat para PDF y
    SaveAs2 para el resto, y SaveAs solo como último recurso."""
    destino = str(Path(destino).resolve())
    errores = []
    pasos = []
    if formato == 17:
        pasos.append(lambda: doc.ExportAsFixedFormat(destino, 17))
    pasos += [lambda: doc.SaveAs2(destino, formato), lambda: doc.SaveAs(destino, formato)]
    for paso in pasos:
        try:
            paso()
            return
        except Exception as e:
            errores.append(str(e))
    raise RuntimeError(" / ".join(errores))


def _pdf_con_word(docx, pdf):
    import pythoncom  # noqa
    import win32com.client as win32
    pythoncom.CoInitialize()
    # instancia propia de Word (DispatchEx): no usa ni cierra el Word que la persona tenga abierto
    word = win32.DispatchEx("Word.Application")
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        d = word.Documents.Open(str(Path(docx).resolve()), False, True)  # FileName, ConfirmConversions, ReadOnly
        try:
            guardar_como_word(d, pdf, 17)  # wdFormatPDF
        finally:
            d.Close(False)
    finally:
        try:
            word.Quit(0)
        except Exception:
            pass
        pythoncom.CoUninitialize()


def _pdf_con_soffice(docx, pdf):
    from dgpdh.servicios import herramientas_locales  # propuesta 7: unoserver, si está configurado y responde
    if herramientas_locales.unoconvert(Path(docx), Path(pdf), "pdf"):
        return
    exe = _soffice()
    if not exe:
        raise RuntimeError("no hay LibreOffice instalado")
    with tempfile.TemporaryDirectory() as tmp:
        perfil = Path(tmp) / "perfil"
        r = subprocess.run(
            [exe, f"-env:UserInstallation={perfil.resolve().as_uri()}", "--headless",
             "--convert-to", "pdf", "--outdir", tmp, str(docx)],
            capture_output=True, timeout=120,
        )
        generado = Path(tmp) / (Path(docx).stem + ".pdf")
        if not generado.is_file():
            raise RuntimeError(f"LibreOffice no generó el PDF ({r.stderr.decode(errors='ignore')[:200]})")
        shutil.move(str(generado), str(pdf))


def convertir_a_pdf(docx, pdf, lock=None):
    """Word si está (Windows, mismo motor que usa la oficina); si no,
    LibreOffice. Levanta RuntimeError con un mensaje claro si ninguno sirve."""
    errores = []
    metodos = []
    if sys.platform.startswith("win"):
        metodos.append(("Word", _pdf_con_word))
    metodos.append(("LibreOffice", _pdf_con_soffice))
    for nombre, fn in metodos:
        try:
            fn(docx, pdf)
            if Path(pdf).is_file():
                return nombre
        except Exception as e:
            logging.warning(f"PDF con {nombre} falló: {e}")
            errores.append(f"{nombre}: {e}")
    raise RuntimeError("No se pudo generar el PDF (" + "; ".join(errores) + ")")


# --- propuesta de expediente desde el texto --------------------------
_EXPTE_TXT = re.compile(
    r"\b(?:expte\.?|expediente|ex)\s*+(?:n[°ºo]\.?|nro\.?)?\s*+[:\-]?\s*+([0-9][0-9A-Za-z\-/\.]{2,30})",
    re.IGNORECASE,
)


# Expedientes con sigla de tipo: «E.T.: 6116/26», «ET 6157», «ET 6035.26» (expedientes de tortura), «E.I. 123/25»…
_EXPTE_TIPO = re.compile(r"(?<![A-Za-z])(E\.?\s?[TIAPC])\.?\s*+(?:N[°ºo]\.?|Nro\.?)?\s*+[:\-]?\s*+(\d{2,6})(?:\s*+[/\.]\s*+(\d{2,4}))?(?![\d])")


_EXPTE_GDE = re.compile(r"\bEX-\d{4}-\d{4,10}-[A-Z0-9#\-]{2,30}", re.IGNORECASE)  # expediente electrónico (GDE)


def _expte_con_tipo(texto):
    cuentan = Counter()
    for m in _EXPTE_TIPO.finditer(texto or ""):
        sigla = re.sub(r"[\s\.]", "", m.group(1)).upper()
        cuentan[f"{sigla} {m.group(2)}" + (f"/{m.group(3)[-2:]}" if m.group(3) else "")] += 1
    if not cuentan:
        return ""
    # «ET 6116/26» y «ET 6116» son el mismo: gana la forma con año
    mejor = max(cuentan, key=lambda k: (sum(v for kk, v in cuentan.items() if kk.split("/")[0] == k.split("/")[0]), "/" in k))
    return mejor


def lleva_numero(nota):
    """Solo las notas llevan número de nota; los informes, proveídos y demás documentos no."""
    return (bool(nota.get("es_nota")) or bool(nota.get("nro_nota")) or "NOTA" in str(nota.get("nombre_original") or "").upper()
            or str(nota.get("tipo") or "").upper().startswith("NOTA"))


def proponer_expediente(texto, nombre_archivo=""):
    """Número de expediente (con su tipo, si lo trae: «ET 6116/26») más mencionado en el texto; si no, el del nombre del
    archivo. Es una propuesta: Mesa la verifica."""
    gde = Counter(m.group(0).upper().rstrip("-") for m in _EXPTE_GDE.finditer(texto or ""))
    if gde:
        return gde.most_common(1)[0][0]
    con_tipo = _expte_con_tipo(texto)
    if con_tipo:
        return con_tipo
    cuentan = Counter(m.group(1).rstrip(".-/") for m in _EXPTE_TXT.finditer(texto or ""))
    if cuentan:
        return cuentan.most_common(1)[0][0]
    return _expte_con_tipo(nombre_archivo or "")


# --- destinatario propuesto desde el proveído ------------------------
_DEST = re.compile(r"^\s*(al|a la|a los|a las|sr\.?|sra\.?|se[ñn]or(a)?|dr\.?|dra\.?)\b\s*(.*)$", re.IGNORECASE)
_SD = re.compile(r"\s*+(s\s*+/\s*+d|s\s*+_+\s*+d|presente)\.?\s*+$", re.IGNORECASE)


def proponer_destinatario(texto, limite_lineas=15):
    """Primera línea del encabezado que parece un destinatario («Al Sr.…»)."""
    vistas = 0
    for linea in (texto or "").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        vistas += 1
        if vistas > limite_lineas:
            break
        if _DEST.match(linea) and len(linea) < 140:
            return _SD.sub("", linea).strip(" .,:;-")
    return ""


def _tokens(s):
    return {t for t in re.findall(r"[a-záéíóúñ]{3,}", (s or "").lower())} - {"del", "los", "las", "para"}


def mejor_contacto(destinatario, contactos):
    """Contacto de la agenda que más se parece al destinatario propuesto
    (al menos 2 palabras en común, o 1 si el nombre es de una sola palabra)."""
    base = _tokens(destinatario)
    mejor, puntos = None, 0
    for c in contactos:
        t = _tokens(c["nombre"])
        comun = len(base & t)
        necesarios = 1 if len(t) <= 1 else 2
        if comun >= necesarios and (comun, c.get("usos", 0)) > (puntos, mejor.get("usos", 0) if mejor else -1):
            mejor, puntos = c, comun
    return mejor


def armar_borrador_eml(destinatario, correo, asunto, cuerpo, adjuntos):
    """Borrador .eml (se abre en Outlook como mensaje sin enviar): la persona
    lo revisa y lo envía ella. adjuntos: [(ruta, nombre)]."""
    from email.message import EmailMessage
    m = EmailMessage()
    m["To"] = f"{destinatario} <{correo}>" if destinatario and correo else (correo or "")
    m["Subject"] = asunto
    m["X-Unsent"] = "1"
    m.set_content(cuerpo)
    for ruta, nombre in adjuntos:
        datos = Path(ruta).read_bytes()
        tipo = ("application", "pdf") if str(nombre).lower().endswith(".pdf") else \
               ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")
        m.add_attachment(datos, maintype=tipo[0], subtype=tipo[1], filename=nombre)
    return bytes(m)
