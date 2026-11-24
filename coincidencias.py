"""Motor de coincidencias: relaciona documentos con personas detenidas y expedientes.
Versión 1.0. Solo sugiere; la confirmación siempre la hace un humano.
"""
import json
import logging
import re
import unicodedata

import database as db

ALGORITMO = "v1"
_MIN_TEXTO = 50


def _normalizar_tokens(s: str) -> set:
    if not s:
        return set()
    nfkd = unicodedata.normalize("NFD", s)
    ascii_s = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    tokens = re.sub(r"[^a-z0-9 ]", " ", ascii_s.lower()).split()
    return set(tokens) - {"de", "del", "la", "las", "los", "el", "y"}


def _similitud_nombre(a: str, b: str) -> float:
    ta, tb = _normalizar_tokens(a), _normalizar_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _dnis_del_texto(texto: str) -> list:
    patrones = [
        r'\bD\.?N\.?I\.?[:\s#]*(\d{2}[\.\s]?\d{3}[\.\s]?\d{3})\b',
        r'\b(\d{2}[\.\s]?\d{3}[\.\s]?\d{3})\b',
    ]
    dnis = []
    for p in patrones:
        for m in re.finditer(p, texto, re.IGNORECASE):
            d = re.sub(r'[\s\.]', '', m.group(1))
            if len(d) in (7, 8) and d not in dnis:
                dnis.append(d)
    return dnis[:5]


def _lpus_del_texto(texto: str) -> list:
    try:
        import agentes
        return [str(lpu) for lpu, _, _ in (agentes.lpus_del_texto(texto) or [])]
    except Exception:
        pass
    lpus = []
    for m in re.finditer(r'\bL\.?P\.?U\.?\s*[:#N°]?\s*(\d{5,7})\b', texto, re.IGNORECASE):
        n = m.group(1)
        if n not in lpus:
            lpus.append(n)
    return lpus[:5]


def _id_evento_del_archivo(nombre: str) -> str | None:
    """Número al final del nombre = ID del evento en el sistema externo (SER/Menú PPN)."""
    m = re.search(r'[-_](\d+)\.\w+$', nombre or "")
    return m.group(1) if m else None


def _id_evento_del_texto(texto: str) -> str | None:
    m = re.search(r'(?:^|\n)\s*ID\s+(\d+)\s*(?:\n|$)', texto)
    return m.group(1) if m else None


def _nombre_del_texto(texto: str) -> str:
    """Extrae nombre del formato 'Detalle de evento' (campos rotulados)."""
    m = re.search(r'Apellido\s+([A-ZÁÉÍÓÚÑ ]{2,40})\s+Nombre\s+([A-ZÁÉÍÓÚÑ ]{2,40})', texto)
    if m:
        return f"{m.group(1).strip()} {m.group(2).strip()}"
    return ""


def _transposicion(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    difs = [(i, a[i], b[i]) for i in range(len(a)) if a[i] != b[i]]
    if len(difs) == 1:
        return True
    if len(difs) == 2:
        i0, i1 = difs[0][0], difs[1][0]
        if i1 == i0 + 1 and a[i0] == b[i1] and a[i1] == b[i0]:
            return True
    return False


def _obtener_texto(nota_id: int) -> str:
    with db._conexion() as con:
        row = con.execute(
            "SELECT contenido FROM notas_texto WHERE nota_id=?", (nota_id,)
        ).fetchone()
    return (row["contenido"] or "").strip() if row else ""


def _extraer_texto_pdf(nota: dict) -> str:
    from pathlib import Path
    ruta = Path(nota.get("ruta_original") or "")
    if not ruta.is_file():
        return ""
    try:
        from pypdf import PdfReader
        paginas = PdfReader(str(ruta)).pages[:30]
        texto = ""
        for p in paginas:
            try:
                t = p.extract_text(extraction_mode="layout")
            except Exception:
                t = p.extract_text()
            texto += (t or "") + "\n"
        return texto.strip()
    except Exception as e:
        logging.debug(f"coincidencias: no se pudo extraer texto del PDF {nota.get('id')}: {e}")
        return ""


def _buscar_detenidos(lpus: list, dnis: list) -> dict:
    vistos = {}
    with db._conexion() as con:
        for lpu in lpus[:3]:
            for row in con.execute(
                "SELECT * FROM detenidos WHERE (lpu=? OR lpu_norm=?) AND (motivo_baja IS NULL OR motivo_baja NOT LIKE 'Unificada%')",
                (lpu, lpu),
            ).fetchall():
                vistos[row["id"]] = dict(row)
        for dni in dnis[:3]:
            for row in con.execute(
                "SELECT * FROM detenidos WHERE dni=? AND (motivo_baja IS NULL OR motivo_baja NOT LIKE 'Unificada%')",
                (dni,),
            ).fetchall():
                if row["id"] not in vistos:
                    vistos[row["id"]] = dict(row)
    return vistos


def _buscar_expedientes(detenido: dict) -> list:
    exptes = []
    if detenido.get("nro_expediente_ppn"):
        exptes.append({"expte": detenido["nro_expediente_ppn"],
                       "fuente": "detenidos", "fila": None})
    try:
        import expedientes as _exp
        lpu = detenido.get("lpu") or ""
        if lpu and _exp.estado().get("cantidad"):
            for e in _exp.buscar_lpu(lpu)[0] or []:
                for col in ("expediente", "nro_expediente", "expte"):
                    n = str(e.get("datos", {}).get(col) or "").strip()
                    if n and n not in {x["expte"] for x in exptes}:
                        exptes.append({"expte": n,
                                       "fuente": f"planilla {e.get('archivo', '')}",
                                       "fila": e.get("fila")})
                        break
    except Exception:
        pass
    return exptes


def _calcular_nivel(datos: dict, detenido: dict) -> tuple:
    lpu_doc = datos.get("lpu")
    dnis_doc = datos.get("dnis", [])
    nombre_doc = datos.get("nombre", "")
    lpu_base = str(detenido.get("lpu") or "")
    dni_base = str(detenido.get("dni") or "")
    nombre_base = detenido.get("nombre_completo") or ""

    lpu_ok = bool(lpu_doc and lpu_base and lpu_doc == lpu_base)
    dni_ok = any(d == dni_base for d in dnis_doc)
    dni_tr = not dni_ok and any(_transposicion(d, dni_base) for d in dnis_doc)
    sim = _similitud_nombre(nombre_doc, nombre_base) if (nombre_doc or nombre_base) else 0.0
    nombre_ok = sim >= 0.75
    nombre_parcial = 0.4 <= sim < 0.75

    # Nivel 1: LPU + (nombre OK o DNI exacto)
    if lpu_ok and (nombre_ok or dni_ok):
        razon = "nombre" if nombre_ok else "DNI"
        return 1, 98, f"LPU {lpu_doc} exacto y {razon} coinciden"
    # Nivel 2: DNI + nombre OK
    if dni_ok and nombre_ok:
        return 2, 95, f"DNI exacto y nombre coincide"
    # Nivel 3: LPU + nombre OK + DNI transposición
    if lpu_ok and nombre_ok and dni_tr:
        dni_doc = next(d for d in dnis_doc if _transposicion(d, dni_base))
        return 3, 95, f"LPU exacto, nombre coincide; DNI {dni_doc} ≠ base {dni_base} (posible error de tipeo)"
    # Nivel 4: LPU solo, o DNI solo
    if lpu_ok:
        return 4, 85, f"LPU {lpu_doc} exacto (nombre sin confirmar)"
    if dni_ok:
        return 4, 85, f"DNI exacto (nombre sin confirmar)"
    # Nivel 5: nombre exacto (Jaccard >= 0.75), necesita una sola persona
    if nombre_ok:
        return 5, 80, f"Nombre '{nombre_doc}' coincide con '{nombre_base}'"
    # Nivel 6: nombre parcial
    if nombre_parcial:
        return 6, int(50 + sim * 50), f"Nombre parcialmente coincide ({int(sim*100)}%)"
    return 7, 20, "Sin campo clave coincidente"


def proponer(nota_id: int) -> list:
    """Calcula candidatos para una nota. No guarda nada. Retorna lista de dicts."""
    nota = db.obtener_nota(nota_id)
    if nota is None:
        return []

    texto = _obtener_texto(nota_id)
    if len(texto) < _MIN_TEXTO and db.es_pdf(nota):
        texto = _extraer_texto_pdf(nota)

    if len(texto.strip()) < _MIN_TEXTO:
        return []

    lpus = _lpus_del_texto(texto)
    dnis = _dnis_del_texto(texto)
    nombre = _nombre_del_texto(texto)
    id_evento = _id_evento_del_texto(texto) or _id_evento_del_archivo(nota.get("nombre_original", ""))

    if not lpus and not dnis and not nombre:
        return []

    datos = {"lpu": lpus[0] if lpus else None, "dnis": dnis,
             "nombre": nombre, "id_evento": id_evento}

    vistos = _buscar_detenidos(lpus, dnis)
    if not vistos:
        return []

    # Detectar conflicto: LPU apunta a persona A, DNI a persona B distinta
    ids_por_lpu = set()
    ids_por_dni = set()
    with db._conexion() as con:
        for lpu in lpus[:3]:
            for row in con.execute("SELECT id FROM detenidos WHERE lpu=? OR lpu_norm=?", (lpu, lpu)).fetchall():
                ids_por_lpu.add(row["id"])
        for dni in dnis[:3]:
            for row in con.execute("SELECT id FROM detenidos WHERE dni=?", (dni,)).fetchall():
                ids_por_dni.add(row["id"])
    hay_conflicto = bool(ids_por_lpu and ids_por_dni and not (ids_por_lpu & ids_por_dni))

    resultados = []
    for det_id, det in vistos.items():
        nivel, confianza, motivo = _calcular_nivel(datos, det)
        if confianza < 25:
            continue
        exptes = _buscar_expedientes(det)
        aprendizaje = db.aprendizaje_coincidencias(det_id)
        if aprendizaje > 0:
            confianza = min(confianza + 2, 100)
            motivo += f"; vinculado en {aprendizaje} documento(s) previo(s)"
        if hay_conflicto:
            confianza = min(confianza, 60)
            motivo = "⚠ Conflicto: revisar — LPU y DNI apuntan a personas distintas. " + motivo

        resultados.append({
            "detenido_id": det_id,
            "detenido": det,
            "nivel": nivel,
            "confianza": confianza,
            "motivo": motivo,
            "datos_doc": datos,
            "expte_propuesto": exptes[0]["expte"] if exptes else None,
            "fuente_expte": exptes[0]["fuente"] if exptes else None,
            "todos_exptes": exptes,
            "id_evento": id_evento,
            "conflicto": hay_conflicto,
        })

    return sorted(resultados, key=lambda x: (-x["confianza"], x["nivel"]))


def buscar_y_guardar(nota_id: int) -> int:
    """Corre proponer() y persiste en nota_coincidencias. Retorna cantidad guardada."""
    candidatos = proponer(nota_id)
    guardados = 0
    for c in candidatos:
        try:
            db.guardar_coincidencia(
                nota_id=nota_id,
                detenido_id=c["detenido_id"],
                expte_propuesto=c["expte_propuesto"],
                fuente_expte=c["fuente_expte"],
                nivel=c["nivel"],
                confianza=c["confianza"],
                motivo=c["motivo"],
                datos_doc=json.dumps(c["datos_doc"], ensure_ascii=False),
                algoritmo=ALGORITMO,
            )
            guardados += 1
        except Exception as e:
            logging.warning(f"coincidencias nota {nota_id}: {e}")
    return guardados
