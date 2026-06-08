# patches/ — historical

> This patch is **legacy**. The current `install.sh` pins spotiflac to
> `>=0.6.9,<0.9`, which has a rewritten `link_resolver.py` that no longer
> exhibits the bug fixed below. Kept here as a historical artifact and a
> reference for anyone running pinned to the 0.5.x line.

## `spotiflac-0.5.1-link-resolver.patch`

Patches `SpotiFLAC/core/link_resolver.py` in **spotiflac 0.5.x**.

### What it fixes

Upstream called Odesli (api.song.link) with `?id=<raw_id>&platform=<platform>`,
which Odesli deprecated in 2026. The call returns `HTTP 400
invalid_entity_type` and every resolution attempt fails. The patch rebuilds a
canonical track URL per platform and submits it via `?url=…` instead — the
form Odesli still supports.

### Why it's no longer applied automatically

The author of spotiflac released 0.6.9 with a complete rewrite of
`link_resolver.py` (multi-provider Go-style implementation with direct
Deezer ISRC API calls, Amazon URL normalization, etc.). The HTTP 400 bug
went away as a side-effect of that rewrite. `install.sh` no longer applies
this patch — it just installs from the post-rewrite version range directly.

### Apply manually (only if you have a reason to pin to 0.5.x)

```bash
pip install "spotiflac>=0.5.1,<0.6"
SITE=$(python -c "import SpotiFLAC, os; print(os.path.dirname(os.path.dirname(SpotiFLAC.__file__)))")
cd "$SITE"
patch -p1 < /path/to/patches/spotiflac-0.5.1-link-resolver.patch
```

### Reasons you might want to pin to 0.5.x anyway

- The 0.6.9+ rewrite changed provider semantics (Deezer now via direct ISRC
  API instead of the `api.zarz.moe` proxy). If you specifically need the
  zarz.moe path, you want the 0.5.x line.
- Larger install footprint in 0.7.x+ (pulls in pywebview and other GUI deps,
  even when you only use the CLI).
