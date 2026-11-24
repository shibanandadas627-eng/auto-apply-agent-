"""Remember things on the visitor's own device (browser localStorage), not on the server."""
from pathlib import Path

import streamlit.components.v1 as components

_comp = components.declare_component("local_store", path=str(Path(__file__).parent / "frontend"))


def local_store(op, id, keys=None, items=None):
    """op: 'get' (returns {'ok', 'items'}), 'set' or 'clear' (return {'ok', 'id'}).
    Returns None until the browser has answered."""
    return _comp(op=op, id=id, keys=keys or [], items=items or {}, key=f"ls_{op}", default=None)
