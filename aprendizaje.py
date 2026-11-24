"""
aprendizaje.py — Minería de patrones repetidos en las modificaciones del
Director, para sugerir nuevas reglas al diccionario de reemplazos.

Alcance deliberadamente acotado (Tier 1 del plan acordado): NO reentrena
ningún modelo, NO cambia el comportamiento de la IA. Solo compara, para
cada corrección que el Director MODIFICÓ, qué le sacó y qué le puso, y si
el mismo par se repite varias veces entre notas distintas, lo deja listado
para que una persona decida si lo suma al diccionario (ver
/aprendizaje/patrones en main.py).

Deliberadamente fuera de alcance:
- Las decisiones "rechazar" (nota completa) no se usan acá: según lo
  acordado, un rechazo operativo no dice nada sobre redacción.
- Las decisiones "rechazar" de un PÁRRAFO sí son una señal de que algo
  estuvo mal, pero no se minan todavía: el motivo de un rechazo de párrafo
  puede ser muchas cosas (no solo de redacción) y mezclarlo acá generaría
  más ruido que señal. Si hace falta, es una extensión futura separada.
"""
from collections import Counter
from difflib import SequenceMatcher

UMBRAL_REPETICION = 2  # cuántas veces debe repetirse un patrón para sugerirlo
MAX_NOTAS_EJEMPLO = 5


def extraer_pares(texto_propuesto, texto_final):
    """Compara palabra por palabra lo que la corrección automática propuso
    contra lo que el Director dejó finalmente, y devuelve los fragmentos
    que cambiaron como pares (frase_que_sacó, frase_que_puso). Es un diff
    literal de texto, no un análisis semántico: agrupa ediciones
    textualmente iguales, no conceptos parecidos dichos distinto."""
    palabras_a = texto_propuesto.split()
    palabras_b = texto_final.split()
    sm = SequenceMatcher(None, palabras_a, palabras_b, autojunk=False)
    pares = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        quitada = " ".join(palabras_a[i1:i2]).strip().lower()
        puesta = " ".join(palabras_b[j1:j2]).strip().lower()
        if quitada and puesta and quitada != puesta:
            pares.append((quitada, puesta))
    return pares


def analizar_patrones(correcciones_modificadas, ya_aprobadas=frozenset()):
    """`correcciones_modificadas` es una lista de dicts con, al menos,
    nota_id, texto_propuesto, texto_final (filas de correcciones_director
    con decision == "modificado"). `ya_aprobadas` es el conjunto de pares
    (quitada, puesta) que ya se sumaron al diccionario, para no volver a
    sugerirlos.

    Devuelve los pares que aparecen al menos UMBRAL_REPETICION veces,
    ordenados de más a menos frecuentes, cada uno con hasta
    MAX_NOTAS_EJEMPLO ids de nota donde se vio (para poder ir a revisar el
    contexto real antes de aprobar la regla)."""
    contador = Counter()
    notas_por_par = {}

    for c in correcciones_modificadas:
        for par in extraer_pares(c["texto_propuesto"], c["texto_final"]):
            if par in ya_aprobadas:
                continue
            contador[par] += 1
            notas_por_par.setdefault(par, [])
            if c["nota_id"] not in notas_por_par[par]:
                notas_por_par[par].append(c["nota_id"])

    patrones = [
        {
            "quitada": quitada,
            "puesta": puesta,
            "veces": veces,
            "notas_ejemplo": notas_por_par[(quitada, puesta)][:MAX_NOTAS_EJEMPLO],
        }
        for (quitada, puesta), veces in contador.items()
        if veces >= UMBRAL_REPETICION
    ]
    patrones.sort(key=lambda p: p["veces"], reverse=True)
    return patrones
