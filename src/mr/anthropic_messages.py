"""Providers whose 协议 is Anthropic Messages: Claude itself, and services that offer the same interface.

An answer comes back as the OpenAI interfaces give theirs to the rest of the program: (text, usage, finish), with
usage counted the OpenAI way (prompt tokens include the cached ones) and finish 'stop' for a reply that ended on its
own, 'content_filter' for a refusal, anything else for a reply that did not end normally.
"""
import re
import anthropic

ERRORS = (anthropic.APIError,)
AUTH_ERRORS = (anthropic.AuthenticationError, anthropic.PermissionDeniedError)
CONNECTION_ERRORS = (anthropic.APIConnectionError,)
NOT_FOUND = anthropic.NotFoundError

MAX_TOKENS = 32000          # the reply's ceiling when the model's 其他参数 name no max_tokens; thinking counts towards it
FINISH = {'end_turn': 'stop', 'stop_sequence': 'stop', 'refusal': 'content_filter', 'max_tokens': 'length'}
_DATA_URL = re.compile(r'data:(image/[\w.+-]+);base64,(.*)', re.S)
_FENCE = re.compile(r'```(?:json)?[ \t]*\r?\n?(.*?)\r?\n?```', re.S)


def base_url(url):
    """The SDK adds /v1/messages itself, so an address written the OpenAI way, ending in /v1, works as well."""
    url = (url or '').strip().rstrip('/')
    return url[:-3] if url.endswith('/v1') else url

def client(url, key, timeout):
    # The key is always passed, so the SDK reads no credentials of its own from the environment or a profile:
    # a key meant for one service is never sent to the address given here.
    return anthropic.Anthropic(api_key=key, base_url=base_url(url), timeout=timeout, max_retries=0)

def models(c):
    return sorted(m.id for m in c.models.list())

def _image(url):
    m = _DATA_URL.match(url)
    if m:
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': m[1], 'data': m[2]}}
    return {'type': 'image', 'source': {'type': 'url', 'url': url}}

def _json_text(text):
    """The JSON object in a reply: without a code fence around it, or words before and after it."""
    t = text.strip()
    fenced = _FENCE.fullmatch(t)
    if fenced:
        t = fenced[1].strip()
    if not t.startswith('{') and '{' in t and '}' in t:
        t = t[t.index('{'):t.rindex('}') + 1]
    return t

def ask(c, spec, system, user, json_mode, images=None):
    """One call. The model's 其他参数 go into the request body as they are, except max_tokens (the reply's ceiling)
    and reasoning_effort (the 推理强度 setting, sent as output_config.effort)."""
    params = dict(spec.get('params', {}))
    max_tokens = params.pop('max_tokens', MAX_TOKENS)
    if 'reasoning_effort' in params:
        params['output_config'] = {**(params.get('output_config') or {}), 'effort': params.pop('reasoning_effort')}
    if json_mode:
        # This interface has no plain "JSON object" mode; the instructions ask for one and the reply is read as one.
        user = user + '\n\n（请只输出一个 JSON 对象，不要输出其他文字。）'
    content = [_image(u) for u in images] + [{'type': 'text', 'text': user}] if images else user
    # The instructions are the same for every block of a stage: marked for caching, so that later calls read them
    # at the cached price (a service that does not cache ignores the mark).
    kwargs = dict(model=spec['model'], max_tokens=max_tokens, messages=[{'role': 'user', 'content': content}],
                  system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}])
    if params:
        kwargs['extra_body'] = params
    with c.messages.stream(**kwargs) as s:            # streamed: a long reply does not run into the request timeout
        m = s.get_final_message()
    text = ''.join(b.text for b in m.content if b.type == 'text')
    u = m.usage
    read, written = getattr(u, 'cache_read_input_tokens', 0) or 0, getattr(u, 'cache_creation_input_tokens', 0) or 0
    usage = {'prompt_tokens': (u.input_tokens or 0) + read + written, 'completion_tokens': u.output_tokens or 0,
             'cached_tokens': read, 'reasoning_tokens': 0}
    return (_json_text(text) if json_mode else text), usage, FINISH.get(m.stop_reason, m.stop_reason or 'unknown')
