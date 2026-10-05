import unittest
from tools.quest3.publication_wait import parse


def line(values, t=11, pid=42):
    return f'{t:.3f} {pid} 44 I ALVR: [Q3PW_EVENT_WAIT] {values}'


class PublicationCounters(unittest.TestCase):
    def test_actual_waits_and_spurious_reparks_are_counted_separately(self):
        values='requested=1 calls=2 condvar_waits=4 pending_seen=1 fallback_calls=0'
        r=parse([line(values),line(values,t=11.5)],10,12,42)
        self.assertEqual(r['status'],'parsed')
        self.assertEqual(r['configurations'][0]['condvar_waits'],8)
        self.assertEqual(r['configurations'][0]['pending_seen'],2)
        self.assertFalse(r['performance_acceptance'])

    def test_pid_and_half_open_window_exclude_unrelated_records(self):
        values='requested=1 calls=2 condvar_waits=2 pending_seen=1 fallback_calls=0'
        r=parse([line(values,t=9),line(values,t=10),line(values,t=12),line(values,pid=43)],10,12,42)
        self.assertEqual(r['configurations'][0]['interval_records'],1)

    def test_requested_is_distinct_from_execution_and_fallback(self):
        r=parse([line('requested=1 calls=0 condvar_waits=0 pending_seen=0 fallback_calls=3'),
                 line('requested=0 calls=0 condvar_waits=0 pending_seen=0 fallback_calls=0')],10,12,42)
        self.assertEqual(r['status'],'parsed')
        self.assertEqual(r['configurations'][1]['calls'],0)
        self.assertEqual(r['configurations'][1]['fallback_calls'],3)

    def test_malformed_or_contradictory_records_invalidate_result(self):
        for values in ('requested=0 calls=1 condvar_waits=0 pending_seen=0 fallback_calls=0',
                       'requested=1 calls=1 condvar_waits=1 pending_seen=2 fallback_calls=0',
                       'requested=1 calls=oops', 'requested=2 calls=0 condvar_waits=0 pending_seen=0 fallback_calls=0'):
            r=parse([line(values)],10,12,42)
            self.assertEqual(r['status'],'invalid_records')
            self.assertIsNone(r['configurations'])

    def test_missing_and_invalid_capture_identity(self):
        self.assertEqual(parse([],10,12,42)['status'],'no_event_wait_records')
        for start,end,pid in ((12,10,42),(float('nan'),12,42),(10,12,True),(10,12,0)):
            with self.assertRaises(ValueError): parse([],start,end,pid)
