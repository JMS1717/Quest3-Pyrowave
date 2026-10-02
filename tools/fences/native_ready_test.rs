// SPDX-License-Identifier: MIT
// Compile the real import helper against deterministic EGL stand-ins. CPU-only;
// this validates failure/FD ownership, never GPU synchronization or performance.
#[path = "native_ready.rs"]
mod native_ready;
#[path = "ready_wait.rs"]
mod ready_wait;
use std::{
    ffi::{c_char, c_void},
    os::fd::{FromRawFd, OwnedFd},
    sync::atomic::{AtomicU32, Ordering},
};
static FAILURE: AtomicU32 = AtomicU32::new(0);
struct Display;
impl Display {
    fn as_ptr(&self) -> *mut c_void {
        std::ptr::null_mut()
    }
}
struct GraphicsContext {
    egl_display: Display,
}
impl GraphicsContext {
    unsafe fn gl_proc_address(&self, n: &str) -> *const c_void {
        match n {
            "eglQueryString" => query as *const c_void,
            "eglCreateSyncKHR" => create as *const c_void,
            "eglDestroySyncKHR" => destroy as *const c_void,
            "eglWaitSyncKHR" => wait as *const c_void,
            _ => std::ptr::null(),
        }
    }
}
mod direct_eye {
    pub fn import_diagnostic(_: i32, _: String) {}
}
unsafe extern "C" fn query(_: *mut c_void, _: i32) -> *const c_char {
    c"EGL_ANDROID_native_fence_sync EGL_KHR_wait_sync".as_ptr()
}
unsafe extern "C" fn create(_: *mut c_void, kind: u32, attrs: *const i32) -> *mut c_void {
    assert_eq!(kind, 0x3144);
    assert_eq!(*attrs, 0x3145);
    assert_eq!(*attrs.add(2), 0x3038);
    let fd = OwnedFd::from_raw_fd(*attrs.add(1));
    if FAILURE.load(Ordering::Relaxed) & 1 != 0 {
        drop(fd);
        return std::ptr::null_mut();
    }
    Box::into_raw(Box::new(fd)).cast()
}
unsafe extern "C" fn destroy(_: *mut c_void, sync: *mut c_void) -> u32 {
    if FAILURE.load(Ordering::Relaxed) & 4 != 0 {
        return 0;
    }
    drop(Box::from_raw(sync.cast::<OwnedFd>()));
    1
}
unsafe extern "C" fn wait(_: *mut c_void, _: *mut c_void, flags: i32) -> u32 {
    assert_eq!(flags, 0);
    if FAILURE.load(Ordering::Relaxed) & 2 != 0 {
        0
    } else {
        1
    }
}
#[test]
fn checked_import_wait_failure_and_retained_cleanup() {
    use native_ready::NativeReady;
    use std::{
        io::{Read, Write},
        os::unix::net::UnixStream,
        rc::Rc,
    };
    let context = Rc::new(GraphicsContext {
        egl_display: Display,
    });
    let closed = |p: &mut UnixStream| matches!(p.read(&mut [0]), Ok(0));
    for failure in [0, 1, 2, 4] {
        FAILURE.store(failure, Ordering::Relaxed);
        let (source, mut peer) = UnixStream::pair().unwrap();
        peer.set_nonblocking(true).unwrap();
        // For failures requiring CPU fallback, provide a real poll event.
        if failure != 0 {
            peer.write_all(b"x").unwrap();
        }
        let mut helper = unsafe { NativeReady::new(&context) };
        assert!(unsafe { helper.wait(&context, source.into()) });
        if failure == 4 {
            assert!(!closed(&mut peer));
        } else {
            assert!(closed(&mut peer));
        }
        FAILURE.store(0, Ordering::Relaxed);
        drop(helper);
        assert!(closed(&mut peer));
    }
    FAILURE.store(1, Ordering::Relaxed);
    let (source, _peer) = UnixStream::pair().unwrap();
    let mut helper = unsafe { NativeReady::new(&context) };
    // Failure plus an unsignaled original must reject sampling after timeout.
    assert!(!unsafe { helper.wait(&context, source.into()) });
    FAILURE.store(0, Ordering::Relaxed);
}
