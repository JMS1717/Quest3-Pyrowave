// SPDX-License-Identifier: MIT
// Imported in the ALVR graphics crate. A server wait orders sampling; it does
// not prove CPU-observed completion. The original FD survives import failure.
use super::{direct_eye::import_diagnostic, ready_wait::wait_ready, GraphicsContext};
use std::{
    ffi::{c_void, CStr},
    os::fd::{AsFd, IntoRawFd, OwnedFd},
    rc::Rc,
    time::{Duration, Instant},
};
type Create = unsafe extern "C" fn(*mut c_void, u32, *const i32) -> *mut c_void;
type Destroy = unsafe extern "C" fn(*mut c_void, *mut c_void) -> u32;
type Wait = unsafe extern "C" fn(*mut c_void, *mut c_void, i32) -> u32;
type Query = unsafe extern "C" fn(*mut c_void, i32) -> *const std::ffi::c_char;

pub(crate) struct NativeReady {
    functions: Option<(Create, Destroy, Wait)>,
    server_waits: u64,
    cpu_waits: u64,
    server_us: u64,
    server_max_us: u64,
    context: Rc<GraphicsContext>,
    cleanup: Option<(Destroy, *mut c_void)>,
}
impl NativeReady {
    pub(crate) unsafe fn new(context: &Rc<GraphicsContext>) -> Self {
        let addresses = [
            "eglQueryString",
            "eglCreateSyncKHR",
            "eglDestroySyncKHR",
            "eglWaitSyncKHR",
        ]
        .map(|n| context.gl_proc_address(n));
        let functions = if addresses.iter().any(|a| a.is_null()) {
            None
        } else {
            let query: Query = std::mem::transmute(addresses[0]);
            let raw = query(context.egl_display.as_ptr(), 0x3055);
            if raw.is_null() {
                None
            } else {
                let extensions = CStr::from_ptr(raw).to_string_lossy();
                if ["EGL_ANDROID_native_fence_sync", "EGL_KHR_wait_sync"]
                    .iter()
                    .all(|e| extensions.split_whitespace().any(|s| s == *e))
                {
                    Some((
                        std::mem::transmute(addresses[1]),
                        std::mem::transmute(addresses[2]),
                        std::mem::transmute(addresses[3]),
                    ))
                } else {
                    None
                }
            }
        };
        import_diagnostic(
            4,
            format!(
                "[Q3PW_READY_FD] EGL server_wait_available={}",
                functions.is_some()
            ),
        );
        Self {
            functions,
            server_waits: 0,
            cpu_waits: 0,
            server_us: 0,
            server_max_us: 0,
            context: Rc::clone(context),
            cleanup: None,
        }
    }
    pub(crate) unsafe fn wait(&mut self, context: &GraphicsContext, fd: OwnedFd) -> bool {
        if let Some((create, destroy, wait)) = self.functions {
            if let Ok(duplicate) = fd.try_clone() {
                // EGL owns the duplicated FD after this call. Keep the original
                // for a checked fallback; never close/use the imported duplicate.
                let attrs = [0x3145, duplicate.into_raw_fd(), 0x3038];
                let display = context.egl_display.as_ptr();
                let started = Instant::now();
                let sync = create(display, 0x3144, attrs.as_ptr());
                if !sync.is_null() {
                    let ordered = wait(display, sync, 0) != 0;
                    let mut destroyed = destroy(display, sync) != 0;
                    if !destroyed {
                        // A failed destroy must not silently lose its handle.
                        destroyed = destroy(display, sync) != 0;
                        if !destroyed {
                            self.cleanup = Some((destroy, sync));
                        }
                    }
                    if ordered && destroyed {
                        self.server_waits += 1;
                        let elapsed_us = started.elapsed().as_micros() as u64;
                        self.server_us += elapsed_us;
                        self.server_max_us = self.server_max_us.max(elapsed_us);
                        if self.server_waits <= 3 || self.server_waits % 120 == 0 {
                            import_diagnostic(4,format!("[Q3PW_READY_FD] server_waits={} cpu_fallbacks={} server_mean_us={:.1} server_max_us={} completion_claim=false",self.server_waits,self.cpu_waits,self.server_us as f64 / self.server_waits as f64,self.server_max_us));
                        }
                        return true;
                    }
                }
                // Disable imports after failure. At most one failed sync is
                // retained for teardown; the display stays alive through Rc.
                self.functions = None;
                import_diagnostic(
                    6,
                    "[Q3PW_READY_FD] EGL import/wait/destroy failure; checked CPU fallback".into(),
                );
            }
        }
        self.cpu_waits += 1;
        let ready = wait_ready(fd.as_fd(), Duration::from_secs(1));
        if self.cpu_waits <= 3 || !ready {
            import_diagnostic(
                5,
                format!(
                    "[Q3PW_READY_FD] cpu_fallback={} verified_ready={ready}",
                    self.cpu_waits
                ),
            );
        }
        ready
    }
}
impl Drop for NativeReady {
    fn drop(&mut self) {
        if let Some((destroy, sync)) = self.cleanup.take() {
            let ok = unsafe { destroy(self.context.egl_display.as_ptr(), sync) != 0 };
            if !ok {
                import_diagnostic(
                    6,
                    "[Q3PW_READY_FD] retained_sync_cleanup_failed=true".into(),
                );
            }
        }
    }
}
