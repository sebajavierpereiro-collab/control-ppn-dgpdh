"""Documentos de referencia para la IA: una carpeta del sistema (con las subcarpetas que se quiera) donde se guardan
documentos YA FIRMADOS para que la IA local aprenda de ellos.

Qué hace con esos documentos (todo local; no sale nada del organismo y no se reentrena el modelo):
- Términos institucionales: los nombres, siglas y palabras propias del organismo que aparecen en lo firmado y que el
  diccionario general no conoce (Ezeiza, CPF, PPN, apellidos…) dejan de «corregirse» (ver respetar_terminos).
- Parecidos: para un documento nuevo busca los firmados más parecidos y, como las carpetas tienen nombre, propone de
  qué carpeta (tema, tipo) es. Solo propone.
- Estilo: a las mejoras de redacción se les da como ejemplo de tono el firmado más parecido (sin copiar datos).

Los archivos se pueden copiar directamente en la carpeta (con el Explorador de Windows) o subir desde la pantalla
Administración → IA → Documentos de referencia. Son copias solo para la IA: se pueden eliminar de verdad (uno por uno, todos
los que no se pueden leer o están repetidos, y las carpetas vacías) y reordenar en carpetas por tipo, tema o año.
"""
import hashlib
import logging
import math
import re
import shutil
import threading
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import database as db

CARPETA = Path(__file__).resolve().parent / "datos" / "referencia_ia"
RETIRADOS = "_retirados"
EXTENSIONES = {".docx", ".doc", ".pdf"}
FIRMAS = {".docx": b"PK\x03\x04", ".doc": b"\xd0\xcf\x11\xe0", ".pdf": b"%PDF"}
MAX_BYTES = 30 * 1024 * 1024
MIN_REPETICIONES_TERMINO = 2
_PROHIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_LOCK_ESCANEO = threading.Lock()
_CACHE = {"version": None, "terminos": None, "indice": None}
ESTADO = {"escaneando": False, "ultimo": None, "ultimo_resultado": None}

_STOP = set("""de la que el en y a los del se las por un para con no una su al lo como mas más pero sus le ya o este
esta entre cuando muy sin sobre también tambien me hasta hay donde quien desde todo nos durante todos uno les ni contra otros
ese eso ante ellos e esto mi antes algunos qué unos yo otro otras otra él tanto esa estos mucho quienes nada muchos cual poco
ella estar estas algunas algo nosotros dicho dicha dichos dichas mismo misma asimismo cual cuales cuyo cuya señor señora sr sra
dr dra nota fecha número numero expediente direccion dirección general procuracion procuración penitenciaria nacion nación""".split())


def configurar(config):
    global CARPETA
    if config is not None and config.has_option("referencia_ia", "carpeta"):
        CARPETA = Path(config.get("referencia_ia", "carpeta")).expanduser()


def inicializar(con):
    con.execute("""CREATE TABLE IF NOT EXISTS referencia_docs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ruta TEXT NOT NULL UNIQUE, carpeta TEXT NOT NULL, nombre TEXT NOT NULL,
        tamano INTEGER, mtime REAL, sha256 TEXT, texto TEXT, palabras INTEGER DEFAULT 0, estado TEXT NOT NULL,
        detalle TEXT, agregado_por TEXT, agregado_en TEXT, indexado_en TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_referencia_carpeta ON referencia_docs(carpeta)")
    con.execute("""CREATE TABLE IF NOT EXISTS referencia_registro (
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, usuario TEXT, accion TEXT NOT NULL, detalle TEXT)""")


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _registrar(accion, detalle, usuario=None):
    with db._conexion() as con:
        con.execute("INSERT INTO referencia_registro (fecha, usuario, accion, detalle) VALUES (?, ?, ?, ?)",
                    (_ahora(), usuario or db._quien(), accion, (detalle or "")[:500]))


# ------------------------------------------------------------------ carpetas y archivos
class ErrorReferencia(ValueError):
    """Error explicable a la persona (nombre inválido, archivo que no corresponde…)."""


def nombre_valido(nombre):
    """Nombre de carpeta o archivo apto para Windows. Devuelve el nombre limpio o levanta ErrorReferencia."""
    crudo = unicodedata.normalize("NFC", (nombre or "").strip())
    if crudo in (".", ".."):
        raise ErrorReferencia(f"«{crudo}» no se puede usar como nombre.")
    n = crudo.rstrip(". ")
    if not n:
        raise ErrorReferencia("Falta el nombre.")
    if n.startswith("_retirados"):
        raise ErrorReferencia(f"«{n}» no se puede usar como nombre.")
    if _PROHIBIDOS.search(n):
        raise ErrorReferencia(f"«{n}» tiene caracteres que Windows no acepta (< > : \" / \\ | ? *).")
    if n.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ErrorReferencia(f"«{n}» es un nombre reservado de Windows.")
    if len(n) > 120:
        raise ErrorReferencia("El nombre es demasiado largo (máximo 120 caracteres).")
    return n


def _ruta_segura(rel):
    """Ruta absoluta dentro de CARPETA para una ruta relativa «a/b/c» (cada parte validada)."""
    partes = [p for p in re.split(r"[\\/]+", rel or "") if p and p != "."]
    destino = CARPETA.joinpath(*[nombre_valido(p) for p in partes]) if partes else CARPETA
    base = CARPETA.resolve()
    real = destino.resolve()
    if real != base and base not in real.parents:
        raise ErrorReferencia("Ruta fuera de la carpeta de referencia.")
    return destino


def rel_de(ruta):
    return Path(ruta).resolve().relative_to(CARPETA.resolve()).as_posix() if Path(ruta).resolve() != CARPETA.resolve() else ""


def crear_carpeta(padre, nombre, usuario=None):
    """Crea una subcarpeta. Dentro de una misma carpeta no puede haber dos con el mismo nombre (sin distinguir mayúsculas)."""
    nombre = nombre_valido(nombre)
    base = _ruta_segura(padre)
    if not base.is_dir():
        raise ErrorReferencia("La carpeta donde querés crearla no existe.")
    if any(h.name.lower() == nombre.lower() for h in base.iterdir()):
        raise ErrorReferencia(f"Ya hay una carpeta o archivo llamado «{nombre}» en ese lugar: elegí otro nombre.")
    (base / nombre).mkdir()
    rel = rel_de(base / nombre)
    _registrar("CARPETA", f"Carpeta creada: {rel}", usuario)
    return rel


def _nombre_libre(carpeta, nombre):
    destino = carpeta / nombre
    if not destino.exists():
        return destino
    stem, suf = Path(nombre).stem, Path(nombre).suffix
    i = 2
    while (carpeta / f"{stem} ({i}){suf}").exists():
        i += 1
    return carpeta / f"{stem} ({i}){suf}"


def guardar_archivo(carpeta_rel, ruta_relativa, datos, usuario=None):
    """Guarda un archivo subido. `ruta_relativa` puede traer subcarpetas («Salud/2025/nota.docx», al subir una carpeta
    entera): se crean. No pisa nada: si el nombre existe se agrega « (2)». Devuelve la ruta relativa final."""
    partes = [p for p in re.split(r"[\\/]+", ruta_relativa or "") if p and p != "."]
    if not partes:
        raise ErrorReferencia("Archivo sin nombre.")
    nombre = nombre_valido(partes[-1])
    suf = Path(nombre).suffix.lower()
    if suf not in EXTENSIONES:
        raise ErrorReferencia(f"«{nombre}»: solo se aceptan .docx, .doc y .pdf.")
    if not datos:
        raise ErrorReferencia(f"«{nombre}» está vacío.")
    if len(datos) > MAX_BYTES:
        raise ErrorReferencia(f"«{nombre}» supera el máximo de {MAX_BYTES // (1024 * 1024)} MB.")
    if datos[:4] != FIRMAS[suf]:
        raise ErrorReferencia(f"«{nombre}»: el contenido no coincide con la extensión.")
    carpeta = _ruta_segura("/".join([carpeta_rel or ""] + partes[:-1]))
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = _nombre_libre(carpeta, nombre)
    destino.write_bytes(datos)
    rel = rel_de(destino)
    with db._conexion() as con:
        con.execute("""INSERT INTO referencia_docs (ruta, carpeta, nombre, estado, agregado_por, agregado_en)
                       VALUES (?, ?, ?, 'PENDIENTE', ?, ?) ON CONFLICT(ruta) DO NOTHING""",
                    (rel, rel_de(destino.parent), destino.name, usuario or db._quien(), _ahora()))
    return rel


def retirar(doc_id, usuario=None):
    """Saca un documento de la referencia sin borrarlo: se mueve a _retirados (con su misma ruta) y deja de usarse."""
    d = obtener(doc_id)
    if not d or d["estado"] == "RETIRADO":
        raise ErrorReferencia("El documento ya no está en la referencia.")
    origen = _ruta_segura(d["ruta"])
    if origen.is_file():
        carpeta_ret = CARPETA / RETIRADOS / d["carpeta"] if d["carpeta"] else CARPETA / RETIRADOS
        carpeta_ret.mkdir(parents=True, exist_ok=True)
        destino = _nombre_libre(carpeta_ret, d["nombre"])
        shutil.move(str(origen), str(destino))
    with db._conexion() as con:
        con.execute("UPDATE referencia_docs SET estado = 'RETIRADO', detalle = ? WHERE id = ?",
                    (f"Retirado por {usuario or db._quien()} el {_ahora()[:10]}", doc_id))
    _registrar("RETIRAR", f"Retirado: {d['ruta']}", usuario)
    _invalidar()


def obtener(doc_id):
    with db._conexion() as con:
        f = con.execute("SELECT * FROM referencia_docs WHERE id = ?", (doc_id,)).fetchone()
    return dict(f) if f else None


# ------------------------------------------------------------------ lectura (escaneo)
def _sha(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _extraer_texto(ruta):
    suf = ruta.suffix.lower()
    if suf == ".pdf":
        from dgpdh.servicios import herramientas_locales  # propuesta 4: si no tiene texto y el OCR está activo, la copia con OCR
        return herramientas_locales.texto_pdf(ruta)
    import nucleo
    return nucleo.texto_de_documento(ruta)  # .doc: se convierte con Word si está instalado


def _archivos():
    if not CARPETA.is_dir():
        return []
    salida = []
    for p in CARPETA.rglob("*"):
        rel = p.relative_to(CARPETA)
        if not p.is_file() or rel.parts[0] == RETIRADOS or any(x.startswith((".", "~$")) for x in rel.parts):
            continue
        if p.suffix.lower() in EXTENSIONES:
            salida.append(p)
    return salida


def escanear(usuario=None):
    """Revisa la carpeta: lee lo nuevo o cambiado, marca lo que ya no está. Liviano si no cambió nada.
    Devuelve {"nuevos", "actualizados", "ausentes", "errores"}."""
    if not _LOCK_ESCANEO.acquire(blocking=False):
        return None  # ya hay una revisión en curso
    ESTADO["escaneando"] = True
    res = {"nuevos": 0, "actualizados": 0, "ausentes": 0, "errores": 0}
    try:
        CARPETA.mkdir(parents=True, exist_ok=True)
        with db._conexion() as con:
            previos = {f["ruta"]: dict(f) for f in con.execute("SELECT id, ruta, tamano, mtime, sha256, estado FROM referencia_docs")}
        vistos = set()
        hashes = {p["sha256"] for p in previos.values() if p["sha256"] and p["estado"] in ("INDEXADO", "SIN_TEXTO")}
        for p in _archivos():
            rel = p.relative_to(CARPETA).as_posix()
            vistos.add(rel)
            st = p.stat()
            prev = previos.get(rel)
            if prev and prev["estado"] not in ("PENDIENTE", "AUSENTE", "ELIMINADO") and prev["tamano"] == st.st_size and prev["mtime"] == st.st_mtime:
                continue
            try:
                sha = _sha(p)
                if prev and prev["sha256"] == sha and prev["estado"] in ("INDEXADO", "SIN_TEXTO", "DUPLICADO"):
                    with db._conexion() as con:
                        con.execute("UPDATE referencia_docs SET tamano = ?, mtime = ? WHERE ruta = ?", (st.st_size, st.st_mtime, rel))
                    continue
                if sha in hashes and not (prev and prev["sha256"] == sha):
                    estado, texto, detalle = "DUPLICADO", None, "El mismo archivo ya está en otra carpeta: no se cuenta dos veces."
                else:
                    texto = _extraer_texto(p) or ""
                    estado = "INDEXADO" if len(texto.strip()) >= 40 else "SIN_TEXTO"
                    detalle = None if estado == "INDEXADO" else "No tiene texto legible (¿PDF escaneado?): no se usa."
                    hashes.add(sha)
            except Exception as e:
                logging.info(f"Referencia IA: no se pudo leer {rel}: {e}")
                sha, estado, texto, detalle = None, "ERROR", None, "No se pudo leer el archivo (¿.doc sin Word o dañado?)."
                res["errores"] += 1
            palabras = len(_RE_PALABRA.findall(texto or ""))
            with db._conexion() as con:
                con.execute("""INSERT INTO referencia_docs (ruta, carpeta, nombre, tamano, mtime, sha256, texto, palabras, estado,
                                   detalle, agregado_por, agregado_en, indexado_en)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                               ON CONFLICT(ruta) DO UPDATE SET tamano = excluded.tamano, mtime = excluded.mtime, sha256 = excluded.sha256,
                                   texto = excluded.texto, palabras = excluded.palabras, estado = excluded.estado,
                                   detalle = excluded.detalle, indexado_en = excluded.indexado_en""",
                            (rel, Path(rel).parent.as_posix() if "/" in rel else "", p.name, st.st_size, st.st_mtime, sha, texto,
                             palabras, estado, detalle, "copiado en la carpeta", _ahora(), _ahora()))
            res["actualizados" if prev and prev["estado"] != "PENDIENTE" else "nuevos"] += 1
        for rel, prev in previos.items():
            if rel not in vistos and prev["estado"] not in ("RETIRADO", "AUSENTE", "ELIMINADO"):
                with db._conexion() as con:
                    con.execute("UPDATE referencia_docs SET estado = 'AUSENTE', detalle = 'El archivo ya no está en la carpeta.' WHERE ruta = ?", (rel,))
                res["ausentes"] += 1
                if prev["estado"] == "INDEXADO" and prev["sha256"]:
                    _promover_repetido(prev["sha256"])
        if any(res.values()):
            _registrar("REVISION", f"{res['nuevos']} nuevo(s), {res['actualizados']} actualizado(s), {res['ausentes']} que ya no están, "
                                   f"{res['errores']} con error", usuario or "Sistema")
            _invalidar()
        ESTADO["ultimo"], ESTADO["ultimo_resultado"] = _ahora(), res
        return res
    finally:
        ESTADO["escaneando"] = False
        _LOCK_ESCANEO.release()


def _promover_repetido(sha):
    """Si se fue el documento en uso y quedaba una copia marcada «repetido», esa copia pasa a usarse."""
    with db._conexion() as con:
        if con.execute("SELECT 1 FROM referencia_docs WHERE sha256 = ? AND estado = 'INDEXADO'", (sha,)).fetchone():
            return
        f = con.execute("SELECT id, ruta FROM referencia_docs WHERE sha256 = ? AND estado = 'DUPLICADO' ORDER BY id LIMIT 1", (sha,)).fetchone()
    if not f:
        return
    try:
        ruta = _ruta_segura(f["ruta"])
        texto = _extraer_texto(ruta) or ""
    except Exception:
        return
    estado = "INDEXADO" if len(texto.strip()) >= 40 else "SIN_TEXTO"
    with db._conexion() as con:
        con.execute("UPDATE referencia_docs SET estado = ?, texto = ?, palabras = ?, detalle = NULL, indexado_en = ? WHERE id = ?",
                    (estado, texto, len(_RE_PALABRA.findall(texto)), _ahora(), f["id"]))


def escanear_en_segundo_plano(usuario=None):
    threading.Thread(target=lambda: _seguro(escanear, usuario), name="referencia-ia", daemon=True).start()


def _seguro(fn, *a):
    try:
        fn(*a)
    except Exception:
        logging.exception("Error revisando los documentos de referencia de la IA")


# ------------------------------------------------------------------ lo que aprende
_RE_PALABRA = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


def _sin_tildes(t):
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _invalidar():
    _CACHE.update(version=None, terminos=None, indice=None, chequeado=None)


def _version():
    with db._conexion() as con:
        f = con.execute("SELECT COUNT(*) AS n, MAX(indexado_en) AS m, SUM(estado = 'RETIRADO') AS r FROM referencia_docs").fetchone()
    return (f["n"], f["m"], f["r"])


def _textos():
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT id, ruta, carpeta, nombre, texto FROM referencia_docs WHERE estado = 'INDEXADO'")]


def _al_dia():
    """Se fija si cambiaron los documentos (como mucho cada 30 s: la corrección lo consulta párrafo por párrafo)."""
    import time
    ahora = time.monotonic()
    if _CACHE.get("chequeado") and ahora - _CACHE["chequeado"] < 30 and _CACHE["version"] is not None:
        return
    _CACHE["chequeado"] = ahora
    v = _version()
    if _CACHE["version"] != v:
        _CACHE.update(version=v, terminos=None, indice=None)


_DICC = {"sin_tildes": None}


def _diccionario():
    """(conoce(palabra), conjunto de palabras del diccionario sin tildes) o None si no hay corrector instalado."""
    try:
        import nucleo
        spell = nucleo.spell
    except Exception:
        spell = None
    if spell is None:
        return None
    if _DICC["sin_tildes"] is None:
        try:
            _DICC["sin_tildes"] = {_sin_tildes(w) for w in spell.word_frequency.keys()}
        except Exception:
            _DICC["sin_tildes"] = set()
    return (lambda w: bool(spell.known([w])), _DICC["sin_tildes"])


def terminos_institucionales():
    """Palabras de lo firmado que la corrección tiene que respetar: aparecen al menos dos veces en los documentos de
    referencia y el diccionario general no las conoce (nombres propios, siglas, lugares, palabras del organismo).
    No entran las palabras comunes escritas sin tilde («pabellon»): esas se siguen corrigiendo."""
    _al_dia()
    if _CACHE["terminos"] is not None:
        return _CACHE["terminos"]
    cuenta = Counter()
    for d in _textos():
        cuenta.update(_RE_PALABRA.findall(d["texto"] or ""))
    dic = _diccionario()
    terminos = set()
    for w, n in cuenta.items():
        if n < MIN_REPETICIONES_TERMINO or len(w) < 2:
            continue
        if dic is None:
            if any(c.isupper() for c in w):
                terminos.add(w)
            continue
        conoce, sin_tildes = dic
        baja = w.lower()
        if conoce(baja):
            continue
        if _sin_tildes(baja) == baja and baja in sin_tildes:
            continue  # palabra común a la que le falta la tilde
        terminos.add(w)
    _CACHE["terminos"] = terminos
    return terminos


_RE_TOKENS = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*|\d+|[^\w]+|_", re.UNICODE)


def respetar_terminos(antes, despues):
    """Deshace los cambios que la corrección automática hizo sobre términos institucionales (los deja como estaban).
    Devuelve el texto corregido sin esos cambios."""
    if antes == despues or not antes:
        return despues
    try:
        terminos = terminos_institucionales()
    except Exception:
        return despues
    if not terminos:
        return despues
    from difflib import SequenceMatcher
    a, b = _RE_TOKENS.findall(antes), _RE_TOKENS.findall(despues)
    if "".join(a) != antes or "".join(b) != despues:
        return despues
    salida = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            salida += a[i1:i2]
            continue
        palabras = [t for t in a[i1:i2] if _RE_PALABRA.fullmatch(t)]
        if palabras and all(t in terminos for t in palabras) and tag in ("replace", "delete"):
            salida += a[i1:i2]  # se respeta como figura en lo firmado
        else:
            salida += b[j1:j2]
    return "".join(salida)


def _terminos_doc(texto):
    return [_sin_tildes(w.lower()) for w in _RE_PALABRA.findall(texto or "") if len(w) >= 4 and w.lower() not in _STOP]


def _indice():
    _al_dia()
    if _CACHE["indice"] is not None:
        return _CACHE["indice"]
    docs = _textos()
    df = Counter()
    tfs = []
    for d in docs:
        tf = Counter(_terminos_doc(d["texto"]))
        tfs.append(tf)
        df.update(tf.keys())
    n = len(docs) or 1
    idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
    vectores = []
    for d, tf in zip(docs, tfs):
        v = {t: (1 + math.log(c)) * idf[t] for t, c in tf.items()}
        norma = math.sqrt(sum(x * x for x in v.values())) or 1
        vectores.append((d, {t: x / norma for t, x in v.items()}))
    _CACHE["indice"] = {"idf": idf, "vectores": vectores}
    return _CACHE["indice"]


def parecidos(texto, n=5, minimo=0.12):
    """Los documentos firmados más parecidos: [{"id", "nombre", "carpeta", "ruta", "parecido" (0-1)}]."""
    ind = _indice()
    if not ind["vectores"] or not (texto or "").strip():
        return []
    tf = Counter(_terminos_doc(texto))
    q = {t: (1 + math.log(c)) * ind["idf"].get(t, 0) for t, c in tf.items() if t in ind["idf"]}
    norma = math.sqrt(sum(x * x for x in q.values())) or 1
    q = {t: x / norma for t, x in q.items()}
    puntajes = []
    for d, v in ind["vectores"]:
        s = sum(x * v.get(t, 0) for t, x in q.items())
        if s >= minimo:
            puntajes.append((s, d))
    puntajes.sort(key=lambda x: -x[0])
    return [{"id": d["id"], "nombre": d["nombre"], "carpeta": d["carpeta"], "ruta": d["ruta"], "parecido": round(s, 2)}
            for s, d in puntajes[:n]]


def sugerir_carpeta(texto):
    """Propone de qué carpeta sería el documento según los firmados más parecidos (voto de los 5 más cercanos).
    Devuelve {"carpeta", "votos", "de", "parecidos"} o None."""
    cercanos = parecidos(texto, n=5)
    if not cercanos:
        return None
    votos = Counter()
    for c in cercanos:
        votos[c["carpeta"] or "(carpeta principal)"] += c["parecido"]
    carpeta, _ = votos.most_common(1)[0]
    cuantos = sum(1 for c in cercanos if (c["carpeta"] or "(carpeta principal)") == carpeta)
    return {"carpeta": carpeta, "votos": cuantos, "de": len(cercanos), "parecidos": cercanos}


def ejemplo_de_estilo(texto, max_chars=1200):
    """El comienzo del documento firmado más parecido, para darle a la IA el tono habitual (no sus datos)."""
    cercanos = parecidos(texto, n=1, minimo=0.2)
    if not cercanos:
        return None
    with db._conexion() as con:
        f = con.execute("SELECT texto FROM referencia_docs WHERE id = ?", (cercanos[0]["id"],)).fetchone()
    return (f["texto"] or "")[:max_chars] if f else None


# ------------------------------------------------------------------ para la pantalla
def arbol():
    """Carpetas (con subcarpetas) y cuántos documentos usables tiene cada una (incluidas sus subcarpetas)."""
    CARPETA.mkdir(parents=True, exist_ok=True)
    with db._conexion() as con:
        cuenta = Counter(f["carpeta"] for f in con.execute("SELECT carpeta FROM referencia_docs WHERE estado = 'INDEXADO'"))

    def nodo(ruta):
        rel = rel_de(ruta)
        hijos = sorted((p for p in ruta.iterdir() if p.is_dir() and not p.name.startswith((".", RETIRADOS))), key=lambda p: p.name.lower())
        hijos = [nodo(h) for h in hijos]
        propios = cuenta.get(rel, 0)
        return {"nombre": ruta.name if rel else "Documentos de referencia", "ruta": rel, "propios": propios,
                "total": propios + sum(h["total"] for h in hijos), "hijos": hijos}
    return nodo(CARPETA)


def contenido(carpeta_rel):
    base = _ruta_segura(carpeta_rel)
    if not base.is_dir():
        raise ErrorReferencia("Esa carpeta no existe.")
    rel = rel_de(base)
    with db._conexion() as con:
        docs = [dict(f) for f in con.execute(
            """SELECT id, ruta, nombre, tamano, palabras, estado, detalle, agregado_por, agregado_en, indexado_en
               FROM referencia_docs WHERE carpeta = ? AND estado NOT IN ('RETIRADO', 'ELIMINADO', 'AUSENTE') ORDER BY nombre COLLATE NOCASE""", (rel,))]
    subcarpetas = sorted((p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith((".", RETIRADOS))), key=str.lower)
    return {"ruta": rel, "partes": rel.split("/") if rel else [], "subcarpetas": subcarpetas, "documentos": docs}


def resumen():
    with db._conexion() as con:
        filas = {f["estado"]: f["n"] for f in con.execute("SELECT estado, COUNT(*) AS n FROM referencia_docs GROUP BY estado")}
        registro = [dict(f) for f in con.execute("SELECT * FROM referencia_registro ORDER BY id DESC LIMIT 25")]
    try:
        terminos = sorted(terminos_institucionales(), key=str.lower)
    except Exception:
        terminos = []
    carpetas = sum(1 for p in CARPETA.rglob("*") if p.is_dir() and RETIRADOS not in p.relative_to(CARPETA).parts) if CARPETA.is_dir() else 0
    malos, _ = a_limpiar(True)
    return {"usables": filas.get("INDEXADO", 0), "por_estado": filas, "carpetas": carpetas, "terminos": terminos,
            "ilegibles": sum(1 for m in malos if m["estado"] in NO_SIRVEN), "repetidos": sum(1 for m in malos if m["estado"] == "DUPLICADO"),
            "vacias": len(carpetas_vacias()), "criterios": CRITERIOS,
            "registro": registro, "escaneando": ESTADO["escaneando"], "ultimo": ESTADO["ultimo"], "carpeta_disco": str(CARPETA)}


# ------------------------------------------------------------------ eliminar y ordenar (son copias solo para la IA)
NO_SIRVEN = ("SIN_TEXTO", "ERROR")


def eliminar(doc_id, usuario=None):
    """Borra el archivo de verdad (es una copia para la IA). Queda la constancia en el registro."""
    d = obtener(doc_id)
    if not d or d["estado"] == "ELIMINADO":
        raise ErrorReferencia("El documento ya no está.")
    try:
        ruta = _ruta_segura(d["ruta"])
        if ruta.is_file():
            ruta.unlink()
    except ErrorReferencia:
        pass
    with db._conexion() as con:
        con.execute("UPDATE referencia_docs SET estado = 'ELIMINADO', texto = NULL, detalle = ? WHERE id = ?",
                    (f"Eliminado por {usuario or db._quien()} el {_ahora()[:10]}", doc_id))
    if d["estado"] == "INDEXADO" and d.get("sha256"):
        _promover_repetido(d["sha256"])
    _registrar("ELIMINAR", f"Eliminado: {d['ruta']}", usuario)
    _invalidar()
    return d


def _vacia(carpeta):
    for p in carpeta.iterdir():
        if p.is_dir():
            return False
        if p.name.lower() not in ("desktop.ini", "thumbs.db", ".ds_store"):
            return False
    return True


def carpetas_vacias():
    """Carpetas sin ningún documento (ni en sus subcarpetas), de la más profunda a la menos profunda."""
    if not CARPETA.is_dir():
        return []
    salida, con_algo = [], set()
    todas = sorted((p for p in CARPETA.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for p in todas:
        archivos = [x for x in p.iterdir() if x.is_file() and x.name.lower() not in ("desktop.ini", "thumbs.db", ".ds_store")]
        hijas_con_algo = any(h in con_algo for h in p.iterdir() if h.is_dir())
        if archivos or hijas_con_algo:
            con_algo.add(p)
        else:
            salida.append(p)
    return salida


def eliminar_carpetas_vacias(usuario=None):
    n = 0
    for p in carpetas_vacias():
        try:
            for x in p.iterdir():  # desktop.ini, thumbs.db
                if x.is_file():
                    x.unlink()
            p.rmdir()
            n += 1
        except OSError:
            logging.info(f"No se pudo borrar la carpeta vacía {p}")
    if n:
        _registrar("CARPETAS", f"{n} carpeta(s) vacía(s) eliminada(s)", usuario)
    return n


def a_limpiar(incluir_repetidos=True):
    estados = NO_SIRVEN + (("DUPLICADO",) if incluir_repetidos else ())
    with db._conexion() as con:
        filas = [dict(f) for f in con.execute(
            "SELECT id, ruta, nombre, estado FROM referencia_docs WHERE estado IN (%s) ORDER BY ruta" % ",".join("?" * len(estados)), estados)]
        ausentes = con.execute("SELECT COUNT(*) AS n FROM referencia_docs WHERE estado = 'AUSENTE'").fetchone()["n"]
    return filas, ausentes


def limpiar(incluir_repetidos=True, usuario=None):
    """Elimina de una vez los archivos que no se pueden leer (y los repetidos, si se pide), olvida los que ya no están
    en la carpeta y borra las carpetas que quedaron vacías. Devuelve {"archivos", "carpetas"}."""
    filas, _ = a_limpiar(incluir_repetidos)
    for f in filas:
        try:
            eliminar(f["id"], usuario)
        except ErrorReferencia:
            pass
    with db._conexion() as con:
        con.execute("UPDATE referencia_docs SET estado = 'ELIMINADO', texto = NULL WHERE estado = 'AUSENTE'")
    carpetas = eliminar_carpetas_vacias(usuario)
    _registrar("LIMPIEZA", f"{len(filas)} archivo(s) que no servían y {carpetas} carpeta(s) vacía(s) eliminados", usuario)
    return {"archivos": len(filas), "carpetas": carpetas}


CRITERIOS = {
    "tipo": "Tipo de documento (Nota, Oficio, Informe, Proveído…)",
    "tema": "Tema (salud, traslados, alojamiento…)",
    "anio": "Año del documento",
    "tipo_anio": "Tipo de documento y, dentro, año",
    "tema_tipo": "Tema y, dentro, tipo de documento",
}
_MESES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_RE_FECHA = re.compile(r"\b\d{1,2}\s+de\s+(?:%s)\s+(?:de(?:l)?\s+)?((?:19|20)\d{2})\b|\b\d{1,2}[/.-]\d{1,2}[/.-]((?:19|20)\d{2})\b" % _MESES, re.I)


def _anio(texto, ruta):
    m = _RE_FECHA.search(texto or "")
    if m:
        return m.group(1) or m.group(2)
    try:
        return str(datetime.fromtimestamp(_ruta_segura(ruta).stat().st_mtime).year)
    except Exception:
        return "Sin fecha"


def _carpeta_segun(criterio, d):
    import analisis
    import tipos_documentales
    texto = d.get("texto") or ""
    tipo = tipos_documentales.ETIQUETA.get(tipos_documentales.proponer(texto, d["nombre"])[0], "Nota")
    tema = (analisis.clasificar_por_palabras(texto)[0] or "General").replace("_", " ")
    anio = _anio(texto, d["ruta"])
    partes = {"tipo": [tipo], "tema": [tema], "anio": [anio], "tipo_anio": [tipo, anio], "tema_tipo": [tema, tipo]}[criterio]
    return "/".join(_PROHIBIDOS.sub("-", str(p)).strip(" .") or "Otros" for p in partes)


def propuesta_reorden(criterio):
    """Cómo quedaría todo ordenado según el criterio: {"destinos": [(carpeta, cantidad)], "movimientos": [...], "quedan": n}."""
    if criterio not in CRITERIOS:
        raise ErrorReferencia("Elegí un criterio.")
    with db._conexion() as con:
        docs = [dict(f) for f in con.execute("SELECT id, ruta, carpeta, nombre, texto FROM referencia_docs WHERE estado = 'INDEXADO' ORDER BY ruta")]
    movimientos, cuenta = [], Counter()
    for d in docs:
        destino = _carpeta_segun(criterio, d)
        cuenta[destino] += 1
        movimientos.append({"id": d["id"], "nombre": d["nombre"], "desde": d["carpeta"] or "(principal)", "hacia": destino,
                            "se_mueve": (d["carpeta"] or "") != destino})
    return {"criterio": criterio, "etiqueta": CRITERIOS[criterio], "destinos": sorted(cuenta.items()),
            "movimientos": movimientos, "se_mueven": sum(1 for m in movimientos if m["se_mueve"])}


def aplicar_reorden(criterio, usuario=None):
    """Mueve cada documento en uso a la carpeta que le corresponde según el criterio y borra las carpetas que quedan vacías."""
    p = propuesta_reorden(criterio)
    movidos = 0
    for m in p["movimientos"]:
        if not m["se_mueve"]:
            continue
        d = obtener(m["id"])
        origen = _ruta_segura(d["ruta"])
        if not origen.is_file():
            continue
        carpeta = _ruta_segura(m["hacia"])
        carpeta.mkdir(parents=True, exist_ok=True)
        destino = _nombre_libre(carpeta, d["nombre"])
        shutil.move(str(origen), str(destino))
        st = destino.stat()
        with db._conexion() as con:
            con.execute("UPDATE referencia_docs SET ruta = ?, carpeta = ?, nombre = ?, mtime = ?, tamano = ? WHERE id = ?",
                        (rel_de(destino), rel_de(destino.parent), destino.name, st.st_mtime, st.st_size, m["id"]))
        movidos += 1
    carpetas = eliminar_carpetas_vacias(usuario)
    _registrar("REORDEN", f"Reordenado por {CRITERIOS[criterio].lower()}: {movidos} documento(s) movido(s), {carpetas} carpeta(s) vacía(s) eliminada(s)", usuario)
    _invalidar()
    return {"movidos": movidos, "carpetas": carpetas}
