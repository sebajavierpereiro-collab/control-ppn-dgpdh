"""Beta 01 (151): importación de unidades desde CSV con validación previa y mensajes claros.

Nunca llega a la pantalla un error técnico: cada problema se informa con fila, columna y qué hacer.
Primero se valida todo (vista previa); se guarda recién al confirmar, y solo las filas sin errores."""
import csv
import io
import re
import unicodedata

import database as db

ETIQUETAS = {"nombre": "Nombre", "tipo": "Tipo", "zona": "Zona", "ciudad": "Ciudad", "provincia": "Provincia", "fuerza": "Fuerza",
             "correo": "Correo", "direccion": "Dirección", "alias": "Alias", "lat": "Latitud", "lng": "Longitud",
             "telefono_guardia": "Teléfono de guardia", "horarios_visita": "Horarios de visita", "contactos": "Otros contactos",
             "activa": "Activa"}
SINONIMOS = {"unidad": "nombre", "nombre_de_la_unidad": "nombre", "latitud": "lat", "longitud": "lng", "telefono": "telefono_guardia",
             "telefono_de_guardia": "telefono_guardia", "guardia": "telefono_guardia", "horarios": "horarios_visita", "email": "correo",
             "mail": "correo", "e_mail": "correo", "domicilio": "direccion", "localidad": "ciudad"}
COLUMNAS = list(db.CAMPOS_UNIDAD) + ["activa"]


def _clave(encabezado):
    t = unicodedata.normalize("NFD", str(encabezado or "")).encode("ascii", "ignore").decode().strip().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return SINONIMOS.get(t, t)


def _texto(crudo):
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return crudo.decode(cod)
        except UnicodeDecodeError:
            continue
    return crudo.decode("latin-1", errors="replace")


def leer(crudo):
    """Devuelve {'filas': [{'fila', 'datos', 'errores', 'accion'}], 'errores_generales': [...], 'columnas', 'ignoradas'}."""
    res = {"filas": [], "errores_generales": [], "columnas": [], "ignoradas": []}
    if not crudo or not crudo.strip():
        res["errores_generales"].append("El archivo está vacío.")
        return res
    if crudo[:4] == b"PK\x03\x04":
        res["errores_generales"].append("El archivo es un Excel (.xlsx). Guardalo como «CSV (delimitado por punto y coma)» desde Excel y volvé a subirlo.")
        return res
    texto = _texto(crudo[:2_000_000]).replace("\r\n", "\n").replace("\r", "\n")
    primera = next((l for l in texto.split("\n") if l.strip()), "")
    sep = ";" if primera.count(";") >= primera.count(",") else ","
    lector = csv.reader(io.StringIO(texto), delimiter=sep)
    try:
        encabezado = next(lector)
    except (StopIteration, csv.Error):
        res["errores_generales"].append("No se pudo leer la primera fila (los nombres de las columnas).")
        return res
    claves = [_clave(h) for h in encabezado]
    res["columnas"] = [ETIQUETAS.get(c, c) for c in claves if c in COLUMNAS]
    res["ignoradas"] = [h for h, c in zip(encabezado, claves) if c not in COLUMNAS and str(h).strip()]
    if "nombre" not in claves:
        res["errores_generales"].append("Falta la columna «nombre» (o «unidad»). Descargá la plantilla para ver el formato.")
        return res
    existentes = {db._norm(u["nombre"]): u for u in db.listar_unidades(solo_activas=False)}
    vistos = {}
    while True:
        linea = lector.line_num + 1
        try:
            valores = next(lector)
        except StopIteration:
            break
        except csv.Error:
            res["errores_generales"].append(f"Fila {linea}: hay comillas sin cerrar; el resto del archivo no se pudo leer. Revisá esa fila.")
            break
        if not any(str(v).strip() for v in valores):
            continue
        errores = []
        if len(valores) != len(claves):
            errores.append({"columna": "—", "mensaje": f"La fila tiene {len(valores)} columnas y el encabezado {len(claves)}: puede sobrar o faltar un «{sep}»."})
        datos = {}
        for c, v in zip(claves, valores):
            if c not in COLUMNAS:
                continue
            if "\n" in v:
                errores.append({"columna": ETIQUETAS[c], "mensaje": "Se detectó un salto de línea dentro de la celda."})
            datos[c] = re.sub(r"\s+", " ", v).strip()
        if not datos.get("nombre"):
            errores.append({"columna": "Nombre", "mensaje": "Falta el nombre de la unidad."})
        for c, tope in (("lat", 90), ("lng", 180)):
            if datos.get(c):
                try:
                    if abs(float(datos[c].replace(",", "."))) > tope:
                        errores.append({"columna": ETIQUETAS[c], "mensaje": "Está fuera de rango."})
                except ValueError:
                    errores.append({"columna": ETIQUETAS[c], "mensaje": "Tiene que ser un número (ej.: -34.60)."})
        if datos.get("correo") and not all(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", x.strip()) for x in re.split(r"[;,]", datos["correo"]) if x.strip()):
            errores.append({"columna": "Correo", "mensaje": "No es un correo válido (varios, separados por «;»)."})
        n = db._norm(datos.get("nombre") or "")
        if n and n in vistos:
            errores.append({"columna": "Nombre", "mensaje": f"La unidad se repite en el archivo (también en la fila {vistos[n]})."})
        elif n:
            vistos[n] = linea
        res["filas"].append({"fila": linea, "datos": datos, "errores": errores,
                             "accion": "error" if errores else ("actualizar" if n in existentes else "nueva")})
    if not res["filas"] and not res["errores_generales"]:
        res["errores_generales"].append("El archivo no tiene filas con datos debajo del encabezado.")
    return res


def resumen(res):
    f = res["filas"]
    return {"nuevas": sum(1 for x in f if x["accion"] == "nueva"), "actualizar": sum(1 for x in f if x["accion"] == "actualizar"),
            "errores": sum(1 for x in f if x["accion"] == "error")}


def aplicar(res):
    """Guarda solo las filas sin errores. Devuelve (creadas, actualizadas, errores)."""
    validas = [x["datos"] for x in res["filas"] if x["accion"] != "error"]
    return db.importar_unidades(validas)


def plantilla():
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(COLUMNAS[:-1])
    w.writerow(["-CPF I- Complejo Penitenciario Federal I de Ezeiza", "Complejo", "AMBA", "Ezeiza", "Buenos Aires", "SPF",
                "direccion@ejemplo.gob.ar", "Ruta 205 km 45", "CPF I; Ezeiza", "-34.85", "-58.53", "011-0000-0000", "Lunes a viernes 9 a 13", ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def informe_errores(res):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Fila", "Unidad", "Columna", "Problema"])
    for m in res["errores_generales"]:
        w.writerow(["", "", "", m])
    for x in res["filas"]:
        for e in x["errores"]:
            w.writerow([x["fila"], x["datos"].get("nombre", ""), e["columna"], e["mensaje"]])
    return ("﻿" + buf.getvalue()).encode("utf-8")
