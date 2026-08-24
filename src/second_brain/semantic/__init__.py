"""
second_brain.semantic -- the vault's semantic layer.

Public surface, deliberately small:

    from second_brain.semantic import load
    m = load()
    m.resolve("EV tolls")     -> Concept(id='advanced_air_mobility', ...)
    m.expand("aam")           -> every equivalent surface form
    m.fts_query("aam")        -> an FTS5 MATCH expression covering all of them
    m.closure("entity_swim")  -> ['entity_swim', 'aviation']

    from second_brain.semantic import semantic_search
    semantic_search("gig economy")   # concept-expanded search over the vault

Everything else (compiling into SQLite, exports, drift) is reachable through
the CLI: `python3 -m second_brain.semantic --help`.

Imports nothing that requires credentials -- see model.py's module docstring
for why that constraint is load-bearing rather than incidental.
"""
from second_brain.semantic.model import (  # noqa: F401
    Agent,
    Concept,
    Facet,
    Metric,
    SemanticModel,
    SemanticModelError,
    load,
    normalize,
)

__all__ = [
    "Agent", "Concept", "Facet", "Metric", "SemanticModel",
    "SemanticModelError", "load", "normalize",
    "compile_layer", "drift_report", "evaluate_metrics", "semantic_search",
]


def __getattr__(name: str):
    """Lazy re-export of the compile-side helpers.

    Kept lazy so that merely importing the model never opens the index DB --
    `from second_brain.semantic import load` must stay usable on a machine that
    has the repo but not the vault index (a dev checkout, a container that
    doesn't mount the data volume, a fresh clone).
    """
    if name in ("compile_layer", "drift_report", "evaluate_metrics",
                "semantic_search"):
        from second_brain.semantic import compile as _compile
        return getattr(_compile, name)
    raise AttributeError(name)
