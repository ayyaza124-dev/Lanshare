[app]
title = LANShare
package.name = lanshare
package.domain = org.lanshare
source.dir = .
source.include_exts = py
source.exclude_dirs = .github, bin, .buildozer, dist, build, tests
source.exclude_patterns = windows_app.py
version = 1.0.0

requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,pyjnius,android

orientation = portrait
fullscreen = 0

android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,WAKE_LOCK,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = False

# use a stable python-for-android release (the newest one builds Python 3.14, which breaks Kivy 2.3.0)
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
