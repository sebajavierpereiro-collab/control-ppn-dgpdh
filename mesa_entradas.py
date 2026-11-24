"""Compatibilidad con Mesa de Entradas PPN (ppn-mesa-entradas): PREPARADO, SIN CONECTAR.

Qué es Mesa de Entradas (según su código, octubre 2026):
  * Un expediente («record») se identifica por TIPO + NÚMERO + AÑO. El número es correlativo por tipo
    (no se reinicia por año) y lo asigna el sistema al crear el expediente.
  * Cada expediente tiene documentos PDF ordenados (el 1 es la carátula), con versiones e historial.
  * No tiene notas, pases, estados ni numeración de notas: eso sigue siendo de este sistema.
  * API: GraphQL en POST {url}/graphql. Buscar: recordsWithFilter(number, year, typeId).
    Adjuntar un PDF: createDocument(title, typeId, recordId, file: Upload).
  * Hoy toda la API exige una sesión de usuario del portal PPN (token JWT + mismo navegador). Para que este
    sistema le hable directamente hace falta que Mesa de Entradas agregue una credencial de servicio (no existe).

Por eso este módulo deja listo, sin red:
  * leer y escribir el número de expediente en el formato de Mesa de Entradas («EP 1234/2025»);
  * la consulta y la carga ya escritas (GraphQL), que solo se usan si se configura [mesa_entradas];
  * el enlace al detalle del expediente en Mesa de Entradas.
"""
import json
import re
import unicodedata
from pathlib import Path

# Tipos de expediente de Mesa de Entradas (EAtomicRecordType en su código): sigla -> (id, nombre)
TIPOS = {
    "AN": (1, "Anexo"), "IP": (2, "Informes de Prensa"), "LP": (3, "Legajos Penitenciarios"),
    "PP": (4, "Privado del Procurador"), "SE": (5, "Sin Expediente"), "NC": (6, "No clasificado"),
    "EF": (7, "Expediente Fallecimiento"), "LS": (8, "Legajo Sótano"), "AP": (9, "Acceso a Información Pública"),
    "BI": (10, "Legajo Bis"), "EA": (11, "Expedientes Administrativos"), "EP": (12, "Temáticos y de Unidades"),
    "ET": (13, "Tortura y Malos Tratos"),
}

URL = ""          # backend de Mesa de Entradas (sin / final)
URL_WEB = ""      # dirección que ve la gente (para el enlace al detalle)
TOKEN = ""        # credencial de servicio, el día que exista
TIEMPO_ESPERA = 15


def configurar(config):
    global URL, URL_WEB, TOKEN
    URL = (config.get("mesa_entradas", "url", fallback="") or "").strip().rstrip("/")
    URL_WEB = (config.get("mesa_entradas", "url_web", fallback="") or "").strip().rstrip("/")
    TOKEN = (config.get("mesa_entradas", "token_servicio", fallback="") or "").strip()


def activo():
    return bool(URL and TOKEN)


def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn")


_PATRON = re.compile(
    r"""^\s*(?:expte\.?|expediente)?\s*(?:n[°ºo.]*)?\s*
        (?:(?P<t1>[a-z]{2})\s*[-\s.]?\s*)?            # sigla adelante: EP 1234/2025
        (?:n[°ºo.]*\s*)?
        (?P<num>\d{1,7})\s*[/\-.]\s*(?P<anio>\d{2}|\d{4})
        (?:\s*[-\s]\s*(?P<t2>[a-z]{2}))?\s*$          # o sigla al final: 1234/2025 EP
    """, re.X | re.I)


def parsear(texto):
    """'EP 1234/2025', 'EP-1234-25', 'Expte. EA N° 12/2024', '1234/2025 EP' -> {'tipo','numero','anio'} o None.
    El tipo es opcional (None si no vino); un tipo que Mesa de Entradas no tiene devuelve None."""
    t = _sin_acentos(str(texto or "")).strip()[:80]  # un número de expediente es corto: evita textos enormes
    m = _PATRON.match(t)
    if not m:
        return None
    tipo = (m.group("t1") or m.group("t2") or "").upper() or None
    if tipo and tipo not in TIPOS:
        return None
    anio = int(m.group("anio"))
    if anio < 100:
        anio += 2000 if anio <= 69 else 1900
    return {"tipo": tipo, "numero": int(m.group("num")), "anio": anio}


def formatear(ref):
    """{'tipo','numero','anio'} -> 'EP 1234/2025' (sin tipo: '1234/2025')."""
    if not ref:
        return ""
    base = f"{ref['numero']}/{ref['anio']}"
    return f"{ref['tipo']} {base}" if ref.get("tipo") else base


def compatibilidad(numeros):
    """Cuántos números de expediente cargados acá ya se pueden leer en el formato de Mesa de Entradas."""
    completos, sin_tipo, ilegibles = 0, 0, []
    for n in numeros:
        r = parsear(n)
        if r and r["tipo"]:
            completos += 1
        elif r:
            sin_tipo += 1
        else:
            ilegibles.append(n)
    return {"total": len(numeros), "completos": completos, "sin_tipo": sin_tipo, "ilegibles": ilegibles[:20],
            "n_ilegibles": len(ilegibles)}


def enlace_detalle(record_id):
    return f"{URL_WEB}/dashboard/expediente/detalle/{record_id}" if URL_WEB and record_id else ""


# ---------------------------------------------------------------------
# Consultas ya escritas (solo se usan si se configura [mesa_entradas] y existe la credencial de servicio)
# ---------------------------------------------------------------------
CONSULTA_BUSCAR = """query Buscar($number: Int, $year: Int, $typeId: Int) {
  recordsWithFilter(number: $number, year: $year, typeId: $typeId) {
    id number year cover openingDate observations type { id name abbreviation }
  }
}"""

MUTACION_ADJUNTAR = """mutation Adjuntar($title: String!, $typeId: Int!, $recordId: Int!, $file: Upload!) {
  createDocument(title: $title, typeId: $typeId, recordId: $recordId, file: $file)
}"""

TIPO_DOCUMENTO_SIN_ASIGNAR = 1


def _cabeceras():
    return {"authorization": TOKEN, "Accept": "application/json"}


def buscar(ref, sesion=None):
    """Expedientes de Mesa de Entradas que coinciden con la referencia (lista; vacía si no hay)."""
    if not activo():
        raise RuntimeError("Mesa de Entradas no está configurada.")
    import requests
    s = sesion or requests
    variables = {"number": ref["numero"], "year": ref["anio"], "typeId": TIPOS[ref["tipo"]][0] if ref.get("tipo") else None}
    r = s.post(URL + "/graphql", json={"query": CONSULTA_BUSCAR, "variables": variables}, headers=_cabeceras(), timeout=TIEMPO_ESPERA)
    r.raise_for_status()
    datos = r.json()
    if datos.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "") for e in datos["errors"]))
    return (datos.get("data") or {}).get("recordsWithFilter") or []


def adjuntar_pdf(record_id, ruta_pdf, titulo, sesion=None):
    """Sube un PDF como documento del expediente (especificación multipart de GraphQL para archivos)."""
    if not activo():
        raise RuntimeError("Mesa de Entradas no está configurada.")
    import requests
    s = sesion or requests
    ruta = Path(ruta_pdf)
    operaciones = {"query": MUTACION_ADJUNTAR, "variables": {"title": titulo, "typeId": TIPO_DOCUMENTO_SIN_ASIGNAR,
                                                            "recordId": int(record_id), "file": None}}
    with ruta.open("rb") as f:
        r = s.post(URL + "/graphql", headers=dict(_cabeceras(), **{"apollo-require-preflight": "true"}), timeout=TIEMPO_ESPERA * 4,
                   data={"operations": json.dumps(operaciones), "map": json.dumps({"0": ["variables.file"]})},
                   files={"0": (ruta.name, f, "application/pdf")})
    r.raise_for_status()
    datos = r.json()
    if datos.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "") for e in datos["errors"]))
    return bool((datos.get("data") or {}).get("createDocument"))
