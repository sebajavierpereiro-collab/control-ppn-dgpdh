"""Importar planillas de metas de períodos anteriores (el Excel «Tabla de metas por trimestre» de cada equipo).

Como la importación de listados de personas detenidas:
  1. Se sube la planilla y se elige el equipo. Cada hoja «1er Trimestre 2025», «2do Trimestre 2026 CPF II»… propone su
     período (editable o destildable); las hojas de resumen («Hoja 1»…) se usan solo para controlar los totales.
  2. Vista previa: siglas de la planilla → integrantes (o «persona anterior», que suma al equipo y se puede vincular
     después a un usuario), indicadores reconocidos por su nombre, filas sin reconocer (se asignan o se descartan),
     totales que no cierran y períodos que ya tienen datos (reemplazar o completar solo lo que falta).
  3. Confirmar: queda una carga registrada que se ve en «Cargas de planillas» y se puede anular entera. Nada se borra.

Reglas:
  * Si el TOTAL del mes es mayor que la suma de las personas, la diferencia queda «del equipo, sin asignar».
  * Visitas, audiencias e ingreso a pabellones de un mes importado toman el valor de la planilla en lugar de lo calculado
    desde los viajes (así no se cuenta dos veces); se marcan «según planilla».
"""
import json
import re
import unicodedata
from collections import defaultdict

import database as db
import metas

ORDINALES = {"1": 1, "1er": 1, "1ro": 1, "primer": 1, "primero": 1, "2": 2, "2do": 2, "segundo": 2,
             "3": 3, "3er": 3, "3ro": 3, "tercer": 3, "tercero": 3, "4": 4, "4to": 4, "cuarto": 4}
MESES_NORM = {m.lower(): i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                                   "septiembre", "octubre", "noviembre", "diciembre"], 1)}
MESES_NORM["setiembre"] = 9
# Nombres de las filas de la planilla → indicador del sistema (por inclusión, sin tildes ni mayúsculas; el orden importa)
SINONIMOS = [
    ("visitas_equipo", ("visitas equipo", "visitas del equipo", "visitas a unidades")),
    ("entrevistas", ("entrevistas ppl", "entrevistas a ppl", "audiencias", "entrevistas en unidad")),
    ("videollamadas", ("videollamada", "video llamada")),
    ("ingreso_pabellones", ("ingreso a pabellon", "ingresos a pabellon", "ingreso a sectores", "ingreso pabellon")),
    ("monitoreos", ("monitoreo",)),
    ("informes_generales", ("informes generales", "informe general", "tematico")),
    ("informes_particulares", ("informes particulares", "informe particular")),
    ("presentaciones_judiciales", ("presentaciones judiciales", "presentacion judicial")),
    ("reuniones_grupo", ("interior grupo", "interior del grupo", "al interior")),
    ("reuniones_externos", ("organismos externos", "organismo externo")),
    ("reuniones_interareas", ("interareas", "inter areas", "interarea")),
    ("requerimientos_presenciales", ("presenciales", "presencial")),
    ("requerimientos_remotos", ("remotos", "remoto")),
    ("com_familiares", ("familiares", "familia")),
    ("com_judicial", ("poder judicial", "defensas", "defensoria")),
    ("com_otros", ("otros organismos", "otro organismo")),
]
# Filas que son títulos de grupo o totales: se ignoran sin avisar
TITULOS = ("(titulo)", "titulo", "visitas unidades", "reuniones", "requerimientos al spf", "requerimientos al", "total", "comunicaciones")


def _norm(s):
    t = "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9()/ ]", " ", t.lower()).split())


def indicador_de(nombre):
    n = _norm(nombre)
    if not n:
        return None
    for clave, claves in SINONIMOS:
        if any(k in n for k in claves):
            return clave
    return None


def es_titulo(nombre):
    n = _norm(nombre)
    return any(n == t or n.startswith(t) for t in TITULOS) or "titulo" in n


def periodo_de_hoja(titulo, primera_celda=None):
    """«1er Trimestre 2025» → (2025, 1). Si el nombre no trae el año lo busca en la primera celda («Trimestre 2027»)."""
    n = _norm(titulo)
    m = re.search(r"\b(1er|1ro|2do|3er|3ro|4to|primer|primero|segundo|tercer|tercero|cuarto|[1-4])\s*(?:o\s*)?trimestre", n)
    if not m:
        return None, None
    t = ORDINALES.get(m.group(1))
    a = re.search(r"(20\d{2})", n) or re.search(r"(20\d{2})", _norm(primera_celda))
    return (int(a.group(1)) if a else None), t


def _entero(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    t = re.sub(r"[^\d-]", "", str(v))
    return int(t) if t and t != "-" else None


def leer_planilla(contenido):
    """Lee el Excel. Devuelve {"hojas": [...], "avisos": [...]}; cada hoja trimestral trae su período propuesto, los meses,
    las siglas de las personas y, por fila, los valores por persona y mes, el TOTAL y la diferencia sin asignar."""
    import io
    import openpyxl
    libro = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True)
    try:
        hojas, avisos, resumenes = [], [], {}
        for pos, ws in enumerate(libro.worksheets):
            filas = [list(r) for r in ws.iter_rows(values_only=True)]
            anio, tri = periodo_de_hoja(ws.title, filas[0][0] if filas and filas[0] else None)
            # fila de meses
            ifila = next((i for i, r in enumerate(filas[:8]) if sum(1 for c in r if _norm(c) in MESES_NORM) >= 1), None)
            if ifila is None or ifila + 1 >= len(filas):
                continue
            cab, sig = filas[ifila], filas[ifila + 1]
            inicios = [(j, MESES_NORM[_norm(c)]) for j, c in enumerate(cab) if _norm(c) in MESES_NORM]
            if not any(_norm(c) == "total" for c in sig):  # hoja de resumen: un mes por columna
                resumenes[ws.title] = {"meses": inicios, "filas": filas[ifila + 1:], "pos": pos}
                continue
            col_tri = next((j for j, c in enumerate(cab) if "total trimestre" in _norm(c)), None)
            bloques = []
            for k, (j, mes) in enumerate(inicios):
                fin = inicios[k + 1][0] if k + 1 < len(inicios) else (col_tri if col_tri is not None else len(sig))
                cols = list(range(j, fin))
                jtot = next((c for c in cols if _norm(sig[c] if c < len(sig) else "") == "total"), None)
                personas = [(c, str(sig[c]).strip()) for c in cols if c < len(sig) and c != jtot and sig[c] not in (None, "") and _norm(sig[c]) != "total"]
                sin_nombre = [c for c in cols if c < len(sig) and c != jtot and sig[c] in (None, "") and (jtot is None or c < jtot)]
                bloques.append({"mes": mes, "personas": personas, "sin_nombre": sin_nombre, "total": jtot})
            if tri is None and inicios:
                tri = metas.trimestre_de_mes(inicios[0][1])
            siglas = list(dict.fromkeys(s for b in bloques for _, s in b["personas"]))
            datos, no_reconocidas, sueltas = [], [], []
            for nfila, r in enumerate(filas[ifila + 2:], ifila + 3):
                nombre = r[0] if r else None
                valores = {}
                for b in bloques:
                    por_persona = {s: _entero(r[c]) for c, s in b["personas"] if c < len(r) and _entero(r[c])}
                    extra = sum(_entero(r[c]) or 0 for c in b["sin_nombre"] if c < len(r))
                    total = _entero(r[b["total"]]) if b["total"] is not None and b["total"] < len(r) else None
                    suma = sum(por_persona.values()) + extra
                    if total is None:
                        total = suma
                    sin_asignar = max(0, total - sum(por_persona.values()))
                    if por_persona or sin_asignar:
                        valores[b["mes"]] = {"personas": por_persona, "sin_asignar": sin_asignar, "total": max(total, suma),
                                             "no_cierra": total < suma}
                if not valores:
                    continue
                clave = indicador_de(nombre)
                fila = {"fila": nfila, "nombre": str(nombre or "").strip(), "indicador": clave, "valores": valores}
                if clave:
                    datos.append(fila)
                elif not str(nombre or "").strip():
                    sueltas.append(fila)
                elif not es_titulo(nombre):
                    no_reconocidas.append(fila)
            hojas.append({"hoja": ws.title, "pos": pos, "anio": anio, "trimestre": tri, "meses": [b["mes"] for b in bloques], "siglas": siglas,
                          "filas": datos, "no_reconocidas": no_reconocidas, "sueltas": sueltas,
                          "no_cierran": [(f["nombre"], m) for f in datos for m, v in f["valores"].items() if v["no_cierra"]]})
        if not hojas:
            avisos.append("No encontré hojas con meses y columnas por persona («ENERO … TOTAL»). Revisá que sea la planilla de metas del equipo.")
        control = []
        _controlar_con_resumenes(hojas, resumenes, control)
        if control:
            avisos.append(f"Control con las hojas de resumen: {len(control)} total(es) no coinciden (se toma la hoja trimestral). "
                          + " ".join(control[:6]) + (f" Y {len(control) - 6} más." if len(control) > 6 else ""))
        return {"hojas": hojas, "avisos": avisos}
    finally:
        libro.close()


def normalizar(lectura):
    """Después de guardarla como JSON entre la vista previa y la confirmación, los meses vuelven a ser números."""
    for h in lectura.get("hojas", []):
        h["meses"] = [int(m) for m in h["meses"]]
        for f in h["filas"] + h["no_reconocidas"] + h["sueltas"]:
            f["valores"] = {int(k): v for k, v in f["valores"].items()}
    return lectura


def _controlar_con_resumenes(hojas, resumenes, avisos):
    """Las hojas «Hoja N» repiten los totales por mes: si alguno no coincide con la hoja trimestral, se avisa."""
    for nombre, r in resumenes.items():
        meses = [m for _, m in r["meses"]]
        candidatas = [x for x in hojas if meses and set(meses) <= set(x["meses"])]
        if not candidatas:
            continue
        h = min(candidatas, key=lambda x: abs(x["pos"] - r["pos"]))  # la hoja trimestral más cercana en el libro
        por_ind = {f["indicador"]: f for f in h["filas"]}
        for fila in r["filas"]:
            clave = indicador_de(fila[0] if fila else None)
            if not clave or clave not in por_ind:
                continue
            for j, mes in r["meses"]:
                v = _entero(fila[j]) if j < len(fila) else None
                hecho = por_ind[clave]["valores"].get(mes, {}).get("total", 0)
                if v is not None and v != hecho:
                    avisos.append(f"«{h['hoja']}»: {metas.POR_CLAVE[clave]['nombre']} de {metas.MESES[mes - 1]} da {hecho} y en «{nombre}» figura {v}.")


# ---------------------------------------------------------------- base
def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS metas_cargas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, archivo TEXT, equipo_id INTEGER NOT NULL REFERENCES equipos(id),
        periodos TEXT, modo TEXT, valores INTEGER, usuario TEXT, creado_en TEXT NOT NULL,
        anulado_en TEXT, anulado_por TEXT, motivo_anulacion TEXT)""")
    # usuario_id NULL = persona anterior o sin usuario (se identifica por la sigla); sin_asignar = 1: del equipo, sin persona
    con.execute("""CREATE TABLE IF NOT EXISTS metas_importado (
        id INTEGER PRIMARY KEY AUTOINCREMENT, carga_id INTEGER NOT NULL REFERENCES metas_cargas(id),
        equipo_id INTEGER NOT NULL, anio INTEGER NOT NULL, periodo TEXT NOT NULL, indicador TEXT NOT NULL,
        usuario_id INTEGER REFERENCES usuarios(id), sigla TEXT, sin_asignar INTEGER NOT NULL DEFAULT 0,
        cantidad INTEGER NOT NULL DEFAULT 0, anulada INTEGER NOT NULL DEFAULT 0, reemplazado_por INTEGER)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_metas_imp ON metas_importado(equipo_id, anio)")
    try:
        con.execute("ALTER TABLE metas_realizado ADD COLUMN reemplazado_por INTEGER")  # la reemplazó una carga de planilla
    except Exception:
        pass


def importado(anio=None, equipo_id=None):
    """Filas vigentes (no anuladas ni reemplazadas) de las planillas importadas."""
    sql, args = "SELECT * FROM metas_importado WHERE anulada = 0 AND reemplazado_por IS NULL", []
    if anio is not None:
        sql += " AND anio = ?"; args.append(anio)
    if equipo_id is not None:
        sql += " AND equipo_id = ?"; args.append(equipo_id)
    with db._conexion() as con:
        return [dict(f) for f in con.execute(sql, args)]


def cubiertos(anio):
    """{(equipo_id, mes, indicador)} que vienen de una planilla importada vigente."""
    out = set()
    for f in importado(anio):
        for m in metas.meses_de(f["periodo"]):
            out.add((f["equipo_id"], m, f["indicador"]))
    return out


def _sugerir(sigla, gente):
    s = re.sub(r"[^A-Z]", "", str(sigla).upper())
    for u in gente:
        if metas.iniciales(u["nombre"]) == s:
            return u["id"]
    for u in gente:  # «GF» = Gisela Fernández aunque tenga dos nombres
        p = [x for x in u["nombre"].replace(",", " ").split() if x[:1].isalpha()]
        if len(p) >= 2 and len(s) == 2 and (p[0][0] + p[-1][0]).upper() == s:
            return u["id"]
    return None


def previa(lectura, equipo_id, elegidas, asignaciones=None):
    """elegidas = {nombre_hoja: (anio, trimestre)} (las destildadas no van). Devuelve lo que se importaría."""
    gente = metas.integrantes(equipo_id)
    siglas = sorted({s for h in lectura["hojas"] if h["hoja"] in elegidas for s in h["siglas"]})
    sugeridas = {s: _sugerir(s, gente) for s in siglas}
    existentes = _existentes(equipo_id, {a for a, _ in elegidas.values()})
    asignaciones = asignaciones or {}
    resumen, choques = [], []
    for h in lectura["hojas"]:
        if h["hoja"] not in elegidas:
            continue
        anio, t = elegidas[h["hoja"]]
        mapa_meses = dict(zip(h["meses"], metas.meses_de(f"T{t}")))  # por posición: si se cambia el trimestre, los meses lo siguen
        filas = list(h["filas"]) + [dict(f, indicador=asignaciones.get(f"{h['hoja']}|{f['fila']}")) for f in h["no_reconocidas"] + h["sueltas"]
                                    if asignaciones.get(f"{h['hoja']}|{f['fila']}") in metas.POR_CLAVE]
        valores = sum(1 for f in filas for v in f["valores"].values() for _ in list(v["personas"]) + ([1] if v["sin_asignar"] else []))
        for f in filas:
            for mes_h in f["valores"]:
                mes = mapa_meses.get(mes_h)
                if mes and (anio, mes, f["indicador"]) in existentes:
                    choques.append((anio, mes, f["indicador"], existentes[(anio, mes, f["indicador"])]))
        resumen.append({"hoja": h["hoja"], "anio": anio, "trimestre": t, "meses": [mapa_meses.get(m) for m in h["meses"]],
                        "filas": len(filas), "valores": valores})
    choques = sorted(set(choques))
    return {"siglas": siglas, "sugeridas": sugeridas, "gente": gente, "resumen": resumen, "choques": choques,
            "periodos_con_datos": sorted({(a, m) for a, m, _, _ in choques})}


def _existentes(equipo_id, anios):
    """{(anio, mes, indicador): origen} de lo que ya hay: carga manual, otra planilla o (visitas/audiencias) los viajes."""
    out = {}
    eq = next((e for e in db.listar_equipos() if e["id"] == equipo_id), {"nombre": ""})
    for anio in anios:
        if not anio:
            continue
        for f in metas.cargado(anio, equipo_id):
            for m in metas.meses_de(f["periodo"]):
                if f["cantidad"]:
                    out[(anio, m, f["indicador"])] = "carga manual"
        for f in importado(anio, equipo_id):
            for m in metas.meses_de(f["periodo"]):
                out[(anio, m, f["indicador"])] = f"planilla (carga #{f['carga_id']})"
        via = metas.desde_viajes(anio)
        for (eqn, mes, clave), n in via["equipo"].items():
            if n and eqn == eq["nombre"].strip().lower():
                out.setdefault((anio, mes, clave), "viajes")
    return out


def aplicar(lectura, equipo_id, elegidas, vinculos, modo, usuario, archivo=None, asignaciones=None):
    """vinculos = {sigla: usuario_id o None}. modo: «reemplazar» (lo de la planilla manda en los meses que trae) o
    «completar» (solo entra lo que falta). Devuelve el id de la carga y cuántos valores entraron."""
    if modo not in ("reemplazar", "completar"):
        raise ValueError("Elegí si la planilla reemplaza lo que ya hay o solo completa lo que falta.")
    if not elegidas:
        raise ValueError("Elegí al menos una hoja para importar.")
    asignaciones = asignaciones or {}
    existentes = _existentes(equipo_id, {a for a, _ in elegidas.values()})
    quien, ahora = usuario["nombre"], db._ahora()
    validos = {u["id"] for u in db.listar_usuarios()}
    n, periodos = 0, set()
    with db._conexion() as con:
        cid = con.execute("INSERT INTO metas_cargas (archivo, equipo_id, modo, usuario, creado_en) VALUES (?,?,?,?,?)",
                          (archivo, equipo_id, modo, quien, ahora)).lastrowid
        for h in lectura["hojas"]:
            if h["hoja"] not in elegidas:
                continue
            anio, t = elegidas[h["hoja"]]
            if not anio or t not in (1, 2, 3, 4):
                raise ValueError(f"Falta el año o el trimestre de la hoja «{h['hoja']}».")
            mapa_meses = dict(zip(h["meses"], metas.meses_de(f"T{t}")))
            filas = list(h["filas"]) + [dict(f, indicador=asignaciones.get(f"{h['hoja']}|{f['fila']}")) for f in h["no_reconocidas"] + h["sueltas"]
                                        if asignaciones.get(f"{h['hoja']}|{f['fila']}") in metas.POR_CLAVE]
            for f in filas:
                for mes_h, v in f["valores"].items():
                    mes = mapa_meses.get(int(mes_h))
                    if not mes:
                        continue
                    periodo = f"M{mes:02d}"
                    clave = f["indicador"]
                    if (anio, mes, clave) in existentes:
                        if modo == "completar":
                            continue
                        # reemplazar: lo anterior queda guardado, marcado como reemplazado por esta carga
                        con.execute("""UPDATE metas_realizado SET reemplazado_por = ? WHERE equipo_id = ? AND anio = ? AND indicador = ?
                                       AND reemplazado_por IS NULL AND (periodo = ? OR periodo = ?)""",
                                    (cid, equipo_id, anio, clave, periodo, f"T{metas.trimestre_de_mes(mes)}"))
                        con.execute("""UPDATE metas_importado SET reemplazado_por = ? WHERE equipo_id = ? AND anio = ? AND indicador = ?
                                       AND periodo = ? AND anulada = 0 AND reemplazado_por IS NULL AND carga_id != ?""",
                                    (cid, equipo_id, anio, clave, periodo, cid))
                    for sigla, cant in v["personas"].items():
                        uid = vinculos.get(sigla)
                        uid = int(uid) if str(uid or "").isdigit() and int(uid) in validos else None
                        con.execute("""INSERT INTO metas_importado (carga_id, equipo_id, anio, periodo, indicador, usuario_id, sigla, cantidad)
                                       VALUES (?,?,?,?,?,?,?,?)""", (cid, equipo_id, anio, periodo, clave, uid, sigla, int(cant)))
                        n += 1
                    if v["sin_asignar"]:
                        con.execute("""INSERT INTO metas_importado (carga_id, equipo_id, anio, periodo, indicador, sin_asignar, cantidad)
                                       VALUES (?,?,?,?,?,1,?)""", (cid, equipo_id, anio, periodo, clave, int(v["sin_asignar"])))
                        n += 1
                    periodos.add(f"{anio}-{periodo}")
        con.execute("UPDATE metas_cargas SET periodos = ?, valores = ? WHERE id = ?", (json.dumps(sorted(periodos)), n, cid))
    return cid, n


def cargas(equipo_ids=None, limite=200):
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("""SELECT c.*, e.nombre AS equipo FROM metas_cargas c LEFT JOIN equipos e ON e.id = c.equipo_id
                                                 ORDER BY c.id DESC LIMIT ?""", (limite,))]
        for c in filas:
            c["periodos"] = json.loads(c["periodos"] or "[]")
            c["sin_usuario"] = [r[0] for r in con.execute("""SELECT DISTINCT sigla FROM metas_importado WHERE carga_id = ? AND usuario_id IS NULL
                                                             AND sin_asignar = 0 AND sigla IS NOT NULL ORDER BY sigla""", (c["id"],))]
    if equipo_ids is not None:
        filas = [c for c in filas if c["equipo_id"] in equipo_ids]
    return filas


def nombre_periodos(periodos):
    """['2025-M01', '2025-M02', …] → «ene. a mar. 2025, abr. a jun. 2025» (agrupado por trimestre)."""
    por = defaultdict(list)
    for p in periodos:
        a, per = p.split("-")
        por[(int(a), metas.trimestre_de_mes(int(per[1:])))].append(int(per[1:]))
    return ", ".join(f"{t}.º trim. {a}" for a, t in sorted(por))


def anular(carga_id, motivo, usuario):
    motivo = (motivo or "").strip()[:300]
    if not motivo:
        raise ValueError("Contá por qué se anula la carga.")
    with db._conexion() as con:
        c = con.execute("SELECT * FROM metas_cargas WHERE id = ?", (carga_id,)).fetchone()
        if not c:
            raise ValueError("La carga no existe.")
        if c["anulado_en"]:
            raise ValueError("Esa carga ya estaba anulada.")
        con.execute("UPDATE metas_importado SET anulada = 1 WHERE carga_id = ?", (carga_id,))
        # lo que esta carga había reemplazado vuelve a contar
        r1 = con.execute("UPDATE metas_realizado SET reemplazado_por = NULL WHERE reemplazado_por = ?", (carga_id,)).rowcount
        r2 = con.execute("UPDATE metas_importado SET reemplazado_por = NULL WHERE reemplazado_por = ?", (carga_id,)).rowcount
        con.execute("UPDATE metas_cargas SET anulado_en = ?, anulado_por = ?, motivo_anulacion = ? WHERE id = ?",
                    (db._ahora(), usuario["nombre"], motivo, carga_id))
    return {"restituidos": r1 + r2, "equipo_id": c["equipo_id"]}


def vincular_sigla(carga_id, sigla, usuario_id):
    """Una persona anterior (sigla) pasa a ser un usuario del sistema: sus valores cuentan para esa persona."""
    if not db.obtener_usuario(int(usuario_id)):
        raise ValueError("Elegí un usuario.")
    with db._conexion() as con:
        return con.execute("UPDATE metas_importado SET usuario_id = ? WHERE carga_id = ? AND sigla = ? AND usuario_id IS NULL AND sin_asignar = 0",
                           (int(usuario_id), carga_id, sigla)).rowcount


def puede_importar(usuario, equipo_id):
    import auth
    return auth.es_admin(usuario) or (usuario["rol"] == "COORDINADOR" and usuario.get("equipo_id") == equipo_id)
