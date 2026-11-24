"""Ideas 31, 32, 41-48: IA adaptativa, verificador, lote/deshacer, atajos, plazos,
comentarios, glosario/modelos, duplicados, ausencias, correo."""
import sys, tempfile, ast, re, logging, asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import docx, jinja2
import database as db
import auth, nucleo, analisis, automatico, carga, plazos, avisos_correo, respaldo, fechas
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
ase_b = db.crear_usuario("aseb", h, s, "Beto Asesor", "ASESOR", eq)
coo = db.crear_usuario("coo", h, s, "Carlos Coord", "COORDINADOR", eq)
coo2 = db.crear_usuario("coo2", h, s, "Otro Coord", "COORDINADOR", eq2)
direc = next(u for u in db.listar_usuarios() if u["rol"] == "DIRECTOR")
U = {k: db.obtener_usuario(v) for k, v in dict(ase=ase, ase_b=ase_b, coo=coo, coo2=coo2).items()}
U["dir"] = db.obtener_usuario(direc["id"])

# ---- 31 IA adaptativa
assert carga.hay_capacidad(medicion={"cpu": 20, "ram_mb": 4000}, oficina=True)
assert not carga.hay_capacidad(medicion={"cpu": 50, "ram_mb": 4000}, oficina=True)
assert carga.hay_capacidad(medicion={"cpu": 50, "ram_mb": 4000}, oficina=False)
assert not carga.hay_capacidad(medicion={"cpu": 5, "ram_mb": 300})
assert not carga.activa(); carga.activar(True); assert carga.activa()
t = automatico.Trabajador()
automatico.CONFIG["activa"] = True
orig_hay = carga.hay_capacidad
carga.hay_capacidad = lambda oficina=False, medicion=None: False
assert not t._hay_lugar() and t._puede_correr()          # adaptativa: puede seguir, no arranca sin lugar
t.forzar = True; assert t._hay_lugar(); t.forzar = False
carga.activar(False); assert t._hay_lugar()               # sin adaptativa no limita
carga.hay_capacidad = orig_hay
print("31 adaptativa OK")

# ---- 32 verificador + tablero
nucleo.USAR_IA = True; nucleo._MODELOS_DESCARGADOS = {"m1", "m2"}
nucleo.OLLAMA_MODEL = "m1"; nucleo.OLLAMA_MODELOS.update({p: "m1" for p in nucleo.PROCESOS_IA})
assert not automatico.verificador_activo()
nucleo.OLLAMA_MODELOS["verificacion"] = "m2"; assert automatico.verificador_activo()
nucleo.OLLAMA_MODELOS["verificacion"] = "m1"; db.guardar_ajuste("verificador", "1"); assert automatico.verificador_activo()
hz = {"valores": [{"fragmento": "Juan Pérez, DNI 1"}, {"fragmento": "Juan Pérez, DNI 2"}]}
respuestas = iter(['{"contradice": true, "motivo": "dni"}', '{"contradice": false}', "basura"])
nucleo.consultar_ollama = lambda proceso, prompt, **k: (next(respuestas) if proceso == "verificacion" else None)
assert analisis.verificar_hallazgo_ia(hz) == "OK" and analisis.verificar_hallazgo_ia(hz) == "DUDOSO" and analisis.verificar_hallazgo_ia(hz) is None
nucleo._MODELOS_DESCARGADOS = set()
assert analisis.verificar_hallazgo_ia(hz) is None
# insertar hallazgos con verificación
n1 = db.crear_nota("N1.docx", eq, mkdoc("n1.docx", ["a"]), usuario_id=ase, borrador=True)
def hall(dato, ver):
    x = analisis._hallazgo("AVISO", dato, "det", [{"doc_id": 1, "doc": "A", "valor": "x" + dato, "fragmento": "x" * 9},
                                                   {"doc_id": 2, "doc": "B", "valor": "y" + dato, "fragmento": "y" * 9}])
    x["clave"] = analisis.clave_hallazgo(x); x.update(origen="IA", modelo="m1", verificacion=ver); return x
db.reemplazar_hallazgos(n1, None, [hall("uno (IA)", "OK"), hall("dos (IA)", "DUDOSO"), hall("tres (IA)", None)])
filas = {f["dato"]: f for f in db.obtener_nota(n1)["hallazgos"]} if "hallazgos" in db.obtener_nota(n1) else {}
with db._conexion() as con:
    est = {f["dato"]: (f["estado"], f["resuelto_por"]) for f in con.execute("SELECT dato, estado, resuelto_por FROM consistencia_hallazgos")}
assert est["uno (IA)"][0] == "ABIERTO" and est["dos (IA)"] == ("IGNORADO", "Verificador IA") and est["tres (IA)"][0] == "ABIERTO", est
assert len(db.listar_notificaciones(ase)) == 1 and "2 alertas" in db.listar_notificaciones(ase)[0]["texto"]
cal = db.calidad_ia()
hm = cal["hallazgos"][0]
assert hm["modelo"] == "m1" and hm["total"] == 3 and hm["dudosos_verificador"] == 1 and hm["confirmados_verificador"] == 1, hm
print("32 verificador/tablero OK")

# ---- 43 plazos
hoy = date(2026, 10, 7)
def nota_fake(prior="NORMAL", creada="2026-10-01", vence=None, estado="SUBIDA"):
    return dict(prioridad=prior, creado_en=creada + "T12:00:00+00:00", vence=vence, estado=estado)
assert plazos.semaforo(nota_fake("URGENTE", "2026-10-01"), hoy)["nivel"] == "vencida"       # vence 10-03
assert plazos.semaforo(nota_fake("URGENTE", "2026-10-06"), hoy)["texto"] == "Vence mañana"
assert plazos.semaforo(nota_fake("PRIORITARIA", "2026-10-05"), hoy)["nivel"] == "ambar"     # vence 10-10 (3 d)
assert plazos.semaforo(nota_fake("NORMAL", "2026-10-06"), hoy)["nivel"] == "verde"
assert plazos.semaforo(nota_fake(vence="2026-10-07"), hoy)["nivel"] == "rojo"
assert plazos.semaforo(nota_fake(estado="DESPACHADA"), hoy) is None
n2 = db.crear_nota("N2.docx", eq, mkdoc("n2.docx", ["b"]), usuario_id=ase, prioridad="URGENTE")
with db._conexion() as con:
    con.execute("UPDATE notas SET creado_en = '2020-01-01T00:00:00+00:00' WHERE id = ?", (n2,))
av = db.escalar_vencidas(hoy)
assert n2 in av
for uid in (coo, direc["id"]):
    assert any("superó su plazo" in x["texto"] for x in db.listar_notificaciones(uid)), uid
assert not any("superó su plazo" in x["texto"] for x in db.listar_notificaciones(coo2))   # otro equipo
assert db.escalar_vencidas(hoy) == []                                                      # una sola vez
db.fijar_vencimiento(n2, "2030-01-01")
assert db.obtener_nota(n2)["vence"] == "2030-01-01" and db.obtener_nota(n2)["escalada_en"] is None
print("43 plazos OK")

# ---- helpers de main.py por AST
src = open(APP + "/main.py", encoding="utf-8").read(); tree = ast.parse(src)
class HTTPExc(Exception):
    def __init__(self, status_code, detail=None): self.status_code, self.detail = status_code, detail
class Redir:
    def __init__(self, url, status_code=307): self.url = url
class Req:
    def __init__(self, usuario): self.session = {}; self.usuario = usuario; self.f = None
    async def form(self): return self.f
class Form:
    def __init__(self, d): self.d = d
    def get(self, k, defecto=None): v = self.d.get(k, defecto); return v[0] if isinstance(v, list) else v
    def getlist(self, k): v = self.d.get(k, []); return v if isinstance(v, list) else [v]
ns = dict(Request=object, Form=lambda *a, **k: None, db=db, auth=auth, nucleo=nucleo, re=re, Path=Path, HTTPException=HTTPExc, RedirectResponse=Redir, plazos=plazos,
          ESTADOS_ACTIVOS=("BORRADOR", "SUBIDA", "PROCESANDO", "EN_REVISION", "PROCESADA", "FIRMADA", "ERROR"),
          _flash=lambda r, t: r.session.__setitem__("flash", t), usuario_actual=lambda r: r.usuario,
          _CITA_MENCION=re.compile(r"@([A-Za-z0-9_.\-]{2,40})"), _fecha_valida=lambda v: v if re.match(r"\d{4}-\d\d-\d\d$", v or "") else None)
def fuente(n):
    for nodo in tree.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name == n:
            src_ = ast.get_source_segment(src, nodo)
            return re.sub(r"^@app\.[^\n]+\n", "", src_)
    raise KeyError(n)
exec(fuente("_puede_gestionar_nota"), ns)
for n in ("lote_prioridad", "deshacer", "fijar_vence", "agregar_comentario", "_glosario_en", "_texto_para_glosario"):
    exec(fuente(n), ns)
ns["_puede_ver_nota"] = lambda u, n: u["rol"] in ("COORDINADOR", "DIRECTOR") and (u["rol"] == "DIRECTOR" or u["equipo_id"] == n["equipo_id"]) or u["id"] == n["usuario_id"]

# ---- 41 lote + deshacer
n3 = db.crear_nota("N3.docx", eq, mkdoc("n3.docx", ["c"]), usuario_id=ase)
n4 = db.crear_nota("N4.docx", eq, mkdoc("n4.docx", ["d"]), usuario_id=ase_b)    # de otro asesor: no gestionable por Ana
rq = Req(U["ase"])
r = (setattr(rq, "f", Form({"prioridad": "URGENTE", "nota_ids": [str(n3), str(n4), "x"]})), asyncio.run(ns["lote_prioridad"](rq)))
assert db.obtener_nota(n3)["prioridad"] == "URGENTE" and db.obtener_nota(n4)["prioridad"] == "NORMAL"
assert list(rq.session["deshacer"]["previas"]) == [str(n3)]
ns["deshacer"](rq, "")
assert db.obtener_nota(n3)["prioridad"] == "NORMAL" and "deshacer" not in rq.session
(setattr(rq, "f", Form({"prioridad": "NOEXISTE", "nota_ids": [str(n3)]})), asyncio.run(ns["lote_prioridad"](rq)))
assert "deshacer" not in rq.session and db.obtener_nota(n3)["prioridad"] == "NORMAL"
(setattr(rq, "f", Form({"prioridad": "PRIORITARIA", "nota_ids": [str(n3)]})), asyncio.run(ns["lote_prioridad"](rq)))
ns["deshacer"](rq, "1")                       # "Cerrar": descarta sin deshacer
assert db.obtener_nota(n3)["prioridad"] == "PRIORITARIA" and "deshacer" not in rq.session
print("41 lote/deshacer OK")

# ---- 44 comentarios + menciones
tok = db.USUARIO_ACTUAL.set(U["coo"])
rq = Req(U["coo"])
ns["agregar_comentario"](rq, n3, "Revisar fechas @ase y @coo2 y @noexiste")
db.USUARIO_ACTUAL.reset(tok)
cs = db.listar_comentarios(n3)
assert len(cs) == 1 and cs[0]["usuario"] == "Carlos Coord"
av = [x["texto"] for x in db.listar_notificaciones(ase)]
assert any("Carlos Coord comentó" in x for x in av)
assert not db.listar_notificaciones(coo2), "coo2 es de otro equipo: sin acceso, sin aviso"
assert "No tienen acceso" in rq.session["flash"] and "@coo2" in rq.session["flash"]
ns["agregar_comentario"](rq, n3, "   ")
assert db.listar_comentarios(n3).__len__() == 1
print("44 comentarios OK")

# ---- 45 glosario y modelos
doc = mkdoc("g.docx", ["Se remite el informe al interno, conforme a lo solicitado."])
n5 = db.crear_nota("N5.docx", eq, doc, usuario_id=ase)
db.agregar_termino("interno", "persona privada de la libertad", "Término preferido")
db.agregar_termino("inexistente", None, None)
hall_ = ns["_glosario_en"](db.obtener_nota(n5))
assert [x["termino"] for x in hall_] == ["interno"] and hall_[0]["preferido"] == "persona privada de la libertad"
assert ns["_glosario_en"](db.obtener_nota(n3)) == []
db.agregar_modelo_nota("Modelo oficio", "para oficios", doc)
m = db.listar_modelos_nota()[0]; assert db.obtener_modelo_nota(m["id"])["nombre"] == "Modelo oficio"
db.borrar_modelo_nota(m["id"]); assert db.listar_modelos_nota() == []
print("45 glosario/modelos OK")

# ---- 46 duplicados
a = mkdoc("d1.docx", ["Texto idéntico para probar."]); b = mkdoc("d2.docx", ["Texto idéntico para probar."]); c = mkdoc("d3.docx", ["Otro texto."])
ids = [db.crear_nota(f"D{i}.docx", eq, p, usuario_id=ase) for i, p in enumerate((a, b, c))]
for i, p in zip(ids, (a, b, c)): db.guardar_hash_texto(i, nucleo.hash_texto_documento(p))
d = db.buscar_duplicados(ids[1]); assert [x["id"] for x in d] == [ids[0]] and not d[0]["mismo_archivo"]
assert db.buscar_duplicados(ids[2]) == []
nv = db.crear_nota("D1 v2.docx", eq, a, usuario_id=ase, version_de=ids[0]); db.guardar_hash_texto(nv, nucleo.hash_texto_documento(a))
assert ids[0] not in [x["id"] for x in db.buscar_duplicados(nv)], "una versión no es duplicado de su original"
print("46 duplicados OK")

# ---- 47 ausencias
hoy_s = datetime.now(fechas.zona()).date()
db.crear_ausencia(ase, (hoy_s - timedelta(days=1)).isoformat(), (hoy_s + timedelta(days=1)).isoformat(), ase_b)
assert db.ausencia_vigente(ase)["nombre"] == "Ana Asesora"
n6 = db.crear_nota("N6.docx", eq, mkdoc("n6.docx", ["z"]), usuario_id=ase, borrador=True)
db.enviar_a_coordinacion(n6)       # aviso a coordinación (no a ase)
tok = db.USUARIO_ACTUAL.set(U["coo"])
db.notificar_usuarios([ase], "Algo para Ana", "/nota/1")
db.USUARIO_ACTUAL.reset(tok)
assert any(x["texto"] == "Algo para Ana" for x in db.listar_notificaciones(ase))
assert any(x["texto"] == "(Delegado de Ana Asesora) Algo para Ana" for x in db.listar_notificaciones(ase_b))
db.crear_ausencia(coo, "2020-01-01", "2020-01-02", coo)   # vencida: no cuenta
assert db.ausencia_vigente(coo) is None
aid = db.listar_ausencias(ase)[0]["id"]; db.borrar_ausencia(aid); assert db.ausencia_vigente(ase) is None
print("47 ausencias OK")

# ---- 48 correo
enviados = []
fake = lambda d, a, c: enviados.append((d, a, c))
avisos_correo.CONFIG.update(servidor="smtp.x", remitente="sistema@x.ar", resumen_hora="08:00", url_base="http://pc:8000")
assert avisos_correo.tick(enviar_fn=fake) == 0                      # apagado por defecto
db.fijar_correo_usuario(coo, "Carlos@X.ar")
db.guardar_ajuste("correo_activo", "1")
ahora = datetime(2026, 10, 8, 9, 0, tzinfo=fechas.zona())
tok = db.USUARIO_ACTUAL.set(U["ase"]); n7 = db.crear_nota("N7.docx", eq, mkdoc("n7.docx", ["q"]), usuario_id=ase); db.USUARIO_ACTUAL.reset(tok)
assert db.correos_pendientes() == []                                # modo resumen: no hay cola
k = avisos_correo.tick(ahora=ahora, enviar_fn=fake)
assert k >= 1 and any(d == "carlos@x.ar" and "Resumen diario" in a and "http://pc:8000/nota/" in c for d, a, c in enviados), enviados
assert avisos_correo.tick(ahora=ahora, enviar_fn=fake) == 0         # el resumen sale una vez por día
assert avisos_correo.tick(ahora=ahora.replace(hour=7), enviar_fn=fake) == 0
enviados.clear(); db.guardar_ajuste("correo_inmediatos", "1")
tok = db.USUARIO_ACTUAL.set(U["ase"]); db.crear_nota("N8.docx", eq, mkdoc("n8.docx", ["w"]), usuario_id=ase); db.USUARIO_ACTUAL.reset(tok)
assert len(db.correos_pendientes()) >= 1
avisos_correo.tick(ahora=ahora.replace(hour=7), enviar_fn=fake)
assert enviados and db.correos_pendientes() == []
def falla(*a): raise OSError("sin servidor")
tok = db.USUARIO_ACTUAL.set(U["ase"]); db.crear_nota("N9.docx", eq, mkdoc("n9.docx", ["e"]), usuario_id=ase); db.USUARIO_ACTUAL.reset(tok)
avisos_correo.tick(ahora=ahora.replace(hour=7), enviar_fn=falla)
assert len(db.correos_pendientes()) >= 1                            # si falla el envío, queda en cola
print("48 correo OK")

# ---- plantillas nuevas
env = jinja2.Environment(loader=jinja2.ChoiceLoader([jinja2.DictLoader({"base.html": "{% block contenido %}{% endblock %}"}), jinja2.FileSystemLoader(APP + "/templates")]), autoescape=True)
env.globals["ico"] = __import__("iconos").svg
env.filters["hora"] = fechas.a_hora_local
env.globals.update(es_admin=auth.es_admin, semaforo=lambda n: plazos.semaforo(n, hoy), estado_respuesta=plazos.estado_respuesta, comentarios_de=lambda n: db.listar_comentarios(n["id"]),
                   duplicados_de=lambda n: db.buscar_duplicados(n["id"]), glosario_en=lambda n: [{"termino": "interno", "preferido": "PPL", "nota": "x"}])
class St: pass
class R: pass
st = St(); st.usuario = U["coo"]; rq = R(); rq.state = st
o = env.get_template("_extras_nota.html").render(request=rq, nota=dict(db.obtener_nota(n3), estado="SUBIDA", creado_en="2026-10-01T00:00:00+00:00"))
assert "Comentarios internos (1)" in o and "Fijar plazo" in o and "Revisar fechas" in o
o = env.get_template("biblioteca.html").render(usuario=U["dir"], terminos=db.listar_terminos(), modelos=[])
assert "interno" in o and "Agregar término" in o
o = env.get_template("ausencias.html").render(usuario=U["ase"], candidatos=[U["ase_b"]], hoy="2026-10-07", ausencias=[], es_dir=False, vigente=None)
assert "Registrar ausencia" in o
for t in ("admin.html", "index.html", "nota.html"): env.get_template(t)    # compilan
env.globals.update(ETIQUETAS_TRABAJO=automatico.ETIQUETAS_TRABAJO, ETIQUETAS_ESTADO_TRABAJO=automatico.ETIQUETAS_ESTADO_TRABAJO)
ctx = dict(request=rq, usuario=U["dir"], mensaje=None, pestanas={"ia": ("IA", [("calidad", "Calidad")]), "sistema": ("Sistema", [("correo", "Correo")])},
           q="", usuarios_total=1, feedback_ia=[], modelos_ia=[], ia_estado={}, trabajos_ia=[], usuarios=db.listar_usuarios(), equipos=[], roles=auth.ROLES,
           etiquetas_rol=auth.ETIQUETAS_ROL, roles_de_equipo=auth.ROLES_DE_EQUIPO, intentos_fallidos=[], accesos=[], respaldo={}, salud={},
           adaptativa=carga.estado(), verificador_activo=True,
           correo=dict(activo=True, inmediatos=False, configurado=True, servidor="smtp.x", hora="08:00"))
o = env.get_template("admin.html").render(pestana="ia", sub="calidad", calidad=db.calidad_ia(), **ctx)
assert "IA adaptativa" in o and "Verificador cruzado" in o and "m1" in o
o = env.get_template("admin.html").render(pestana="sistema", sub="correo", calidad=None, **ctx)
assert "Avisos por correo" in o and "Carlos Coord" in o
# atajos J/K
exec(fuente("_anotar_prioridad_efectiva"), ns)
ns["_filtrar_por_visibilidad"] = lambda todas, u: todas
exec(fuente("nota_siguiente"), ns)
u_ = U["dir"]; rq2 = Req(u_)
ns["_puede_ver_nota"] = lambda u, n: True
a_, b_ = db.crear_nota("S1.docx", eq, mkdoc("s1.docx", ["1"]), usuario_id=ase), db.crear_nota("S2.docx", eq, mkdoc("s2.docx", ["2"]), usuario_id=ase)
db.actualizar_prioridad(b_, "URGENTE", "t")
sig = ns["nota_siguiente"](rq2, b_, 1).url
assert sig.startswith("/nota/") and sig != f"/nota/{b_}"
assert ns["nota_siguiente"](rq2, int(sig.split("/")[2]), -1).url == f"/nota/{b_}"       # ida y vuelta
# recorrido completo: llega al final y avisa
actual, vistos = b_, set()
while True:
    r_ = ns["nota_siguiente"](rq2, actual, 1)
    if r_.url == f"/nota/{actual}": break
    actual = int(r_.url.split("/")[2]); assert actual not in vistos; vistos.add(actual)
assert "No hay más" in rq2.session["flash"]
print("plantillas OK")
print("ALL OK")
