"""Confirmación de lo que se crea o se guarda, y rendimiento: Ollama por IPv4 y con caché, Administración que calcula
solo la pestaña abierta, IA en pausa mientras alguien usa el sistema, mantenimiento cada 10 minutos y registro de pantallas lentas."""
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

db.RUTA_DB = tmp / "t.db"
import main, auth, nucleo, unidades_csv
db.RUTA_DB = tmp / "t.db"; db.inicializar_db()
import types, asyncio, json, shutil, re
from docx import Document

h, s = auth.generar_hash_clave("Clave1234")
E1 = db.crear_equipo("Equipo Salud")
def U(us, nombre, rol, eq=None):
    return db.obtener_usuario(db.crear_usuario(us, h, s, nombre, rol, eq))
ana, coo = U("ana", "Ana Pérez", "ASESOR", E1), U("coo", "Cora Coord", "COORDINADOR", E1)
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
class URL:
    def __init__(self, p): self.path = p; self.query = ""; self.scheme = "http"
class Form(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def req(u, p="/", form=None, q=None):
    async def _f(): return Form(form or {})
    return types.SimpleNamespace(state=types.SimpleNamespace(usuario=db.obtener_usuario(u["id"])), url=URL(p), session={}, cookies={},
                                 headers={}, query_params=q or {}, form=_f, client=None)
run = lambda x: asyncio.run(x) if asyncio.iscoroutine(x) else x
def docx(nombre, parrafos):
    d = Document(); [d.add_paragraph(p) for p in parrafos]; r = tmp / nombre; d.save(r); return r
def nota(nombre, estados, u=None):
    r = docx(nombre, ["Al Sr. Director", "Cuerpo."])
    nid = db.crear_nota(nombre, E1, str(r), usuario_id=(u or ana)["id"])
    for e in estados: db.actualizar_estado(nid, e)
    with db._conexion() as con: con.execute("UPDATE notas SET ruta_resultado = ? WHERE id = ?", (str(r), nid))
    return nid
IDX = dict(mostrar_despachadas=False, orden="", direccion="asc", f_nombre="", f_tipo="", f_etapa="", f_fecha="", f_prioridad="", f_tema="",
           f_tarea="", pagina=1, vista="", ver="", agrupar="")

import automatico, time as _t
from starlette.responses import Response as SResp, RedirectResponse as SRedir
class Hdr(dict):
    def get(self, k, d=None): return dict.get(self, k.lower(), d)
def rq(metodo, path, session=None, headers=None):
    return types.SimpleNamespace(method=metodo, url=types.SimpleNamespace(path=path), session=session if session is not None else {}, headers=Hdr(headers or {}))

# ---------------------------------------------------------------- confirmación general
r = rq("POST", "/biblioteca/terminos/4/borrar"); main._confirmar_guardado(r, SRedir("/biblioteca", status_code=303))
assert r.session["flash"] == main.MENSAJE_GUARDADO
r = rq("POST", "/nota/3/tema", {"flash": "Tema guardado."}); main._confirmar_guardado(r, SRedir("/nota/3", status_code=303))
assert r.session["flash"] == "Tema guardado.", "si la pantalla dejó su mensaje, se respeta"
r = rq("POST", "/admin/usuarios", {"flash_admin": "Usuario creado"}); main._confirmar_guardado(r, SRedir("/admin/usuarios", status_code=303))
assert "flash" not in r.session
r = rq("POST", "/bloque/2/despachar"); main._confirmar_guardado(r, SResp(status_code=200, headers={"HX-Refresh": "true"}))
assert r.session["flash"] == main.MENSAJE_GUARDADO, "también cuando la página se recarga sola"
for metodo, path, resp in [("GET", "/admin", SRedir("/x", status_code=303)), ("POST", "/login", SRedir("/", status_code=303)),
                           ("POST", "/nota/5/firmar", SResp(status_code=200, headers={"HX-Redirect": "/nota/6"})),
                           ("POST", "/notificaciones/leer-todas", SRedir("/", status_code=303)),
                           ("POST", "/nota/5/aprobar-coordinacion", SResp("<div>tarjeta</div>", status_code=200))]:
    r = rq(metodo, path); main._confirmar_guardado(r, resp); assert "flash" not in r.session, path
print("confirmación general OK")

# ---------------------------------------------------------------- mensajes concretos al crear
class F(dict):
    def getlist(self, k): v = self.get(k); return v if isinstance(v, list) else ([v] if v else [])
def rform(u, form, path="/admin", referer="http://x/admin/equipos/nuevo"):
    rr = req(u, path, form=form); rr.headers = {"referer": referer}; return rr
rr = rform(direc, {"nombre": "Mesa de Entrada"})
resp = run(main.admin_crear_equipo(rr))
assert rr.session["flash_admin"] == "Equipo «Mesa de Entrada» creado correctamente." and resp.headers["location"] == "/admin/equipos"
assert any(e["nombre"] == "Mesa de Entrada" for e in db.listar_equipos())
rr = rform(direc, {"usuario": "jperez", "clave": "Provisoria123", "nombre": "Juan Pérez", "rol": "MESA_ASESOR"}, referer="http://x/admin/usuarios/nuevo")
resp = run(main.admin_crear_usuario(rr))
assert rr.session["flash_admin"].startswith("Usuario «Juan Pérez» (jperez) creado correctamente como Mesa de Entrada — Asesor/a.")
assert resp.headers["location"] == "/admin/usuarios", "después de crear se vuelve al listado, donde se ve lo creado"
nuevo = db.obtener_usuario_por_nombre("jperez")
rr = rform(direc, {}, referer="http://x/admin/usuarios"); run(main.admin_desactivar_usuario(rr, nuevo["id"]))
assert rr.session["flash_admin"] == "Usuario «Juan Pérez» desactivado (se conserva su historial)."
html = main.admin_panel(req(direc, "/admin"), "equipos").body
assert "Mesa de Entrada" in html
rr = req(direc, "/biblioteca"); main.biblioteca_termino(rr, termino="pabellón", preferido="", nota="")
assert rr.session["flash"] == "Término «pabellón» agregado correctamente."
print("mensajes concretos OK")

# ---------------------------------------------------------------- usuarios: el equipo se guarda para cualquier rol; ordenar y agrupar
eq_mesa = next(e["id"] for e in db.listar_equipos() if e["nombre"] == "Mesa de Entrada")
mesa1 = db.obtener_usuario(db.crear_usuario("mmesa", h, s, "Marta Mesa", "MESA_JEFE", None))
rr = rform(direc, {"nombre": "Marta Mesa", "rol": "MESA_JEFE", "equipo_id": str(eq_mesa), "acceso_notas_enviado": "1", "acceso_notas": "1"},
           referer="http://x/admin/usuarios/listado")
run(main.admin_editar_usuario(rr, mesa1["id"]))
assert db.obtener_usuario(mesa1["id"])["equipo_id"] == eq_mesa, "antes se perdía en silencio para los roles que no son de equipo"
assert rr.session["flash_admin"] == "Datos de «Marta Mesa» guardados correctamente (equipo: sin equipo → Mesa de Entrada)."
rr = rform(direc, {"nombre": "Ana Pérez", "rol": "ASESOR", "equipo_id": str(eq_mesa), "acceso_notas_enviado": "1", "acceso_notas": "1"}); run(main.admin_editar_usuario(rr, ana["id"]))
assert db.obtener_usuario(ana["id"])["equipo_id"] == eq_mesa and "equipo: Equipo Salud → Mesa de Entrada" in rr.session["flash_admin"]
rr = rform(direc, {"usuario": "dir2", "clave": "Provisoria123", "nombre": "Dora Dirección", "rol": "DIRECTOR", "equipo_id": str(eq_mesa)}); run(main.admin_crear_usuario(rr))
assert db.obtener_usuario_por_nombre("dir2")["equipo_id"] == eq_mesa
LIST = dict(orden="", dir="asc", f_rol="", f_equipo="", f_estado="", agrupar="")
html = main.admin_panel(req(direc, "/admin"), "usuarios", "listado", **dict(LIST, agrupar="equipo")).body
assert html.count('class="fila-grupo"') >= 2 and "Mesa de Entrada" in html and "Sin equipo" in html
assert html.index("Mesa de Entrada <span") < html.index("Sin equipo <span"), "«Sin equipo» al final"
html = main.admin_panel(req(direc, "/admin"), "usuarios", "listado", **dict(LIST, agrupar="rol")).body
assert "Mesa de Entrada — Jefe/a <span" in html
html = main.admin_panel(req(direc, "/admin"), "usuarios", "listado", **dict(LIST, f_equipo=str(eq_mesa))).body
assert "Marta Mesa" in html and "Cora Coord" not in html and "3 de" in html
html = main.admin_panel(req(direc, "/admin"), "usuarios", "listado", **dict(LIST, f_rol="COORDINADOR")).body
assert "Cora Coord" in html and "Marta Mesa" not in html
html = main.admin_panel(req(direc, "/admin"), "usuarios", "listado", **dict(LIST, orden="nombre", dir="desc")).body
nombres = [n for n in ("Marta Mesa", "Dora Dirección", "Cora Coord", "Ana Pérez") if n in html]
assert sorted(nombres, key=lambda n: html.index("<td>" + n + "</td>")) == sorted(nombres, reverse=True), "orden por nombre, descendente"
assert 'orden=nombre&dir=asc' in html and "▼" in html
html = main.admin_panel(req(direc, "/admin"), "usuarios", "listado", **dict(LIST, f_estado="desactivados")).body
assert "Juan Pérez" in html and "Marta Mesa" not in html
print("usuarios OK")

# ---------------------------------------------------------------- rendimiento
assert nucleo._sin_localhost("http://localhost:11434/api/generate") == "http://127.0.0.1:11434/api/generate"
assert nucleo._sin_localhost("http://LOCALHOST/api") == "http://127.0.0.1/api" and nucleo._sin_localhost("http://servidor-ia:11434/x") == "http://servidor-ia:11434/x"
assert 'OLLAMA_URL = _sin_localhost(config.get("ollama", "url"' in open(APP + "/nucleo.py", encoding="utf-8").read(), "la URL de config.ini pasa por _sin_localhost"
llamadas = []
class RespT:
    status_code = 200
    def json(self): return {"models": [{"name": "qwen2.5:7b"}]}
_get = nucleo.requests.get
nucleo.requests.get = lambda url, **k: (llamadas.append(url), RespT())[1]
nucleo._ULTIMA_CONSULTA_MODELOS[0] = 0.0
nucleo.estado_modelos_ia(); nucleo.estado_modelos_ia(); nucleo.estado_modelos_ia()
assert len(llamadas) == 1 and llamadas[0].startswith("http://127.0.0.1"), llamadas
nucleo.refrescar_modelos_ollama(); assert len(llamadas) == 2, "el chequeo que hace falta (antes de un trabajo) sí consulta"
nucleo.requests.get = _get
# Administración: la pestaña Usuarios no consulta a la IA, ni la cola, ni los respaldos
def prohibido(*a, **k): raise AssertionError("no debía calcularse en esta pestaña")
_e, _t2, _r = nucleo.estado_modelos_ia, automatico.estado_trabajador, main.respaldo.estado
nucleo.estado_modelos_ia = automatico.estado_trabajador = main.respaldo.estado = prohibido
for p in ("usuarios", "equipos"):
    assert "<h2>" in main.admin_panel(req(direc, "/admin"), p).body or True
nucleo.estado_modelos_ia, automatico.estado_trabajador, main.respaldo.estado = _e, _t2, _r
assert main.admin_panel(req(direc, "/admin"), "ia").body
# IA en pausa mientras alguien usa el sistema
t = automatico.Trabajador()
automatico._ACTIVIDAD["ultima"] = 0.0
assert t._hay_lugar() and not automatico.en_uso()
automatico.registrar_actividad()
assert automatico.en_uso() and not t._hay_lugar(), "con alguien usando el sistema no toma trabajos nuevos"
t.forzar = True; assert t._hay_lugar(), "«Ejecutar ahora» no espera"; t.forzar = False
automatico._ACTIVIDAD["ultima"] = _t.monotonic() - 91
assert not automatico.en_uso() and t._hay_lugar()
automatico.CONFIG["pausa_por_uso_segundos"] = 0; automatico.registrar_actividad(); assert not automatico.en_uso(); automatico.CONFIG["pausa_por_uso_segundos"] = 90
# mantenimiento cada 10 minutos
veces = []
t._mantenimiento = lambda: veces.append(1)
automatico.respaldo.toca_respaldo_automatico = lambda: False; automatico.respaldo.toca_verificacion = lambda: False
automatico.CONFIG["activa"] = False
for _ in range(5): t.ciclo()
assert len(veces) == 1, veces
t._ultimo_mantenimiento -= automatico.MANTENIMIENTO_CADA_SEGUNDOS; t.ciclo(); assert len(veces) == 2
# pantallas lentas
main.PEDIDOS.update(total=0, lentos=0, max_ms=0, ultimos_lentos=[])
for ms in (120, 2300):
    resp = SResp("x"); main._medir_pedido(rq("GET", "/nota/123/revisar"), resp, ms)
assert resp.headers["server-timing"] == "app;dur=2300"
assert main.PEDIDOS["total"] == 2 and main.PEDIDOS["lentos"] == 1 and main.PEDIDOS["ultimos_lentos"][0]["pantalla"] == "GET /nota/…/revisar"
html = main.admin_panel(req(direc, "/admin"), "sistema", "base").body
assert "Tiempos de respuesta de las pantallas" in html and "GET /nota/…/revisar" in html and "2.3 s" in html
print("rendimiento OK")
print("ALL OK")

# ---------------------------------------------------------------- usuarios: editar en fila completa, selección múltiple y permisos por equipo
gu = lambda u: db.obtener_usuario(u["id"])
bea = U("bea", "Bea Asesora", "ASESOR", E1); chofi = U("chofi", "Chofi Chofer", "CHOFER")
html = main.admin_panel(req(direc, "/admin"), "usuarios", "listado", **LIST).body
assert f'id="editar-u-{ana["id"]}" hidden' in html and f'data-editar-usuario="editar-u-{ana["id"]}"' in html, "editar abre una fila completa"
assert 'data-marcar-todos' in html and f'value="{ana["id"]}" form="lote-usuarios"' in html and 'action="/admin/usuarios/lote"' in html
# lote: Autos PPN deshabilitado y circuito Delegaciones para ana, bea y el chofer (al chofer no se le quita Autos)
rr = rform(direc, {"usuario_id": [str(ana["id"]), str(bea["id"]), str(chofi["id"])], "notas": "", "autos": "0", "circuito": "DELEGACIONES",
                   "delegacion_id": "", "coordina": "", "personal": "1", "equipo_id": "", "rol": "", "estado": ""}, path="/admin/usuarios/lote",
           referer="http://x/admin/usuarios/listado")
run(main.admin_usuarios_lote(rr))
assert rr.session["flash_admin"].startswith("Cambios aplicados a 3 usuario(s).") and "choferes solo Autos PPN" in rr.session["flash_admin"], rr.session["flash_admin"]
assert not gu(ana)["acceso_traslados"] and not gu(bea)["acceso_traslados"] and gu(chofi)["acceso_traslados"]
assert gu(ana)["circuito"] == gu(bea)["circuito"] == "DELEGACIONES" and gu(ana)["personal"] and gu(ana)["acceso_notas"], "lo que queda «sin cambios» no se toca"
# lote sin elegir cambios / sin marcar
rr = rform(direc, {"usuario_id": [str(ana["id"])]}, path="/admin/usuarios/lote"); run(main.admin_usuarios_lote(rr))
assert rr.session["flash_admin"] == "⚠ No se eligió ningún cambio."
rr = rform(direc, {"autos": "1"}, path="/admin/usuarios/lote"); run(main.admin_usuarios_lote(rr)); assert rr.session["flash_admin"].startswith("⚠ Marcá")
# lote: no se desactiva uno mismo; sí los demás; y se reactivan
rr = rform(direc, {"usuario_id": [str(direc["id"]), str(bea["id"])], "estado": "desactivar"}, path="/admin/usuarios/lote"); run(main.admin_usuarios_lote(rr))
assert gu(direc)["activo"] and not gu(bea)["activo"] and "Tu propia cuenta no se desactiva." in rr.session["flash_admin"]
rr = rform(direc, {"usuario_id": [str(bea["id"])], "estado": "activar"}, path="/admin/usuarios/lote"); run(main.admin_usuarios_lote(rr)); assert gu(bea)["activo"]
# lote: pasar a Asesor sin equipo no se permite
rr = rform(direc, {"usuario_id": [str(chofi["id"])], "rol": "ASESOR"}, path="/admin/usuarios/lote"); run(main.admin_usuarios_lote(rr))
assert gu(chofi)["rol"] == "CHOFER" and "necesita equipo" in rr.session["flash_admin"]
try: run(main.admin_usuarios_lote(rform(ana, {"usuario_id": [str(bea["id"])], "autos": "1"}, path="/admin/usuarios/lote"))); raise AssertionError
except main.HTTPException as e: assert e.status_code == 403
# equipos: permisos generales aplicados a cada integrante activo
html = main.admin_panel(req(direc, "/admin"), "equipos", "listado", **LIST).body
assert f'action="/admin/equipos/{E1}/permisos"' in html and "Editar permisos del equipo" in html and "Autos PPN 1/" in html
rr = rform(direc, {"notas": "", "autos": "1", "circuito": "AMBA", "delegacion_id": ""}, path=f"/admin/equipos/{E1}/permisos", referer="http://x/admin/equipos/listado")
r = run(main.admin_equipo_permisos(rr, E1))
miembros = [u for u in db.listar_usuarios() if u["activo"] and u.get("equipo_id") == E1]
assert rr.session["flash_admin"].startswith(f"Permisos aplicados a los {len(miembros)} integrante(s) de «Equipo Salud».") and r.headers["location"] == f"/admin/equipos/listado#equipo-{E1}"
assert all(u["acceso_traslados"] and u["circuito"] == "AMBA" and u["delegacion_id"] is None for u in miembros)
rr = rform(direc, {"rol": "DIRECTOR", "estado": "desactivar"}, path=f"/admin/equipos/{E1}/permisos"); run(main.admin_equipo_permisos(rr, E1))
assert all(u["rol"] != "DIRECTOR" and u["activo"] for u in db.listar_usuarios() if u.get("equipo_id") == E1 and u["id"] != direc["id"]), "el panel de equipos solo cambia permisos"
print("usuarios en lote y permisos por equipo OK")

# ---------------------------------------------------------------- importar usuarios desde la pantalla
import io as _io, openpyxl
main.CARPETA_IMPORTACIONES = tmp / "importaciones"
class Subida:
    def __init__(self, nombre, datos): self.filename, self._d = nombre, datos
    async def read(self): return self._d
lib = openpyxl.Workbook(); h_ = lib.active
h_.append(["Email", "Nombre", "Apellido", "Área", "Rol"])  # títulos en otro orden
h_.append(["nuevo.uno@ppn.gov.ar", "Nuevo", "Uno", "Equipo Importado", "Asesor"])
h_.append(["ana", "Ana", "Repetida", "Equipo Salud", "Asesor"])          # email inválido
h_.append(["mesa.dos@ppn.gov.ar", "Mesa", "Dos", "Mesa de Entradas", "Coordinador"])
h_.append(["sin.area@ppn.gov.ar", "Sin", "Area", "", "Asesor"])          # asesor sin área
buf = _io.BytesIO(); lib.save(buf)
html = main.admin_panel(req(direc, "/admin"), "usuarios", "importar", **LIST).body
assert 'action="/admin/usuarios/importar/revisar"' in html and "Traer el listado para revisar" in html and "Descargar planilla modelo" in html
rr = rform(direc, {"planilla": Subida("usuarios.xlsx", buf.getvalue())}, path="/admin/usuarios/importar/revisar")
r = run(main.admin_importar_revisar(rr)); loc = r.headers["location"]; token = loc.split("revision=")[1]
assert loc.startswith("/admin/usuarios/importar?revision=") and db.obtener_usuario_por_nombre("nuevo.uno@ppn.gov.ar") is None, "revisar no guarda nada"
html = main.admin_panel(req(direc, "/admin", q={"revision": token}), "usuarios", "importar", **LIST).body
assert "todavía no se guardó nada" in html and "nuevo.uno@ppn.gov.ar" in html and "Mesa de Entrada" in html and "Equipo Importado" in html
assert "email inválido" in html and "necesita Área" in html and "Importar 2 usuarios" in html
rr = rform(direc, {"token": token}, path="/admin/usuarios/importar/confirmar"); r = run(main.admin_importar_confirmar(rr))
assert r.headers["location"] == "/admin/usuarios/listado" and rr.session["flash_admin"].startswith("Importación lista: 2 usuario(s) y 1 equipo(s) nuevos"), rr.session["flash_admin"]
nu = db.obtener_usuario_por_nombre("nuevo.uno@ppn.gov.ar"); assert nu and nu["rol"] == "ASESOR" and nu["debe_cambiar_clave"]
assert db.obtener_usuario_por_nombre("mesa.dos@ppn.gov.ar")["rol"] == "MESA_COORDINADOR"
rr = rform(direc, {"token": token}, path="/admin/usuarios/importar/confirmar"); run(main.admin_importar_confirmar(rr))
assert "ya no está disponible" in rr.session["flash_admin"], "una revisión se usa una sola vez"
rr = rform(direc, {"planilla": Subida("viejo.xls", b"x")}, path="/admin/usuarios/importar/revisar"); run(main.admin_importar_revisar(rr))
assert "tiene que ser de Excel (.xlsx)" in rr.session["flash_admin"]
rr = rform(direc, {"token": "../../etc"}, path="/admin/usuarios/importar/confirmar"); run(main.admin_importar_confirmar(rr))
assert "ya no está disponible" in rr.session["flash_admin"]
m = main.admin_importar_modelo(req(direc)); assert "modelo_importar_usuarios.xlsx" in m.headers["content-disposition"]
try: run(main.admin_importar_revisar(rform(ana, {"planilla": Subida("u.xlsx", buf.getvalue())}))); raise AssertionError
except main.HTTPException as e: assert e.status_code == 403
print("importar usuarios desde la pantalla OK")
