"""Replace credentials in chat text with typed placeholders before any model sees it.

A placeholder names what the value was (`⟦API Key·OpenAI·K3⟧`) so a model can still reason
about it and the report can say what was shared. The original value is never stored: only a
short hash, so the same value keeps the same code across messages.
"""
import hashlib, re
from dataclasses import dataclass, field

PROVIDERS = [
    (r'openai|chatgpt|gpt', 'OpenAI'), (r'anthropic|claude', 'Anthropic'), (r'deepseek|深度求索', 'DeepSeek'),
    (r'gemini|google|谷歌', 'Google'), (r'siliconflow|硅基', 'SiliconFlow'), (r'kimi|moonshot|月之暗面', 'Moonshot'),
    (r'智谱|glm|zhipu', '智谱'), (r'通义|qwen|dashscope|百炼', '阿里云百炼'), (r'openrouter', 'OpenRouter'),
    (r'github', 'GitHub'), (r'\baws\b|amazon', 'AWS'), (r'腾讯云|tencent', '腾讯云'), (r'百度|文心|baidu', '百度'),
    (r'阿里云|aliyun', '阿里云'), (r'grok|xai', 'xAI'),
]
_PROVIDER_RES = [(re.compile(p, re.I), name) for p, name in PROVIDERS]

def provider_hint(context):
    for pattern, name in _PROVIDER_RES:
        if pattern.search(context or ''):
            return name
    return None

def luhn(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9: n -= 9
        total += n
    return total % 10 == 0

_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
def id_card_ok(value):
    total = sum(int(d) * w for d, w in zip(value[:17], _ID_WEIGHTS))
    return '10X98765432'[total % 11] == value[17].upper()

# (regex, code letter, label, group holding the value, validator). Order matters: specific
# formats first, so a key is not swallowed by the generic keyword rules.
_V = r'[:：=\s]*(?:是|为)?[:：=\s]*'
RULES = [
    (r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----', 'S', '私钥', 0, None),
    (r'(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{20,}', 'K', 'API Key·Anthropic', 0, None),
    (r'(?<![A-Za-z0-9])sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}', 'K', 'API Key', 0, None),
    (r'(?<![A-Za-z0-9])AIza[0-9A-Za-z_\-]{35}', 'K', 'API Key·Google', 0, None),
    (r'(?<![A-Za-z0-9])(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}', 'K', 'Token·GitHub', 0, None),
    (r'(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])', 'K', 'AccessKey·AWS', 0, None),
    (r'(?<![A-Za-z0-9])LTAI[0-9A-Za-z]{12,30}(?![A-Za-z0-9])', 'K', 'AccessKey·阿里云', 0, None),
    (r'(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9\-]{10,}', 'K', 'Token·Slack', 0, None),
    (r'(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}', 'K', 'Token·JWT', 0, None),
    (r'(?:api[\s_\-]?key|apikey|access[\s_\-]?key|secret[\s_\-]?key|token|密钥|秘钥|key)' + _V + r'([A-Za-z0-9_\-\.]{16,})', 'K', 'API Key', 1, None),
    # ｜ separates fields in the program's own lists ("账号密码｜<chat>｜<time>"); it is not part of a value.
    (r'(?:密码|口令|password|passwd|pwd|pass)' + _V + r'([^\s，。,；;：:、｜一-鿿]{4,40})', 'P', '密码', 1, None),
    (r'(?:验证码|校验码|动态码|verification code)' + _V + r'(\d{4,8})(?!\d)', 'V', '验证码', 1, None),
    (r'(?:账号|帐号|账户|帐户|用户名|登录名|username|user\s?name|account)' + _V + r'([A-Za-z0-9_@.\-]{3,64})', 'A', '账号', 1, None),
    (r'(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dA-Za-z])', 'I', '身份证号', 0, id_card_ok),
    (r'(?<!\d)(?:62|4\d|5[1-5])\d{14,17}(?!\d)', 'B', '银行卡号', 0, luhn),
    # Long mixed letter+digit strings outside URLs are usually keys or tokens.
    # Needs upper, lower and digit; not an @name, file name or URL-encoded fragment.
    (r'(?<![A-Za-z0-9/=_\-\.@%])(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[a-z])(?=[A-Za-z0-9_\-]*[A-Z])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9/=_\-\.%])', 'K', '疑似密钥', 0, None),
]
_RULES = [(re.compile(p, re.I if code in 'KPVA' and grp else 0), code, label, grp, check) for p, code, label, grp, check in RULES]
_URL = re.compile(r'https?://\S+')
_PLACEHOLDER = re.compile(r'⟦[^⟦⟧]*⟧')

@dataclass
class Masker:
    """One per run, so the same value gets the same code everywhere in the month."""
    codes: dict = field(default_factory=dict)      # hash -> placeholder
    counters: dict = field(default_factory=dict)   # code letter -> n
    found: list = field(default_factory=list)      # (placeholder, label, where)
    keep_values: bool = False                      # only for showing originals to me on this computer; never saved
    skip: tuple = ()                               # labels whose rules are not applied

    def _placeholder(self, value, code, label, where):
        digest = hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]
        if digest not in self.codes:
            self.counters[code] = self.counters.get(code, 0) + 1
            self.codes[digest] = f'⟦{label}·{code}{self.counters[code]}⟧'
        placeholder = self.codes[digest]
        self.found.append({'placeholder': placeholder, 'label': label, 'where': where, 'hash': digest,
                           **({'value': value} if self.keep_values else {})})
        return placeholder

    def mask(self, text, context='', where=None):
        if not text:
            return text
        # URLs are replaced with their site later; keep keys hidden inside query strings too.
        spans = [(m.start(), m.end()) for m in _URL.finditer(text)]
        for pattern, code, label, grp, check in _RULES:
            if label in self.skip:
                continue
            def repl(m):
                value = m.group(grp)
                if _PLACEHOLDER.search(m.group(0)):
                    return m.group(0)
                if check and not check(value):
                    return m.group(0)
                inside_url = any(a <= m.start() < b for a, b in spans)
                if label == '疑似密钥' and inside_url:
                    return m.group(0)
                final = label
                if label in ('API Key', '疑似密钥', 'API Key·Anthropic') or label.startswith('Token'):
                    hint = provider_hint(text + ' ' + (context or ''))
                    if hint and '·' not in label:
                        final = f'{label}·{hint}'
                placeholder = self._placeholder(value, code, final, where)
                start, end = m.span(grp)
                return m.group(0)[:start - m.start()] + placeholder + m.group(0)[end - m.start():]
            text = pattern.sub(repl, text)
            spans = [(m.start(), m.end()) for m in _URL.finditer(text)]
        return text

# A report names accounts in its own words ("账号密码｜<chat>"), so account names are not checked there.
_REPORT_SKIP = ('账号',)
# Kept as they are: placeholders, message links ("[#123](<…>)", "[05-29]") with their archive paths.
_KEEP = re.compile(r'(⟦[^⟦⟧]*⟧|\[#?[\d-]+\](?:\((?:<[^>]*>|file:[^)]*)\))?)')

def leaks(text):
    """Patterns that should never survive into a report (placeholders and message links excepted)."""
    return scrub(text)[1]

def scrub(text):
    """The report with anything leaks() would flag replaced by a placeholder; returns (text, labels replaced).
    New codes continue after the ones the text already uses, so they never name another value."""
    masker = Masker(skip=_REPORT_SKIP)
    for m in re.finditer(r'⟦[^⟦⟧]*·([A-Z])(\d+)⟧', text):
        masker.counters[m.group(1)] = max(masker.counters.get(m.group(1), 0), int(m.group(2)))
    parts = _KEEP.split(text)          # link targets and placeholders stay as they are
    out = ''.join(part if i % 2 else masker.mask(part) for i, part in enumerate(parts))
    return out, [f['label'] for f in masker.found]
