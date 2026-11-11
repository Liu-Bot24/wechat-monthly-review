"""Batch voice transcription over a range of months, apart from any report. Month runs reuse the results."""
from collections import Counter
from pathlib import Path
import openai
from . import config
from .archive import Archive, month_bounds
from .media import Transcriber, read_transcript
from .progress import Progress
from .selection import Selection

STEPS = [('asr', '语音转写', 1.0)]

def clips(archive, selection, start, end, chats=None):
    """Voice clips in [start, end) of the chats a run would read (or of the given chats, whatever the selection says)."""
    rows = archive.voices(start, end)
    out, seen = [], set()
    for r in rows:
        if r['id'] in seen:
            continue
        if chats:
            if r['chat'] not in chats:
                continue
        elif selection.reason(r['chat'], archive.category(r['chat']), r['chat'] in archive.excluded):
            continue
        seen.add(r['id'])
        out.append(r)
    return out

def summary(settings, models_config, first, last, chats=None):
    """Counts for the voice page: clips in range, transcribed, pending, and the measured pace."""
    archive = Archive(settings['archive_root'], settings['output_root'])
    selection = Selection(config.selection_path(), settings['default_skip_categories'])
    start, end = month_bounds(first)[0], month_bounds(last)[1]
    rows = clips(archive, selection, start, end, chats)
    done = [r for r in rows if read_transcript(archive.path(r['main_path'])) is not None]
    return {'total': len(rows), 'done': len(done), 'pending': len(rows) - len(done)}

def run(settings, models_config, first, last, chats=None):
    root = Path(settings['output_root']) / '语音转写'
    root.mkdir(parents=True, exist_ok=True)
    progress = Progress(root / 'progress.json', f'{first}–{last}', 'asr', STEPS)
    try:
        if not models_config['stages'].get('asr'):
            raise SystemExit('"语音转写"环节还没有选择模型：请在 设置 › 接口与模型 › 环节 中选择。')
        archive = Archive(settings['archive_root'], settings['output_root'])
        selection = Selection(config.selection_path(), settings['default_skip_categories'])
        start, end = month_bounds(first)[0], month_bounds(last)[1]
        rows = clips(archive, selection, start, end, chats)
        asr = Transcriber(models_config, settings['output_root'], archive)
        todo = [r for r in rows if asr.cached(archive.path(r['main_path'])) is None]
        progress.begin('asr', len(todo), f'{first} 至 {last}：语音 {len(rows)} 条，待转写 {len(todo)} 条（{asr.model}）')
        print(f'语音 {len(rows)} 条，待转写 {len(todo)} 条', flush=True)
        stats = Counter()
        for r in todo:
            try:
                asr.transcribe(archive.path(r['main_path']), r['main_path'])
                stats['done'] += 1
                progress.tick('asr')
            except openai.APIConnectionError:
                raise SystemExit(f'语音转写服务连不上（{asr.base_url}）。请确认语音识别服务已经启动、接口地址正确。')
            except Exception as e:
                stats['failed'] += 1
                progress.tick('asr', ok=False)
                progress.event(f'转写失败 {Path(r["main_path"]).name}：{str(e)[:120]}')
                if stats['failed'] >= 20 and not stats['done']:
                    raise SystemExit('连续 20 条转写失败，已停止。请检查语音识别服务。')
        progress.end('asr')
        message = f'转写完成：新转写 {stats["done"]} 条' + (f'，失败 {stats["failed"]} 条' if stats['failed'] else '')
        progress.finish('done' if not stats['failed'] else 'done-with-problems', message)
        print(message)
        return 0
    except BaseException as e:
        progress.finish('stopped' if isinstance(e, KeyboardInterrupt) else 'failed', str(e)[:600])
        raise
