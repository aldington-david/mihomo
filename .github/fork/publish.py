import hashlib
import json
import os
from pathlib import Path
import subprocess

from prepare import asset_names, check_published_assets, release_by_tag

tag, sha = os.environ["TAG"], os.environ["SOURCE_SHA"]
repository = os.environ["GITHUB_REPOSITORY"]
dist = Path("dist")
names = asset_names(tag)
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
existing = release_by_tag(repository, tag)
if existing and not existing["draft"]:
    raise RuntimeError("Refusing to overwrite a published release")
if not existing:
    subprocess.run(["gh", "release", "create", tag, "--repo", repository, "--verify-tag", "--draft",
                    "--title", f"{tag} — AnyTLS + REALITY", "--notes-file", "release-notes.md"], check=True)
subprocess.run(["gh", "release", "upload", tag, "--repo", repository, "--clobber",
                *[str(path) for path in sorted(dist.iterdir())]], check=True)
uploaded = release_by_tag(repository, tag)
if not uploaded or not uploaded["draft"]:
    raise RuntimeError("Expected a draft release before publication")
check_published_assets(uploaded, tag)
subprocess.run(["gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest"], check=True)
