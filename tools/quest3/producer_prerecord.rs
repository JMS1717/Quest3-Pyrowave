//! Worker metadata only. Native code owns slots, command buffers and completion.
//! No packet FIFO: one preparation plus the receive thread's one latest payload.
use std::time::Duration;
use std::ffi::OsString;

/// Thread-local native cleanup. Declare after the allocation environment so
/// native resources drain before the process environment is restored, including
/// Rust unwind. The callback must not panic; native destruction remains checked
/// by the independent hardware restorer, not by these CPU tests.
pub struct NativeOwner<F: FnOnce()> { destroy: Option<F> }
impl<F: FnOnce()> NativeOwner<F> {
    pub fn new(destroy: F) -> Self { Self { destroy: Some(destroy) } }
}
impl<F: FnOnce()> Drop for NativeOwner<F> {
    fn drop(&mut self) {
        if let Some(destroy) = self.destroy.take() { destroy(); }
    }
}

/// Single decoder owner only. The eligible TCP worker is the sole constructor;
/// teardown joins it before another decoder is created. Restore process state
/// after native destruction when the later-declared NativeOwner drains first.
/// Constructor failure owns no native decoder.
pub struct AllocationEnvironment { previous: Option<OsString> }
impl AllocationEnvironment {
    pub fn enable() -> Self {
        let previous=std::env::var_os("PYROWAVE_NO_LINEAR_TEX");
        std::env::set_var("PYROWAVE_NO_LINEAR_TEX","1");
        Self { previous }
    }
}
impl Drop for AllocationEnvironment {
    fn drop(&mut self) {
        if let Some(value)=self.previous.take() { std::env::set_var("PYROWAVE_NO_LINEAR_TEX",value); }
        else { std::env::remove_var("PYROWAVE_NO_LINEAR_TEX"); }
    }
}

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
    #[test]
    fn allocation_scope_restores_prior_value_and_unset_state() {
        // One test owns this process variable; no other portable test reads it.
        let original=std::env::var_os("PYROWAVE_NO_LINEAR_TEX");
        std::env::set_var("PYROWAVE_NO_LINEAR_TEX","0");
        { let _guard=AllocationEnvironment::enable(); assert_eq!(std::env::var("PYROWAVE_NO_LINEAR_TEX").unwrap(),"1"); }
        assert_eq!(std::env::var("PYROWAVE_NO_LINEAR_TEX").unwrap(),"0");
        std::env::remove_var("PYROWAVE_NO_LINEAR_TEX");
        let _=std::panic::catch_unwind(|| { let _guard=AllocationEnvironment::enable(); panic!("constructor failed"); });
        assert!(std::env::var_os("PYROWAVE_NO_LINEAR_TEX").is_none());
        // Exercise the production guard and declaration order used by the
        // worker. This proves callback ordering, not actual GPU destruction.
        let destroyed=std::cell::Cell::new(0);
        for panic_after_creation in [false,true] {
            let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let _environment=AllocationEnvironment::enable();
                let _native=NativeOwner::new(|| {
                    assert_eq!(std::env::var("PYROWAVE_NO_LINEAR_TEX").unwrap(),"1");
                    destroyed.set(destroyed.get()+1);
                });
                if panic_after_creation { panic!("worker failed after native creation"); }
            }));
            assert_eq!(result.is_err(),panic_after_creation);
            assert!(std::env::var_os("PYROWAVE_NO_LINEAR_TEX").is_none());
        }
        assert_eq!(destroyed.get(),2);
        if let Some(v)=original { std::env::set_var("PYROWAVE_NO_LINEAR_TEX",v); }
    }
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
