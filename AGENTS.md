## Cursor Cloud specific instructions

This repository is **GIMP** (GNU Image Manipulation Program): a native **Meson + Ninja** desktop app, not a Node/web stack. Official contributor docs: https://developer.gimp.org/core/setup/build/

### Services (runtime)

| Component | Role |
|-----------|------|
| **Built GIMP** (`_build-local/app/gimp-3.3`, `gimp-console-3.3`) | Main GUI / batch binary when developing from source |
| **babl + GEGL** (built into `_install/`) | Hard dependencies; upstream CI builds them from GNOME git because distro versions are often too old |
| **Xvfb + dbus-run-session** | Required for Meson UI tests and headless GUI runs (see `tools/run_test_env.sh`) |

There is no long-running server to keep up—only build artifacts and optional Xvfb for tests.

### One-time setup (Ubuntu/Debian)

1. **Submodule:** `git submodule update --init gimp-data` (required for build).
2. **System packages:** Install build/runtime deps from `.gitlab-ci.yml` (`deps-debian-nonreloc` list). On Ubuntu 24.04, `libbacktrace-dev` is unavailable—skip it. Install `g++` if GEGL’s Meson configure fails on C++.
3. **babl & GEGL:** Clone `https://gitlab.gnome.org/GNOME/babl.git` and `gegl.git` next to the repo (siblings under `/workspace`), then:
   - `source tools/setup_gimp_env.sh`
   - `meson setup babl/_build-local babl -Dprefix="$GIMP_PREFIX"` → `ninja -C babl/_build-local install`
   - `meson setup gegl/_build-local gegl -Dprefix="$GIMP_PREFIX" -Dcairo=enabled -Dintrospection=true` → `ninja -C gegl/_build-local install`
4. **GIMP:** `source tools/setup_gimp_env.sh` then `meson setup _build-local -Dprefix="$GIMP_PREFIX"` and `ninja -C _build-local`.

`tools/setup_gimp_env.sh` sets `GIMP_PREFIX` (default `/workspace/_install`) and `PKG_CONFIG_PATH` / `LD_LIBRARY_PATH` like CI’s `ENVIRON` anchor in `.gitlab-ci.yml`.

### Build / test / run

| Task | Command (after `source tools/setup_gimp_env.sh`) |
|------|--------------------------------------------------|
| Build | `ninja -C _build-local` |
| Tests | `ninja -C _build-local test` (uses Xvfb + D-Bus via Meson `headless` setup) |
| In-build batch/API tests | Use `tools/in-build-gimp.py` as the GIMP executable with `libgimp/tests/libgimp-run-python-test.py` (see Meson `gimp_run_env` in `meson.build`). **Do not** set `GIMP_TESTING_PLUGINDIRS` manually. |
| GUI (headless) | `xvfb-run --auto-servernum dbus-run-session -- tools/in-build-gimp.py -- …` with the same env vars as tests (`GIMP_SELF_IN_BUILD`, `GIMP_GLOBAL_*`, `GIMP_TESTING_*`). |

### Gotchas

- **Never** point batch tests at the raw `gimp-3.3` ELF from Python; use `tools/in-build-gimp.py`.
- **GEGL** needs a working C++ compiler (`g++`); default `clang` alone may fail Meson’s C++ probe.
- Rebuild **babl/GEGL** into the same `GIMP_PREFIX` before rebuilding GIMP when updating those trees.
- Incremental work: `ninja -C _build-local` only; full dependency rebuild is only needed when babl/gegl sources change.
