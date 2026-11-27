"""Local web UI for the git assistant.

  python -m git_assistant.webui                    start it; the address is printed in the terminal
  python -m git_assistant.webui --demo merge_conflict      build that broken repo and open the browser

What it shows, per repo: the problem (what the user typed, the failing command and its output),
the repo state read by the tool, the assistant's plain-language explanation, and the proposed
fix with a risk tier on every step. Steps run through the same approval gate as the MCP tool
(see session.py); afterwards the repo is inspected again and the page says whether it worked.

For now it only works on scenario repos it builds in a temporary workspace: the git tool is
limited to that folder for this process.

Safety of the page itself (same rules as the approval page, docs/approval_page.md):
- listens on 127.0.0.1 only, under a random secret path; any other path is 404
- the Host header must be this exact address (stops DNS-rebinding pages)
- a POST from another origin is refused (stops other websites from pressing the buttons)
- no JavaScript at all (CSP default-src 'none'), no framing, no caching, everything escaped
Standard library + FastAPI/uvicorn, which the project already depends on.
"""

import argparse
import html
import os
import secrets
import socket
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from git_assistant.session import Workspace, tuned_scenarios

TIER_COLOR = {"safe": "#1a7f37", "careful": "#9a6700", "destructive": "#cf222e", "blocked": "#57606a"}
e = lambda x: html.escape(str(x), quote=True)


# ------------------------------------------------------------------ html

CSS = """
:root{--bg:#fff;--fg:#1f2328;--muted:#57606a;--line:#d0d7de;--card:#f6f8fa;--code:#eff1f3;--ok:#1a7f37;--bad:#cf222e}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8b949e;--line:#30363d;--card:#161b22;--code:#1f2630;--ok:#3fb950;--bad:#f85149}}
*{box-sizing:border-box} body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:20px auto;padding:0 16px}
a{color:inherit} h1{font-size:22px;margin:4px 0 2px} h2{font-size:16px;margin:0 0 8px}
.card{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:14px 16px;margin:12px 0}
.muted,.meta{color:var(--muted);font-size:13px} pre{background:var(--code);padding:10px;border-radius:6px;overflow-x:auto;font-size:13px;margin:6px 0;white-space:pre-wrap;word-break:break-word}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:14px}
.tier{color:#fff;border-radius:10px;padding:1px 9px;font-size:12px;font-weight:600;white-space:nowrap}
ol.steps{padding-left:22px;margin:0} ol.steps li{margin:10px 0} .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
form{margin:0;display:inline} input{font:inherit;padding:5px 8px;margin:4px 6px 4px 0;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--line);cursor:pointer;background:var(--bg);color:var(--fg)}
button.primary{background:#1a7f37;color:#fff;border-color:#1a7f37} button.danger{background:#cf222e;color:#fff;border-color:#cf222e}
button:disabled{opacity:.5;cursor:default}
.ok{color:var(--ok);font-weight:600} .bad{color:var(--bad);font-weight:600}
.flash{padding:8px 12px;border-radius:6px;margin:8px 0;font-size:14px} .flash.error{background:#ffebe9;color:#82071e} .flash.warn{background:#fff8c5;color:#4d2d00} .flash.info{background:#ddf4ff;color:#0a3069}
.wait{border-color:#9a6700;border-width:2px} .codebox{font-weight:700;letter-spacing:.12em;font-size:18px}
table{border-collapse:collapse;width:100%;font-size:13px} td,th{border-top:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:4px 16px}
@media (max-width:600px){body{margin:12px auto} table{display:block;overflow-x:auto}}
"""


def page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><title>{e(title)}</title><style>{CSS}</style></head>'
            f"<body>{body}</body></html>")


def badge(tier: str | None) -> str:
    if not tier:
        return ""
    return f'<span class="tier" style="background:{TIER_COLOR.get(tier, "#57606a")}">{e(tier)}</span>'


def flashes(messages: list, extra: str = "") -> str:
    out = [f'<div class="flash {e(kind)}">{e(text)}</div>' for kind, text in messages]
    if extra:
        out.append(f'<div class="flash info">{e(extra)}</div>')
    return "".join(out)


def index_html(ws: Workspace, base: str, msg: str = "") -> str:
    rows = "".join(
        f'<tr><td><a href="{base}repo/{e(c.name)}">{e(c.name)}</a></td><td>{e(c.key["title"])}</td>'
        f'<td>{"fixed" if c.result and c.result["fixed"] else "not fixed yet" if c.result and c.result["fixed"] is False else "-"}</td></tr>'
        for c in ws.cases.values()) or '<tr><td colspan="3" class="muted">No repo yet: build one below.</td></tr>'
    options = "".join(f'<option value="{e(sid)}">{e(sid.replace("_", " "))}</option>' for sid in tuned_scenarios())
    return page("Git assistant", f"""
<h1>Git assistant</h1><p class="muted">Local only. Every change goes through the approval gate:
safe steps run with one click, careful ones need the approval code, destructive ones the code and a typed phrase.</p>
{flashes([], msg)}
<div class="card"><h2>Repos in this workspace</h2><table><tr><th>repo</th><th>problem</th><th>status</th></tr>{rows}</table>
<p class="meta">Workspace: {e(ws.root)}. For now the assistant only works on scenario repos built here.</p></div>
<div class="card"><h2>Build a broken scenario repo</h2>
<form method="post" action="{base}build"><select name="scenario">{options}</select>
<button class="primary">Build it</button></form>
<p class="meta">Only the tuned scenarios are offered; the 6 held-out ones are kept for the benchmark.</p></div>""")


def _state_html(view: dict) -> str:
    s = view["state"]
    if not s.get("is_repo"):
        return f"<p>{e(s.get('explain', 'not a repo'))}</p>"
    facts = [("branch", s["branch"] or f"detached at {s['head']}"), ("upstream", s["upstream"] or "none"),
             ("ahead / behind", f"{s['ahead']} / {s['behind']}"), ("in progress", s["in_progress"] or "nothing")]
    for k in ("conflicted", "staged", "unstaged", "untracked"):
        if s[k]["count"]:
            facts.append((k, ", ".join(s[k]["items"][:8]) + (" ..." if s[k]["count"] > 8 else "")))
    grid = "".join(f"<div><span class='muted'>{e(k)}:</span> {e(v)}</div>" for k, v in facts)
    return (f"<p><b>{e(view['plain'])}</b></p><div class='grid'>{grid}</div>"
            f"<details><summary class='muted'>git log</summary><pre>{e(view['log'])}</pre></details>")


def case_html(ws: Workspace, case, base: str) -> str:
    view = ws.inspect(case)
    k, r = case.key, f"{base}repo/{e(case.name)}/"
    msgs = case.messages[:]
    case.messages.clear()
    parts = [f'<p class="muted"><a href="{base}">All repos</a></p><h1>{e(k["title"])}</h1>',
             f'<p class="meta">{e(case.name)} · {e(case.work)}</p>', flashes(msgs)]

    parts.append(f"""<div class="card"><h2>The problem</h2><p>“{e(k['user_says'])}”</p>
<p class="meta">Command that failed: <code>{e(k['command'])}</code></p><pre>{e(k['error_output'][:3000])}</pre></div>""")
    parts.append(f'<div class="card"><h2>Your repo right now</h2>{_state_html(view)}</div>')

    plan = case.plan
    if plan is None:
        parts.append(f"""<div class="card"><h2>What's wrong, and how to fix it</h2>
<form method="post" action="{r}diagnose"><input type="hidden" name="mode" value="llm">
<button class="primary">Ask the assistant</button></form>
<form method="post" action="{r}diagnose"><input type="hidden" name="mode" value="key">
<button>Show the answer key instead (no LLM)</button></form>
<p class="meta">The assistant reads the repo state above and the git docs, then explains in plain words.
It uses your LLM budget (one or two calls).</p></div>""")
    else:
        warn = "".join(f"<li>{e(w)}</li>" for w in plan.get("warnings", []))
        note = f'<p class="meta">{e(plan["note"])}</p>' if plan.get("note") else ""
        docs = (f'<p class="meta">Docs used: {e(", ".join(plan["docs"]))}</p>' if plan.get("docs") else "")
        parts.append(f"""<div class="card"><h2>What's wrong</h2><p class="meta">From: {e(case.plan_source)}</p>
<p><b>{e(plan.get('cause') or '(no cause given)')}</b></p><p>{e(plan.get('explanation', ''))}</p>
{f'<p><b>Before you run anything:</b></p><ul>{warn}</ul>' if warn else ''}{note}{docs}</div>""")
        busy = bool(case.pending or case.manual)
        items = []
        for i, s in enumerate(plan["steps"]):
            if i in case.done:
                status = '<span class="ok">done</span>'
            elif i in case.skipped:
                status = f'<span class="muted">not run by the tool: {e(case.skipped[i])}</span>'
            elif case.current == i:
                status = '<span class="muted">running: waiting for you below</span>'
            else:
                status = (f'<form method="post" action="{r}run"><input type="hidden" name="step" value="{i}">'
                          f'<button{" disabled" if busy else ""}>'
                          f'{f"Run step {i + 1}" if s["command"] else f"Do step {i + 1} by hand"}</button></form>')
            cmd = (f"<code>{e(s['command'])}</code> {badge(s.get('tier'))}" if s["command"]
                   else '<span class="muted">do this yourself:</span>')
            reason = f'<div class="meta">{e(s["tier"])}: {e(s.get("tier_reason", ""))}</div>' if s.get("tier") else ""
            items.append(f'<li><div class="row">{cmd} {status}</div><div>{e(s.get("why", ""))}</div>{reason}</li>')
        parts.append(f"""<div class="card"><h2>Proposed fix</h2><ol class="steps">{"".join(items) or "<li>No steps.</li>"}</ol>
<p class="meta">The tier is checked again by the tool when a step runs, in the repo as it is then.
<form method="post" action="{r}diagnose"><input type="hidden" name="mode" value="{"llm" if case.plan_source == "assistant" else "key"}">
<button>Plan again</button></form></p></div>""")

    if case.pending:
        parts.append(approval_html(ws, case, r))
    if case.manual:
        m = case.manual
        sim = (f'<form method="post" action="{r}manual"><input type="hidden" name="simulate" value="1">'
               f'<button>Do the scenario’s edit for me</button></form>') if m["can_simulate"] else ""
        parts.append(f"""<div class="card wait"><h2>Your turn: a manual step</h2><p>{e(m['text'])}</p>
<div class="row"><form method="post" action="{r}manual"><input type="hidden" name="simulate" value="0">
<button class="primary">I did it</button></form>{sim}</div></div>""")

    if case.result:
        parts.append(result_html(case.result, r))
    if case.ran:
        rows = "".join(
            f"<tr><td>{x['step']}</td><td><code>{e(x['command'])}</code></td><td>{badge(x.get('tier')) if x.get('tier') in TIER_COLOR else e(x.get('tier', ''))}</td>"
            f"<td>{e(x.get('result', ''))}{'<pre>' + e(x['output']) + '</pre>' if x.get('output') else ''}</td>"
            f"<td>{e('; '.join(x.get('backups') or [])) or '-'}</td><td>{e(_undo(x.get('undo')))}</td></tr>"
            for x in case.ran)
        parts.append(f'<div class="card"><h2>What ran</h2><table><tr><th>step</th><th>command</th><th>tier</th>'
                     f'<th>result</th><th>backup</th><th>undo</th></tr>{rows}</table></div>')
    return page(f"{k['title']} · Git assistant", "".join(parts))


def _undo(u) -> str:
    if not u:
        return "-"
    return "; ".join(map(str, u)) if isinstance(u, (list, tuple)) else str(u)


def approval_html(ws: Workspace, case, r: str) -> str:
    from mcp_servers.git_local import execute as ex
    p = case.pending
    lose = "".join(f"<li>{e(x)}</li>" for x in p["would_lose"])
    phrase = (f'<label>Type <code>{e(p["phrase"])}</code>: <input name="phrase" autocomplete="off" required size="34"></label>'
              if p["phrase"] else "")
    if ws.approval_ui:      # GIT_APPROVAL_UI=1: the button carries the code, as on the approval page
        human = ex.human_view(p["step_id"]) or {}
        code_field = (f'<input type="hidden" name="code" value="{e(human.get("code", ""))}">'
                      f'<p class="meta">Approval code <span class="codebox">{e(human.get("code", ""))}</span> '
                      f'(single use, expires in 10 minutes)</p>')
    else:                   # default: the code is only in the terminal and logs/approvals.log
        code_field = ('<label>Approval code from the terminal where you started the UI '
                      '(also in <code>logs/approvals.log</code>): '
                      '<input name="code" autocomplete="off" required size="8" maxlength="6"></label>')
    return f"""<div class="card wait"><h2>Waiting for your approval</h2>
<div class="row"><code>{e(p['command'])}</code> {badge(p['tier'])}</div><p>{e(p['explain'])}</p>
{f'<p><b>Would lose:</b></p><ul>{lose}</ul><p class="meta">A backup is made automatically before it runs.</p>' if lose else ''}
<form method="post" action="{r}approve"><input type="hidden" name="step_id" value="{e(p['step_id'])}">
{code_field}{phrase}<button class="{"danger" if p["tier"] == "destructive" else "primary"}">Approve and run</button></form>
<form method="post" action="{r}reject"><input type="hidden" name="step_id" value="{e(p['step_id'])}">
<button>Reject</button></form></div>"""


def result_html(res: dict, r: str) -> str:
    def lines(checks):
        return "".join(f'<li><span class="{"ok" if c["ok"] else "bad"}">{"✓" if c["ok"] else "✗"}</span> '
                       f'{e(c["check"])}{(": " + e(c["detail"])) if c.get("detail") and not c["ok"] else ""}</li>'
                       for c in checks)
    head = ('<p class="ok">Fixed: the repo is in the state it should be.</p>' if res["fixed"] else
            '<p class="muted">This problem is about the advice; there is nothing to fix inside the repo.</p>'
            if res["fixed"] is None else '<p class="bad">Not fixed yet.</p>')
    safe = ('<span class="ok">all safety checks pass</span>' if res["safe"] else '<span class="bad">a safety check failed</span>')
    return f"""<div class="card"><h2>Did it work?</h2>{head}<p class="meta">Repo now: {e(res['now'])}</p>
<details{' open' if res['fixed'] is False else ''}><summary>The repo checks</summary><ul>{lines(res['state'])}</ul></details>
<details{' open' if not res['safe'] else ''}><summary>Safety: {safe}</summary><ul>{lines(res['safety'])}</ul></details>
<form method="post" action="{r}recheck"><button>Inspect again</button></form></div>"""


# ------------------------------------------------------------------ app

def create_app(ws: Workspace, secret: str, allowed_hosts: set[str]) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    base = f"/{secret}/"
    origins = {f"http://{h}" for h in allowed_hosts}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in allowed_hosts or not request.url.path.startswith(base):
            resp: Response = PlainTextResponse("not found", status_code=404)
        elif request.method == "POST" and (
                (request.headers.get("origin") is not None and request.headers["origin"] not in origins)
                or request.headers.get("sec-fetch-site") == "cross-site"):
            resp = PlainTextResponse("cross-site request refused", status_code=403)
        else:
            resp = await call_next(request)
        resp.headers.update({"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
                             "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                                        "form-action 'self'; frame-ancestors 'none'"})
        return resp

    async def form(request: Request) -> dict:
        body = (await request.body())[:20_000].decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(body).items()}

    def back(path: str, msg: str = "") -> RedirectResponse:
        return RedirectResponse(base + path + (f"?msg={quote(msg)}" if msg else ""), status_code=303)

    def case_or_404(name: str):
        try:
            return ws.get(name)
        except KeyError:
            return None

    @app.get(base, response_class=HTMLResponse)
    def index(msg: str = ""):
        return index_html(ws, base, msg[:200])

    @app.post(base + "build")
    async def build(request: Request):
        f = await form(request)
        try:
            case = ws.build(f.get("scenario", ""))
        except ValueError as err:
            return back("", str(err))
        return back(f"repo/{case.name}")

    @app.get(base + "repo/{name}", response_class=HTMLResponse)
    def show(name: str):
        case = case_or_404(name)
        if case is None:
            return HTMLResponse(page("Not found", "<p>No such repo.</p>"), status_code=404)
        with ws.lock:                       # rendering reads and clears the case's messages
            return case_html(ws, case, base)

    @app.post(base + "repo/{name}/{action}")
    async def act(name: str, action: str, request: Request):
        case = case_or_404(name)
        if case is None:
            return PlainTextResponse("not found", status_code=404)
        f = await form(request)
        try:
            if action == "diagnose":
                ws.diagnose(case, use_llm=f.get("mode") != "key")
            elif action == "run":
                ws.run_step(case, int(f.get("step", "-1")))
            elif action == "approve":
                ws.approve(case, f.get("step_id", ""), f.get("code", ""), f.get("phrase"))
            elif action == "reject":
                ws.reject(case, f.get("step_id", ""))
            elif action == "manual":
                ws.manual_done(case, simulate=f.get("simulate") == "1")
            elif action == "recheck":
                ws.recheck(case)
            else:
                return PlainTextResponse("not found", status_code=404)
        except (ValueError, KeyError) as err:
            case.messages.append(("error", f"Not possible: {err}"))
        except Exception as err:                      # an LLM or network failure: show it, don't crash
            case.messages.append(("error", f"{type(err).__name__}: {str(err)[:300]}"))
        return back(f"repo/{case.name}")

    return app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv=None) -> None:
    import uvicorn
    from common.console import utf8_console
    utf8_console()
    ap = argparse.ArgumentParser(description="Local web UI for the git assistant (scenario repos only).")
    ap.add_argument("--port", type=int, default=0, help="0 = any free port")
    ap.add_argument("--workspace", help="folder for the scenario repos (default: a new temp folder)")
    ap.add_argument("--demo", metavar="SCENARIO", nargs="?", const="merge_conflict",
                    help="build this broken scenario repo first (default merge_conflict) and open the browser")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)

    workspace = Path(a.workspace or tempfile.mkdtemp(prefix="git-assistant-ui-")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["GIT_TOOL_ALLOWED_ROOTS"] = str(workspace)     # the tool may touch nothing else
    ws = Workspace(workspace, approval_ui=os.environ.get("GIT_APPROVAL_UI", "0") == "1")
    port = a.port or _free_port()
    secret = secrets.token_urlsafe(24)
    app = create_app(ws, secret, {f"127.0.0.1:{port}", f"localhost:{port}"})
    url = f"http://127.0.0.1:{port}/{secret}/"
    if a.demo:
        url += f"repo/{ws.build(a.demo).name}"
    print(f"[git-assistant] web UI: {url}", file=sys.stderr, flush=True)
    print(f"[git-assistant] workspace: {workspace}  (approval codes are printed here)", file=sys.stderr, flush=True)
    if a.demo and not a.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
