# Supuestos y decisiones de diseno

Fecha de revision: 2026-10-14.

Este documento recoge el contexto de diseno de spoty y propuestas para una
refactorizacion incremental. No describe una arquitectura ya implementada ni
autoriza cambios en el codigo. La revision que lo origina es estatica: no se han
medido tiempos, consumo de memoria ni planes de ejecucion de PostgreSQL.

## Estado de las decisiones

- Confirmado por el usuario: una sola persona y una sola cuenta Spotify por ahora.
- Confirmado por el usuario: los botones de la UI no muestran ayudas emergentes al pasar el cursor; no pasar texto mediante `help` en componentes Streamlit.
- Observado en el codigo: Streamlit, scripts CLI y GitHub Actions consumen Spotify
  y PostgreSQL; existen analisis con pandas y scripts exploratorios de metadatos.
- Propuesto, pendiente de aprobacion: monolito modular en un unico repositorio,
  contratos explicitos de error, servicios reutilizables y persistencia acotada.
- No requerido actualmente: multitenancy, microservicios, API HTTP interna,
  orquestador de trabajos o plataforma externa de observabilidad.

## Supuestos generales

| ID | Supuesto y estado | Consecuencia | Cuando revisarlo |
| --- | --- | --- | --- |
| S01 | Un usuario y una cuenta Spotify. Confirmado. | No agregar user_id ni aislamiento entre cuentas ahora. | Antes de admitir otra cuenta o usuario. |
| S02 | Un repositorio para producto, CLI, analisis y laboratorio. Propuesto. | Separar responsabilidades con paquetes, no con servicios remotos. | Si una parte tiene equipo, consumidores o ciclo de publicacion independiente. |
| S03 | PostgreSQL es el almacenamiento principal; Neon es el despliegue actual. Observado. | No cambiar motor; no convertir Neon en requisito del dominio. | Ante otro despliegue, pruebas locales o requisitos de coste. |
| S04 | Spotify es autoridad de favoritos y estado de reproduccion. Propuesto. | La DB es una replica reconciliable de favoritos; los snapshots son auxiliares. | Si se incorporan proveedores o preferencias propias independientes. |
| S05 | Un usuario no implica un solo proceso. Observado. | UI, CLI y Actions pueden solaparse; preservar idempotencia y controlar sincronizaciones destructivas. | Si aumenta la frecuencia o aparecen conflictos. |
| S06 | Priorizar integridad y claridad antes que rendimiento no medido. Propuesto. | Evitar borrados con datos parciales; medir antes de agregar pooling o caches adicionales. | Si latencia, volumen o conexiones incumplen objetivos acordados. |
| S07 | UTC para instantes persistidos y zona configurable para presentacion. Propuesto. | Unificar conversiones; no reinterpretar una fecha terminada en Z como hora local. | Antes de migrar datos existentes, comprobar que instantes contienen. |
| S08 | Metadatos externos son enriquecimiento opcional. Propuesto. | Un fallo de Last.fm o MusicBrainz no debe impedir registrar escuchas ni reproducir. | Si pasan a ser indispensables para una funcion. |
| S09 | El estado local de reproduccion es recuperable, no fuente de verdad. Propuesto. | Admitir snapshots obsoletos y fallo de escritura sin perder datos principales. | Con varias instancias o almacenamiento efimero. |

## 1. Errores, ausencia de datos y sincronizacion parcial

### Problema observado

[SpotifyClient._run](spotifyapi/spotifyclient.py#L104) convierte fallos en None o
False. Sus consumidores no pueden distinguir una respuesta valida sin contenido
de un timeout, un 429 o un problema de autorizacion.

[save_liked_track_ids](_liked_tracks.py#L14) sale del bucle tanto al terminar como
al fallar una pagina y despues calcula borrados. Una lectura incompleta, o una
lectura iniciada con offset distinto de cero, no permite reconciliar toda la lista.

### Contrato propuesto

- En el adaptador Spotify, devolver datos cuando la lectura tuvo exito. Una lista
  vacia o playback ausente siguen siendo resultados validos.
- Traducir fallos a excepciones pequenas y especificas: autorizacion, limite de
  peticiones, proveedor no disponible y respuesta invalida. Conservar la causa.
- En el servicio, representar el estado del caso de uso: completado, parcial,
  aplazado o fallido, con contadores y motivo. No duplicar excepciones y Result
  en todas las funciones: cada contrato tiene una frontera concreta.
- Un 429 debe conservar retry_after; no equivale a cero favoritos ni a no estar
  reproduciendo. Un fallo tampoco debe cachearse como un valor negativo valido.

Para favoritos, descargar y validar todas las paginas antes de reconciliar.
Exigir inicio desde cero y finalizacion verificable segun la paginacion del
proveedor. Una respuesta malformada, interrupcion o cobertura parcial cancela
los borrados. Una lista vacia completa si puede justificar eliminaciones.

No mantener una transaccion DB abierta durante la descarga. Aplicar altas y
bajas juntas en una transaccion corta al terminar. Para el volumen actual se
puede mantener el conjunto de IDs en memoria; staging solo si el volumen lo
justifica. Un recorrido completo no garantiza un snapshot atomico en Spotify:
si la biblioteca cambia durante la paginacion, abortar ante inconsistencias
detectables y reconciliar de nuevo. Evitar dos reconciliaciones concurrentes y
coordinar las acciones locales de favoritos con esa misma politica.

Pruebas necesarias: fallo en primera pagina, fallo intermedio, 429, respuesta
malformada, offset parcial, biblioteca vacia completa y sincronizacion repetida.

## 2. Conexiones, transacciones y escalabilidad

### Problema observado

[SimpleDB](register/db.py#L10) abre una conexion y un cursor y ejecuta DDL en su
constructor. Sus escrituras hacen commit individual y no ofrece cierre explicito.
El registro guarda varias entidades separadamente: si una falla, las anteriores
pueden haber quedado confirmadas. Cada commit supone trabajo y latencia remota.

CREATE TABLE IF NOT EXISTS no migra tablas existentes: no introduce cambios de
columnas, restricciones o indices en una tabla que ya exista. Ejecutarlo en cada
instancia tampoco deberia ser un requisito de una lectura o de un favorito.

### Solucion propuesta

- Crear/cerrar conexiones mediante context managers con un propietario explicito.
- Quitar la creacion de esquema del constructor y usar migraciones versionadas,
  ejecutadas como paso de preparacion o despliegue, no en cada operacion.
- Hacer que el servicio controle la transaccion y que las operaciones de
  persistencia usen esa conexion sin confirmar cada fila.
- En el registro, guardar catalogo y evento de forma atomica por evento o lote
  acotado. Elegir tamano y tratamiento de registros invalidos con pruebas y medidas.
- No mantener transacciones ni bloqueos durante llamadas HTTP o pausas de reintento.
- Preservar restricciones de unicidad en DB: comprobar antes de insertar es una
  optimizacion, no proteccion suficiente frente a ejecuciones concurrentes.

No se propone una conexion global compartida por todos los hilos ni una
transaccion enorme para un trabajo entero. Transacciones cortas y lotes acotados
mejoran consistencia sin bloquear innecesariamente. Un pool limitado puede
amortizar conexiones si las medidas lo justifican; necesita limites por proceso,
no es requisito para un usuario. Revisar planes e indices antes de agregar caches.

Spotify y PostgreSQL no comparten transaccion. Si un favorito se cambia en
Spotify pero falla su replica local, informar de exito remoto con replica
pendiente y reconciliar. No prometer atomicidad ni deshacer automaticamente una
accion remota cuyo estado pueda ser incierto.

## 3. Alcance de usuario unico

No se introducira soporte multiusuario como parte de la refactorizacion propuesta.
Un cache de tokens y un snapshot local pueden mantenerse con permisos adecuados,
exclusion de Git y rutas configurables. Nunca registrar sus contenidos sensibles.
Sus escrituras siguen necesitando una politica ante procesos simultaneos.

El aislamiento de usuarios se reabrira antes de ofrecer la app a otras cuentas.
En ese momento revisar tokens, propietarios de datos, caches, rate limits y
snapshots, no solamente agregar una columna user_id.

## 4. Reproduccion: presentacion frente a coordinacion

### Problema observado

[La vista](app_views/reproduction.py#L153) contiene persistencia en disco,
estimacion del progreso, cache, restauracion de cola/contexto/dispositivo y
acciones ademas del renderizado. Esas decisiones dependen de st.session_state
y st.rerun, por lo que resulta dificil probarlas sin simular Streamlit.

[ReproductionService](services/reproduction.py#L6) solo cubre acciones con DB.
La frontera propuesta debe ser el caso de uso, no si una accion utiliza DB.

### Solucion propuesta

- Un controlador de reproduccion mantiene un estado tipado y coordina consulta,
  pausa, reanudacion, anterior, siguiente y favoritos.
- Recibe cliente Spotify, almacenamiento de snapshots y reloj; no importa Streamlit.
- La vista envia una accion, recibe estado/resultado y decide como renderizar y
  cuando pedir rerun. Los fragments siguen siendo responsabilidad de la UI.
- Un adaptador de sesion aloja el estado en Streamlit. Esas claves no deben
  filtrarse al controlador; la persistencia en disco es otro adaptador pequeno.
- Mantener politicas actuales utiles: intervalos de consulta, estimacion de
  progreso, consultas por lotes y firma de cambios visibles.
- Extraer estilos/composiciones solo donde aclare el modulo; no crear un archivo
  por funcion ni convertir el controlador en otro modulo que lo contenga todo.

Una frontera similar para resumen permite obtener datos mediante un servicio de
consulta y reutilizarlos sin ejecutar SQL dentro del renderizado. La navegacion
no deberia ocultar una sincronizacion: si se conserva ese comportamiento, debe
ser una accion de entrada explicita con su estado y politica de repeticion.

## 5. Modelos, consultas y conversiones

### Problema observado

[Track](register/track.py#L131) conoce tablas y serializacion SQL. El registro y
[la recuperacion de metadatos](_missing_data.py#L9) repiten conversiones Spotify.
[Resumen](app_views/summary.py#L41) y analisis abren conexiones y transforman
datos por caminos diferentes. No todo SQL distinto es duplicacion: una consulta
de ranking y la carga para analisis pueden requerir implementaciones diferentes.

### Solucion propuesta

- Modelos/dataclasses para datos y reglas; sin save() ni conexion DB como objetivo.
- Conversiones desde Spotify centralizadas, separando campos requeridos y
  enriquecimiento opcional. Ausencia de un campo no debe borrar metadata existente.
- Repositorios pequenos por necesidad: guardar un evento con su catalogo,
  reconciliar favoritos y consultar resumen/rankings. No una interfaz por tabla
  ni un repositorio generico que publique SQL arbitrario a todas las vistas.
- Serializacion y SQL dentro de persistencia. Consultas analiticas pueden
  devolver DataFrames; no imponer modelos de dominio a todos los informes.
- Compartir reglas de fechas, IDs y periodos, no forzar una consulta universal.

Mantener psycopg es compatible con esta arquitectura. Un ORM no es requisito.
Cambiar TEXT JSON a JSONB o normalizar relaciones debe ser una migracion aparte,
con beneficios medidos, compatibilidad y verificacion de datos historicos.

## 6. Un repositorio, responsabilidades separadas

La recomendacion provisional es un monolito modular, migrado mediante extracciones
pequenas. UI, CLI y jobs reutilizan los mismos casos de uso y adaptadores.

Distribucion conceptual, no estructura ya acordada:

```text
spoty/
  settings.py          configuracion cargada en el arranque
  playback/            estado y coordinacion de reproduccion
  listening/           registro de eventos
  library/             favoritos y catalogo
  analytics/           calculos e informes
  metadata/            enriquecimiento opcional
  integrations/        Spotify y proveedores externos
  persistence/         conexiones y consultas
  ui/                  vistas y componentes Streamlit
  cli.py               comandos que llaman a los mismos servicios
migrations/            evolucion explicita del esquema
examples/              demo del laboratorio visual
tests/                 pruebas de contratos y casos de uso
```

Solo crear paquetes cuando tengan contenido y una responsabilidad concreta.
El codigo reutilizable del laboratorio debe ser distinguible de su demo, aunque
ambos permanezcan en el mismo repositorio. Datos externos, outputs y caches no
son paquetes de codigo ni deberian formar parte del artefacto instalable.

Corregir empaquetado y configuracion es independiente de dividir repositorios:
[pyproject.toml](pyproject.toml#L16) referencia algunos nombres que no coinciden
con los archivos actuales. Elegir un minimo Python compatible con el codigo y
una fuente principal de dependencias, con extras para UI, analisis y desarrollo.
Validar cada configuracion cuando se utilice, no exigir credenciales Spotify
para analizar una DB. Importar un modulo no debe lanzar HTTP ni escribir archivos.

Separar repositorios solo ante consumidores externos reales, publicacion
independiente o equipos/propiedad diferentes. Un monorepo tambien permite
desplegar UI y jobs separadamente. Mas repositorios no eliminan acoplamiento:
lo transforman en coordinacion de versiones y contratos.

## 7. Observabilidad util y discreta

- Usar logging estandar con niveles y salida a consola; archivo rotativo local
  opcional. No requiere un servicio externo al inicio.
- Registrar operacion, proveedor, tipo de fallo, duracion, contador y run_id.
  El detalle tecnico y traceback corresponden al log, no a la pantalla normal.
- No registrar tokens, claves, DSN, cabeceras sensibles ni URLs OAuth con code.
  Filtrar/redactar mensajes de proveedores antes de enviarlos al log.
- Registrar un error una sola vez en la frontera responsable. Las capas internas
  conservan y propagan su causa en lugar de emitir el mismo traceback repetidamente.
- En consultas automaticas, conservar el ultimo dato valido y marcarlo como
  desactualizado con hora de ultima actualizacion; no sustituirlo por un vacio falso.
- Mostrar un aviso discreto por incidente, no uno por segundo o rerun. Agrupar
  repeticiones por operacion/tipo y actualizar contador; indicar recuperacion.
- En acciones explicitas, informar inmediatamente si fallo o si queda replica
  local pendiente. No silenciar un fallo de guardado o una necesidad de autorizar.
- Ofrecer detalles bajo una seccion desplegable; evitar tracebacks permanentes,
  popups repetidos y notificaciones externas por fallos transitorios.
- En CLI y Actions, terminar con codigo no cero ante un fallo, o sincronizacion
  incompleta que no cumpla el objetivo del job; imprimir un resumen verificable.

Reintentos acotados con backoff, jitter y Retry-After cuando corresponda. No
reintentar automaticamente comandos no idempotentes como siguiente o agregar
a cola: un timeout no prueba que Spotify no ejecutara el comando. Reconsultar
estado cuando sea posible y comunicar incertidumbre cuando no pueda resolverse.

## Orden de trabajo propuesto

1. Acordar estos contratos y agregar pruebas de fallos, datos parciales y fechas.
2. Corregir semantica de error y seguridad de reconciliacion; incorporar logging
   minimo y resultados de acciones para poder diagnosticar los cambios siguientes.
3. Acotar conexiones y transacciones y separar inicializacion/migraciones de DB.
4. Extraer consultas de resumen y controlador de reproduccion sin cambiar la UI.
5. Unificar conversiones, configuracion y empaquetado; mover modulos y demo.
6. Medir antes de introducir pooling, indices nuevos, caches o trabajos adicionales.

Cada paso debe conservar el comportamiento comprobado salvo cambios de contrato
aprobados. No incluye migracion multiusuario ni separacion de repositorios.

## Criterios de aceptacion para una futura refactorizacion

- Un fallo o una descarga parcial nunca se interpreta como biblioteca vacia.
- Se puede repetir una sincronizacion sin duplicar eventos y sin perder favoritos.
- Una unidad de escritura tiene commit/rollback y cierre de conexion verificables.
- No se mantienen transacciones durante consultas HTTP ni esperas de reintento.
- La coordinacion de reproduccion se prueba sin Streamlit, red ni DB reales.
- UI, CLI y jobs reutilizan contratos sin importar vistas desde servicios.
- Un fallo automatico no genera avisos repetitivos y no oculta datos desactualizados.
- Los logs permiten identificar la operacion fallida sin revelar secretos.
- El artefacto instalable y sus comandos resuelven los modulos publicados.
- Las fechas historicas se verifican antes de cualquier correccion o migracion.

## Mantenimiento del documento

Actualizar estado, motivo y consecuencias al aprobar una decision. Mantener
separadas decisiones vigentes, propuestas y hechos observados. Revisar S01,
S05 y S09 antes de agregar cuentas, instancias o procesos de sincronizacion.
