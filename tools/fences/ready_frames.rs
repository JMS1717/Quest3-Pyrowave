// SPDX-License-Identifier: MIT
// FD ownership only; neither moving a descriptor nor taking a frame means GPU completion.
use std::os::fd::OwnedFd;

#[derive(Default)]
pub struct ReadyFrames {
    pending: Option<OwnedFd>,
    leased: Option<(usize, OwnedFd)>,
}
impl ReadyFrames {
    pub fn replace_pending(&mut self, fd: Option<OwnedFd>) {
        self.pending = fd;
    }
    pub fn lease(&mut self, buffer: usize) {
        self.leased = self.pending.take().map(|fd| (buffer, fd));
    }
    pub fn consume(&mut self, buffer: usize) -> Option<OwnedFd> {
        if buffer == 0 || self.leased.as_ref().map(|(b, _)| *b) != Some(buffer) {
            return None;
        }
        self.leased.take().map(|(_, fd)| fd)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Read, os::unix::net::UnixStream};
    fn descriptor() -> (OwnedFd, UnixStream) {
        let (a, b) = UnixStream::pair().unwrap();
        b.set_nonblocking(true).unwrap();
        (a.into(), b)
    }
    fn closed(peer: &mut UnixStream) -> bool {
        matches!(peer.read(&mut [0]), Ok(0))
    }
    #[test]
    fn superseding_closes_only_the_pending_descriptor() {
        let mut f = ReadyFrames::default();
        let (leased, mut lease_peer) = descriptor();
        f.replace_pending(Some(leased));
        f.lease(10);
        let (old, mut old_peer) = descriptor();
        f.replace_pending(Some(old));
        let (new, mut new_peer) = descriptor();
        f.replace_pending(Some(new));
        assert!(closed(&mut old_peer));
        assert!(!closed(&mut lease_peer));
        assert!(!closed(&mut new_peer));
    }
    #[test]
    fn consume_requires_the_matching_nonzero_buffer_and_is_once_only() {
        let mut f = ReadyFrames::default();
        let (fd, mut peer) = descriptor();
        f.replace_pending(Some(fd));
        f.lease(20);
        assert!(f.consume(0).is_none());
        assert!(f.consume(19).is_none());
        let owned = f.consume(20).unwrap();
        assert!(f.consume(20).is_none());
        assert!(!closed(&mut peer));
        drop(owned);
        assert!(closed(&mut peer));
    }
    #[test]
    fn taking_a_new_frame_closes_an_unconsumed_old_lease() {
        let mut f = ReadyFrames::default();
        let (fd, mut old) = descriptor();
        f.replace_pending(Some(fd));
        f.lease(20);
        let (next, mut new) = descriptor();
        f.replace_pending(Some(next));
        f.lease(30);
        assert!(closed(&mut old));
        assert!(!closed(&mut new));
        assert!(f.consume(20).is_none());
    }
    #[test]
    fn synchronous_replacement_and_teardown_close_descriptors() {
        let mut f = ReadyFrames::default();
        let (fd, mut peer) = descriptor();
        f.replace_pending(Some(fd));
        f.replace_pending(None);
        assert!(closed(&mut peer));
        let (fd, mut leased) = descriptor();
        f.replace_pending(Some(fd));
        f.lease(20);
        let (fd, mut pending) = descriptor();
        f.replace_pending(Some(fd));
        drop(f);
        assert!(closed(&mut leased));
        assert!(closed(&mut pending));
    }
}
