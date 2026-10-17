"""Keep exported feature contracts independent of unrelated API schemas."""


def prune_schema_components(schema: dict) -> None:
    """Mutate an export copy, retaining the schemas referenced by its paths."""
    components = schema.get("components", {}).get("schemas", {})
    required: set[str] = set()

    def discover(value):
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[-1]
                if name not in required:
                    required.add(name)
                    discover(components[name])
            for item in value.values():
                discover(item)
        elif isinstance(value, list):
            for item in value:
                discover(item)

    discover(schema["paths"])
    schema["components"]["schemas"] = {
        name: value for name, value in components.items() if name in required
    }
