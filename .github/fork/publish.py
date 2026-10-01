import hashlib
import json
import os
from pathlib import Path
import subprocess

from prepare import api

tag, sha = os.environ["TAG"], os.environ["SOURCE_SHA"]
repository = os.environ["GITHUB_REPOSITORY"]
dist = Path("dist")
names = [f"mihomo-{target}-{tag}.{extension}" for target, extension in [
    ("linux-amd64-v1", "gz"), ("linux-amd64-compatible", "gz"), ("linux-arm64", "gz"),
    ("darwin-amd64-v1", "gz"), ("darwin-amd64-compatible", "gz"), ("darwin-arm64", "gz"),
    ("windows-amd64-v1", "zip"), ("windows-amd64-compatible", "zip"),
    ("windows-amd64-v1-go120", "zip"), ("windows-amd64-compatible-go120", "zip"),
]]
if sorted(path.name for path in dist.iterdir()) != sorted(names):
    raise RuntimeError("Missing or unexpected build artifacts")
record = json.loads(Path(".anytls-build.json").read_text())
record.update(source_sha=sha, release_tag=tag, artifacts=sorted(names),
              integration="REALITY TCP and UDP; TLS regression; wrong credentials rejected")
(dist / "build-info.json").write_text(json.dumps(record, indent=2) + "\n")
(dist / "version.txt").write_text(tag + "\n")
checksums = []
for path in sorted(dist.iterdir()):
    if path.stat().st_size == 0:
        raise RuntimeError("Empty asset: " + path.name)
    checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name)
(dist / "sha256sum.txt").write_text("\n".join(checksums) + "\n")
notes = (f"Custom AnyTLS + REALITY core based on {record['upstream_repository']} {tag}.\n\n"
         f"Upstream commit: `{record['upstream_sha']}`\nPatched source: `{sha}`\n\n"
         "TCP/UDP loopback integration and negative authentication checks passed. "
         "Windows go120 assets retain Windows 7's Go 1.20 baseline; OS execution was not tested on Windows 7. "
         "Clients must download only from this fork. This is not an upstream official build.\n")
Path("release-notes.md").write_text(notes)
existing = api(f"repos/{repository}/releases/tags/{tag}")
if existing and not existing["draft"]:
    raise RuntimeError("Refusing to overwrite a published release")
if not existing:
    subprocess.run(["gh", "release", "create", tag, "--repo", repository, "--verify-tag", "--draft",
                    "--title", f"{tag} — AnyTLS + REALITY", "--notes-file", "release-notes.md"], check=True)
subprocess.run(["gh", "release", "upload", tag, "--repo", repository, "--clobber",
                *[str(path) for path in sorted(dist.iterdir())]], check=True)
subprocess.run(["gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest"], check=True)
