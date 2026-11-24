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
