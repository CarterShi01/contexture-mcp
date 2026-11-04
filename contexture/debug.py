"""Loopback-only developer UI for one immutable Node Catalog.

This adapter deliberately serves the structural catalog, not the MCP gateway.
It has no invocation route, opens no Channels, and accepts no mutation method.
The default and only supported bind class is a loopback address so definition
text does not accidentally become a network service.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator
from urllib.parse import unquote, urlsplit

from .core.errors import ModelValidationError
from .introspection import NodeCatalog


_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Contexture Node Catalog</title>
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <header>
    <div><span class="eyebrow">CONTEXTURE</span><h1>Node Catalog</h1></div>
    <div id="status" role="status">Loading compiled graph…</div>
  </header>
  <main>
    <aside>
      <label for="search">Find a node</label>
      <input id="search" type="search" placeholder="ref, name, or description">
      <nav id="tree" aria-label="Compiled node tree"></nav>
    </aside>
    <article id="detail">
      <p class="empty">Choose a Role, Skill, or Tool to inspect its compiled metadata.</p>
    </article>
  </main>
  <script src="/app.js" defer></script>
</body>
</html>
""".encode("utf-8")

_CSS = """:root{color-scheme:dark;--bg:#0d1117;--panel:#151b23;--line:#2a3441;--text:#e6edf3;--muted:#9da9b5;--accent:#7ee787;--role:#79c0ff;--skill:#d2a8ff;--tool:#ffa657;font:14px/1.5 ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}header{height:78px;display:flex;align-items:center;justify-content:space-between;padding:14px 24px;border-bottom:1px solid var(--line);background:#10161e}h1{display:inline;margin:0 0 0 12px;font-size:21px}.eyebrow{color:var(--accent);font:700 11px/1 ui-monospace,monospace;letter-spacing:.18em}#status{color:var(--muted);font:12px ui-monospace,monospace}main{display:grid;grid-template-columns:minmax(280px,34%) 1fr;height:calc(100vh - 78px)}aside{padding:20px;border-right:1px solid var(--line);overflow:auto;background:var(--panel)}label{display:block;margin-bottom:7px;color:var(--muted);font-size:12px}input{width:100%;padding:10px 12px;margin-bottom:18px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text)}button.node{display:grid;grid-template-columns:50px 1fr;width:100%;padding:8px;border:0;border-left:2px solid transparent;background:transparent;color:var(--text);text-align:left;cursor:pointer}button.node:hover,button.node.active{background:#1c2530;border-left-color:var(--accent)}.kind{font:700 10px ui-monospace,monospace;text-transform:uppercase}.kind.role{color:var(--role)}.kind.skill{color:var(--skill)}.kind.tool{color:var(--tool)}.ref{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:12px ui-monospace,monospace}.depth-1{padding-left:20px!important}.depth-2{padding-left:34px!important}.depth-3{padding-left:48px!important}.depth-4{padding-left:62px!important}article{padding:30px 36px;overflow:auto}article h2{margin:0 0 6px;font-size:26px}article .description{max-width:78ch;color:var(--muted)}.badges{display:flex;gap:8px;margin:14px 0 24px}.badge{padding:3px 8px;border:1px solid var(--line);border-radius:999px;font:11px ui-monospace,monospace}section{margin:25px 0}section h3{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}pre{max-width:100%;padding:16px;border:1px solid var(--line);border-radius:7px;overflow:auto;background:#090c10;color:#c9d1d9;white-space:pre-wrap;word-break:break-word}.empty{color:var(--muted)}dl{display:grid;grid-template-columns:120px 1fr;gap:6px 12px}dt{color:var(--muted)}dd{margin:0;font-family:ui-monospace,monospace}@media(max-width:760px){header{height:auto;align-items:flex-start;gap:10px}main{display:block;height:auto}aside{border-right:0;border-bottom:1px solid var(--line);max-height:45vh}article{padding:24px}}
""".encode("utf-8")

_JS = r"""'use strict';
const tree=document.querySelector('#tree'),detail=document.querySelector('#detail'),search=document.querySelector('#search'),status=document.querySelector('#status');
let catalog=null,selected=null;
const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node};
function depth(ref){return Math.min(4,(ref.match(/\//g)||[]).length)}
function definition(node){return node.role||node.skill||node.tool||null}
function renderTree(){const q=search.value.trim().toLocaleLowerCase();tree.replaceChildren();for(const node of catalog.nodes){const hay=JSON.stringify(node).toLocaleLowerCase();if(q&&!hay.includes(q))continue;const button=el('button',undefined,`node depth-${depth(node.ref)}${selected===node.ref?' active':''}`);button.type='button';button.append(el('span',node.kind,`kind ${node.kind}`),el('span',node.ref,'ref'));button.addEventListener('click',()=>{selected=node.ref;renderTree();renderDetail(node)});tree.append(button)}}
function row(dl,key,value){dl.append(el('dt',key),el('dd',value===null?'—':String(value)))}
function codeSection(title,value){const section=el('section');section.append(el('h3',title));section.append(el('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));return section}
function renderDetail(node){detail.replaceChildren();detail.append(el('h2',node.presentation?.title||node.name));detail.append(el('p',node.description,'description'));const badges=el('div',undefined,'badges');for(const value of [node.kind,node.plane,node.ref])badges.append(el('span',value,'badge'));detail.append(badges);const facts=el('dl');row(facts,'Parent',node.parent_ref);row(facts,'Ordinal',node.ordinal);row(facts,'Members',node.members.length);row(facts,'Uses',node.uses.length);row(facts,'Dependents',node.dependents.length);detail.append(facts);const def=definition(node);if(def){if(def.authored_instructions!==undefined)detail.append(codeSection('Authored instructions',def.authored_instructions));if(def.effective_instructions!==undefined)detail.append(codeSection('Effective instructions',def.effective_instructions));if(def.input_schema!==undefined)detail.append(codeSection('Input schema',def.input_schema));detail.append(codeSection('Compiled definition',def))}if(node.members.length)detail.append(codeSection('Members',node.members));if(node.uses.length)detail.append(codeSection('Uses',node.uses));if(node.dependents.length)detail.append(codeSection('Dependents',node.dependents));if(Object.keys(node.extensions).length)detail.append(codeSection('Extensions',node.extensions))}
search.addEventListener('input',renderTree);
fetch('/api/catalog',{headers:{Accept:'application/json'}}).then(response=>{if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json()}).then(value=>{catalog=value;status.textContent=`${value.application.name} · ${value.nodes.length} nodes · ${value.content_digest.slice(0,19)}…`;renderTree();if(value.nodes.length){selected=value.nodes[0].ref;renderTree();renderDetail(value.nodes[0])}}).catch(error=>{status.textContent='Catalog unavailable';detail.replaceChildren(el('pre',String(error)))})
""".encode("utf-8")


@dataclass(frozen=True, slots=True)
class DebugAddress:
    """The actual loopback endpoint selected for a running debug server."""

    host: str
    port: int

    @property
    def url(self) -> str:
        rendered = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{rendered}:{self.port}"


class CatalogDebugServer:
    """Serve one immutable catalog through a read-only local HTTP interface."""

    def __init__(self, catalog: NodeCatalog) -> None:
        self.catalog = catalog
        self._payload = catalog.snapshot().to_json(indent=None).encode("utf-8")
        self._etag = f'"{catalog.snapshot().content_digest}"'
        self._nodes = {
            node.ref: json.dumps(
                node.as_json(), ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            for node in catalog.snapshot().nodes
        }

    def run(self, *, host: str = "127.0.0.1", port: int = 8787) -> None:
        """Block while serving the UI; stop normally with Ctrl-C."""

        server = self._http_server(host, port)
        address = _address(server)
        print(f"Contexture Node Catalog: {address.url}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()

    @contextmanager
    def running(
        self, *, host: str = "127.0.0.1", port: int = 0
    ) -> Iterator[DebugAddress]:
        """Run in a background thread for tests or an embedding developer app."""

        server = self._http_server(host, port)
        thread = threading.Thread(
            target=server.serve_forever,
            name="contexture-catalog-debug",
            daemon=True,
        )
        thread.start()
        try:
            yield _address(server)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def _http_server(self, host: str, port: int) -> ThreadingHTTPServer:
        _require_loopback(host)
        if not 0 <= port <= 65535:
            raise ModelValidationError("A debug HTTP port must be between 0 and 65535.")
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ContextureCatalog/1"

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                if not self._trusted_request():
                    return
                path = urlsplit(self.path).path
                if path == "/":
                    self._send(HTTPStatus.OK, _HTML, "text/html; charset=utf-8")
                elif path == "/app.css":
                    self._send(HTTPStatus.OK, _CSS, "text/css; charset=utf-8")
                elif path == "/app.js":
                    self._send(
                        HTTPStatus.OK, _JS, "text/javascript; charset=utf-8"
                    )
                elif path == "/healthz":
                    self._send(
                        HTTPStatus.OK,
                        b'{"status":"ok"}',
                        "application/json; charset=utf-8",
                    )
                elif path == "/api/catalog":
                    self._json(adapter._payload, adapter._etag)
                elif path.startswith("/api/nodes/"):
                    ref = unquote(path.removeprefix("/api/nodes/"))
                    payload = adapter._nodes.get(ref)
                    if payload is None:
                        self._error(HTTPStatus.NOT_FOUND, "Unknown catalog ref.")
                    else:
                        self._json(payload, adapter._etag)
                else:
                    self._error(HTTPStatus.NOT_FOUND, "Not found.")

            def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler contract
                if not self._trusted_request():
                    return
                if urlsplit(self.path).path == "/api/catalog":
                    self._json(adapter._payload, adapter._etag, body=False)
                else:
                    self._error(HTTPStatus.NOT_FOUND, "Not found.", body=False)

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
                if self._trusted_request():
                    self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Read-only endpoint.")

            do_PUT = do_POST
            do_PATCH = do_POST
            do_DELETE = do_POST

            def _trusted_request(self) -> bool:
                host = self.headers.get("Host", "")
                origin = self.headers.get("Origin")
                if not _trusted_authority(host):
                    self._error(HTTPStatus.BAD_REQUEST, "Untrusted Host header.")
                    return False
                if origin is not None and not _trusted_origin(origin, host):
                    self._error(HTTPStatus.FORBIDDEN, "Cross-origin request refused.")
                    return False
                return True

            def _json(self, payload: bytes, etag: str, *, body: bool = True) -> None:
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(HTTPStatus.NOT_MODIFIED)
                    self.send_header("ETag", etag)
                    self._security_headers()
                    self.end_headers()
                    return
                self._send(
                    HTTPStatus.OK,
                    payload,
                    "application/json; charset=utf-8",
                    etag=etag,
                    body=body,
                )

            def _error(
                self, status: HTTPStatus, message: str, *, body: bool = True
            ) -> None:
                payload = json.dumps({"error": message}).encode("utf-8")
                self._send(
                    status,
                    payload,
                    "application/json; charset=utf-8",
                    body=body,
                )

            def _send(
                self,
                status: HTTPStatus,
                payload: bytes,
                content_type: str,
                *,
                etag: str | None = None,
                body: bool = True,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-cache")
                if etag is not None:
                    self.send_header("ETag", etag)
                self._security_headers()
                self.end_headers()
                if body:
                    self.wfile.write(payload)

            def _security_headers(self) -> None:
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src data:; base-uri 'none'; "
                    "frame-ancestors 'none'",
                )

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        try:
            server_type = ThreadingHTTPServer
            try:
                if ipaddress.ip_address(host).version == 6:
                    class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
                        address_family = socket.AF_INET6

                    server_type = IPv6ThreadingHTTPServer
            except ValueError:
                pass  # ``localhost`` deliberately uses the platform resolver.
            return server_type((host, port), Handler)
        except OSError as error:
            raise ModelValidationError(
                f"Cannot bind Contexture debug HTTP server to {host}:{port}: {error}"
            ) from error


def _require_loopback(host: str) -> None:
    if host.casefold() == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ModelValidationError(
            "Contexture debug HTTP accepts only an explicit loopback host "
            "(127.0.0.1, ::1, or localhost)."
        ) from error
    if not address.is_loopback:
        raise ModelValidationError(
            "Contexture debug HTTP is loopback-only; use an authenticated "
            "application adapter to publish catalog data remotely."
        )


def _trusted_authority(authority: str) -> bool:
    host = authority.rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host[1 : host.find("]")] if "]" in host else ""
    else:
        host = host.split(":", 1)[0]
    try:
        _require_loopback(host)
    except ModelValidationError:
        return False
    return True


def _trusted_origin(origin: str, authority: str) -> bool:
    parsed = urlsplit(origin)
    return (
        parsed.scheme == "http"
        and parsed.netloc.casefold() == authority.casefold()
        and _trusted_authority(parsed.netloc)
    )


def _address(server: ThreadingHTTPServer) -> DebugAddress:
    host, port = server.server_address[:2]
    return DebugAddress(str(host), int(port))


__all__ = ["CatalogDebugServer", "DebugAddress"]
