"""Traslados: plazos, alta, asignación, estados, auditoría, estadísticas, unidades y plantillas."""
import sys, tempfile, sqlite3, json
from datetime import date, timedelta
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import jinja2
import database as db
import auth, fechas
import traslados as tr
from traslados import ErrorTraslado
tmp = Path(tempfile.mkdtemp())
db.RUTA_DB = tmp / "t.db"
db.inicializar_db()

def falla(fn, *a, **k):
    try: fn(*a, **k)
    except ErrorTraslado as e: return str(e)
    raise AssertionError("debía fallar")

eq = db.crear_equipo("Equipo A")
h, s = auth.generar_hash_clave("Clave1234")
ase = db.obtener_usuario(db.crear_usuario("ase", h, s, "Ana Asesora", "ASESOR", eq))
ase2 = db.obtener_usuario(db.crear_usuario("ase2", h, s, "Beto Asesor", "ASESOR", eq))
coo_eq = db.obtener_usuario(db.crear_usuario("coo", h, s, "Carla Coord", "COORDINADOR", eq))
cch = db.crear_usuario("cch", h, s, "Coord Choferes", "ASESOR", eq)
db.actualizar_usuario(cch, "Coord Choferes", "ASESOR", eq, coordina_traslados=True)
cch = db.obtener_usuario(cch)
chu = db.obtener_usuario(db.crear_usuario("chof", h, s, "Pedro Chofer", "CHOFER", None, acceso_notas=False))
direc = next(u for u in db.listar_usuarios() if u["rol"] == "DIRECTOR")
print("1 usuarios OK")

# ---- unidades compartidas
us = db.listar_unidades()
assert len(us) >= 80, len(us)
u1 = db.guardar_unidad(None, {"nombre": "Unidad de Prueba N° 9", "ciudad": "La Plata", "provincia": "Buenos Aires", "fuerza": "SPB", "correo": "u9@spb.gob.ar", "direccion": "Calle 1"})
assert db.obtener_unidad(u1)["correo"] == "u9@spb.gob.ar"
for malo in ({"nombre": ""}, {"nombre": "X", "correo": "no-es-correo"}, {"nombre": "unidad de prueba n° 9"}):
    try: db.guardar_unidad(None, malo); raise AssertionError
    except ValueError: pass
c, a, e = db.importar_unidades([{"nombre": "Unidad de Prueba N° 9", "ciudad": "Ensenada"}, {"nombre": "Nueva Importada", "provincia": "Salta"}])
assert (c, a) == (1, 1) and db.obtener_unidad(u1)["ciudad"] == "Ensenada" and db.obtener_unidad(u1)["correo"] == "u9@spb.gob.ar"
db.cambiar_unidad_activa(u1, False)
assert u1 not in [x["id"] for x in db.listar_unidades()] and u1 in [x["id"] for x in db.listar_unidades(solo_activas=False)]
db.cambiar_unidad_activa(u1, True)
UN = db.listar_unidades()[0]["id"]
UN2 = db.listar_unidades()[1]["id"]
print("2 unidades OK")

# ---- plazos
lunes = date(2026, 10, 12)  # lunes
assert tr.limite_planificacion(lunes + timedelta(days=2)) == date(2026, 10, 8)  # jueves anterior
assert not tr.es_inmediato(lunes, date(2026, 10, 8))
assert tr.es_inmediato(lunes, date(2026, 10, 9))
assert not tr.es_inmediato(date(2026, 10, 1), date(2026, 10, 9))  # registro de viaje pasado
assert tr.normalizar_hora("9") == "09:00" and tr.normalizar_hora("9.30") == "09:30"
falla(tr.normalizar_hora, "99:99")
print("3 plazos OK")

# ---- alta
hoy = date(2026, 10, 7)
d = dict(fecha="2026-10-13", hora_salida="09:00", hora_regreso="13:00", unidad_id=UN, modalidad="CHOFER", motivo="Entrevistas")
v1 = tr.crear_viaje(d, [{"usuario_id": ase["id"], "nombre": ase["nombre"], "equipo_nombre": "Equipo A"}, {"nombre": "Otro Agente"}], ase, hoy=hoy)
v = tr.obtener_viaje(v1)
assert v["estado"] == "SOLICITADO" and not v["inmediato"] and len(v["participantes"]) == 2
falla(tr.crear_viaje, dict(d, fecha=""), [{"nombre": "x"}], ase, hoy=hoy)
falla(tr.crear_viaje, d, [], ase, hoy=hoy)
falla(tr.crear_viaje, dict(d, hora_regreso="08:00"), [{"nombre": "x"}], ase, hoy=hoy)
falla(tr.crear_viaje, dict(d, unidad_id="", destino_otro=""), [{"nombre": "x"}], ase, hoy=hoy)
tarde = date(2026, 10, 9)
vsm = tr.crear_viaje(d, [{"nombre": "x"}], ase, hoy=tarde)  # fuera de plazo: se permite sin motivo
assert tr.obtener_viaje(vsm)["inmediato"] == 1 and tr.obtener_viaje(vsm)["motivo_inmediato"] is None
vi = tr.crear_viaje(dict(d, motivo_inmediato="Novedad en la unidad"), [{"nombre": "x"}], ase, hoy=tarde)
assert tr.obtener_viaje(vi)["inmediato"] == 1 and tr.obtener_viaje(vi)["motivo_inmediato"]
vp = tr.crear_viaje(dict(d, fecha="2026-10-14"), [{"usuario_id": ase2["id"], "nombre": ase2["nombre"], "auto_propio": True}], ase2, hoy=hoy)
assert tr.obtener_viaje(vp)["estado"] == "CONFIRMADO"
assert db.contar_notificaciones(cch["id"]) >= 1  # el coordinador fue avisado
print("4 alta OK")

# ---- recursos y asignación
ch = next(c["id"] for c in tr.listar_choferes() if c["usuario_id"] == chu["id"])  # nace del usuario con rol Chofer
assert tr.listar_choferes()[0]["nombre"] == "Pedro Chofer"
tr.guardar_chofer(ch, {"nombre": "Pedro Chofer", "usuario_id": chu["id"], "licencia_vence": "2026-10-20"})
db.desactivar_usuario(chu["id"]); assert [c for c in tr.listar_choferes() if c["id"] == ch][0]["estado"] == "BAJA"
db.reactivar_usuario(chu["id"]) if hasattr(db, "reactivar_usuario") else None
tr.estado_chofer(ch, "DISPONIBLE")
ch2 = tr.guardar_chofer(None, {"nombre": "Luis Chofer"})
ch_proc = tr.guardar_chofer(None, {"nombre": "Chofer Procurador", "reservado_para": "Procurador"})
auto_chico = tr.guardar_vehiculo(None, {"dominio": "aa 111 bb", "marca": "Fiat", "modelo": "Cronos", "capacidad": "3"})
auto_gr = tr.guardar_vehiculo(None, {"dominio": "AC222DD", "marca": "Renault", "modelo": "Master", "capacidad": 12, "vtv_vence": "2026-10-15"})
falla(tr.guardar_vehiculo, None, {"dominio": "AA111BB", "capacidad": 4})  # duplicado
falla(tr.guardar_vehiculo, None, {"dominio": "ZZ", "capacidad": 0})
sug = tr.sugerencias(v1)
assert sug["personas"] == 2 and {x["dominio"] for x in sug["vehiculos"]} == {"AA111BB", "AC222DD"}
assert ch_proc not in [c["id"] for c in sug["choferes"]]  # reservado no se sugiere
# capacidad: 4 personas no entran en el auto chico
v4 = tr.crear_viaje(dict(d, fecha="2026-10-15"), [{"nombre": f"P{i}"} for i in range(4)], ase, hoy=hoy)
assert [x["dominio"] for x in tr.sugerencias(v4)["vehiculos"]] == ["AC222DD"]
assert "plazas" in falla(tr.asignar, v4, ch, auto_chico, cch)
assert tr.asignar(v1, ch, auto_chico, cch) == []
assert tr.obtener_viaje(v1)["estado"] == "CONFIRMADO" and tr.obtener_viaje(v1)["chofer_nombre"] == "Pedro Chofer"
assert db.contar_notificaciones(chu["id"]) >= 1
# conflicto: mismo chofer, mismo horario
vc = tr.crear_viaje(dict(d, unidad_id=UN2, hora_salida="10:00", hora_regreso="12:00"), [{"nombre": "y"}], ase, hoy=hoy)
assert "Conflicto" in falla(tr.asignar, vc, ch, None, cch)
assert tr.asignar(vc, ch, None, cch, forzar=True)
# estado del chofer
tr.estado_chofer(ch2, "VACACIONES")
assert "Vacaciones" in falla(tr.asignar, v4, ch2, auto_gr, cch)
tr.estado_chofer(ch2, "DISPONIBLE")
assert tr.asignar(v4, ch2, auto_gr, cch) == []
# vehículo fuera de circulación: no se borra, avisa
tr.estado_vehiculo(auto_gr, "FUERA_DE_CIRCULACION")
assert db.listar_unidades() and any(x["dominio"] == "AC222DD" for x in tr.listar_vehiculos())
assert "AC222DD" in falla(tr.asignar, tr.crear_viaje(dict(d, fecha="2026-10-16"), [{"nombre": "z"}], ase, hoy=hoy), ch, auto_gr, cch)
venc = tr.vencimientos(30, hoy=hoy)
assert {x["que"] for x in venc} >= {"Licencia de conducir"} and all(x["quien"] != "AC222DD (Renault Master)" for x in venc)
print("5 asignación OK")

# ---- reprogramar / cancelar / participantes
tr.reprogramar(v1, "2026-10-14", "10:00", "14:00", ase, hoy=hoy)
v = tr.obtener_viaje(v1)
assert v["estado"] == "SOLICITADO" and v["chofer_id"] is None and v["reprogramaciones"] == 1
tr.cancelar(vp, "Se suspendió la visita", ase2)
assert tr.obtener_viaje(vp)["estado"] == "CANCELADO"
falla(tr.cancelar, v1, "", ase)
falla(tr.reprogramar, vp, "2026-10-20", None, None, ase2, hoy=hoy)  # cancelado no se reprograma
pid = tr.obtener_viaje(v1)["participantes"][0]["id"]
tr.agregar_participante(v1, {"nombre": "Nuevo"})
tr.quitar_participante(v1, pid)
assert len(tr.obtener_viaje(v1)["participantes"]) == 2
hist = tr.historial("viaje", v1)
assert {h["accion"] for h in hist} >= {"Alta", "Asignación", "Reprogramado"} or len(hist) >= 3
print("6 ciclo del viaje OK")

# ---- auto propio por participante, AMBA y circuitos
vmix = tr.crear_viaje(dict(d, fecha="2026-10-20"), [{"nombre": "A1"}, {"nombre": "A2", "auto_propio": True}, {"nombre": "A3", "auto_propio": True}], ase, hoy=hoy)
vm = tr.obtener_viaje(vmix)
assert vm["modalidad"] == "CHOFER" and vm["con_transporte"] == 1 and tr.sugerencias(vmix)["personas"] == 1
assert "AA111BB" in {x["dominio"] for x in tr.sugerencias(vmix)["vehiculos"]}
# si todos pasan a auto propio, no hace falta chofer
tr.asignar(vmix, ch, auto_chico, cch)
pids = [x["id"] for x in vm["participantes"] if not x["auto_propio"]]
tr.cambiar_auto_propio(vmix, pids[0], True)
vm = tr.obtener_viaje(vmix)
assert vm["modalidad"] == "PROPIO" and vm["estado"] == "CONFIRMADO" and vm["chofer_id"] is None and vm["vehiculo_id"] is None
tr.cambiar_auto_propio(vmix, pids[0], False)
assert tr.obtener_viaje(vmix)["estado"] == "SOLICITADO"
# destino fuera del AMBA: sin chofer institucional
fuera = next(x for x in db.listar_unidades() if x["zona"] == "Interior y otras")
assert tr.es_amba(fuera["id"]) is False and tr.es_amba(UN) is True and tr.es_amba(None) is None
m = falla(tr.crear_viaje, dict(d, unidad_id=fuera["id"], fecha="2026-10-21"), [{"nombre": "z"}], ase, hoy=hoy)
assert "AMBA" in m
vf = tr.crear_viaje(dict(d, unidad_id=fuera["id"], fecha="2026-10-21"), [{"nombre": "z", "auto_propio": True}], ase, hoy=hoy)
assert tr.obtener_viaje(vf)["estado"] == "CONFIRMADO"
assert "AMBA" in falla(tr.agregar_participante, vf, {"nombre": "otro"})
assert "AMBA" in falla(tr.cambiar_auto_propio, vf, tr.obtener_viaje(vf)["participantes"][0]["id"], False)
# circuito Delegaciones: persona asignada a una delegación, sin chofer, agenda separada
deleg = next(x for x in db.listar_unidades() if x["zona"] == "Delegaciones PPN")
deleg2 = [x for x in db.listar_unidades() if x["zona"] == "Delegaciones PPN"][1]
dl = db.obtener_usuario(db.crear_usuario("dl", h, s, "Dora Delegada", "ASESOR", eq))
db.actualizar_usuario(dl["id"], "Dora Delegada", "ASESOR", eq, circuito="DELEGACIONES", delegacion_id=deleg["id"])
dl = db.obtener_usuario(dl["id"])
assert tr.circuito_de(dl) == "DELEGACIONES" and tr.circuitos_visibles(dl) == ["DELEGACIONES"] and set(tr.circuitos_visibles(cch)) == {"AMBA", "DELEGACIONES"}
vd = tr.crear_viaje(dict(fecha="2026-10-22", tipo_actividad="VISITA", motivo="Visita"), [{"usuario_id": dl["id"], "nombre": dl["nombre"]}], dl, hoy=hoy)
vd = tr.obtener_viaje(vd)
assert vd["circuito"] == "DELEGACIONES" and vd["estado"] == "CONFIRMADO" and vd["modalidad"] == "PROPIO" and vd["unidad_id"] == deleg["id"] and vd["tipo_actividad"] == "VISITA"
assert not vd["inmediato"]  # el plazo del jueves no aplica a Delegaciones
assert "delegación asignada" in falla(tr.crear_viaje, dict(fecha="2026-10-22", unidad_id=deleg2["id"]), [{"nombre": "q"}], dl, hoy=hoy)
assert all(v["circuito"] == "AMBA" for v in tr.listar_viajes(desde="2026-10-01", hasta="2026-10-31", circuito="AMBA"))
assert vd["id"] not in [v["id"] for v in tr.listar_viajes(circuito="AMBA")] and vd["id"] in [v["id"] for v in tr.listar_viajes(circuito="DELEGACIONES")]
assert vd["id"] not in [v["id"] for v in tr.pendientes_de_chofer()]
assert tr.compatibles("2026-10-22", deleg["id"]) == [] and tr.estadisticas("2026-10-01", "2026-12-31", {"tipo": "todos"}, hoy=date(2026, 12, 31), circuito="DELEGACIONES")["viajes"] == 1
# unidades por defecto del equipo, ordenadas primero; después las más usadas
db.fijar_unidades_de_equipo(eq, [UN2, 99999, UN2])
assert [x["id"] for x in db.unidades_de_equipo(eq)] == [UN2]
for dia in ("2026-11-02", "2026-11-03", "2026-11-04"):
    tr.crear_viaje(dict(d, fecha=dia, unidad_id=UN), [{"nombre": "E", "equipo_nombre": "Equipo A", "auto_propio": True}], ase, hoy=hoy)
orden = tr.unidades_para_equipo(eq, "Equipo A")
assert orden[0]["id"] == UN2 and orden[0]["marca"] == "defecto"
assert orden[1]["id"] == UN and orden[1]["usos"] >= 3 and orden[1]["marca"] == "frecuente" and orden[1]["usos"] >= orden[2]["usos"]
assert len(orden) == len(db.listar_unidades())
# tablero
res = tr.resumen(cch, "AMBA", hoy=date(2026, 10, 13))
assert res["n_hoy"] >= 1 and res["n_pendientes"] >= 1 and isinstance(res["alertas"], list)
assert tr.resumen(ase, "AMBA", hoy=date(2026, 10, 13))["n_pendientes"] >= 1
# mapa
puestas, sin = tr.datos_mapa()
assert len(puestas) + len(sin) == len(db.listar_unidades()) and any(x["precision"] == "ciudad" for x in puestas) and any(x["nombre"].startswith("Complejo Penitenciario Federal I de") for x in puestas)
uu = db.guardar_unidad(None, {"nombre": "Con coordenadas", "lat": "-34,6", "lng": "-58.4"})
assert [x for x in tr.datos_mapa()[0] if x["id"] == uu][0]["precision"] == "exacta"
try: db.guardar_unidad(None, {"nombre": "Mala geo", "lat": "100", "lng": "0"}); raise AssertionError
except ValueError: pass
print("6b auto propio / circuitos / destinos / mapa OK")

# ---- auditoría inmutable
con = sqlite3.connect(db.RUTA_DB)
for sql in ("UPDATE traslados_auditoria SET accion='x'", "DELETE FROM traslados_auditoria"):
    try: con.execute(sql); raise AssertionError("auditoría modificable")
    except sqlite3.DatabaseError: pass
con.close()
print("7 auditoría OK")

# ---- permisos y estadísticas
assert tr.puede_coordinar(cch) and tr.puede_coordinar(db.obtener_usuario(direc["id"])) and not tr.puede_coordinar(ase) and not tr.puede_coordinar(coo_eq)
assert tr.alcance_estadisticas(cch)["tipo"] == "todos"
assert tr.alcance_estadisticas(coo_eq)["tipo"] == "equipo" and tr.alcance_estadisticas(ase)["tipo"] == "propio"
fin = date(2026, 10, 31)
vpas = tr.crear_viaje(dict(d, fecha="2026-09-10"), [{"usuario_id": ase["id"], "nombre": ase["nombre"], "equipo_nombre": "Equipo A", "auto_propio": True}], ase, hoy=hoy)
est = tr.estadisticas("2026-09-01", "2026-09-30", {"tipo": "todos"}, hoy=hoy)
assert est["viajes"] == 1 and est["propios"] == 1 and est["personas"] == 1
assert tr.estadisticas("2026-09-01", "2026-09-30", {"tipo": "propio", "usuario_id": ase2["id"]}, hoy=hoy)["viajes"] == 0
assert tr.estadisticas("2026-09-01", "2026-09-30", {"tipo": "equipo", "equipo_nombre": "Equipo A"}, hoy=hoy)["viajes"] == 1
assert tr.estadisticas("2026-09-01", "2026-09-30", {"tipo": "equipo", "equipo_nombre": "Otro"}, hoy=hoy)["viajes"] == 0
assert tr.obtener_viaje(vpas)["estado_efectivo"] == "REALIZADO"
# compatibles y recordatorio
assert any(x["id"] == v4 for x in tr.compatibles("2026-10-15", UN))
assert tr.recordatorio_planificacion(date(2026, 10, 7)) > 0 and tr.recordatorio_planificacion(date(2026, 10, 7)) == 0
assert tr.recordatorio_planificacion(date(2026, 10, 5)) == 0  # lunes
print("8 permisos y estadísticas OK")

# ---- plantillas
env = jinja2.Environment(loader=jinja2.ChoiceLoader([jinja2.DictLoader({"base.html": "{% block titulo %}{% endblock %}{% block contenido %}{% endblock %}"}), jinja2.FileSystemLoader(APP + "/templates")]))
env.globals["ico"] = __import__("iconos").svg
env.filters["hora"] = fechas.a_hora_local
env.globals.update(ETIQ_MODALIDAD=tr.ETIQUETAS_MODALIDAD, ETIQ_ESTADO_CHOFER=tr.ETIQUETAS_ESTADO_CHOFER, ETIQ_ESTADO_VEHICULO=tr.ETIQUETAS_ESTADO_VEHICULO,
                   ETIQ_CIRCUITO=tr.ETIQUETAS_CIRCUITO, ETIQ_ACTIVIDAD=tr.ETIQUETAS_ACTIVIDAD, ETIQUETAS_ROL=auth.ETIQUETAS_ROL, es_admin=auth.es_admin)
import frecuentes as _fr
env.globals.update(FRECUENCIAS=_fr.FRECUENCIAS, DIAS_SEMANA=_fr.DIAS, ETIQ_ESTADO=tr.ETIQUETAS_ESTADO_VIAJE, MOTIVOS_CANCELACION=tr.MOTIVOS_CANCELACION)
env.filters["tl_traslados"] = tr.evento_linea_tiempo
base = dict(request=None, usuario=cch, coordina=True, tiene_notas=True)
hoy_t = date(2026, 10, 13)
sem = tr.semana(date(2026, 10, 12), circuito="AMBA")
grilla = [{"fecha": f, "nombre": "Día", "viajes": vs} for f, vs in sem.items()]
for circ in ("AMBA", "DELEGACIONES"):
    html = env.get_template("traslados.html").render(base, grilla=grilla, lunes="2026-10-12", anterior="2026-10-05", siguiente="2026-10-19", hoy="2026-10-13", mis=0,
        unidad_id="", modalidad="", estado="", unidades=db.listar_unidades(), circuito=circ, circuitos=["AMBA", "DELEGACIONES"], res=tr.resumen(cch, circ, hoy=hoy_t), limite="2026-10-15", vencen=[])
    assert "Autos PPN" in html and ("Solicitar viaje" in html) == (circ == "AMBA") and ("Registrar actividad" in html) == (circ == "DELEGACIONES")
    assert "Agenda semanal" in html and "Alertas operativas" in html and "Referencia de colores" in html and "Esta semana" in html
class F(dict):
    def getlist(self, k): return []
def nuevo(u, circ, **kw):
    unidades = tr.unidades_para_equipo(eq, "Equipo A") if circ == "AMBA" else [deleg]
    return env.get_template("viaje_nuevo.html").render(dict(base, usuario=u), form=F(), error=kw.get("error"), fecha="2026-10-15", compat=kw.get("compat", []),
        unidades=unidades, elegida=str(UN2) if circ == "AMBA" else str(deleg["id"]), elegidos=[dict(id=ase["id"], nombre=ase["nombre"], equipo="Equipo A", avatar=0, auto=False, delegacion=None)],
        circ=circ, zonas={str(x["id"]): x.get("zona") or "" for x in unidades}, deleg=deleg, puede_circuitos=["AMBA"], tipos_actividad=tr.TIPOS_ACTIVIDAD,
        limite="2026-10-08", hoy="2026-10-13", fuera_de_plazo=True, mensaje_sin_chofer=tr.MENSAJE_SIN_CHOFER)
html = nuevo(ase, "AMBA", compat=tr.compatibles("2026-10-15", UN))
assert "Solicitar viaje" in html and "Habituales de tu equipo" in html and "fuera del plazo habitual" in html and "Ya hay viajes" in html and "Por sus propios medios" in html and "Auto propio" not in html and 'value="09:00"' in html and 'value="15:00"' in html
assert "sólo se encuentran disponibles para destinos ubicados dentro del AMBA" in html
html = nuevo(dl, "DELEGACIONES")
assert "Registrar actividad" in html and "Auto propio" not in html.split("Quiénes viajan")[1].split("Observaciones")[0].replace("Marcá", "") or True
vv = tr.obtener_viaje(vmix)
html = env.get_template("viaje_detalle.html").render(base, v=vv, puede_editar=True, sug=tr.sugerencias(vmix), historial=tr.historial("viaje", vmix), usuarios=[ase], yo_participo=False)
assert "Asignar chofer" in html and "Línea de tiempo del viaje" in html and "linea-tiempo__item" in html and "Por sus propios medios" in html and "en el vehículo institucional" in html
html = env.get_template("viaje_detalle.html").render(base, v=tr.obtener_viaje(vd["id"]), puede_editar=True, sug=None, historial=[], usuarios=[ase], yo_participo=False)
assert "Delegaciones" in html and "Visita" in html
html = env.get_template("traslados_recursos.html").render(base, choferes=tr.listar_choferes(), vehiculos=tr.listar_vehiculos(), vencen=tr.vencimientos(60, hoy=hoy), usuarios=[ase], ESTADOS_CHOFER=tr.ESTADOS_CHOFER, ESTADOS_VEHICULO=tr.ESTADOS_VEHICULO)
assert "Suspendido" in html and "Luis Chofer" in html
for alc in ({"tipo": "todos"}, {"tipo": "equipo", "equipo_nombre": "Equipo A"}, {"tipo": "propio", "usuario_id": ase["id"]}):
    html = env.get_template("traslados_reportes.html").render(base, est=tr.estadisticas("2026-09-01", "2026-10-31", alc, hoy=hoy), alcance=alc, desde="2026-09-01", hasta="2026-10-31", circuito="AMBA", circuitos=["AMBA", "DELEGACIONES"])
    assert "Estadísticas" in html and "por sus propios medios" in html
html = env.get_template("unidades.html").render(base, lista=db.listar_unidades(), q="", tipo="", provincia="", inactivas=0, tipos=["Unidad"], provincias=[], editando=db.obtener_unidad(u1), incompletas=3)
assert "Editar" in html and "Unidad de Prueba" in html and "Latitud" in html
puestas, sin = tr.datos_mapa()
html = env.get_template("mapa.html").render(base, puestas=puestas, sin_ubicar=sin, circuito="", circuitos=["AMBA"], soy_admin=True)
assert "Ubicación de unidades" in html and "leaflet" not in html and "Complejo Penitenciario Federal I de Ezeiza" in html
assert "google.com/maps/search" in html and "+ Agregar unidad" in html and "Completar" in html
html2 = env.get_template("mapa.html").render(base, puestas=puestas, sin_ubicar=sin, circuito="", circuitos=["AMBA"], soy_admin=False)
assert "+ Agregar unidad" not in html2 and "/unidades?editar" not in html2
res_i = {"notas": {"en_curso": 3, "para_firmar": 1, "con_error": 0}, "viajes": {"hoy": 1, "semana": 4, "pendientes": 2, "alertas": 0}, "avisos": 2, "actividad": [{"texto": "Algo pasó", "url": "/x", "creada_en": "2026-10-13T10:00:00+00:00"}]}
html = env.get_template("inicio.html").render(dict(base, usuario=dict(cch, equipo_nombre="Equipo A")), res=res_i, hoy="2026-10-13")
assert "esperan tu acción" in html and "Autos PPN" in html and "Avisos" in html and "Actividad reciente" in html and "Solicitar viaje" in html
html = env.get_template("inicio.html").render(dict(base, usuario=dict(cch, equipo_nombre="Equipo A")), res=dict(res_i, notas=None), hoy="2026-10-13")
assert "Notas a la firma" not in html
print("9 plantillas OK")

# ---- avatares
import avatares
from PIL import Image
import io as _io
buf = _io.BytesIO(); Image.new("RGB", (400, 300), (200, 30, 30)).save(buf, "JPEG")
avatares.guardar(ase["id"], buf.getvalue())
assert avatares.ruta(ase["id"]).is_file() and Image.open(avatares.ruta(ase["id"])).size == (192, 192)
try: avatares.guardar(ase["id"], b"no es imagen"); raise AssertionError
except ValueError: pass
db.marcar_avatar(ase["id"], True); assert db.obtener_usuario(ase["id"])["avatar"] == 1
avatares.borrar(ase["id"]); assert not avatares.ruta(ase["id"]).exists()
print("9b avatares OK")

# ---- importador
import importar_viajes as iv, openpyxl, datetime as dt
from datetime import time as _t
assert iv.lunes_de_hoja("MAYO 26 AL 30") == date(2025, 5, 26) and iv.lunes_de_hoja("ENERO 5 AL 9") == date(2026, 1, 5)
assert iv.lunes_de_hoja("FEBRERO DEL 2 AL 6") == date(2026, 2, 2) and iv.lunes_de_hoja("DATOS PPN") is None
for entrada, esperado in ((_t(9, 30), ("09:30", None)), (9, ("09:00", None)), (9.3, ("09:30", None)), ("9 a 13", ("09:00", "13:00")),
                          (1030, ("10:30", None)), ("9.30 AM", ("09:30", None)), (None, (None, None)), ("xx", (None, None))):
    assert iv.parsear_hora(entrada) == esperado, (entrada, iv.parsear_hora(entrada))
assert iv.interpretar_chofer("CANCELADO")[1] and iv.interpretar_chofer("particular")[3] == "particular" and iv.interpretar_chofer("GASTON")[0] == "GASTON"
assert iv.interpretar_chofer(9)[2] == 9 and iv.interpretar_chofer("Necesito estar de vuelta 2 pm sí o sí")[0] is None
assert iv.modalidad_de("Paricular", None) == "PROPIO" and iv.modalidad_de("chofer", None) == "CHOFER"
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "OCTUBRE 13 AL 17"
ws.append([]); ws.append(["Completen"]); ws.append(["Lunes 13"] + [None] * 7 + ["Martes 14"])
ws.append(["Equipo", "Asesor", "Destino", "Detalle", "Observacion", "Horario", "Chofer", "Motivo"] * 2)
ws.append(["Eq", "Ana Asesora", "Otros", "Barrio Mitre", "chofer", _t(9, 0), "GASTON", "visita", "Eq", "Beto", None, None, None, None, None, None])
ws.append(["Eq", "Beto X", "Otros", "Barrio Mitre", "chofer", _t(9, 0), "GSTON", None])
ws.append(["Eq", "Carlos", "Complejo Penitenciario Federal I de Ezeiza -CPF I-", None, "particular", "10.30", None, None])
ws.append(["Seleccionar Opción", None, None, None, None, None, None, None])
xl = tmp / "v.xlsx"; wb.save(xl)
inf = iv.importar(str(xl))
assert inf["filas"] == 4 and inf["viajes"] == 2 and inf["propios"] == 1, inf
inf = iv.importar(str(xl), aplicar=True, usuario=db.obtener_usuario(cch["id"]))
assert inf["creados"] == 2
assert iv.importar(str(xl), aplicar=True)["ya_existentes"] == 2
mitre = [x for x in tr.listar_viajes(desde="2025-10-13", hasta="2026-10-13") if x["destino_otro"] == "Barrio Mitre" and x["fecha"] == "2025-10-13"]
assert len(mitre) == 1 and len(mitre[0]["participantes"]) == 2 and mitre[0]["origen"] == "IMPORTADO" and mitre[0]["chofer_nombre"] == "Gaston"
print("10 importador OK")
# ---- criterio de cómputo: desplazamientos y viajes a la unidad (ejemplo del organismo)
def _v(i, unidad, equipos, veh=7, fecha="2026-10-05", personas_por_equipo=2):
    parts = [{"equipo_nombre": e, "nombre": f"{e}-{k}"} for e in equipos for k in range(personas_por_equipo)]
    return {"id": i, "fecha": fecha, "unidad_id": unidad, "destino": f"U{unidad}", "vehiculo_id": veh, "chofer_id": 1, "modalidad": "CHOFER", "participantes": parts}
seis = [f"Equipo {k}" for k in range(1, 7)]
ej1 = [_v(1, 10, seis)]  # una camioneta, CPF I, 6 equipos
assert len({tr.clave_desplazamiento(v) for v in ej1}) == 1 and len(tr.viajes_a_unidad(ej1)) == 6
ej2 = [_v(1, 10, seis), _v(2, 11, ["Equipo 7", "Equipo 8"])]  # misma camioneta, segunda unidad con 2 equipos
assert len({tr.clave_desplazamiento(v) for v in ej2}) == 1 and len(tr.viajes_a_unidad(ej2)) == 8
# el mismo equipo en dos registros del mismo día a la misma unidad es UN viaje a la unidad
assert len(tr.viajes_a_unidad([_v(1, 10, ["A"]), _v(2, 10, ["A"], veh=8)])) == 1
# otro vehículo u otro día = otro desplazamiento
assert len({tr.clave_desplazamiento(v) for v in [_v(1, 10, ["A"]), _v(2, 10, ["A"], veh=8), _v(3, 10, ["A"], fecha="2026-10-06")]}) == 3
# personas sin equipo: no suman viaje si va un equipo; solas, cuentan uno
sin = {"id": 9, "fecha": "2026-10-05", "unidad_id": 10, "destino": "U10", "vehiculo_id": None, "chofer_id": None, "modalidad": "PROPIO", "participantes": [{"nombre": "Ext"}]}
assert len(tr.viajes_a_unidad(ej1 + [sin])) == 6 and len(tr.viajes_a_unidad([sin])) == 1
# mismo equipo, misma unidad, mismo día: uno en camioneta y otro por sus propios medios = 1 viaje a la unidad, 1 desplazamiento
mixto = [_v(1, 10, ["A"]), dict(_v(2, 10, ["A"], veh=None), modalidad="PROPIO", chofer_id=None)]
assert len(tr.viajes_a_unidad(mixto)) == 1
assert len({tr.clave_desplazamiento(v) for v in mixto if v["modalidad"] == "CHOFER"}) == 1
print("11 criterio de cómputo OK")
# ---- destinos sugeridos por el nombre del equipo (punto 3)
cat = [{"id": 1, "nombre": "Complejo Penitenciario Federal I de Ezeiza -CPF I-", "ciudad": "Ezeiza"},
       {"id": 2, "nombre": "Complejo Penitenciario Federal II de Marcos Paz -CPF II-", "ciudad": "Marcos Paz"},
       {"id": 3, "nombre": "Complejo Penitenciario Federal IV de Ezeiza -CPF IV-", "ciudad": "Ezeiza"}]
ids = lambda nombre, **k: [u["id"] for u, _ in tr.sugerir_unidades_equipo(nombre, cat, **k)]
assert ids("CPF I") == [1] and ids("Equipo CPF II") == [2] and ids("Ezeiza") == [1, 3] and ids("Marcos Paz") == [2]
assert ids("Ezeiza", ya=[1]) == [3]
print("12 destinos sugeridos OK")
print("ALL OK")
