%global debug_package %{nil}

Name:           timekeeper
Version:        0.1.0
Release:        0.beta1%{?dist}
Summary:        Unofficial QQ Music player for Linux
License:        GPL-3.0-or-later
URL:            https://github.com/Timekeeperxxx/Timekeeper
BuildArch:      x86_64
Requires:       python3 >= 3.12
Requires:       python3-gobject
Requires:       python3-gstreamer1
Requires:       gobject-introspection
Requires:       gtk4
Requires:       libglvnd-gles
Requires:       gstreamer1
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good

%description
A native GTK music player with QQ account sign-in, library, synchronized lyrics,
and online streaming. This is an unofficial client.

%prep

%build

%install
mkdir -p %{buildroot}
cp -a %{timekeeper_payload}/. %{buildroot}/

%files
%{_bindir}/timekeeper
%{_libdir}/timekeeper
%{_datadir}/applications/io.github.timekeeper.qqmusic.desktop
%{_datadir}/icons/hicolor/scalable/apps/timekeeper.svg
%{_datadir}/doc/timekeeper

%changelog
* Fri Sep 25 2026 Timekeeper Project - 0.1.0-0.beta1
- Initial beta release
