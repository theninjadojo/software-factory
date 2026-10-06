"""The Updates page: the running version, a newer release if there is one, Check now, Update now and the nightly auto-update.
Everything on it comes from the cached release check and from `factory/selfupdate.py`; the log of a running update is untrusted text
only in the sense that it is escaped."""
import re
import time

from .. import selfupdate as SU
from .. import updates, version
from . import views
from .views import csrf_field, esc

MODE_TEXT = {"off": ("Off", "Updates wait for you to press Update now."),
             "patch": ("Patch releases", "Every night a newer patch (x.y.Z) installs itself; a new minor or major version waits for you."),
             "all": ("Every release", "Every night any newer release installs itself, minor and major versions too.")}


def checked_line(state_dir) -> str:
    c = updates._read(state_dir)
    when = float(c.get("checked", 0) or 0)
    return f"Last checked {views.ago(when)}." if when else "Not checked yet."


STAGES = ("Pause and check no agent run is in flight", "Download and check the release", "Back up and install",
          "Rebuild sandbox images", "Restart services", "Health check")
_MARK = re.compile(r"^##STAGE ([1-6])/6 [a-z]+$", re.M)   # only these exact lines count; deploy/update-native.sh prints them
_TARGET = re.compile(r"^Updating: \S+ -> (v\d+\.\d+\.\d+)$", re.M)


def parse_log(tail: str):
    """(last step 1-6 or 0, the target tag or "", the log without the marker lines)."""
    marks, tags = _MARK.findall(tail), _TARGET.findall(tail)
    return (int(marks[-1]) if marks else 0), (tags[-1] if tags else ""), _MARK.sub("", tail).strip("\n")


def _rows(step: int) -> str:
    out = []
    for i, name in enumerate(STAGES, 1):
        if i < step:
            out.append(f'<li class="done"><span aria-hidden="true">✓</span> {esc(name)}</li>')
        elif i == step:
            out.append(f'<li class="now"><span aria-hidden="true">●</span> {esc(name)} <small>now</small></li>')
        else:
            out.append(f'<li><span aria-hidden="true">○</span> {esc(name)}</li>')
    return f'<ol class="stage-list">{"".join(out)}</ol>'


def live_body(state_dir, csrf: str, run=None) -> str:
    """The part that refreshes itself: an update in progress (as a step list), or how the last one ended."""
    import subprocess
    run = run or subprocess.run
    tail = SU.log_tail(state_dir)
    step, tag, log = parse_log(tail)
    if SU.running(run):
        n, total = step or 1, len(STAGES)
        note = ('<p>The factory is restarting. This page will reconnect by itself.</p>' if n >= 5 else
                '<p class="muted">It refuses (and says so) if an agent run is in flight.</p>')
        return (f'<div class="card" aria-live="polite"><h3>Updating{" to " + esc(tag) if tag else ""}</h3>'
                f'<p>Step {n} of {total}: <strong>{esc(STAGES[n - 1])}</strong></p>'
                f'<div class="bar" role="progressbar" aria-label="Update steps" aria-valuemin="0" aria-valuemax="{total}" aria-valuenow="{n}">'
                f'<div style="width:{round(100 * n / total)}%"></div></div>{_rows(n)}{note}'
                f'<details><summary>Show log</summary><pre>{esc(log)}</pre></details></div>')
    if not tail:
        return ""
    failed_step = f'<p class="bad-text"><span aria-hidden="true">✕</span> {esc(STAGES[step - 1])}</p>' if step else ""
    if "Now on " in tail or "Already on" in tail:
        state, text, msg, failed_step = "good", "finished", (f"Now on {tag}." if tag and "Now on " in tail else "Already up to date."), ""
    elif "CHECK FAILED" in tail:
        state, text, msg = "bad", "did not finish", f"{tag or 'The new version'} does not start on this machine. Nothing was changed."
    elif "TESTS FAILED" in tail:
        state, text, msg = "bad", "did not finish", f"The tests failed on {tag or 'the new version'}. Nothing was changed."
    elif "rolling back" in tail:
        state, text, msg = "warn", "rolled back", "The new version did not come up healthy, so the previous version is running again."
    elif "agent run is in flight" in tail:
        state, text, msg, failed_step = "warn", "waiting", "An agent run is in flight. Try again when it finishes.", ""
    else:
        state, text, msg = "bad", "did not finish", "The update did not finish."
    return (f'<details class="card"{"" if state == "good" else " open"}><summary>Last update <span class="badge {state}">{text}</span></summary>'
            f'<p>{esc(msg)}</p>{failed_step}<pre>{esc(log)}</pre></details>')


def page_body(cfg, state_dir, csrf: str, run=None) -> str:
    import subprocess
    run = run or subprocess.run
    have = version.current()
    got = updates.available(state_dir, have)
    latest = (updates._read(state_dir).get("latest") or {}).get("tag", "")
    native = SU.native(state_dir)
    head = (f'<p>You are on <strong>{esc(have)}</strong>. '
            + (f'<strong>{esc(got["tag"])}</strong> is available (<a href="{esc(got["url"])}" rel="noopener noreferrer" target="_blank">release notes</a>).' if got else
               (f'The latest release is {esc(latest)}: you are up to date.' if latest else "No release is known yet; press Check now."))
            + f' <span class="muted">{esc(checked_line(state_dir))}</span></p>')
    check = (f'<form method="post" action="/updates/check" class="inline">{csrf_field(csrf)}<button>Check now</button></form>')
    if got and native:
        apply = (f'<form method="post" action="/updates/apply" class="inline">{csrf_field(csrf)}<input type="hidden" name="tag" value="{esc(got["tag"])}">'
                 f'<button>Update to {esc(got["tag"])}</button></form>')
    elif got:
        apply = ('<p class="muted">This install cannot update itself from here. On the host run <code>./scripts/update.sh</code> '
                 '(Docker) or <code>update-native.sh</code> (systemd).</p>')
    else:
        apply = ""
    explain = ('<p class="muted">An update downloads the release (with the token the factory already has for the repository), runs its tests first, '
               'rebuilds the sandbox images and restarts the services. Nothing changes if the tests fail, it waits while an agent run is in '
               'flight, and it rolls back by itself if the new version does not come up.</p>') if native else ""
    mode = SU.auto_mode(state_dir, run) if native else "off"
    radios = "".join(f'<div class="field"><label class="check"><input type="radio" name="mode" value="{m}"{" checked" if m == mode else ""}> {esc(t)}</label>'
                     f'<div class="muted">{esc(d)}</div></div>' for m, (t, d) in MODE_TEXT.items())
    auto = ((f'<form method="post" action="/updates/auto" class="card">{csrf_field(csrf)}<h3>Update by itself</h3>{radios}'
             '<p class="muted">It runs every night around 03:30, tests first like a manual update, and waits (trying again the next night) while an '
             'agent run is in flight. Verification workers follow the factory: see <a href="/workers">Workers</a>.</p><button>Save</button></form>')
            if native else "")
    return head + f'<p>{check} {apply}</p>' + explain + f'<div id="live" data-src="/updates/status">{live_body(state_dir, csrf, run)}</div>' + auto
