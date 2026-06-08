# patches/

## `spotiflac-0.5.1-link-resolver.patch`

Patches `SpotiFLAC/core/link_resolver.py` in the spotiflac 0.5.1 package.

### What it fixes

Upstream calls Odesli (api.song.link) with `?id=<raw_id>&platform=<platform>`,
which Odesli deprecated. As of mid-2026 that form returns `HTTP 400
invalid_entity_type` and resolution fails for every track. The patch rebuilds a
canonical track URL per platform and submits it via `?url=…` instead — the form
Odesli still supports.

### Apply

`install.sh` applies it automatically once the venv is built. To apply manually:

```bash
SITE=$($SPOTIFLAC_VENV/bin/python3 -c \
  "import SpotiFLAC, os; print(os.path.dirname(SpotiFLAC.__file__))")
cd "$(dirname "$SITE")"
patch -p1 < /path/to/patches/spotiflac-0.5.1-link-resolver.patch
```

### Upstream

This patch should eventually become a PR against the upstream spotiflac repo;
contributions welcome. Once upstream merges an equivalent fix, this patch
becomes a no-op and `install.sh` will skip it.
