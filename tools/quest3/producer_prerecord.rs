//! Worker metadata only. Native code owns slots, command buffers and completion.
//! No packet FIFO: one preparation plus the receive thread's one latest payload.
use std::time::Duration;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Prepared {
    pub timestamp: Duration,
    pub order: u64,
    pub generation: u64,
}

pub fn allowed(requested: bool, workers: usize, native_geometry: bool,
               low_priority: bool, forbidden_mode: bool) -> bool {
    requested && workers == 1 && native_geometry && low_priority && !forbidden_mode
}

/// Called under the receive-slot lock after the ordinary publication wait.
/// Any new pending payload supersedes the prepared frame; do not compare codec
/// sequence fields, which wrap independently of the receive order.
pub fn cancel_before_submit(prepared: Option<Prepared>, new_pending: bool,
                            active: bool) -> Option<Prepared> {
    prepared.filter(|_| new_pending || !active)
}

/// Restore a deferred complete packet only when no newer receive arrived.
/// Caller must hold the same receive-slot lock used by ingestion.
pub fn restore_deferred<T>(slot: &mut Option<T>, packet: T) -> bool {
    if slot.is_none() { *slot=Some(packet); true } else { false }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex, mpsc};
    use std::thread;
    fn prepared() -> Prepared { Prepared { timestamp: Duration::from_nanos(9), order: u64::MAX, generation: 17 } }
    #[test]
    fn gate_requires_single_native_low_worker_and_excludes_other_modes() {
        assert!(allowed(true,1,true,true,false));
        assert!(!allowed(false,1,true,true,false));
        assert!(!allowed(true,2,true,true,false));
        assert!(!allowed(true,1,false,true,false));
        assert!(!allowed(true,1,true,false,false));
        assert!(!allowed(true,1,true,true,true));
    }
    #[test]
    fn latest_receive_supersedes_even_at_order_wrap() {
        let p=prepared();
        assert_eq!(cancel_before_submit(Some(p),true,true),Some(p));
        assert_eq!(cancel_before_submit(Some(p),false,true),None);
        assert_eq!(cancel_before_submit(Some(p),false,false),Some(p));
        assert_eq!(cancel_before_submit(None,true,false),None);
    }
    #[test]
    fn timestamp_generation_and_order_stay_bound_to_prepared_content() {
        let p=prepared();
        let mut metadata=Some(p);
        let submitted=metadata.take().unwrap();
        assert_eq!(submitted.timestamp,Duration::from_nanos(9));
        assert_eq!(submitted.order,u64::MAX);
        assert_eq!(submitted.generation,17);
        assert!(metadata.is_none());
    }
    #[test]
    fn empty_deferred_slot_restores_original_packet() {
        let mut slot=None;
        assert!(restore_deferred(&mut slot,(9,vec![1,2,3])));
        assert_eq!(slot,Some((9,vec![1,2,3])));
    }
    #[test]
    fn receive_during_native_deferral_keeps_newest_payload() {
        let slot=Arc::new(Mutex::new(Some((8,vec![8]))));
        let old=slot.lock().unwrap().take().unwrap();
        let (tx,rx)=mpsc::channel(); let receive=Arc::clone(&slot);
        let t=thread::spawn(move || { *receive.lock().unwrap()=Some((9,vec![9])); tx.send(()).unwrap(); });
        rx.recv().unwrap();
        assert!(!restore_deferred(&mut slot.lock().unwrap(),old));
        assert_eq!(*slot.lock().unwrap(),Some((9,vec![9])));
        t.join().unwrap();
    }
}
