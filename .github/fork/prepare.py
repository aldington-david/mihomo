"""Prepare an auditable patched source tag from an official stable release."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UPSTREAM = "MetaCubeX/mihomo"


def api(path):
    request = Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "User-Agent": "anytls-release-sync",
    })
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise


def git(*args, cwd=None):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def stable_tag(release):
    if not release or release.get("draft") or release.get("prerelease"):
        raise ValueError("An official, published stable release is required")
    tag = release["tag_name"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Unsupported stable version format: " + tag)
    return tag


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for name, value in values.items():
            stream.write(f"{name}={value}\n")


def main():
    control = Path.cwd()
    repository = os.environ["GITHUB_REPOSITORY"]
    requested = os.environ.get("UPSTREAM_TAG", "")
    if requested and not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", requested):
        raise ValueError("Invalid upstream tag")
    release = api(f"repos/{UPSTREAM}/releases/" + ("tags/" + requested if requested else "latest"))
    tag = stable_tag(release)
    existing = api(f"repos/{repository}/releases/tags/{tag}")
    if existing and not existing["draft"]:
        output(needed="false", tag=tag)
        print("Already published:", tag)
        return
    ref = api(f"repos/{repository}/git/ref/tags/{tag}")
    if ref:
        record = api(f"repos/{repository}/contents/.anytls-build.json?ref={tag}")
        if not record:
            raise RuntimeError("Existing tag has no custom build provenance; refusing to reuse it")
        output(needed="true", tag=tag, sha=ref["object"]["sha"])
        return
    source = control / "build-source"
    git("clone", "--no-checkout", "--filter=blob:none", "--no-tags", "--depth", "1", f"https://github.com/{UPSTREAM}.git", str(source))
    git("fetch", "--no-tags", "--depth", "1", "origin", "refs/tags/" + tag, cwd=source)
    git("checkout", "--detach", "FETCH_HEAD", cwd=source)
    upstream_sha = git("rev-parse", "HEAD", cwd=source)
    git("apply", "--check", str(control / ".github/fork/source.patch"), cwd=source)
    git("apply", str(control / ".github/fork/source.patch"), cwd=source)
    for name, target in [("anytls_reality_test.go", "adapter/outbound/anytls_reality_test.go"), ("fork_test.go", "component/updater/fork_test.go")]:
        shutil.copy2(control / ".github/fork" / name, source / target)
    shutil.rmtree(source / ".github/workflows")
    shutil.copytree(control / ".github/workflows", source / ".github/workflows")
    shutil.copytree(control / ".github/fork", source / ".github/fork", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(control / "tests/cli", source / "tests/cli", dirs_exist_ok=True)
    shutil.copy2(control / "ANYTLS-REALITY.md", source / "ANYTLS-REALITY.md")
    record = {"upstream_repository": UPSTREAM, "upstream_tag": tag, "upstream_sha": upstream_sha,
              "anytls_reality": True, "automation_sha": os.environ["GITHUB_SHA"],
              "runtime_update_repository": repository}
    (source / ".anytls-build.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    git("config", "user.name", "github-actions[bot]", cwd=source)
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=source)
    git("add", "adapter/outbound/anytls.go", "adapter/outbound/anytls_reality_test.go", "component/updater", ".github", "tests/cli", ".anytls-build.json", "ANYTLS-REALITY.md", cwd=source)
    git("-c", "commit.gpgsign=false", "commit", "-m", f"Build {tag} with AnyTLS REALITY and private fork update source", cwd=source)
    sha = git("rev-parse", "HEAD", cwd=source)
    # setup-git uses the runner's ephemeral token, never a token-bearing remote URL.
    subprocess.run(["gh", "auth", "setup-git"], check=True)
    git("push", f"https://github.com/{repository}.git", f"HEAD:refs/tags/{tag}", cwd=source)
    output(needed="true", tag=tag, sha=sha)


if __name__ == "__main__":
    main()
