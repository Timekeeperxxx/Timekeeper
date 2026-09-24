import unittest

from lyrics_sync import Line, Word, highlight_color, highlight_markup, parse


class LyricParsingTest(unittest.TestCase):
    def test_highlight_fades_continuously(self):
        self.assertEqual(highlight_color(0), "#a8a8b2")
        self.assertEqual(highlight_color(1), "#ffffff")
        self.assertEqual(highlight_color(0.5), "#d4d4d8")
        self.assertEqual(highlight_color(-1), "#a8a8b2")
        self.assertEqual(highlight_color(1, "#ff6679"), "#ff6679")

    def test_highlight_markup_preserves_word_positions(self):
        line = Line(0, 500, "你&好", [Word(0, 200, 0, 1), Word(200, 500, 1, 3)])
        markup = highlight_markup(line, 300, line.words[1])
        self.assertIn('foreground="#ffffff">你</span>', markup)
        self.assertIn('&amp;好</span>', markup)
        self.assertIn('foreground="#ff6679">你</span>', highlight_markup(line, 300, line.words[1], "#ff6679"))

    def test_qrc_words_and_lrc_fallback(self):
        qrc = '<QrcInfos><Lyric_1 LyricContent="[0,700]你(0,300)好(300,400)\n[700,500]世(700,250)界(950,250)"/></QrcInfos>'
        line = parse(qrc)[0]
        self.assertEqual((line.text, line.start, line.end), ("你好", 0, 700))
        self.assertEqual([(word.first, word.last, word.start) for word in line.words], [(0, 1, 0), (1, 2, 300)])
        self.assertEqual(len(parse(qrc)), 2)
        lrc = parse("[00:01.20]第一句\n[00:03.50]第二句")
        self.assertEqual([(line.start, line.end, line.text) for line in lrc], [(1200, 3500, "第一句"), (3500, 8500, "第二句")])

    def test_qrc_quotes_inside_lyrics_do_not_truncate_later_lines(self):
        qrc = '<QrcInfos><Lyric_1 LyricContent="[0,500]noise/\"(0,100)hitman\"(100,400)\n[500,500]下一句(500,500)"/></QrcInfos>'
        lines = parse(qrc)
        self.assertEqual([line.text for line in lines], ['noise/"hitman"', "下一句"])


if __name__ == "__main__":
    unittest.main()
