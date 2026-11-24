"""Puente entre las notas (módulos históricos) y la clasificación/protocolo del paquete dgpdh (reforma 14/11/2026).

- `de_nota(nota)`: lo confirmado o, si falta, la propuesta del sistema (tipo documental, subtipo, alcance, confidencialidad,
  expediente y destinatario), para la ficha.
- `guardar(nota_id, datos, usuario)`: valida y guarda lo que confirma la persona (queda en la auditoría).
- `confirmar_propuesta(nota, usuario)`: al firmar un documento que llegó sin clasificar (anterior a la reforma), quien firma
  confirma la propuesta del sistema y queda registrado.
"""
import re

import database as db
import despacho
import tipos_documentales
from dgpdh.dominio import clasificacion as cl
from dgpdh.seguridad import auditoria as au

ETIQUETA_CONFIDENCIALIDAD = cl.CONFIDENCIALIDAD
ETIQUETA_ALCANCE = cl.ALCANCE
SUBTIPOS = cl.SUBTIPOS
_DNI = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}\b")


def confirmada(nota):
    return bool(nota and nota.get("clasificado_en"))


def es_informe(nota):
    """La clasificación obligatoria (al enviar y al firmar) es solo para los INFORMEs. Lo confirmado manda; si no hay
    nada confirmado se usa la misma regla que la propuesta del sistema (tipo del circuito o nombre del archivo), así la
    ficha y el envío coinciden."""
    if not nota:
        return False
    if nota.get("tipo_documental"):
        return nota["tipo_documental"] == "INFORME"
    return tipos_documentales.proponer("", nota.get("nombre_original") or "", nota.get("tipo"))[0] == "INFORME"


def _texto(nota):
    """Texto del documento: el índice de búsqueda o, si todavía no se indexó (recién subido), el propio .docx."""
    try:
        t = db.texto_indexado(nota["id"]) or ""
    except Exception:
        t = ""
    if t.strip():
        return t
    from pathlib import Path
    for clave in ("ruta_resultado", "ruta_base", "ruta_original"):
        ruta = nota.get(clave)
        if ruta and str(ruta).lower().endswith(".docx") and Path(ruta).is_file():
            try:
                from docx import Document
                return "\n".join(p.text for p in Document(str(ruta)).paragraphs)
            except Exception:
                continue
    return ""


def proponer(nota, texto=None):
    texto = _texto(nota) if texto is None else texto
    nombre = nota.get("nombre_original") or ""
    td = tipos_documentales.de_nota(nota, texto) or {"clave": "NOTA"}
    tipo = td["clave"]
    base = (nombre + " " + texto[:3000]).lower()
    subtipo = None
    if tipo in cl.SUBTIPOS:
        subtipo = "OBSTACULIZACION" if "obstaculiz" in base else next(iter(cl.SUBTIPOS[tipo]))
    alcance = None
    if tipo in cl.TIPOS_CON_ALCANCE:
        alcance = "COLECTIVO" if ("colectiv" in base or len(set(_DNI.findall(texto))) > 1) else "INDIVIDUAL"
    return {"tipo": tipo, "subtipo": subtipo, "alcance": alcance,
            "confidencialidad": "INTERNO" if tipo in ("INFORME", "COMUNICACION_INTERNA", "PROVEIDO") else "PUBLICO",
            "expediente": despacho.proponer_expediente(texto, nombre) or nota.get("nro_expte") or "",
            "destinatario": despacho.proponer_destinatario(texto) or ""}


def de_nota(nota, texto=None):
    """Clasificación para mostrar: {'confirmada', 'valores', 'propuesta', 'por', 'en', etiquetas...}."""
    propuesta = proponer(nota, texto)
    if confirmada(nota):
        valores = {"tipo": nota.get("tipo_documental"), "subtipo": nota.get("clasif_subtipo"), "alcance": nota.get("clasif_alcance"),
                   "confidencialidad": nota.get("confidencialidad"), "expediente": nota.get("clasif_expediente") or "",
                   "destinatario": nota.get("clasif_destinatario") or ""}
    else:
        valores = propuesta
    return {"confirmada": confirmada(nota), "valores": valores, "propuesta": propuesta,
            "por": nota.get("clasificado_por"), "en": nota.get("clasificado_en"),
            "tipo_etiqueta": tipos_documentales.ETIQUETA.get(valores["tipo"] or "", valores["tipo"] or "—"),
            "subtipo_etiqueta": cl.SUBTIPOS.get(valores["tipo"] or "", {}).get(valores.get("subtipo") or ""),
            "alcance_etiqueta": cl.ALCANCE.get(valores.get("alcance") or ""),
            "confidencialidad_etiqueta": cl.CONFIDENCIALIDAD.get(valores.get("confidencialidad") or ""),
            "lleva_protocolo": (valores["tipo"] or "") in cl.SIGLA_PROTOCOLO}


def _escribir(con, nota, c, usuario, origen):
    antes = {k: nota.get(k) for k in ("tipo_documental", "clasif_subtipo", "clasif_alcance", "confidencialidad", "clasif_expediente",
                                      "clasif_destinatario")}
    ahora = db._ahora()
    con.execute("""UPDATE notas SET tipo_documental = ?, tipo_documental_origen = ?, tipo_documental_por = ?, clasif_subtipo = ?,
                   clasif_alcance = ?, confidencialidad = ?, clasif_expediente = ?, clasif_destinatario = ?, clasificado_por = ?,
                   clasificado_en = ? WHERE id = ?""",
                (c.tipo, origen, usuario["nombre"], c.subtipo, c.alcance, c.confidencialidad, c.expediente, c.destinatario,
                 usuario["nombre"], ahora, nota["id"]))
    despues = {"tipo_documental": c.tipo, "clasif_subtipo": c.subtipo, "clasif_alcance": c.alcance, "confidencialidad": c.confidencialidad,
               "clasif_expediente": c.expediente, "clasif_destinatario": c.destinatario}
    au.registrar(con, accion="Clasificación" + (" (propuesta confirmada al firmar)" if origen == "AL_FIRMAR" else ""), entidad="nota",
                 entidad_id=nota["id"], antes=antes if confirmada(nota) else None, despues=despues, actor=au.Actor.desde(usuario))


def guardar(nota_id, datos, usuario):
    """Valida (DatoInvalido con mensaje para la persona) y guarda. Devuelve la Clasificacion."""
    nota = db.obtener_nota(nota_id)
    c = cl.validar(datos, tipos_documentales.ETIQUETA)
    with db._conexion() as con:
        _escribir(con, nota, c, usuario, "CONFIRMADO")
    db.registrar_evento(nota_id, f"Clasificada por {usuario['nombre']}: {tipos_documentales.ETIQUETA[c.tipo]}"
                                 + (f" ({cl.ALCANCE[c.alcance].lower()})" if c.alcance else "") + f", {cl.CONFIDENCIALIDAD[c.confidencialidad].lower()}")
    return c


def confirmar_propuesta(nota, usuario):
    """Para documentos que llegan a la firma sin clasificar: quien firma confirma la propuesta (queda registrado)."""
    p = proponer(nota)
    if not p["destinatario"]:
        p["destinatario"] = "Sin destinatario indicado"
    c = cl.validar(p, tipos_documentales.ETIQUETA)
    with db._conexion() as con:
        _escribir(con, nota, c, usuario, "AL_FIRMAR")
    db.registrar_evento(nota["id"], f"Clasificación propuesta por el sistema confirmada al firmar por {usuario['nombre']}")
    return c


def clasificacion_guardada(nota):
    return cl.Clasificacion(tipo=nota["tipo_documental"], subtipo=nota.get("clasif_subtipo"), alcance=nota.get("clasif_alcance"),
                            confidencialidad=nota.get("confidencialidad") or "INTERNO", destinatario=nota.get("clasif_destinatario") or "",
                            expediente=nota.get("clasif_expediente"))
