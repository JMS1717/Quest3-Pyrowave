// SPDX-License-Identifier: MIT
// Checked CPU fallback only. Readiness is not inferred from timeout or HUP.
use std::{
    os::fd::{AsRawFd, BorrowedFd},
    time::{Duration, Instant},
};

#[repr(C)]
struct PollFd {
    fd: i32,
    events: i16,
    revents: i16,
}
extern "C" {
    fn poll(fds: *mut PollFd, count: usize, timeout: i32) -> i32;
}

pub fn wait_ready(fd: BorrowedFd<'_>, timeout: Duration) -> bool {
    let begin = Instant::now();
    loop {
        let remaining = timeout.saturating_sub(begin.elapsed());
        let millis = remaining
            .as_nanos()
            .div_ceil(1_000_000)
            .min(i32::MAX as u128) as i32;
        let mut p = PollFd {
            fd: fd.as_raw_fd(),
            events: 1,
            revents: 0,
        };
        let result = unsafe { poll(&mut p, 1, millis) };
        if result > 0 {
            return p.revents & 1 != 0 && p.revents & (8 | 16 | 32) == 0;
        }
        if result < 0 && std::io::Error::last_os_error().raw_os_error() != Some(4) {
            return false;
        }
        if begin.elapsed() >= timeout {
            return false;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Write, os::unix::net::UnixStream};
    #[test]
    fn an_unsignaled_descriptor_times_out_without_becoming_ready() {
        let (fd, _producer) = UnixStream::pair().unwrap();
        use std::os::fd::AsFd;
        assert!(!wait_ready(fd.as_fd(), Duration::from_millis(5)));
    }
    #[test]
    fn a_real_readiness_event_is_required() {
        let (fd, mut producer) = UnixStream::pair().unwrap();
        producer.write_all(b"x").unwrap();
        use std::os::fd::AsFd;
        assert!(wait_ready(fd.as_fd(), Duration::ZERO));
    }
    #[test]
    fn peer_hangup_is_not_a_fence_signal() {
        let (fd, producer) = UnixStream::pair().unwrap();
        drop(producer);
        use std::os::fd::AsFd;
        assert!(!wait_ready(fd.as_fd(), Duration::from_millis(5)));
    }
}
