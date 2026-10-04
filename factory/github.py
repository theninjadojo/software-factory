import json
import re
import urllib.error
import urllib.parse
import urllib.request


STEP_TITLE_PREFIX = "[factory] "


class GitHub:
    """Small GitHub client. Writes are limited to: comments, labels, pull requests, and the step sub-issues the
    factory creates itself (fixed title prefix, no labels or assignees)."""

    def __init__(self, token: str | None):
        self.token = token

    def _req(self, method: str, path: str, data: dict | None = None):
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "shikumi"},
        )
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            return json.loads(body) if body else None

    def raw_file(self, repo: str, path: str, ref: str, max_bytes: int = 4_000_000) -> bytes:
        """One file of a repository at a commit, as bytes (works for private repositories, with the token)."""
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}",
            headers={"Accept": "application/vnd.github.raw+json", "User-Agent": "shikumi"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("file too large")
        return data

    def _get(self, path: str):
        return self._req("GET", path)

    def labeled_issues(self, repo: str, label: str) -> list[dict]:
        q = urllib.parse.urlencode({"labels": label, "state": "open", "per_page": 50})
        return [i for i in self._get(f"/repos/{repo}/issues?{q}") if "pull_request" not in i]

    def issues(self, repo: str, state: str = "open", label: str | None = None, page: int = 1, per_page: int = 50) -> tuple[list[dict], bool]:
        """One page of issues (pull requests dropped) and whether GitHub may have more."""
        q = {"state": state, "per_page": per_page, "page": page, **({"labels": label} if label else {})}
        raw = self._get(f"/repos/{repo}/issues?{urllib.parse.urlencode(q)}")
        return [i for i in raw if "pull_request" not in i], len(raw) == per_page

    def search_issues(self, repo: str, text: str, state: str = "open", page: int = 1, per_page: int = 50) -> tuple[list[dict], bool]:
        items, more, _ = self.search_page(repo, text, state, page, per_page)
        return items, more

    def search_page(self, repo: str, text: str = "", state: str = "open", page: int = 1, per_page: int = 50, labels: tuple = (), label: str = "") -> tuple[list[dict], bool, int]:
        """One page of a search, with GitHub's total match count. Free text only: a ':' would let the caller add search qualifiers
        (repo:, user:, org:...) and read other repositories. `labels` (any of) and `label` come from the caller's code or are cleaned here."""
        text = re.sub(r"[^\w\s#.-]", " ", text)[:100].strip()
        term = f"repo:{repo} is:issue" + (f" in:title {text}" if text else "") + ("" if state == "all" else f" is:{state}")
        clean = lambda v: re.sub(r'[^\w .:/-]', "", v)[:50]
        if label:
            term += f' label:"{clean(label)}"'
        if labels:
            term += " label:" + ",".join(f'"{clean(v)}"' for v in labels)
        got = self._get("/search/issues?" + urllib.parse.urlencode({"q": term, "per_page": per_page, "page": page, "sort": "updated", "order": "desc"}))
        raw = got["items"]
        return raw, len(raw) == per_page, int(got.get("total_count", len(raw)))

    def count_issues(self, repo: str, state: str = "open", label: str = "") -> int:
        return self.search_page(repo, "", state, 1, 1, label=label)[2]

    def repo_labels(self, repo: str, max_pages: int = 5) -> list[dict]:
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            got = self._get(f"/repos/{repo}/labels?per_page=100&page={page}")
            out += got
            if len(got) < 100:
                break
        return out

    def label_actor(self, repo: str, issue: int, label: str) -> str | None:
        events = self._get(f"/repos/{repo}/issues/{issue}/events?per_page=100")
        actors = [
            e["actor"]["login"]
            for e in events
            if e.get("event") == "labeled" and e["label"]["name"] == label and e.get("actor")
        ]
        return actors[-1] if actors else None

    def permission(self, repo: str, login: str) -> str:
        try:
            return self._get(f"/repos/{repo}/collaborators/{urllib.parse.quote(login)}/permission")["permission"]
        except urllib.error.HTTPError:
            return "none"

    def get_issue(self, repo: str, issue: int) -> dict:
        return self._get(f"/repos/{repo}/issues/{issue}")

    def issue_comments(self, repo: str, issue: int) -> list[dict]:
        return self._get(f"/repos/{repo}/issues/{issue}/comments?per_page=30")

    def get_pr(self, repo: str, number: int) -> dict:
        return self._get(f"/repos/{repo}/pulls/{number}")

    def check_runs(self, repo: str, sha: str) -> list[dict]:
        """CI results for a commit. Uses the Checks API when the token may read it; otherwise falls back to the GitHub Actions
        API (workflow jobs), which needs only 'Actions: read' and carries the same status, conclusion and job id."""
        try:
            return self._get(f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")["check_runs"]
        except urllib.error.HTTPError as e:
            if e.code not in (403, 404):
                raise
        return self._actions_jobs(repo, sha)

    def _actions_jobs(self, repo: str, sha: str) -> list[dict]:
        items = []
        for run in self._get(f"/repos/{repo}/actions/runs?head_sha={sha}&per_page=30")["workflow_runs"]:
            jobs = self._get(f"/repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]
            for j in jobs:
                items.append({"id": j["id"], "name": f"{run['name']} / {j['name']}", "status": j["status"],
                              "conclusion": j.get("conclusion"), "html_url": j.get("html_url"), "output": {"title": ""}})
            if not jobs:                                  # a run that has queued but not produced jobs yet is still pending
                items.append({"id": run["id"], "name": run["name"], "status": run["status"], "conclusion": run.get("conclusion"),
                              "html_url": run.get("html_url"), "output": {"title": ""}})
        return items

    def commit_statuses(self, repo: str, sha: str) -> list[dict]:
        """External (non-Actions) commit statuses. Optional: a token without 'Commit statuses: read' simply sees none."""
        try:
            return self._get(f"/repos/{repo}/commits/{sha}/status")["statuses"]
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return []
            raise

    def job_log_tail(self, repo: str, job_id: int, chars: int = 6000) -> str | None:
        """Tail of an Actions job log. GitHub answers with a redirect to a signed URL; follow it WITHOUT our credentials."""
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs",
                                     headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                                              "User-Agent": "shikumi"})
        try:
            urllib.request.build_opener(NoRedirect).open(req, timeout=20)
            return None
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
                return None
            with urllib.request.urlopen(e.headers["Location"], timeout=30) as r:
                return r.read().decode("utf-8", "replace")[-chars:]

    def default_branch(self, repo: str) -> str:
        return self._get(f"/repos/{repo}")["default_branch"]

    def branch_sha(self, repo: str, branch: str) -> str:
        return self._get(f"/repos/{repo}/commits/{urllib.parse.quote(branch, safe='')}")["sha"]

    def comment(self, repo: str, issue: int, body: str) -> str:
        return self._req("POST", f"/repos/{repo}/issues/{issue}/comments", {"body": body})["html_url"]

    def login(self) -> str:
        """Our own account; only comments authored by it are trusted as stage outputs."""
        if not getattr(self, "_login", None):
            self._login = self._get("/user")["login"]
        return self._login

    def add_labels(self, repo: str, issue: int, labels: list[str]) -> None:
        self._req("POST", f"/repos/{repo}/issues/{issue}/labels", {"labels": labels})

    def create_label(self, repo: str, name: str, color: str = "ededed", description: str = "") -> None:
        """Create a repository label if it does not exist yet (GitHub answers 422 when it does)."""
        try:
            self._req("POST", f"/repos/{repo}/labels", {"name": name, "color": color, "description": description[:100]})
        except urllib.error.HTTPError as e:
            if e.code != 422:
                raise

    def remove_label(self, repo: str, issue: int, label: str) -> None:
        try:
            self._req("DELETE", f"/repos/{repo}/issues/{issue}/labels/{urllib.parse.quote(label, safe='')}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

    def create_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool = False) -> str:
        return self._req("POST", f"/repos/{repo}/pulls", {"head": head, "base": base, "title": title, "body": body, "draft": draft})["html_url"]

    def mark_ready(self, repo: str, number: int) -> bool:
        """Take a draft PR out of draft. Only for a PR on a factory/ branch; a PR that is not a draft is left alone."""
        pr = self.get_pr(repo, number)
        if not pr.get("draft") or not pr["head"]["ref"].startswith("factory/"):
            return False
        out = self._req("POST", "/graphql", {"query": "mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) "
                                                      "{ pullRequest { id } } }", "variables": {"id": pr["node_id"]}})
        if out.get("errors"):
            raise RuntimeError(f"markPullRequestReadyForReview failed: {out['errors']}")
        return True

    def create_issue(self, repo: str, title: str, body: str) -> dict:
        """Only for step sub-issues: the title must carry the factory prefix, and labels/assignees cannot be set."""
        if not title.startswith(STEP_TITLE_PREFIX):
            raise ValueError("factory issues must carry the step title prefix")
        return self._req("POST", f"/repos/{repo}/issues", {"title": title, "body": body})

    def create_ticket(self, repo: str, title: str, body: str, labels: list[str] | None = None) -> dict:
        """A person's new ticket (from the UI). Sends no assignees. It carries a label only when the caller passes one: the UI
        passes at most one, taken from its server-side list of start actions, so nothing starts unless a person chose it."""
        data = {"title": title, "body": body}
        if labels:
            data["labels"] = list(labels)
        return self._req("POST", f"/repos/{repo}/issues", data)

    def create_scheduled_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict:
        """A ticket opened by an admin-configured schedule (see schedules.py). Unlike create_ticket it carries the labels the
        admin chose, normally the auto label; assignees are never set."""
        return self._req("POST", f"/repos/{repo}/issues", {"title": title, "body": body, "labels": list(labels)})

    def update_issue(self, repo: str, number: int, body: str | None = None, state: str | None = None) -> dict:
        data = {k: v for k, v in (("body", body), ("state", state)) if v is not None}
        if state == "closed":
            data["state_reason"] = "completed"
        return self._req("PATCH", f"/repos/{repo}/issues/{int(number)}", data)

    def add_sub_issue(self, repo: str, parent: int, sub_id: int) -> None:
        self._req("POST", f"/repos/{repo}/issues/{int(parent)}/sub_issues", {"sub_issue_id": int(sub_id)})
