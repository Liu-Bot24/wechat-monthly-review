"""Providers on the Anthropic Messages interface, against a stand-in service that speaks it on localhost:
what is sent, how the reply is read, and how a run, the connection test and the model test use them."""
import json, os, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from mr import anthropic_messages as am, config, llm
from mr.llm import Models


class Service:
    """Answers POST /v1/messages as a stream, GET /v1/models with a list (or 404), and keeps what it was sent."""
    def __init__(self):
        self.requests, self.reply, self.stop, self.status, self.models = [], '{"ok": true}', 'end_turn', 200, 200
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, kind='application/json'):
                data = body.encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                service.requests.append({'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()}})
                if service.models != 200:
                    return self._send(service.models, json.dumps({'type': 'error', 'error': {'type': 'not_found_error', 'message': 'no'}}))
                self._send(200, json.dumps({'data': [{'type': 'model', 'id': i, 'display_name': i, 'created_at': '2026-01-01T00:00:00Z'}
                                                     for i in ('m-b', 'm-a')], 'has_more': False, 'first_id': 'm-b', 'last_id': 'm-a'}))

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                service.requests.append({'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': body})
                if service.status != 200:
                    kind = {401: 'authentication_error', 400: 'invalid_request_error'}.get(service.status, 'api_error')
                    return self._send(service.status, json.dumps({'type': 'error', 'error': {'type': kind, 'message': 'refused'}}))
                usage = {'input_tokens': 10, 'output_tokens': 1, 'cache_read_input_tokens': 30, 'cache_creation_input_tokens': 5}
                events = [('message_start', {'type': 'message_start', 'message': {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': body['model'],
                                                                                   'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': usage}}),
                          ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})]
                events += [('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': part}})
                           for part in (service.reply[:5], service.reply[5:])]
                events += [('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
                           ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': service.stop, 'stop_sequence': None}, 'usage': {'output_tokens': 7}}),
                           ('message_stop', {'type': 'message_stop'})]
                self._send(200, ''.join(f'event: {e}\ndata: {json.dumps(d)}\n\n' for e, d in events), 'text/event-stream')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AnthropicMessagesTest(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.addCleanup(self.service.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A key in the environment must never be what is sent: the provider's own key (or none) is.
        env = mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'from-the-environment', 'ANTHROPIC_AUTH_TOKEN': 'token-from-the-environment'})
        env.start()
        self.addCleanup(env.stop)

    def provider(self, url=None):
        return {'base_url': url or self.service.url, 'api': 'anthropic', 'key': {'type': 'none'}}

    def ask(self, params=None, json_mode=True, images=None, url=None):
        c = llm.new_client(self.provider(url), 'local', 30)
        return am.ask(c, {'model': 'claude-x', 'params': params or {}}, '说明', '材料', json_mode, images)

    def test_a_json_reply_is_read_and_counted(self):
        self.service.reply = '```json\n{"ok": true}\n```'
        text, usage, finish = self.ask()
        self.assertEqual((json.loads(text), finish), ({'ok': True}, 'stop'))
        # Counted the OpenAI way: prompt tokens include those read from and written to the cache.
        self.assertEqual(usage, {'prompt_tokens': 45, 'completion_tokens': 7, 'cached_tokens': 30, 'reasoning_tokens': 0})
        (r,) = self.service.requests
        self.assertEqual(r['path'], '/v1/messages')
        self.assertEqual(r['headers'].get('authorization'), 'Bearer local')        # the provider's key, not one from the environment
        self.assertNotIn('environment', json.dumps(r['headers']))
        body = r['body']
        self.assertEqual(body['system'], [{'type': 'text', 'text': '说明', 'cache_control': {'type': 'ephemeral'}}])
        self.assertEqual(body['max_tokens'], am.MAX_TOKENS)
        self.assertTrue(body['stream'])
        self.assertIn('只输出一个 JSON 对象', body['messages'][0]['content'])

    def test_parameters_images_and_an_address_with_v1(self):
        self.ask({'max_tokens': 900, 'reasoning_effort': 'high', 'output_config': {'x': 1}, 'top_k': 5}, json_mode=False,
                 images=['data:image/jpeg;base64,QUJD'], url=self.service.url + '/v1/')
        (r,) = self.service.requests
        self.assertEqual(r['path'], '/v1/messages')                    # not /v1/v1/messages
        body = r['body']
        self.assertEqual((body['max_tokens'], body['output_config'], body['top_k']), (900, {'x': 1, 'effort': 'high'}, 5))
        self.assertNotIn('reasoning_effort', body)
        image, text = body['messages'][0]['content']
        self.assertEqual(image, {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'QUJD'}})
        self.assertEqual(text, {'type': 'text', 'text': '材料'})

    def test_how_a_reply_ended(self):
        for stop, finish in (('refusal', 'content_filter'), ('max_tokens', 'length'), ('stop_sequence', 'stop')):
            self.service.stop = stop
            self.assertEqual(self.ask(json_mode=False)[2], finish)

    def test_a_run_uses_it_and_a_wrong_key_is_not_retried(self):
        m = {'providers': {'c': self.provider()}, 'models': {'claude': {'provider': 'c', 'model': 'claude-x', 'params': {}, 'vision': True, 'price': {}, 'note': ''}},
             'stages': {'at_me': 'claude'}, 'fallback': {'enabled': False, 'models': ['', '']}}
        models = Models(m, Path(self.tmp.name))
        self.assertEqual(models.call('at_me', 'k1', 'sys', 'user'), {'ok': True})
        receipt = json.loads(models.receipts.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((receipt['api'], receipt['status'], receipt['prompt_tokens'], receipt['cached_tokens']), ('anthropic', 'ok', 45, 30))
        self.service.status = 401
        with mock.patch.object(llm.time, 'sleep'), self.assertRaises(RuntimeError):
            (Path(self.tmp.name) / 'again').mkdir()
            Models(m, Path(self.tmp.name) / 'again').call('at_me', 'k2', 'sys', 'user')
        self.assertEqual(sum(r['path'] == '/v1/messages' for r in self.service.requests), 2)   # one call that worked, one refused, none repeated

    def test_the_connection_test_and_the_model_test(self):
        from mr import server
        p = self.provider()
        self.assertEqual(server.probe_provider('c', p)['models'], ['m-a', 'm-b'])
        self.service.models = 404
        found = server.probe_provider('c', p)
        self.assertTrue(found['ok'])
        self.assertEqual(found['models'], [])
        self.assertIn('不提供模型列表', found['note'])
        self.assertEqual(server.probe_provider('c', self.provider('http://127.0.0.1:9'))['state'], 'down')
        with mock.patch.object(config, 'HOME', Path(self.tmp.name) / 'home'):
            config._save_models({'providers': {'c': p}, 'models': {'claude': {'provider': 'c', 'model': 'claude-x', 'params': {}, 'vision': True, 'price': {}, 'note': ''}},
                                 'stages': {}, 'fallback': {'enabled': False, 'models': ['', '']}})
            result = server.test_model('claude')
        self.assertTrue(result['ok'])
        self.assertEqual(json.loads(result['answer']), {'ok': True})

    def test_the_protocol_can_be_chosen(self):
        self.assertIn('anthropic', config.APIS)
        self.assertEqual(config._clean_provider({'base_url': self.service.url + '/', 'api': 'anthropic'})['api'], 'anthropic')


if __name__ == '__main__':
    unittest.main()
