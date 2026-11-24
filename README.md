# Notas DGPDH — app web (Etapa 1)

Envuelve el script de corrección y firma de notas en una aplicación web.
Un usuario, sin login todavía (llega en la Etapa 2 del plan), pero el
modelo de datos ya incluye "equipo" para no tener que migrar después.

## Qué hace y qué no hace (todavía)

- El asesor sube el documento, pero **la corrección no arranca sola**:
  queda esperando a que Coordinación la apruebe ("Aprobar y enviar a
  corrección") o la rechace. Recién al aprobarla se dispara el análisis
  automático (diccionario + LanguageTool + IA, solo si el nombre contiene
  "Nota"; el resto va directo a firma sin tocarse).
- Cuando el análisis termina, el Director ve el documento completo en dos
  columnas (original | propuesto) en `/nota/{id}/revisar`, con cada párrafo
  cambiado como **Aceptar**, **Modificar** (edita el texto a mano) o
  **Rechazar** (mantiene el original). Los párrafos sin cambios se ven en
  una sola columna, como un documento continuo.
- El **rechazo de la nota completa** (criterio operativo — la nota no
  corresponde, está mal dirigida, etc.) es una acción aparte, separada de
  las decisiones de redacción, disponible tanto en la pantalla de
  Coordinación como en la del Director. Requiere un motivo.
- La firma y el despacho siguen siendo acciones separadas y manuales desde
  la pantalla de cada documento.
- El progreso se ve en 4 etapas (Revisión inicial → Nota a la firma de
  DGPDH → Nota firmada a despacho → Nota despachada) más dos estados fuera
  del flujo normal (`ERROR`, `RECHAZADA`).
- **El original nunca se borra ni se sobrescribe**: se archiva apenas se
  sube en `datos/originales/<AAAA>/<MM>/`, con el nombre original
  conservado (prefijado por el id de la nota). Es el repositorio del
  asesor — está siempre disponible para descargar desde cada documento,
  pase lo que pase con las correcciones.
- **Aprendizaje por patrones** (`/aprendizaje/patrones`): cuando el
  Director modifica una propuesta, se registra qué sacó y qué puso. Si el
  mismo cambio se repite varias veces entre notas distintas, aparece ahí
  como patrón sugerido para sumar al diccionario — con un botón para
  aprobarlo. No cambia nada solo; necesita que alguien lo apruebe. Las
  decisiones de "rechazar nota completa" no se usan para esto, porque son
  un criterio operativo, no de redacción.
- **Reporte** (`/reportes`, con descarga en `.csv`): total de archivos,
  total de Notas, notas aprobadas/modificadas/rechazadas/en curso, y los
  tres tiempos promedio pedidos (subida → aprobación de Coordinación,
  demora en firma, demora total hasta el despacho). Se puede filtrar por
  fecha.
- **No** valida usuarios/permisos todavía — eso es la próxima etapa
  (login + roles: Usuario de equipo, Coordinador, Director General, Mesa
  de Entrada). Mientras tanto, "Aprobar y enviar a corrección" y "Marcar
  como despachada" quedan disponibles para cualquiera que abra la nota,
  como placeholder de esos roles.
- **No** genera número de expediente/nota ni envía correos — quedó definido
  como actividad manual de Mesa de Entrada.

## Requisitos

- Windows, con Microsoft Word instalado (necesario solo si vas a subir
  archivos `.doc` viejos; los `.docx` no lo necesitan).
- Python 3.11+.
- Java instalado (lo pide `language_tool_python` para correr LanguageTool
  en local).
- [Ollama](https://ollama.com) corriendo en la misma máquina, con el
  modelo configurado descargado (por defecto `qwen2.5:7b`):
  `ollama pull qwen2.5:7b`
  Si no está disponible, la app sigue funcionando: se omite la corrección
  por IA y quedan el diccionario y LanguageTool.

## Instalación

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Configuración

La primera vez que arranca, se crea un `config.ini` al lado de `main.py`
con valores por defecto. Ahí se edita:

- `[firma] nombre_firmante` y `archivo_firma` — el nombre a buscar en el
  bloque de firma y la ruta a la imagen de la firma.
- `[ollama] url`, `modelo`, `usar_ia` — para apagar la IA sin tocar código,
  poné `usar_ia = false`.

## Cómo correrla

Con doble clic en `iniciar.bat` (arranca la app y la reinicia sola si se cae), o a mano:

```
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Abrir `http://localhost:8000` (o `http://<ip-de-la-máquina>:8000` desde otra PC de la red).

**Un solo proceso.** No uses `--workers N`: la cola de análisis, el respaldo y la ventana nocturna
corren en un hilo de la app y deben existir una sola vez. Si por error se arrancan dos procesos, el
segundo se da cuenta (archivo `datos/trabajador.lock`), no arranca su trabajador y lo avisa en el log.

**Primer ingreso.** Usuario `director`, clave `director123`: el sistema obliga a cambiarla antes de seguir.
Las claves que crea Administración también son provisorias y se cambian al primer ingreso.

## Estructura

```
dgpdh_web/
  main.py           endpoints FastAPI
  nucleo.py         lógica de corrección/firma (misma que el script original)
  database.py       SQLite: notas, equipos, historial de estados
  templates/        HTML (Jinja2 + HTMX para el refresco de estado)
  static/estilos.css  hoja de estilos única (base + tema PPN + sistema de diseño)
  static/interfaz.js  ayudas plegables, filas clicables, selector de archivos, horas 24 h
  iconos.py           íconos de línea (función ico() en las plantillas)
  datos/            (se crea sola) originales, procesados, firmados, dgpdh.db
  config.ini        (se crea sola la primera vez)
```

## Próximas etapas (ver plan acordado)

1. ~~Envolver el pipeline en FastAPI~~
2. ~~Pantalla de revisión de cambios (aceptar/modificar/rechazar por
   párrafo, vista en dos columnas)~~
3. ~~SQLite y registro de actividad~~
4. ~~Reordenar el pipeline (aprobación de Coordinación antes de corregir),
   rechazo operativo de nota completa, repositorio histórico de
   originales, aprendizaje por patrones y reporte~~ ← estamos acá
5. Login + roles reales (Usuario de equipo, Coordinador, Director
   General, Mesa de Entrada) y permisos por acción, no por pantalla —
   hoy "Aprobar y enviar a corrección" y "Marcar como despachada" están
   abiertos a cualquiera, como placeholder de esos roles
6. Multiusuario (varios equipos, cola de trabajos real si hace falta)
7. Few-shot con ejemplos de referencia para la IA (Tier 2 del aprendizaje,
   una vez que haya volumen real de modificaciones registradas)

## Análisis automático (clasificación, consistencia, mejoras) y modelos por proceso

- **Modelos por proceso** (`config.ini`, sección `[ollama]`): `modelo_correccion`, `modelo_clasificacion`,
  `modelo_consistencia`, `modelo_redaccion`. Si falta alguno se usa `modelo`. Cada modelo se descarga con
  `ollama pull <nombre>`; Administración → "Inteligencia artificial" muestra cuáles están disponibles y permite probarlos.
  Para las mejoras de redacción se recomienda un modelo de 8B o más.
- **Corrida nocturna** (`[ia_automatica]`): `activa`, `hora_inicio = 17:00`, `hora_fin = 07:00`,
  `fines_de_semana_todo_el_dia`. Hora de Buenos Aires (requiere `tzdata`, ya incluido en requirements.txt).
  Todo documento subido entra a una cola persistente; solo se procesa dentro de la ventana, URGENTE primero.
  Dirección puede usar "Ejecutar la cola ahora" y "Analizar los documentos ya cargados" en Administración.
- **Temas**: `datos/temas.json` (se crea solo) es editable: tema → palabras clave.
- Todo es sugerencia: la prioridad sugerida, las mejoras del informe y los avisos de consistencia los decide una persona.

## Seguridad (auditoría)

- **Permisos**: cada acción verifica rol y pertenencia (equipo/propietario); los patrones sugeridos solo los ve y
  aprueba Dirección; los reportes, Dirección (todo) y Coordinación (solo su equipo).
- **Claves**: mínimo 8 caracteres con letras y números, sin claves típicas; la clave inicial/provisoria es de un solo uso.
- **Ingreso**: 5 intentos fallidos bloquean al usuario 15 minutos (30 por IP); Administración muestra los intentos.
- **CSRF**: se rechazan pedidos de escritura cuyo `Origin`/`Referer` no sea esta misma app. Cookies `SameSite=Lax`,
  sesión de `horas_sesion` (12 por defecto), cabeceras `X-Frame-Options`, `nosniff`, `no-store` en las pantallas.
- **Archivos subidos**: se valida el contenido (firma .docx/.doc y estructura), el tamaño (`max_mb`) y se guarda su huella SHA-256.
- **Cadena de custodia**: huella del original, del procesado y del firmado. No se firma un procesado que cambió desde la
  revisión; Administración → "Verificar integridad" revisa todo el archivo.
- **Trazabilidad**: cada cambio de estado guarda quién lo hizo; se registra quién ve/descarga cada documento.

### HTTPS (no se puede activar solo desde el código)
La app habla HTTP. Para cifrar el tráfico en la red, publicala detrás de un proxy inverso con certificado
(Caddy, IIS+ARR o nginx) apuntando a `127.0.0.1:8000`, y en `config.ini` poné `[seguridad] https = true`
(las cookies pasan a ser solo-HTTPS). Si el proxy está en otra máquina, que reenvíe `Host` / `X-Forwarded-Host`.
Ejemplo mínimo con Caddy: `notas.dgpdh.interno { reverse_proxy 127.0.0.1:8000 }`.

### Cifrado en reposo y permisos de carpeta
Los documentos y la base quedan en `datos/` sin cifrar. Se recomienda cifrar el disco (BitLocker) y que solo la
cuenta que corre la app y Sistemas tengan permisos NTFS sobre `datos/` y sobre la carpeta de respaldos.

## Respaldos y recuperación

`[respaldos]` en `config.ini`: `activo`, `carpeta` (conviene de RED u otro disco), `conservar` (copias de la base),
`hora` (desde esa hora, una vez por día). Copia la base con la API de respaldo de SQLite (consistente aunque la
app esté en uso) y los documentos de forma incremental. Administración permite hacer uno al instante.

**Recuperar**: detener la app → copiar el `.db` elegido sobre `datos/dgpdh.db` (y borrar `dgpdh.db-wal` /
`dgpdh.db-shm`) → copiar la carpeta `documentos/` del respaldo sobre `datos/` → arrancar. Si la carpeta de la
app cambió de lugar, las rutas guardadas se corrigen solas al arrancar.

## Operación

- `GET /salud` responde 200 si la app y la base andan (para un monitoreo externo). Administración muestra espacio en
  disco, tamaño de la base, estado del respaldo y de la IA.
- La base usa modo WAL e índices; el listado se pagina (50 por página) y nunca oculta notas todavía en circuito.
- Word (`.doc` viejos) se usa de a una conversión por vez y con tiempo máximo (120 s) para que un Word colgado no frene la app.
- Fechas: se guardan en UTC y se muestran en hora de Buenos Aires.

## Funciones agregadas por la auditoría

Búsqueda por nombre y por texto (caja del encabezado), avisos internos (campana "Avisos"), versiones de un documento
(subir una corrección enlazada a la anterior y ver qué cambió párrafo a párrafo), pantallas de error claras, usuarios
(editar, reactivar, clave provisoria), y textos de IA protegidos contra instrucciones escondidas en los documentos.

## Retención de datos (decisión pendiente de la institución)
El sistema no borra nada. Definir con Archivo/Legales por cuánto tiempo se conservan originales, firmados y respaldos
y quién autoriza una baja; mientras tanto, `conservar` en `[respaldos]` solo limita las copias de la base.

## Pruebas

Sin servidor ni Word (se prueban base, análisis, cola, ventana nocturna, respaldos, validación de subidas, plantillas y
la protección de cada endpoint):

```
python tests/test_reformas.py
python tests/test_audit.py
```

## Ola 1 de reformas (interfaz y listados)
- Prioridad editable desde el listado (selector en cada fila); la fecha de la nota se define al firmar (por defecto hoy, modificable).
- Barra fija para armar bloques al marcar documentos; opción "Agrupar en un bloque" al subir 2 o más archivos.
- Filtros y orden desde el encabezado de cada columna (el panel superior de filtros se mantiene; se puede plegar).
- Avisos con pestañas (Todos / Nuevos / Leídos), abren el documento en otra pestaña y dicen quién hizo qué.
- Segmentos plegables (el estado se recuerda por navegador), contador de tareas pendientes en "Documentos".
- Administración en pestañas y subpestañas (`/admin/usuarios/nuevo`, etc.); logo de la PPN y buscador con lupa (tecla `/`).
- Tests nuevos: `python tests/test_ola1.py`.

## Ola 2 — tanda A (diseño y flujo del asesor)
- Barra superior nueva (logo + DGPDH, buscador visible, enlaces a la derecha); tablas con scroll propio; botón "Ver".
- Administración: pestañas como botones; "Estado del sistema" (antes "Respaldos y salud"); Aprendizaje lista los reportes erróneos y permite quitarlos.
- Estado nuevo **BORRADOR** ("Sin enviar"): lo que sube un Asesor no llega a Coordinación hasta que lo envía (panel "Sin enviar a Coordinación"). El asesor puede armar bloques, cambiar prioridades y corregir el texto de sus notas mientras Coordinación no las evalúa; si lo hace estando ya enviada, Coordinación recibe un aviso.
- Avisos de consistencia: solo se muestran los que requieren acción. Botones: Aplicar la corrección (con vista previa), Ya lo revisé, Ignorar, **Es un reporte erróneo** (la IA deja de repetirlo y lo usa como ejemplo negativo).
- Avisos con formato «Usuario + acción + documento»; se marcan leídos al abrir la nota; confirmaciones visibles tras cada acción; botones htmx se desactivan mientras actúan.
- Estilos y scripts llevan versión automática (no hace falta Ctrl+F5 tras actualizar).
- Tests: `python tests/test_flujo.py`.

## Ola 2 — tanda B1: despacho con números y PDF
- Mesa de Entrada carga **Nota N°** y **Expte. N°** (el expediente se propone desde el texto) en la ficha de la nota o, para bloques, en `/bloque/{id}/despacho`.
- El sistema inserta los números en una **copia** del firmado (`datos/despacho/{id}/`) y genera el PDF (Word si está; si no, LibreOffice). El firmado original no se toca.
- Validaciones: número de nota obligatorio, sin repetidos (en el sistema ni dentro del bloque), caracteres válidos. Se puede corregir y regenerar hasta despachar.
- No se puede «Marcar como despachada» sin documento generado; si la copia se altera, se pide regenerar.
- Se buscan las líneas «Nota N°» / «Expte. N°» de la plantilla; si no están, se agregan arriba y se avisa.
- Las líneas «Nota N°»/«Expte. N°» en blanco ya no se marcan como campo pendiente.
- Test: `python tests\test_despacho.py`

## Ola 2 — tanda B2/C: envíos, expedientes y actividad
- **Envío (ficha de la nota, Mesa):** destinatario propuesto desde el proveído («Al Sr./A la Dra. …»), agenda de contactos que se completa sola con cada envío registrado, borrador .eml con el PDF adjunto (se abre en Outlook, lo enviás vos) y registro de «enviado por correo» / «entregado en mano». Todo queda en el historial de la nota.
- **Expedientes (`/expedientes`):** lee SOLO LECTURA las planillas .xls/.xlsx de `[expedientes] carpeta` en config.ini (los .xls necesitan `pip install xlrd`). Búsqueda por número, nombre o DNI. Al cargar el expediente en el despacho se avisa si no figura en las planillas. Las consultas se registran y solo Dirección las ve (`/expedientes/consultas`).
- **Actividad (`/actividad`, solo Dirección):** ingresos, primer/último movimiento, tiempo estimado, accesos a documentos y acciones por usuario y día; descarga CSV (abre en Excel).
- Tests: `python tests\test_envios_expedientes.py`
- Pendiente de próximas tandas: IA adaptativa a la carga de la PC, verificador cruzado con tablero de calidad, y las ideas (lote con deshacer, plazos/semáforo, comentarios, delegación, avisos por correo).

## Ola 2 — tanda D: ideas 31, 32 y 41–48
- **IA adaptativa (#31):** `Administración → IA → Calidad`. Con el interruptor activo, el análisis corre cuando la PC tiene capacidad libre (más holgura en horario de oficina); umbrales en `[ia_adaptativa]` de config.ini. Necesita `pip install psutil`; sin él no limita nada.
- **Verificador cruzado (#32):** un segundo modelo (`modelo_verificacion` o el interruptor en Calidad) confirma cada aviso de consistencia de la IA. Los que duda quedan como «ignorados por el verificador» (no molestan al asesor). Tablero por modelo: trabajos, tiempos, avisos útiles / erróneos / ignorados, aceptación de mejoras.
- **Lote con deshacer (#41):** en la barra de selección, «Aplicar prioridad» a varios documentos; aparece «Deshacer» arriba.
- **Atajos del Director (#42):** en la ficha, `J` siguiente / `K` anterior dentro del mismo estado, en orden de urgencia.
- **Plazos (#43):** semáforo (verde / ámbar / rojo / vencida) por plazo según prioridad (URGENTE 2 d, PRIORITARIA 5 d, NORMAL 10 d) o fijado a mano; al vencer se avisa una sola vez a Coordinación del equipo y Dirección.
- **Comentarios internos (#44):** en la ficha, con `@usuario` que avisa (solo a quien puede ver la nota).
- **Biblioteca (#45):** modelos de nota (Word) y glosario; la ficha marca los términos del glosario que aparecen en el texto. Altas y bajas: solo Dirección.
- **Duplicados (#46):** al subir se compara huella del archivo y texto; aviso y registro en el historial (no bloquea).
- **Ausencias (#47):** `Ausencias`: mientras alguien está ausente, sus avisos también le llegan a su delegado (mismo rol). No transfiere permisos.
- **Correo (#48):** apagado por defecto. `Administración → Estado del sistema → Correo` + sección `[correo]` de config.ini. Resumen diario a la hora configurada, o aviso inmediato; correo por persona; prueba de envío.
- Test: `python tests\test_ideas.py`


## Módulo «Autos PPN» (traslados institucionales; antes «Traslados»)

Reemplaza el Excel «Listado de viajes». Vive dentro del mismo sistema y comparte usuarios y el listado de **unidades** con Notas.

- **Después del login** aparece un portal: quien tiene permiso de Notas elige entre Notas y Traslados; el resto entra directo a Traslados (Admin → Editar usuario → «Ve el sistema de notas»). Los choferes nunca ven Notas.
- **Agenda semanal** para todos. Cualquier asesor carga viajes y suma a varios agentes, con chofer o por medios propios (queda en el registro igual). Hasta el **jueves anterior** se cargan los viajes de rutina; después quedan como *inmediatos* con motivo. Recordatorio automático miércoles y jueves.
- **Estados**: un viaje queda confirmado salvo que se cargue cancelación (con motivo) o reprogramación. Con chofer queda «sin asignar» hasta que la coordinación asigna chofer y vehículo (se sugieren autos con plazas suficientes; avisa choques de horario).
- **Coordinación de traslados** (Admin → «Coordina traslados»; también Dirección): asigna choferes y autos, cambia su estado, agrega autos, saca de circulación (nunca elimina), ve vencimientos de VTV/seguro/service/licencias, importa el Excel histórico.
- **Estadísticas**: coordinación y Dirección ven toda la base; coordinadores de equipo, su equipo; asesores, lo propio. Exporta CSV.
- **Auditoría inalterable** de todo cambio (triggers de SQLite impiden modificar o borrar).
- **Unidades** (`/unidades`): nombre, ciudad, provincia, fuerza, correo, dirección; editable, con baja lógica, exportar/importar CSV. En Notas, la unidad de destino propone el correo del envío.
- **Importar el historial**: `Traslados → Importar Excel` (primero «prueba», luego «Guardar»), o `python importar_viajes.py archivo.xlsx --aplicar`. No duplica al repetirse.

Tests: `tests/test_traslados.py` y `tests/test_rutas_traslados.py`.


### Rediseño de Traslados (octubre)
- **Inicio con tarjetas** (Notas, Traslados, Avisos, Actividad reciente) y **tablero de Traslados** (hoy, próximos, solicitudes pendientes, alertas); "Solicitar viaje" es la acción principal y la agenda semanal queda abajo.
- **Dos circuitos**: AMBA (con chofer o auto propio) y Delegaciones (actividades, comisiones, visitas; sin chofer; siempre en la delegación asignada). Se asigna por persona en Administración → Usuarios. Las agendas, filtros y estadísticas no se mezclan; Dirección y la coordinación ven ambos.
- **Participantes**: se cargan los integrantes del equipo; buscador global (nombre, apellido, correo, equipo, delegación); selección múltiple; avatar o iniciales (cada persona sube su imagen en Mi perfil; requiere Pillow).
- **Auto propio por persona**: no ocupa lugar en el vehículo ni pide chofer; si todos van en auto propio el viaje no requiere chofer. Por defecto el viaje es "con chofer".
- **Solo AMBA con chofer**: al elegir un destino de otra zona se deshabilita el chofer (el servidor también lo valida). La zona se edita en Administración → Unidades.
- **Fuera de plazo**: aviso visual y motivo opcional.
- **Destinos por equipo**: Administración → Equipos → unidades habituales (una o varias). Al cargar un viaje se proponen primero esas, luego las más usadas por el equipo (según el historial) y después el resto.
- **Administración**: Unidades (ABM, solo Dirección General), Equipos, Usuarios, etc.
- **Mapa de unidades** (`/traslados/mapa`): mapa interactivo con filtros; usa latitud/longitud si están cargadas y, si no, la ciudad o provincia. Requiere internet para el mapa base (OpenStreetMap).

## Novedades (octubre 2026)

**Traslados**
- Agenda arriba y avisos en una columna lateral; pestaña **Todos** (Dirección y coordinación) que suma AMBA y Delegaciones. Leyenda de colores.
- Horarios precargados 09:00–15:00; «Auto propio» pasa a llamarse **«Por sus propios medios»**; el botón de la agenda dice «Esta semana».
- **Personas sin usuario**: se agregan con nombre, área y observaciones, quedan en un listado reutilizable (aparecen en el buscador con la etiqueta «sin usuario») y se pueden **vincular** a un usuario conservando su historial (`Traslados → Personas sin usuario`, solo coordinación). Los nombres ya cargados se migran solos al iniciar.
- **Buscador de destino** al escribir (nombre, siglas o ciudad): primero las habituales del equipo, después las que más usó el equipo y luego las más usadas en general. En `Administración → Equipos` se **sugieren** destinos habituales por el nombre del equipo (respeta siglas: «CPF I» no sugiere «CPF II»).
- **Viajes frecuentes** (`frecuentes.py`): plantilla con personas, destino, días, horario y frecuencia (semanal, quincenal, mensual, con fecha de fin). Cada semana se **precargan «a confirmar»** los de la semana siguiente; los confirma quien viaja o su coordinación (plazo: jueves anterior). Sin confirmar no cuentan como realizados ni en estadísticas. Incluye «Repetir este viaje», sugerencias por patrones (3 de las últimas 8 semanas) y filtro «Solo frecuentes».
- **Estadísticas**: *desplazamiento* = salida de un vehículo institucional en un día (aunque recorra varias unidades); *viaje a la unidad* = día + unidad + equipo. Los viajes por sus propios medios suman viajes a la unidad pero no desplazamientos.
- **Presentismo** para el área de Personal (`Traslados → Presentismo`): cuadro semanal por persona y Excel con hojas «Semana», «Detalle» y «Sin usuario». Permiso «Área de Personal» en `Administración → Usuarios`.
- «Ubicación de unidades» reemplaza el mapa embebido: listado con enlace a Google Maps y acceso a cargar/completar unidades.

**Notas**
- Pestañas **Mis documentos / Documentos del equipo** con contador de pendientes (asesor: «Mis documentos» por defecto; coordinador: equipo).

Tests nuevos: `tests/test_frecuentes.py`, `tests/test_presentismo.py`, `tests/test_vistas_documentos.py`.

## Plan de reformas — ola 1 completa (octubre 2026)

**Notas**: tarjetas del inicio por perfil (esperan tu acción, para firmar, rechazadas, vencidas, por vencer, sin moverse); historial como **línea de tiempo**; semáforo de **días en la etapa** (`[plazos] dias_etapa_ambar/dias_etapa_rojo`, por defecto 3 y 6); **filtros guardados** como botones; **atajos** (J/K, F firmar, A aprobar, C volver a corregir, D despacho, `?` ayuda); confirmaciones **dentro de la página** (`data-confirmar` / `hx-confirm`, sin ventanas del navegador) y «Deshacer» también para el cambio de prioridad de una nota; **gráficos** en el Reporte; resumen por persona de quién abrió cada documento.
**Sistema**: **modo oscuro** y **tamaño de letra** en «Mi perfil → Apariencia» (por navegador); **guía de estilo** en `/guia-estilo`; cierre por inactividad, revisión trimestral de usuarios, verificación mensual del respaldo y copia externa.
**Traslados**: panel de **flota** (services, aceite, cubiertas, matafuego, VTV, seguro… con semáforo de vencimientos), ficha de vehículo y de chofer, ocupación, línea de tiempo del viaje, motivos de cancelación, contactos de la unidad, «Mi semana», presentismo.

## Sistemas PPN: preparado para vincular, SIN CONECTAR

Por decisión de la Dirección **no** se vincula por ahora. Todo queda listo y apagado (se activa solo completando `config.ini`);
el estado se ve en `Administración → Usuarios → Sistemas PPN (preparación)`.

**Portal de ingreso (`ppn-sso`, `sso.py`).** El portal manda a `<esta app>/auth/ingreso/<token>`; la app pregunta quién es
(`/api/application-session/get-session`, cabeceras `authorization` y `application-id`) y la hace entrar con **su** usuario de acá
(vinculado o con el mismo correo). El portal solo identifica; rol y equipo se deciden acá. Quien no tiene usuario queda como
pedido de acceso. Mientras no se configure, la ruta responde «no configurado» y no se consulta nada.
Preparación sin conexión: correo de cada persona (la clave del vínculo), área del portal de cada equipo (catálogo de 57 áreas
copiado de su código en `datos_semilla/areas_portal_ppn.json`, con sugerencia automática a revisar) y planilla CSV para quien
administre el portal.

**Mesa de Entradas (`ppn-mesa-entradas`, `mesa_entradas.py`).** Un expediente es TIPO + NÚMERO + AÑO (p. ej. `EP 1234/2025`;
número correlativo por tipo; 13 tipos: AN, IP, LP, PP, SE, NC, EF, LS, AP, BI, EA, EP, ET). Guarda PDFs por expediente; no tiene
notas, pases, estados ni numeración de notas (eso sigue acá). Quedan escritas la búsqueda (`recordsWithFilter`) y la carga del PDF
despachado (`createDocument`, subida multipart de GraphQL). **Falta de su lado** una credencial de servicio: hoy su API solo acepta
sesiones de personas (token + mismo navegador). La pestaña muestra cuántos números de expediente cargados acá ya tienen ese formato.

**Almacenamiento (`ppn-storage`, `almacen_ppn.py`).** Subida `POST /api/file/` (campo `file`, cabecera
`system-identifier-authorization: <uuid del sistema>`), descarga `GET /api/file/<uuid>/<archivo>`. Antes de usarlo para documentos
con datos de personas detenidas hay que resolver que la descarga no pide clave y que no guarda huellas ni versiones.

```ini
[sso]
url =
aplicacion_id =
portal_url =
verificar_cada_minutos = 10

[mesa_entradas]
url =
url_web =
token_servicio =

[almacen_ppn]
url =
sistema_id =
```

**Observaciones de seguridad vistas en esos códigos (para quien los mantiene):** en Mesa de Entradas las rutas REST
(`/api/storage/:archivo`, `/api/migration/*`, `/api/record-unified-document/*`) y varias consultas (`documents`, `annexes`,
`recordHistories`, `usersForSelect`) no piden sesión, y la consulta al portal desactiva la verificación del certificado
(`rejectUnauthorized: false`); en el almacenamiento la descarga es pública para quien conozca el uuid y el nombre del archivo.

## Estética de los sistemas PPN (`static/tema-ppn.css`)

Misma línea visual que el portal y Mesa de Entradas (plantilla «Minimal»): color institucional bordó `#AC2423`, letra
**Public Sans** (incluida en `static/fonts`, licencia OFL; funciona sin internet), menú lateral con secciones, barra superior
con buscador, avisos y perfil, tarjetas de 16 px con sombra suave, botones y campos de 8 px, tablas con encabezado gris y filas
punteadas, etiquetas «suaves», pantalla de ingreso dividida con bienvenida. Modo oscuro con la misma paleta. Desde la evaluación
de interfaz (octubre 2026) todo vive en una sola hoja, `static/estilos.css`; `style.css` y `tema-ppn.css` quedan en disco pero
ya no se cargan.

Tests nuevos: `tests/test_sso.py`, `tests/test_integraciones.py`.

## Plan de reformas — ola 2: asistentes que proponen (`agentes.py`)

Regla: **los asistentes proponen y una persona decide**. Todo funciona sin IA (reglas); con Ollama, el resumen y el proveído
se redactan con el modelo local. Cada propuesta queda en `sugerencias_ia` (o como alerta) con quién la aceptó o descartó.

- **Prerrevisión (1)** al subir: campos sin completar, destinatario, expediente, unidad fuera del catálogo, fechas raras, año de
  la fecha de la nota, anexos mencionados sin bloque. Son alertas «Prerrevisión» (no las borra el control cruzado) y el botón
  «Enviar a Coordinación» pide confirmación si quedan abiertas.
- **Proveído (2)**: en la nota, «Proponer: envío por correo / entrega en mano a la persona detenida» → borrador editable → .docx.
- **Control cruzado (3)**: además de números, fechas y nombres, ahora compara la **unidad** y verifica que la persona a la que va
  dirigida la nota al detenido figure en el otro documento.
- **Clasificación (4)**: tipo por contenido (NOTA, NOTA A PPL, PROVEIDO, INFORME) y nuevas señales de urgencia (aislamiento,
  falta de atención médica, autolesiones, agresiones). La prioridad sigue siendo una sugerencia.
- **Plazos (5)**: detecta «dentro de N días (hábiles/corridos)», «en el término de 48 horas»; si se acepta, el plazo de respuesta
  se cuenta desde el despacho y se avisa al autor y a la coordinación. Además, recordatorio el día anterior al vencimiento interno.
- **Resumen (6)** de 3 líneas en «Pendientes de tu firma» (por reglas al subir; con IA en la cola nocturna, trabajo RESUMEN).
- **Antecedentes (7)** y **casi iguales (8)**: personas, DNI, unidades y expedientes leídos del texto (`nota_entidades`).
- **Estilo (9)**: glosario PPN inicial (se carga si está vacío) con el fragmento donde aparece; nunca reemplaza solo.
- **Destinatario y correo (10)** sugeridos desde el texto y la agenda de envíos, visibles desde el inicio del circuito.
- **Estadísticas (13)**: Reporte con unidades más mencionadas y tipo de pedido, sin carga manual.
- **Aprendizaje (15)**: se corrigió un error que impedía detectar los patrones del Director («modificar» vs «modificado»); el inicio
  avisa a la Dirección cuando hay reglas nuevas para aprobar.
- **Hoy (23)**: lista única priorizada en el inicio (vencidas, urgentes, alertas, viajes, respuestas por vencer).
- **Tablero de agentes (24)**: Administración → IA → Tablero de agentes (aceptación, pendientes, tiempo de la IA, lectura).
- **Política de uso de IA (25)**: `/politica-ia`.
- **Control de cambios (30)**: en «Revisar correcciones», ✓ / ✗ por cada cambio; el texto final se arma con lo aceptado.

Tests: `tests/test_ola2.py`.

## Plan de reformas — ola 3: circuito y despacho (`circuito.py`, `rutas_circuito.py`)

- **Numeración anual (56)**: el sistema propone el próximo número de nota y de proveído del año (el mayor entre el contador y
  lo ya usado, +1); Mesa siempre puede cambiarlo y el contador nunca retrocede. `Despacho del día → Numeración`: fijar el último
  número, **leer el Excel de registro** que ya usan (detecta solo las columnas de número y fecha; no lo modifica), ver salteados y
  descargar el registro del año en Excel. Formato en `config.ini [numeracion]`.
- **Despacho del día (11)** (Mesa): notas firmadas agrupadas por destinatario con números correlativos propuestos; se cargan y
  generan todas juntas. Sin correos por ahora.
- **Respuestas (12)**: en la nota despachada, «Cargar una respuesta» (fecha, medio, de quién, qué responde, archivo). Avisa al
  autor y a la coordinación y corta el aviso de plazo vencido. Mesa ahora sigue viendo lo que despachó.
- **Circuito en columnas (27)**: `/circuito`, una columna por etapa; en rojo lo trabado.
- **Vista con formato (29)**: en la nota y en «Revisar correcciones», el .docx se ve con su formato sin descargarlo.
- **Firma en serie (37)**: «Firmar en serie» en «Pendientes de tu firma»: recorre las notas en el orden de prioridad;
  «Firmar y seguir» (atajo S) y «Saltar» (N).
- **Entrega en mano (58) y viajes (59)**: registro de quién la llevó, cuándo, a quién y en qué viaje; «Mandar con ese viaje»
  avisa a quienes viajan, y en el viaje aparecen los documentos para marcar «Entregada» o «No se pudo entregar».
- **Plantillas con campos (61)**: en el Word, «Campo» o [[Campo]]; «Completar y usar» pide los campos y crea el borrador
  o descarga el Word. Se cargan tres plantillas de ejemplo si la biblioteca está vacía (colchón, trabajo, traslado).
- **Carpeta del caso (63)**: `/caso` por persona, unidad o expediente: notas, proveídos, envíos, viajes y respuestas.
- **Plazos por destinatario y reiteración (64)**: el plazo aceptado se recuerda por destinatario y se propone en las próximas
  notas; vencido sin respuesta, se sugiere una **nota de reiteración** (borrador editable).
- **Firma delegada (65)**: si el/la Director/a carga una ausencia delegando en la Subdirección, se le avisa a quien queda a cargo
  y cada firma queda registrada «en reemplazo de».
- **PDF/A (66)**: «Descargar PDF/A para el archivo» en cada nota firmada; opcionalmente el despacho entero en PDF/A
  (`[archivo] pdfa_en_despacho = true`).
- **Tiempos por equipo (84)** e **informe del período (87)**: en Reporte, tramos del circuito por equipo y un borrador en Word
  trimestral o anual con las cifras del sistema (y traslados para la Dirección).

Tests: `tests/test_ola3.py`.

## Plan de reformas — ola 4: traslados, planificación y operación (`operacion.py`, `rutas_operacion.py`)

Los asistentes **proponen**; asignar sigue siendo decisión de la coordinación.

- **Desplazamiento (68)**: cada salida de un vehículo (o de un chofer sin vehículo) en un día, con sus paradas y los equipos de
  cada una. Se arma solo al asignar, y se rearma al reprogramar, cancelar o cambiar quiénes viajan. Al arrancar la app se
  arman los de los viajes ya asignados.
- **Tablero del día (44)** `/traslados/hoy`: sin chofer, asignados, en curso y realizados; salidas del día con su hoja de ruta
  y novedades. El chofer ve «Mis salidas».
- **Hoja de ruta imprimible (50)**: paradas, horarios, dirección, guardia y contactos de la unidad, quiénes viajan, documentos
  para entregar y espacio para km, combustible, novedades y firma.
- **Cierre de la salida (69/70)**: horario real, km de salida y regreso, litros e importe. Actualiza el km del vehículo; en
  Estadísticas se suman km, litros, km por litro e importe por vehículo. Quien viaja, el chofer o la coordinación pueden cargar
  el horario real de cada viaje.
- **Novedades del viaje (76)**: ingreso denegado, demora para ingresar, requisa, entrevista no realizada, sin espacio y demora en
  el traslado (con minutos). Se suman por unidad en Estadísticas.
- **Licencias de choferes (71)** `/traslados/licencias`: calendario de cuatro semanas y carga con fechas. Si en esas fechas hay
  viajes asignados, el sistema avisa que hay que reasignarlos.
- **Reservas de vehículos (72)** `/traslados/reservas`: para usos que no son viajes (service, trámites, actos).
- **Lista de espera (73)**: con el vehículo completo, «Anotarme en la lista de espera». Si se libera un lugar, se avisa a la
  primera persona de la lista y a la coordinación.
- **Planificador semanal (16)** `/traslados/planificar`: junta en una salida los pedidos del mismo día a la misma unidad o a
  unidades cercanas (hasta 25 km, o la misma ciudad) que entran en un vehículo. Propone el vehículo más chico que alcanza y un
  chofer. Cada propuesta se confirma con un botón y se puede cambiar.
- **Chofer sugerido (17)**: al asignar, primero el chofer libre, sin licencia ni licencia de conducir vencida y con menos salidas
  en la semana, con el motivo. Con licencia, reserva, VTV o seguro vencidos, el sistema avisa y pide confirmación («Aceptar
  solapamiento o aviso»).
- **Recordatorio del jueves personalizado (18)**: miércoles y jueves, a cada asesor/a le dice qué viajes habituales le faltan
  cargar y cuáles frecuentes le faltan confirmar. Quien no tiene nada pendiente recibe el aviso general.
- **Anomalías (19)**: chofer con licencia o licencia de conducir vencida, vehículo con VTV o seguro vencidos, reservado o fuera
  de servicio, más de 2 salidas por día u 8 por semana, más de 10 h en el día y horarios inusuales. Aparecen en las alertas de
  Traslados, en el tablero del día y en el planificador.
- **Hora de regreso estimada (20)**: con 3 o más viajes anteriores al mismo destino, el formulario propone la hora de vuelta
  (mediana; usa el horario real si está cargado). Se aplica con un botón.
- **Agenda por hora (41)** `/traslados/horas` y **vista mensual (42)** `/traslados/mes`, con una barra de vistas en todas las
  pantallas de Traslados. En la agenda por hora se marca en rojo el mismo chofer en dos vehículos a la vez.
- **Solicitud en pasos (53)**: Cuándo, Adónde, Quiénes y Confirmar (con resumen). «Ver todo en una sola página» vuelve al
  formulario completo; la elección se recuerda en el navegador.
- **Comparación Excel anterior ↔ sistema (79)**: en «Importar Excel», «Comparar». Muestra lo que coincide, las diferencias de
  horario o chofer y lo que está solo en un lado. No importa ni cambia nada.

Tests: `tests/test_ola4.py`.

## Borrar notas de prueba (Administración → Estado del sistema → Borrar notas de prueba)

Para empezar una prueba nueva. Muestra antes cuánto se borra y cuánto se conserva, pide escribir `BORRAR NOTAS`, hace un
respaldo completo (base y documentos; si falla, no borra nada) y recién ahí borra.

- **Se borra:** las notas, sus archivos (solo los que están dentro de la carpeta de datos), historial, avisos de consistencia,
  informes, respuestas, envíos, entregas en viajes, comentarios, vistas, texto indexado y datos extraídos (así la prueba nueva no
  recibe antecedentes ni avisos de «casi igual» de las notas viejas), avisos que apuntan a notas y la numeración guardada.
- **Se conserva lo aprendido:** correcciones del Director (patrones), reportes marcados como erróneos, glosario, plazos por
  destinatario, modelos y plantillas, y las decisiones sobre sugerencias de los asistentes. También usuarios, equipos, unidades,
  traslados y configuración.
- **Numeración:** sin base cargada, el sistema ya no propone números: el próximo sale del registro de Mesa de Entradas
  (se fija en «Numeración», a mano o desde el Excel del registro).

## Expedientes: búsqueda por campos y verificación del LPU

- **Búsqueda (pantalla Expedientes):** cuatro campos, LPU, DNI, N° de Expediente y Nombre y Apellido, con un solo botón «Buscar».
  Se pueden completar varios a la vez y se combinan. Cada dato se busca en la columna de la planilla que le corresponde, según el
  encabezado: «LPU»/«Legajo», «DNI»/«Documento», «Expediente»/«Expte», «Nombre»/«Apellido»/«Interno». Si una planilla no
  tiene esa columna, se busca en todas sus columnas y se avisa. Los números se comparan sin puntos ni barras; desde 4
  caracteres también se acepta una parte del número.
- **Verificación del LPU (notas y proveídos):** en la prerrevisión, cada LPU que menciona el documento se busca en la base de
  expedientes. Hay aviso si el LPU no figura. Hay discrepancia si el DNI del documento no es el de esa persona, o si su nombre no
  aparece en el documento. Si el expediente mencionado no es el de ese LPU, también hay aviso. Solo informa: no cambia nada. Sin
  planillas, o sin columna de LPU, no se verifica. Al arrancar la app se revisan una vez los documentos que ya están en circuito.

## Sistema de diseño (evaluación de interfaz, 35 propuestas)

Se aplicaron las 35 propuestas de `Evaluacion_UI_DGPDH.xlsx`. Lo principal:

- **Una sola hoja de estilos** (`static/estilos.css`) con tokens: tamaños de letra `--t-xs … --t-display` (escalan con el
  tamaño elegido en Mi perfil), espacios `--esp-1 … --esp-6`, radios `--r-control`/`--r-tarjeta`/`--r-pildora`, colores
  semánticos `--color-alerta/atencion/exito/info/neutro` con su versión para modo oscuro, y `--cat-1 … --cat-8` para series.
  Pantallas nuevas: usar los componentes de la **Guía de estilo** (catálogo en `/guia-estilo`), no estilos propios.
- **Un solo botón primario por pantalla**; el resto, secundarios. Las acciones irreversibles usan `boton--peligro`.
- **Componentes únicos**: Aviso (`aviso--info/exito/atencion/error`), Etiqueta (`etiqueta--…`), tabla con filas clicables
  (`tr[data-href]`), formulario en grilla, selector de archivos con arrastre, íconos de línea con `ico("nombre")`.
- **Ayudas plegadas** detrás de un botón «?» (en Mi perfil se puede elegir verlas siempre).
- **Notas**: la ficha se ordena en pestañas Documento / Asistentes / Seguimiento / Historial, con la acción principal fija
  abajo al desplazarse. La revisión muestra 2 columnas (el original se activa aparte) y tiene atajos N/P/A/R.
  El listado tiene una sola fila de pestañas (incluye los filtros guardados).
- **Traslados**: quien solo pide viajes entra a «Mis viajes» (pedir, confirmar, próximos viajes). Una sola barra de vistas;
  el circuito se elige en un desplegable y los accesos poco usados están en «Más».
- Contraste verificado (WCAG AA) en modo claro y oscuro sobre las 18 pantallas principales.

## Plan de reformas — ola 5: datos, seguridad y capacitación

Todo nuevo y aditivo (tablas nuevas, sin tocar datos existentes). Pruebas: `tests/test_ola5.py`.

**Datos para decidir** (menú «Datos», solo Dirección; `indicadores.py`, `rutas_datos.py`):
- **81 Tablero**: notas del período, firmadas y despachadas, lo que espera firma hoy, tiempos del circuito, viajes a las
  unidades y desplazamientos, unidades más visitadas y más mencionadas, tipos de pedido y «unidades con notas y sin visitas». Excel.
- **82 Por unidad**: notas que la nombran, visitas, equipos, pedidos, mes a mes y notas esperando entrega en mano. Excel para el informe.
- **86 Mapa de temas**: unidad × mes y unidad × tipo de pedido (escala de un color, el número siempre visible) y «problemas que se repiten».
- **88 Datos publicables**: tablas agregadas sin datos personales; celdas de 1 o 2 casos se publican como «menos de 3». CSV y Excel con notas metodológicas.
- **89 Uso del sistema por área**: quién entró, ingresos, documentos, acciones, viajes; quién no entra hace 30 días o nunca.
- **83 Reportes programados**: lunes 8 h (semana anterior) y día 1, 8 h (mes anterior), en `datos/reportes_programados`, con aviso en el
  sistema. Alcance acordado: descarga; el envío por correo cuando se vincule.
- Criterios: se cuentan notas (no informes ni proveídos), sin borradores ni rechazadas; fecha = firma o, si no, carga; unidad = la fijada
  en la nota o las que nombra el texto.

**14 Consultar** (menú «Consultar», todos los que ven notas): pregunta en lenguaje común → filtros visibles y corregibles → la base cuenta.
Reglas primero (fechas, unidades del catálogo, tipos de pedido, estados, «por unidad/mes/…»); si hay IA, solo completa lo que falte y se
valida contra el catálogo. Cada persona cuenta solo lo que puede ver.

**80 Presentismo** (Traslados → Presentismo): además de la semana, **Mes** (cuadro por persona y día, Excel), **Por persona** y **Cruce con
la planilla de Personal** (.xlsx/.xls/.csv, una fila por persona y día o una columna por día con códigos P/A/L/C). Marca quién viajó con
ausencia o licencia, comisiones sin viaje cargado y nombres sin usuario. No corrige nada.

**93 PIN de firma** (`pin.py`): Dirección y Mesa de Entradas confirman un PIN de 4 números una sola vez por sesión, antes de la primera
firma o despacho (se crea en ese momento si no existe). Hash + salt, 5 intentos fallidos → 15 min de bloqueo y aviso a la Dirección.
Administración → Seguridad → PIN de firma para borrarlo; Mi perfil para cambiarlo. El servidor lo exige aunque se saltee la pantalla.

**92 HTTPS interno** (`https_interno.py`, `iniciar_https.bat`): autoridad certificante interna + certificado del servidor (825 días).
`python https_interno.py --nombre SERVIDOR --ip IP`, luego `[seguridad] https = true` e `iniciar_https.bat` (puerto 8443); instalar
`certs\ca.crt` en las PCs. `certs\ca.key` no se comparte. Requiere `pip install cryptography`. Con HTTPS activo se envía HSTS.

**98 PostgreSQL (preparado)**: SQLite queda afinada (WAL, espera, caché) y medida (Administración → Estado del sistema → Base de datos).
`python migrar_postgresql.py --sql dgpdh.sql` o `--url postgresql://…` copia todo y verifica (probado generando la copia; falta correrlo contra un PostgreSQL real). Para que la app funcione sobre PostgreSQL
falta cambiar la conexión de `database.py` (y los módulos que abren `db._conexion()`), reemplazar la búsqueda FTS5 por `tsvector`,
`COLLATE NOCASE` por `citext` o `lower()`, y las funciones `substr`/`strftime` de fechas. Conviene hacerlo con el pase a servidor (91).

**99 Salud del sistema** (`salud.py`, Administración → Estado del sistema → Salud): disco, base, respaldo, IA, cola de análisis, memoria,
correo y certificado HTTPS, cada 15 minutos; avisa a la Dirección solo cuando algo cambia de estado (y cuando se normaliza).

**40 Accesibilidad**: auditoría automática de las pantallas (etiquetas de campos, nombres de botones, títulos en orden, tablas con
encabezados, foco inicial en «Saltar al contenido», contraste AA en claro y oscuro), respeto de «reducir movimiento» y alto contraste de Windows.

**100 Ayuda** (menú «Ayuda», `manual.py`): manual por rol (asesoría, coordinación, Dirección, Mesa, traslados, Personal, choferes) y
9 videos cortos sin sonido con los textos en pantalla (`static/videos/`, grabados con datos de prueba).

## Personas detenidas (ABM) — `detenidos.py`, `rutas_detenidos.py`

Menú **«Personas detenidas»** (todos los que ven Notas, salvo choferes). Decisión del organismo: los datos de la persona salen de un ABM
propio, que se carga a mano o se importa, con bases exportables, y queda preparado para intercambiar datos con el sistema del menú de la
Procuración.

- **Campos**: nombre completo, DNI, LPU, alias, fecha de nacimiento, es adulto, edad, número de expediente PPN, código, centro de
  detención (del catálogo de unidades u «otro»), módulo, pabellón, celda, juzgado, tribunal, defensor/a, situación procesal,
  nacionalidad y observaciones. **Edad** y **es adulto** se calculan con la fecha de nacimiento; sin fecha, «es adulto» se carga a mano.
- **Controles**: nombre obligatorio; DNI de 6 a 9 números y sin repetir; LPU sin repetir; fecha válida, no futura y de menos de 110 años;
  aviso si falta DNI y LPU o si hay otra persona con el mismo nombre.
- **Nunca se borra**: baja con motivo y reactivación. Cada cambio queda registrado campo por campo (quién, cuándo, antes, después y si vino
  de una planilla). Cada apertura de ficha, exportación, importación y consulta por API queda en `seguridad_eventos`.
- **Ficha**: datos, notas del archivo que nombran a la persona (por DNI leído del texto o por todas las palabras del nombre), historial.
- **Importar** (Dirección y Mesa): .xlsx/.xls/.csv con encabezados (se reconocen por nombre: «Apellido y nombre», «D.N.I.», «Unidad»…),
  o «Traer de las planillas de Mesa» (las de Expedientes, solo lectura). Vista previa con altas, actualizaciones (solo los campos que la
  planilla trae con dato; «Gómez, Juan» no reescribe «Juan Gómez») y filas con error; nada se guarda hasta confirmar.
- **Exportar** (Dirección y Mesa): Excel o CSV de la lista con sus filtros.
- **Intercambio**: `/api/detenidos` (lectura, JSON con claves estables; `?dni=`, `?lpu=`, `?q=`) con `[detenidos] api_token`.
  `url_sistema_procuracion` y `token_sistema_procuracion` dejan listo pedir una persona al sistema de la Procuración para precargar el
  alta (`/detenidos/nueva?dni=…`); sin configurar no sale a la red.
- Pruebas: `python tests/test_detenidos.py`.

## Plan de reformas — ola 6: rápidas (`tipos_documentales.py`, `trazabilidad_ia.py`)

IA: sigue siendo **solo local** (decisión de la Dirección, octubre 2026). Ningún texto sale del organismo.

- **112 Avisos**: pestañas Nuevos / Leídos / Todos; abre en «Nuevos» si hay sin leer; los nuevos resaltados; «Marcar como no leído».
- **113 Aprobación por jerarquía** (Asesor → Coordinador → Dirección): lo que sube la Coordinación queda «sin enviar», igual que lo del
  asesor (corregir el texto, armar bloques, prioridades). Cuando la Coordinación del equipo (o la Dirección) lo envía, o interviene sobre
  algo ya enviado, no espera su propia aprobación: la etapa se da por cumplida, arranca la corrección y el historial dice «Etapa de
  Coordinación considerada cumplida por intervención de …». Otra coordinación no la cumple. Lo que sube la Dirección pasa directo.
- **120 Firma en un paso**: «Firmar» → una confirmación → firma con la fecha del servidor. «Otra fecha» queda plegada para casos
  excepcionales (adelantar trabajo de días de licencia) y se registra en el historial con la fecha del servidor. El PIN se mantiene.
  También en la firma en serie.
- **109 Plazo de respuesta explícito**: «En término / Fuera de término / Vencido / Sin plazo» en cada respuesta, en el listado (notas
  despachadas con plazo) y en Datos → Tablero (sección «Respuestas a las notas despachadas»). Al cargar una respuesta se calcula el
  vencimiento si todavía no estaba, y se conserva la fecha de la primera respuesta.
- **114 Leyenda de colores** en «Revisar». Se corrigió además un error viejo: había un script dentro del título de esa página.
- **102 Búsquedas guardadas en Expedientes** (por persona, en sus preferencias, hasta 12).
- **131 Tipo documental ampliado**: Nota, Nota a persona detenida, Oficio, Pedido de informe, Requerimiento, Solicitud, Contestación,
  Respuesta a organismo, Presentación judicial, Comunicación a unidad, Comunicación interna, Informe y Proveído. Las reglas proponen
  (con el motivo) y quien lleva la nota confirma o cambia en la ficha; queda en el historial. Es un dato aparte del «tipo» que usa el
  circuito; lo va a usar el Generador para elegir plantilla y fórmulas.
- **137 Trazabilidad de la IA** (`ia_registro`): cada llamada al modelo local guarda tarea, documento, modelo, fecha, duración, si se usó
  o se descartó (y por qué) y lo que devolvió (recortado). Del pedido solo se guarda tamaño y huella. Al aplicar la revisión se registra
  cuántas propuestas de la IA se aceptaron, se editaron o se rechazaron. Se ve en la ficha → Historial → «Lo que propuso la IA».
- Pruebas: `python tests/test_ola6.py`.

## Especificación de octubre 2026 (`soberania.py`, `control_cambios.py`)

- **Política de aislamiento y soberanía de datos — Modo Seguro Institucional** (activo por defecto, `config.ini` → `[seguridad]`).
  Toda conexión de red que abre la app se controla a nivel de socket (requests, urllib, correo, descargas): pasa lo interno
  (localhost, IP privadas, nombres de servidor sin dominio y `dominios_internos`); lo externo se bloquea y queda registrado
  (usuario, fecha y hora, destino, tipo, motivo, módulo que lo pidió). Los sistemas propios configurados (portal PPN, Mesa de
  Entradas, almacén, sistema de la Procuración) y los `destinos_autorizados` pasan, registrados. La IA y el correo externos se
  bloquean. Respaldos en carpetas de nube pública (OneDrive, Google Drive, Dropbox, iCloud, Box): bloqueados. Para una función
  futura que deba salir al exterior por pedido de una persona: `soberania.salida_confirmada(...)` (advertencia, datos, destino,
  confirmación expresa y registro). Administración → Seguridad → Modo seguro: estado, clasificación de cada conexión configurada
  y registro. Las páginas ya no envían su dirección a terceros (`no-referrer`); para que los navegadores no descarguen htmx de
  internet, guardar una copia en `static/htmx.min.js` (se usa sola si existe).
- **Firma continua**: al firmar se registra, se confirma y se abre el siguiente documento pendiente de firma (prioridad, bloque y
  antigüedad), con «Documento N de M · Restan K». Sin pendientes: «No existen más documentos pendientes de firma» con accesos a la
  bandeja de firmas, al despacho y al inicio. Nunca se abre el despacho al firmar.
- **Equipo y asesor editables** en la ficha (mismo estilo que la prioridad). Dirección: equipo y asesor; Coordinación: el asesor
  dentro de su equipo. Auditoría en `notas_reasignaciones` (usuario, antes, después, fecha y hora) y en el historial; aviso a la
  persona asignada. Bandejas, reportes, filtros e indicadores leen esos campos, así que se actualizan solos. Una nota en un bloque
  no cambia de equipo hasta sacarla del bloque.
- **Correcciones como control de cambios**: en «Ver los cambios aplicados» y en «Revisar», agregado en verde, quitado en rojo
  tachado, palabra por palabra; cada tramo con un subrayado según su tipo (ortografía, gramática y puntuación, reformulación) y
  los totales arriba.
- **IA los fines de semana**: sábados y domingos la cola corre las 24 horas (correcciones, clasificación, análisis, resúmenes y
  lo que se sume a la cola); de lunes a viernes, igual que antes. Se activa o desactiva en Administración → IA, sin tocar código.
- Pruebas: `python tests/test_especificacion_octubre.py`.

## DGPDH - Beta 01 · Tanda A (`unidades_csv.py`)

Según la evaluación devuelta el 17/10 (backup y nombre de versión quedan en cola).

- **164 Bandejas**: al entrar al listado, si hay tareas se abre siempre «Pendientes de mi acción» (para la Dirección queda arriba
  «Pendientes de tu firma»); la pestaña elegida a mano se respeta. La bandeja con tareas se muestra abierta aunque se haya
  retraído antes (`data-abrir` en `plegable.js`).
- **157 Cambios sin diferencia visible**: un cambio que solo toca espacios duros, dobles o caracteres invisibles no genera
  propuesta (`nucleo.igual_a_la_vista`); las propuestas ya guardadas se limpian al abrir «Revisar».
- **158 Reversión**: en «Revisar», «Deshacer» (y Ctrl+Z fuera de los cuadros), «Restaurar original» y «Volver a la propuesta» por
  párrafo, y «Restaurar todo el original» / «Volver a todas las propuestas». Nada se guarda hasta «Guardar».
- **163 Barra fija de firma**: con la nota a la firma, Firmar · Devolver · Ver documento · Descargar · Historial · Otra fecha en una
  barra fija; equipo, asesor, tipo y prioridad plegados en «Datos de la nota».
- **161 Al terminar de firmar**: se ofrecen los documentos pendientes de control (revisión de correcciones), por prioridad.
- **151 Importación de unidades**: vista previa antes de guardar, mensajes con fila y columna («Se detectó un salto de línea dentro
  de la celda»), plantilla e informe de errores descargables; se importan solo las filas sin error; nunca un error técnico.
- Pruebas: `python tests/test_beta01_a.py`.

## Traslados: vincular choferes y pasajeros con usuarios

Nombres cargados de otra forma («L.Aguilar», «A. Perez») ya no quedan como «sin usuario» para siempre:
- **Pasajeros**: en el detalle del viaje, la coordinación elige «es el usuario…» (primero los nombres parecidos) y vincula. Si la
  persona estaba en «Personas sin usuario», se vinculan todos sus viajes; si no, ese registro. Esa página también propone el usuario
  por nombre parecido (no solo idéntico).
- **Choferes**: en «Choferes y vehículos», una ficha cargada por nombre se vincula con su usuario; si ese usuario ya tenía ficha, se
  unifican: todo registro con ese chofer (viajes, licencias, reservas, operación) pasa a la ficha del usuario y la otra queda en Baja
  con la constancia. Nada se borra; todo queda en el historial.
- El parecido (`traslados.parecido_usuario`) usa apellidos e iniciales y solo propone: vincula una persona.
- Pruebas: `python tests/test_vinculos_traslados.py`.

## DGPDH - Beta 01 · Tanda B: PDF, duplicados y «Autos PPN»

- **Archivo directo de PDF**: se pueden subir `.pdf` (se verifica que el contenido sea realmente un PDF). Quedan «Archivados»:
  sin corrección, IA, tareas ni firma. Su texto (si tiene) se indexa para la búsqueda. Se ven y descargan tal como se cargaron.
- **PDF que acompaña**: desde la ficha del PDF, «Acompañar a un documento» (si ese documento ya está en un bloque se suma a ese
  bloque; si no, se arma un bloque con los dos) o «Agregar como adjunto» a un bloque abierto. Dentro de un bloque queda «Adjunto del
  bloque»: no cuenta como «falta firmar» y Mesa lo despacha junto con el resto. Si se saca del bloque vuelve a «Archivado».
- **Duplicados** (decisión: comparar a mano): antes de terminar la carga se avisa si el archivo o su texto ya están cargados, con
  documento, usuario y fecha: «Cancelar», «Cargar igualmente» o «Reemplazar» (como nueva versión). Lo cargado igual queda marcado
  «Posible duplicado», con filtro en el listado y la comparación de los dos documentos lado a lado (`/nota/{id}/duplicado`).
  Desde ahí: «No es duplicado» o «Descartar». **Descartar no borra**: el documento sale de las bandejas, se conserva y se recupera
  desde «Descartados» (vuelve al estado que tenía). Lo firmado, en corrección o despachado no se descarta.
- **«Autos PPN»**: el módulo de Traslados cambia de nombre en menú, títulos y manual (las direcciones `/traslados/…` no cambian).
- Pruebas: `python tests/test_beta01_b.py`.

## «Solo Mesa de Entradas» y funciones de Mesa (Beta 01)

- **Mesa de Entradas es un área** (jefe, coordinador y asesores). Sus funciones —Despacho del día, números y PDF, despacho,
  envíos, numeración y la carga «Solo Mesa de Entradas»— las ejerce esa área. **La Dirección General (Director y
  Subdirector/a, con las mismas facultades) las puede ejercer excepcionalmente** (`auth.ROLES_MESA_EXCEPCIONAL`; los perfiles
  superiores que se creen se agregan ahí): todo lo que deja en el historial queda con la marca «excepcional: función de Mesa
  de Entradas».
- **«Solo Mesa de Entradas»**: al subir un .docx se elige el circuito (para Mesa viene marcado). Queda «para remitir»
  (`PARA_REMITIR`, `modalidad = SOLO_MESA`): sin control, coordinación, firma ni IA; Coordinación no recibe aviso, el resto del
  área sí. Se numera, se genera el PDF y se despacha como una nota firmada; el número se inserta en una copia del original
  (que queda intacto y se verifica por su huella). Etiqueta propia en el listado y en la ficha.
- Pruebas: `python tests/test_solo_mesa.py`.

## Confirmaciones y rendimiento (Beta 01)

- **Todo lo que se crea o se guarda lo confirma.** Crear equipo, usuario, término, cargar documentos, agregar a un bloque,
  despachar… muestran su mensaje («Equipo «X» creado correctamente.»); después de crear un equipo o un usuario se vuelve al
  listado, donde se ve lo creado. Si alguna acción no deja mensaje propio, el sistema muestra «Listo: se guardó correctamente.»
  (`main._confirmar_guardado`; no aplica a ingresar, firmar ni avisos).
- **Ollama por IPv4.** En Windows «localhost» se prueba primero por IPv6 y cada conexión a Ollama perdía ~2 s; la URL de
  `config.ini` se usa con `127.0.0.1` (misma máquina). El estado de los modelos se reutiliza 1 minuto en pantalla.
- **Administración calcula solo la pestaña abierta** (antes cualquier pestaña consultaba a Ollama, la cola, respaldos y accesos).
- **La IA espera mientras alguien usa el sistema**: no toma trabajos nuevos hasta 90 s sin uso
  (`[ia_automatica] pausa_por_uso_segundos`; 0 = no pausar). En Administración → IA → Cola se ve «en pausa».
- **Mantenimiento de fondo cada 10 minutos** (plazos, recordatorios, sugerencias, salud, reportes) en vez de cada minuto.
- **Ayuda**: miniaturas livianas (JPG, 640 px) y el video se carga recién al tocarlo (antes 24 imágenes de 1280 px).
- **Pantallas lentas**: cada pedido de más de 1 s queda registrado (solo pantalla y demora) en Administración → Estado del
  sistema → Base de datos; las respuestas llevan la cabecera `Server-Timing`.
- Pruebas: `python tests/test_confirmaciones_rendimiento.py`.

## Documentos de referencia de la IA (`referencia_ia.py`, `rutas_referencia.py`)

- Carpeta `datos/referencia_ia/` con las subcarpetas que se quiera (nombres libres; dentro de una misma carpeta no se repiten,
  y se validan los caracteres que Windows no acepta). Se administra en **Administración → IA → Documentos de referencia**
  (`/ia/referencia`, solo Dirección General): crear carpetas, subir documentos o **una carpeta entera con sus subcarpetas**
  (se conserva la estructura), retirar (va a `_retirados`, no se borra) y ver el registro. También se puede copiar a mano con el
  Explorador: la carpeta se revisa sola cada 10 minutos o con «Revisar la carpeta ahora». Acepta .docx, .doc (con Word) y .pdf
  con texto; un archivo repetido no se cuenta dos veces.
- **Qué aprende** (todo local, sin reentrenar el modelo): (1) *términos propios*: palabras que aparecen al menos dos veces en lo
  firmado y que el diccionario general no conoce (nombres, siglas, lugares) dejan de «corregirse» en la corrección automática
  (diccionario, LanguageTool e IA); las palabras comunes sin tilde se siguen corrigiendo; (2) *parecidos*: en «Análisis
  automático» de cada nota, a qué carpeta se parece según los 5 firmados más cercanos (solo propone); (3) *estilo*: las mejoras
  de redacción reciben como ejemplo de tono el firmado más parecido, con la orden de no copiar sus datos (los datos que no están
  en el informe se siguen descartando).
- Ruta configurable: `config.ini` → `[referencia_ia] carpeta = ...`. Pruebas: `python tests/test_referencia_ia.py`.

## Administración → Usuarios: ordenar, filtrar, agrupar y equipo para cualquier rol

- Listado con filtros por **rol**, **equipo** (o «sin equipo») y **estado**; orden tocando el título de cada columna (otra vez:
  al revés); **agrupar por equipo o por rol**.
- **Corrección:** el equipo se guardaba solo para Asesor y Coordinador; para los demás roles (Mesa de Entradas, Dirección…) se
  descartaba sin aviso. Ahora se guarda para cualquier rol (sigue siendo obligatorio para Asesor y Coordinador) y el mensaje dice
  qué cambió («equipo: sin equipo → Mesa de Entrada»).

## Autos PPN: sistemas por persona, flota, aviso de vehículos y planificador por ubicación

- **Notas y Autos PPN, por separado**: en Administración → Usuarios cada persona tiene dos interruptores («✓ Notas», «✓ Autos
  PPN») en el listado y en «Editar». Quien no tiene Autos PPN no lo ve en el menú ni en el inicio y no entra a `/traslados`;
  quien no tiene ninguno ve un aviso. La Dirección General ve siempre los dos; los choferes, solo Autos PPN; quien coordina
  traslados siempre entra a Autos PPN. (Columna `usuarios.acceso_traslados`.)
- **Vehículos** (Choferes y vehículos, ficha del vehículo y Panel de flota): dar de alta, a taller / volvió del taller,
  **suspender** (con motivo; no se asigna hasta reactivarlo) y **eliminar** (con motivo: sale de la flota y de las listas, se
  conserva el historial y se puede **recuperar** desde «Vehículos eliminados»). Estado `BAJA`, columna `vehiculos.estado_motivo`.
- **Aviso al cargar un viaje**: al elegir día y horario se consulta si quedan vehículos libres (asignados, reservados y pedidos
  que esperan vehículo; sin horario se cuenta el día entero). Si no queda ninguno se avisa (el viaje se puede cargar igual) y, si
  es prioritario, se pide la **revisión de la Dirección General** (Director y Subdirector/a) con el motivo; también desde el
  detalle del viaje. La Dirección decide «Dar prioridad» (se avisa a la coordinación para liberar o reasignar un vehículo) o «No
  corresponde» (con motivo, se avisa a quien lo pidió). Los pendientes aparecen como alerta para la Dirección.
- **Planificador por ubicación del destino** (vista por defecto; la propuesta automática sigue en su pestaña): pedidos con
  chofer sin asignar agrupados por zona y ciudad del destino, por día y hora. «Unificar los marcados» (mismo día): al mismo
  destino quedan en un solo viaje (los demás, «Unificado con el viaje #N», sin borrarse); a destinos distintos, una sola salida
  con el mismo chofer y vehículo. No deja juntar más personas que las plazas del vehículo más grande.
- Pruebas: `python tests/test_autos_y_flota.py`.
- **Ordenar y limpiar** (son copias solo para la IA, se borran de verdad): «Eliminar» por documento; eliminar de una vez los que
  no se pueden leer (y los repetidos, dejando siempre una copia en uso) y las carpetas vacías; **reordenar** todo por tipo de
  documento, tema, año, tipo y año, o tema y tipo, con vista previa de cómo quedaría antes de aplicar. Las carpetas que quedan
  vacías se borran y el texto ya leído se conserva (no se vuelve a leer).
