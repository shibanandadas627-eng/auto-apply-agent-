"""Remember things on the visitor's own device (browser localStorage), not on the server.
The small browser-side helper is built into this file, so no extra folder needs to be uploaded."""
import tempfile
from pathlib import Path

import streamlit.components.v1 as components

_HTML = r"""<!DOCTYPE html>
<html>
<body>
<script>
  // Tiny Streamlit component that reads/writes this browser's localStorage.
  // Data never leaves the visitor's device through this component.
  function send(type, data) {
    window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*");
  }
  function handle(args) {
    var out = {id: args.id, op: args.op, ok: true};
    try {
      if (args.op === "get") {
        out.items = {};
        try { out.tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { out.tz = "UTC"; }
        (args.keys || []).forEach(function (k) { out.items[k] = window.localStorage.getItem(k); });
      } else if (args.op === "set") {
        Object.keys(args.items || {}).forEach(function (k) { window.localStorage.setItem(k, args.items[k]); });
      } else if (args.op === "clear") {
        (args.keys || []).forEach(function (k) { window.localStorage.removeItem(k); });
      }
    } catch (e) {
      out.ok = false;
      out.error = String(e);
    }
    send("streamlit:setComponentValue", {value: out, dataType: "json"});
  }
  var last = null;
  window.addEventListener("message", function (event) {
    if (!event.data || event.data.type !== "streamlit:render") return;
    var args = event.data.args || {};
    var sig = JSON.stringify([args.op, args.id]);
    if (sig === last) return;
    last = sig;
    handle(args);
  });
  send("streamlit:componentReady", {apiVersion: 1});
  send("streamlit:setFrameHeight", {height: 0});
</script>
</body>
</html>
"""


def _make():
    folder = Path(tempfile.mkdtemp(prefix="local_store_"))
    (folder / "index.html").write_text(_HTML, encoding="utf-8")
    return components.declare_component("local_store", path=str(folder))


try:
    _comp = _make()
except Exception:      # if this ever fails the app still works, it just can't remember things
    _comp = None


def local_store(op, id, keys=None, items=None):
    """op: 'get' (returns {'ok', 'items', 'tz'}), 'set' or 'clear' (return {'ok', 'id'}).
    Returns None until the browser has answered."""
    if _comp is None:
        return None
    return _comp(op=op, id=id, keys=keys or [], items=items or {}, key=f"ls_{op}", default=None)
