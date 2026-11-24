# Arquitectura de dgpdh_web

Sistema de gestión de la Dirección General de Protección de Derechos Humanos (PPN). Tiene cuatro áreas: **Notas a la firma**
(corrección con IA local, firma y despacho), **Autos PPN** (viajes, flota y presentismo), **Personas detenidas** (base cargada
desde los listados de las fuerzas) y **Metas** (planificación y seguimiento por equipo).

Este documento describe cómo está hecho hoy, hacia dónde va y con qué reglas. Las decisiones puntuales están en `docs/adr/`.
El mapa de quién importa a quién se regenera con `python herramientas/mapa_dependencias.py` (`docs/mapa_dependencias.md`).

## Reglas que no se negocian
1. **IA solo local** (Ollama en la misma máquina o en la red interna). El sistema no transmite información al exterior por su cuenta.
2. **La IA propone; decide una persona.** Toda propuesta de IA guarda qué modelo la hizo, sobre qué y quién la aceptó o la rechazó.
3. **Nada se borra físicamente.** Las bajas son lógicas y las anulaciones se pueden revertir.
4. **Trazabilidad:** toda acción que cambia datos deja quién, qué, cuándo, el valor anterior y el nuevo.
5. **Cambios en producción solo con «genera nueva versión»:** primero se preparan y se prueban, después se instalan juntos.

## Cómo está hoy (noviembre 2026)
- FastAPI + Jinja2 (HTML servido por el servidor, con htmx), sqlite3 en modo WAL y sin ORM. Windows hoy, Ubuntu 24.04 en la migración. **Python 3.12 o más nuevo.**
- Módulos en la raíz del proyecto, con las capas mezcladas. `main.py` (≈5.700 líneas) concentra unas 140 rutas; los demás
  sistemas registran sus rutas con `registrar(app, ctx)` en los `rutas_*.py`. `database.py` crea el esquema y hace las migraciones al
  arrancar, con `ALTER TABLE` dentro de try/except.
- Trabajos en segundo plano: `automatico.Trabajador` (tabla `trabajos_ia`, ventana nocturna) y la cola de corrección de notas
  (en memoria; se reconstruye al arrancar a partir de las notas en PROCESANDO).
- La auditoría está repartida en `historial_estados`, `traslados_auditoria`, `detenidos_cambios`, `unidades_cambios`,
  `accesos_documentos` e `intentos_login`.
- Los permisos están en funciones de `auth.py` y de cada módulo (`puede_coordinar`, `puede_ver`, `puede_mantener`…).

## Hacia dónde va
```
dgpdh/                  paquete nuevo, tipado y con mypy estricto
  errores.py            errores explícitos (DatoInvalido, NoEncontrado, PermisoDenegado, Conflicto…)
  dominio/              entidades, estados y reglas puras: sin base ni web, fáciles de probar
  persistencia/         conexión, migraciones numeradas, repositorios (SQL portable a PostgreSQL)
  servicios/            casos de uso (firmar, asignar viaje, importar listado): abren la transacción, controlan el permiso, auditan
  ia/                   cliente Ollama único, cola persistente de trabajos, evaluación y trazabilidad
  integracion/          puertos hacia ppn-sso, ppn-mesa-entradas y ppn-storage, con adaptadores deshabilitados por defecto y bandeja de salida
  seguridad/            matriz de permisos rol × acción, sesiones, PIN, auditoría encadenada
  web/                  routers por sistema (APIRouter); /api/v1 documentada con OpenAPI
```
**Sentido de las dependencias:** web → servicios → dominio; servicios → persistencia, ia, integracion y seguridad.
El dominio no importa nada del resto.

**Cómo se migra:** gradualmente (ADR-0001). Cada módulo histórico pasa a `dgpdh/` con pruebas de caracterización previas. El
módulo viejo queda como reexportación mientras alguien lo use, y cada paso se instala como una versión aparte.

## Calidad y verificación
- `python herramientas/verificar.py` corre ruff (falla solo con avisos nuevos respecto de la línea de base), mypy estricto
  sobre `dgpdh/` y pytest (`pruebas/`, que también corre los scripts históricos de `tests/`).
- Cada cambio trae su prueba: el caso normal, los casos borde y los permisos (quién puede y quién recibe 403).
- Antes de instalar se comparan los archivos de la PC con la última versión entregada, para no pisar cambios hechos allá.

## Datos personales
La base de personas detenidas tiene datos sensibles (Ley 25.326). La ven y la cargan las personas con el sistema de Notas
habilitado, salvo los choferes. La administran la Dirección y Mesa de Entradas, y la mantiene quien tenga ese permiso individual
(ADR-0006). Los informes que se devuelven a las fuerzas llevan la leyenda de reserva. Los datos reales no salen de la PC o del
servidor del organismo, tampoco para pruebas: las pruebas usan datos ficticios.
