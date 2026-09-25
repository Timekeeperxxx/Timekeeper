#!/bin/sh
set -eu

cd "$(dirname "$0")"
appimagetool=${APPIMAGETOOL:-appimagetool}
deb='dist/timekeeper_0.1.0.beta1_amd64.deb'
[ -f "$deb" ] || { echo "先运行 ./build-deb.sh" >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
appdir="$work/Timekeeper.AppDir"
mkdir -p "$appdir"
dpkg-deb -x "$deb" "$appdir"
cp "$appdir/usr/share/applications/io.github.timekeeper.qqmusic.desktop" "$appdir/timekeeper.desktop"
cp "$appdir/usr/share/icons/hicolor/scalable/apps/timekeeper.svg" "$appdir/timekeeper.svg"
ln -s timekeeper.svg "$appdir/.DirIcon"
cat > "$appdir/AppRun" <<'LAUNCHER'
#!/bin/sh
set -eu
appdir=${APPDIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}
export PYTHONPATH="$appdir/usr/lib/timekeeper/vendor"
exec /usr/bin/python3 "$appdir/usr/lib/timekeeper/app.py" "$@"
LAUNCHER
chmod 755 "$appdir/AppRun"
if [ -n "${APPIMAGE_RUNTIME:-}" ]; then
    set -- --runtime-file "$APPIMAGE_RUNTIME"
else
    set --
fi
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$appimagetool" --no-appstream "$@" "$appdir" \
    dist/Timekeeper-0.1.0-beta.1-x86_64.AppImage
