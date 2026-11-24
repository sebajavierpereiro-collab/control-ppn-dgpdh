"""Traslados: vincular con usuarios a choferes y pasajeros cargados por nombre (p. ej. «L.Aguilar»), sin perder registros."""
import sys, tempfile
from datetime import date, timedelta
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import database as db
import auth
import traslados as tr
from traslados import ErrorTraslado
tmp = Path(tempfile.mkdtemp())
db.RUTA_DB = tmp / "t.db"
db.inicializar_db()

eq = db.crear_equipo("Equipo A")
h, s = auth.generar_hash_clave("Clave1234")
U = lambda us, n, rol, e=eq: db.obtener_usuario(db.crear_usuario(us, h, s, n, rol, e))
ana = U("ana", "Ana María Pérez", "ASESOR")
lucas = U("laguilar", "Lucas Aguilar", "CHOFER", None)
diego = U("dcejas", "Diego Cejas", "CHOFER", None)
otro = U("aguilar2", "Laura Aguilar Ríos", "ASESOR")

# ---------------------------------------------------------------- parecidos
P = tr.parecido_usuario
assert P("Lucas Aguilar", "Lucas Aguilar") == 100 and P("Aguilar Lucas", "Lucas Aguilar") == 100
assert P("L.Aguilar", "Lucas Aguilar") > P("L.Aguilar", "Laura Aguilar Ríos") > 0, (P("L.Aguilar", "Lucas Aguilar"), P("L.Aguilar", "Laura Aguilar Ríos"))
assert P("D.Cejas", "Diego Cejas") >= 50 and P("D.Cejas", "Lucas Aguilar") == 0 and P("Perez", "Ana María Pérez") > 0
assert P("A. Perez", "Ana María Pérez") > P("Perez", "Ana María Pérez")
usuarios = [x for x in db.listar_usuarios() if x["activo"]]
assert tr.sugerir_usuarios("L.Aguilar", usuarios)[0]["id"] == lucas["id"]
assert tr.sugerir_usuarios("Gaston", usuarios) == []

# ---------------------------------------------------------------- choferes
tr.sincronizar_choferes()   # Lucas y Diego (rol Chofer) ya tienen su ficha
ficha_lucas = next(c for c in tr.listar_choferes() if c["usuario_id"] == lucas["id"])
imp = tr.guardar_chofer(None, {"nombre": "L.Aguilar"})       # ficha creada por nombre al importar viajes
gaston = tr.guardar_chofer(None, {"nombre": "Gaston"})
hoy = date.today() + timedelta(days=14)
d = dict(fecha=hoy.isoformat(), hora_salida="09:00", hora_regreso="13:00", destino_otro="CPF I", modalidad="CHOFER", motivo="x")
v1 = tr.crear_viaje(d, [{"usuario_id": ana["id"], "nombre": ana["nombre"]}], ana, hoy=date.today())
v2 = tr.crear_viaje(dict(d, hora_salida="14:00", hora_regreso="16:00"), [{"nombre": "A. Perez"}], ana, hoy=date.today())
with db._conexion() as con:
    con.execute("UPDATE viajes SET chofer_id = ? WHERE id IN (?, ?)", (imp, v1, v2))
    con.execute("INSERT INTO chofer_licencias (chofer_id, desde, hasta, tipo, creado_en) VALUES (?,?,?,?,?)", (imp, "2026-11-01", "2026-11-05", "LICENCIA", db._ahora())) if any(r[0] == "chofer_licencias" for r in con.execute("SELECT name FROM sqlite_master")) else None
r = tr.vincular_chofer(imp, lucas["id"])
assert r["unificada"] and r["registros"] >= 2, r
with db._conexion() as con:
    assert {f[0] for f in con.execute("SELECT chofer_id FROM viajes WHERE id IN (?, ?)", (v1, v2))} == {ficha_lucas["id"]}, "los viajes pasan a la ficha del usuario"
    vieja = con.execute("SELECT * FROM choferes WHERE id = ?", (imp,)).fetchone()
assert vieja["estado"] == "BAJA" and "Unificada con la ficha de Lucas Aguilar" in vieja["observaciones"], "la ficha vieja no se borra: queda en Baja con la constancia"
assert any("Unificada" in h_["accion"] for h_ in tr.historial("chofer", imp))
r = tr.vincular_chofer(gaston, ana["id"]); assert not r["unificada"]
assert next(c for c in tr.listar_choferes() if c["id"] == gaston)["usuario_id"] == ana["id"]
try:
    tr.vincular_chofer(9999, ana["id"]); raise SystemExit("debía fallar")
except ErrorTraslado:
    pass

# ---------------------------------------------------------------- pasajeros
p2 = next(p for p in tr.obtener_viaje(v2)["participantes"] if p["nombre"] == "A. Perez")
assert p2["externo"] and p2.get("externo_id")
v3 = tr.crear_viaje(dict(d, fecha=(hoy + timedelta(days=1)).isoformat()), [{"nombre": "A. Perez"}], ana, hoy=date.today())  # otro viaje, mismo nombre
n = tr.vincular_participante(v2, p2["id"], ana["id"])
assert n == 2, "se vinculan todos los viajes de esa persona"
for v in (v2, v3):
    assert all(p["usuario_id"] == ana["id"] for p in tr.obtener_viaje(v)["participantes"])
try:
    tr.vincular_participante(v2, p2["id"], ana["id"]); raise SystemExit("ya vinculada")
except ErrorTraslado as e:
    assert "ya está vinculada" in str(e)
# un registro suelto sin catálogo (cargado a mano en la base)
with db._conexion() as con:
    pid = con.execute("INSERT INTO viaje_participantes (viaje_id, usuario_id, nombre, auto_propio) VALUES (?,?,?,0)", (v1, None, "Diego C.")).lastrowid
assert tr.vincular_participante(v1, pid, diego["id"]) == 1
assert next(p for p in tr.obtener_viaje(v1)["participantes"] if p["id"] == pid)["nombre"] == "Diego Cejas"
try:
    with db._conexion() as con:
        pid2 = con.execute("INSERT INTO viaje_participantes (viaje_id, usuario_id, nombre, auto_propio) VALUES (?,?,?,0)", (v1, None, "Ana")).lastrowid
    tr.vincular_participante(v1, pid2, ana["id"]); raise SystemExit("repetida en el viaje")
except ErrorTraslado as e:
    assert "ya figura en este viaje" in str(e)
print("ALL OK")
