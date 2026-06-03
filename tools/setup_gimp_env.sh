#!/usr/bin/env bash
# Source this before building/running GIMP from a local prefix (matches CI ENVIRON).
export GIMP_PREFIX="${GIMP_PREFIX:-/workspace/_install}"
gcc -print-multi-os-directory 2>/dev/null | grep -q ./ && export LIB_DIR=$(gcc -print-multi-os-directory | sed 's/\.\.\///g') || export LIB_DIR="lib"
gcc -print-multiarch 2>/dev/null | grep -q . && export LIB_SUBDIR=$(echo $(gcc -print-multiarch)'/') || export LIB_SUBDIR=
export PKG_CONFIG_PATH="${GIMP_PREFIX}/${LIB_DIR}/${LIB_SUBDIR}pkgconfig:${GIMP_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export XDG_DATA_DIRS="${GIMP_PREFIX}/share:/usr/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
export PATH="${GIMP_PREFIX}/bin:$PATH"
export LD_LIBRARY_PATH="${GIMP_PREFIX}/${LIB_DIR}/${LIB_SUBDIR}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export GI_TYPELIB_PATH="${GIMP_PREFIX}/${LIB_DIR}/${LIB_SUBDIR}girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
