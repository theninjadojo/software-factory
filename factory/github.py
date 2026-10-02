import json
import urllib.error
import urllib.parse
import urllib.request


class GitHub:
    """Small GitHub client. Writes are limited to: comments, labels, pull requests."""

    def __init__(self, token: str | None):
        self.token = token

    def _req(self, method: str, path: str, data: dict | None = None):
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "software-factory"},
        )
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            return json.loads(body) if body else None

    def _get(self, path: str):
        return self._req("GET", path)

    def labeled_issues(self, repo: str, label: str) -> list[dict]:
        q = urllib.parse.urlencode({"labels": label, "state": "open", "per_page": 50})
        return [i for i in self._get(f"/repos/{repo}/issues?{q}") if "pull_request" not in i]

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
        return self._get(f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")["check_runs"]

    def commit_statuses(self, repo: str, sha: str) -> list[dict]:
        return self._get(f"/repos/{repo}/commits/{sha}/status")["statuses"]

    def job_log_tail(self, repo: str, job_id: int, chars: int = 6000) -> str | None:
        """Tail of an Actions job log. GitHub answers with a redirect to a signed URL; follow it WITHOUT our credentials."""
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs",
                                     headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                                              "User-Agent": "software-factory"})
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

    def comment(self, repo: str, issue: int, body: str) -> str:
        return self._req("POST", f"/repos/{repo}/issues/{issue}/comments", {"body": body})["html_url"]

    def login(self) -> str:
        """Our own account; only comments authored by it are trusted as stage outputs."""
        if not getattr(self, "_login", None):
            self._login = self._get("/user")["login"]
        return self._login

    def add_labels(self, repo: str, issue: int, labels: list[str]) -> None:
        self._req("POST", f"/repos/{repo}/issues/{issue}/labels", {"labels": labels})

    def remove_label(self, repo: str, issue: int, label: str) -> None:
        try:
            self._req("DELETE", f"/repos/{repo}/issues/{issue}/labels/{urllib.parse.quote(label, safe='')}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

    def create_pr(self, repo: str, head: str, base: str, title: str, body: str) -> str:
        return self._req("POST", f"/repos/{repo}/pulls", {"head": head, "base": base, "title": title, "body": body})["html_url"]
