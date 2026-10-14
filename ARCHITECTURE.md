# Arquitectura propuesta

Este diagrama describe una arquitectura objetivo, no el estado completo actual
del sistema. El motor de recomendaciones y el workflow de tests y calidad están
pendientes de implementación. La sincronización puede iniciarse desde Streamlit
o ejecutarse desde un job de GitHub Actions.

```mermaid
flowchart TD
    SP[Spotify API] --> SY[Servicio de sincronizacion]
    SY --> DB[(PostgreSQL / Neon)]

    UI[Aplicacion Streamlit] --> DB
    UI -. accion de sincronizacion .-> SY

    DB --> EN[Servicio de enriquecimiento]
    EN -->|consulta| LF[Last.fm]
    LF -->|respuesta| EN
    EN -->|consulta| MB[MusicBrainz]
    MB -->|respuesta| EN
    EN -->|consulta, si disponible| AB[AcousticBrainz]
    AB -->|respuesta| EN
    EN --> DB

    DB --> REC[Motor de recomendaciones, futuro]
    REC --> UI

    GH[GitHub Actions] -. dispara job .-> RUN[Runner]
    RUN -. ejecuta .-> SY
    GH -. workflow de CI, por configurar .-> Q[Tests y calidad]
```