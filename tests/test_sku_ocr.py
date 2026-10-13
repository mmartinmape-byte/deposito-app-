import os
import sys
import tempfile
import unittest
from pathlib import Path

# La importación inicializa el esquema: usar una base temporal, nunca la del depósito.
_temp = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(_temp.name) / 'test.db').replace('\\', '/')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as deposito
from sqlalchemy import text


class SkuOCRTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = deposito.app.test_client()
        with deposito.engine.begin() as conn:
            conn.execute(text("INSERT INTO productos (nombre, sku, color) VALUES ('Organizador', 'UBBU75174', 'Blanco')"))
            conn.execute(text("INSERT INTO productos (nombre, sku, color) VALUES ('Otro', 'AB-123', 'Negro')"))
            conn.execute(text("INSERT INTO mape_productos (nombre, sku, color) VALUES ('Mesa', 'MAPE12345', 'Natural')"))
            conn.execute(text("INSERT INTO palets (codigo) VALUES ('P01')"))
            conn.execute(text("INSERT INTO stock (palet_id, producto, color, cajas, piezas_por_caja) VALUES (1, 'Organizador', 'Blanco', 3, 12)"))

    @classmethod
    def tearDownClass(cls):
        deposito.engine.dispose()
        _temp.cleanup()

    def read(self, texto):
        response = self.client.post('/api/sku/leer', json={'texto': texto})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_label_and_split_code(self):
        data = self.read('PRODUCTO BLANCO\nSKU: UBBU 75174\n12 PIEZAS')
        self.assertTrue(data['exacto'])
        self.assertEqual(data['leido'], 'UBBU75174')
        self.assertEqual(data['resultados'][0]['stock'][0]['palet_codigo'], 'P01')

    def test_hyphenated_and_mape(self):
        self.assertEqual(self.read('ab-123')['resultados'][0]['sku'], 'AB-123')
        self.assertTrue(self.read('MAPE12345')['resultados'][0]['mape'])

    def test_code_label_means_sku(self):
        for sample in ('CODE: UBBU75174', 'code UBBU 75174', 'CODE\nUBBU75174', 'CODEUBBU75174'):
            data = self.read(sample)
            self.assertTrue(data['exacto'], sample)
            self.assertEqual(data['leido'], 'UBBU75174', sample)
        for sample in ('CODE: U8BU75174', 'SKU: U8BU75174'):
            data = self.read(sample)
            self.assertFalse(data['exacto'])
            self.assertEqual(data['leido'], 'U8BU75174')
        self.assertEqual(self.read('CODE')['resultados'], [])
        data = self.client.get('/api/sku/buscar', query_string={'q': 'CODE: UBBU75174'}).get_json()
        self.assertTrue(data['exacto'])
        self.assertEqual(data['leido'], 'UBBU75174')

    def test_ocr_confusion_is_only_suggestion(self):
        data = self.read('SKU U8BU75174')
        self.assertFalse(data['exacto'])
        self.assertEqual(data['leido'], 'U8BU75174')
        self.assertEqual(data['resultados'][0]['sku'], 'UBBU75174')

    def test_multiple_codes_not_silently_selected(self):
        data = self.read('UBBU75174\nAB-123')
        self.assertEqual({p['sku'] for p in data['resultados']}, {'UBBU75174', 'AB-123'})
        self.assertIn('varios SKU', data['aviso'])

    def test_empty_noise_and_unknown(self):
        for sample in ('', 'COLOR BLANCO', 'ZZZZ999999999999'):
            self.assertEqual(self.read(sample)['resultados'], [])

    def test_invalid_payload(self):
        for payload in (None, [], {'texto': 123}, {'texto': 'A' * 4001}, {'imagen': 'old-client'}):
            self.assertEqual(self.client.post('/api/sku/leer', json=payload).status_code, 400)

    def test_manual_and_mobile_remain_available(self):
        data = self.client.get('/api/sku/buscar?q=UBBU75174').get_json()
        self.assertTrue(data['exacto'])
        self.assertEqual(self.client.get('/mobile').status_code, 200)
        with self.client.get('/static/sku-ocr.js') as response:
            self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
