"""
automatico.py — Análisis automático de documentos fuera del horario de
oficina (reforma 14).

Qué hace:
  * Mantiene una COLA PERSISTENTE de trabajos de IA (tabla trabajos_ia) por
    documento: CLASIFICAR, CONSISTENCIA, MEJORAS y CORRECCION.
  * Un hilo (Trabajador) los ejecuta de a uno, SOLO dentro de la ventana
    horaria configurada (por defecto 17:00 a 07:00, hora de Buenos Aires),
    para no ralentizar el sistema en horario de oficina. Orden: URGENTE,
    Prioritaria, Normal y, dentro de cada una, el más antiguo primero.
  * No toma trabajos nuevos fuera de la ventana; un trabajo largo (la
    corrección de una nota) chequea el reloj entre párrafos y, si se acaba
    la ventana, se corta y se retoma desde cero la noche siguiente.
  * Si Ollama no responde o falta el modelo, el trabajo se reintenta la
    noche siguiente (hasta MAX_INTENTOS veces).

Además, acá viven las funciones de análisis que también se usan "a pedido"
desde la app (por ejemplo, recalcular la consistencia al armar un bloque).

Nada de esto aplica cambios solo: todo termina como sugerencia o aviso que
una persona revisa.
"""
import logging
import os
import threading
import time as _reloj
from datetime import datetime, timedelta, timezone, time as hora_del_dia

import analisis
import database as db
import nucleo
import respaldo
import carga
import avisos_correo
import traslados

ZONA_HORARIA = "America/Argentina/Buenos_Aires"
MAX_INTENTOS = 3
INTERVALO_SEGUNDOS = 60

TIPOS_TRABAJO = ("CLASIFICAR", "CONSISTENCIA", "MEJORAS", "CORRECCION", "RESUMEN")
PROCESO_DE_TRABAJO = {
    "CLASIFICAR": "clasificacion",
    "CONSISTENCIA": "consistencia",
    "MEJORAS": "redaccion",
    "CORRECCION": "correccion",
    "RESUMEN": "redaccion",
}
ETIQUETAS_TRABAJO = {
    "CLASIFICAR": "Clasificación",
    "CONSISTENCIA": "Consistencia",
    "MEJORAS": "Mejoras desde informe",
    "CORRECCION": "Corrección previa",
    "RESUMEN": "Resumen para la firma",
}
ETIQUETAS_ESTADO_TRABAJO = {
    "PENDIENTE": "pendiente (corre en el horario nocturno)",
    "EN_CURSO": "en curso",
    "HECHO": "listo",
    "ERROR": "con error",
    "OMITIDO": "no aplica",
}
_RANGO = {"URGENTE": 0, "PRIORITARIA": 1, "NORMAL": 2}
ESTADOS_TERMINALES = ("DESPACHADA", "RECHAZADA")


class IANoDisponible(Exception):
    """Ollama no responde o falta el modelo del proceso."""


# =====================================================================
# VENTANA HORARIA
# =====================================================================
CONFIG = {
    "activa": True,
    "hora_inicio": hora_del_dia(17, 0),
    "hora_fin": hora_del_dia(7, 0),
    "fines_de_semana_todo_el_dia": False,
    # Mientras alguien usa el sistema, la IA no toma trabajos nuevos (la PC queda libre para atender pantallas).
    # Segundos desde el último uso; 0 = no pausar.
    "pausa_por_uso_segundos": 90,
}
_ACTIVIDAD = {"ultima": 0.0}
# Mantenimiento de fondo (plazos vencidos, recordatorios, sugerencias): no hace falta cada minuto.
MANTENIMIENTO_CADA_SEGUNDOS = 600


def registrar_actividad():
    """Lo llama cada pedido de una persona (no los chequeos automáticos de las páginas)."""
    _ACTIVIDAD["ultima"] = _reloj.monotonic()


def en_uso(ahora=None):
    pausa = CONFIG.get("pausa_por_uso_segundos") or 0
    return bool(pausa and _ACTIVIDAD["ultima"] and (ahora or _reloj.monotonic()) - _ACTIVIDAD["ultima"] < pausa)


def _parsear_hora(texto, por_defecto):
    try:
        h, m = (int(x) for x in texto.strip().split(":"))
        return hora_del_dia(h, m)
    except Exception:
        logging.warning(f"Hora inválida en config.ini ({texto!r}); se usa {por_defecto}.")
        return por_defecto


def configurar(config):
    """Lee la sección [ia_automatica] de config.ini (todas las claves son
    opcionales)."""
    if config.has_section("ollama"):
        CONFIG["keep_alive_noche"] = config.get("ollama", "keep_alive_noche", fallback="").strip() or None
        CONFIG["keep_alive_dia"] = config.get("ollama", "keep_alive_dia", fallback="").strip() or None
    if not config.has_section("ia_automatica"):
        return
    CONFIG["agrupar_por_modelo"] = config.getboolean("ia_automatica", "agrupar_por_modelo", fallback=True)
    CONFIG["activa"] = config.getboolean("ia_automatica", "activa", fallback=True)
    CONFIG["hora_inicio"] = _parsear_hora(config.get("ia_automatica", "hora_inicio", fallback="17:00"), hora_del_dia(17, 0))
    CONFIG["hora_fin"] = _parsear_hora(config.get("ia_automatica", "hora_fin", fallback="07:00"), hora_del_dia(7, 0))
    CONFIG["fines_de_semana_todo_el_dia"] = config.getboolean(
        "ia_automatica", "fines_de_semana_todo_el_dia", fallback=False
    )
    try:
        CONFIG["pausa_por_uso_segundos"] = max(0, config.getint("ia_automatica", "pausa_por_uso_segundos", fallback=90))
    except ValueError:
        logging.warning("[ia_automatica] pausa_por_uso_segundos inválido: se usa 90")


def _zona():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(ZONA_HORARIA)
    except Exception:
        # Sin base de zonas horarias (Windows sin tzdata): Argentina no usa
        # horario de verano, así que UTC-3 fijo es equivalente.
        return timezone(timedelta(hours=-3))


def ahora_local():
    return datetime.now(_zona())


def en_ventana(ahora=None, cfg=None):
    """¿Estamos dentro de la ventana en la que se permite correr la IA?
    `ahora` es un datetime (hora de Buenos Aires); se puede inyectar para
    probar. Con inicio > fin (17:00 a 07:00) la ventana cruza la medianoche."""
    explicito = cfg is not None
    cfg = cfg or CONFIG
    ahora = ahora or ahora_local()
    fines = cfg.get("fines_de_semana_todo_el_dia") if explicito else fines_de_semana_activo()
    if fines and ahora.weekday() >= 5:
        return True
    t = ahora.time().replace(second=0, microsecond=0)
    ini, fin = cfg["hora_inicio"], cfg["hora_fin"]
    if ini == fin:
        return True
    if ini < fin:
        return ini <= t < fin
    return t >= ini or t < fin


def fines_de_semana_activo():
    """Sábados y domingos la IA corre las 24 horas (todas las tareas de la cola). Se cambia en Administración → IA,
    sin tocar código; si nunca se cambió, está activo."""
    try:
        v = db.obtener_ajuste("ia_fines_de_semana")
    except Exception:
        v = None
    return True if v is None else v == "1"


def _id_de_noche(ahora, cfg=None):
    """Identifica la 'noche' en curso (la fecha en que arrancó la ventana),
    para no reintentar durante la misma noche un trabajo que ya falló."""
    cfg = cfg or CONFIG
    fecha = ahora.date()
    if cfg["hora_inicio"] > cfg["hora_fin"] and ahora.time() < cfg["hora_fin"]:
        fecha = fecha - timedelta(days=1)
    return fecha.isoformat()


def descripcion_ventana():
    c = CONFIG
    txt = f"{c['hora_inicio'].strftime('%H:%M')} a {c['hora_fin'].strftime('%H:%M')} (hora de Buenos Aires)"
    txt = ("lunes a viernes " + txt) if fines_de_semana_activo() else txt
    if fines_de_semana_activo():
        txt += "; sábados y domingos, las 24 horas"
    return txt


# =====================================================================
# TEXTO ACTUAL Y DOCUMENTOS RELACIONADOS
# =====================================================================
def ruta_texto_actual(nota):
    """Documento con el texto vigente de la nota: el procesado si ya se
    revisó, o la copia de Coordinación / el original si todavía no."""
    if nota["estado"] in ("PROCESADA", "FIRMADA", "DESPACHADA") and nota.get("ruta_resultado"):
        return nota["ruta_resultado"]
    if nota["estado"] == "EN_REVISION" and nota.get("ruta_analizada"):
        return nota["ruta_analizada"]
    return nota.get("ruta_base") or nota["ruta_original"]


def documentos_relacionados(nota):
    """Otros documentos con los que se compara esta nota: los de su bloque
    más el documento de referencia elegido a mano."""
    rel, vistos = [], {nota["id"]}
    if nota.get("bloque_id"):
        bloque = db.obtener_bloque(nota["bloque_id"])
        for bn in (bloque or {"notas": []})["notas"]:
            if bn["id"] not in vistos:
                vistos.add(bn["id"])
                rel.append(db.obtener_nota(bn["id"]))
    if nota.get("ref_nota_id") and nota["ref_nota_id"] not in vistos:
        ref = db.obtener_nota(nota["ref_nota_id"])
        if ref:
            rel.append(ref)
    return [r for r in rel if r and not db.es_pdf(r)]  # los PDF adjuntos no se comparan


def documento_de_referencia(nota):
    """El informe / proveído que sirve de fuente para mejorar la nota: el
    elegido a mano, o el primer INFORME/PROVEIDO de su bloque."""
    if nota.get("ref_nota_id"):
        ref = db.obtener_nota(nota["ref_nota_id"])
        if ref:
            return ref
    for r in documentos_relacionados(nota):
        if nucleo.clasificar_documento(r["nombre_original"]) in ("INFORME", "PROVEIDO"):
            return r
    return None


def _doc(nota):
    return {"id": nota["id"], "nombre": nota["nombre_original"],
            "texto": nucleo.texto_de_documento(ruta_texto_actual(nota))}


# =====================================================================
# ANÁLISIS (cada uno se puede llamar a pedido o desde la cola)
# =====================================================================
def _exigir_ia(proceso):
    """Si la IA está habilitada pero no responde para este proceso, se
    reintenta otra noche (la parte sin IA ya se hizo y quedó guardada)."""
    if nucleo.USAR_IA:
        nucleo.refrescar_modelos_ollama()
        if not nucleo.ia_disponible(proceso):
            raise IANoDisponible(f"IA no disponible para «{proceso}» (modelo {nucleo.modelo_para(proceso)})")


def clasificar_rapido(nota_id):
    """Clasificación por palabras clave (instantánea, sin IA): se hace al
    subir para que el listado ya tenga tema y prioridad sugerida."""
    nota = db.obtener_nota(nota_id)
    if not nota:
        return None
    texto = nucleo.texto_de_documento(ruta_texto_actual(nota))
    db.indexar_texto(nota_id, texto)  # para la búsqueda
    res = analisis.clasificar_documento(texto, usar_ia=False)
    db.guardar_clasificacion(nota_id, res["tema"], "auto", res["prioridad"], res["motivo"])
    agentes_por_reglas(nota_id, texto)
    return res


def agentes_por_reglas(nota_id, texto):
    """Agentes instantáneos (sin IA) al subir o editar: datos del texto, prerrevisión, plazos, casi iguales y resumen
    provisorio. Todo queda como sugerencia o alerta; nada cambia la nota."""
    import agentes
    try:
        agentes.registrar_entidades(nota_id, texto)
        nota = db.obtener_nota(nota_id)
        if not nota.get("tipo") and texto.strip():
            db.fijar_tipo_si_falta(nota_id, agentes.tipo_por_contenido(texto, nota["nombre_original"]))
        if nota["estado"] in ("BORRADOR", "SUBIDA", "ERROR"):
            agentes.correr_prerrevision(nota_id, texto)
        for p in agentes.detectar_plazos(texto)[:2]:
            if not nota.get("plazo_respuesta"):
                db.proponer_sugerencia(nota_id, "PLAZO", f"{p['cantidad']}-{p['unidad']}-{p['habiles']}", p)
        for d in agentes.casi_iguales(nota_id, texto):
            db.proponer_sugerencia(nota_id, "DUPLICADO", str(d["nota_id"]), d)
        try:  # propuesta 2: reglas de la Dirección «al subir» (solo proponen)
            import rutas_notas_ia
            rutas_notas_ia.evaluar_reglas(nota_id, "SUBIDA", texto)
        except Exception:
            logging.exception("Reglas de la Dirección: error en la nota %s", nota_id)
        if nucleo.es_nota(nota["nombre_original"]) and not nota.get("resumen"):
            lineas = agentes.resumen_reglas(texto)
            if lineas:
                db.guardar_resumen(nota_id, "\n".join(lineas), "reglas")
    except Exception:
        logging.exception("Agentes por reglas: error en la nota %s", nota_id)


def analizar_clasificacion(nota_id, usar_ia=True):
    nota = db.obtener_nota(nota_id)
    if not nota:
        return {"omitido": "la nota ya no existe"}
    # PDFs: extraer texto con pypdf (nucleo.texto_de_documento no soporta PDF)
    if db.es_pdf(nota):
        texto = ""
        try:
            from pypdf import PdfReader
            from pathlib import Path
            ruta = nota.get("ruta_original") or ""
            if ruta and Path(ruta).is_file():
                texto = "\n".join((p.extract_text() or "") for p in PdfReader(ruta).pages[:200])
        except Exception:
            logging.exception("No se pudo extraer texto del PDF %s para análisis", nota_id)
        if texto.strip():
            agentes_por_reglas(nota_id, texto)
        return {"omitido": "PDF — solo se indexaron entidades"}
    texto = nucleo.texto_de_documento(ruta_texto_actual(nota))
    db.indexar_texto(nota_id, texto)  # para la búsqueda (también cubre los .doc y el texto editado)
    quiere_ia = usar_ia and nucleo.USAR_IA
    if quiere_ia:
        nucleo.refrescar_modelos_ollama()
    res = analisis.clasificar_documento(texto, usar_ia=quiere_ia)
    db.guardar_clasificacion(nota_id, res["tema"], "ia" if res["origen"] == "ia" else "auto",
                             res["prioridad"], res["motivo"])
    agentes_por_reglas(nota_id, texto)
    if quiere_ia and res["origen"] != "ia" and not nucleo.ia_disponible("clasificacion"):
        raise IANoDisponible("IA de clasificación no disponible; quedó la clasificación por palabras clave")
    return {"tema": res["tema"], "prioridad": res["prioridad"], "origen": res["origen"]}


def verificador_activo():
    """El verificador corre si se activó en Administración o si config.ini
    define un modelo propio (modelo_verificacion)."""
    return db.obtener_ajuste("verificador") == "1" or nucleo.OLLAMA_MODELOS.get("verificacion") not in (None, nucleo.OLLAMA_MODEL)


def analizar_consistencia(nota_id, usar_ia=False):
    """Compara la nota con los documentos relacionados y guarda los
    hallazgos (conservando los que una persona ya revisó)."""
    nota = db.obtener_nota(nota_id)
    if not nota:
        return {"omitido": "la nota ya no existe"}
    relacionados = documentos_relacionados(nota)
    if not relacionados:
        db.reemplazar_hallazgos(nota_id, nota.get("bloque_id"), [])
        return {"omitido": "sin documentos relacionados (ni bloque ni documento de referencia)"}
    doc_nota = _doc(nota)
    hallazgos, vistos = [], set()
    quiere_ia = usar_ia and nucleo.USAR_IA
    if quiere_ia:
        nucleo.refrescar_modelos_ollama()
    for rel in relacionados:
        doc_rel = _doc(rel)
        encontrados = analisis.comparar_documentos([doc_nota, doc_rel])
        try:
            import agentes
            ya = {h.get("clave") or analisis.clave_hallazgo(h) for h in encontrados}
            encontrados += [h for h in agentes.cruce_documentos(doc_nota, doc_rel) if h["clave"] not in ya]
        except Exception:
            logging.exception("Control cruzado de unidad/persona")
        if quiere_ia:
            encontrados += analisis.hallazgos_ia(doc_nota, doc_rel, ejemplos_negativos=db.ejemplos_erroneos())
        modelo_ia = nucleo.modelo_para("consistencia")
        for h in encontrados:
            if h["dato"].endswith("(IA)"):
                h["origen"], h["modelo"] = "IA", modelo_ia
                if quiere_ia and verificador_activo():
                    h["verificacion"] = analisis.verificar_hallazgo_ia(h)
            if h["clave"] not in vistos:
                vistos.add(h["clave"])
                hallazgos.append(h)
    db.reemplazar_hallazgos(nota_id, nota.get("bloque_id"), hallazgos)
    if quiere_ia and not nucleo.ia_disponible("consistencia"):
        raise IANoDisponible("IA de consistencia no disponible; se guardó el chequeo por reglas")
    return {"hallazgos": len(hallazgos), "comparado_con": [r["id"] for r in relacionados]}


def analizar_mejoras(nota_id):
    nota = db.obtener_nota(nota_id)
    if not nota:
        return {"omitido": "la nota ya no existe"}
    if not nucleo.es_nota(nota["nombre_original"]):
        return {"omitido": "el documento no es una nota"}
    if nota["estado"] in ("PROCESADA", "FIRMADA", "DESPACHADA"):
        return {"omitido": "la nota ya fue revisada"}
    ref = documento_de_referencia(nota)
    if ref is None:
        return {"omitido": "no hay un informe o proveído de referencia"}
    if not nucleo.USAR_IA:
        return {"omitido": "IA desactivada por configuración"}
    _exigir_ia("redaccion")
    parrafos = nucleo.parrafos_de_documento(ruta_texto_actual(nota))
    texto_informe = nucleo.texto_de_documento(ruta_texto_actual(ref))
    mejoras = analisis.sugerir_mejoras_desde_informe(parrafos, texto_informe)
    db.reemplazar_mejoras(nota_id, ref["id"], mejoras)
    return {"sugerencias": len(mejoras), "informe": ref["id"]}


def precalcular_correccion(nota_id, continuar=None):
    """Corre la corrección completa (diccionario + LanguageTool + IA) sobre
    una nota que todavía no aprobó Coordinación y deja el resultado en
    caché, asociado a la huella de su texto. Al aprobar, si el texto no
    cambió, se usa tal cual."""
    nota = db.obtener_nota(nota_id)
    if not nota:
        return {"omitido": "la nota ya no existe"}
    if nota["estado"] not in ("BORRADOR", "SUBIDA"):
        return {"omitido": "la nota ya fue aprobada"}
    if not nucleo.es_nota(nota["nombre_original"]):
        return {"omitido": "el documento no es una nota (no lleva corrección automática)"}
    ruta = nota.get("ruta_base") or nota["ruta_original"]
    ruta = nucleo._asegurar_docx(ruta)
    huella = nucleo.hash_texto_documento(ruta)
    if db.obtener_propuestas_previas(nota_id, huella) is not None:
        return {"omitido": "ya estaba calculada para este texto"}
    if nucleo.USAR_IA:
        _exigir_ia("correccion")
    with nucleo.con_tipo_documental(nota.get("tipo_documental")):  # propuesta 17: perfil de corrección del tipo
        resultado = nucleo.generar_propuestas_documento(ruta, nota["nombre_original"], continuar=continuar)
    db.guardar_propuestas_previas(nota_id, huella, resultado)
    return {"parrafos": len(resultado["propuestas"])}


def propuestas_para_aprobacion(nota_id, ruta_docx, nombre_original):
    """Usado al aprobar: devuelve la corrección precalculada si el texto no
    cambió desde entonces; si no, la calcula en el momento (como siempre)."""
    try:
        huella = nucleo.hash_texto_documento(ruta_docx)
        previas = db.obtener_propuestas_previas(nota_id, huella)
        if previas is not None:
            logging.info(f"Nota {nota_id}: se usa la corrección precalculada.")
            return previas
    except Exception:
        logging.exception("No se pudo verificar la corrección precalculada; se calcula de nuevo")
    nota = db.obtener_nota(nota_id) or {}
    with nucleo.con_tipo_documental(nota.get("tipo_documental")):  # propuesta 17
        return nucleo.generar_propuestas_documento(ruta_docx, nombre_original)


# =====================================================================
# COLA
# =====================================================================
def _encolar(nota_id, tipo):
    return db.encolar_trabajo(nota_id, tipo, nucleo.modelo_para(PROCESO_DE_TRABAJO[tipo]))


def encolar_analisis(nota_id):
    """Encola el análisis automático de un documento recién subido (o de uno
    existente, en el relevamiento). Los trabajos que no apliquen se marcan
    "no aplica" al ejecutarse."""
    nota = db.obtener_nota(nota_id)
    if not nota or nota["estado"] in ESTADOS_TERMINALES:
        return 0
    n = 0
    n += bool(_encolar(nota_id, "CLASIFICAR"))
    if nucleo.es_nota(nota["nombre_original"]):
        n += bool(_encolar(nota_id, "RESUMEN"))
    if nota["estado"] in ("BORRADOR", "SUBIDA") and nucleo.es_nota(nota["nombre_original"]):
        n += bool(_encolar(nota_id, "CORRECCION"))
    if documentos_relacionados(nota):
        n += bool(_encolar(nota_id, "CONSISTENCIA"))
        if nucleo.es_nota(nota["nombre_original"]):
            n += bool(_encolar(nota_id, "MEJORAS"))
    return n


def reencolar_relacionados(nota_id):
    """Cuando cambia un bloque, un documento de referencia o el texto: hay
    que volver a comparar este documento y los de su bloque."""
    nota = db.obtener_nota(nota_id)
    if not nota:
        return
    ids = {nota_id}
    if nota.get("bloque_id"):
        ids |= {bn["id"] for bn in (db.obtener_bloque(nota["bloque_id"]) or {"notas": []})["notas"]}
    for i in ids:
        n = db.obtener_nota(i)
        if not n or n["estado"] in ESTADOS_TERMINALES:
            continue
        _encolar(i, "CONSISTENCIA")
        if nucleo.es_nota(n["nombre_original"]):
            _encolar(i, "MEJORAS")


def analizar_pendientes():
    """Relevamiento de lo ya cargado: encola el análisis de todos los
    documentos que siguen en circuito."""
    total = 0
    for n in db.listar_notas(limite=100000):
        if n["estado"] not in ESTADOS_TERMINALES:
            total += encolar_analisis(n["id"])
    return total


# Propuesta 21: dentro de la ventana nocturna, a igual prioridad, primero los trabajos del modelo que ya está cargado en
# memoria (Ollama no carga y descarga varios GB por trabajo). Se apaga con [ia_automatica] agrupar_por_modelo = false.
PROCESO_DE_TIPO = {"CLASIFICAR": "clasificacion", "CONSISTENCIA": "consistencia", "MEJORAS": "redaccion",
                   "CORRECCION": "correccion", "RESUMEN": "redaccion"}
ULTIMO_MODELO = [None]


def _modelo_de_trabajo(t):
    proceso = PROCESO_DE_TIPO.get(t.get("tipo"))
    return nucleo.modelo_para(proceso) if proceso else None


def _orden_trabajos(trabajos, agrupar_modelo=None):
    por_bloque = db.prioridades_por_bloque()
    if agrupar_modelo is None:
        agrupar_modelo = CONFIG.get("agrupar_por_modelo", True) and ULTIMO_MODELO[0] is not None and en_ventana()
    cargado = ULTIMO_MODELO[0]

    def clave(t):
        efectiva = por_bloque.get(t["bloque_id"], t["prioridad"]) if t.get("bloque_id") else t["prioridad"]
        otro_modelo = 1 if agrupar_modelo and _modelo_de_trabajo(t) != cargado else 0
        return (_RANGO.get(efectiva, 2), otro_modelo, t["creado_en"], t["id"])

    return sorted(trabajos, key=clave)


def keep_alive_actual():
    """Propuesta 21: cuánto tiempo deja Ollama el modelo en memoria después de cada pedido (config.ini [ollama]
    keep_alive_noche y keep_alive_dia, por ejemplo «30m» y «5m»). Sin configurar, no se manda y vale el de Ollama."""
    return CONFIG.get("keep_alive_noche") if en_ventana() else CONFIG.get("keep_alive_dia")


def ejecutar_trabajo(trabajo, continuar=None):
    """Ejecuta un trabajo. Devuelve (estado, resultado)."""
    tipo, nota_id = trabajo["tipo"], trabajo["nota_id"]
    import trazabilidad_ia
    ULTIMO_MODELO[0] = _modelo_de_trabajo(trabajo)
    with trazabilidad_ia.para(nota_id, tipo):
        return _ejecutar_trabajo(tipo, nota_id, continuar)


def _ejecutar_trabajo(tipo, nota_id, continuar=None):
    if tipo == "CLASIFICAR":
        res = analizar_clasificacion(nota_id, usar_ia=True)
    elif tipo == "CONSISTENCIA":
        res = analizar_consistencia(nota_id, usar_ia=True)
    elif tipo == "MEJORAS":
        res = analizar_mejoras(nota_id)
    elif tipo == "CORRECCION":
        res = precalcular_correccion(nota_id, continuar=continuar)
    elif tipo == "RESUMEN":
        import agentes
        nota = db.obtener_nota(nota_id)
        if not nota or not nucleo.es_nota(nota["nombre_original"]):
            res = {"omitido": "solo se resumen notas"}
        else:
            if nucleo.USAR_IA:
                nucleo.refrescar_modelos_ollama()
            res = agentes.generar_resumen(nota_id, usar_ia=nucleo.USAR_IA)
            if res.get("origen") == "reglas" and nucleo.USAR_IA and not nucleo.ia_disponible("redaccion"):
                raise IANoDisponible("IA de redacción no disponible; quedó el resumen por reglas")
    else:
        raise ValueError(f"Tipo de trabajo desconocido: {tipo}")
    return ("OMITIDO" if "omitido" in res else "HECHO"), res


# =====================================================================
# TRABAJADOR (hilo)
# =====================================================================
class Trabajador(threading.Thread):
    def __init__(self):
        super().__init__(name="ia-nocturna", daemon=True)
        self._parar = threading.Event()
        self.forzar = False          # "Ejecutar ahora": ignora la ventana hasta vaciar la cola
        self.actual = None           # trabajo en curso (para mostrar en /admin)
        self._aplazados = {}         # id de trabajo -> noche en la que ya falló
        self._contador_forzado = 0
        self.ultimo_error = None
        self._ultimo_mantenimiento = None  # None: el primer ciclo lo hace

    def detener(self):
        self._parar.set()

    def _noche(self):
        return f"forzado-{self._contador_forzado}" if self.forzar else _id_de_noche(ahora_local())

    def _puede_correr(self):
        """¿Se puede seguir corriendo? (también se usa dentro de un trabajo: no
        depende de la carga, que sube justamente porque la IA está trabajando)."""
        return CONFIG["activa"] and (self.forzar or en_ventana() or carga.activa())

    def _hay_lugar(self):
        """Antes de tomar un trabajo: si alguien está usando el sistema se espera (así las pantallas no se demoran);
        con la IA adaptativa además se exige capacidad libre (más holgura en horario de oficina)."""
        if self.forzar:
            return True
        if en_uso():
            return False
        if not carga.activa():
            return True
        return carga.hay_capacidad(oficina=not en_ventana())

    def run(self):
        if CONFIG["activa"]:
            logging.info(f"Análisis automático nocturno iniciado. Ventana: {descripcion_ventana()}")
        else:
            logging.info("Análisis automático nocturno DESACTIVADO ([ia_automatica] activa = false): solo corre lo que se pida a mano.")
        while not self._parar.is_set():
            hizo_algo = False
            try:
                hizo_algo = self.ciclo()
            except Exception as e:
                self.ultimo_error = str(e)
                logging.exception("Error en el ciclo del análisis automático")
            if not hizo_algo:
                self._parar.wait(INTERVALO_SEGUNDOS)

    def siguiente(self):
        noche = self._noche()
        candidatos = [t for t in db.trabajos_pendientes()
                      if self._aplazados.get(t["id"]) != noche
                      and t["nota_estado"] not in ESTADOS_TERMINALES]
        ordenados = _orden_trabajos(candidatos)
        return ordenados[0] if ordenados else None

    def ciclo(self):
        """Ejecuta UN trabajo si corresponde. Devuelve True si hizo algo."""
        # El respaldo diario se hace en el mismo hilo, al caer la tarde y
        # fuera de la ventana de la IA no importa: es liviano.
        try:
            if respaldo.toca_respaldo_automatico():
                respaldo.hacer_respaldo()
            if respaldo.toca_verificacion():
                ok, detalle = respaldo.verificar_ultimo_respaldo()
                if not ok:
                    db.notificar_usuarios([u["id"] for u in db.listar_usuarios() if u["activo"] and u["rol"] in ("DIRECTOR", "SUBDIRECTOR")],
                                          f"La prueba mensual de restauración del respaldo falló: {detalle}", "/admin/sistema/estado")
        except Exception:
            logging.exception("Error en el respaldo automático")
        try:
            db.recordar_revision_usuarios()
        except Exception:
            logging.exception("Error en el recordatorio de revisión de usuarios")
        try:
            avisos_correo.tick()  # avisos inmediatos: cada minuto
        except Exception:
            logging.exception("Error en los avisos por correo")
        if self._ultimo_mantenimiento is None or _reloj.monotonic() - self._ultimo_mantenimiento >= MANTENIMIENTO_CADA_SEGUNDOS:
            self._ultimo_mantenimiento = _reloj.monotonic()
            self._mantenimiento()
        if not self._puede_correr() or not self._hay_lugar():
            return False
        trabajo = self.siguiente()
        if trabajo is None:
            if self.forzar:
                self.forzar = False  # se vació la cola: se vuelve al horario normal
            return False
        self._correr(trabajo)
        return True

    def _mantenimiento(self):
        """Plazos vencidos, recordatorios, sugerencias, salud, reportes programados y documentos de referencia de la IA
        (lo copiado a mano en su carpeta): cada 10 minutos."""
        try:
            import referencia_ia
            referencia_ia.escanear("Sistema")
        except Exception:
            logging.exception("Error revisando los documentos de referencia de la IA")
        try:
            db.escalar_vencidas()
            db.recordar_por_vencer()
            import traslados  # propuesta 69: los plazos en días hábiles descuentan los feriados del calendario único
            db.seguimiento_respuestas(feriados=traslados.feriados_iso())
            import agentes
            agentes.sugerir_reiteraciones()
            agentes.sugerir_plazos_por_destinatario()
            agentes.avisar_firma_delegada()
        except Exception:
            logging.exception("Error en avisos por correo / escalamiento de plazos")
        try:
            import operacion  # ola 4: a cada asesor/a le dice qué le falta cargar
            operacion.recordatorio_personalizado()
        except Exception:
            logging.exception("Error en el recordatorio de traslados")
        try:
            import metas  # propuestas 84 y 91: aviso cuando una meta pasa a «atrasada» y recordatorio de carga del mes
            metas.avisos_de_fondo()
        except Exception:
            logging.exception("Error en los avisos de metas")
        try:
            import salud  # ola 5 (99): salud del sistema cada 15 minutos, avisa solo los cambios
            salud.tick()
        except Exception:
            logging.exception("Error en el monitoreo de salud")
        try:
            import programados  # ola 5 (83): reportes de los lunes y del día 1
            programados.tick()
        except Exception:
            logging.exception("Error en los reportes programados")
        try:
            import rutas_notas_ia  # Modificación 2: rechazadas sin movimiento (aviso cada 7 días, archivo a los 15); una vez por día
            rutas_notas_ia.revisar_inactividad()
        except Exception:
            logging.exception("Error revisando la inactividad de los documentos rechazados")
        try:
            import revision_ia  # una pasada por noche sobre la base de detenidos (solo propone)
            revision_ia.tick()
        except Exception:
            logging.exception("Error en la revisión de detenidos con IA")

    def _correr(self, trabajo):
        proceso = PROCESO_DE_TRABAJO[trabajo["tipo"]]
        db.iniciar_trabajo(trabajo["id"], nucleo.modelo_para(proceso))
        self.actual = {"id": trabajo["id"], "tipo": trabajo["tipo"], "nota": trabajo["nombre_original"]}
        try:
            estado, res = ejecutar_trabajo(trabajo, continuar=self._puede_correr)
            db.terminar_trabajo(trabajo["id"], estado, resultado=res)
        except nucleo.InterrumpidoPorHorario:
            logging.info(f"Trabajo {trabajo['id']} cortado por fin de la ventana; sigue la próxima noche.")
            db.terminar_trabajo(trabajo["id"], "PENDIENTE")
            self._aplazados[trabajo["id"]] = self._noche()
        except IANoDisponible as e:
            self._reintentar_o_cerrar(trabajo, str(e), omitir_al_agotar=True)
        except Exception as e:
            logging.exception(f"Error en el trabajo {trabajo['id']} ({trabajo['tipo']})")
            self._reintentar_o_cerrar(trabajo, str(e), omitir_al_agotar=False)
        finally:
            self.actual = None

    def _reintentar_o_cerrar(self, trabajo, mensaje, omitir_al_agotar):
        intentos = trabajo["intentos"] + 1
        if intentos >= MAX_INTENTOS:
            estado = "OMITIDO" if omitir_al_agotar else "ERROR"
            db.terminar_trabajo(trabajo["id"], estado, error=mensaje, sumar_intento=True)
        else:
            db.terminar_trabajo(trabajo["id"], "PENDIENTE", error=mensaje, sumar_intento=True)
            self._aplazados[trabajo["id"]] = self._noche()


_TRABAJADOR = None
_LOCK_FILE = None


def _tomar_lock_unico(ruta):
    """Garantiza UN solo trabajador nocturno aunque se arranque la app con
    varios procesos (uvicorn --workers N): el primero toma un lock de
    archivo y los demás no arrancan el suyo. Devuelve True si lo obtuvo."""
    global _LOCK_FILE
    try:
        ruta = os.fspath(ruta)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        f = open(ruta, "a+")
        try:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FILE = f  # se mantiene abierto mientras viva el proceso
        return True
    except OSError:
        return False


def iniciar_trabajador(ruta_lock=None):
    global _TRABAJADOR
    if _TRABAJADOR is not None and _TRABAJADOR.is_alive():
        return _TRABAJADOR
    if ruta_lock is not None and not _tomar_lock_unico(ruta_lock):
        logging.warning(
            "Ya hay otro proceso con el análisis automático nocturno: este no arranca el suyo. "
            "Usá un solo proceso de uvicorn (sin --workers)."
        )
        return None
    reencoladas = db.reencolar_trabajos_huerfanos()
    if reencoladas:
        logging.info(f"{reencoladas} trabajo(s) de IA interrumpidos vuelven a la cola.")
    _TRABAJADOR = Trabajador()
    _TRABAJADOR.start()
    return _TRABAJADOR


def detener_trabajador():
    if _TRABAJADOR is not None:
        _TRABAJADOR.detener()


def ejecutar_ahora():
    """Dirección: corre la cola ya mismo, sin esperar a la ventana. Se apaga
    sola cuando la cola queda vacía."""
    if _TRABAJADOR is None:
        return False
    _TRABAJADOR._contador_forzado += 1
    _TRABAJADOR.forzar = True
    return True


def estado_trabajador():
    ahora = ahora_local()
    t = _TRABAJADOR
    return {
        "activa": CONFIG["activa"],
        "ventana": descripcion_ventana(),
        "en_ventana": en_ventana(ahora),
        "ahora": ahora.strftime("%d/%m/%Y %H:%M"),
        "hilo_vivo": bool(t and t.is_alive()),
        "forzado": bool(t and t.forzar),
        "en_pausa_por_uso": en_uso() and not (t and t.forzar),
        "actual": t.actual if t else None,
        "ultimo_error": t.ultimo_error if t else None,
        "resumen": db.resumen_trabajos(),
    }
