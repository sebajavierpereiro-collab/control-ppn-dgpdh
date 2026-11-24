"""
reportes.py — Reporte sobre los documentos subidos por los asesores.

Todo se calcula a partir de historial_estados (ya se registraba cada
transición de estado con su fecha, así que no hizo falta agregar columnas
nuevas) y de correcciones_director (para distinguir notas aprobadas tal
cual de notas que el Director modificó).

Los tres tiempos pedidos:
- aprobación de coordinación: SUBIDA -> PROCESANDO (el click de "Aprobar y
  enviar a corrección" deja el estado en PROCESANDO de inmediato, antes de
  arrancar el procesamiento en segundo plano, así el timestamp es el del
  click real, no el de cuando termina de procesar).
- firma: desde que queda lista para firmar (PROCESADA) hasta FIRMADA.
- despacho total: desde la subida (SUBIDA) hasta DESPACHADA.
"""
from datetime import datetime
from statistics import mean

import database as db


def _parse(fecha_iso):
    return datetime.fromisoformat(fecha_iso)


def _promedio_legible(segundos_lista):
    if not segundos_lista:
        return None
    prom = mean(segundos_lista)
    horas = prom / 3600
    if horas < 1:
        return f"{prom / 60:.0f} min"
    if horas < 48:
        return f"{horas:.1f} h"
    return f"{horas / 24:.1f} días"


def generar_reporte(desde=None, hasta=None, equipo_id=None):
    """`desde`/`hasta` son strings ISO (se comparan contra notas.creado_en
    tal cual vienen, sin parsear) o None para no acotar ese extremo.
    `equipo_id` limita el reporte a un equipo (para Coordinación)."""
    with db.conexion() as con:
        notas = [dict(n) for n in con.execute("SELECT * FROM notas").fetchall()]
        historial = [dict(h) for h in con.execute(
            "SELECT * FROM historial_estados ORDER BY nota_id, id"
        ).fetchall()]
        correcciones = [dict(c) for c in con.execute(
            "SELECT nota_id, decision FROM correcciones_director"
        ).fetchall()]
        equipos = {e["id"]: e["nombre"] for e in con.execute("SELECT id, nombre FROM equipos").fetchall()}

    if equipo_id is not None:
        notas = [n for n in notas if n["equipo_id"] == equipo_id]
    if desde:
        notas = [n for n in notas if n["creado_en"] >= desde]
    if hasta:
        notas = [n for n in notas if n["creado_en"] <= hasta]
    ids_filtrados = {n["id"] for n in notas}

    historial_por_nota = {}
    for h in historial:
        if h["nota_id"] not in ids_filtrados:
            continue
        historial_por_nota.setdefault(h["nota_id"], []).append(h)

    modificadas_ids = {
        c["nota_id"] for c in correcciones
        if c["decision"] in ("modificado", "modificar") and c["nota_id"] in ids_filtrados
    }

    aprobadas = modificadas = rechazadas = en_curso = 0
    tiempos_aprobacion, tiempos_firma, tiempos_despacho = [], [], []
    # Reforma 39: series para los gráficos del reporte (subidos y despachados por mes, por equipo, demora por mes).
    por_mes, despachadas_mes, por_equipo, demora_mes = {}, {}, {}, {}

    for n in notas:
        por_estado = {}
        for e in historial_por_nota.get(n["id"], []):
            por_estado.setdefault(e["estado"], []).append(_parse(e["fecha"]))

        t_subida = por_estado.get("SUBIDA", [None])[0]
        t_procesando = por_estado.get("PROCESANDO", [None])[0]
        t_procesada = por_estado.get("PROCESADA")
        t_procesada = t_procesada[-1] if t_procesada else None
        t_firmada = por_estado.get("FIRMADA", [None])[0]
        t_despachada = por_estado.get("DESPACHADA", [None])[0]
        t_rechazada = por_estado.get("RECHAZADA", [None])[0]

        if t_subida and t_procesando:
            tiempos_aprobacion.append((t_procesando - t_subida).total_seconds())
        if t_procesada and t_firmada:
            tiempos_firma.append((t_firmada - t_procesada).total_seconds())
        if t_subida and t_despachada:
            tiempos_despacho.append((t_despachada - t_subida).total_seconds())
            demora_mes.setdefault(t_despachada.isoformat()[:7], []).append((t_despachada - t_subida).total_seconds())
        mes = (n.get("creado_en") or "")[:7]
        if mes:
            por_mes[mes] = por_mes.get(mes, 0) + 1
        if t_despachada:
            despachadas_mes[t_despachada.isoformat()[:7]] = despachadas_mes.get(t_despachada.isoformat()[:7], 0) + 1
        eq = equipos.get(n.get("equipo_id"), "Sin equipo")
        por_equipo[eq] = por_equipo.get(eq, 0) + 1

        if t_rechazada:
            rechazadas += 1
        elif n["id"] in modificadas_ids:
            modificadas += 1
        elif t_firmada or t_despachada:
            aprobadas += 1
        else:
            en_curso += 1

    return {
        "total_archivos": len(notas),
        "total_notas": sum(1 for n in notas if n["es_nota"]),
        "notas_aprobadas": aprobadas,
        "notas_modificadas": modificadas,
        "notas_rechazadas": rechazadas,
        "notas_en_curso": en_curso,
        "tiempo_promedio_aprobacion_coordinacion": _promedio_legible(tiempos_aprobacion),
        "tiempo_promedio_firma": _promedio_legible(tiempos_firma),
        "tiempo_promedio_despacho_total": _promedio_legible(tiempos_despacho),
        "por_mes": sorted(por_mes.items())[-12:],
        "despachadas_mes": sorted(despachadas_mes.items())[-12:],
        "por_equipo": sorted(por_equipo.items(), key=lambda x: (-x[1], x[0])),
        "demora_mes_dias": [(m, round(sum(v) / len(v) / 86400, 1)) for m, v in sorted(demora_mes.items())[-12:]],
        "muestras": {
            "aprobacion_coordinacion": len(tiempos_aprobacion),
            "firma": len(tiempos_firma),
            "despacho_total": len(tiempos_despacho),
        },
    }
