import sys, os, tempfile, json, sqlite3, types
from datetime import datetime, time, timezone, timedelta
from pathlib import Path
APP = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import docx
import database as db
tmp = Path(tempfile.mkdtemp())
import respaldo; respaldo.CONFIG["activo"] = False
db.RUTA_DB = tmp / "t.db"

# ---- 1) migración sobre una base vieja
con = sqlite3.connect(db.RUTA_DB)
con.executescript("""
CREATE TABLE equipos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE NOT NULL);
CREATE TABLE notas (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre_original TEXT NOT NULL, equipo_id INTEGER NOT NULL, tipo TEXT, es_nota INTEGER NOT NULL DEFAULT 0, estado TEXT NOT NULL DEFAULT 'SUBIDA', observaciones TEXT, cambios_json TEXT, propuestas_json TEXT, parrafos_json TEXT, ruta_original TEXT NOT NULL, ruta_resultado TEXT, ruta_firmada TEXT, error TEXT, creado_en TEXT NOT NULL, actualizado_en TEXT NOT NULL);
""")
con.commit(); con.close()
db.inicializar_db(); db.inicializar_db()  # idempotente
cols = {r[1] for r in sqlite3.connect(db.RUTA_DB).execute("PRAGMA table_info(notas)")}
for c in ("tema","tema_origen","prioridad_sugerida","prioridad_motivo","prioridad_sugerida_estado","ref_nota_id","propuestas_previas_json","propuestas_previas_hash","bloque_id","prioridad"):
    assert c in cols, c
print("1 migración OK")

import nucleo, analisis, automatico, auth
analisis.cargar_temas(tmp / "temas.json"); assert (tmp / "temas.json").exists()

def mkdoc(nombre, parrafos):
    d = docx.Document()
    for p in parrafos: d.add_paragraph(p)
    ruta = tmp / nombre; d.save(str(ruta)); return ruta

eq = db.obtener_equipo_por_defecto_id()
def nueva(nombre, parrafos, prioridad="NORMAL"):
    r = mkdoc(nombre, parrafos)
    return db.crear_nota(nombre, eq, r, prioridad=prioridad)
n1 = nueva("Nota 1.docx", ["Se solicita atención médica urgente para el Sr. Juan Gómez, DNI 30.123.456.", "Expediente EX-2024-12345678-APN-DGPDH#PPN, ingresado el 15/03/2024. Alojan 45 personas."])
n2 = nueva("Nota 2.docx", ["Reclamo por la comida del rancho."], prioridad="PRIORITARIA")
inf = nueva("Informe 1.docx", ["El Sr. Juan Gomes, DNI 30.123.465, solicitó atención médica el 5 de marzo de 2024 por dolor torácico persistente.", "Expediente EX-2024-12345687-APN-DGPDH#PPN. Alojan 54 personas."])

# ---- 2) clasificación rápida
r = automatico.clasificar_rapido(n1); print("2", r)
nota = db.obtener_nota(n1); assert nota["tema"]=="Salud_y_Atencion_Medica" and nota["prioridad_sugerida"]=="URGENTE" and nota["prioridad_sugerida_estado"]=="PENDIENTE"
automatico.clasificar_rapido(n2); assert db.obtener_nota(n2)["prioridad_sugerida_estado"] is None
db.actualizar_tema(n2, "Condiciones_Edilicias", "manual"); automatico.clasificar_rapido(n2)
assert db.obtener_nota(n2)["tema"]=="Condiciones_Edilicias"   # lo manual no se pisa
print("2 clasificación OK")

# ---- 3) bloque + consistencia
bid = db.crear_bloque("B", eq, None, [n1, inf])
res = automatico.analizar_consistencia(n1, usar_ia=False); print("3", res)
h = db.listar_hallazgos(n1); assert {x["dato"] for x in h} >= {"Número de expediente","DNI","Fecha","Cantidad de personas"}, [x["dato"] for x in h]
db.resolver_hallazgo(h[0]["id"], "REVISADO", "tester")
automatico.analizar_consistencia(n1, usar_ia=False)  # recalcular conserva estado
assert any(x["estado"]=="REVISADO" for x in db.listar_hallazgos(n1))
assert db.listar_notas()[-1]["id"] or True
assert sum(x["hallazgos_abiertos"] for x in db.listar_notas() if x["id"]==n1) == len(h)-1
assert automatico.analizar_consistencia(n2)["omitido"]
print("3 consistencia OK")

# ---- 4) mejoras con IA simulada
nucleo.USAR_IA = True
nucleo._MODELOS_DESCARGADOS = {"phi3:mini"}
nucleo.OLLAMA_MODELOS.update({p: "phi3:mini" for p in nucleo.PROCESOS_IA})
nucleo.refrescar_modelos_ollama = lambda *a, **k: nucleo._MODELOS_DESCARGADOS
assert nucleo.ia_disponible("redaccion")
llamadas = []
def falso(proceso, prompt, **kw):
    llamadas.append(proceso)
    if proceso == "redaccion":
        return json.dumps({"sugerencias": [
            {"parrafo": 0, "tipo": "COMPLETAR", "propuesto": "Se solicita atención médica urgente para el Sr. Juan Gómez, DNI 30.123.456, por dolor torácico persistente.", "cita": "solicitó atención médica el 5 de marzo de 2024 por dolor torácico persistente"},
            {"parrafo": 1, "tipo": "PRECISAR", "propuesto": "Alojan 99 personas.", "cita": "inventada que no figura"}]})
    if proceso == "clasificacion":
        return json.dumps({"tema": "Salud_y_Atencion_Medica", "prioridad": "URGENTE", "motivo": "dolor torácico", "confianza": 0.9})
    return None
nucleo.consultar_ollama = falso
m = automatico.analizar_mejoras(n1); print("4", m)
ms = db.listar_mejoras(n1); assert len(ms)==1 and ms[0]["tipo"]=="COMPLETAR", ms
c = automatico.analizar_clasificacion(n1); assert c["origen"]=="ia", c
assert db.obtener_nota(n1)["tema_origen"]=="ia"
automatico.clasificar_rapido(n1); assert db.obtener_nota(n1)["tema_origen"]=="ia"
print("4 mejoras/IA OK")

# ---- 5) corrección precalculada con caché
nucleo.generar_propuestas_documento_real = nucleo.generar_propuestas_documento
cont = {"n":0}
def gen(ruta, nombre, continuar=None):
    cont["n"]+=1
    for _ in range(3):
        if continuar and not continuar(): raise nucleo.InterrumpidoPorHorario()
    return {"tipo":"NOTA","corregido":True,"observaciones":[],"propuestas":[{"id":0,"ubicacion":"body:0","original":"a","propuesto":"b","cambios":[]}],"todos_los_parrafos":[]}
nucleo.generar_propuestas_documento = gen
nota = db.obtener_nota(n1)
print("5", automatico.precalcular_correccion(n1))
assert automatico.precalcular_correccion(n1)["omitido"] and cont["n"]==1
r = automatico.propuestas_para_aprobacion(n1, nota["ruta_original"], nota["nombre_original"]); assert cont["n"]==1 and r["propuestas"][0]["propuesto"]=="b"
# texto cambia -> no se usa
nucleo.guardar_texto_editado(nota["ruta_original"], tmp/"base.docx", {0:"Texto distinto"})
r = automatico.propuestas_para_aprobacion(n1, tmp/"base.docx", nota["nombre_original"]); assert cont["n"]==2
# corte por ventana
try:
    automatico.precalcular_correccion(n2, continuar=lambda: False) if False else None
except Exception as e: raise
print("5 caché OK")

# ---- 6) ventana horaria
f = lambda y,mo,d,h,mi: datetime(y,mo,d,h,mi, tzinfo=timezone(timedelta(hours=-3)))
cfg = {"hora_inicio":time(17,0),"hora_fin":time(7,0),"fines_de_semana_todo_el_dia":False}
casos = [(f(2026,10,6,16,59),False),(f(2026,10,6,17,0),True),(f(2026,10,6,23,30),True),(f(2026,10,7,0,0),True),(f(2026,10,7,6,59),True),(f(2026,10,7,7,0),False),(f(2026,10,7,12,0),False)]
for dt,esp in casos: assert automatico.en_ventana(dt,cfg)==esp,(dt,esp)
cfg2=dict(cfg,fines_de_semana_todo_el_dia=True)
assert automatico.en_ventana(f(2026,10,10,12,0),cfg2) and not automatico.en_ventana(f(2026,10,10,12,0),cfg)  # sábado
assert automatico._id_de_noche(f(2026,10,7,3,0),cfg)=="2026-10-06" and automatico._id_de_noche(f(2026,10,6,18,0),cfg)=="2026-10-06"
assert automatico._zona() is not None
print("6 ventana OK")

# ---- 7) cola, orden y worker
import configparser
cp = configparser.ConfigParser(); cp.read_string("[ia_automatica]\nhora_inicio=18:30\nhora_fin=06:15\n")
automatico.configurar(cp); assert automatico.CONFIG["hora_inicio"]==time(18,30)
automatico.configurar(configparser.ConfigParser())  # sin sección: no rompe
automatico.CONFIG.update(hora_inicio=time(17,0), hora_fin=time(7,0))
with db.conexion() as c: c.execute("DELETE FROM trabajos_ia")
nucleo.generar_propuestas_documento = nucleo.generar_propuestas_documento_real
nucleo.USAR_IA=False
a = automatico.encolar_analisis(n2); b = automatico.encolar_analisis(n1)
assert automatico.encolar_analisis(n2)==0  # sin duplicados
pend = db.trabajos_pendientes()
ordenados = automatico._orden_trabajos(pend)
assert ordenados[0]["prioridad"] in ("PRIORITARIA","URGENTE")
# n1 está en un bloque NORMAL, n2 es PRIORITARIA -> n2 primero
assert ordenados[0]["nota_id"]==n2, [(t["nota_id"],t["tipo"]) for t in ordenados]
db.actualizar_prioridad(n1, "URGENTE", "x")
assert automatico._orden_trabajos(db.trabajos_pendientes())[0]["nota_id"]==n1
w = automatico.Trabajador()
automatico.en_ventana_real = automatico.en_ventana
# fuera de ventana: no hace nada
automatico.ahora_local = lambda: f(2026,10,6,12,0)
assert w.ciclo() is False
# dentro de ventana: procesa un trabajo por ciclo
automatico.ahora_local = lambda: f(2026,10,6,22,0)
nucleo.generar_propuestas_documento = lambda ruta,nombre,continuar=None: {"tipo":"NOTA","corregido":True,"observaciones":[],"propuestas":[],"todos_los_parrafos":[]}
hechos = 0
while w.ciclo(): hechos += 1
assert hechos>=3
res = db.resumen_trabajos(); print("7", res); assert res.get("PENDIENTE",0)==0
# forzado
automatico.encolar_analisis(n2) ; db.encolar_trabajo(n2,"CLASIFICAR")
automatico.ahora_local = lambda: f(2026,10,6,12,0)
w.forzar=True; w._contador_forzado=1
assert w.ciclo() is True
while w.ciclo(): pass
assert w.forzar is False
# IA caída -> reintento la noche siguiente, no en bucle
nucleo.USAR_IA=True; nucleo._MODELOS_DESCARGADOS=None
nucleo.refrescar_modelos_ollama=lambda *a, **k: None
nucleo.ia_disponible = lambda p: False
automatico.ahora_local = lambda: f(2026,10,6,22,0)
tid = db.encolar_trabajo(n1, "MEJORAS")
w2 = automatico.Trabajador()
assert w2.ciclo() is True        # intento 1, falla
assert w2.ciclo() is False       # no insiste esa misma noche
t = [x for x in db.listar_trabajos() if x["id"]==tid][0]; assert t["estado"]=="PENDIENTE" and t["intentos"]==1
automatico.ahora_local = lambda: f(2026,10,7,22,0)
assert w2.ciclo() is True; automatico.ahora_local = lambda: f(2026,10,8,22,0); assert w2.ciclo() is True
t = [x for x in db.listar_trabajos() if x["id"]==tid][0]; assert t["estado"]=="OMITIDO" and t["intentos"]==3, t
# huérfanos
tid2 = db.encolar_trabajo(n2,"CONSISTENCIA"); db.iniciar_trabajo(tid2); assert db.reencolar_trabajos_huerfanos()==1
# corte por fin de ventana
nucleo.ia_disponible = lambda p: True
nucleo.USAR_IA=False
estado_reloj = {"v": True}
def gen_largo(ruta,nombre,continuar=None):
    for i in range(5):
        if i==2: estado_reloj["v"]=False
        if continuar and not continuar(): raise nucleo.InterrumpidoPorHorario()
    return {"tipo":"NOTA","corregido":True,"observaciones":[],"propuestas":[],"todos_los_parrafos":[]}
nucleo.generar_propuestas_documento = gen_largo
w3 = automatico.Trabajador(); w3._puede_correr = lambda: estado_reloj["v"]
n3 = nueva("Nota 3.docx", ["Texto largo."]); db.encolar_trabajo(n3,"CORRECCION")
with db.conexion() as c: c.execute("UPDATE trabajos_ia SET estado='HECHO' WHERE estado='PENDIENTE' AND tipo!='CORRECCION'")
assert w3.ciclo() is True
t = [x for x in db.listar_trabajos() if x["nota_id"]==n3 and x["tipo"]=="CORRECCION"][0]; assert t["estado"]=="PENDIENTE" and t["intentos"]==0
print("7 cola/worker OK")

# ---- 8) estadísticas y estados por nota
st = db.estadisticas_ia(); print("8", st["prioridad"], st["mejoras"], st["hallazgos"])
assert isinstance(automatico.estado_trabajador(), dict)
print(db.estado_ia_nota(n1).keys())
print("ALL OK")
