# AnyTLS + REALITY fork

This fork adds AnyTLS REALITY to Mihomo by connecting the existing REALITY TLS implementation. It does not add a new protocol or Xray dependency. Upstream Mihomo does not support this combination.

The `anytls-reality` branch maintains the small runtime patch and release automation. Each published tag contains the exact upstream stable source plus this patch and records its provenance in `.anytls-build.json`; release `build-info.json` also records the patched source SHA.

Every hour, at minute 17, Actions checks for an upstream stable release. It strictly applies the patch, builds the desktop cores, runs TCP/UDP loopback and invalid-credential tests, and only then publishes a complete release. Patch conflicts or failed tests stop publication. Rerun the workflow to retry a failed build. An empty maintenance commit after 30 days keeps GitHub's 60-day inactivity rule from disabling the schedule; GitHub scheduling can still be delayed. No cross-repository token is required.

An existing published release is skipped only when all ten core archives and three metadata files are present, uploaded and nonempty. An unpublished source tag is reusable only when its upstream/fork identity and source-input fingerprint match the current patch, injected tests, and publishing scripts executed from that tag. If those inputs change after a failed build, the workflow stops for an explicit backup and recreation of that failed tag; it never silently moves a tag or overwrites a public release. Documentation-only changes do not invalidate the source fingerprint. Existing public releases do not need a new fingerprint.

```yaml
proxies:
  - name: AnyTLS REALITY
    type: anytls
    server: node.example.com
    port: 443
    password: "your-password"
    udp: true
    sni: camouflage.example.com
    client-fingerprint: chrome
    reality-opts:
      public-key: "your-REALITY-public-key"
      short-id: "0123456789abcdef"
```

Do not mix REALITY with ECH, ShadowTLS, Restls or JLS. Private keys belong only on the server. A successful `-t` alone does not prove REALITY connectivity; upstream cores may ignore the fields.

Runtime core updates, including requests for the Alpha channel, stay on this fork's stable releases. Windows 7 uses the dedicated Go 1.20 asset and keeps that variant during updates. Other desktop assets follow the MetaCubeX Go toolchain from upstream CI. Win7 and macOS compatibility are build targets, not claims of OS-level installation tests.

The initial loopback check uses a local TLS decoy and seeded post-handshake record profile. It verifies cryptographic authentication and forwarding, not public decoy compatibility, censorship resistance, performance or production deployment.

Original licenses and attribution are retained. This repository is independently maintained and is not an official MetaCubeX release.
