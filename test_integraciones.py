"""Compatibilidad preparada (sin conexión) con Mesa de Entradas y el almacenamiento PPN."""
import configparser, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mesa_entradas as me, almacen_ppn as al

p = me.parsear
assert p("EP 1234/2025") == {"tipo": "EP", "numero": 1234, "anio": 2025}
assert p("ep-1234-25") == {"tipo": "EP", "numero": 1234, "anio": 2025}
assert p("Expte. EA N° 12/2024") == {"tipo": "EA", "numero": 12, "anio": 2024}
assert p("1234/2025 ET") == {"tipo": "ET", "numero": 1234, "anio": 2025}
assert p("1234/2025") == {"tipo": None, "numero": 1234, "anio": 2025}
assert p("ZZ 1/2025") is None and p("sin número") is None and p("") is None
assert me.formatear(p("ep-7-26")) == "EP 7/2026" and me.formatear(p("7/26")) == "7/2026"
c = me.compatibilidad(["EP 1/2025", "3/2024", "ver carpeta"])
assert (c["completos"], c["sin_tipo"], c["n_ilegibles"]) == (1, 1, 1)
print("formato de expediente OK")

cfg = configparser.ConfigParser(); cfg.read_string("[mesa_entradas]\nurl = https://me.test/\nurl_web = https://me-web.test\ntoken_servicio = abc\n[almacen_ppn]\nurl = https://st.test\nsistema_id = uuid-sis\n")
vacio = configparser.ConfigParser()
me.configurar(vacio); al.configurar(vacio)
assert not me.activo() and not al.activo() and al.url_descarga("x.pdf") == ""
for f in (lambda: me.buscar(p("EP 1/2025")), lambda: al.subir(__file__)):
    try: f(); raise AssertionError("debía fallar sin configurar")
    except RuntimeError: pass
me.configurar(cfg); al.configurar(cfg)
assert me.enlace_detalle(5) == "https://me-web.test/dashboard/expediente/detalle/5"

class R:
    def __init__(s, datos, code=200): s.datos, s.status_code = datos, code
    def json(s): return s.datos
    def raise_for_status(s):
        if s.status_code >= 400: raise RuntimeError(s.status_code)
class Sesion:
    def __init__(s): s.llamadas = []
    def post(s, url, **k):
        s.llamadas.append((url, k))
        if "files" in k and url.endswith("/graphql"):
            ops = json.loads(k["data"]["operations"]); assert ops["variables"]["recordId"] == 9 and json.loads(k["data"]["map"]) == {"0": ["variables.file"]}
            return R({"data": {"createDocument": True}})
        if url.endswith("/graphql"):
            assert k["json"]["variables"] == {"number": 1234, "year": 2025, "typeId": 12} and k["headers"]["authorization"] == "abc"
            return R({"data": {"recordsWithFilter": [{"id": 9, "number": "1234", "year": 2025}]}})
        assert url == "https://st.test/api/file/" and k["headers"] == {"system-identifier-authorization": "uuid-sis"} and "file" in k["files"]
        return R({"filename": "u-1.pdf", "size": 3})
    def get(s, url, **k): return R({"freeStorageSize": 10})
    def delete(s, url, **k): return R({}, 200)
ses = Sesion()
assert me.buscar(p("EP 1234/2025"), ses)[0]["id"] == 9
pdf = Path(tempfile.mkdtemp()) / "n.pdf"; pdf.write_bytes(b"%PDF-1.4")
assert me.adjuntar_pdf(9, pdf, "Nota 12/2026", ses) is True
assert al.subir(pdf, ses) == "u-1.pdf" and al.url_descarga("u-1.pdf") == "https://st.test/api/file/uuid-sis/u-1.pdf"
assert al.estado(ses)["freeStorageSize"] == 10 and al.eliminar("u-1.pdf", ses)
print("consultas preparadas OK")
print("ALL OK")
