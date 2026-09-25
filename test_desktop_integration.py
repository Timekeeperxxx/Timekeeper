"""A small check for the metadata GNOME reads from MPRIS."""

import unittest
from types import SimpleNamespace

from desktop_integration import DesktopIntegration


class DesktopIntegrationTest(unittest.TestCase):
    def test_song_metadata(self):
        desktop = DesktopIntegration.__new__(DesktopIntegration)
        desktop.track_number = 0
        changed = {}
        desktop.changed = lambda name, value: changed.update({name: value.unpack()})
        song = SimpleNamespace(title="测试歌曲", name="测试歌曲", interval=95,
                               singer=[SimpleNamespace(name="歌手")],
                               cover_url=lambda size: "https://example.com/cover.jpg" if size == 500 else "")
        desktop.song_changed(song)
        self.assertEqual(desktop.metadata["xesam:title"].unpack(), "测试歌曲")
        self.assertEqual(desktop.metadata["xesam:artist"].unpack(), ["歌手"])
        self.assertEqual(desktop.metadata["mpris:length"].unpack(), 95_000_000)
        self.assertEqual(desktop.metadata["mpris:artUrl"].unpack(), "https://example.com/cover.jpg")
        self.assertTrue(changed["CanPlay"])
        self.assertTrue(changed["CanGoNext"])


if __name__ == "__main__":
    unittest.main()
