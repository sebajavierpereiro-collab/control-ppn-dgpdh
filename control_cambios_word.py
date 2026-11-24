"""Propuesta 10: copia del documento con las correcciones como CONTROL DE CAMBIOS real de Word (w:ins / w:del).

El asesor o la Dirección abren la copia en Word y aceptan o rechazan cada cambio ahí. El documento analizado no se toca: se
arma una copia aparte, párrafo por párrafo, con el texto propuesto comparado palabra por palabra contra el original. Dentro de
un párrafo con cambios se conserva el formato del primer fragmento de texto (lo mismo que hace la corrección hoy).
"""
import copy
import difflib
import re
from datetime import datetime, timezone

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

AUTOR = "Corrección automática"
_TOKENS = re.compile(r"\s+|[^\s]+")


def _run(texto, rpr, borrado=False):
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    t = OxmlElement("w:delText" if borrado else "w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = texto
    r.append(t)
    return r


def _marca(tipo, ident, autor, fecha):
    m = OxmlElement(tipo)
    m.set(qn("w:id"), str(ident))
    m.set(qn("w:author"), autor)
    m.set(qn("w:date"), fecha)
    return m


def segmentos(original, propuesto):
    """[(tipo, texto)] con tipo igual / quitado / agregado, palabra por palabra (los espacios cuentan como palabra)."""
    a, b = _TOKENS.findall(original or ""), _TOKENS.findall(propuesto or "")
    salida = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            salida.append(("igual", "".join(a[i1:i2])))
        else:
            if i2 > i1:
                salida.append(("quitado", "".join(a[i1:i2])))
            if j2 > j1:
                salida.append(("agregado", "".join(b[j1:j2])))
    return [s for s in salida if s[1]]


def marcar_parrafo(parrafo, original, propuesto, autor=AUTOR, fecha=None, ident=1):
    """Reemplaza los fragmentos del párrafo por la versión con marcas. Devuelve el próximo id libre."""
    fecha = fecha or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    p = parrafo._p
    runs = p.findall(qn("w:r"))
    rpr = runs[0].find(qn("w:rPr")) if runs else None
    for r in runs:
        p.remove(r)
    for tipo, texto in segmentos(original, propuesto):
        if tipo == "igual":
            p.append(_run(texto, rpr))
            continue
        m = _marca("w:del" if tipo == "quitado" else "w:ins", ident, autor, fecha)
        m.append(_run(texto, rpr, borrado=(tipo == "quitado")))
        p.append(m)
        ident += 1
    return ident


def generar(ruta_docx, ruta_salida, propuestas, autor=AUTOR):
    """`propuestas`: las de la revisión ({ubicacion, original, propuesto}). Devuelve cuántos párrafos quedaron marcados."""
    import nucleo
    doc = Document(str(ruta_docx))
    ident, marcados = 1, 0
    for pr in propuestas:
        if not pr.get("propuesto") or pr["propuesto"] == pr["original"]:
            continue
        try:
            parrafo = nucleo._resolver_ubicacion(doc, pr["ubicacion"])
        except (IndexError, ValueError):
            continue
        if parrafo.text != pr["original"]:
            continue  # el párrafo cambió desde el análisis: no se marca a ciegas
        ident = marcar_parrafo(parrafo, pr["original"], pr["propuesto"], autor, ident=ident)
        marcados += 1
    doc.save(str(ruta_salida))
    return marcados
