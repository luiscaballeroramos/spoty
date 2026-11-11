# Distribucion de la informacion

```mermaid
flowchart LR
    S["Spotify<br/>Ultimas 50 escuchas"]

    subgraph APP["spoty · captura y envio"]
        A["Action<br/>Cron externo / manual"]
        C["spotifyapi/<br/>Consulta Spotify"]
        E["register/listeningevent.py<br/>Valida eventos"]
        P["publish_listening_events.py<br/>Envia eventos"]
        A --> C --> E --> P
    end

    subgraph DATA["spoty-data · guardado"]
        D["Action receptora"]
        R["record_listening_events.py<br/>Valida y deduplica"]
        CSV[("listening_events.csv<br/>Commit + push")]
        D --> R --> CSV
    end

    S --> C
    P -->|"repository_dispatch"| D

    style APP fill:#e8f0fe,stroke:#4285f4
    style DATA fill:#e6f4ea,stroke:#34a853
```

- **Credenciales:** `.env` local o Actions secrets en `spoty`; nunca en el CSV.
- **CSV:** version, ID de cancion, fecha Unix UTC y contexto; sin titulos ni artistas.
- **Sin CSV intermedio:** solo `spoty-data` escribe datos.
- **`old/` en ambos repos:** legado, fuera del flujo nuevo.
