# third_party/

Everything here except this file and `manifest.yml` is **downloaded by the installer and ignored by git**.
Nothing from these projects is vendored into the CLIP MASTER repository.

- `manifest.yml` lists every external tool: name, kind, URL, pinned ref (tag or commit), license and purpose.
- `./install.sh` / `install.ps1` fetch the `binary` entries (checksum verified) and, with `--with-reference`, the reference-only `git` entries.
- `./install.sh --check` (or `clip-master doctor`) reports what is installed.
- To bump a pin: change `ref` (and the `sha256_*` values for binaries) in `manifest.yml`, run the installer again.

Licenses: see `NOTICE.md`. Projects without a license (for example `remotion-dev/skills`) are deliberately not fetched.
