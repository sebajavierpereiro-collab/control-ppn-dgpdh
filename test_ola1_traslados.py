"""Ola 1 · traslados: panel de flota, fichas, Mi semana, agenda (avatares, plazas, colores), cancelación tipificada, contactos de unidades, gráficos y cumplimiento del plazo."""
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
import flota, frecuentes as fr
from datetime import date as D, timedelta as TD
hoy = D.today()
amba = next(x for x in db.listar_unidades() if (x.get("zona") or "") == "AMBA")
llamar("POST", "/traslados/recursos/vehiculo", cch, {"dominio": "AB 123 CD", "marca": "Toyota", "modelo": "Hiace", "capacidad": "8"})
veh = next(v for v in tr.listar_vehiculos() if v["dominio"] == "AB123CD")
chofer = next(c for c in tr.listar_choferes() if c.get("usuario_id") == chof["id"])

# ---- 101 / 46 · panel de flota y controles
assert prohibido("GET", "/traslados/flota", ase) == 403
p = llamar("GET", "/traslados/flota", cch).content
assert "Panel de flota" in p and "AB123CD" in p and "semaforo--sin_dato" in p
llamar("POST", "/traslados/vehiculos/{vid:int}/control", cch, {"tipo": "MATAFUEGO", "fecha": (hoy - TD(days=360)).isoformat(), "vence_fecha": (hoy + TD(days=5)).isoformat(), "detalle": "Recarga"}, vid=veh["id"])
llamar("POST", "/traslados/vehiculos/{vid:int}/control", cch, {"tipo": "ACEITE", "fecha": hoy.isoformat(), "km": "50000", "vence_km": "50500"}, vid=veh["id"])
llamar("POST", "/traslados/vehiculos/{vid:int}/control", cch, {"tipo": "VTV", "fecha": hoy.isoformat(), "vence_fecha": (hoy - TD(days=1)).isoformat()}, vid=veh["id"])
assert "posterior" in flashes[-1], "un vencimiento anterior a la fecha del control se rechaza"
llamar("POST", "/traslados/vehiculos/{vid:int}/control", cch, {"tipo": "VTV", "fecha": (hoy - TD(days=400)).isoformat(), "vence_fecha": (hoy - TD(days=3)).isoformat()}, vid=veh["id"])
v2 = next(v for v in tr.listar_vehiculos() if v["id"] == veh["id"])
assert v2["km"] == 50000 and v2["vtv_vence"] == (hoy - TD(days=3)).isoformat(), "VTV actualiza el aviso de vencimientos y el km del vehículo"
e = flota.estado_vehiculo(v2)
niv = {c["tipo"]: c["nivel"] for c in e["controles"]}
assert niv["MATAFUEGO"] == "pronto" and niv["ACEITE"] == "pronto" and niv["VTV"] == "vencido" and niv["CUBIERTAS"] == "sin_dato" and e["peor"] == "vencido"
venc = [x["que"] for x in tr.vencimientos(30)]
assert "Matafuego" in venc and any(x.startswith("Aceite") for x in venc) and "VTV" in venc
assert prohibido("POST", "/traslados/vehiculos/{vid:int}/control", ase, {"tipo": "OTRO"}, vid=veh["id"]) == 403
llamar("POST", "/traslados/vehiculos/{vid:int}/km", chof, {"km": "49000"}, vid=veh["id"]); assert "no puede bajar" in flashes[-1]
llamar("POST", "/traslados/vehiculos/{vid:int}/km", chof, {"km": "50600"}, vid=veh["id"])
assert flota.estado_vehiculo(next(v for v in tr.listar_vehiculos() if v["id"] == veh["id"]))["controles"][1]["nivel"] == "vencido", "aceite pasado de km"
f = llamar("GET", "/traslados/vehiculos/{vid:int}", cch, vid=veh["id"]).content
assert "Mantenimiento" in f and "Recarga" in f and "Registrar un control" in f and "linea-tiempo" in f
assert "Registrar un control" not in llamar("GET", "/traslados/vehiculos/{vid:int}", chof, vid=veh["id"]).content
print("101/46 flota OK")

# ---- 47 · ficha del chofer
llamar("POST", "/traslados/recursos/chofer", cch, {"id": str(chofer["id"]), "nombre": chofer["nombre"], "usuario_id": str(chof["id"]), "licencia_vence": (hoy + TD(days=10)).isoformat(), "licencia_categoria": "D1"})
fc = llamar("GET", "/traslados/choferes/{cid:int}", cch, cid=chofer["id"]).content
assert "Pedro Chofer" in fc and "vence en 10 días" in fc and "D1" in fc
assert "Pedro Chofer" in llamar("GET", "/traslados/choferes/{cid:int}", chof, cid=chofer["id"]).content
assert prohibido("GET", "/traslados/choferes/{cid:int}", ase, cid=chofer["id"]) == 403
print("47 ficha chofer OK")

# ---- 49 / 51 / 43 · agenda y detalle
fecha_v = (hoy + TD(days=1)).isoformat()
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": fecha_v, "unidad_id": str(amba["id"]), "agentes": [str(ase["id"]), str(cch["id"])]})
vid = int(r.url.rsplit("/", 1)[1])
llamar("POST", "/traslados/{viaje_id:int}/asignar", cch, {"chofer_id": str(chofer["id"]), "vehiculo_id": str(veh["id"])}, viaje_id=vid)
assert "VTV vencida" in flashes[-1], "ola 4: el vehículo con la VTV vencida pide confirmación"
llamar("POST", "/traslados/{viaje_id:int}/asignar", cch, {"chofer_id": str(chofer["id"]), "vehiculo_id": str(veh["id"]), "forzar": "1"}, viaje_id=vid)
d = llamar("GET", "/traslados/{viaje_id:int}", ase, viaje_id=vid).content
assert "2 de 8 plazas" in d and "ocupacion__barra" in d, flashes[-3:]
ag = llamar("GET", "/traslados", ase, lunes=fecha_v).content
assert 'class="viaje__plazas"' in ag and ">6 libres<" in ag and "Con chofer PPN" in ag and 'id="colorear-por"' in ag and 'data-equipo="Equipo A"' in ag
print("49/51/43 agenda OK")

# ---- 77 · contactos de la unidad
db.guardar_unidad(amba["id"], dict(db.obtener_unidad(amba["id"]), telefono_guardia="011-4444-5555", horarios_visita="Lun a vie 9 a 17", contactos="Jefe de turno: int. 12"))
d = llamar("GET", "/traslados/{viaje_id:int}", ase, viaje_id=vid).content
assert "011-4444-5555" in d and "Lun a vie 9 a 17" in d and "Jefe de turno" in d
assert "011-4444-5555" in llamar("GET", "/traslados/mapa", ase).content
assert "telefono_guardia" in llamar("GET", "/unidades.csv", direc).content
print("77 contactos OK")

# ---- 75 · cancelación tipificada
r = llamar("POST", "/traslados/nuevo", ase, {"fecha": fecha_v, "unidad_id": str(amba["id"]), "agentes": [str(ase["id"])]})
v3 = int(r.url.rsplit("/", 1)[1])
llamar("POST", "/traslados/{viaje_id:int}/cancelar", ase, {"motivo_tipo": "Otro", "motivo": ""}, viaje_id=v3)
assert "Contá el motivo" in flashes[-1] and tr.obtener_viaje(v3)["estado"] != "CANCELADO"
llamar("POST", "/traslados/{viaje_id:int}/cancelar", ase, {"motivo_tipo": "Clima", "motivo": "alerta naranja"}, viaje_id=v3)
assert tr.obtener_viaje(v3)["motivo_cancelacion"] == "Clima: alerta naranja"
assert "La unidad no permite el ingreso" in llamar("GET", "/traslados/{viaje_id:int}", ase, viaje_id=vid).content
print("75 cancelación OK")

# ---- 48 · Mi semana
lunes = tr.lunes_de(hoy)
dia_h = (D.fromisoformat(fecha_v).weekday() + 2) % 5  # un día distinto del viaje cargado arriba (que puede caer la semana próxima)
for k in (1, 2, 3):
    f_ = (lunes - TD(weeks=k) + TD(days=dia_h)).isoformat()
    with db._conexion() as con:
        i = con.execute("INSERT INTO viajes (fecha, unidad_id, modalidad, estado, origen, circuito, creado_por_id, creado_en, actualizado_en) VALUES (?,?,'PROPIO','CONFIRMADO','SISTEMA','AMBA',?,'x','x')", (f_, amba["id"], ase["id"])).lastrowid
        con.execute("INSERT INTO viaje_participantes (viaje_id, usuario_id, nombre, equipo_nombre, auto_propio) VALUES (?,?,?,?,1)", (i, ase["id"], ase["nombre"], "Equipo A"))
falta = fr.faltan_cargar(ase, hoy=hoy)
assert falta and falta[0]["dia"] == dia_h and falta[0]["unidad_id"] == amba["id"], falta
ms = llamar("GET", "/traslados/mi-semana", ase).content
assert "Mi semana" in ms and "Solés viajar y todavía no lo cargaste" in ms and "Esta semana" in ms and "Semana próxima" in ms
assert f'repetir={falta[0]["viaje_id"]}&amp;fecha={falta[0]["fecha"]}' in ms or f'repetir={falta[0]["viaje_id"]}&fecha={falta[0]["fecha"]}' in ms
rp = llamar("GET", "/traslados/nuevo", ase, repetir=falta[0]["viaje_id"], fecha=falta[0]["fecha"]).content
assert f'value="{falta[0]["fecha"]}"' in rp and "revisá y confirmá" in rp
assert prohibido("GET", "/traslados/mi-semana", chof) == 403
print("48 mi semana OK")

# ---- 54 / 85 · gráficos y cumplimiento del plazo
with db._conexion() as con:
    con.execute("UPDATE viajes SET fecha = ?, estado = 'CONFIRMADO' WHERE id = ?", ((hoy - TD(days=2)).isoformat(), vid))
    con.execute("UPDATE viajes SET inmediato = 1 WHERE id = ?", (vid,))
est = tr.estadisticas("2020-01-01", hoy.isoformat(), {"tipo": "todos"})
assert est["por_mes_viajes_unidad"] and est["cumplimiento_plazo"] and est["cumplimiento_plazo"][0]["pct"] < 100
rep = llamar("GET", "/traslados/reportes", direc, desde="2020-01-01", hasta=hoy.isoformat()).content
assert "Viajes a la unidad por mes" in rep and 'class="barras__barra"' in rep and "Cumplimiento del plazo del jueves" in rep
print("54/85 estadísticas OK")
print("ALL OK")
