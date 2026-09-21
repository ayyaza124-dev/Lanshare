# LANShare

Send files, photos and videos between a **Windows PC** and an **Android phone** over a Wi-Fi hotspot.
No internet, no cloud. Files are saved as normal files in a folder you choose (for example on your SSD).

* **Windows app** → `LANShare.exe` (one file, double-click)
* **Android app** → `LANShare.apk`

Either device can be the host. The other device just opens a link in its browser and types a PIN, so it
can even be an iPhone or a Mac. Both apps run the same server as the earlier `lanshare.py`.

You do not need to install any programming tools. GitHub builds both apps for you, free, in the cloud.

---

## Part 1: Build the apps (about 45 minutes, mostly waiting)

1. Make a free account at **github.com** (skip if you have one).
2. Click **+** (top right) → **New repository**. Name it `lanshare`, choose **Public** (Public gives unlimited
   free build time), click **Create repository**.
3. On the new repository page click **uploading an existing file**.
4. Unzip this project on your PC. Select **everything inside the folder** (including the `.github` folder)
   and drag it onto the GitHub page. Wait for the upload, then click **Commit changes**.
   * If the `.github` folder did not upload: click **Add file → Create new file**, type the name
     `.github/workflows/build.yml`, open that file from the unzipped project in Notepad, copy all of it,
     paste it into GitHub and click **Commit changes**.
5. Click the **Actions** tab. If it asks, click **I understand my workflows, enable them**.
6. Click **Build LANShare apps** on the left → **Run workflow** → **Run workflow**.
   (It may already be running, because uploading files starts it.)
7. Wait. The Windows part takes about 5 minutes, the Android part 30-45 minutes the first time.
   A green tick means done. A red cross means it failed: open it, copy the red error text and send it to me.
8. Click the finished run. At the bottom, under **Artifacts**, download **LANShare-Windows** and
   **LANShare-Android** (each is a zip).

## Part 2: Install

**Windows:** unzip `LANShare-Windows`, double-click `LANShare.exe`.
Windows may say "protected your PC": click **More info → Run anyway** (the app is not code-signed).
When the firewall asks, click **Allow access**.

**Android:** copy the `.apk` from `LANShare-Android` to the phone and tap it.
Allow "Install unknown apps" for your file manager or browser when asked. If Play Protect warns, choose
**Install anyway** (the app is not from the Play Store).

## Part 3: Use

1. Put both devices on the same network: turn on the phone's hotspot and connect the PC to it (or the reverse).
2. On the **host** (whichever device will store the files):
   * **PC:** open LANShare, click **Browse** and choose a folder on your SSD, click **Start sharing**.
   * **Phone:** open LANShare. The first time, Android opens a page asking for **Allow access to all files**:
     switch it on and come back. Files are saved in **Internal storage / LANShare**.
3. The host shows a **link** (like `http://192.168.43.1:8080`) and a **PIN**.
4. On the **other device**, open that link in the browser, type the PIN, tap the big box and choose files.
   They are written straight into the host's folder. The same page lists everything in the folder, so the
   other device can download files too.
5. On the host itself, the button **Open on this PC / Open LANShare on this phone** opens the same page
   without needing the PIN, so the host can send and download files too.

## Good to know

* **Android: keep the app open while sharing.** Android may stop background apps to save battery. Turn
  off battery optimisation for LANShare if transfers stop when the screen is off.
* Android 10 and older cannot use the shared folder; the app then saves into its own folder and says so.
* Very large files are fine (streamed to disk). Folders cannot be sent: zip them first.
* Anyone who opens the link needs the PIN. Traffic is not encrypted, which is fine on your own WPA2 hotspot.
* The host can stop sharing any time (Stop button, or close the app). Your files stay in the folder.
* `lanshare.py` still works on its own from a terminal: `python lanshare.py --dir D:\LANShare`.
