#!/bin/sh
set -eu

cd "$(dirname "$0")"
[ "$(rpm --eval '%{_arch}')" = x86_64 ] || { echo '仅支持 x86_64' >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
payload="$work/payload"
libdir=$(rpm --eval '%{_libdir}')
mkdir -p "$payload$libdir/timekeeper/vendor" "$payload/usr/bin" \
    "$payload/usr/share/applications" "$payload/usr/share/icons/hicolor/scalable/apps" \
    "$payload/usr/share/doc/timekeeper" "$work/rpmbuild/RPMS"
if [ -n "${VENDOR_DIR:-}" ]; then
    cp -a "$VENDOR_DIR/." "$payload$libdir/timekeeper/vendor/"
else
    python3 -m venv "$work/venv"
    "$work/venv/bin/python" -m pip install --disable-pip-version-check \
        --target "$payload$libdir/timekeeper/vendor" -r requirements.txt
fi
find "$payload$libdir/timekeeper/vendor" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$payload$libdir/timekeeper/vendor/bin"
cp app.py desktop_integration.py music_service.py lyrics_sync.py style.css "$payload$libdir/timekeeper/"
cp assets/timekeeper.svg "$payload/usr/share/icons/hicolor/scalable/apps/"
cp LICENSE RELEASE_NOTES.md "$payload/usr/share/doc/timekeeper/"
cat > "$payload/usr/bin/timekeeper" <<LAUNCHER
#!/bin/sh
export PYTHONPATH=$libdir/timekeeper/vendor
exec /usr/bin/python3 $libdir/timekeeper/app.py "\$@"
LAUNCHER
chmod 755 "$payload/usr/bin/timekeeper"
cat > "$payload/usr/share/applications/io.github.timekeeper.qqmusic.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Timekeeper
Comment=QQ 音乐播放器
Exec=timekeeper
Icon=timekeeper
Terminal=false
Categories=AudioVideo;Audio;Music;Player;
DESKTOP
rpmbuild -bb --define "_topdir $work/rpmbuild" \
    --define "_tmppath $work" --define "_dbpath $work/rpmdb" \
    --define 'dist .fc44' \
    --define "timekeeper_payload $payload" packaging/timekeeper.spec
mkdir -p dist
cp "$work"/rpmbuild/RPMS/x86_64/*.rpm dist/
