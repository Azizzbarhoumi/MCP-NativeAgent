"""The human's approval page for git-local: a tiny local web page, served by the MCP server
process itself (standard library only, no new dependency).

  http://127.0.0.1:<port>/<secret>/      lists the steps the assistant prepared, with the command,
                                         its risk tier, what a destructive step would lose, and the
                                         approval code. Approve / Reject buttons.

Why it is safe:
- The page lives in the server process, so approving needs no file or chat hand-off: Approve
  marks the step approved in memory, then the model's `execute_step(step_id)` runs it.
- The model never sees the page address or the codes: the address goes to stderr and
  logs/approvals.log (the human's side), like the codes. No MCP tool returns either.
- Bound to 127.0.0.1 only. Every request needs the random secret in the path, a Host header
  for this exact address (stops DNS-rebinding pages), and POSTs need a same-origin Origin or
  none (stops other websites from submitting the form).
- Destructive steps need the confirmation phrase typed on the page.

Start:  OFF by default (design under review, see docs/approval_page.md). GIT_APPROVAL_UI=1 makes
        server.py start it; GIT_APPROVAL_UI_PORT picks a port. Off, approval works as before: the
        code goes to stderr and logs/approvals.log, and the user types it to the assistant.
"""

import html
import secrets
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from mcp_servers.git_local import execute as ex

TIER_COLOR = {"safe": "#1a7f37", "careful": "#9a6700", "destructive": "#cf222e"}
_server: ThreadingHTTPServer | None = None
_url: str | None = None


def _page(secret: str, flash: str = "") -> str:
    steps = ex.pending_for_human()
    rows = []
    for s in steps:
        e = {k: html.escape(str(v)) for k, v in s.items() if k != "would_lose"}
        lose = "".join(f"<li>{html.escape(str(x))}</li>" for x in s["would_lose"])
        phrase = ""
        if s["tier"] == "destructive" and not s["approved"]:
            phrase = (f'<label>Type <code>{e["phrase"]}</code> to approve: '
                      f'<input name="confirmation" autocomplete="off" required size="32"></label>')
        state = ('<b class="ok">Approved: the assistant can run it now.</b>' if s["approved"] else
                 f'''<form method="post" action="/{secret}/approve">
                       <input type="hidden" name="step_id" value="{e['step_id']}">{phrase}
                       <button class="approve">Approve</button></form>
                     <form method="post" action="/{secret}/reject">
                       <input type="hidden" name="step_id" value="{e['step_id']}">
                       <button class="reject">Reject</button></form>''')
        rows.append(f'''<section class="step">
          <div class="cmd"><code>{e['command']}</code>
            <span class="tier" style="background:{TIER_COLOR.get(s['tier'], '#57606a')}">{e['tier']}</span></div>
          <p>{e['explain']}</p>
          {f'<p><b>Would lose:</b></p><ul>{lose}</ul><p>A backup is made automatically before it runs.</p>' if lose else ''}
          <p class="meta">step {e['step_id']} · repo {e['repo']} · prepared {e['created']} UTC ·
             approval code <code class="code">{e['code']}</code> (or type it to the assistant)</p>
          <div class="actions">{state}</div></section>''')
    body = "".join(rows) or "<p class='empty'>No step is waiting for your approval.</p>"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="4">
<title>Git approvals</title><style>
 :root{{--bg:#fff;--fg:#1f2328;--muted:#57606a;--line:#d0d7de;--card:#f6f8fa}}
 @media (prefers-color-scheme:dark){{:root{{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;--card:#161b22}}}}
 body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px}}
 .step{{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:14px 16px;margin:12px 0}}
 .cmd{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}} .cmd code{{font-size:16px}}
 .tier{{color:#fff;border-radius:10px;padding:1px 9px;font-size:12px;font-weight:600}}
 .meta,.empty{{color:var(--muted);font-size:13px}} .code{{font-weight:700;letter-spacing:.1em}}
 .actions{{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap}} form{{margin:0}}
 input{{font:inherit;padding:5px 8px;margin:4px 6px 4px 0}} button{{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--line);cursor:pointer}}
 .approve{{background:#1a7f37;color:#fff}} .reject{{background:transparent;color:var(--fg)}} .ok{{color:#1a7f37}}
 .flash{{padding:8px 12px;border-radius:6px;background:#fff8c5;color:#1f2328}}
</style></head><body><h1>Git steps waiting for you</h1>
<p class="meta">Only you see this page. Nothing runs until you approve; the assistant then runs exactly this step.</p>
{f'<p class="flash">{html.escape(flash)}</p>' if flash else ''}{body}</body></html>'''


class _Handler(BaseHTTPRequestHandler):
    secret = ""
    allowed_hosts: set[str] = set()
    allowed_origins: set[str] = set()

    def log_message(self, *args):          # keep stderr for the approval lines only
        pass

    def _guard(self) -> bool:
        if self.headers.get("Host", "") not in self.allowed_hosts or not self.path.startswith(f"/{self.secret}/"):
            self._send(404, "not found", "text/plain")
            return False
        return True

    def _send(self, status: int, text: str, ctype: str = "text/html; charset=utf-8", location: str | None = None):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.send_header("Referrer-Policy", "same-origin")   # no-referrer would make browsers send "Origin: null" on our own form posts
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._guard():
            return
        flash = parse_qs(self.path.split("?", 1)[1]).get("msg", [""])[0] if "?" in self.path else ""
        self._send(200, _page(self.secret, flash))

    def do_POST(self):
        if not self._guard():
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.allowed_origins:
            self._send(403, "cross-site request refused", "text/plain")
            return
        size = min(int(self.headers.get("Content-Length") or 0), 10_000)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(size).decode("utf-8", "replace")).items()}
        action = self.path[len(self.secret) + 2:].split("?")[0]
        step_id = form.get("step_id", "")
        if action == "approve":
            r = ex.approve_by_human(step_id, form.get("confirmation"))
        elif action == "reject":
            r = ex.reject_by_human(step_id)
        else:
            self._send(404, "not found", "text/plain")
            return
        msg = "Approved." if r["ok"] and action == "approve" else "Rejected." if r["ok"] else r["error"]
        from urllib.parse import quote
        self._send(303, "", location=f"/{self.secret}/?msg={quote(msg)}")


def start(port: int = 0) -> str:
    """Start the page in a daemon thread (once) and return its URL. The URL holds the secret:
    print it only where the human reads it, never in a tool result."""
    global _server, _url
    if _server is not None:
        return _url
    secret = secrets.token_urlsafe(24)
    handler = type("Handler", (_Handler,), {"secret": secret})
    _server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    real_port = _server.server_address[1]
    handler.allowed_hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}"}
    handler.allowed_origins = {f"http://{h}" for h in handler.allowed_hosts}
    ex.APPROVAL_PAGE = True
    threading.Thread(target=_server.serve_forever, daemon=True, name="git-approval-ui").start()
    _url = f"http://127.0.0.1:{real_port}/{secret}/"
    line = f"[git-local] approval page: {_url}"
    print(line, file=sys.stderr, flush=True)
    ex.APPROVALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ex.APPROVALS_LOG.open("a", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat(timespec="seconds") + " " + line + "\n")
    return _url


def stop() -> None:
    global _server, _url
    if _server is not None:
        _server.shutdown()
        _server.server_close()
    _server, _url = None, None
    ex.APPROVAL_PAGE = False
