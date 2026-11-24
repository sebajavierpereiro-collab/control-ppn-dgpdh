"""Registro persistente de requests lentos (> umbral_segundos).
Se complementa con el registro en memoria que ya tiene main.py.
"""
import json
from datetime import datetime
from pathlib import Path

CARPETA = Path("datos/latencia")
CARPETA.mkdir(parents=True, exist_ok=True)
UMBRAL_DEFAULT = 2.0
MAX_ENTRADAS = 500


def registrar(ruta: str, metodo: str, duracion_ms: float,
              codigo_http: int = 200, contexto: dict = None,
              umbral: float = UMBRAL_DEFAULT):
    """Llamar después de cada request. Solo guarda si supera el umbral."""
    if duracion_ms / 1000 < umbral:
        return
    entrada = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "ruta": ruta,
        "metodo": metodo,
        "duracion_ms": round(duracion_ms),
        "codigo_http": codigo_http,
        "contexto": contexto or {},
    }
    archivo = CARPETA / "lentos.jsonl"
    with archivo.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    _rotar(archivo)


def _rotar(archivo: Path):
    lineas = archivo.read_text(encoding="utf-8").splitlines()
    if len(lineas) > MAX_ENTRADAS:
        archivo.write_text("\n".join(lineas[-MAX_ENTRADAS:]) + "\n", encoding="utf-8")


def listar_lentos(limite: int = 100, umbral_ms: float = 2000) -> list:
    archivo = CARPETA / "lentos.jsonl"
    if not archivo.is_file():
        return []
    entradas = []
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(linea)
            if e.get("duracion_ms", 0) >= umbral_ms:
                entradas.append(e)
        except Exception:
            continue
    return sorted(entradas, key=lambda x: x["duracion_ms"], reverse=True)[:limite]
