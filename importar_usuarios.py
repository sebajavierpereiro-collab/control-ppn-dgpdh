"""
importar_usuarios.py — Alta masiva de equipos y usuarios desde una planilla .xlsx.

Uso (desde la carpeta de la app, con el entorno donde corre la app):
    python importar_usuarios.py usuarios_base.xlsx --simular     # solo muestra qué haría
    python importar_usuarios.py usuarios_base.xlsx               # lo hace

Columnas esperadas: Nombre, Apellido, Email, Rol, Area (con títulos en cualquier orden, o en ese orden).
También se usa desde la pantalla Administración → Usuarios → Importar.
  * usuario = el email completo (en minúsculas)
  * clave   = 12345678 (provisoria: la app obliga a cambiarla en el primer ingreso)
  * cada Área se crea como Equipo (si no existe)
Rol de la planilla -> rol del sistema:
  Asesor / coordinador            -> ASESOR / COORDINADOR (con el equipo del Área)
  Asesor / coordinador de "Mesa de Entradas..." -> MESA_ASESOR / MESA_COORDINADOR (sin equipo)
  director general                -> DIRECTOR (el primero) y SUBDIRECTOR (el segundo); sin equipo
Es repetible: los emails que ya existen se saltean.
"""
import argparse
import re
import sys
from pathlib import Path

import openpyxl

import auth
import database as db

CLAVE_PROVISORIA = "12345678"


def _limpio(valor):
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def _rol_del_sistema(rol_planilla, area, ya_hay_director):
    rol = _limpio(rol_planilla).lower()
    es_mesa = _limpio(area).lower().startswith("mesa de entrada")
    if rol.startswith("director"):
        return auth.ROL_SUBDIRECTOR if ya_hay_director else auth.ROL_DIRECTOR
    if es_mesa:
        return auth.ROL_MESA_COORDINADOR if rol.startswith("coordinador") else auth.ROL_MESA_ASESOR
    if rol.startswith("coordinador"):
        return auth.ROL_COORDINADOR
    if rol.startswith("asesor"):
        return auth.ROL_ASESOR
    raise ValueError(f"Rol desconocido: {rol_planilla!r}")


COLUMNAS = ("nombre", "apellido", "email", "rol", "area")
_ALIAS = {"correo": "email", "mail": "email", "e-mail": "email", "área": "area", "equipo": "area", "rol en el sistema": "rol"}


def leer_planilla(origen):
    """Filas [(nombre, apellido, email, rol, area)] de un .xlsx (ruta o bytes). Si la primera fila tiene los títulos
    (en cualquier orden: Nombre, Apellido, Email, Rol, Área), se usan; si no, el orden de las columnas."""
    import io
    libro = openpyxl.load_workbook(io.BytesIO(origen) if isinstance(origen, (bytes, bytearray)) else origen, read_only=True, data_only=True)
    try:
        filas = list(libro.active.iter_rows(values_only=True))
    finally:
        libro.close()  # en Windows, si no se cierra, el archivo queda tomado
    if not filas:
        return []
    titulos = [_ALIAS.get(_limpio(t).lower(), _limpio(t).lower()) for t in filas[0]]
    if set(COLUMNAS) <= set(titulos):
        idx = [titulos.index(c) for c in COLUMNAS]
        return [tuple(f[i] if i < len(f) else None for i in idx) for f in filas[1:]]
    return [tuple((list(f) + [None] * 5)[:5]) for f in filas[1:]]


def procesar(filas, simular=True):
    """Crea (o con `simular`, solo calcula) equipos y usuarios. Devuelve un resumen con las listas para mostrar."""
    db.inicializar_db()
    equipos = {e["nombre"].lower(): e["id"] for e in db.listar_equipos()}
    existentes = {u["usuario"].lower() for u in db.listar_usuarios()}
    hay_director = any(u["rol"] == auth.ROL_DIRECTOR and u["activo"] for u in db.listar_usuarios() if u["usuario"] != "director")
    nuevos, salteados, equipos_nuevos, errores = [], [], [], []
    for n, fila in enumerate(filas, start=2):
        nombre, apellido, email, rol, area = (list(fila) + [None] * 5)[:5]
        email = _limpio(email).lower()
        if not email:
            continue
        try:
            if "@" not in email:
                raise ValueError("email inválido")
            rol_sis = _rol_del_sistema(rol, area, hay_director)
            if rol_sis == auth.ROL_DIRECTOR:
                hay_director = True
            area_l = _limpio(area)
            equipo_id = None
            if rol_sis in auth.ROLES_DE_EQUIPO:
                if not area_l:
                    raise ValueError("un Asesor o Coordinador necesita Área")
                clave_eq = area_l.lower()
                if clave_eq not in equipos:
                    equipos[clave_eq] = -1 if simular else db.crear_equipo(area_l)
                    equipos_nuevos.append(area_l)
                equipo_id = equipos[clave_eq]
            if email in existentes:
                salteados.append(email)
                continue
            nombre_c = f"{_limpio(nombre)} {_limpio(apellido)}".strip() or email
            if not simular:
                h, s = auth.generar_hash_clave(CLAVE_PROVISORIA)
                db.crear_usuario(email, h, s, nombre_c, rol_sis, equipo_id if equipo_id != -1 else None, debe_cambiar_clave=True)
            existentes.add(email)
            nuevos.append({"usuario": email, "nombre": nombre_c, "rol": rol_sis, "rol_texto": auth.ETIQUETAS_ROL.get(rol_sis, rol_sis),
                           "equipo": area_l if equipo_id else "—"})
        except Exception as e:
            errores.append(f"Fila {n} ({email or 'sin email'}): {e}")
    por_rol = {}
    for x in nuevos:
        por_rol[x["rol_texto"]] = por_rol.get(x["rol_texto"], 0) + 1
    return {"nuevos": nuevos, "salteados": salteados, "equipos_nuevos": equipos_nuevos, "errores": errores,
            "por_rol": sorted(por_rol.items()), "simulado": simular}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("planilla")
    ap.add_argument("--simular", action="store_true")
    args = ap.parse_args()
    r = procesar(leer_planilla(args.planilla), simular=args.simular)
    print(("SIMULACIÓN — no se guardó nada\n" if args.simular else "") +
          f"Equipos nuevos: {len(r['equipos_nuevos'])} | usuarios nuevos: {len(r['nuevos'])} | ya existían: {len(r['salteados'])} | errores: {len(r['errores'])}")
    for rol, c in r["por_rol"]:
        print(f"  {rol}: {c}")
    for e in r["errores"]:
        print("ERROR", e)
    return 1 if r["errores"] else 0


if __name__ == "__main__":
    sys.exit(main())
