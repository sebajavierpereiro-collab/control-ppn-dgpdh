"""Ola 6 (131): tipo documental ampliado. Las reglas PROPONEN el tipo leyendo el nombre del archivo y el comienzo del texto;
una persona lo confirma o lo cambia en la ficha. No reemplaza al campo «tipo» que usa el circuito (Nota, Nota a PPL,
Proveído, Informe): es un dato aparte que después elige plantilla y fórmulas (Generador de documentos, Ola 9).
"""
import re
import unicodedata

TIPOS = [
    ("NOTA", "Nota"),
    ("NOTA_PPL", "Nota a persona detenida"),
    ("OFICIO", "Oficio"),
    ("PEDIDO_INFORME", "Pedido de informe"),
    ("REQUERIMIENTO", "Requerimiento"),
    ("SOLICITUD", "Solicitud"),
    ("CONTESTACION", "Contestación"),
    ("RESPUESTA_ORGANISMO", "Respuesta a organismo"),
    ("PRESENTACION_JUDICIAL", "Presentación judicial"),
    ("COMUNICACION_UNIDAD", "Comunicación a unidad"),
    ("COMUNICACION_INTERNA", "Comunicación interna"),
    ("INFORME", "Informe"),
    ("PROVEIDO", "Proveído"),
]
ETIQUETA = dict(TIPOS)
_UNIDAD = r"(complejo penitenciario|unidad (penal|residencial|n)|instituto|colonia penal|c\.?p\.?f\.?|servicio penitenciario|alcaid[ií]a|m[oó]dulo)"
_JUDICIAL = r"(juzgado|tribunal|c[aá]mara|v\.?\s?s\.?|s\.?\s?s\.?|excm[oa]|su se[nñ]or[ií]a|juez|jueza)"
_INTERNO = r"(procuraci[oó]n penitenciaria|procurador|coordinaci[oó]n|direcci[oó]n general de protecci[oó]n|[aá]rea de|departamento de|subdirecci[oó]n)"


def _norm(t):
    t = unicodedata.normalize("NFD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t)


def proponer(texto, nombre="", tipo_circuito=None):
    """Devuelve (clave, motivo). Reglas en orden: lo más específico primero; ante la duda, «Nota»."""
    n, t = _norm(nombre), _norm((texto or "")[:2500])
    inicio = t[:400]
    if tipo_circuito == "PROVEIDO" or "proveido" in n:
        return "PROVEIDO", "el sistema ya lo identificó como proveído"
    if tipo_circuito == "INFORME" or "informe" in n and "pedido" not in n:
        return "INFORME", "el nombre o el comienzo dicen «informe»"
    if tipo_circuito == "NOTA A PPL":
        return "NOTA_PPL", "dirigida a la persona detenida"
    if "oficio" in n or re.match(r"^\s*oficio\b", t):
        return "OFICIO", "el nombre o el encabezado dicen «oficio»"
    if re.search(r"\b(en respuesta a|en contestaci[oó]n|contestando|respondiendo a|me dirijo .{0,40} a fin de responder)", t):
        return ("CONTESTACION" if re.search(_JUDICIAL, inicio) else "RESPUESTA_ORGANISMO"), "dice «en respuesta a» o «en contestación»"
    if re.search(_JUDICIAL, inicio) and re.search(r"\b(vengo a|se presenta|presento|solicito a v|solicito a s)", t):
        return "PRESENTACION_JUDICIAL", "va dirigida a un juzgado o tribunal y se presenta ante él"
    if re.search(r"\b(se requiere|requiero|requerimos|intimase|se intima|emplazo)\b", t):
        return "REQUERIMIENTO", "usa «se requiere» o «se intima»"
    if re.search(r"\b(solicit\w* .{0,60}informe|informe .{0,40}(sobre|acerca)|tenga a bien informar|se sirva informar|informar a este organismo)", t):
        return "PEDIDO_INFORME", "pide que se informe"
    if re.search(_INTERNO, inicio) and not re.search(_UNIDAD, inicio):
        return "COMUNICACION_INTERNA", "va dirigida a un área del propio organismo"
    if re.search(_UNIDAD, inicio):
        return "COMUNICACION_UNIDAD", "va dirigida a una unidad o complejo"
    if re.search(r"\b(solicito|se solicita|solicitamos)\b", t):
        return "SOLICITUD", "usa «solicito» o «se solicita»"
    return "NOTA", "no hay señales de otro tipo"


def de_nota(nota, texto=None):
    """{'clave','etiqueta','confirmado','motivo','por'} para la ficha: lo confirmado, o la propuesta de las reglas."""
    if nota.get("tipo_documental"):
        return {"clave": nota["tipo_documental"], "etiqueta": ETIQUETA.get(nota["tipo_documental"], nota["tipo_documental"]),
                "confirmado": True, "motivo": "", "por": nota.get("tipo_documental_por")}
    if texto is None:
        return None
    clave, motivo = proponer(texto, nota.get("nombre_original", ""), nota.get("tipo"))
    return {"clave": clave, "etiqueta": ETIQUETA[clave], "confirmado": False, "motivo": motivo, "por": None}
