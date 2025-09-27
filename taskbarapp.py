#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
WebLinkLauncher - Windows taskbar-like launcher bar for links

- Per-user storage under %APPDATA%\\LinkLauncher\\config.json
- Registers as an AppBar (top/left/right). Default: top (26 px).
- Fetches systems + connections from API, builds left-aligned launch menus.
- Supports HTTP/HTTPS (default browser), SSH/Telnet (PuTTY), FTP/FTPS/SFTP/SCP (WinSCP), RDP (mstsc).
- Connection editor ALWAYS auto-updates port to protocol defaults on type change.
- Settings: external tool paths, docking, default ports, and **custom color theme overrides**.
- Admin-only: Groups Editor, User Editor (group assign, ACLs, token rotate, disable).
- The taskbar background now ALWAYS paints your chosen color (accent or custom).
"""

import json, os, sys, ctypes, ctypes.wintypes as wintypes, webbrowser, subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

# ---- DPI policy MUST be set before Q(Gui)Application is created ----
QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
    QtCore.Qt.HighDpiScaleFactorRoundingPolicy.RoundPreferFloor
)

APP_NAME = "LinkLauncher"
API_BASE = "https://www.ypursite.com/api/api1.php"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

DOCK_TOP, DOCK_LEFT, DOCK_RIGHT = "top", "left", "right"
TOP_HEIGHT = 26
SIDE_WIDTH = 76

PERM_BITS = [
    ("READ", 1),
    ("WRITE", 2),
    ("MODIFY", 4),
    ("EDIT", 8),
    ("DELETE", 16),
    ("ARCHIVE", 32),
]

# ---------------------------
# Windows AppBar
# ---------------------------

class AppBar:
    ABM_NEW = 0x00000000
    ABM_REMOVE = 0x00000001
    ABM_QUERYPOS = 0x00000002
    ABM_SETPOS = 0x00000003
    ABE_LEFT = 0
    ABE_TOP = 1
    ABE_RIGHT = 2
    ABE_BOTTOM = 3

    class APPBARDATA(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.DWORD),
            ('hWnd', wintypes.HWND),
            ('uCallbackMessage', wintypes.UINT),
            ('uEdge', wintypes.UINT),
            ('rc', wintypes.RECT),
            ('lParam', wintypes.LPARAM),
        ]

    SHAppBarMessage = ctypes.windll.shell32.SHAppBarMessage

    @staticmethod
    def edge_from_dock(dock: str) -> int:
        return {
            DOCK_LEFT: AppBar.ABE_LEFT,
            DOCK_RIGHT: AppBar.ABE_RIGHT,
            DOCK_TOP: AppBar.ABE_TOP,
        }.get(dock, AppBar.ABE_TOP)

    @staticmethod
    def register(hwnd: int):
        abd = AppBar.APPBARDATA()
        abd.cbSize = ctypes.sizeof(AppBar.APPBARDATA)
        abd.hWnd = wintypes.HWND(hwnd)
        AppBar.SHAppBarMessage(AppBar.ABM_NEW, ctypes.byref(abd))

    @staticmethod
    def remove(hwnd: int):
        abd = AppBar.APPBARDATA()
        abd.cbSize = ctypes.sizeof(AppBar.APPBARDATA)
        abd.hWnd = wintypes.HWND(hwnd)
        AppBar.SHAppBarMessage(AppBar.ABM_REMOVE, ctypes.byref(abd))

    @staticmethod
    def set_pos(hwnd: int, dock: str, geom: QtCore.QRect):
        abd = AppBar.APPBARDATA()
        abd.cbSize = ctypes.sizeof(AppBar.APPBARDATA)
        abd.hWnd = wintypes.HWND(hwnd)
        abd.uEdge = AppBar.edge_from_dock(dock)
        abd.rc = wintypes.RECT(geom.left(), geom.top(), geom.right(), geom.bottom())
        AppBar.SHAppBarMessage(AppBar.ABM_QUERYPOS, ctypes.byref(abd))
        AppBar.SHAppBarMessage(AppBar.ABM_SETPOS, ctypes.byref(abd))

# ---------------------------
# Windows Accent Color (from registry)
# ---------------------------

class Accent:
    @staticmethod
    def _read_dword(path: str, name: str) -> Optional[int]:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                val, typ = winreg.QueryValueEx(key, name)
                if typ == winreg.REG_DWORD:
                    return int(val)
        except Exception:
            return None
        return None

    @staticmethod
    def get_accent() -> QtGui.QColor:
        # HKCU\Software\Microsoft\Windows\DWM\AccentColor (fallback ColorizationColor)
        d = Accent._read_dword(r"Software\Microsoft\Windows\DWM", "AccentColor")
        if d is None:
            d = Accent._read_dword(r"Software\Microsoft\Windows\DWM", "ColorizationColor")
        if d is not None:
            a = 255
            b = (d >> 16) & 0xFF
            g = (d >> 8) & 0xFF
            r = d & 0xFF
            return QtGui.QColor(r, g, b, a)
        # fallback slate
        return QtGui.QColor(45, 55, 72, 255)

    @staticmethod
    def is_dark(c: QtGui.QColor) -> bool:
        l = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
        return l < 0.5

# ---------------------------
# Color helpers
# ---------------------------

def qcolor_from_hex(h: str, fallback: QtGui.QColor) -> QtGui.QColor:
    try:
        c = QtGui.QColor(h)
        if c.isValid():
            return c
    except Exception:
        pass
    return fallback

def hex_from_qcolor(c: QtGui.QColor) -> str:
    return "#{:02X}{:02X}{:02X}".format(c.red(), c.green(), c.blue())

def choose_color(parent, start_hex: str) -> Optional[str]:
    c0 = qcolor_from_hex(start_hex, QtGui.QColor(0,0,0))
    c = QtWidgets.QColorDialog.getColor(c0, parent, "Choose color",
                                        QtWidgets.QColorDialog.ColorDialogOption.ShowAlphaChannel ^ QtWidgets.QColorDialog.ColorDialogOption.ShowAlphaChannel)
    if c.isValid():
        return hex_from_qcolor(c)
    return None

def readable_text_color(bg: QtGui.QColor) -> QtGui.QColor:
    # WCAG-ish luminance heuristic
    l = (0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()) / 255.0
    return QtGui.QColor(240, 240, 240) if l < 0.5 else QtGui.QColor(20, 20, 20)

# ---------------------------
# Config manager
# ---------------------------

DEFAULT_CONFIG = {
    "api_base": API_BASE,
    "verify_ssl": False,  # ignore certificates
    "token": "",
    "dock": DOCK_TOP,
    "monitor_index": 0,

    # External tools
    "winscp_path": "C:\\Program Files (x86)\\WinSCP\\WinSCP.exe",
    "putty_path": "C:\\Program Files\\PuTTY\\putty.exe",
    "mstsc_path": r"%WINDIR%\\System32\\mstsc.exe",

    # Launch templates
    "winscp_template": "\"{winscp}\" {proto}://{userpart}{host}:{port}{path}",
    "putty_template_ssh": "\"{putty}\" -ssh {userpart}{host} -P {port}",
    "putty_template_telnet": "\"{putty}\" -telnet {host} -P {port}",
    "http_template": "{proto}://{host}:{port}{path}",
    "rdp_template": "\"{mstsc}\" /v:{host}:{port}",

    # Default ports
    "http_default_port": 80,
    "https_default_port": 443,
    "ssh_default_port": 22,
    "telnet_default_port": 23,
    "ftp_default_port": 21,
    "ftps_default_port": 990,
    "sftp_default_port": 22,
    "scp_default_port": 22,
    "rdp_default_port": 3389,

    # Theme
    "use_system_colors": True,
    "color_bg": "#2D3748",
    "color_text": "#FFFFFF",
    "color_button": "#3A475E",
    "color_button_text": "#FFFFFF",
    "color_table_bg": "#2F3B52",
    "color_table_text": "#FFFFFF",
}

def default_port(proto: str, cfg: Optional["Config"]=None) -> int:
    key = f"{proto}_default_port"
    src = cfg or DEFAULT_CONFIG
    try:
        return int(src.get(key, 0))
    except Exception:
        return int(DEFAULT_CONFIG.get(key, 0))

class Config:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._cfg = DEFAULT_CONFIG.copy()
        if CONFIG_FILE.exists():
            try:
                self._cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
            except Exception:
                pass

    def get(self, k: str, d=None):
        return self._cfg.get(k, d)

    def set(self, k: str, v):
        self._cfg[k] = v

    def save(self):
        CONFIG_FILE.write_text(json.dumps(self._cfg, indent=2), encoding="utf-8")

# ---------------------------
# API Client
# ---------------------------

class ApiError(Exception):
    pass

class ApiClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _params(self, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
        p = {"user_token": self.cfg.get("token", "")}
        if extra: p.update(extra)
        return p

    def _get(self, action: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        url = self.cfg.get("api_base", API_BASE)
        try:
            r = requests.get(url, params={"action": action, **(params or {})},
                             timeout=20, verify=self.cfg.get("verify_ssl", False))
        except Exception as e:
            raise ApiError(str(e))
        try:
            data = r.json()
        except Exception:
            raise ApiError(f"Bad response from API: {r.status_code}")
        if not data.get("ok", False):
            msg = data.get("error", "API error")
            det = data.get("detail")
            raise ApiError(f"{msg}" + (f" — {det}" if det else ""))
        return data

    def _post(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = self.cfg.get("api_base", API_BASE)
        try:
            r = requests.post(url, data={"action": action, **params},
                              timeout=20, verify=self.cfg.get("verify_ssl", False))
        except Exception as e:
            raise ApiError(str(e))
        try:
            data = r.json()
        except Exception:
            raise ApiError(f"Bad response from API: {r.status_code}")
        if not data.get("ok", False):
            msg = data.get("error", "API error")
            det = data.get("detail")
            raise ApiError(f"{msg}" + (f" — {det}" if det else ""))
        return data

    # Basic
    def sanity(self): return self._get("sanity")
    def me(self): return self._get("me", self._params())
    def systems(self): return self._get("systems", self._params())

    # Systems CRUD
    def create_system(self, **kwargs): return self._post("create_system", self._params(kwargs))
    def update_system(self, **kwargs): return self._post("update_system", self._params(kwargs))
    def delete_system(self, **kwargs): return self._post("delete_system", self._params(kwargs))

    # Connections
    def list_connections(self, system_id: int): return self._get("list_connections", self._params({"system_id": system_id}))
    def create_connection(self, **kwargs): return self._post("create_connection", self._params(kwargs))
    def update_connection(self, **kwargs): return self._post("update_connection", self._params(kwargs))
    def delete_connection(self, **kwargs): return self._post("delete_connection", self._params(kwargs))

    # Profile / token
    def update_profile(self, **kwargs): return self._post("update_profile", self._params(kwargs))
    def change_password(self, **kwargs): return self._post("change_password", self._params(kwargs))
    def rotate_token(self, **kwargs): return self._post("rotate_token", self._params(kwargs))

    # Admin: groups
    def admin_list_groups(self): return self._get("admin_list_groups", self._params())
    def admin_create_group(self, **kwargs): return self._post("admin_create_group", self._params(kwargs))
    def admin_update_group(self, **kwargs): return self._post("admin_update_group", self._params(kwargs))
    def admin_delete_group(self, **kwargs): return self._post("admin_delete_group", self._params(kwargs))

    # Admin: users
    def admin_list_users(self): return self._get("admin_list_users", self._params())
    def admin_create_user(self, **kwargs): return self._post("admin_create_user", self._params(kwargs))
    def admin_update_user(self, **kwargs): return self._post("admin_update_user", self._params(kwargs))
    def admin_delete_user(self, **kwargs): return self._post("admin_delete_user", self._params(kwargs))
    def admin_set_user_groups(self, **kwargs): return self._post("admin_set_user_groups", self._params(kwargs))

    # Admin: ACL + disable/role
    def admin_get_user_acl(self, **kwargs): return self._get("admin_get_user_acl", self._params(kwargs))
    def admin_set_user_acl(self, **kwargs): return self._post("admin_set_user_acl", self._params(kwargs))
    def admin_disable_user(self, **kwargs): return self._post("admin_disable_user", self._params(kwargs))
    def admin_set_role(self, **kwargs): return self._post("admin_set_role", self._params(kwargs))

# ---------------------------
# UI helpers
# ---------------------------

def msg_error(parent, text:str):
    QtWidgets.QMessageBox.critical(parent, "Error", text)

def msg_info(parent, text:str):
    QtWidgets.QMessageBox.information(parent, "Info", text)

def copy_to_clipboard(text: str):
    QtWidgets.QApplication.clipboard().setText(text)

# ---------------------------
# Group picker dialog
# ---------------------------

class GroupSelectDialog(QtWidgets.QDialog):
    def __init__(self, groups: List[Dict[str,Any]], preselected_ids: Optional[List[int]]=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Groups")
        self.resize(380, 420)
        self.groups = groups
        pre = set(preselected_ids or [])

        lay = QtWidgets.QVBoxLayout(self)
        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        for g in groups:
            it = QtWidgets.QListWidgetItem(g["name"])
            it.setData(Qt.ItemDataRole.UserRole, g)
            self.list.addItem(it)
            if g["id"] in pre:
                it.setSelected(True)
        lay.addWidget(self.list)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def selected_group_ids(self)->List[int]:
        out=[]
        for it in self.list.selectedItems():
            out.append(int(it.data(Qt.ItemDataRole.UserRole)["id"]))
        return out

# ---------------------------
# Token + Settings + Profile
# ---------------------------

class TokenDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enter API Token")
        self.setModal(True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Paste your API token:"))
        self.edit = QtWidgets.QLineEdit()
        self.edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        lay.addWidget(self.edit)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def token(self) -> str:
        return self.edit.text().strip()

class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Settings")
        self.resize(620, 680)

        main = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        # Core settings
        self.api_base = QtWidgets.QLineEdit(self.cfg.get("api_base"))
        self.verify_ssl = QtWidgets.QCheckBox("Verify SSL certificates (uncheck to ignore)")
        self.verify_ssl.setChecked(bool(self.cfg.get("verify_ssl")))
        self.dock_combo = QtWidgets.QComboBox(); self.dock_combo.addItems([DOCK_TOP, DOCK_LEFT, DOCK_RIGHT])
        self.dock_combo.setCurrentText(self.cfg.get("dock", DOCK_TOP))
        self.monitor_spin = QtWidgets.QSpinBox(); self.monitor_spin.setRange(0, 15); self.monitor_spin.setValue(int(self.cfg.get("monitor_index", 0)))

        # Tools + templates
        self.winscp_path = QtWidgets.QLineEdit(self.cfg.get("winscp_path"))
        self.putty_path = QtWidgets.QLineEdit(self.cfg.get("putty_path"))
        self.mstsc_path = QtWidgets.QLineEdit(self.cfg.get("mstsc_path"))
        self.winscp_tpl = QtWidgets.QLineEdit(self.cfg.get("winscp_template"))
        self.putty_tpl_ssh = QtWidgets.QLineEdit(self.cfg.get("putty_template_ssh"))
        self.putty_tpl_tel = QtWidgets.QLineEdit(self.cfg.get("putty_template_telnet"))
        self.http_tpl = QtWidgets.QLineEdit(self.cfg.get("http_template"))
        self.rdp_tpl = QtWidgets.QLineEdit(self.cfg.get("rdp_template"))

        # Defaults ports
        self.port_spins: Dict[str, QtWidgets.QSpinBox] = {}
        def mk_port(name, val):
            sb = QtWidgets.QSpinBox(); sb.setRange(1,65535); sb.setValue(int(self.cfg.get(name, val)))
            self.port_spins[name] = sb
            return sb

        form.addRow("API Base:", self.api_base)
        form.addRow("", self.verify_ssl)
        form.addRow("Dock edge:", self.dock_combo)
        form.addRow("Monitor index:", self.monitor_spin)

        form.addRow("WinSCP path:", self.winscp_path)
        form.addRow("PuTTY path:", self.putty_path)
        form.addRow("MSTSC path:", self.mstsc_path)

        form.addRow("WinSCP template:", self.winscp_tpl)
        form.addRow("PuTTY SSH template:", self.putty_tpl_ssh)
        form.addRow("PuTTY Telnet template:", self.putty_tpl_tel)
        form.addRow("HTTP template:", self.http_tpl)
        form.addRow("RDP template:", self.rdp_tpl)

        ports_grid = QtWidgets.QGridLayout()
        row = 0
        for proto, key in [("http","http_default_port"),("https","https_default_port"),
                           ("ssh","ssh_default_port"),("telnet","telnet_default_port"),
                           ("ftp","ftp_default_port"),("ftps","ftps_default_port"),
                           ("sftp","sftp_default_port"),("scp","scp_default_port"),
                           ("rdp","rdp_default_port")]:
            ports_grid.addWidget(QtWidgets.QLabel(proto), row, 0)
            ports_grid.addWidget(mk_port(key, DEFAULT_CONFIG[key]), row, 1)
            row += 1
        gb_ports = QtWidgets.QGroupBox("Default ports by protocol"); gb_ports.setLayout(ports_grid)

        # Theme overrides
        self.use_sys_colors = QtWidgets.QCheckBox("Use Windows accent colors (recommended)")
        self.use_sys_colors.setChecked(bool(self.cfg.get("use_system_colors", True)))

        def color_row(label, key):
            hbox = QtWidgets.QHBoxLayout()
            line = QtWidgets.QLineEdit(self.cfg.get(key))
            line.setMaxLength(7)
            btn = QtWidgets.QPushButton("Pick…")
            swatch = QtWidgets.QLabel("    ")
            swatch.setAutoFillBackground(True)
            def paint_swatch(hexval):
                c = qcolor_from_hex(hexval, QtGui.QColor(0,0,0))
                pal = swatch.palette()
                pal.setColor(QtGui.QPalette.ColorRole.Window, c)
                swatch.setPalette(pal)
                swatch.setFrameStyle(QtWidgets.QFrame.Shape.Panel.value | QtWidgets.QFrame.Shadow.Sunken.value)
            paint_swatch(line.text())
            def on_pick():
                chosen = choose_color(self, line.text())
                if chosen:
                    line.setText(chosen)
                    paint_swatch(chosen)
            btn.clicked.connect(on_pick)
            hbox.addWidget(line, 1); hbox.addWidget(btn); hbox.addWidget(swatch)
            return (label, key, line, hbox)

        self._color_fields = []
        for lbl, key in [
            ("Background", "color_bg"),
            ("Text", "color_text"),
            ("Button", "color_button"),
            ("Button text", "color_button_text"),
            ("Table background", "color_table_bg"),
            ("Table text", "color_table_text"),
        ]:
            rec = color_row(lbl, key)
            self._color_fields.append(rec)

        gb_colors = QtWidgets.QGroupBox("Custom theme colors (active when accent colors are OFF)")
        lay_colors = QtWidgets.QFormLayout()
        lay_colors.addRow("", self.use_sys_colors)
        for lbl, key, _line, hbox in self._color_fields:
            lay_colors.addRow(lbl + " color:", hbox)
        gb_colors.setLayout(lay_colors)

        main.addLayout(form)
        main.addWidget(gb_ports)
        main.addWidget(gb_colors)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.on_save)
        bb.rejected.connect(self.reject)
        main.addWidget(bb)

    def on_save(self):
        self.cfg.set("api_base", self.api_base.text().strip())
        self.cfg.set("verify_ssl", self.verify_ssl.isChecked())
        self.cfg.set("dock", self.dock_combo.currentText())
        self.cfg.set("monitor_index", self.monitor_spin.value())

        self.cfg.set("winscp_path", self.winscp_path.text().strip())
        self.cfg.set("putty_path", self.putty_path.text().strip())
        self.cfg.set("mstsc_path", self.mstsc_path.text().strip())
        self.cfg.set("winscp_template", self.winscp_tpl.text().strip())
        self.cfg.set("putty_template_ssh", self.putty_tpl_ssh.text().strip())
        self.cfg.set("putty_template_telnet", self.putty_tpl_tel.text().strip())
        self.cfg.set("http_template", self.http_tpl.text().strip())
        self.cfg.set("rdp_template", self.rdp_tpl.text().strip())

        for key, sb in self.port_spins.items():
            self.cfg.set(key, sb.value())

        self.cfg.set("use_system_colors", self.use_sys_colors.isChecked())
        for _, key, line, _ in self._color_fields:
            self.cfg.set(key, line.text().strip() or DEFAULT_CONFIG[key])

        self.cfg.save()
        self.accept()

class ProfileDialog(QtWidgets.QDialog):
    def __init__(self, api: ApiClient, me: Dict[str,Any], parent=None):
        super().__init__(parent)
        self.api = api
        self.me = me
        self.setWindowTitle("Profile")
        self.resize(420, 220)
        lay = QtWidgets.QFormLayout(self)
        self.display = QtWidgets.QLineEdit(me.get("display_name",""))
        self.email = QtWidgets.QLineEdit(me.get("email","") or "")
        lay.addRow("Display name:", self.display)
        lay.addRow("Email:", self.email)

        self.old_pw = QtWidgets.QLineEdit(); self.old_pw.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.new_pw = QtWidgets.QLineEdit(); self.new_pw.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        lay.addRow("Current password:", self.old_pw)
        lay.addRow("New password:", self.new_pw)

        h = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("Save Profile")
        self.btn_pw = QtWidgets.QPushButton("Change Password")
        h.addWidget(self.btn_save); h.addWidget(self.btn_pw)
        lay.addRow(h)

        self.btn_save.clicked.connect(self.on_save_profile)
        self.btn_pw.clicked.connect(self.on_change_password)

    def on_save_profile(self):
        try:
            self.api.update_profile(display_name=self.display.text().strip(), email=self.email.text().strip())
            msg_info(self, "Profile updated.")
        except ApiError as e:
            msg_error(self, str(e))

    def on_change_password(self):
        try:
            self.api.change_password(old_password=self.old_pw.text(), new_password=self.new_pw.text())
            msg_info(self, "Password changed.")
        except ApiError as e:
            msg_error(self, str(e))

# ---------------------------
# Admin: Create User dialog (with group picker)
# ---------------------------

class CreateUserDialog(QtWidgets.QDialog):
    def __init__(self, api: ApiClient, groups: List[Dict[str,Any]], parent=None):
        super().__init__(parent)
        self.api = api
        self.all_groups = groups
        self.selected_group_ids: List[int] = []

        self.setWindowTitle("Create User")
        self.resize(480, 380)
        outer = QtWidgets.QVBoxLayout(self)
        f = QtWidgets.QFormLayout()
        self.username = QtWidgets.QLineEdit()
        self.display = QtWidgets.QLineEdit()
        self.email = QtWidgets.QLineEdit()
        self.password = QtWidgets.QLineEdit(); self.password.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.can_group = QtWidgets.QCheckBox(); self.can_group.setChecked(True)
        self.can_personal = QtWidgets.QCheckBox(); self.can_personal.setChecked(True)
        f.addRow("Username:", self.username)
        f.addRow("Display name:", self.display)
        f.addRow("Email:", self.email)
        f.addRow("Password:", self.password)
        f.addRow("Can create group systems:", self.can_group)
        f.addRow("Can create personal systems:", self.can_personal)
        outer.addLayout(f)

        gh = QtWidgets.QHBoxLayout()
        self.grp_label = QtWidgets.QLabel("Groups: (none)")
        self.btn_add_group = QtWidgets.QPushButton("Add Group…")
        self.btn_clear_group = QtWidgets.QPushButton("Clear")
        gh.addWidget(self.grp_label); gh.addStretch(1); gh.addWidget(self.btn_add_group); gh.addWidget(self.btn_clear_group)
        outer.addLayout(gh)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        outer.addWidget(bb)

        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.btn_add_group.clicked.connect(self.on_add_group)
        self.btn_clear_group.clicked.connect(self.on_clear_groups)

    def on_add_group(self):
        dlg = GroupSelectDialog(self.all_groups, self.selected_group_ids, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.selected_group_ids = dlg.selected_group_ids()
            self._refresh_groups_label()

    def on_clear_groups(self):
        self.selected_group_ids = []
        self._refresh_groups_label()

    def _refresh_groups_label(self):
        if not self.selected_group_ids:
            self.grp_label.setText("Groups: (none)")
        else:
            names = [g["name"] for g in self.all_groups if g["id"] in self.selected_group_ids]
            self.grp_label.setText("Groups: " + ", ".join(names))

    def values(self)->Dict[str,Any]:
        return dict(
            username=self.username.text().strip(),
            display_name=self.display.text().strip(),
            email=self.email.text().strip(),
            password=self.password.text(),
            can_group='1' if self.can_group.isChecked() else '0',
            can_personal='1' if self.can_personal.isChecked() else '0',
            group_ids=",".join(map(str, self.selected_group_ids))
        )

# ---------------------------
# Admin: User Editor
# ---------------------------

class UserEditorDialog(QtWidgets.QDialog):
    def __init__(self, api: ApiClient, parent=None):
        super().__init__(parent)
        self.api = api
        self.setWindowTitle("User Editor (Admin)")
        self.resize(980, 600)

        main = QtWidgets.QHBoxLayout(self)

        # Left: user list
        left = QtWidgets.QVBoxLayout()
        self.user_list = QtWidgets.QListWidget()
        left.addWidget(self.user_list)
        hb = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add")
        self.btn_del = QtWidgets.QPushButton("Delete")
        hb.addWidget(self.btn_add); hb.addWidget(self.btn_del)
        left.addLayout(hb)

        # Right: details + groups + ACL
        right = QtWidgets.QFormLayout()
        self.u_id = QtWidgets.QLabel("-")
        self.u_username = QtWidgets.QLabel("-")
        self.u_display = QtWidgets.QLineEdit()
        self.u_email = QtWidgets.QLineEdit()
        self.u_role = QtWidgets.QComboBox(); self.u_role.addItems(["user","admin"])
        self.u_disabled = QtWidgets.QCheckBox("Disabled")
        self.u_can_group = QtWidgets.QCheckBox("Can create group systems")
        self.u_can_personal = QtWidgets.QCheckBox("Can create personal systems")
        right.addRow("ID:", self.u_id)
        right.addRow("Username:", self.u_username)
        right.addRow("Display name:", self.u_display)
        right.addRow("Email:", self.u_email)
        right.addRow("Role:", self.u_role)
        right.addRow(self.u_disabled)
        right.addRow(self.u_can_group)
        right.addRow(self.u_can_personal)

        grp_row = QtWidgets.QHBoxLayout()
        grp_row.addWidget(QtWidgets.QLabel("Group Membership:"))
        self.btn_add_group = QtWidgets.QPushButton("Add Group…")
        grp_row.addStretch(1)
        grp_row.addWidget(self.btn_add_group)

        self.groups_box = QtWidgets.QGroupBox()
        vb = QtWidgets.QVBoxLayout(self.groups_box)
        self.group_list = QtWidgets.QListWidget()
        self.group_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        vb.addLayout(grp_row)
        vb.addWidget(self.group_list)

        self.acl_box = QtWidgets.QGroupBox("Per-Group Permissions")
        vb2 = QtWidgets.QVBoxLayout(self.acl_box)
        self.acl_table = QtWidgets.QTableWidget(0, 1+len(PERM_BITS))
        headers = ["Group"] + [name for name,_ in PERM_BITS]
        self.acl_table.setHorizontalHeaderLabels(headers)
        self.acl_table.horizontalHeader().setStretchLastSection(True)
        self.acl_table.verticalHeader().setVisible(False)
        vb2.addWidget(self.acl_table)

        act = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("Save")
        self.btn_copy_token = QtWidgets.QPushButton("Copy Token (Rotate)")
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        act.addWidget(self.btn_save); act.addWidget(self.btn_copy_token); act.addWidget(self.btn_refresh)

        col = QtWidgets.QVBoxLayout()
        col.addLayout(right)
        col.addWidget(self.groups_box)
        col.addWidget(self.acl_box)
        col.addLayout(act)

        main.addLayout(left, 1)
        main.addLayout(col, 2)

        self.user_list.currentItemChanged.connect(self.on_user_selected)
        self.btn_add.clicked.connect(self.on_add)
        self.btn_del.clicked.connect(self.on_delete)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_copy_token.clicked.connect(self.on_copy_token)
        self.btn_refresh.clicked.connect(self.load_data)
        self.btn_add_group.clicked.connect(self.on_add_group_to_user)

        self._users: List[Dict[str,Any]] = []
        self._groups: List[Dict[str,Any]] = []
        self._current_user: Optional[Dict[str,Any]] = None
        self.load_data()

    def load_data(self):
        try:
            ures = self.api.admin_list_users()
            gres = self.api.admin_list_groups()
        except ApiError as e:
            msg_error(self, f"Load failed: {e}")
            return
        self._users = ures.get("users", [])
        self._groups = gres.get("groups", [])

        self.user_list.clear()
        for u in self._users:
            item = QtWidgets.QListWidgetItem(f"{u['username']} ({u['display_name']})")
            item.setData(Qt.ItemDataRole.UserRole, u)
            self.user_list.addItem(item)

        self.group_list.clear()
        for g in self._groups:
            it = QtWidgets.QListWidgetItem(f"{g['name']}  [#{g['id']}]")
            it.setData(Qt.ItemDataRole.UserRole, g)
            self.group_list.addItem(it)

        self._current_user = None
        self.clear_details()

    def clear_details(self):
        self.u_id.setText("-")
        self.u_username.setText("-")
        self.u_display.setText("")
        self.u_email.setText("")
        self.u_role.setCurrentText("user")
        self.u_disabled.setChecked(False)
        self.u_can_group.setChecked(True)
        self.u_can_personal.setChecked(True)
        self.acl_table.setRowCount(0)
        for i in range(self.group_list.count()):
            self.group_list.item(i).setSelected(False)

    def on_user_selected(self, cur: QtWidgets.QListWidgetItem, prev: QtWidgets.QListWidgetItem):
        self.clear_details()
        if not cur: return
        u = cur.data(Qt.ItemDataRole.UserRole)
        self._current_user = u
        self.u_id.setText(str(u["id"]))
        self.u_username.setText(u["username"])
        self.u_display.setText(u["display_name"])
        self.u_email.setText(u.get("email") or "")
        self.u_role.setCurrentText(u.get("role","user"))
        self.u_disabled.setChecked(bool(u.get("disabled",0)))
        self.u_can_group.setChecked(bool(u.get("can_group",1)))
        self.u_can_personal.setChecked(bool(u.get("can_personal",1)))

        gids = set(u.get("group_ids",[]))
        for i in range(self.group_list.count()):
            it = self.group_list.item(i)
            g = it.data(Qt.ItemDataRole.UserRole)
            it.setSelected(g["id"] in gids)

        try:
            acl = self.api.admin_get_user_acl(id=u["id"]).get("acl", [])
        except ApiError as e:
            msg_error(self, f"ACL load failed: {e}")
            return
        self.acl_table.setRowCount(len(acl))
        for r, row in enumerate(acl):
            gname = f"{row['name']}  [#{row['group_id']}]"
            item = QtWidgets.QTableWidgetItem(gname); item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.acl_table.setItem(r, 0, item)
            perms = int(row.get("perms", 0))
            for c, (_, bit) in enumerate(PERM_BITS, start=1):
                cb = QtWidgets.QCheckBox()
                cb.setChecked(bool(perms & bit))
                self.acl_table.setCellWidget(r, c, cb)
            self.acl_table.setRowHeight(r, 26)

    def on_add(self):
        try:
            gres = self.api.admin_list_groups()
        except ApiError as e:
            msg_error(self, f"Group list failed: {e}")
            return
        dlg = CreateUserDialog(self.api, gres.get("groups", []), self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals["username"] or not vals["display_name"] or not vals["password"]:
                msg_error(self, "Username, display name and password are required.")
                return
            try:
                res = self.api.admin_create_user(
                    username=vals["username"],
                    display_name=vals["display_name"],
                    email=vals["email"],
                    password=vals["password"],
                    can_group=vals["can_group"],
                    can_personal=vals["can_personal"],
                )
                new_id = res.get("id")
                new_token = res.get("new_token","")
                if vals.get("group_ids"):
                    self.api.admin_set_user_groups(id=new_id, group_ids=vals["group_ids"])
                if new_token:
                    copy_to_clipboard(new_token)
                    msg_info(self, f"User created.\nToken copied to clipboard:\n{new_token}")
                else:
                    msg_info(self, "User created.")
            except ApiError as e:
                msg_error(self, f"Create failed: {e}")
            self.load_data()

    def on_delete(self):
        u = self._current_user
        if not u: return
        if QtWidgets.QMessageBox.question(self, "Delete", f"Delete user '{u['username']}'?") != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.admin_delete_user(id=u["id"])
            self.load_data()
        except ApiError as e:
            msg_error(self, f"Delete failed: {e}")

    def on_save(self):
        u = self._current_user
        if not u: return
        uid = u["id"]
        try:
            self.api.admin_update_user(
                id=uid,
                display_name=self.u_display.text().strip(),
                email=self.u_email.text().strip(),
                can_group='1' if self.u_can_group.isChecked() else '0',
                can_personal='1' if self.u_can_personal.isChecked() else '0',
            )
            self.api.admin_set_role(id=uid, role=self.u_role.currentText())
            self.api.admin_disable_user(id=uid, disabled='1' if self.u_disabled.isChecked() else '0')

            gids=[]
            for i in range(self.group_list.count()):
                if self.group_list.item(i).isSelected():
                    gids.append(str(self.group_list.item(i).data(Qt.ItemDataRole.UserRole)["id"]))
            self.api.admin_set_user_groups(id=uid, group_ids=",".join(gids))

            acl=[]
            for r in range(self.acl_table.rowCount()):
                label = self.acl_table.item(r,0).text()
                gid = int(label.rsplit("#",1)[-1].rstrip("]"))
                perms = 0
                for c, (_, bit) in enumerate(PERM_BITS, start=1):
                    w = self.acl_table.cellWidget(r,c)
                    if isinstance(w, QtWidgets.QCheckBox) and w.isChecked():
                        perms |= bit
                acl.append({"group_id": gid, "perms": perms})
            self.api.admin_set_user_acl(id=uid, acl=json.dumps(acl))
            msg_info(self, "Saved.")
            self.load_data()
        except ApiError as e:
            msg_error(self, f"Save failed: {e}")

    def on_copy_token(self):
        u = self._current_user
        if not u: return
        try:
            res = self.api.rotate_token(id=u["id"])
            tok = res.get("new_token","")
            if tok:
                copy_to_clipboard(tok)
                msg_info(self, f"New token copied to clipboard:\n{tok}")
            else:
                msg_info(self, "Token rotated.")
        except ApiError as e:
            msg_error(self, f"Token operation failed: {e}")

    def on_add_group_to_user(self):
        if not self._groups or not self._current_user:
            return
        cur_ids=[]
        for i in range(self.group_list.count()):
            if self.group_list.item(i).isSelected():
                cur_ids.append(int(self.group_list.item(i).data(Qt.ItemDataRole.UserRole)["id"]))
        dlg = GroupSelectDialog(self._groups, cur_ids, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            sel = dlg.selected_group_ids()
            for i in range(self.group_list.count()):
                gid = int(self.group_list.item(i).data(Qt.ItemDataRole.UserRole)["id"])
                self.group_list.item(i).setSelected(gid in sel)

# ---------------------------
# Connections / Systems dialogs
# ---------------------------

class ConnectionEditDialog(QtWidgets.QDialog):
    """
    Add/Edit a connection.
    ALWAYS adopts protocol default when changing type; you can override after.
    """
    def __init__(self, cfg: Config, parent=None, data: Optional[Dict[str,Any]]=None):
        super().__init__(parent)
        self.cfg = cfg

        self.setWindowTitle("Edit Connection" if data else "Add Connection")
        self.resize(560, 340)
        f = QtWidgets.QFormLayout(self)
        self.type = QtWidgets.QComboBox(); self.type.addItems(['http','https','ssh','telnet','ftp','ftps','sftp','scp','rdp'])
        self.host = QtWidgets.QLineEdit()
        self.port = QtWidgets.QSpinBox(); self.port.setRange(1, 65535)
        self.username = QtWidgets.QLineEdit()
        self.path = QtWidgets.QLineEdit()
        self.params = QtWidgets.QLineEdit()
        f.addRow("Type:", self.type)
        f.addRow("Host:", self.host)
        f.addRow("Port:", self.port)
        f.addRow("Username:", self.username)
        f.addRow("Path:", self.path)
        f.addRow("Params:", self.params)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        f.addRow(bb)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)

        if data:
            self.type.setCurrentText(data.get("type","http"))
            self.host.setText(data.get("host",""))
            pv = int(data.get("port",0) or 0)
            if pv <= 0: pv = default_port(self.type.currentText(), self.cfg)
            self.port.setValue(pv)
            self.username.setText(data.get("username","") or "")
            self.path.setText(data.get("path","") or "")
            self.params.setText(data.get("params","") or "")
        else:
            # New row: set initial default
            self.port.setValue(default_port(self.type.currentText(), self.cfg))

        self.type.currentTextChanged.connect(self.on_type_changed)

    def on_type_changed(self, new_type: str):
        # ALWAYS adopt protocol default on change
        self.port.setValue(default_port(new_type, self.cfg))

    def values(self)->Dict[str,Any]:
        return dict(
            type=self.type.currentText(),
            host=self.host.text().strip(),
            port=str(self.port.value()),
            username=self.username.text().strip(),
            path=self.path.text().strip(),
            params=self.params.text().strip()
        )

class ConnectionsDialog(QtWidgets.QDialog):
    """Manage connections for one system (CRUD)."""
    def __init__(self, api: ApiClient, cfg: Config, system: Dict[str,Any], parent=None):
        super().__init__(parent)
        self.api = api
        self.cfg = cfg
        self.system = system
        self.setWindowTitle(f"Connections - {system.get('name','')}")
        self.resize(760, 420)

        main = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID","Type","Host","Port","Username","Path","Params"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        main.addWidget(self.table)

        hb = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add")
        self.edit_btn = QtWidgets.QPushButton("Edit")
        self.del_btn = QtWidgets.QPushButton("Delete")
        self.launch_btn = QtWidgets.QPushButton("Launch")
        hb.addWidget(self.add_btn); hb.addWidget(self.edit_btn); hb.addWidget(self.del_btn); hb.addStretch(1); hb.addWidget(self.launch_btn)
        main.addLayout(hb)

        self.add_btn.clicked.connect(self.on_add)
        self.edit_btn.clicked.connect(self.on_edit)
        self.del_btn.clicked.connect(self.on_delete)
        self.launch_btn.clicked.connect(self.on_launch)

        self.load()

    def load(self):
        try:
            res = self.api.list_connections(system_id=self.system["id"])
        except ApiError as e:
            msg_error(self, f"Load failed: {e}")
            return
        rows = res.get("connections", [])
        self.table.setRowCount(len(rows))
        for r, c in enumerate(rows):
            for col, key in enumerate(["id","type","host","port","username","path","params"]):
                it = QtWidgets.QTableWidgetItem(str(c.get(key,"")))
                it.setData(Qt.ItemDataRole.UserRole, c)
                self.table.setItem(r, col, it)

    def current(self)->Optional[Dict[str,Any]]:
        r = self.table.currentRow()
        if r < 0: return None
        return self.table.item(r,0).data(Qt.ItemDataRole.UserRole)

    def on_add(self):
        dlg = ConnectionEditDialog(self.cfg, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals["host"]:
                msg_error(self, "Host is required.")
                return
            if not vals["port"] or vals["port"] == "0":
                vals["port"] = str(default_port(vals["type"], self.cfg))
            try:
                self.api.create_connection(system_id=self.system["id"], **vals)
                self.load()
            except ApiError as e:
                msg_error(self, f"Create failed: {e}")

    def on_edit(self):
        cur = self.current()
        if not cur: return
        dlg = ConnectionEditDialog(self.cfg, self, data=cur)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals["port"] or vals["port"] == "0":
                vals["port"] = str(default_port(vals["type"], self.cfg))
            try:
                self.api.update_connection(id=cur["id"], **vals)
                self.load()
            except ApiError as e:
                msg_error(self, f"Update failed: {e}")

    def on_delete(self):
        cur = self.current()
        if not cur: return
        if QtWidgets.QMessageBox.question(self, "Delete", f"Delete connection #{cur['id']}?") != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.delete_connection(id=cur["id"])
            self.load()
        except ApiError as e:
            msg_error(self, f"Delete failed: {e}")

    def on_launch(self):
        cur = self.current()
        if not cur: return
        launch_connection(self.cfg, cur)

class SystemEditDialog(QtWidgets.QDialog):
    def __init__(self, api: ApiClient, groups: List[Dict[str,Any]], system: Optional[Dict[str,Any]]=None, parent=None):
        super().__init__(parent)
        self.api = api
        self.groups = groups
        self.system = system
        self.setWindowTitle("Edit System" if system else "Create System")
        self.resize(520, 360)
        f = QtWidgets.QFormLayout(self)
        self.name = QtWidgets.QLineEdit(system.get("name","") if system else "")
        self.scope = QtWidgets.QComboBox(); self.scope.addItems(["system","group","user"])
        if system: self.scope.setCurrentText(system.get("scope","system"))
        self.locked = QtWidgets.QCheckBox("Locked"); self.locked.setChecked(bool(system and system.get("locked")))
        self.archived = QtWidgets.QCheckBox("Archived"); self.archived.setChecked(bool(system and system.get("archived")))
        self.admin_url = QtWidgets.QLineEdit(system.get("admin_url","") if system else "")
        self.user_url  = QtWidgets.QLineEdit(system.get("user_url","") if system else "")

        f.addRow("Name:", self.name)
        f.addRow("Scope:", self.scope)
        f.addRow(self.locked)
        f.addRow(self.archived)
        f.addRow("Admin URL:", self.admin_url)
        f.addRow("User URL:", self.user_url)

        self.grp = QtWidgets.QListWidget(); self.grp.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        for g in groups:
            it = QtWidgets.QListWidgetItem(f"{g['name']}  [#{g['id']}]")
            it.setData(Qt.ItemDataRole.UserRole, g)
            self.grp.addItem(it)
        f.addRow(QtWidgets.QLabel("Groups (for scope=group):"))
        f.addRow(self.grp)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        f.addRow(bb)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)

    def values(self)->Dict[str,Any]:
        gids=[]
        for i in range(self.grp.count()):
            if self.grp.item(i).isSelected():
                gids.append(str(self.grp.item(i).data(Qt.ItemDataRole.UserRole)["id"]))
        return dict(
            name=self.name.text().strip(),
            scope=self.scope.currentText(),
            locked='1' if self.locked.isChecked() else '0',
            archived='1' if self.archived.isChecked() else '0',
            admin_url=self.admin_url.text().strip(),
            user_url=self.user_url.text().strip(),
            group_ids=",".join(gids)
        )

class SystemsDialog(QtWidgets.QDialog):
    def __init__(self, api: ApiClient, groups: List[Dict[str,Any]], systems: List[Dict[str,Any]], parent=None):
        super().__init__(parent)
        self.api = api
        self.groups = groups
        self.setWindowTitle("Manage Systems")
        self.resize(780, 420)

        main = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID","Name","Scope","Locked","Archived","URLs"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        main.addWidget(self.table)

        hb = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add")
        self.edit_btn = QtWidgets.QPushButton("Edit")
        self.del_btn = QtWidgets.QPushButton("Delete")
        self.conn_btn = QtWidgets.QPushButton("Connections…")
        hb.addWidget(self.add_btn); hb.addWidget(self.edit_btn); hb.addWidget(self.del_btn); hb.addStretch(1); hb.addWidget(self.conn_btn)
        main.addLayout(hb)

        self.add_btn.clicked.connect(self.on_add)
        self.edit_btn.clicked.connect(self.on_edit)
        self.del_btn.clicked.connect(self.on_delete)
        self.conn_btn.clicked.connect(self.on_connections)

        self._systems: List[Dict[str,Any]] = []
        self.reload(systems)

    def reload(self, systems: List[Dict[str,Any]]):
        self._systems = systems
        self.table.setRowCount(len(systems))
        for r, s in enumerate(systems):
            paired = [
                str(s.get("id","")),
                s.get("name",""),
                s.get("scope",""),
                "Yes" if s.get("locked") else "No",
                "Yes" if s.get("archived") else "No",
                f"A:{s.get('admin_url','') or ''}  U:{s.get('user_url','') or ''}"
            ]
            for c, val in enumerate(paired):
                it = QtWidgets.QTableWidgetItem(val)
                it.setData(Qt.ItemDataRole.UserRole, s)
                self.table.setItem(r,c,it)

    def current_system(self)->Optional[Dict[str,Any]]:
        r = self.table.currentRow()
        if r < 0: return None
        return self.table.item(r,0).data(Qt.ItemDataRole.UserRole)

    def on_add(self):
        dlg = SystemEditDialog(self.api, self.groups, None, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            vals = dlg.values()
            if not vals["name"]:
                msg_error(self, "Name is required.")
                return
            try:
                self.api.create_system(**vals)
                msg_info(self, "Created.")
                self.accept()
            except ApiError as e:
                msg_error(self, f"Create failed: {e}")

    def on_edit(self):
        s = self.current_system()
        if not s: return
        dlg = SystemEditDialog(self.api, self.groups, s, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            vals = dlg.values()
            try:
                self.api.update_system(id=s["id"], **vals)
                msg_info(self, "Updated.")
                self.accept()
            except ApiError as e:
                msg_error(self, f"Update failed: {e}")

    def on_delete(self):
        s = self.current_system()
        if not s: return
        if QtWidgets.QMessageBox.question(self, "Delete", f"Delete system '{s.get('name','')}'?") != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.delete_system(id=s["id"])
            msg_info(self, "Deleted.")
            self.accept()
        except ApiError as e:
            msg_error(self, f"Delete failed: {e}")

    def on_connections(self):
        s = self.current_system()
        if not s: return
        dlg = ConnectionsDialog(self.api, self.parent().cfg, s, self)
        dlg.exec()

# ---------------------------
# Launchers
# ---------------------------

def launch_connection(cfg: Config, c: Dict[str,Any]):
    typ = c.get("type","")
    host = c.get("host","")
    try:
        port = int(c.get("port") or 0)
    except Exception:
        port = 0
    username = c.get("username","")
    path = c.get("path","") or ""
    params = c.get("params","") or ""

    if port <= 0:
        port = default_port(typ, cfg)

    if typ in ("http","https"):
        proto = typ
        url = cfg.get("http_template").format(proto=proto, host=host, port=port, path=path)
        try:
            webbrowser.open(url)
        except Exception:
            os.startfile(url)  # type: ignore
        return

    if typ in ("ssh","telnet"):
        putty = cfg.get("putty_path")
        if not putty or not Path(putty).exists():
            QtWidgets.QMessageBox.warning(None, "PuTTY", "PuTTY path not set or not found in Settings.")
            return
        tpl = cfg.get("putty_template_ssh" if typ=="ssh" else "putty_template_telnet")
        userpart = (username + "@") if username else ""
        cmd = tpl.format(putty=putty, userpart=userpart, host=host, port=port, path=path, params=params)
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception as e:
            msg_error(None, f"Failed launching PuTTY: {e}")
        return

    if typ in ("ftp","ftps","sftp","scp"):
        winscp = cfg.get("winscp_path")
        if not winscp or not Path(winscp).exists():
            QtWidgets.QMessageBox.warning(None, "WinSCP", "WinSCP path not set or not found in Settings.")
            return
        tpl = cfg.get("winscp_template")
        userpart = (username + "@") if username else ""
        proto = typ
        cmd = tpl.format(winscp=winscp, proto=proto, userpart=userpart, host=host, port=port, path=path, params=params)
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception as e:
            msg_error(None, f"Failed launching WinSCP: {e}")
        return

    if typ == "rdp":
        mstsc = os.path.expandvars(cfg.get("mstsc_path") or "mstsc.exe")
        tpl = cfg.get("rdp_template")
        if not Path(mstsc).exists():
            mstsc = "mstsc"
            tpl = "mstsc /v:{host}:{port}"
        cmd = tpl.format(mstsc=mstsc, host=host, port=port)
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception as e:
            msg_error(None, f"Failed launching RDP: {e}")
        return

# ---------------------------
# The Taskbar Widget
# ---------------------------

class Taskbar(QtWidgets.QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.setObjectName("TaskbarRoot")  # for targeted stylesheet
        self.cfg = cfg
        self.api = ApiClient(cfg)
        self.me: Dict[str,Any] = {}
        self.systems: List[Dict[str,Any]] = []
        self.groups: List[Dict[str,Any]] = []

        # theme cache for paintEvent
        self._bg_color = QtGui.QColor(45,55,72)
        self._text_color = QtGui.QColor(255,255,255)

        self.dock_edge = cfg.get("dock", DOCK_TOP)
        self.monitor_index = int(cfg.get("monitor_index", 0))

        self.setWindowTitle("Link Launcher")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.root = QtWidgets.QHBoxLayout(self)
        self.root.setContentsMargins(6, 2, 6, 2)
        self.root.setSpacing(6)

        # Left: systems (left-aligned)
        self.left_scroll = QtWidgets.QScrollArea()
        self.left_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.left_scroll.setWidgetResizable(True)
        self.left_widget = QtWidgets.QWidget()
        self.left_layout = QtWidgets.QHBoxLayout(self.left_widget)
        self.left_layout.setContentsMargins(0,0,0,0)
        self.left_layout.setSpacing(4)
        self.left_scroll.setWidget(self.left_widget)
        self.root.addWidget(self.left_scroll, 1)

        # Right: inset with menu + username
        self.menu_container = QtWidgets.QFrame()
        self.menu_container.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.menu_container.setObjectName("menuInset")
        inset = QtWidgets.QHBoxLayout(self.menu_container)
        inset.setContentsMargins(8,2,8,2)
        inset.setSpacing(6)

        self.menu_btn = QtWidgets.QToolButton()
        self.menu_btn.setText("☰")
        self.menu_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.menu_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)

        self.menu = QtWidgets.QMenu(self.menu_btn)
        self.act_settings = self.menu.addAction("Settings")
        self.act_manage = self.menu.addAction("Manage Systems")
        self.act_profile = self.menu.addAction("Profile")
        self.act_refresh = self.menu.addAction("Refresh")
        self.menu.addSeparator()
        self.act_groups_editor = self.menu.addAction("Groups Editor (Admin)")
        self.act_groups_editor.setVisible(False)
        self.act_user_editor = self.menu.addAction("User Editor (Admin)")
        self.act_user_editor.setVisible(False)
        self.menu.addSeparator()
        self.act_logout = self.menu.addAction("Logout")
        self.menu_btn.setMenu(self.menu)

        inset.addWidget(self.menu_btn)

        self.user_label = QtWidgets.QLabel("")
        f = self.user_label.font(); f.setBold(True); self.user_label.setFont(f)
        inset.addWidget(self.user_label)

        self.root.addWidget(self.menu_container, 0)

        self.act_settings.triggered.connect(self.on_settings)
        self.act_manage.triggered.connect(self.on_manage)
        self.act_profile.triggered.connect(self.on_profile)
        self.act_refresh.triggered.connect(lambda: self.refresh_data(build_ui=True))
        self.act_user_editor.triggered.connect(self.on_user_editor)
        self.act_groups_editor.triggered.connect(self.on_groups_editor)
        self.act_logout.triggered.connect(self.on_logout)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.refresh_data)
        self.timer.start()

        self.apply_theme()  # sets _bg_color, stylesheet, palettes
        self.initial_login_and_load()
        self.position_and_appbar()

    # ---- Theme application ----
    def apply_theme(self):
        use_sys = bool(self.cfg.get("use_system_colors", True))
        if use_sys:
            bg = Accent.get_accent()
            text = readable_text_color(bg)
            # derive button slightly different from bg for contrast
            if Accent.is_dark(bg):
                btn_bg = QtGui.QColor(
                    min(int(bg.red()*1.12),255),
                    min(int(bg.green()*1.12),255),
                    min(int(bg.blue()*1.12),255)
                )
            else:
                btn_bg = QtGui.QColor(
                    max(int(bg.red()*0.92),0),
                    max(int(bg.green()*0.92),0),
                    max(int(bg.blue()*0.92),0)
                )
            btn_text = readable_text_color(btn_bg)
            table_bg = btn_bg
            table_text = btn_text
        else:
            bg = qcolor_from_hex(self.cfg.get("color_bg", DEFAULT_CONFIG["color_bg"]), QtGui.QColor(45,55,72))
            text = qcolor_from_hex(self.cfg.get("color_text", DEFAULT_CONFIG["color_text"]), readable_text_color(bg))
            btn_bg = qcolor_from_hex(self.cfg.get("color_button", DEFAULT_CONFIG["color_button"]), bg)
            btn_text = qcolor_from_hex(self.cfg.get("color_button_text", DEFAULT_CONFIG["color_button_text"]), readable_text_color(btn_bg))
            table_bg = qcolor_from_hex(self.cfg.get("color_table_bg", DEFAULT_CONFIG["color_table_bg"]), btn_bg)
            table_text = qcolor_from_hex(self.cfg.get("color_table_text", DEFAULT_CONFIG["color_table_text"]), readable_text_color(table_bg))

        # Cache for paintEvent
        self._bg_color = bg
        self._text_color = text

        # Palettes for child widgets
        pal = self.palette()
        for role in (
            QtGui.QPalette.ColorRole.Window,
            QtGui.QPalette.ColorRole.Base,
            QtGui.QPalette.ColorRole.AlternateBase,
            QtGui.QPalette.ColorRole.ToolTipBase,
        ):
            pal.setColor(role, bg)
        for role in (
            QtGui.QPalette.ColorRole.WindowText,
            QtGui.QPalette.ColorRole.Text,
            QtGui.QPalette.ColorRole.ToolTipText,
            QtGui.QPalette.ColorRole.BrightText,
        ):
            pal.setColor(role, text)
        pal.setColor(QtGui.QPalette.ColorRole.Button, btn_bg)
        pal.setColor(QtGui.QPalette.ColorRole.ButtonText, btn_text)

        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.left_widget.setPalette(pal)
        self.left_widget.setAutoFillBackground(True)
        self.left_scroll.viewport().setPalette(pal)
        self.left_scroll.viewport().setAutoFillBackground(True)

        # Explicit styles — include top-level exact selector so the BAR always repaints.
        self.setStyleSheet(f"""
            QWidget#TaskbarRoot {{
                background-color: rgb({bg.red()},{bg.green()},{bg.blue()});
            }}
            QFrame#menuInset {{
                background-color: rgb({btn_bg.red()},{btn_bg.green()},{btn_bg.blue()});
                border-radius: 8px;
            }}
            QToolButton {{
                border: 0px; padding: 2px 8px;
                color: rgb({btn_text.red()},{btn_text.green()},{btn_text.blue()});
                background: transparent;
                font-weight: 600;
            }}
            QLabel {{
                color: rgb({btn_text.red()},{btn_text.green()},{btn_text.blue()});
            }}
            QMenu {{
                background-color: rgb({bg.red()},{bg.green()},{bg.blue()});
                color: rgb({text.red()},{text.green()},{text.blue()});
                border: 1px solid rgba(0,0,0,60);
            }}
            QMessageBox, QDialog {{
                background-color: rgb({bg.red()},{bg.green()},{bg.blue()});
                color: rgb({text.red()},{text.green()},{text.blue()});
            }}
            QHeaderView::section {{
                background-color: rgb({btn_bg.red()},{btn_bg.green()},{btn_bg.blue()});
                color: rgb({btn_text.red()},{btn_text.green()},{btn_text.blue()});
                border: 1px solid rgba(0,0,0,60);
                padding: 2px 6px;
            }}
            QLineEdit, QSpinBox, QComboBox, QListWidget {{
                background-color: rgb({btn_bg.red()},{btn_bg.green()},{btn_bg.blue()});
                color: rgb({btn_text.red()},{btn_text.green()},{btn_text.blue()});
                border: 1px solid rgba(0,0,0,60);
            }}
            QTableWidget {{
                background-color: rgb({table_bg.red()},{table_bg.green()},{table_bg.blue()});
                color: rgb({table_text.red()},{table_text.green()},{table_text.blue()});
                gridline-color: rgba(0,0,0,90);
            }}
            QPushButton {{
                background-color: rgb({btn_bg.red()},{btn_bg.green()},{btn_bg.blue()});
                color: rgb({btn_text.red()},{btn_text.green()},{btn_text.blue()});
                border: 1px solid rgba(0,0,0,60);
                padding: 4px 10px;
                font-weight: 600;
            }}
            QPushButton::disabled {{
                background-color: rgba({btn_bg.red()},{btn_bg.green()},{btn_bg.blue()},160);
                color: rgba({btn_text.red()},{btn_text.green()},{btn_text.blue()},160);
            }}
        """)

        # Trigger repaint so background changes immediately
        self.update()

    # Force the taskbar background to paint our theme color, no matter what:
    def paintEvent(self, ev: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), self._bg_color)
        p.end()
        super().paintEvent(ev)

    # ---- Login & data ----
    def initial_login_and_load(self):
        token = self.cfg.get("token","")
        if not token:
            dlg = TokenDialog(self)
            if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                tok = dlg.token()
                if not tok:
                    QtWidgets.QMessageBox.critical(self, "Token", "Token is required.")
                    sys.exit(1)
                self.cfg.set("token", tok); self.cfg.save()
            else:
                sys.exit(0)
        self.refresh_data(build_ui=True)

    def refresh_data(self, build_ui: bool=False):
        try:
            me = self.api.me().get("user", {})
            sysdata = self.api.systems()
        except ApiError as e:
            msg_error(self, f"API error: {e}")
            return
        self.me = me
        self.user_label.setText(me.get("display_name","") or me.get("username",""))
        is_admin = (me.get("role") == "admin")
        self.act_user_editor.setVisible(is_admin)
        self.act_groups_editor.setVisible(is_admin)
        self.systems = sysdata.get("systems", [])
        try:
            self.groups = self.api.admin_list_groups().get("groups", []) if is_admin else []
        except Exception:
            self.groups = []
        if build_ui:
            self.rebuild_buttons()

    def rebuild_buttons(self):
        while self.left_layout.count():
            it = self.left_layout.takeAt(0)
            w = it.widget()
            if w: w.deleteLater()

        for s in self.systems:
            btn = QtWidgets.QToolButton()
            btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setText(s.get("name",""))
            btn.setAutoRaise(True)
            btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
            icon = self.make_letter_icon(s.get("name","")[:2] or "??")
            btn.setIcon(icon)
            btn.setIconSize(QtCore.QSize(24,24))

            menu = QtWidgets.QMenu(btn)
            aurl = s.get("admin_url") or ""
            uurl = s.get("user_url") or ""
            if aurl:
                act = menu.addAction("Open Admin URL")
                act.triggered.connect(lambda _, url=aurl: webbrowser.open(url))
            if uurl:
                act = menu.addAction("Open User URL")
                act.triggered.connect(lambda _, url=uurl: webbrowser.open(url))
            if aurl or uurl: menu.addSeparator()

            for c in s.get("connections", []):
                p = int(c.get('port') or 0) or default_port(c.get('type',''), self.cfg)
                label = f"{c.get('type').upper()} {c.get('host')}:{p}"
                act = menu.addAction(label)
                act.triggered.connect(lambda _, cc=c: launch_connection(self.cfg, cc))

            btn.setMenu(menu)
            self.left_layout.addWidget(btn)

        self.left_layout.addStretch(1)

    def make_letter_icon(self, letters: str) -> QtGui.QIcon:
        # derive icon chip from current bar color so it always reads
        bg = self._bg_color
        # slightly different for contrast
        if Accent.is_dark(bg):
            chip = QtGui.QColor(min(int(bg.red()*1.18),255),
                                min(int(bg.green()*1.18),255),
                                min(int(bg.blue()*1.18),255))
        else:
            chip = QtGui.QColor(max(int(bg.red()*0.86),0),
                                max(int(bg.green()*0.86),0),
                                max(int(bg.blue()*0.86),0))
        pm = QtGui.QPixmap(32,32); pm.fill(chip)
        p = QtGui.QPainter(pm)
        f = QtGui.QFont(); f.setBold(True); f.setPointSize(10)
        p.setFont(f)
        p.setPen(readable_text_color(chip))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, letters.upper())
        p.end()
        return QtGui.QIcon(pm)

    # ---- Menu actions ----
    def on_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.apply_theme()  # recolor bar immediately
            self.dock_edge = self.cfg.get("dock", DOCK_TOP)
            self.monitor_index = int(self.cfg.get("monitor_index", 0))
            self.position_and_appbar()
            self.refresh_data(build_ui=True)

    def on_manage(self):
        dlg = SystemsDialog(self.api, self.groups, self.systems, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.refresh_data(build_ui=True)

    def on_profile(self):
        dlg = ProfileDialog(self.api, self.me, self)
        dlg.exec()

    def on_user_editor(self):
        dlg = UserEditorDialog(self.api, self)
        dlg.exec()
        self.refresh_data(build_ui=True)

    def on_groups_editor(self):
        # Simple groups CRUD for admins
        try:
            gres = self.api.admin_list_groups()
        except ApiError as e:
            msg_error(self, f"Load groups failed: {e}")
            return
        groups = gres.get("groups", [])

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Groups Editor (Admin)")
        dlg.resize(520, 380)
        v = QtWidgets.QVBoxLayout(dlg)
        tbl = QtWidgets.QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(["ID","Name"])
        tbl.horizontalHeader().setStretchLastSection(True)
        v.addWidget(tbl)

        def reload():
            try:
                g2 = self.api.admin_list_groups().get("groups", [])
            except ApiError as e:
                msg_error(dlg, str(e)); return
            tbl.setRowCount(len(g2))
            for r, g in enumerate(g2):
                tbl.setItem(r,0, QtWidgets.QTableWidgetItem(str(g["id"])))
                tbl.setItem(r,1, QtWidgets.QTableWidgetItem(g["name"]))
        reload()

        h = QtWidgets.QHBoxLayout()
        btn_add = QtWidgets.QPushButton("Add")
        btn_ren = QtWidgets.QPushButton("Rename")
        btn_del = QtWidgets.QPushButton("Delete")
        h.addWidget(btn_add); h.addWidget(btn_ren); h.addWidget(btn_del); h.addStretch(1)
        v.addLayout(h)

        def cur_id():
            r = tbl.currentRow()
            if r < 0: return None
            return tbl.item(r,0).text()

        def on_add():
            name, ok = QtWidgets.QInputDialog.getText(dlg, "Add Group", "Group name:")
            if not ok or not name.strip(): return
            try:
                self.api.admin_create_group(name=name.strip())
                reload()
            except ApiError as e:
                msg_error(dlg, str(e))

        def on_ren():
            gid = cur_id()
            if not gid: return
            name, ok = QtWidgets.QInputDialog.getText(dlg, "Rename Group", "New name:")
            if not ok or not name.strip(): return
            try:
                self.api.admin_update_group(id=gid, name=name.strip())
                reload()
            except ApiError as e:
                msg_error(dlg, str(e))

        def on_del():
            gid = cur_id()
            if not gid: return
            if QtWidgets.QMessageBox.question(dlg, "Delete", f"Delete group #{gid}?") != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            try:
                self.api.admin_delete_group(id=gid)
                reload()
            except ApiError as e:
                msg_error(dlg, str(e))

        btn_add.clicked.connect(on_add)
        btn_ren.clicked.connect(on_ren)
        btn_del.clicked.connect(on_del)

        dlg.exec()

    def on_logout(self):
        if QtWidgets.QMessageBox.question(self, "Logout", "Log out and clear local token?") == QtWidgets.QMessageBox.StandardButton.Yes:
            self.cfg.set("token",""); self.cfg.save()
            QtWidgets.QMessageBox.information(self, "Logout", "Token cleared. Exiting.")
            QtCore.QCoreApplication.quit()

    # ---- Docking/appbar ----
    def position_and_appbar(self):
        screens = QtGui.QGuiApplication.screens()
        idx = min(max(0, self.monitor_index), max(0, len(screens)-1))
        scr = screens[idx]
        geo = scr.geometry()
        if self.dock_edge == DOCK_TOP:
            self.setGeometry(geo.x(), geo.y(), geo.width(), TOP_HEIGHT)
        elif self.dock_edge == DOCK_LEFT:
            self.setGeometry(geo.x(), geo.y(), SIDE_WIDTH, geo.height())
        else:  # right
            self.setGeometry(geo.right()-SIDE_WIDTH+1, geo.y(), SIDE_WIDTH, geo.height())
        self.show()

        hwnd = int(self.winId())
        AppBar.register(hwnd)
        AppBar.set_pos(hwnd, self.dock_edge, self.geometry())

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        try:
            AppBar.remove(int(self.winId()))
        except Exception:
            pass
        return super().closeEvent(e)

# ---------------------------
# Main
# ---------------------------

def main():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QtWidgets.QApplication(sys.argv)
    cfg = Config()
    tb = Taskbar(cfg)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
