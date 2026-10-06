import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from usage_limits import UsageLimits, BudgetExceeded


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, {
            'SEARCH_USAGE_DB': self.folder.name + '/usage.db',
            'SEARCH_WINDOW_SECONDS': '600', 'SEARCH_MAX_CALLS_PER_WINDOW': '3',
            'SEARCH_MAX_CALLS_PER_DAY': '20', 'SEARCH_MAX_TOKENS_PER_DAY': '200000',
            'SEARCH_TOKEN_RESERVATION': '12000', 'SEARCH_BUDGET_TIMEZONE': 'Asia/Tokyo',
        })
        env.start()
        self.addCleanup(env.stop)

    def test_parallel_reservations_stop_at_three(self):
        def attempt(_):
            try:
                UsageLimits().reserve()
                return 1
            except BudgetExceeded:
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(12))), 3)

    def test_persistent_window_and_expiry(self):
        with patch('usage_limits.time.time', return_value=100000):
            for _ in range(3): UsageLimits().reserve()
            with self.assertRaises(BudgetExceeded): UsageLimits().reserve()
        with patch('usage_limits.time.time', return_value=100600):
            UsageLimits().reserve()

    def test_daily_calls_and_tokyo_midnight(self):
        os.environ['SEARCH_MAX_CALLS_PER_DAY'] = '1'
        start = datetime(2026, 10, 7, 23, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()
        with patch('usage_limits.time.time', return_value=start): UsageLimits().reserve()
        with patch('usage_limits.time.time', return_value=start+700):
            with self.assertRaises(BudgetExceeded): UsageLimits().reserve()
        with patch('usage_limits.time.time', return_value=start+3600): UsageLimits().reserve()

    def test_tokens_reserve_settle_and_overshoot(self):
        os.environ['SEARCH_MAX_TOKENS_PER_DAY'] = '20000'
        budget = UsageLimits()
        first = budget.reserve()
        with self.assertRaises(BudgetExceeded): budget.reserve()
        budget.settle(first, 2000)
        second = budget.reserve()
        budget.settle(second, 30000)
        with self.assertRaises(BudgetExceeded): UsageLimits().reserve()

    def test_unknown_usage_keeps_reservation(self):
        os.environ['SEARCH_MAX_TOKENS_PER_DAY'] = '12000'
        UsageLimits().reserve()
        with self.assertRaises(BudgetExceeded): UsageLimits().reserve()

    def test_invalid_config_and_corrupt_store(self):
        os.environ['SEARCH_MAX_CALLS_PER_DAY'] = '0'
        with self.assertRaises(ValueError): UsageLimits()
        os.environ['SEARCH_MAX_CALLS_PER_DAY'] = '20'
        with open(os.environ['SEARCH_USAGE_DB'], 'w') as f: f.write('broken database')
        with self.assertRaises(Exception): UsageLimits().reserve()
