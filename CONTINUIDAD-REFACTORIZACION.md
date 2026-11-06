# Continuidad de la refactorizacion CSV/Git

Actualizado: 2026-11-06. Este documento permite retomar el trabajo sin la
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
| Taxonomia editorial | Dataset propio validado/versionado, aun pendiente en CSV |
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
- `spoty/storage/csv_codec.py`: esquemas, NULL sin perdida, envelopes y despacho
  por tipo de lote; snapshots/receipts evolucionados hasta v3.
- `spoty/storage/contracts.py`: protocolo `DataStore`, errores, validacion
  de rutas/commits y publicacion de lotes compartida.
- `spoty/storage/local_git.py`: repositorio bare local SHA-1, commits atomicos
  y compare-and-swap de ref; no utiliza el worktree del producto como datos.
- `spoty/storage/snapshots.py`: lecturas fijadas por commit, particiones,
  manifiestos y compatibilidad explicita de snapshots v1/v2/v3.
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

## 6. GitHub real: estado y bloqueo ACTUAL

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

**Proxima decision pendiente:**

1. GitHub Pro (repo personal privado): habilitar/aplicar reglas y repetir preflight.
2. Mantener GitHub Free: estudiar alternativas y revisar garantias del transporte
   para historia alterable, sin llamar equivalente a la garantia actual.
3. Reconsiderar almacenamiento con concurrencia adecuada, conservando CSV/Streamlit.

El usuario pidio explicacion de implicaciones pero NO eligio entre estas opciones.
No asumir que acepta pagar ni hacer publico el repo privado como solucion.

### Credenciales en el PC personal

- No copiar ni commitear credenciales del equipo actual.
- `.env` fue comprobado ignorado/no versionado en este worktree.
- El usuario confirmo que el token usado no estaba compartido en adjuntos/chat.
- En el PC nuevo, configurar un token propio restringido SOLO al repo de datos,
  Contents Read and write, caducidad corta; no pegarlo en el chat.
- Loader admite GH_TOKEN o GITHUB_TOKEN en archivo explicito; entorno tiene
  prioridad. No carga `.env` implicitamente al importar el paquete.
- Streamlit/Actions usaran sus Secrets al desplegar, no `.env` del repo.
- No leer/mostrar archivos de credenciales con view/cat ni volcar respuestas HTTP.
- Revocar el token temporal del equipo que se deja cuando ya no sea necesario.

## 7. Validacion realizada y comandos para retomar

Validaciones focalizadas sucesivas:

- Prototipo inicial: 87 pruebas correctas (33 nuevas + 54 regresiones), demo,
  sintaxis y wheel instalada desde copia temporal limpia.
- Metadatos: 120 pruebas focalizadas correctas.
- Favoritos: 171 pruebas focalizadas correctas; comprobaciones finales de receipts
  antiguos y manifiesto requerido tambien correctas.
- Transporte: 109 pruebas combinadas correctas (23 nuevas entonces + 86 previas),
  seguido de 25 pruebas transporte correctas con limite exacto/retries.
- Configuracion/preflight: 30 pruebas focalizadas correctas.
- Compileall y git diff --check correctos; Pylance sin errores en comprobaciones
  previas. Ultimas consultas Pylance de demo/loader remoto expiraron: no afirmar
  que sus diagnosticos finales fueron comprobados. Tests y compilacion si pasaron.
- No se ha certificado rendimiento/coste del export completo.

En Windows, desde la raiz del repo, con dependencias del proyecto instaladas:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_csv_git_prototype tests.test_csv_metadata_prototype tests.test_csv_favorites_prototype tests.test_github_data_store tests.test_github_local_configuration -q
.\venv\Scripts\python.exe -m examples.csv_git_demo
.\venv\Scripts\python.exe -m compileall -q spoty examples
git --no-pager diff --check
```

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

1. Resolver decision GitHub Free/Pro/otro backend y documentar garantia final.
2. Validar destino real con datos sinteticos: reglas, permisos, lectura fija,
   publicacion atomica, retries/concurrencia, recovery e idempotencia.
3. Medir tiempo, memoria, peticiones y crecimiento Git con volumen representativo;
   optimizar updates incrementales/particiones antes de dar por viable el export.
4. Completar servicios CSV faltantes: adquisicion/recovery remoto de favoritos,
   candidatos/enriquecimiento, refresh scheduling y resultados de errores,
   taxonomia, consultas de cobertura/resumen/historico y analisis.
5. Separar integraciones/analisis/UI de almacenamiento reutilizando helpers.
   Cablear cada superficie; no sustituir SQL por lectura de CSV en cada grafico.
6. Migracion validada desde export: schemas reales por migraciones, checksums,
   integridad referencial, totales, muestras; invalidos aparte sin perder origen.
   COPY PostgreSQL necesita lector que distinga NULL y cadena vacia entrecomillada:
   DictReader generico no basta.
7. Integrar Streamlit: modo/flag CSV explicito, pendientes visibles, errores de
   persistencia separados, Pulse live conservado y favoritos reconciliables.
8. Integrar Actions: captura durable y consolidacion, Secrets, ubicacion,
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
> El acceso al repo privado ya funcionaba, pero GitHub devolvia 403 por plan al
> consultar reglas de main. Primero estudiemos/decidamos Free vs Pro o alternativa.
> No leas/muestres secretos, no publiques datos reales, no cambies produccion
> y no supongas resuelta la proteccion ni terminada la migracion.
