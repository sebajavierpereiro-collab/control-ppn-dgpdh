"""Juego único de íconos de línea (el mismo trazo que el menú lateral) para usar en el contenido.

En las plantillas: {{ ico('atras') }}. Reemplaza los símbolos de texto y emoji (← → ✓ ✗ ★ ⓘ ↻ ⚠ ▾)."""
from markupsafe import Markup

TRAZOS = {
    "atras": '<path d="M15 18l-6-6 6-6"/>',
    "editar": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>',
    "adelante": '<path d="M9 18l6-6-6-6"/>',
    "abajo": '<path d="M6 9l6 6 6-6"/>',
    "arriba": '<path d="M18 15l-6-6-6 6"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "x": '<path d="M18 6L6 18M6 6l12 12"/>',
    "estrella": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.5v.5"/>',
    "repetir": '<path d="M17 2l3 3-3 3"/><path d="M4 11V9a4 4 0 0 1 4-4h12M7 22l-3-3 3-3"/><path d="M20 13v2a4 4 0 0 1-4 4H4"/>',
    "alerta": '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17v.5"/>',
    "ayuda": '<circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4M12 17v.5"/>',
    "subir": '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    "mas": '<path d="M12 5v14M5 12h14"/>',
    "opciones": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
}


def svg(nombre, clase="", titulo=""):
    trazo = TRAZOS.get(nombre, TRAZOS["info"])
    etiqueta = f' role="img" aria-label="{titulo}"' if titulo else ' aria-hidden="true"'
    return Markup(f'<svg class="ico{(" " + clase) if clase else ""}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                  f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"{etiqueta}>{trazo}</svg>')
