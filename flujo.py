"""
flujo.py — Las 4 etapas del proceso, tal como las ve el usuario.

Son distintas de los "estados" internos de la base (SUBIDA, PROCESANDO,
PROCESADA, FIRMADA, DESPACHADA, ERROR): los estados internos son técnicos
y sirven para saber qué hacer con cada nota; las etapas son la versión
simplificada que se muestra en pantalla, pensada para que cualquiera
entienda de un vistazo dónde está su documento.
"""

ETAPAS = [
    {
        "clave": "REVISION_INICIAL",
        "numero": 1,
        "nombre": "Revisión inicial",
        "descripcion": "El asesor subió el documento y se están aplicando las correcciones.",
    },
    {
        "clave": "A_FIRMA_DGPDH",
        "numero": 2,
        "nombre": "Nota a la firma de DGPDH",
        "descripcion": "El documento ya fue corregido y está pendiente de la firma del Director General.",
    },
    {
        "clave": "FIRMADA_A_DESPACHO",
        "numero": 3,
        "nombre": "Nota firmada a despacho",
        "descripcion": "El documento ya fue firmado y está en camino a despacho.",
    },
    {
        "clave": "DESPACHADA",
        "numero": 4,
        "nombre": "Nota despachada",
        "descripcion": "El documento fue despachado.",
    },
]

# Los estados internos que representa cada etapa. PROCESANDO se agrupa
# dentro de "Revisión inicial" porque, de cara al usuario, sigue siendo la
# misma etapa mientras el documento todavía no está listo para firma.
_MAPA_ESTADO_A_ETAPA = {
    "BORRADOR": "REVISION_INICIAL",
    "SUBIDA": "REVISION_INICIAL",
    "PROCESANDO": "REVISION_INICIAL",
    "EN_REVISION": "REVISION_INICIAL",
    "PROCESADA": "A_FIRMA_DGPDH",
    "FIRMADA": "FIRMADA_A_DESPACHO",
    "DESPACHADA": "DESPACHADA",
    # PDFs: A_CONFIRMAR es "aprobado por Coordinación, esperando confirmación de Dirección"
    "A_CONFIRMAR": "FIRMADA_A_DESPACHO",
    # "ERROR" queda fuera del flujo a propósito: no es una etapa, es una
    # excepción que hay que resolver antes de que el documento pueda seguir.
}


def etapa_para_estado(estado):
    """Devuelve el dict de la etapa correspondiente a un estado interno,
    o None si ese estado no forma parte del flujo visible (por ejemplo,
    ERROR)."""
    clave = _MAPA_ESTADO_A_ETAPA.get(estado)
    if clave is None:
        return None
    return next(e for e in ETAPAS if e["clave"] == clave)
