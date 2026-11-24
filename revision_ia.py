"""Revisión de la base de personas detenidas con IA local.

La base tiene dos niveles:
  · Alojados: fichas activas (lo que dicen los últimos listados y las cargas manuales vigentes).
  · Histórico: fichas de baja (egresos, penas cumplidas, bajas manuales). Las «Unificada con #…» no cuentan: ya son otra ficha.

Primero se toman los alojados (empezando por los del último listado de cada fuerza) y se constatan contra TODA la base
(alojados e histórico) para encontrar posibles duplicados que las reglas exactas no ven: nombres con un error de tipeo o
en otro orden, DNI con un dígito cambiado, el documento extranjero cargado como DNI en otra ficha, fechas de nacimiento
con día y mes invertidos.

Las reglas arman los pares candidatos (rápido, sin IA). Después la IA local (Ollama, en esta PC: nada sale a la red)
da su opinión sobre cada par. **La IA no cambia nada**: deja una propuesta con la corrección preparada (unificar,
conservando la ficha sugerida) y la decide quien mantiene la base, en Revisión de la base. Lo decidido no vuelve a
proponerse.

Cuándo corre: después de cada carga de listado (solo sus personas), una vez por noche dentro de la ventana de la IA
(toda la base de alojados) y a pedido, con el botón «Revisar con IA ahora».
"""
import json
import logging
import re
import threading
from datetime import datetime, timezone

import database as db
from dgpdh.dominio import personas

PROCESO = "revision_detenidos"
UMBRAL = 55                # puntaje mínimo de las reglas para que un par sea candidato
LIMITE_IA_CARGA = 60       # pares que se le consultan a la IA después de una carga
LIMITE_IA_NOCHE = 300      # pares por corrida nocturna (lo que no entra queda con la opinión de las reglas)
MAX_POR_TOKEN = 3000       # una combinación demasiado común no sirve para agrupar (se usan las otras)
VEREDICTOS = {"misma": "Misma persona", "dudosa": "Dudosa", "distinta": "Personas distintas", "": "Sin opinión de la IA"}

_candado = threading.Lock()


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS detenidos_ia_propuestas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, det_a INTEGER NOT NULL, det_b INTEGER NOT NULL, clave TEXT NOT NULL,
        puntaje INTEGER, reglas TEXT, veredicto TEXT, confianza INTEGER, motivo_ia TEXT, modelo TEXT,
        mantener_id INTEGER, origen TEXT, corrida_id INTEGER, estado TEXT NOT NULL DEFAULT 'PENDIENTE',
        decidido_por TEXT, decidido_en TEXT, decision TEXT, creado_en TEXT NOT NULL)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ia_prop_clave ON detenidos_ia_propuestas(clave, estado)")
    con.execute("""CREATE TABLE IF NOT EXISTS detenidos_ia_corridas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, origen TEXT, usuario TEXT, inicio TEXT NOT NULL, fin TEXT,
        revisadas INTEGER, comparadas INTEGER, candidatos INTEGER, consultadas_ia INTEGER, propuestas INTEGER,
        con_ia INTEGER, estado TEXT NOT NULL DEFAULT 'EN_CURSO', error TEXT)""")
    # si la PC se apagó a mitad de una corrida, queda cortada (no «en curso» para siempre)
    con.execute("UPDATE detenidos_ia_corridas SET estado = 'CORTADA', fin = ? WHERE estado = 'EN_CURSO'", (_ahora(),))


# ---------------------------------------------------------------- niveles
def nivel(f):
    return "alojado" if f.get("activo") else "historico"


def _fichas():
    """Todas las fichas que cuentan (alojados + histórico), sin las ya unificadas con otra."""
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(
            "SELECT id, nombre_completo, nombre_norm, apellido, nombres, dni, lpu, lpu_norm, doc_extranjero, fecha_nacimiento, "
            "nacionalidad, unidad_id, centro_otro, fuerza_id, fecha_listado, listado_id, activo, motivo_baja FROM detenidos")]
    return [f for f in filas if not str(f.get("motivo_baja") or "").startswith("Unificada")]


def ultimos_listados():
    """El id del último listado (no anulado) de cada fuerza."""
    with db._conexion() as con:
        filas = con.execute("""SELECT fuerza_id, MAX(id) FROM detenidos_listados WHERE anulado_en IS NULL GROUP BY fuerza_id""").fetchall()
    return {r[1] for r in filas if r[1]}


def contar_niveles():
    fichas = _fichas()
    ult = ultimos_listados()
    return {"alojados": sum(1 for f in fichas if f["activo"]), "historico": sum(1 for f in fichas if not f["activo"]),
            "ultimo_listado": sum(1 for f in fichas if f["activo"] and f.get("listado_id") in ult)}


# ---------------------------------------------------------------- reglas (sin IA)
def _digitos(s):
    return re.sub(r"\D", "", str(s or ""))


def _numeros_parecidos(a, b):
    """Mismo largo y un dígito distinto, o dos dígitos vecinos invertidos."""
    if not a or not b or a == b or len(a) != len(b):
        return None
    dif = [i for i in range(len(a)) if a[i] != b[i]]
    if len(dif) == 1:
        return "un dígito distinto"
    if len(dif) == 2 and dif[1] == dif[0] + 1 and a[dif[0]] == b[dif[1]] and a[dif[1]] == b[dif[0]]:
        return "dos dígitos invertidos"
    return None


def _fechas(a, b):
    """Propuesta 35: además de igual, invertida y un año, reconoce los dígitos del año invertidos, el siglo mal leído y las
    fechas de relleno («dudosa»: no suman ni restan). Ver dgpdh.dominio.personas.comparar_fechas."""
    return personas.comparar_fechas(a, b)


def comparar(a, b, frec=None):
    """(puntaje 0-100, [motivos]) de que dos fichas sean la misma persona. Solo reglas, sin IA.
    `frec` (propuesta 29): frecuencia de las palabras del nombre en la base; ajusta los puntos de «mismo nombre»."""
    ta, tb = a["nombre_norm"].split(), b["nombre_norm"].split()
    if not ta or not tb:
        return 0, []
    motivos, p = [], 0
    # documentos y fechas primero (es lo barato): si ya descartan el par, no se compara el nombre
    da, db_ = _digitos(a.get("dni")), _digitos(b.get("dni"))
    ea, eb = _digitos(a.get("doc_extranjero")), _digitos(b.get("doc_extranjero"))
    la, lb = a.get("lpu_norm") or "", b.get("lpu_norm") or ""
    cerca = _numeros_parecidos(da, db_)
    if cerca:
        p += 30; motivos.append(f"DNI con {cerca} ({a.get('dni')} / {b.get('dni')})")
    elif da and db_ and da != db_:
        p -= 35; motivos.append("DNI distintos")
    if (ea and ea in (db_, eb)) or (eb and eb in (da, ea)):
        p += 35; motivos.append("el documento extranjero de una ficha es el documento de la otra")
    cerca_l = _numeros_parecidos(la, lb)
    if cerca_l:
        p += 20; motivos.append(f"LPU con {cerca_l}")
    elif la and lb and la != lb:
        p -= 25; motivos.append("LPU distintos")
    if (not da or not db_) and (not la or not lb) and not (ea and eb):
        motivos.append("a una de las fichas le falta el documento")
        p += 5
    fe = _fechas(a.get("fecha_nacimiento"), b.get("fecha_nacimiento"))
    if fe in personas.PUNTOS_FECHA:
        puntos, texto = personas.PUNTOS_FECHA[fe]
        p += puntos; motivos.append(texto)
    na, nb = (a.get("nacionalidad") or "").strip().lower()[:6], (b.get("nacionalidad") or "").strip().lower()[:6]
    if na and nb and na != nb:
        p -= 10; motivos.append("nacionalidades distintas")
    if a.get("unidad_id") and a.get("unidad_id") == b.get("unidad_id"):
        p += 5; motivos.append("misma unidad")
    if p + 55 < UMBRAL and not (frec is not None and p + 63 >= UMBRAL and sorted(ta) == sorted(tb)):
        return 0, []  # (un nombre raro e idéntico puede sumar hasta 8 más: propuesta 29)
    sa, sb = set(ta), set(tb)
    comunes = sa & sb
    nombre = []
    antes = p
    if a["nombre_norm"] == b["nombre_norm"]:
        p += 55; nombre.append("mismo nombre")
    elif sa == sb:
        p += 50; nombre.append("mismo nombre con las palabras en otro orden")
    elif len(comunes) >= 2 and (sa <= sb or sb <= sa):
        p += 42; nombre.append("un nombre contiene al otro (falta un segundo nombre o un apellido)")
    elif p + 45 >= UMBRAL:
        fa_, fb_ = personas.tokens_foneticos(a["nombre_norm"]), personas.tokens_foneticos(b["nombre_norm"])
        ratio = personas.parecido(a["nombre_norm"], b["nombre_norm"])
        if fa_ == fb_:   # propuesta 32: Vázquez/Básquez, Jiménez/Giménez, Yanina/Llanina
            p += 45; nombre.append("nombre que suena igual (posible error de carga)")
        elif ratio >= 0.9:
            p += 45; nombre.append("nombre casi igual (posible error de tipeo)")
        elif len(fa_ & fb_) >= 2 and (fa_ <= fb_ or fb_ <= fa_):
            p += 38; nombre.append("un nombre contiene al otro, con una palabra que suena igual")
        elif ratio >= 0.82 and comunes:
            p += 32; nombre.append("nombre parecido")
        elif len(comunes) / len(sa | sb) >= 0.5:
            p += 22; nombre.append("comparten parte del nombre")
    if frec is not None and p - antes >= 38:
        ajuste, motivo = frec.ajuste(comunes)
        if motivo:
            p += ajuste; nombre.append(motivo)
    return max(0, min(100, p)), nombre + motivos


def _claves_nombre(tokens):
    """Claves para agrupar: pares de palabras (dos en común) y, en nombres cortos, una palabra más el comienzo de otra
    (un error de tipeo suele dejar el comienzo)."""
    t = sorted({x for x in tokens if len(x) >= 2})
    claves = {("p", x, y) for i, x in enumerate(t) for y in t[i + 1:]}
    if len(t) <= 3:
        claves |= {("q", x, y[:2]) for x in t for y in t if x != y and len(x) >= 3}
    if len(t) == 1:
        claves.add(("s", t[0]))
    return claves


def _claves_foneticas(tokens, ambiguas):
    """Propuesta 32: pares de palabras por cómo suenan. Solo hacen falta si alguna de las dos se escribe de más de una forma en
    la base (`ambiguas`: Vázquez y Básquez); si no, el par escrito igual ya las agrupa."""
    t = sorted({personas.clave_fonetica(x) for x in tokens if len(x) >= 2} - {""})
    return {("f", x, y) for i, x in enumerate(t) for y in t[i + 1:] if x in ambiguas or y in ambiguas}


def _foneticas_ambiguas(todas):
    formas = {}
    for f in todas:
        for x in set(f["nombre_norm"].split()):
            formas.setdefault(personas.clave_fonetica(x), set()).add(x)
    return {k for k, v in formas.items() if len(v) > 1}


def candidatos(base, todas, decididas=frozenset()):
    """Pares (a, b, puntaje, motivos) entre las fichas de `base` y todas las demás, ordenados por puntaje."""
    por_clave, por_doc = {}, {}
    foneticos = {}
    frec = personas.FrecuenciaNombres.desde(f["nombre_norm"] for f in todas)
    ambiguas = _foneticas_ambiguas(todas)
    for f in todas:
        tokens = f["nombre_norm"].split()
        foneticos[f["id"]] = personas.tokens_foneticos(f["nombre_norm"])
        for k in _claves_nombre(tokens) | _claves_foneticas(tokens, ambiguas):
            por_clave.setdefault(k, []).append(f)
        for n in {_digitos(f.get("dni")), _digitos(f.get("doc_extranjero")), f.get("lpu_norm") or ""}:
            if len(n) >= 5:
                for i in range(len(n)):  # una posición tapada: encuentra el número con un dígito cambiado
                    por_doc.setdefault((len(n), n[:i] + "*" + n[i + 1:]), []).append(f)
                for i in range(len(n) - 1):  # dos vecinos invertidos
                    por_doc.setdefault(("inv", n[:i] + n[i + 1] + n[i] + n[i + 2:]), []).append(f)
                por_doc.setdefault(("igual", n), []).append(f)
    vistos, salida, comparadas = set(), [], 0
    for a in base:
        ta = set(a["nombre_norm"].split())
        elegidos = {}
        fa_ = foneticos.get(a["id"]) or personas.tokens_foneticos(a["nombre_norm"])
        for k in _claves_nombre(ta) | _claves_foneticas(ta, ambiguas):
            grupo = por_clave.get(k, [])
            if len(grupo) > MAX_POR_TOKEN:
                continue
            for b in grupo:
                if b["id"] in elegidos or b["id"] == a["id"]:
                    continue
                if k[0] == "f":
                    fb_ = foneticos[b["id"]]
                    if len(fa_ & fb_) < min(len(fa_), len(fb_)) - 1:
                        continue  # más de una palabra que suena distinto
                    elegidos[b["id"]] = b
                    continue
                tb = set(b["nombre_norm"].split())
                n = len(ta & tb)
                if k[0] == "p" and n < min(len(ta), len(tb)) - 1:
                    continue  # más de una palabra distinta
                if k[0] == "q":  # una palabra en común + otra que empieza igual
                    if not ({x[:2] for x in ta - tb} & {x[:2] for x in tb - ta}):
                        continue
                elegidos[b["id"]] = b
        for n in {_digitos(a.get("dni")), _digitos(a.get("doc_extranjero")), a.get("lpu_norm") or ""}:
            if len(n) >= 5:
                for k in [(len(n), n[:i] + "*" + n[i + 1:]) for i in range(len(n))] + [("inv", n), ("igual", n)]:
                    for b in por_doc.get(k, []):
                        if b["id"] != a["id"]:
                            elegidos[b["id"]] = b
        for b in elegidos.values():
            par = (a["id"], b["id"]) if a["id"] < b["id"] else (b["id"], a["id"])
            if par in vistos:
                continue
            vistos.add(par)
            if f"dup:{par[0]}:{par[1]}" in decididas:
                continue
            # dos alojados con el mismo nombre exacto ya los muestran las reglas fijas («Posible misma persona»)
            if a["activo"] and b["activo"] and a["nombre_norm"] == b["nombre_norm"]:
                continue
            comparadas += 1
            p, motivos = comparar(a, b, frec)
            if p >= UMBRAL:
                salida.append((a, b, p, motivos))
    salida.sort(key=lambda x: -x[2])
    return salida, comparadas


# ---------------------------------------------------------------- IA local
def _ficha_para_ia(f, unidades):
    return {"ficha": f["id"], "estado": "alojada" if f["activo"] else f"histórico ({f.get('motivo_baja') or 'baja'})",
            "nombre": f.get("nombre_completo"), "dni": f.get("dni"), "lpu": f.get("lpu"), "documento_extranjero": f.get("doc_extranjero"),
            "fecha_nacimiento": f.get("fecha_nacimiento"), "nacionalidad": f.get("nacionalidad"),
            "alojamiento": unidades.get(f.get("unidad_id")) or f.get("centro_otro"), "fecha_listado": f.get("fecha_listado")}


def opinion_ia(a, b, motivos, unidades):
    """(veredicto, confianza, motivo, modelo) o None si no hay IA. La IA solo opina."""
    import nucleo
    if not nucleo.ia_disponible(PROCESO):
        return None
    prompt = ("Sos un asistente que revisa una base de datos de personas privadas de la libertad para encontrar fichas duplicadas. "
              "Tenés que decir si estas DOS fichas corresponden a la MISMA persona. Tené en cuenta errores de tipeo, nombres en otro orden, "
              "segundos nombres o apellidos maternos que faltan, dígitos cambiados en el DNI, documentos extranjeros cargados como DNI y fechas "
              "con día y mes invertidos. Si los documentos son claramente distintos, probablemente son personas distintas aunque se llamen igual. "
              "No inventes datos. Respondé SOLO con JSON: {\"veredicto\": \"misma\" | \"distinta\" | \"dudosa\", \"confianza\": 0 a 100, "
              "\"motivo\": \"una o dos oraciones en castellano\"}.\n\n"
              f"Ficha A: {json.dumps(_ficha_para_ia(a, unidades), ensure_ascii=False)}\n"
              f"Ficha B: {json.dumps(_ficha_para_ia(b, unidades), ensure_ascii=False)}\n"
              f"Lo que encontraron las reglas: {'; '.join(motivos)}\n")
    from dgpdh.ia import esquemas, modelos
    salida = nucleo.consultar_ollama(PROCESO, prompt, num_predict=220, timeout=120, formato_json=True,
                                     esquema=esquemas.esquema(esquemas.OpinionDuplicado), version_prompt=modelos.version_prompt(prompt.split("Ficha A:")[0]))
    if not salida:
        return None
    # propuesta 19: con el esquema, la respuesta ya llega con veredicto, confianza y motivo válidos
    validada = esquemas.validar(esquemas.OpinionDuplicado, salida)
    if validada is not None:
        return validada.veredicto, validada.confianza, validada.motivo, nucleo.modelo_para(PROCESO)
    # Ollama anterior a 0.5 (sin esquemas): se interpreta como antes
    try:
        m = re.search(r"\{.*\}", salida, re.S)
        r = json.loads(m.group(0) if m else salida)
    except (ValueError, AttributeError):
        return None
    ver = str(r.get("veredicto") or "").strip().lower()
    ver = {"misma persona": "misma", "mismo": "misma", "distintas": "distinta", "distinto": "distinta", "dudoso": "dudosa"}.get(ver, ver)
    if ver not in ("misma", "distinta", "dudosa"):
        return None
    try:
        conf = max(0, min(100, int(float(r.get("confianza") or 0))))
    except (TypeError, ValueError):
        conf = 0
    return ver, conf, str(r.get("motivo") or "")[:500], nucleo.modelo_para(PROCESO)


# ---------------------------------------------------------------- corrida
def revisar(origen="PEDIDO", ids=None, usuario=None, limite_ia=None):
    """Arma las propuestas. `ids`: solo esas personas como base (por ejemplo, las de una carga); si no, todos los alojados
    (primero los del último listado de cada fuerza). Devuelve el resumen de la corrida, o None si ya hay otra en curso."""
    if not _candado.acquire(blocking=False):
        return None
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else (usuario or "Sistema")
    with db._conexion() as con:
        cid = con.execute("INSERT INTO detenidos_ia_corridas (origen, usuario, inicio) VALUES (?,?,?)", (origen, quien, _ahora())).lastrowid
    try:
        import detenidos as det
        todas = _fichas()
        if ids is not None:
            ids = set(ids)
            base = [f for f in todas if f["id"] in ids and f["activo"]]
        else:
            ult = ultimos_listados()
            base = sorted([f for f in todas if f["activo"]], key=lambda f: (0 if f.get("listado_id") in ult else 1, f["id"]))
        with db._conexion() as con:
            decididas = {r[0] for r in con.execute("SELECT clave FROM detenidos_revision")}
            ya = {r[0] for r in con.execute("SELECT clave FROM detenidos_ia_propuestas")}
        pares, comparadas = candidatos(base, todas, decididas)
        pares = [x for x in pares if f"dup:{min(x[0]['id'], x[1]['id'])}:{max(x[0]['id'], x[1]['id'])}" not in ya]
        unidades = {u["id"]: u["nombre"] for u in db.listar_unidades(solo_activas=False)}
        limite = LIMITE_IA_CARGA if limite_ia is None and ids is not None else (LIMITE_IA_NOCHE if limite_ia is None else limite_ia)
        consultadas = con_ia = 0
        for i, (a, b, p, motivos) in enumerate(pares):
            op = None
            if i < limite:
                consultadas += 1
                op = opinion_ia(a, b, motivos, unidades)
                con_ia += 1 if op else 0
            ver, conf, mot, modelo = op or ("", None, None, None)
            mantener = det.ficha_a_conservar([a["id"], b["id"]])
            x, y = sorted((a["id"], b["id"]))
            with db._conexion() as con:
                con.execute("""INSERT INTO detenidos_ia_propuestas (det_a, det_b, clave, puntaje, reglas, veredicto, confianza, motivo_ia, modelo,
                               mantener_id, origen, corrida_id, creado_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (x, y, f"dup:{x}:{y}", p, json.dumps(motivos, ensure_ascii=False), ver, conf, mot, modelo, mantener, origen, cid, _ahora()))
        resumen = {"corrida_id": cid, "revisadas": len(base), "comparadas": comparadas, "candidatos": len(pares),
                   "consultadas_ia": consultadas, "con_ia": con_ia, "propuestas": len(pares)}
        with db._conexion() as con:
            con.execute("""UPDATE detenidos_ia_corridas SET fin = ?, revisadas = ?, comparadas = ?, candidatos = ?, consultadas_ia = ?,
                           propuestas = ?, con_ia = ?, estado = 'TERMINADA' WHERE id = ?""",
                        (_ahora(), len(base), comparadas, len(pares), consultadas, len(pares), con_ia, cid))
        if pares:
            _avisar(len(pares), origen)
        return resumen
    except Exception as e:
        logging.exception("Error en la revisión de detenidos con IA")
        with db._conexion() as con:
            con.execute("UPDATE detenidos_ia_corridas SET fin = ?, estado = 'ERROR', error = ? WHERE id = ?", (_ahora(), str(e)[:500], cid))
        return {"corrida_id": cid, "error": str(e)}
    finally:
        _candado.release()


def _avisar(n, origen):
    """Aviso dentro del sistema a quienes mantienen la base (no sale a la red)."""
    try:
        import detenidos as det
        ids = [u["id"] for u in db.listar_usuarios() if u["activo"] and det.puede_mantener(u)]
        if ids:
            db.notificar_usuarios(ids, f"Revisión con IA ({origen.lower()}): {n} posible(s) ficha(s) duplicada(s) para decidir.",
                                  "/detenidos/revision?tipo=ia")
    except Exception:
        logging.exception("No se pudo avisar de las propuestas de la IA")


def en_segundo_plano(origen, ids=None, usuario=None):
    """Para no demorar la pantalla (confirmar una carga, el botón): corre en un hilo aparte."""
    if _candado.locked():
        return False
    threading.Thread(target=revisar, kwargs={"origen": origen, "ids": ids, "usuario": usuario}, name="revision-ia", daemon=True).start()
    return True


def toca_nocturna(hoy=None):
    """¿Falta la corrida de esta noche? (una por día, dentro de la ventana de la IA)."""
    import automatico
    if not automatico.en_ventana():
        return False
    hoy = (hoy or datetime.now()).strftime("%Y-%m-%d")
    with db._conexion() as con:
        f = con.execute("SELECT MAX(inicio) FROM detenidos_ia_corridas WHERE origen = 'NOCHE' AND estado IN ('TERMINADA', 'EN_CURSO')").fetchone()[0]
    # inicio está en UTC: alcanza con que la última sea de hace menos de 20 horas
    if f:
        horas = (datetime.now(timezone.utc) - datetime.fromisoformat(f)).total_seconds() / 3600
        return horas >= 20
    return True


def tick():
    """Lo llama el trabajador nocturno cada 10 minutos."""
    if toca_nocturna():
        en_segundo_plano("NOCHE")


# ---------------------------------------------------------------- para el panel
def corridas(limite=10):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM detenidos_ia_corridas ORDER BY id DESC LIMIT ?", (limite,))]


def en_curso():
    return _candado.locked()


def pendientes():
    with db._conexion() as con:
        decididas = {r[0] for r in con.execute("SELECT clave FROM detenidos_revision")}
        filas = [dict(f) for f in con.execute("SELECT * FROM detenidos_ia_propuestas WHERE estado IN ('PENDIENTE', 'EN_REVISION') ORDER BY "
                                              "CASE veredicto WHEN 'misma' THEN 0 WHEN 'dudosa' THEN 1 WHEN '' THEN 2 ELSE 3 END, "
                                              "COALESCE(confianza, 0) DESC, puntaje DESC, id")]
    return [f for f in filas if f["clave"] not in decididas]


def cerrar(pid, decision, usuario):
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario
    with db._conexion() as con:
        p = con.execute("SELECT * FROM detenidos_ia_propuestas WHERE id = ?", (pid,)).fetchone()
        if not p:
            raise ValueError("La propuesta no existe.")
        con.execute("UPDATE detenidos_ia_propuestas SET estado = ?, decision = ?, decidido_por = ?, decidido_en = ? WHERE clave = ? "
                    "AND estado IN ('PENDIENTE', 'EN_REVISION')",
                    ("ACEPTADA" if decision == "unificar" else "RECHAZADA", decision, quien, _ahora(), p["clave"]))
    return dict(p)


ESTADOS_ABIERTOS = ("PENDIENTE", "EN_REVISION")


def dejar_en_revision(pid, usuario, nota=""):
    """Modificación 7: la propuesta queda pendiente con estado propio («En revisión») hasta que alguien decida. Auditada."""
    from dgpdh.seguridad import auditoria
    quien = (usuario or {}).get("nombre") if isinstance(usuario, dict) else usuario
    with db._conexion() as con:
        p = con.execute("SELECT * FROM detenidos_ia_propuestas WHERE id = ?", (pid,)).fetchone()
        if not p or p["estado"] not in ESTADOS_ABIERTOS:
            raise ValueError("La propuesta ya fue decidida.")
        con.execute("UPDATE detenidos_ia_propuestas SET estado = 'EN_REVISION', en_revision_por = ?, en_revision_en = ?, en_revision_nota = ? "
                    "WHERE id = ?", (quien, _ahora(), (nota or "").strip()[:300] or None, pid))
        auditoria.registrar(con, accion="Dejar en revisión", entidad="detenidos_ia_propuesta", entidad_id=pid, antes={"estado": p["estado"]},
                            despues={"estado": "EN_REVISION"}, actor=auditoria.Actor.desde(usuario if isinstance(usuario, dict) else None),
                            motivo=(nota or "").strip()[:300] or None, origen="detenidos")


def obtener(pid):
    with db._conexion() as con:
        p = con.execute("SELECT * FROM detenidos_ia_propuestas WHERE id = ?", (pid,)).fetchone()
    return dict(p) if p else None


# ---------------------------------------------------------------- propuestas 30 y 41: aprender de lo decidido
def ordenar_para_decidir_primero(alertas):
    """Propuesta 41: las propuestas más inciertas primero (cerca del umbral, reglas e IA en desacuerdo, fichas activas)."""
    from dgpdh.dominio import calibracion

    def clave(al):
        pr = al.get("propuesta") or {}
        activas = all((x or {}).get("activo") for x in (al.get("personas") or [])) and len(al.get("personas") or []) == 2
        return -calibracion.incertidumbre(pr.get("puntaje"), pr.get("veredicto"), pr.get("confianza"), ambas_activas=activas, umbral=UMBRAL)
    return sorted(alertas, key=clave)


def calibracion():
    """Propuesta 30: pesos estimados de cada coincidencia y umbral sugerido, a partir de las propuestas ya decididas.
    Es un informe: los puntos de `comparar` no cambian solos."""
    from dgpdh.dominio import calibracion as cal
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute("SELECT puntaje, reglas, decision FROM detenidos_ia_propuestas "
                                              "WHERE estado IN ('ACEPTADA', 'RECHAZADA') AND decision IN ('unificar', 'distintas')")]
    decisiones = [(json.loads(f["reglas"] or "[]"), f["decision"] == "unificar") for f in filas]
    return {"decisiones": len(filas), "minimo": cal.MINIMO_DECISIONES,
            "pesos": cal.pesos(decisiones) if len(filas) >= cal.MINIMO_DECISIONES else [],
            "umbral": cal.umbral_sugerido([(int(f["puntaje"] or 0), f["decision"] == "unificar") for f in filas], UMBRAL)}


# ---------------------------------------------------------------- propuesta 31: grupos de la misma persona
def grupos_pendientes():
    """Las propuestas pendientes agrupadas por persona probable: [{fichas, propuestas, sugerida}] (solo grupos de 3 o más
    fichas; los pares sueltos se ven como siempre)."""
    import detenidos as det
    from dgpdh.dominio import grupos as gr
    props = pendientes()
    salida = []
    for g in gr.componentes((p["det_a"], p["det_b"]) for p in props):
        if len(g) < 3:
            continue
        miembros = set(g)
        salida.append({"fichas": [det.obtener(i) for i in g], "propuestas": [p for p in props if p["det_a"] in miembros],
                       "sugerida": gr.sugerida(g, det.ficha_a_conservar)})
    return salida


def unificar_grupo(ids, mantener, usuario):
    """Unifica todas las fichas del grupo en la que se conserva y cierra sus propuestas. Devuelve cuántas se unieron."""
    import detenidos as det
    ids = sorted(set(int(i) for i in ids))
    if mantener not in ids or len(ids) < 2:
        raise ValueError("Elegí cuál ficha se conserva.")
    hechas = 0
    for otra in ids:
        if otra == mantener:
            continue
        ficha = det.obtener(otra)
        if not ficha or str(ficha.get("motivo_baja") or "").startswith("Unificada"):
            continue
        det.unificar(mantener, otra, usuario)
        with db._conexion() as con:
            det._decidir(con, det.clave_duplicado(mantener, otra), "duplicado", "unificar", "Grupo de la misma persona", det._quien(usuario))
        hechas += 1
    for p in pendientes():
        if p["det_a"] in ids and p["det_b"] in ids:
            cerrar(p["id"], "unificar", usuario)
    return hechas


def sacar_del_grupo(ids, ficha, usuario):
    """La ficha no es la misma persona que las demás del grupo: sus pares con ellas quedan como personas distintas."""
    import detenidos as det
    ids = {int(i) for i in ids}
    if ficha not in ids:
        raise ValueError("Esa ficha no está en el grupo.")
    n = 0
    for p in pendientes():
        if ficha in (p["det_a"], p["det_b"]) and {p["det_a"], p["det_b"]} <= ids:
            with db._conexion() as con:
                det._decidir(con, p["clave"], "duplicado", "distintas", "Sacada del grupo", det._quien(usuario))
            cerrar(p["id"], "distintas", usuario)
            n += 1
    return n
