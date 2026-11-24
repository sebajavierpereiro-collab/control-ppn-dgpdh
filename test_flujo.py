"""Flujo del asesor: borrador -> envío, edición de texto, avisos accionables,
Aplicar y reporte erróneo."""
import sys, tempfile, ast, logging, io
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import docx, jinja2
import database as db
import auth, nucleo, analisis, automatico, fechas, respaldo, flujo
tmp = Path(tempfile.mkdtemp())
db.RUTA_DB = tmp / "t.db"
respaldo.CONFIG["activo"] = False
db.inicializar_db()

def mkdoc(nombre, parrafos):
    d = docx.Document()
    for p in parrafos: d.add_paragraph(p)
    r = tmp / nombre; d.save(str(r)); return r

eq = db.crear_equipo("A"); eq2 = db.crear_equipo("B")
h, s = auth.generar_hash_clave("Clave1234")
ase = db.crear_usuario("ase", h, s, "Ana Asesora", "ASESOR", eq)
ase2 = db.crear_usuario("ase2", h, s, "Beto Asesor", "ASESOR", eq)
coo = db.crear_usuario("coo", h, s, "Carlos Coord", "COORDINADOR", eq)
direc = next(u for u in db.listar_usuarios() if u["rol"] == "DIRECTOR")
U = {k: db.obtener_usuario(v) for k, v in dict(ase=ase, ase2=ase2, coo=coo).items()}
U["dir"] = db.obtener_usuario(direc["id"])

# 1) borrador: sin aviso a Coordinación hasta enviar
tok = db.USUARIO_ACTUAL.set(U["ase"])
n1 = db.crear_nota("Nota 1.docx", eq, mkdoc("n1.docx", ["Expte. Nro. 12.345 del reo"]), usuario_id=ase, borrador=True)
n2 = db.crear_nota("Nota 2.docx", eq, mkdoc("n2.docx", ["otro"]), usuario_id=ase, borrador=True)
assert db.obtener_nota(n1)["estado"] == "BORRADOR"
assert not db.listar_notificaciones(coo)
db.enviar_a_coordinacion(n1)
assert db.obtener_nota(n1)["estado"] == "SUBIDA"
av = db.listar_notificaciones(coo)
assert len(av) == 1 and "Ana Asesora subió el documento «Nota 1.docx»" in av[0]["texto"], av
try:
    db.enviar_a_coordinacion(n1); raise SystemExit("debió fallar")
except db.ConflictoDeEstado: pass
db.USUARIO_ACTUAL.reset(tok)
print("1 borrador -> envío OK")

# 2) helpers de main.py
src = open(APP + "/main.py", encoding="utf-8").read(); tree = ast.parse(src)
class HTTPExc(Exception):
    def __init__(self, status_code, detail=None): self.status_code, self.detail = status_code, detail
class AutoStub:
    encolados = []
    @staticmethod
    def encolar_analisis(i): AutoStub.encolados.append(i)
CARPETA_BASE = tmp / "base"; CARPETA_BASE.mkdir()
ns = dict(Request=object, auth=auth, db=db, nucleo=nucleo, logging=logging, HTTPException=HTTPExc, automatico=AutoStub,
          CARPETA_BASE=CARPETA_BASE, _reanalizar_consistencia=lambda i: None)
def fuente(nombre):
    for nodo in tree.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name == nombre:
            return ast.get_source_segment(src, nodo)
    raise KeyError(nombre)
for nombre in ("_puede_ver_nota", "_filtrar_por_visibilidad", "_puede_editar_texto", "_proponer_aplicacion", "_aplicar_ediciones_texto"):
    exec(fuente(nombre), ns)
nota2 = db.obtener_nota(n2)
assert ns["_puede_ver_nota"](U["ase"], nota2) and not ns["_puede_ver_nota"](U["ase2"], nota2)
assert not ns["_puede_ver_nota"](U["coo"], nota2) and not ns["_puede_ver_nota"](U["dir"], nota2)
lista = [db.obtener_nota(n1), nota2]
assert [n["id"] for n in ns["_filtrar_por_visibilidad"](lista, U["ase"])] == [n1, n2]
assert [n["id"] for n in ns["_filtrar_por_visibilidad"](lista, U["coo"])] == [n1]
assert [n["id"] for n in ns["_filtrar_por_visibilidad"](lista, U["dir"])] == [n1]
nota1 = db.obtener_nota(n1)
assert ns["_puede_editar_texto"](U["ase"], nota1) and ns["_puede_editar_texto"](U["coo"], nota1) and ns["_puede_editar_texto"](U["dir"], nota1)
assert not ns["_puede_editar_texto"](U["ase2"], nota1)
assert ns["_puede_editar_texto"](U["ase"], nota2) and not ns["_puede_editar_texto"](U["coo"], nota2)
print("2 visibilidad y permisos de edición OK")

# 3) edición del asesor mientras espera: se guarda, se registra y se avisa a Coordinación
n = ns["_aplicar_ediciones_texto"](nota1, {0: "Expte. Nro. 37.811 del reo"}, U["ase"])
assert n == 1
nota1 = db.obtener_nota(n1)
assert nota1["ruta_base"] and "Ana Asesora" in nota1["historial"][-1]["comentario"]
assert docx.Document(nota1["ruta_base"]).paragraphs[0].text == "Expte. Nro. 37.811 del reo"
assert docx.Document(nota1["ruta_original"]).paragraphs[0].text == "Expte. Nro. 12.345 del reo"   # original intacto
assert any("corrigió el texto" in a["texto"] for a in db.listar_notificaciones(coo))
assert ns["_aplicar_ediciones_texto"](nota1, {0: "Expte. Nro. 37.811 del reo"}, U["ase"]) == 0    # sin cambios reales
print("3 edición del asesor OK")

# 4) Aplicar: reemplazo seguro del dato
nota1 = db.obtener_nota(n1)
parr, _ = nucleo.obtener_vista_previa(nota1["ruta_original"])
h_ok = dict(tipo="DISCREPANCIA", valores=[{"doc_id": n1, "doc": "Nota 1", "valor": "12.345"}, {"doc_id": 99, "doc": "Proveído", "valor": "37.811"}])
p = ns["_proponer_aplicacion"](nota1, h_ok, parr)
assert p and p["despues"] == "Expte. Nro. 37.811 del reo" and p["ref_doc"] == "Proveído" and p["indice"] == 0
assert ns["_proponer_aplicacion"](nota1, dict(h_ok, tipo="AVISO"), parr) is None
assert ns["_proponer_aplicacion"](nota1, dict(h_ok, valores=[h_ok["valores"][0]]), parr) is None
assert ns["_proponer_aplicacion"](nota1, dict(h_ok, valores=[dict(h_ok["valores"][0], valor="99.999"), h_ok["valores"][1]]), parr) is None
dup = [{"indice": 0, "texto": "12.345 y 12.345"}]
assert ns["_proponer_aplicacion"](nota1, h_ok, dup) is None
print("4 aplicar corrección OK")

# 5) hallazgos vacíos, reporte erróneo y alertas al asesor
assert analisis.es_hallazgo_vacio("No hay contradicción en los nombres mencionados en los documentos.", [])
assert analisis.es_hallazgo_vacio("", [{"valor": "LPU 437612"}, {"valor": "lpu  437612"}])
assert not analisis.es_hallazgo_vacio("Las fechas difieren.", [{"valor": "1/1/2024"}, {"valor": "2/1/2024"}])
mk = lambda clave, det="Difiere": dict(tipo="DISCREPANCIA", severidad="ALTA", dato="Expediente", detalle=det, valores=[{"doc_id": n1, "doc": "a", "valor": "1", "fragmento": "x"}], clave=clave)
db.reemplazar_hallazgos(n2, None, [mk("k1"), mk("k2")])
assert any("detectó 2 alertas" in a["texto"] for a in db.listar_notificaciones(ase))
hs = db.listar_hallazgos(n2); id1 = next(x["id"] for x in hs if x["clave"] == "k1")
db.guardar_feedback_hallazgo(db.obtener_hallazgo(id1), "no corresponde", "Carlos Coord")
db.resolver_hallazgo(id1, "ERRONEO", "Carlos Coord")
db.reemplazar_hallazgos(n1, None, [mk("k1")])                      # otra nota, mismo caso: ya no aparece
assert db.listar_hallazgos(n1) == []
db.reemplazar_hallazgos(n2, None, [mk("k1"), mk("k2")])             # misma nota: conserva el estado
assert next(x for x in db.listar_hallazgos(n2) if x["clave"] == "k1")["estado"] == "ERRONEO"
assert db.ejemplos_erroneos()[0]["dato"] == "Expediente"
assert "no los repitas" in analisis._bloque_negativos(db.ejemplos_erroneos())
fid = db.listar_feedback()[0]["id"]; db.desactivar_feedback(fid)
db.reemplazar_hallazgos(n1, None, [mk("k1")]); assert len(db.listar_hallazgos(n1)) == 1  # quitado el aprendizaje, vuelve a poder aparecer
print("5 hallazgos vacíos / reporte erróneo / alertas OK")

# 6) plantilla de la ficha con hallazgos
env = jinja2.Environment(loader=jinja2.FileSystemLoader(APP + "/templates"), autoescape=True)
env.globals["ico"] = __import__("iconos").svg
env.globals.update(es_admin=auth.es_admin, ETIQUETAS_ROL=auth.ETIQUETAS_ROL, ROLES_MESA=auth.ROLES_MESA, ROLES_DE_EQUIPO=auth.ROLES_DE_EQUIPO,
  PRIORIDADES=auth.PRIORIDADES, ETIQUETAS_PRIORIDAD=auth.ETIQUETAS_PRIORIDAD, etiqueta_tema=analisis.etiqueta_tema,
  ETIQUETAS_TRABAJO=automatico.ETIQUETAS_TRABAJO, ETIQUETAS_ESTADO_TRABAJO=automatico.ETIQUETAS_ESTADO_TRABAJO,
  contar_notificaciones=db.contar_notificaciones, hoy_local=lambda: "2026-10-07",
  hallazgo_vacio=lambda h: analisis.es_hallazgo_vacio(h.get("detalle"), h.get("valores")))
env.filters["hora"] = fechas.a_hora_local; env.filters["fecha_corta"] = fechas.fecha_corta
class St: pass
class Req:
    def __init__(self, u): self.state = St(); self.state.usuario = u; self.session = {}
hallazgos = [
    dict(id=1, estado="ABIERTO", tipo="DISCREPANCIA", dato="Número de expediente", detalle="Difiere", valores=[{"doc": "Nota", "fragmento": "12.345"}], aplicable=True, resuelto_por=None),
    dict(id=2, estado="ABIERTO", tipo="AVISO", dato="Nombre del reo (IA)", detalle="No hay contradicción en los nombres.", valores=[], aplicable=False, resuelto_por=None),
    dict(id=3, estado="IGNORADO", tipo="AVISO", dato="Fecha", detalle="x", valores=[], aplicable=False, resuelto_por="Carlos"),
    dict(id=4, estado="ERRONEO", tipo="AVISO", dato="Lugar", detalle="x", valores=[], aplicable=False, resuelto_por="Carlos"),
]
h = env.get_template("_consistencia.html").render(analisis_hallazgos=hallazgos, puede_resolver=True, destino="/nota/1", hallazgo_vacio=env.globals["hallazgo_vacio"])
assert "1 para revisar" in h and "/hallazgo/1/aplicar" in h and "Es un reporte erróneo" in h and 'value="ERRONEO"' in h
assert "Nombre del reo" in h and h.index("verificación(es) sin diferencias") < h.index("Nombre del reo")   # solo dentro del resumen plegado
assert h.count('name="estado" value="REVISADO"') == 1
h = env.get_template("_consistencia.html").render(analisis_hallazgos=hallazgos[1:2], puede_resolver=True, destino="/")
assert "para revisar" not in h and "Sin pendientes" in h
print("6 plantillas de avisos OK")
print("ALL OK")
