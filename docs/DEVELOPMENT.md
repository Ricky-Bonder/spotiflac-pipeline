# Development

## Local layout

```
spotiflac-pipeline/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── .gitignore
├── config.example.env             # documented config reference
├── install.sh                     # idempotent bootstrap
├── patches/
│   ├── README.md
│   └── spotiflac-0.5.1-link-resolver.patch
├── bin/                           # user-facing scripts (cron + manual)
│   ├── _common.sh                 # shared config loader for shell scripts
│   ├── _common.py                 # shared config loader for python scripts
│   ├── run_all.sh
│   ├── spotiflac-watchdog.sh
│   ├── migrate-to-flat.py
│   ├── verify-and-cleanup.py
│   ├── spotify-diff.py
│   ├── audit-spotdl.py
│   ├── redownload-spotdl.py
│   └── dedup-tracks.py
├── docs/
│   ├── ARCHITECTURE.md
│   ├── INSTALL.md
│   ├── CONFIGURATION.md
│   ├── TROUBLESHOOTING.md
│   └── DEVELOPMENT.md            # you are here
└── examples/
    ├── crontab.example
    └── playlists.txt.example
```

## Setting up a dev environment

```bash
git clone https://github.com/Ricky-Bonder/spotiflac-pipeline
cd spotiflac-pipeline
./install.sh

# Point the pipeline at a sandbox so you don't touch your real library
mkdir -p /tmp/spf-dev/{music,state,venv}
cat > ~/.config/spotiflac-pipeline/spotiflac.env <<EOF
SPOTIFLAC_MUSIC_ROOT="/tmp/spf-dev/music"
SPOTIFLAC_STATE_DIR="/tmp/spf-dev/state"
EOF
```

Test against a small public playlist with a few tracks. The
`Liked Songs Today` or `Top 50 — <country>` playlists are easy targets.

## Running scripts directly

Every script has a `__main__` guard and can be run standalone:

```bash
bin/spotify-diff.py
bin/verify-and-cleanup.py          # verify only
bin/verify-and-cleanup.py --clean  # verify + clean
bin/migrate-to-flat.py
bin/dedup-tracks.py                # dry run (default)
bin/dedup-tracks.py --do           # actually move files
bin/audit-spotdl.py
bin/redownload-spotdl.py --limit 5 --dry-run
```

The shell scripts (`run_all.sh`, `spotiflac-watchdog.sh`) source
`bin/_common.sh` automatically. The Python scripts add `bin/` to
`sys.path` so they can import `_common.py` directly.

## Tests

The `tests/` directory holds pytest cases for the pure-logic parts:

- `tests/test_audit_match.py` — `audit-spotdl.py`'s `norm`, `fname_parse`,
  and `find_match` (filename → playlist-track fuzzy matching)
- `tests/test_dedup_priority.py` — `dedup-tracks.py`'s `keeper_sort_key`
  (verified-good > FLAC > MP3 > M4A > bitrate > size)

Run locally:

```bash
pip install pytest
pytest tests/ -v
```

GitHub Actions runs the same suite under Python 3.10 and 3.12 on every push
and PR — see `.github/workflows/ci.yml`.

The `bin/*.py` files have hyphens in their names (so they're invokable as
CLI commands), so plain `import` doesn't work. `tests/conftest.py` provides
`audit` and `dedup` fixtures that load them via `importlib`.

These tests intentionally cover only the pure-logic pieces. Anything that
touches the filesystem, network, or external binaries (ffprobe, yt-dlp,
spotiflac) is integration territory and stays out of CI for now. Add an
integration-test harness when the project grows enough to warrant it.

## Style

- Python: stdlib-only where reasonable; we use `urllib`, `re`, `subprocess`,
  `pathlib`, `json`. No external deps beyond what `install.sh` puts in the
  venv (spotiflac, yt-dlp).
- Shell: bash 4+, `set -u`. Quote variable expansions. Prefer
  `$(command)` over backticks.
- Paths: always derived from `_common`, never hardcoded.
- Comments: explain *why*, not *what*. Date them when they reference a
  specific incident or upstream change.

## Adding a new config key

1. Add a documented entry to `config.example.env` (comment lines starting
   with `#`, then the `#KEY=default` line)
2. Add a loader line to `bin/_common.sh` (`KEY="${KEY:-default}"`)
3. Add a loader line to `bin/_common.py` (`_env_*(...)`)
4. Update the corresponding section in `docs/CONFIGURATION.md`
5. Note the change in `CHANGELOG.md` under *Unreleased*

## Filing a PR

1. Branch off `main`
2. Make sure `bin/*.py` parse: `for f in bin/*.py; do python3 -m py_compile "$f"; done`
3. Make sure `bin/*.sh` parse: `for f in bin/*.sh; do bash -n "$f"; done`
4. Update `CHANGELOG.md` under `[Unreleased]`
5. Open a PR with a description of *why* (the *what* should be in the diff)

## Upstreaming the spotiflac patch

The `link_resolver.py` patch should eventually become a PR against the
upstream [spotiflac](https://pypi.org/project/spotiflac/) repo. If you have
contact with the maintainer or want to file it yourself, the patch is
self-contained — it only changes one file.

## Releases

Tag releases as `vMAJOR.MINOR.PATCH`. The `CHANGELOG.md` entry's section
header should match the tag (without the `v`). Push the tag; GitHub picks
it up as a Release.
