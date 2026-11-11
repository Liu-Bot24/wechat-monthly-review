import unittest
from mr.mask import Masker, leaks, scrub

class MaskTest(unittest.TestCase):
    def setUp(self):
        self.m = Masker()

    def test_openai_key_with_context(self):
        out = self.m.mask('这个是我的 openai 的 key：sk-proj-abcDEF1234567890abcdEFGH1234 你先用')
        self.assertIn('⟦API Key·OpenAI·K1⟧', out)
        self.assertNotIn('sk-proj', out)

    def test_sk_key_provider_from_neighbour(self):
        out = self.m.mask('sk-0123456789abcdefABCDEF0123456789', context='deepseek 充值了')
        self.assertIn('API Key·DeepSeek', out)

    def test_same_value_same_code(self):
        a = self.m.mask('key: sk-aaaaaaaaaaaaaaaaaaaaaaaa1234')
        b = self.m.mask('再发一次 sk-aaaaaaaaaaaaaaaaaaaaaaaa1234')
        self.assertEqual(a.split('⟦')[1].split('⟧')[0], b.split('⟦')[1].split('⟧')[0])

    def test_password_and_account(self):
        out = self.m.mask('账号：zhangsan_2024 密码：Abc#12345')
        self.assertIn('⟦账号·A1⟧', out)
        self.assertIn('⟦密码·P1⟧', out)
        self.assertNotIn('Abc#12345', out)

    def test_verification_code(self):
        self.assertIn('⟦验证码·V1⟧', self.m.mask('验证码是 482913，五分钟内有效'))

    def test_id_card_checksum(self):
        self.assertIn('身份证号', self.m.mask('身份证 11010519491231002X'))
        self.assertNotIn('身份证号', self.m.mask('订单 110105194912310021'))  # wrong check digit

    def test_bank_card_luhn(self):
        self.assertIn('银行卡号', self.m.mask('卡号 4111111111111111'))
        self.assertNotIn('银行卡号', self.m.mask('快递单号 4111111111111112'))

    def test_plain_chat_untouched(self):
        for text in ['密码忘了怎么办', '明天老地方见，记得带镜头', '这个 key 很关键', '价格 12999 元',
                     'https://mp.weixin.qq.com/s/AbCdEf1234567890AbCdEf1234567890xyz', '我的账号被封了',
                     '- 【仅定位】账号密码｜Morgan.K｜03-14 09:26｜[#10203040](<../a.md#msg-1>)']:
            self.assertEqual(self.m.mask(text), text, text)

    def test_generic_key_needs_mixed_case_and_skips_names(self):
        self.assertIn('疑似密钥', self.m.mask('给你 Ab3dEf7hIj9kLm1nOp5qRs2tUv4wXy6zA8bC'))
        for text in ['@a3b5c7d9e1f3a5b7c9d1e3f5a7b9c1d3e5f7 你好', '9f8e7d6c5b4a39281706f5e4d3c2b1a0aa.docx',
                     'id%3Dab12cd34ef56ab12cd34ef56ab12cd34ef56']:
            self.assertNotIn('疑似密钥', self.m.mask(text), text)

    def test_values_kept_only_when_asked(self):
        self.m.mask('密码：Abc#12345')
        self.assertNotIn('value', self.m.found[0])          # a run never holds originals
        shown = Masker(keep_values=True)
        shown.mask('账号：zhangsan_2024 密码：Abc#12345')
        self.assertEqual([f['value'] for f in shown.found], ['Abc#12345', 'zhangsan_2024'])   # rule order: password before account

    def test_leaks_detects_raw_secret(self):
        self.assertTrue(leaks('报告里不该有 sk-proj-abcDEF1234567890abcdEFGH1234'))
        self.assertFalse(leaks('报告里只有 ⟦API Key·OpenAI·K1⟧'))
    def test_scrub_masks_what_leaks_flags(self):
        text = '- 已有 ⟦密码·P1⟧，又写了 密码：Qwe#98765 [#3](<../a/sk-proj-abcDEF1234567890abcdEFGH1234.md#msg-1>)'
        out, labels = scrub(text)
        self.assertEqual(labels, ['密码'])
        self.assertIn('⟦密码·P2⟧', out)                    # continues after the codes already used
        self.assertIn('<../a/sk-proj-abcDEF1234567890abcdEFGH1234.md#msg-1>', out)   # link targets are paths
        self.assertFalse(leaks(out))
        plain = '- 【仅定位】账号密码｜Morgan.K｜03-14 09:26｜[#10203040](<../a.md#msg-1>)'
        self.assertEqual(scrub(plain), (plain, []))

if __name__ == '__main__':
    unittest.main()
