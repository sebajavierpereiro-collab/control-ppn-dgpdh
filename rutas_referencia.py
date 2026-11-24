"""Pantalla de los documentos de referencia de la IA (Administración → IA → Documentos de referencia).

  /ia/referencia ................... carpetas y subcarpetas, documentos de cada una, términos que se respetan, registro
  /ia/referencia/carpeta ........... crear una subcarpeta (nombres distintos dentro de una misma carpeta)
  /ia/referencia/subir ............. subir documentos o una carpeta entera con sus subcarpetas (se conserva la estructura)
  /ia/referencia/revisar ........... leer ya lo que se copió a mano en la carpeta
  /ia/referencia/doc/{id}/retirar .. sacar un documento sin borrarlo (va a _retirados)
Solo la Dirección General. Se registra desde main.py con `registrar(app, ctx)`.
"""
import json
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import auth
import referencia_ia as ref


def registrar(app, ctx):
    templates, usuario_actual, flash = ctx["templates"], ctx["usuario_actual"], ctx["flash"]

    def exigir(request):
        u = usuario_actual(request)
        if not auth.es_admin(u):
            raise HTTPException(status_code=403, detail="Solo la Dirección General administra los documentos de referencia.")
        return u

    def volver(carpeta, mensaje, request):
        flash(request, mensaje)
        return RedirectResponse(url="/ia/referencia" + (f"?carpeta={quote(carpeta)}" if carpeta else ""), status_code=303)

    @app.get("/ia/referencia", response_class=HTMLResponse)
    def ver_referencia(request: Request, carpeta: str = "", reorden: str = ""):
        u = exigir(request)
        try:
            actual = ref.contenido(carpeta)
        except ref.ErrorReferencia as e:
            flash(request, f"⚠ {e}")
            actual = ref.contenido("")
        propuesta = None
        if reorden:
            try:
                propuesta = ref.propuesta_reorden(reorden)
            except ref.ErrorReferencia as e:
                flash(request, f"⚠ {e}")
        return templates.TemplateResponse("ia_referencia.html", {
            "request": request, "usuario": u, "arbol": ref.arbol(), "actual": actual, "resumen": ref.resumen(),
            "estados": ETIQUETAS_ESTADO, "propuesta": propuesta})

    @app.post("/ia/referencia/doc/{doc_id:int}/eliminar")
    async def eliminar_doc(request: Request, doc_id: int):
        u = exigir(request)
        d = ref.obtener(doc_id)
        if not d:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        try:
            ref.eliminar(doc_id, u["nombre"])
        except ref.ErrorReferencia as e:
            return volver(d["carpeta"], f"⚠ {e}", request)
        return volver(d["carpeta"], f"«{d['nombre']}» eliminado.", request)

    @app.post("/ia/referencia/limpiar")
    async def limpiar(request: Request):
        u = exigir(request)
        f = await request.form()
        if f.get("que") == "vacias":
            n = ref.eliminar_carpetas_vacias(u["nombre"])
            return volver("", f"{n} carpeta(s) vacía(s) eliminada(s)." if n else "No había carpetas vacías.", request)
        r = ref.limpiar(incluir_repetidos=f.get("repetidos") == "1", usuario=u["nombre"])
        return volver("", f"Se eliminaron {r['archivos']} archivo(s) que no servían y {r['carpetas']} carpeta(s) vacía(s).", request)

    @app.post("/ia/referencia/reordenar")
    async def reordenar(request: Request):
        u = exigir(request)
        f = await request.form()
        try:
            r = ref.aplicar_reorden(f.get("criterio") or "", u["nombre"])
        except ref.ErrorReferencia as e:
            return volver("", f"⚠ {e}", request)
        return volver("", f"Listo: {r['movidos']} documento(s) reordenado(s) por {ref.CRITERIOS[f.get('criterio')].split(' (')[0].lower()}"
                          + (f"; {r['carpetas']} carpeta(s) vacía(s) eliminada(s)." if r["carpetas"] else "."), request)

    @app.post("/ia/referencia/carpeta")
    async def crear_carpeta(request: Request):
        u = exigir(request)
        f = await request.form()
        padre = f.get("padre") or ""
        try:
            rel = ref.crear_carpeta(padre, f.get("nombre") or "", u["nombre"])
        except ref.ErrorReferencia as e:
            return volver(padre, f"⚠ {e}", request)
        return volver(rel, f"Carpeta «{rel.split('/')[-1]}» creada correctamente.", request)

    @app.post("/ia/referencia/subir")
    async def subir(request: Request):
        u = exigir(request)
        f = await request.form()
        carpeta = f.get("carpeta") or ""
        archivos = [a for a in f.getlist("archivos") if getattr(a, "filename", "")]
        try:
            rutas = json.loads(f.get("rutas") or "[]")
        except ValueError:
            rutas = []
        guardados, errores = [], []
        for i, a in enumerate(archivos):
            ruta = rutas[i] if i < len(rutas) and rutas[i] else a.filename
            try:
                guardados.append(ref.guardar_archivo(carpeta, ruta, await a.read(), u["nombre"]))
            except ref.ErrorReferencia as e:
                errores.append(str(e))
        if guardados:
            ref._registrar("SUBIR", f"{len(guardados)} documento(s) en «{carpeta or 'carpeta principal'}»", u["nombre"])
            ref.escanear_en_segundo_plano(u["nombre"])
        mensaje = (f"Se agregaron {len(guardados)} documento(s) correctamente; se están leyendo." if guardados else "⚠ No se agregó ningún documento.")
        if errores:
            mensaje += " No se agregaron: " + " ".join(errores[:5]) + (f" (y {len(errores) - 5} más)" if len(errores) > 5 else "")
        if request.headers.get("x-requested-with") == "fetch":
            flash(request, mensaje)
            return JSONResponse({"ok": bool(guardados), "guardados": len(guardados), "errores": errores})
        return volver(carpeta, mensaje, request)

    @app.post("/ia/referencia/revisar")
    async def revisar(request: Request):
        u = exigir(request)
        f = await request.form()
        ref.escanear_en_segundo_plano(u["nombre"])
        return volver(f.get("carpeta") or "", "Se está revisando la carpeta: en un momento aparecen los documentos nuevos.", request)

    @app.post("/ia/referencia/doc/{doc_id:int}/retirar")
    async def retirar(request: Request, doc_id: int):
        u = exigir(request)
        d = ref.obtener(doc_id)
        if not d:
            raise HTTPException(status_code=404, detail="Documento no encontrado")
        try:
            ref.retirar(doc_id, u["nombre"])
        except ref.ErrorReferencia as e:
            return volver(d["carpeta"], f"⚠ {e}", request)
        return volver(d["carpeta"], f"«{d['nombre']}» retirado de la referencia (se guardó en _retirados, no se borró).", request)


ETIQUETAS_ESTADO = {
    "INDEXADO": ("ok", "En uso"), "PENDIENTE": ("aviso", "Leyendo…"), "SIN_TEXTO": ("aviso", "Sin texto legible"),
    "DUPLICADO": ("neutro", "Repetido"), "ERROR": ("alto", "No se pudo leer"), "AUSENTE": ("neutro", "Ya no está en la carpeta"),
}
