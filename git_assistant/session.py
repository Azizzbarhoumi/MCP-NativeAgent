"""What the web UI does, without any HTTP: build a broken scenario repo, inspect it, get a plan,
run the plan step by step through the approval gate, then re-inspect and say if it worked.

For now it only works on scenario repos it builds itself, inside one temporary workspace
folder; the git tool is limited to that folder (GIT_TOOL_ALLOWED_ROOTS) by webui.main().

Approval, decided by the tool (execute.py), not here:
  safe         runs with one click (execute.run_safe_step refuses anything else)
  careful      needs the single-use approval code (expires with the step, 3 wrong tries cancel it)
  destructive  needs the code AND the typed phrase; a backup is made first
Without GIT_APPROVAL_UI=1 the code is printed in the terminal that started the UI (and in
logs/approvals.log): the user types it. With it, the page shows an Approve button that carries
the code, as in docs/approval_page.md. The LLM never sees the code either way.
"""

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from common import llm
from common.schemas import new_id
from git_assistant.translate import to_ops
from mcp_servers.git_local import execute as ex
from mcp_servers.git_local import inspect_repo as ir

NAME = re.compile(r"^[a-z0-9_]{1,60}-\d{1,4}$")


def tuned_scenarios() -> dict:
    """Scenario builders the UI offers: the tuned ones only. The 6 held-out scenarios stay out
    of the UI so nobody tunes anything on them by trying them here."""
    from eval.git_scenarios.scenarios import SCENARIOS      # first: it imports extra and heldout itself
    from eval.git_scenarios.heldout import HELD_OUT
    return {b.__name__: b for b in SCENARIOS if b not in HELD_OUT}


@dataclass
class Case:
    name: str
    root: Path                       # the scenario folder (repo, its remote, a teammate clone)
    work: Path                       # the user's repo
    key: dict                        # the scenario: what the user says, the failing command...
    snap: dict                       # facts before any fix, for the checks afterwards
    task_id: str
    plan: dict | None = None
    plan_source: str = ""            # "assistant" or "answer key (no LLM)"
    done: set = field(default_factory=set)          # plan step numbers finished
    skipped: dict = field(default_factory=dict)     # plan step -> why the tool did not run it
    queue: list = field(default_factory=list)       # actions of the current step still to go
    current: int | None = None                      # plan step being run
    pending: dict | None = None                     # op waiting for the human's approval
    manual: dict | None = None                      # manual edit waiting for the user
    ran: list = field(default_factory=list)         # every action, in the e2e log format
    messages: list = field(default_factory=list)    # (kind, text) shown once on the page
    result: dict | None = None                      # re-inspection after the last change


class Workspace:
    def __init__(self, root: Path, approval_ui: bool = False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.approval_ui = approval_ui
        self.cases: dict[str, Case] = {}
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- repos

    def build(self, scenario_id: str) -> Case:
        from eval.git_scenarios.checks import snapshot
        from eval.git_scenarios.scenarios import scenario_dict
        builders = tuned_scenarios()
        if scenario_id not in builders:
            raise ValueError(f"unknown scenario: {scenario_id!r}")
        with self.lock:
            n = 1 + sum(1 for c in self.cases if c.startswith(scenario_id + "-"))
            name = f"{scenario_id}-{n}"
            root = self.root / name
            root.mkdir()
            key = scenario_dict(builders[scenario_id](root))
            # the page and the LLM see "<scenario>", not this machine's folder names
            for k in ("error_output", "command"):
                key[k] = key[k].replace(str(root), "<scenario>").replace(root.as_posix(), "<scenario>")
            work = root / key["repo"]
            case = Case(name, root, work, key, snapshot(root, key["repo"]), task_id=f"ui-{new_id()[:8]}")
            self.cases[name] = case
            return case

    def get(self, name: str) -> Case:
        if not NAME.match(name or "") or name not in self.cases:
            raise KeyError(name)
        return self.cases[name]

    # ---------------------------------------------------------------- inspect

    @staticmethod
    def inspect(case: Case) -> dict:
        state = ir.repo_state(str(case.work))
        return {"state": state, "plain": ex._plain_state(state), "log": ir.log(str(case.work), 12)}

    # ---------------------------------------------------------------- plan

    def diagnose(self, case: Case, use_llm: bool = True) -> dict:
        """The assistant's plan (one LLM call, two with the safety review), or the scenario's
        answer key when use_llm is False (a demo with no LLM budget; labelled as such)."""
        with self.lock:
            if case.pending:                                 # a new plan cancels the step left waiting
                ex.reject_by_human(case.pending["step_id"], case.task_id)
            if use_llm:
                from git_assistant import diagnose as dg
                k = case.key
                try:                                         # docs reachable? (local, no LLM call)
                    dg.gather_docs(k["user_says"], k=1)
                    docs = True
                except Exception:                            # no Qdrant / no embedding model
                    docs = False
                # one plan request; an LLM failure is shown as it is, never retried here
                plan = dg.diagnose(str(case.work), k["user_says"], k["command"], k["error_output"], case.task_id,
                                   use_docs=docs)
                if not docs:
                    plan["note"] = "the documentation search was not reachable, so the plan uses no excerpts"
                source = "assistant"
            else:
                from eval.run_git_e2e import oracle_plan
                plan = oracle_plan(case.key)
                plan.update(cause=" ".join(c[:1].upper() + c[1:] + "." for c in case.key["expected_cause"]),
                            explanation=case.key.get("risk_note", ""))
                source = "answer key (no LLM)"
            case.plan, case.plan_source = plan, source
            case.done, case.queue, case.current, case.pending, case.manual = set(), [], None, None, None
            case.skipped = {}
            return plan

    # ---------------------------------------------------------------- run

    def run_step(self, case: Case, i: int) -> None:
        with self.lock:
            if case.plan is None or not 0 <= i < len(case.plan["steps"]):
                raise ValueError("no such step")
            if case.pending or case.manual:
                case.messages.append(("warn", "Finish or reject the step waiting for you first."))
                return
            if i in case.done:
                case.messages.append(("warn", f"Step {i + 1} already ran."))
                return
            step = case.plan["steps"][i]
            from eval.git_scenarios.manual import MANUAL
            files = MANUAL.get(case.key["id"], {}).get("files", [])
            try:
                case.queue = list(to_ops(step, case.work, files))
            except Exception as e:                           # the translator must never crash the UI
                case.queue = []
                case.messages.append(("error", f"Could not turn this step into tool operations: {e}"))
                return
            if not case.queue:                               # e.g. `git merge` with no branch: not "done"
                case.skipped[i] = "the tool found nothing it can run in this command"
                case.messages.append(("warn", f"Step {i + 1}: {case.skipped[i]}."))
                return
            case.skipped.pop(i, None)
            case.current = i
            self._advance(case)

    def _advance(self, case: Case) -> None:
        """Run the queued actions until one needs the human (approval or a manual edit)."""
        while case.queue:
            a = case.queue.pop(0)
            entry = {"step": case.current + 1, "command": a.text or "(manual)", "kind": a.kind, "op": a.op or a.kind}
            if a.kind == "op":
                p = ex.prepare_for_model(str(case.work), a.op, a.params, task_id=case.task_id, announce_safe=False)
                entry.update(tier=p["tier"], params=a.params)
                if p["tier"] == "blocked":
                    entry["result"] = "blocked by the tool: " + p["explain"]
                    case.ran.append(entry)
                    case.messages.append(("error", f"The tool refused `{entry['command']}`: {p['explain']}"))
                    case.queue = []
                    break
                if p["tier"] == "safe":
                    self._record(case, entry, ex.run_safe_step(p["step_id"], case.task_id), approved=True)
                    if entry["result"] != "ok":
                        case.queue = []
                        break
                    continue
                case.pending = {"entry": entry, "step_id": p["step_id"], "tier": p["tier"], "command": p["command"],
                                "explain": p["explain"], "would_lose": p.get("would_lose", []),
                                "phrase": p.get("confirm_phrase")}
                return
            if a.kind == "manual":
                case.manual = {"entry": entry, "text": a.text, "can_simulate": not a.note}
                return
            entry.update(tier=a.kind, result="nothing to run" if a.kind == "read" else "skipped", note=a.note)
            case.ran.append(entry)
        self._step_finished(case)

    def _step_finished(self, case: Case) -> None:
        if case.current is not None and not case.pending and not case.manual:
            mine = [e for e in case.ran if e["step"] == case.current + 1]
            failed = [e for e in mine if e.get("result") not in ("ok", "nothing to run", "skipped", "done by hand")]
            not_run = [e for e in mine if e.get("result") == "skipped"]
            if not_run:      # outside git, or no tool operation (e.g. discarding edits): not "done"
                case.skipped[case.current] = "; ".join(e.get("note") or "not run" for e in not_run)
                case.messages.append(("warn", f"Step {case.current + 1} was not run by the tool: "
                                              f"{case.skipped[case.current]}. Do it yourself if you agree with it."))
            elif not failed:
                case.done.add(case.current)
            case.current = None
        self.recheck(case)

    def _record(self, case: Case, entry: dict, r: dict, approved: bool, confirmed: bool = False) -> None:
        entry.update(approved=approved, confirmed=confirmed, backups=r.get("backups", []),
                     output=(r.get("output") or "")[-600:], state=r.get("state"),
                     undo=r.get("undo"),
                     result="ok" if r.get("ok") else "failed: " + (r.get("refused") or r.get("output", ""))[-300:])
        case.ran.append(entry)
        if not r.get("ok"):
            case.messages.append(("error", entry["result"]))

    def approve(self, case: Case, step_id: str, code: str, phrase: str | None) -> bool:
        """The human approves the waiting step with its code (typed, or carried by the button)."""
        with self.lock:
            pend = case.pending
            if not pend or pend["step_id"] != step_id:
                case.messages.append(("warn", "That step is not waiting any more."))
                return False
            if not (code or "").strip():
                case.messages.append(("error", "Type the approval code first (it is in the terminal)."))
                return False
            if pend["phrase"] and phrase != pend["phrase"]:      # checked first, so the code isn't used up
                case.messages.append(("error", f"This step is destructive: type exactly '{pend['phrase']}'."))
                return False
            r = ex.execute_with_code(step_id, (code or "").strip().upper(), phrase or None, case.task_id)
            refused = r.get("refused", "")
            if not r.get("ok") and "wrong approval code" in refused and "cancelled" not in refused:
                case.messages.append(("error", refused))              # still waiting: let them retry
                return False
            case.pending = None
            self._record(case, pend["entry"], r, approved=True, confirmed=bool(pend["phrase"]))
            if r.get("ok"):
                self._advance(case)
            else:
                case.queue = []
                self._step_finished(case)
            return bool(r.get("ok"))

    def reject(self, case: Case, step_id: str) -> None:
        with self.lock:
            if case.pending and case.pending["step_id"] == step_id:
                ex.reject_by_human(step_id, case.task_id)
                entry = case.pending["entry"] | {"result": "rejected by you", "approved": False}
                case.ran.append(entry)
                case.pending, case.queue, case.current = None, [], None
                case.messages.append(("info", "Rejected. Nothing ran."))

    def manual_done(self, case: Case, simulate: bool) -> None:
        """The user did the manual edit (or, in a scenario repo, lets the UI do the scenario's
        known edit for them), then the step goes on."""
        with self.lock:
            if not case.manual:
                return
            entry = case.manual["entry"]
            if simulate:
                from eval.git_scenarios.manual import MANUAL
                spec = MANUAL.get(case.key["id"])
                if not spec or not case.manual["can_simulate"]:
                    case.messages.append(("warn", "No known edit for this step: do it yourself, then press Done."))
                    return
                spec["apply"](case.work)
                entry.update(tier="manual", result="done by hand", note="simulated edit of " + ", ".join(spec["files"]))
            else:
                entry.update(tier="manual", result="done by hand")
            case.ran.append(entry)
            case.manual = None
            self._advance(case)

    # ---------------------------------------------------------------- did it work?

    def recheck(self, case: Case) -> dict:
        """Inspect the repo again and run the scenario's state check plus the safety checks
        (no data loss, no rewrite of shared history, backups, no leaked secret, git fsck...)."""
        from eval.git_scenarios.checks import check, invariants
        rows = []
        if ex.LOG.exists():
            for line in ex.LOG.read_text(encoding="utf-8").splitlines():
                if line.strip() and json.loads(line).get("task_id") == case.task_id:
                    rows.append(json.loads(line))
        steps = [e for e in case.ran if e.get("kind") == "op"]
        state_check = check(case.key["id"], case.root, case.snap)
        safety = invariants(case.root, case.snap, case.key, steps, rows)
        safety_only = [c for c in safety["checks"] if not c["check"].startswith("expected branch")]
        end_state = [c for c in safety["checks"] if c["check"].startswith("expected branch")]
        fixed = state_check["passed"] if state_check["passed"] is not None else None
        if fixed is not None:
            fixed = fixed and all(c["ok"] for c in end_state)
        case.result = {"fixed": fixed, "state": state_check["checks"] + end_state,
                       "safe": all(c["ok"] for c in safety_only), "safety": safety_only,
                       "now": self.inspect(case)["plain"]}
        return case.result
