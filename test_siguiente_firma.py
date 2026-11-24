"""Pruebas para siguiente_firma.py — datos en memoria, sin acceso a la base."""
try:
    import pytest
except ImportError:
    # pytest no disponible: las aserciones del test runner propio lo hacen funcionar igual
    pytest = None

from siguiente_firma import (
    normalizar_persona, normalizar_asunto, es_proveido,
    son_relacionadas, relacionadas_pendientes, siguiente_para_firmar,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def nota(id_, nombre, estado="PROCESADA", prioridad="NORMAL", creado_en="2026-01-01T10:00:00",
         usuario_id=1, bloque_id=None, actualizado_en=None):
    return {
        "id": id_, "nombre_original": nombre, "estado": estado,
        "prioridad": prioridad, "prioridad_efectiva": prioridad,
        "creado_en": creado_en, "usuario_id": usuario_id,
        "bloque_id": bloque_id,
        "actualizado_en": actualizado_en or creado_en,
    }


# ── normalizar_persona ──────────────────────────────────────────────────────

def test_normalizar_persona_basico():
    assert normalizar_persona("GOITEA CRISTIAN GONZALO-Nota PPL-Visitas íntimas.docx") == "GOITEA CRISTIAN GONZALO"

def test_normalizar_persona_sin_tilde():
    assert normalizar_persona("FELCHER ANDRÉS LEONEL FEDERICO-Nota Jefe-Videollamada.docx") == "FELCHER ANDRES LEONEL FEDERICO"

def test_normalizar_persona_sin_guion():
    assert normalizar_persona("SinGuion.docx") is None

def test_normalizar_persona_igual_con_sin_tilde():
    a = normalizar_persona("FELCHER ANDRÉS LEONEL FEDERICO-Nota Jefe-Videollamada.docx")
    b = normalizar_persona("FELCHER ANDRES LEONEL FEDERICO-Proveido-Videollamada.docx")
    assert a == b


# ── es_proveido ─────────────────────────────────────────────────────────────

def test_es_proveido_true():
    assert es_proveido("GOITEA CRISTIAN GONZALO-Proveido-Visitas íntimas.docx")
    assert es_proveido("GOMEZ-Proveído-Comparendo.docx")

def test_es_proveido_false():
    assert not es_proveido("GOITEA-Nota PPL-Visitas íntimas.docx")


# ── son_relacionadas ────────────────────────────────────────────────────────

def test_relacionadas_misma_persona_mismo_usuario():
    a = nota(1, "GOITEA CRISTIAN GONZALO-Nota PPL-Visitas íntimas.docx", usuario_id=5)
    b = nota(2, "GOITEA CRISTIAN GONZALO-Nota Jefe-Visitas íntimas.docx", usuario_id=5)
    assert son_relacionadas(a, b)

def test_relacionadas_misma_persona_ventana():
    a = nota(1, "GOITEA-Nota PPL-Visitas.docx", creado_en="2026-01-01T10:00:00", usuario_id=1)
    b = nota(2, "GOITEA-Nota Jefe-Visitas.docx", creado_en="2026-01-01T11:30:00", usuario_id=2)
    # 90 min < 120 → relacionadas
    assert son_relacionadas(a, b, ventana_min=120)

def test_relacionadas_fuera_de_ventana():
    a = nota(1, "GOITEA-Nota PPL-X.docx", creado_en="2026-01-01T10:00:00", usuario_id=1)
    b = nota(2, "GOITEA-Nota Jefe-Y.docx", creado_en="2026-01-01T14:00:00", usuario_id=2)
    # 240 min > 120, asuntos distintos, usuarios distintos → no relacionadas
    assert not son_relacionadas(a, b, ventana_min=120)

def test_relacionadas_mismo_asunto():
    # Mismo asunto "VIDEOLLAMADA", misma persona (con/sin tilde), usuarios distintos y fuera de ventana
    a = nota(1, "FELCHER ANDRES LEONEL FEDERICO-Nota Jefe-Videollamada.docx", usuario_id=1)
    b = nota(2, "FELCHER ANDRÉS LEONEL FEDERICO-Proveido-Videollamada.docx", usuario_id=2,
             creado_en="2026-01-01T20:00:00")
    assert son_relacionadas(a, b, ventana_min=120)

def test_no_relacionadas_persona_distinta():
    a = nota(1, "GOITEA-Nota PPL-Visitas.docx", usuario_id=5)
    b = nota(2, "GOMEZ BRIAN-Nota Jefe-Visitas.docx", usuario_id=5)
    assert not son_relacionadas(a, b)

def test_felcher_con_sin_tilde():
    a = nota(1, "FELCHER ANDRÉS LEONEL FEDERICO-Nota Jefe-Videollamada.docx", usuario_id=1)
    b = nota(2, "FELCHER ANDRES LEONEL FEDERICO-Proveido-Videollamada.docx", usuario_id=1)
    assert son_relacionadas(a, b)


# ── siguiente_para_firmar ───────────────────────────────────────────────────

def test_siguiente_mismo_bloque():
    firmada = nota(1, "GOITEA-Nota PPL-Visitas.docx", bloque_id=10, creado_en="2026-01-01T09:00:00")
    pend = [
        nota(2, "GOITEA-Nota Jefe-Visitas.docx", bloque_id=10, creado_en="2026-01-01T09:30:00"),
        nota(3, "URGENTE-Sin-Nombre.docx", prioridad="URGENTE", actualizado_en="2026-01-01T08:00:00"),
    ]
    sig_id, motivo = siguiente_para_firmar(firmada, pend, set())
    # El del mismo bloque va primero, aunque haya un URGENTE fuera
    assert sig_id == 2
    assert motivo == "mismo bloque"

def test_siguiente_relacionada_sin_bloque():
    firmada = nota(1, "GOITEA CRISTIAN GONZALO-Nota PPL-Visitas íntimas.docx", usuario_id=5)
    pend = [
        nota(2, "GOITEA CRISTIAN GONZALO-Nota Jefe-Visitas íntimas.docx", usuario_id=5),
        nota(3, "GOITEA CRISTIAN GONZALO-Proveido-Visitas íntimas.docx", usuario_id=5),
    ]
    sig_id, motivo = siguiente_para_firmar(firmada, pend, set())
    # Nota Jefe antes que Proveido
    assert sig_id == 2
    assert motivo == "relacionada"

def test_siguiente_proveido_al_final():
    firmada = nota(1, "GOITEA-Nota Jefe-Visitas.docx", usuario_id=5,
                   creado_en="2026-01-01T10:30:00")
    pend = [
        nota(2, "GOITEA-Proveido-Visitas.docx", usuario_id=5, creado_en="2026-01-01T11:00:00"),
    ]
    sig_id, motivo = siguiente_para_firmar(firmada, pend, set())
    assert sig_id == 2
    assert motivo == "relacionada"

def test_siguiente_urgente_sin_bloque_ni_relacionadas():
    firmada = nota(1, "X-Nota-A.docx", usuario_id=1)
    pend = [
        nota(2, "Y-Nota-B.docx", prioridad="NORMAL", actualizado_en="2026-01-01T08:00:00"),
        nota(3, "Z-Nota-C.docx", prioridad="URGENTE", actualizado_en="2026-01-01T09:00:00"),
    ]
    sig_id, motivo = siguiente_para_firmar(firmada, pend, set())
    assert sig_id == 3
    assert motivo == "urgente"

def test_siguiente_antiguedad_igual_prioridad():
    firmada = nota(1, "X-Nota-A.docx")
    pend = [
        nota(2, "Y-Nota-B.docx", actualizado_en="2026-01-01T12:00:00"),
        nota(3, "Z-Nota-C.docx", actualizado_en="2026-01-01T08:00:00"),
    ]
    sig_id, motivo = siguiente_para_firmar(firmada, pend, set())
    assert sig_id == 3  # más antigua
    assert motivo == "antigüedad"

def test_siguiente_salteada_excluida():
    firmada = nota(1, "X-Nota-A.docx", bloque_id=10)
    pend = [
        nota(2, "X-Nota-B.docx", bloque_id=10),
        nota(3, "X-Nota-C.docx", bloque_id=10),
    ]
    sig_id, _ = siguiente_para_firmar(firmada, pend, salteadas={2})
    assert sig_id == 3

def test_siguiente_sin_pendientes():
    firmada = nota(1, "X-Nota-A.docx")
    sig_id, motivo = siguiente_para_firmar(firmada, [], set())
    assert sig_id is None
    assert motivo == ""

def test_siguiente_firmada_por_otra_persona():
    # Si la nota 2 ya fue firmada (no está en pendientes), no se ofrece
    firmada = nota(1, "GOITEA-Nota PPL-Visitas.docx", bloque_id=10)
    pend = [nota(3, "GOITEA-Nota Jefe-Visitas.docx", bloque_id=10)]
    # nota 2 ausente de pendientes: fue firmada por otra persona
    sig_id, _ = siguiente_para_firmar(firmada, pend, set())
    assert sig_id == 3

def test_siguiente_usuario_sin_permiso():
    # Si la nota no está en la lista de pendientes del usuario, nunca se ofrece
    firmada = nota(1, "X-Nota-A.docx")
    # nota 2 NO está en pendientes (el usuario no tiene permiso)
    pend = [nota(3, "Z-Nota-C.docx")]
    sig_id, _ = siguiente_para_firmar(firmada, pend, set())
    assert sig_id == 3

def test_siguiente_relacionada_en_revision():
    """EN_REVISION también es candidato (caso: 612 firmada → 613 relacionada en revisión)."""
    firmada = nota(612, "FELCHER ANDRES LEONEL FEDERICO-Nota Jefe-Videollamada.docx",
                   estado="FIRMADA", usuario_id=5)
    n613 = nota(613, "FELCHER ANDRES LEONEL FEDERICO-Proveido-Videollamada.docx",
                estado="EN_REVISION", usuario_id=5)
    pend = [n613]
    sig_id, motivo = siguiente_para_firmar(firmada, pend, set())
    assert sig_id == 613
    assert motivo == "relacionada"


def test_goitea_orden_completo():
    # Firmar Nota PPL → sigue Nota Jefe → sigue Proveído
    n_ppl   = nota(1, "GOITEA CRISTIAN GONZALO-Nota PPL-Visitas íntimas.docx",   usuario_id=5, creado_en="2026-01-01T09:00:00")
    n_jefe  = nota(2, "GOITEA CRISTIAN GONZALO-Nota Jefe-Visitas íntimas.docx",  usuario_id=5, creado_en="2026-01-01T09:10:00")
    n_prov  = nota(3, "GOITEA CRISTIAN GONZALO-Proveido-Visitas íntimas.docx",   usuario_id=5, creado_en="2026-01-01T09:20:00")

    pend_tras_ppl = [n_jefe, n_prov]
    sig1, m1 = siguiente_para_firmar(n_ppl, pend_tras_ppl, set())
    assert sig1 == 2 and m1 == "relacionada"

    pend_tras_jefe = [n_prov]
    sig2, m2 = siguiente_para_firmar(n_jefe, pend_tras_jefe, set())
    assert sig2 == 3 and m2 == "relacionada"

    sig3, _ = siguiente_para_firmar(n_prov, [], set())
    assert sig3 is None
