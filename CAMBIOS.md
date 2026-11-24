# Cambios por versión

## v41.3 — primera versión en GitHub (19/11/2026)
Base: lo instalado en la PC de la DGPDH el 16/11/2026 (v41c), más las correcciones del informe de Sistemas del 17/11/2026.

- **Despacho del día y firma de algunas notas colgaban el servidor** (hallazgo prioritario del informe): la búsqueda del
  número de expediente (`despacho.proponer_expediente`) tardaba horas con textos que traen «ex» seguido de miles de
  espacios, y congelaba todo el proceso. Corregido en v41c junto con otras cinco búsquedas con el mismo defecto
  (`pruebas/test_regex_sin_cuelgues.py`).
- `requirements.txt`: se agrega `setuptools` (language-tool-python 2.8 no carga sin él en Python 3.12).
- `linux/exportar_datos.py`: busca LanguageTool también donde lo guarda la librería en Windows, y deja el manifiesto
  además al lado del .zip.
- Con `[ia_automatica] activa = false` el registro ahora dice «DESACTIVADO» en vez de «iniciado».
- `linux/verificar_servidor.py`: reconoce la zona horaria en Ubuntu 26.04 (sin /etc/timezone).
- Nuevo `config.ini.ejemplo` con todas las opciones.

**¿Cambió config.ini?** No es obligatorio tocarlo. Hay secciones nuevas optativas (ver `config.ini.ejemplo`).
**¿Cambió requirements.txt?** Sí: `setuptools`. Correr `pip install -r requirements.txt`.
**¿Migraciones?** Las de v41 (0014–0019, 0026–0029, 0040–0041, 0050–0054), si el servidor todavía no las tiene; se aplican solas.
**¿Servidor?** No.
