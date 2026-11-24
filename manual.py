"""Ola 5 · Reforma 100: manual por rol y videos cortos de capacitación (se ven en /manual, desde el menú «Ayuda»).

El contenido está acá (y no en una plantilla) para poder corregirlo sin tocar el diseño. Los videos son grabaciones
cortas de las pantallas con datos de prueba (sin datos reales), sin sonido y con subtítulos; viven en static/videos/
(un .webm, una imagen de portada .png y los subtítulos .vtt por video). Si una pantalla cambia mucho, se vuelven a grabar.
"""
from pathlib import Path

import auth

CARPETA_VIDEOS = Path(__file__).resolve().parent / "static" / "videos"

GENERALES = [
    ("Entrar y salir", ["Entrá con tu usuario y clave (o desde el portal PPN si está vinculado). La sesión se cierra sola después de un rato sin uso.",
                        "La primera vez el sistema te pide cambiar la clave provisoria: mínimo 8 caracteres, combinando letras y números."]),
    ("Mi perfil", ["Desde tu nombre, arriba a la derecha: cambiar la clave, el tamaño de letra, el modo claro u oscuro y si querés ver siempre las ayudas.",
                   "Si firmás o despachás, ahí también podés cambiar tu PIN de firma."]),
    ("Avisos", ["La campana muestra lo que necesita tu atención (una nota que vuelve, un viaje sin confirmar, un reporte listo). Tocá el aviso para ir directo.",
                "Avisos abre en «Nuevos»; un aviso ya leído se puede volver a marcar como no leído para acordarte."]),
    ("Tipo de documento e IA", ["En la ficha, el sistema propone el tipo de documento (oficio, pedido de informe, requerimiento…); quien lleva la nota lo confirma o lo cambia.",
                                "En Historial → «Lo que propuso la IA» queda qué hizo la IA sobre el documento, con qué modelo y cuándo, y qué decidió la persona."]),
    ("Ayudas en pantalla", ["El botón «?» al lado de cada título despliega la explicación de esa sección. En Mi perfil podés elegir verlas siempre abiertas."]),
    ("Personas detenidas", ["En «Personas detenidas» buscás por nombre, alias, DNI, LPU, expediente o código, y cargás o corregís los datos. La edad y si es mayor de edad se calculan con la fecha de nacimiento.",
                            "La ficha muestra las notas que nombran a la persona, el historial de cada cambio y el último listado que informó su alojamiento (al día si tiene 30 días o menos). Las bajas, la importación y la exportación las hacen la Dirección y Mesa de Entradas; nadie se borra.",
                            "«Búsqueda colectiva» combina filtros (nacionalidad, fuerza, lugar, juzgado, edad, estado del alojamiento), muestra gráficos del grupo y permite exportar todo o lo marcado."]),
    ("Preguntar al archivo", ["En «Consultar» escribís la pregunta como la dirías («¿cuántas notas por colchones al CPF I en 2026?»). El sistema muestra cómo la entendió; si algo no es así, lo corregís en el formulario y vuelve a contar."]),
]

ROLES = {
    "asesor": {
        "titulo": "Asesoras y asesores",
        "intro": "Subís los proyectos de nota, seguís su recorrido hasta el despacho y cargás tus viajes a las unidades.",
        "secciones": [
            ("Subir una nota", [
                "En «Notas a la firma» tocá «Subir documentos» y elegí los archivos (o arrastralos). Podés subir la nota junto con su informe y su proveído.",
                "Si la nota es urgente o prioritaria, elegilo en «Prioridad»; si no, queda Normal.",
                "«Agrupar en un bloque» es solo para documentos que tienen que salir JUNTOS (por ejemplo, varias notas con el mismo informe). Cada documento de un bloque suma análisis de consistencia, así que usalo solo cuando hace falta.",
                "La nota queda en borrador hasta que la enviás a Coordinación."]),
            ("Seguir el recorrido", [
                "Cada nota muestra en qué etapa está: Revisión inicial, Nota a la firma, Firmada y Despachada.",
                "Si la rechazan o piden volver a corregirla, te llega un aviso con el motivo. Corregí el archivo y subí la nueva versión desde la misma nota (pestaña «Historial», «Versiones del documento»).",
                "En la pestaña «Asistentes» ves lo que el sistema detectó (unidad, personas, plazos, datos que no coinciden). Son sugerencias: decidís vos."]),
            ("Viajes a las unidades", [
                "En Autos PPN → «Solicitar un viaje»: cuándo, adónde, quiénes y confirmar. Los de rutina de la semana próxima se cargan hasta el jueves.",
                "Si vas por tus medios, marcá «auto propio»: el viaje cuenta igual pero no ocupa lugar en el vehículo.",
                "Los viajes frecuentes se precargan solos: confirmalos la semana anterior desde «Mis viajes»."]),
        ],
        "atajos": [("/", "buscar en las notas"), ("J / K", "siguiente / anterior nota")],
    },
    "coordinador": {
        "titulo": "Coordinación de equipo",
        "intro": "Revisás lo que sube tu equipo, lo aprobás para corrección o lo devolvés, y seguís los tiempos y viajes del equipo.",
        "secciones": [
            ("Aprobar o devolver", [
                "En «Pendientes de mi acción» están las notas de tu equipo que esperan tu revisión.",
                "«Aprobar y enviar a corrección» la manda al corrector automático y después a la firma. «Rechazar esta nota» la frena con un motivo (es un criterio operativo, no de redacción).",
                "Podés cambiar la prioridad desde el listado sin abrir la nota.",
                "Podés reasignar el asesor responsable de una nota de tu equipo desde su ficha (queda registrado).",
                "Lo que subís vos queda «sin enviar», igual que lo de un asesor: podés corregir el texto, armar bloques y cambiar prioridades. Al tocar «Enviar a corrección» no espera tu propia aprobación: la etapa de Coordinación se da por cumplida y queda dicho en el historial."]),
            ("Mi equipo", [
                "«Mi equipo» muestra todas las notas del equipo y en qué etapa están; en ámbar o rojo las que llevan varios días sin moverse.",
                "En «Reporte» ves los tiempos del circuito de tu equipo."]),
            ("Viajes del equipo", [
                "Confirmás los viajes frecuentes de tu equipo la semana anterior y ves las estadísticas de viajes del equipo en Autos PPN → Reportes."]),
        ],
        "atajos": [("A", "aprobar la nota abierta"), ("J / K", "siguiente / anterior")],
    },
    "direccion": {
        "titulo": "Dirección General",
        "intro": "Revisás las correcciones propuestas, firmás, y tenés el tablero con los datos de notas, viajes y uso del sistema.",
        "secciones": [
            ("Revisar y firmar", [
                "En «Pendientes de tu firma» abrí la nota y, si hace falta, «Revisar»: cada cambio propuesto se acepta (A) o se rechaza (R); N y P pasan al siguiente y anterior.",
                "«Firmar» pone TU firma (la del Director o la de la Subdirectora, según quién entró) con la fecha de hoy, después de una sola confirmación.",
                "Para casos excepcionales (por ejemplo, adelantar trabajo de días de licencia) abrí «Otra fecha» antes de firmar: queda registrado en el historial.",
                "En «Revisar», arriba está la referencia de colores: verde se agrega, rojo tachado se quita.",
                "La primera vez que firmás en la sesión el sistema te pide tu PIN de 4 números. Después no lo vuelve a pedir hasta que salgas.",
                "Con la nota a la firma, arriba queda fija una barra con Firmar, Devolver, Ver documento, Descargar e Historial; equipo, asesor y prioridad quedan plegados en «Datos de la nota».",
                "En «Revisar», «Deshacer» (o Ctrl+Z) vuelve atrás el último paso, «Restaurar original» deja un párrafo como estaba y «Restaurar todo el original» hace lo mismo con todo el documento; nada se guarda hasta «Guardar».",
                "Después de firmar se abre solo el siguiente documento pendiente (por prioridad y antigüedad), con el avance «Documento 3 de 27 · Restan 24». Al terminar, el sistema avisa que no quedan más y ofrece revisar los documentos pendientes de control (por prioridad) o ir a la bandeja, al despacho o al inicio. El despacho nunca se abre solo.",
                "«Firmar en serie» abre una nota tras otra para firmar seguido.",
                "En la ficha podés cambiar el equipo y el asesor responsables de una nota; queda registrado quién lo cambió, el valor anterior y el nuevo."]),
            ("PIN de firma", [
                "Si todavía no tenés PIN, el sistema te pide crearlo la primera vez que firmás (4 números, no iguales ni seguidos).",
                "Para cambiarlo: Mi perfil → PIN de firma. Si alguien lo olvida, se borra desde Administración → Seguridad → PIN de firma y crea uno nuevo.",
                "5 intentos fallidos lo bloquean 15 minutos y avisan a la Dirección."]),
            ("Datos para decidir", [
                "Datos → Tablero: notas, firmas, tiempos, viajes y unidades más visitadas del período, y las unidades con notas pero sin visitas.",
                "Por unidad: todo lo de una unidad penal (notas, visitas, pedidos) con descarga en Excel para el informe.",
                "Mapa de temas: qué pedidos se repiten en qué unidad y en qué meses.",
                "Datos publicables: tablas sin datos personales (las celdas chicas se ocultan) listas para publicar.",
                "Uso del sistema: por área, quién entra y quién no, para saber dónde hace falta capacitación.",
                "Reportes programados: se generan solos los lunes (y el día 1) y quedan para descargar."]),
            ("Usuarios: qué sistema ve cada persona", [
                "En Administración → Usuarios, los botones «Notas» y «Autos PPN» de cada fila habilitan o deshabilitan cada sistema (también en «Editar»).",
                "Con Autos PPN pedido como prioritario y sin vehículos libres, el viaje llega a la Dirección General para «Dar prioridad» o «No corresponde»."]),
            ("Documentos de referencia de la IA", [
                "Administración → IA → Documentos de referencia: carpetas y subcarpetas (con el nombre que quieras) con documentos ya firmados.",
                "Se suben de a varios o una carpeta entera con sus subcarpetas; también se pueden copiar con el Explorador en la carpeta que indica la pantalla.",
                "La IA aprende de ellos los nombres, siglas y términos del organismo (no los «corrige»), propone a qué carpeta se parece cada nota nueva y toma el tono para las mejoras de redacción. «Retirar» lo deja de usar sin borrarlo."]),
            ("Funciones de Mesa de Entradas (excepcional)", [
                "La Dirección General (Director y Subdirector/a) puede, excepcionalmente, ejercer las funciones del área Mesa de Entradas: «Despacho del día», números y PDF, despacho, envíos, numeración y la carga «Solo Mesa de Entradas».",
                "Todo lo que se hace así queda en el historial con la marca «excepcional: función de Mesa de Entradas»."]),
            ("Administración", [
                "Usuarios y equipos, inteligencia artificial, respaldos, salud del sistema (con avisos cuando algo falla), conexión cifrada y PIN de firma."]),
        ],
        "atajos": [("F", "insertar firma"), ("A / R", "aceptar / rechazar cambio en Revisar"), ("N / P", "siguiente / anterior cambio")],
    },
    "mesa": {
        "titulo": "Mesa de Entradas",
        "intro": "Numerás, preparás el PDF y despachás las notas firmadas; registrás entregas en mano y respuestas.",
        "secciones": [
            ("Despacho del día", [
                "En «Despacho del día» están las notas firmadas. El sistema propone el número de nota (del registro de Mesa) y el expediente si lo encuentra en el texto: revisalos y corregilos si hace falta.",
                "Con los números cargados se prepara la copia con números y el PDF.",
                "«Registrar despacho» marca la nota como despachada. Si es parte de un bloque, se despacha el bloque completo.",
                "La primera vez que despachás en la sesión se te pide tu PIN de 4 números (si no lo tenés, lo creás en ese momento)."]),
            ("Solo Mesa de Entradas", [
                "Al subir un Word, «Solo Mesa de Entradas» (viene marcado) lo deja «para remitir»: sin control, coordinación ni firma, y sin IA.",
                "Se numera, se genera el PDF y se despacha igual que una nota firmada; el número se inserta en una copia y el original queda intacto.",
                "Se carga el .docx (no el .doc viejo) porque el número se inserta en el documento. Para un trámite con firma elegí «Normal»."]),
            ("Después del despacho", [
                "Las notas para personas detenidas se entregan en mano: registrá la entrega (quién, cuándo y, si fue en un viaje, cuál).",
                "Cuando llega la respuesta, cargala en la nota: avisa al asesor y a la coordinación."]),
            ("Expedientes", ["Buscá por LPU, DNI, N.º de expediente o nombre; los campos se combinan.",
                             "Después de buscar, «Guardar esta búsqueda» la deja arriba como atajo (por persona)."]),
            ("Base de personas detenidas", [
                "«Importar planilla» toma un Excel o CSV con al menos nombre y DNI o LPU. Hay que indicar la fuerza que mandó el listado (si no está, se crea) y la fecha del listado. Primero muestra qué personas se dan de alta, cuáles se actualizan y qué filas tienen error, y nada se guarda hasta confirmar.",
                "El alojamiento solo cambia si el listado es más nuevo que el último registrado; si es anterior, se conserva y queda anotado. Un lugar que no está cargado se vincula a una unidad existente o queda con el nombre del listado.",
                "«Traer de las planillas de Mesa» hace lo mismo con las planillas de expedientes (que no se modifican).",
                "La baja pide un motivo (egreso, traslado de jurisdicción, carga duplicada) y se puede reactivar. Exportar respeta la búsqueda y los filtros de la lista."]),
        ],
        "atajos": [("D", "registrar despacho")],
    },
    "traslados": {
        "titulo": "Coordinación de traslados",
        "intro": "Asignás chofer y vehículo, seguís el día y planificás la semana.",
        "secciones": [
            ("Planificador y vehículos", [
                "El planificador muestra los pedidos agrupados por lugar de destino. Marcá los del mismo día y «Unificar»: al mismo destino quedan en un viaje; a destinos distintos, en una salida con el mismo chofer y vehículo.",
                "En «Choferes y vehículos» das de alta un vehículo, lo mandás a taller, lo suspendés (con motivo) o lo eliminás; un eliminado se puede recuperar.",
                "Si al cargar un viaje no quedan vehículos, el sistema avisa; los prioritarios los revisa la Dirección General y, si da prioridad, te llega el aviso para reasignar."]),
            ("El día", [
                "«Tablero del día»: viajes sin chofer, asignados, en curso y realizados.",
                "«Asignar choferes»: solo los pedidos de auto pendientes, de todos los días. Marcás los que van juntos, elegís chofer y vehículo y «Asignar».",
                "El sistema sugiere chofer y vehículo según disponibilidad y lugares; decidís vos."]),
            ("Nombres que no coinciden con un usuario", [
                "Si un pasajero figura «sin usuario» porque su nombre se cargó distinto (por ejemplo «L.Aguilar»), en el viaje elegí «es el usuario…» y tocá «Vincular»: el sistema propone primero los nombres parecidos. Se vinculan todos sus viajes.",
                "En «Choferes y vehículos», un chofer cargado solo por su nombre se vincula con su usuario; si el usuario ya tenía ficha, se unifican y sus viajes, licencias y reservas pasan a esa ficha. La ficha vieja queda en Baja con la constancia."]),
            ("La semana", [
                "«Planificador»: arma las salidas de la semana agrupando viajes al mismo destino.",
                "«Licencias» y «Reservas» bloquean choferes y vehículos para que no se sugieran."]),
            ("Flota", [
                "«Panel de flota»: vencimientos y novedades de cada vehículo. En la ficha, «Cargar» registra combustible, vales, novedades mecánicas y kilometraje.",
                "Cada novedad mecánica avanza por estados: cargada, reportada al área responsable, reparación solicitada, en reparación y reparada (o no requiere reparación, con motivo). Podés poner el vehículo «En taller» al mismo tiempo."]),
            ("Tablero de Dirección y entrevistas", [
                "El «Tablero de Dirección» resume viajes realizados, cobertura AMBA y resto del país, alertas y avance de metas por equipo y trimestre.",
                "En Más → «Equipos del historial» vinculás los equipos de los viajes migrados del Excel con los equipos del sistema (se puede deshacer)."]),
            ("Presentismo", ["Autos PPN → Presentismo: semana, mes, por persona y cruce con la planilla de Personal."]),
        ],
        "atajos": [],
    },
    "personal": {
        "titulo": "Área de Personal",
        "intro": "Controlás quién estuvo de viaje para el presentismo y lo cruzás con la planilla de Personal.",
        "secciones": [
            ("Reportes", [
                "Semana: quién viajó cada día. Mes: un cuadro por persona con los días de viaje. Por persona: todos los viajes de alguien en un período.",
                "El Excel semanal y el mensual se generan solos (lunes y día 1 a las 8) y te llega un aviso para descargarlos."]),
            ("Cruce con la planilla de Personal", [
                "En «Cruce» subís la planilla del mes (una fila por persona y día con su novedad, o una columna por día con el código P, A, L, C...).",
                "El sistema marca: quien viajó pero figura ausente o con licencia, quien figura en comisión sin viaje cargado, y los nombres que no encontró.",
                "No corrige nada solo: es una lista para revisar con la coordinación."]),
        ],
        "atajos": [],
    },
    "chofer": {
        "titulo": "Choferes",
        "intro": "Ves tus viajes asignados, la hoja de ruta y registrás horarios y novedades.",
        "secciones": [
            ("Mis viajes", [
                "En Autos PPN ves tus viajes del día y de la semana con destino, horario y pasajeros.",
                "La hoja de ruta se imprime desde el desplazamiento.",
                "Al volver, registrá el horario real, los kilómetros y cualquier novedad.",
                "Las cargas de combustible y las novedades mecánicas del vehículo se registran en su ficha («Cargar»)."]),
        ],
        "atajos": [],
    },
}

VIDEOS = [
    {"id": "subir-nota", "titulo": "Subir una nota y enviarla a Coordinación", "roles": ["asesor"], "duracion": "30 s"},
    {"id": "ficha-nota", "titulo": "La ficha de la nota: etapas, asistentes y seguimiento", "roles": ["asesor", "coordinador", "direccion"], "duracion": "30 s"},
    {"id": "revisar-firmar", "titulo": "Revisar las correcciones y firmar con PIN", "roles": ["direccion"], "duracion": "30 s"},
    {"id": "despacho", "titulo": "Despacho del día: números, PDF y despacho con PIN", "roles": ["mesa"], "duracion": "30 s"},
    {"id": "solicitar-viaje", "titulo": "Solicitar un viaje en cuatro pasos", "roles": ["asesor", "coordinador", "traslados"], "duracion": "30 s"},
    {"id": "tablero-dia", "titulo": "Tablero del día de traslados", "roles": ["traslados"], "duracion": "30 s"},
    {"id": "datos-direccion", "titulo": "Datos para decidir: tablero, unidad y mapa de temas", "roles": ["direccion"], "duracion": "30 s"},
    {"id": "consultar", "titulo": "Preguntar al archivo en lenguaje común", "roles": ["asesor", "coordinador", "direccion", "mesa"], "duracion": "30 s"},
    {"id": "presentismo", "titulo": "Presentismo y cruce con la planilla de Personal", "roles": ["personal", "traslados"], "duracion": "30 s"},    {"id": "nueva-version", "titulo": "Corregir una nota rechazada y subir la nueva versión", "roles": ["asesor", "coordinador"], "duracion": "30 s"},
    {"id": "armar-bloque", "titulo": "Armar un bloque: cuándo sí y cuándo no", "roles": ["asesor", "coordinador", "mesa"], "duracion": "30 s"},
    {"id": "firma-serie", "titulo": "Firmar en serie", "roles": ["direccion"], "duracion": "30 s"},
    {"id": "biblioteca", "titulo": "Biblioteca: modelos de nota y glosario", "roles": ["asesor", "coordinador"], "duracion": "30 s"},
    {"id": "entrega-en-mano", "titulo": "Entrega en mano a personas detenidas", "roles": ["mesa", "asesor"], "duracion": "30 s"},
    {"id": "respuesta-plazos", "titulo": "Respuestas y plazos de respuesta", "roles": ["mesa", "asesor", "coordinador"], "duracion": "30 s"},
    {"id": "expedientes", "titulo": "Buscar expedientes y el aviso de LPU", "roles": ["mesa", "coordinador", "direccion"], "duracion": "30 s"},
    {"id": "viajes-frecuentes", "titulo": "Viajes frecuentes y su confirmación", "roles": ["asesor", "coordinador", "traslados"], "duracion": "30 s"},
    {"id": "medios-propios", "titulo": "Viajes por medios propios y fuera de plazo", "roles": ["asesor", "coordinador"], "duracion": "30 s"},
    {"id": "choferes", "titulo": "Choferes: viajes del día, hoja de ruta y cierre", "roles": ["chofer", "traslados"], "duracion": "30 s"},
    {"id": "planificador", "titulo": "Planificador, licencias y reservas", "roles": ["traslados"], "duracion": "30 s"},
    {"id": "mi-perfil", "titulo": "Mi perfil: clave, PIN y apariencia", "roles": ["asesor", "coordinador", "direccion", "mesa", "traslados", "personal", "chofer"], "duracion": "30 s"},
    {"id": "avisos", "titulo": "Avisos: qué llega y cómo se resuelve", "roles": ["asesor", "coordinador", "direccion", "mesa", "traslados", "personal", "chofer"], "duracion": "30 s"},
    {"id": "usuarios-equipos", "titulo": "Administración: usuarios y equipos", "roles": ["direccion"], "duracion": "30 s"},
    {"id": "asignar-choferes", "titulo": "Asignar chofer y vehículo a los pedidos pendientes", "roles": ["traslados"], "duracion": "30 s"},
    {"id": "flota", "titulo": "Panel de flota: cargas y novedades mecánicas con estado", "roles": ["traslados", "chofer"], "duracion": "40 s"},
    {"id": "tablero-direccion", "titulo": "Tablero de Dirección, entrevistas y metas", "roles": ["direccion", "traslados"], "duracion": "45 s"},
    {"id": "detenidos-importar", "titulo": "Importar un listado de personas detenidas (fuerza, fecha y lugares)", "roles": ["mesa", "direccion"], "duracion": "40 s"},
    {"id": "busqueda-colectiva", "titulo": "Búsqueda colectiva de personas detenidas", "roles": ["asesor", "coordinador", "direccion", "mesa"], "duracion": "30 s"},
    {"id": "salud-respaldos", "titulo": "Salud del sistema y respaldos", "roles": ["direccion"], "duracion": "30 s"},
]


TODOS = {"titulo": "Todos los videos", "intro": "Los videos de todos los roles juntos. Cada uno dura alrededor de medio minuto.",
         "secciones": [], "atajos": []}


def rol_de(usuario):
    if not usuario:
        return "asesor"
    if usuario["rol"] in auth.ROLES_DIRECCION:
        return "direccion"
    if usuario["rol"] in auth.ROLES_MESA:
        return "mesa"
    if usuario["rol"] == auth.ROL_CHOFER:
        return "chofer"
    if usuario.get("personal"):
        return "personal"
    if usuario.get("coordina_traslados"):
        return "traslados"
    if usuario["rol"] == auth.ROL_COORDINADOR:
        return "coordinador"
    return "asesor"


def videos_de(rol):
    salida = []
    for v in VIDEOS:
        if rol == "todos" or rol in v["roles"]:
            v = dict(v, existe=(CARPETA_VIDEOS / f"{v['id']}.webm").is_file(),
                     miniatura=f"{v['id']}.jpg" if (CARPETA_VIDEOS / f"{v['id']}.jpg").is_file() else f"{v['id']}.png")
            salida.append(v)
    return salida
