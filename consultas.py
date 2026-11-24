"""Ola 5 · Reforma 14: preguntas en lenguaje común sobre el archivo.

«¿Cuántas notas por colchones al CPF I en 2026?» se traduce a FILTROS que la persona ve y puede corregir antes de
confiar en el número (la IA solo propone la interpretación; el conteo lo hace la base de datos, nunca la IA):

    fuente   notas | viajes
    palabras palabras que tiene que contener el texto de la nota («colchones» → busca colchón/colchones)
    unidades unidades del catálogo
    pedidos  tipos de pedido que el sistema lee del texto (Atención médica, Huelga de hambre, ...)
    equipos  equipos
    estados  firmadas, despachadas, a la firma, en curso, rechazadas
    desde / hasta
    agrupar  unidad | mes | pedido | equipo | estado (o nada)

Primero se interpreta con reglas (sin IA, instantáneo y siempre igual). Si la IA está disponible, se le pide una
segunda lectura y solo se usa para completar lo que las reglas no encontraron.
"""
import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import date, timedelta

import database as db
import indicadores as ind
import traslados as tr

MESES = {m: i for i, m in enumerate(("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                      "septiembre", "octubre", "noviembre", "diciembre"), 1)}
MESES["setiembre"] = 9
ESTADOS = {
    "FIRMADAS": ("Firmadas o despachadas", ("FIRMADA", "DESPACHADA")),
    "DESPACHADAS": ("Despachadas", ("DESPACHADA",)),
    "A_LA_FIRMA": ("Esperando la firma", ("PROCESADA",)),
    "EN_CURSO": ("En curso (sin firmar)", ("SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA", "ERROR")),
    "RECHAZADAS": ("Rechazadas", ("RECHAZADA",)),
}
AGRUPAR = {"unidad": "Unidad", "mes": "Mes", "pedido": "Tipo de pedido", "equipo": "Equipo", "estado": "Estado"}
VACIAS = set("""a al algo algun alguna algunas alguno algunos ante antes cada como con contra cual cuales cuando cuanta cuantas
cuanto cuantos de del desde donde dos durante e el ella ellas ellos en entre era es esa esas ese eso esos esta estas este esto
estos fue fueron ha han hay hasta hicieron hizo la las le les lo los mas me mi muy no nos o otra otro para pero poco por que
quien se segun ser si sin sobre son su sus tambien te tiene tienen todas todo todos tu un una unas uno unos y ya
dame decime mostrame mostrar listar lista lista quiero quisiera saber ver total totales cantidad numero cuantas cuantos cuales
nota notas documento documentos archivo archivos expediente expedientes enviamos enviaron enviadas enviada emitimos emitieron
emitidas emitida salieron hecha hechas mandamos mandaron mes meses ano anos dia dias semana semanas trimestre semestre ultimo
ultimos ultima ultimas pasado pasada actual este esta corriente hoy ayer unidad unidades complejo penitenciario federal
viaje viajes visita visitas visitamos fuimos desplazamiento desplazamientos firmadas firmada firmados despachadas despachada
pendientes pendiente firma rechazadas rechazada curso tema temas pedido pedidos motivo motivos equipo equipos estado estados
agrupado agrupadas agrupar separado separadas desglose abierto abiertas cuantas hubo tuvimos tenemos registradas registrados
primer primero segundo segunda tercer tercero cuarto cuarta""".split())


def _norm(t):
    return re.sub(r"\s+", " ", "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower())
                                       if unicodedata.category(c) != "Mn")).strip()


def raiz(palabra):
    """Raíz simple para buscar plurales y singulares: colchones → colchon, visitas → visita."""
    p = _norm(palabra)
    if len(p) > 6 and p.endswith("ces"):
        return p[:-3]
    if len(p) > 5 and p.endswith("es") and p[-3] not in "aeiou":
        return p[:-2]
    if len(p) > 4 and p.endswith("s"):
        return p[:-1]
    return p


def filtros_vacios():
    return {"fuente": "notas", "palabras": [], "unidades": [], "pedidos": [], "equipos": [], "estados": [],
            "desde": "", "hasta": "", "agrupar": "", "origen": "reglas", "no_entendido": []}


# ---------------------------------------------------------------- fechas
def _ult_dia(y, m):
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def _fechas(t, hoy):
    """Devuelve (desde, hasta, palabras consumidas) a partir del texto normalizado."""
    y = hoy.year
    m = re.search(r"\b(?:entre|desde)(?: el)? (\d{1,2})/(\d{1,2})/(\d{2,4}) (?:y|al|hasta)(?: el)? (\d{1,2})/(\d{1,2})/(\d{2,4})\b", t)
    if m:
        a = [int(x) for x in m.groups()]
        fy = lambda v: v + 2000 if v < 100 else v
        try:
            return date(fy(a[2]), a[1], a[0]).isoformat(), date(fy(a[5]), a[4], a[3]).isoformat()
        except ValueError:
            pass
    m = re.search(r"\bultim[oa]s? (\d{1,3}) (dias|semanas|meses|anos)\b", t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        dias = {"dias": 1, "semanas": 7, "meses": 30, "anos": 365}[u] * n
        return (hoy - timedelta(days=dias)).isoformat(), hoy.isoformat()
    if re.search(r"\beste mes\b|\bmes (actual|corriente|en curso)\b", t):
        return date(y, hoy.month, 1).isoformat(), hoy.isoformat()
    if re.search(r"\b(el )?mes pasado\b", t):
        pm = date(y, hoy.month, 1) - timedelta(days=1)
        return date(pm.year, pm.month, 1).isoformat(), pm.isoformat()
    if re.search(r"\besta semana\b", t):
        return (hoy - timedelta(days=hoy.weekday())).isoformat(), hoy.isoformat()
    if re.search(r"\bsemana pasada\b", t):
        l = hoy - timedelta(days=hoy.weekday() + 7)
        return l.isoformat(), (l + timedelta(days=6)).isoformat()
    if re.search(r"\bayer\b", t):
        a = hoy - timedelta(days=1)
        return a.isoformat(), a.isoformat()
    if re.search(r"\bhoy\b", t):
        return hoy.isoformat(), hoy.isoformat()
    anio = None
    m = re.search(r"\b(20\d\d)\b", t)
    if m:
        anio = int(m.group(1))
    elif re.search(r"\b(el )?ano pasado\b", t):
        anio = y - 1
    elif re.search(r"\beste ano\b|\bano (actual|en curso)\b", t):
        anio = y
    m = re.search(r"\b(primer|segundo|tercer|cuarto)(?:o)? trimestre\b", t)
    if m:
        q = ("primer", "segundo", "tercer", "cuarto").index(m.group(1))
        a = anio or y
        return date(a, 3 * q + 1, 1).isoformat(), _ult_dia(a, 3 * q + 3).isoformat()
    m = re.search(r"\b(primer|segundo) semestre\b", t)
    if m:
        a, s = anio or y, (0 if m.group(1) == "primer" else 1)
        return date(a, 6 * s + 1, 1).isoformat(), _ult_dia(a, 6 * s + 6).isoformat()
    meses = [MESES[w] for w in re.findall(r"\b(" + "|".join(MESES) + r")\b", t)]
    if meses:
        a = anio or (y if min(meses) <= hoy.month else y - 1)
        return date(a, min(meses), 1).isoformat(), _ult_dia(a, max(meses)).isoformat()
    if anio:
        return date(anio, 1, 1).isoformat(), date(anio, 12, 31).isoformat()
    return "", ""


# ---------------------------------------------------------------- reglas
def interpretar_reglas(pregunta, hoy=None):
    hoy = hoy or date.today()
    f = filtros_vacios()
    t = _norm(pregunta)
    if re.search(r"\b(viajes?|visitas?|visitamos|fuimos|desplazamientos?|traslados? a)\b", t) and not re.search(r"\bnotas?\b", t):
        f["fuente"] = "viajes"
    f["desde"], f["hasta"] = _fechas(t, hoy)
    for u in db.proponer_unidades(pregunta, 5):
        f["unidades"].append(u["id"])
    import agentes
    pedidos_nombrados = [p for p in agentes.PEDIDOS if _norm(p) in t]
    f["pedidos"] = pedidos_nombrados
    with db._conexion() as con:
        equipos = [dict(e) for e in con.execute("SELECT id, nombre FROM equipos")]
    for e in equipos:
        ne = _norm(e["nombre"])
        resto = ne.replace("equipo ", "").strip()
        if (len(resto) >= 4 and re.search(r"\b" + re.escape(resto) + r"\b", t)) or ne in t:
            if not f["unidades"] or resto not in " ".join(_norm(u["nombre"]) for u in db.listar_unidades() if u["id"] in f["unidades"]):
                f["equipos"].append(e["id"])
    if re.search(r"\bdespachad", t):
        f["estados"].append("DESPACHADAS")
    elif re.search(r"\bfirmad", t):
        f["estados"].append("FIRMADAS")
    if re.search(r"\b(a la firma|pendientes? de firma|sin firmar|para firmar)\b", t):
        f["estados"] = ["A_LA_FIRMA"]
    elif re.search(r"\ben curso\b|\bpendientes?\b", t) and not f["estados"]:
        f["estados"].append("EN_CURSO")
    if re.search(r"\brechazad", t):
        f["estados"].append("RECHAZADAS")
    m = re.search(r"\b(?:por|segun|separad[oa]s? por|agrupad[oa]s? por|desglosad[oa]s? por|cada) (unidad|unidades|mes|meses|tema|temas|pedido|pedidos|motivo|motivos|equipo|equipos|estado|estados)\b", t)
    if m:
        g = m.group(1)
        f["agrupar"] = ("unidad" if g.startswith("unidad") else "mes" if g.startswith("mes") else
                        "pedido" if g.startswith(("tema", "pedido", "motivo")) else "equipo" if g.startswith("equipo") else "estado")
    # palabras de contenido: lo que queda
    usadas = set()
    for uid in f["unidades"]:
        u = db.obtener_unidad(uid)
        usadas |= set(re.findall(r"[a-z0-9]+", _norm((u or {}).get("nombre", "") + " " + ((u or {}).get("alias") or ""))))
    for eid in f["equipos"]:
        usadas |= set(re.findall(r"[a-z0-9]+", _norm(next((e["nombre"] for e in equipos if e["id"] == eid), ""))))
    for p in f["pedidos"]:
        usadas |= set(re.findall(r"[a-z]+", _norm(p)))
    palabras = []
    for w in re.findall(r"[a-z0-9ñ]+", t):
        if w in VACIAS or w in usadas or w in MESES or w.isdigit() or len(w) < 4 or re.fullmatch(r"[ivxl]+", w):
            continue
        if w in ("ultimos", "ultimas", "agrupad", "separad") or w.startswith(("firmad", "despachad", "rechazad", "agrupad", "separad", "desglos")):
            continue
        palabras.append(w)
    f["palabras"] = list(dict.fromkeys(palabras))[:5]
    if f["fuente"] == "viajes":
        f["palabras"], f["pedidos"], f["estados"] = [], [], []
    return f


# ---------------------------------------------------------------- IA (segunda lectura)
def interpretar_ia(pregunta, hoy=None):
    """Pide a la IA la misma estructura. Devuelve un dict parcial validado, o None si no hay IA o no respondió bien."""
    import agentes
    import nucleo
    hoy = hoy or date.today()
    if not nucleo.ia_disponible("clasificacion"):
        return None
    prompt = (
        "Convertí la pregunta en filtros de búsqueda para un archivo de notas de un organismo de control penitenciario. "
        f"Hoy es {hoy.isoformat()}. Respondé SOLO un JSON con estas claves: "
        '"fuente" ("notas" o "viajes"), "palabras" (lista de palabras clave del asunto, sin unidades ni fechas), '
        '"unidad" (texto de la unidad penal tal como aparece, o ""), '
        f'"pedidos" (lista, solo de estos valores: {json.dumps(list(agentes.PEDIDOS), ensure_ascii=False)}), '
        '"desde" y "hasta" (fechas AAAA-MM-DD o ""), "agrupar" ("unidad", "mes", "pedido", "equipo", "estado" o "").\n'
        f"Pregunta: {pregunta}\nJSON:")
    import trazabilidad_ia
    with trazabilidad_ia.para(None, "CONSULTA"):
        texto = nucleo.consultar_ollama("clasificacion", prompt, num_predict=250, timeout=60, formato_json=True)
    if not texto:
        return None
    try:
        d = json.loads(texto)
    except Exception:
        logging.info("La IA no devolvió un JSON válido para la consulta")
        return None
    out = {}
    if d.get("fuente") in ("notas", "viajes"):
        out["fuente"] = d["fuente"]
    out["palabras"] = [str(p)[:40] for p in (d.get("palabras") or []) if isinstance(p, str) and len(p) >= 4][:5]
    out["pedidos"] = [p for p in (d.get("pedidos") or []) if p in agentes.PEDIDOS]
    if d.get("unidad"):
        out["unidades"] = [u["id"] for u in db.proponer_unidades(str(d["unidad"]), 2)]
    for k in ("desde", "hasta"):
        try:
            out[k] = date.fromisoformat(str(d.get(k) or "")).isoformat()
        except ValueError:
            pass
    if d.get("agrupar") in AGRUPAR:
        out["agrupar"] = d["agrupar"]
    return out


def interpretar(pregunta, usar_ia=True, hoy=None):
    f = interpretar_reglas(pregunta, hoy)
    if usar_ia:
        try:
            ia = interpretar_ia(pregunta, hoy)
        except Exception:
            logging.exception("Error al interpretar la consulta con IA")
            ia = None
        if ia:
            completo = False
            for k in ("unidades", "pedidos", "agrupar", "desde", "hasta"):
                if not f.get(k) and ia.get(k):
                    f[k] = ia[k]
                    completo = True
            if ia.get("palabras") and f["fuente"] == "notas":
                nuevas = [p for p in ia["palabras"] if raiz(p) not in {raiz(x) for x in f["palabras"]}]
                if not f["palabras"] and nuevas:
                    f["palabras"] = nuevas[:3]
                    completo = True
            if completo:
                f["origen"] = "reglas + IA"
    return f


def filtros_desde_form(form):
    """La persona corrige la interpretación en el formulario: se toma tal cual."""
    f = filtros_vacios()
    f["fuente"] = "viajes" if form.get("fuente") == "viajes" else "notas"
    f["palabras"] = [w for w in re.split(r"[,\s]+", str(form.get("palabras") or "")) if len(w) >= 3][:6]
    f["unidades"] = [int(x) for x in form.getlist("unidades") if str(x).isdigit()] if hasattr(form, "getlist") else []
    f["pedidos"] = [p for p in (form.getlist("pedidos") if hasattr(form, "getlist") else []) if p]
    f["equipos"] = [int(x) for x in form.getlist("equipos") if str(x).isdigit()] if hasattr(form, "getlist") else []
    f["estados"] = [e for e in (form.getlist("estados") if hasattr(form, "getlist") else []) if e in ESTADOS]
    for k in ("desde", "hasta"):
        try:
            f[k] = date.fromisoformat(str(form.get(k) or "")).isoformat()
        except ValueError:
            f[k] = ""
    f["agrupar"] = form.get("agrupar") if form.get("agrupar") in AGRUPAR else ""
    f["origen"] = "corregida a mano"
    return f


# ---------------------------------------------------------------- ejecución
def _ids_por_texto(palabras):
    """Notas cuyo texto contiene TODAS las palabras (por raíz)."""
    if not palabras:
        return None
    ids = None
    for p in palabras:
        r = raiz(p)
        hallados = {i for i, _ in db.buscar_texto(r, limite=100000)}
        ids = hallados if ids is None else ids & hallados
    return ids or set()


def ejecutar(f, puede_ver=None, usuario=None, hoy=None):
    """Aplica los filtros. Devuelve total, desglose (si se agrupó) y la lista (las notas que la persona puede ver)."""
    hoy = hoy or date.today()
    desde = f["desde"] or "2000-01-01"
    hasta = f["hasta"] or hoy.isoformat()
    if f["fuente"] == "viajes":
        return _ejecutar_viajes(f, desde, hasta, hoy)
    notas = ind.notas_periodo(desde, hasta, solo_notas=True)
    if puede_ver and usuario:
        con_estado = {n["id"]: n for n in notas}
        visibles = []
        for n in notas:
            completa = db.obtener_nota(n["id"])
            if completa and puede_ver(usuario, completa):
                visibles.append(con_estado[n["id"]])
        notas = visibles
    por_texto = _ids_por_texto(f["palabras"])
    if por_texto is not None:
        notas = [n for n in notas if n["id"] in por_texto]
    if f["unidades"]:
        quiero = set(f["unidades"])
        notas = [n for n in notas if quiero & {i for i, _ in n["unidades"]}]
    if f["pedidos"]:
        notas = [n for n in notas if set(f["pedidos"]) & set(n["pedidos"])]
    if f["equipos"]:
        notas = [n for n in notas if n["equipo_id"] in f["equipos"]]
    if f["estados"]:
        validos = {e for k in f["estados"] for e in ESTADOS[k][1]}
        notas = [n for n in notas if n["estado"] in validos]
    desglose = []
    if f["agrupar"]:
        c = Counter()
        for n in notas:
            if f["agrupar"] == "unidad":
                for _, u in (n["unidades"] or [(0, "Sin unidad detectada")]):
                    c[u] += 1
            elif f["agrupar"] == "mes":
                c[n["mes"]] += 1
            elif f["agrupar"] == "pedido":
                for p in n["pedidos"]:
                    c[p] += 1
            elif f["agrupar"] == "equipo":
                c[n["equipo"]] += 1
            elif f["agrupar"] == "estado":
                c[n["estado"].capitalize()] += 1
        desglose = sorted(c.items()) if f["agrupar"] == "mes" else c.most_common()
        if f["agrupar"] == "mes":
            desglose = [(ind.etiqueta_mes(k), v) for k, v in desglose]
    notas.sort(key=lambda n: n["fecha"], reverse=True)
    return {"fuente": "notas", "total": len(notas), "desglose": desglose, "lista": notas[:200], "desde": desde, "hasta": hasta,
            "unidad_total": "nota" if len(notas) == 1 else "notas"}


def _ejecutar_viajes(f, desde, hasta, hoy):
    viajes = ind._viajes_hechos(desde, hasta, hoy)
    if f["unidades"]:
        viajes = [v for v in viajes if v.get("unidad_id") in f["unidades"]]
    if f["equipos"]:
        with db._conexion() as con:
            nombres = {_norm(e["nombre"]) for e in con.execute("SELECT nombre FROM equipos WHERE id IN (%s)" % ",".join("?" * len(f["equipos"])), f["equipos"])}
        viajes = [v for v in viajes if any(_norm(p.get("equipo_nombre")) in nombres for p in v["participantes"])]
    vu = tr.viajes_a_unidad(viajes)
    desglose = []
    if f["agrupar"] == "unidad":
        desglose = Counter(k[2] for k in vu).most_common()
    elif f["agrupar"] == "mes":
        desglose = [(ind.etiqueta_mes(k), v) for k, v in sorted(Counter(k[0][:7] for k in vu).items())]
    elif f["agrupar"] == "equipo":
        desglose = Counter(k[3] for k in vu).most_common()
    viajes.sort(key=lambda v: v["fecha"], reverse=True)
    return {"fuente": "viajes", "total": len(vu), "desglose": desglose, "lista": viajes[:200], "desde": desde, "hasta": hasta,
            "unidad_total": "viaje a la unidad" if len(vu) == 1 else "viajes a las unidades", "viajes_registrados": len(viajes)}


def describir(f):
    """Frase con la interpretación, para que la persona la confirme."""
    partes = []
    partes.append("viajes a las unidades (equipo + día)" if f["fuente"] == "viajes" else "notas")
    if f["palabras"]:
        partes.append("que mencionan " + " y ".join(f"«{p}»" for p in f["palabras"]))
    if f["pedidos"]:
        partes.append("con pedido de " + " o ".join(f["pedidos"]))
    if f["unidades"]:
        nombres = [((db.obtener_unidad(i) or {}).get("nombre") or f"#{i}") for i in f["unidades"]]
        partes.append(("sobre " if f["fuente"] == "notas" else "a ") + " o ".join(nombres))
    if f["equipos"]:
        with db._conexion() as con:
            ne = [e["nombre"] for e in con.execute("SELECT nombre FROM equipos WHERE id IN (%s)" % ",".join("?" * len(f["equipos"])), f["equipos"])]
        partes.append("del equipo " + " o ".join(ne))
    if f["estados"]:
        partes.append("(" + ", ".join(ESTADOS[e][0].lower() for e in f["estados"]) + ")")
    if f["desde"] or f["hasta"]:
        partes.append(f"entre el {ind._dmy(f['desde']) or 'principio'} y el {ind._dmy(f['hasta']) or 'hoy'}")
    else:
        partes.append("de todo el archivo")
    if f["agrupar"]:
        partes.append(f"separadas por {AGRUPAR[f['agrupar']].lower()}")
    return " ".join(partes)


EJEMPLOS = (
    "¿Cuántas notas por colchones al CPF I en 2026?",
    "Notas de huelga de hambre por unidad en los últimos 6 meses",
    "¿Cuántas visitas al CPF II el mes pasado?",
    "Notas firmadas sobre atención médica por mes este año",
    "Viajes a las unidades por equipo en el primer trimestre",
)
