#!/usr/bin/env python3
# VentoyGTK4 — interfaccia GTK4/libadwaita per Ventoy2Disk.sh
#
#   python3 VentoyGTK4.py                        avvia l'interfaccia
#   python3 VentoyGTK4.py --ventoy-dir CARTELLA  usa (e ricorda) quel pacchetto Ventoy
#   python3 VentoyGTK4.py --install-desktop      crea il lanciatore nel menu di GNOME
#
# Serve un pacchetto Linux di Ventoy già estratto (ventoy-x.y.z-linux.tar.gz): viene
# cercato accanto allo script, nelle cartelle ventoy-* più comuni, oppure scelto
# dalla finestra; l'ultima scelta è salvata in ~/.config/ventoy-gtk4/config.json.
#
# Le operazioni sul disco girano come root tramite pkexec; l'interfaccia no.

import glob
import json
import os
import re
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

APP_ID = "net.oprtorino.VentoyGTK4"
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILE = os.path.join(GLib.get_user_config_dir(), "ventoy-gtk4", "config.json")
VENTOY_DIR = None  # cartella del pacchetto Ventoy in uso (vedi find_ventoy_dir)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
WIN11_KEYS = ("VTOY_WIN11_BYPASS_CHECK", "VTOY_WIN11_BYPASS_NRO")


# --------------------------------------------------------------------------
# utilità
# --------------------------------------------------------------------------
def is_ventoy_package(path):
    """Vero se path è un pacchetto Linux di Ventoy già compilato (non i sorgenti)."""
    return bool(path) and all(os.path.isfile(os.path.join(path, f)) for f in
                              ("Ventoy2Disk.sh", "boot/boot.img", "ventoy/version"))


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(**values):
    cfg = load_config()
    cfg.update(values)
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def find_ventoy_dir(explicit=None):
    """Cerca il pacchetto Ventoy: argomento, $VENTOY_DIR, ultima scelta salvata,
    cartella dello script, poi le cartelle ventoy-x.y.z più comuni (la più recente)."""
    for cand in (explicit, os.environ.get("VENTOY_DIR"), load_config().get("ventoy_dir"),
                 SCRIPT_DIR, os.path.join(SCRIPT_DIR, "..", "..", "INSTALL")):
        if is_ventoy_package(cand):
            return os.path.realpath(cand)
    home = os.path.expanduser("~")
    found = []
    for pattern in ("ventoy-*", "Downloads/ventoy-*", "Scaricati/ventoy-*",
                    "Documents/ventoy-*", "Documents/*/ventoy-*", "Documenti/*/ventoy-*",
                    "Applications/ventoy-*", "opt/ventoy-*"):
        found += [d for d in glob.glob(os.path.join(home, pattern)) if is_ventoy_package(d)]
    found += [d for d in glob.glob("/opt/ventoy*") if is_ventoy_package(d)]

    def ver(d):
        try:
            return tuple(int(x) for x in package_version(d).split("."))
        except ValueError:
            return (0,)
    return max(found, key=ver) if found else None


def package_version(path=None):
    path = path or VENTOY_DIR
    if not path:
        return "?"
    try:
        with open(os.path.join(path, "ventoy", "version")) as f:
            return f.read().strip()
    except OSError:
        return "?"


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return str(n)


def list_disks(show_all=False):
    """Restituisce i dischi come lista di dict, leggendo lsblk (non serve root)."""
    try:
        out = subprocess.run(
            ["lsblk", "-J", "-b", "-o",
             "NAME,PATH,SIZE,MODEL,VENDOR,TRAN,RM,HOTPLUG,TYPE,LABEL,FSTYPE,MOUNTPOINT"],
            capture_output=True, text=True, check=True).stdout
        data = json.loads(out)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return []

    def as_bool(v):
        return v in (True, 1, "1", "true")

    disks = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk" or dev.get("name", "").startswith(("zram", "loop")):
            continue
        removable = as_bool(dev.get("rm")) or as_bool(dev.get("hotplug")) or dev.get("tran") == "usb"
        if not show_all and not removable:
            continue
        parts = dev.get("children") or []
        is_ventoy = len(parts) >= 2 and parts[1].get("label") == "VTOYEFI"
        p1 = parts[0] if parts else {}
        model = " ".join(x for x in ((dev.get("vendor") or "").strip(),
                                    (dev.get("model") or "").strip()) if x) or "Disco"
        disks.append({
            "path": dev["path"],
            "size": int(dev.get("size") or 0),
            "model": model,
            "tran": dev.get("tran") or "",
            "removable": removable,
            "ventoy": is_ventoy,
            "part1": p1.get("path") if is_ventoy else None,
            "part1_label": p1.get("label") if is_ventoy else None,
            "mountpoint": p1.get("mountpoint") if is_ventoy else None,
            "mounted_any": any(p.get("mountpoint") for p in parts),
        })
    return disks


# --------------------------------------------------------------------------
# finestra
# --------------------------------------------------------------------------
class VentoyWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ventoy")
        self.set_default_size(580, 820)
        self.disks = []
        self.proc = None
        self.last_output = []

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        # --- header
        header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Ventoy")
        header.set_title_widget(self.title)
        self.folder_btn = Gtk.Button(icon_name="folder-symbolic",
                                     tooltip_text="Scegli la cartella del pacchetto Ventoy")
        self.folder_btn.connect("clicked", lambda *_: self.choose_package())
        header.pack_end(self.folder_btn)
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                      tooltip_text="Aggiorna l'elenco dei dispositivi")
        self.refresh_btn.connect("clicked", lambda *_: self.reload_disks())
        header.pack_start(self.refresh_btn)
        view.add_top_bar(header)

        self.banner = Adw.Banner(title="Pacchetto Ventoy non trovato: scarica ventoy-x.y.z-linux.tar.gz, "
                                       "estrailo e indicami la cartella",
                                 button_label="Scegli…")
        self.banner.connect("button-clicked", lambda *_: self.choose_package())
        view.add_top_bar(self.banner)

        page = Adw.PreferencesPage()
        view.set_content(page)

        # --- dispositivo
        g_dev = Adw.PreferencesGroup(title="Dispositivo")
        page.add(g_dev)

        self.dev_model = Gtk.StringList()
        self.dev_row = Adw.ComboRow(title="Disco", model=self.dev_model)
        self.dev_row.connect("notify::selected", lambda *_: self.on_disk_changed())
        g_dev.add(self.dev_row)

        self.showall_row = Adw.SwitchRow(
            title="Mostra tutti i dischi",
            subtitle="Include anche i dischi interni: attenzione a cosa selezioni")
        self.showall_row.connect("notify::active", lambda *_: self.reload_disks())
        g_dev.add(self.showall_row)

        self.status_row = Adw.ActionRow(title="Ventoy sul dispositivo")
        self.info_btn = Gtk.Button(label="Dettagli", valign=Gtk.Align.CENTER,
                                   tooltip_text="Legge versione, stile e Secure Boot (chiede la password)")
        self.info_btn.connect("clicked", lambda *_: self.run_ventoy("list"))
        self.status_row.add_suffix(self.info_btn)
        g_dev.add(self.status_row)

        # --- opzioni
        g_opt = Adw.PreferencesGroup(title="Opzioni di installazione")
        page.add(g_opt)

        self.style_row = Adw.ComboRow(title="Stile partizione",
                                      model=Gtk.StringList.new(["GPT", "MBR"]))
        self.style_row.set_subtitle("GPT per PC UEFI recenti, MBR per la massima compatibilità")
        g_opt.add(self.style_row)

        self.sb_row = Adw.SwitchRow(title="Supporto Secure Boot", active=True)
        g_opt.add(self.sb_row)

        self.label_row = Adw.EntryRow(title="Etichetta della partizione dati")
        self.label_row.set_text("Ventoy")
        g_opt.add(self.label_row)

        self.reserve_row = Adw.SpinRow.new_with_range(0, 2_000_000, 1024)
        self.reserve_row.set_title("Spazio riservato a fine disco")
        self.reserve_row.set_subtitle("MiB lasciati liberi per altre partizioni")
        g_opt.add(self.reserve_row)

        self.nd_row = Adw.SwitchRow(
            title="Installazione non distruttiva",
            subtitle="Prova a mantenere i dati esistenti (solo NTFS/ext, sperimentale)")
        self.nd_row.connect("notify::active", lambda *_: self.update_sensitivity())
        g_opt.add(self.nd_row)

        # --- ventoy.json
        g_win = Adw.PreferencesGroup(
            title="Windows 11",
            description="Scritto in ventoy/ventoy.json sulla chiavetta; "
                        "le altre impostazioni del file vengono mantenute")
        page.add(g_win)

        self.bypass_row = Adw.SwitchRow(title="Salta il controllo dei requisiti",
                                        subtitle="TPM 2.0, Secure Boot, CPU, RAM")
        self.nro_row = Adw.SwitchRow(title="Permetti l'account locale",
                                     subtitle="Installazione senza account Microsoft né rete")
        g_win.add(self.bypass_row)
        g_win.add(self.nro_row)

        self.mount_row = Adw.ActionRow(title="Partizione Ventoy")
        self.mount_btn = Gtk.Button(label="Monta", valign=Gtk.Align.CENTER)
        self.mount_btn.connect("clicked", lambda *_: self.mount_part1())
        self.open_btn = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                                   tooltip_text="Apri la chiavetta per copiare le ISO")
        self.open_btn.connect("clicked", lambda *_: self.open_part1())
        self.save_json_btn = Gtk.Button(label="Scrivi", valign=Gtk.Align.CENTER,
                                        css_classes=["suggested-action"])
        self.save_json_btn.connect("clicked", lambda *_: self.write_json())
        for b in (self.mount_btn, self.open_btn, self.save_json_btn):
            self.mount_row.add_suffix(b)
        g_win.add(self.mount_row)

        # --- registro
        self.g_log = Adw.PreferencesGroup(title="Registro", visible=False)
        page.add(self.g_log)
        self.log_buf = Gtk.TextBuffer()
        log_view = Gtk.TextView(buffer=self.log_buf, editable=False, monospace=True,
                                wrap_mode=Gtk.WrapMode.WORD_CHAR, cursor_visible=False,
                                top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.log_view = log_view
        sw = Gtk.ScrolledWindow(child=log_view, min_content_height=200,
                                css_classes=["card"])
        self.log_sw = sw
        self.g_log.add(sw)

        # --- barra inferiore
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         margin_top=10, margin_bottom=12, margin_start=12, margin_end=12)
        self.progress = Gtk.ProgressBar(visible=False)
        bottom.append(self.progress)
        btns = Gtk.Box(spacing=8, homogeneous=True)
        self.update_btn = Gtk.Button(label="Aggiorna Ventoy", css_classes=["pill"])
        self.update_btn.connect("clicked", lambda *_: self.confirm("update"))
        self.install_btn = Gtk.Button(label="Installa", css_classes=["pill", "destructive-action"])
        self.install_btn.connect("clicked", lambda *_: self.confirm("install"))
        btns.append(self.update_btn)
        btns.append(self.install_btn)
        bottom.append(btns)
        view.add_bottom_bar(bottom)

        self.update_package_ui()
        self.reload_disks()
        GLib.timeout_add_seconds(3, self.poll_disks)

    # ---------------------------------------------------------------- pacchetto
    def update_package_ui(self):
        ok = is_ventoy_package(VENTOY_DIR)
        self.banner.set_revealed(not ok)
        self.title.set_subtitle(f"Pacchetto {package_version()}" if ok else "Nessun pacchetto")
        self.folder_btn.set_tooltip_text(
            f"Pacchetto in uso: {VENTOY_DIR}\nClic per cambiarlo" if ok
            else "Scegli la cartella del pacchetto Ventoy")

    def choose_package(self):
        dlg = Gtk.FileDialog(title="Cartella del pacchetto Ventoy (contiene Ventoy2Disk.sh)")
        if VENTOY_DIR:
            dlg.set_initial_folder(Gio.File.new_for_path(VENTOY_DIR))
        dlg.select_folder(self, None, self.on_package_chosen)

    def on_package_chosen(self, dlg, res):
        global VENTOY_DIR
        try:
            path = dlg.select_folder_finish(res).get_path()
        except GLib.Error:
            return  # annullato
        if not is_ventoy_package(path):
            self.alert("Non è un pacchetto Ventoy",
                       f"In {path} mancano Ventoy2Disk.sh, boot/boot.img o ventoy/version.\n\n"
                       "Serve la cartella estratta da ventoy-x.y.z-linux.tar.gz "
                       "(i sorgenti del repository non bastano).")
            return
        VENTOY_DIR = os.path.realpath(path)
        save_config(ventoy_dir=VENTOY_DIR)
        self.update_package_ui()
        self.update_sensitivity()
        self.toast(f"Uso Ventoy {package_version()}")

    # ------------------------------------------------------------------ dischi
    def current_disk(self):
        i = self.dev_row.get_selected()
        if 0 <= i < len(self.disks):
            return self.disks[i]
        return None

    def reload_disks(self, keep=None):
        keep = keep or (self.current_disk() or {}).get("path")
        self.disks = list_disks(self.showall_row.get_active())
        self.dev_model.splice(0, self.dev_model.get_n_items(),
                              [self.disk_label(d) for d in self.disks])
        idx = next((i for i, d in enumerate(self.disks) if d["path"] == keep), 0)
        if self.disks:
            self.dev_row.set_selected(idx)
        self.on_disk_changed()

    def poll_disks(self):
        # rileva chiavette inserite/rimosse senza dover premere aggiorna
        if self.proc is None:
            new = list_disks(self.showall_row.get_active())
            if new != self.disks:
                self.reload_disks()
        return True

    @staticmethod
    def disk_label(d):
        tag = " · Ventoy" if d["ventoy"] else ""
        return f"{d['model']} · {human_size(d['size'])} · {d['path'].removeprefix('/dev/')}{tag}"

    def on_disk_changed(self):
        d = self.current_disk()
        if d is None:
            self.status_row.set_subtitle("Nessuna chiavetta trovata: inseriscine una")
        elif d["ventoy"]:
            self.status_row.set_subtitle("Installato — premi Dettagli per versione e opzioni")
        else:
            self.status_row.set_subtitle("Non installato")

        if d and d["ventoy"]:
            if d["mountpoint"]:
                self.mount_row.set_subtitle(f"{d['part1']} montata in {d['mountpoint']}")
            else:
                self.mount_row.set_subtitle(f"{d['part1']} non montata")
            self.load_json_state(d)
        else:
            self.mount_row.set_subtitle("Disponibile dopo l'installazione di Ventoy")
        self.update_sensitivity()

    def update_sensitivity(self):
        d = self.current_disk()
        busy = self.proc is not None
        has = d is not None and is_ventoy_package(VENTOY_DIR)
        self.install_btn.set_sensitive(has and not busy)
        self.update_btn.set_sensitive(has and not busy and d["ventoy"])
        self.info_btn.set_sensitive(has and not busy and d["ventoy"])
        self.dev_row.set_sensitive(not busy)
        self.refresh_btn.set_sensitive(not busy)
        mounted = bool(has and d["ventoy"] and d["mountpoint"])
        self.mount_btn.set_visible(bool(has and d["ventoy"] and not d["mountpoint"]))
        self.mount_btn.set_sensitive(not busy)
        self.open_btn.set_visible(mounted)
        self.save_json_btn.set_sensitive(mounted and not busy)
        # nella modalità non distruttiva lo stile resta quello del disco
        nd = self.nd_row.get_active()
        self.style_row.set_sensitive(not nd)
        self.reserve_row.set_sensitive(not nd)
        self.label_row.set_sensitive(not nd)

    # ------------------------------------------------------------ ventoy.json
    def json_path(self, d):
        return os.path.join(d["mountpoint"], "ventoy", "ventoy.json")

    def load_json_state(self, d):
        bypass, nro = True, True  # default di Ventoy nelle versioni recenti
        if d.get("mountpoint"):
            try:
                with open(self.json_path(d)) as f:
                    for item in json.load(f).get("control", []):
                        if WIN11_KEYS[0] in item:
                            bypass = str(item[WIN11_KEYS[0]]) == "1"
                        if WIN11_KEYS[1] in item:
                            nro = str(item[WIN11_KEYS[1]]) == "1"
            except (OSError, ValueError, AttributeError):
                pass
        self.bypass_row.set_active(bypass)
        self.nro_row.set_active(nro)

    def write_json(self):
        d = self.current_disk()
        if not d or not d["mountpoint"]:
            return
        path = self.json_path(d)
        data = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError
            except (OSError, ValueError):
                self.alert("ventoy.json non valido",
                           f"{path} esiste ma non è JSON valido: correggilo o cancellalo prima.")
                return
        control = [c for c in data.get("control", [])
                   if not (isinstance(c, dict) and any(k in c for k in WIN11_KEYS))]
        control.append({WIN11_KEYS[0]: "1" if self.bypass_row.get_active() else "0"})
        control.append({WIN11_KEYS[1]: "1" if self.nro_row.get_active() else "0"})
        data["control"] = control
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=4)
                f.write("\n")
            os.replace(tmp, path)
            os.sync()
        except OSError as e:
            self.alert("Scrittura non riuscita", str(e))
            return
        self.toast("ventoy.json aggiornato")

    def mount_part1(self):
        d = self.current_disk()
        if not d or not d["part1"]:
            return
        r = subprocess.run(["udisksctl", "mount", "-b", d["part1"]],
                           capture_output=True, text=True)
        if r.returncode != 0:
            self.alert("Montaggio non riuscito", (r.stderr or r.stdout).strip())
        self.reload_disks(d["path"])

    def open_part1(self):
        d = self.current_disk()
        if d and d["mountpoint"]:
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(d["mountpoint"]), None)

    # ---------------------------------------------------------- operazioni
    def confirm(self, mode):
        d = self.current_disk()
        if not d:
            return
        what = f"{d['model']} ({human_size(d['size'])}, {d['path']})"
        if mode == "update":
            heading = "Aggiornare Ventoy?"
            body = (f"Ventoy su {what} verrà portato alla versione {package_version()}.\n"
                    "Le ISO e gli altri file sulla chiavetta restano intatti.")
            label, appearance = "Aggiorna", Adw.ResponseAppearance.SUGGESTED
        elif self.nd_row.get_active():
            heading = "Installazione non distruttiva"
            body = (f"Ventoy proverà a installarsi su {what} mantenendo i dati.\n"
                    "L'operazione può comunque fallire: fai un backup prima.")
            label, appearance = "Installa", Adw.ResponseAppearance.DESTRUCTIVE
        else:
            heading = "Cancellare tutto il disco?"
            body = f"Tutti i dati su {what} andranno persi."
            if d["ventoy"]:
                body += "\n\nSul disco c'è già Ventoy: se vuoi solo aggiornarlo usa «Aggiorna Ventoy»."
            if not d["removable"]:
                body += "\n\n⚠ Questo NON sembra un disco rimovibile."
            label, appearance = "Cancella e installa", Adw.ResponseAppearance.DESTRUCTIVE

        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("cancel", "Annulla")
        dlg.add_response("ok", label)
        dlg.set_response_appearance("ok", appearance)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda _d, r: r == "ok" and self.run_ventoy(mode))
        dlg.present(self)

    def build_args(self, mode, d):
        if mode == "list":
            return ["-l", d["path"]]
        if mode == "update":
            return ["-u", "-s" if self.sb_row.get_active() else "-S", d["path"]]
        args = ["-I" if d["ventoy"] else "-i"]
        if self.nd_row.get_active():
            args.append("-n")
        else:
            if self.style_row.get_selected() == 0:
                args.append("-g")
            label = self.label_row.get_text().strip() or "Ventoy"
            args += ["-L", label]
            reserve = int(self.reserve_row.get_value())
            if reserve > 0:
                args += ["-r", str(reserve)]
        args.append("-s" if self.sb_row.get_active() else "-S")
        args.append(d["path"])
        return args

    def run_ventoy(self, mode):
        d = self.current_disk()
        if not d or self.proc:
            return
        args = self.build_args(mode, d)
        argv = ["pkexec", "/bin/sh", "-c", 'cd "$1" && shift && exec /bin/sh ./Ventoy2Disk.sh "$@"',
                "ventoy-gtk4", VENTOY_DIR] + args
        self.mode = mode
        self.last_output = []
        self.log_buf.set_text("")
        self.append_log("$ Ventoy2Disk.sh " + " ".join(args) + "\n")
        self.g_log.set_visible(True)

        try:
            self.proc = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
                | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            self.proc = None
            self.alert("Impossibile avviare pkexec", e.message)
            return

        # risponde "y" alle conferme dello script (la conferma vera l'ha già data l'utente)
        stdin = self.proc.get_stdin_pipe()
        stdin.write_all(b"y\ny\ny\n", None)
        stdin.close(None)

        self.stream = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self.stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.on_line)
        self.proc.wait_check_async(None, self.on_done)

        self.progress.set_visible(mode != "list")
        self.pulse_id = GLib.timeout_add(120, self.pulse)
        self.update_sensitivity()

    def pulse(self):
        self.progress.pulse()
        return True

    def on_line(self, stream, res):
        try:
            line, _ = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            return
        line = ANSI_RE.sub("", line)
        self.last_output.append(line)
        self.append_log(line + "\n")
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.on_line)

    def append_log(self, text):
        self.log_buf.insert(self.log_buf.get_end_iter(), text)
        mark = self.log_buf.create_mark(None, self.log_buf.get_end_iter(), False)
        self.log_view.scroll_mark_onscreen(mark)

    def on_done(self, proc, res):
        try:
            proc.wait_check_finish(res)
        except GLib.Error:
            pass
        # lascia arrivare le ultime righe prima di valutare l'esito
        GLib.timeout_add(300, self.finish, proc.get_exit_status() if proc.get_if_exited() else -1)

    def finish(self, status):
        GLib.source_remove(self.pulse_id)
        self.progress.set_visible(False)
        self.proc = None
        out = "\n".join(self.last_output)
        path = (self.current_disk() or {}).get("path")

        if status in (126, 127) and "Ventoy" not in out:
            self.toast("Autenticazione annullata")
        elif self.mode == "list":
            info = [l.strip() for l in self.last_output if ":" in l and
                    l.strip().startswith(("Ventoy Version", "Disk Partition", "Secure Boot"))]
            sub = " · ".join(i.split(":", 1)[1].strip() for i in info) or "Informazioni non disponibili"
            self.status_row.set_subtitle(sub)
        elif status == 0 and "successfully finished" in out:
            self.toast("Fatto! Ventoy installato" if self.mode == "install"
                       else "Fatto! Ventoy aggiornato")
            GLib.timeout_add_seconds(2, lambda: self.reload_disks(path) or False)
        else:
            tail = "\n".join(l for l in self.last_output[-8:] if l.strip())
            self.alert("Operazione non riuscita", tail or f"Codice di uscita {status}")
        self.update_sensitivity()
        return False

    # -------------------------------------------------------------- avvisi
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def alert(self, heading, body):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("ok", "OK")
        dlg.present(self)
        return False


class VentoyApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.get_active_window() or VentoyWindow(self)
        win.present()


def install_desktop():
    apps = os.path.join(GLib.get_user_data_dir(), "applications")
    os.makedirs(apps, exist_ok=True)
    icon = next((p for p in (os.path.join(SCRIPT_DIR, "..", "..", "ICON", "logo_256.png"),
                             os.path.join(VENTOY_DIR or "", "WebUI", "static", "img", "VentoyLogo.png"))
                 if os.path.isfile(p)), "drive-removable-media")
    path = os.path.join(apps, "ventoy-gtk4.desktop")
    with open(path, "w") as f:
        f.write("[Desktop Entry]\n"
                "Name=Ventoy\n"
                "GenericName=Chiavette USB multiboot\n"
                "Comment=Installa e aggiorna Ventoy sulle chiavette USB\n"
                f"Exec=python3 \"{os.path.realpath(__file__)}\"\n"
                f"Icon={os.path.realpath(icon) if os.path.isfile(icon) else icon}\n"
                "Terminal=false\nType=Application\n"
                "Categories=System;Utility;\nKeywords=usb;iso;boot;multiboot;\n"
                f"StartupWMClass={APP_ID}\n")
    print(f"Lanciatore creato: {path}")
    # nasconde eventuali vecchi lanciatori che aprono la GUI GTK3 originale
    for old in glob.glob(os.path.join(apps, "*.desktop")):
        if old == path:
            continue
        try:
            with open(old) as f:
                text = f.read()
        except OSError:
            continue
        if "VentoyGUI" in text or "Ventoy2Disk.gtk" in text:
            os.replace(old, old + ".bak")
            print(f"Vecchio lanciatore disattivato: {old} → {os.path.basename(old)}.bak")
    os.system(f"update-desktop-database -q {apps} 2>/dev/null")
    if VENTOY_DIR:
        save_config(ventoy_dir=VENTOY_DIR)
        print(f"Pacchetto Ventoy: {VENTOY_DIR}")
    else:
        print("Pacchetto Ventoy non trovato: lo potrai scegliere dalla finestra.")


if __name__ == "__main__":
    explicit = None
    if "--ventoy-dir" in sys.argv:
        k = sys.argv.index("--ventoy-dir")
        explicit = sys.argv[k + 1] if k + 1 < len(sys.argv) else None
    VENTOY_DIR = find_ventoy_dir(explicit)
    if explicit and VENTOY_DIR:
        save_config(ventoy_dir=VENTOY_DIR)
    if "--install-desktop" in sys.argv:
        install_desktop()
        sys.exit(0)
    sys.exit(VentoyApp().run([sys.argv[0]]))
