//! Opt-in packet-arrival grace inside the existing latest-frame wait budget.
use std::time::Duration;

pub fn packet_grace(property: &str) -> Duration {
    Duration::from_micros(property.trim().parse::<u64>().unwrap_or(0).min(1000))
}

/// A packet may start decoding during grace; an active decode may then use the
/// remainder of the existing budget. Grace never adds time to that budget.
pub fn continue_wait(elapsed: Duration, budget: Duration, grace: Duration, decoding: bool) -> bool {
    elapsed < budget && (decoding || elapsed < grace.min(budget))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn us(value: u64) -> Duration {
        Duration::from_micros(value)
    }

    #[test]
    fn default_preserves_decode_only_wait() {
        assert_eq!(packet_grace(""), Duration::ZERO);
        assert!(!continue_wait(us(0), us(4000), Duration::ZERO, false));
        assert!(continue_wait(us(3999), us(4000), Duration::ZERO, true));
        assert!(!continue_wait(us(4000), us(4000), Duration::ZERO, true));
    }

    #[test]
    fn late_packet_can_enter_decode_without_extending_deadline() {
        assert!(continue_wait(us(499), us(4000), us(500), false));
        assert!(continue_wait(us(501), us(4000), us(500), true));
        assert!(!continue_wait(us(4000), us(4000), us(500), true));
    }

    #[test]
    fn empty_queue_stops_at_grace_or_frame_budget() {
        assert!(!continue_wait(us(500), us(4000), us(500), false));
        assert!(!continue_wait(us(200), us(200), us(1000), false));
        assert!(!continue_wait(us(0), Duration::ZERO, us(1000), true));
        assert!(!continue_wait(us(5000), us(4000), us(1000), true));
    }

    #[test]
    fn malformed_or_excessive_property_cannot_create_long_wait() {
        for s in ["-1", "NaN", "garbage", "18446744073709551616"] {
            assert_eq!(packet_grace(s), Duration::ZERO);
        }
        assert_eq!(packet_grace(" 500 "), us(500));
        assert_eq!(packet_grace("18446744073709551615"), us(1000));
    }
}
