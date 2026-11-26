# Informe de ejecucion autonoma: refactorizacion CSV/Git

Fecha: 2026-11-09.

**Actualizacion 2026-11-10:** completada validacion remota sintetica tras
diagnosticar el 403 de plan. La seccion 7 actualiza el bloqueo y las escrituras;
las secciones anteriores conservan el estado al cierre del 9 de noviembre.

## 1. Resultado ejecutivo

Se ha avanzado autonomamente en rendimiento, adquisicion/recuperacion,
operaciones y validacion, manteniendo produccion intacta.

**La migracion completa NO esta terminada.** La app predeterminada sigue
utilizando SQL/Neon. No se han migrado datos reales ni retirado dependencias
productivas. Un bloqueo real de GitHub impide completar la validacion remota;
no se ha ocultado ni eludido.

Estado verificable: **288 pruebas afectadas correctas**, revalidacion focalizada
final de 126 (corridas solapadas, no sumar), compilacion correcta, demo sintetica
completa y benchmarks con comprobaciones de contenido/historicos.
No equivale a certificar toda la suite historica ni Streamlit Community Cloud.

## 2. Trabajo realizado en esta tanda

### A. Reducir memoria al actualizar SmarterPlaylists

- Lectura de proveedores por particiones verificadas, sin materializar todo
  el dataset en una lista Python.
- Staging en archivos y SQLite **temporales**, usando biblioteca estandar;
  CSV/Git sigue siendo el almacenamiento persistente, sin nueva dependencia.
- Merge solo de los registros objetivo, manteniendo procedencia, capturas
  anteriores, idempotencia, reglas de conflicto y rollback de lote completo.
- Cobertura calculada durante el escaneo y audio actualizado con el helper
  compartido. Se preservan metadatos huerfanos y prioridad del nombre de catalogo.
- Publicacion de particiones desde disco, una a una: los adaptadores local y
  GitHub ya no ordenan/materializan todos los valores de los cambios.
- Temporal eliminado tambien ante error. Fallo de disco bloquea publicacion;
  no consume inbox ni mueve head. Reintentos vuelven al commit actual.
- Sin cambios de forma CSV o manifiesto v6.

Medicion sintetica pesada: 7.297 tracks iniciales, 75.000 bytes raw/track,
50 escuchas conocidas, 50 tracks nuevos, 50 Last.fm y 50 SPL.

| Medida SPL, 50 actualizaciones | Antes de esta tanda | Staging final |
| --- | ---: | ---: |
| RSS Python muestreado | ~1,23 GiB | **237,52 MiB** |
| Tiempo | 38,87 s | **68,56 s** |
| Bytes leidos | 552.336.226 | 552.336.226 |
| Procesos Git | 139 | 139 |

Se cumple el objetivo **medido** de menos de 300 MiB en esa fase.
**Hay una regresion de latencia:** se reduce memoria a costa de mas I/O y
serializacion. No se presenta como una mejora general de velocidad.
Stagings anteriores midieron 70,11 s/~237 MiB y 96,79 s/~240 MiB; tampoco mejoraban
el tiempo previo. La variacion refuerza que no son speedups estadisticos aislados.

Compacto final: SPL 12,96 s/~80,51 MiB. Last.fm final: pesado
4,92 s/~80,56 MiB y compacto 7,15 s/~75,93 MiB.
No son speedups estadisticamente aislados: corridas unicas, equipo compartido,
sin cache fria ni RSS de procesos hijos Git.

El limite no es universal: catalogos, claves, cobertura, ledger, audio y filas
objetivo del lote siguen en memoria. Filas/lotes mas grandes cambian el pico.
El espacio temporal maximo en disco no se ha medido. Inicial, mixtos y rebuilds
completos siguen por rutas mas costosas.

En cada benchmark se verifican: 7.347 tracks, 11.171 escuchas, 3.004 favoritos,
7.297 tracks enriquecidos, 50 registros nuevos por fase, audio correcto, inbox
vacio y snapshots anteriores sin cambios.

### B. Servicios de adquisicion y recuperacion

[CsvAcquisitionService](spoty/services/acquisition.py), con clientes inyectados:

- Captura de escuchas recientes y conservacion del item Spotify original.
- Publicacion durable de resultados normalizados y capturas SPL.
- Descarga paginada completa de favoritos, con reserva durable.
- Interrupciones mantienen la reserva; reintentos no simulan paginas faltantes.
- Paginacion incompleta/inconsistente nunca reemplaza la replica de favoritos.
- Recuperacion de un cambio pendiente mediante observacion de estado asentado.
  **No vuelve a ejecutar like/unlike.**
- Errores de proveedor/persistencia propagados; no SQL, configuracion implicita,
  reintentos HTTP ocultos ni consolidacion automatica.

Esta capa ya existe y esta probada; no significa que todos los adaptadores
productivos/proveedores, candidatos o scheduling esten conectados.

### C. Operacion y GitHub Free

- CLI sintetico: status/audit de solo lectura y consolidate con flag explicito.
- Solo repos bare existentes con cuentas synthetic-; no crea/copias repos reales.
- Audit agota particiones verificadas e identidades inbox/archive/revisiones;
  no verifica el contenido de cuarentena ni sustituye un plan de migracion.
- Preflight GitHub estricto por defecto.
- Politica explicita allow-unprotected-private para el riesgo Free aceptado:
  muestra riesgos/protecciones reales, no afirma que esten activadas.
- Publicos, archivados, identidad distinta, respuestas incompletas y errores
  HTTP siguen bloqueando. Errores CLI exponen tipo/status, no cuerpos ni tokens.

**Intento remoto real de solo lectura:**

- Repositorio/branch accesibles.
- GET de reglas de main: **HTTP 403**.
- No se ha establecido la causa (permisos/plan de GitHub).
- No se ha hecho publicacion sintetica remota ni cambiado reglas.

Este fallo es un bloqueo comprobado, no un permiso para relajar controles.

### D. Automatizacion e integracion verificable

- Nueva [demo de pipeline](examples/csv_pipeline_demo.py):
  adquisicion -> inbox -> consolidacion -> consultas/audit -> recuperacion.
  Usa Spotify falso y Git temporal, sin HTTP ni SQL, con capturas idempotentes
  e historicos fijados por commit.
- Nuevo [workflow manual](.github/workflows/csv-validation.yml) de pruebas
  sinteticas: permiso contents read, sin Secrets de datos, schedules ni checkout
  del repo de datos. No se ha subido ni ejecutado en GitHub.
- Los workflows productivos actuales no se han modificado.
- Se mantiene la UI CSV Summary de solo lectura, con sus pruebas AppTest;
  no se ha ampliado silenciosamente a reproduccion/escrituras reales.

## 3. Verificacion realizada

- Suite combinada final: **288 pruebas correctas**.
- Revalidacion focalizada posterior: 126 correctas, incluido un test adicional
  que comprueba reconstruccion audio completa frente a una proyeccion obsoleta.
  No se suman: las suites se solapan.
- Incluye almacenamiento local/GitHub simulado, agrupacion, staging, proyecciones,
  taxonomia, favoritos, adquisicion, recovery, CLI, Summary SQL/CSV y Streamlit.
- Incluye backups **solo sinteticos** en directorios temporales; no copia real.
- Tests de publicacion lazy, checksums/limites/orden, duplicados, disco lleno,
  cleanup, rollback tardio, reintentos y favoritos mal ubicados.
- Igualdad de todas las filas entre consolidacion incremental y rebuild oracle.
- Demo completa local correcta; tests bloquean llamadas HTTP/SQL.
- Benchmark pesado final supera comprobaciones y objetivo RSS <300 MiB.
- Compilacion Python correcta; Pylance sin errores en codigo nuevo/cambiado
  inspeccionado. Avisos informativos de mocks/fakes y bare-mode Streamlit no
  se confunden con fallos.

No se certifica la suite completa historica: existen incidencias previas
documentadas fuera de este alcance. No se ha usado una base SQL de produccion.

## 4. Estado del proceso total y lo que sigue

| Bloque | Estado |
| --- | --- |
| Arquitectura CSV/Git y servicios centrales | Implementados y probados localmente |
| Resumen/historico/cobertura/taxonomia CSV | Implementados |
| Catalogo y metadata incrementales | Implementados; SPL baja memoria con mayor latencia |
| Adquisicion y recovery de favoritos | Capa inyectada implementada; cableado remoto pendiente |
| Streamlit | Summary local read-only; resto sigue en implementacion original |
| GitHub real | Preflight bloqueado por HTTP 403; escritura/recovery no validados |
| Actions | Validacion sintetica manual preparada; produccion sin migrar |
| Backup personal | Pendiente exclusivamente en PC personal |
| Migracion de datos reales y paridad completa | Pendientes; no hay export real validado en este trabajo |
| Retirada de Neon | NO realizada; requiere superar los gates anteriores |

Orden restante:

1. Resolver acceso verificable a reglas/permisos GitHub y validar publicacion
   sintetica, concurrencia, idempotencia y recuperacion en namespace separado.
   No se puede resolver modificando permisos o plan a ciegas.
2. Ejecutar/verificar primer backup en PC personal y programarlo alli.
3. Reducir I/O/latencia de grandes proveedores; medir inicial/full/mixtos y disco.
4. Completar adaptadores/candidatos/planificacion/editor taxonomia/analisis.
5. Migrador desde export con esquema real: preservar campos legacy, originales,
   estados y distincion NULL/cadena vacia/JSON null; comparar totales/referencias.
6. Cablear superficies restantes de Streamlit y captura/consolidacion Actions,
   con flags, errores visibles, pending separado y rollback.
7. Validar paridad y despliegue; solo entonces retirar Neon y dependencias.

No se han marcado estos pasos como terminados por disponer de mocks o servicios.

## 5. Comandos seguros para retomar

Demo completa, sin datos ni credenciales:

```powershell
.\venv\Scripts\python.exe -m examples.csv_pipeline_demo
```

Consultar CLI, sin abrir repositorios:

```powershell
.\venv\Scripts\python.exe -m spoty.cli.operations --help
.\venv\Scripts\python.exe -m examples.github_data_demo --help
```

Preflight real de solo lectura, solo cuando corresponda volver a validar acceso:

```powershell
.\venv\Scripts\python.exe -m examples.github_data_demo --owner luiscaballeroramos --repository spoty-data --env-file .env --protection-policy allow-unprotected-private
```

No incluir --publish-synthetic hasta que el preflight sea satisfactorio y se use
un namespace nuevo. No clonar/respaldar datos reales en este equipo.

## 6. Persistencia y limites respetados

- Codigo, pruebas, este informe y continuidad estan guardados en el worktree.
- **Sin commit ni push.** Clonar el remoto no recupera cambios untracked/locales.
- No se han mostrado/copiado/actualizado credenciales.
- No se ha accedido al CSV adjunto, ni migrado/leido datasets reales.
- No se ha creado backup real, activado schedules, cambiado app desplegada
  ni retirado Neon.
- Temporales propios eliminados; no servidores ni tareas de fondo dejados activos.

Detalle general y antecedentes: [CONTINUIDAD-REFACTORIZACION.md](CONTINUIDAD-REFACTORIZACION.md).

## 7. Actualizacion: GitHub sintetico remoto validado (2026-11-10)

### Causa y tratamiento del 403

Se diagnostico sin mostrar token/cuerpo arbitrario:
GitHub responde exactamente "Upgrade to GitHub Pro or make this repository public
to enable this feature." en reglas de main. Cabecera de permiso aceptado:
metadata=read; cuota restante observada 4997. No era prueba de falta de escritura.

El transporte clasifica SOLO ese mensaje exacto/403 en un codigo no sensible,
descartando el cuerpo de error. Preflight permite esa causa exclusivamente en
allow-unprotected-private, despues de verificar repo privado/no archivado/head.
Strict sigue rechazando. Otros 403, permisos, rate limit, formato erroneo y
fallos al leer detalles de rulesets no se convierten en exito.

Salida explicita:
- rules_verification: unavailable_on_current_plan.
- branch_rules_verified: false.
- deletion/non_fast_forward: null (desconocido, no false ni "protegido").
- Riesgos branch_deletion/force_push siguen aceptados y pendientes de mitigacion
  por backup personal. No se cambio plan, permisos ni rulesets.

### Escrituras y lecturas remotas verificadas

Solo cuenta `synthetic-validation-20261110-0945`, privada, fixture artificial:
1. Capturas listening, SPL y favorito observados, consolidacion atomica.
2. Repetir publicacion en inbox/archive devuelve already_saved.
3. Head obsoleto rechazado; no force push.
4. Snapshot inicial: `60dcfa05ce27a806fbf8e1e524bcb177f015b731`.
5. Nuevo proceso reserva cambio favorito pendiente y lo resuelve con fake
   observacion asentada: no like/unlike ni llamada real a Spotify.
6. Consolidacion de favorito y lote Last.fm por ruta metadata-only/staging.
7. Snapshot final: `3994a69b35fa2e0f4d0c4b7932e5c49b46555832`.
8. Lectura independiente con adaptador nuevo verifica un track/artista/album/
   escucha, cero favoritos actuales, Last.fm disponible, inbox vacio y favorito
   anterior conservado en commit inicial.

La demo agrega --verify-synthetic-recovery para continuar SOLO un fixture
sintetico completado conocido. No es reparacion generica ni permiso de datos reales.
El historial de prueba se conserva para inspeccion; no se borra ni resetea.

No se ejecutaron estres multiwriter simultaneo, perdida real de red a mitad de
publicacion ni restauracion desde backup. Esas garantias siguen cubiertas
parcialmente por mocks y requieren pruebas operativas adicionales.
No se certifican cuotas/latencia remota ni despliegue Community Cloud.

Produccion/Neon/credenciales siguen sin cambios; no hubo backup ni migracion real.
Este avance sustituye el bloqueo del punto 1 del orden restante, no los demas gates.

### Validacion final local

110 pruebas pasaron en una ejecucion conjunta de transporte/preflight GitHub,
adquisicion/recovery, metadatos incrementales, operaciones, staging y pipeline.
Incluyen rechazo de cuenta no sintetica antes de crear transporte y ejecucion
explicita de recovery tras preflight. Compileall y git diff --check pasaron.
Pylance no detecto errores en los tests de GitHub; solo parametros no usados
en dobles de prueba. Estas 110 pruebas se solapan con ejecuciones anteriores,
no deben sumarse como casos distintos.

### Paso siguiente: imports aislados para Streamlit CSV (2026-11-10)

El entrypoint local ya salia antes de cargar OAuth/navegacion/otras paginas,
pero `app_views.summary` importaba al cargar el modulo partes del servicio SQL,
registro y Spotify. Se movieron al backend/accion que los necesita: en CSV el
import guardado confirma que no se intenta cargar `services.summary_queries`,
`services.synchronization`, `spotifyapi.errors` ni `_registration`.
El backend SQL conserva sus imports de manera diferida al seleccionarse.

Validacion: `tests.test_summary_queries` y `tests.test_csv_summary_view`, 22
pruebas correctas; compileall y `git diff --check` correctos. La primera
ejecucion de regresion detecto dos tests que parcheaban el antiguo simbolo
reexportado en `app_views.summary`; se actualizaron para parchear el servicio
SQL en su modulo fuente y quedaron correctos. Los avisos de contexto Streamlit
en AppTest son los avisos usuales de ejecucion bare-mode.

### Siguiente superficie: Metadatos en preview CSV

Se agrego seleccion explicita Resumen/Metadatos solo al arranque CSV. La pagina
de Metadatos reutiliza `metadata_overview` del servicio CSV para tracks, artists
y albums y conserva las graficas de disponibilidad por fuente/campo. No invoca
refresco de proveedores ni escritura; muestra commit/snapshot y lotes pendientes.
El SQL SummaryQueryService de la vista legacy ahora tambien se carga de forma
diferida cuando se usa sin servicio inyectado. El camino SQL normal no cambia.

Validacion conjunta SQL y CSV de Summary/Metadatos: 28 pruebas correctas,
incluyendo acceso CSV sin SQL/HTTP/config/OAuth/Spotify, selector y graficas;
compileall y `git diff --check` correctos. La primera expectativa del test uso
un conteo de tracks que no tenia metadatos en el fixture; se corrigio para
seleccionar el artista que si tiene enriquecimiento sintetico.

### Siguiente superficie: Pulse historico en preview CSV

Se agrego Pulse al selector de preview CSV. Usa la consulta historica existente
`PulseQueryService` con la proyeccion `track_audio` del snapshot y reutiliza los
graficos temporal/hexagonal. El filtro dispone de 1h/6h/1d/todo. Las escuchas
inbox pendientes quedan excluidas; no hay playback actual, polling, Spotify auth,
captura ni refresh de proveedor. Imports de reproduccion, auth, Summary SQL y
SmarterPlaylists quedan diferidos al camino live.

Validacion: 52 pruebas combinadas de Summary/Metadata/Pulse legacy y CSV correctas.
AppTest cubre periodo infinito, una escucha consolidada y la ausencia de imports
de auth/reproduccion/refresh y escritura. Compileall y `git diff --check` correctos.
El primer test intento usar periodo 1d con eventos sinteticos del 6 de noviembre
y reloj del 10; se corrigio para elegir explicitamente todo el historial.

Alcance actual: CSV ofrece Summary, Metadatos y Pulse **historico** de solo lectura.
Reproduccion y Pulse live, favoritos CSV, captura, escritura remota, Actions
productivos y cambio del default Neon/SQL no estan habilitados.

### Siguiente superficie de reproduccion: historial consolidado

Se agrego "Historial" al preview local CSV, reutilizando
`recent_listening_events` y `artist_names` de `CsvSummaryQueryService`. Muestra
las escuchas recientes del snapshot fijado, fecha UTC, cancion/artistas/album,
numero de reproducciones y ranking. No presenta control de playback ni estado
liked (que podria ser obsoleto frente a Spotify), ni ejecuta like/unlike.
La query no lee pendientes inbox; estos se muestran por separado. Acciones
live permanecen en la app SQL y no se importan en esta vista.

Validacion combinada: 125 pruebas correctas (historial CSV, queries, Pulse y
reproduccion SQL existente). AppTest verifica datos reales del fixture sintético,
ausencia de SQL/HTTP/imports Spotify y reproduccion y rechazo de escrituras.
Compileall, diff --check y diagnosticos Pylance correctos.

### Primitive service de mutacion de favoritos CSV

Se completo `CsvAcquisitionService.change_favorite`: primero persiste el request
ID/intencion en `FavoritesCoordinator`; unicamente si la reserva queda nueva
(`reserved`) ejecuta una llamada like/unlike; solo una respuesta `True` deja
publicar una revision CSV confirmada. Request IDs ya pendientes/resueltos no
disparan otra mutacion. Excepcion remota, respuesta False o fallo local al
guardar resultado dejan un journal pendiente. Recovery consulta el estado
actual con `are_tracks_liked` y publica `reconciled`, unicamente tras que el
caller confirme `remote_settled`; un timeout no demuestra settlement.

Se agregaron tests para asegurar journal antes del dispatch, exactamente una
mutacion ante retry, errores/False y fallo de persistencia tras mutacion seguido
de reconciliacion sin replay. Suite de adquisicion/coordinador: 61 pruebas
correctas. No se usaron tokens ni API Spotify real.

No se conecto aun a botones UI: el preview CSV solo tiene `LocalGitStore` en
read-only. Hay una frontera aparte para escritura local opt-in de fixtures
sinteticos, sin remotos, solo cuentas sinteticas y limitada a la cuenta elegida.
El constructor y la factoria exigen `SPOTY_CSV_ENABLE_SYNTHETIC_WRITES=1`.
Playback, favoritos UI y produccion siguen en SQL; la primitiva no es aun una
app CSV completamente independiente de Neon.

### Writer local opt-in para fixtures sinteticos

Se agrego `SyntheticLocalGitStore` y `load_synthetic_writable_store()`. Ambos
exigen opt-in explicito; validan repositorio bare sin remotos, rechazan cuentas
no sinteticas o repositorios mixtos y limitan cada publicacion al prefijo de la
cuenta sintetica seleccionada. No se conectaron botones de Streamlit ni
proveedores; la vista CSV continua read-only y SQL/Neon sigue siendo el backend
predeterminado. Las pruebas ejercitan la frontera usando fixtures locales y un
coordinador de favoritos, sin credenciales ni llamadas reales.

### Integracion sintetica de favoritos y contrato de recovery

Se integro el `CsvAcquisitionService` con un repositorio Git bare temporal y un
proveedor fake que aplica el like pero simula perdida de respuesta. Una nueva
instancia del servicio recupera el journal persistido; repetir el mismo request
ID no reenvia el like. Sin `remote_settled=True` no se consulta al proveedor.
Tras confirmar settlement, recovery solo observa el estado actual y guarda el
resultado como `reconciled` en el inbox; no vuelve a mutar. La integracion usa
Git local y proveedor fake; no hubo llamadas reales.

El preview CSV incorpora ahora un aviso de solo lectura del journal pendiente en
Resumen, Metadatos, Pulse e Historial. Lee el estado del HEAD local actual y lo
distingue de lotes inbox y del snapshot de datos fijado; si no hay pending tambien
lo indica. AppTest verifica el mensaje, la separacion del conteo inbox, ausencia
de botones, escrituras, SQL y HTTP. Un caso fija el snapshot a un commit previo
sin inbox pendiente y verifica que el aviso sigue detectando el journal durable
del HEAD actual. Las 15 pruebas de Summary/CSV UI pasan.

El renderer admite un callback de recovery solo si lo inyecta el llamador. El
flujo muestra checkbox de settlement y boton deshabilitado hasta confirmar;
pasa al handler el request ID que se estaba mostrando. `recover_pending_favorite`
puede exigir ese ID y rechaza un journal cambiado antes de consultar el
proveedor. Con proveedor fake se prueba que no hay observacion sin checkbox,
que el flujo confirmado solo observa y escribe un lote `reconciled`, y que un
fallo conserva el journal y muestra un incidente. El servicio rechaza un request
ID distinto al que se presento al usuario. Validacion final: 8 pruebas de
frontera/writer, 32 de adquisicion y 15 de Summary/CSV UI (58 en total); junto
con 62 regresiones de Spotify/reproduction pasaron 120 pruebas.

Se implemento `AuthenticatedSpotifyFavoriteRecovery`, un adaptador opt-in que
carga solo secretos Spotify de Streamlit al invocarse, confirma que el request
ID sigue pendiente antes de construir el cliente y pasa al servicio una interfaz
de solo lectura. `spotifyapi.spotifyclient` ahora importa `register.artist`
solo dentro de los metodos de artista, evitando cargar registro/DB en la ruta
de recovery. Pruebas verifican carga lazy de secretos sintéticos, falta de
construccion del cliente antes de consentimiento/ID válido y que no se importa
el registro heredado. No se conectó el adaptador al entrypoint Streamlit, ni se
probaron credenciales o llamadas reales; ese flujo necesita validación
autorizada aparte.
