"""Rutas de Traslados/Unidades con un FastAPI de mentira: ejecuta cada handler con
formularios reales y renderiza las plantillas verdaderas."""
import sys, types, tempfile, asyncio, inspect, warnings
from datetime import date, timedelta
from pathlib import Path
warnings.simplefilter("ignore")
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)

# ---- stub de fastapi
class HTTPException(Exception):
    def __init__(self, status_code, detail=""): self.status_code, self.detail = status_code, detail
class Resp:
    def __init__(self, content=None, status_code=200, media_type=None, headers=None, url=None):
        self.content, self.status_code, self.headers, self.url = content, status_code, headers or {}, url
class RedirectResponse(Resp):
    def __init__(self, url, status_code=307): super().__init__(None, status_code, url=url)
fa = types.ModuleType("fastapi"); fa.HTTPException = HTTPException; fa.Request = object
fr = types.ModuleType("fastapi.responses"); fr.HTMLResponse = Resp; fr.JSONResponse = lambda c, status_code=200: Resp(c, status_code); fr.RedirectResponse = RedirectResponse; fr.Response = Resp
sys.modules["fastapi"], sys.modules["fastapi.responses"] = fa, fr

import jinja2
import database as db, auth, fechas
import traslados as tr
import rutas_traslados
tmp = Path(tempfile.mkdtemp()); db.RUTA_DB = tmp / "t.db"; db.inicializar_db()

class App:
    def __init__(self): self.rutas = {}
    def _reg(self, metodo):
        def deco(path, **kw):
            def w(fn): self.rutas[(metodo, path)] = fn; return fn
            return w
        return deco
    def __getattr__(self, n):
        if n in ("get", "post"): return self._reg(n.upper())
        raise AttributeError(n)
app = App()

class Tpl:
    def __init__(self):
        self.env = jinja2.Environment(loader=jinja2.FileSystemLoader(APP + "/templates"))
        self.env.globals.update(ROLES_DE_EQUIPO=auth.ROLES_DE_EQUIPO, ROLES_MESA=auth.ROLES_MESA, contar_notificaciones=db.contar_notificaciones,
                                static_v="t", tomar_flash=lambda r: None)
        self.env.filters["hora"] = fechas.a_hora_local
        self.env.globals.update(ETIQUETAS_ROL=auth.ETIQUETAS_ROL, es_admin=auth.es_admin)
    def TemplateResponse(self, nombre, ctx):
        class _S: pass
        r = _S(); r.state = _S(); r.state.usuario = ctx.get("usuario"); ctx = dict(ctx, request=r)
        return Resp(self.env.get_template(nombre).render(**ctx))
flashes = []
class Req:
    def __init__(self, usuario, form=None):
        self.usuario, self._form = usuario, form or {}
    async def form(self): return Form(self._form)
class Form(dict):
    def getlist(self, k):
        v = self.get(k, [])
        return v if isinstance(v, list) else [v]
class Archivo:
    filename = "u.csv"
    def __init__(self, b): self.b = b
    async def read(self): return self.b

H = {"u": None}
rutas_traslados.registrar(app, {"templates": Tpl(), "usuario_actual": lambda r: r.usuario, "flash": lambda r, t: flashes.append(t),
                                "tiene_notas": lambda u: bool(u.get("acceso_notas", 1)), "puede_gestionar_nota": lambda u, n: True})
import traslados as _tr

def llamar(metodo, path, usuario, form=None, **kw):
    fn = app.rutas[(metodo, path)]
    req = Req(usuario, form)
    r = fn(req, **kw)
    if inspect.iscoroutine(r): r = asyncio.run(r)
    return r

def prohibido(*a, **k):
    try: llamar(*a, **k)
    except HTTPException as e: return e.status_code
    raise AssertionError("debía dar 403")

eq = db.crear_equipo("Equipo A")
h, s = auth.generar_hash_clave("Clave1234")
ase = db.obtener_usuario(db.crear_usuario("ase", h, s, "Ana Asesora", "ASESOR", eq))
cid = db.crear_usuario("cch", h, s, "Coord Choferes", "ASESOR", eq); db.actualizar_usuario(cid, "Coord Choferes", "ASESOR", eq, coordina_traslados=True)
cch = db.obtener_usuario(cid)
direc = db.obtener_usuario(next(x for x in db.listar_usuarios() if x["rol"] == "DIRECTOR")["id"])
dele = next(x for x in db.listar_unidades() if x["zona"] == "Delegaciones PPN")
dl = db.obtener_usuario(db.crear_usuario("dl", h, s, "Dora Delegada", "ASESOR", eq))
db.actualizar_usuario(dl["id"], "Dora Delegada", "ASESOR", eq, circuito="DELEGACIONES", delegacion_id=dele["id"]); dl = db.obtener_usuario(dl["id"])
chof = db.obtener_usuario(db.crear_usuario("chof", h, s, "Pedro Chofer", "CHOFER", None, acceso_notas=False))
UN = db.listar_unidades()[0]["id"]
manana = (date.today() + timedelta(days=20)).isoformat()

# agenda para todos
for u in (cch, chof):
    r = llamar("GET", "/traslados", u)
    assert "Agenda semanal" in r.content
# quien solo carga viajes entra a «Mis viajes» (evaluación de interfaz); la agenda sigue a un clic
r = llamar("GET", "/traslados", ase)
assert "Mis viajes" in r.content and "Solicitar un viaje" in r.content and "Agenda semanal" not in r.content
assert "Agenda semanal" in llamar("GET", "/traslados", ase, ver="agenda").content
assert "Importar Excel" in llamar("GET", "/traslados", cch).content and "Importar Excel" not in llamar("GET", "/traslados", ase).content
# nuevo viaje
assert "Solicitar viaje" in llamar("GET", "/traslados/nuevo", ase).content
g = llamar("GET", "/traslados/nuevo", ase)
assert "Solicitar viaje" in g.content and "Ana Asesora" in g.content  # integrantes del equipo precargados
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": manana, "hora_salida": "09:00", "unidad_id": str(UN), "motivo": "m", "agentes": [str(ase["id"])], "otros": "Persona Externa\nOtra"})
assert r.status_code == 303 and r.url.startswith("/traslados/"), (r.url, flashes)
vid = int(r.url.rsplit("/", 1)[1])
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": manana, "unidad_id": "", "agentes": []})
assert "Indicá la unidad" in r.content or "al menos una" in r.content  # vuelve al formulario con el error
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": manana, "unidad_id": str(UN), "agentes": [str(ase["id"])], "accion": "revisar"})
assert "Ya hay viajes" in r.content
# buscador de personas (nombre, correo, equipo)
b = llamar("GET", "/traslados/personas", ase, q="asesora")
assert b.content and b.content[0]["nombre"] == "Ana Asesora" and llamar("GET", "/traslados/personas", ase, q="a").content == []
assert all(x["nombre"] != "Pedro Chofer" for x in llamar("GET", "/traslados/personas", ase, q="pedro").content)  # los choferes no viajan como agentes
# destino fuera del AMBA desde la pantalla: sin chofer
fuera = next(x for x in db.listar_unidades() if x["zona"] == "Interior y otras")
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": manana, "unidad_id": str(fuera["id"]), "agentes": [str(ase["id"])]})
assert "AMBA" in r.content
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": manana, "unidad_id": str(fuera["id"]), "agentes": [str(ase["id"])], f"auto_{ase['id']}": "1"})
assert r.status_code == 303
# delegaciones: pantalla propia, sin chofer
g = llamar("GET", "/traslados/nuevo", dl)
assert "Registrar actividad" in g.content
r = llamar("POST", "/traslados/nuevo", dl, {"fecha": date.today().isoformat(), "tipo_actividad": "COMISION", "agentes": [str(dl["id"])], "motivo": "x"})
assert r.status_code == 303
vdl = int(r.url.rsplit("/", 1)[1]); assert _tr.obtener_viaje(vdl)["circuito"] == "DELEGACIONES"
assert "Registrar una actividad" in llamar("GET", "/traslados", dl).content and "Registrar actividad" in llamar("GET", "/traslados", dl, ver="agenda").content
# la agenda AMBA no muestra Delegaciones y viceversa
a_amba = llamar("GET", "/traslados", cch, circuito="AMBA").content
a_del = llamar("GET", "/traslados", cch, circuito="DELEGACIONES").content
assert f"/traslados/{vdl}\"" not in a_amba and f"/traslados/{vdl}\"" in a_del
assert f"/traslados/{vdl}\"" not in llamar("GET", "/traslados", ase, circuito="DELEGACIONES").content  # un asesor AMBA no puede forzar ver Delegaciones
# detalle
d = llamar("GET", "/traslados/{viaje_id:int}", ase, viaje_id=vid).content
assert "Persona Externa" in d and "Asignar chofer" not in d
d = llamar("GET", "/traslados/{viaje_id:int}", cch, viaje_id=vid).content
assert "Asignar chofer" in d
assert "/vincular" in d and "es el usuario" in d, "la coordinación puede vincular al pasajero sin usuario"
assert "/vincular" not in llamar("GET", "/traslados/{viaje_id:int}", ase, viaje_id=vid).content
open(str(tmp / "viaje_detalle_vincular.html"), "w").write(d)
# asignar: solo coordinación
assert prohibido("POST", "/traslados/{viaje_id:int}/asignar", ase, {"chofer_id": "1"}, viaje_id=vid) == 403
# recursos
assert prohibido("GET", "/traslados/recursos", ase) == 403
assert "Pedro Chofer" in llamar("GET", "/traslados/recursos", cch).content  # creado desde el panel de usuarios
_cimp = _tr.guardar_chofer(None, {"nombre": "P.Chofer"})   # cargado por nombre al importar
rc = llamar("GET", "/traslados/recursos", cch).content
assert f"/traslados/recursos/chofer/{_cimp}/vincular" in rc and "Parecidos" in rc
open(str(tmp / "recursos_vincular.html"), "w").write(rc)
llamar("POST", "/traslados/recursos/chofer/{cid:int}/vincular", cch, {"usuario_id": str(chof["id"])}, cid=_cimp)
assert "unificadas" in flashes[-1].lower() or "vinculado" in flashes[-1].lower(), flashes[-1]
llamar("POST", "/traslados/recursos/vehiculo", cch, {"dominio": "AA111BB", "capacidad": "5", "marca": "Fiat"})
assert "AA111BB" in llamar("GET", "/traslados/recursos", cch).content
ch = tr.listar_choferes()[0]["id"]; llamar("POST", "/traslados/recursos/chofer", cch, {"id": str(ch), "nombre": "Pedro Chofer", "telefono": "11-5555"}); assert tr.listar_choferes()[0]["telefono"] == "11-5555"; ve = tr.listar_vehiculos()[0]["id"]
r = llamar("POST", "/traslados/{viaje_id:int}/asignar", cch, {"chofer_id": str(ch), "vehiculo_id": str(ve)}, viaje_id=vid)
assert tr.obtener_viaje(vid)["estado"] == "CONFIRMADO", flashes[-1]
llamar("POST", "/traslados/recursos/vehiculo/{vid:int}/estado", cch, {"estado": "TALLER"}, vid=ve)
assert tr.listar_vehiculos()[0]["estado"] == "TALLER"
# otro asesor no puede cancelar ajeno; el dueño sí; cualquiera puede sumarse
otro = db.obtener_usuario(db.crear_usuario("otro", h, s, "Otro Asesor", "ASESOR", eq))
assert prohibido("POST", "/traslados/{viaje_id:int}/cancelar", otro, {"motivo": "x"}, viaje_id=vid) == 403
llamar("POST", "/traslados/{viaje_id:int}/participantes", otro, {"usuario_id": str(otro["id"])}, viaje_id=vid)
assert any(p["usuario_id"] == otro["id"] for p in tr.obtener_viaje(vid)["participantes"])
llamar("POST", "/traslados/{viaje_id:int}/reprogramar", ase, {"fecha": manana, "hora_salida": "10:00"}, viaje_id=vid)
assert tr.obtener_viaje(vid)["estado"] == "SOLICITADO"
llamar("POST", "/traslados/{viaje_id:int}/cancelar", ase, {"motivo": "Suspendido"}, viaje_id=vid)
assert tr.obtener_viaje(vid)["estado"] == "CANCELADO"
# reportes por alcance
for u in (ase, cch):
    assert "Estadísticas" in llamar("GET", "/traslados/reportes", u, desde="2026-01-01", hasta="2026-12-31").content
csvr = llamar("GET", "/traslados/reportes.csv", cch, desde="2026-01-01", hasta="2026-12-31")
assert csvr.content.startswith("﻿Fecha")
# unidades
assert prohibido("GET", "/unidades", ase) == 403 and prohibido("POST", "/unidades", cch, {"nombre": "Z"}) == 403 and prohibido("GET", "/unidades.csv", dl) == 403
assert "Unidades" in llamar("GET", "/unidades", direc).content
llamar("POST", "/unidades", direc, {"nombre": "Nueva U", "correo": "x@y.com", "ciudad": "Rosario", "zona": "Interior y otras", "lat": "-32,9", "lng": "-60.6"})
assert any(x["nombre"] == "Nueva U" and x["lat"] == -32.9 for x in db.listar_unidades())
llamar("POST", "/unidades", direc, {"nombre": "Mala", "correo": "xx"}); assert "correo" in flashes[-1].lower()
assert prohibido("POST", "/unidades", chof, {"nombre": "Z"}) == 403
previa = llamar("POST", "/unidades/importar", direc, {"archivo": Archivo("nombre;ciudad;provincia\nNueva U;Rosario;Santa Fe\nOtra Más;Salta;Salta\n".encode())})
cuerpo = getattr(previa, "content", None) or previa.body
assert "vista previa" in cuerpo and "Otra Más" in cuerpo
import re as _re
llamar("POST", "/unidades/importar/confirmar", direc, {"token": _re.search(r'name="token" value="([^"]+)"', cuerpo).group(1)})
assert db.listar_unidades(q="Otra Más") and "Importación: 1 nuevas, 1 actualizadas" in flashes[-1], flashes[-1]
assert llamar("GET", "/unidades.csv", direc).content.lstrip("\ufeff").startswith("nombre")
for u_ in (ase, dl, cch, chof):
    assert "Ubicación de unidades" in llamar("GET", "/traslados/mapa", u_).content
assert prohibido("POST", "/admin/equipos/{equipo_id:int}/unidades", ase, {"unidad_id": []}, equipo_id=eq) == 403
llamar("POST", "/admin/equipos/{equipo_id:int}/unidades", direc, {"unidad_id": [str(UN)]}, equipo_id=eq)
assert [x["id"] for x in db.unidades_de_equipo(eq)] == [UN]
assert "Habituales" in llamar("GET", "/traslados/nuevo", ase).content
import io as _io
from PIL import Image
buf = _io.BytesIO(); Image.new("RGB", (50, 80), (0, 90, 200)).save(buf, "PNG")
class Img(Archivo): filename = "a.png"
llamar("POST", "/perfil/avatar", ase, {"imagen": Img(buf.getvalue())})
assert db.obtener_usuario(ase["id"])["avatar"] == 1
assert llamar("GET", "/avatar/{usuario_id:int}", cch, usuario_id=ase["id"]).content[:4] == b"\x89PNG"
llamar("POST", "/perfil/avatar", ase, {"imagen": Archivo(b"basura")}); assert "imagen" in flashes[-1].lower()
llamar("POST", "/perfil/avatar/quitar", ase); assert db.obtener_usuario(ase["id"])["avatar"] == 0
# importar excel (pantalla)
assert "Importar el Excel" in llamar("GET", "/traslados/importar", cch).content
assert prohibido("GET", "/traslados/importar", ase) == 403
# ---- personas sin usuario (punto 6)
import json as _json
ext = _json.dumps({"k": "a1", "nombre": "Lucía Médica", "area": "Salud", "observaciones": "Médica de guardia"})
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": manana, "unidad_id": str(UN), "agentes": [str(ase["id"])], "externos": [ext], "auto_ext_a1": "1"})
assert r.status_code == 303, flashes[-3:]
v6 = tr.obtener_viaje(int(r.url.rsplit("/", 1)[1]))
pe = next(p for p in v6["participantes"] if p["nombre"] == "Lucía Médica")
assert pe["externo"] and pe["externo_id"] and pe["auto_propio"] == 1 and pe["externo_area"] == "Salud"
assert "Persona Externa" in [e["nombre"] for e in tr.listar_externos()]  # el formato anterior también queda en el listado
# el buscador la ofrece (sin acentos) con la etiqueta
b = llamar("GET", "/traslados/personas", ase, q="lucia").content
assert any(x.get("externo") and x["nombre"] == "Lucía Médica" for x in b)
# reusar desde el buscador no la duplica
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": manana, "unidad_id": str(UN), "agentes": [str(ase["id"])],
            "externos": [_json.dumps({"k": "b", "id": pe["externo_id"], "nombre": "Lucía Médica"})]})
assert r.status_code == 303 and sum(1 for e in tr.listar_externos() if e["nombre"] == "Lucía Médica") == 1
# si vuelve con error, las personas sin usuario siguen en el formulario
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": manana, "unidad_id": str(fuera["id"]), "agentes": [str(ase["id"])], "externos": [ext]})
assert "AMBA" in r.content and "Lucía Médica" in r.content and "sin usuario" in r.content
# detalle: escribir un nombre suma a esa persona (antes se sumaba quien estaba elegido en la lista)
v7 = vid7 = int(llamar("POST", "/traslados/nuevo", cch, {"fecha": manana, "unidad_id": str(UN), "agentes": [str(cch["id"])]}).url.rsplit("/", 1)[1])
det = llamar("GET", "/traslados/{viaje_id:int}", cch, viaje_id=vid7).content
assert "— elegir —" in det and "Persona sin usuario" in det and "lista-externos" in det
llamar("POST", "/traslados/{viaje_id:int}/participantes", cch, {"usuario_id": "", "nombre": "Juan Externo", "area": "Prensa"}, viaje_id=vid7)
assert any(p["nombre"] == "Juan Externo" and p["externo"] for p in tr.obtener_viaje(vid7)["participantes"]), flashes[-1]
llamar("POST", "/traslados/{viaje_id:int}/participantes", cch, {"usuario_id": str(ase["id"]), "nombre": "Otro"}, viaje_id=vid7)
assert "no las dos cosas" in flashes[-1]
llamar("POST", "/traslados/{viaje_id:int}/participantes", ase, {"yo": "1"}, viaje_id=vid7)
assert any(p["usuario_id"] == ase["id"] for p in tr.obtener_viaje(vid7)["participantes"])
assert "sin usuario" in llamar("GET", "/traslados/{viaje_id:int}", cch, viaje_id=vid7).content
# listado: solo coordinación; vincular a un usuario conserva el historial
assert prohibido("GET", "/traslados/externos", ase) == 403
assert "Juan Externo" in llamar("GET", "/traslados/externos", cch).content
juan = next(e for e in tr.listar_externos() if e["nombre"] == "Juan Externo")
jid = db.crear_usuario("juan", h, s, "Juan Externo", "ASESOR", eq)
assert "mismo nombre" in llamar("GET", "/traslados/externos", cch).content
llamar("POST", "/traslados/externos/{eid:int}/vincular", cch, {"usuario_id": str(jid)}, eid=juan["id"])
assert "1 viaje" in flashes[-1] and any(p["usuario_id"] == jid for p in tr.obtener_viaje(vid7)["participantes"])
assert not any(x.get("externo") and x["nombre"] == "Juan Externo" for x in llamar("GET", "/traslados/personas", ase, q="juan").content)
llamar("POST", "/traslados/externos/{eid:int}", cch, {"nombre": "Lucía Médica", "area": "Sanidad"}, eid=pe["externo_id"])
assert next(e for e in tr.listar_externos() if e["id"] == pe["externo_id"])["area"] == "Sanidad"
llamar("POST", "/traslados/externos/{eid:int}/activo", cch, {"activo": "0"}, eid=pe["externo_id"])
assert not llamar("GET", "/traslados/personas", ase, q="lucia").content and tr.obtener_viaje(v6["id"])["participantes"]
ayer = (date.today() - timedelta(days=1)).isoformat()
with db._conexion() as con: con.execute("UPDATE viajes SET fecha = ?, estado = 'CONFIRMADO' WHERE id = ?", (ayer, v6["id"]))
csvr = llamar("GET", "/traslados/reportes.csv", cch, desde="2020-01-01", hasta=ayer)
assert "Lucía Médica (sin usuario)" in csvr.content
print("personas sin usuario OK")
# ---- pestaña «Todos» (Dirección / coordinación) y avisos al costado
hoy_s = date.today().isoformat()
r = llamar("POST", "/traslados/nuevo", dl, {"fecha": hoy_s, "agentes": [str(dl["id"])], "circuito": "DELEGACIONES", "tipo_actividad": "VISITA"})
assert r.status_code == 303, flashes[-2:]
llamar("POST", "/traslados/nuevo", ase, {"fecha": hoy_s, "hora_salida": "10:00", "unidad_id": str(UN), "motivo": "m", "agentes": [str(ase["id"])]})  # uno AMBA el mismo día (la prueba no depende del día de la semana)
t = llamar("GET", "/traslados", direc, circuito="TODOS").content
assert "Autos PPN · Todos" in t and '<option value="TODOS" selected>Todos</option>' in t  # el circuito se elige en la barra única
assert "chip-circuito--delegaciones" in t and "chip-circuito--amba" in t, "en Todos se ven los dos circuitos"
assert t.index("Agenda semanal") < t.index("Alertas operativas") and "traslados-lateral" in t
a_ = llamar("GET", "/traslados", direc, circuito="AMBA").content
assert 'class="chip-circuito ' not in a_ and "Autos PPN · AMBA" in a_
assert "value=\"TODOS\"" not in llamar("GET", "/traslados", ase, circuito="TODOS", ver="agenda").content  # quien ve un solo circuito no tiene «Todos»
er = llamar("GET", "/traslados/reportes", direc, circuito="TODOS", desde="2020-01-01", hasta=hoy_s).content
assert "Desplazamientos" in er and "Viajes a la unidad" in er and 'value="TODOS" selected' in er
assert "Equipos (viajes a la unidad)" in llamar("GET", "/traslados/reportes.csv", direc, circuito="TODOS", desde="2020-01-01", hasta=hoy_s).content
print("pestaña Todos OK")
print("rutas de traslados OK")
print("ALL OK")
