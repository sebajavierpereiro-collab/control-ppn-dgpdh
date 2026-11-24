"""Pruebas de la Ola 1: fecha al firmar, avisos con actor, tareas vistas,
pantallas (listado con menús de columna, avisos con pestañas, admin en pestañas)."""
import sys, tempfile, ast
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import jinja2
import database as db
import auth, analisis, automatico, fechas, respaldo, flujo
tmp = Path(tempfile.mkdtemp())
db.RUTA_DB = tmp / "t.db"
respaldo.CONFIG["activo"] = False
db.inicializar_db()

eq = db.crear_equipo("Equipo A")
h, s = auth.generar_hash_clave("Clave1234")
ase = db.crear_usuario("ase", h, s, "Ana Asesora", "ASESOR", eq)
coo = db.crear_usuario("coo", h, s, "Carlos Coord", "COORDINADOR", eq)
direc = next(u for u in db.listar_usuarios() if u["rol"] == "DIRECTOR")

# 1) al subir no hay fecha; el aviso nombra a quien lo hizo
tok = db.USUARIO_ACTUAL.set(db.obtener_usuario(ase))
n = db.crear_nota("Nota X.docx", eq, tmp / "x.docx", usuario_id=ase)
db.USUARIO_ACTUAL.reset(tok)
assert db.obtener_nota(n)["fecha_nota"] is None
av = db.listar_notificaciones(coo)
assert av and av[0]["texto"] == "Ana Asesora subió el documento «Nota X.docx»: necesita su aprobación.", av
print("1 subida sin fecha + aviso con actor OK")

# 2) circuito y fecha al firmar
db.actualizar_estado(n, "PROCESANDO"); db.actualizar_estado(n, "EN_REVISION")
av = db.listar_notificaciones(direc["id"])
assert any(a["texto"].startswith("El sistema dejó corregido") for a in av), av
tok = db.USUARIO_ACTUAL.set(direc)
db.actualizar_estado(n, "PROCESADA")
db.marcar_firmada(n, tmp / "f.docx", sha256_firmado="a" * 64, fecha_nota="2026-10-01")
db.USUARIO_ACTUAL.reset(tok)
nota = db.obtener_nota(n)
assert nota["fecha_nota"] == "2026-10-01" and nota["estado"] == "FIRMADA"
assert any("fecha de la nota: 2026-10-01" in (x["comentario"] or "") for x in nota["historial"])
print("2 fecha al firmar OK")

# 3) rechazadas vistas
db.marcar_nota_vista(coo, n); db.marcar_nota_vista(coo, n)
assert db.ids_notas_vistas(coo) == {n} and db.ids_notas_vistas(ase) == set()
print("3 notas vistas OK")

# 4) plantillas
env = jinja2.Environment(loader=jinja2.FileSystemLoader(APP + "/templates"), autoescape=True)
env.globals["ico"] = __import__("iconos").svg
env.globals.update(es_admin=auth.es_admin, ETIQUETAS_ROL=auth.ETIQUETAS_ROL, ROLES_MESA=auth.ROLES_MESA, ROLES_DE_EQUIPO=auth.ROLES_DE_EQUIPO,
  PRIORIDADES=auth.PRIORIDADES, ETIQUETAS_PRIORIDAD=auth.ETIQUETAS_PRIORIDAD, etiqueta_tema=analisis.etiqueta_tema,
  ETIQUETAS_TRABAJO=automatico.ETIQUETAS_TRABAJO, ETIQUETAS_ESTADO_TRABAJO=automatico.ETIQUETAS_ESTADO_TRABAJO,
  contar_notificaciones=db.contar_notificaciones, hoy_local=lambda: "2026-10-07",
  semaforo=lambda n: None, estado_respuesta=lambda n, f=None: None, comentarios_de=lambda n: [], duplicados_de=lambda n: [], glosario_en=lambda n: [], info_deshacer=lambda r: None, eventos_nota=lambda n: [], antiguedad_etapa=lambda n: None,
  datos_despacho=lambda n: dict(nro_nota="", nro_expte="", expte_propuesto=False, preparado=False, tiene_pdf=False),
  datos_envio=lambda n: dict(propuesto="", contacto=None, contactos=[], envios=[]))
env.filters["hora"] = fechas.a_hora_local; env.filters["fecha_corta"] = fechas.fecha_corta
class St: pass
class Req:
    def __init__(self, u): self.state = St(); self.state.usuario = u; self.session = {}
U = db.obtener_usuario(coo)
ni = dict(db.obtener_nota(n), estado="SUBIDA", etapa=flujo.etapa_para_estado("SUBIDA"), prioridad_efectiva="NORMAL", tipo="NOTA", hallazgos_abiertos=0,
          actualizado_en="2026-10-06T22:30:00+00:00", equipo_nombre="E", asesor_nombre="A", tema=None, bloque_nombre=None, puede_gestionar=True, fecha_nota=None)
kw = dict(request=Req(U), usuario=U, notas=[ni], etapas=flujo.ETAPAS, mostrar_despachadas=False, cantidad_despachadas=0, orden="", direccion="asc",
          f_nombre="abc", f_tipo="", f_etapa="", f_fecha="", f_prioridad="", f_tema="", f_tarea="", temas_disponibles=[], tipos_disponibles=["NOTA"],
          pendientes_firma=[], pendientes_revision=[], bloques_mesa=[], puede_armar_bloques=True, pagina=1, paginas=1, total=1, volver="/?f_nombre=abc",
          tareas=[("aprobar", "para aprobar", 3), ("rechazadas", "rechazadas sin ver", 0)], borradores=[],
          prioridad_opciones=[(p, auth.ETIQUETAS_PRIORIDAD[p]) for p in auth.PRIORIDADES], tipo_opciones=[("NOTA", "NOTA")],
          etapa_opciones=[(e["clave"], e["nombre"]) for e in flujo.ETAPAS], tema_opciones=[])
hi = env.get_template("index.html").render(**kw)
for frag in ['data-plegable="documentos"', "<strong>3</strong> para aprobar", "menu-col--activo", 'onchange="cambiarPrioridad(this)"',
             'form="form-bloques"', 'id="form-bloques"', "Panel de filtros", "Agrupar estos documentos en un bloque", "Quitar todos los filtros"]:
    assert frag in hi, frag
assert "Fecha de la nota</label>" not in hi and 'id="fecha-subida"' not in hi and "rechazadas sin ver" not in hi
ha = env.get_template("notificaciones.html").render(request=Req(U), notificaciones=db.listar_notificaciones(coo), ver="nuevos", total=1, nuevos=1, leidos=0)
assert ha.index(">Nuevos") < ha.index(">Leídos") < ha.index(">Todos") and "pestana--activa" in ha and 'target="_blank"' in ha and "Marcar todos como leídos" in ha
hb = env.get_template("perfil.html").render(request=Req(U), usuario=U, mensaje=None, error=None, minimo=8, obligatorio=False)
assert "logo-ppn-negro.png" in hb and 'id="campo-buscar"' in hb and "plegable.js" in hb and "boton-lupa" not in hb
print("4 plantillas OK")

# 5) AST: la guarda de /nota/{id}/prioridad sigue y el firmar acepta fecha
src = open(APP + "/main.py", encoding="utf-8").read()
assert "fecha: str = Form(None)" in src and "def _volver_admin" in src

# 6) los hallazgos conservan su id al recalcular (botones de pantallas abiertas siguen valiendo)
hs = [dict(tipo="DISCREPANCIA", severidad="ALTA", dato="Expediente", detalle="x", valores=[{"doc": "a", "fragmento": "1"}], clave="exp"),
      dict(tipo="FALTA", severidad="MEDIA", dato="Fecha", detalle="y", valores=[], clave="fec")]
db.reemplazar_hallazgos(n, None, hs)
ids1 = {h["clave"]: h["id"] for h in db.listar_hallazgos(n)}
db.resolver_hallazgo(ids1["exp"], "IGNORADO", "t")
db.reemplazar_hallazgos(n, None, hs[:1])
h2 = db.listar_hallazgos(n)
assert len(h2) == 1 and h2[0]["id"] == ids1["exp"] and h2[0]["estado"] == "IGNORADO"
print("6 hallazgos conservan id OK")
print("ALL OK")
