"""Prepare an auditable patched source tag from an official stable release."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UPSTREAM = "MetaCubeX/mihomo"
SOURCE_INPUTS = (
    ".github/fork/prepare.py", ".github/fork/publish.py",
    ".github/fork/source.patch", ".github/fork/anytls_reality_test.go",
    ".github/fork/fork_test.go", "tests/cli/cli_windows_test.go",
    "tests/cli/go.mod", "tests/cli/go.sum",
)


def asset_names(tag, metadata=False):
    names = [f"mihomo-{target}-{tag}.{extension}" for target, extension in [
        ("linux-amd64-v1", "gz"), ("linux-amd64-compatible", "gz"), ("linux-arm64", "gz"),
        ("darwin-amd64-v1", "gz"), ("darwin-amd64-compatible", "gz"), ("darwin-arm64", "gz"),
        ("windows-amd64-v1", "zip"), ("windows-amd64-compatible", "zip"),
        ("windows-amd64-v1-go120", "zip"), ("windows-amd64-compatible-go120", "zip"),
    ]]
    return names + (["build-info.json", "version.txt", "sha256sum.txt"] if metadata else [])


def check_published_assets(release, tag):
    assets = release.get("assets", [])
    if sorted(item["name"] for item in assets) != sorted(asset_names(tag, metadata=True)):
        raise RuntimeError("Published release has missing or unexpected assets; refusing to skip or overwrite it")
    if any(item.get("size", 0) <= 0 or item.get("state") != "uploaded" for item in assets):
        raise RuntimeError("Published release has empty or incomplete assets; refusing to skip or overwrite it")


def source_fingerprint(control):
    digest = hashlib.sha256()
    for name in SOURCE_INPUTS:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((control / name).read_text(encoding="utf-8").encode("utf-8") + b"\0")
    return digest.hexdigest()


def check_existing_source(contents, tag, repository, fingerprint):
    try:
        if not contents or contents.get("encoding") != "base64":
            raise ValueError("Missing source provenance")
        record = json.loads(base64.b64decode(contents["content"]))
        if not isinstance(record, dict) or record.get("anytls_reality") is not True:
            raise ValueError("Invalid source provenance")
        expected = {"upstream_repository": UPSTREAM, "upstream_tag": tag,
                    "anytls_reality": True, "runtime_update_repository": repository,
                    "source_fingerprint": fingerprint}
        if any(record.get(key) != value for key, value in expected.items()):
            raise ValueError("Source provenance does not match this build")
        if not re.fullmatch(r"[0-9a-f]{40}", record.get("upstream_sha", "")):
            raise ValueError("Invalid upstream source commit")
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Unpublished tag has missing or stale source provenance; "
                           "back up and recreate the failed tag before retrying") from error


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


def release_by_tag(repository, tag):
    result = subprocess.run(["gh", "release", "view", tag, "--repo", repository, "--json", "apiUrl"],
                            capture_output=True, text=True)
    if result.returncode:
        if result.stderr.strip() == "release not found":
            return None
        raise RuntimeError(result.stderr)
    release_id = int(json.loads(result.stdout)["apiUrl"].rsplit("/", 1)[1])
    return api(f"repos/{repository}/releases/{release_id}")


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
        check_published_assets(existing, tag)
        output(needed="false", tag=tag)
        print("Already published:", tag)
        return
    ref = api(f"repos/{repository}/git/ref/tags/{tag}")
    fingerprint = source_fingerprint(control)
    if ref:
        record = api(f"repos/{repository}/contents/.anytls-build.json?ref={tag}")
        check_existing_source(record, tag, repository, fingerprint)
        if ref["object"]["type"] != "commit":
            raise RuntimeError("Existing source tag must point directly to its generated commit")
        output(needed="true", tag=tag, sha=ref["object"]["sha"])
        return
    source = control / "build-source"
    git("clone", "--no-checkout", "--filter=blob:none", "--no-tags", f"https://github.com/{UPSTREAM}.git", str(source))
    git("fetch", "--no-tags", "origin", "refs/tags/" + tag, cwd=source)
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
              "runtime_update_repository": repository, "source_fingerprint": fingerprint}
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
