import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--original-render':
        from ni_to_sp.original import render_request
        ok = render_request(sys.argv[2])
        sys.stdout.flush()
        sys.stderr.flush()
        if os.name == 'nt':
            # The receipt and WAV are closed and hashed. Avoid NI DLL detach
            # callbacks racing its remaining background threads on ExitProcess.
            import ctypes
            kernel = ctypes.WinDLL('kernel32')
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.TerminateProcess(kernel.GetCurrentProcess(), 0 if ok else 1)
        os._exit(0 if ok else 1)
    if len(sys.argv) == 3 and sys.argv[1] == '--original-queue':
        from ni_to_sp.original import run_queue
        run_queue(sys.argv[2])
        raise SystemExit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        from ni_to_sp.diagnostics import self_check
        raise SystemExit(0 if self_check(sys.argv[2]) else 1)
    if '--capture' in sys.argv:
        from ni_to_sp.capture_ui import App
    else:
        from ni_to_sp.converter_ui import App
    App().mainloop()
