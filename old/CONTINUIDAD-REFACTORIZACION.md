# Continuidad de la refactorizacion CSV/Git

Actualizado: 2026-11-10. Este documento permite retomar el trabajo sin la
conversacion anterior. No contiene tokens ni autoriza publicaciones adicionales.

## 1. Objetivo y decisiones confirmadas

- Eliminar Neon/PostgreSQL como dependencia obligatoria del producto.
- CSV es el almacenamiento principal, no una exportacion auxiliar de SQL.
- Mantener Streamlit Community Cloud como despliegue de la app.
- Mantener capturas automaticas y consolidacion con GitHub Actions.
- Un repositorio de codigo con paquetes separados; no dividir el codigo en
  multiples repos por ahora.
- Un repositorio privado independiente de datos: `luiscaballeroramos/spoty-data`.
- Git normal con CSV particionados; no Git LFS, Releases ni respuestas originales
  descartadas para reducir volumen.
- Una cuenta inicialmente; todos los accesos nuevos llevan `account_key` explicito.
  Esto prepara separacion de cuentas, no implementa autenticacion multiusuario.
- Conservar todas las respuestas originales, procedencia, estados negativos y
  campos desconocidos de capturas aceptadas.
- Se permiten objetos/listas JSON dentro de celdas CSV y manifiestos JSON.
- Resumen/historico leen snapshots consolidados. Los lotes pendientes se muestran
  aparte y no se cuentan como escuchas consolidadas.
- Pulse mantiene consulta live; sus capturas deben conservarse para uso posterior.
- La frecuencia y ubicacion de los workflows se decidiran tras medir consumo.
- Trabajar paso a paso; preguntar antes de decisiones de comportamiento/coste.
- No migrar datos reales, retirar Neon ni cambiar la app desplegada hasta validar
  las superficies necesarias.

## 2. Fuentes de verdad y flujo

| Informacion | Autoridad |
| --- | --- |
| Reproduccion y favoritos actuales | Spotify |
| Historico capturado | Registros duraderos del repositorio de datos |
| Respuestas de proveedores | Capturas originales almacenadas con fecha/procedencia |
| Taxonomia editorial | Dataset propio validado/versionado; importacion/discovery CSV implementados |
| Inbox | Capturas duraderas pendientes, no snapshot listo para analisis |
| Snapshot consolidado | Vista validada del historico/catalogo/replicas |
| Proyecciones, graficos y cache | Derivados reconstruibles |

Flujo objetivo:

```text
Spotify/proveedores -> servicios de captura -> inbox en Git privado
                                             |
                                   Actions consolida
                                             |
                      datasets + archivo + receipts + manifiesto
                                             |
                           consultas -> analisis -> Streamlit
```

Una captura live puede mostrarse antes de consolidarse. Su fallo de persistencia
debe informarse separado del resultado live. Spotify y Git no forman una
transaccion distribuida: no prometer atomicidad entre ambos.

## 3. Producto operativo: todavia NO migrado

- Streamlit, scripts y Actions siguen usando los servicios originales.
- Neon sigue presente para favoritos, metadatos, taxonomia y consultas/analisis.
- Captura original de escuchas ya escribe CSV local, pero el workflow Registration
  no publica ese CSV: puede perderse al terminar el runner.
- Pulse original conserva la consulta SmarterPlaylists antes de existir catalogo.
- La pagina de metadatos actual consulta cobertura; no lanza enriquecimiento.
- Favoritos originales usan `generation` y `local_revision` SQL para evitar
  snapshots obsoletos. Los botones mutan Spotify y luego actualizan la replica.
- No se han modificado vistas/workflows productivos ni ejecutado migraciones.

## 4. Codigo implementado

El paquete nuevo permanece en `spoty/`, siguiendo el descubrimiento setuptools
existente. La propuesta futura `src/spoty/` no se ha aplicado globalmente.
`pyproject.toml` incluye el paquete nuevo; mantiene dependencias y diez entrypoints.

### Dominio y almacenamiento

- `spoty/domain/listening.py`: cuentas, tiempos UTC, JSON canonico, rechazo de
  secretos, validacion de catalogo e identidad SHA-256 de `ListeningBatch`.
- `spoty/domain/metadata.py`: `MetadataBatch`, estados/IDs de proveedores y
  adaptacion de resultados ya normalizados de enriquecimiento.
- `spoty/domain/favorites.py`: `FavoritesBatch`, operaciones/revisiones y
  validacion estricta de paginacion completa.
- `spoty/domain/taxonomy.py`: validacion de referencias/ciclos/status de mappings
  y resolucion de todas las familias raiz.
- `spoty/storage/csv_codec.py`: esquemas, NULL sin perdida, envelopes y despacho
  por tipo de lote; snapshot v6 con cobertura ligera que conserva huerfanos, taxonomia desde v4,
  receipts hasta v3.
- `spoty/storage/contracts.py`: protocolo `DataStore`, errores, validacion
  de rutas/commits y publicacion de lotes compartida.
- `spoty/storage/local_git.py`: repositorio bare local SHA-1, commits atomicos
  y compare-and-swap de ref; no utiliza el worktree del producto como datos.
- `spoty/storage/snapshots.py`: lecturas fijadas por commit, particiones,
  manifiestos v6 y compatibilidad explicita de snapshots v1-v5.
  Serializador atomico compartido por consolidacion y ediciones de taxonomia.
- `spoty/storage/github.py`: API GitHub de objetos Git, transporte HTTP inyectado,
  repositorio privado existente y branch inicializada, nunca force update.

### Servicios

- `spoty/services/consolidation.py`: aplica lotes y publica datasets, archivo,
  receipts y manifiesto en un commit; reintentos acotados ante concurrencia.
- `spoty/services/metadata.py`: reutiliza normalizacion/merge offline existentes
  de `services.smarterplaylists`; no duplica reglas ni importa SQL/HTTP.
- `spoty/services/pulse_queries.py`: contrato actual de filas de Pulse a partir
  de proyeccion ligera `track_audio`; reutiliza `services.pulse.build_pulse`.
- `spoty/services/favorites.py`: coordinacion duradera y replica ordenada;
  `FavoritesCoordinator` y `FavoriteQueryService`.
- `spoty/services/genre_taxonomy.py`: importacion aditiva de semillas y
  descubrimiento preview/apply; conserva decisiones manuales y semillas originales.
- `spoty/services/summary_queries.py`: resumen/historico/cobertura y etiquetas/
  familias desde un snapshot consolidado por cuenta/commit.
- `spoty/services/coverage.py`: proyeccion de campos no vacios por entidad/fuente,
  derivacion compartida y contrato de estadisticas sin cargar valores raw en v5/v6.
- `spoty/cli/credentials.py`: carga EXPLICITA de GH_TOKEN/GITHUB_TOKEN desde
  entorno o archivo local indicado; no importa config productiva ni muestra
  secretos ni carga configuracion Spotify/Neon al entorno.

### Ejemplos y pruebas

- `examples/csv_git_demo.py`: demo completa local con datos sinteticos y limpieza
  automatica de repositorio temporal.
- `examples/github_data_demo.py`: preflight real de solo lectura; publicacion
  sintetica requiere flag y namespace nuevo explicitamente.
- `tests/test_csv_git_prototype.py`: 33 pruebas de escuchas/catalogo.
- `tests/test_csv_metadata_prototype.py`: 23 pruebas de metadatos/Pulse.
- `tests/test_csv_favorites_prototype.py`: 30 pruebas de favoritos.
- `tests/test_github_data_store.py`: 25 pruebas offline del transporte.
- `tests/test_github_local_configuration.py`: 5 pruebas de credenciales/preflight.

Documentacion relacionada actualizada: `README.md`, `ARCHITECTURE.md`,
`TODO.md` y `DESIGN_ASSUMPTIONS.md`. Sus secciones historicas SQL no son requisitos
de la arquitectura objetivo.

## 5. Contratos importantes que no deben perderse

- IDs Spotify nuevos: 22 caracteres alfanumericos. IDs legacy invalidos se
  conservan/reportan aparte; no inventar correcciones.
- Clave escucha: `(account_key, track_id, played_at)`; segundos UTC, sin cambiar
  deduplicacion a milisegundos durante la migracion.
- Fechas de capturas: ISO UTC canonico con Z; rechazar fechas sin zona.
- CSV UTF-8 sin BOM, coma, LF, cabecera/orden exactos y version explicita.
- NULL de celda: `\N`. Backslash inicial literal se escapa; cadena vacia, cero,
  false, NULL y JSON null son distintos. `JSON_NULL` expresa JSON null de nivel
  superior, no NULL SQL.
- JSON rechaza claves duplicadas y numeros no finitos.
- Limite exacto por archivo: 20 MiB, contando cabecera/escapado.
- Particiones estables: 16 buckets SHA-256; escuchas/receipts tambien por mes UTC.
- Lectores verifican hashes, bytes, numero de filas y claves unicas.
- Inbox publicado no equivale a snapshot consolidado.
- Reintentar con la misma identidad/timestamp tras respuesta incierta.
- Entradas invalidas de escuchas/metadatos van a cuarentena con receipt explicito.
- Metadatos Last.fm/MusicBrainz/AcousticBrainz: gana primer registro consolidado,
  incluidos negativos. AcousticBrainz tiene claves distintas por nivel low/high.
- SmarterPlaylists: merge por campo/fecha, preservar parciales y valores 0/false;
  contradiccion a igual fecha rechaza todo el lote sin escritura parcial.
- Captura Pulse live de un track puede preceder al catalogo; no crear entidades
  ficticias ni promover valores relacionados a tablas de artista/album.
- Contadores de escuchas, metadatos y favoritos separados.

### Favoritos: politica elegida por el usuario

Una modificacion pendiente por cuenta:

1. Guardar una reserva antes de llamar a Spotify. Solo `reserved` autoriza una
   ejecucion; retries `already_pending`/`already_resolved` no autorizan repetirla.
2. Pendiente bloquea otra modificacion y un nuevo download completo.
3. Resultado incierto requiere reconciliar el estado observado, sin forzar una
   intencion antigua ni desbloquear automaticamente por timeout.
4. El llamador debe comprobar que la peticion original ya no sigue en vuelo:
   un timeout por si solo no lo demuestra.
5. Resolucion publica lote observado, revision y desbloqueo en un commit.
6. Snapshot completo se reserva ANTES del download. Otra generacion/revision
   o modificacion pendiente lo vuelve `superseded`, sin sustituir la replica.
7. Capturas invalidas/superseded se conservan en journal; invalidas dan error.
8. Aplicar revisiones consecutivas, no ordenar por timestamp/hash. Ausencia o
   corrupcion de revision bloquea consolidacion; no saltarla ni inventar estado.

La coordinacion actual NO ejecuta Spotify ni implementa recovery remoto.
Paginacion completa/totales estables no detectan todos los cambios externos
con igual numero de favoritos: siguen siendo necesarias reconciliaciones.

## 6. GitHub real: estado y diagnostico

**Actualizacion 2026-11-10:** el bloqueo del preflight ya esta resuelto para
la politica Free explicitamente aceptada. Se identifico el mensaje exacto de
upgrade de plan y se completaron publicacion/recuperacion sinteticas reales.
Ver seccion "GitHub remoto sintetico validado" mas abajo. El relato siguiente
conserva el estado historico previo; NO implica que protecciones esten activadas.

El usuario creo `luiscaballeroramos/spoty-data`, privado, con rama `main`.
Autorizo pruebas reales SOLO sinteticas. No se han publicado capturas en remoto.

Comprobaciones reales de solo lectura:

- El token configurado autentica correctamente y pertenece al propietario esperado.
- Tras corregir seleccion de repositorio y Contents Read and write, la consulta
  del repo devuelve 200 y confirma privado; main devuelve 200.
- Consulta `/rules/branches/main` devuelve 403, indicando necesidad de mejorar
  el plan de GitHub. No es rate-limit ni falta de acceso al contenido.
- El usuario creo ruleset contra borrados/force-push, pero no puede darse por
  aplicado: el plan actual no permite esas reglas en repositorios privados.
- NO se probo escritura ni se continuo publicacion saltando el preflight.

El backend local ofrece CAS literal; API GitHub ref ofrece fast-forward sin
force, NO un parametro expected-SHA. Evita carreras entre commits hermanos
si la rama solo avanza. Rewinds/borrados externos rompen esa precondicion.
El diseno actual exige rama sin force-push/borrados y sin bypass de escritores.

### Investigacion posterior: mantener GitHub Free

El usuario eligio estudiar primero una alternativa gratuita y mantener el repo
privado; no ha aprobado cambiar de proveedor ni aceptar menos garantia.

GitHub actualiza refs mediante fast-forward si `force=false`. Por tanto, las
escrituras de esta implementacion aun pueden evitar que dos escritores
cooperantes se pisen: uno gana y el otro tiene que releer/recalcular. No depende
de `main` protegido para detectar el conflicto normal entre publicaciones.
Sin reglas activas, sin embargo, un actor con permisos suficientes puede borrar
la rama o reescribirla desde la UI/u otro cliente. El escritor API no lo hace,
pero el repo no puede imponerlo con el plan actual. Un rewind puede ocultar
commits del tip; no afirmar recuperacion garantizada solo porque queden objetos
Git accesibles temporalmente. Un backup ayuda a restaurar, no equivale a prevenir.

Alternativas que preservan la privacidad:

1. **Seguir con GitHub Free con riesgo residual aceptado.** Conservar en el
   adaptador `force=false`, restringir token solo a `spoty-data`, no darlo a
   scripts/personas adicionales, monitorear avance, exportar backup recuperable
   y documentar que el propietario aun puede reescribir/borrar. Es la ruta de
   menor coste/cambio y conserva Git como fuente; no cumple la garantia fuerte
   de historia protegida ante escritores privilegiados.
2. **GitHub Free + backup independiente.** Mantener Git fuente principal y
   crear replica periodica en otro destino bajo credenciales separadas. Aumenta
   coste/operacion y permite recuperacion de incidentes, pero el backup retrasado
   no evita perdida entre ejecuciones y tampoco impide una reescritura.
3. **Cloudflare R2 como almacen de datos (no Git).** Documentacion vigente
   describe consistencia fuerte global y `PutObject If-Match/If-None-Match`
   evaluado atomically; `If-None-Match: *` da create-once contra escritores
   concurrentes. Bucket locks pueden impedir overwrite/delete en prefijos por
   retencion incluso indefinida. Diseno candidato: CSV/commit records
   inmutables en claves content-addressed/secuenciales con create-once, y un
   head/checkpoint por CAS condicional; bloquear los prefijos de datos. El
   principal de app solo tendria acceso de objetos, separado del permiso de
   editar lock rules. R2 no ofrece versionado S3 clasico: la historia se debe
   construir con objetos inmutables propios. El admin de la cuenta puede
   cambiar configuracion; no es proteccion frente al propio account owner.
   Tier gratuito publicado: 10 GB-mes, 1M operaciones A y 10M B; egress gratis;
   exceso Standard publicado $0.015/GB-mes, $4.50/M operaciones A, $0.36/M B.
   El export historico medido es ~0.85 GB, pero Git history/dataset crecimiento,
   operaciones de snapshots y coste real aun se deben medir. Requiere cuenta
   Cloudflare, secretos nuevos, S3 signer/SDK y adaptador nuevo; deja de cumplir
   la preferencia actual de Git como fuente de datos.
4. **GitHub Pro u otro servicio privado con CAS/retencion.** Mas cerca de la
   arquitectura actual; confirmar precio, reglas disponibles y condiciones del
   proveedor antes de comprometerse.

Decision del usuario (2026-11-09): mantener GitHub Free y mitigar/documentar
el riesgo residual con backups. Esto NO equivale a proteccion activa de main.
El transporte actual sigue evitando non-fast-forward en sus propios writes;
no autoriza a presentar como imposible un rewind/delete privilegiado.

El usuario eligio una copia offline en PC/disco cifrado con objetivo diario.
Se implemento `spoty/storage/git_backup.py` y la CLI
`examples.backup_spoty_data`:

- Clona/fetch un mirror Git a un destino explicito fuera del repo de codigo.
- Antes de cada fetch guarda `refs/heads/main` en un ref unico bajo
  `refs/backup/checkpoints/`; no hace push ni prune.
- Comprueba la rama antes y despues del fetch; informa explicitamente si se
  borro o cambio durante la operacion y conserva el checkpoint anterior.
- Limita el origen a GitHub HTTPS sin credenciales en URL (o ruta Git local
  para pruebas sinteticas). No consulta `.env`.
- El destino debe existir como padre y estar en una ubicacion protegida; debe
  desconectarse el disco cifrado tras una copia correcta para que sea offline.

La CLI y pruebas sinteticas locales existen. **No se ha ejecutado ni programado
un backup real en este equipo**: el usuario confirmo que no es su PC personal.
La primera copia queda pendiente hasta trasladar el codigo y operar desde su PC
personal, con disco cifrado y Git Credential Manager configurado. No conectar
esta sesion/equipo de trabajo al repo privado ni guardar aqui una replica de sus
datos. Usar una credencial dedicada read-only, restringida al repo de datos
(`Contents: read`); no reutilizar el token de escritura de la app. RPO objetivo:
un dia, pero el atraso real incluye dias en que el PC/disco no esten disponibles
o la tarea falle. Un checkpoint local ayuda a recuperar un rewind/borrado
posterior a la ultima copia exitosa; no protege si tambien se pierde el disco o
su clave de cifrado.

Las alternativas de segundo repo privado o almacen independiente R2 siguen sin
aprobarse y no se implementan. No guardar datos en el repo de codigo ni
exportarlos a ubicaciones compartidas.

Fuentes consultadas el 2026-11-09:

- [GitHub REST API: update a reference](https://docs.github.com/en/rest/git/refs?apiVersion=2022-11-28):
  `force=false` requiere fast-forward; la API no recibe un SHA esperado/CAS literal.
- [GitHub rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets):
  alcance y enforcement de rulesets. Para este repo la respuesta observada fue
  403 con mensaje de upgrade de plan; no se probo write.
- [R2 consistency model](https://developers.cloudflare.com/r2/reference/consistency/):
  lecturas/listados fuertes globalmente y writes condicionales atomicos; un
  listado no arregla una escritura ya incierta/in-flight.
- [R2 conditional S3 operations](https://developers.cloudflare.com/r2/api/s3/extensions/#conditional-operations-in-putobject):
  `If-Match`, `If-None-Match` y create-once.
- [R2 bucket locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/):
  retencion por prefijo y proteccion de overwrite/delete.
- [R2 pricing](https://developers.cloudflare.com/r2/pricing/):
  limite gratuito/operaciones y tarifas Standard revisadas en esta fecha.
- [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/):
  comprobar operaciones soportadas antes de elegir una libreria/SDK; R2 no se
  debe tratar como clon completo de S3/Git.

**Siguientes pasos (pendientes para el PC personal):**

1. En el PC/disco cifrado elegido, crear la carpeta de destino y comprobar que
   Git Credential Manager accede al repo privado con credencial read-only.
2. Ejecutar manualmente `python -m examples.backup_spoty_data --destination
   "<destino>\spoty-data.git"` desde la raiz del repo; verificar el resultado y
   la recuperabilidad antes de programar la copia diaria.
3. Programar la tarea diaria en Windows Task Scheduler solo despues de elegir y
   probar el destino; mantener el disco desconectado entre ejecuciones.
4. Mantener bloqueado el preflight/publicacion sintetica real hasta resolver
   como verificar proteccion/reglas de `main` sin el upgrade requerido. Ningun
   dato se ha publicado en `spoty-data`.

No asumir que acepta pagar ni cambiar a R2, ni hacer publico el repo privado.
El usuario acepta GitHub Free con riesgo residual mitigado mediante backup
offline. La primera copia queda pendiente hasta estar en su PC personal; no
ejecutarla en este equipo.

### Credenciales en el PC personal

- No copiar ni commitear credenciales del equipo actual.
- `.env` fue comprobado ignorado/no versionado en este worktree.
- El usuario confirmo que el token usado no estaba compartido en adjuntos/chat.
- En el PC nuevo, configurar un token propio restringido SOLO al repo de datos,
  Contents Read and write, caducidad corta; no pegarlo en el chat.
- Para el backup diario, crear una credencial distinta de solo lectura y
  restringida al repo de datos. Git Credential Manager debe tenerla disponible
  antes de ejecutar la tarea, que no permite introducir credenciales de forma
  interactiva.
- Loader admite GH_TOKEN o GITHUB_TOKEN en archivo explicito; entorno tiene
  prioridad. No carga `.env` implicitamente al importar el paquete.
- Streamlit/Actions usaran sus Secrets al desplegar, no `.env` del repo.
- No leer/mostrar archivos de credenciales con view/cat ni volcar respuestas HTTP.
- Revocar el token temporal del equipo que se deja cuando ya no sea necesario.

## 7. Validacion realizada y comandos para retomar

### Ampliacion local de consultas CSV

`spoty/services/summary_queries.py` incorpora `SummaryQueryService(snapshot)`:
recuentos, firma del dashboard, historico con ranking calculado sobre todas las
escuchas antes del limite, nombres de artistas, cobertura por fuente/campo y
generos por etiquetas/familias. Reutiliza PulseQueryService. Mantiene filas sin catalogo
(LEFT JOIN), favoritos sin ficha y solapamiento completo entre etiquetas.
Los empates temporales del historico se ordenan por track_id ascendente; SQL no
define ese desempate. Los limites rechazan tipos no enteros y booleanos.

Lecturas fijadas por cuenta/commit; pendientes separados mediante
`snapshot.pending_batches()`. Summary tiene el modo opcional local descrito abajo;
Neon predeterminado, workflows y datos reales no se han cambiado.
No se ejecuta backup en este equipo.

`grouping="families"` ya funciona en snapshots v4 con taxonomia. Los snapshots
v1/v2/v3 siguen legibles, pero solicitan importar taxonomia mediante un error
explicito si se piden familias. Las vistas predeterminadas conservan SQL.
Cobertura v5/v6 usa proyeccion ligera; en versiones anteriores y para generos se
cargan particiones de proveedores. Falta certificar export completo/Cloud y
verificar paridad contra PostgreSQL real.
Las pruebas nuevas usan CSV serializados, manifiestos y snapshots reales con un
DataStore en memoria, sin red ni credenciales.

### Taxonomia CSV y familias (2026-11-09)

`GenreTaxonomyRepository(store, account).import_seed(seed)` acepta la semilla
existente de `services.genre_taxonomy.load_seed`. Reutiliza sus helpers puros,
sin importar repositorio SQL, configuracion ni HTTP. Conserva generos existentes,
relaciones no incluidas en la nueva semilla y mappings aprobados/excluidos.
Solo agrega o promueve pendientes; conserva created_at al promoverlos.
Valida tambien ciclos que aparezcan al unir dos importaciones validas.

`discover_labels()` es preview sin escrituras. `discover_labels(apply=True)`
guarda etiquetas desconocidas de artistas del catalogo como pending, sin
clasificarlas. Metadata de artistas sin ficha no se usa en descubrimiento,
igual que en SQL. Proveedores, campo y status filtran los mappings de familias.

Manifiesto v4 agrega genres/genre_relations/genre_label_mappings; receipts no
cambian de v3. Semillas canonicas archivadas por SHA-256 en
accounts/<cuenta>/taxonomy/imports/. Importacion idempotente, conflictos con
reintentos acotados y errores restantes propagados. Las ediciones publican
directamente un snapshot atomico por DataStore: no son capturas inbox y no
consumen pendientes. Solo se han ejecutado contra datos sinteticos locales.
No usar todavia un store GitHub real para estas operaciones.

Familias: todos los ancestros raiz de mappings aprobados, deduplicados por
track/raiz, conservando solapamientos, denominador de escuchas clasificadas y
limite 1..20. Se ha probado el mismo ejemplo del test SQL existente (Folk/Rock),
sin consultar PostgreSQL. Falta paridad contra base real, editor manual CSV,
migracion del export, medicion e integracion opcional de vistas.
La CLI antigua services.genre_taxonomy sigue siendo SQL, no se ha cambiado.

### Summary en Streamlit: preview CSV local de solo lectura

Eleccion del usuario: fuente Git local de solo lectura, no GitHub ni backup
en este equipo. Implementado en streamlit_app.py antes de cargar Secrets,
config/.env, OAuth, navegacion, playback o capturas automaticas.

Activacion EXPLICITA por entorno del proceso, antes del arranque:

```powershell
$env:SPOTY_SUMMARY_BACKEND = "csv_local"
$env:SPOTY_CSV_REPOSITORY = "C:\synthetic-data\spoty-demo.git"
$env:SPOTY_CSV_ACCOUNT = "account-demo"
.\venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Ruta/cuenta de ejemplo: requieren un bare existente con snapshot consolidado
sintetico. No se ha creado una copia real ni dejado servidor ejecutandose.
SPOTY_CSV_COMMIT opcional acepta SHA-1 completo; sin el, cada rerun fija main
local una vez para totales, generos y pendientes. No hace fetch.
Se muestran cuenta, commit, fecha de consolidacion y numero de pendientes aparte.
La seleccion familias/etiquetas conserva UX. No hay otras paginas ni acciones
de Spotify en este modo. LocalGitStore(read_only=True) bloquea publicaciones
y comandos Git mutantes. Los errores se notifican con el mecanismo UI existente;
no se sustituyen por resultados vacios ni hay fallback SQL.

El backend normal sigue siendo sql; valor desconocido falla explicitamente.
Este selector se resuelve antes de cargar archivos: no configurarlo en .env ni
Streamlit Secrets; usar variables del proceso. Para volver, detener preview y
arrancar otro proceso con SPOTY_SUMMARY_BACKEND=sql o sin esa variable.
No se han modificado el .env local ni la configuracion de despliegue.
Pendientes: preview visual con volumen representativo, otras vistas, despliegue
Cloud, acceso remoto aprobado y migracion. La cobertura de UI se verifica con
AppTest sobre streamlit_app.py, sin servidor ni navegador externo.

Validaciones focalizadas sucesivas:

- Prototipo inicial: 87 pruebas correctas (33 nuevas + 54 regresiones), demo,
  sintaxis y wheel instalada desde copia temporal limpia.
- Metadatos: 120 pruebas focalizadas correctas.
- Favoritos: 171 pruebas focalizadas correctas; comprobaciones finales de receipts
  antiguos y manifiesto requerido tambien correctas.
- Transporte: 109 pruebas combinadas correctas (23 nuevas entonces + 86 previas),
  seguido de 25 pruebas transporte correctas con limite exacto/retries.
- Configuracion/preflight: 30 pruebas focalizadas correctas.
- Backup Git offline: 6 pruebas sinteticas correctas; cubren mirror incremental,
  checkpoint antes de rewind, rama borrada, cambios durante fetch, fuente y
  destino incorrectos. No prueban credenciales reales ni escriben en GitHub.
- Consultas CSV: 15 pruebas nuevas y 35 regresiones de resumen/metadatos
  correctas (50 en total). Incluyen ranking global, limites 1..20, JSON NULL,
  solapamiento de etiquetas, cobertura, aislamiento de cuenta/commit, pendientes,
  particiones corruptas y Pulse sin lectura de metadatos raw. Compilacion y
  diff --check correctos; Pylance sin errores (solo parametros no usados en
  el doble de publicacion de las pruebas).
- Taxonomia/familias CSV: 21 pruebas nuevas; 134 pruebas combinadas correctas
  (escuchas/catalogo, metadatos, favoritos, taxonomia y consultas CSV/SQL mock).
  Incluyen importacion real de la semilla empaquetada, idempotencia, decisiones
  previas, concurrencia, errores, lectores v1-v3 y consolidacion en Git temporal.
  Compilacion y diff --check correctos; diagnosticos Pylance de los archivos
  nuevos y del almacenamiento/consolidacion modificados sin errores.
- Summary Streamlit CSV: 10 pruebas AppTest/solo lectura nuevas. Suite final
  de 91 pruebas correcta (UI, consultas CSV/SQL mock, almacenamiento y taxonomia).
  Compilacion, diff --check y Pylance sin errores; solo aviso preexistente de
  import no usado en LocalGitStore. No servidor persistente ni acceso real.
- Compileall y git diff --check correctos; Pylance sin errores en comprobaciones
  previas. Ultimas consultas Pylance de demo/loader remoto expiraron: no afirmar
  que sus diagnosticos finales fueron comprobados. Tests y compilacion si pasaron.
- No se ha certificado rendimiento/coste del export completo.

En Windows, desde la raiz del repo, con dependencias del proyecto instaladas:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_csv_git_prototype tests.test_csv_metadata_prototype tests.test_csv_favorites_prototype tests.test_github_data_store tests.test_github_local_configuration tests.test_git_backup -q
.\venv\Scripts\python.exe -m unittest tests.test_csv_summary_queries tests.test_summary_queries -q
.\venv\Scripts\python.exe -m unittest tests.test_csv_summary_view tests.test_csv_summary_queries tests.test_summary_queries -q
.\venv\Scripts\python.exe -m unittest tests.test_csv_git_prototype tests.test_csv_metadata_prototype tests.test_csv_favorites_prototype tests.test_csv_genre_taxonomy tests.test_csv_summary_queries tests.test_summary_queries -q
.\venv\Scripts\python.exe -m examples.csv_git_demo
.\venv\Scripts\python.exe -m compileall -q spoty examples
git --no-pager diff --check
```

Para validar la CLI cuando el destino/cuenta local esten listos, primero
comprobar acceso de lectura con `git ls-remote` y despues usar el comando
documentado en README. La CLI requiere carpeta padre existente y no acepta
destinos dentro del repositorio de codigo.

Si no existe venv, seguir instalacion de README. No asumir que el interprete
seleccionado en el editor coincide con el del terminal.

Preflight real (solo lectura, credencial local explicita):

```powershell
.\venv\Scripts\python.exe -m examples.github_data_demo --owner luiscaballeroramos --repository spoty-data --env-file .env
```

Actualmente falla por la proteccion/plan descritos arriba. No usar el flag de
publicacion hasta resolver esa decision y verificar reglas/permisos.
Publicacion de prueba, SOLO despues de preflight correcto y confirmacion:

```powershell
.\venv\Scripts\python.exe -m examples.github_data_demo --owner luiscaballeroramos --repository spoty-data --env-file .env --publish-synthetic --account synthetic-ID-NUEVO
```

La demo exige namespace nuevo y deja datos sinteticos para inspeccion; no borra
historial de pruebas ni hace force push. Usa observacion ficticia de favoritos,
sin llamar a Spotify.

### Incidencias previas ajenas al prototipo

Baseline completo inicial: 278 entradas unittest; 272 exitos, 2 tests SQL
deliberadamente omitidos, 1 fallo y 3 errores. No se arreglaron:

- cryptography falta en venv/manifest, impidiendo tests de escuchas cifradas.
- Test antiguo importa `app_views.summary.run_metadata_enrichment`, ya inexistente.
- Falta `GENRE_TAXONOMY.md` requerido por test.
- HTML de taxonomia generado difiere solo por newline final.
- Tests SQL reales requieren SPOTY_TEST_DATABASE_URL; no apuntarlos a produccion.

No modificar copias `build/lib`, egg-info o scripts duplicados como fuente ni
arreglar incidencias ajenas automaticamente al retomar.

## 8. Volumen y riesgos estudiados

Export local historico estudiado, no export nuevo ni consulta de Neon:

- 18 CSV, 847.41 MiB en total.
- Metadatos SmarterPlaylists track: 522.081 MiB (requiere particiones).
- Respuestas originales: aproximadamente 578.381 MiB de campos decodificados.
- Procedencia por campo SmarterPlaylists: aproximadamente 107.760 MiB.
- Unas 7297 tracks, 11071 escuchas y 3004 favoritos en ese export.
- GitHub avisa sobre archivos >50 MiB y bloquea >100 MiB; prototipo fija 20 MiB.
- Consolidacion actual lee/reparticiona datasets en memoria: NO escalado certificado.
- Las capturas recientes Spotify pueden limitarse a 50 items. Schedules Actions
  pueden retrasarse/perder ejecuciones: no prometer cero perdidas.
- 3004 favoritos son unas 61 paginas; sleeps actuales pueden sumar 10-15 minutos.
- GITHUB_TOKEN de workflow no da escritura en otro repo; PAT restringido o GitHub
  App seran necesarios segun ubicacion elegida. Permisos contents no son por carpeta.

## 9. Plan general pendiente

### Medicion sintetica completada (2026-11-09)

`examples/benchmark_csv.py`: CLI Windows sin dependencias nuevas, credenciales
ni rutas a repositorios existentes. Crea/elimina un bare temporal en cada
ejecucion. `tests/test_csv_benchmark.py` verifica generacion determinista,
formas CSV, perfiles invalidos y medicion/incremento de extremo a extremo.

```powershell
.\venv\Scripts\python.exe -m examples.benchmark_csv --output benchmark-compact.json
.\venv\Scripts\python.exe -m examples.benchmark_csv --raw-bytes-per-track 75000 --output benchmark-heavy.json
.\venv\Scripts\python.exe -m unittest tests.test_csv_benchmark -q
```

JSON a stdout y archivo nuevo opcional; no sobreescribe informes.
Ejecutados ambos perfiles: 7297 tracks, 11071 escuchas, 3004 likes,
2433 artistas y 730 albums; incremento posterior de 50 escuchas.
Version Python 3.13.12, Windows, una ejecucion por perfil.

| Medida | Compacto | Raw 75000 bytes/track |
|---|---:|---:|
| Particiones CSV iniciales | 3.71 MiB | 525.64 MiB |
| Directorio Git inicial, objetos loose | 0.40 MiB | 306.32 MiB |
| Carga de manifiesto | 1.13 s | 2.97 s |
| Recuentos | 5.31 s | 6.76 s |
| Historico reciente | 3.11 s | 3.48 s |
| Etiquetas / familias | 3.59 / 3.75 s | 3.88 / 3.93 s |
| Cobertura de metadatos de tracks | 3.25 s | 33.38 s |
| Consolidar 50 escuchas | 21.56 s | 112.02 s |
| Pico RSS Python muestreado, cobertura | 56.55 MiB | 695.75 MiB |
| Pico RSS Python muestreado, consolidacion | 75.32 MiB | 1262.65 MiB |
| Crecimiento Git al agregar escuchas | 15.82 KiB | 16.83 KiB |

Las operaciones verificaron totales exactos, cobertura completa, pesos completos
en ambas etiquetas/familias, longitud de historico, 50 eventos guardados sin
rechazos, inbox final vacio y snapshot anterior intacto (11071 vs 11121 eventos).
Recuentos: 96 llamadas/procesos Git por ejecucion. Cobertura pesada:
549543541 bytes leidos para devolver solo estadisticas.

Limites: RSS cada 50 ms del proceso Python, NO suma memoria de Git; puede perder
picos breves. Peak de vida del proceso tambien se informa y es acumulativo.
Se mide backend local, no render de Streamlit ni HTTP, sin limpiar cache del SO,
sin repack/gc ni historial largo. Raw es texto hexadecimal SHA-256 determinista,
procedencia por campo vacia, taxonomia minima y otros proveedores sin payloads.
No equivale a todo el export historico de 847 MiB ni a su compresibilidad.
El escaso crecimiento Git refleja blobs iguales reutilizados, NO consolidacion
incremental eficiente. Los Git temporales se eliminaron al finalizar.
No se leyo staging.csv, Neon, spoty-data ni ningun dato real.
Validacion final: 30 pruebas correctas (3 nuevas de benchmark mas consultas
CSV/SQL mock), compilacion/diff --check correctos y Pylance sin errores.
Solo aviso informativo de rama no Windows no analizada en el entorno Windows.

Conclusiones iniciales: proyeccion ligera y datasets afectados, implementados
en la siguiente seccion. Reducir procesos por particion en Git local sigue
pendiente. No certificar viabilidad Cloud con estas mediciones unicas.

### Optimizacion v5 y repeticion de benchmarks (2026-11-09)

Manifiesto v5 agrega metadata_coverage (tipo/ID/fuente/lista de campos, no
valores ni raw). Mismos filtros, denominadores, orden y tratamiento de cero/
false/null/vacios que el lector anterior. Todas las publicaciones completas
derivan la proyeccion; consolidacion y taxonomia comparten ese serializador.
v1-v4 conservan cobertura desde proveedores; siguiente publicacion efectiva
completa los migra. Una proyeccion v5 corrupta falla, no se sustituye por raw.

Ruta rapida solo si el snapshot es v5, todos los lotes seleccionados son
escuchas, no se agrega ningun track/artista/album y el limite es el predeterminado.
Solo publica eventos/receipts y archivo/inbox/manifiesto; reutiliza descriptores
de catalogo, metadatos, favoritos, taxonomia y proyecciones. Catalogo nuevo,
metadata/favoritos, snapshots antiguos o max_bytes personalizado hacen rebuild
completo, para no perder la cobertura de metadatos antes huerfanos ni audio.
Reintentos releen la nueva cabeza sin perder lotes concurrentes.

Integridad: valida estructura de descriptores heredados, pero NO relee/rehashea
blobs no afectados. No es auditoria global. Lectura posterior con Snapshot.rows
mantiene validaciones de tamano/checksum/filas/claves y errores de almacenamiento.
Un raw danado puede conservarse en esa ruta y fallara al leerlo; cobertura
valida su propio dataset, no el raw. Esto esta probado explicitamente.
Los snapshots historicos mantienen bytes/descriptores y resultados previos.

Mismos comandos/perfiles, repetidos en procesos nuevos; resultados:

| Medida | Compacto v5 | Pesado v5 | Pesado anterior |
|---|---:|---:|---:|
| Cobertura | 1.54 s | 1.65 s | 33.38 s |
| Bytes leidos en cobertura | 2.76 MiB | 2.76 MiB | 524.09 MiB |
| Pico RSS Python, cobertura | 51.21 MiB | 59.14 MiB | 695.75 MiB |
| Consolidar 50 escuchas conocidas | 6.28 s | 6.34 s | 112.02 s |
| Pico RSS Python, consolidacion | 53.61 MiB | 59.05 MiB | 1262.65 MiB |
| CSV inicial | 5.66 MiB | 527.59 MiB | 525.64 MiB |
| Git inicial | 0.49 MiB | 306.42 MiB | 306.32 MiB |
| Crecimiento Git del incremento | 16.78 KiB | 17.78 KiB | 16.83 KiB |

Cobertura pesada lee 2895233 bytes (antes 549543541); consolidacion pesada
lee 1598135 bytes y hace 145 llamadas Git. Recuentos aun hacen 96 llamadas.
Se mantuvieron totales, pesos de generos, incremento de 50 y lectura historica.
La carga inicial pesada todavia alcanza ~1.2 GiB RSS Python; reconstrucciones
completas no optimizadas. Sin nueva medicion de lote con catalogo desconocido.
No presentar los cocientes de tiempos como speedups estadisticamente aislados:
cache/carga de maquina varia y solo hay una corrida por perfil/version.
Mismas limitaciones del benchmark anterior (sin Git RSS/HTTP/render/etc).

160 pruebas focalizadas correctas: almacenamiento, escuchas, metadatos,
favoritos, taxonomia, consultas, Streamlit y benchmark; 13 nuevas de proyeccion/
reutilizacion, incluyendo compatibilidad v4, errores, conflictos y catalogo nuevo.
Revalidacion final de 31 pruebas de proyeccion/benchmark/consultas correcta;
compilacion, diff --check y Pylance sin errores. Avisos informativos previos
de parametros de test no usados y rama no Windows no analizada.
No cambios de produccion/credenciales ni lecturas del archivo adjunto/datos reales.

Prioridad de ese punto: agrupar lecturas Git y medir catalogo nuevo,
completado en la seccion siguiente. Medicion de updates de metadata pendiente.

### Lecturas Git agrupadas y catalogo nuevo (2026-11-09)

Capacidad opcional GroupedReadStore.read_many(commit, paths) implementada por
LocalGitStore con git cat-file --batch. Snapshot agrupa particiones hasta
20 MiB declarados por llamada. No carga todo el raw en una sola respuesta,
no crea procesos persistentes/cache ni cambia version v5. El transporte GitHub
sin esa capacidad conserva lectura individual (25 pruebas de transporte pasan).
Parsing estricto de headers/blob/longitud/separadores, payload binario intacto.
Fallos/missing/tree/protocolo invalido son errores explicitos sin fallback.
Se validan SHA/path antes y checksums/filas/claves despues, igual que antes.
El limite es sobre tamanos declarados, no un limite absoluto de memoria ante
blobs inesperadamente grandes. La autorizacion read-only solo agrega la tupla
exacta cat-file --batch; otros comandos permanecen bloqueados.

Instrumentacion de benchmark actualizada para contar bytes de ambas rutas.
Mismos perfiles anteriores, con fase extra de 50 tracks nuevos usando
artista/album existentes y sin metadatos de track nuevos. Mismos comandos,
archivo de salida nuevo para no sobreescribir. Resultados:

| Operacion | Procesos antes / ahora | Compacto agrupado | Pesado agrupado |
|---|---:|---:|---:|
| Recuentos | 96 / 5 | 0.60 s | 1.34 s |
| Historico | 64 / 3 | 0.34 s | 1.01 s |
| Etiquetas | 64 / 3 | 0.38 s | 1.38 s |
| Familias | 71 / 6 | 0.48 s | 1.50 s |
| Cobertura tracks | 32 / 2 | 0.46 s | 1.91 s |
| 50 escuchas conocidas | 145 / 69 | 3.10 s | 5.21 s |
| 50 canciones nuevas | sin medida previa / 226 o 273 | 10.80 s | 98.11 s |

Heavy nuevo catalogo: 553294532 bytes leidos y pico RSS Python muestreado
1337462784 bytes (~1.25 GiB), crecimiento Git 225574 bytes. Compacto:
6013101 bytes leidos, ~95.56 MiB RSS, crecimiento Git 224576 bytes.
Cobertura sigue leyendo 2895233 bytes. No todos los tiempos mejoran siempre:
cobertura pesada observada 1.91 s vs 1.65 s en anterior corrida.
Una ejecucion por perfil, no speedups aislados; RSS de hijos Git/render/
HTTP/cache fria no se mide. Carga inicial/rebuild completo siguen pesados.

Verificado tras los dos incrementos: 7347 tracks, 11171 escuchas, 3004 likes,
7297 tracks enriquecidos (los nuevos no lo estan), 11171 escuchas clasificadas
por familias heredadas de artistas, inbox vacio y ambos snapshots previos intactos.
Se actualizaron tests de Pulse y corrupcion UI para observar tambien read_many,
evitando que un mock de read individual dejara de cubrir la lectura real.
196 pruebas focalizadas correctas: agrupacion (11 nuevas), CSV completo,
proyecciones, taxonomia, favoritos, Streamlit y transporte GitHub simulado.
Revalidacion final de 14 pruebas agrupacion/benchmark correcta, compileall y
diff --check correctos; Pylance sin errores (avisos informativos preexistentes).
Sin acceso a archivo adjunto, datos reales, red, credenciales ni produccion.

La prioridad de catalogo incremental se completa en la seccion siguiente.

### Catalogo incremental y proyeccion v6 (2026-11-09)

Mismos esquemas CSV que v5, distinta completitud de metadata_coverage:
ahora conserva campos validos de proveedores incluso sin ficha de catalogo.
Summary solo los cuenta cuando la entidad esta catalogada. Spotify con ID fuera
del catalogo sigue siendo invalido; se valida antes de extender la proyeccion.
Se mantienen estados matched/found/nivel de proveedor y reglas de campos vacios.

En este paso, lotes de escuchas en v6 y limite estandar permiten reutilizacion
(metadata tambien se optimiza en la seccion siguiente):
- Catalogo ya conocido: se publican events/receipts, resto sin tocar.
- Nuevos tracks/artistas/albums: se publican catalogos, events/receipts y
  metadata_coverage/track_audio. Raw/proveedores/favoritos/taxonomia se reutilizan.
- Se agregan campos Spotify nuevos; los campos de proveedores antes huerfanos
  ya estaban conservados. Nombre de catalogo nuevo sustituye nombre fallback
  del audio huerfano; valores audio se conservan, sin inventar filas.
- Catalogo existente sigue insert-only. Captura con nombre vacio sigue invalida.
- Lectura/checksum/semantica invalidos fallan sin consumir inbox ni mover head.
- Descriptores heredados solo validan estructura, no auditoria de raw no leidos.

Snapshots v1-v5 siguen legibles con sus contratos originales. v5 no conservaba
proveedores huerfanos en cobertura: no basta cambiar numero de manifiesto.
Siguiente consolidacion efectiva/publicacion completa reconstruye todo y migra
a v6 una vez. Favoritos/custom partition y lotes mixtos siguen por ruta completa.
Metadata-only incorpora ruta incremental en la siguiente seccion.
Sin inbox, consolidar no publica ni migra. Commits historicos intactos.

Suite afectada final: 204 pruebas correctas (8 nuevas frente a 196), incluyendo
compatibilidad v5 real, cobertura de los proveedores huerfanos y oracle completo.
Incluye comprobaciones de artistas/albums huerfanos, Spotify invalido antes
de alta, captura vacia y conjunto obligatorio de proyecciones en publicacion parcial.
Compilacion y diff --check correctos; Pylance sin errores en los archivos afectados.

Repeticion sintetica, mismos perfiles, corrida unica por version/perfil:

| 50 canciones nuevas | Compacto v6 | Pesado v6 | Pesado agrupado anterior |
|---|---:|---:|---:|
| Tiempo consolidacion | 12.93 s | 13.32 s | 98.11 s |
| Pico RSS Python muestreado | 73.41 MiB | 84.32 MiB | ~1.25 GiB |
| Bytes leidos | 4123357 | 4126428 | 553294532 |
| Procesos Git | 153 | 153 | 273 |

Compacto NO mejora tiempo de corrida anterior (10.80 s), aunque baja RSS/lecturas.
Pesado conocido: 4.73 s/~68.16 MiB RSS; inicial pesado 88.67 s/~1.20 GiB.
Crecimiento Git nuevo catalogo ~219-220 KiB como antes.
Verificado en ambos: 7347 tracks, 11171 escuchas, 3004 likes, 7297 enriquecidos,
11171 clasificadas por familias, inbox vacio y ambos snapshots anteriores iguales.
Informes JSON fuera del repo en files de esta sesion:
benchmark-csv-heavy-catalog-v6.json y benchmark-csv-compact-catalog-v6.json.
No se necesita acceder a ellos para retomar: medidas/limitaciones quedan aqui.

No presentar tiempos como speedup aislado ni certificar Cloud. RSS muestrea
Python cada 50 ms, no hijos Git; sin cache fria/render/HTTP/historial prolongado.
Carga inicial y actualizaciones completas siguen pesadas.
Medicion/optimizacion por datasets de metadata completada en la siguiente seccion.
Sin red/datos reales/backup/credenciales/produccion ni cambios de workflows.

### Metadata incremental por datasets y benchmark (2026-11-09)

Se agregan dos fases al benchmark, despues de los incrementos de escuchas:
50 inserts Last.fm matched y 50 actualizaciones SPL track (energy 0.75/tempo 120,
captura posterior a la inicial). Ambas usan incremental_events como cantidad.
Mismos defaults/perfiles y limites de medicion; no se cambia CLI ni dependencias.
Tiny test ejecuta fases reales y verifica resultados, no solo shapes.

Ruta nueva solo para seleccion metadata-only, snapshot v6 y limite estandar:
- Decodifica envelopes seleccionados y planifica proveedores antes de leer raw.
  Valida cuenta/ruta; envelope invalido sigue a cuarentena/receipt de error.
- Carga catalogos, receipts, cobertura y datasets de proveedores afectados;
  ledger SPL si hay capturas SPL. Mantiene validacion de revisiones favoritas.
- apply_metadata prepara solo mapas afectados con copia superficial; no muta
  filas existentes. merge_metadata sigue siendo fuente de reglas/procedencia.
  Conflicto tardio descarta todo el lote, incluidos otros proveedores.
- Recalcula solo fuentes afectadas de cobertura, conservando otras y huerfanos;
  valida primero semantica de la proyeccion existente. No fallback a raw.
- Solo SPL track actualiza audio, reutilizando mismo audio_projection;
  SPL artist/album y otros proveedores no leen SPL track ni audio.
- Publicacion atomica de datasets afectados/receipts/proyecciones y archivo/
  cuarentena/inbox/manifiesto. Descriptores/blobs ajenos se reutilizan.
- Serializer exige cobertura/receipts y ledger/audio cuando corresponda.
  Errores de integridad/almacenamiento bloquean head e inbox; reintentos de
  concurrencia recalculan todo desde head nuevo. Sin cambio de manifiesto v6.
- Dataset afectado se lee/reparticiona ENTERO. No es optimizacion por particion
  ni por fila, ni auditoria de blobs ajenos reutilizados.
- Mixtos listening/metadata/favorites, legacy y max_bytes custom siguen completos.

Medicion pesada antes/despues en este paso, corrida unica por perfil/version:

| Metadata (50) | Pesado antes | Pesado por dataset | Compacto por dataset |
|---|---:|---:|---:|
| Last.fm tiempo | 38.95 s | 3.03 s | 2.90 s |
| Last.fm RSS Python muestreado | ~1.25 GiB | 73.69 MiB | 70.16 MiB |
| Last.fm bytes leidos | 553291891 | 3143790 | 3140719 |
| Last.fm procesos Git | 289 | 57 | 57 |
| SPL tiempo | 43.61 s | 38.87 s | 4.96 s |
| SPL RSS Python muestreado | ~1.25 GiB | ~1.23 GiB | 88.48 MiB |
| SPL bytes leidos | 553321245 | 552336226 | 5054795 |
| SPL procesos Git | 305 | 139 | 92 |

Sin baseline compacto de metadata: fases nuevas, anteriores corridas no las median.
Se verifica cada fase: 50 guardados, 1 aceptado/0 rechazados, inbox vacio,
7347 tracks/11171 escuchas/3004 likes sin cambios, 7297 enriquecidos, campos
esperados y snapshots anteriores intactos; SPL verifica energia/tempo/nombre
de catalogo. Mismas limitaciones: cache no aislada, Python RSS muestreado cada
50 ms, sin hijos Git, red/render/Cloud ni historial prolongado.
No prometer speedups aislados ni todos los metadatos ligeros.

215 pruebas afectadas correctas (11 nuevas de metadata incremental). Incluyen:
lecturas prohibidas de proveedores ajenos, descriptores/blobs reutilizados,
oracle de todas las filas igual a rebuild completo, artist/album/track, estados/
niveles/huerfanos, insercion inmutable, rollback incluso con copia superficial,
corrupcion local y ajena, envelopes invalidos, retries y legacy/custom limits.
Benchmark heavy baseline, heavy incremental y compact incremental correctos.
Revalidacion final de 73 pruebas de metadata/proyeccion/consultas/benchmark
correcta tras endurecer el test de rollback tardio y mantener coverage_projection
completo estricto. Compileall y diff --check correctos; Pylance sin errores,
solo aviso informativo de rama no Windows no analizada en el benchmark.
Informes fuera del repo en files de esta sesion:
benchmark-csv-heavy-metadata-baseline.json,
benchmark-csv-heavy-metadata-incremental.json,
benchmark-csv-compact-metadata-incremental.json.
No hacen falta para retomar: medidas y limites quedan en este documento.

El paso posterior reduce memoria SPL mediante staging temporal; ver abajo.
Sin produccion/red/credenciales/backup/datos reales/archivo adjunto ni commits/push.

### Ejecucion autonoma: staging, adquisicion y operaciones (2026-11-09)

Informe final de esta tanda: [INFORME-REFACTORIZACION-2026-11-09.md](INFORME-REFACTORIZACION-2026-11-09.md).
No confundir avance local con migracion/deploy completados.

Implementado:
- Snapshot.iter_rows verifica particiones y duplicados sin retener dataset entero;
  rows conserva contrato eager. Iterator debe agotarse para validar todo.
- Metadata-only v6 usa services/metadata_consolidation.py: lee envelopes uno
  a uno, carga proveedores afectados en staging SQLite TEMPORAL y obtiene
  solo filas objetivo para merge. SQLite biblioteca estandar, NO backend nuevo.
  Cobertura se recoge durante escaneo; audio se actualiza con helper compartido.
- storage/staging.py guarda CSV temporales y publica mediante Mapping lazy.
  Local/GitHub ordenan claves sin materializar todos los bytes. Manifiesto final
  conserva escritura atomica, non-force/CAS, rollback y reintentos.
- La misma forma/version v6; checksums/CSV/filas/claves se validan. Proveedores
  ajenos conservan alcance de integridad anterior (no auditoria global).
- Scratch y DB se eliminan al terminar, tambien en error/retry; disco lleno
  aborta sin consumir inbox ni cambiar head. Filas grandes/targets/catalogo/
  keys/proyecciones siguen condicionando memoria: NO limite universal.
- CsvAcquisitionService inyecta clientes: captura reciente, proveedores/SPL,
  adquisicion paginada de favoritos con reserva durable y recuperacion de
  cambio pendiente observando estado asentado, sin repetir like/unlike.
- CLI spoty.cli.operations: status/audit read-only y consolidate con flag,
  solo cuentas/repos sinteticos existentes. Audit usa iter_rows, no carga raw entero.
- Demo examples.csv_pipeline_demo enlaza adquisicion/inbox/consolidacion/
  consultas/audit/recovery con fake Spotify, sin SQL/HTTP y limpieza de Git temporal.
- .github/workflows/csv-validation.yml: manual, contents read, sin data repo,
  Secrets, schedules ni cambios de workflows productivos. No dispatch/push.

Medicion verificada final pesada: 7297 tracks iniciales/75000 bytes raw por track,
mismos incrementos. SPL 50: 68.556 s, 249053184 bytes RSS Python (~237.52 MiB),
552336226 bytes leidos/139 procesos. Objetivo observado <300 MiB PASA.
Antes: 38.8664 s/~1.23 GiB con dataset entero; baja memoria pero empeora tiempo.
Staging anteriores: 70.1081 s/~237 MiB y 96.7899 s/~240.06 MiB.
No ocultar regresion de latencia ni variacion del equipo compartido.
Last.fm pesado final: 4.919 s/~80.56 MiB, 3143790 bytes/57 procesos.
Compacto final: SPL 12.9642 s/~80.51 MiB, Last.fm 7.1478 s/~75.93 MiB.
Una corrida por implementacion/perfil, equipo compartido/cache no aislada;
no hijos Git/RSS absoluta/disk peak/render/Cloud. Inicial/full/mixtos no optimizados.
Verificado en cada corrida: 50 metadata guardados, totales 7347/11171/3004,
cobertura, energia/tempo/nombres de catalogo y snapshots anteriores intactos.

Validacion final amplia: 288 pruebas correctas (incluye backup SOLO sintetico
en temporales), compileall correcto y Pylance sin errores en cambios.
Nuevo test cubre favorito mal ubicado: sigue bloqueando, no se manda a cuarentena.
Tests protegen publicacion lazy, split/limite/hash/orden, disco lleno, cleanup,
igualdad de filas contra rebuild, merge tardio con rollback, favoritos y AppTest.
Revalidacion final de 126 pruebas correcta, con una regresion adicional:
el escaneo SPL reconstruye audio completo como el rebuild, incluso si una
proyeccion audio anterior era semanticamente obsoleta. No se repasa raw una
segunda vez: se deriva al escanear y se actualizan los targets aceptados.
Los conteos de corridas se solapan y no se suman.

GitHub: politica opt-in allow-unprotected-private refleja decision Free previa,
strict sigue por defecto. Nunca trata errores HTTP como protecciones verificadas.
Se intento preflight real SOLO LECTURA con credencial local ya configurada:
repo privado/branch accesibles; GET /rules/branches/main devuelve HTTP 403.
No se establecio si falta permiso o el plan limita la API. No eludirlo.
No hubo publicacion remota, clone/backup real, lectura de datasets reales,
migracion, cambios de credenciales/produccion ni commits/push.
Informe CLI muestra error_type/http_status, nunca token/cuerpo de respuesta.

Bloqueos/gates: resolver 403 para validar escritura sintetica real, primer backup
en PC personal, migracion con export/schema reales validados, completar
adaptadores/candidatos/scheduling/UI y Actions productivos, paridad/rollback.
La aplicacion predeterminada sigue SQL/Neon; CSV UI continua Summary read-only.
Informes de benchmarks completos en files de la sesion, nombres terminados
metadata-disk-final.json (heavy/compact) y heavy-metadata-disk-verified.json
(ultima verificacion); este texto conserva datos para retomar.

### GitHub remoto sintetico validado (2026-11-10)

Diagnostico real: 403 con mensaje exacto "Upgrade to GitHub Pro or make this
repository public to enable this feature." No ampliar permisos a ciegas.
Transporte descarta cuerpo y clasifica solo ese 403. Preflight strict rechaza;
allow-unprotected-private acepta explicitamente el riesgo de protecciones
desconocidas, previa verificacion de repositorio privado/no archivado/head.
Reporta rules_verification=unavailable_on_current_plan, branch_rules_verified=false,
protecciones null y riesgos force_push/branch_deletion. Otros errores bloquean.

Namespace nuevo synthetic-validation-20261110-0945: se publico/consolido fixture
de escucha, SPL y favorito, repeticion inbox/archive idempotente y stale head
rechazado. Snapshot inicial 60dcfa05ce27a806fbf8e1e524bcb177f015b731.
Despues, adaptador nuevo recupero cambio pendiente con fake observacion asentada,
sin mutar Spotify; consolido unlike sintetico y Last.fm metadata-only en disco.
Snapshot final 3994a69b35fa2e0f4d0c4b7932e5c49b46555832.
Tercer proceso de solo lectura verifico totales 1 track/artista/album/escucha,
0 favoritos actuales, Last.fm disponible, inbox vacio e historico favorito previo.
Historial sintetico conservado, sin borrar/reset/force-push.

Demo nueva opcion --verify-synthetic-recovery continua solo fixture conocido.
No se ejecutaron estres multiwriter simultaneo, perdida real de red a mitad
de write ni restauracion backup; no confundir stale-head con stress de concurrencia.
Backup personal, limites de Cloud/cuotas, migracion y despliegue siguen pendientes.
No lecturas/escrituras de datasets reales, credenciales cambiadas ni produccion.
Informe general actualizado en su seccion 7 con evidencias y limites.

### Integracion incremental de Streamlit CSV (2026-11-10)

`app_views.summary` difiere la importacion del Summary SQL, `_registration`,
`SynchronizationService`/`SyncStatus` y `SpotifyError`; CSV ya no importa esos
modulos ni necesita cargarlos para mostrar el snapshot. La entrada SQL sigue
cargando el mismo servicio de consultas y registro al seleccionarse.
El preview CSV ofrece navegacion propia entre Resumen, Metadatos y Pulse historico.
Metadatos usa
`CsvSummaryQueryService.metadata_overview` del snapshot cargado y presenta las
coberturas por fuente/campo sin SQL ni refresh de proveedor. El servicio SQL
de `app_views.metadata` tambien se importa de forma diferida solo si la vista
legacy lo necesita. AppTest demuestra que CSV no intenta los imports de SQL/
registro/Spotify, ni configura SQL/HTTP, y mantiene errores visibles, lote
pendiente, revision fijada y escritura prohibida. Regresion SQL/CSV de resumen
y metadatos: 28 pruebas correctas; compileall y diff --check correctos.
Pulse reutiliza `PulseQueryService.recent_audio_history` y los graficos existentes
para una lectura historica por periodo desde `track_audio`, sin playback live,
polling, auth, captura o refresh. El modo CSV difiere imports de reproduction,
auth Spotify y refresh de SmarterPlaylists; vista read-only sin SQL/HTTP. Prueba
AppTest verifica snapshot/pending, periodo infinito y una escucha consolidada,
sin imports live ni escritura.

Validacion tras Pulse: 52 pruebas de Summary/Metadata/Pulse SQL y CSV correctas,
incluidas pruebas de Pulse live legacy; compileall y diff --check correctos.
La fecha del fixture es anterior a 1 dia, por eso el test elige explicitamente
el periodo infinito; el lote inbox pendiente queda excluido. No cambia la app
por defecto. Pulse CSV es historico, no alternativa de playback live.

La siguiente separacion de reproduccion es `Historial`: lee
`recent_listening_events`/`artist_names` del snapshot y lista escuchas
consolidadas con fecha UTC, track/artistas/album, recuento y ranking. No integra
los controles actuales de playback ni muestra/edita favoritos (la lectura de
liked CSV no prueba reconciliacion actual con Spotify). No importa
`app_views.reproduction`, `SpotifyClient`, OAuth, SQL ni SmarterPlaylists.
La vista presenta estado de solo lectura y pendientes excluidos.

Validacion conjunta: 125 pruebas de History/CSV queries, Pulse live, servicios
SQL y reproduction existentes correctas. AppTest valida nombre multilinea,
artista/fecha, commit/pending, sin HTTP/SQL/imports live ni escrituras.
Compileall, diff --check y Pylance sin errores.

El servicio CSV ahora tambien ofrece `change_favorite`: persiste la intencion
estable antes de invocar like/unlike, lo hace solo si la reserva devuelve
`reserved`, y publica como confirmada solo una respuesta positiva. Respuesta
false, excepcion remota o error al publicar la resolucion dejan la operacion
pendiente; repetir el mismo request ID no vuelve a ejecutar la mutacion.
`recover_pending_favorite` consulta `are_tracks_liked` y resuelve como
`reconciled`, pero solo si el llamador confirma explicitamente que la peticion
remota anterior ya se asento. Un timeout aislado no autoriza recovery.

Tres pruebas nuevas cubren reserva-antes-de-dispatch, retry sin reenvio,
fallo/resultado falso y reconciliacion observada, incluyendo error al guardar
la resolucion posterior a la mutacion. 61 pruebas de adquisicion/coordinacion
CSV correctas; sin credenciales ni Spotify real.

Esto es una primitiva de servicio, no aun botones de UI. El preview permanece
read-only y usa `LocalGitStore(read_only=True)`. Existe una frontera opt-in para
escritura local de fixtures: el constructor y la factoria requieren
`SPOTY_CSV_ENABLE_SYNTHETIC_WRITES=1`, repositorio bare sin remotos, todas las
cuentas sinteticas, cuenta destino existente y cambios limitados a su carpeta.
Una integracion sobre Git local, con proveedor fake, cubre like aplicado con
respuesta perdida, reapertura del store, retry sin replay y recovery por
observacion asentada. Prueba UI inyectable confirma settlement, liga la accion
al request ID mostrado, solo observa y preserva el journal si falla; el servicio
rechaza IDs que cambiaron antes de la observacion. `AuthenticatedSpotifyFavoriteRecovery`
usa el `SpotifyClient` existente, carga secretos Spotify bajo demanda y expone
solo lectura de liked state; primero verifica que el request ID sigue pendiente.
El import heredado de `register.artist` en `spotifyclient` ahora es lazy. 11
pruebas writer, 32 adquisicion y 15 UI CSV (58); junto a 62 regresiones Spotify
son 120 correctas. No habilitar recovery en Streamlit: el entrypoint no inyecta
callback. No se validaron credenciales, consentimiento real ni deployment contra
Spotify; produccion sigue SQL.

El preview CSV ya muestra un aviso de solo lectura del journal pendiente en las
cuatro paginas (Resumen/Metadatos/Pulse/Historial). Distingue el journal de los
lotes inbox y del snapshot consultado; lee el HEAD local actual incluso si la
consulta esta fijada a un commit historico. Sin botones, SQL o llamadas a
proveedores. AppTest valida aviso, inbox independiente, ausencia de botones,
ninguna escritura ni red. Caso adicional fija un commit consolidado anterior
con 0 inbox batches y confirma que el aviso sigue leyendo el journal del HEAD
actual. Las 15 pruebas de la vista CSV pasan.

### Resto del plan

1. Decision tomada: GitHub Free con riesgo residual aceptado y backup offline
   diario. Primera copia/programacion pendientes EXCLUSIVAMENTE en el PC personal.
2. Preflight Free y publicacion/recovery sinteticos reales validados 2026-11-10.
   Protecciones no verificables en plan actual; riesgo de rewind/borrado sigue.
   Stress multiwriter, cuotas/carga y conectividad Cloud todavia sin certificar.
3. Medicion local sintetica inicial y repeticion v5 completadas; cobertura y
   consolidacion de escuchas conocidas e I/O Git agrupada implementadas.
   Catalogo nuevo y metadata por datasets optimizados en v6 y medidos.
   SPL metadata-only usa staging por partes (~238 MiB medidos), pero ~69 s.
   Reducir I/O/latencia e inicial/full/mixtos; no prometer Cloud listo.
   Faltan HTTP, render, historial largo,
   volumen/complejidad completos y repeticion estadistica antes de certificar.
4. Adquisicion/recovery de favoritos y publicacion de proveedores ya tienen
   servicio inyectado probado offline. Falta cableado remoto real,
   candidatos/enriquecimiento, refresh scheduling y resultados de errores,
   revision manual CSV de taxonomia, analisis y paridad completa SQL/CSV.
   Recuentos/historico/cobertura/etiquetas/familias y operaciones de importacion/
   descubrimiento de taxonomia ya tienen servicios CSV locales probados.
5. Separar integraciones/analisis/UI de almacenamiento reutilizando helpers.
   Cablear cada superficie; no sustituir SQL por lectura de CSV en cada grafico.
6. Migracion validada desde export: schemas reales por migraciones, checksums,
   integridad referencial, totales, muestras; invalidos aparte sin perder origen.
   COPY PostgreSQL necesita lector que distinga NULL y cadena vacia entrecomillada:
   DictReader generico no basta.
7. Summary, Metadatos, Pulse historico e Historial CSV read-only con flag,
   pendientes visibles e imports aislados validados. La coordinacion service
   de likes CSV esta implementada/testeada pero aun no conectada a UI; playback
   y boton de favoritos CSV permanecen pendientes. Pulse live solo en app SQL.
8. Workflow manual de validacion sintetica preparado, no ejecutado en GitHub.
   Integrar Actions productivos: captura durable y consolidacion, Secrets, ubicacion,
   frecuencia/concurrencia y consumo medidos; no cambiar schedules por intuicion.
9. Comparacion funcional SQL/CSV, rollout/rollback y eliminacion de dependencia
   Neon solo al cubrir y verificar todos los consumidores.

## 10. Trasladar el trabajo al PC personal

Al crear este documento, rama local `main`, ultimo commit del producto:
`5f816eb` (Merge remote-tracking branch origin/main).
Los cambios de esta refactorizacion NO estan commiteados ni subidos al repo
de codigo. Clonar el remoto por si solo NO recuperara el trabajo nuevo.

Archivos modificados: README.md, ARCHITECTURE.md, TODO.md,
DESIGN_ASSUMPTIONS.md, pyproject.toml.
Archivos nuevos: este documento, paquete spoty/, dos demos y cinco suites
de pruebas indicadas arriba.

Para trasladarlos:

- Opcion recomendada: revisar, hacer commit de archivos del codigo y push al
  repo de codigo, despues pull/clone en el PC personal. No se ha realizado
  commit/push automaticamente como parte de este documento.
- Alternativa: copiar SOLO los archivos fuente/documentos mencionados (incluidos
  nuevos no versionados). Un git diff normal no incluye archivos untracked.
- NO copiar .env, venv, exports/backups, caches, adjuntos de chat ni credenciales.
- No confundir repo de codigo con spoty-data; el codigo va al primero, las pruebas
  sinteticas futuras al segundo.

Los informes exploratorios completos quedaron fuera del repo, en la carpeta
de la sesion de Copilot del equipo original: inventario-refactorizacion.md,
viabilidad-git-datos.md y contrato-csv-paquetes.md. Este documento conserva
sus decisiones/hechos necesarios para continuar sin depender de esa carpeta.

### Mensaje sugerido para la siguiente sesion

> Lee CONTINUIDAD-REFACTORIZACION.md y el codigo del prototipo antes de actuar.
> Retomamos la refactorizacion para quitar Neon, con CSV/Git privado y Streamlit.
> Se eligio GitHub Free con riesgo residual y backup offline diario; la primera
> copia y programacion siguen pendientes en el PC personal, no en este equipo.
> Hay lector CSV local de resumen/historico/cobertura/etiquetas/familias y
> taxonomia v4 con importacion aditiva/discovery.
> Summary ya tiene modo local CSV de solo lectura bajo flag, probado con AppTest.
> Optimizacion v5 implementada y benchmark repetido: cobertura pesada 1.65 s,
> consolidar 50 escuchas conocidas 6.34 s/~59 MiB RSS por fase.
> I/O Git agrupada implementada (recuentos 96 a 5 procesos). Catalogo incremental
> v6 conserva cobertura/audio huerfanos: 50 canciones nuevas pesadas 13.32 s/
> ~84.32 MiB Python, antes 98 s/~1.25 GiB. Inicial/rebuild completos siguen pesados.
> Metadata por datasets implementada: Last.fm 50 pasa de 38.95 s/~1.25 GiB a
> SPL metadata-only usa staging TEMPORAL: ~238 MiB pero ~69 s; 288 pruebas amplias
> y revalidacion focalizada final de 126 (no sumar).
> Hay CsvAcquisitionService, CLI sintetico y workflow manual, sin produccion.
> GitHub sintetico remoto validado 2026-11-10; 403 identificado como limitacion
> de plan, aceptada SOLO por politica Free explicita con protecciones desconocidas.
> Lee tambien la actualizacion del informe. Seguir cableado CSV/UI/Actions,
> bajar latencia y completar migracion/paridad; NO declarar Neon eliminado.
> No leer datasets reales ni ejecutar backup en este PC.
> No leas/muestres secretos, no publiques datos reales, no cambies produccion
> y no supongas resuelta la proteccion ni terminada la migracion.
