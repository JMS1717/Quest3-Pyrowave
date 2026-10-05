//! CPU-only scheduling observations used by the production TCP decoder.
//! These are native-call wall-time bounds, not GPU or optical timings.
use std::time::Instant;

pub fn enabled(requested: bool, workers: usize, handoff: bool, ready: bool,
               release: bool, async_eye: bool) -> bool {
    requested && workers == 1 && !handoff && !ready && !release && !async_eye
}

#[derive(Default, Debug, PartialEq, Eq)]
pub struct Opportunity {
    pub calls: u64,
    pub pending: u64,
    pub ready_before: u64,
    pub arrived_during: u64,
    pub arrived_after: u64,
    pub overlap_sum_us: u64,
    pub overlap_max_us: u64,
    pub call_sum_us: u64,
    pub headroom: u64,
}

impl Opportunity {
    pub fn observe(&mut self, start: Instant, end: Instant, ready: Option<Instant>,
                   protected: [usize; 2], output: usize) {
        // The production caller supplies monotonic bounds around one completed
        // synchronous native call, followed by a separate slot-state snapshot.
        let Some(call) = end.checked_duration_since(start) else { return; };
        self.calls += 1;
        self.call_sum_us = self.call_sum_us.saturating_add(micros(call));
        let handles = [protected[0], protected[1], output];
        let occupied = handles.iter().enumerate().filter(|(i, handle)|
            **handle != 0 && !handles[..*i].contains(handle)).count();
        if occupied < 3 { self.headroom += 1; }
        let Some(ready) = ready else { return; };
        self.pending += 1;
        if ready > end {
            // Receive can race with the observation after the native return.
            // Such a packet provides no evidence of overlap during the call.
            self.arrived_after += 1;
            return;
        }
        if ready <= start { self.ready_before += 1; }
        else { self.arrived_during += 1; }
        let overlap = micros(end.duration_since(ready.max(start)));
        self.overlap_sum_us = self.overlap_sum_us.saturating_add(overlap);
        self.overlap_max_us = self.overlap_max_us.max(overlap);
    }

    pub fn take(&mut self) -> Self { std::mem::take(self) }

    pub fn row(&self) -> String {
        format!("calls={} pending={} ready_before={} arrived_during={} arrived_after={} overlap_sum_us={} overlap_max_us={} call_sum_us={} headroom_at_snapshot={}",
            self.calls, self.pending, self.ready_before, self.arrived_during,
            self.arrived_after, self.overlap_sum_us, self.overlap_max_us,
            self.call_sum_us, self.headroom)
    }
}

fn micros(value: std::time::Duration) -> u64 {
    value.as_micros().min(u64::MAX as u128) as u64
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    fn us(n: u64) -> Duration { Duration::from_micros(n) }

    #[test]
    fn opt_in_is_limited_to_single_worker_synchronous_path() {
        assert!(enabled(true, 1, false, false, false, false));
        assert!(!enabled(false, 1, false, false, false, false));
        for workers in [0, 2, 3] {
            assert!(!enabled(true, workers, false, false, false, false));
        }
        for flags in [[true, false, false, false], [false, true, false, false],
                      [false, false, true, false], [false, false, false, true]] {
            assert!(!enabled(true, 1, flags[0], flags[1], flags[2], flags[3]));
        }
    }

    #[test]
    fn absent_packet_is_not_an_overlap_opportunity() {
        let start = Instant::now(); let mut stats = Opportunity::default();
        stats.observe(start, start + us(8000), None, [1, 2], 3);
        assert_eq!((stats.calls, stats.pending, stats.headroom, stats.call_sum_us), (1, 0, 0, 8000));
    }
    #[test]
    fn pending_before_call_is_capped_to_call_duration() {
        let ready = Instant::now(); let start = ready + us(5000);
        let mut stats = Opportunity::default();
        stats.observe(start, start + us(8000), Some(ready), [1, 0], 2);
        assert_eq!((stats.ready_before, stats.overlap_sum_us, stats.headroom), (1, 8000, 1));
    }
    #[test]
    fn late_ingress_only_counts_remaining_wall_time() {
        let start = Instant::now(); let mut stats = Opportunity::default();
        stats.observe(start, start + us(8000), Some(start + us(7000)), [1, 2], 3);
        assert_eq!((stats.arrived_during, stats.overlap_sum_us), (1, 1000));
    }
    #[test]
    fn receive_racing_after_return_is_not_positive_overlap() {
        let start = Instant::now(); let mut stats = Opportunity::default();
        stats.observe(start, start + us(8000), Some(start + us(8100)), [1, 2], 3);
        assert_eq!((stats.pending, stats.arrived_after, stats.overlap_sum_us), (1, 1, 0));
    }
    #[test]
    fn duplicates_and_null_handles_do_not_exhaust_ring() {
        let start = Instant::now(); let mut stats = Opportunity::default();
        stats.observe(start, start + us(1), None, [1, 1], 2);
        stats.observe(start, start + us(1), None, [0, 0], 1);
        assert_eq!(stats.headroom, 2);
    }
    #[test]
    fn boundary_has_zero_overlap_and_interval_take_resets() {
        let start = Instant::now(); let mut stats = Opportunity::default();
        stats.observe(start, start + us(8000), Some(start + us(8000)), [1, 2], 3);
        let interval = stats.take();
        assert_eq!((interval.arrived_during, interval.overlap_max_us), (1, 0));
        assert_eq!(stats, Opportunity::default());
        assert!(interval.row().contains("headroom_at_snapshot=0"));
    }
    #[test]
    fn backwards_bounds_are_rejected_without_panicking() {
        let end = Instant::now(); let mut stats = Opportunity::default();
        stats.observe(end + us(1), end, Some(end), [1, 2], 3);
        assert_eq!(stats, Opportunity::default());
    }
}
