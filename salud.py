"""Ola 5 · Reforma 99: monitoreo de salud con aviso a la administración.

Cada 15 minutos (desde el mismo hilo del análisis automático) se revisan: espacio en disco, base de datos, respaldo,
IA (Ollama y modelos), cola de análisis, hilo de trabajo, memoria y correo (solo si está vinculado). Cuando un
chequeo pasa a «atención» o «falla», la Dirección recibe UN aviso en el sistema; cuando se normaliza, otro.
No repite el mismo aviso cada 15 minutos.
"""
import json
import logging
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import auth
import database as db

OK, ATENCION, FALLA, NEUTRO = "OK", "ATENCION", "FALLA", "NEUTRO"
ETIQUETA_ESTADO = {OK: "Bien", ATENCION: "Atención", FALLA: "Falla", NEUTRO: "Sin controlar"}
CADA_MINUTOS = 15
CONFIG = {"carpeta_datos": Path("datos"), "disco_min_gb": 5.0, "disco_min_pct": 10, "respaldo_max_horas": 36,
          "cola_max_horas": 72, "memoria_max_pct": 92}
_ULTIMA = {"fecha": None}


def configurar(carpeta_datos, config=None):
    CONFIG["carpeta_datos"] = Path(carpeta_datos)
    if config is not None:
        CONFIG["https"] = config.getboolean("seguridad", "https", fallback=False)
    if config is not None and config.has_section("salud"):
        for clave in ("disco_min_gb", "respaldo_max_horas", "cola_max_horas"):
            try:
                CONFIG[clave] = config.getfloat("salud", clave, fallback=CONFIG[clave])
            except ValueError:
                pass


def _r(chequeo, etiqueta, estado, detalle, accion=""):
    return {"chequeo": chequeo, "etiqueta": etiqueta, "estado": estado, "estado_texto": ETIQUETA_ESTADO[estado],
            "detalle": detalle, "accion": accion}


def chequear_disco():
    try:
        u = shutil.disk_usage(CONFIG["carpeta_datos"])
    except OSError as e:
        return _r("disco", "Espacio en disco", FALLA, f"No se pudo leer el disco: {e}")
    libre_gb, pct = u.free / 1e9, 100 * u.free / u.total if u.total else 0
    texto = f"{libre_gb:.1f} GB libres ({pct:.0f} % del disco)"
    if libre_gb < CONFIG["disco_min_gb"] / 2 or pct < CONFIG["disco_min_pct"] / 2:
        return _r("disco", "Espacio en disco", FALLA, texto, "Liberar espacio o mover los respaldos a otro disco: sin espacio la app deja de guardar.")
    if libre_gb < CONFIG["disco_min_gb"] or pct < CONFIG["disco_min_pct"]:
        return _r("disco", "Espacio en disco", ATENCION, texto, "Conviene liberar espacio pronto.")
    return _r("disco", "Espacio en disco", OK, texto)


def chequear_base():
    try:
        with db._conexion() as con:
            con.execute("SELECT 1").fetchone()
            tam = Path(db.RUTA_DB).stat().st_size / 1e6 if Path(db.RUTA_DB).exists() else 0
    except Exception as e:
        return _r("base", "Base de datos", FALLA, f"No responde: {e}", "Revisar el registro de la app y reiniciarla.")
    m = db.MEDIDOR
    texto = f"Responde. {tam:.0f} MB. Desde que arrancó: {m['conexiones']} accesos, {m['lentas']} lentos (más de 1 s), {m['bloqueos']} bloqueos."
    if m["bloqueos"] >= 5:
        return _r("base", "Base de datos", ATENCION, texto, "Hubo esperas por escrituras simultáneas: ver Administración → Base de datos.")
    return _r("base", "Base de datos", OK, texto)


def _ultimo_respaldo():
    try:
        import respaldo
        carpeta = respaldo._carpeta() / "db"
        copias = sorted(carpeta.glob("dgpdh_*.db"), key=lambda p: p.stat().st_mtime) if carpeta.is_dir() else []
        return (datetime.fromtimestamp(copias[-1].stat().st_mtime, tz=timezone.utc), respaldo.CONFIG.get("activo", True)) if copias else (None, respaldo.CONFIG.get("activo", True))
    except Exception:
        return None, True


def chequear_respaldo(ahora=None):
    ahora = ahora or datetime.now(timezone.utc)
    ultimo, activo = _ultimo_respaldo()
    if not activo:
        return _r("respaldo", "Respaldo", ATENCION, "El respaldo automático está desactivado en config.ini.", "Activarlo ([respaldo] activo = true).")
    if not ultimo:
        return _r("respaldo", "Respaldo", ATENCION, "Todavía no hay ningún respaldo.", "Hacer uno desde Administración → Respaldos.")
    horas = (ahora - ultimo).total_seconds() / 3600
    texto = f"Último: {ultimo.astimezone().strftime('%d/%m/%Y %H:%M')} (hace {horas:.0f} h)"
    if horas > CONFIG["respaldo_max_horas"] * 2:
        return _r("respaldo", "Respaldo", FALLA, texto, "El respaldo diario no se está haciendo: revisar la carpeta de respaldos.")
    if horas > CONFIG["respaldo_max_horas"]:
        return _r("respaldo", "Respaldo", ATENCION, texto, "El último respaldo tiene más de un día.")
    return _r("respaldo", "Respaldo", OK, texto)


def chequear_ia():
    try:
        import nucleo
        if not nucleo.USAR_IA:
            return _r("ia", "IA (Ollama)", NEUTRO, "Desactivada por configuración.")
        descargados = nucleo.refrescar_modelos_ollama()
        if descargados is None:
            return _r("ia", "IA (Ollama)", FALLA, "Ollama no responde: las sugerencias de IA quedan en espera.",
                      "Revisar que el servicio Ollama esté iniciado en el servidor.")
        faltan = sorted({nucleo.modelo_para(p) for p in nucleo.PROCESOS_IA if not nucleo._modelo_en(nucleo.modelo_para(p), descargados)})
        if faltan:
            return _r("ia", "IA (Ollama)", ATENCION, "Responde, pero falta descargar: " + ", ".join(faltan), "ollama pull " + faltan[0])
        return _r("ia", "IA (Ollama)", OK, f"Responde; {len(descargados)} modelo(s) descargado(s).")
    except Exception as e:
        return _r("ia", "IA (Ollama)", ATENCION, f"No se pudo comprobar: {e}")


def chequear_cola(ahora=None):
    ahora = ahora or datetime.now(timezone.utc)
    try:
        import automatico
        est = automatico.estado_trabajador()
        with db._conexion() as con:
            f = con.execute("SELECT COUNT(*) AS n, MIN(creado_en) AS viejo FROM trabajos_ia WHERE estado = 'PENDIENTE'").fetchone()
            errores = con.execute("SELECT COUNT(*) AS n FROM trabajos_ia WHERE estado = 'ERROR'").fetchone()["n"]
    except Exception as e:
        return _r("cola", "Análisis automático", ATENCION, f"No se pudo comprobar: {e}")
    if est.get("activa") and not est.get("hilo_vivo"):
        return _r("cola", "Análisis automático", FALLA, "El proceso de análisis no está corriendo.", "Reiniciar la app.")
    texto = f"{f['n']} trabajo(s) pendiente(s)" + (f", {errores} con error" if errores else "")
    if f["n"] and f["viejo"]:
        try:
            v = datetime.fromisoformat(f["viejo"])
            horas = (ahora - (v if v.tzinfo else v.replace(tzinfo=timezone.utc))).total_seconds() / 3600
        except ValueError:
            horas = 0
        texto += f"; el más viejo espera hace {horas:.0f} h"
        if horas > CONFIG["cola_max_horas"]:
            return _r("cola", "Análisis automático", ATENCION, texto, "La cola no avanza: revisar la IA o usar «Ejecutar ahora».")
    return _r("cola", "Análisis automático", OK, texto)


def chequear_memoria():
    try:
        import psutil
        m = psutil.virtual_memory()
    except Exception:
        return _r("memoria", "Memoria", NEUTRO, "No se puede medir en este equipo.")
    texto = f"{m.percent:.0f} % en uso de {m.total / 1e9:.0f} GB"
    if m.percent >= CONFIG["memoria_max_pct"]:
        return _r("memoria", "Memoria", ATENCION, texto, "El equipo está al límite: la IA puede volverse muy lenta.")
    return _r("memoria", "Memoria", OK, texto)


def chequear_correo():
    try:
        import avisos_correo
        if not avisos_correo.activo():
            return _r("correo", "Correo", NEUTRO, "Sin vincular (decisión del organismo): no se controla.")
        with db._conexion() as con:
            n = con.execute("SELECT COUNT(*) AS n FROM cola_correo WHERE enviado = 0").fetchone()["n"]
        if n > 50:
            return _r("correo", "Correo", ATENCION, f"{n} correos sin enviar.", "Revisar el servidor de correo en Administración → Correo.")
        return _r("correo", "Correo", OK, f"{n} correo(s) en cola.")
    except Exception as e:
        return _r("correo", "Correo", ATENCION, f"No se pudo comprobar: {e}")


def chequear_https():
    if not CONFIG.get("https"):
        return _r("https", "Conexión cifrada", NEUTRO, "No está activada (https = false).", "Ver Administración → Seguridad → Conexión cifrada.")
    try:
        import https_interno
        e = https_interno.estado()
    except Exception as ex:
        return _r("https", "Conexión cifrada", ATENCION, f"No se pudo leer el certificado: {ex}")
    if not e.get("hay_servidor") or e.get("dias") is None:
        return _r("https", "Conexión cifrada", ATENCION, "Activada pero sin certificado propio en la carpeta certs (puede estar detrás de otro servidor).")
    if e["dias"] < 0:
        return _r("https", "Conexión cifrada", FALLA, "El certificado del servidor venció.", "python https_interno.py --renovar y reiniciar la app.")
    if e["dias"] < 30:
        return _r("https", "Conexión cifrada", ATENCION, f"El certificado vence en {e['dias']} días.", "python https_interno.py --renovar y reiniciar la app.")
    return _r("https", "Conexión cifrada", OK, f"Certificado vigente ({e['dias']} días).")


def revisar(ahora=None):
    return [chequear_disco(), chequear_base(), chequear_respaldo(ahora), chequear_ia(), chequear_cola(ahora),
            chequear_memoria(), chequear_correo(), chequear_https()]


def resumen(resultados):
    peor = FALLA if any(r["estado"] == FALLA for r in resultados) else ATENCION if any(r["estado"] == ATENCION for r in resultados) else OK
    return {"estado": peor, "texto": ETIQUETA_ESTADO[peor]}


def tick(ahora=None, forzar=False):
    """Llamado desde el hilo de fondo. Revisa cada CADA_MINUTOS y avisa solo los cambios de estado."""
    ahora = ahora or datetime.now(timezone.utc)
    if not forzar and _ULTIMA["fecha"] and ahora - _ULTIMA["fecha"] < timedelta(minutes=CADA_MINUTOS):
        return None
    _ULTIMA["fecha"] = ahora
    resultados = revisar(ahora)
    try:
        previo = json.loads(db.obtener_ajuste("salud_estado", "{}") or "{}")
    except ValueError:
        previo = {}
    nuevo, avisos = {}, []
    for r in resultados:
        nuevo[r["chequeo"]] = r["estado"]
        antes = previo.get(r["chequeo"], OK)
        if r["estado"] != antes and r["estado"] in (ATENCION, FALLA):
            avisos.append(f"{r['etiqueta']}: {r['estado_texto'].lower()}. {r['detalle']}")
        elif antes in (ATENCION, FALLA) and r["estado"] == OK:
            avisos.append(f"{r['etiqueta']}: se normalizó.")
        if r["estado"] != antes:
            with db._conexion() as con:
                con.execute("INSERT INTO salud_eventos (chequeo, estado, detalle, fecha) VALUES (?,?,?,?)",
                            (r["chequeo"], r["estado"], r["detalle"], ahora.isoformat(timespec="seconds")))
    db.guardar_ajuste("salud_estado", json.dumps(nuevo))
    db.guardar_ajuste("salud_ultima", ahora.isoformat(timespec="seconds"))
    if avisos:
        try:
            admins = [u["id"] for u in db.listar_usuarios() if u["activo"] and u["rol"] in auth.ROLES_DIRECCION]
            for texto in avisos:
                db.notificar_usuarios(admins, "Salud del sistema · " + texto, "/admin/sistema/salud")
        except Exception:
            logging.exception("No se pudo avisar el cambio de salud del sistema")
    return resultados


def historial(limite=40):
    with db._conexion() as con:
        return [dict(f) for f in con.execute("SELECT * FROM salud_eventos ORDER BY id DESC LIMIT ?", (limite,))]
