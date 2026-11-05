import os
import sqlite3
import unittest
from unittest.mock import patch

import app


class SQLAndAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.build_database()

    def test_dataset_is_complete(self):
        self.assertEqual(app.read_query('SELECT COUNT(*) AS total FROM deals')[1][0][0], 500000)
        self.assertEqual(app.read_query('SELECT COUNT(*) AS total FROM contracts')[1][0][0], 6616)
        self.assertEqual(app.read_query('SELECT COUNT(*) AS total FROM participants')[1][0][0], 14392)
        self.assertEqual(app.read_query('SELECT COUNT(*) AS total FROM brokers')[1][0][0], 120)

    def test_known_year_count(self):
        cols, rows = app.read_query("SELECT count(*) AS deals_2025 FROM deals WHERE deal_date BETWEEN '2025-01-01' AND '2025-12-31'")
        self.assertEqual(rows, [(115763,)])

    def test_fx_coverage(self):
        cols, rows = app.read_query("""
            SELECT COUNT(*) AS missing_rates FROM deals AS d
            JOIN contracts AS c ON d.contract_id = c.contract_id
            LEFT JOIN usd_rates AS r ON r.rate_date = d.deal_date
            WHERE c.currency = 'Доллар США' AND r.uzs_per_usd IS NULL
        """)
        self.assertEqual(rows, [(0,)])

    def test_sql_readonly(self):
        for sql in ['DELETE FROM deals', 'DROP TABLE deals', 'SELECT * FROM sqlite_master', 'SELECT load_extension(\'a\')']:
            with self.subTest(sql=sql), self.assertRaises((ValueError, sqlite3.Error)):
                app.read_query(sql)

    def test_no_match_means_no_fabrication(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'dummy'}), patch.object(app, 'query_plan', return_value={
            'status': 'query', 'sql': 'SELECT deal_id FROM deals WHERE deal_id = -999', 'language': 'ru'
        }):
            result = app.create_response('Укажите несуществующую сделку')
            self.assertIn('ответ не найден', result)
            self.assertNotIn('-999 |', result)

    def test_result_and_sql_visible_in_uzbek(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'dummy'}), patch.object(app, 'query_plan', return_value={
            'status': 'query',
            'sql': "SELECT COUNT(*) AS bitimlar FROM deals WHERE deal_date BETWEEN '2025-01-01' AND '2025-12-31'",
            'language': 'uz',
        }):
            result = app.create_response('2025-yilda nechta bitim bo‘lgan?')
            self.assertIn('115 763', result)
            self.assertIn('Dalil', result)
            self.assertIn('```sql', result)

    def test_unsupported_answer_is_not_invented(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'dummy'}), patch.object(app, 'query_plan', return_value={
            'status': 'unsupported', 'sql': '', 'language': 'ru', 'reason': 'Нет данных за 2027 год.'
        }):
            result = app.create_response('Каков прогноз на 2027 год?')
            self.assertIn('Недостаточно данных', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
