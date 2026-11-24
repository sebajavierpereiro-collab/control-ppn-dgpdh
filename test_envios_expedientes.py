"""Envíos (agenda, borrador .eml) y expedientes (planillas Excel, solo lectura)."""
import sys, tempfile, email, ast
from email import policy
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import openpyxl, docx
import database as db
import auth, despacho, expedientes, respaldo
tmp = Path(tempfile.mkdtemp())
db.RUTA_DB = tmp / "t.db"
respaldo.CONFIG["activo"] = False
db.inicializar_db()

# 1) destinatario propuesto
txt = "Buenos Aires, 5 de mayo\n\nAl Sr. Director del Servicio Penitenciario Federal\nDr. Juan Pérez\nS/D\n\nTengo el agrado..."
assert despacho.proponer_destinatario(txt) == "Al Sr. Director del Servicio Penitenciario Federal", despacho.proponer_destinatario(txt)
assert despacho.proponer_destinatario("Nada que ver\nTexto") == ""
assert despacho.proponer_destinatario("A la Dra. María López S/D") == "A la Dra. María López"
print("1 destinatario OK")

# 2) agenda: se retroalimenta y se ordena por uso
eq = db.crear_equipo("A")
f = tmp / "f.docx"; d = docx.Document(); d.add_paragraph("x"); d.save(str(f))
n = db.crear_nota("A.docx", eq, f)
db.actualizar_estado(n, "FIRMADA", ruta_firmada=str(f))
db.registrar_envio(n, "CORREO", "Dra. María López", "Maria@Ejemplo.gob.ar")
db.registrar_envio(n, "CORREO", "Dra. María López", "maria@ejemplo.gob.ar")
db.registrar_envio(n, "MANO", "Juan Pérez", None)
cs = db.listar_contactos()
assert cs[0]["nombre"] == "Dra. María López" and cs[0]["usos"] == 2 and cs[0]["correo"] == "maria@ejemplo.gob.ar", cs
assert len(db.listar_envios(n)) == 3
m = despacho.mejor_contacto("A la Dra. María López", cs)
assert m and m["correo"] == "maria@ejemplo.gob.ar"
assert despacho.mejor_contacto("Sr. Otro Distinto", cs) is None
print("2 agenda OK")

# 3) .eml
eml = despacho.armar_borrador_eml("Dra. López", "m@x.ar", "Nota N° 5", "Hola", [(f, "doc.docx")])
msg = email.message_from_bytes(eml, policy=policy.default)
assert msg["X-Unsent"] == "1" and "m@x.ar" in msg["To"] and msg["Subject"] == "Nota N° 5"
assert [a.get_filename() for a in msg.iter_attachments()] == ["doc.docx"]
print("3 eml OK")

# 4) expedientes (.xlsx real; .xls con xlrd simulado)
carp = tmp / "exp"; carp.mkdir()
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["Planilla de expedientes"]); ws.append(["Expediente", "Apellido", "Nombre", "DNI", "Unidad"])
ws.append(["12.345/26", "Gómez", "Ana María", 30111222, "Unidad 1"])
ws.append(["777-26", "Pérez", "Juan", 28999000, "Unidad 2"]); ws.append([])
wb.save(carp / "a.xlsx"); (carp / "~$tmp.xlsx").write_bytes(b"x"); (carp / "roto.xlsx").write_bytes(b"no es excel")
expedientes.CONFIG["carpeta"] = str(carp)
st = expedientes.estado()
assert st["cantidad"] == 2 and len(st["errores"]) == 1 and "roto.xlsx" in st["errores"][0], st
assert expedientes.buscar_numero("12345-26")[0]["datos"]["Apellido"] == "Gómez"
assert expedientes.existe("777/26") and not expedientes.existe("999/99")
assert expedientes.buscar_texto("gomez ana")[0]["datos"]["DNI"] == "30111222"
assert expedientes.buscar_texto("30111222")[0]["datos"]["Nombre"] == "Ana María"
assert expedientes.buscar_texto("zzz") == [] and expedientes.buscar_texto("  ") == []
# cache por mtime: se recarga si cambia
import time; time.sleep(0.01)
ws.append(["888/26", "Nuevo", "Caso", 1, "U3"]); wb.save(carp / "a.xlsx")
assert expedientes.existe("888/26")
# .xls sin xlrd: error claro, sin tirar abajo a las otras
(carp / "b.xls").write_bytes(b"x")
assert any("xlrd" in e or "b.xls" in e for e in expedientes.estado()["errores"])
# la carpeta no se modificó
assert sorted(p.name for p in carp.iterdir()) == sorted(["a.xlsx", "~$tmp.xlsx", "roto.xlsx", "b.xls"])
expedientes.CONFIG["carpeta"] = ""
assert expedientes.estado()["cantidad"] == 0
print("4 expedientes OK")

# 5) consultas registradas
db.registrar_consulta_expediente("gomez", 1, nota_id=n)
c = db.listar_consultas_expedientes()
assert c[0]["consulta"] == "gomez" and c[0]["nota_id"] == n
print("5 consultas OK")

# 6) plantillas
import jinja2
env = jinja2.Environment(loader=jinja2.ChoiceLoader([jinja2.DictLoader({"base.html": "{% block contenido %}{% endblock %}"}), jinja2.FileSystemLoader(APP + "/templates")]))
env.globals["ico"] = __import__("iconos").svg
env.filters["hora"] = lambda v: str(v)
env.globals.update(es_admin=auth.es_admin, hoy_local=lambda: "2026-01-01", ROLES_MESA=auth.ROLES_MESA, funciones_mesa=auth.funciones_mesa, mesa_excepcional=auth.mesa_excepcional,
    datos_despacho=lambda n: dict(nro_nota="5", nro_expte="1", expte_propuesto=False, preparado=True, tiene_pdf=True),
    datos_envio=lambda n: dict(propuesto="Al Sr. X", contacto=cs[0], contactos=cs, envios=db.listar_envios(n["id"])))
class St: pass
class Req: pass
st_ = St(); st_.usuario = dict(rol=auth.ROL_MESA_JEFE, id=1); rq = Req(); rq.state = st_
nota = db.obtener_nota(n); nota["etapa"] = None
out = env.get_template("_estado_nota.html").render(request=rq, nota=nota, etapas=[])
assert "Envío al destinatario" in out and "Dra. María López" in out and "Registrar: enviado por correo" in out
usr = dict(rol=auth.ROL_MESA_JEFE, id=1, nombre="M")
r = env.get_template("expedientes.html").render(request=rq, usuario=usr, filtros={"lpu": "", "dni": "", "expediente": "", "nombre": "gomez"}, campos=expedientes.CAMPOS, buscado=True, avisos=[], resultados=[dict(archivo="a.xlsx", fila=3, datos={"Apellido": "Gómez"})], error=None,
    estado=dict(configurado=True, cantidad=1, archivos=["a.xlsx"], errores=[]), nota_id=None, contar_notificaciones=lambda i: 0)
assert "Gómez" in r
r = env.get_template("expedientes_consultas.html").render(request=rq, usuario=usr, consultas=c, contar_notificaciones=lambda i: 0)
assert "gomez" in r
print("6 plantillas OK")

# 7) actividad
import actividad
h, sa = auth.generar_hash_clave("Clave1234")
uid = db.crear_usuario("mesa1", h, sa, "Mia Mesa", "MESA_JEFE", None)
db.registrar_intento_login("mesa1", "1.1.1.1", True)
db.registrar_intento_login("mesa1", "1.1.1.1", False)
tok = db.USUARIO_ACTUAL.set(db.obtener_usuario(uid))
db.registrar_acceso(n, "VER"); db.registrar_envio(n, "MANO", "X", None)
db.USUARIO_ACTUAL.reset(tok)
r = actividad.generar()
f = [x for x in r["filas"] if x["usuario"] == "Mia Mesa"]
assert len(f) == 1 and f[0]["ingresos"] == 1 and f[0]["accesos"] == 1 and f[0]["acciones"] >= 1, r
assert actividad.generar("2020-01-01", "2020-01-02")["filas"] == []
env2 = jinja2.Environment(loader=jinja2.ChoiceLoader([jinja2.DictLoader({"base.html": "{% block contenido %}{% endblock %}"}), jinja2.FileSystemLoader(APP + "/templates")]))
env2.globals["ico"] = __import__("iconos").svg
env2.filters["hora"] = lambda v: str(v)
assert "Mia Mesa" in env2.get_template("actividad.html").render(**r, accesos=[])
print("7 actividad OK")
print("ALL OK")
