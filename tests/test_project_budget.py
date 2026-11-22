import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from shared.asxlib.errors import AsxError
from shared.asxlib.project_budget import configure, reserve
from shared.asxlib.state import connect_db, create_task_intent, record_charge_consent, update_task


class ProjectBudgetTest(unittest.TestCase):
    def test_concurrent_hold_retry_release_and_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def connect():
                return connect_db(root / 'ledger.db')
            with patch('shared.asxlib.project_budget.connect_db', connect):
                configure(root, '0.100', 'USD')
                with closing(connect()) as db:
                    for key in ('a', 'b', 'c'):
                        create_task_intent(db, operation='generate', model='m', idempotency_key=key,
                                           request={}, output_dir=str(root / 'art'), base_url='dev', local_id=key)
                        record_charge_consent(db, idempotency_key=key, identity=None, gateway='dev', task_type='image',
                                              body={}, consent={'currency': 'USD', 'unit_amount': '0.085'})
                    db.commit()
                def attempt(key):
                    with closing(connect()) as db:
                        try:
                            reserve(db, key)
                            return True
                        except AsxError as err:
                            self.assertEqual(err.code, 'project_limit_exceeded')
                            return False
                with ThreadPoolExecutor(2) as pool:
                    accepted = list(pool.map(attempt, ('a', 'b')))
                self.assertEqual(sum(accepted), 1)
                winner, loser = ('a','b') if accepted[0] else ('b','a')
                self.assertTrue(attempt(winner))
                self.assertEqual(configure(root, None, 'USD')['reserved'], '0.085')
                for key, status in [(winner, 'failed'), (loser, 'canceled')]:
                    self.assertTrue(attempt(key))
                    with closing(connect()) as db:
                        update_task(db, key, status=status, remote={'status': status, 'billing': {'charge_status':'unbilled'}})
                    self.assertEqual(configure(root, None, 'USD')['reserved'], '0')
                self.assertTrue(attempt('c'))
                with closing(connect()) as db:
                    update_task(db, 'c', status='succeeded', billing={'currency':'USD','charge_status':'paid','actual_amount':'0.081'})
                for _ in range(2):
                    report = configure(root, None, 'USD')
                    self.assertEqual((report['actual'], report['reserved']), ('0.081', '0'))
                    self.assertEqual(len(report['tasks']), 3)
