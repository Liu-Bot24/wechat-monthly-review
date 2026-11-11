"""Local media for the model: images shrunk to a data URL, voice clips transcribed by the local speech service."""
import base64, hashlib, io, json, time
from pathlib import Path
import openai
from PIL import Image
from .config import resolve_key

def image_data_url(path, max_side):
    """JPEG data URL of the image, longest side at most max_side, and a hash of the bytes sent."""
    with Image.open(path) as im:
        im = im.convert('RGB')
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=85)
    data = buf.getvalue()
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii'), hashlib.sha256(data).hexdigest()[:16]


def transcript_path(audio_path):
    """Where a voice clip's text lives: a .txt of the same name beside it in the archive, readable without this tool."""
    return Path(audio_path).with_suffix('.txt')

def read_transcript(audio_path):
    """The clip's text ('' when it holds no speech), or None when it has not been transcribed."""
    path = transcript_path(audio_path)
    try:
        return path.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return None

def write_transcript(audio_path, text):
    path = transcript_path(audio_path)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)

def spread_transcripts(archive, paths):
    """Gives every copy of each clip (paths relative to the archive) the text it already has. Returns how many files were written."""
    written = 0
    for rel in paths:
        text = read_transcript(archive.path(rel))
        if text is None:
            continue
        for path in archive.voice_copies(rel):
            if read_transcript(path) is None:
                write_transcript(path, text)
                written += 1
    return written

def voice_pace(output_root):
    """Measured seconds per clip, for estimates; None until something has been transcribed."""
    try:
        p = json.loads((Path(output_root) / '语音转写' / 'pace.json').read_text(encoding='utf-8'))
        return p['seconds'] / p['clips'] if p.get('clips') else None
    except (OSError, ValueError, KeyError):
        return None

def _add_pace(store, clips, seconds):
    path = store / 'pace.json'
    try:
        p = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        p = {'clips': 0, 'seconds': 0.0}
    p = {'clips': p.get('clips', 0) + clips, 'seconds': round(p.get('seconds', 0.0) + seconds, 2)}
    tmp = store / 'pace.tmp'
    tmp.write_text(json.dumps(p), encoding='utf-8')
    tmp.replace(path)


class Transcriber:
    """Voice-to-text before reading. Each result is a text file beside its clip in the archive, shared by every month and 专题."""

    def __init__(self, models_config, output_root, archive=None):
        name = models_config['stages']['asr']
        spec = models_config['models'][name]
        self.provider_name = spec['provider']
        self.provider = models_config['providers'][self.provider_name]
        self.base_url = self.provider['base_url']
        self.model = spec['model']
        self.params = dict(spec.get('params') or {})
        self._client = None
        self.archive = archive
        self.store = Path(output_root) / '语音转写'       # job state and the measured pace; the texts are in the archive
        self.store.mkdir(parents=True, exist_ok=True)

    @property
    def client(self):
        # Made on first use, so counting what is already transcribed needs no key and no running service.
        if self._client is None:
            self._client = openai.OpenAI(base_url=self.base_url, api_key=resolve_key(self.provider_name, self.provider),
                                         timeout=600, max_retries=1)
        return self._client

    def cached(self, audio_path):
        return read_transcript(audio_path)

    def transcribe(self, audio_path, rel=None):
        """With rel, the clip's path inside the archive (and the archive), the text is also written beside its copies in other chats."""
        hit = self.cached(audio_path)
        if hit is not None:
            return hit
        started = time.time()
        params = {'prompt': '以下是普通话的句子，使用简体中文。', **self.params}   # the prompt nudges Whisper towards simplified Chinese
        with open(audio_path, 'rb') as f:
            r = self.client.audio.transcriptions.create(model=self.model, file=f, response_format='json', **params)
        text = (r.text or '').strip()
        write_transcript(audio_path, text)
        if rel and self.archive is not None:
            spread_transcripts(self.archive, [rel])
        _add_pace(self.store, 1, time.time() - started)
        return text
