"""
respaldo.py — Copias de seguridad de la base y de los documentos.

  * La base se copia con la API de respaldo de SQLite (consistente aunque la
    app esté en uso) a  <carpeta>/db/dgpdh_AAAAAMMDD_HHMM.db  y se conservan
    las últimas `conservar` copias.
  * Los documentos (originales, base, procesados y firmados) se copian de
    forma incremental a  <carpeta>/documentos/...  : solo lo que falta o
    cambió de tamaño. Nunca se borra nada del destino.

Config (config.ini):
    [respaldos]
    activo = true
    carpeta =            ; vacío = datos/respaldos. Conviene una carpeta de RED
    conservar = 14       ; copias de la base que se guardan
    hora = 18:00         ; el trabajador la hace una vez por día, desde esta hora
    copia_externa =      ; opcional: segunda copia en OTRO equipo o disco de red (\\servidor\respaldos)

Una vez por mes se hace una PRUEBA DE RESTAURACIÓN automática: se abre el último
respaldo como si se fuera a recuperar, se verifica que la base esté sana
(PRAGMA integrity_check) y que tenga las notas y viajes esperables.

Para recuperar: detener la app, copiar el .db elegido sobre datos/dgpdh.db
(y borrar dgpdh.db-wal / dgpdh.db-shm si existen) y copiar la carpeta
documentos/ sobre datos/. Al arrancar, las rutas se reubican solas.
"""
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import database as db

CONFIG = {"activo": True, "carpeta": None, "conservar": 14, "hora": "18:00", "copia_externa": None}
CARPETAS_DOCUMENTOS = ("originales", "base", "procesados", "firmados", "despacho", "respuestas", "bases_cargadas",
                       "obstaculizaciones")  # evidencia del Registro de Obstaculizaciones e informes generados
ARCHIVOS_SUELTOS = ("clave_auditoria.key", "auditoria_ancla.txt")  # sello de la auditoría: sin la clave no se verifica la cadena
_ultimo = {"fecha": None, "resultado": None}
_verificacion = {"fecha": None, "ok": None, "detalle": None}


def configurar(config, carpeta_datos):
    CONFIG["carpeta_datos"] = Path(carpeta_datos)
    if not config.has_section("respaldos"):
        CONFIG["carpeta"] = Path(carpeta_datos) / "respaldos"
        return
    CONFIG["activo"] = config.getboolean("respaldos", "activo", fallback=True)
    carpeta = config.get("respaldos", "carpeta", fallback="").strip()
    CONFIG["carpeta"] = Path(carpeta) if carpeta else Path(carpeta_datos) / "respaldos"
    CONFIG["conservar"] = max(1, config.getint("respaldos", "conservar", fallback=14))
    CONFIG["hora"] = config.get("respaldos", "hora", fallback="18:00").strip() or "18:00"
    externa = config.get("respaldos", "copia_externa", fallback="").strip()
    CONFIG["copia_externa"] = Path(externa) if externa else None


def _carpeta():
    return CONFIG.get("carpeta") or Path(CONFIG.get("carpeta_datos", Path("datos"))) / "respaldos"


def respaldar_base():
    """Copia consistente de la base. Devuelve la ruta del respaldo."""
    destino_dir = _carpeta() / "db"
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"dgpdh_{datetime.now():%Y%m%d_%H%M%S}.db"
    origen = sqlite3.connect(db.RUTA_DB, timeout=30)
    try:
        copia = sqlite3.connect(destino)
        try:
            origen.backup(copia)
        finally:
            copia.close()
    finally:
        origen.close()
    # Rotación: se conservan las últimas N.
    copias = sorted(destino_dir.glob("dgpdh_*.db"))
    for vieja in copias[:-CONFIG["conservar"]]:
        try:
            vieja.unlink()
        except OSError:
            logging.warning(f"No se pudo borrar el respaldo viejo {vieja}")
    return destino


def respaldar_documentos():
    """Copia incremental de los documentos. Devuelve cuántos archivos copió."""
    base = Path(CONFIG.get("carpeta_datos", Path("datos")))
    raiz_destino = _carpeta() / "documentos"
    copiados = 0
    for nombre in CARPETAS_DOCUMENTOS:
        origen = base / nombre
        if not origen.is_dir():
            continue
        for archivo in origen.rglob("*"):
            if not archivo.is_file():
                continue
            destino = raiz_destino / nombre / archivo.relative_to(origen)
            try:
                if destino.exists() and destino.stat().st_size == archivo.stat().st_size \
                        and destino.stat().st_mtime >= archivo.stat().st_mtime:
                    continue
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archivo, destino)
                copiados += 1
            except OSError as e:
                logging.warning(f"No se pudo respaldar {archivo}: {e}")
    for nombre in ARCHIVOS_SUELTOS:
        archivo = base / nombre
        if archivo.is_file():
            try:
                destino = raiz_destino / "_auditoria" / nombre
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(archivo, destino)
            except OSError as e:
                logging.warning(f"No se pudo respaldar {archivo}: {e}")
    return copiados


def hacer_respaldo():
    """Respaldo completo (base + documentos). Devuelve un resumen legible."""
    import soberania
    try:
        soberania.verificar_respaldo(_carpeta())  # Modo Seguro Institucional: nunca en una nube pública
        ruta_db = respaldar_base()
        copiados = respaldar_documentos()
        resumen = f"Respaldo listo: base en {ruta_db.name}, {copiados} documento(s) nuevo(s) copiado(s) a {_carpeta()}"
        if CONFIG.get("copia_externa"):
            try:
                soberania.verificar_respaldo(CONFIG["copia_externa"])
                n = copiar_externa(ruta_db)
                resumen += f"; copia externa en {CONFIG['copia_externa']} ({n} archivo(s) nuevo(s))"
            except (OSError, soberania.SalidaBloqueada) as e:
                resumen += f"; ATENCIÓN: no se pudo hacer la copia externa ({e})"
        logging.info(resumen)
        _ultimo.update(fecha=datetime.now(), resultado=resumen)
        return True, resumen
    except soberania.SalidaBloqueada as e:
        logging.warning(str(e))
        _ultimo.update(fecha=datetime.now(), resultado=f"BLOQUEADO: {e}")
        return False, f"El respaldo no se hizo: {e}"
    except Exception as e:
        logging.exception("Falló el respaldo")
        _ultimo.update(fecha=datetime.now(), resultado=f"ERROR: {e}")
        return False, f"El respaldo falló: {e}"


def copiar_externa(ruta_db):
    """Segunda copia (base + documentos, incremental) en otro equipo o disco. Devuelve archivos copiados."""
    externa = Path(CONFIG["copia_externa"])
    (externa / "db").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ruta_db, externa / "db" / ruta_db.name)
    for vieja in sorted((externa / "db").glob("dgpdh_*.db"))[:-CONFIG["conservar"]]:
        try:
            vieja.unlink()
        except OSError:
            pass
    copiados = 1
    origen_docs = _carpeta() / "documentos"
    for archivo in origen_docs.rglob("*") if origen_docs.is_dir() else []:
        if archivo.is_file():
            destino = externa / "documentos" / archivo.relative_to(origen_docs)
            if destino.exists() and destino.stat().st_size == archivo.stat().st_size:
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archivo, destino)
            copiados += 1
    return copiados


def verificar_ultimo_respaldo():
    """Prueba de restauración: abre el último respaldo de la base (sin tocar la base en uso) y revisa que esté
    sano y con datos. Devuelve (ok, detalle)."""
    copias = sorted((_carpeta() / "db").glob("dgpdh_*.db")) if (_carpeta() / "db").is_dir() else []
    if not copias:
        ok, detalle = False, "No hay ningún respaldo de la base para probar."
    else:
        ultima = copias[-1]
        try:
            con = sqlite3.connect(f"file:{ultima}?mode=ro", uri=True)
            try:
                integridad = con.execute("PRAGMA integrity_check").fetchone()[0]
                notas = con.execute("SELECT COUNT(*) FROM notas").fetchone()[0]
                usuarios = con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
                try:
                    viajes = con.execute("SELECT COUNT(*) FROM viajes").fetchone()[0]
                except sqlite3.OperationalError:
                    viajes = 0
            finally:
                con.close()
            ok = integridad == "ok" and usuarios > 0
            detalle = (f"{ultima.name}: base {'sana' if integridad == 'ok' else 'DAÑADA (' + integridad + ')'}, "
                       f"{notas} nota(s), {viajes} viaje(s), {usuarios} usuario(s).")
            if usuarios == 0:
                detalle += " ATENCIÓN: no tiene usuarios."
        except sqlite3.Error as e:
            ok, detalle = False, f"No se pudo abrir {ultima.name}: {e}"
    _verificacion.update(fecha=datetime.now(), ok=ok, detalle=detalle)
    try:
        (_carpeta() / "ultima_verificacion.txt").write_text(f"{datetime.now():%Y-%m-%d %H:%M} {'OK' if ok else 'FALLA'} {detalle}", encoding="utf-8")
    except OSError:
        pass
    return ok, detalle


def toca_verificacion(ahora=None):
    """Una prueba de restauración por mes (se recuerda en ultima_verificacion.txt, sobrevive reinicios)."""
    ahora = ahora or datetime.now()
    marca = _carpeta() / "ultima_verificacion.txt"
    try:
        hecha = marca.read_text(encoding="utf-8")[:7] if marca.exists() else ""
    except OSError:
        hecha = ""
    return CONFIG["activo"] and hecha != f"{ahora:%Y-%m}" and (_carpeta() / "db").is_dir()


def en_la_misma_pc():
    """True si los respaldos quedan dentro de la carpeta de datos y no hay copia externa: si falla el disco, se pierde todo."""
    try:
        datos = Path(CONFIG.get("carpeta_datos", Path("datos"))).resolve()
        return not CONFIG.get("copia_externa") and str(_carpeta().resolve()).startswith(str(datos))
    except OSError:
        return False


def toca_respaldo_automatico(ahora=None):
    """True si el respaldo diario está activo, ya pasó la hora configurada y
    todavía no se hizo hoy."""
    if not CONFIG["activo"]:
        return False
    ahora = ahora or datetime.now()
    try:
        h, m = (int(x) for x in CONFIG["hora"].split(":"))
    except ValueError:
        h, m = 18, 0
    if (ahora.hour, ahora.minute) < (h, m):
        return False
    ultimo = _ultimo["fecha"]
    if ultimo is not None and ultimo.date() == ahora.date():
        return False
    # Si la app se reinició, mira si ya hay un respaldo de hoy en disco.
    destino_dir = _carpeta() / "db"
    if destino_dir.is_dir() and any(destino_dir.glob(f"dgpdh_{ahora:%Y%m%d}_*.db")):
        _ultimo["fecha"] = ahora
        return False
    return True


def estado():
    return {
        "activo": CONFIG["activo"],
        "carpeta": str(_carpeta()),
        "conservar": CONFIG["conservar"],
        "hora": CONFIG["hora"],
        "ultimo": _ultimo["resultado"],
        "ultima_fecha": _ultimo["fecha"].strftime("%d/%m/%Y %H:%M") if _ultimo["fecha"] else None,
        "copia_externa": str(CONFIG["copia_externa"]) if CONFIG.get("copia_externa") else None,
        "misma_pc": en_la_misma_pc(),
        "verificacion": _verificacion_texto(),
    }


def _verificacion_texto():
    if _verificacion["fecha"]:
        return {"fecha": _verificacion["fecha"].strftime("%d/%m/%Y %H:%M"), "ok": _verificacion["ok"], "detalle": _verificacion["detalle"]}
    marca = _carpeta() / "ultima_verificacion.txt"
    try:
        if marca.exists():
            t = marca.read_text(encoding="utf-8")
            return {"fecha": t[:16], "ok": " OK " in t[:22], "detalle": t[17:].split(" ", 1)[-1]}
    except OSError:
        pass
    return None
