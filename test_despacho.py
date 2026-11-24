"""Despacho: números de nota/expediente insertados en copia + PDF."""
import sys, tempfile, ast, logging
from pathlib import Path
APP = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, APP)
import docx
import database as db
import auth, nucleo, despacho, respaldo, integridad
tmp = Path(tempfile.mkdtemp())
db.RUTA_DB = tmp / "t.db"
respaldo.CONFIG["activo"] = False
db.inicializar_db()

def mk(nombre, parrafos):
    d = docx.Document()
    for p in parrafos: d.add_paragraph(p)
    r = tmp / nombre; d.save(str(r)); return r

# 1) ubicar y completar líneas existentes
f = mk("f.docx", ["Nota N° ____", "Expte. N°:", "Buenos Aires, 1 de enero", "Nota N° 5 del año pasado dice otra cosa.",
                  "Ver Expte. Nro. 12.345/26 y también expediente 12.345/26."])
av = despacho.completar_documento(f, tmp / "o.docx", "123/26", "12.345/26")
t = [p.text for p in docx.Document(str(tmp / "o.docx")).paragraphs]
assert t[0] == "Nota N° 123/26" and t[1] == "Expte. N°: 12.345/26", t
assert t[3].startswith("Nota N° 5 del año"), "no debe tocar oraciones"
assert av == [], av
assert [p.text for p in docx.Document(str(f)).paragraphs][0] == "Nota N° ____", "el firmado queda intacto"
print("1 completar líneas OK")

# 2) sin líneas: se agregan arriba y avisa
g = mk("g.docx", ["Texto sin encabezado"])
av = despacho.completar_documento(g, tmp / "o2.docx", "9", "8")
t = [p.text for p in docx.Document(str(tmp / "o2.docx")).paragraphs]
assert t[0] == "Nota N° 9" and t[1] == "Expte. N° 8" and len(av) == 2, (t, av)
print("2 sin líneas OK")

# 3) validación y propuesta
assert despacho.validar_numero("", "número de nota")[1]
assert despacho.validar_numero("12<script>", "número de nota")[1]
assert despacho.validar_numero(" 12 / 26 ", "número de nota") == ("12 / 26", None)
assert despacho.proponer_expediente(f"Expte. N° 111-2 ... Expediente 111-2. y ex 999/1") == "111-2"
assert despacho.proponer_expediente("sin nada") == ""
print("3 validación/propuesta OK")

# 4) PDF (LibreOffice si está)
try:
    how = despacho.convertir_a_pdf(tmp / "o.docx", tmp / "o.pdf")
    assert (tmp / "o.pdf").read_bytes()[:4] == b"%PDF"; print("4 PDF OK con", how)
except RuntimeError as e:
    print("4 PDF omitido (sin conversor):", str(e)[:80])

# 5) campos pendientes: Nota/Expte en blanco no cuentan
assert nucleo.buscar_campos_pendientes("Nota N° ____\nExpte. N°: [ ]\nTexto") == []
assert nucleo.buscar_campos_pendientes("Hola XXXX") != []
print("5 campos pendientes OK")

# 6) base: duplicados, preparación, limpieza al re-firmar
eq = db.crear_equipo("A")
n1 = db.crear_nota("Nota A.docx", eq, f); n2 = db.crear_nota("Nota B.docx", eq, g)
for n in (n1, n2):
    db.actualizar_estado(n, "FIRMADA", ruta_firmada=str(f), sha256_firmado=integridad.sha256_archivo(f))
db.guardar_despacho_preparado(n1, "77/26", "1", tmp / "o.docx", None, integridad.sha256_archivo(tmp / "o.docx"))
assert db.numero_de_nota_en_uso("77/26", excluir_id=n2) == n1
assert db.numero_de_nota_en_uso("77/26", excluir_id=n1) is None
assert db.numero_de_nota_en_uso("78/26") is None
db.actualizar_estado(n1, "PROCESADA"); db.marcar_firmada(n1, f, integridad.sha256_archivo(f))
assert db.obtener_nota(n1)["ruta_despacho_docx"] is None and db.obtener_nota(n1)["nro_nota"] == "77/26"
print("6 base OK")

# 7) helpers de main.py
src = open(APP + "/main.py", encoding="utf-8").read(); tree = ast.parse(src)
class HTTPExc(Exception):
    def __init__(self, status_code, detail=None): self.status_code, self.detail = status_code, detail
ns = dict(db=db, despacho=despacho, integridad=integridad, Path=Path, HTTPException=HTTPExc, nucleo=nucleo)
for nodo in tree.body:
    if isinstance(nodo, ast.FunctionDef) and nodo.name in ("_despacho_preparado", "_exigir_preparado", "_validar_numeros"):
        exec(compile(ast.Module([nodo], []), "main", "exec"), ns)
a, b = db.obtener_nota(n1), db.obtener_nota(n2)
l, e = ns["_validar_numeros"]([(a, "5", ""), (b, "5", "")], None)
assert any("se repite" in x for x in e), e
l, e = ns["_validar_numeros"]([(a, "77/26", ""), (b, "77/26", "")], None)
assert any("ya está en uso" in x or "se repite" in x for x in e)
l, e = ns["_validar_numeros"]([(a, "", ""), (b, "6", "bad<>")], None)
assert len(e) == 1 and "expediente" in e[0] and l[0][1] == "", "el número de nota es opcional; el expediente inválido sí frena"
l, e = ns["_validar_numeros"]([(a, "1", "x1"), (b, "2", "")], None)
assert not e, e
assert ns["_exigir_preparado"]([b]) == [b["nombre_original"]], "sin número se puede despachar: se informa"
db.guardar_despacho_preparado(n2, "2", "", tmp / "o2.docx", None, integridad.sha256_archivo(tmp / "o2.docx"))
ns["_exigir_preparado"]([db.obtener_nota(n2)])
(tmp / "o2.docx").write_bytes(b"alterado")
try: ns["_exigir_preparado"]([db.obtener_nota(n2)]); raise SystemExit("debió detectar alteración")
except HTTPExc: pass
print("7 helpers main OK")

# 8) plantillas
import jinja2
env = jinja2.Environment(loader=jinja2.FileSystemLoader(APP + "/templates"))
env.globals["ico"] = __import__("iconos").svg
env.filters["hora"] = lambda v: str(v)
env.globals.update(es_admin=lambda u: False, hoy_local=lambda: "2026-01-01", ROLES_MESA=auth.ROLES_MESA, funciones_mesa=auth.funciones_mesa, mesa_excepcional=auth.mesa_excepcional,
                   datos_envio=lambda n: dict(propuesto="", contacto=None, contactos=[], envios=[]),
                   datos_despacho=lambda n: dict(nro_nota="", nro_expte="12/3", expte_propuesto=True, preparado=False, tiene_pdf=False, lleva_numero=True))
class St: pass
class Req: pass
st = St(); st.usuario = dict(rol=auth.ROL_MESA_JEFE, id=1); rq = Req(); rq.state = st
txt = env.get_template("_estado_nota.html").render(request=rq,
    nota=dict(id=1, estado="FIRMADA", bloque_id=None, nombre_original="x", etapa=None, alerta=None, observaciones=None),
    etapas=[])
assert "Números para el despacho" in txt and 'value="12/3"' in txt and "Marcar como despachada sin número" in txt, "sin número también se puede despachar"
print("8 plantilla OK")
print("ALL OK")

# Word 365: «SaveAs» no está disponible por acceso dinámico (error «Open.SaveAs»): se usa ExportAsFixedFormat / SaveAs2
class _DocNuevo:
    def __init__(self): self.hecho = []
    def __getattr__(self, n):
        if n == "SaveAs": raise AttributeError("Open.SaveAs")
        raise AttributeError(n)
    def ExportAsFixedFormat(self, destino, fmt): self.hecho.append(("pdf", fmt))
    def SaveAs2(self, destino, fmt): self.hecho.append(("saveas2", fmt))
d_ = _DocNuevo(); despacho.guardar_como_word(d_, tmp / "x.pdf", 17); assert d_.hecho == [("pdf", 17)]
d_ = _DocNuevo(); despacho.guardar_como_word(d_, tmp / "x.docx", 16); assert d_.hecho == [("saveas2", 16)]
class _DocViejo:
    def __init__(self): self.hecho = []
    def SaveAs(self, destino, fmt): self.hecho.append(("saveas", fmt))
d_ = _DocViejo(); despacho.guardar_como_word(d_, tmp / "x.docx", 16); assert d_.hecho == [("saveas", 16)]
print("PDF con Word nuevo OK")
