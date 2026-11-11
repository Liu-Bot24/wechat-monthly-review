"""User configuration, kept apart from the program so the program folder can be replaced on upgrade.

Everything lives in HOME: MR_HOME when set; else the 配置 folder beside the program, when there is one (the portable build);
else %APPDATA%\\ChatMonthly:
  settings.json   general and advanced settings; missing keys fall back to DEFAULTS
  models.json     providers, models and which model each stage uses; the program ships none
  会话选择.json    which chats are read (see selection.py)
  勾选/           the to-do ticks in each month's report
export_config / import_config carry these to another installation as one .zip. Keys are never written here: a provider's key is
typed in the settings and kept in Windows Credential Manager, and read from there only when a request is made.
"""
import copy, hashlib, json, os, re, shutil, sys, time, zipfile
from pathlib import Path

PORTABLE = '配置'

def _home():
    if os.environ.get('MR_HOME'):
        return Path(os.environ['MR_HOME'])
    if getattr(sys, 'frozen', False):
        beside = Path(sys.executable).parent / PORTABLE
        if beside.is_dir():                 # the portable build: the configuration travels with the program folder
            return beside
    return Path(os.environ.get('APPDATA', Path.home())) / 'ChatMonthly'

HOME = _home()
CREDENTIAL_SERVICE = 'ChatMonthly'
KEY_TYPES = ('none', 'credential')

DEFAULTS = {
    'archive_root': '',                    # chosen on first use; there is no place an archive is found by default
    'output_root': str(Path.home() / '聊天记录月报'),   # not Documents: OneDrive often syncs it, and reports quote private chats
    'budget_tokens': None,                 # per month; None means no cap
    'concurrency': 6,
    'notify': True,
    'usage_since': None,                   # usage page counts calls from this time (epoch seconds); None = all
    'usage_shown': [],                     # months and 专题 ('专题/<id>') whose earlier calls are counted all the same
    'markdown_viewer': '',                 # program that opens archive Markdown at an anchor: <exe> "<path>#<anchor>"; empty = default program
    'context_hours': 72,
    'chunk_tokens': {'read_a': 24000, 'scan_b': 10000},
    'overlap_lines': 30,
    'small_group_members': 50,
    'active_group': {'self_messages': 10, 'max_messages': 3000},
    'segment_lines': 300,
    'window_lines': 40,
    'images': {'max_side': 1280, 'max_per_month': 3000, 'per_call': 4},
    'asr': True,
    'recall': True,
    'verify': True,
    'vision': True,
    'external_stages': [],                 # stages whose calls an external agent answers through files (see external.py)
    'external_root': '',                   # where those files go; empty = 外部处理 inside the output folder
    'external_audit': 5,                   # percent of the agent's answers re-asked of the stage's model and compared; 0 = none
    'default_skip_categories': [],         # category folders are the archive's own; none is assumed
    'whole_chat_a_categories': [],
}

# The stages are part of the pipeline; only the model each one uses is the user's choice.
# needs: what a model must support. switch: the setting that turns an optional stage off.
STAGES = [
    {'key': 'asr', 'label': '语音转写', 'needs': 'audio', 'switch': 'asr', 'desc': '读取之前把语音转成文字，存为语音文件旁边的同名 .txt，所有月份和专题共用。需要语音转写接口。'},
    {'key': 'scan_b', 'label': '扫读', 'needs': 'text', 'desc': '我没有参与的群聊，按话题摘要，量最大。适合便宜、速度快的模型。'},
    {'key': 'read_a', 'label': '精读', 'needs': 'text', 'desc': '私聊、工作群和我参与的对话，写结论卡。决定报告质量的主要环节。'},
    {'key': 'escalate', 'label': '申请精读', 'needs': 'text', 'desc': '扫读时发现与我有关的片段，重新精读。调用很少。'},
    {'key': 'recall_a', 'label': '查漏', 'needs': 'text', 'switch': 'recall', 'desc': '第二位阅读者对照原文和已有的卡片，只补写漏掉的内容。用和精读不同的模型效果更好。'},
    {'key': 'verify', 'label': '结论卡复核', 'needs': 'text', 'switch': 'verify', 'desc': '把每张卡和它引用的原消息逐张核对：是谁说的、日期金额、说法和事实。'},
    {'key': 'vision', 'label': '看图', 'needs': 'vision', 'switch': 'vision', 'desc': '看阅读时申请的图片。按上下文判断为隐私的图片不会发送。需要支持图片的模型。'},
    {'key': 'at_me', 'label': '@我', 'needs': 'text', 'desc': '逐条说明每条 @我 是什么事、要我做什么。'},
    {'key': 'compose_category', 'label': '分类详情', 'needs': 'text', 'desc': '按会话分类写详情。'},
    {'key': 'compose_main', 'label': '总览成稿', 'needs': 'text', 'desc': '一页速览、需要你处理、本月要事和洞察。建议用最强的模型。'},
]
STAGE_KEYS = [s['key'] for s in STAGES]
# Stages an external agent can answer: text in, text out. Images and audio stay with their models.
EXTERNAL_STAGES = [s['key'] for s in STAGES if s['needs'] == 'text']
# Fallbacks are shared by every stage: when a stage's model keeps failing, the call is asked again of these, in order.
FALLBACK_SLOTS = 2
APIS = {'responses': 'OpenAI Responses', 'chat': 'OpenAI Chat Completions', 'audio': '语音转写（OpenAI 兼容）'}
_NAME = re.compile(r'^[^\s/\\:*?"<>|]{1,60}$')


class ConfigError(ValueError):
    """A user-facing reason why a change was refused."""


def _read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else copy.deepcopy(default)

def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ---------- settings ----------
def settings_path():
    return HOME / 'settings.json'

def load_settings():
    return _merge(DEFAULTS, _read(settings_path(), {}))

def save_settings(patch):
    """Merge patch into the stored settings and return the result. Only known keys are accepted."""
    unknown = sorted(set(patch) - set(DEFAULTS))
    if unknown:
        raise ConfigError('不认识的设置项：' + '、'.join(unknown))
    stored = {k: v for k, v in _merge(_read(settings_path(), {}), patch).items() if k in DEFAULTS}   # settings this version no longer has are dropped
    merged = _merge(DEFAULTS, stored)
    b = merged['budget_tokens']
    if b is not None and (not isinstance(b, int) or b <= 0):
        raise ConfigError('每月预算要么不限，要么是正整数')
    if not isinstance(merged['concurrency'], int) or not 1 <= merged['concurrency'] <= 32:
        raise ConfigError('并发数应在 1 到 32 之间')
    if not isinstance(merged['usage_shown'], list) or not all(isinstance(x, str) for x in merged['usage_shown']):
        raise ConfigError('恢复统计的清单应是月份或专题的列表')
    handed = merged['external_stages']
    if not isinstance(handed, list) or any(s not in EXTERNAL_STAGES for s in handed):
        raise ConfigError('交给外部 Agent 的只能是文字环节')
    if not isinstance(merged['external_root'], str):
        raise ConfigError('交接文件夹应是一个路径')
    a = merged['external_audit']
    if isinstance(a, bool) or not isinstance(a, int) or not 0 <= a <= 100:
        raise ConfigError('抽查比例应在 0 到 100 之间')
    _write(settings_path(), stored)
    return merged

# ---------- carrying the configuration to another installation ----------
CONFIG_FILES = ('settings.json', 'models.json', '会话选择.json')
TICKS = '勾选'
_TICK_NAME = re.compile(r'^[^/\\:*?"<>|]+\.json$')

def _config_entries(read):
    """{name in the bundle: bytes} from read(name) -> bytes | None, checked before anything is written."""
    out = {}
    for name in CONFIG_FILES:
        data = read(name)
        if data is None:
            continue
        try:
            v = json.loads(data.decode('utf-8-sig'))
        except (UnicodeDecodeError, ValueError):
            raise ConfigError(f'{name} 不是有效的 JSON，没有导入任何内容。')
        if not isinstance(v, dict) or (name == 'models.json' and not all(isinstance(v.get(k, {}), dict) for k in ('providers', 'models', 'stages'))):
            raise ConfigError(f'{name} 的内容不对，没有导入任何内容。')
        out[name] = data
    if not any(n in out for n in ('settings.json', 'models.json')):
        raise ConfigError('这里没有消息月报的配置（找不到 settings.json 或 models.json）。')
    return out

def export_config(dest):
    """Writes the configuration to dest (.zip). Returns what went in."""
    dest = Path(dest)
    if dest.suffix.lower() != '.zip':
        raise ConfigError('导出的文件要以 .zip 结尾。')
    names = [n for n in CONFIG_FILES if (HOME / n).exists()]
    ticks = sorted(p.name for p in (HOME / TICKS).glob('*.json')) if (HOME / TICKS).is_dir() else []
    if not names:
        raise ConfigError(f'配置目录里还没有配置：{HOME}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + '.tmp')
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps({'app': 'ChatMonthly', 'exported_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                                                'files': names, 'ticks': len(ticks)}, ensure_ascii=False, indent=2))
        for n in names:
            z.write(HOME / n, n)
        for t in ticks:
            z.write(HOME / TICKS / t, f'{TICKS}/{t}')
    tmp.replace(dest)
    return {'path': str(dest), 'files': names, 'ticks': len(ticks)}

def import_config(src):
    """Replaces the configuration with the one in src: a .zip from export_config, or a configuration folder.

    What is replaced is first copied to HOME/导入前备份/<time>. Files the source does not have are kept."""
    src = Path(src)
    if src.is_dir():
        if src.resolve() == HOME.resolve():
            raise ConfigError('这就是当前的配置目录。')
        entries = _config_entries(lambda n: (src / n).read_bytes() if (src / n).is_file() else None)
        tick_dir = src / TICKS
        ticks = {p.name: p.read_bytes() for p in tick_dir.glob('*.json')} if tick_dir.is_dir() else {}
    elif src.is_file() and src.suffix.lower() == '.zip':
        try:
            z = zipfile.ZipFile(src)
        except zipfile.BadZipFile:
            raise ConfigError('这个文件不是有效的 .zip。')
        with z:
            listed = set(z.namelist())
            entries = _config_entries(lambda n: z.read(n) if n in listed else None)
            ticks = {n.split('/', 1)[1]: z.read(n) for n in listed
                     if n.startswith(TICKS + '/') and _TICK_NAME.match(n.split('/', 1)[1])}
    else:
        raise ConfigError(f'找不到配置：{src}')
    backup = HOME / '导入前备份' / time.strftime('%Y%m%d-%H%M%S')
    kept = [n for n in entries if (HOME / n).exists()] + [f'{TICKS}/{t}' for t in ticks if (HOME / TICKS / t).exists()]
    for n in kept:
        (backup / n).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HOME / n, backup / n)
    for n, data in entries.items():
        _write_bytes(HOME / n, data)
    for t, data in ticks.items():
        _write_bytes(HOME / TICKS / t, data)
    return {'files': list(entries), 'ticks': len(ticks), 'backup': str(backup) if kept else None}

def _write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)

def default_export_path():
    return str(Path.home() / 'Desktop' / f'消息月报配置_{time.strftime("%Y-%m-%d")}.zip')

def latest_export():
    """The newest bundle exported to the desktop under the default name, offered first on import."""
    found = sorted((Path.home() / 'Desktop').glob('消息月报配置_*.zip'), key=lambda p: p.stat().st_mtime)
    return str(found[-1]) if found else ''


def selection_path():
    return HOME / '会话选择.json'


# ---------- providers, models, stages ----------
def models_path():
    return HOME / 'models.json'

def load_models():
    data = _read(models_path(), {'providers': {}, 'models': {}, 'stages': {}})
    for k in ('providers', 'models', 'stages'):
        data.setdefault(k, {})
    fb = data.get('fallback') if isinstance(data.get('fallback'), dict) else {}
    names = [n if isinstance(n, str) else '' for n in fb.get('models', [])][:FALLBACK_SLOTS]
    data['fallback'] = {'enabled': fb.get('enabled', True) is not False, 'models': names + [''] * (FALLBACK_SLOTS - len(names))}
    for p in data['providers'].values():
        if (p.get('key') or {}).get('type', 'none') not in KEY_TYPES:   # a source earlier versions had: the key now has to be typed in
            p['key'] = {'type': 'credential'}
    return data

def _save_models(data):
    _write(models_path(), data)
    return data

def _check_name(name, what):
    if not isinstance(name, str) or not _NAME.match(name):
        raise ConfigError(f'{what}名称不能为空，不超过 60 个字，不能有空格和 / \\ : * ? " < > |')

def _clean_provider(p):
    out = {'label': str(p.get('label') or '').strip(), 'base_url': str(p.get('base_url') or '').strip().rstrip('/'),
           'api': p.get('api') or 'responses', 'key': p.get('key') or {'type': 'none'}}
    if not re.match(r'^https?://', out['base_url']):
        raise ConfigError('接口地址要以 http:// 或 https:// 开头')
    if out['api'] not in APIS:
        raise ConfigError('协议只能是：' + '、'.join(APIS.values()))
    if out['key'].get('type') not in KEY_TYPES:
        raise ConfigError('密钥只能是：不需要，或者填写后存进 Windows 凭据管理器')
    out['key'] = {'type': out['key']['type']}
    return out

def save_provider(name, data, old_name=None):
    """Add (old_name None) or change a provider; renaming carries its models and stored key along."""
    _check_name(name, '接口')
    m = load_models()
    if old_name is None and name in m['providers']:
        raise ConfigError(f'已经有名为"{name}"的接口')
    if old_name is not None and old_name not in m['providers']:
        raise ConfigError(f'没有名为"{old_name}"的接口')
    if old_name and name != old_name and name in m['providers']:
        raise ConfigError(f'已经有名为"{name}"的接口')
    clean = _clean_provider(data)
    if old_name and name != old_name:
        m['providers'] = _renamed(m['providers'], old_name, name)
        for spec in m['models'].values():
            if spec['provider'] == old_name:
                spec['provider'] = name
        secret = credential_get(old_name)
        if secret:
            credential_set(name, secret)
            credential_delete(old_name)
    m['providers'][name] = clean
    return _save_models(m)

def delete_provider(name):
    m = load_models()
    used = [k for k, v in m['models'].items() if v['provider'] == name]
    if used:
        raise ConfigError(f'还有模型使用这个接口：{"、".join(used)}。请先删除或改用其他接口。')
    m['providers'].pop(name, None)
    credential_delete(name)
    return _save_models(m)

def _clean_model(m, d):
    provider = d.get('provider')
    if provider not in m['providers']:
        raise ConfigError('请选择已有的接口')
    model = str(d.get('model') or '').strip()
    if not model:
        raise ConfigError('模型名不能为空')
    params = d.get('params') or {}
    if not isinstance(params, dict):
        raise ConfigError('参数要写成 JSON 对象')
    price = d.get('price') or {}
    clean_price = {}
    for k in ('input', 'cached_input', 'output'):
        v = price.get(k)
        if v not in (None, ''):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ConfigError('单价要填数字（每百万 token）')
            if v < 0:
                raise ConfigError('单价不能是负数')
            clean_price[k] = v
    if clean_price:
        clean_price['currency'] = price.get('currency') or 'USD'
    if price.get('local'):
        clean_price = {'local': True}               # runs on a machine of the user's own: no per-token cost
    return {'provider': provider, 'model': model, 'params': params, 'vision': bool(d.get('vision')),
            'price': clean_price, 'note': str(d.get('note') or '').strip()}

def save_model(name, data, old_name=None):
    """Add or change a model. Renaming keeps every stage that used it pointing at it."""
    _check_name(name, '模型')
    m = load_models()
    if old_name is None and name in m['models']:
        raise ConfigError(f'已经有名为"{name}"的模型')
    if old_name is not None and old_name not in m['models']:
        raise ConfigError(f'没有名为"{old_name}"的模型')
    if old_name and name != old_name and name in m['models']:
        raise ConfigError(f'已经有名为"{name}"的模型')
    clean = _clean_model(m, data)
    if old_name and name != old_name:
        m['models'] = _renamed(m['models'], old_name, name)
        for stage, chosen in m['stages'].items():
            if chosen == old_name:
                m['stages'][stage] = name
        m['fallback']['models'] = [name if n == old_name else n for n in m['fallback']['models']]
    m['models'][name] = clean
    if m['providers'][clean['provider']]['api'] == 'audio':      # a transcription model cannot stand in for a reading one
        m['fallback']['models'] = ['' if n == name else n for n in m['fallback']['models']]
    # A stage keeps a model only while the model still meets what the stage needs.
    for stage, chosen in list(m['stages'].items()):
        if chosen == name and not _fits(m, stage, name):
            del m['stages'][stage]
    return _save_models(m)

def _renamed(d, old, new):
    """d with the key old renamed to new, in the same place: the order is the user's (lists and menus follow it)."""
    return {new if k == old else k: v for k, v in d.items()}

def move_model(name, by):
    """Moves a model up (by < 0) or down in the list; the stage menus list models in this order."""
    m = load_models()
    names = list(m['models'])
    if name not in names:
        raise ConfigError(f'没有名为"{name}"的模型')
    i = names.index(name)
    j = max(0, min(len(names) - 1, i + by))
    names.insert(j, names.pop(i))
    m['models'] = {n: m['models'][n] for n in names}
    return _save_models(m)

def delete_model(name):
    m = load_models()
    used = [s['label'] for s in STAGES if m['stages'].get(s['key']) == name]
    used += [FALLBACK_LABELS[i] for i, n in enumerate(m['fallback']['models']) if n == name]
    if used:
        raise ConfigError(f'这些环节正在使用这个模型：{"、".join(used)}。请先为它们换一个模型。')
    m['models'].pop(name, None)
    return _save_models(m)

def _fits(m, stage, name):
    spec, need = m['models'][name], next((s['needs'] for s in STAGES if s['key'] == stage), 'text')
    api = m['providers'][spec['provider']]['api']
    if need == 'audio':
        return api == 'audio'
    if api == 'audio':
        return False
    return spec['vision'] if need == 'vision' else True

def fit_reason(m, stage, name):
    """Why a model cannot serve a stage, or None."""
    if _fits(m, stage, name):
        return None
    need = next(s['needs'] for s in STAGES if s['key'] == stage)
    return {'audio': '这个环节需要语音转写接口', 'vision': '这个环节需要支持图片的模型', 'text': '语音转写接口不能用于阅读和写作'}[need]

def set_stage(stage, name):
    m = load_models()
    if stage not in STAGE_KEYS:
        raise ConfigError(f'没有"{stage}"这个环节')
    if name in (None, ''):
        m['stages'].pop(stage, None)
        return _save_models(m)
    if name not in m['models']:
        raise ConfigError(f'没有名为"{name}"的模型')
    reason = fit_reason(m, stage, name)
    if reason:
        raise ConfigError(reason)
    m['stages'][stage] = name
    return _save_models(m)

FALLBACK_LABELS = ['兜底一', '兜底二']

def set_fallback(enabled=None, models=None):
    """Turn the fallbacks on or off, or choose them: models is a list of up to FALLBACK_SLOTS names, '' for an empty slot."""
    m = load_models()
    if enabled is not None:
        m['fallback']['enabled'] = bool(enabled)
    if models is not None:
        if not isinstance(models, list) or len(models) > FALLBACK_SLOTS:
            raise ConfigError(f'兜底模型最多 {FALLBACK_SLOTS} 个')
        names = [n or '' for n in models]
        for n in names:
            if n and n not in m['models']:
                raise ConfigError(f'没有名为"{n}"的模型')
            if n and m['providers'][m['models'][n]['provider']]['api'] == 'audio':
                raise ConfigError('语音转写接口不能用作兜底')
        if len([n for n in names if n]) != len({n for n in names if n}):
            raise ConfigError('两个兜底不能选同一个模型')
        m['fallback']['models'] = names + [''] * (FALLBACK_SLOTS - len(names))
    return _save_models(m)

def fallbacks_for(m, stage):
    """The fallback models that can serve this stage, in order: none when turned off; a stage that needs images gets only
    models that take images, and transcription has no fallback."""
    if not m.get('fallback', {}).get('enabled') or next((s['needs'] for s in STAGES if s['key'] == stage), 'text') == 'audio':
        return []
    return [n for n in m['fallback']['models'] if n in m['models'] and _fits(m, stage, n)]

def missing_stages(m, settings):
    """Stages that would run but have no model."""
    return [s for s in STAGES if not m['stages'].get(s['key']) and (not s.get('switch') or settings.get(s['switch']))]

def call_spec(m, stage):
    """What actually decides an answer: where it is sent and how. Names and prices are left out on purpose."""
    spec = m['models'][m['stages'][stage]]
    p = m['providers'][spec['provider']]
    return {'base_url': p['base_url'], 'api': p['api'], 'model': spec['model'], 'params': spec['params']}


# ---------- keys ----------
def credential_get(provider):
    import keyring
    try:
        return keyring.get_password(CREDENTIAL_SERVICE, provider)
    except Exception:
        return None

def credential_set(provider, secret):
    import keyring
    keyring.set_password(CREDENTIAL_SERVICE, provider, secret)

def credential_delete(provider):
    import keyring
    try:
        keyring.delete_password(CREDENTIAL_SERVICE, provider)
    except Exception:
        pass

def resolve_key(provider_name, provider):
    """The provider's key, read only now and never stored or printed."""
    key = provider.get('key') or {'type': 'none'}
    if key['type'] == 'none':
        return 'local'
    secret = credential_get(provider_name)
    if not secret:
        raise ConfigError(f'接口"{provider_name}"还没有保存密钥：请在 设置 › 接口与模型 中填写')
    return secret


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:12]
