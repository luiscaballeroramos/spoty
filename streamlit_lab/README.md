# Streamlit Lab

Subproyecto independiente para experimentar con interfaces definidas mediante
reglas. No importa codigo de Spotify ni necesita sus credenciales o base de datos.
Requiere Python 3.10 o posterior. Todos los comandos parten de esta carpeta.

## Ejecutar en PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502
```

## Estructura

- `blocks.py`: datos y validacion; no depende de Streamlit.
- `layout.py`: cuadriculas y grupos/minilayouts; sin Streamlit.
- `styles.py`: apariencia, espacios, esquinas, bordes y sombras; sin Streamlit.
- `controls.py`: editores de cada configuracion en la barra lateral.
- `renderer.py`: traduce las reglas a CSS y devuelve un contenedor Streamlit.
- `app.py`: ejemplos y controles para experimentar.
- `test_blocks.py`: pruebas de reglas y de la aplicacion.

## Definir un bloque

```python
block = Block(
    key="resumen",
    color="#dcefe7",
    width=Size(65, "%", min_px=280, max_px=900),
    height=Size(30, "vh", min_px=160, max_px=400),
)
with render_block(block):
    st.write("Contenido")
```

Cada bloque necesita una clave unica. `Size` contiene el valor deseado, su unidad
y limites opcionales en pixeles. `%` representa el ancho del contenedor disponible;
`vw` y `vh` representan porcentajes del ancho y alto de la ventana completa
(incluida la zona ocupada por la barra lateral). Para alto proporcional se usa `vh`,
no `%`. `px` representa un tamano fijo, sujeto a los limites.

El ancho disponible tiene prioridad sobre el minimo: un bloque nunca debe ensanchar
la pagina en movil. La altura se limita independientemente y el contenido que no
cabe dispone de desplazamiento interno. Un minimo superior al maximo se rechaza
y la vista conserva la ultima configuracion valida.

## Posicion y alineacion

```python
from blocks import Block, Position, Size
from layout import Layout
from renderer import render_block, render_layout

layout = Layout(rows=2, columns=2, horizontal="center", vertical="end", gap_px=16)
blocks = [
    Block("resumen", width=Size(80, "%"), position=Position(row=1, column=2)),
    Block("detalle"),
]
with render_layout(blocks, layout):
    for block in layout.ordered_blocks(blocks):
        with render_block(block):
            st.write(block.key)
```

`Layout` ofrece una cuadricula fija o un flujo automatico por filas o columnas.
En flujo por filas, `columns` fija el ancho de la cuadricula y las filas crecen
segun se necesiten; en flujo por columnas, `rows` fija su alto y se crean columnas
segun hagan falta. El modo fijo conserva los limites de filas y columnas. Una fila
con varias columnas coloca bloques en horizontal; varias filas con una columna los apilan. Las alturas
de fila se ajustan al contenido, incluidas las necesidades de los bloques que
abarcan varias filas. `horizontal` y `vertical` aceptan `start`, `center` y `end`,
y alinean cada bloque dentro de su area reservada.
El centrado vertical se aprecia cuando hay bloques de distintas alturas en una fila.

`Position` usa indices desde 1. Sin posicion explicita, los bloques ocupan las
celdas libres por filas, respetando primero todas las posiciones fijas. Se rechazan
claves repetidas, colisiones, posiciones fuera de rango y cuadriculas insuficientes.
El panel conserva la ultima configuracion valida cuando una edicion no es valida.

Los anchos porcentuales se calculan respecto al area asignada. Por defecto se
mantienen las filas y columnas en movil. Con `mobile_breakpoint_px` el layout se
convierte en una columna por debajo de ese ancho de ventana.

## Minilayouts anidados

Un `Group` contiene claves de bloques u otros grupos y tiene su propio `Layout`.
El grupo solo define estructura y distribucion: no tiene color, tamano ni estilos.
Cada bloque conserva sus dimensiones y apariencia. El orden declarado en
`children` determina el recorrido; no se necesita `order`.

```python
from layout import Group, Layout
from renderer import render_group

dashboard = Group(
    "dashboard",
    children=(
        Group("contenido", ("principal", "resumen"), Layout(rows=1, columns=1, flow="row")),
        Group("lateral", ("secundario", "detalle", "actividad"), Layout(rows=1, columns=1, flow="row")),
    ),
    layout=Layout(rows=1, columns=2, mobile_breakpoint_px=640),
)

render_group(dashboard, blocks, lambda block: st.write(block.key))
```

En el ejemplo, `contenido` y `lateral` son columnas en escritorio. En pantalla
estrecha, el layout raíz se apila y muestra primero `principal` y `resumen`,
seguidos de `secundario`, `detalle` y `actividad`. Los layouts internos son
independientes; `position` y `span` de cada bloque se interpretan dentro de su
grupo. El breakpoint raíz se hereda para que `hide_on_mobile` también funcione
dentro de los grupos. En móvil se adaptan visualmente las posiciones y extensiones,
pero los valores guardados y los estilos y medidas de los bloques se conservan.

## Composiciones multicelda

`Span(rows, columns)` define la extension rectangular de un bloque, por defecto
1 x 1. Funciona con posicion fija o automatica:

```python
from blocks import Block, Position, Span

block = Block("principal", position=Position(1, 1), span=Span(rows=2, columns=2))
```

`Position` es la esquina superior izquierda y `Span` reserva todas las celdas del
rectangulo. `Size` sigue controlando el tamano visual dentro de esa area: aumentar
el numero de filas no multiplica automaticamente la altura del bloque. El ancho
porcentual se refiere al area completa, incluidos los espacios entre sus columnas.

El ejemplo inicial usa un grupo raíz con dos minilayouts: `contenido` contiene
`principal` y `resumen`; `lateral` contiene `secundario`, `detalle` y `actividad`.
Los hijos se apilan según su layout local. `Layout.place()` sigue disponible para
composiciones planas que necesiten posiciones o extensiones explícitas.

El panel `Distribucion` edita el layout raíz. El selector de bloque conserva los
controles de apariencia y dimensiones individuales. `Restablecer ejemplo` recupera
los cinco bloques y sus grupos. Los cambios duran durante la sesión. El JSON incluye
la jerarquía de grupos y la configuración de los bloques; todavía no se puede
importar. La estructura inicial está en `DEFAULT_GROUP` dentro de `app.py`.

El renderizador usa la clase `st-key-...` del contenedor y CSS: hay que comprobar
la vista al actualizar Streamlit. La dependencia queda fijada a la version probada.

## Configurar layout y bloque

Todas las clases de configuracion son dataclasses inmutables. Para cambiar una
propiedad sin perder otras, usa `dataclasses.replace`. Las medidas de estilo son
pixeles; las opacidades estan entre 0 y 1. Ninguna clase de datos requiere Streamlit.

```python
from dataclasses import replace
from blocks import Block, Position, Size, Span
from layout import Layout
from styles import BlockStyle, Border, Corners, Insets, Shadow

layout = Layout(
    rows=3, columns=4,
    row_gap_px=12, column_gap_px=20,
    padding=Insets(top=16, right=24, bottom=16, left=24),
    column_weights=(2, 2, 1, 1),
    min_row_height_px=100,
    horizontal="center", vertical="start",
    mobile_breakpoint_px=640,
)

appearance = BlockStyle(
    padding=16,
    radius=Corners(top_left=8, top_right=8),
    border=Border(width_px=2, color="#365c4a", style="solid"),
    shadow=Shadow(y_px=3, blur_px=10, opacity=0.12),
    background_color="#e7f3ee", background_opacity=0.9,
    text_color="#173427",
    content_horizontal="center", content_vertical="center",
    content_gap_px=8, overflow="auto",
)

block = Block(
    key="resumen",
    width=Size(100, "%"), height=Size(220),
    position=Position(1, 1), span=Span(2, 2),
    style=appearance,
    horizontal="end",
    hover_style=replace(appearance, border=Border(2, "#177b54")),
    selected_style=replace(appearance, background_color="#c6eadb"),
)
block = replace(block, state="selected")
```

### Layout

| Propiedad | Uso |
| --- | --- |
| `rows`, `columns` | Dimensiones de la cuadricula; el eje de avance crece en los modos de flujo |
| `flow` | `grid` para cuadricula fija, `row` para flujo por filas o `column` para flujo por columnas |
| `column_weights` | Pesos relativos por columna, o `None` para iguales |
| `min_row_height_px` | Altura minima de todas las filas; el contenido puede ampliarlas |
| `gap_px` | Separacion comun, compatible con la configuracion anterior |
| `row_gap_px`, `column_gap_px` | Separaciones por eje; `None` hereda `gap_px`, `0` elimina el espacio |
| `padding` | Espacio interior del conjunto: numero uniforme o `Insets` por lado |
| `horizontal`, `vertical` | Alineacion general del bloque dentro de su area |
| `mobile_breakpoint_px` | `None` desactiva la adaptacion; un ancho activa el apilado |

En movil adaptado se ignoran las posiciones y extensiones de escritorio solo
visualmente. Se conserva el orden declarado de los hijos y se recalcula el ancho
porcentual respecto a la nueva area. El alto y estilo del bloque no cambian.

### Bloque

| Propiedad | Uso |
| --- | --- |
| `width`, `height` | Reglas `Size`; `height=None` permite alto automatico |
| `aspect_ratio` | Proporcion ancho/alto, por ejemplo `16 / 9`; exige `height=None` |
| `position`, `span` | Posicion inicial y extension en celdas |
| `horizontal`, `vertical` | `None` hereda el layout; `start`, `center`, `end` lo sobrescriben |
| `visible` | Si es falso no se renderiza ni reserva celdas |
| `hide_on_mobile` | Oculta el bloque al activarse el breakpoint del layout |
| `style` | Apariencia normal mediante `BlockStyle` |
| `state` | Estado visual: `normal`, `selected`, `disabled` |
| `hover_style`, `selected_style`, `disabled_style` | Estilos opcionales completos; `None` conserva el estilo normal |

Los estilos por estado sustituyen al normal, no se mezclan campo a campo: usa
`replace(style, ...)` para conservar lo que no quieras cambiar. Hover tiene prioridad
visual sobre seleccionado; no se aplica a deshabilitado. Seleccionado tiene un
contorno interior y deshabilitado reduce la opacidad. Son estados de apariencia,
no bloquean teclado, eventos ni widgets hijos: cada widget debe recibir su propio
`disabled=True` cuando corresponda. El panel permite elegir el estado; los estilos
alternativos se definen en Python.

### Apariencia

| Propiedad de `BlockStyle` | Uso |
| --- | --- |
| `padding` | Numero uniforme o `Insets(top, right, bottom, left)` |
| `radius` | Numero uniforme o `Corners(top_left, top_right, bottom_right, bottom_left)` |
| `border` | `Border(width_px, color, style)`; el grosor admite `Insets` por lado; `0` lo oculta |
| `shadow` | `Shadow(x_px, y_px, blur_px, spread_px, color, opacity, inset)` o `None` |
| `background_color` | Color hexadecimal; `None` conserva `Block.color` |
| `background_opacity` | Opacidad del fondo, sin afectar al texto |
| `background_image`, `background_fit` | URL HTTP(S), ajuste `cover` o `contain`; la imagen se carga en el navegador |
| `text_color` | Color manual o `None` para contraste automatico basado en el fondo solido |
| `content_horizontal`, `content_vertical` | Alineacion del contenido, independiente de la posicion del bloque |
| `content_gap_px` | Separacion entre elementos Streamlit dentro del bloque |
| `overflow` | `auto`, `scroll`, `hidden` o `visible` |

Los valores originales se conservan: padding 20, radio 6, sin borde ni sombra.
El contraste automatico no analiza imagenes ni el fondo que se ve a traves de una
transparencia; usa `text_color` en esos casos. Los widgets nativos conservan su
propia apariencia y ancho; la alineacion no cambia sus parametros internos.
Padding y borde cuentan dentro del tamano (`border-box`); no puede haber un alto
visual menor que la suma de esos espacios. `overflow="visible"` permite que el
contenido salga del bloque deliberadamente. Usa `auto` para contenerlo.

La separacion exterior se configura en `Layout`, no con margenes individuales,
para evitar reglas duplicadas. Los controles de apariencia y comportamiento
conservan las propiedades que no editan; la exportacion JSON incluye todas las
configuraciones, pero su importacion sigue fuera del alcance de este laboratorio.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s . -p "test_*.py"
```

La carpeta puede moverse a otro repositorio. La integracion con la aplicacion
principal queda para cuando las pruebas de interfaz sean satisfactorias.
