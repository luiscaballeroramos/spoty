"""Local genre taxonomy, independent of provider metadata."""

import json
import re
from importlib.resources import files


def normalize_label(value: str) -> str:
    return value.strip().lower()


def validate_graph(genre_ids, relations):
    parents = {genre_id: set() for genre_id in genre_ids}
    for parent_id, child_id in relations:
        if parent_id not in parents or child_id not in parents:
            raise ValueError("Unknown genre in relation.")
        parents[child_id].add(parent_id)
    visiting, visited = set(), set()

    def visit(genre_id):
        if genre_id in visiting:
            raise ValueError("Genre relations contain a cycle.")
        if genre_id in visited:
            return
        visiting.add(genre_id)
        for parent_id in parents[genre_id]:
            visit(parent_id)
        visiting.remove(genre_id)
        visited.add(genre_id)

    for genre_id in parents:
        visit(genre_id)


def validate_seed(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported taxonomy seed version.")
    genres = data.get("genres")
    if not isinstance(genres, list) or not genres:
        raise ValueError("Expected a nonempty genre list.")
    genre_ids, labels, relations = set(), set(), []
    for genre in genres:
        if not isinstance(genre, dict):
            raise ValueError("Invalid genre.")
        genre_id, name = genre.get("id"), genre.get("name")
        if not isinstance(genre_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", genre_id):
            raise ValueError("Invalid genre identifier.")
        if genre_id in genre_ids or not isinstance(name, str) or not name.strip():
            raise ValueError("Duplicate genre identifier or empty name.")
        genre_ids.add(genre_id)
        for field in ("parents", "labels"):
            values = genre.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"Invalid genre {field}.")
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate genre {field}.")
        relations.extend((parent, genre_id) for parent in genre["parents"])
        for label in genre["labels"]:
            normalized = normalize_label(label)
            if normalized in labels:
                raise ValueError("Duplicate normalized label.")
            labels.add(normalized)
    validate_graph(genre_ids, relations)
    return data


def load_seed(path=None) -> dict:
    if path is None:
        content = files("services").joinpath("genre_taxonomy_seed.json").read_text(encoding="utf-8")
    else:
        from pathlib import Path

        content = Path(path).read_text(encoding="utf-8")
    return validate_seed(json.loads(content))


def render_taxonomy_markdown(seed: dict) -> str:
    """Render every seed relationship and label in navigable Mermaid diagrams."""
    from html import escape

    validate_seed(seed)
    genres = {genre["id"]: genre for genre in seed["genres"]}
    roots = [genre for genre in seed["genres"] if not genre["parents"]]
    children = {genre_id: set() for genre_id in genres}
    for genre in genres.values():
        for parent_id in genre["parents"]:
            children[parent_id].add(genre["id"])

    def node_id(genre_id):
        return "g_" + genre_id.replace("-", "_")

    def node_line(genre_id):
        return f'    {node_id(genre_id)}["{escape(genres[genre_id]["name"], quote=True)}"]'

    label_count = sum(len(genre["labels"]) for genre in genres.values())
    lines = [
        "# Taxonomia De Generos", "",
        "[Abrir visor interactivo con busqueda y ramas plegables](GENRE_TAXONOMY.html)", "",
        "Documento generado desde [services/genre_taxonomy_seed.json](services/genre_taxonomy_seed.json).",
        "No editar este esquema a mano; regenerarlo despues de modificar la semilla.", "",
        f"{len(roots)} familias operativas, {len(genres)} nodos canonicos y {label_count} etiquetas de origen.", "",
        "## Criterios", "",
        "- Clasificacion editorial local, no una jerarquia oficial de Spotify.",
        "- Las raices incluyen familias musicales, regionales y contextuales; no son ejes excluyentes.",
        "- Las etiquetas listadas bajo un nodo son equivalencias de agrupacion, no necesariamente sinonimos.",
        "- Cada flecha continua une un padre con un hijo; varias entradas representan fusiones o varias pertenencias.",
        "- Las flechas discontinuas muestran padres de otras ramas, dibujados en gris.",
        "- Indie sin estilo, lo-fi generico, epoca festiva y otras etiquetas transversales no se fuerzan a rock o pop.",
        "- Los nodos regionales agrupan variantes conservando sus etiquetas originales; no se infieren propiedades del audio.",
        "- Una escucha puede pertenecer a varias familias; los porcentajes no tienen por que sumar 100%.", "",
        "## Familias", "", "```mermaid", "flowchart TB", '    taxonomy["Taxonomia local"]',
    ]
    for root in roots:
        lines.extend([node_line(root["id"]), f'    taxonomy --> {node_id(root["id"])}'])
    lines.extend(["```", "", "## Indice", ""])
    lines.extend(f'- [{root["name"]}](#familia-{root["id"]})' for root in roots)
    for root in roots:
        reachable, pending = set(), [root["id"]]
        while pending:
            genre_id = pending.pop()
            if genre_id not in reachable:
                reachable.add(genre_id)
                pending.extend(children[genre_id])
        related = {parent_id for genre_id in reachable for parent_id in genres[genre_id]["parents"]} - reachable
        lines.extend(["", f'<a id="familia-{root["id"]}"></a>', f'## {root["name"]}', "", "```mermaid", "flowchart TB"])
        for genre_id in sorted(reachable | related):
            lines.append(node_line(genre_id))
        for genre_id in sorted(reachable):
            for parent_id in genres[genre_id]["parents"]:
                arrow = "-->" if parent_id in reachable else "-.->"
                lines.append(f"    {node_id(parent_id)} {arrow} {node_id(genre_id)}")
        lines.extend([
            "    classDef family fill:#111111,color:#ffffff,stroke:#111111",
            "    classDef related fill:#f3f3f3,color:#444444,stroke:#999999",
            f'    class {node_id(root["id"])} family',
        ])
        for genre_id in sorted(related):
            lines.append(f"    class {node_id(genre_id)} related")
        lines.extend(["```", "", "### Etiquetas Incluidas", ""])
        for genre_id in sorted(reachable):
            genre = genres[genre_id]
            if genre["labels"]:
                labels = ", ".join(f"`{label}`" for label in genre["labels"])
                lines.append(f'- **{genre["name"]}**: {labels}')
    return "\n".join(lines) + "\n"


def render_taxonomy_html(seed: dict) -> str:
    """Build a standalone, offline tree explorer from the validated seed."""
    validate_seed(seed)
    template = files("services").joinpath("genre_taxonomy_viewer.html").read_text(encoding="utf-8")
    payload = json.dumps(seed, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return template.replace("__TAXONOMY_DATA__", payload)


def main():
    import argparse

    from config import require_database_url
    from register.genre_taxonomy import GenreTaxonomyRepository

    parser = argparse.ArgumentParser(description="Manage the local genre taxonomy without provider calls.")
    parser.add_argument("operation", choices=("import", "discover", "diagram", "viewer"))
    parser.add_argument("--seed", help="Optional JSON seed; defaults to the packaged initial taxonomy.")
    parser.add_argument("--output", help="Output path for diagram or viewer; defaults to standard output.")
    parser.add_argument("--apply", action="store_true", help="Explicitly allow database writes.")
    args = parser.parse_args()
    if args.operation == "discover" and args.seed:
        parser.error("--seed is only valid for import, diagram or viewer")
    if args.output and args.operation not in {"diagram", "viewer"}:
        parser.error("--output is only valid for diagram or viewer")
    if args.operation in {"diagram", "viewer"}:
        if args.apply:
            parser.error("diagram and viewer never write to the database; omit --apply")
        seed = load_seed(args.seed)
        content = render_taxonomy_html(seed) if args.operation == "viewer" else render_taxonomy_markdown(seed)
        if args.output:
            from pathlib import Path

            Path(args.output).write_text(content, encoding="utf-8")
        else:
            print(content, end="")
        return
    repository = GenreTaxonomyRepository()
    if args.operation == "import":
        seed = load_seed(args.seed)
        result = repository.import_seed(require_database_url(), seed) if args.apply else {
            "status": "validated", "genres": len(seed["genres"]), "applied": False,
        }
    else:
        result = repository.discover_labels(require_database_url(), apply=args.apply)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
