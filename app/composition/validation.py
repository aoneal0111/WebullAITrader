from app.composition.exceptions import CircularDependencyError, MissingDependencyError


def validate_factory_graph(factories):
    """Validate references and cycles in deterministic registration order."""
    names = tuple(factories)
    for name in names:
        for dependency in factories[name].dependencies:
            if dependency not in factories:
                raise MissingDependencyError(f"{name} requires missing component: {dependency}")

    states = {}
    path = []

    def visit(name):
        state = states.get(name, 0)
        if state == 1:
            start = path.index(name)
            cycle = path[start:] + [name]
            raise CircularDependencyError("circular dependency: " + " -> ".join(cycle))
        if state == 2:
            return
        states[name] = 1
        path.append(name)
        for dependency in factories[name].dependencies:
            visit(dependency)
        path.pop()
        states[name] = 2

    for name in names:
        visit(name)
    return True


def implements_methods(*method_names):
    if not method_names or any(not isinstance(name, str) or not name for name in method_names):
        raise ValueError("method names must be non-empty strings")
    return lambda component: all(callable(getattr(component, name, None)) for name in method_names)
