"""Ola 5 · Reforma 83: reportes programados.

Alcance acordado: por ahora se GENERAN solos y quedan para descargar (con un aviso en el sistema a quien corresponde);
el envío por correo se suma cuando se vincule el correo. Nadie tiene que acordarse de pedirlos.

Cada reporte tiene una frecuencia: semanal (lunes a las 8, sobre la semana anterior) o mensual (día 1 a las 8, sobre
el mes anterior). Se guardan en datos/reportes_programados y se listan en Datos → Reportes programados.
"""
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import auth
import database as db

CARPETA = Path("datos") / "reportes_programados"
HORA = 8
REPORTES = {
    "presentismo_semanal": {"titulo": "Presentismo de la semana anterior (Excel)", "frecuencia": "semanal",
                            "para": "Personal y coordinación de traslados", "defecto": True},
    "presentismo_mensual": {"titulo": "Presentismo del mes anterior (Excel)", "frecuencia": "mensual",
                            "para": "Personal y coordinación de traslados", "defecto": True},
    "tablero_semanal": {"titulo": "Tablero de la Dirección de la semana anterior (Excel)", "frecuencia": "semanal",
                        "para": "Dirección", "defecto": True},
    "uso_mensual": {"titulo": "Uso del sistema por área del mes anterior (Excel)", "frecuencia": "mensual",
                    "para": "Dirección", "defecto": False},
    "abiertos_mensual": {"titulo": "Datos agregados publicables del mes anterior (Excel)", "frecuencia": "mensual",
                         "para": "Dirección", "defecto": False},
}


def configurar(carpeta_datos):
    global CARPETA
    CARPETA = Path(carpeta_datos) / "reportes_programados"


def activos():
    try:
        guardado = json.loads(db.obtener_ajuste("reportes_programados", "") or "{}")
    except ValueError:
        guardado = {}
    return {k: bool(guardado.get(k, r["defecto"])) for k, r in REPORTES.items()}


def guardar_activos(claves):
    db.guardar_ajuste("reportes_programados", json.dumps({k: (k in claves) for k in REPORTES}))


def periodo_que_toca(clave, ahora):
    """(periodo, desde, hasta) del último período cerrado si ya pasó la hora de generarlo; si no, None."""
    hoy = ahora.date()
    if REPORTES[clave]["frecuencia"] == "semanal":
        lunes = hoy - timedelta(days=hoy.weekday())
        if hoy == lunes and ahora.hour < HORA:
            lunes -= timedelta(days=7)
        desde = lunes - timedelta(days=7)
        return desde.isoformat(), desde, desde + timedelta(days=6)
    primero = date(hoy.year, hoy.month, 1)
    if hoy == primero and ahora.hour < HORA:
        primero = date(primero.year - (primero.month == 1), (primero.month - 2) % 12 + 1, 1)
    fin = primero - timedelta(days=1)
    desde = date(fin.year, fin.month, 1)
    return desde.isoformat()[:7], desde, fin


def generar(clave, desde, hasta):
    """Devuelve (bytes, nombre de archivo)."""
    import indicadores as ind
    import presentismo
    if clave == "presentismo_semanal":
        return presentismo.excel(presentismo.semana(desde), generado_por="el sistema (programado)"), f"presentismo_semana_{desde.isoformat()}.xlsx"
    if clave == "presentismo_mensual":
        return presentismo.excel_mes(presentismo.mes(desde.year, desde.month), generado_por="el sistema (programado)"), f"presentismo_{desde.isoformat()[:7]}.xlsx"
    if clave == "tablero_semanal":
        return tablero_excel(ind.tablero(desde.isoformat(), hasta.isoformat())), f"tablero_semana_{desde.isoformat()}.xlsx"
    if clave == "uso_mensual":
        return uso_excel(ind.uso_sistema(desde.isoformat(), hasta.isoformat())), f"uso_sistema_{desde.isoformat()[:7]}.xlsx"
    if clave == "abiertos_mensual":
        return ind.abiertos_excel(ind.datos_abiertos(desde.isoformat(), hasta.isoformat())), f"datos_agregados_{desde.isoformat()[:7]}.xlsx"
    raise KeyError(clave)


def _destinatarios(clave):
    usuarios = [u for u in db.listar_usuarios() if u["activo"]]
    if clave.startswith("presentismo"):
        return [u["id"] for u in usuarios if u.get("personal") or u.get("coordina_traslados") or u["rol"] in auth.ROLES_DIRECCION]
    return [u["id"] for u in usuarios if u["rol"] in auth.ROLES_DIRECCION]


def tick(ahora=None):
    """Llamado desde el hilo de fondo: genera lo que corresponde y todavía no se generó."""
    ahora = ahora or datetime.now()
    hechos = []
    for clave, activo in activos().items():
        if not activo:
            continue
        periodo, desde, hasta = periodo_que_toca(clave, ahora)
        with db._conexion() as con:
            ya = con.execute("SELECT 1 FROM reportes_generados WHERE tipo = ? AND periodo = ?", (clave, periodo)).fetchone()
        if ya:
            continue
        try:
            contenido, nombre = generar(clave, desde, hasta)
            CARPETA.mkdir(parents=True, exist_ok=True)
            (CARPETA / nombre).write_bytes(contenido)
            with db._conexion() as con:
                cur = con.execute("INSERT OR IGNORE INTO reportes_generados (tipo, periodo, archivo, creado_en) VALUES (?,?,?,?)",
                                  (clave, periodo, nombre, ahora.isoformat(timespec="seconds")))
                rid = cur.lastrowid
            db.notificar_usuarios(_destinatarios(clave), f"Listo para descargar: {REPORTES[clave]['titulo'].split(' (')[0].lower()}.",
                                  f"/datos/programados/{rid}/descargar")
            hechos.append(nombre)
        except Exception:
            logging.exception(f"No se pudo generar el reporte programado {clave}")
    return hechos


def listar(limite=60):
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT * FROM reportes_generados ORDER BY id DESC LIMIT ?", (limite,))]
    for f in filas:
        f["titulo"] = REPORTES.get(f["tipo"], {}).get("titulo", f["tipo"])
        f["existe"] = (CARPETA / f["archivo"]).is_file()
    return filas


def obtener(rid):
    with db._conexion() as con:
        f = con.execute("SELECT * FROM reportes_generados WHERE id = ?", (rid,)).fetchone()
    return dict(f) if f else None


def puede_descargar(usuario, fila):
    if auth.es_admin(usuario):
        return True
    return fila["tipo"].startswith("presentismo") and bool(usuario.get("personal") or usuario.get("coordina_traslados"))


# ---------------------------------------------------------------- planillas de los reportes nuevos
def _libro(titulo, subtitulo):
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = titulo
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = subtitulo
    ws["A2"].font = Font(italic=True, size=9, color="555555")
    return wb, ws


def _tabla(ws, fila, encabezado, filas, anchos=None):
    from openpyxl.styles import Font, PatternFill
    for j, t in enumerate(encabezado, 1):
        c = ws.cell(row=fila, column=j, value=t)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="7C1413")
        if anchos:
            ws.column_dimensions[c.column_letter].width = anchos[j - 1]
    for i, f in enumerate(filas, fila + 1):
        for j, v in enumerate(f, 1):
            ws.cell(row=i, column=j, value=v)
    return fila + len(filas) + 2


def tablero_excel(d):
    import indicadores as ind
    wb, ws = _libro("Tablero de la Dirección", f"Del {ind._dmy(d['desde'])} al {ind._dmy(d['hasta'])}. Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}.")
    k, t = d["kpis"], d["tiempos"]
    r = _tabla(ws, 4, ["Indicador", "Valor"], [
        ("Notas del período", k["notas"]), ("Firmadas", k["firmadas"]), ("Despachadas", k["despachadas"]),
        ("Esperando la firma (hoy)", k["a_la_firma"]), ("En el circuito (hoy)", k["en_circuito"]),
        ("Viajes a las unidades", k["viajes_unidad"]), ("Desplazamientos", k["desplazamientos"]),
        ("Personas que viajaron", k["personas"]), ("% de viajes fuera de plazo", k["pct_inmediatos"]),
        ("Tiempo promedio subida → aprobación", t["aprobacion"] or "sin datos"), ("Tiempo promedio a la firma", t["firma"] or "sin datos"),
        ("Tiempo promedio total hasta el despacho", t["despacho"] or "sin datos")], [46, 18])
    r = _tabla(ws, r, ["Unidades más visitadas", "Viajes"], d["unidades_visitadas"])
    r = _tabla(ws, r, ["Unidades más mencionadas en notas", "Notas"], d["unidades_notas"])
    r = _tabla(ws, r, ["Tipos de pedido", "Notas"], d["pedidos"])
    _tabla(ws, r, ["Unidades con notas y sin visitas en el período", "Notas"], [(u, n) for u, n, _ in d["sin_visita"]])
    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def uso_excel(d):
    import indicadores as ind
    wb, ws = _libro("Uso del sistema por área", f"Del {ind._dmy(d['desde'])} al {ind._dmy(d['hasta'])}.")
    r = _tabla(ws, 4, ["Área", "Personas", "Entraron", "% que entró", "Ingresos", "Documentos subidos", "Acciones sobre notas", "Viajes cargados",
                       "Sin entrar hace más de 30 días", "Nunca entraron"],
               [(a["area"], a["personas"], a["entraron"], a["pct"], a["ingresos"], a["subidos"], a["acciones"], a["viajes"],
                 ", ".join(n for n, _ in a["sin_entrar"]), ", ".join(a["nunca"])) for a in d["filas"]],
               [30, 10, 10, 10, 10, 12, 12, 12, 40, 40])
    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
