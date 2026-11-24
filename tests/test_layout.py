"""The 月报 folder (1.4.0): where its three subfolders are, the copy in 正式月报, and arranging an older version's folder."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.parse import quote, unquote

from archive_fixture import write_chat
from mr import config, jobs, layout, server
from mr.archive import Archive

MONTH = '2026-10'
ANCHORS = [f'msg-{n:024x}' for n in range(3)]


def chat_messages():
    return [(a, f'2026-10-0{n + 1} 09:00:00', '张三', '文本', 'zhang', '', [f'第 {n} 句'], []) for n, a in enumerate(ANCHORS)]


def links(text):
    """The targets of the Markdown links in text, as written (<…> unwrapped)."""
    return [t[1:-1] if t.startswith('<') else unquote(t) for t in re.findall(r'\]\((<[^>\n]*>|[^)\s<>]+)\)', text)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.archive = self.base / '归档'
        self.doc = write_chat(self.archive, '01_私聊/张三', '张三', MONTH, 'zhang', 'me', chat_messages())
        # Never the real folders of this machine: the home, the old default output folder, the cache.
        for patch in (mock.patch.object(config, 'HOME', self.home),
                      mock.patch.object(config, 'OLD_DEFAULT_OUTPUT', self.base / '没有这个文件夹'),
                      mock.patch.object(config, 'cache_dir', lambda: self.home / 'cache')):
            patch.start()
            self.addCleanup(patch.stop)

    def stored(self):
        return json.loads(config.settings_path().read_text(encoding='utf-8'))

    def assert_reaches_anchor(self, report, target):
        """target, a link written in report, opens the archive document that holds the anchor."""
        path, _, anchor = layout.split_fragment(target)
        doc = (report.parent / path).resolve()
        self.assertTrue(doc.is_file(), f'{target} from {report}')
        self.assertTrue(doc.is_relative_to(self.archive.resolve()))
        self.assertIn(f'<a id="{anchor}"></a>', doc.read_text(encoding='utf-8'))

    def working_copy(self, run_dir, extra=''):
        """A finished report as a run writes it: its message links made by the archive, from the month's folder."""
        a = Archive(str(self.archive), str(run_dir.parent), index=self.home / 'index.sqlite')
        try:
            rel = self.doc.relative_to(self.archive).as_posix()
            lines = [f'- 第 {n} 条 [#{n + 1}]({a.link(rel, anchor, run_dir)})' for n, anchor in enumerate(ANCHORS)]
        finally:
            a.close()
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / layout.report_name(MONTH)
        path.write_text(f'# 2026 年 10 月回顾\n\n' + '\n'.join(lines) + '\n[同页](#摘要) [网页](https://example.invalid/a)\n' + extra,
                        encoding='utf-8')
        return path


class FolderSettingsTest(Base):
    def test_the_folder_follows_the_archive_until_one_is_chosen(self):
        s = config.save_settings({'archive_root': str(self.archive)})
        self.assertEqual(s['reports_root'], str(self.archive / '月报'))
        self.assertFalse(s['reports_root_custom'])
        self.assertEqual(s['output_root'], str(self.archive / '月报' / '原始材料'))
        self.assertEqual(config.final_dir(s), self.archive / '月报' / '正式月报')
        self.assertNotIn('output_root', self.stored())            # worked out, never stored
        other = self.base / '另一个归档'
        other.mkdir()
        self.assertEqual(config.save_settings({'archive_root': str(other)})['reports_root'], str(other / '月报'))

    def test_no_archive_no_folder(self):
        s = config.load_settings()
        self.assertEqual((s['reports_root'], s['output_root']), ('', ''))
        self.assertIsNone(config.final_dir(s))

    def test_a_chosen_folder_stays_where_it_is(self):
        config.save_settings({'archive_root': str(self.archive)})
        chosen = self.base / '我的月报'
        s = config.save_settings({'reports_root': str(chosen)})
        self.assertTrue(s['reports_root_custom'])
        self.assertEqual(s['output_root'], str(chosen / '原始材料'))
        other = self.base / '另一个归档'
        other.mkdir()
        self.assertEqual(config.save_settings({'archive_root': str(other)})['reports_root'], str(chosen))
        # Choosing exactly the folder the archive gives is the same as not choosing: it follows the archive again.
        s = config.save_settings({'reports_root': str(other / '月报')})
        self.assertFalse(s['reports_root_custom'])
        self.assertEqual(self.stored()['reports_root'], '')

    def test_the_folder_may_be_inside_the_archive_but_not_hold_it(self):
        config.save_settings({'archive_root': str(self.archive)})
        for bad in (self.archive, self.base):
            with self.assertRaises(config.ConfigError):
                config.save_settings({'reports_root': str(bad)})
        self.assertEqual(config.save_settings({'reports_root': str(self.archive / '别处')})['reports_root'], str(self.archive / '别处'))

    def test_the_output_folder_of_an_older_version_is_remembered_for_arranging(self):
        config._write(config.settings_path(), {'archive_root': str(self.archive), 'output_root': str(self.base / '旧月报')})
        s = config.load_settings()
        self.assertEqual(s['legacy_output_root'], str(self.base / '旧月报'))
        self.assertEqual(s['output_root'], str(self.archive / '月报' / '原始材料'))
        config.save_settings({'concurrency': 3})
        self.assertNotIn('output_root', self.stored())
        self.assertEqual(self.stored()['legacy_output_root'], str(self.base / '旧月报'))


class ArchiveSkipTest(Base):
    def test_the_folder_inside_the_archive_is_not_read_as_chats(self):
        s = config.save_settings({'archive_root': str(self.archive)})
        # Copies of the chat inside the 月报 folder, in the archive's own format, as a careless tool might leave them.
        write_chat(Path(s['output_root']), MONTH, '副本', MONTH, 'copy1', 'me', chat_messages())
        write_chat(config.final_dir(s), '', '副本', MONTH, 'copy2', 'me', chat_messages())
        a = Archive(s['archive_root'], s['output_root'], index=self.home / 'index.sqlite')
        try:
            start, end = datetime(2026, 10, 1).timestamp(), datetime(2026, 11, 1).timestamp()
            self.assertEqual([c for c, _ in a.active_chats(start, end)], ['zhang'])
        finally:
            a.close()


class PublishTest(Base):
    def test_the_copy_in_正式月报_reaches_the_same_messages(self):
        s = config.save_settings({'archive_root': str(self.archive)})
        run_dir = Path(s['output_root']) / MONTH
        work = self.working_copy(run_dir)
        dest = layout.publish(s, MONTH)
        self.assertEqual(dest, self.archive / '月报' / '正式月报' / '月报_2026-10.md')
        for report in (work, dest):
            found = [t for t in links(report.read_text(encoding='utf-8')) if '#msg-' in t]
            self.assertEqual(len(found), 3)
            for t in found:
                self.assert_reaches_anchor(report, t)
        text = dest.read_text(encoding='utf-8')
        self.assertIn('[同页](#摘要)', text)
        self.assertIn('(https://example.invalid/a)', text)
        # Generated again: the copy is replaced whole, with nothing left beside it.
        work.write_text(work.read_text(encoding='utf-8') + '\n重新生成\n', encoding='utf-8')
        layout.publish(s, MONTH)
        self.assertIn('重新生成', dest.read_text(encoding='utf-8'))
        self.assertEqual(sorted(p.name for p in dest.parent.iterdir()), ['月报_2026-10.md'])

    def test_a_chosen_folder_elsewhere(self):
        config.save_settings({'archive_root': str(self.archive)})
        s = config.save_settings({'reports_root': str(self.base / '别处' / '月报')})
        self.working_copy(Path(s['output_root']) / MONTH)
        dest = layout.publish(s, MONTH)
        for t in links(dest.read_text(encoding='utf-8')):
            if '#msg-' in t:
                self.assert_reaches_anchor(dest, t)

    def test_a_month_without_a_report_publishes_nothing(self):
        s = config.save_settings({'archive_root': str(self.archive)})
        self.assertIsNone(layout.publish(s, MONTH))
        self.assertFalse((self.archive / '月报').exists())

    def test_the_window_shows_the_copy_in_正式月报(self):
        s = config.save_settings({'archive_root': str(self.archive)})
        self.working_copy(Path(s['output_root']) / MONTH)
        self.assertEqual(server._shown_report(s, MONTH), None)
        dest = layout.publish(s, MONTH)
        self.assertEqual(Path(server._shown_report(s, MONTH)), dest)
        shown = server.report(MONTH)
        self.assertEqual((Path(shown['path']), shown['markdown']), (dest, dest.read_text(encoding='utf-8')))
        route = next(r for r in server.app.routes if getattr(r, 'path', '') == '/api/reports/{month}' and 'GET' in r.methods)
        self.assertIs(route.endpoint, server.report)


class HashInFolderNameTest(Base):
    """Chat folders whose names hold a '#': a link moved to another folder still opens the same document and message."""
    ANCHOR = 'msg-' + 'ab' * 12

    def setUp(self):
        super().setUp()
        self.old_dir = self.base / '旧' / MONTH
        self.new_dir = self.base / '新' / '月报' / config.FINAL
        self.old_dir.mkdir(parents=True)
        self.new_dir.mkdir(parents=True)

    def doc_in(self, folder):
        doc = self.archive / '05_普通群聊' / folder / '聊天记录' / '2021-04_001.md'
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(f'<a id="{self.ANCHOR}"></a>\n### 2021-04-01 09:00:00 · 张三 · 文本\n', encoding='utf-8')
        return doc

    def rebased(self, target):
        """target written in old_dir, rewritten for new_dir; the new target as it stands in the text."""
        text = layout.rebase_links(f'[原文]({target})', self.old_dir, self.new_dir)
        (new,) = links(text)
        self.assertNotEqual(new, target)
        return new

    def assert_opens(self, new, doc, anchor):
        path, _, frag = layout.split_fragment(new)
        self.assertEqual(frag, anchor)
        self.assertEqual((self.new_dir / path).resolve(), doc.resolve())

    def angle(self, doc, anchor=None):
        rel = Path(os.path.relpath(doc, self.old_dir)).as_posix()
        return '<' + rel + ('#' + anchor if anchor else '') + '>'

    def test_a_folder_ending_in_space_hash_number(self):
        doc = self.doc_in('00_兴趣/读书会 #2')
        self.assert_opens(self.rebased(self.angle(doc, self.ANCHOR)), doc, self.ANCHOR)

    def test_a_folder_named_just_hash(self):
        doc = self.doc_in('#')
        self.assert_opens(self.rebased(self.angle(doc, self.ANCHOR)), doc, self.ANCHOR)

    def test_a_folder_ending_in_hash(self):
        doc = self.doc_in('问答小组#')
        self.assert_opens(self.rebased(self.angle(doc, self.ANCHOR)), doc, self.ANCHOR)
        doc = self.doc_in('甲方&乙方#运营#')
        self.assert_opens(self.rebased(self.angle(doc, self.ANCHOR)), doc, self.ANCHOR)

    def test_a_link_into_a_hash_folder_without_an_anchor(self):
        doc = self.doc_in('#')
        self.assert_opens(self.rebased(self.angle(doc)), doc, '')
        doc = self.doc_in('问答小组#')
        self.assert_opens(self.rebased(self.angle(doc)), doc, '')

    def test_a_bare_link_with_an_encoded_hash(self):
        doc = self.doc_in('问答小组#')
        rel = quote(Path(os.path.relpath(doc, self.old_dir)).as_posix(), safe='/')   # '#' written as %23
        self.assertIn('%23', rel)
        self.assert_opens(self.rebased(rel + '#' + self.ANCHOR), doc, self.ANCHOR)
        self.assert_opens(self.rebased(rel), doc, '')

    def test_links_that_stay_as_they_are(self):
        for target in ('#摘要', '#', 'https://example.invalid/a#b', '<#摘要>'):
            self.assertEqual(layout.rebase_links(f'[x]({target})', self.old_dir, self.new_dir), f'[x]({target})')


class ArrangeTest(Base):
    """An older version's output folder, with every kind of file that names a place, arranged into the new layout."""
    def setUp(self):
        super().setUp()
        self.old = old = self.base / '旧月报'
        run = old / MONTH
        work = self.working_copy(run, extra='\n[缓存](<cache/note.md>)\n')
        (run / 'cache').mkdir()
        (run / 'cache' / 'note.md').write_text('缓存', encoding='utf-8')
        (run / 'status.json').write_text(json.dumps({'state': 'done', 'report': str(work), 'text': str(run / 'text.md')},
                                                    ensure_ascii=False, indent=2), encoding='utf-8')
        topic = old / '专题' / 't1'
        topic.mkdir(parents=True)
        rel = Path(os.path.relpath(self.doc, topic)).as_posix()
        (topic / '专题.md').write_text(f'[#1](<{rel}#{ANCHORS[0]}>)\n', encoding='utf-8')
        (topic / 'status.json').write_text(json.dumps({'report': str(topic / '专题.md')}, ensure_ascii=False), encoding='utf-8')
        task = old / '外部处理' / '阅读' / MONTH / '任务' / 'a.md'
        task.parent.mkdir(parents=True)
        task.write_text(f'结果文件：{old / "外部处理" / "阅读" / MONTH / "结果" / "a.md"}\n\n【要求】\n\n读\n', encoding='utf-8')
        (self.home / 'jobs.json').write_text(json.dumps({'current': None, 'side': None, 'queue': [], 'history': [
            {'id': 'x', 'kind': 'run', 'month': MONTH, 'log': str(run / 'run.log'), 'state': 'done'}]}, ensure_ascii=False), encoding='utf-8')
        # The settings as 1.3 left them.
        config._write(config.settings_path(), {'archive_root': str(self.archive), 'output_root': str(old),
                                                'external_root': str(old / '外部处理')})

    def test_everything_moves_and_still_points_where_it_did(self):
        s = config.load_settings()
        st = layout.state(s)
        self.assertEqual((st['legacy'], st['months'], st['problem']), (str(self.old), 1, None))
        self.assertEqual(layout.arrange(s, say=lambda *_: None), 0)
        new = self.archive / '月报' / '原始材料'
        self.assertFalse(self.old.exists())
        run = new / MONTH
        status = json.loads((run / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual((status['report'], status['text']), (str(run / '月报_2026-10.md'), str(run / 'text.md')))
        topic = new / '专题' / 't1'
        self.assertEqual(json.loads((topic / 'status.json').read_text(encoding='utf-8'))['report'], str(topic / '专题.md'))
        first = (new / '外部处理' / '阅读' / MONTH / '任务' / 'a.md').read_text(encoding='utf-8').splitlines()[0]
        self.assertEqual(first, f'结果文件：{new / "外部处理" / "阅读" / MONTH / "结果" / "a.md"}')
        history = json.loads((self.home / 'jobs.json').read_text(encoding='utf-8'))['history']
        self.assertEqual(history[0]['log'], str(run / 'run.log'))
        for report in (run / '月报_2026-10.md', topic / '专题.md', self.archive / '月报' / '正式月报' / '月报_2026-10.md'):
            found = [t for t in links(report.read_text(encoding='utf-8')) if '#msg-' in t]
            self.assertTrue(found, report)
            for t in found:
                self.assert_reaches_anchor(report, t)
        self.assertIn('[缓存](<cache/note.md>)', (run / '月报_2026-10.md').read_text(encoding='utf-8'))
        # No file left naming the old folder.
        for root in (self.archive / '月报', self.home):
            for f in root.rglob('*'):
                if f.is_file() and f.suffix in ('.json', '.md'):
                    self.assertNotIn(str(self.old), f.read_text(encoding='utf-8'), f)
        s = config.load_settings()
        self.assertEqual(s['legacy_output_root'], '-')
        self.assertEqual(s['external_root'], str(new / '外部处理'))
        self.assertIsNone(layout.state(s)['legacy'])
        self.assertEqual(layout.arrange(s, say=lambda *_: None), 0)            # nothing more to do

    def test_on_another_drive_it_is_copied_then_the_old_folder_recycled(self):
        import shutil
        from mr import plugins
        recycled = []
        def recycle(paths):
            recycled.extend(Path(p) for p in paths)
            for p in paths:
                shutil.rmtree(p)
        other_drive = OSError(18, 'cross-device link')
        with mock.patch.object(layout.os, 'rename', side_effect=other_drive), mock.patch.object(plugins, 'recycle', recycle):
            self.assertEqual(layout.arrange(config.load_settings(), say=lambda *_: None), 0)
        self.assertEqual(recycled, [self.old])
        new = self.archive / '月报' / '原始材料'
        self.assertTrue((new / MONTH / 'status.json').exists())
        self.assertTrue((self.archive / '月报' / '正式月报' / '月报_2026-10.md').exists())

    def test_a_failed_copy_changes_nothing(self):
        import shutil
        from mr import plugins
        recycled = []
        def broken(src, dst, **kw):
            Path(dst).mkdir(parents=True)
            (Path(dst) / 'half').write_text('x', encoding='utf-8')
            raise shutil.Error('disk full')
        with mock.patch.object(layout.os, 'rename', side_effect=OSError(18, 'cross-device link')), \
                mock.patch.object(layout.shutil, 'copytree', broken), \
                mock.patch.object(plugins, 'recycle', lambda paths: recycled.extend(paths) or [shutil.rmtree(p) for p in paths]):
            self.assertEqual(layout.arrange(config.load_settings(), say=lambda *_: None), 2)
        self.assertEqual([Path(p) for p in recycled], [self.archive / '月报' / '原始材料'])
        self.assertTrue((self.old / MONTH / 'status.json').exists())
        self.assertNotEqual(config.load_settings()['legacy_output_root'], '-')
        self.assertIsNone(layout.state(config.load_settings())['problem'])      # it can be tried again

    def test_material_already_in_the_new_place_is_not_mixed_in(self):
        taken = self.archive / '月报' / '原始材料' / '2026-09'
        taken.mkdir(parents=True)
        s = config.load_settings()
        self.assertIn('已经有内容', layout.state(s)['problem'])
        self.assertEqual(layout.arrange(s, say=lambda *_: None), 2)
        self.assertTrue((self.old / MONTH / 'status.json').exists())
        self.assertNotEqual(config.load_settings()['legacy_output_root'], '-')

    def test_a_month_being_run_holds_it_back(self):
        lock = self.old / MONTH / '.lock'
        lock.write_text(json.dumps({'pid': os.getpid(), 'created': None}), encoding='utf-8')
        with mock.patch.object(jobs, 'alive', return_value=True):
            self.assertEqual(layout.arrange(config.load_settings(), say=lambda *_: None), 2)
        self.assertTrue((self.old / MONTH / 'status.json').exists())

    def test_the_old_default_folder_is_found_without_a_setting(self):
        config._write(config.settings_path(), {'archive_root': str(self.archive)})
        with mock.patch.object(config, 'OLD_DEFAULT_OUTPUT', self.old):
            self.assertEqual(layout.state(config.load_settings())['legacy'], str(self.old))

    def test_not_asking_again_leaves_the_folder_alone(self):
        layout.dismiss()
        self.assertIsNone(layout.state(config.load_settings())['legacy'])
        self.assertTrue((self.old / MONTH / 'status.json').exists())


class LayoutJobTest(unittest.TestCase):
    def test_arranging_runs_as_a_job_and_keeps_its_log_in_the_configuration(self):
        job = {'kind': 'layout', 'month': '整理', 'id': 'x'}
        self.assertEqual(jobs.command(job), ['layout', 'arrange'])
        with tempfile.TemporaryDirectory() as home, mock.patch.object(config, 'HOME', Path(home)):
            self.assertEqual(jobs.log_path('', job), Path(home) / '整理' / 'run.log')
            self.assertEqual(jobs.progress_path('', job), Path(home) / '整理' / 'progress.json')

    def test_no_other_job_is_added_while_arranging(self):
        fake = mock.Mock(has=lambda kind: kind == 'layout')
        with mock.patch.object(server, 'jobs', fake):
            with self.assertRaises(server.HTTPException) as e:
                server.add_jobs({'jobs': [{'kind': 'plan', 'month': MONTH}]})
            self.assertEqual(e.exception.status_code, 409)


if __name__ == '__main__':
    unittest.main()
