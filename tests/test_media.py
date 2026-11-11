import tempfile
import unittest
from pathlib import Path
from mr import media


class TranscriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.voice = self.root / 'archive' / '群' / '语音'
        self.voice.mkdir(parents=True)
        self.out = self.root / 'out'

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_sits_beside_the_clip(self):
        clip = self.voice / '2025-06-27_230029_语音_000005.mp3'
        clip.write_bytes(b'x')
        self.assertIsNone(media.read_transcript(clip))
        media.write_transcript(clip, '好的')
        self.assertEqual((self.voice / '2025-06-27_230029_语音_000005.txt').read_text(encoding='utf-8'), '好的')
        self.assertEqual(media.read_transcript(clip), '好的')
        media.write_transcript(clip, '')                     # no speech still counts as transcribed
        self.assertEqual(media.read_transcript(clip), '')
        self.assertEqual(sorted(p.name for p in self.voice.iterdir()), ['2025-06-27_230029_语音_000005.mp3', '2025-06-27_230029_语音_000005.txt'])

    def test_the_same_clip_in_another_chat_gets_the_text(self):
        from mr.archive import Archive
        from urllib.parse import quote
        from archive_fixture import write_chat
        archive = self.root / 'archive'
        other = archive / '另一个群' / '语音'
        other.mkdir(parents=True)
        (self.voice / 'a.mp3').write_bytes(b'clip a')
        (other / 'a 的副本.mp3').write_bytes(b'clip a')
        (other / 'c.mp3').write_bytes(b'clip c')          # same size, other content
        msg = lambda n, f: (f'msg-{n}', '2026-10-01 09:00:00', '张三', '语音', 'zs', '', ['\\[语音\\]'], [f'../语音/{quote(f)}'])
        write_chat(archive, '群', '群', '2026-10', 'g@chatroom', 'me', [msg(1, 'a.mp3')])
        write_chat(archive, '另一个群', '另一个群', '2026-10', 'h@chatroom', 'me', [msg(2, 'a 的副本.mp3'), msg(3, 'c.mp3')])
        a = Archive(archive, index=self.root / 'index.sqlite')
        try:
            media.write_transcript(self.voice / 'a.mp3', '一')
            self.assertEqual(media.spread_transcripts(a, ['群/语音/a.mp3']), 1)
            self.assertEqual(media.read_transcript(other / 'a 的副本.mp3'), '一')
            self.assertIsNone(media.read_transcript(other / 'c.mp3'))
            self.assertEqual(media.spread_transcripts(a, ['群/语音/a.mp3']), 0)
        finally:
            a.close()

if __name__ == '__main__':
    unittest.main()
