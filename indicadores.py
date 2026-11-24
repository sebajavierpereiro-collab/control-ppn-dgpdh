"""Ola 5 · Datos para decidir (reformas 81, 82, 86, 88 y 89).

Todo sale de lo que el sistema ya registra; no hay carga nueva:
  * notas: estado, fechas (historial_estados), tema, y lo que se lee del texto al subirlas
    (unidades del catálogo y tipo de pedido, tabla nota_entidades);
  * traslados: viajes realizados con el criterio del organismo (desplazamiento / viaje a la unidad);
  * uso: ingresos (intentos_login), documentos subidos, acciones sobre notas y viajes cargados.

Criterios comunes:
  * Se cuentan NOTAS (no informes ni proveídos, que repetirían el mismo asunto), sin borradores ni rechazadas.
  * La fecha de una nota es su fecha de firma si la tiene; si no, el día en que se subió.
  * La unidad de una nota es la que se fijó a mano; si no hay, las que nombra el texto (puede ser más de una).
"""
import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import auth
import database as db
import reportes
import traslados as tr

MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
MINIMO_PUBLICABLE = 3  # datos abiertos: las celdas con 1 o 2 casos se muestran como «menos de 3»
ESTADOS_EMITIDA = ("FIRMADA", "DESPACHADA")
ESTADOS_FUERA = ("BORRADOR", "RECHAZADA")


# ---------------------------------------------------------------- período
def rango(desde="", hasta="", hoy=None):
    """Período pedido o, por defecto, los últimos 12 meses completos más el actual."""
    hoy = hoy or date.today()
    try:
        h = date.fromisoformat(hasta) if hasta else hoy
    except ValueError:
        h = hoy
    try:
        d = date.fromisoformat(desde) if desde else date(h.year - 1, h.month, 1) + timedelta(days=0)
    except ValueError:
        d = date(h.year - 1, h.month, 1)
    if d > h:
        d, h = h, d
    return d.isoformat(), h.isoformat()


def meses_entre(desde, hasta):
    d, h = date.fromisoformat(desde), date.fromisoformat(hasta)
    out, y, m = [], d.year, d.month
    while (y, m) <= (h.year, h.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def etiqueta_mes(m, largo=False):
    i = int(m[5:7]) - 1
    return f"{MESES[i]} {m[:4]}" if largo else f"{MESES_CORTOS[i]} {m[2:4]}"


# ---------------------------------------------------------------- notas con sus datos leídos
def es_nota(n):
    """Nota (y no informe o proveído): por el tipo que lee el sistema del texto; si todavía no tiene, por el nombre."""
    tipo = (n.get("tipo") or "").upper()
    if tipo:
        return tipo.startswith("NOTA")
    return bool(n.get("es_nota")) or "NOTA" in (n.get("nombre_original") or "").upper()


def nombre_corto(nombre):
    """«Complejo Penitenciario Federal I de Ezeiza -CPF I-» → «CPF I» (para gráficos; el nombre completo va en el título)."""
    import re
    m = re.search(r"-\s*([^-]{2,20}?)\s*-\s*\.?$", nombre or "")
    return m.group(1).strip() if m and not m.group(1).lower().startswith("ex ") else (nombre or "")


def _unidades_por_id():
    return {u["id"]: u for u in db.listar_unidades(solo_activas=False)}


def notas_periodo(desde, hasta, solo_notas=True, incluir_todas_las_fechas=False):
    """Notas del período con fecha, mes, equipo, tema, unidades [(id, nombre)] y pedidos [texto]."""
    unidades = _unidades_por_id()
    with db._conexion() as con:
        equipos = {f["id"]: f["nombre"] for f in con.execute("SELECT id, nombre FROM equipos")}
        filas = [dict(f) for f in con.execute(
            "SELECT id, nombre_original, estado, es_nota, tipo, equipo_id, usuario_id, tema, unidad_id, nro_nota, fecha_nota, creado_en "
            "FROM notas WHERE estado NOT IN (%s)" % ",".join("?" * len(ESTADOS_FUERA)), ESTADOS_FUERA)]
        ent_u, ent_p = defaultdict(list), defaultdict(list)
        for f in con.execute("SELECT nota_id, tipo, valor, norm FROM nota_entidades WHERE tipo IN ('UNIDAD', 'PEDIDO')"):
            if f["tipo"] == "UNIDAD" and str(f["norm"]).startswith("u:"):
                try:
                    ent_u[f["nota_id"]].append(int(f["norm"][2:]))
                except ValueError:
                    pass
            elif f["tipo"] == "PEDIDO":
                ent_p[f["nota_id"]].append(f["valor"])
    salida = []
    for n in filas:
        n["es_nota"] = es_nota(n)
        if solo_notas and not n["es_nota"]:
            continue
        fecha = (n.get("fecha_nota") or n.get("creado_en") or "")[:10]
        if not fecha:
            continue
        if not incluir_todas_las_fechas and not (desde <= fecha <= hasta):
            continue
        ids = [n["unidad_id"]] if n.get("unidad_id") else list(dict.fromkeys(ent_u.get(n["id"], [])))
        n.update(fecha=fecha, mes=fecha[:7], equipo=equipos.get(n["equipo_id"], "Sin equipo"),
                 unidades=[(i, unidades[i]["nombre"]) for i in ids if i in unidades],
                 pedidos=sorted(set(ent_p.get(n["id"], []))) or ["Sin tipo de pedido detectado"],
                 emitida=n["estado"] in ESTADOS_EMITIDA)
        salida.append(n)
    return salida


def _viajes_hechos(desde, hasta, hoy=None):
    hoy = hoy or date.today()
    tope = min(hasta, hoy.isoformat())
    if tope < desde:
        return []
    return [tr._decorar(v, hoy) for v in tr.listar_viajes(desde=desde, hasta=tope, limite=100000)
            if v["estado"] == "CONFIRMADO"]


# ---------------------------------------------------------------- 81) tablero de la Dirección
def tablero(desde, hasta, hoy=None):
    hoy = hoy or date.today()
    notas = notas_periodo(desde, hasta)
    with db._conexion() as con:
        ahora = Counter(f["estado"] for f in con.execute("SELECT estado FROM notas WHERE estado NOT IN ('BORRADOR')"))
        hist = [dict(f) for f in con.execute(
            "SELECT h.nota_id, h.estado, h.fecha FROM historial_estados h "
            "WHERE h.estado IN ('SUBIDA', 'FIRMADA', 'DESPACHADA') AND substr(h.fecha, 1, 10) BETWEEN ? AND ? ORDER BY h.id",
            (desde, hasta))]
    ids_notas = {n["id"] for n in notas_periodo(desde, hasta, incluir_todas_las_fechas=True)}
    primeras = {}
    for h in hist:
        if h["nota_id"] not in ids_notas:
            continue
        primeras.setdefault((h["nota_id"], h["estado"]), h["fecha"][:10])
    cuenta_estado = Counter(e for (_, e) in primeras)
    por_mes_firmadas = Counter(f[:7] for (_, e), f in primeras.items() if e == "FIRMADA")
    rep = reportes.generar_reporte(desde=desde, hasta=hasta + "T23:59:59")

    est = tr.estadisticas(desde, hasta, {"tipo": "todos"}, hoy=hoy)
    unidades_notas = Counter(nombre for n in notas for _, nombre in n["unidades"])
    id_de = {nombre: i for n in notas for i, nombre in n["unidades"]}
    visitadas = dict(est["por_unidad_viajes"])
    sin_visita = [(u, c, id_de.get(u)) for u, c in unidades_notas.most_common() if not visitadas.get(u)][:8]
    meses = meses_entre(desde, hasta)[-12:]
    serie_notas = Counter(n["mes"] for n in notas)
    serie_vu = dict(est["por_mes_viajes_unidad"])
    return {
        "desde": desde, "hasta": hasta,
        "kpis": {
            "notas": len(notas),
            "firmadas": cuenta_estado.get("FIRMADA", 0),
            "despachadas": cuenta_estado.get("DESPACHADA", 0),
            "a_la_firma": ahora.get("PROCESADA", 0),
            "en_circuito": sum(ahora.get(e, 0) for e in ("SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA")),
            "viajes_unidad": est["viajes_unidad"], "desplazamientos": est["desplazamientos"],
            "personas": est["personas"], "pct_inmediatos": est["pct_inmediatos"],
        },
        "respuestas": db.resumen_respuestas(desde, hasta),
        "tiempos": {"aprobacion": rep["tiempo_promedio_aprobacion_coordinacion"], "firma": rep["tiempo_promedio_firma"],
                    "despacho": rep["tiempo_promedio_despacho_total"], "muestras": rep["muestras"]},
        "unidades_visitadas": [(nombre_corto(u), n) for u, n in est["por_unidad_viajes"][:10]],
        "unidades_notas": [(nombre_corto(u), n) for u, n in unidades_notas.most_common(10)],
        "pedidos": Counter(p for n in notas for p in n["pedidos"]).most_common(10),
        "sin_visita": sin_visita,
        "serie": [{"mes": m, "etq": etiqueta_mes(m), "notas": serie_notas.get(m, 0), "firmadas": por_mes_firmadas.get(m, 0),
                   "viajes": serie_vu.get(m, 0)} for m in meses],
        "serie_notas": [(m, serie_notas.get(m, 0)) for m in meses],
        "serie_viajes": [(m, serie_vu.get(m, 0)) for m in meses],
        "por_equipo_viajes": est["por_equipo_viajes"][:10],
    }


# ---------------------------------------------------------------- 82) monitoreo por unidad penal
def por_unidad(unidad_id, desde, hasta, hoy=None):
    u = db.obtener_unidad(unidad_id)
    if not u:
        return None
    notas = [n for n in notas_periodo(desde, hasta, solo_notas=False) if any(i == unidad_id for i, _ in n["unidades"])]
    solo_notas = [n for n in notas if n["es_nota"]]
    viajes = [v for v in _viajes_hechos(desde, hasta, hoy) if v.get("unidad_id") == unidad_id]
    visitas = defaultdict(lambda: {"equipos": set(), "personas": 0, "modalidades": set(), "viajes": []})
    for v in viajes:
        d = visitas[v["fecha"]]
        d["equipos"] |= {(p.get("equipo_nombre") or "").strip() or "(sin equipo)" for p in v["participantes"]}
        d["personas"] += len(v["participantes"])
        d["modalidades"].add("con chofer" if v["modalidad"] == "CHOFER" else "medios propios")
        d["viajes"].append(v["id"])
    lista_visitas = [{"fecha": f, "equipos": sorted(d["equipos"]), "personas": d["personas"],
                      "modalidades": sorted(d["modalidades"]), "viajes": d["viajes"]} for f, d in sorted(visitas.items(), reverse=True)]
    vu = tr.viajes_a_unidad(viajes)
    meses = meses_entre(desde, hasta)
    notas_mes, visitas_mes = Counter(n["mes"] for n in solo_notas), Counter(k[0][:7] for k in vu)
    with db._conexion() as con:
        ultima = con.execute("SELECT MAX(fecha) AS f FROM viajes WHERE unidad_id = ? AND estado = 'CONFIRMADO' AND fecha <= ?",
                             (unidad_id, (hoy or date.today()).isoformat())).fetchone()["f"]
    return {
        "unidad": u, "desde": desde, "hasta": hasta,
        "notas": sorted(notas, key=lambda n: n["fecha"], reverse=True),
        "n_notas": len(solo_notas), "n_emitidas": sum(1 for n in solo_notas if n["emitida"]),
        "n_documentos": len(notas),
        "visitas": lista_visitas, "n_visitas": len(lista_visitas), "n_viajes_unidad": len(vu),
        "equipos": Counter(k[3] for k in vu).most_common(),
        "pedidos": Counter(p for n in solo_notas for p in n["pedidos"]).most_common(),
        "temas": Counter((n.get("tema") or "Sin tema").replace("_", " ") for n in solo_notas).most_common(),
        "serie": [{"mes": m, "etq": etiqueta_mes(m), "notas": notas_mes.get(m, 0), "visitas": visitas_mes.get(m, 0)} for m in meses],
        "ultima_visita": ultima,
        "esperando_entrega": db.notas_esperando_entrega(unidad_id),
    }


def unidades_con_actividad(desde, hasta):
    """Para el selector: unidades con notas o visitas en el período primero (con sus cantidades), después el resto."""
    notas = notas_periodo(desde, hasta)
    cn = Counter(i for n in notas for i, _ in n["unidades"])
    cv = Counter(v["unidad_id"] for v in _viajes_hechos(desde, hasta) if v.get("unidad_id"))
    unidades = db.listar_unidades()
    for u in unidades:
        u["n_notas"], u["n_viajes"] = cn.get(u["id"], 0), cv.get(u["id"], 0)
    return sorted(unidades, key=lambda u: (-(u["n_notas"] + u["n_viajes"]), u["nombre"].lower()))


def excel_unidad(d):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.Workbook()
    cab, fondo = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="7C1413")

    def encabezar(ws, fila, titulos, anchos):
        for j, (t, a) in enumerate(zip(titulos, anchos), 1):
            c = ws.cell(row=fila, column=j, value=t)
            c.font, c.fill, c.alignment = cab, fondo, Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[openpyxl.utils.get_column_letter(j)].width = a

    ws = wb.active
    ws.title = "Resumen"
    u = d["unidad"]
    ws["A1"] = f"Monitoreo de {u['nombre']}"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Período: {_dmy(d['desde'])} al {_dmy(d['hasta'])}. Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}."
    ws["A2"].font = Font(italic=True, size=9, color="555555")
    filas = [("Notas que la mencionan", d["n_notas"]), ("de ellas, firmadas o despachadas", d["n_emitidas"]),
             ("Días con visita", d["n_visitas"]), ("Viajes a la unidad (equipo + día)", d["n_viajes_unidad"]),
             ("Última visita registrada", _dmy(d["ultima_visita"]) if d["ultima_visita"] else "sin visitas")]
    for i, (k, v) in enumerate(filas, 4):
        ws.cell(row=i, column=1, value=k)
        ws.cell(row=i, column=2, value=v)
    r = 4 + len(filas) + 1
    encabezar(ws, r, ["Tipo de pedido", "Notas"], [44, 12])
    for k, v in d["pedidos"]:
        r += 1
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v)
    r += 2
    encabezar(ws, r, ["Mes", "Notas", "Viajes a la unidad"], [44, 12, 18])
    for s in d["serie"]:
        r += 1
        ws.cell(row=r, column=1, value=etiqueta_mes(s["mes"], True))
        ws.cell(row=r, column=2, value=s["notas"])
        ws.cell(row=r, column=3, value=s["visitas"])

    wn = wb.create_sheet("Notas")
    encabezar(wn, 1, ["Fecha", "N.º de nota", "Documento", "Equipo", "Tipo de pedido", "Estado"], [12, 14, 48, 26, 40, 14])
    for n in d["notas"]:
        wn.append([date.fromisoformat(n["fecha"]), n.get("nro_nota") or "", n["nombre_original"], n["equipo"],
                   "; ".join(n["pedidos"]), n["estado"].capitalize()])
        wn.cell(row=wn.max_row, column=1).number_format = "DD/MM/YYYY"
    wn.auto_filter.ref = wn.dimensions

    wv = wb.create_sheet("Visitas")
    encabezar(wv, 1, ["Fecha", "Equipos", "Personas", "Modalidad"], [12, 50, 10, 26])
    for v in d["visitas"]:
        wv.append([date.fromisoformat(v["fecha"]), ", ".join(v["equipos"]), v["personas"], ", ".join(v["modalidades"])])
        wv.cell(row=wv.max_row, column=1).number_format = "DD/MM/YYYY"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _dmy(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso and len(iso) >= 10 else (iso or "")


# ---------------------------------------------------------------- 86) mapa de calor de temas
def mapa_temas(desde, hasta, pedido="", filas=15):
    """Unidad × mes (cantidad de notas, opcionalmente de un tipo de pedido) y unidad × tipo de pedido."""
    notas = [n for n in notas_periodo(desde, hasta) if n["unidades"]]
    if pedido:
        notas_f = [n for n in notas if pedido in n["pedidos"]]
    else:
        notas_f = notas
    meses = meses_entre(desde, hasta)
    celdas, total_u = defaultdict(Counter), Counter()
    for n in notas_f:
        for _, nombre in n["unidades"]:
            celdas[nombre][n["mes"]] += 1
            total_u[nombre] += 1
    unidades = [u for u, _ in total_u.most_common(filas)]
    maximo = max((celdas[u][m] for u in unidades for m in meses), default=0)
    mes_x_unidad = {"meses": [{"mes": m, "etq": etiqueta_mes(m)} for m in meses],
                    "filas": [{"unidad": u, "corto": nombre_corto(u), "total": total_u[u], "celdas": [_celda(celdas[u][m], maximo) for m in meses]} for u in unidades],
                    "maximo": maximo}
    # unidad × tipo de pedido (siempre sobre todos los pedidos)
    cp, tot_u2, tot_p = defaultdict(Counter), Counter(), Counter()
    for n in notas:
        for _, nombre in n["unidades"]:
            for p in n["pedidos"]:
                cp[nombre][p] += 1
                tot_p[p] += 1
            tot_u2[nombre] += 1
    pedidos = [p for p, _ in tot_p.most_common() if p != "Sin tipo de pedido detectado"][:10]
    unidades2 = [u for u, _ in tot_u2.most_common(filas)]
    maximo2 = max((cp[u][p] for u in unidades2 for p in pedidos), default=0)
    unidad_x_pedido = {"pedidos": pedidos,
                       "filas": [{"unidad": u, "corto": nombre_corto(u), "total": tot_u2[u], "celdas": [_celda(cp[u][p], maximo2) for p in pedidos]} for u in unidades2],
                       "maximo": maximo2}
    # repeticiones: misma unidad y mismo pedido en 3 meses distintos o más del período
    meses_up = defaultdict(set)
    for n in notas:
        for _, nombre in n["unidades"]:
            for p in n["pedidos"]:
                if p != "Sin tipo de pedido detectado":
                    meses_up[(nombre, p)].add(n["mes"])
    repetidos = sorted(((u, p, len(ms)) for (u, p), ms in meses_up.items() if len(ms) >= 3), key=lambda x: (-x[2], x[0]))[:15]
    todos_pedidos = sorted({p for n in notas_periodo(desde, hasta) for p in n["pedidos"]})
    return {"mes_x_unidad": mes_x_unidad, "unidad_x_pedido": unidad_x_pedido, "repetidos": repetidos,
            "pedidos": todos_pedidos, "pedido": pedido, "n_notas": len(notas_f), "sin_unidad": len(notas_periodo(desde, hasta)) - len(notas)}


def _celda(n, maximo):
    """Nivel 0–5 de la escala (0 = sin casos)."""
    if not n or not maximo:
        return {"n": n, "nivel": 0}
    return {"n": n, "nivel": max(1, min(5, round(5 * n / maximo)))}


# ---------------------------------------------------------------- 88) datos agregados publicables
def _publicable(n, minimo=MINIMO_PUBLICABLE):
    return n if (n == 0 or n >= minimo) else f"menos de {minimo}"


def datos_abiertos(desde, hasta, minimo=MINIMO_PUBLICABLE, hoy=None):
    """Tablas agregadas sin datos personales: solo meses, unidades (instituciones públicas), tipos de pedido y totales.
    Las celdas con 1 o 2 casos se ocultan (pueden identificar a una persona en una unidad chica)."""
    notas = notas_periodo(desde, hasta)
    meses = meses_entre(desde, hasta)
    pedidos = sorted({p for n in notas for p in n["pedidos"]})
    c_mp = Counter((n["mes"], p) for n in notas for p in n["pedidos"])
    t1 = {"titulo": "Notas por mes y tipo de pedido", "columnas": ["Mes"] + pedidos + ["Total de notas"],
          "filas": [[etiqueta_mes(m, True)] + [_publicable(c_mp[(m, p)], minimo) for p in pedidos] +
                    [_publicable(sum(1 for n in notas if n["mes"] == m), minimo)] for m in meses]}
    c_up, unidades = Counter(), Counter()
    for n in notas:
        for _, u in n["unidades"]:
            unidades[u] += 1
            for p in n["pedidos"]:
                c_up[(u, p)] += 1
    t2 = {"titulo": "Notas por unidad y tipo de pedido", "columnas": ["Unidad"] + pedidos + ["Total de notas"],
          "filas": [[u] + [_publicable(c_up[(u, p)], minimo) for p in pedidos] + [_publicable(t, minimo)] for u, t in unidades.most_common()]}
    viajes = _viajes_hechos(desde, hasta, hoy)
    vu = tr.viajes_a_unidad(viajes)
    c_vu = Counter((k[2], k[0][:7]) for k in vu)
    destinos = Counter(k[2] for k in vu)
    t3 = {"titulo": "Viajes a las unidades por mes (equipo y día; criterio del organismo)", "columnas": ["Unidad"] + [etiqueta_mes(m) for m in meses] + ["Total"],
          "filas": [[u] + [_publicable(c_vu[(u, m)], minimo) for m in meses] + [_publicable(t, minimo)] for u, t in destinos.most_common()]}
    desplaz = Counter(k[0][:7] for k in {tr.clave_desplazamiento(v) for v in viajes if v["modalidad"] == "CHOFER"})
    c_vu_mes = Counter(k[0][:7] for k in vu)
    t4 = {"titulo": "Desplazamientos y viajes a las unidades por mes", "columnas": ["Mes", "Desplazamientos", "Viajes a las unidades"],
          "filas": [[etiqueta_mes(m, True), desplaz.get(m, 0), c_vu_mes.get(m, 0)] for m in meses]}
    return {"desde": desde, "hasta": hasta, "minimo": minimo, "tablas": [t1, t2, t3, t4],
            "notas_metodologicas": [
                "Se cuentan notas (no informes ni proveídos), sin borradores ni rechazadas. La fecha es la de firma o, si no tiene, la de carga.",
                "La unidad y el tipo de pedido los lee el sistema del texto de cada nota; una nota puede tener más de una unidad y más de un pedido, por eso las columnas no suman el total.",
                f"Para proteger a las personas, las celdas con 1 a {minimo - 1} casos se publican como «menos de {minimo}».",
                "Viaje a la unidad: un equipo que va a una unidad un día. Desplazamiento: la salida de un vehículo institucional en un día.",
                "No contiene nombres, documentos, números de expediente ni de nota, ni datos de las personas privadas de la libertad o del personal.",
            ]}


def abiertos_csv(d):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"Datos agregados DGPDH · {_dmy(d['desde'])} al {_dmy(d['hasta'])}"])
    for t in d["tablas"]:
        w.writerow([])
        w.writerow([t["titulo"]])
        w.writerow(t["columnas"])
        w.writerows(t["filas"])
    w.writerow([])
    w.writerow(["Notas metodológicas"])
    for n in d["notas_metodologicas"]:
        w.writerow([n])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def abiertos_excel(d):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Leeme"
    ws["A1"] = "Datos agregados DGPDH (aptos para publicar)"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Período: {_dmy(d['desde'])} al {_dmy(d['hasta'])}. Generado el {datetime.now().strftime('%d/%m/%Y')}."
    for i, n in enumerate(d["notas_metodologicas"], 4):
        ws.cell(row=i, column=1, value="• " + n).alignment = Alignment(wrap_text=True)
    ws.column_dimensions["A"].width = 110
    for k, t in enumerate(d["tablas"], 1):
        h = wb.create_sheet(f"Tabla {k}")
        h["A1"] = t["titulo"]
        h["A1"].font = Font(bold=True, size=12)
        h.append(t["columnas"])
        for j in range(1, len(t["columnas"]) + 1):
            c = h.cell(row=2, column=j)
            c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="7C1413")
            c.alignment = Alignment(wrap_text=True, vertical="center")
            h.column_dimensions[openpyxl.utils.get_column_letter(j)].width = 44 if j == 1 else 14
        for f in t["filas"]:
            h.append(f)
        h.freeze_panes = "B3"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- 89) uso del sistema por área
def area_de(u, equipos):
    if u["rol"] in auth.ROLES_DIRECCION:
        return "Dirección General"
    if u["rol"] in auth.ROLES_MESA:
        return "Mesa de Entradas"
    if u["rol"] == auth.ROL_CHOFER:
        return "Choferes"
    return equipos.get(u.get("equipo_id")) or "Sin área asignada"


def uso_sistema(desde, hasta, hoy=None):
    """Por área: personas activas, cuántas entraron en el período, ingresos, documentos subidos, acciones sobre notas,
    viajes cargados y quiénes no entran hace más de 30 días (dónde puede hacer falta capacitación o acompañamiento)."""
    hoy = hoy or date.today()
    with db._conexion() as con:
        equipos = {f["id"]: f["nombre"] for f in con.execute("SELECT id, nombre FROM equipos")}
        usuarios = [dict(f) for f in con.execute("SELECT * FROM usuarios WHERE activo = 1")]
        ingresos = Counter(f["usuario"].lower() for f in con.execute(
            "SELECT usuario FROM intentos_login WHERE exito = 1 AND substr(fecha, 1, 10) BETWEEN ? AND ?", (desde, hasta)))
        ultimo = {f["u"]: f["f"] for f in con.execute(
            "SELECT lower(usuario) AS u, MAX(fecha) AS f FROM intentos_login WHERE exito = 1 GROUP BY lower(usuario)")}
        subidos = Counter(f["usuario_id"] for f in con.execute(
            "SELECT usuario_id FROM notas WHERE usuario_id IS NOT NULL AND substr(creado_en, 1, 10) BETWEEN ? AND ?", (desde, hasta)))
        acciones = Counter((f["usuario"] or "").lower() for f in con.execute(
            "SELECT usuario FROM historial_estados WHERE usuario IS NOT NULL AND substr(fecha, 1, 10) BETWEEN ? AND ?", (desde, hasta)))
        viajes = Counter(f["creado_por_id"] for f in con.execute(
            "SELECT creado_por_id FROM viajes WHERE creado_por_id IS NOT NULL AND substr(creado_en, 1, 10) BETWEEN ? AND ?", (desde, hasta)))
    areas = defaultdict(lambda: {"personas": 0, "entraron": 0, "ingresos": 0, "subidos": 0, "acciones": 0, "viajes": 0,
                                 "sin_entrar": [], "nunca": []})
    limite = (hoy - timedelta(days=30)).isoformat()
    for u in usuarios:
        a = areas[area_de(u, equipos)]
        clave = u["usuario"].lower()
        a["personas"] += 1
        a["entraron"] += 1 if ingresos.get(clave) else 0
        a["ingresos"] += ingresos.get(clave, 0)
        a["subidos"] += subidos.get(u["id"], 0)
        a["acciones"] += acciones.get(u["nombre"].lower(), 0) + acciones.get(clave, 0)
        a["viajes"] += viajes.get(u["id"], 0)
        ult = ultimo.get(clave)
        if not ult:
            a["nunca"].append(u["nombre"])
        elif ult[:10] < limite:
            a["sin_entrar"].append((u["nombre"], ult[:10]))
    filas = []
    for nombre, a in areas.items():
        a["area"] = nombre
        a["pct"] = round(100 * a["entraron"] / a["personas"]) if a["personas"] else 0
        a["sin_entrar"].sort(key=lambda x: x[1])
        a["nunca"].sort()
        filas.append(a)
    filas.sort(key=lambda a: (a["pct"], a["area"].lower()))
    return {"desde": desde, "hasta": hasta, "filas": filas,
            "total": {"personas": sum(a["personas"] for a in filas), "entraron": sum(a["entraron"] for a in filas),
                      "ingresos": sum(a["ingresos"] for a in filas)}}
