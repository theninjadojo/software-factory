"""Builds in waves: a change to a repo that publishes packages ships before the repos that install them.

A project repo with a `publish` table publishes packages (to GitHub Packages, npm or anything else); a repo whose `depends_on`
names it installs the published version. When one build changes both, the factory opens only the publishing repo's PR (wave 1)
and holds the rest back. A person merges that PR; the factory then watches GitHub until the merge is published (a release, a tag
or a successful publish workflow that contains the merge commit) and only then builds the held repos (wave 2) in a fresh run,
against the published code. The waiting is recorded in the `waves` table, one row per publishing PR of a ticket, and shown on
the ticket with the `factory:waiting-release` label.

States of a row: merge (waiting for a person to merge the PR), publish (merged, waiting for the publish), released (published:
wave 2 may start), building (wave 2 is running), done, failed (wave 2 did not open a PR: the build label retries wave 2),
closed (the PR was closed unmerged, or the ticket was closed). A build label on a ticket left in failed or building (an
interrupted second build) runs wave 2 again."""
import calendar
import dataclasses
import json
import logging
import re
import time

from .config import Config, Project, PublishCfg, Route, project_for

log = logging.getLogger("factory.waves")
WAITING = "factory:waiting-release"
OPEN = ("merge", "publish", "released")
SAFE = re.compile(r"[^\w.@/+-]")
WARN_FAILED, WARN_LATE = 1, 2


def ensure_tables(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS waves (
            issue_repo TEXT NOT NULL, issue_num INTEGER NOT NULL, repo TEXT NOT NULL, pr INTEGER NOT NULL,
            held TEXT NOT NULL,                     -- space-separated repos built after the publish
            state TEXT NOT NULL, route TEXT NOT NULL DEFAULT '',
            merge_sha TEXT NOT NULL DEFAULT '', merged_at REAL NOT NULL DEFAULT 0,
            released TEXT NOT NULL DEFAULT '', released_url TEXT NOT NULL DEFAULT '',
            warned INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
            PRIMARY KEY (issue_repo, issue_num, repo))"""
    )


def split(project: Project, changed) -> tuple[list[str], list[str]]:
    """The changed repos that ship now, and those held until a changed repo they depend on is published."""
    changed = list(changed)
    later = [r for r in changed if (pr := project.repo(r)) and any(d in changed for d in pr.depends_on)]
    return [r for r in changed if r not in later], later


def upstream(project: Project, now, later) -> list[str]:
    """The repos of this wave whose publish the held repos wait for."""
    return [r for r in now if any(r in (project.repo(h).depends_on if project.repo(h) else ()) for h in later)]


def clean(text: str, n: int = 80) -> str:
    """Release and tag names are written by people with write access, and end up in prompts and comments: keep them plain."""
    return SAFE.sub("", str(text or ""))[:n] or "?"


def _epoch(iso: str | None) -> float:
    try:
        return float(calendar.timegm(time.strptime(iso or "", "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


def _route(text: str) -> Route | None:
    try:
        return Route(**{k: tuple(v) if isinstance(v, list) else v for k, v in json.loads(text).items()})
    except (ValueError, TypeError):
        return None


def record(db, issue_repo: str, issue_num: int, prs: list[tuple[str, int]], held: list[str], route: Route) -> None:
    now, r = time.time(), json.dumps(dataclasses.asdict(route))
    db.execute("DELETE FROM waves WHERE issue_repo=? AND issue_num=?", (issue_repo, issue_num))     # a new build replaces any earlier wait
    for repo, number in prs:
        db.execute("INSERT OR REPLACE INTO waves (issue_repo, issue_num, repo, pr, held, state, route, created, updated) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", (issue_repo, issue_num, repo, int(number), " ".join(held), "merge", r, now, now))
    db.commit()


def rows(db, issue_repo: str, issue_num: int, states=None) -> list[dict]:
    cur = db.execute("SELECT * FROM waves WHERE issue_repo=? AND issue_num=? ORDER BY repo", (issue_repo, issue_num))
    names = [c[0] for c in cur.description]
    out = [dict(zip(names, x)) for x in cur.fetchall()]
    return [x for x in out if states is None or x["state"] in states]


def open_tickets(db) -> list[tuple[str, int]]:
    q = f"SELECT DISTINCT issue_repo, issue_num FROM waves WHERE state IN ({','.join('?' * len(OPEN))}) ORDER BY created"
    return [(a, int(b)) for a, b in db.execute(q, OPEN).fetchall()]


def set_state(db, issue_repo: str, issue_num: int, state: str, repo: str | None = None, **fields) -> None:
    fields["state"], fields["updated"] = state, time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    where, args = "issue_repo=? AND issue_num=?", [issue_repo, issue_num]
    if repo is not None:
        where, args = where + " AND repo=?", args + [repo]
    db.execute(f"UPDATE waves SET {sets} WHERE {where}", [*fields.values(), *args])
    db.commit()


def close_open(db, issue_repo: str, issue_num: int) -> None:
    """A new build of the whole ticket: whatever it was waiting for no longer applies."""
    db.execute(f"UPDATE waves SET state='closed', updated=? WHERE issue_repo=? AND issue_num=? AND state IN ({','.join('?' * len(OPEN))})",
               (time.time(), issue_repo, issue_num, *OPEN))
    db.commit()


def held(ws: list[dict]) -> list[str]:
    return list(dict.fromkeys(h for w in ws for h in w["held"].split() if h))


def route_of(ws: list[dict]) -> Route | None:
    return next((r for r in map(_route, (w["route"] for w in ws)) if r), None)


def short(repo: str) -> str:
    return repo.split("/")[1]


def run_link(repo: str, pub: PublishCfg) -> str:
    return f"https://github.com/{repo}/actions/workflows/{pub.workflow}" if pub.detect == "workflow" else ""


def how(pub: PublishCfg) -> str:
    return {"release": "a GitHub release", "tag": "a tag",
            "workflow": f"a successful run of the {pub.workflow} workflow"}[pub.detect] + " that contains the merge"


def detect(gh, repo: str, pub: PublishCfg, sha: str, merged_at: float) -> dict | None:
    """Whether the merge commit `sha` has been published: {"ok": bool, "what", "url"} (ok False: the publish workflow failed),
    or None while there is nothing yet. Only what was made after the merge is looked at."""
    after = merged_at - 120                            # clock skew between GitHub's records
    if pub.detect == "release":
        for rel in gh.releases(repo):
            if rel.get("draft") or _epoch(rel.get("published_at")) < after:
                continue
            if gh.contains(repo, rel["tag_name"], sha):
                return {"ok": True, "what": f"release {clean(rel['tag_name'])}", "url": rel.get("html_url", "")}
    elif pub.detect == "tag":
        for t in gh.tags(repo)[:5]:
            if t.get("commit", {}).get("sha") == sha or gh.contains(repo, t["name"], sha):
                return {"ok": True, "what": f"tag {clean(t['name'])}", "url": f"https://github.com/{repo}/releases/tag/{clean(t['name'], 200)}"}
    else:
        for run in gh.workflow_runs(repo, pub.workflow):           # any branch or tag: a run on a release tag counts if it holds the merge
            if run.get("status") != "completed" or _epoch(run.get("created_at")) < after:
                continue
            if run.get("head_sha") == sha or gh.contains(repo, run["head_sha"], sha):
                ok = run.get("conclusion") == "success"
                return {"ok": ok, "what": f"{clean(pub.workflow)} run {'passed' if ok else clean(run.get('conclusion'))}",
                        "url": run.get("html_url", "")}
    return None


def context(cfg: Config, issue_repo: str, ws: list[dict]) -> str:
    """What the wave 2 agent is told about the published change. Built only from fields the factory controls."""
    project = project_for(cfg, issue_repo)
    lines = []
    for w in ws:
        pub = project.repo(w["repo"]).publish if project.repo(w["repo"]) else None
        pk = f" (packages: {', '.join(clean(p, 100) for p in pub.packages)})" if pub and pub.packages else ""
        lines.append(f"- {short(w['repo'])}/: pull request #{int(w['pr'])} was merged as commit {clean(w['merge_sha'], 40)} and "
                     f"published as {w['released'] or 'a new version'}{pk}.")
    return ("Part of this ticket was built earlier, in the repos that publish packages, and has now been merged and published:\n"
            + "\n".join(lines) + "\nTheir directories hold the default branch, which includes those changes. Do not change them again.\n"
            f"Change only these repos: {', '.join(short(h) + '/' for h in held(ws))}. Make them use the published version: update the "
            "dependency version in their package manifest to the version published above (read it from the publishing repo's "
            "manifest), and build the rest of the ticket against the published code. You have no network, so you cannot update a "
            "lockfile entry that needs a download hash: say so in your summary if one is left to update.")


def tick(cfg: Config, gh, db, notify, emit, submit) -> None:
    """Move each waiting ticket on: merged? published? then hand wave 2 to submit(issue_repo, issue_num, rows, route), which
    returns False when it cannot start now (paused, or no free slot), so a later poll tries again. Never raises."""
    for issue_repo, num in open_tickets(db):
        try:
            if gh.get_issue(issue_repo, num).get("state") == "closed":
                set_state(db, issue_repo, num, "closed")
                gh.remove_label(issue_repo, num, WAITING)
                emit("waves:closed", "the ticket was closed: the held repos will not be built", issue_repo, num)
                continue
            for w in rows(db, issue_repo, num, ("merge", "publish")):
                _advance(cfg, gh, db, issue_repo, num, w, notify, emit)
            ws = rows(db, issue_repo, num)
            live = [w for w in ws if w["state"] in OPEN]
            if live and all(w["state"] == "released" for w in live):
                route = route_of(live)
                if route is None:
                    set_state(db, issue_repo, num, "failed")
                    emit("waves:failed", "no route recorded for the second build: apply the build label to start it", issue_repo, num)
                elif submit(issue_repo, num, live, route):
                    for w in live:
                        set_state(db, issue_repo, num, "building", w["repo"])
                    emit("waves:build", f"published; building {', '.join(short(h) for h in held(live))}", issue_repo, num)
        except Exception:
            log.exception("waves: %s#%s", issue_repo, num)          # GitHub hiccup: the next poll tries again


def _advance(cfg: Config, gh, db, issue_repo: str, num: int, w: dict, notify, emit) -> None:
    repo, pr_num, waiting = w["repo"], int(w["pr"]), ", ".join(short(h) for h in w["held"].split())
    project = project_for(cfg, issue_repo)
    pub = project.repo(repo).publish if project.repo(repo) else None
    if w["state"] == "merge":
        pr = gh.get_pr(repo, pr_num)
        if pr.get("merged"):
            w = {**w, "merge_sha": pr.get("merge_commit_sha") or "", "merged_at": _epoch(pr.get("merged_at")) or time.time()}
            set_state(db, issue_repo, num, "publish", repo, merge_sha=w["merge_sha"], merged_at=w["merged_at"])
            emit("waves:merged", f"{short(repo)}#{pr_num} merged; waiting for it to be published", issue_repo, num)
            notify(f"Merged {repo}#{pr_num} for {issue_repo}#{num}. Waiting for {how(pub) if pub else 'the publish'}, then building {waiting}."
                   + (f"\nIf {pub.workflow} does not run on its own, start it: {run_link(repo, pub)}" if pub and pub.detect == "workflow" else ""))
        elif pr.get("state") == "closed":
            set_state(db, issue_repo, num, "closed")
            gh.remove_label(issue_repo, num, WAITING)
            gh.comment(issue_repo, num, f"{repo}#{pr_num} was closed without being merged, so {waiting} will not be built. "
                                        f"Apply `{cfg.trigger_label}` to build the ticket again.")
            emit("waves:closed", f"{short(repo)}#{pr_num} closed unmerged: {waiting} not built", issue_repo, num)
            notify(f"{repo}#{pr_num} was closed without merging: {issue_repo}#{num} stops waiting, {waiting} is not built.", "failure")
            return
        else:
            return
    if pub is None:                                         # the publish table was removed: nothing to wait for any more
        set_state(db, issue_repo, num, "released", repo, released="the merged code (no publish settings)")
        return
    found = detect(gh, repo, pub, w["merge_sha"], w["merged_at"])
    if found and found["ok"]:
        set_state(db, issue_repo, num, "released", repo, released=found["what"], released_url=found["url"])
        emit("waves:published", f"{short(repo)} published: {found['what']}", issue_repo, num)
        notify(f"Published {repo} ({found['what']}) for {issue_repo}#{num}. Building {waiting} next.")
    elif found and not w["warned"] & WARN_FAILED:
        set_state(db, issue_repo, num, "publish", repo, warned=w["warned"] | WARN_FAILED)
        emit("waves:publish-failed", f"{short(repo)}: {found['what']}", issue_repo, num)
        notify(f"Publishing {repo} for {issue_repo}#{num} failed ({found['what']}): {found['url']}\n"
               f"The factory keeps waiting for a successful publish before building {waiting}.", "failure")
    elif time.time() - w["merged_at"] > pub.timeout_hours * 3600 and not w["warned"] & WARN_LATE:
        set_state(db, issue_repo, num, "publish", repo, warned=w["warned"] | WARN_LATE)
        emit("waves:late", f"{short(repo)} not published {pub.timeout_hours}h after the merge", issue_repo, num)
        notify(f"{repo}#{pr_num} was merged {pub.timeout_hours}h ago but no {how(pub)} has appeared. {issue_repo}#{num} is still "
               f"waiting to build {waiting}.", "needs_human")
