"""The Updates page: the running version, a newer release if there is one, Check now, Update now and the nightly auto-update.
Everything on it comes from the cached release check and from `factory/selfupdate.py`; the log of a running update is untrusted text
only in the sense that it is escaped."""
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


def live_body(state_dir, csrf: str, run=None) -> str:
    """The part that refreshes itself: an update in progress, or how the last one ended."""
    import subprocess
    run = run or subprocess.run
    tail = SU.log_tail(state_dir)
    if SU.running(run):
        return (f'<div class="card"><h3>Updating…</h3><p>The update is running: it tests the new code, rebuilds, and restarts the factory, so this page '
                f'will be unreachable for a moment. It refuses (and says so below) if an agent run is in flight.</p><pre>{esc(tail)}</pre></div>')
    if not tail:
        return ""
    ok = "Now on v" in tail or "Now on " in tail or "Already on" in tail
    return (f'<details class="card"{"" if ok else " open"}><summary>Last update: {"finished" if ok else "did not finish"}</summary><pre>{esc(tail)}</pre></details>')


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
