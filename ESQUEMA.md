# Distribucion de la informacion

```mermaid
flowchart LR
    S["Spotify<br/>Ultimas 50 escuchas"]

    subgraph APP["spoty · captura y envio"]
        A["Action<br/>Cron externo / manual"]
        C["spotifyapi/<br/>Consulta Spotify"]
        E["register/<br/>Eventos y catalogo Spotify"]
        P["publish_data.py<br/>Envio comun"]
        A --> C --> E --> P
    end

    subgraph DATA["spoty-data · guardado"]
        D["Action receptora"]
        R["record_data.py<br/>Valida y combina"]
        CSV[("Escuchas + canciones<br/>Artistas + albumes<br/>Commit + push")]
        D --> R --> CSV
    end

    S --> C
    P -->|"repository_dispatch"| D
    O["Futuras fuentes<br/>Cuando hagan falta"] -.-> P

    style APP fill:#e8f0fe,stroke:#4285f4
    style DATA fill:#e6f4ea,stroke:#34a853
```

- **Credenciales:** `.env` local o Actions secrets en `spoty`; nunca en el CSV.
- **CSV:** `listening_events.csv`, `tracks.csv`, `artists.csv` y `albums.csv`.
- **Sin CSV intermedio:** solo `spoty-data` escribe datos.
- **`old/` en ambos repos:** legado, fuera del flujo nuevo.
