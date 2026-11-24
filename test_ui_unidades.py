"""Modificaciones 3 y 3B (ABM de Unidades): alta plegable con «Agregar unidad», el formulario queda abierto y con lo cargado si la
validación falla, y el nombre de cada fila enlaza siempre a /unidades/{id}/editar, también con filtros en la URL."""
import re
import arnes_ui as a
from arnes_ui import R, req, run, body, falla, direc, ana
import database as db

listar = R[("GET", "/unidades")]
editar = R[("GET", "/unidades/{uid:int}/editar")]
guardar = R[("POST", "/unidades")]

# ---------------------------------------------------------------- 3: sin panel fijo; «Agregar unidad» despliega el alta
html = body(listar(req(direc, "/unidades")))
assert re.search(r'<details class="alta-unidad" id="alta-unidad">\s*<summary class="boton boton--primario alta-unidad__boton">', html), "alta plegada"
assert "Agregar unidad</summary>" in html
assert "<script>" not in html[html.index("<title>"):html.index("</title>")], "el título de la pestaña no lleva código"
assert html.count('class="atenuado"') == 0, "las unidades activas no se ven atenuadas"
falla(lambda: listar(req(ana, "/unidades")), 403)

# alta que falla por validación: el formulario queda abierto y conserva lo cargado
r = run(guardar(req(direc, "/unidades", ses={}, form={"nombre": "Unidad de prueba Norte", "ciudad": "Salta", "correo": "no-es-correo", "filtros": "q=x"})))
html = body(r)
assert '<details class="alta-unidad" id="alta-unidad" open>' in html and "El correo no es válido" in html
assert 'value="Unidad de prueba Norte"' in html and 'value="Salta"' in html and 'value="no-es-correo"' in html
assert not [u for u in db.listar_unidades(solo_activas=False) if u["nombre"] == "Unidad de prueba Norte"], "no se guardó nada"
# alta correcta: vuelve al listado con los filtros
ses = {}
r = run(guardar(req(direc, "/unidades", ses=ses, form={"nombre": "Unidad de prueba Norte", "ciudad": "Salta", "provincia": "Salta",
                                                       "filtros": "q=Norte&tipo=&provincia=&inactivas=0&fuerza_id=&delegacion=&pag=1"})))
assert r.status_code == 303 and r.headers["location"].startswith("/unidades?q=Norte") and ses["flash"] == "Unidad guardada."
nueva = next(u for u in db.listar_unidades(solo_activas=False) if u["nombre"] == "Unidad de prueba Norte")
print("Modificación 3 OK")

# ---------------------------------------------------------------- 3B: con filtros en la URL, el nombre enlaza a la edición
for args in (("Norte",), ("Norte", "", "Salta"), ("", "", "", 1)):
    html = body(listar(req(direc, "/unidades"), *args))
    enlaces = re.findall(r'<a class="unidad-enlace" href="/unidades/(\d+)/editar\?([^"]*)">([^<]+)</a>', html)
    assert enlaces, ("sin enlaces de edición", args)
    filas = re.findall(r"<tr[^>]*><td>", html)
    assert len(enlaces) == len(filas), "cada fila tiene su enlace"
assert (str(nueva["id"]), "Unidad de prueba Norte") in [(i, n) for i, _, n in re.findall(r'href="/unidades/(\d+)/editar\?([^"]*)">([^<]+)</a>', body(listar(req(direc, "/unidades"), "Norte")))]
assert "q=Norte" in re.search(r'href="/unidades/\d+/editar\?([^"]*)"', body(listar(req(direc, "/unidades"), "Norte"))).group(1), "conserva los filtros"
# la edición: formulario visible con los datos, conserva los filtros, 404 y permisos
html = body(editar(req(direc, f"/unidades/{nueva['id']}/editar"), nueva["id"], "Norte"))
assert "Editar «Unidad de prueba Norte»" in html and f'name="id" value="{nueva["id"]}"' in html and 'id="alta-unidad"' not in html
assert 'href="/unidades?q=Norte' in html, "«Cancelar» vuelve al listado filtrado"
falla(lambda: editar(req(direc, "/x"), 999999), 404)
falla(lambda: editar(req(ana, "/x"), nueva["id"]), 403)
# edición que falla: vuelve a la edición con lo cargado
html = body(run(guardar(req(direc, "/unidades", ses={}, form={"id": str(nueva["id"]), "nombre": "Unidad de prueba Norte", "lat": "abc"}))))
assert "Editar «Unidad de prueba Norte»" in html and "La latitud y la longitud tienen que ser números" in html and 'value="abc"' in html
print("Modificación 3B OK")
print("TODO OK")
