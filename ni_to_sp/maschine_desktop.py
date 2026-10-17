"""Product adapter for the observed Maschine 2.18.4 Windows desktop layout.

This adapter only operates the selected Maschine process. A foreground change,
unexpected modal, dirty user project or layout mismatch stops the batch.
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import time


class DesktopAttention(RuntimeError):
    pass


class MaschineDesktop:
    WIDTH, HEIGHT = 1212, 810

    def __init__(self, cancel=None, status_callback=None):
        self.cancel, self.status_callback = cancel, status_callback
        self.user = C.WinDLL('user32', use_last_error=True)
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.user.SetProcessDpiAwarenessContext(C.c_void_p(-4))
        self.user.GetForegroundWindow.restype = W.HWND
        self.user.GetWindow.restype = W.HWND
        self.user.WindowFromPoint.argtypes = [W.POINT]
        self.user.WindowFromPoint.restype = W.HWND
        self.user.GetDpiForWindow.argtypes = [W.HWND]
        self.user.GetDpiForWindow.restype = W.UINT
        self.user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        self.user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.kernel.OpenProcess.restype = W.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self._configure_input()
        expected = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Native Instruments/Maschine 2/Maschine 2.exe'
        windows = [(h, p) for h, p in self.windows() if self.executable(p) == expected
                   and self.class_name(h) != '#32770' and not self.user.GetWindow(h, 4)]
        if len(windows) != 1:
            raise DesktopAttention('Open one Maschine 2 window on a saved or new blank project, then resume.')
        self.hwnd, self.pid = windows[0]
        version = C.WinDLL('version')
        version.GetFileVersionInfoSizeW.argtypes = [W.LPCWSTR, C.c_void_p]
        version.GetFileVersionInfoW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p]
        version.VerQueryValueW.argtypes = [C.c_void_p, W.LPCWSTR, C.POINTER(C.c_void_p), C.POINTER(W.UINT)]
        size = version.GetFileVersionInfoSizeW(str(expected), None)
        data = C.create_string_buffer(size)
        pointer, length = C.c_void_p(), W.UINT()
        if not size or not version.GetFileVersionInfoW(str(expected), 0, size, data) or not version.VerQueryValueW(data, '\\', C.byref(pointer), C.byref(length)):
            raise DesktopAttention('Could not verify the installed Maschine desktop version.')
        fields = C.cast(pointer, C.POINTER(W.DWORD))
        if (fields[2] >> 16, fields[2] & 65535, fields[3] >> 16) != (2, 18, 4):
            raise DesktopAttention('The automated desktop profile is verified for Maschine 2.18.4 only.')
        title = self.title(self.hwnd)
        if title.endswith('*'):
            raise DesktopAttention('Save your current Maschine project before starting. Unsaved work is never discarded.')
        self.scale = max(self.user.GetDpiForWindow(self.hwnd) or 96, self.user.GetDpiForSystem()) / 96
        self.user.ShowWindow(self.hwnd, 9)
        self.user.SetWindowPos(self.hwnd, 0, round(24 * self.scale), round(24 * self.scale),
                               round(self.WIDTH * self.scale), round(self.HEIGHT * self.scale), 0x0014)
        self.user.SetForegroundWindow(self.hwnd)
        time.sleep(.35)
        self.wait_for_focus()

    def wait_for_focus(self):
        notified = False
        while self.process_id(self.user.GetForegroundWindow()) != self.pid:
            if self.cancel and self.cancel.is_set():
                raise InterruptedError('Original rendering paused. Completed kits are kept.')
            if not self.user.IsWindow(self.hwnd):
                raise DesktopAttention('Maschine closed. Reopen it, then resume the queue.')
            if not notified and self.status_callback:
                self.status_callback('Waiting for Maschine to be in front. Bring its window forward to continue.')
                notified = True
            time.sleep(.25)
        # Only resume at a clean boundary; no stale mouse action is retried.
        time.sleep(.4)
        self.check()

    def _configure_input(self):
        ULONG_PTR = C.c_size_t
        class Mouse(C.Structure):
            _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD),
                        ('dwFlags', W.DWORD), ('time', W.DWORD), ('dwExtraInfo', ULONG_PTR)]
        class Key(C.Structure):
            _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD),
                        ('time', W.DWORD), ('dwExtraInfo', ULONG_PTR)]
        class Union(C.Union):
            _fields_ = [('mi', Mouse), ('ki', Key)]
        class Input(C.Structure):
            _anonymous_ = ('data',)
            _fields_ = [('type', W.DWORD), ('data', Union)]
        self.Input, self.Key, self.Mouse = Input, Key, Mouse
        self.user.SendInput.argtypes = [W.UINT, C.POINTER(Input), C.c_int]

    def title(self, hwnd):
        data = C.create_unicode_buffer(1024)
        self.user.GetWindowTextW(hwnd, data, len(data))
        return data.value

    def class_name(self, hwnd):
        data = C.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, data, len(data))
        return data.value

    def process_id(self, hwnd):
        pid = W.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        return pid.value

    def windows(self):
        result = []
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        @callback_type
        def visit(hwnd, _):
            if self.user.IsWindowVisible(hwnd):
                result.append((hwnd, self.process_id(hwnd)))
            return True
        self.user.EnumWindows(visit, 0)
        return result

    def executable(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return Path('')
        try:
            buffer = C.create_unicode_buffer(32768)
            size = W.DWORD(len(buffer))
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(size)):
                return Path('')
            return Path(buffer.value)
        finally:
            self.kernel.CloseHandle(handle)

    def check(self):
        if self.cancel and self.cancel.is_set():
            raise InterruptedError('Original rendering paused. Completed kits are kept.')
        if not self.user.IsWindow(self.hwnd) or self.process_id(self.hwnd) != self.pid:
            raise DesktopAttention('The Maschine window closed. Reopen Maschine and resume.')
        if self.process_id(self.user.GetForegroundWindow()) != self.pid:
            foreground = self.executable(self.process_id(self.user.GetForegroundWindow())).name
            raise DesktopAttention(f'Maschine lost focus to {foreground or "another window"} during a loading step. Resume the queue.')

    def _send(self, events):
        self.check()
        array = (self.Input * len(events))(*events)
        if self.user.SendInput(len(events), array, C.sizeof(self.Input)) != len(events):
            raise DesktopAttention('Windows did not accept the Maschine input. No automatic retry was attempted.')

    def key(self, vk, modifiers=()):
        values = []
        for code in modifiers:
            item = self.Input(type=1)
            item.ki = self.Key(code, 0, 0, 0, 0)
            values.append(item)
        for flag in (0, 2):
            item = self.Input(type=1)
            item.ki = self.Key(vk, 0, flag, 0, 0)
            values.append(item)
        for code in reversed(modifiers):
            item = self.Input(type=1)
            item.ki = self.Key(code, 0, 2, 0, 0)
            values.append(item)
        self._send(values)
        time.sleep(.12)

    def text(self, value):
        values = []
        for offset in range(0, len(value.encode('utf-16-le')), 2):
            raw = value.encode('utf-16-le')[offset:offset + 2]
            code = int.from_bytes(raw, 'little')
            for flag in (4, 6):
                item = self.Input(type=1)
                item.ki = self.Key(0, code, flag, 0, 0)
                values.append(item)
        self._send(values)
        time.sleep(.15)

    def click(self, x, y, right=False):
        self.check()
        rect = W.RECT()
        self.user.GetWindowRect(self.hwnd, C.byref(rect))
        px, py = rect.left + round(x * self.scale), rect.top + round(y * self.scale)
        pointed = self.user.WindowFromPoint(W.POINT(px, py))
        if self.process_id(pointed) != self.pid:
            other = self.executable(self.process_id(pointed)).name
            raise DesktopAttention(f'Maschine control is covered by {other or "another window"} at {px},{py}; '
                f'window {rect.left},{rect.top},{rect.right},{rect.bottom}, scale {self.scale:g}. Bring Maschine forward, then resume.')
        self.user.SetCursorPos(px, py)
        # Maschine's custom menus update their hit target on mouse movement.
        # Let that movement be processed before injecting the button press.
        time.sleep(.12)
        events = []
        for flag in ((8, 16) if right else (2, 4)):
            item = self.Input(type=0)
            item.mi = self.Mouse(0, 0, 0, flag, 0, 0)
            events.append(item)
        self._send(events)
        time.sleep(.2)

    def modal(self, name, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            dialogs = [h for h, p in self.windows() if p == self.pid and self.class_name(h) == '#32770']
            matches = [h for h in dialogs if self.title(h) == name]
            if len(matches) == 1:
                return matches[0]
            if dialogs:
                raise DesktopAttention('Unexpected Maschine dialog: ' + ', '.join(self.title(h) for h in dialogs))
            time.sleep(.1)
        raise DesktopAttention('Maschine did not open the expected ' + name + ' dialog.')

    def file_dialog(self, name, path):
        dialog = self.modal(name)
        self.key(ord('N'), (0x12,))
        self.key(ord('A'), (0x11,))
        self.text(str(Path(path).resolve()))
        # Commit keyboard entry; setting text alone does not update the shell
        # dialog's selected filename model.
        self.key(0x0D)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            self.check()
            if not self.user.IsWindow(dialog) and self.user.IsWindowEnabled(self.hwnd):
                time.sleep(.25)
                return
            unexpected = [self.title(h) for h, p in self.windows()
                          if p == self.pid and h != dialog and self.class_name(h) == '#32770']
            if unexpected:
                raise DesktopAttention('Maschine needs attention: ' + ', '.join(unexpected))
            time.sleep(.15)
        raise DesktopAttention('Maschine did not finish loading or saving. Check its window before resuming.')

    def capture_state(self, kit, midi, project):
        """Fresh project per kit prevents old patterns or bus settings leaking."""
        self.wait_for_focus()
        if self.title(self.hwnd).endswith('*'):
            raise DesktopAttention('Maschine has unsaved changes. Save them before resuming.')
        self.key(ord('N'), (0x11,))
        time.sleep(.45)
        self.check()
        if self.title(self.hwnd) != 'New Project':
            raise DesktopAttention('A fresh Maschine project could not be verified.')
        self.click(404, 340, right=True)
        self.click(450, 352)
        self.file_dialog('Load Group', kit['path'])
        self.click(404, 340, right=True)
        self.click(465, 439)
        self.file_dialog('Import MIDI', midi)
        self.key(ord('S'), (0x11, 0x10))
        self.file_dialog('Save Project As', project)
        if not Path(project).is_file() or self.title(self.hwnd).endswith('*'):
            raise DesktopAttention('Maschine did not save the private capture state.')
