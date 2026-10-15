# TODO - Arquitectura de spoty

Plan basado en [ARCHITECTURE.md](ARCHITECTURE.md) y
[DESIGN_ASSUMPTIONS.md](DESIGN_ASSUMPTIONS.md).

## Alcance y forma de trabajo

Un usuario, una cuenta Spotify y un repositorio. Objetivo: monolito modular con
servicios compartidos por Streamlit, CLI y GitHub Actions. El diagrama representa
flujos de datos; la UI consultara PostgreSQL mediante servicios de consulta,
no ejecutando SQL dentro del renderizado.

Completar los pasos en orden, mediante cambios pequenos. Cada paso indica su
criterio de cierre. Marcarlo terminado solo tras verificarlo; este documento
no implica que se haya ejecutado ninguna refactorizacion.

## Fase 1 - Pruebas y contratos

- [x] **1. Registrar la base de pruebas actual.** Ejecutar las suites de reproduccion
	y laboratorio y anotar comandos y fallos previos. Cierre: se conoce la base de
	comparacion sin cambiar comportamiento ni datos reales.
- [x] **2. Incorporar CI de pruebas y calidad.** Crear un workflow para push y PR
	con las pruebas existentes y un chequeo de calidad acotado; usar mocks para
	servicios externos. Cierre: CI funciona sin tokens Spotify ni acceso a Neon.
- [x] **3. Definir contratos de error.** Separar respuesta vacia valida de error de
	autorizacion, timeout, 429 y respuesta invalida; conservar la causa y Retry-After.
	Cierre: pruebas de contrato distinguen esos casos y los consumidores afectados
	se adaptan juntos, sin convertir errores en ceros o falsos favoritos.
- [x] **4. Anadir logging minimo.** Registrar operacion, duracion, tipo de fallo y
	run_id, una vez por incidente y sin secretos. Cierre: se puede diagnosticar un
	fallo simulado sin duplicar tracebacks ni revelar credenciales.

### Base de pruebas registrada - 2026-10-14

Comandos ejecutados desde la raiz del repositorio, en procesos separados:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -p test_reproduction.py -v
.\venv\Scripts\python.exe -m unittest streamlit_lab.test_blocks -v
```

Entorno local: Python 3.13.12, Streamlit 1.63.0, Spotipy 2.26.0,
psycopg 3.3.4, pandas 3.0.5 y matplotlib 3.11.2.

| Suite | Ejecutadas | Correctas | Fallos | Tiempo unittest | Salida |
| --- | --- | --- | --- | --- | --- |
| Reproduccion | 41 | 40 | 1 | 0.232 s | Codigo 1 |
| Laboratorio | 32 | 32 | 0 | 2.494 s | Codigo 0 |

Fallo previo resuelto: `test_track_separators_use_black_and_white_lines_in_both_orientations`.
La [asercion](tests/test_reproduction.py) exigia `filter: blur(6px)`,
pero el [CSS actual](app_views/reproduction.py) define `filter: blur(4px)`.
Se ha corregido la expectativa a 4 px sin modificar la apariencia ni omitir la
prueba. Verificacion posterior, 2026-10-14: el mismo comando de reproduccion
ejecuta 41 pruebas en 0.243 s, todas correctas, con codigo de salida 0.
La tabla conserva los resultados iniciales como referencia historica.

Avisos observados: `missing ScriptRunContext` en AppTest/bare mode; no producen
fallos. Los mensajes `device unavailable` corresponden a excepciones simuladas
en pruebas que pasan, no a peticiones fallidas contra Spotify real.

Las pruebas de reproduccion usan mocks del cliente y snapshots temporales; las
del laboratorio ejecutan su demo con AppTest. No se han ejecutado sincronizaciones,
acciones reales de reproduccion ni migraciones. En ese paso no se cambio codigo funcional.
La ejecucion inicial usaba el entorno local; el aislamiento de configuracion se
ha comprobado posteriormente en el paso 2, como se detalla a continuacion.

### CI incorporada - paso 2, 2026-10-14

Workflow: [tests.yml](.github/workflows/tests.yml), para push, pull request y
ejecucion manual, con Python 3.12 y 3.13. No usa secretos, servicios de DB ni jobs
de sincronizacion. Instala las dependencias existentes y ejecuta ambas suites
en procesos separados; la suite del laboratorio tambien corre si falla reproduccion.
El control de calidad inicial es de sintaxis, no un lint general ni cobertura.

```powershell
.\venv\Scripts\python.exe -m compileall -q app_views services register spotifyapi streamlit_lab tests streamlit_app.py config.py
```

La configuracion usada al importar reproduccion en los tests es ficticia y
aislada mediante mocks; no se modifica config.py de produccion. Validacion local
en una copia temporal del codigo sin .env, sin variables de credenciales y con
socket.connect bloqueado: sintaxis correcta, 41/41 pruebas de reproduccion y
32/32 de laboratorio. La copia se elimino y el entorno se restauro al terminar.

Pendiente de observar la primera ejecucion remota: esta comprobacion usa el
entorno instalado de Windows/Python 3.13.12, no una instalacion limpia en Ubuntu
ni una ejecucion local con Python 3.12. El workflow verificara esos entornos al
publicar los cambios; no se han enviado commits ni disparado Actions.

### Contratos de error implementados - paso 3, 2026-10-14

[errors.py](spotifyapi/errors.py) define errores de autorizacion, timeout,
indisponibilidad, respuesta invalida, rechazo HTTP y limite de peticiones.
El cliente conserva la causa con exception chaining y expone http_status,
accion y retry_after cuando corresponde. Los mensajes publicos no incluyen
el contenido sensible de excepciones del proveedor.

Una lectura correcta puede devolver una lista vacia o ausencia de playback;
un fallo no devuelve None ni False. Las lecturas validan objetos/listas y la
consulta de favoritos exige un booleano por ID, sin fabricar falsos negativos.
Un 429 inicia cooldown; solicitudes durante ese periodo generan el mismo tipo
de error con el tiempo restante. El transporte no reintenta automaticamente
peticiones: un timeout de comando puede tener resultado remoto desconocido.

La vista conserva el ultimo estado valido y lo marca como desactualizado;
una lectura fallida no sobrescribe caches ni snapshots. El polling solo pide
rerun al cambiar entre estado valido y fallo. Los errores de acciones se muestran
sin actualizar favoritos como si hubiera exito; resumen informa de fallos Spotify
durante el registro. El servicio propaga los errores Spotify y los scripts fallan
antes de continuar con datos ficticiamente vacios. Los fallbacks de restauracion
se limitan a rechazos 400/404, nunca a timeout, autorizacion o 429.

Validacion: 54/54 pruebas de reproduccion y contratos, 32/32 de laboratorio,
sintaxis correcta y sin diagnosticos nuevos en los archivos modificados.
Ambas suites pasan en copia temporal sin .env ni credenciales y con red bloqueada.
Una prueba de sincronizacion comprueba que fallar al descargar una pagina no
alcanza el calculo de bajas. La reconciliacion completa, offsets parciales y
escrituras atomicas siguen pendientes de los pasos 5 y 10; no se han probado
integraciones reales ni se han ejecutado sincronizaciones contra Spotify/Neon.

### Logging minimo implementado - paso 4, 2026-10-14

[diagnostics.py](spotifyapi/diagnostics.py) configura un logger propio con
salida JSON a consola, sin alterar el root logger. Registra operacion, proveedor,
duracion, categoria, run_id y estado HTTP/Retry-After cuando existen. El run_id
identifica una operacion o incidente dentro del proceso, no un job completo.

Solo se serializan metadatos aprobados; no se incluyen mensajes de excepciones,
argumentos de peticiones, respuestas, tokens, codigos OAuth ni DSN. Las causas
originales siguen disponibles en las excepciones para el codigo, pero no se
vuelcan como traceback en este logger. Una excepcion ya registrada no se vuelve
a registrar al propagarse por otra capa.

Cobertura inicial: inicializacion/token, operaciones y validacion de respuestas
Spotify, y escrituras locales del servicio de favoritos. Los errores repetidos
se agrupan por operacion/categoria hasta recuperacion, que informa del contador
y conserva el ID. Exitos normales se registran solo en DEBUG; por defecto INFO.
SPOTY_LOG_LEVEL permite cambiar el nivel antes de iniciar el proceso. Uso y
ubicacion de los registros documentados en [README.md](README.md).

Se mantienen la politica de reintentos y los avisos existentes de la UI: no se
agregan popups ni avisos por cada evento de log. No se sustituye todo el output
historico del repositorio ni el logging propio de bibliotecas externas.

Validacion aislada: 61/61 pruebas de reproduccion/contratos/logging y 32/32 del
laboratorio, sintaxis correcta y sin diagnosticos nuevos. Las pruebas cubren
secretos ficticios, errores DB, autenticacion, respuestas invalidas, cooldown,
duplicacion entre capas, repeticion y recuperacion. Ejecutadas sin .env ni
credenciales, con red bloqueada y sin modificar datos reales.

## Fase 2 - Sincronizacion segura

- [x] **5. Proteger la descarga de favoritos.** Descargar y validar todas las
	paginas antes de calcular bajas; no reconciliar desde un offset parcial.
	Cierre: pruebas de fallo inicial/intermedio, 429 y respuesta malformada impiden
	borrados, y una biblioteca vacia completa se reconoce como valida.
- [x] **6. Unificar la conversion de datos Spotify.** Reutilizar el mapeo de tracks,
	albums y artists en registro y recuperacion de metadata. Cierre: campos
	opcionales ausentes no borran enriquecimiento previamente disponible.
- [x] **7. Verificar fechas e idempotencia.** Interpretar instantes Spotify en UTC,
	aplicar zona configurable al mostrar y conservar unicidad de eventos en DB.
	Cierre: repetir una sincronizacion no duplica eventos; revisar una muestra
	historica antes de decidir si hace falta corregir datos existentes.

Verificacion del paso 7, 2026-10-15: los timestamps Spotify ahora se parsean
como instantes aware y se almacenan como epoch UTC; la zona de presentacion se
configura con `SPOTY_DISPLAY_TIMEZONE` (por defecto `Europe/Rome`). Una muestra de
solo lectura comparo los 50 eventos mas recientes de Neon con Spotify: 43 IDs
coincidieron y los 43 timestamps almacenados estaban 7200 segundos antes. El
historial confirma que el registro aplicaba `+02:00` desde la primera version.
Tras dry-run, sin colisiones potenciales, se corrigieron 10.606 filas en una
transaccion, sumando 7200 segundos. La verificacion posterior mantuvo el conteo
y encontro 47 coincidencias recientes con Spotify, todas con delta de 0 segundos.

Backup previo fuera del repo: `%LOCALAPPDATA%\spoty\backups\listening_events_before_utc_fix_20261015T085727Z_61a05cde.csv`.
SHA-256: `87a10b0126047eb55cc31dc44ebd043308a678acbc12e17090f52e99112435ee`.

## Fase 3 - Persistencia y transacciones

- [x] **8. Acotar la vida de las conexiones.** Introducir apertura/cierre explicitos
	y permitir que operaciones relacionadas compartan una conexion. Cierre:
	conexiones y cursores se cierran tanto con exito como ante una excepcion.
- [x] **9. Separar esquema y funcionamiento.** Sustituir DDL del constructor por
	preparacion y migraciones versionadas; comprobar el esquema existente antes
	de establecer la base inicial. Cierre: una lectura no crea tablas y se prueban
	tanto una DB nueva como la actualizacion de una DB de prueba existente.
- [x] **10. Aplicar transacciones cortas.** Guardar catalogo y evento por unidad
	atomica y aplicar altas/bajas de favoritos juntas tras completar la descarga.
	Cierre: un fallo intermedio hace rollback; no hay transacciones abiertas durante
	HTTP ni esperas, y se mantienen las restricciones contra duplicados.
- [x] **11. Controlar sincronizaciones simultaneas.** Coordinar reconciliaciones
	y acciones locales de favoritos entre UI, CLI y jobs. Distinguir exito Spotify
	de replica DB pendiente. Cierre: una prueba de solapamiento no pierde favoritos
	y un fallo local no se comunica como fracaso remoto ni exito completo.

Implementacion de Fase 3, 2026-10-15: `SimpleDB` cierra recursos con context
manager, usa autocommit para operaciones simples y expone transacciones explicitas.
El runner `python -m register.migrations` versiona y valida el esquema inicial;
registro, metadata y reconciliacion agrupan escrituras relacionadas. La coordinacion
de favoritos usa generaciones y revisiones locales para descartar snapshots viejos,
evitando mantener conexiones, transacciones o locks durante llamadas HTTP. La UI
diferencia un cambio Spotify exitoso con replica Neon pendiente.

La migracion `0001_initial_schema` se aplico en Neon tras validar el esquema previo.
La misma migracion se probo en un esquema vacio temporal dentro de una transaccion
revertida. Verificacion posterior de solo lectura: 10.666 eventos unicos, 2.863
favoritos y estado singleton inicializado; el preflight previo habia observado
10.606 eventos, asi que los 60 eventos adicionales aparecieron durante el intervalo
y no se atribuyen a la migracion.

Validacion local: 83 pruebas de producto, 32 de laboratorio, compilacion y Pylance
sin errores. Los tests cubren rollback, transacciones por evento, reconciliacion
atomica, snapshots obsoletos, cambios locales durante la descarga y replica pendiente.

## Fase 4 - Servicios e interfaz

- [x] **12. Extraer el servicio de sincronizacion.** Reunir registro y favoritos
	bajo casos de uso reutilizables con resultados completado/parcial/aplazado/fallido.
	Cierre: CLI y Streamlit llaman a los mismos servicios; los entrypoints solo
	configuran dependencias y presentan resultados.
- [x] **13. Conectar GitHub Actions al servicio.** Mantener jobs separados de CI,
	ejecutar los entrypoints compartidos y resumir contadores/resultados. Cierre:
	un trabajo fallido o incompleto que incumple su objetivo termina con codigo no
	cero; los secretos solo se necesitan en jobs de integracion real.
- [x] **14. Extraer las consultas del resumen.** Encapsular SQL y acceso a DB en
	persistencia/servicio de consulta, reutilizando reglas con los analisis donde
	corresponda. Cierre: la vista renderiza datos sin abrir conexiones ni construir SQL.
- [x] **15. Extraer el controlador de reproduccion.** Separar coordinacion, cache,
	snapshots y reloj del renderizado; conservar polling, restauracion y lotes.
	Cierre: la logica se prueba sin Streamlit y la interfaz conserva su comportamiento
	y composicion en escritorio y movil.
- [x] **16. Mostrar fallos sin ruido.** Conservar el ultimo dato valido con su hora,
	mostrar un aviso por incidente y ofrecer detalle desplegable. Cierre: los reruns
	no multiplican avisos y cada accion explicita comunica fallo, incertidumbre o
	replica pendiente; no reintentar a ciegas comandos no idempotentes.

Implementacion de Fase 4, 2026-10-15: registro y favoritos delegan en
`SynchronizationService`, que devuelve estados completado/parcial/aplazado/fallido,
contadores y codigo de salida. CLI imprime JSON; Actions invoca los mismos
entrypoints y valida secretos sin mostrarlos. CI descubre todos los
`tests/test_*.py`. Las queries del resumen viven en `SummaryQueryService`; la vista
mantiene datos cacheados durante fallos. `PlaybackController` coordina polling,
cache, snapshot, restauracion, cola y transporte sin depender de Streamlit. Los
avisos quedan agrupados por incidente, con detalles seguros y recuperacion visible.

Validacion: 101 tests de producto y 32 de laboratorio, compilacion y Pylance sin
errores. Suites ejecutadas localmente sin credenciales de Spotify ni Neon.

## Fase 5 - Estructura y configuracion

- [x] **17. Reorganizar y corregir el empaquetado.** Agrupar codigo por responsabilidad,
	separar componentes visuales de la demo, validar configuracion al usarla y alinear
	modulos publicados, dependencias y minimo Python. Mantener `ARCHITECTURE.md`
	como documento canonico de arquitectura, distinguir estado actual de propuestas y
	mantener los flujos actualizados. Cierre: instalacion limpia, imports sin
	HTTP/escrituras, analisis DB sin credenciales Spotify y comandos disponibles;
	actualizar enlaces, workflows y documentacion tras mover archivos.

Implementacion del paso 17, 2026-10-15: `pyproject.toml` declara Python `>=3.10`,
publica los modulos y paquetes reales, y ofrece Streamlit mediante el extra `ui`.
`requirements.txt` instala el proyecto editable con ese extra. Se normalizaron
los nombres de cuatro modulos CLI para que coincidan con sus entrypoints.

La validacion Neon ocurre al abrir conexiones, no al importar configuracion; las
credenciales Spotify se validan al crear el cliente. La demo de `streamlit_lab`
vive en `examples/` y sus pruebas en `tests/`. `ARCHITECTURE.md` distingue flujos
actuales de propuestas futuras y se actualizaron README, workflow CI y enlaces.

Cierre verificado: instalacion desde `requirements.txt`, wheel aislada con imports
bloqueando HTTP/DB, minimo Python y cinco entrypoints; analisis importable sin
credenciales Spotify; 137 pruebas, compilacion y diagnosticos sin errores. El
workflow conserva una linea vacia al final que `git diff --check` señala, sin
errores de YAML ni impacto funcional.

## Fase 6 - Enriquecimiento opcional

- [ ] **18. Convertir scripts de metadata en adaptadores.** Encapsular Last.fm,
	MusicBrainz y AcousticBrainz sin llamadas al importar ni tracks fijos. Cierre:
	aceptan identificadores, validan respuestas y se prueban con mocks; confirmar
	disponibilidad de AcousticBrainz y omitirlo si no es utilizable.
- [ ] **19. Crear el servicio de enriquecimiento.** Leer candidatos de PostgreSQL,
	consultar proveedores fuera de transacciones y persistir datos normalizados
	con origen y fecha, conservando respuestas originales cuando sea util.
	Priorizar generos, imagenes y fechas de lanzamiento; incluir popularidad,
	seguidores o audio features solo si se confirma una fuente fiable y accesible.
	Cierre: una prueba cubre DB -> proveedor -> DB sin sobrescribir datos fiables
	con vacios, y repetir el proceso no duplica resultados.
- [ ] **20. Habilitar enriquecimiento bajo demanda.** Ofrecer un comando y, si
	procede, una accion Streamlit; definir limites, reintentos y candidatos pendientes.
	Cierre: un proveedor caido no bloquea reproduccion ni registro de escuchas.

## Fase 7 - Futuro, tras validar las anteriores

- [ ] **21. Definir el primer caso de recomendacion.** Acordar entradas, resultado
	esperado y criterio de utilidad antes de elegir algoritmo. Cierre: existe un
	ejemplo verificable y una regla para datos insuficientes; no introducir ML por defecto.
- [ ] **22. Implementar una recomendacion minima.** Construir un motor separado
	del renderizado que lea datos mediante servicios y devuelva sugerencias explicables.
	Cierre: pruebas deterministas y una vista Streamlit permiten revisar el resultado;
	escribir playlists en Spotify queda fuera hasta aprobar ese caso de uso.
- [ ] **23. Medir y optimizar solo lo necesario.** Medir consultas, conexiones,
	memoria y llamadas API; evaluar indices, pooling, cache o tareas adicionales.
	Cierre: cada optimizacion responde a un cuello de botella medido y tiene
	comparacion antes/despues, sin romper integridad ni contratos.

## Fase 8 - Playlists, tras definir y validar el caso de uso

- [ ] **24. Definir la seleccion de playlists.** Acordar listas iniciales basadas
	en canciones destacadas y recientes, junto con filtros configurables y su
	comportamiento ante datos insuficientes. Cierre: ejemplos verificables y
	criterios de inclusion/exclusion antes de implementar consultas dinamicas.
- [ ] **25. Generar playlists revisables.** Reutilizar servicios de consulta y
	mostrar una vista previa antes de escribir en Spotify; definir como evitar
	duplicados y manejar fallos parciales. Cierre: pruebas deterministas cubren
	seleccion y errores, y ninguna playlist se modifica sin una accion explicita.
- [ ] **26. Automatizar actualizaciones si aportan valor.** Programar la
	actualizacion de playlists aprobadas mediante GitHub Actions, con limites,
	idempotencia y registro de resultados; no requiere un worker permanente.
	Cierre: ejecuciones repetidas son seguras y los fallos no borran ni reemplazan
	una playlist con resultados incompletos.

## Fuera de alcance por ahora

Multiusuario, microservicios, repositorios separados, ORM obligatorio, plataforma
externa de observabilidad y migracion general de TEXT JSON a JSONB. Mejoras
opcionales de la demo, como importar configuraciones JSON, no bloquean este plan.
