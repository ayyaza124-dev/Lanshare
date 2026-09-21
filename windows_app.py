#!/usr/bin/env python3
"""
LANShare for Windows (also runs on macOS/Linux).

A small window to start/stop the file-sharing server, pick the folder (your SSD),
and see the link + PIN to type on the phone. Built into LANShare.exe with PyInstaller.
"""
import json
import os
import queue
import secrets
import sys
import webbrowser
from pathlib import Path

import lanshare

APP_NAME = "LANShare"
DEFAULT_PORT = 8080


def settings_file():
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME / "settings.json"


class Controller:
    """All the logic; the window below only displays it (so it can be tested without a screen)."""

    def __init__(self, settings_path=None):
        self.settings_path = Path(settings_path) if settings_path else settings_file()
        self.srv = None
        self.port = DEFAULT_PORT
        self.pin = ""
        self.events = queue.Queue()
        saved = self._load()
        self.folder = saved.get("folder") or str(Path.home() / "LANShare")
        self.port = int(saved.get("port") or DEFAULT_PORT)
        lanshare.EVENT_HOOK = self.events.put

    def _load(self):
        try:
            return json.loads(self.settings_path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps({"folder": self.folder, "port": self.port}), "utf-8")
        except OSError:
            pass

    @property
    def running(self):
        return self.srv is not None

    def start(self, folder, port, pin=""):
        """Returns "" on success, otherwise a message to show the user."""
        if self.running:
            return ""
        try:
            port = int(port)
            if not 1024 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            return "Port must be a number between 1024 and 65535."
        pin = (pin or "").strip() or "%06d" % secrets.randbelow(10**6)
        if len(pin) < 4:
            return "The PIN must be at least 4 characters."
        if not str(folder).strip():
            return "Choose a folder to save files in."
        try:
            self.srv = lanshare.serve(folder, pin, port, host_ui=True)
        except (lanshare.FolderError, lanshare.PortError, ValueError) as e:
            return str(e)
        self.folder, self.port, self.pin = str(lanshare.CONFIG["root"]), port, pin
        self._save()
        self.events.put("Sharing started. Files are saved in %s" % self.folder)
        return ""

    def stop(self):
        if self.srv:
            srv, self.srv = self.srv, None
            lanshare.stop(srv)
            self.events.put("Sharing stopped.")

    def urls(self):
        return ["http://%s:%d" % (ip, self.port) for ip in lanshare.lan_ips()]

    def local_url(self):
        return "http://127.0.0.1:%d/" % self.port

    def open_here(self):
        webbrowser.open(self.local_url())

    def drain_events(self):
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, ttk

    ctl = Controller()
    root = tk.Tk()
    root.title(APP_NAME)
    root.minsize(560, 520)
    style = ttk.Style()
    try:
        style.theme_use("vista" if "vista" in style.theme_names() else style.theme_use())
    except tk.TclError:
        pass

    pad = {"padx": 14, "pady": 6}
    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="LANShare", font=("Segoe UI", 20, "bold")).pack(anchor="w", **pad)
    ttk.Label(frm, text="Send files between this PC and your phone over a Wi-Fi hotspot. "
                        "No internet needed.", wraplength=520).pack(anchor="w", padx=14)

    box = ttk.LabelFrame(frm, text="Save files in this folder (choose your SSD)")
    box.pack(fill="x", **pad)
    folder_var = tk.StringVar(value=ctl.folder)
    folder_entry = ttk.Entry(box, textvariable=folder_var)
    folder_entry.pack(side="left", fill="x", expand=True, padx=8, pady=8)

    def browse():
        chosen = filedialog.askdirectory(initialdir=folder_var.get() or str(Path.home()),
                                         title="Choose the folder where files are saved")
        if chosen:
            folder_var.set(os.path.normpath(chosen))
    browse_btn = ttk.Button(box, text="Browse...", command=browse)
    browse_btn.pack(side="right", padx=8, pady=8)

    opts = ttk.Frame(frm)
    opts.pack(fill="x", **pad)
    ttk.Label(opts, text="Port").pack(side="left")
    port_var = tk.StringVar(value=str(ctl.port))
    port_entry = ttk.Entry(opts, textvariable=port_var, width=7)
    port_entry.pack(side="left", padx=(6, 18))
    ttk.Label(opts, text="PIN (leave empty for a random one)").pack(side="left")
    pin_var = tk.StringVar()
    pin_entry = ttk.Entry(opts, textvariable=pin_var, width=12)
    pin_entry.pack(side="left", padx=6)

    status_var = tk.StringVar(value="Stopped.")
    ttk.Label(frm, textvariable=status_var, foreground="#8a1f1f", wraplength=520).pack(anchor="w", **pad)

    info = ttk.LabelFrame(frm, text="Open this on your phone (same Wi-Fi / hotspot)")
    link_var = tk.StringVar()
    pin_show = tk.StringVar()
    link_entry = ttk.Entry(info, textvariable=link_var, state="readonly", font=("Consolas", 14))
    link_entry.pack(fill="x", padx=8, pady=(8, 2))
    ttk.Label(info, textvariable=pin_show, font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=8, pady=4)
    btns = ttk.Frame(info)
    btns.pack(fill="x", padx=8, pady=(2, 8))

    def copy_link():
        root.clipboard_clear()
        root.clipboard_append(link_var.get())
    ttk.Button(btns, text="Copy link", command=copy_link).pack(side="left")
    ttk.Button(btns, text="Open on this PC", command=ctl.open_here).pack(side="left", padx=8)
    ttk.Button(btns, text="Open folder",
               command=lambda: os.startfile(ctl.folder) if hasattr(os, "startfile") else None
               ).pack(side="left")

    go_btn = ttk.Button(frm, text="Start sharing")
    go_btn.pack(anchor="w", **pad)

    log_frame = ttk.LabelFrame(frm, text="Activity")
    log_frame.pack(fill="both", expand=True, **pad)
    log = tk.Text(log_frame, height=8, state="disabled", wrap="word", relief="flat")
    log.pack(fill="both", expand=True, padx=6, pady=6)

    def add_log(msg):
        log.configure(state="normal")
        log.insert("end", msg + "\n")
        log.see("end")
        log.configure(state="disabled")

    def show_running(on):
        for w in (folder_entry, browse_btn, port_entry, pin_entry):
            w.configure(state="disabled" if on else "normal")
        go_btn.configure(text="Stop sharing" if on else "Start sharing")
        if on:
            info.pack(fill="x", before=go_btn, **pad)
            status_var.set("Running. Other devices connect with the link and PIN below.")
        else:
            info.pack_forget()

    def refresh_link():
        if not ctl.running:
            return
        urls = ctl.urls()
        link_var.set(urls[0] if urls else "No network found: turn on Wi-Fi or your hotspot")
        if len(urls) > 1:
            link_var.set("   or   ".join(urls))
        pin_show.set("PIN: " + ctl.pin)
        root.after(3000, refresh_link)

    def toggle():
        if ctl.running:
            ctl.stop()
            show_running(False)
            status_var.set("Stopped.")
            return
        err = ctl.start(folder_var.get(), port_var.get(), pin_var.get())
        if err:
            status_var.set(err)
            return
        show_running(True)
        refresh_link()
    go_btn.configure(command=toggle)

    def pump():
        for m in ctl.drain_events():
            add_log(m)
        root.after(300, pump)

    def on_close():
        ctl.stop()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    pump()
    root.mainloop()


if __name__ == "__main__":
    run_gui()
