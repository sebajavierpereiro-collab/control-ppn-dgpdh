"""Vista de «control de cambios» de las correcciones: lo agregado en verde, lo quitado en rojo tachado, palabra por palabra,
con cada tramo clasificado (ortografía, gramática o reformulación) y los totales. No cambia nada del documento: es solo
cómo se muestran los cambios ya propuestos o aplicados."""
import re
import unicodedata
from difflib import SequenceMatcher

CLASES = {"ortografia": "Ortografía", "gramatica": "Gramática y puntuación", "reformulacion": "Reformulación / estilo"}
FUENTES = {"diccionario": "Diccionario", "languagetool": "Corrector ortográfico y gramatical", "ia_ollama": "IA", "informe": "Desde el informe",
           "reglas": "Regla de estilo", "estilo": "Regla de estilo", "director (manual)": "Edición manual", "manual": "Edición manual"}
_TOKEN = re.compile(r"\w+|[^\w\s]|\s+", re.UNICODE)
_CORTAS = {"a", "al", "de", "del", "el", "la", "los", "las", "un", "una", "y", "e", "o", "u", "que", "en", "por", "para", "con", "se", "lo", "su", "sus"}


def _base(t):
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn" and not c.isspace())


def _palabras(tokens):
    return [t for t in tokens if t.strip()]


def clasificar_tramo(quitado, agregado):
    """ortografia | gramatica | reformulacion para un tramo cambiado."""
    q, a = _palabras(quitado), _palabras(agregado)
    letras = lambda ts: _base("".join(t for t in ts if re.search(r"\w", t)))
    if q and a and letras(q) and letras(q) == letras(a):
        # mismas letras: cambió solo acento, mayúscula o puntuación pegada a la palabra
        return "ortografia" if any(re.search(r"\w", t) for t in q + a) and [t for t in q if re.search(r"\w", t)] != [t for t in a if re.search(r"\w", t)] else "gramatica"
    if q and a and len(q) == len(a) and len(q) <= 2:
        if all(_base(x) == _base(y) or SequenceMatcher(None, _base(x), _base(y)).ratio() >= 0.7 for x, y in zip(q, a)):
            return "ortografia"  # acentos, mayúsculas, letras cambiadas
    if all(re.fullmatch(r"[^\w\s]", t) for t in q + a):
        return "gramatica"  # solo puntuación
    if len(q) + len(a) <= 3 and all(_base(t) in _CORTAS or re.fullmatch(r"[^\w\s]", t) for t in q + a):
        return "gramatica"  # artículos, preposiciones, concordancias cortas
    return "reformulacion"


def _unir(ops, a):
    """Une los cambios separados solo por un espacio («a la brevedad» → «en el plazo») para leerlos como un tramo."""
    salida = []
    for op in ops:
        if salida and op[0] != "equal" and len(salida) >= 2 and salida[-1][0] == "equal" and salida[-2][0] != "equal" \
                and not "".join(a[salida[-1][1]:salida[-1][2]]).strip():
            eq, prev = salida.pop(), salida.pop()
            salida.append(("replace", prev[1], op[2], prev[3], op[4]))
        else:
            salida.append(op)
    return salida


def diferencias(original, corregido):
    """Segmentos [{'tipo': 'igual'|'quitado'|'agregado', 'texto', 'clase'}] y conteo por clase."""
    a, b = _TOKEN.findall(original or ""), _TOKEN.findall(corregido or "")
    segmentos, conteo = [], {k: 0 for k in CLASES}
    for op, i1, i2, j1, j2 in _unir(SequenceMatcher(None, a, b, autojunk=False).get_opcodes(), a):
        if op == "equal":
            segmentos.append({"tipo": "igual", "texto": "".join(a[i1:i2]), "clase": ""})
            continue
        quitado, agregado = a[i1:i2], b[j1:j2]
        if not _palabras(quitado) and not _palabras(agregado):  # solo espacios
            segmentos.append({"tipo": "igual", "texto": "".join(agregado), "clase": ""})
            continue
        clase = clasificar_tramo(quitado, agregado)
        conteo[clase] += 1
        if quitado:
            segmentos.append({"tipo": "quitado", "texto": "".join(quitado), "clase": clase})
        if agregado:
            segmentos.append({"tipo": "agregado", "texto": "".join(agregado), "clase": clase})
    return segmentos, conteo


def etiqueta_fuente(fuente):
    partes = [p.strip() for p in str(fuente or "").split("+") if p.strip()]
    return " + ".join(FUENTES.get(p, p.capitalize()) for p in partes) or "—"


def vista(cambios, clave_original="original", clave_nuevo="corregido"):
    """Para una lista de cambios ({fuente, original, corregido}): filas con segmentos y los totales."""
    filas, total = [], {k: 0 for k in CLASES}
    for c in cambios or []:
        seg, cuenta = diferencias(c.get(clave_original), c.get(clave_nuevo))
        for k, v in cuenta.items():
            total[k] += v
        filas.append({"fuente": etiqueta_fuente(c.get("fuente")), "segmentos": seg, "conteo": cuenta})
    return {"filas": filas, "conteo": total, "total": sum(total.values())}
