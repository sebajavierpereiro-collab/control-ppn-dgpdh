"""Análisis estático del código fuente del proyecto dgpdh_web.
Genera reportes sobre rendimiento, calidad y seguridad sin afectar
el servidor en producción. Se ejecuta bajo demanda o de noche.
"""
import ast
import json
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

CARPETA_REPORTES = Path("datos/reportes_codigo")
CARPETA_REPORTES.mkdir(parents=True, exist_ok=True)
MAX_REPORTES = 20

ARCHIVOS_A_ANALIZAR = [
    "main.py", "database.py", "nucleo.py", "expedientes.py",
    "despacho.py", "clasificacion_notas.py", "automatico.py",
    "analisis.py", "agentes.py", "mesa_entradas.py",
]

SEVERIDAD = {"critico": 1, "alto": 2, "medio": 3, "bajo": 4, "info": 5}


@dataclass
class Hallazgo:
    archivo: str
    linea_inicio: int
    linea_fin: int
    funcion: str
    categoria: str
    severidad: str
    titulo: str
    descripcion: str
    sugerencia: str
    impacto_estimado: str


@dataclass
class Reporte:
    fecha: str
    duracion_segundos: float
    archivos_analizados: int
    total_hallazgos: int
    hallazgos: list = field(default_factory=list)
    resumen: dict = field(default_factory=dict)
    error: Optional[str] = None


# ── Detectores ────────────────────────────────────────────────────────────────

def _analizar_archivo(ruta: Path) -> list:
    hallazgos = []
    nombre = ruta.name
    try:
        fuente = ruta.read_text(encoding="utf-8")
        arbol = ast.parse(fuente, filename=str(ruta))
    except Exception:
        return []

    lineas = fuente.splitlines()

    for nodo in ast.walk(arbol):
        # 1. Funciones muy largas (> 100 líneas)
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            largo = (nodo.end_lineno or nodo.lineno) - nodo.lineno
            if largo > 100:
                hallazgos.append(Hallazgo(
                    archivo=nombre, linea_inicio=nodo.lineno,
                    linea_fin=nodo.end_lineno or nodo.lineno,
                    funcion=nodo.name, categoria="calidad",
                    severidad="medio",
                    titulo=f"Función larga: {nodo.name} ({largo} líneas)",
                    descripcion=f"La función tiene {largo} líneas. Funciones largas son difíciles de mantener.",
                    sugerencia="Dividir en subfunciones más pequeñas con responsabilidad única.",
                    impacto_estimado="mantenibilidad"
                ))

        # 2. Bucles anidados (posible O(n²))
        if isinstance(nodo, (ast.For, ast.While)):
            for hijo in ast.walk(nodo):
                if hijo is not nodo and isinstance(hijo, (ast.For, ast.While)):
                    func_padre = _funcion_padre(arbol, nodo.lineno)
                    hallazgos.append(Hallazgo(
                        archivo=nombre, linea_inicio=nodo.lineno,
                        linea_fin=nodo.lineno, funcion=func_padre,
                        categoria="rendimiento", severidad="medio",
                        titulo=f"Bucle anidado en línea {nodo.lineno}",
                        descripcion="Bucles anidados pueden ser O(n²). Con volúmenes grandes puede causar latencia.",
                        sugerencia="Revisar si se puede usar un dict/set para lookup O(1) en vez del bucle interno.",
                        impacto_estimado="CPU — latencia en requests con muchos registros"
                    ))
                    break

        # 3. Lectura de archivo dentro de bucle
        if isinstance(nodo, (ast.For, ast.While)):
            for hijo in ast.walk(nodo):
                if isinstance(hijo, ast.Call):
                    func = _nombre_llamada(hijo)
                    if func in ("open", "read_text", "read_bytes", "leer_planilla", "cargar"):
                        func_padre = _funcion_padre(arbol, nodo.lineno)
                        hallazgos.append(Hallazgo(
                            archivo=nombre, linea_inicio=nodo.lineno,
                            linea_fin=nodo.lineno, funcion=func_padre,
                            categoria="rendimiento", severidad="alto",
                            titulo=f"Lectura de archivo dentro de bucle (línea {nodo.lineno})",
                            descripcion=f"Se llama a '{func}' dentro de un bucle. Cada iteración hace E/S de disco.",
                            sugerencia="Leer el archivo/planilla una vez antes del bucle y reutilizar el resultado.",
                            impacto_estimado="E/S de disco — muy lento con muchos registros"
                        ))
                        break

        # 4. Excepciones silenciadas
        if isinstance(nodo, ast.ExceptHandler):
            cuerpo = nodo.body
            es_silencioso = all(
                isinstance(s, ast.Pass) or
                (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and
                 _nombre_llamada(s.value) in ("logging.warning", "logging.debug", "print", "logger.warning"))
                for s in cuerpo
            )
            if es_silencioso and len(cuerpo) <= 1:
                func_padre = _funcion_padre(arbol, nodo.lineno)
                hallazgos.append(Hallazgo(
                    archivo=nombre, linea_inicio=nodo.lineno,
                    linea_fin=nodo.lineno, funcion=func_padre,
                    categoria="calidad", severidad="medio",
                    titulo=f"Excepción silenciada en línea {nodo.lineno}",
                    descripcion="Un bloque except con solo pass o log silencia errores importantes.",
                    sugerencia="Registrar con logging.exception() para tener el traceback completo.",
                    impacto_estimado="diagnóstico — dificulta detectar bugs"
                ))

    # 5. Patrones de seguridad en texto fuente
    patrones_seguridad = [
        ("password", "Posible contraseña en texto plano", "alto"),
        ("secret", "Posible secreto en texto plano", "alto"),
        ("print(", "print() en producción expone datos en logs", "bajo"),
        ("TODO", "Código pendiente (TODO)", "info"),
        ("FIXME", "Código marcado como a reparar (FIXME)", "medio"),
    ]
    for n, linea_txt in enumerate(lineas, start=1):
        linea_lower = linea_txt.lower().strip()
        if linea_lower.startswith("#"):
            continue
        for patron, titulo, severidad in patrones_seguridad:
            if patron.lower() in linea_lower and "log" not in linea_lower:
                hallazgos.append(Hallazgo(
                    archivo=nombre, linea_inicio=n, linea_fin=n,
                    funcion="—", categoria="seguridad", severidad=severidad,
                    titulo=f"{titulo} (línea {n})",
                    descripcion=f"Se encontró '{patron}' fuera de un comentario.",
                    sugerencia="Verificar que no se expone información sensible.",
                    impacto_estimado="seguridad"
                ))

    return hallazgos


def _nombre_llamada(nodo_call) -> str:
    if isinstance(nodo_call.func, ast.Attribute):
        return f"{_nombre_llamada_base(nodo_call.func.value)}.{nodo_call.func.attr}"
    if isinstance(nodo_call.func, ast.Name):
        return nodo_call.func.id
    return ""


def _nombre_llamada_base(nodo) -> str:
    if isinstance(nodo, ast.Name):
        return nodo.id
    if isinstance(nodo, ast.Attribute):
        return nodo.attr
    return ""


def _funcion_padre(arbol, linea) -> str:
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if nodo.lineno <= linea <= (nodo.end_lineno or linea):
                return nodo.name
    return "—"


# ── API pública ───────────────────────────────────────────────────────────────

def _ejecutar_bandit(raiz: Path) -> list:
    """Auditoría de seguridad con Bandit. Solo severidad medium y high."""
    try:
        venv_bin = Path("/opt/dgpdh/venv/bin/bandit")
        if not venv_bin.is_file():
            venv_bin = raiz.parent / "venv" / "bin" / "bandit"
        resultado = subprocess.run(
            [str(venv_bin), "-r", str(raiz), "-f", "json", "-ll",
             "--exclude", f"{raiz}/datos,{raiz}/respaldos_codigo,{raiz}/static"],
            capture_output=True, text=True, timeout=120, cwd=str(raiz)
        )
        datos = json.loads(resultado.stdout or "{}")
        hallazgos = []
        for issue in datos.get("results", []):
            sev_raw = issue.get("issue_severity", "LOW").upper()
            sev = {"HIGH": "alto", "MEDIUM": "medio", "LOW": "bajo"}.get(sev_raw, "bajo")
            hallazgos.append({
                "archivo": Path(issue.get("filename", "?")).name,
                "linea_inicio": issue.get("line_number", 0),
                "linea_fin": issue.get("line_number", 0),
                "funcion": issue.get("test_id", ""),
                "categoria": "seguridad",
                "severidad": sev,
                "titulo": issue.get("issue_text", ""),
                "descripcion": f"Bandit {issue.get('test_id','')}: {issue.get('issue_text','')}",
                "sugerencia": issue.get("more_info", "Ver documentación de Bandit."),
                "impacto_estimado": f"Confianza: {issue.get('issue_confidence','?')}",
            })
        return hallazgos
    except FileNotFoundError:
        return [{"archivo": "—", "linea_inicio": 0, "linea_fin": 0, "funcion": "—",
                 "categoria": "seguridad", "severidad": "info",
                 "titulo": "Bandit no encontrado", "descripcion": "Instalar con pip install bandit",
                 "sugerencia": "", "impacto_estimado": ""}]
    except Exception:
        return []


def _ejecutar_radon(raiz: Path) -> list:
    """Complejidad ciclomática. Funciones CC > 10 son problemáticas."""
    try:
        venv_bin = Path("/opt/dgpdh/venv/bin/radon")
        if not venv_bin.is_file():
            venv_bin = raiz.parent / "venv" / "bin" / "radon"
        resultado = subprocess.run(
            [str(venv_bin), "cc", str(raiz), "-j", "-n", "C",
             "--exclude", "datos,respaldos_codigo,venv,static"],
            capture_output=True, text=True, timeout=60, cwd=str(raiz)
        )
        datos = json.loads(resultado.stdout or "{}")
        hallazgos = []
        for archivo_path, funciones in datos.items():
            for f in (funciones if isinstance(funciones, list) else []):
                cc = f.get("complexity", 0)
                sev = "critico" if cc > 20 else "alto" if cc > 15 else "medio"
                hallazgos.append({
                    "archivo": Path(archivo_path).name,
                    "linea_inicio": f.get("lineno", 0),
                    "linea_fin": f.get("endline", 0),
                    "funcion": f.get("name", ""),
                    "categoria": "rendimiento",
                    "severidad": sev,
                    "titulo": f"Complejidad ciclomática {cc}: {f.get('name','')}",
                    "descripcion": f"CC={cc}. Valores > 10 son difíciles de testear. > 20 son críticos.",
                    "sugerencia": "Dividir en funciones más pequeñas con responsabilidad única.",
                    "impacto_estimado": "mantenibilidad y testabilidad",
                })
        return hallazgos
    except Exception:
        return []


def _ejecutar_vulture(raiz: Path) -> list:
    """Detecta código muerto (funciones/variables que nunca se usan)."""
    try:
        resultado = subprocess.run(
            ["/opt/dgpdh/venv/bin/python", "-m", "vulture",
             str(raiz), "--min-confidence", "80",
             "--exclude", "datos,respaldos_codigo,venv,static,tests"],
            capture_output=True, text=True, timeout=60, cwd=str(raiz)
        )
        hallazgos = []
        for linea in resultado.stdout.splitlines():
            if ":" not in linea:
                continue
            partes = linea.split(":", 2)
            if len(partes) < 3:
                continue
            try:
                nro = int(partes[1])
            except ValueError:
                nro = 0
            hallazgos.append({
                "archivo": Path(partes[0]).name,
                "linea_inicio": nro,
                "linea_fin": nro,
                "funcion": "—",
                "categoria": "calidad",
                "severidad": "bajo",
                "titulo": partes[2].strip(),
                "descripcion": "Código que parece no usarse. Verificar antes de eliminar (puede ser llamado dinámicamente).",
                "sugerencia": "Confirmar que no se usa y eliminar para reducir mantenimiento.",
                "impacto_estimado": "mantenibilidad",
            })
        return hallazgos
    except Exception:
        return []


def _a_hallazgo(d: dict) -> Hallazgo:
    campos = {f for f in Hallazgo.__dataclass_fields__}
    return Hallazgo(**{k: d.get(k, "") for k in campos})


def ejecutar_analisis(raiz: Path = None) -> Reporte:
    """Ejecuta el análisis estático. Devuelve un Reporte y lo guarda en disco."""
    raiz = raiz or Path(".")
    inicio = time.time()
    todos_hallazgos = []

    archivos = [raiz / a for a in ARCHIVOS_A_ANALIZAR if (raiz / a).is_file()]
    dgpdh_dir = raiz / "dgpdh"
    if dgpdh_dir.is_dir():
        ids_ya = {a.resolve() for a in archivos}
        for sub in dgpdh_dir.rglob("*.py"):
            if sub.resolve() not in ids_ya:
                archivos.append(sub)

    for archivo in archivos:
        todos_hallazgos.extend(_analizar_archivo(archivo))
        time.sleep(0.05)

    for d in _ejecutar_bandit(raiz):
        try: todos_hallazgos.append(_a_hallazgo(d))
        except Exception: pass
    for d in _ejecutar_radon(raiz):
        try: todos_hallazgos.append(_a_hallazgo(d))
        except Exception: pass
    for d in _ejecutar_vulture(raiz):
        try: todos_hallazgos.append(_a_hallazgo(d))
        except Exception: pass

    duracion = time.time() - inicio
    resumen: dict = {}
    for h in todos_hallazgos:
        resumen[h.severidad] = resumen.get(h.severidad, 0) + 1

    reporte = Reporte(
        fecha=datetime.now().isoformat(timespec="seconds"),
        duracion_segundos=round(duracion, 2),
        archivos_analizados=len(archivos),
        total_hallazgos=len(todos_hallazgos),
        hallazgos=[asdict(h) for h in
                   sorted(todos_hallazgos, key=lambda h: SEVERIDAD.get(h.severidad, 9))],
        resumen=resumen,
    )
    _guardar_reporte(reporte)
    return reporte


def _guardar_reporte(reporte: Reporte):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archivo = CARPETA_REPORTES / f"reporte_{ts}.json"
    archivo.write_text(json.dumps(asdict(reporte), ensure_ascii=False, indent=2), encoding="utf-8")
    todos = sorted(CARPETA_REPORTES.glob("reporte_*.json"))
    for viejo in todos[:-MAX_REPORTES]:
        viejo.unlink(missing_ok=True)


def listar_reportes() -> list:
    reportes = []
    for p in sorted(CARPETA_REPORTES.glob("reporte_*.json"), reverse=True):
        try:
            datos = json.loads(p.read_text(encoding="utf-8"))
            reportes.append({
                "archivo": p.name,
                "fecha": datos.get("fecha"),
                "total_hallazgos": datos.get("total_hallazgos", 0),
                "resumen": datos.get("resumen", {}),
                "archivos_analizados": datos.get("archivos_analizados", 0),
                "duracion_segundos": datos.get("duracion_segundos", 0),
            })
        except Exception:
            continue
    return reportes


def obtener_reporte(nombre_archivo: str) -> dict:
    p = CARPETA_REPORTES / nombre_archivo
    if not p.is_file() or not nombre_archivo.startswith("reporte_"):
        return {}
    return json.loads(p.read_text(encoding="utf-8"))
