"""
fechas.py — Formato de fechas para mostrar en pantalla.

La base guarda todo en UTC (ISO 8601). Las pantallas muestran hora de
Buenos Aires con formato dd/mm/aaaa HH:MM.
"""
from datetime import datetime, timedelta, timezone

ZONA_HORARIA = "America/Argentina/Buenos_Aires"


def zona():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(ZONA_HORARIA)
    except Exception:
        # Sin base de zonas horarias (Windows sin tzdata): Argentina no usa
        # horario de verano, así que UTC-3 fijo es equivalente.
        return timezone(timedelta(hours=-3))


def a_hora_local(valor, formato="%d/%m/%Y %H:%M"):
    """'2026-10-06T21:00:00+00:00' -> '06/10/2026 18:00'."""
    if not valor:
        return "—"
    try:
        dt = datetime.fromisoformat(str(valor))
    except ValueError:
        return str(valor)[:16]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zona()).strftime(formato)


def fecha_corta(valor):
    """'2026-10-06' (o un ISO completo) -> '06/10/2026'. Es una fecha
    administrativa, sin conversión de zona."""
    if not valor:
        return "—"
    texto = str(valor)[:10]
    try:
        return datetime.strptime(texto, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(valor)
