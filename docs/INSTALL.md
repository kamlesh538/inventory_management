# Installing and updating

## Getting the installer

**From GitHub (no tools needed):** on the repository page open **Actions → Build installer → Run workflow**.
When it finishes (about 10 minutes), download **JBHonda-Setup** from the run's *Artifacts* section and unzip it.

**On a Windows PC with the tools:** install [uv](https://docs.astral.sh/uv/) and
[Inno Setup 6](https://jrsoftware.org/isdl.php), then in the project folder run:

```
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The installer is written to `dist\JBHonda-Setup-<version>.exe`. The script runs all tests and starts
the built app once before packaging, and stops if anything fails.

## Installing on the shop computer

1. Copy `JBHonda-Setup-<version>.exe` to the computer (Windows 10 or 11, 64-bit) and double-click it.
2. Windows may show "Windows protected your PC" because the installer isn't code-signed:
   click **More info → Run anyway**.
3. Open the app from the desktop shortcut and fill in **Settings** (address, GSTIN, bank details, terms)
   before making the first invoice.

## Where the data is

Everything is in `C:\ProgramData\JaiBajrangBaliHonda\`:

| Folder / file | What |
|---|---|
| `data.db` | All records |
| `backups\` | Automatic daily backups (last 30) and manual ones |
| `invoices\` | Invoice PDFs, by financial year |
| `logs\app.log` | Error details, if something goes wrong |

**Uninstalling, reinstalling and updating never touch this folder.** Keep a copy of a recent backup on a pen
drive (Export & Backup → *Backup to pen drive / folder*).

## Updating to a new version

Close the app, run the new installer over the old one. On first start the app backs up the data and then
upgrades it to the new version automatically.

## Moving to a new computer

1. On the old computer: Export & Backup → *Backup to pen drive / folder*.
2. On the new computer: install the app, open Export & Backup → *Restore from backup...*, pick the file
   from the pen drive. The app closes; open it again.
