"""Plazos y semáforo. Si nadie fijó un vencimiento, se usa un plazo por prioridad
contado desde que se subió el documento (días corridos)."""
from datetime import date, datetime, timedelta

PLAZOS_DIAS = {"URGENTE": 2, "PRIORITARIA": 5, "NORMAL": 10}
ESTADOS_CON_PLAZO = ("SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA", "FIRMADA", "PARA_REMITIR", "ERROR")


def vencimiento(nota):
    if nota.get("vence"):
        return date.fromisoformat(nota["vence"][:10])
    base = date.fromisoformat(str(nota["creado_en"])[:10])
    return base + timedelta(days=PLAZOS_DIAS.get(nota.get("prioridad") or "NORMAL", 10))


def semaforo(nota, hoy=None):
    """{'nivel': vencida|rojo|ambar|verde, 'dias', 'vence', 'texto', 'fijado'} o None."""
    if nota.get("estado") not in ESTADOS_CON_PLAZO:
        return None
    hoy = hoy or date.today()
    v = vencimiento(nota)
    dias = (v - hoy).days
    if dias < 0:
        nivel, texto = "vencida", f"Vencida hace {-dias} d"
    elif dias == 0:
        nivel, texto = "rojo", "Vence hoy"
    elif dias == 1:
        nivel, texto = "rojo", "Vence mañana"
    elif dias <= 3:
        nivel, texto = "ambar", f"Vence en {dias} d"
    else:
        nivel, texto = "verde", f"Vence en {dias} d"
    return {"nivel": nivel, "dias": dias, "vence": v.isoformat(), "texto": texto, "fijado": bool(nota.get("vence"))}


# ---------------------------------------------------------------- Ola 6 (109): plazo de respuesta explícito
ESTADOS_RESPUESTA = {"en_termino": "En término", "pendiente": "En término", "fuera_de_termino": "Fuera de término",
                     "vencido": "Vencido", "sin_plazo": "Sin plazo"}
CLASE_RESPUESTA = {"en_termino": "ok", "pendiente": "ok", "fuera_de_termino": "aviso", "vencido": "alto", "sin_plazo": "neutra"}


def _dm(iso):
    return f"{iso[8:10]}/{iso[5:7]}"


def estado_respuesta(nota, fecha_respuesta=None, hoy=None):
    """Etiqueta del plazo de respuesta de una nota despachada (o de una respuesta puntual, con su fecha de recepción):
    en término / fuera de término / vencido / sin plazo. Devuelve {'clave','texto','detalle','clase'} o None si no corresponde."""
    if nota.get("estado") != "DESPACHADA" and not fecha_respuesta:
        return None
    hoy = (hoy or date.today()).isoformat() if not isinstance(hoy, str) else hoy
    vence = (nota.get("respuesta_vence") or "")[:10]
    recibida = (fecha_respuesta or nota.get("respuesta_recibida_en") or "")[:10]
    if not nota.get("plazo_respuesta") and not vence:
        clave, detalle = "sin_plazo", "La nota no fija plazo de respuesta"
    elif not vence:
        clave, detalle = "pendiente", "Con plazo; el vencimiento se calcula desde el despacho"
    elif recibida:
        clave = "en_termino" if recibida <= vence else "fuera_de_termino"
        detalle = f"Recibida el {_dm(recibida)}; vencía el {_dm(vence)}"
    elif hoy > vence:
        clave, detalle = "vencido", f"Venció el {_dm(vence)} sin respuesta"
    else:
        clave, detalle = "pendiente", f"Esperando respuesta; vence el {_dm(vence)}"
    return {"clave": clave, "texto": ESTADOS_RESPUESTA[clave], "detalle": detalle, "clase": CLASE_RESPUESTA[clave]}
