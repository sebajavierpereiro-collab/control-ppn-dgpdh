"""Reporte semanal para el área de Personal: quiénes estuvieron (o van a estar) de viaje cada día, para el control
de presentismo. Sale de los viajes cargados en Traslados (AMBA y Delegaciones).

Qué entra:
  * viajes confirmados, realizados y pedidos de chofer (la persona viaja aunque falte asignar el auto);
  * los frecuentes «a confirmar» de días que todavía no pasaron, marcados como tales;
  * NO entran los cancelados ni los frecuentes que nadie confirmó.
"""
import io
from collections import defaultdict
from datetime import date, datetime, timedelta

import database as db
import traslados as tr

DIAS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
ESTADO_TEXTO = {"CONFIRMADO": "Confirmado", "REALIZADO": "Realizado", "SOLICITADO": "Pedido de chofer", "A_CONFIRMAR": "A confirmar"}


def puede_ver(usuario):
    return bool(usuario) and (tr.puede_coordinar(usuario) or bool(usuario.get("personal")))


def semana(lunes, hoy=None):
    """Datos de la semana que empieza en `lunes`: filas de detalle, cuadro por persona y personas sin usuario."""
    l = tr.lunes_de(lunes)
    fechas = [l + timedelta(days=i) for i in range(7)]
    d = periodo(fechas[0], fechas[-1], hoy)
    d["lunes"] = l
    return d


def periodo(desde, hasta, hoy=None):
    """Igual que la semana, para cualquier período (mes, persona): detalle, cuadro por persona y sin usuario."""
    fechas = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
    usuarios = {u["id"]: u for u in db.listar_usuarios()}
    detalle, sin_usuario = [], []
    for v in tr.listar_viajes(desde=fechas[0].isoformat(), hasta=fechas[-1].isoformat()):
        tr._decorar(v, hoy)
        if v["estado_efectivo"] not in ESTADO_TEXTO:
            continue
        for p in v["participantes"]:
            u = usuarios.get(p.get("usuario_id")) if p.get("usuario_id") else None
            fila = {"fecha": v["fecha"], "dia": DIAS[tr._fecha(v["fecha"]).weekday()], "persona": u["nombre"] if u else p["nombre"],
                    "usuario_id": u["id"] if u else None, "correo": (u or {}).get("correo") or "", "equipo": (u or {}).get("equipo_nombre") or p.get("equipo_nombre") or "",
                    "circuito": tr.ETIQUETAS_CIRCUITO.get(v.get("circuito") or "AMBA", ""), "destino": v["destino"],
                    "salida": v["hora_salida"] or "", "regreso": v["hora_regreso"] or "",
                    "modalidad": "Por sus propios medios" if p.get("auto_propio") else "Con chofer institucional",
                    "estado": ESTADO_TEXTO[v["estado_efectivo"]], "viaje_id": v["id"], "frecuente": bool(v.get("frecuente_id"))}
            (detalle if u else sin_usuario).append(fila)
    detalle.sort(key=lambda f: (f["persona"].lower(), f["fecha"], f["salida"]))
    sin_usuario.sort(key=lambda f: (f["persona"].lower(), f["fecha"]))
    cuadro = defaultdict(lambda: {"dias": defaultdict(list)})
    for f in detalle:
        c = cuadro[f["usuario_id"]]
        c.update(persona=f["persona"], equipo=f["equipo"], correo=f["correo"], usuario_id=f["usuario_id"])
        c["dias"][f["fecha"]].append(f)
    personas = sorted(cuadro.values(), key=lambda c: (c["equipo"].lower(), c["persona"].lower()))
    for c in personas:
        c["n_dias"] = len(c["dias"])
    return {"desde": fechas[0], "hasta": fechas[-1], "fechas": fechas, "detalle": detalle, "sin_usuario": sin_usuario, "personas": personas}


def _celda(filas):
    return " / ".join(f"{f['destino']} {f['salida']}{('–' + f['regreso']) if f['regreso'] else ''}".strip()
                      + (" (a confirmar)" if f["estado"] == "A confirmar" else "") for f in filas)


def excel(datos, generado_por=""):
    """Devuelve el .xlsx (bytes) con tres hojas: cuadro semanal, detalle y personas sin usuario."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    titulo = Font(bold=True, size=13)
    cab = Font(bold=True, color="FFFFFF")
    fondo_cab = PatternFill("solid", fgColor="1F3A5F")
    fondo_viaje = PatternFill("solid", fgColor="E6F0FA")
    fondo_conf = PatternFill("solid", fgColor="F3E8FF")
    borde = Border(*(Side(style="thin", color="C9D2DC"),) * 4)
    envolver = Alignment(wrap_text=True, vertical="top")
    l, fechas = datos["lunes"], datos["fechas"]

    # Hoja 1: cuadro semanal por persona
    ws = wb.active
    ws.title = "Semana"
    ws["A1"] = f"Personal en viaje · semana del {l.strftime('%d/%m/%Y')} al {fechas[-1].strftime('%d/%m/%Y')}"
    ws["A1"].font = titulo
    ws["A2"] = (f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}" + (f" por {generado_por}" if generado_por else "") +
                ". Incluye viajes confirmados, realizados y pedidos de chofer; los frecuentes sin confirmar se marcan «a confirmar». No incluye cancelados.")
    ws["A2"].font = Font(italic=True, size=9, color="555555")
    encabezado = ["Persona", "Equipo"] + [f"{DIAS[i]} {f.strftime('%d/%m')}" for i, f in enumerate(fechas)] + ["Días en viaje"]
    ws.append([])
    ws.append(encabezado)
    for col in range(1, len(encabezado) + 1):
        c = ws.cell(row=4, column=col)
        c.font, c.fill, c.border, c.alignment = cab, fondo_cab, borde, Alignment(horizontal="center", vertical="center", wrap_text=True)
    for p in datos["personas"]:
        fila = [p["persona"], p["equipo"]] + [_celda(p["dias"].get(f.isoformat(), [])) for f in fechas] + [p["n_dias"]]
        ws.append(fila)
        r = ws.max_row
        for col in range(1, len(fila) + 1):
            c = ws.cell(row=r, column=col)
            c.border, c.alignment = borde, envolver
            if 3 <= col <= 9 and c.value:
                c.fill = fondo_conf if "a confirmar" in str(c.value) else fondo_viaje
    if not datos["personas"]:
        ws.append(["Sin viajes registrados esa semana."])
    anchos = [28, 20] + [24] * 7 + [12]
    for i, a in enumerate(anchos, start=1):
        ws.column_dimensions[get_column_letter(i)].width = a
    ws.freeze_panes = "C5"

    # Hoja 2: detalle (una fila por persona y viaje), apta para filtrar o cruzar con otra planilla
    wd = wb.create_sheet("Detalle")
    cols = [("Fecha", "fecha"), ("Día", "dia"), ("Persona", "persona"), ("Correo", "correo"), ("Equipo", "equipo"), ("Circuito", "circuito"),
            ("Destino", "destino"), ("Salida", "salida"), ("Regreso", "regreso"), ("Modalidad", "modalidad"), ("Estado", "estado"),
            ("Viaje frecuente", "frecuente"), ("N.º de viaje", "viaje_id")]
    wd.append([c for c, _ in cols])
    for col in range(1, len(cols) + 1):
        c = wd.cell(row=1, column=col)
        c.font, c.fill = cab, fondo_cab
    for f in datos["detalle"]:
        wd.append([date.fromisoformat(f["fecha"]) if k == "fecha" else ("Sí" if f[k] else "") if k == "frecuente" else f[k] for _, k in cols])
        wd.cell(row=wd.max_row, column=1).number_format = "DD/MM/YYYY"
    for i, a in enumerate([12, 11, 28, 28, 18, 12, 40, 8, 8, 24, 16, 10, 10], start=1):
        wd.column_dimensions[get_column_letter(i)].width = a
    wd.auto_filter.ref = wd.dimensions
    wd.freeze_panes = "A2"

    # Hoja 3: personas sin usuario (no son necesariamente personal del organismo)
    we = wb.create_sheet("Sin usuario")
    we.append(["Fecha", "Persona", "Destino", "Salida", "Regreso", "Estado", "N.º de viaje"])
    for col in range(1, 8):
        c = we.cell(row=1, column=col)
        c.font, c.fill = cab, fondo_cab
    for f in datos["sin_usuario"]:
        we.append([date.fromisoformat(f["fecha"]), f["persona"], f["destino"], f["salida"], f["regreso"], f["estado"], f["viaje_id"]])
        we.cell(row=we.max_row, column=1).number_format = "DD/MM/YYYY"
    for i, a in enumerate([12, 28, 40, 8, 8, 16, 10], start=1):
        we.column_dimensions[get_column_letter(i)].width = a

    for hoja in wb.worksheets:  # para imprimir: apaisado y ajustado al ancho de la hoja
        hoja.page_setup.orientation = "landscape"
        hoja.page_setup.paperSize = hoja.PAPERSIZE_A4
        hoja.sheet_properties.pageSetUpPr.fitToPage = True
        hoja.page_setup.fitToWidth, hoja.page_setup.fitToHeight = 1, 0
    ws.print_title_rows = "4:4"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =====================================================================
# Ola 5 · Reforma 80: presentismo MENSUAL y POR PERSONA, y cruce con la planilla del área de Personal
# =====================================================================
import re
import unicodedata

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def mes(anio, numero, hoy=None):
    """Personal en viaje en el mes: por persona, los días del mes con viaje y el total."""
    ini = date(anio, numero, 1)
    fin = (date(anio + (numero == 12), numero % 12 + 1, 1) - timedelta(days=1))
    d = periodo(ini, fin, hoy)
    d["anio"], d["mes"], d["titulo"] = anio, numero, f"{MESES[numero - 1]} {anio}"
    return d


def persona(usuario_id, desde, hasta, hoy=None):
    """Todos los días de viaje de una persona en el período (para responder un reclamo o un control puntual)."""
    d = periodo(desde, hasta, hoy)
    filas = [f for f in d["detalle"] if f["usuario_id"] == usuario_id]
    dias = sorted({f["fecha"] for f in filas})
    return {"desde": desde, "hasta": hasta, "filas": filas, "dias": dias, "usuario": db.obtener_usuario(usuario_id)}


def excel_mes(datos, generado_por=""):
    """Cuadro mensual: una fila por persona, una columna por día (V = viaje; V? = a confirmar) y el total."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mes"
    fechas = datos["fechas"]
    ws["A1"] = f"Personal en viaje · {datos['titulo']}"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = (f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}" + (f" por {generado_por}" if generado_por else "") +
                ". V = día de viaje; V? = viaje frecuente sin confirmar. El detalle de cada viaje está en la hoja «Detalle».")
    ws["A2"].font = Font(italic=True, size=9, color="555555")
    cab = ["Persona", "Equipo"] + [f"{DIAS[f.weekday()][:2]} {f.day}" for f in fechas] + ["Días de viaje"]
    ws.append([])
    ws.append(cab)
    borde = Border(*(Side(style="thin", color="C9D2DC"),) * 4)
    for j in range(1, len(cab) + 1):
        c = ws.cell(row=4, column=j)
        c.font, c.fill, c.border = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F"), borde
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    finde = PatternFill("solid", fgColor="EEF1F4")
    viaje = PatternFill("solid", fgColor="E6F0FA")
    for p in datos["personas"]:
        fila = [p["persona"], p["equipo"]]
        for f in fechas:
            x = p["dias"].get(f.isoformat(), [])
            fila.append(("V?" if all(y["estado"] == "A confirmar" for y in x) else "V") if x else "")
        fila.append(p["n_dias"])
        ws.append(fila)
        r = ws.max_row
        for j, f in enumerate(fechas, 3):
            c = ws.cell(row=r, column=j)
            c.alignment, c.border = Alignment(horizontal="center"), borde
            if c.value:
                c.fill = viaje
            elif f.weekday() >= 5:
                c.fill = finde
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 28, 20
    for j in range(3, 3 + len(fechas)):
        ws.column_dimensions[get_column_letter(j)].width = 5
    ws.column_dimensions[get_column_letter(3 + len(fechas))].width = 10
    ws.freeze_panes = "C5"
    wd = wb.create_sheet("Detalle")
    cols = [("Fecha", "fecha"), ("Persona", "persona"), ("Equipo", "equipo"), ("Destino", "destino"), ("Salida", "salida"),
            ("Regreso", "regreso"), ("Modalidad", "modalidad"), ("Estado", "estado"), ("N.º de viaje", "viaje_id")]
    wd.append([c for c, _ in cols])
    for f in datos["detalle"]:
        wd.append([date.fromisoformat(f["fecha"]) if k == "fecha" else f[k] for _, k in cols])
        wd.cell(row=wd.max_row, column=1).number_format = "DD/MM/YYYY"
    wd.auto_filter.ref = wd.dimensions
    for hoja in wb.worksheets:
        hoja.page_setup.orientation = "landscape"
        hoja.sheet_properties.pageSetUpPr.fitToPage = True
        hoja.page_setup.fitToWidth, hoja.page_setup.fitToHeight = 1, 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- planilla del área de Personal
CLASES = {
    "AUSENCIA": ("ausente", "ausencia", "inasistencia", "falta", "injustificad"),
    "LICENCIA": ("licencia", "vacacion", "enfermedad", "art", "medica", "examen", "maternidad", "paternidad", "duelo", "franco", "compensatorio", "feriado"),
    "COMISION": ("comision", "viaje", "mision", "servicio externo", "traslado", "visita", "unidad", "monitoreo"),
    "PRESENTE": ("presente", "asistio", "asistencia"),
}
CODIGOS = {"P": "PRESENTE", "A": "AUSENCIA", "L": "LICENCIA", "V": "LICENCIA", "C": "COMISION", "LIC": "LICENCIA", "COM": "COMISION",
           "AUS": "AUSENCIA", "F": "AUSENCIA", "E": "LICENCIA"}
ETIQUETA_CLASE = {"AUSENCIA": "Ausencia", "LICENCIA": "Licencia", "COMISION": "Comisión / viaje", "PRESENTE": "Presente", "OTRA": "Otra novedad"}


def _norm(t):
    return " ".join("".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn").split())


def clasificar(novedad):
    n = _norm(novedad)
    if not n:
        return None
    if n.upper() in CODIGOS or n.upper().rstrip(".") in CODIGOS:
        return CODIGOS[n.upper().rstrip(".")]
    for clase, claves in CLASES.items():
        if any(re.search(r"\b" + re.escape(k), n) for k in claves):
            return clase
    return "OTRA"


def _fecha_celda(v, anio=None, mes_n=None):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    t = str(v or "").strip()
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", t)
    if m:
        d_, m_, a_ = (int(x) for x in m.groups())
        try:
            return date(a_ + 2000 if a_ < 100 else a_, m_, d_)
        except ValueError:
            return None
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        return None


def _filas_de_archivo(nombre, contenido):
    """Devuelve las filas (listas) de la primera hoja de un .xlsx, .xls o .csv."""
    nombre = (nombre or "").lower()
    if nombre.endswith(".csv"):
        import csv
        texto = contenido.decode("utf-8-sig", errors="replace")
        separador = ";" if texto.count(";") > texto.count(",") else ","
        return [r for r in csv.reader(io.StringIO(texto), delimiter=separador)]
    if nombre.endswith(".xls"):
        import xlrd
        libro = xlrd.open_workbook(file_contents=contenido)
        h = libro.sheet_by_index(0)
        filas = []
        for i in range(h.nrows):
            fila = []
            for j in range(h.ncols):
                c = h.cell(i, j)
                fila.append(xlrd.xldate_as_datetime(c.value, libro.datemode) if c.ctype == xlrd.XL_CELL_DATE else c.value)
            filas.append(fila)
        return filas
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True)
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


_ENC_PERSONA = ("apellido y nombre", "nombre y apellido", "apellido", "nombre", "agente", "persona", "empleado", "personal")
_ENC_FECHA = ("fecha", "dia", "día")
_ENC_NOVEDAD = ("novedad", "situacion", "situación", "estado", "observacion", "observación", "motivo", "concepto", "codigo", "código")
_ENC_CORREO = ("correo", "mail", "email", "e-mail")


def leer_planilla(nombre, contenido, anio=None, mes_n=None):
    """Lee la planilla de Personal en cualquiera de los dos formatos habituales:
      * LARGO: una fila por persona y día (columnas Persona, Fecha y Novedad/Situación; Correo opcional);
      * MENSUAL: una fila por persona y una columna por día del mes (1, 2, ..., 31) con el código (P, A, L, C...).
        En este formato el mes y el año se eligen en el formulario.
    Devuelve (registros [{persona, correo, fecha, novedad, clase}], avisos)."""
    filas = [r for r in _filas_de_archivo(nombre, contenido) if r and any(str(c or "").strip() for c in r)]
    avisos = []
    for i, fila in enumerate(filas[:15]):
        enc = [_norm(c) for c in fila]
        dias = {j: int(float(c)) for j, c in enumerate(fila) if re.fullmatch(r"\d{1,2}(\.0)?", str(c or "").strip()) and 1 <= int(float(c)) <= 31}
        col_p = next((j for j, h in enumerate(enc) if any(h.startswith(k) for k in _ENC_PERSONA)), None)
        if col_p is None:
            continue
        col_c = next((j for j, h in enumerate(enc) if any(k in h for k in _ENC_CORREO)), None)
        registros = []
        if len(dias) >= 10:
            if not (anio and mes_n):
                return [], ["La planilla tiene una columna por día: elegí el mes y el año en el formulario."]
            for r in filas[i + 1:]:
                nombre_p = str(r[col_p] or "").strip() if col_p < len(r) else ""
                if not nombre_p:
                    continue
                for j, dia in dias.items():
                    if j < len(r) and str(r[j] or "").strip():
                        try:
                            f = date(anio, mes_n, dia)
                        except ValueError:
                            continue
                        nov = str(r[j]).strip()
                        registros.append({"persona": nombre_p, "correo": str(r[col_c] or "").strip() if col_c is not None and col_c < len(r) else "",
                                          "fecha": f, "novedad": nov, "clase": clasificar(nov)})
            return registros, avisos
        col_f = next((j for j, h in enumerate(enc) if h in _ENC_FECHA or h.startswith("fecha")), None)
        col_n = next((j for j, h in enumerate(enc) if any(h.startswith(_norm(k)) for k in _ENC_NOVEDAD)), None)
        if col_f is None or col_n is None:
            continue
        sin_fecha = 0
        for r in filas[i + 1:]:
            nombre_p = str(r[col_p] or "").strip() if col_p < len(r) else ""
            if not nombre_p:
                continue
            f = _fecha_celda(r[col_f] if col_f < len(r) else None)
            if not f:
                sin_fecha += 1
                continue
            nov = str(r[col_n] or "").strip() if col_n < len(r) else ""
            registros.append({"persona": nombre_p, "correo": str(r[col_c] or "").strip() if col_c is not None and col_c < len(r) else "",
                              "fecha": f, "novedad": nov, "clase": clasificar(nov) or "PRESENTE"})
        if sin_fecha:
            avisos.append(f"{sin_fecha} fila(s) sin una fecha que se pueda leer: no se tomaron.")
        return registros, avisos
    return [], ["No encontré los encabezados. La planilla necesita una columna con el nombre de la persona y, o bien las columnas "
                "Fecha y Novedad (una fila por día), o bien una columna por cada día del mes (1 a 31)."]


def _vincular(registros):
    """Asocia cada nombre de la planilla con un usuario: por correo o por el nombre (en cualquier orden, sin acentos)."""
    usuarios = db.listar_usuarios()
    por_correo = {(u.get("correo") or "").lower(): u["id"] for u in usuarios if u.get("correo")}
    por_nombre = {}
    for u in usuarios:
        por_nombre.setdefault(frozenset(_norm(u["nombre"]).replace(",", " ").split()), u["id"])
    cache = {}
    for r in registros:
        clave = (r["persona"], r.get("correo", ""))
        if clave not in cache:
            uid = por_correo.get((r.get("correo") or "").lower())
            if not uid:
                tokens = frozenset(_norm(r["persona"]).replace(",", " ").split())
                uid = por_nombre.get(tokens)
                if not uid and len(tokens) >= 2:  # «PÉREZ, Ana» contra «Ana María Pérez»: todas las palabras de la planilla están en el usuario
                    candidatos = [i for t, i in por_nombre.items() if tokens <= t]
                    uid = candidatos[0] if len(candidatos) == 1 else None
            cache[clave] = uid
        r["usuario_id"] = cache[clave]
    return registros


def importar(nombre, contenido, usuario, anio=None, mes_n=None):
    registros, avisos = leer_planilla(nombre, contenido, anio, mes_n)
    if not registros:
        return None, avisos
    _vincular(registros)
    desde, hasta = min(r["fecha"] for r in registros), max(r["fecha"] for r in registros)
    sin = len({r["persona"] for r in registros if not r["usuario_id"]})
    with db._conexion() as con:
        cur = con.execute("INSERT INTO personal_importaciones (archivo, desde, hasta, filas, sin_vincular, usuario, creado_en) VALUES (?,?,?,?,?,?,?)",
                          (nombre, desde.isoformat(), hasta.isoformat(), len(registros), sin, usuario["nombre"] if usuario else None,
                           datetime.now().isoformat(timespec="seconds")))
        imp = cur.lastrowid
        # Una importación nueva del mismo período reemplaza a la anterior (para esas mismas fechas).
        db.borrar_con_registro(con, "personal_novedades", "fecha BETWEEN ? AND ? AND importacion_id != ?", (desde.isoformat(), hasta.isoformat(), imp))
        con.executemany("INSERT INTO personal_novedades (importacion_id, persona, usuario_id, fecha, novedad, clase) VALUES (?,?,?,?,?,?)",
                        [(imp, r["persona"], r["usuario_id"], r["fecha"].isoformat(), r["novedad"], r["clase"]) for r in registros])
    return {"id": imp, "desde": desde, "hasta": hasta, "filas": len(registros), "sin_vincular": sin}, avisos


def importaciones(limite=10):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM personal_importaciones ORDER BY id DESC LIMIT ?", (limite,))]


def cruce(desde, hasta, hoy=None):
    """Compara la planilla de Personal con los viajes registrados en Traslados para el período:
      * viajó pero Personal registra ausencia o licencia ese día (revisar uno de los dos registros);
      * Personal registra comisión/viaje pero no hay viaje cargado (falta cargar el viaje o es otra comisión);
      * nombres de la planilla que no coinciden con ningún usuario.
    Nada se corrige solo: es una lista para que Personal y la coordinación revisen."""
    viajes = periodo(desde, hasta, hoy)["detalle"]
    por_dia = {}
    for f in viajes:
        por_dia.setdefault((f["usuario_id"], f["fecha"]), []).append(f)
    with db._conexion() as con:
        nov = [dict(f) for f in con.execute("SELECT * FROM personal_novedades WHERE fecha BETWEEN ? AND ? ORDER BY fecha",
                                            (desde.isoformat(), hasta.isoformat()))]
    usuarios = {u["id"]: u for u in db.listar_usuarios()}
    viajo_con_ausencia, comision_sin_viaje, coinciden = [], [], 0
    claves_nov = set()
    for n in nov:
        if not n["usuario_id"]:
            continue
        claves_nov.add((n["usuario_id"], n["fecha"]))
        v = por_dia.get((n["usuario_id"], n["fecha"]))
        persona_txt = usuarios.get(n["usuario_id"], {}).get("nombre", n["persona"])
        if v and n["clase"] in ("AUSENCIA", "LICENCIA"):
            viajo_con_ausencia.append({"persona": persona_txt, "fecha": n["fecha"], "novedad": n["novedad"], "viajes": v})
        elif not v and n["clase"] == "COMISION":
            comision_sin_viaje.append({"persona": persona_txt, "fecha": n["fecha"], "novedad": n["novedad"]})
        elif v:
            coinciden += 1
    personas_planilla = {n["usuario_id"] for n in nov if n["usuario_id"]}
    viajes_sin_planilla = [f for f in viajes if f["usuario_id"] in personas_planilla and (f["usuario_id"], f["fecha"]) not in claves_nov]
    sin_vincular = sorted({n["persona"] for n in nov if not n["usuario_id"]})
    return {"desde": desde, "hasta": hasta, "hay_planilla": bool(nov), "novedades": len(nov),
            "viajo_con_ausencia": viajo_con_ausencia, "comision_sin_viaje": comision_sin_viaje,
            "viajes_sin_planilla": viajes_sin_planilla, "sin_vincular": sin_vincular, "coinciden": coinciden}
