//! `debug.q3pw.cadence_probe=<Hz>[:<seconds>]`: once the startup refresh probe has finished
//! and before any stream starts, hold the lobby at that rate with no decoder work, then restore
//! the previous rate. This is the no-decode ceiling for a rate: the runtime's xrWaitFrame return
//! intervals, the predicted display time steps and the display slots skipped between consecutive
//! frames. It is not a streaming result. The first second after the request is settling time
//! and is excluded from the summary. Copied into client_openxr by tools/ci/fetch_sources.sh.

pub const DEFAULT_SECONDS: u32 = 15;
const SETTLE_NS: u64 = 1_000_000_000;
const WINDOW_NS: u64 = 1_000_000_000;

/// "207" or "207:30". Rates outside 60..=240 Hz and windows outside 1..=120 s are ignored.
pub fn parse_request(value: &str) -> Option<(f32, u32)> {
    let mut parts = value.trim().splitn(2, ':');
    let hz: u32 = parts.next()?.trim().parse().ok()?;
    let seconds = match parts.next() {
        Some(text) => text.trim().parse().ok()?,
        None => DEFAULT_SECONDS,
    };
    ((60..=240).contains(&hz) && (1..=120).contains(&seconds)).then_some((hz as f32, seconds))
}

#[derive(Default, Clone, Debug, PartialEq)]
pub struct Totals {
    pub frames: u64,
    pub skipped_slots: u64,
    pub stalled_frames: u64,
    pub late_waits: u64,
    pub elapsed_ns: u64,
    intervals_ns: Vec<u64>,
    period_sum_ns: u64,
}

impl Totals {
    fn add(&mut self, interval_ns: Option<u64>, display_step_ns: Option<i64>, period_ns: u64) {
        self.frames += 1;
        self.period_sum_ns += period_ns;
        if let Some(interval) = interval_ns {
            self.intervals_ns.push(interval);
            if period_ns > 0 && interval * 2 > period_ns * 3 {
                self.late_waits += 1;
            }
        }
        if let Some(step) = display_step_ns {
            if step <= 0 || period_ns == 0 {
                self.stalled_frames += 1;
            } else {
                let slots = (step as u64 + period_ns / 2) / period_ns;
                self.skipped_slots += slots.saturating_sub(1);
            }
        }
    }

    fn percentile_us(&self, fraction: f64) -> f64 {
        if self.intervals_ns.is_empty() {
            return 0.0;
        }
        let mut sorted = self.intervals_ns.clone();
        sorted.sort_unstable();
        let index = ((fraction * sorted.len() as f64).ceil() as usize).clamp(1, sorted.len()) - 1;
        sorted[index] as f64 / 1000.0
    }

    pub fn fps(&self) -> f64 {
        if self.elapsed_ns == 0 { 0.0 } else { self.frames as f64 * 1e9 / self.elapsed_ns as f64 }
    }

    pub fn mean_period_ns(&self) -> u64 {
        if self.frames == 0 { 0 } else { self.period_sum_ns / self.frames }
    }

    fn describe(&self) -> String {
        format!(
            "frames={} fps={:.1} period_ns={} skipped_slots={} stalled_frames={} late_waits={} wait_p50_us={:.0} wait_p99_us={:.0}",
            self.frames, self.fps(), self.mean_period_ns(), self.skipped_slots, self.stalled_frames,
            self.late_waits, self.percentile_us(0.5), self.percentile_us(0.99)
        )
    }
}

pub struct CadenceProbe {
    target_hz: f32,
    restore_hz: f32,
    duration_ns: u64,
    start_ns: Option<u64>,
    window_start_ns: u64,
    last_wait_ns: Option<u64>,
    last_display_ns: Option<i64>,
    window: Totals,
    total: Totals,
    finished: bool,
}

impl CadenceProbe {
    pub fn new(target_hz: f32, seconds: u32, restore_hz: f32) -> Self {
        Self {
            target_hz,
            restore_hz,
            duration_ns: seconds as u64 * 1_000_000_000,
            start_ns: None,
            window_start_ns: 0,
            last_wait_ns: None,
            last_display_ns: None,
            window: Totals::default(),
            total: Totals::default(),
            finished: false,
        }
    }

    pub fn restore_hz(&self) -> f32 {
        self.restore_hz
    }

    pub fn finished(&self) -> bool {
        self.finished
    }

    pub fn summary(&self) -> &Totals {
        &self.total
    }

    /// One call per xrWaitFrame return, with a monotonic clock, the predicted display time and
    /// period. Returns a log line once per second of measurement and a summary at the end.
    pub fn observe(&mut self, now_ns: u64, display_ns: i64, period_ns: u64) -> Option<String> {
        if self.finished {
            return None;
        }
        let start = *self.start_ns.get_or_insert(now_ns);
        let interval = self.last_wait_ns.map(|last| now_ns.saturating_sub(last));
        let step = self.last_display_ns.map(|last| display_ns - last);
        self.last_wait_ns = Some(now_ns);
        self.last_display_ns = Some(display_ns);
        if now_ns < start + SETTLE_NS {
            return None;
        }
        if self.window.frames == 0 && self.window_start_ns == 0 {
            self.window_start_ns = now_ns;
            // The first measured frame has no in-window predecessor, so it only opens the window.
            return None;
        }
        self.window.add(interval, step, period_ns);
        self.total.add(interval, step, period_ns);
        let window_ns = now_ns - self.window_start_ns;
        let measured_ns = now_ns - (start + SETTLE_NS);
        self.total.elapsed_ns = measured_ns;
        if measured_ns >= self.duration_ns {
            self.finished = true;
            return Some(format!(
                "[Q3PW_CADENCE_SUMMARY] target_hz={} seconds={:.1} {} no_decode=true streaming=false",
                self.target_hz, measured_ns as f64 / 1e9, self.total.describe()
            ));
        }
        if window_ns >= WINDOW_NS {
            self.window.elapsed_ns = window_ns;
            let line = format!("[Q3PW_CADENCE] target_hz={} {}", self.target_hz, self.window.describe());
            self.window = Totals::default();
            self.window_start_ns = now_ns;
            return Some(line);
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const P207: u64 = 4_830_918;

    fn run(probe: &mut CadenceProbe, frames: u64, period: u64, skip_every: u64) -> Vec<String> {
        let mut lines = vec![];
        let (mut now, mut display) = (0u64, 50_000_000i64);
        for i in 0..frames {
            let slots = if skip_every > 0 && i % skip_every == skip_every - 1 { 2 } else { 1 };
            now += period * slots;
            display += (period * slots) as i64;
            if let Some(line) = probe.observe(now, display, period) {
                lines.push(line);
            }
            if probe.finished() {
                break;
            }
        }
        lines
    }

    #[test]
    fn parses_rate_and_optional_window() {
        assert_eq!(parse_request("207"), Some((207.0, DEFAULT_SECONDS)));
        assert_eq!(parse_request(" 144:30 "), Some((144.0, 30)));
        assert_eq!(parse_request(""), None);
        assert_eq!(parse_request("300"), None);
        assert_eq!(parse_request("207:0"), None);
        assert_eq!(parse_request("207.5"), None);
    }

    #[test]
    fn steady_207_reports_no_skips_after_settling() {
        let mut probe = CadenceProbe::new(207.0, 5, 90.0);
        let lines = run(&mut probe, 10_000, P207, 0);
        assert!(probe.finished());
        assert_eq!(lines.iter().filter(|l| l.starts_with("[Q3PW_CADENCE] ")).count(), 4);
        let summary = lines.last().unwrap();
        assert!(summary.starts_with("[Q3PW_CADENCE_SUMMARY] target_hz=207 seconds=5.0"));
        assert!(summary.contains("no_decode=true"));
        let total = probe.summary();
        assert_eq!((total.skipped_slots, total.stalled_frames, total.late_waits), (0, 0, 0));
        assert!((total.fps() - 207.0).abs() < 0.5, "{}", total.fps());
        assert_eq!(total.mean_period_ns(), P207);
        assert_eq!(probe.restore_hz(), 90.0);
        assert_eq!(probe.observe(u64::MAX / 2, 0, P207), None);
    }

    #[test]
    fn missed_slots_are_counted_and_lower_fps() {
        let mut probe = CadenceProbe::new(207.0, 4, 120.0);
        run(&mut probe, 10_000, P207, 4);
        let total = probe.summary();
        assert!(total.skipped_slots > 100, "{total:?}");
        assert_eq!(total.skipped_slots, total.late_waits);
        assert!(total.fps() < 170.0 && total.fps() > 160.0, "{}", total.fps());
    }

    #[test]
    fn repeated_display_time_counts_as_stalled() {
        let mut probe = CadenceProbe::new(207.0, 2, 120.0);
        let mut now = 0;
        for _ in 0..300 {
            now += P207;
            probe.observe(now, 7, P207);
        }
        assert!(probe.summary().stalled_frames > 0);
        assert_eq!(probe.summary().skipped_slots, 0);
    }
}
