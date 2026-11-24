"""Avisos por correo y resumen diario. APAGADO por defecto.

Se activa desde Administración → Estado del sistema → Correo, y necesita la
sección [correo] de config.ini (servidor SMTP). Dos modos:
  * resumen diario (por defecto): una vez al día, a la hora configurada, un
    mail por persona con lo que tiene sin leer;
  * inmediatos: un mail por cada aviso (se activa aparte).
Solo se manda a quien tiene correo cargado. Nunca se adjuntan documentos.
"""
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage

import database as db
import fechas

CONFIG = {"servidor": "", "puerto": 587, "usuario": "", "clave": "", "remitente": "", "tls": True,
          "resumen_hora": "08:00", "url_base": ""}


def configurar(cfg):
    if not cfg.has_section("correo"):
        return
    g = lambda k, d="": cfg.get("correo", k, fallback=d).strip()
    CONFIG.update(servidor=g("servidor"), usuario=g("usuario"), clave=cfg.get("correo", "clave", fallback=""),
                  remitente=g("remitente") or g("usuario"), resumen_hora=g("resumen_hora", "08:00"),
                  url_base=g("url_base").rstrip("/"))
    CONFIG["puerto"] = cfg.getint("correo", "puerto", fallback=587)
    CONFIG["tls"] = cfg.getboolean("correo", "tls", fallback=True)


def activo():
    return db.obtener_ajuste("correo_activo") == "1"


def inmediatos():
    return db.obtener_ajuste("correo_inmediatos") == "1"


def configurado():
    return bool(CONFIG["servidor"] and CONFIG["remitente"])


def enviar(destino, asunto, cuerpo):
    """Manda un mail. Levanta excepción si falla (el que llama decide qué hacer)."""
    if not configurado():
        raise RuntimeError("Falta configurar la sección [correo] de config.ini")
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = CONFIG["remitente"], destino, asunto
    m.set_content(cuerpo)
    with smtplib.SMTP(CONFIG["servidor"], CONFIG["puerto"], timeout=30) as s:
        if CONFIG["tls"]:
            s.starttls()
        if CONFIG["usuario"]:
            s.login(CONFIG["usuario"], CONFIG["clave"])
        s.send_message(m)


def _link(url):
    return f"{CONFIG['url_base']}{url}" if CONFIG["url_base"] and url else ""


def armar_resumen(nombre, items):
    lineas = [f"Hola {nombre}, esto es lo que tenés sin leer en Notas DGPDH:", ""]
    for texto, url in items[:30]:
        lineas.append(f"- {texto}" + (f"\n  {_link(url)}" if _link(url) else ""))
    if len(items) > 30:
        lineas.append(f"... y {len(items) - 30} más.")
    return "\n".join(lineas)


def _hora_ok(ahora):
    try:
        h, m = CONFIG["resumen_hora"].split(":")
        return (ahora.hour, ahora.minute) >= (int(h), int(m))
    except ValueError:
        return False


def tick(ahora=None, enviar_fn=None):
    """Lo llama el trabajador de fondo cada minuto. Devuelve cuántos mails mandó."""
    if not activo() or not configurado():
        return 0
    enviar_fn = enviar_fn or enviar
    ahora = ahora or datetime.now(fechas.zona())
    enviados = 0
    pendientes = db.correos_pendientes()
    hechos = []
    for c in pendientes:
        try:
            enviar_fn(c["correo"], c["asunto"], c["cuerpo"] + (f"\n{_link(c['url'])}" if _link(c["url"]) else ""))
            hechos.append(c["id"]); enviados += 1
        except Exception as e:
            logging.warning(f"No se pudo enviar el aviso {c['id']} a {c['correo']}: {e}")
            break  # si el servidor no responde, se reintenta en el próximo ciclo
    db.marcar_correos_enviados(hechos)
    hoy = ahora.date().isoformat()
    if _hora_ok(ahora) and db.obtener_ajuste("correo_ultimo_resumen") != hoy:
        usuarios = {u["id"]: u for u in db.listar_usuarios() if u.get("correo") and u["activo"]}
        for uid, items in db.resumen_pendientes_por_usuario().items():
            u = usuarios.get(uid)
            if not u:
                continue
            try:
                enviar_fn(u["correo"], "Resumen diario — Notas DGPDH", armar_resumen(u["nombre"], items))
                enviados += 1
            except Exception as e:
                logging.warning(f"No se pudo enviar el resumen a {u['correo']}: {e}")
        db.guardar_ajuste("correo_ultimo_resumen", hoy)
    return enviados
