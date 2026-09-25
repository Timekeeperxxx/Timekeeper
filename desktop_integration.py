"""GNOME media controls (MPRIS) and StatusNotifierItem tray integration."""

from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst


MPRIS_XML = """
<node>
 <interface name="org.mpris.MediaPlayer2">
  <method name="Raise"/><method name="Quit"/>
  <property name="CanQuit" type="b" access="read"/>
  <property name="CanRaise" type="b" access="read"/>
  <property name="HasTrackList" type="b" access="read"/>
  <property name="Identity" type="s" access="read"/>
  <property name="DesktopEntry" type="s" access="read"/>
  <property name="SupportedUriSchemes" type="as" access="read"/>
  <property name="SupportedMimeTypes" type="as" access="read"/>
 </interface>
 <interface name="org.mpris.MediaPlayer2.Player">
  <method name="Next"/><method name="Previous"/><method name="Pause"/>
  <method name="PlayPause"/><method name="Stop"/><method name="Play"/>
  <method name="Seek"><arg type="x" direction="in"/></method>
  <method name="SetPosition"><arg type="o" direction="in"/><arg type="x" direction="in"/></method>
  <property name="PlaybackStatus" type="s" access="read"/>
  <property name="Metadata" type="a{sv}" access="read"/>
  <property name="Position" type="x" access="read"/>
  <property name="CanGoNext" type="b" access="read"/>
  <property name="CanGoPrevious" type="b" access="read"/>
  <property name="CanPlay" type="b" access="read"/>
  <property name="CanPause" type="b" access="read"/>
  <property name="CanSeek" type="b" access="read"/>
  <property name="CanControl" type="b" access="read"/>
  <property name="Volume" type="d" access="readwrite"/>
  <property name="Rate" type="d" access="read"/>
  <property name="MinimumRate" type="d" access="read"/>
  <property name="MaximumRate" type="d" access="read"/>
  <property name="Shuffle" type="b" access="readwrite"/>
  <property name="LoopStatus" type="s" access="readwrite"/>
  <signal name="Seeked"><arg type="x"/></signal>
 </interface>
</node>"""

TRAY_XML = """
<node><interface name="org.kde.StatusNotifierItem">
 <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
 <property name="Category" type="s" access="read"/>
 <property name="Id" type="s" access="read"/>
 <property name="Title" type="s" access="read"/>
 <property name="Status" type="s" access="read"/>
 <property name="IconName" type="s" access="read"/>
 <property name="IconThemePath" type="s" access="read"/>
 <property name="ItemIsMenu" type="b" access="read"/>
 <property name="Menu" type="o" access="read"/>
 <signal name="NewStatus"><arg type="s"/></signal>
 <signal name="NewIcon"/>
</interface></node>"""

MENU_XML = """
<node><interface name="com.canonical.dbusmenu">
 <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/>
  <arg type="as" direction="in"/><arg type="u" direction="out"/>
  <arg type="(ia{sv}av)" direction="out"/></method>
 <method name="GetGroupProperties"><arg type="ai" direction="in"/>
  <arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/></method>
 <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/>
  <arg type="v" direction="in"/><arg type="u" direction="in"/></method>
 <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
 <property name="Version" type="u" access="read"/>
 <property name="TextDirection" type="s" access="read"/>
 <property name="Status" type="s" access="read"/>
 <property name="IconThemePath" type="as" access="read"/>
</interface></node>"""


def variant(value):
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, float):
        return GLib.Variant("d", value)
    if isinstance(value, int):
        return GLib.Variant("x", value)
    if isinstance(value, list):
        return GLib.Variant("as", value)
    return GLib.Variant("s", value)


class DesktopIntegration:
    def __init__(self, app):
        self.app = app
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.track_number = 0
        self.track_id = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
        self.status = "Stopped"
        self.metadata = {}
        self.tray_registered = False
        self.ids = []
        for xml, path in ((MPRIS_XML, "/org/mpris/MediaPlayer2"),
                          (TRAY_XML, "/StatusNotifierItem"), (MENU_XML, "/MenuBar")):
            for interface in Gio.DBusNodeInfo.new_for_xml(xml).interfaces:
                self.ids.append(self.connection.register_object(
                    path, interface, self.on_method, self.on_get, self.on_set))
        Gio.bus_own_name_on_connection(
            self.connection, "org.mpris.MediaPlayer2.timekeeper", Gio.BusNameOwnerFlags.NONE,
            None, None)
        self.connection.call(
            "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
            "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self.connection.get_unique_name(),)), None,
            Gio.DBusCallFlags.NONE, 2000, None, self.on_tray_registered, None)

    def on_tray_registered(self, connection, result, _data):
        try:
            connection.call_finish(result)
            self.tray_registered = True
        except GLib.Error:
            pass  # Desktops without a tray can still use MPRIS.

    def on_get(self, _connection, _sender, _path, interface, name):
        app = self.app
        if interface == "org.mpris.MediaPlayer2":
            values = {"CanQuit": True, "CanRaise": True, "HasTrackList": False,
                      "Identity": "Timekeeper", "DesktopEntry": "io.github.timekeeper.qqmusic",
                      "SupportedUriSchemes": [], "SupportedMimeTypes": []}
        elif interface == "org.mpris.MediaPlayer2.Player":
            ok, position = app.audio.query_position(Gst.Format.TIME)
            values = {"PlaybackStatus": self.status, "Metadata": GLib.Variant("a{sv}", self.metadata),
                      "Position": position // 1000 if ok else 0,
                      "CanGoNext": bool(app.queue or app.songs),
                      "CanGoPrevious": bool(app.queue or app.songs),
                      "CanPlay": bool(app.current_song or app.queue or app.songs),
                      "CanPause": bool(app.current_song), "CanSeek": bool(app.current_song),
                      "CanControl": True, "Volume": float(app.audio.get_property("volume")),
                      "Rate": 1.0, "MinimumRate": 1.0, "MaximumRate": 1.0,
                      "Shuffle": app.shuffle.get_active(),
                      "LoopStatus": "Track" if app.repeat.get_active() else "Playlist"}
        elif interface == "org.kde.StatusNotifierItem":
            icon_dir = "/usr/share/icons/hicolor/scalable/apps"
            if not Path(icon_dir, "timekeeper.svg").exists():
                icon_dir = str(Path(__file__).parent / "assets")
            values = {"Category": "ApplicationStatus", "Id": "timekeeper", "Title": "Timekeeper",
                      "Status": "Active", "IconName": "timekeeper", "IconThemePath": icon_dir,
                      "ItemIsMenu": False, "Menu": GLib.Variant("o", "/MenuBar")}
        else:
            values = {"Version": GLib.Variant("u", 3), "TextDirection": "ltr",
                      "Status": "normal", "IconThemePath": []}
        value = values.get(name)
        return value if isinstance(value, GLib.Variant) else variant(value)

    def on_set(self, _connection, _sender, _path, interface, name, value):
        if interface != "org.mpris.MediaPlayer2.Player":
            return False
        value = value.unpack()
        if name == "Volume":
            self.app.volume.set_value(max(0, min(1, value)))
        elif name == "Shuffle":
            self.app.shuffle.set_active(value)
        elif name == "LoopStatus":
            self.app.repeat.set_active(value == "Track")
        else:
            return False
        self.changed(name, variant(value))
        return True

    def on_method(self, _connection, _sender, _path, interface, method, args, invocation):
        app = self.app
        if interface == "com.canonical.dbusmenu":
            labels = {1: "显示窗口", 2: "播放 / 暂停", 3: "上一首", 4: "下一首", 5: "退出"}
            if method == "GetLayout":
                children = [GLib.Variant("(ia{sv}av)",
                            (item, {"label": variant(label)}, [])) for item, label in labels.items()]
                layout = (0, {}, children) if args.unpack()[0] == 0 else (args.unpack()[0],
                          {"label": variant(labels.get(args.unpack()[0], ""))}, [])
                invocation.return_value(GLib.Variant("(u(ia{sv}av))", (1, layout)))
                return
            if method == "GetGroupProperties":
                items = [(item, {"label": variant(labels[item])}) for item in args.unpack()[0] if item in labels]
                invocation.return_value(GLib.Variant("(a(ia{sv}))", (items,)))
                return
            if method == "AboutToShow":
                invocation.return_value(GLib.Variant("(b)", (False,)))
                return
            if method == "Event":
                item, event, *_ = args.unpack()
                if event == "clicked":
                    if item == 1: app.window.present()
                    elif item == 2: app.toggle_pause(None)
                    elif item == 3: app.step(-1)
                    elif item == 4: app.step(1)
                    elif item == 5: app.quit_player()
        elif interface == "org.kde.StatusNotifierItem":
            if method == "Activate":
                app.window.present()
            elif method == "SecondaryActivate":
                app.toggle_pause(None)
        elif interface == "org.mpris.MediaPlayer2":
            if method == "Raise":
                app.window.present()
            elif method == "Quit":
                app.quit_player()
        else:
            if method == "Next": app.step(1)
            elif method == "Previous": app.step(-1)
            elif method == "PlayPause": app.toggle_pause(None)
            elif method == "Pause" and self.status == "Playing": app.toggle_pause(None)
            elif method == "Play" and self.status != "Playing": app.toggle_pause(None)
            elif method == "Stop":
                app.audio.set_state(Gst.State.NULL)
                app.set_playing(False, stopped=True)
            elif method in ("Seek", "SetPosition") and app.current_song:
                position = self.on_get(None, None, None, "org.mpris.MediaPlayer2.Player", "Position").unpack()
                target = position + args.unpack()[0] if method == "Seek" else args.unpack()[1]
                if method == "Seek" or args.unpack()[0] == self.track_id:
                    target = max(0, target)
                    app.audio.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, target * 1000)
                    self.connection.emit_signal(None, "/org/mpris/MediaPlayer2", interface,
                                                "Seeked", GLib.Variant("(x)", (target,)))
        invocation.return_value(None)

    def changed(self, name, value):
        self.connection.emit_signal(None, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties",
                                    "PropertiesChanged", GLib.Variant("(sa{sv}as)",
                                    ("org.mpris.MediaPlayer2.Player", {name: value}, [])))

    def song_changed(self, song):
        self.track_number += 1
        self.track_id = f"/org/mpris/MediaPlayer2/Track/{self.track_number}"
        self.metadata = {
            "mpris:trackid": GLib.Variant("o", self.track_id),
            "xesam:title": variant(song.title or song.name),
            "xesam:artist": variant([singer.name for singer in song.singer]),
            "mpris:length": variant(int(song.interval * 1_000_000)),
        }
        try:
            cover = song.cover_url(500)
            if cover:
                self.metadata["mpris:artUrl"] = variant(cover)
        except ValueError:
            pass
        self.changed("Metadata", GLib.Variant("a{sv}", self.metadata))
        for name in ("CanPlay", "CanPause", "CanSeek", "CanGoNext", "CanGoPrevious"):
            self.changed(name, variant(True))

    def playback_changed(self, playing, stopped=False):
        self.status = "Stopped" if stopped else "Playing" if playing else "Paused"
        self.changed("PlaybackStatus", variant(self.status))

    def clear(self):
        self.metadata = {}
        self.status = "Stopped"
        self.changed("Metadata", GLib.Variant("a{sv}", {}))
        self.changed("PlaybackStatus", variant(self.status))
        for name in ("CanPlay", "CanPause", "CanSeek", "CanGoNext", "CanGoPrevious"):
            self.changed(name, variant(False))
