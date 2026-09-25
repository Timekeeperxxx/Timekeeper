#!/bin/sh
set -eu

cd "$(dirname "$0")"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

if [ "$(dpkg --print-architecture)" != amd64 ] || [ "$(python3 -c 'import sys; print(sys.version_info[:2] == (3, 12))')" != True ]; then
    echo '此构建脚本目前面向 Ubuntu 24.04 amd64 / Python 3.12' >&2
    exit 1
fi

if [ -z "${VENDOR_DIR:-}" ]; then
    if [ -n "${PIP_PYTHON:-}" ]; then
        pip_python=$PIP_PYTHON
    else
        python3 -m venv "$work/venv"
        pip_python="$work/venv/bin/python"
    fi
fi

stage="$work/package"
mkdir -p "$stage/DEBIAN" "$stage/usr/lib/timekeeper/vendor" "$stage/usr/bin" "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps" "$stage/usr/share/doc/timekeeper" dist
if [ -n "${VENDOR_DIR:-}" ]; then
    cp -a "$VENDOR_DIR/." "$stage/usr/lib/timekeeper/vendor/"
else
    "$pip_python" -m pip install --disable-pip-version-check --target "$stage/usr/lib/timekeeper/vendor" -r requirements.txt
fi
find "$stage/usr/lib/timekeeper/vendor" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$stage/usr/lib/timekeeper/vendor/bin"
cp app.py desktop_integration.py music_service.py lyrics_sync.py style.css "$stage/usr/lib/timekeeper/"
cp assets/timekeeper.svg "$stage/usr/share/icons/hicolor/scalable/apps/"
cp LICENSE "$stage/usr/share/doc/timekeeper/"
cp RELEASE_NOTES.md "$stage/usr/share/doc/timekeeper/"
cat > "$stage/usr/share/doc/timekeeper/copyright" <<'EOF'
Timekeeper source: https://github.com/Timekeeperxxx/Timekeeper
License: GPL-3.0-or-later; see /usr/share/doc/timekeeper/LICENSE.
Bundled Python packages retain their own notices in /usr/lib/timekeeper/vendor.
QQ Music and its trademarks belong to their respective owners; this is an unofficial client.
EOF

cat > "$stage/DEBIAN/control" <<'EOF'
Package: timekeeper
Version: 0.1.0~beta1
Section: sound
Priority: optional
Architecture: amd64
Maintainer: Timekeeper Project
Depends: python3 (>= 3.12), python3 (<< 3.13), python3-gi, gir1.2-gtk-4.0, gir1.2-gstreamer-1.0, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good
Description: Timekeeper unofficial QQ Music player for Linux
 A native GTK music player with QQ account sign-in, library and streaming.
EOF

cat > "$stage/usr/bin/timekeeper" <<'EOF'
#!/bin/sh
export PYTHONPATH=/usr/lib/timekeeper/vendor
exec /usr/bin/python3 /usr/lib/timekeeper/app.py "$@"
EOF
chmod 755 "$stage/usr/bin/timekeeper"

cat > "$stage/usr/share/applications/io.github.timekeeper.qqmusic.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Timekeeper
Comment=QQ 音乐播放器
Exec=timekeeper
Icon=timekeeper
Terminal=false
Categories=AudioVideo;Audio;Music;Player;
EOF

dpkg-deb --build --root-owner-group "$stage" 'dist/timekeeper_0.1.0~beta1_amd64.deb'
