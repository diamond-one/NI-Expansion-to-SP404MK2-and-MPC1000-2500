import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import customtkinter as ctk
from fixtures import fixture, probe
from ni_to_sp.capture import inspect_render, prepare_kit
from ni_to_sp.library import discover_kits
from ni_to_sp.sp_transfer import TransferError
from ni_to_sp.capture_ui import App


def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)


def button(parent, text):
    return next(widget for widget in children(parent) if isinstance(widget, ctk.CTkButton) and widget.cget("text") == text)


def wait(app, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("UI job did not finish")
        app.update()
        time.sleep(.01)
    app.update()


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.kit, self.folder, self.job, self.master = fixture(self.root)
        self.env = patch.dict(os.environ, {"NI_TO_SP_DATA_DIR": str(self.root / "appdata")})
        self.env.start()
        with patch.object(App, "scan", lambda _: None):
            self.app = App()
        self.app.geometry("1120x800+10000+10000")
        self.app.update()

    def tearDown(self):
        self.app.close()
        self.env.stop()
        self.temp.cleanup()

    def prepared(self):
        report = inspect_render(self.job, self.master, confirmed=True)
        self.app.prepared, self.app.session = prepare_kit(self.job, report, self.root / "prepared")
        self.app.job, self.app.job_folder, self.app.kit = self.job, self.folder, self.kit
        self.app.sync()

    def test_desktop_select_capture_prepare_and_invalidate_workflow(self):
        app = self.app
        app.kits = discover_kits(self.root / "installed")
        app.filter_kits()
        app.kit_list.selection_set(0)
        app.select_kit()
        self.assertEqual(app.kit["name"], "Test Kit")
        app.spacing_var.set("4 seconds")
        app.create_job()
        wait(app, lambda: not app.busy)
        self.assertTrue((app.job_folder / "CAPTURE.mid").is_file())
        self.assertIn("MASTER output", app.guide.get("1.0", "end"))
        app.render_var.set(str(self.master))
        app.confirm_var.set(True)
        app.check_render()
        wait(app, lambda: not app.busy)
        self.assertTrue(app.prepared.is_dir())
        self.assertEqual(app.send_open_button.cget("state"), "normal")
        self.assertEqual(len(app.session.pads), 16)
        app.empty_var.set("1")
        self.assertIsNone(app.prepared)
        self.assertEqual(app.send_open_button.cget("state"), "disabled")

    def test_search_bounds_results_without_mutating_capture(self):
        app = self.app
        app.kits = [{**self.kit, "id": str(i), "name": f"Kit {i}"} for i in range(2000)]
        app.filter_kits()
        self.assertEqual(app.kit_list.size(), 300)
        app.job = self.job
        app.search_var.set("Kit 1999")
        self.assertEqual(app.kit_list.size(), 1)
        self.assertIs(app.job, self.job)

    def test_completed_kit_reopens_without_original_render(self):
        self.prepared()
        prepared = self.app.prepared
        self.master.unlink()
        self.app.invalidate_prepared()
        with patch("ni_to_sp.capture_ui.filedialog.askdirectory", return_value=str(prepared)):
            self.app.open_completed()
            wait(self.app, lambda: not self.app.busy)
        self.assertEqual(self.app.prepared, prepared)
        self.assertEqual(len(self.app.session.pads), 16)
        self.assertEqual(self.app.tabs.get(), "Review & send")
        self.assertEqual(self.app.send_open_button.cget("state"), "normal")
        self.assertIsNone(self.app.job)
        self.app.show_check_report()
        self.app.update()
        report_window = next(w for w in self.app.winfo_children() if isinstance(w, ctk.CTkToplevel))
        self.assertEqual(report_window.title(), "Capture check report")

    def test_transfer_requires_device_bank_review_and_uses_prepared_kit_only(self):
        app = self.app
        self.prepared()
        with patch("ni_to_sp.sp_transfer.list_ports", return_value=[{"port": "COM4"}]), \
             patch("ni_to_sp.sp_transfer.probe", return_value=probe()), \
             patch("ni_to_sp.sp_transfer.send", return_value={"completed_pads": list(range(16)), "journal": "fixture-report"}) as send:
            app.transfer_dialog()
            window = next(w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel))
            app.update()
            self.assertEqual(button(window, "Send to SP").cget("state"), "disabled")
            button(window, "Check connected SP").invoke()
            wait(app, lambda: not app.busy)
            menu = next(w for w in children(window) if isinstance(w, ctk.CTkOptionMenu) and "Choose bank" in w.cget("values"))
            menu.set("B")
            menu._command("B")
            send_button = button(window, "Send 16 pads to bank B")
            self.assertEqual(send_button.cget("state"), "disabled")
            check = next(w for w in children(window) if isinstance(w, ctk.CTkCheckBox) and w.cget("text").startswith("I checked"))
            check.select()
            app.update()
            self.assertEqual(send_button.cget("state"), "normal")
            send.assert_not_called()
            send_button.invoke()
            wait(app, lambda: not app.busy)
            self.assertEqual(send.call_args.kwargs["reviewed"], True)
            self.assertEqual(send.call_args.args[0].bank, "B")
            self.assertEqual(len(send.call_args.args[0].entries), 16)

    def test_failed_transfer_requires_fresh_device_check(self):
        app = self.app
        self.prepared()
        with patch("ni_to_sp.sp_transfer.list_ports", return_value=[{"port": "COM4"}]), \
             patch("ni_to_sp.sp_transfer.probe", return_value=probe()), \
             patch("ni_to_sp.sp_transfer.send", side_effect=TransferError("Disconnected", {"journal": "fixture"})), \
             patch("ni_to_sp.capture_ui.messagebox.showerror"):
            app.transfer_dialog()
            window = next(w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel))
            button(window, "Check connected SP").invoke()
            wait(app, lambda: not app.busy)
            menu = next(w for w in children(window) if isinstance(w, ctk.CTkOptionMenu) and "Choose bank" in w.cget("values"))
            menu.set("B")
            menu._command("B")
            check = next(w for w in children(window) if isinstance(w, ctk.CTkCheckBox) and w.cget("text").startswith("I checked"))
            check.select()
            button(window, "Send 16 pads to bank B").invoke()
            wait(app, lambda: not app.busy)
            self.assertEqual(button(window, "Send 16 pads to bank B").cget("state"), "disabled")
            self.assertEqual(menu.cget("state"), "normal")

    def test_multiple_samplers_require_explicit_port_selection(self):
        app = self.app
        self.prepared()
        with patch("ni_to_sp.sp_transfer.list_ports", return_value=[{"port": "COM4"}, {"port": "COM5"}]), \
             patch("ni_to_sp.sp_transfer.probe") as read_device:
            app.transfer_dialog()
            window = next(w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel))
            button(window, "Check connected SP").invoke()
            wait(app, lambda: not app.busy)
            menu = next(w for w in children(window) if isinstance(w, ctk.CTkOptionMenu) and "COM5" in w.cget("values"))
            self.assertIn("COM4", menu.cget("values"))
            read_device.assert_not_called()


if __name__ == "__main__":
    unittest.main()
