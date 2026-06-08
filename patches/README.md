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

### Upstream status

Upstream has since released **spotiflac 1.0.0** with a completely rewritten
`link_resolver.py` (multi-provider Go-style implementation with Deezer ISRC
lookup, Amazon URL normalization, etc.). The bug fixed by this patch is
resolved as a side-effect of that rewrite — meaning this patch is **only
relevant for users pinned to spotiflac 0.5.x**.

`install.sh` currently pins to `spotiflac>=0.5.1,<0.6` for reproducibility
with the library this project was built against. Migrating to 1.0.0 is on
the roadmap (see `CHANGELOG.md` under *Unreleased*) but requires testing
because the 1.0.0 API surface and provider semantics differ.

If you're starting fresh, you can try `pip install "spotiflac>=1.0.0"`
manually — this patch is then unnecessary and `install.sh` will skip
applying it (the marker `_PLATFORM_URL_TEMPLATES` won't be present, but the
patch's `patch --dry-run` check will also fail, and the script logs that
case).
