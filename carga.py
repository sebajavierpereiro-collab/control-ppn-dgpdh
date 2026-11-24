"""IA adaptativa: corre solo cuando la PC tiene capacidad libre.

Mide CPU y memoria libre (psutil; si no está instalado, no limita nada).
Dentro del horario de oficina se exige más holgura que fuera de él. El
interruptor se maneja desde Administración → IA → Calidad y se guarda en la
base (ajuste `ia_adaptativa`); config.ini ([ia_adaptativa]) da los umbrales.
"""
import logging

import database as db

CONFIG = {"cpu_max_libre": 80.0, "cpu_max_oficina": 35.0, "ram_libre_min_mb": 1500.0}
_ultimo = {"cpu": None, "ram_mb": None, "ok": None}


def configurar(cfg):
    if cfg.has_section("ia_adaptativa"):
        for k in CONFIG:
            try:
                CONFIG[k] = cfg.getfloat("ia_adaptativa", k, fallback=CONFIG[k])
            except ValueError:
                logging.warning(f"[ia_adaptativa] {k} inválido: se usa {CONFIG[k]}")
    if cfg.has_option("ia_adaptativa", "activa") and db.obtener_ajuste("ia_adaptativa") is None:
        db.guardar_ajuste("ia_adaptativa", "1" if cfg.getboolean("ia_adaptativa", "activa", fallback=False) else "0")


def activa():
    return db.obtener_ajuste("ia_adaptativa") == "1"


def activar(valor):
    db.guardar_ajuste("ia_adaptativa", "1" if valor else "0")


def medir():
    """{'cpu': %, 'ram_mb': libre} o None si no se puede medir."""
    try:
        import psutil
        return {"cpu": psutil.cpu_percent(interval=0.5), "ram_mb": psutil.virtual_memory().available / 1048576}
    except Exception:
        return None


def hay_capacidad(oficina=False, medicion=None):
    """True si la PC tiene holgura. Sin forma de medir, no se limita."""
    m = medicion or medir()
    if m is None:
        _ultimo.update(cpu=None, ram_mb=None, ok=True)
        return True
    tope = CONFIG["cpu_max_oficina"] if oficina else CONFIG["cpu_max_libre"]
    ok = m["cpu"] <= tope and m["ram_mb"] >= CONFIG["ram_libre_min_mb"]
    _ultimo.update(cpu=m["cpu"], ram_mb=m["ram_mb"], ok=ok)
    return ok


def estado():
    return {"activa": activa(), "ultimo": dict(_ultimo), "umbrales": dict(CONFIG)}
