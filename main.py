"""
LANShare for Android.

Starts the file-sharing server inside the app and shows the link + PIN to type on the
other device. Files are saved in  Internal storage/LANShare  on the phone.
Keep this app open while sharing.
"""
import secrets
import threading
import time
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.utils import escape_markup

import android_support as droid
import lanshare

PORT = 8080


class LANShareApp(App):
    title = "LANShare"

    def build(self):
        self.srv = None
        self.folder = ""
        self.status = "Starting..."
        self.pin = "%06d" % secrets.randbelow(10**6)
        self.lock = threading.Lock()
        self.booting = False

        root = BoxLayout(orientation="vertical", padding=24, spacing=14)
        root.add_widget(Label(text="LANShare", font_size="34sp", bold=True,
                              size_hint_y=None, height=64))
        self.info = Label(markup=True, halign="center", valign="middle", font_size="17sp")
        self.info.bind(size=lambda w, s: setattr(w, "text_size", (s[0], s[1])))
        root.add_widget(self.info)
        for text, handler in (("Open LANShare on this phone", self.open_here),
                              ("Copy link", self.copy_link),
                              ("Allow storage access", self.retry_storage)):
            root.add_widget(Button(text=text, size_hint_y=None, height=58, on_press=handler))
        Clock.schedule_interval(self.refresh, 2)
        return root

    # ---- lifecycle
    def on_start(self):
        threading.Thread(target=self.boot, daemon=True).start()

    def on_pause(self):
        return True          # keep running when the user switches app

    def on_resume(self):
        pass

    def on_stop(self):
        self.stop_server()

    # ---- server control
    def stop_server(self):
        with self.lock:
            if self.srv:
                try:
                    lanshare.stop(self.srv)
                except Exception:
                    pass
                self.srv = None

    def start_server(self, folder):
        with self.lock:
            self.srv = lanshare.serve(folder, self.pin, PORT, host_ui=True)
            self.folder = str(lanshare.CONFIG["root"])

    def pick_folder(self):
        if not droid.is_android():
            return Path.home() / "LANShare"
        if not droid.has_storage_access():
            self.status = ('Please switch on "Allow access to all files" for LANShare, '
                           "then come back to this app.")
            try:
                droid.request_storage_access()
            except Exception:
                pass
            for _ in range(90):
                time.sleep(1)
                if droid.has_storage_access():
                    break
        return droid.shared_folder() if droid.has_storage_access() else droid.private_folder()

    def boot(self):
        if self.booting:
            return
        self.booting = True
        try:
            if droid.is_android():
                droid.keep_awake()
            self.stop_server()
            folder = self.pick_folder()
            try:
                self.start_server(folder)
                self.status = "Running"
            except lanshare.FolderError:
                if not droid.is_android():
                    raise
                self.start_server(droid.private_folder())
                self.status = ("Running, but Android blocked the normal folder. Tap "
                               '"Allow storage access" to save in Internal storage/LANShare.')
        except lanshare.PortError as e:
            self.status = str(e)
        except Exception as e:  # never crash the app; show the reason instead
            self.status = "Could not start: %s" % e
        finally:
            self.booting = False

    # ---- screen
    def urls(self):
        ips = droid.local_ips() if droid.is_android() else lanshare.lan_ips()
        return ["http://%s:%d" % (ip, PORT) for ip in ips]

    def refresh(self, _dt):
        esc = escape_markup
        lines = ["[b]%s[/b]" % esc(self.status), ""]
        if self.srv:
            urls = self.urls()
            if urls:
                lines.append("On the other device, open this link in a browser:")
                lines += ["[size=24sp][b]%s[/b][/size]" % esc(u) for u in urls]
            else:
                lines.append("Turn on Wi-Fi or this phone's hotspot so other devices can connect.")
            lines += ["", "PIN:  [size=30sp][b]%s[/b][/size]" % esc(self.pin),
                      "", "Files are saved in:", esc(self.folder)]
        self.info.text = "\n".join(lines)

    # ---- buttons
    def open_here(self, *_):
        try:
            droid.open_in_browser("http://127.0.0.1:%d/" % PORT)
        except Exception as e:
            self.status = "Could not open the browser: %s" % e

    def copy_link(self, *_):
        urls = self.urls()
        if urls:
            Clipboard.copy(urls[0])

    def retry_storage(self, *_):
        threading.Thread(target=self.boot, daemon=True).start()


if __name__ == "__main__":
    LANShareApp().run()
