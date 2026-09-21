"""
Everything that talks to Android lives here (through pyjnius), so main.py stays simple.
Every function is defensive: if a call fails on some phone we fall back instead of crashing.
"""
import os
from pathlib import Path

import lanshare

_keepalive = []   # hold references so the locks are not garbage-collected


def is_android():
    return "ANDROID_ARGUMENT" in os.environ


def _activity():
    from jnius import autoclass
    return autoclass("org.kivy.android.PythonActivity").mActivity


def sdk_int():
    from jnius import autoclass
    return autoclass("android.os.Build$VERSION").SDK_INT


def has_storage_access():
    """True when the app may write to the phone's shared storage (Internal storage/LANShare)."""
    try:
        if sdk_int() >= 30:
            from jnius import autoclass
            return bool(autoclass("android.os.Environment").isExternalStorageManager())
        return _activity().checkSelfPermission("android.permission.WRITE_EXTERNAL_STORAGE") == 0
    except Exception:
        return False


def request_storage_access():
    """Android 11+: opens the 'All files access' settings page for this app.
    Android 10 and older: shows the normal permission pop-up."""
    from jnius import autoclass
    act = _activity()
    if sdk_int() >= 30:
        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        Uri = autoclass("android.net.Uri")
        try:
            intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                            Uri.parse("package:" + act.getPackageName()))
            act.startActivity(intent)
        except Exception:
            act.startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
    else:
        act.requestPermissions(["android.permission.WRITE_EXTERNAL_STORAGE",
                                "android.permission.READ_EXTERNAL_STORAGE"], 1001)


def shared_folder():
    """Internal storage/LANShare - visible in the Files app and to other apps."""
    from jnius import autoclass
    base = autoclass("android.os.Environment").getExternalStorageDirectory().getAbsolutePath()
    return Path(str(base)) / "LANShare"


def private_folder():
    """App-only folder that always works without any permission (fallback)."""
    d = _activity().getExternalFilesDir(None)
    base = str(d.getAbsolutePath()) if d is not None else str(_activity().getFilesDir().getAbsolutePath())
    return Path(base) / "LANShare"


_SKIP_IFACES = ("rmnet", "ccmni", "dummy", "tun", "v4-", "lo", "p2p")


def local_ips():
    """This phone's Wi-Fi / hotspot addresses. Works with no internet (hotspot has no route)."""
    ips = []
    try:
        from jnius import autoclass
        NI = autoclass("java.net.NetworkInterface")
        en = NI.getNetworkInterfaces()
        while en is not None and en.hasMoreElements():
            ni = en.nextElement()
            name = str(ni.getName())
            if not ni.isUp() or ni.isLoopback() or name.startswith(_SKIP_IFACES):
                continue
            addrs = ni.getInetAddresses()
            while addrs.hasMoreElements():
                a = addrs.nextElement()
                if str(a.getClass().getName()) == "java.net.Inet4Address":
                    ips.append(str(a.getHostAddress()))
    except Exception:
        pass
    if not ips:
        ips = lanshare.lan_ips()
    out = []
    for ip in ips:
        if ip not in out and lanshare.usable_ip(ip):
            out.append(ip)
    return out


def keep_awake():
    """Keep the CPU and Wi-Fi running while the screen is off (best effort)."""
    try:
        from jnius import autoclass
        Context = autoclass("android.content.Context")
        PM = autoclass("android.os.PowerManager")
        act = _activity()
        wl = act.getSystemService(Context.POWER_SERVICE).newWakeLock(PM.PARTIAL_WAKE_LOCK, "LANShare:server")
        wl.acquire()
        _keepalive.append(wl)
        wifi = act.getApplicationContext().getSystemService(Context.WIFI_SERVICE)
        lk = wifi.createWifiLock(3, "LANShare:wifi")   # 3 = full high performance
        lk.acquire()
        _keepalive.append(lk)
    except Exception:
        pass


def open_in_browser(url):
    """Open the given link in the phone's normal browser (Chrome etc.)."""
    if not is_android():
        import webbrowser
        webbrowser.open(url)
        return
    from jnius import autoclass
    Intent = autoclass("android.content.Intent")
    Uri = autoclass("android.net.Uri")
    intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
    _activity().startActivity(intent)
