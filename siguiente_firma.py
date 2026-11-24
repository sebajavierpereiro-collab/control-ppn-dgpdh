"""
siguiente_firma.py — Lógica de selección del siguiente documento para firmar.

Criterio: 1) mismo bloque  2) relacionadas  3) prioridad  4) antigüedad.
Todo son funciones puras (sin I/O ni acceso a la base); el caller les pasa
las listas de notas ya cargadas.
"""
from __future__ import annotations
import re
import unicodedata
from datetime import datetime

VENTANA_RELACION_MIN = 120
_RANGO = {"URGENTE": 0, "PRIORITARIA": 1, "NORMAL": 2}


def _leer_config_ventana() -> int:
    try:
        import configparser, os
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(os.path.dirname(__file__), "config.ini"))
        return int(cfg.get("firma", "ventana_relacion_minutos"))
    except Exception:
        return VENTANA_RELACION_MIN


def normalizar_persona(nombre_archivo: str) -> str | None:
    """Primer segmento antes del primer '-', normalizado (mayúsculas, sin tildes).
    Devuelve None si el nombre no tiene ese formato."""
    partes = nombre_archivo.split("-", 1)
    if len(partes) < 2:
        return None
    nombre = partes[0].strip()
    if not nombre:
        return None
    nombre = nombre.upper()
    nombre = unicodedata.normalize("NFD", nombre)
    nombre = "".join(c for c in nombre if unicodedata.category(c) != "Mn")
    nombre = re.sub(r"[^A-Z0-9 ]", "", nombre)
    nombre = re.sub(r"\s+", " ", nombre).strip()
    return nombre or None


def normalizar_asunto(nombre_archivo: str) -> str | None:
    """Último segmento después del último '-' (sin extensión), normalizado."""
    base = re.sub(r"\.[a-zA-Z0-9]{1,5}$", "", nombre_archivo)
    partes = base.split("-")
    if len(partes) < 2:
        return None
    asunto = partes[-1].strip()
    asunto = asunto.upper()
    asunto = unicodedata.normalize("NFD", asunto)
    asunto = "".join(c for c in asunto if unicodedata.category(c) != "Mn")
    asunto = re.sub(r"[^A-Z0-9 ]", "", asunto)
    asunto = re.sub(r"\s+", " ", asunto).strip()
    return asunto or None


def es_proveido(nombre_archivo: str) -> bool:
    """True si el nombre contiene 'proveido' / 'proveído' (sin tildes)."""
    base = nombre_archivo.lower()
    base = unicodedata.normalize("NFD", base)
    base = "".join(c for c in base if unicodedata.category(c) != "Mn")
    return "proveido" in base


def _parse_dt(s: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt)
        except (ValueError, TypeError):
            continue
    return None


def son_relacionadas(a: dict, b: dict, ventana_min: int = VENTANA_RELACION_MIN) -> bool:
    """True si a y b son del mismo trámite: misma persona + al menos una condición."""
    pa = normalizar_persona(a.get("nombre_original", ""))
    pb = normalizar_persona(b.get("nombre_original", ""))
    if not pa or not pb or pa != pb:
        return False
    # (a) mismo usuario subidor
    if a.get("usuario_id") and b.get("usuario_id") and a["usuario_id"] == b["usuario_id"]:
        return True
    # (b) diferencia < ventana_min minutos
    ta, tb = a.get("creado_en") or "", b.get("creado_en") or ""
    if ta and tb:
        dta, dtb = _parse_dt(ta), _parse_dt(tb)
        if dta and dtb and abs((dta - dtb).total_seconds()) / 60 < ventana_min:
            return True
    # (c) mismo asunto
    aa = normalizar_asunto(a.get("nombre_original", ""))
    ab = normalizar_asunto(b.get("nombre_original", ""))
    if aa and ab and aa == ab:
        return True
    return False


def _orden_en_grupo(nota: dict) -> tuple:
    """Notas primero, proveído al final; dentro de cada tipo, creado_en e id."""
    return (
        1 if es_proveido(nota.get("nombre_original", "")) else 0,
        nota.get("creado_en", ""),
        nota.get("id", 0),
    )


def relacionadas_pendientes(nota: dict, pendientes: list, ventana_min: int | None = None) -> list:
    """Notas de `pendientes` relacionadas con `nota` (excluye la propia), ordenadas."""
    vm = ventana_min if ventana_min is not None else _leer_config_ventana()
    return sorted(
        [n for n in pendientes if n["id"] != nota.get("id") and son_relacionadas(nota, n, vm)],
        key=_orden_en_grupo,
    )


def siguiente_para_firmar(
    nota_firmada: dict,
    pendientes: list,
    salteadas: set,
    ventana_min: int | None = None,
) -> tuple[int | None, str]:
    """
    Devuelve (id_siguiente, motivo) | (None, "") si no quedan pendientes.

    Orden: 1) mismo bloque  2) relacionada sin bloque  3) prioridad  4) antigüedad.

    `pendientes`: notas PROCESADA visibles al usuario, ya re-validadas.
    `salteadas`: set de nota_ids salteados en esta tanda (nunca se ofrecen de nuevo).
    """
    vm = ventana_min if ventana_min is not None else _leer_config_ventana()
    candidatos = [
        n for n in pendientes
        if n.get("id") != nota_firmada.get("id") and n.get("id") not in salteadas
    ]
    if not candidatos:
        return None, ""

    # 1. Mismo bloque
    bloque_id = nota_firmada.get("bloque_id")
    if bloque_id:
        en_bloque = [n for n in candidatos if n.get("bloque_id") == bloque_id]
        if en_bloque:
            sig = min(en_bloque, key=lambda m: (m.get("creado_en", ""), m.get("id", 0)))
            return sig["id"], "mismo bloque"

    # 2. Relacionadas sin bloque (no mezclar con notas de otros bloques)
    rel = [
        n for n in candidatos
        if not n.get("bloque_id") and son_relacionadas(nota_firmada, n, vm)
    ]
    if rel:
        sig = min(rel, key=_orden_en_grupo)
        return sig["id"], "relacionada"

    # 3+4. Prioridad y antigüedad
    def _clave(m: dict) -> tuple:
        prio = m.get("prioridad_efectiva") or m.get("prioridad") or "NORMAL"
        return (_RANGO.get(prio, 2), m.get("actualizado_en", ""), m.get("id", 0))

    sig = min(candidatos, key=_clave)
    prio = sig.get("prioridad_efectiva") or sig.get("prioridad") or "NORMAL"
    if prio == "URGENTE":
        return sig["id"], "urgente"
    if prio == "PRIORITARIA":
        return sig["id"], "prioritaria"
    return sig["id"], "antigüedad"


def ordenar_con_grupos(notas: list, ventana_min: int | None = None) -> list:
    """
    Versión extendida de _ordenar_para_firma: agrupa también las relacionadas
    (sin bloque) del mismo trámite, del mismo modo que hoy se agrupan los bloques.

    Devuelve las notas ordenadas: bloques y grupos de relacionadas quedan juntos y
    en orden interno (_orden_en_grupo); entre grupos, por prioridad más alta + más antigua.
    """
    vm = ventana_min if ventana_min is not None else _leer_config_ventana()

    # --- Union-Find para agrupar relacionadas (sin bloque) ---
    sin_bloque = [n for n in notas if not n.get("bloque_id")]
    n = len(sin_bloque)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            if son_relacionadas(sin_bloque[i], sin_bloque[j], vm):
                union(i, j)

    grupo_rel: dict[int, list] = {}
    for i in range(n):
        grupo_rel.setdefault(find(i), []).append(sin_bloque[i])

    # --- Construir grupos finales: cada bloque y cada grupo_rel es un "grupo" ---
    grupos: list[list] = []

    # Bloques (ya agrupados por bloque_id)
    por_bloque: dict = {}
    for n_ in notas:
        if n_.get("bloque_id"):
            por_bloque.setdefault(n_["bloque_id"], []).append(n_)
    for miembros in por_bloque.values():
        grupos.append(sorted(miembros, key=lambda m: (m.get("creado_en", ""), m.get("id", 0))))

    # Grupos de relacionadas (ya sin bloque)
    for miembros in grupo_rel.values():
        grupos.append(sorted(miembros, key=_orden_en_grupo))

    # --- Ordenar grupos por (rango_prio_max, actualizado_en_min) ---
    def _clave_grupo(miembros: list) -> tuple:
        rango = min(_RANGO.get(m.get("prioridad_efectiva") or m.get("prioridad") or "NORMAL", 2) for m in miembros)
        mas_antigua = min((m.get("actualizado_en") or "") for m in miembros)
        return (rango, mas_antigua)

    return [m for grupo in sorted(grupos, key=_clave_grupo) for m in grupo]


def abrev_nombre(nombre: str, max_len: int = 35) -> str:
    """Nombre sin extensión, truncado a max_len caracteres."""
    base = re.sub(r"\.[a-zA-Z0-9]{1,5}$", "", nombre)
    return base[:max_len] + "…" if len(base) > max_len else base
