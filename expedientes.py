"""Planillas de expedientes (Excel): SOLO LECTURA.

Se leen los .xls/.xlsx de la carpeta configurada en config.ini
([expedientes] carpeta). Nunca se escribe ni se modifica nada ahí. Las
columnas no se suponen: se toma como encabezado la primera fila con varios
textos y cada registro se muestra con los nombres de columna de la planilla.
Los .xls necesitan `xlrd` (pip install xlrd); los .xlsx, `openpyxl`.

Mejoras nov. 2026: la Dirección puede importar planillas (.xlsx, .xls o .csv) desde la pantalla. Se guardan en una
carpeta propia del sistema (`importadas`, dentro de datos/), nunca en la carpeta configurada, y se consultan junto con
las demás. Importar otra con el mismo nombre la reemplaza (así se actualiza).
"""
import csv
import logging
import re
import shutil
import unicodedata
from pathlib import Path

CONFIG = {"carpeta": "", "importadas": ""}
EXTENSIONES = (".xls", ".xlsx", ".csv")
MAX_BYTES_IMPORTACION = 20 * 1024 * 1024
_cache = {"firma": None, "registros": [], "archivos": [], "errores": []}
MAX_RESULTADOS = 50


def configurar(cfg, carpeta_importadas=None):
    try:
        CONFIG["carpeta"] = cfg.get("expedientes", "carpeta", fallback="").strip()
    except Exception:
        CONFIG["carpeta"] = ""
    if carpeta_importadas is not None:
        CONFIG["importadas"] = str(carpeta_importadas)


def _sin_acentos(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm_texto(s):
    return re.sub(r"\s+", " ", _sin_acentos(str(s)).lower()).strip()


def norm_num(s):
    """Solo letras y números en minúscula: 12.345/26 == 12345-26."""
    return re.sub(r"[^0-9a-z]", "", _sin_acentos(str(s)).lower())


def _celda(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _filas_xls(ruta):
    try:
        import xlrd
    except ImportError:
        raise RuntimeError("Falta xlrd para leer archivos .xls (pip install xlrd)")
    libro = xlrd.open_workbook(str(ruta), on_demand=True, formatting_info=False)
    hoja = libro.sheet_by_index(0)
    return [[_celda(c) for c in hoja.row_values(i)] for i in range(hoja.nrows)]


def _filas_xlsx(ruta):
    import openpyxl
    libro = openpyxl.load_workbook(str(ruta), read_only=True, data_only=True)
    try:
        return [[_celda(c) for c in fila] for fila in libro.worksheets[0].iter_rows(values_only=True)]
    finally:
        libro.close()


def _filas_csv(ruta):
    """CSV con el separador que use la planilla (; , o tabulación), en UTF-8 o, si no, Latin-1 (Excel en castellano)."""
    datos = Path(ruta).read_bytes()
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = datos.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialecto = csv.Sniffer().sniff(texto[:5000], delimiters=";,\t|")
    except csv.Error:
        class dialecto(csv.excel):
            delimiter = ";" if texto[:5000].count(";") > texto[:5000].count(",") else ","
    return [[_celda(c) for c in fila] for fila in csv.reader(texto.splitlines(), dialecto)]


def _leer_filas(ruta):
    ext = Path(ruta).suffix.lower()
    if ext == ".xls":
        return _filas_xls(ruta)
    if ext == ".csv":
        return _filas_csv(ruta)
    return _filas_xlsx(ruta)


def leer_planilla(ruta, nombre=None):
    ruta = Path(ruta)
    filas = _leer_filas(ruta)
    archivo = nombre or ruta.name
    idx = next((i for i, f in enumerate(filas) if sum(1 for c in f if c) >= 2), None)
    if idx is None:
        return []
    enc = [c or f"Columna {j + 1}" for j, c in enumerate(filas[idx])]
    registros = []
    for n, f in enumerate(filas[idx + 1:], start=idx + 2):
        if not any(f):
            continue
        datos = {enc[j]: v for j, v in enumerate(f[: len(enc)]) if v}
        registros.append({"archivo": archivo, "fila": n, "datos": datos,
                          "_num": [norm_num(v) for v in datos.values()],
                          "_txt": norm_text_join(datos.values())})
    return registros


def norm_text_join(valores):
    return norm_texto(" | ".join(valores))


def _planillas_en(carpeta, extensiones):
    if not carpeta:
        return []
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.iterdir()
                  if p.is_file() and p.suffix.lower() in extensiones and not p.name.startswith("~$"))


def _archivos():
    """Las de la carpeta configurada (solo lectura) y las importadas desde la pantalla."""
    return _planillas_en(CONFIG["carpeta"], (".xls", ".xlsx")) + _planillas_en(CONFIG.get("importadas"), EXTENSIONES)


def _nombre_seguro(nombre):
    base = Path(str(nombre or "")).name
    base = re.sub(r"[^\w .,()-]", "_", base, flags=re.UNICODE).strip(" .")
    return base[:150] or "planilla"


def importar_planilla(ruta_temporal, nombre_original):
    """Valida que la planilla se pueda leer y la copia a la carpeta de importadas.
    Devuelve {'archivo', 'registros', 'reemplazo'}. Lanza ValueError con un mensaje para la persona."""
    if not CONFIG.get("importadas"):
        raise ValueError("No está configurada la carpeta de planillas importadas.")
    nombre = _nombre_seguro(nombre_original)
    ext = Path(nombre).suffix.lower()
    if ext not in EXTENSIONES:
        raise ValueError("Formato no admitido: subí una planilla .xlsx, .xls o .csv.")
    ruta_temporal = Path(ruta_temporal)
    if ruta_temporal.stat().st_size == 0:
        raise ValueError("El archivo está vacío.")
    if ruta_temporal.stat().st_size > MAX_BYTES_IMPORTACION:
        raise ValueError(f"El archivo supera los {MAX_BYTES_IMPORTACION // (1024 * 1024)} MB.")
    try:
        registros = leer_planilla(ruta_temporal, nombre)
    except Exception as e:
        raise ValueError(f"No se pudo leer la planilla: {e}") from e
    if not registros:
        raise ValueError("La planilla no tiene registros (hace falta una fila de encabezados y al menos una fila de datos).")
    carpeta = Path(CONFIG["importadas"])
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / nombre
    reemplazo = destino.exists()
    shutil.copyfile(ruta_temporal, destino)
    _cache["firma"] = None  # se relee en la próxima consulta
    return {"archivo": nombre, "registros": len(registros), "reemplazo": reemplazo}


def cargar():
    """Registros de todas las planillas (se relee solo si algún archivo cambió)."""
    archivos = _archivos()
    firma = tuple((str(p), p.stat().st_mtime, p.stat().st_size) for p in archivos)
    if firma == _cache["firma"]:
        return _cache["registros"]
    registros, errores = [], []
    for p in archivos:
        try:
            registros += leer_planilla(p)
        except Exception as e:  # una planilla rota no tira abajo a las demás
            logging.warning(f"Expedientes: no se pudo leer {p.name}: {e}")
            errores.append(f"{p.name}: {e}")
    _cache.update(firma=firma, registros=registros, archivos=[p.name for p in archivos], errores=errores)
    return registros


def estado():
    cargar()
    return {"configurado": bool(CONFIG["carpeta"]) or bool(_cache["archivos"]), "carpeta": CONFIG["carpeta"],
            "importadas": [p.name for p in _planillas_en(CONFIG.get("importadas"), EXTENSIONES)],
            "archivos": _cache["archivos"], "errores": _cache["errores"],
            "cantidad": len(_cache["registros"])}


def buscar_numero(numero):
    """Registros con una celda que coincide con ese número de expediente."""
    n = norm_num(numero)
    if len(n) < 3:
        return []
    return [r for r in cargar() if n in r["_num"]][:MAX_RESULTADOS]


def buscar_texto(consulta):
    """Todas las palabras de la consulta presentes en el registro (sin acentos)."""
    palabras = norm_texto(consulta).split()
    if not palabras:
        return []
    exacto = buscar_numero(consulta) if len(palabras) == 1 else []
    if exacto:
        return exacto
    return [r for r in cargar() if all(p in r["_txt"] for p in palabras)][:MAX_RESULTADOS]


# ---------------------------------------------------------------- búsqueda por campos
# Cada filtro se busca en las columnas de la planilla cuyo encabezado corresponde a ese dato. Si una planilla no tiene
# esa columna, se busca en todas sus columnas (y se avisa), para no perder resultados.
CAMPOS = {
    "lpu": {"etiqueta": "LPU", "placeholder": "Ingrese LPU", "numerico": True,
            "encabezados": ("lpu", "legajo", "l.p.u")},
    "dni": {"etiqueta": "DNI", "placeholder": "Ingrese DNI", "numerico": True,
            "encabezados": ("dni", "documento", "d.n.i", "nro doc", "n doc")},
    "expediente": {"etiqueta": "N° de Expediente", "placeholder": "Ingrese número de expediente", "numerico": True,
                   "encabezados": ("expediente", "expte", "exp.", "exp ", "actuacion")},
    "nombre": {"etiqueta": "Nombre y Apellido", "placeholder": "Ingrese nombre o apellido", "numerico": False,
               "encabezados": ("nombre", "apellido", "interno", "persona", "detenido", "ppl", "causante")},
}


def columnas_de(registro, campo):
    """Columnas del registro que corresponden al campo (por el texto del encabezado)."""
    claves = CAMPOS[campo]["encabezados"]
    salida = []
    for col in registro["datos"]:
        h = " " + norm_texto(col) + " "
        if any((" " + k) in h or h.strip() == k.strip() for k in claves):
            salida.append(col)
    return salida


def _coincide(registro, campo, valor, sin_columna):
    cols = columnas_de(registro, campo)
    if not cols:
        sin_columna.add((registro["archivo"], campo))
        celdas = list(registro["datos"].values())
    else:
        celdas = [registro["datos"][c] for c in cols]
    if CAMPOS[campo]["numerico"]:
        q = norm_num(valor)
        if not q:
            return False
        normalizadas = [norm_num(c) for c in celdas]
        # exacto (12.345/26 == 12345-26) o, desde 4 caracteres, parte del número
        return any(q == c for c in normalizadas) or (len(q) >= 4 and any(q in c for c in normalizadas))
    palabras = norm_texto(valor).split()
    texto = norm_texto(" ".join(celdas))
    return bool(palabras) and all(p in texto for p in palabras)


def buscar_campos(filtros):
    """filtros = {lpu, dni, expediente, nombre}: se combinan (todos los completados tienen que coincidir).
    Devuelve (resultados, avisos) — avisos: planillas sin la columna de algún filtro."""
    activos = {k: str(v).strip() for k, v in (filtros or {}).items() if k in CAMPOS and str(v or "").strip()}
    if not activos:
        return [], []
    sin_columna = set()
    salida = []
    for r in cargar():
        if all(_coincide(r, k, v, sin_columna) for k, v in activos.items()):
            salida.append(r)
            if len(salida) >= MAX_RESULTADOS:
                break
    avisos = sorted({f"La planilla «{a}» no tiene una columna de {CAMPOS[c]['etiqueta']}: ese dato se buscó en todas sus columnas."
                     for a, c in sin_columna})
    return salida, avisos


def buscar_lpu(lpu):
    """(registros con ese LPU exacto en una columna de LPU, hay_columna_lpu). Para verificar las notas: sin búsqueda parcial."""
    q = norm_num(lpu)
    registros = cargar()
    hay_columna = any(columnas_de(r, "lpu") for r in registros)
    if not q:
        return [], hay_columna
    return [r for r in registros if any(norm_num(r["datos"][c]) == q for c in columnas_de(r, "lpu"))], hay_columna


def valores_de(registro, campo):
    return [registro["datos"][c] for c in columnas_de(registro, campo)]


def describir_filtros(filtros):
    return " · ".join(f"{CAMPOS[k]['etiqueta']}: {str(v).strip()}" for k, v in filtros.items() if k in CAMPOS and str(v or "").strip())


def existe(numero):
    return bool(buscar_numero(numero))
