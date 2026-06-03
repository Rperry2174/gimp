# AGENTS.md

## Cursor Cloud specific instructions

This repository is **GIMP** (GNU Image Manipulation Program): a native **Meson/Ninja** C/GTK desktop application (not Node/npm). There is no `docker-compose` or root `package.json`.

### Services

| Component | Role |
|-----------|------|
| **Built GIMP** (`_build/app/gimp-3.3`, `gimp-console-3.3`) | Main GUI and batch/console testing |
| **babl + GEGL** (installed under `$HOME/.local`) | Hard build/runtime deps; Ubuntu packages are often too old for GIMP `master`/`3.3` |
| **gexiv2 0.16 + exiv2 ≥ 0.28** (often `$HOME/.local`) | Required by current `meson.build`; Ubuntu 24.04 ships older versions |
| **Xvfb + `dbus-run-session`** | Used automatically for Meson UI tests when available (`tools/run_test_env.sh`) |

### First-time environment (not in the VM update script)

System packages (Ubuntu/Debian, aligned with `.gitlab-ci.yml` `deps-debian-nonreloc`) include `build-essential`, `meson`, `ninja-build`, `gettext`, `gobject-introspection`, `libgtk-3-dev`, imaging libraries (`libpng-dev`, `libtiff-dev`, `librsvg2-dev`, …), `python3-gi`, `python3-gi-cairo`, `xvfb`, `dbus-x11`, etc. **`libbacktrace-dev` is not on Ubuntu 24.04** — omit it.

Build dependencies into `$HOME/.local` (see [developer build docs](https://developer.gimp.org/core/setup/build/)):

1. **babl** — `meson setup` with `-Dprefix=$HOME/.local` and `-Denable-gir=true`
2. **GEGL** — `-Dcairo=enabled -Dintrospection=true`; set `GI_GIR_PATH=$HOME/.local/share/gir-1.0` when building
3. **exiv2 ≥ 0.28** and **gexiv2 0.16** — upstream master/tags if distro packages are too old (`libinih-dev` needed for exiv2)

Use **`CC=gcc CXX=g++`** for GEGL/gexiv2/GIMP if the default `cc` is Clang without a working C++ toolchain.

### Environment variables for builds and tests

```bash
export GIMP_PREFIX="$HOME/.local"
export PKG_CONFIG_PATH="${GIMP_PREFIX}/lib/pkgconfig:${GIMP_PREFIX}/lib/x86_64-linux-gnu/pkgconfig"
export GI_GIR_PATH="${GIMP_PREFIX}/share/gir-1.0"
export GI_TYPELIB_PATH="${GIMP_PREFIX}/lib/x86_64-linux-gnu/girepository-1.0:${PWD}/_build/libgimp"
export XDG_DATA_DIRS="${GIMP_PREFIX}/share:/usr/share"
export LD_LIBRARY_PATH="${GIMP_PREFIX}/lib:${GIMP_PREFIX}/lib/x86_64-linux-gnu:${PWD}/_build/libgimp:${PWD}/_build/libgimpbase:${PWD}/_build/libgimpcolor:${PWD}/_build/libgimpconfig:${PWD}/_build/libgimpmath:${PWD}/_build/libgimpmodule:${PWD}/_build/libgimpthumb:${PWD}/_build/libgimpwidgets"
export PYTHONPATH="${GIMP_PREFIX}/lib/python3/dist-packages"
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
```

### Configure, build, test

```bash
git submodule update --init --depth 1 gimp-data
meson setup _build -Dprefix="$HOME/.local"   # add --reconfigure after dependency changes
ninja -C _build
ninja -C _build test                         # 21 tests when headless stack is present
```

### Run in-build binaries

Prefer **`tools/in-build-gimp.py`** (sets `GIMP3_DIRECTORY`, plug-in paths, and library paths). Example batch “hello world” (creates an XCF via Python-Fu):

```bash
export GIMP_GLOBAL_BUILD_ROOT="$(pwd)/_build"
export GIMP_GLOBAL_SOURCE_ROOT="$(pwd)"
export GIMP_SELF_IN_BUILD="$(pwd)/_build/app/gimp-console-3.3"
export GIMP_PYTHON_WITH_GI="$(command -v python3)"
export GIMP3_SYSCONFDIR="$(pwd)/etc"
export GIMP_TESTING_PLUG_INS="python/python-eval/"
export GIMP_TESTING_INTERPRETER_DIRS="$(pwd)/_build/plug-ins/python/:$(pwd)/_build/extensions/"
export GIMP_TESTING_ENVIRON_DIRS="$(pwd)/_build/data/environ/"
export GIMP3_LOCALEDIR="$(pwd)/_build/po-plug-ins"
export GIMP_TESTING_MENUS_PATH="$(pwd)/_build/menus:$(pwd)/menus"

python3 tools/in-build-gimp.py -ni --batch-interpreter python-fu-eval -b '
image = Gimp.Image.new(64, 64, Gimp.ImageBaseType.RGB)
drawable = Gimp.Layer.new(image, "Layer", 64, 64, Gimp.ImageType.RGB_IMAGE, 100, Gimp.LayerMode.NORMAL)
image.insert_layer(drawable, None, 0)
Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, image, Gio.File.new_for_path("/tmp/test.xcf"), None)
image.delete()
' --quit
```

GUI: `_build/app/gimp-3.3` under `xvfb-run` (or a real display). **`gimp-console-3.3 --version`** is a quick smoke check without the wrapper.

### Lint / static checks (optional)

CI runs `cppcheck`, `clang-format`, and `shellcheck` on packaging scripts — not a single `npm run lint` equivalent. See `.gitlab-ci.yml`.

### Submodule

**`gimp-data`** is required; initialize before `meson setup`.
