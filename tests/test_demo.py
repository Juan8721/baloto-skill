import importlib.util
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('demo', ROOT / 'skills/baloto-simulador/scripts/demo.py')
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)
TIME = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
INPUT = {'label': 'Prueba sintética', 'budget': 20, 'draw_count': 3,
         'interval_days': 1, 'lines': '01 02 03 04 05 | 06\n07 08 09 10 11 | 12'}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = demo.Store(self.temp.name)

    def test_cost_and_draft_does_not_spend(self):
        c = self.store.create(INPUT, TIME)
        self.assertEqual(c['cost'], 6)
        self.assertEqual(c['spent'], 0)
        self.assertEqual(c['remaining'], 20)

    def test_rejects_invalid_and_duplicate_lines(self):
        for lines in ['', '1 1 2 3 4 | 1', '1 2 3 4 44 | 1', '1 2 3 4 5 | 17',
                      '-1 2 3 4 5 | 1', '1 2 3 4 5 | -1', '1 2 3 4 5',
                      '1 2 3 4 5 | 1\n5 4 3 2 1 | 1']:
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                self.store.create({**INPUT, 'lines': lines}, TIME)
        self.assertEqual(self.store.list(), [])

    def test_rejects_insufficient_budget_bool_and_unknown_fields(self):
        for changes in [{'budget': 5}, {'draw_count': True}, {'interval_days': 0},
                        {'budget': 2.5}, {'real_payment': True}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.create({**INPUT, **changes}, TIME)

    def test_draft_never_has_results(self):
        c = self.store.create(INPUT, TIME)
        self.store.tick(TIME + timedelta(days=10))
        self.assertTrue(all(d['result'] is None for d in self.store.get(c['id'])['draws']))

    def test_only_processes_day_after_draw(self):
        c = self.store.create(INPUT, TIME)
        self.store.action(c['id'], 'confirm', TIME)
        self.store.tick(TIME + timedelta(days=2, seconds=-1))
        self.assertIsNone(self.store.get(c['id'])['draws'][0]['result'])
        self.store.tick(TIME + timedelta(days=2))
        result = self.store.get(c['id'])
        self.assertIsNotNone(result['draws'][0]['result'])
        self.assertIsNone(result['draws'][1]['result'])

    def test_restart_catches_up_without_duplicate_credits(self):
        c = self.store.create(INPUT, TIME)
        self.store.action(c['id'], 'confirm', TIME)
        restarted = demo.Store(self.temp.name)
        restarted.tick(TIME + timedelta(days=4))
        first = restarted.get(c['id'])
        restarted.tick(TIME + timedelta(days=10))
        second = restarted.get(c['id'])
        self.assertEqual(first['draws'], second['draws'])
        self.assertEqual(first['returned'], second['returned'])
        self.assertEqual(second['status'], 'complete')
        self.assertEqual(second['balance'], second['returned'] - 6)
        self.assertEqual(second['remaining'], 14 + second['returned'])

    def test_advance_is_per_campaign(self):
        a, b = self.store.create(INPUT, TIME), self.store.create(INPUT, TIME)
        self.store.action(a['id'], 'confirm', TIME)
        self.store.action(a['id'], 'advance', TIME)
        self.store.action(a['id'], 'advance', TIME)
        self.assertIsNotNone(self.store.get(a['id'])['draws'][0]['result'])
        self.assertEqual(self.store.get(b['id'])['offset_days'], 0)

    def test_winning_credits_and_idempotent_confirmation(self):
        c = self.store.create({**INPUT, 'draw_count': 1}, TIME)
        self.store.action(c['id'], 'confirm', TIME)
        with patch.object(demo.random.Random, 'sample', return_value=[1, 2, 3, 4, 5]), \
             patch.object(demo.random.Random, 'randint', return_value=6):
            self.store.tick(TIME + timedelta(days=2))
        result = self.store.action(c['id'], 'confirm', TIME + timedelta(days=3))
        self.assertEqual(result['returned'], 1000)
        self.assertEqual(result['spent'], 2)
        self.assertEqual(result['remaining'], 1018)
        self.assertEqual(result['draws'][0]['result']['lines'][1]['credits'], 0)

    def test_failed_save_restores_memory_and_disk(self):
        c = self.store.create(INPUT, TIME)
        before = self.store.path.read_text()
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.action(c['id'], 'confirm', TIME)
        self.assertEqual(self.store.get(c['id'])['status'], 'draft')
        self.assertEqual(self.store.path.read_text(), before)

    def test_lock_prevents_two_writers_and_releases(self):
        with demo.process_lock(self.temp.name):
            with self.assertRaises(ValueError):
                with demo.process_lock(self.temp.name):
                    pass
        with demo.process_lock(self.temp.name):
            self.assertTrue((Path(self.temp.name) / '.server.lock').exists())
        self.assertFalse((Path(self.temp.name) / '.server.lock').exists())

    def test_hundreds_of_user_supplied_lines(self):
        import itertools
        lines = '\n'.join(' '.join(map(str, x)) + ' | 1'
                          for x in itertools.islice(itertools.combinations(range(1, 44), 5), 500))
        c = self.store.create({**INPUT, 'budget': 1000, 'draw_count': 2, 'lines': lines}, TIME)
        self.assertEqual(c['cost'], 1000)
        self.assertEqual(len(c['lines']), 500)

    def test_state_is_json_without_public_secret(self):
        c = self.store.create(INPUT, TIME)
        self.assertNotIn('secret', c)
        self.assertNotIn(self.store.state['secret'], json.dumps(self.store.list()))
        self.assertEqual(json.loads(self.store.path.read_text())['version'], 1)


if __name__ == '__main__':
    unittest.main()
