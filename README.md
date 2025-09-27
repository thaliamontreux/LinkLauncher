# Link Launcher

A Windows **taskbar-style launcher** that pulls systems & connections from a **PHP/MariaDB API** and launches them with the right client (Browser, PuTTY, WinSCP, RDP). Supports multi-monitor docking, Windows accent colors or custom colors, per-user settings, users/groups/ACLs, default icons, and token-based auth.

---

## Table of Contents

* [What is it?](#what-is-it)
* [Features](#features)
* [Prerequisites](#prerequisites)
* [Server Installation (API)](#server-installation-api)
* [Windows App Installation](#windows-app-installation)
* [First Run](#first-run)
* [Key Concepts](#key-concepts)
* [Daily Use](#daily-use)
* [Admin Tasks](#admin-tasks)
* [Security Tips](#security-tips)
* [Troubleshooting](#troubleshooting)
* [Uninstall / Reset](#uninstall--reset)
* [Quick Start Checklist](#quick-start-checklist)

---

## What is it?

* **Server API (PHP + MariaDB):** stores users, groups, permissions, systems, and their connection links. Enforces visibility & edit rights and serves the Windows app.
* **Windows App (Python/PyQt or packaged EXE):** a dockable bar (top/left/right) that shows system icons and launches connections (HTTP/HTTPS/SSH/Telnet/FTP/FTPS/SFTP/SCP/RDP) with the correct program.

---

## Features

* **Docking & Multi-Monitor:** top/left/right; reserves screen space so full-screen apps don’t cover it.
* **Look & Feel:** uses Windows accent color or **custom colors** (taskbar, buttons, text, tables).
* **Scopes:** **System-wide** (admin), **Group** (visible to assigned groups), **User** (personal).
* **Permissions (per-group bitmask):** READ, WRITE, MODIFY, EDIT, DELETE, ARCHIVE.
* **Default icons** (HTTP, HTTPS, SSH, TELNET, FTP, FTPS, SFTP, SCP, RDP, etc.).
* **Token-based auth:** per-user tokens (rotate-able; emailed if mail is configured).
* **Launchers supported:**

  * **Browser** (HTTP/HTTPS)
  * **PuTTY** (TELNET/SSH)
  * **WinSCP** (FTP/FTPS/SFTP/SCP)
  * **MSTSC** (RDP)

**Default Ports** (auto-filled; you can override):

| Type   | Port |
| ------ | ---- |
| http   | 80   |
| https  | 443  |
| ssh    | 22   |
| telnet | 23   |
| ftp    | 21   |
| ftps   | 990* |
| sftp   | 22   |
| scp    | 22   |
| rdp    | 3389 |

*FTPS implicit default; explicit FTPS may still use 21.

---

## Prerequisites

### Server (your website host)

* PHP 8.0+ with `pdo_mysql`
* MariaDB/MySQL 10.4+
* Ability to upload PHP files (e.g., `https://yourdomain.com/api/`)
* Optional (recommended): PHP `mail()` works for token emails

### Windows PC(s)

* **Option A:** Use a prebuilt EXE/installer
* **Option B (source):** Python 3.11+ with:

  ```powershell
  pip install PyQt6 requests
  ```
* External clients (install and/or configure paths in Settings → Applications):

  * **PuTTY** (`putty.exe`) for Telnet/SSH
  * **WinSCP** (`winscp.exe`) for FTP/FTPS/SFTP/SCP
  * **MSTSC** (`%windir%\system32\mstsc.exe`) for RDP (built-in)

---

## Server Installation (API)

1. **Upload the installer**

   * Copy `install.php` to your site’s API folder, e.g. `https://yourdomain.com/api/install.php`

2. **Open in a browser**

   * Fill in:

     * MariaDB **root** user/password (temporary for setup)
     * **Database name** (default `taskapp1`)
     * **App DB user** (default `taskapp1_app`)
     * **Admin** username/display/email
     * Leave passwords blank to **auto-generate** strong passwords/tokens
     * Optional mail “From” & “Sender” (for token emails)

3. **Click Install**

   * Creates DB + tables, an app DB user, seeds groups/icons, creates/updates admin user/token
   * Writes:

     * `config.php` (DB + mail configuration)
     * `api1.php` (the API)

4. **Copy credentials**

   * **App DB password** and **Admin Token** are shown with “Copy” buttons—save them.

5. **Test**

   * Click “Test API” → should show `{"ok":true,...}`

> If you prefer raw SQL, import `taskapp1_schema.sql` instead and then place `api1.php` + `config.php`.

---

## Windows App Installation

### Option A: Prebuilt installer/EXE

* Run the installer and launch **Link Launcher** from Start Menu.

### Option B: Run from source

```powershell
pip install PyQt6 requests
python .\taskbarapp.py
```

(Optional) Build an EXE:

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name LinkLauncher taskbarapp.py
```

---

## First Run

* The app prompts for:

  * **API URL:** e.g. `https://yourdomain.com/api/api1.php`
  * **User Token:** paste the **Admin Token** from the installer (or the token for your user)
* The bar **docks to the top** (26px). Drag to **left/right** (76px width). It spans the full edge and reserves space.

**Colors**

* Default: Windows accent color
* **Settings → Appearance** → enable **Custom Colors** to override taskbar, button, text, table colors. Buttons auto-adjust text color for contrast.

**Applications**

* **Settings → Applications**: set custom paths if PuTTY/WinSCP aren’t on PATH.

---

## Key Concepts

* **User** — logs in with a **token** (like a password). Can edit profile & password; can request token rotation.
* **Group** — users can belong to multiple groups.
* **Permissions (per-group)** — READ (1), WRITE (2), MODIFY (4), EDIT (8), DELETE (16), ARCHIVE (32). Admins manage per user per group.
* **System** — a named collection (e.g., “Accounting”). Scope:

  * **System-wide** (admin only)
  * **Group** (assign one/more groups)
  * **User** (personal)
  * **Locked**: only owner/admin can change
* **Connection** — an entry under a System (protocol + host + port + username + path/params). Shown in a dropdown from the System’s icon.

---

## Daily Use

### Launch a link

1. Click a **System** on the left-aligned bar.
2. Pick a connection from the dropdown (if multiple).
3. App launches the right client:

   * Browser (HTTP/HTTPS)
   * PuTTY (Telnet/SSH)
   * WinSCP (FTP/FTPS/SFTP/SCP)
   * MSTSC (RDP)

### Add a System (if permitted)

1. **☰ → Manage Systems → Add**
2. Set **Name** and **Scope**

   * System (admin only)
   * Group (select groups)
   * User (personal)
3. Save → **Connections** → add protocols as needed.

### Add a Connection (auto-ports)

* Select **Type**; **Port** auto-fills (80/443/22/etc.). Override if needed.
* Fill Host, Username (if needed), Path/Params (if needed). Save.

### Edit Profile

* **☰ → Profile** — change display name, email, password; rotate token.

---

## Admin Tasks

* **☰ → User Editor** (only for admin)

  * Add/Edit/Delete users *(cannot delete/disable admins; cannot demote the **last** admin)*
  * Disable / enable user accounts
  * **Add Group**: assign multiple groups to a user (selectable list)
  * **Copy Token**: copy a user’s token
  * Set per-group **permissions** (READ/WRITE/MODIFY/EDIT/DELETE/ARCHIVE)

* **☰ → Manage Groups**

  * Create, rename, delete groups

* **☰ → Manage Systems**

  * Lock/unlock systems, change scope, assign groups

---

## Security Tips

* Treat **tokens** like passwords. Rotate when employees leave or tokens leak.
* Use **valid HTTPS** on your server to protect tokens in transit.
* Use strong admin credentials; restrict DB access (ideally local-only).
* Back up your database regularly.

---

## Troubleshooting

**API “Server error” or HTTP 500**

* Check PHP error logs.
* Ensure `pdo_mysql` is enabled.
* Verify `config.php` DB credentials.
* If tables are missing, re-run `install.php` (or import `taskapp1_schema.sql`).

**“Auth error: Invalid token”**

* Use the admin token from install (or the user’s current token).
* If you rotated, update the app with the **new** token.

**Cannot launch SSH/Telnet/FTP/SFTP/SCP**

* Install PuTTY/WinSCP and set their paths in **Settings → Applications** (or ensure they’re on PATH).

**Colors unreadable**

* **Settings → Appearance** → switch to **Custom Colors** and adjust. Button text auto-contrasts.

**Bar covered by apps**

* The bar reserves space when docked. Toggle docking or restart if needed.

---

## Uninstall / Reset

* **Windows settings**: `%APPDATA%\LinkLauncher\` (delete to reset).
* **Server**: remove `/api/api1.php` + `/api/config.php`.
  To delete all data: `DROP DATABASE taskapp1;` *(irreversible!)*

---

## Quick Start Checklist

* [ ] Upload `install.php` → run it → copy **Admin Token**
* [ ] Test `api1.php?action=sanity`
* [ ] Launch Windows app → set API URL + token
* [ ] (Admin) **User Editor** → create a normal user → **Copy token** & send
* [ ] **Manage Groups** → create “IT”, “Sales”, …
* [ ] **Manage Systems** → add a system (scope as needed) and **Connections** (HTTP/SSH/RDP…)
* [ ] **Settings → Applications** → set PuTTY/WinSCP paths if required
* [ ] **Settings → Appearance** → choose Windows accent or custom colors

