"""Ola 5: consultas en lenguaje común, datos para la Dirección, presentismo mensual y cruce, reportes programados, PIN de firma,
salud del sistema, HTTPS interno, pase a PostgreSQL y manual."""
import sys, types
import starlette.responses as sr, starlette.templating as st, starlette.staticfiles as ss, starlette.exceptions as se
fa = types.ModuleType("fastapi")
class FastAPI:
    def __init__(self, *a, **k): self.rutas = {}; self.state = types.SimpleNamespace()
    def _reg(self, m):
        def deco(path, **kw):
            def w(fn): self.rutas[(m, path)] = fn; return fn
            return w
        return deco
    def get(self, p, **k): return self._reg("GET")(p)
    def post(self, p, **k): return self._reg("POST")(p)
    def middleware(self, *a, **k): return lambda f: f
    def add_middleware(self, *a, **k): pass
    def exception_handler(self, *a, **k): return lambda f: f
    def mount(self, *a, **k): pass
    def on_event(self, *a, **k): return lambda f: f
def _param(default=None, *a, **k): return default
class UploadFile: pass
class BackgroundTasks:
    def add_task(self, *a, **k): pass
class HTTPException(Exception):
    def __init__(self, status_code=500, detail=""): self.status_code, self.detail = status_code, detail
fa.FastAPI, fa.File, fa.Form, fa.UploadFile, fa.BackgroundTasks, fa.Request, fa.HTTPException = FastAPI, _param, _param, UploadFile, BackgroundTasks, object, HTTPException
fe = types.ModuleType("fastapi.exceptions"); fe.RequestValidationError = type("RVE", (Exception,), {})
fr = types.ModuleType("fastapi.responses")
for n in ("FileResponse", "HTMLResponse", "JSONResponse", "RedirectResponse", "Response", "StreamingResponse"): setattr(fr, n, getattr(sr, n))
fs = types.ModuleType("fastapi.staticfiles"); fs.StaticFiles = lambda *a, **k: None
ft = types.ModuleType("fastapi.templating")
class Jinja2Templates:
    def __init__(self, directory): 
        import jinja2
        self.env = jinja2.Environment(loader=jinja2.FileSystemLoader(directory), autoescape=True)
    def TemplateResponse(self, nombre, ctx, *a, **k):
        class R: pass
        r = R(); r.body = self.env.get_template(nombre).render(**ctx); r.status_code = k.get("status_code", 200); return r
ft.Jinja2Templates = Jinja2Templates
sys.modules.update({"fastapi": fa, "fastapi.exceptions": fe, "fastapi.responses": fr, "fastapi.staticfiles": fs, "fastapi.templating": ft})
import tempfile
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, APP)
import database as db
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"

import asyncio, io, json, re
from datetime import date, datetime, timedelta, timezone
import main, auth, automatico
import traslados as tr
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import consultas, indicadores as ind, presentismo, programados, pin, manual, migrar_postgresql
import salud as salud_mod
from docx import Document
programados.CARPETA = tmp / "programados"; salud_mod.CONFIG["carpeta_datos"] = tmp

E1, E2 = db.crear_equipo("Equipo Salud"), db.crear_equipo("Equipo CPF I")
h, s = auth.generar_hash_clave("Clave1234")
def U(us, nombre, rol, eq=None, **kw):
    u = db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
    with db._conexion() as con:
        for k, v in kw.items(): con.execute(f"UPDATE usuarios SET {k} = ? WHERE id = ?", (v, u["id"]))
    return db.obtener_usuario(u["id"])
ana = U("ana", "Ana Pérez", "ASESOR", E1, correo="ana@ppn.gov.ar")
caro = U("caro", "Carolina Ruiz", "ASESOR", E2)
pers = U("pers", "Paula Personal", "ASESOR", E1, personal=1)
mesa = U("mesa", "Marta Mesa", "MESA_ASESOR")
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
hoy = date.today()
CPF1 = next(u for u in db.listar_unidades() if "-CPF I-" in u["nombre"])
CPF2 = next(u for u in db.listar_unidades() if "-CPF II-" in u["nombre"])

def nota(nombre, texto, u, dias, estado="DESPACHADA"):
    d = Document(); [d.add_paragraph(p) for p in texto]; r = tmp / nombre; d.save(r)
    nid = db.crear_nota(nombre, u["equipo_id"], str(r), usuario_id=u["id"])
    for e in {"FIRMADA": ["PROCESANDO", "PROCESADA", "FIRMADA"], "DESPACHADA": ["PROCESANDO", "PROCESADA", "FIRMADA", "DESPACHADA"],
              "PROCESADA": ["PROCESANDO", "PROCESADA"]}.get(estado, []):
        db.actualizar_estado(nid, e)
    f = datetime.now(timezone.utc) - timedelta(days=dias)
    with db._conexion() as con:
        con.execute("UPDATE historial_estados SET fecha=? WHERE nota_id=?", (f.isoformat(timespec="seconds"), nid))
        con.execute("UPDATE notas SET creado_en=?, fecha_nota=? WHERE id=?", (f.isoformat(timespec="seconds"), f.date().isoformat(), nid))
    automatico.clasificar_rapido(nid)
    return nid
T = lambda sig, cosa: [f"Al Sr. Director del -{sig}-", f"Las personas alojadas denuncian {cosa}.", "Saludo atentamente."]
n1 = nota("Nota colchones CPF I.docx", T("CPF I", "colchones en mal estado"), ana, 20)
n2 = nota("Nota colchón CPF I.docx", T("CPF I", "falta de colchón y calefacción"), caro, 50)
n3 = nota("Nota salud CPF II.docx", T("CPF II", "falta de atención médica"), ana, 40)
n4 = nota("Nota huelga CPF II.docx", T("CPF II", "una huelga de hambre"), caro, 70, "FIRMADA")
n5 = nota("Informe CPF I.docx", ["INFORME", "Colchones en el -CPF I-."], ana, 20)   # informe: no cuenta como nota
n6 = nota("Nota vieja CPF I.docx", T("CPF I", "colchones rotos"), ana, 400)          # fuera del período

# ---------------------------------------------------------------- 14) consultas
f = consultas.interpretar_reglas(f"¿Cuántas notas por colchones al CPF I en {hoy.year - 1}?", hoy)
assert f["unidades"] == [CPF1["id"]] and f["desde"] == f"{hoy.year - 1}-01-01" and f["hasta"] == f"{hoy.year - 1}-12-31", f
assert [consultas.raiz(p) for p in f["palabras"]] == ["colchon"], f["palabras"]
f = consultas.interpretar_reglas("notas sobre colchones al CPF I en los últimos 6 meses", hoy)
r = consultas.ejecutar(f)
assert {n["id"] for n in r["lista"]} == {n1, n2}, ("colchón y colchones, solo notas, solo el período", r["lista"])
assert r["total"] == 2
f = consultas.interpretar_reglas("huelga de hambre por unidad este año", hoy)
assert f["pedidos"] == ["Huelga de hambre"] and f["agrupar"] == "unidad", f
f = consultas.interpretar_reglas("visitas al CPF II por mes el mes pasado", hoy)
assert f["fuente"] == "viajes" and f["agrupar"] == "mes" and f["unidades"] == [CPF2["id"]], f
mp = date(hoy.year, hoy.month, 1) - timedelta(days=1)
assert f["desde"] == date(mp.year, mp.month, 1).isoformat() and f["hasta"] == mp.isoformat()
f = consultas.interpretar_reglas("notas firmadas en marzo de 2026", hoy)
assert f["estados"] == ["FIRMADAS"] and (f["desde"], f["hasta"]) == ("2026-03-01", "2026-03-31"), f
f = consultas.interpretar_reglas("notas del primer trimestre de 2026", hoy)
assert (f["desde"], f["hasta"]) == ("2026-01-01", "2026-03-31")
# la persona corrige la interpretación: se usa tal cual
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
f = consultas.filtros_desde_form(Form(fuente="notas", palabras="", unidades=[str(CPF2["id"])], agrupar="pedido", desde="", hasta=""))
r = consultas.ejecutar(f)
assert r["total"] == 2 and dict(r["desglose"]).get("Huelga de hambre") == 1, r["desglose"]
# segunda lectura con IA (simulada): solo completa lo que las reglas no encontraron, y se valida contra el catálogo
import nucleo
_disp, _cons = nucleo.ia_disponible, nucleo.consultar_ollama
nucleo.ia_disponible = lambda p: True
nucleo.consultar_ollama = lambda *a, **k: json.dumps({"fuente": "notas", "palabras": ["humedad"], "unidad": "CPF II", "pedidos": ["Inventado", "Atención médica"],
                                                      "desde": "2026-01-01", "hasta": "basura", "agrupar": "mes"})
f = consultas.interpretar("¿qué pasó con la humedad?", usar_ia=True, hoy=hoy)
assert f["origen"] == "reglas + IA" and f["unidades"] == [CPF2["id"]] and f["pedidos"] == ["Atención médica"] and f["desde"] == "2026-01-01" and f["hasta"] == "", f
f = consultas.interpretar("colchones en el CPF I en 2026", usar_ia=True, hoy=hoy)
assert f["unidades"] == [CPF1["id"]] and f["desde"] == "2026-01-01", "las reglas mandan sobre la IA"
nucleo.consultar_ollama = lambda *a, **k: "esto no es json"
assert consultas.interpretar("colchones", usar_ia=True, hoy=hoy)["origen"] == "reglas"
nucleo.ia_disponible, nucleo.consultar_ollama = _disp, _cons
# visibilidad: un asesor solo cuenta lo que puede ver
r = consultas.ejecutar(consultas.interpretar_reglas("notas del CPF II", hoy), main._puede_ver_nota, ana)
assert r["total"] >= 1 and all(db.obtener_nota(n["id"])["equipo_id"] == E1 for n in r["lista"]), "Ana solo ve su equipo"
assert "colchones" in consultas.describir(consultas.interpretar_reglas("colchones en el CPF I", hoy))

# ---------------------------------------------------------------- viajes
def P(u): return {"usuario_id": u["id"], "nombre": u["nombre"], "equipo_nombre": u["equipo_nombre"], "auto_propio": False}
lunes_pasado = tr.lunes_de(hoy) - timedelta(days=7)
v1 = tr.crear_viaje(dict(fecha=(lunes_pasado + timedelta(days=1)).isoformat(), hora_salida="09:00", hora_regreso="13:00", unidad_id=CPF2["id"],
                         modalidad="CHOFER", motivo="Monitoreo"), [P(ana), P(caro)], ana, hoy=lunes_pasado - timedelta(days=10))
tr.sincronizar_choferes()
with db._conexion() as con:
    con.execute("UPDATE viajes SET estado='CONFIRMADO' WHERE id=?", (v1,))

# ---------------------------------------------------------------- 81/82/86/88/89
d0, d1 = ind.rango((hoy - timedelta(days=200)).isoformat(), hoy.isoformat())
t = ind.tablero(d0, d1)
assert t["kpis"]["notas"] == 4 and t["kpis"]["despachadas"] == 3 and t["kpis"]["firmadas"] == 4, t["kpis"]
assert t["kpis"]["viajes_unidad"] == 2, "dos equipos al CPF II el mismo día = 2 viajes a la unidad"
assert ("CPF I", 2) in t["unidades_notas"] and any(u.endswith("-CPF I-") and uid == CPF1["id"] for u, _, uid in t["sin_visita"]), (t["unidades_notas"], t["sin_visita"])
pu = ind.por_unidad(CPF2["id"], d0, d1)
assert pu["n_notas"] == 2 and pu["n_visitas"] == 1 and pu["n_viajes_unidad"] == 2 and pu["ultima_visita"]
assert ind.excel_unidad(pu)[:2] == b"PK"
m = ind.mapa_temas(d0, d1)
assert m["mes_x_unidad"]["filas"] and m["unidad_x_pedido"]["filas"][0]["corto"] in ("CPF I", "CPF II")
assert ind.nombre_corto("Complejo Penitenciario Federal I de Ezeiza -CPF I-") == "CPF I"
assert ind.nombre_corto("Complejo Penitenciario Federal VII -ex Unidad 31-").startswith("Complejo")
a = ind.datos_abiertos(d0, d1)
celdas = [c for tb in a["tablas"][:3] for fila in tb["filas"] for c in fila[1:]]
assert "menos de 3" in celdas and not any(isinstance(c, int) and 0 < c < 3 for c in celdas), "las celdas chicas se ocultan"
txt = ind.abiertos_csv(a).decode("utf-8-sig")
assert "Ana" not in txt and "Pérez" not in txt and "Nota colchones" not in txt, "sin datos personales"
with db._conexion() as con:
    con.execute("INSERT INTO intentos_login (usuario, ip, exito, fecha) VALUES ('ana', 'x', 1, ?)", (datetime.now(timezone.utc).isoformat(),))
uso = ind.uso_sistema((hoy - timedelta(days=30)).isoformat(), hoy.isoformat())
salud_area = next(x for x in uso["filas"] if x["area"] == "Equipo Salud")
assert salud_area["entraron"] == 1 and "Paula Personal" in salud_area["nunca"], salud_area

# ---------------------------------------------------------------- 80) presentismo
pm = presentismo.mes(lunes_pasado.year, lunes_pasado.month) if (lunes_pasado + timedelta(days=1)).month == lunes_pasado.month else presentismo.mes((lunes_pasado + timedelta(days=1)).year, (lunes_pasado + timedelta(days=1)).month)
assert any(p["persona"] == "Ana Pérez" and p["n_dias"] == 1 for p in pm["personas"]) and presentismo.excel_mes(pm)[:2] == b"PK"
pp = presentismo.persona(ana["id"], hoy - timedelta(days=30), hoy)
assert pp["dias"] == [(lunes_pasado + timedelta(days=1)).isoformat()]
dia_v = lunes_pasado + timedelta(days=1)
import openpyxl
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Novedades"]); ws.append(["Apellido y nombre", "Correo", "Fecha", "Novedad"])
ws.append(["PÉREZ, Ana", "", dia_v.strftime("%d/%m/%Y"), "Licencia anual"])           # viajó con licencia
ws.append(["Ruiz, Carolina", "", dia_v, "Presente"])
ws.append(["Ruiz, Carolina", "", dia_v + timedelta(days=1), "Comisión de servicio"])   # comisión sin viaje
ws.append(["Fulano De Tal", "", dia_v, "Ausente"])                                    # sin usuario
b = io.BytesIO(); wb.save(b)
res, av = presentismo.importar("nov.xlsx", b.getvalue(), pers)
assert res["filas"] == 4 and res["sin_vincular"] == 1, (res, av)
c = presentismo.cruce(dia_v - timedelta(days=3), dia_v + timedelta(days=3))
assert [x["persona"] for x in c["viajo_con_ausencia"]] == ["Ana Pérez"] and [x["persona"] for x in c["comision_sin_viaje"]] == ["Carolina Ruiz"]
assert c["sin_vincular"] == ["Fulano De Tal"] and c["coinciden"] == 1, c
# formato mensual (una columna por día)
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Agente"] + list(range(1, 32))); ws.append(["Ruiz, Carolina", "P", "A", "L", "C"])
b = io.BytesIO(); wb.save(b)
regs, av = presentismo.leer_planilla("m.xlsx", b.getvalue())
assert not regs and "mes" in av[0], "sin mes elegido avisa"
regs, av = presentismo.leer_planilla("m.xlsx", b.getvalue(), 2026, 3)
assert [(r["fecha"].day, r["clase"]) for r in regs] == [(1, "PRESENTE"), (2, "AUSENCIA"), (3, "LICENCIA"), (4, "COMISION")], regs
csv = "Persona;Fecha;Situación\nRuiz, Carolina;05/03/2026;Vacaciones\n".encode()
regs, _ = presentismo.leer_planilla("x.csv", csv)
assert regs[0]["clase"] == "LICENCIA" and regs[0]["fecha"] == date(2026, 3, 5)

# ---------------------------------------------------------------- 83) reportes programados
lunes_8 = datetime.combine(tr.lunes_de(hoy), datetime.min.time()).replace(hour=8, minute=5)
assert programados.periodo_que_toca("presentismo_semanal", lunes_8)[1] == tr.lunes_de(hoy) - timedelta(days=7)
assert programados.periodo_que_toca("presentismo_semanal", lunes_8.replace(hour=7))[1] == tr.lunes_de(hoy) - timedelta(days=14)
assert programados.periodo_que_toca("uso_mensual", datetime(2026, 11, 1, 9))[0] == "2026-10"
assert programados.periodo_que_toca("uso_mensual", datetime(2026, 11, 1, 7))[0] == "2026-09"
hechos = programados.tick()
assert len(hechos) == 3 and programados.tick() == [], "los activos por defecto, una sola vez"
lista = programados.listar()
assert all(g["existe"] for g in lista)
pres = next(g for g in lista if g["tipo"] == "presentismo_semanal")
assert programados.puede_descargar(pers, pres) and not programados.puede_descargar(ana, pres)
assert not programados.puede_descargar(pers, next(g for g in lista if g["tipo"] == "tablero_semanal"))
assert any("Listo para descargar" in n["texto"] for n in db.listar_notificaciones(pers["id"])) if hasattr(db, "listar_notificaciones") else True

# ---------------------------------------------------------------- 93) PIN
assert pin.validar_nuevo("12a4") and pin.validar_nuevo("1111") and pin.validar_nuevo("1234") and pin.validar_nuevo("4826", "4827")
assert pin.validar_nuevo("4826", "4826") is None
assert pin.ruta_protegida("POST", "/nota/5/firmar") and pin.ruta_protegida("POST", "/bloque/2/despachar") and pin.ruta_protegida("POST", "/nota/5/firmar-y-seguir")
assert not pin.ruta_protegida("GET", "/nota/5/firmar") and not pin.ruta_protegida("POST", "/nota/5/vista")
assert pin.requiere(direc) and pin.requiere(mesa) and not pin.requiere(ana)
assert pin.crear(mesa, "4826", "4826") is None
mesa = db.obtener_usuario(mesa["id"])
assert pin.verificar(mesa, "4826")[0] and not pin.verificar(mesa, "0000")[0]
for _ in range(5):
    ok, msg = pin.verificar(mesa, "1357")
assert not ok and pin.bloqueado_hasta(mesa) and ("bloque" in msg.lower() or "intentos" in msg.lower())
assert not pin.verificar(mesa, "4826")[0], "bloqueado aunque ahora acierte"
pin.borrar(mesa["id"], por=direc)
mesa = db.obtener_usuario(mesa["id"])
assert not pin.tiene_pin(mesa) and not pin.bloqueado_hasta(mesa)
# el middleware lo exige antes de firmar o despachar
from starlette.requests import Request as SReq
from starlette.responses import Response as SResp
def pedido(metodo, ruta, sesion):
    scope = {"type": "http", "method": metodo, "path": ruta, "headers": [(b"host", b"app")], "query_string": b"", "session": sesion,
             "client": ("127.0.0.1", 1), "server": ("app", 80), "scheme": "http", "root_path": ""}
    async def llamar(req): return SResp("ok", status_code=200)
    return asyncio.run(main.AuthMiddleware(app=None).dispatch(SReq(scope), llamar))
ses = {"usuario_id": direc["id"], "ultima_actividad": int(datetime.now().timestamp())}
db.registrar_intento_login("director", "x", True)
with db._conexion() as con:
    con.execute("UPDATE usuarios SET debe_cambiar_clave = 0 WHERE id = ?", (direc["id"],))
assert pedido("POST", f"/nota/{n1}/firmar", ses).status_code == 403
assert pedido("GET", f"/nota/{n1}", ses).status_code == 200
ses[pin.CLAVE_SESION] = direc["id"]
assert pedido("POST", f"/nota/{n1}/firmar", ses).status_code == 200, "con el PIN confirmado en la sesión, pasa"
ses2 = {"usuario_id": ana["id"], "ultima_actividad": int(datetime.now().timestamp())}
with db._conexion() as con:
    con.execute("UPDATE usuarios SET debe_cambiar_clave = 0")
assert pedido("POST", "/nota/1/enviar", ses2).status_code == 200

# ---------------------------------------------------------------- 99) salud
res = salud_mod.revisar()
assert {r["chequeo"] for r in res} >= {"disco", "base", "respaldo", "ia", "cola", "memoria", "correo", "https"}
antes = len(db.listar_notificaciones(direc["id"])) if hasattr(db, "listar_notificaciones") else None
salud_mod.tick(forzar=True); salud_mod.tick(forzar=True)
with db._conexion() as con:
    n_ev = con.execute("SELECT COUNT(*) AS n FROM salud_eventos").fetchone()["n"]
    avisos_salud = con.execute("SELECT COUNT(*) AS n FROM notificaciones WHERE texto LIKE 'Salud del sistema%'").fetchone()["n"]
salud_mod.tick(forzar=True)
with db._conexion() as con:
    assert con.execute("SELECT COUNT(*) AS n FROM notificaciones WHERE texto LIKE 'Salud del sistema%'").fetchone()["n"] == avisos_salud, "no repite avisos"
salud_mod.CONFIG["disco_min_gb"] = 10**9
r = salud_mod.chequear_disco(); assert r["estado"] == salud_mod.FALLA
salud_mod.CONFIG["disco_min_gb"] = 5.0

# ---------------------------------------------------------------- 92) HTTPS interno
try:
    import cryptography  # noqa
    import https_interno
    https_interno.CARPETA = tmp / "certs"
    for k in ("CA_CRT", "CA_KEY", "SRV_CRT", "SRV_KEY"):
        setattr(https_interno, k, https_interno.CARPETA / getattr(https_interno, k).name)
    assert https_interno.main(["--nombre", "servidor-dgpdh", "--ip", "10.1.2.3"]) == 0
    e = https_interno.estado()
    assert e["hay_servidor"] and e["dias"] > 800 and "10.1.2.3" in e["nombres"] and "servidor-dgpdh" in e["nombres"]
    import ssl
    ctx = ssl.create_default_context(cafile=str(https_interno.CA_CRT)); ctx.load_verify_locations(cafile=str(https_interno.CA_CRT))
except ImportError:
    print("(sin cryptography: no se prueba HTTPS)")

# ---------------------------------------------------------------- 98) PostgreSQL
sql = tmp / "pg.sql"
filas = migrar_postgresql.escribir_sql(db.RUTA_DB, sql)
texto = sql.read_text(encoding="utf-8")
assert texto.startswith("--") and texto.rstrip().endswith("COMMIT;") and "notas_fts" not in texto and "sqlite_sequence" not in texto
assert 'CREATE TABLE IF NOT EXISTS "notas"' in texto and '"id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY' in texto
assert "setval(pg_get_serial_sequence('\"notas\"', 'id')" in texto
assert filas == sum(n for _, n in migrar_postgresql.informe(db.RUTA_DB))
assert "'Ana Pérez'" in texto and "O''Brien" not in texto
assert migrar_postgresql._literal("O'Brien") == "'O''Brien'" and migrar_postgresql._literal(None) == "NULL" and migrar_postgresql._literal(b"\x01") == "'\\x01'::bytea"
assert db.MEDIDOR["conexiones"] > 0

# ---------------------------------------------------------------- 100) manual
assert manual.rol_de(direc) == "direccion" and manual.rol_de(mesa) == "mesa" and manual.rol_de(pers) == "personal" and manual.rol_de(ana) == "asesor"
assert all(r["secciones"] for r in manual.ROLES.values()) and all(v["roles"] for v in manual.VIDEOS)

# ---------------------------------------------------------------- pantallas
import types
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
def req(u, p="/", ses=None):
    async def _f(): return {}
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(p), session=ses or {}, cookies={}, headers={}, query_params={}, form=_f, client=None)
R = main.app.rutas
for ruta, kw in (("/datos", {}), ("/datos/unidad", {"unidad_id": str(CPF1["id"])}), ("/datos/temas", {}), ("/datos/uso", {}),
                 ("/datos/abiertos", {}), ("/datos/programados", {})):
    html = R[("GET", ruta)](req(direc, ruta), **kw).body
    assert "Datos" in html or "Monitoreo" in html or "Mapa" in html or "Reportes" in html, ruta
try:
    R[("GET", "/datos")](req(ana, "/datos")); raise SystemExit("un asesor no ve el tablero")
except main.HTTPException as e:
    assert e.status_code == 403
html = R[("GET", "/consultas")](req(ana, "/consultas"), q="notas sobre colchones", ia="0").body
assert "Entendí" in html and "colchones" in html
html = main.ver_nota(req(direc, f"/nota/{n1}"), n1).body
assert 'id="dialogo-pin"' in html and "Creá tu PIN" in html, "el Director sin PIN ve el diálogo de creación"
html = main.ver_nota(req(direc, f"/nota/{n1}", {pin.CLAVE_SESION: direc["id"]}), n1).body
assert 'id="dialogo-pin"' not in html, "confirmado en la sesión: no se vuelve a pedir"
html = R[("GET", "/manual")](req(mesa, "/manual")).body
assert "Mesa de Entradas" in html and "Despacho del día" in html
html = R[("GET", "/manual")](req(mesa, "/manual"), rol="todos").body
# los videos (static/videos) no viajan en todas las copias de trabajo: el control se hace solo donde están
if manual.CARPETA_VIDEOS.is_dir() and any(manual.CARPETA_VIDEOS.glob("*.mp4")):
    assert "Todos los videos" in html and html.count('class="video-miniatura"') == len(manual.VIDEOS) == 29, html.count('class="video-miniatura"')
    assert "<video" not in html and html.count('loading="lazy"') >= 29, "la ayuda abre con miniaturas livianas; el video se carga al tocarla"
    assert html.count('.jpg"') == 29
else:
    print("(sin static/videos en esta copia: se omite el control de miniaturas)")
for sub in (("sistema", "salud"), ("sistema", "base"), ("seguridad", "pin"), ("seguridad", "https")):
    assert "<h2>" in main.admin_panel(req(direc, "/admin"), *sub).body
print("OK ola 5")
