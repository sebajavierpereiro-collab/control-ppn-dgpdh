"""Circuito y despacho (ola 3 del plan de reformas).

  * Numeración correlativa anual de notas y proveídos: el sistema PROPONE el próximo número y Mesa de Entrada
    lo puede cambiar. Se puede tomar como punto de partida el Excel donde hoy se registran las notas.
  * Vista del documento con formato (docx → HTML) para revisar sin descargar.
  * Plantillas de notas con campos a completar.
  * Exportación a PDF/A para el archivo definitivo.
  * Tiempos del circuito por equipo e informe trimestral / anual en borrador.
"""
import html
import io
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

import database as db

CONFIG = {"formato_nota": "{numero}/{anio}", "formato_proveido": "{numero}/{anio}", "excel_registro": "", "pdfa": False}


def configurar(config):
    CONFIG["formato_nota"] = config.get("numeracion", "formato_nota", fallback="{numero}/{anio}").strip() or "{numero}/{anio}"
    CONFIG["formato_proveido"] = config.get("numeracion", "formato_proveido", fallback="{numero}/{anio}").strip() or "{numero}/{anio}"
    CONFIG["excel_registro"] = config.get("numeracion", "excel_registro", fallback="").strip()
    try:
        CONFIG["pdfa"] = config.getboolean("archivo", "pdfa_en_despacho", fallback=False)
    except ValueError:
        CONFIG["pdfa"] = False


# =====================================================================
# 56) NUMERACIÓN CORRELATIVA ANUAL
# =====================================================================
TIPOS_NUMERO = {"NOTA": "Notas", "PROVEIDO": "Proveídos"}
_RE_NUM_ANIO = re.compile(r"(?<!\d)(\d{1,6})\s*[/\-]\s*(\d{4}|\d{2})(?!\d)")
_RE_ANIO_NUM = re.compile(r"(?<!\d)(20\d{2})\s*[/\-]\s*(\d{1,6})(?!\d)")


def leer_numero(texto, anio_por_defecto=None):
    """'123/2026', '123-26', 'NO-2026-123', '123' -> (123, 2026) o (None, None)."""
    t = str(texto or "").strip()
    if not t:
        return None, None
    m = _RE_ANIO_NUM.search(t)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = _RE_NUM_ANIO.search(t)
    if m:
        anio = int(m.group(2))
        return int(m.group(1)), (anio + 2000 if anio < 100 else anio)
    m = re.search(r"(?<!\d)(\d{1,6})(?!\d)", t)
    if m:
        return int(m.group(1)), anio_por_defecto
    return None, None


def formatear(tipo, numero, anio):
    fmt = CONFIG["formato_proveido" if tipo == "PROVEIDO" else "formato_nota"]
    try:
        return fmt.format(numero=numero, anio=anio, anio2=str(anio)[-2:])
    except (KeyError, IndexError, ValueError):
        return f"{numero}/{anio}"


def usados_en_el_sistema(tipo, anio):
    """Números ya usados en el año (notas despachadas o con número cargado; proveídos registrados)."""
    usados = set()
    for texto, fecha in db.numeros_cargados(tipo):
        n, a = leer_numero(texto, int(str(fecha)[:4]) if fecha else None)
        if n and a == anio:
            usados.add(n)
    return usados


def proponer(tipo="NOTA", anio=None, saltear=(), usados_base=None):
    """Próximo número libre: el mayor entre el contador (o el Excel de registro) y lo usado en el sistema, + 1.
    `saltear` son números ya reservados en la misma pantalla (para proponer un lote); `usados_base`, los usados en el
    sistema ya calculados (para no recorrer todas las notas una vez por documento)."""
    anio = anio or date.today().year
    if db.contador_inicial(anio, tipo) is None:
        return None  # sin base: el número sale del registro de Mesa de Entradas (se carga en «Numeración»)
    usados = (usados_base if usados_base is not None else usados_en_el_sistema(tipo, anio)) | set(saltear)
    base = max([db.contador_numeracion(anio, tipo) or 0] + list(usados))
    n = base + 1
    while n in usados:
        n += 1
    return n


def proponer_texto(tipo="NOTA", anio=None, saltear=()):
    anio = anio or date.today().year
    n = proponer(tipo, anio, saltear)
    return formatear(tipo, n, anio) if n else ""


def registrar_uso(tipo, texto, anio=None, quien=""):
    """Después de usar un número: si es el mayor del año, avanza el contador (nunca lo hace retroceder)."""
    n, a = leer_numero(texto, anio or date.today().year)
    if n and a:
        db.avanzar_contador(a, tipo, n, quien)
    return n, a


def salteados(tipo="NOTA", anio=None):
    """Números que faltan entre 1 y el último usado (para ver si quedó alguno sin usar o anulado)."""
    anio = anio or date.today().year
    usados = usados_en_el_sistema(tipo, anio)
    tope = max([db.contador_numeracion(anio, tipo) or 0] + list(usados))
    desde = db.contador_inicial(anio, tipo) or 0
    return [n for n in range(desde + 1, tope + 1) if n not in usados][:200]


def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", str(t or "")) if unicodedata.category(c) != "Mn").lower()


def leer_excel_registro(ruta_o_bytes, nombre=""):
    """Lee el Excel donde hoy se registran las notas y devuelve {(anio, tipo): mayor número}. No supone columnas:
    busca un encabezado con «nota» / «n°» / «número» (y «proveído» si lo hay) y una columna de fecha o año."""
    filas = _filas_planilla(ruta_o_bytes, nombre)
    resultado = {}
    encabezado, idx = None, {}
    for i, fila in enumerate(filas[:30]):
        celdas = [_sin_acentos(c) for c in fila]
        if sum(1 for c in celdas if c) >= 2 and any(re.search(r"\b(nota|n[°ºo]|nro|numero)\b", c) for c in celdas):
            encabezado, idx = i, {}
            for j, c in enumerate(celdas):
                if re.search(r"provei", c):
                    idx.setdefault("PROVEIDO", j)
                elif re.search(r"\bnota\b|n[°ºo]\s*de\s*nota|^n[°ºo]$|^nro|^numero", c) and "expte" not in c and "expediente" not in c:
                    idx.setdefault("NOTA", j)
                elif re.search(r"fecha", c):
                    idx.setdefault("FECHA", j)
                elif re.search(r"\bano\b|\baño\b|anio", c):
                    idx.setdefault("ANIO", j)
            if "NOTA" in idx or "PROVEIDO" in idx:
                break
    if encabezado is None:
        return resultado
    for fila in filas[encabezado + 1:]:
        anio = None
        if "ANIO" in idx and idx["ANIO"] < len(fila):
            m = re.search(r"(20\d{2})", str(fila[idx["ANIO"]] or ""))
            anio = int(m.group(1)) if m else None
        if anio is None and "FECHA" in idx and idx["FECHA"] < len(fila):
            v = fila[idx["FECHA"]]
            if isinstance(v, (datetime, date)):
                anio = v.year
            else:
                m = re.search(r"(20\d{2})", str(v or ""))
                anio = int(m.group(1)) if m else None
        for tipo in ("NOTA", "PROVEIDO"):
            if tipo in idx and idx[tipo] < len(fila) and fila[idx[tipo]] not in (None, ""):
                v = fila[idx[tipo]]
                n, a = (int(v), anio) if isinstance(v, (int, float)) and float(v).is_integer() else leer_numero(v, anio)
                if n and a:
                    resultado[(a, tipo)] = max(resultado.get((a, tipo), 0), n)
    return resultado


def _filas_planilla(ruta_o_bytes, nombre=""):
    if isinstance(ruta_o_bytes, (bytes, bytearray)):
        datos, nombre = bytes(ruta_o_bytes), nombre.lower()
    else:
        datos, nombre = Path(ruta_o_bytes).read_bytes(), str(ruta_o_bytes).lower()
    if nombre.endswith(".xls"):
        import xlrd
        libro = xlrd.open_workbook(file_contents=datos)
        filas = []
        for hoja in libro.sheets():
            for i in range(hoja.nrows):
                fila = []
                for c in hoja.row(i):
                    if c.ctype == xlrd.XL_CELL_DATE:
                        fila.append(datetime(*xlrd.xldate_as_tuple(c.value, libro.datemode)))
                    else:
                        fila.append(c.value)
                filas.append(fila)
        return filas
    import openpyxl
    libro = openpyxl.load_workbook(io.BytesIO(datos), read_only=True, data_only=True)
    filas = []
    for hoja in libro.worksheets:
        filas.extend([list(f) for f in hoja.iter_rows(values_only=True)])
    return filas


def exportar_registro(anio):
    """Registro de notas del año en Excel (para pegar o comparar con la planilla que ya usan)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Notas {anio}"
    cab = ["N° de nota", "Fecha", "Destinatario", "Expediente", "Asunto", "Equipo", "Asesor/a", "Envío", "Estado"]
    ws.append(cab)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="7C1413")
    filas = []
    for n in db.notas_con_numero():
        num, a = leer_numero(n["nro_nota"], int(str(n.get("fecha_nota") or n["creado_en"])[:4]))
        if a != anio:
            continue
        filas.append((num or 0, n))
    for num, n in sorted(filas, key=lambda x: x[0]):
        resumen = (n.get("resumen") or "").split("\n")[0]
        fecha = n.get("fecha_nota") or str(n["creado_en"])[:10]
        try:
            fecha = date.fromisoformat(str(fecha)[:10])
        except ValueError:
            pass
        ws.append([n["nro_nota"], fecha, n.get("destinatario") or "", n.get("nro_expte") or "", resumen or n["nombre_original"],
                   n.get("equipo_nombre") or "", n.get("asesor_nombre") or "", n.get("envio") or "", n["estado"].capitalize()])
        ws.cell(row=ws.max_row, column=2).number_format = "DD/MM/YYYY"
    for col, ancho in zip("ABCDEFGHI", (14, 12, 40, 18, 60, 22, 22, 16, 12)):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =====================================================================
# 29) VISTA DEL DOCUMENTO CON FORMATO (docx → HTML seguro)
# =====================================================================
def docx_a_html(ruta, max_parrafos=600):
    """HTML del documento (negritas, cursivas, subrayado, alineación, títulos y tablas). Todo el texto se escapa:
    no se ejecuta nada del archivo. Las imágenes (firma, logos) se marcan con un recuadro."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document(str(ruta))
    alin = {WD_ALIGN_PARAGRAPH.CENTER: "center", WD_ALIGN_PARAGRAPH.RIGHT: "right", WD_ALIGN_PARAGRAPH.JUSTIFY: "justify"}
    partes = []

    def parrafo(p):
        trozos = []
        for r in p.runs:
            t = html.escape(r.text)
            if r._element.xpath(".//w:drawing") or r._element.xpath(".//w:pict"):
                trozos.append('<span class="doc-imagen">[imagen]</span>')
            if not t:
                continue
            if r.bold:
                t = f"<strong>{t}</strong>"
            if r.italic:
                t = f"<em>{t}</em>"
            if r.underline:
                t = f"<u>{t}</u>"
            trozos.append(t)
        contenido = "".join(trozos) or "&nbsp;"
        estilo = (p.style.name or "").lower() if p.style is not None else ""
        a = alin.get(p.alignment) or (alin.get(p.style.paragraph_format.alignment) if p.style is not None else None)
        attr = f' style="text-align:{a}"' if a else ""
        if estilo.startswith("heading") or estilo.startswith("título") or estilo == "title":
            return f"<h4{attr}>{contenido}</h4>"
        return f"<p{attr}>{contenido}</p>"

    cuerpo = doc.element.body
    n = 0
    for hijo in cuerpo.iterchildren():
        etiqueta = hijo.tag.split("}")[-1]
        if etiqueta == "p":
            from docx.text.paragraph import Paragraph
            partes.append(parrafo(Paragraph(hijo, doc)))
            n += 1
        elif etiqueta == "tbl":
            from docx.table import Table
            t = Table(hijo, doc)
            filas = []
            for fila in t.rows:
                celdas = "".join("<td>" + "".join(parrafo(p) for p in c.paragraphs) + "</td>" for c in fila.cells)
                filas.append(f"<tr>{celdas}</tr>")
            partes.append('<table class="doc-tabla">' + "".join(filas) + "</table>")
        if n >= max_parrafos:
            partes.append('<p class="texto-ayuda">… (documento recortado en la vista)</p>')
            break
    return "\n".join(partes)


# =====================================================================
# 61) PLANTILLAS CON CAMPOS A COMPLETAR («Campo» o [[Campo]])
# =====================================================================
_RE_CAMPO = re.compile(r"«([^«»\n]{1,60})»|\[\[([^\[\]\n]{1,60})\]\]")


def campos_de_plantilla(ruta):
    from docx import Document
    doc = Document(str(ruta))
    vistos = []
    textos = [p.text for p in doc.paragraphs] + [p.text for t in doc.tables for f in t.rows for c in f.cells for p in c.paragraphs]
    for t in textos:
        for m in _RE_CAMPO.finditer(t):
            nombre = (m.group(1) or m.group(2)).strip()
            if nombre not in vistos:
                vistos.append(nombre)
    return vistos


def _reemplazar_en_parrafo(p, valores):
    texto = p.text
    if not _RE_CAMPO.search(texto):
        return
    nuevo = _RE_CAMPO.sub(lambda m: valores.get((m.group(1) or m.group(2)).strip(), m.group(0)), texto)
    if not p.runs:
        return
    # conserva el formato del primer tramo del párrafo
    p.runs[0].text = nuevo
    for r in p.runs[1:]:
        r.text = ""


def completar_plantilla(ruta, valores, destino):
    from docx import Document
    doc = Document(str(ruta))
    for p in doc.paragraphs:
        _reemplazar_en_parrafo(p, valores)
    for t in doc.tables:
        for f in t.rows:
            for c in f.cells:
                for p in c.paragraphs:
                    _reemplazar_en_parrafo(p, valores)
    Path(destino).parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(destino))
    return destino


PLANTILLAS_BASE = {
    "Pedido de colchón y ropa de cama": [
        "Al Sr. «Destinatario»", "«Cargo»", "S/D", "",
        "Tengo el agrado de dirigirme a usted en el marco de las funciones de esta Procuración Penitenciaria de la Nación, a fin de "
        "poner en su conocimiento que «Persona» (LPU/DNI «Documento»), alojado/a en «Unidad», «Módulo o pabellón», manifestó no contar "
        "con colchón ni ropa de cama en condiciones.",
        "En virtud de ello, se solicita que se arbitren los medios necesarios para la provisión inmediata de un colchón ignífugo y ropa "
        "de cama, y se informe a este organismo lo actuado dentro del plazo de «Plazo en días» días hábiles.",
        "Sin otro particular, saludo a usted atentamente.",
    ],
    "Pedido de incorporación a trabajo": [
        "Al Sr. «Destinatario»", "«Cargo»", "S/D", "",
        "Tengo el agrado de dirigirme a usted a fin de solicitar que se evalúe la incorporación de «Persona» (LPU/DNI «Documento»), "
        "alojado/a en «Unidad», a una actividad laboral remunerada, en los términos de la Ley 24.660.",
        "Según lo manifestado, «Detalle».",
        "Se solicita que se informe a este organismo lo resuelto dentro del plazo de «Plazo en días» días hábiles.",
        "Sin otro particular, saludo a usted atentamente.",
    ],
    "Pedido de traslado o acercamiento familiar": [
        "Al Sr. «Destinatario»", "«Cargo»", "S/D", "",
        "Tengo el agrado de dirigirme a usted en relación con «Persona» (LPU/DNI «Documento»), alojado/a actualmente en «Unidad», "
        "quien solicitó su traslado a «Unidad de destino» a fin de mantener el vínculo con su grupo familiar.",
        "«Detalle»",
        "Por lo expuesto, se solicita que se evalúe el pedido y se informe a este organismo lo resuelto dentro del plazo de «Plazo en días» días hábiles.",
        "Sin otro particular, saludo a usted atentamente.",
    ],
}


def crear_plantillas_base(carpeta):
    """Si la biblioteca no tiene modelos, deja tres plantillas de ejemplo con campos a completar."""
    if db.listar_modelos_nota():
        return 0
    from docx import Document
    from docx.shared import Pt
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    n = 0
    for nombre, parrafos in PLANTILLAS_BASE.items():
        doc = Document()
        doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = "Arial", Pt(11)
        doc.add_paragraph("Nota N° ").alignment = 2
        for p in parrafos:
            doc.add_paragraph(p)
        ruta = carpeta / (re.sub(r"[^\w\- ]", "", nombre) + ".docx")
        doc.save(str(ruta))
        db.agregar_modelo_nota(nombre, "Plantilla con campos a completar (ejemplo, editable).", str(ruta))
        n += 1
    return n


# =====================================================================
# 66) PDF/A PARA EL ARCHIVO
# =====================================================================
def convertir_a_pdfa(docx, pdf):
    """PDF/A-2b (conservación a largo plazo): Word con UseISO19005_1 o LibreOffice con SelectPdfVersion=2."""
    import shutil
    import subprocess
    import sys
    import tempfile
    docx, pdf = Path(docx), Path(pdf)
    errores = []
    if sys.platform.startswith("win"):
        try:
            import pythoncom
            import win32com.client
            import nucleo
            with nucleo._WORD_LOCK:
                pythoncom.CoInitialize()
                word = win32com.client.DispatchEx("Word.Application")
                try:
                    d = word.Documents.Open(str(docx.resolve()), ReadOnly=True)
                    d.ExportAsFixedFormat(str(pdf.resolve()), 17, UseISO19005_1=True)
                    d.Close(False)
                finally:
                    word.Quit()
            if pdf.is_file():
                return "Word (PDF/A)"
        except Exception as e:
            errores.append(f"Word: {e}")
    import despacho
    soffice = despacho._soffice()
    if not soffice:
        errores.append("LibreOffice: no está instalado (en Linux: sudo apt install libreoffice-writer-nogui)")
        raise RuntimeError("No se pudo generar el PDF/A (" + "; ".join(errores) + ")")
    with tempfile.TemporaryDirectory() as tmp:
        filtro = 'pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":"2"}}'
        perfil = Path(tmp) / "perfil"  # perfil propio: el usuario del servicio puede no tener carpeta personal
        try:
            r = subprocess.run([soffice, f"-env:UserInstallation={perfil.resolve().as_uri()}", "--headless", "--convert-to", filtro,
                                "--outdir", tmp, str(docx)], capture_output=True, timeout=180)
            generado = Path(tmp) / (docx.stem + ".pdf")
            if generado.is_file():
                pdf.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(generado), str(pdf))
                return "LibreOffice (PDF/A-2b)"
            errores.append(f"LibreOffice: {r.stderr.decode(errors='ignore')[:200]}")
        except Exception as e:
            errores.append(f"LibreOffice: {e}")
    raise RuntimeError("No se pudo generar el PDF/A (" + "; ".join(errores) + ")")


def es_pdfa(ruta):
    """Chequeo simple: el PDF declara conformidad PDF/A en sus metadatos XMP."""
    try:
        datos = Path(ruta).read_bytes()
    except OSError:
        return False
    return b"pdfaid:part" in datos or b"pdfaid:conformance" in datos


# =====================================================================
# 84) TIEMPOS DEL CIRCUITO POR EQUIPO
# =====================================================================
def _dias(a, b):
    return (b - a).total_seconds() / 86400


def tiempos_por_equipo(desde=None, hasta=None, equipo_id=None):
    """Días promedio de cada tramo (subida → aprobación → firma → despacho) por equipo."""
    filas = {}
    for n in db.historial_para_tiempos(desde, hasta, equipo_id):
        eventos = n["eventos"]
        def primero(e):
            return eventos.get(e, [None])[0]
        def ultimo(e):
            return (eventos.get(e) or [None])[-1]
        sub, apr, proc, firm, desp = primero("SUBIDA"), primero("PROCESANDO"), ultimo("PROCESADA"), primero("FIRMADA"), primero("DESPACHADA")
        d = filas.setdefault(n["equipo"], {"equipo": n["equipo"], "notas": 0, "aprobacion": [], "correccion": [], "firma": [], "despacho": [], "total": []})
        d["notas"] += 1
        for clave, a, b in (("aprobacion", sub, apr), ("correccion", apr, proc), ("firma", proc, firm), ("despacho", firm, desp), ("total", sub, desp)):
            if a and b and b >= a:
                d[clave].append(_dias(a, b))
    salida = []
    for d in filas.values():
        fila = {"equipo": d["equipo"], "notas": d["notas"]}
        for clave in ("aprobacion", "correccion", "firma", "despacho", "total"):
            v = d[clave]
            fila[clave] = round(sum(v) / len(v), 1) if v else None
            fila["n_" + clave] = len(v)
        salida.append(fila)
    return sorted(salida, key=lambda f: (f["total"] is None, -(f["total"] or 0)))


# =====================================================================
# 87) INFORME TRIMESTRAL / ANUAL (borrador .docx)
# =====================================================================
def periodo(texto):
    """'2026-T3' -> ('2026-07-01', '2026-09-30', '3.er trimestre de 2026'); '2026' -> año completo."""
    m = re.fullmatch(r"(20\d{2})-T([1-4])", texto or "")
    if m:
        a, t = int(m.group(1)), int(m.group(2))
        desde = date(a, 3 * (t - 1) + 1, 1)
        hasta = date(a + (t == 4), (3 * t) % 12 + 1, 1)
        from datetime import timedelta
        return desde.isoformat(), (hasta - timedelta(days=1)).isoformat(), f"{['1.er', '2.º', '3.er', '4.º'][t - 1]} trimestre de {a}"
    m = re.fullmatch(r"(20\d{2})", texto or "")
    if m:
        a = int(m.group(1))
        return f"{a}-01-01", f"{a}-12-31", f"año {a}"
    return None


def informe_docx(texto_periodo, datos):
    """Borrador del informe con las cifras del sistema. Es un punto de partida: se revisa y completa a mano."""
    from docx import Document
    from docx.shared import Pt
    desde, hasta, nombre = periodo(texto_periodo)
    doc = Document()
    doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = "Arial", Pt(11)
    doc.add_heading(f"Dirección General de Protección de Derechos Humanos — Informe del {nombre}", level=1)
    doc.add_paragraph(f"Borrador generado automáticamente el {date.today().strftime('%d/%m/%Y')} con los datos del sistema "
                      f"(del {desde[8:10]}/{desde[5:7]}/{desde[:4]} al {hasta[8:10]}/{hasta[5:7]}/{hasta[:4]}). Revisar y completar antes de usarlo.")
    r = datos["reporte"]

    def tabla(cab, filas):
        t = doc.add_table(rows=1, cols=len(cab))
        t.style = "Light Grid Accent 1" if "Light Grid Accent 1" in [s.name for s in doc.styles] else t.style
        for i, c in enumerate(cab):
            t.rows[0].cells[i].text = str(c)
        for f in filas:
            celdas = t.add_row().cells
            for i, v in enumerate(f):
                celdas[i].text = "" if v is None else str(v)
        doc.add_paragraph()

    doc.add_heading("1. Notas", level=2)
    doc.add_paragraph(f"En el período se cargaron {r['total_archivos']} documentos; {r['notas_aprobadas']} notas fueron firmadas "
                      f"sin modificaciones, {r['notas_modificadas']} con modificaciones de la Dirección y {r['notas_rechazadas']} fueron rechazadas. "
                      f"Quedaron {r['notas_en_curso']} en curso al cierre.")
    if r.get("tiempo_promedio_despacho_total"):
        doc.add_paragraph(f"El tiempo promedio desde la carga hasta el despacho fue de {r['tiempo_promedio_despacho_total']}.")
    if r.get("por_equipo"):
        doc.add_heading("Notas por equipo", level=3)
        tabla(["Equipo", "Documentos"], r["por_equipo"])
    if datos.get("tiempos"):
        doc.add_heading("Tiempos del circuito por equipo (días promedio)", level=3)
        tabla(["Equipo", "Notas", "Aprobación", "Corrección", "Firma", "Despacho", "Total"],
              [(t["equipo"], t["notas"], t["aprobacion"], t["correccion"], t["firma"], t["despacho"], t["total"]) for t in datos["tiempos"]])
    if datos.get("por_pedido"):
        doc.add_heading("Temas planteados (según el texto de las notas)", level=3)
        tabla(["Tipo de pedido", "Notas"], datos["por_pedido"])
    if datos.get("por_unidad"):
        doc.add_heading("Unidades más mencionadas", level=3)
        tabla(["Unidad", "Notas"], datos["por_unidad"])
    if datos.get("respuestas") is not None:
        rr = datos["respuestas"]
        doc.add_paragraph(f"Respuestas: de {rr['con_plazo']} notas con plazo de respuesta, se registraron {rr['respondidas']} respuestas; "
                          f"{rr['vencidas']} quedaron vencidas sin respuesta.")
    t = datos.get("traslados")
    if t:
        doc.add_heading("2. Autos PPN (traslados)", level=2)
        doc.add_paragraph(f"Se realizaron {t.get('desplazamientos', 0)} desplazamientos en vehículo institucional y "
                          f"{t.get('viajes_unidad', 0)} viajes a unidades (contando cada equipo en cada unidad y día).")
        if t.get("por_unidad"):
            tabla(["Unidad", "Viajes"], t["por_unidad"][:15])
    doc.add_heading("3. Observaciones", level=2)
    doc.add_paragraph("[Completar: hechos destacados del período, dificultades y propuestas.]")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
