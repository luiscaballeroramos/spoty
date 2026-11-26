# Prueba aislada de Smarter Playlists

## Objetivo

Obtener los campos disponibles para una cancion, su artista y su album mediante
una sesion propia y autorizada de Smarter Playlists. No modificar la aplicacion,
las playlists de Spotify ni la base de datos del proyecto.

## Pasos

1. Iniciar sesion manualmente en https://smarterplaylists.playlistmachinery.com/.
2. Elegir una cancion concreta, preferiblemente mediante su enlace de Spotify.
3. Inspeccionar las respuestas de la web y su inventario de componentes para
   comprobar que consultas de cancion, artista y album admite realmente.
4. Ejecutar solo consultas necesarias, sin guardar resultados como playlists.
5. Guardar las respuestas originales y un inventario de campos en esta carpeta.
6. Automatizar la consulta o la exportacion segun lo observado.

## Evidencia inicial

- La web consultada indica la version 3.12.3 y requiere iniciar sesion con Spotify.
- Su cliente publico referencia `/api/inventory`,
  `/api/programs/{id}/run?save=false` y `/api/runs/{id}/status`.
- El cliente genera el CSV en el navegador a partir de los campos de resultados.
- Estas rutas son internas: falta comprobar sus respuestas autenticadas y las
  garantias reales de `save=false` antes de ejecutar un programa.
- Aun no se ha comprobado la disponibilidad de metricas de artista o album.

## Seguridad y limites

- Introducir credenciales y aceptar permisos solo en el navegador.
- No copiar cookies, tokens, contrasenas ni archivos de sesion en esta carpeta
  ni en el chat.
- No reutilizar automaticamente las credenciales de Spotify de la aplicacion:
  la autorizacion de Smarter Playlists es independiente.
- No convertir campos ausentes en ceros ni atribuir metricas de una cancion
  a su artista o album. Separar datos originales de cualquier agregado calculado.
- No prometer todas las metricas existentes: registrar lo que el servicio
  devuelve, sus valores ausentes y la fecha de consulta.

## Resultado de la prueba: 2026-10-15

Ejemplo elegido de `outputs/The Killers/Hot Fuss/spotify_track.json`:

- Cancion: Mr. Brightside, `spotify:track:3n3Ppam7vgaVa1iaRUc9Lp`.
- Artista: The Killers, `spotify:artist:0C0XlULifJtAgn6ZNCW2eu`.
- Album: Hot Fuss, `spotify:album:4OHNH3sDzIxnmUADXzv2kT`.

La API interna respondio correctamente desde la sesion autorizada del navegador.
Se crearon tres programas propios, con nombres `LAB metrics 2026-10-15 track`,
`LAB metrics 2026-10-15 artist` y `LAB metrics 2026-10-15 album`. No se modificaron
programas anteriores ni se configuro ninguna programacion automatica.
Los tres resultados finales tienen `playlist_uri: null`.

| Entidad | Algunos valores recibidos |
| --- | --- |
| Cancion | Tempo 148.114; danceability 0.355; energy 0.918; valence 0.24; acousticness 0.00119; instrumentalness 0; loudness -4.36; popularity 66 |
| Artista | Followers 8869133; popularity 81; country US; start year 2001; generos de Spotify y MusicBrainz |
| Album | Popularity 65; total_tracks 14; release_date 2004-06-15; label EMI; album_type album |

Se solicitaron los 41 atributos de ordenacion de cancion considerados relevantes
por el colector, 10 de artista y 8 de album. Tambien se solicito enriquecimiento
de generos para la cancion. Esto no demuestra que no existan otros campos o
fuentes en el servicio.

No se recibieron `album_type`, `artist_end_year`, `followers`, `mo_score`,
`relinked_id` ni `total_tracks` en la entidad cancion. `artist_end_year` tampoco
se recibio en artista. Todos los atributos solicitados del album tuvieron valor.
Algunos campos ausentes en cancion si existen en su album o artista: no son
intercambiables.

Importante: `Sorter` elimina elementos sin el atributo solicitado. Por eso el
colector usa ramas independientes y concatena tambien el resultado original.
Las filas repetidas se combinan por campos para este unico objetivo; se conserva
la respuesta original y se comprueba que no haya valores contradictorios.

Los datos se obtuvieron con `cache_mode: auto`: la fecha de captura no garantiza
que cada valor este actualizado a ese instante. Los campos enriquecidos son
afirmaciones del proveedor, no hechos verificados independientemente.

## Archivos

- `targets.json`: identificadores elegidos del sistema local.
- `collect.js`: consultas autenticadas, definicion de programas y combinacion.
- `capture_server.py`: transporte local limitado a esta carpeta y estos objetivos.
- `report.json`: manifiesto y respuestas originales completas de las tres ejecuciones.
- `summary.json`: campos combinados, ausentes solicitados y conflictos.

## Repetir con asistencia del navegador integrado

1. Mantener una sesion autorizada de Smarter Playlists.
2. Iniciar `py experiments/smarterplaylists/capture_server.py` desde la raiz.
3. Desde una pagina local del servidor, leer `collect.js` y `targets.json` con
  Playwright y cargar el script en el contexto de la pagina de Smarter Playlists.
4. Ejecutar `smarterPlaylistsLab.start(targets)` en ese contexto. Reutiliza solo
  los tres programas con los nombres de laboratorio indicados arriba.
5. Conservar el manifiesto devuelto y ejecutar `smarterPlaylistsLab.collect(manifest)`
  cuando los trabajos hayan terminado. Si siguen pendientes, no se guarda informe.
6. Transferir el informe a una pagina local y enviarlo a `/capture` con JSON.
  El servidor valida identidades, estado, conflictos y ausencia de playlist antes
  de guardar. Una repeticion reemplaza `report.json` y `summary.json`.
7. Detener el servidor local al terminar.

El colector no funciona directamente con Node.js: necesita el contexto autorizado
de la web. No se han guardado cookies ni tokens y aun no hay una tarea desatendida.
