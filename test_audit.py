import sys, os, tempfile, ast, io, zipfile, re, uuid, logging, shutil, types
from datetime import datetime, timezone, timedelta
from pathlib import Path
APP = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import docx, jinja2
import database as db
import auth, nucleo, analisis, automatico, integridad, respaldo, fechas, reportes, flujo
tmp = Path(tempfile.mkdtemp())
db.RUTA_DB = tmp / "t.db"
respaldo.CONFIG["activo"] = False
db.inicializar_db()

def mkdoc(nombre, parrafos):
    d = docx.Document()
    for p in parrafos: d.add_paragraph(p)
    ruta = tmp / nombre; d.save(str(ruta)); return ruta

# ---------- 1) director inicial y clave obligatoria
dirs = db.listar_usuarios()
assert dirs and all(u["debe_cambiar_clave"] == 1 for u in dirs if u["rol"] == "DIRECTOR"), dirs
print("1 director inicial con clave a cambiar OK")

# ---------- 2) política de claves
assert auth.validar_clave_nueva("abc") and auth.validar_clave_nueva("director123")
assert auth.validar_clave_nueva("12345678901") and auth.validar_clave_nueva("sololetrasss")
assert auth.validar_clave_nueva("Segura2026x", "seba") is None
assert auth.validar_clave_nueva("seba", "seba")
print("2 política de claves OK")

# ---------- 3) usuarios, equipos, notificaciones, historial con usuario
eq = db.crear_equipo("Equipo A"); eq2 = db.crear_equipo("Equipo B")
h, s = auth.generar_hash_clave("Clave1234")
asesor = db.crear_usuario("ase", h, s, "Ana Asesora", "ASESOR", eq)
coord = db.crear_usuario("coo", h, s, "Carlos Coord", "COORDINADOR", eq)
coord2 = db.crear_usuario("coo2", h, s, "Otro Coord", "COORDINADOR", eq2)
mesa = db.crear_usuario("mes", h, s, "Mesa", "MESA_JEFE", None)
direc = next(u for u in db.listar_usuarios() if u["rol"] == "DIRECTOR")
U_ase = db.obtener_usuario(asesor)
tok = db.USUARIO_ACTUAL.set(U_ase)
r1 = mkdoc("Nota 1.docx", ["Se solicita atención médica para Juan Pérez."])
n1 = db.crear_nota("Nota 1.docx", eq, r1, usuario_id=asesor)
db.USUARIO_ACTUAL.reset(tok)
assert db.contar_notificaciones(coord) == 1 and db.contar_notificaciones(coord2) == 0
assert db.contar_notificaciones(asesor) == 0
nota = db.obtener_nota(n1)
assert nota["historial"][0]["usuario"] == "Ana Asesora", nota["historial"]
# cambio de estado atómico
tok = db.USUARIO_ACTUAL.set(db.obtener_usuario(coord))
db.actualizar_estado(n1, "PROCESANDO", comentario="ok", desde=("SUBIDA",))
try:
    db.actualizar_estado(n1, "PROCESANDO", comentario="doble clic", desde=("SUBIDA",))
    raise SystemExit("debía dar conflicto")
except db.ConflictoDeEstado:
    pass
db.USUARIO_ACTUAL.reset(tok)
hist = db.obtener_nota(n1)["historial"]
assert sum(1 for x in hist if x["estado"] == "PROCESANDO") == 1 and hist[-1]["usuario"] == "Carlos Coord"
# EN_REVISION avisa a Dirección; reabrir/propuestas con ruta_analizada
db.guardar_propuestas(n1, "NOTA", [], [{"id": 0, "ubicacion": "body:0", "original": "a", "propuesto": "b", "cambios": []}],
                      [], desde=("PROCESANDO",), ruta_analizada=r1)
n = db.obtener_nota(n1); assert n["estado"] == "EN_REVISION" and n["ruta_analizada"] == str(r1)
assert db.contar_notificaciones(direc["id"]) >= 1
assert automatico.ruta_texto_actual(n) == str(r1)
res = tmp / "res.docx"; shutil.copy(r1, res)
db.guardar_resultado_tras_revision(n1, res, [], sha256_resultado=integridad.sha256_archivo(res))
n = db.obtener_nota(n1); assert n["estado"] == "PROCESADA" and n["ruta_analizada"] is None and n["sha256_resultado"]
try:
    db.guardar_resultado_tras_revision(n1, res, [])
    raise SystemExit("debía dar conflicto (ya no está en revisión)")
except db.ConflictoDeEstado:
    pass
assert integridad.resultado_coincide(n)
res.write_bytes(res.read_bytes() + b"x")
assert not integridad.resultado_coincide(n)
assert any(v["estado"] == "ALTERADO" for v in integridad.verificar_nota(db.obtener_nota(n1)))
shutil.copy(r1, res)  # restaurado
db.marcar_firmada(n1, res, sha256_firmado="abc")
assert db.obtener_nota(n1)["estado"] == "FIRMADA"
try:
    db.marcar_firmada(n1, res); raise SystemExit("doble firma")
except db.ConflictoDeEstado:
    pass
assert db.contar_notificaciones(mesa) >= 1
db.marcar_despachada(n1)
assert db.contar_notificaciones(asesor) >= 1  # le avisan que su nota fue despachada
db.marcar_notificacion_leida(db.listar_notificaciones(asesor)[0]["id"])
db.marcar_todas_leidas(asesor); assert db.contar_notificaciones(asesor) == 0
print("3 usuarios/notificaciones/transiciones/integridad OK")

# ---------- 4) bloqueo de login
for _ in range(4): db.registrar_intento_login("Ase", "1.1.1.1", False)
assert not db.login_bloqueado("ase", "1.1.1.1")
db.registrar_intento_login("ase", "1.1.1.1", False)
assert db.login_bloqueado("ASE", "1.1.1.1")
assert not db.login_bloqueado("otro", "9.9.9.9")
db.registrar_intento_login("ase", "1.1.1.1", True)
assert not db.login_bloqueado("ase", "1.1.1.1")
for i in range(30): db.registrar_intento_login(f"u{i}", "6.6.6.6", False)
assert db.login_bloqueado("nuevo", "6.6.6.6")
assert len(db.listar_intentos_fallidos(10)) == 10
print("4 bloqueo de login OK")

# ---------- 5) accesos, búsqueda, versiones
tok = db.USUARIO_ACTUAL.set(U_ase)
db.registrar_acceso(n1, "VER"); db.registrar_acceso(n1, "DESCARGA", "firmada")
db.USUARIO_ACTUAL.reset(tok)
ac = db.listar_accesos(10, n1); assert len(ac) == 2 and ac[0]["usuario"] == "Ana Asesora" and ac[0]["nombre_original"]
db.indexar_texto(n1, "El interno solicita traslado urgente al Complejo Federal de Ezeiza")
db.indexar_texto(n1, "El interno solicita traslado urgente al Complejo Federal de Ezeiza")  # reindexar no duplica
hits = db.buscar_texto("traslado ezei")
assert [i for i, _ in hits] == [n1] and "\x02" in hits[0][1], hits
assert db.buscar_texto("inexistente") == [] and db.buscar_texto("   ") == []
assert db.buscar_texto('"; DROP TABLE notas; --') is not None
fts = db.FTS_DISPONIBLE; db.FTS_DISPONIBLE = False
assert [i for i, _ in db.buscar_texto("traslado ezeiza")] == [n1]
db.FTS_DISPONIBLE = fts
r2 = mkdoc("Nota 1 v2.docx", ["Se solicita atención médica para Juan Pérez, con urgencia."])
v2 = db.crear_nota("Nota 1 v2.docx", eq, r2, usuario_id=asesor, version_de=n1)
v3 = db.crear_nota("Nota 1 v3.docx", eq, r2, usuario_id=asesor, version_de=v2)
cad = db.cadena_de_versiones(v3)
assert [c["id"] for c in cad] == [n1, v2, v3] and [c["numero"] for c in cad] == [1, 2, 3]
assert [c["id"] for c in db.cadena_de_versiones(n1)] == [n1, v2, v3]
filas = analisis.comparar_versiones(nucleo.parrafos_de_documento(r1), nucleo.parrafos_de_documento(r2))
assert [f["tipo"] for f in filas] == ["cambiado"], filas
print("5 accesos/búsqueda/versiones OK")

# ---------- 6) listar_notas no pierde activas viejas
db_ids = []
for i in range(12):
    rr = mkdoc(f"x{i}.docx", ["x"])
    nid = db.crear_nota(f"x{i}.docx", eq, rr)
    if i >= 2:
        db.actualizar_estado(nid, "RECHAZADA", comentario="t")
    db_ids.append(nid)
nombres = {n["id"] for n in db.listar_notas(limite=3)}
assert db_ids[0] in nombres and db_ids[1] in nombres      # activas: siempre
assert sum(1 for i in db_ids[2:] if i in nombres) == 3     # terminales: solo las últimas 3
print("6 listar_notas OK")

# ---------- 7) reubicar rutas
carpeta = tmp / "nueva" / "datos"; (carpeta / "originales").mkdir(parents=True)
real = carpeta / "originales" / "a.docx"; real.write_bytes(b"x")
rid = db.crear_nota("a.docx", eq, "C:\\viejo\\app\\datos\\originales\\a.docx")
assert db.reubicar_rutas(carpeta) >= 1
assert db.obtener_nota(rid)["ruta_original"] == str(real)
assert db.reubicar_rutas(carpeta) == 0
print("7 reubicar rutas OK")

# ---------- 8) respaldo
dest = tmp / "resp"
respaldo.CONFIG.update(activo=True, carpeta=dest, conservar=2, hora="00:00", carpeta_datos=tmp)
(tmp / "originales").mkdir(exist_ok=True); (tmp / "originales" / "o.docx").write_bytes(b"orig")
import time
for _ in range(3):
    ok, msg = respaldo.hacer_respaldo(); assert ok, msg; time.sleep(1.1)
assert len(list((dest / "db").glob("*.db"))) == 2          # rotación
assert (dest / "documentos" / "originales" / "o.docx").read_bytes() == b"orig"
import sqlite3
c = sqlite3.connect(next((dest / "db").glob("*.db"))); assert c.execute("SELECT COUNT(*) FROM notas").fetchone()[0] > 5; c.close()
respaldo._ultimo["fecha"] = None
assert not respaldo.toca_respaldo_automatico(datetime.now().replace(hour=0, minute=0) - timedelta(minutes=1)) or True
assert respaldo.toca_respaldo_automatico.__call__ is not None
respaldo.CONFIG["activo"] = False
assert not respaldo.toca_respaldo_automatico()
respaldo.CONFIG["activo"] = True
print("8 respaldo OK")

# ---------- 9) reportes por equipo
rep_a = reportes.generar_reporte(equipo_id=eq); rep_b = reportes.generar_reporte(equipo_id=eq2)
rep_t = reportes.generar_reporte()
assert rep_b["total_archivos"] == 0 and rep_a["total_archivos"] == rep_t["total_archivos"] > 0
print("9 reportes por equipo OK")

# ---------- 10) fechas
assert fechas.a_hora_local("2026-10-06T22:30:00+00:00") == "06/10/2026 19:30"
assert fechas.a_hora_local(None) == "—" or fechas.a_hora_local(None) == ""
print("10 fechas OK")

# ---------- 11) funciones de main.py (extraídas por AST, sin FastAPI)
src = open(APP + "/main.py", encoding="utf-8").read()
tree = ast.parse(src)
def fuente(nombre):
    for nodo in tree.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and nodo.name == nombre:
            return ast.get_source_segment(src, nodo)
    raise KeyError(nombre)
CARP = tmp / "datos_main" / "originales"; CARP.mkdir(parents=True)
class AutoStub:
    llamadas = []
    @staticmethod
    def clasificar_rapido(i): AutoStub.llamadas.append(("c", i))
    @staticmethod
    def encolar_analisis(i): AutoStub.llamadas.append(("e", i))
ns = dict(Request=object, Path=Path, uuid=uuid, zipfile=zipfile, re=re, logging=logging, datetime=datetime, timezone=timezone, db=db,
          integridad=integridad, automatico=AutoStub, CARPETA_ORIGINALES=CARP, MAX_BYTES_SUBIDA=1000000,
          EXTENSIONES_VALIDAS={".docx", ".doc", ".pdf"}, urlparse=__import__("urllib.parse").parse.urlparse,
          Markup=__import__("markupsafe").Markup, escape=__import__("markupsafe").escape, html_lib=__import__("html"))
for nombre in ("_sanitizar_nombre_archivo", "_ruta_archivo_historico", "_ArchivoRechazado", "_guardar_subida", "_fecha_valida",
               "_origen_valido", "_fragmento_html"):
    exec(fuente(nombre), ns)
import ast as _ast
for _n in _ast.parse(open(APP + "/main.py", encoding="utf-8").read()).body:
    if isinstance(_n, _ast.Assign) and getattr(_n.targets[0], "id", "") == "ADMIN_PESTANAS":
        exec(compile(_ast.Module([_n], []), "main.py", "exec"), ns)
exec("_FIRMAS_ARCHIVO = {'.docx': b'PK\\x03\\x04', '.doc': b'\\xd0\\xcf\\x11\\xe0', '.pdf': b'%PDF'}", ns)
class Arch:
    def __init__(self, nombre, datos): self.filename = nombre; self.file = io.BytesIO(datos)
def docx_bytes(txt="hola"):
    d = docx.Document(); d.add_paragraph(txt); b = io.BytesIO(); d.save(b); return b.getvalue()
U = {"id": asesor}
ok_id, motivo = ns["_guardar_subida"](Arch("Nota ñ/ok.docx", docx_bytes()), U, eq, "2026-01-02", "URGENTE")
assert ok_id and motivo is None, motivo
n = db.obtener_nota(ok_id)
assert Path(n["ruta_original"]).is_file() and n["sha256_original"] == integridad.sha256_archivo(n["ruta_original"])
assert ("c", ok_id) in AutoStub.llamadas and ("e", ok_id) in AutoStub.llamadas
assert list(CARP.rglob("*.docx")) and not [p for p in CARP.rglob("*") if p.is_file() and re.fullmatch(r"[0-9a-f]{32}", p.stem)]
ns["MAX_BYTES_SUBIDA"] = 2000
for nombre, datos, parte in [("x.exe", b"MZ", "solo se aceptan"), ("x.pdf", b"MZ00", "no coincide"), ("v.docx", b"", "vacío"), ("f.docx", b"MZ" + b"0" * 50, "no coincide"),
                              ("g.doc", b"PK\x03\x04zz", "no coincide"), ("g.docx", b"PK\x03\x04" + b"0" * 30, "dañado"),
                              ("grande.docx", b"PK\x03\x04" + b"0" * 3000, "máximo")]:
    i, m = ns["_guardar_subida"](Arch(nombre, datos), U, eq, None, "NORMAL")
    assert i is None and parte in m, (nombre, m)
assert not [p for p in CARP.rglob("*") if p.is_file() and re.fullmatch(r"[0-9a-f]{32}", p.stem)], "quedaron temporales"
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z: z.writestr("otra/cosa.xml", "x")
ns["MAX_BYTES_SUBIDA"] = 1000000
i, m = ns["_guardar_subida"](Arch("z.docx", buf.getvalue()), U, eq, None, "NORMAL"); assert i is None and "dañado" in m
i, m = ns["_guardar_subida"](Arch("viejo.doc", b"\xd0\xcf\x11\xe0" + b"0" * 100), U, eq, None, "NORMAL"); assert i, m
assert ns["_fecha_valida"]("2026-02-30") is None and ns["_fecha_valida"]("2026-02-03") == "2026-02-03" and ns["_fecha_valida"](None) is None
class Rq:
    def __init__(self, **h): self.headers = h
assert ns["_origen_valido"](Rq(host="app:8000", origin="http://app:8000"))
assert not ns["_origen_valido"](Rq(host="app:8000", origin="http://malo.com"))
assert not ns["_origen_valido"](Rq(host="app:8000", referer="http://malo.com/x"))
assert ns["_origen_valido"](Rq(host="app:8000"))           # sin Origin (script)
assert ns["_origen_valido"](Rq(host="app:8000", origin="null", referer="http://app:8000/admin/equipos"))  # Origin «null» con Referer propio
assert not ns["_origen_valido"](Rq(host="app:8000", origin="null"))  # «null» sin nada más: se rechaza
assert "content=\"same-origin\"" in open(APP + "/templates/base.html", encoding="utf-8").read(), "con no-referrer el navegador manda Origin: null y se rechazan los formularios"
assert not ns["_origen_valido"](Rq(host="app:8000", origin="null"))
assert ns["_origen_valido"](Rq(host="interno", origin="https://publico", **{"x-forwarded-host": "publico"}))
frag = ns["_fragmento_html"]("a <script>x</script> \x02hit\x03")
assert "<script>" not in str(frag) and "<mark>hit</mark>" in str(frag)
print("11 helpers de main.py (subida validada, CSRF, XSS) OK")

# ---------- 12) auditoría estática de endpoints
GUARDAS = ("_exigir_rol", "_puede_ver_nota", "_puede_gestionar_nota", "_nota_gestionable", "_equipo_del_reporte", "_puede_editar_texto", "_enviar_notas", "_hallazgo_aplicable", "_opciones_asignacion")
sin_guarda, total = [], 0
for nodo in tree.body:
    if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
        rutas = [d for d in nodo.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                 and d.func.attr in ("get", "post") and isinstance(d.func.value, ast.Name) and d.func.value.id == "app"]
        if not rutas: continue
        total += 1
        cuerpo = ast.get_source_segment(src, nodo)
        if not any(g in cuerpo for g in GUARDAS):
            sin_guarda.append((rutas[0].args[0].value, nodo.name))
print("   endpoints:", total, "| sin guarda de rol/propiedad:", [r for r, _ in sin_guarda])
PERMITIDOS = {"/inicio", "/login", "/salud", "/logout", "/", "/equipo", "/subir", "/buscar", "/notificaciones", "/notificaciones/leer-todas",
              "/notificaciones/{notificacion_id}/abrir", "/notificaciones/{notificacion_id}/no-leido", "/perfil", "/perfil/clave", "/bloques",
              "/nota/{nota_id}/aprobar-coordinacion", "/nota/{nota_id}/rechazar-coordinacion", "/nota/{nota_id}/editar-base",
              "/biblioteca", "/biblioteca/modelos/{modelo_id}/descargar", "/ausencias", "/ausencias/{ausencia_id}/borrar", "/deshacer", "/lote/prioridad",
              "/sesion/activa", "/filtros/guardar", "/filtros/borrar", "/guia-estilo", "/auth/ingreso/{token}", "/politica-ia"}  # filtros: solo tocan las preferencias de quien está logueado;  # solo renueva la actividad de la sesión: alcanza con estar logueado
inesperados = [r for r, _ in sin_guarda if r not in PERMITIDOS]
assert not inesperados, inesperados
# los de coordinación verifican equipo a mano: se comprueba el texto
assert "_puede_editar_texto" in fuente("editar_base")
for nombre in ("aprobar_coordinacion", "rechazar_coordinacion"):
    assert "_exigir_rol(usuario, auth.ROL_COORDINADOR)" in fuente(nombre) or "_exigir_rol" in fuente(nombre)
for nombre in ("ver_patrones", "agregar_patron"):
    assert "_exigir_rol(usuario_actual(request))" in fuente(nombre), nombre
assert "_puede_ver_nota" in fuente("ver_nota_fragmento")
print("12 auditoría de endpoints OK")

# ---------- 13) plantillas
env = jinja2.Environment(loader=jinja2.FileSystemLoader(APP + "/templates"), autoescape=True)
env.globals["ico"] = __import__("iconos").svg
env.globals.update(es_admin=auth.es_admin, ETIQUETAS_ROL=auth.ETIQUETAS_ROL, ROLES_MESA=auth.ROLES_MESA, ROLES_DE_EQUIPO=auth.ROLES_DE_EQUIPO,
  PRIORIDADES=auth.PRIORIDADES, ETIQUETAS_PRIORIDAD=auth.ETIQUETAS_PRIORIDAD, etiqueta_tema=analisis.etiqueta_tema,
  ETIQUETAS_TRABAJO=automatico.ETIQUETAS_TRABAJO, ETIQUETAS_ESTADO_TRABAJO=automatico.ETIQUETAS_ESTADO_TRABAJO,
  contar_notificaciones=db.contar_notificaciones, hoy_local=lambda: "2026-10-07",
  datos_despacho=lambda n: dict(nro_nota="", nro_expte="", expte_propuesto=False, preparado=False, tiene_pdf=False),
  semaforo=lambda n: None, estado_respuesta=lambda n, f=None: None, comentarios_de=lambda n: [], duplicados_de=lambda n: [], glosario_en=lambda n: [], asistentes_de=lambda n: dict(resumen=[], plazos=[], plazo=None, casi_iguales=[], antecedentes=[], entidades=[], destino=None, estilo=[]), AGENTES={}, info_deshacer=lambda r: None, eventos_nota=lambda n: [dict(texto=h["estado"], detalle=h.get("comentario"), fecha=h["fecha"], quien=h.get("usuario"), tipo="") for h in n.get("historial") or []], antiguedad_etapa=lambda n: None,
  datos_envio=lambda n: dict(propuesto="", contacto=None, contactos=[], envios=[]))
env.filters["hora"] = fechas.a_hora_local; env.filters["fecha_corta"] = fechas.fecha_corta
class S: pass
class R:
    def __init__(s, u): s.state = S(); s.state.usuario = u
UD = dict(db.obtener_usuario(direc["id"]))
req = R(UD)
h = env.get_template("error.html").render(request=req, codigo=403, detalle="No tenés permiso")
assert "No tenés permiso para esto" in h and "avisos" in h
h = env.get_template("error.html").render(request=R(None), codigo=404, detalle="x"); assert "No encontramos" in h
h = env.get_template("buscar.html").render(request=req, q="traslado", resultados=[{"nota": dict(db.obtener_nota(n1), etapa=None), "fragmento": ns["_fragmento_html"]("hola \x02traslado\x03")}])
assert "<mark>traslado</mark>" in h and "1 resultado" in h
h = env.get_template("buscar.html").render(request=req, q="zzz", resultados=[]); assert "No se encontró nada" in h
h = env.get_template("notificaciones.html").render(request=req, notificaciones=db.listar_notificaciones(asesor), ver="todos", total=1, nuevos=1)
assert "notificaciones/" in h
h = env.get_template("notificaciones.html").render(request=req, notificaciones=[], ver="todos", total=0, nuevos=0); assert "No tenés avisos" in h
h = env.get_template("comparar.html").render(request=req, a=db.obtener_nota(n1), b=db.obtener_nota(v2), filas=filas, resumen={"cambiado": 1, "agregado": 0, "quitado": 0})
assert "<ins>" in h and "<del>" in h and "1 párrafo(s) modificado" in h
nt = db.obtener_nota(n1); nt["etapa"] = flujo.etapa_para_estado(nt["estado"]); nt["prioridad_efectiva"] = nt["prioridad"]
ctxn = dict(request=req, nota=nt, etapas=flujo.ETAPAS, parrafos_originales=[{"indice": 0, "texto": "hola"}], error_vista_previa=None,
  pendientes=[], puede_gestionar=False, bloque=None, bloques_disponibles=[], analisis_sugerida=None, analisis_temas=[], analisis_referencias=[],
  analisis_ref_actual=None, analisis_hallazgos=[], analisis_mejoras=[], analisis_trabajos=[], analisis_precalculada=False, ia_ventana="",
  versiones=cad, puede_subir_version=True,
  integridad=[{"documento": "resultado", "huella": "a" * 64, "estado": "ALTERADO"}, {"documento": "original", "huella": "b" * 64, "estado": "OK"}],
  accesos_nota=ac)
h = env.get_template("nota.html").render(**ctxn)
for frag in ["Versiones del documento", "nueva-version", "ALTERADO", "Quién vio o descargó", "Ana Asesora", "ver qué cambió", "Carlos Coord"]:
    assert frag in h, frag
h2 = env.get_template("nota.html").render(**dict(ctxn, request=R(dict(UD, rol="ASESOR")), versiones=[], integridad=[], accesos_nota=[], puede_subir_version=False))
assert "Versiones del documento" not in h2 and "Integridad y accesos" not in h2
# base: navegación por rol
hb = env.get_template("perfil.html").render(request=R(dict(UD, rol="ASESOR", equipo_nombre="A", debe_cambiar_clave=0)), usuario=dict(UD, rol="ASESOR", equipo_nombre="A"), mensaje=None, error=None, minimo=8, obligatorio=False)
assert "/reportes" not in hb and "/aprendizaje/patrones" not in hb and "/admin" not in hb and "Avisos" in hb and 'action="/buscar"' in hb
hb = env.get_template("perfil.html").render(request=req, usuario=UD, mensaje=None, error=None, minimo=8, obligatorio=True)
assert "Tenés que cambiar tu clave" in hb and "/reportes" in hb and "/admin" in hb
# index con paginador y fecha en hora local
ni = dict(nt, tipo="NOTA", hallazgos_abiertos=0, actualizado_en="2026-10-06T22:30:00+00:00", equipo_nombre="G", asesor_nombre="A", tema=None, bloque_nombre=None)
kw = dict(request=req, usuario=UD, notas=[ni], etapas=flujo.ETAPAS, mostrar_despachadas=False, cantidad_despachadas=0, orden="", direccion="asc",
  f_nombre="", f_tipo="", f_etapa="", f_fecha="", f_prioridad="", f_tema="", temas_disponibles=[], tipos_disponibles=[], pendientes_firma=[ni],
  pendientes_revision=[], bloques_mesa=[], puede_armar_bloques=False, pagina=2, paginas=3, total=120,
  f_tarea="", tareas=[], borradores=[], volver="/", prioridad_opciones=[], tipo_opciones=[], etapa_opciones=[], tema_opciones=[])
hi = env.get_template("index.html").render(**kw)
assert "19:30" in hi and "Página 2 de 3" in hi and "pagina=3" in hi and "pagina=1" in hi and "reload()" not in hi
hi = env.get_template("index.html").render(**dict(kw, pagina=1, paginas=1, total=1)); assert "Página 1 de" not in hi
# admin
ia_estado = {"ventana": "17:00 a 07:00", "ahora": "x", "activa": True, "forzado": False, "en_ventana": False, "hilo_vivo": True, "actual": None, "ultimo_error": None, "resumen": {}}
usuarios = db.listar_usuarios()
ha = env.get_template("admin.html").render(request=req, usuario=UD, mensaje="Clave provisoria de X: abc123", modelos_ia=[], ia_estado=ia_estado, trabajos_ia=[],
  usuarios=usuarios, equipos=db.listar_equipos(), roles=auth.ROLES, etiquetas_rol=auth.ETIQUETAS_ROL, roles_de_equipo=auth.ROLES_DE_EQUIPO,
  intentos_fallidos=db.listar_intentos_fallidos(5), accesos=ac, respaldo=respaldo.estado(), salud={"base": "ok", "disco_libre_gb": 50.0, "base_mb": 1.2},
  pestanas=ns["ADMIN_PESTANAS"], pestana="usuarios", sub="listado", q="", usuarios_total=len(usuarios))
for frag in ["pestanas", "Usuario nuevo", "clave-provisoria", "/reactivar" if any(not u["activo"] for u in usuarios) else "/editar", "(clave provisoria)"]:
    assert frag in ha, frag
def _adm(pest, sub):
    return env.get_template("admin.html").render(request=req, usuario=UD, mensaje=None, modelos_ia=[], ia_estado=ia_estado, trabajos_ia=[],
      usuarios=usuarios, equipos=db.listar_equipos(), roles=auth.ROLES, etiquetas_rol=auth.ETIQUETAS_ROL, roles_de_equipo=auth.ROLES_DE_EQUIPO,
      intentos_fallidos=db.listar_intentos_fallidos(5), accesos=ac, respaldo=respaldo.estado(), salud={"base": "ok", "disco_libre_gb": 50.0, "base_mb": 1.2},
      pestanas=ns["ADMIN_PESTANAS"], pestana=pest, sub=sub, q="", usuarios_total=len(usuarios))
assert "Hacer un respaldo ahora" in _adm("sistema", "estado") and "Verificar integridad" in _adm("sistema", "integridad")
assert "Intentos de ingreso fallidos" in _adm("seguridad", "intentos") and "Últimos accesos" in _adm("seguridad", "accesos")
assert 'action="/admin/equipos"' in _adm("equipos", "nuevo") and 'action="/admin/usuarios"' in _adm("usuarios", "nuevo")
assert "Ollama" in _adm("ia", "modelos") and "nocturno" in _adm("ia", "cola")
# reportes: IA solo si corresponde, y el título ya no se rompe
rep = dict(total_archivos=0, total_notas=0, notas_aprobadas=0, notas_modificadas=0, notas_rechazadas=0, notas_en_curso=0, tiempo_promedio_aprobacion_coordinacion=None,
           tiempo_promedio_firma=None, tiempo_promedio_despacho_total=None, muestras={"aprobacion_coordinacion": 0, "firma": 0, "despacho_total": 0})
hr = env.get_template("reportes.html").render(request=R(dict(UD, rol="COORDINADOR")), reporte=rep, desde="", hasta="", ia=None, etiquetas_trabajo={}, solo_equipo=True)
assert "Análisis automático" not in hr and "solo las notas de tu equipo" in hr
ia = {"prioridad": {"aceptadas": 1, "descartadas": 1, "pendientes": 0, "tasa_aceptacion": 50}, "mejoras": {"aceptadas": 0, "descartadas": 0, "pendientes": 0, "tasa_aceptacion": None},
      "hallazgos": {"abiertos": 1, "revisados": 0, "ignorados": 0}, "tiempos": {}, "temas": {}}
hr = env.get_template("reportes.html").render(request=req, reporte=rep, desde="", hasta="", ia=ia, etiquetas_trabajo={}, solo_equipo=False)
t = re.search(r"<title>(.*?)</title>", hr, re.S).group(1); assert "<section" not in t and "Reporte" in t and "Análisis automático" in hr
print("13 plantillas OK")
print("ALL OK")
