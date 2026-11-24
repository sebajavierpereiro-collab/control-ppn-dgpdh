"""Importa el historial de viajes del Excel «Listado de viajes» (una hoja por semana,
bloques de 8 columnas por día) al módulo de Traslados.

Uso:  python importar_viajes.py archivo.xlsx [--aplicar]      (sin --aplicar solo informa)
También desde la pantalla «Importar Excel» de Traslados (solo coordinación).
"""
import difflib
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

import database as db
import traslados as tr

MESES = {"ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7, "AGOSTO": 8,
         "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12}
DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
VACIOS = {"", "seleccionar opcion", "seleccionar opción", "-", "—", "s/d", "n/a"}


def _sin_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")


def _norm(s):
    return re.sub(r"\s+", " ", _sin_acentos(s or "").lower()).strip()


def _texto(v):
    if v is None:
        return None
    t = re.sub(r"\s+", " ", str(v)).strip()
    return None if _norm(t) in VACIOS else t


def lunes_de_hoja(nombre, anios=(2025, 2026, 2024, 2027)):
    """Fecha del lunes de la semana a partir del nombre de la hoja («MAYO 26 AL 30»)."""
    m = re.match(r"^\s*([A-Za-zÁÉÍÓÚáéíóú]+)\s+(?:del?\s+)?(\d{1,2})", nombre or "", re.I)
    if not m:
        return None
    mes = MESES.get(_sin_acentos(m.group(1)).upper())
    if not mes:
        return None
    for a in anios:
        try:
            f = date(a, mes, int(m.group(2)))
        except ValueError:
            continue
        if f.weekday() == 0:
            return f
    return None


def parsear_hora(v):
    """Devuelve (salida, regreso) como 'HH:MM' o None."""
    if v is None or v == "":
        return None, None
    if isinstance(v, datetime):
        return f"{v.hour:02d}:{v.minute:02d}", None
    if isinstance(v, time):
        return f"{v.hour:02d}:{v.minute:02d}", None
    if isinstance(v, bool):
        return None, None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not float(v).is_integer():
            h, frac = int(v), round((v - int(v)) * 100)
            return (f"{h:02d}:{frac:02d}", None) if h < 24 and frac < 60 else (None, None)
        v = int(v)
        if v < 24:
            return f"{v:02d}:00", None
        if 100 <= v <= 2359 and v % 100 < 60:
            return f"{v // 100:02d}:{v % 100:02d}", None
        return None, None
    t = _norm(v).replace("hs", "").replace("horas", "").replace("h", " ").replace("am", "").replace("pm", "")
    nums = re.findall(r"(\d{1,2})(?:[:.](\d{2}))?", t)
    horas = []
    for h, mi in nums:
        if int(h) < 24 and (not mi or int(mi) < 60):
            horas.append(f"{int(h):02d}:{mi or '00'}")
    if not horas:
        return None, None
    if len(horas) >= 2 and horas[1] > horas[0]:
        return horas[0], horas[1]
    return horas[0], None


_NO_CHOFER = {"chofer", "particular", "paricular", "no", "si", "camioneta unica", "camioneta", "auto", "propio", "otro", "visita regular"}


def interpretar_chofer(v):
    """(nombre|None, cancelado, hora_en_la_celda, nota). La columna a veces trae
    horas, aclaraciones o «particular» en lugar del chofer."""
    t = _texto(v)
    if t is None:
        return None, False, None, None
    if isinstance(v, (int, float)) or re.fullmatch(r"\d{1,2}([.:]\d{1,2})?", t):
        return None, False, v, None
    n = _norm(t)
    if n == "cancelado":
        return None, True, None, None
    if n in _NO_CHOFER or "partic" in n:
        return None, False, None, ("particular" if "partic" in n else None)
    if len(t) < 3 or len(t) > 22 or len(t.split()) > 3 or re.search(r"\d", t):
        return None, False, None, t
    return t, False, None, None


def modalidad_de(observacion, chofer):
    o = _norm(observacion)
    if "partic" in o or "paricular" in o or "propio" in o:
        return "PROPIO"
    if "chofer" in o:
        return "CHOFER"
    return "CHOFER" if chofer else "PROPIO"


def _unidad_para(texto, unidades_por_nombre):
    if not texto:
        return None
    k = _norm(texto).rstrip(". ")
    if k in unidades_por_nombre:
        return unidades_por_nombre[k]
    cand = db.proponer_unidades(texto, 1)
    return cand[0]["id"] if cand else None


def leer_excel(ruta):
    """Filas crudas: dicts con fecha, equipo, asesor, destino, detalle, obs, hora, chofer, motivo, hoja."""
    import openpyxl
    # en Windows, un libro abierto en modo lectura deja el archivo tomado hasta cerrarlo (y no se puede borrar el temporal)
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    try:
        filas, hojas_sin_fecha = [], []
        for ws in wb.worksheets:
            lunes = lunes_de_hoja(ws.title)
            if not lunes:
                hojas_sin_fecha.append(ws.title)
                continue
            rows = list(ws.iter_rows(values_only=True))
            cab = next((i for i, r in enumerate(rows[:10]) if any(c and _norm(c).split(" ")[0] in DIAS for c in r)), None)
            if cab is None:
                hojas_sin_fecha.append(ws.title)
                continue
            bloques = []
            for j, c in enumerate(rows[cab]):
                w = _norm(c).split(" ")[0] if c else ""
                if w in DIAS:
                    bloques.append((j, DIAS.index(w)))
            for r in rows[cab + 2:]:
                for j, dia in bloques:
                    cel = list(r[j:j + 8]) + [None] * 8
                    asesor = _texto(cel[1])
                    if not asesor:
                        continue
                    filas.append(dict(fecha=(lunes + timedelta(days=dia)).isoformat(), equipo=_texto(cel[0]), asesor=asesor,
                                      destino=_texto(cel[2]), detalle=_texto(cel[3]), obs=_texto(cel[4]), hora=cel[5],
                                      chofer=_texto(cel[6]), motivo=_texto(cel[7]), hoja=ws.title))
    finally:
        wb.close()
    return filas, hojas_sin_fecha


def _unificar_choferes(nombres):
    """Agrupa variantes (GSTON → GASTON) con el nombre más frecuente."""
    cuenta = Counter(n for n in nombres if n)
    canon = []
    mapa = {}
    for n, _ in cuenta.most_common():
        k = _norm(n).replace(".", "").replace(" ", "")
        par = difflib.get_close_matches(k, [c[0] for c in canon], n=1, cutoff=0.75)
        if par:
            mapa[n] = next(c[1] for c in canon if c[0] == par[0])
        else:
            canon.append((k, n))
            mapa[n] = n
    return mapa


def armar_viajes(filas):
    por_unidad = {_norm(u["nombre"]).rstrip(". "): u["id"] for u in db.listar_unidades(solo_activas=False)}
    estados_chofer = {}
    nombres_choferes = [interpretar_chofer(f["chofer"])[0] for f in filas]
    mapa_ch = _unificar_choferes(nombres_choferes)
    grupos = defaultdict(list)
    sin_unidad = Counter()
    for f in filas:
        nombre_ch, cancelado, hora_alt, nota = interpretar_chofer(f["chofer"])
        chofer = mapa_ch.get(nombre_ch) if nombre_ch else None
        salida, regreso = parsear_hora(f["hora"])
        if not salida and hora_alt is not None:
            salida, regreso = parsear_hora(hora_alt)
        obs = f["obs"] or ""
        if nota == "particular":
            obs += " particular"
        elif nota:
            f["detalle"] = (f["detalle"] + "; " if f["detalle"] else "") + nota
        mod = modalidad_de(obs, chofer)
        destino = f["destino"]
        if destino and _norm(destino) == "otros" and f["detalle"]:
            destino = f["detalle"]  # «Otros» + detalle: el lugar real está en el detalle
        uid = _unidad_para(destino, por_unidad)
        otro = None if uid else destino
        if not uid and destino:
            sin_unidad[destino] += 1
        clave = (f["fecha"], uid or _norm(otro), salida, _norm(chofer), mod, cancelado)
        grupos[clave].append((f, uid, otro, salida, regreso, mod, chofer, cancelado))
    viajes = []
    for clave, items in grupos.items():
        f0, uid, otro, salida, regreso, mod, chofer, cancelado = items[0]
        motivos = [x[0]["motivo"] for x in items if x[0]["motivo"]]
        detalles = [x[0]["detalle"] for x in items if x[0]["detalle"]]
        if not (uid or otro):
            continue
        participantes, vistos = [], set()
        for x in items:
            k = _norm(x[0]["asesor"])
            if k not in vistos:
                vistos.add(k)
                participantes.append({"nombre": x[0]["asesor"], "equipo_nombre": x[0]["equipo"] if _norm(x[0]["equipo"]) not in VACIOS else None})
        viajes.append(dict(fecha=f0["fecha"], hora_salida=salida, hora_regreso=regreso, unidad_id=uid, destino_otro=otro,
                           motivo=(Counter(motivos).most_common(1)[0][0] if motivos else None), modalidad=mod, chofer=chofer,
                           cancelado=cancelado, observaciones="; ".join(sorted(set(detalles)))[:500] or None,
                           participantes=participantes, hoja=f0["hoja"]))
    viajes.sort(key=lambda v: (v["fecha"], v["hora_salida"] or ""))
    return viajes, sin_unidad


def importar(ruta, aplicar=False, usuario=None):
    filas, hojas_sin_fecha = leer_excel(ruta)
    viajes, sin_unidad = armar_viajes(filas)
    informe = {"filas": len(filas), "viajes": len(viajes), "hojas_sin_fecha": hojas_sin_fecha,
               "sin_unidad": sin_unidad.most_common(15), "choferes": sorted({v["chofer"] for v in viajes if v["chofer"]}),
               "cancelados": sum(1 for v in viajes if v["cancelado"]), "propios": sum(1 for v in viajes if v["modalidad"] == "PROPIO"),
               "creados": 0, "ya_existentes": 0, "desde": viajes[0]["fecha"] if viajes else None, "hasta": viajes[-1]["fecha"] if viajes else None}
    if not aplicar:
        return informe
    quien = (usuario or {}).get("nombre") or "Importación"
    quien_id = (usuario or {}).get("id")
    usuarios = {_norm(u["nombre"]): u["id"] for u in db.listar_usuarios()}
    ahora = db._ahora()
    with db._conexion() as con:
        ids_ch = {_norm(c["nombre"]): c["id"] for c in con.execute("SELECT id, nombre FROM choferes")}
        for nombre in informe["choferes"]:
            if _norm(nombre) not in ids_ch:
                cur = con.execute("INSERT INTO choferes (nombre, estado, observaciones, creado_en, actualizado_en) VALUES (?,?,?,?,?)",
                                  (nombre.title(), "DISPONIBLE", "Creado desde el Excel histórico", ahora, ahora))
                ids_ch[_norm(nombre)] = cur.lastrowid
                tr.auditar("chofer", cur.lastrowid, "Alta (importado)", nuevo=nombre, con=con)
        for v in viajes:
            ch_id = ids_ch.get(_norm(v["chofer"])) if v["chofer"] else None
            repetido = con.execute(
                """SELECT 1 FROM viajes WHERE origen='IMPORTADO' AND fecha=? AND COALESCE(unidad_id,0)=? AND COALESCE(destino_otro,'')=?
                   AND COALESCE(hora_salida,'')=? AND modalidad=? AND COALESCE(chofer_id,0)=?
                   AND EXISTS (SELECT 1 FROM viaje_participantes p WHERE p.viaje_id=viajes.id AND p.nombre=? COLLATE NOCASE)""",
                (v["fecha"], v["unidad_id"] or 0, v["destino_otro"] or "", v["hora_salida"] or "", v["modalidad"], ch_id or 0,
                 v["participantes"][0]["nombre"])).fetchone()
            if repetido:
                informe["ya_existentes"] += 1
                continue
            cur = con.execute(
                """INSERT INTO viajes (fecha, hora_salida, hora_regreso, unidad_id, destino_otro, motivo, modalidad, estado, inmediato,
                       chofer_id, observaciones, motivo_cancelacion, origen, creado_por_id, creado_por, creado_en, actualizado_en)
                   VALUES (?,?,?,?,?,?,?,?,0,?,?,?,'IMPORTADO',?,?,?,?)""",
                (v["fecha"], v["hora_salida"], v["hora_regreso"], v["unidad_id"], v["destino_otro"], v["motivo"], v["modalidad"],
                 "CANCELADO" if v["cancelado"] else "CONFIRMADO", ch_id, v["observaciones"],
                 "Figuraba cancelado en el Excel" if v["cancelado"] else None, quien_id, quien, ahora, ahora))
            for p in v["participantes"]:
                eq = tr.equipo_por_alias(p["equipo_nombre"], con) if p["equipo_nombre"] else None  # vínculo ya hecho en «Equipos del historial»
                con.execute("INSERT INTO viaje_participantes (viaje_id, usuario_id, nombre, equipo_nombre, equipo_original) VALUES (?,?,?,?,?)",
                            (cur.lastrowid, usuarios.get(_norm(p["nombre"])), p["nombre"], eq["nombre"] if eq else p["equipo_nombre"],
                             p["equipo_nombre"] if eq else None))
            informe["creados"] += 1
        tr.auditar("viaje", 0, "Importación de Excel", nuevo=f"{informe['creados']} viajes", con=con)
    return informe


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    db.inicializar_db()
    r = importar(sys.argv[1], aplicar="--aplicar" in sys.argv)
    for k, v in r.items():
        print(f"{k}: {v}")
