"""Unidades compatibles con la base de unidades del Menú de la Procuración (para migrarlas y unirlas después).

- Lee el Excel que exporta el Menú (ID, Código, Código RNCT, Nombre, Director, Dirección, Correo electrónico, Teléfono,
  Activo). Ese archivo trae celdas con el texto «Infinity» que la lectura común no admite: se lee directo del XML.
- Valores de relleno («N/A», «a», «sa», «a@ppn.govar», «Infinity», códigos 0) se toman como vacíos.
- Del nombre salen la Delegación PPN (prefijo «(DLIT)», «(CABA)»…), si está a cargo de la DGPDH (sufijo «(DGPDH)») y
  una fuerza propuesta (SPB, Policía de la Ciudad, PFA, Gendarmería, SPF…).
- Cruce con las unidades del sistema: 1) por el ID del Menú; 2) por tipo + número (+ si es del SPB), como sugerencia;
  3) por nombre o alias iguales. Si el número no coincide, no hay vínculo. Lo demás entra como alta nueva.
- Solo se completan datos vacíos del sistema; el nombre del Menú queda como alias. La carga se registra y se puede anular.
"""
import io
import json
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

import database as db

RELLENO = {"n/a", "na", "a", "sa", "-", "s/d", "infinity", "a@ppn.govar", "null", "none"}
COLUMNAS = {"id": ("id",), "codigo": ("codigo",), "rnct": ("codigo rnct", "rnct"), "nombre": ("nombre",), "director": ("director",),
            "direccion": ("direccion", "domicilio"), "correo": ("correo electronico", "correo", "email", "mail"), "telefono": ("telefono",),
            "activo": ("activo", "activa")}
DELEGACIONES = {"CABA": "Ciudad de Buenos Aires", "DLIT": "Delegación Litoral", "DMIS": "Delegación Misiones",
                "DCNP": "Delegación Centro-Norte (Provincia)", "DCOR": "Delegación Córdoba", "DCUY": "Delegación Cuyo",
                "DNEA": "Delegación NEA", "DNOA": "Delegación NOA", "DCEN": "Delegación Centro", "DVIE": "Delegación Viedma",
                "DSUR": "Delegación Sur", "DCOM": "Delegación Comodoro Rivadavia", "SVIE": "Subdelegación Viedma"}
ROMANOS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12,
           "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20}


def _norm(s):
    t = "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def limpio(v):
    t = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
    return None if not t or t.lower() in RELLENO else t


# ---------------------------------------------------------------- lectura del Excel del Menú
def _filas_xlsx(contenido):
    """Primera hoja de un .xlsx leída del XML (tolera «Infinity» y otros valores que openpyxl rechaza)."""
    z = zipfile.ZipFile(io.BytesIO(contenido))
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    compartidos = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("a:si", ns):
            compartidos.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["a"])))
    hojas = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    if not hojas:
        return []
    filas = []
    for r in ET.fromstring(z.read(hojas[0])).iter("{%s}row" % ns["a"]):
        fila = {}
        for c in r.findall("a:c", ns):
            col = re.match(r"[A-Z]+", c.get("r") or "A").group()
            v, t = c.find("a:v", ns), c.get("t")
            if t == "s" and v is not None:
                val = compartidos[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["a"]))
            else:
                val = v.text if v is not None else None
            fila[col] = val
        if fila:
            filas.append(fila)
    orden = lambda col: sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(col)))
    columnas = sorted(set().union(*filas), key=orden)
    return [[f.get(c) for c in columnas] for f in filas]


def leer(contenido, nombre_archivo=""):
    """Devuelve (unidades del Menú, avisos)."""
    if contenido[:4] == b"PK\x03\x04":
        filas = _filas_xlsx(contenido)
    else:
        import csv
        texto = contenido.decode("utf-8-sig", errors="replace")
        sep = ";" if texto.split("\n", 1)[0].count(";") >= texto.split("\n", 1)[0].count(",") else ","
        filas = list(csv.reader(io.StringIO(texto), delimiter=sep))
    if not filas:
        return [], ["El archivo no tiene filas."]
    enc = [_norm(h) for h in filas[0]]
    mapa = {}
    for clave, opciones in COLUMNAS.items():
        j = next((j for j, h in enumerate(enc) if h in opciones and j not in mapa.values()), None)
        if j is not None:
            mapa[clave] = j
    if "id" not in mapa or "nombre" not in mapa:
        return [], ["No encontré las columnas «ID» y «Nombre» de la base de unidades del Menú de la Procuración."]
    out, avisos = [], []
    for fila in filas[1:]:
        g = lambda k: fila[mapa[k]] if k in mapa and mapa[k] < len(fila) else None
        nombre = re.sub(r"\s+", " ", str(g("nombre") or "")).strip()
        mid = str(g("id") or "").strip()
        if not nombre or not mid.isdigit():
            continue
        codigo, rnct = limpio(g("codigo")), limpio(g("rnct"))
        if codigo in ("0",):
            codigo = None
        if rnct in ("0",):
            rnct = None
        correo = limpio(g("correo"))
        if correo and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", correo.split(";")[0].strip()):
            correo = None
        out.append(dict(menu_id=int(mid), codigo=codigo, rnct=rnct, nombre_menu=nombre, nombre=nombre_limpio(nombre),
                        director=limpio(g("director")), direccion=limpio(g("direccion")), correo=correo, telefono=limpio(g("telefono")),
                        activo=str(g("activo") or "1").strip() not in ("0", "false", "no"), delegacion=delegacion(nombre),
                        a_cargo_dgpdh="(DGPDH)" in nombre.upper(), fuerza_sigla=fuerza_propuesta(nombre)))
    ids = [u["menu_id"] for u in out]
    if len(ids) != len(set(ids)):
        avisos.append("Hay IDs repetidos en el archivo: se toma la primera aparición de cada uno.")
        vistos, unicos = set(), []
        for u in out:
            if u["menu_id"] not in vistos:
                vistos.add(u["menu_id"]); unicos.append(u)
        out = unicos
    return out, avisos


def delegacion(nombre):
    m = re.match(r"\s*\(([A-Z]{3,5})\)", nombre or "") or re.search(r"\((CABA)\)\s*$", nombre or "")
    return m.group(1) if m and m.group(1) != "DGPDH" else None


def nombre_limpio(nombre):
    t = re.sub(r"^\s*\([A-Z]{3,5}\)\s*", "", nombre or "")
    t = re.sub(r"\s*\((DGPDH|CABA)\)\s*", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -")
    return t


def fuerza_propuesta(nombre):
    """Sigla de la fuerza que se deduce del nombre (o None)."""
    n = " " + _norm(nombre) + " "
    if " spb " in n or "bonaerense" in n:
        return "SPB"
    if "pcaba" in n or "alcaidia comunal" in n or "comisaria vecinal" in n or "policia de la ciudad" in n:
        return "PC"
    if " pfa " in n or "policia federal" in n:
        return "PFA"
    if "escuadron" in n or "gendarmeria" in n or " gna " in n:
        return "GNA"
    if "prefectura" in n or " pna " in n:
        return "PNA"
    if " psa " in n or "seguridad aeroportuaria" in n:
        return "PSA"
    if "provincial" in n or "comisaria" in n or "unidad regional" in n or "division" in n:
        return None  # policía o servicio provincial: se elige a mano
    if " c p f " in n or " cpf " in n or "complejo penitenciario federal" in n or "complejo federal" in n or re.search(r" unidad (n |no |nro )?\d", n) \
            or "instituto penitenciario federal" in n or re.search(r" u \d", n):
        return "SPF"
    return None


def clave(nombre):
    """(tipo, número, es_spb) para cruzar unidades con nombres distintos. Lo que va entre paréntesis («Ex U2») no cuenta."""
    t = re.sub(r"\([^)]*\)", " ", str(nombre or ""))
    n = " " + _norm(t) + " "
    es_spb = fuerza_propuesta(nombre) == "SPB"  # «(SPB)» entre paréntesis sí cuenta para la fuerza
    if " c p f " in n or " cpf " in n or "complejo" in n:
        tipo = "complejo"
    elif "alcaidia" in n:
        tipo = "alcaidia"
    elif "comisaria" in n:
        tipo = "comisaria"
    elif "escuadron" in n:
        tipo = "escuadron"
    elif "unidad" in n or re.search(r" u \d", n):
        tipo = "unidad"
    else:
        tipo = "otro"
    num = None
    m = re.search(r"(?:unidad|complejo|c p f|cpf|alcaidia|comisaria|escuadron|federal|u)\s+(?:vecinal |comunal |penal )?(?:n |no |nro |numero )?(\d+|[ivx]{1,5})(?: ?([a-d])\b)?\b", n)
    if m:
        v = m.group(1)
        num = int(v) if v.isdigit() else ROMANOS.get(v)
        if num is not None and m.group(2):
            num = f"{num}{m.group(2)}"
    return tipo, num, es_spb


# ---------------------------------------------------------------- vista previa y aplicación
def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS unidades_cargas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, archivo TEXT, filas INTEGER, vinculadas INTEGER, altas INTEGER, omitidas INTEGER,
        detenidos_reasignados INTEGER, usuario TEXT, creado_en TEXT NOT NULL, anulado_en TEXT, anulado_por TEXT, motivo_anulacion TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS unidades_cambios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, carga_id INTEGER, unidad_id INTEGER, campo TEXT, antes TEXT, despues TEXT, fecha TEXT NOT NULL)""")
    try:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_unidades_menu ON unidades(menu_id) WHERE menu_id IS NOT NULL")
    except Exception:
        pass
    # la fuerza a cargo que estaba escrita a mano se vincula sola si coincide con una fuerza registrada (el texto se conserva)
    for f in con.execute("SELECT id, fuerza FROM unidades WHERE fuerza_id IS NULL AND fuerza IS NOT NULL AND fuerza != ''").fetchall():
        fid = db.fuerza_por_texto(f["fuerza"], con)
        if fid:
            con.execute("UPDATE unidades SET fuerza_id = ? WHERE id = ?", (fid, f["id"]))


def _fuerzas_por_sigla():
    with db._conexion() as con:
        return {(f["sigla"] or "").upper(): f["id"] for f in con.execute("SELECT id, sigla FROM fuerzas") if f["sigla"]}


_VACIAS = {"de", "del", "la", "las", "los", "el", "y", "n", "no", "nro", "ex", "unidad", "complejo", "penitenciario", "federal", "c", "p", "f",
           "cpf", "dgpdh", "caba", "pfa", "spb", "pcaba", "alcaidia", "comisaria", "centro", "u", "delegacion", "policia", "gna", "pna", "psa",
           "prefectura", "gendarmeria", "naval", "argentina", "nacional", "provincia", "espera", "cupo", "anexo"}


def _palabras(nombre):
    return {w for w in _norm(re.sub(r"\([A-Z]{3,5}\)", " ", nombre or "")).split() if w not in _VACIAS and len(w) > 1}


def _parecida(nombre, candidatas):
    """La unidad cuyo nombre comparte casi todas las palabras significativas (o None si hay dudas)."""
    import difflib
    a = _palabras(nombre)
    if not a:
        return None
    puntajes = []
    fz = fuerza_propuesta(nombre)
    for u in candidatas:
        fu = fuerza_propuesta(u["nombre"])
        if fz and fu and fz != fu:
            continue  # una es de Prefectura y la otra de la Federal, por ejemplo: no son la misma
        b = _palabras(u["nombre"]) | _palabras(u.get("alias") or "")
        if not b:
            continue
        comunes = len(a & b) / max(1, min(len(a), len(b)))
        ratio = difflib.SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
        puntajes.append((1.0 if comunes == 1 and len(a & b) >= 2 else ratio, u))
    puntajes.sort(key=lambda x: -x[0])
    if puntajes and puntajes[0][0] >= 0.85 and (len(puntajes) == 1 or puntajes[1][0] < puntajes[0][0]):
        return puntajes[0][1]
    return None


def previa(menu):
    """Para cada unidad del Menú: «vinculada» (por ID o nombre), «sugerida» (por tipo y número, a confirmar) o «nueva»."""
    sistema = db.listar_unidades(solo_activas=False)
    por_menu = {u["menu_id"]: u for u in sistema if u.get("menu_id")}
    por_nombre = {}
    for u in sistema:
        for n in [u["nombre"], u.get("nombre_menu")] + [x for x in re.split(r"[;|]", u.get("alias") or "") if x.strip()]:
            if n:
                por_nombre.setdefault(_norm(nombre_limpio(n)), u)
    por_clave = {}
    for u in sistema:
        k = clave(u["nombre"])
        if k[1] is not None:
            por_clave.setdefault(k, []).append(u)
    usadas = set()
    filas = []
    sig = {v: k_ for k_, v in _fuerzas_por_sigla().items()}  # fuerza_id -> sigla
    for m in menu:
        destino, estado = None, "nueva"
        if m["menu_id"] in por_menu:
            destino, estado = por_menu[m["menu_id"]], "vinculada"
        elif _norm(m["nombre"]) in por_nombre:
            destino, estado = por_nombre[_norm(m["nombre"])], "vinculada"
        else:
            k = clave(m["nombre_menu"])
            fm = m.get("fuerza_sigla")
            # no se sugiere una unidad que está a cargo de otra fuerza (Unidad 9 del SPB no es la Unidad 9 del SPF)
            cand = [u for u in por_clave.get(k, []) if not u.get("menu_id")
                    and not (fm and u.get("fuerza_id") and sig.get(u["fuerza_id"]) and sig[u["fuerza_id"]] != fm)] if k[1] is not None else []
            if len(cand) == 1:
                destino, estado = cand[0], "sugerida"
            elif k[1] is None:  # sin número: nombres parecidos del mismo tipo, como sugerencia a confirmar
                mejor = _parecida(m["nombre_menu"], [u for u in sistema if not u.get("menu_id") and u["id"] not in usadas and clave(u["nombre"])[:1] == k[:1]
                                                     and clave(u["nombre"])[1] is None])
                if mejor:
                    destino, estado = mejor, "sugerida"
        if destino and destino["id"] in usadas and estado != "vinculada":
            destino, estado = None, "nueva"
        if destino:
            usadas.add(destino["id"])
        filas.append(dict(m, estado=estado, destino_id=destino["id"] if destino else None, destino_nombre=destino["nombre"] if destino else None))
    sin_vinculo = [u for u in sistema if u["id"] not in usadas and not u.get("menu_id") and u.get("activa")]
    return {"filas": filas, "sin_vinculo": sin_vinculo,
            "cuenta": {e: sum(1 for f in filas if f["estado"] == e) for e in ("vinculada", "sugerida", "nueva")}}


def aplicar(menu, decisiones, usuario, archivo=None):
    """decisiones = {menu_id: unidad_id | "nueva" | "omitir"} (lo que no viene usa lo propuesto en la vista previa).
    Devuelve el resumen y el id de la carga."""
    p = previa(menu)
    siglas = _fuerzas_por_sigla()
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario
    validas = {u["id"]: u for u in db.listar_unidades(solo_activas=False)}
    vinculadas = altas = omitidas = 0
    ahora = db._ahora()
    with db._conexion() as con:
        cid = con.execute("INSERT INTO unidades_cargas (archivo, filas, usuario, creado_en) VALUES (?,?,?,?)",
                          (archivo, len(menu), quien, ahora)).lastrowid

        def cambiar(uid, campo, antes, despues):
            con.execute(f"UPDATE unidades SET {campo} = ?, actualizada_en = ? WHERE id = ?", (despues, ahora, uid))
            con.execute("INSERT INTO unidades_cambios (carga_id, unidad_id, campo, antes, despues, fecha) VALUES (?,?,?,?,?,?)",
                        (cid, uid, campo, None if antes is None else str(antes), None if despues is None else str(despues), ahora))

        for f in p["filas"]:
            d = str(decisiones.get(str(f["menu_id"]), "") or "")
            if d == "omitir" or (not d and f["estado"] == "sugerida" and decisiones.get("_sugeridas") == "no"):
                omitidas += 1
                continue
            destino = int(d) if d.isdigit() and int(d) in validas else (None if d == "nueva" else f["destino_id"])
            fid = siglas.get(f["fuerza_sigla"] or "")
            if destino:
                u = dict(con.execute("SELECT * FROM unidades WHERE id = ?", (destino,)).fetchone())
                ocupada = con.execute("SELECT id FROM unidades WHERE menu_id = ? AND id != ?", (f["menu_id"], destino)).fetchone()
                if ocupada:
                    omitidas += 1
                    continue
                for campo, valor in (("menu_id", f["menu_id"]), ("menu_codigo", f["codigo"]), ("menu_rnct", f["rnct"]), ("nombre_menu", f["nombre_menu"]),
                                     ("a_cargo_dgpdh", 1 if f["a_cargo_dgpdh"] else 0)):
                    if str(u.get(campo) if u.get(campo) is not None else "") != str(valor if valor is not None else ""):
                        cambiar(destino, campo, u.get(campo), valor)
                for campo, valor in (("director", f["director"]), ("direccion", f["direccion"]), ("correo", f["correo"]),
                                     ("telefono_guardia", f["telefono"]), ("delegacion_ppn", f["delegacion"]), ("fuerza_id", fid)):
                    if valor and not u.get(campo):  # solo se completa lo vacío
                        cambiar(destino, campo, u.get(campo), valor)
                alias = [x.strip() for x in re.split(r"[;|]", u.get("alias") or "") if x.strip()]
                if _norm(f["nombre_menu"]) not in {_norm(x) for x in alias + [u["nombre"]]}:
                    cambiar(destino, "alias", u.get("alias"), "; ".join(alias + [f["nombre_menu"]]))
                vinculadas += 1
            else:
                nombre = f["nombre"]
                if con.execute("SELECT 1 FROM unidades WHERE nombre = ? COLLATE NOCASE", (nombre,)).fetchone():
                    nombre = f["nombre_menu"]
                if con.execute("SELECT 1 FROM unidades WHERE nombre = ? COLLATE NOCASE", (nombre,)).fetchone():
                    nombre = f"{f['nombre_menu']} (Menú #{f['menu_id']})"
                tipo = {"complejo": "Complejo penitenciario", "unidad": "Unidad penitenciaria", "alcaidia": "Alcaidía", "comisaria": "Comisaría",
                        "escuadron": "Escuadrón"}.get(clave(f["nombre_menu"])[0])
                nid = con.execute("""INSERT INTO unidades (nombre, tipo, zona, fuerza, correo, direccion, alias, telefono_guardia, activa, creada_en, actualizada_en,
                                     fuerza_id, menu_id, menu_codigo, menu_rnct, director, delegacion_ppn, a_cargo_dgpdh, nombre_menu)
                                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  (nombre, tipo, "AMBA" if f["delegacion"] == "CABA" else None, None, f["correo"], f["direccion"],
                                   f["nombre_menu"] if f["nombre_menu"] != nombre else None, f["telefono"], 1 if f["activo"] else 0, ahora, ahora,
                                   fid, f["menu_id"], f["codigo"], f["rnct"], f["director"], f["delegacion"], 1 if f["a_cargo_dgpdh"] else 0,
                                   f["nombre_menu"])).lastrowid
                if fid:
                    fz = con.execute("SELECT nombre FROM fuerzas WHERE id = ?", (fid,)).fetchone()
                    con.execute("UPDATE unidades SET fuerza = ? WHERE id = ?", (fz["nombre"] if fz else None, nid))
                con.execute("INSERT INTO unidades_cambios (carga_id, unidad_id, campo, antes, despues, fecha) VALUES (?,?,?,?,?,?)",
                            (cid, nid, "_alta", None, nombre, ahora))
                altas += 1
        con.execute("UPDATE unidades_cargas SET vinculadas = ?, altas = ?, omitidas = ? WHERE id = ?", (vinculadas, altas, omitidas, cid))
    reasignados = reasignar_detenidos(cid, usuario)
    with db._conexion() as con:
        con.execute("UPDATE unidades_cargas SET detenidos_reasignados = ? WHERE id = ?", (reasignados, cid))
    return {"carga_id": cid, "vinculadas": vinculadas, "altas": altas, "omitidas": omitidas, "detenidos": reasignados}


def reasignar_detenidos(carga_id, usuario):
    """Personas detenidas cargadas con el lugar escrito tal cual («centro_otro»): si ahora coincide con el nombre o alias
    de una unidad, pasan a esa unidad. Queda en el historial de cada persona."""
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario
    nombres = {}
    for u in db.listar_unidades(solo_activas=False):
        for n in [u["nombre"], u.get("nombre_menu")] + [x for x in re.split(r"[;|]", u.get("alias") or "") if x.strip()]:
            if n:
                nombres.setdefault(_norm(n), u["id"])
                nombres.setdefault(_norm(nombre_limpio(n)), u["id"])
    n = 0
    try:
        with db._conexion() as con:
            filas = con.execute("SELECT id, centro_otro FROM detenidos WHERE unidad_id IS NULL AND centro_otro IS NOT NULL AND centro_otro != ''").fetchall()
            for f in filas:
                uid = nombres.get(_norm(f["centro_otro"])) or nombres.get(_norm(nombre_limpio(f["centro_otro"])))
                if not uid:
                    continue
                con.execute("UPDATE detenidos SET unidad_id = ?, centro_otro = NULL WHERE id = ?", (uid, f["id"]))
                con.execute("""INSERT INTO detenidos_cambios (detenido_id, campo, antes, despues, usuario, origen, fecha) VALUES (?,?,?,?,?,?,?)""",
                            (f["id"], "unidad_id", f["centro_otro"], str(uid), quien, f"UNIDADES_MENU #{carga_id}", db._ahora()))
                n += 1
    except Exception:
        return n
    return n


def cargas(limite=20):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM unidades_cargas ORDER BY id DESC LIMIT ?", (limite,))]


def anular(carga_id, motivo, usuario):
    """Deshace una carga del Menú: los datos que cambió vuelven a su valor anterior (si nadie los tocó después) y las
    unidades que dio de alta quedan inactivas. No se borra nada."""
    motivo = (motivo or "").strip()[:300]
    if not motivo:
        raise ValueError("Contá por qué se anula la carga.")
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario
    with db._conexion() as con:
        c = con.execute("SELECT * FROM unidades_cargas WHERE id = ?", (carga_id,)).fetchone()
        if not c:
            raise ValueError("La carga no existe.")
        if c["anulado_en"]:
            raise ValueError("Esa carga ya estaba anulada.")
        bajas = revertidos = 0
        for x in con.execute("SELECT * FROM unidades_cambios WHERE carga_id = ? ORDER BY id DESC", (carga_id,)).fetchall():
            if x["campo"] == "_alta":
                con.execute("UPDATE unidades SET activa = 0, menu_id = NULL, actualizada_en = ? WHERE id = ?", (db._ahora(), x["unidad_id"]))
                bajas += 1
                continue
            actual = con.execute(f"SELECT {x['campo']} AS v FROM unidades WHERE id = ?", (x["unidad_id"],)).fetchone()
            if actual is not None and str(actual["v"] if actual["v"] is not None else "") == str(x["despues"] or ""):
                con.execute(f"UPDATE unidades SET {x['campo']} = ?, actualizada_en = ? WHERE id = ?", (x["antes"], db._ahora(), x["unidad_id"]))
                revertidos += 1
        con.execute("UPDATE unidades_cargas SET anulado_en = ?, anulado_por = ?, motivo_anulacion = ? WHERE id = ?",
                    (db._ahora(), quien, motivo, carga_id))
    return {"bajas": bajas, "revertidos": revertidos}


def exportar(unidades=None):
    """Excel con el mismo formato del Menú (más el ID del sistema, la fuerza y la delegación) para el intercambio."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    unidades = unidades if unidades is not None else db.listar_unidades(solo_activas=False)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Unidades"
    ws.append(["ID", "Código", "Código RNCT", "Nombre", "Director", "Dirección", "Correo electrónico", "Teléfono", "Activo",
                "ID sistema DGPDH", "Fuerza", "Delegación PPN", "A cargo DGPDH"])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="7C1413")
    for u in unidades:
        ws.append([u.get("menu_id"), u.get("menu_codigo"), u.get("menu_rnct"), u.get("nombre_menu") or u["nombre"], u.get("director"),
                   u.get("direccion"), u.get("correo"), u.get("telefono_guardia"), 1 if u.get("activa") else 0, u["id"], u.get("fuerza"),
                   u.get("delegacion_ppn"), "Sí" if u.get("a_cargo_dgpdh") else ""])
    for col, w in zip("ABCDEFGHIJKLM", (8, 10, 12, 60, 24, 40, 32, 22, 7, 10, 30, 12, 10)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
