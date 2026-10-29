"""
app.py — NóminaApp: Gestión de Nómina Empresarial
Todos los CRUDs completos, manejo de errores robusto,
cambio de contraseña en todos los roles,
colillas de pago independientes de la nómina.
"""
import tkinter as tk
from tkinter import ttk, messagebox
import os, re, subprocess, sys, threading, logging, time
from logging.handlers import RotatingFileHandler
from datetime import datetime

import database as db
import pdf_generator as pdf
from paths import get_reports_dir, LOG_DIR, APP_VERSION

# ─── INFRAESTRUCTURA (logging, instancia única, errores globales) ─────────────
log = logging.getLogger("nomina_app")


def _setup_logging():
    """Log rotativo en logs/nomina_app.log (1 MB x 5 archivos)."""
    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "nomina_app.log"),
        maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("=== NominaApp %s iniciada ===", APP_VERSION)


_MUTEX_HANDLE = None  # Referencia global para que el mutex viva todo el proceso


def _check_single_instance():
    """Impedir dos instancias de la app sobre la misma SQLite.

    En Windows usa un mutex con nombre; en otros SO, un lockfile."""
    global _MUTEX_HANDLE
    if sys.platform == "win32":
        import ctypes
        _MUTEX_HANDLE = ctypes.windll.kernel32.CreateMutexW(
            None, False, "Local\\NominaApp_SingleInstance")
        ERROR_ALREADY_EXISTS = 183
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            root = tk.Tk(); root.withdraw()
            messagebox.showwarning("NominaApp ya está abierta",
                "Ya hay una instancia de NominaApp en ejecución.\n"
                "Ciérrala antes de abrir otra (dos instancias pueden\n"
                "corromper la base de datos).")
            root.destroy()
            raise SystemExit(0)
    else:
        lockfile = os.path.join(os.path.dirname(db.DB_PATH), ".nominaapp.lock")
        try:
            _MUTEX_HANDLE = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            root = tk.Tk(); root.withdraw()
            messagebox.showwarning("NominaApp ya está abierta",
                "Ya hay una instancia de NominaApp en ejecución.")
            root.destroy()
            raise SystemExit(0)


def _install_error_hooks():
    """Captura errores no manejados: log + messagebox amigable."""
    def _hook(exc_type, exc, tb):
        log.error("Excepción no controlada", exc_info=(exc_type, exc, tb))
        try:
            messagebox.showerror("Error inesperado",
                "Ocurrió un error inesperado y la aplicación se cerrará.\n"
                f"Detalle: {exc}\n\nQuedó registrado en logs/nomina_app.log")
        except Exception:
            pass
    sys.excepthook = _hook

    def _thread_hook(args):
        log.error("Excepción en hilo %s", args.thread,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = _thread_hook


def _tk_error_callback(self, exc, val, tb):
    """Reemplaza tk.Tk.report_callback_exception (a nivel de clase).
    Errores dentro de callbacks de Tkinter: la app NO se cierra,
    solo se registra y se avisa al usuario."""
    log.error("Error en callback de UI", exc_info=(exc, val, tb))
    try:
        messagebox.showerror("Error inesperado",
            f"Ocurrió un error al procesar la acción:\n{val}\n\n"
            "Quedó registrado en logs/nomina_app.log")
    except Exception:
        pass


# ─── PALETA ───────────────────────────────────────────────────────────────────
C = {
    "bg":      "#0F172A", "card":    "#1E293B", "input":   "#334155",
    "hover":   "#2D3B55", "accent":  "#4F46E5", "accent2": "#6366F1",
    "alight":  "#818CF8", "ok":      "#10B981", "warn":    "#F59E0B",
    "err":     "#EF4444", "txt":     "#F1F5F9", "sub":     "#94A3B8",
    "muted":   "#64748B", "border":  "#334155", "white":   "#FFFFFF",
    "sidebar": "#1E293B", "topbar":  "#0F172A",
}
F = {
    "h1": ("Segoe UI", 24, "bold"), "h2": ("Segoe UI", 18, "bold"),
    "h3": ("Segoe UI", 14, "bold"), "h4": ("Segoe UI", 12, "bold"),
    "bd": ("Segoe UI", 10),         "sm": ("Segoe UI", 9),
    "lg": ("Segoe UI", 20, "bold"), "bt": ("Segoe UI", 10, "bold"),
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def fmt(v):
    try:    return f"$ {float(v):,.0f}".replace(",", ".")
    except: return "$ 0"

def abrir(path, parent=None):
    try:    os.startfile(path)
    except AttributeError:
        try: subprocess.Popen(["xdg-open", path])
        except Exception:
            if parent: messagebox.showwarning("Aviso",
                f"No se pudo abrir automáticamente.\nRuta: {path}", parent=parent)

def val_pass(nueva, conf, minimo=8):
    if not nueva:           return False, "La contraseña no puede estar vacía."
    if len(nueva) < minimo: return False, f"Mínimo {minimo} caracteres."
    if not any(c.isalpha() for c in nueva):
        return False, "Debe incluir al menos una letra."
    if not any(c.isdigit() for c in nueva):
        return False, "Debe incluir al menos un número."
    if nueva != conf:       return False, "Las contraseñas no coinciden."
    return True, ""

def setup_style():
    s = ttk.Style(); s.theme_use("clam")
    s.configure("Treeview", background=C["card"], foreground=C["txt"],
        rowheight=32, fieldbackground=C["card"], borderwidth=0, font=F["bd"])
    s.configure("Treeview.Heading", background=C["bg"], foreground=C["alight"],
        font=F["h4"], relief="flat", borderwidth=0)
    s.map("Treeview", background=[("selected", C["accent"])],
          foreground=[("selected", C["white"])])
    s.map("Treeview.Heading", background=[("active", C["hover"])])
    s.configure("TScrollbar", background=C["input"], troughcolor=C["card"],
        bordercolor=C["card"], arrowcolor=C["sub"])

# ─── WIDGETS BASE ─────────────────────────────────────────────────────────────
class Btn(tk.Button):
    _M = {
        "pri": (C["accent"],  C["accent2"], C["white"]),
        "ok":  (C["ok"],     "#059669",    C["white"]),
        "err": (C["err"],    "#DC2626",    C["white"]),
        "gh":  (C["card"],   C["hover"],   C["sub"]),
        "wa":  (C["warn"],   "#D97706",    C["bg"]),
        "out": (C["bg"],     C["card"],    C["alight"]),
    }
    def __init__(self, p, text="", cmd=None, sty="pri", icon="", w=None, **kw):
        bg, hv, fg = self._M.get(sty, self._M["pri"])
        lbl = f"{icon}  {text}" if icon else text
        super().__init__(p, text=lbl, command=cmd, bg=bg, fg=fg,
            activebackground=hv, activeforeground=fg, font=F["bt"],
            relief="flat", cursor="hand2", padx=16, pady=8, borderwidth=0, **kw)
        if w: self.config(width=w)
        self._bg, self._hv = bg, hv
        self.bind("<Enter>", lambda e: self.config(bg=self._hv))
        self.bind("<Leave>", lambda e: self.config(bg=self._bg))

class Inp(tk.Entry):
    def __init__(self, p, ph="", show="", **kw):
        super().__init__(p, bg=C["input"], fg=C["txt"], insertbackground=C["alight"],
            relief="flat", font=F["bd"], show=show,
            highlightthickness=1, highlightbackground=C["border"],
            highlightcolor=C["accent"], **kw)
        self._ph, self._on = ph, False
        if ph and not show:
            self._set(); self.bind("<FocusIn>", self._clr); self.bind("<FocusOut>", self._rst)
    def _set(self, *_):
        if not super().get(): self.insert(0, self._ph); self.config(fg=C["muted"]); self._on=True
    def _rst(self, *_):
        if not super().get(): self._set()
    def _clr(self, *_):
        if self._on: self.delete(0, tk.END); self.config(fg=C["txt"]); self._on=False
    def val(self): return "" if self._on else super().get().strip()

class Cbo(ttk.Combobox):
    def __init__(self, p, **kw): super().__init__(p, font=F["bd"], **kw)

class ImgSelector(tk.Frame):
    """Widget para seleccionar y mostrar imágenes."""
    def __init__(self, p, img_type="logo", callback=None, **kw):
        super().__init__(p, bg=C["card"], highlightthickness=1, 
                     highlightbackground=C["border"], **kw)
        self.img_type = img_type
        self.callback = callback
        self.img_path = None
        
        # Botón para seleccionar imagen
        self.btn = Btn(self, text=f"Seleccionar {img_type}", cmd=self._select_img, 
                      sty="gh", icon="📷")
        self.btn.pack(side="left", padx=(8,4))
        
        # Label para mostrar nombre del archivo
        self.lbl = tk.Label(self, text=f"No hay {img_type} seleccionado", 
                          font=F["sm"], bg=C["card"], fg=C["muted"], width=25, anchor="w")
        self.lbl.pack(side="left", padx=4)
        
        # Botón para eliminar imagen
        self.btn_del = Btn(self, text="✕", cmd=self._clear_img, sty="err", w=3)
        self.btn_del.pack(side="right", padx=(4,8))
        self.btn_del.pack_forget()  # Oculto inicialmente
    
    def _select_img(self):
        from tkinter import filedialog
        
        file_types = [
            ("Archivos de imagen", "*.png *.jpg *.jpeg *.gif *.bmp"),
            ("Todos los archivos", "*.*")
        ]
        
        file_path = filedialog.askopenfilename(
            title=f"Seleccionar {self.img_type}",
            filetypes=file_types
        )
        
        if file_path:
            self.img_path = file_path
            filename = os.path.basename(file_path)
            self.lbl.config(text=filename[:30] + "..." if len(filename) > 30 else filename, fg=C["txt"])
            self.btn_del.pack(side="right", padx=(4,8))
            
            if self.callback:
                self.callback(file_path)
    
    def _clear_img(self):
        self.img_path = None
        self.lbl.config(text=f"No hay {self.img_type} seleccionado", fg=C["muted"])
        self.btn_del.pack_forget()
        
        if self.callback:
            self.callback(None)
    
    def get_path(self):
        return self.img_path
    
    def set_path(self, path):
        self.img_path = path
        if path:
            filename = os.path.basename(path)
            self.lbl.config(text=filename[:30] + "..." if len(filename) > 30 else filename, fg=C["txt"])
            self.btn_del.pack(side="right", padx=(4,8))
        else:
            self._clear_img()

class Card(tk.Frame):
    def __init__(self, p, **kw):
        super().__init__(p, bg=C["card"], highlightthickness=1,
            highlightbackground=C["border"], **kw)

class StatCard(tk.Frame):
    def __init__(self, p, title, value, icon, color, **kw):
        super().__init__(p, bg=C["card"], highlightthickness=1,
            highlightbackground=C["border"], padx=20, pady=16, **kw)
        tk.Label(self, text=icon, font=("Segoe UI Emoji", 24),
            bg=C["card"], fg=color).pack(anchor="w")
        tk.Label(self, text=value, font=F["h2"],
            bg=C["card"], fg=color).pack(anchor="w", pady=(4, 0))
        tk.Label(self, text=title, font=F["sm"],
            bg=C["card"], fg=C["sub"]).pack(anchor="w")

# ─── MODAL CONTRASEÑA (reutilizable) ──────────────────────────────────────────
def modal_password(parent, uid, nombre, pide_actual=True, titulo="Cambiar contraseña"):
    win = tk.Toplevel(parent)
    win.title(titulo); win.configure(bg=C["bg"])
    win.geometry("440x" + ("390" if pide_actual else "310"))
    win.grab_set(); win.resizable(False, False); win.transient(parent)

    tk.Label(win, text=f"🔐  {titulo}", font=F["h3"],
        bg=C["bg"], fg=C["txt"]).pack(pady=(20,2), padx=24, anchor="w")
    sub = "Verifica tu contraseña actual antes de cambiarla." if pide_actual \
          else f"Restablecer contraseña de: {nombre}"
    tk.Label(win, text=sub, font=F["sm"],
        bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(0,14))

    e_act = None
    if pide_actual:
        tk.Label(win, text="Contraseña actual *", font=F["sm"],
            bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w")
        e_act = Inp(win, show="●", width=44)
        e_act.pack(padx=24, fill="x", ipady=7, pady=(2,12))

    tk.Label(win, text="Nueva contraseña *", font=F["sm"],
        bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w")
    e_new = Inp(win, show="●", width=44)
    e_new.pack(padx=24, fill="x", ipady=7, pady=(2,12))

    tk.Label(win, text="Confirmar contraseña *", font=F["sm"],
        bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w")
    e_cf = Inp(win, show="●", width=44)
    e_cf.pack(padx=24, fill="x", ipady=7, pady=(2,8))

    lbl = tk.Label(win, text="", font=F["sm"], bg=C["bg"],
        fg=C["err"], wraplength=390, justify="left")
    lbl.pack(padx=24, anchor="w")

    def save():
        nueva = e_new.get().strip(); conf = e_cf.get().strip()
        ok_v, msg_v = val_pass(nueva, conf)
        if not ok_v: lbl.config(text=f"⚠  {msg_v}"); return
        if pide_actual:
            act = e_act.get().strip()
            if not act: lbl.config(text="⚠  Ingresa tu contraseña actual."); return
            try:    ok, msg = db.cambiar_password_propio(uid, act, nueva)
            except Exception as ex: lbl.config(text=f"Error: {ex}"); return
        else:
            try:    ok, msg = db.cambiar_password(uid, nueva)
            except Exception as ex: lbl.config(text=f"Error: {ex}"); return
        if ok:  messagebox.showinfo("✓ Éxito", msg, parent=win); win.destroy()
        else:   lbl.config(text=f"✗  {msg}")

    br = tk.Frame(win, bg=C["bg"]); br.pack(pady=10, padx=24, anchor="e")
    Btn(br, "Cancelar",          cmd=win.destroy, sty="gh").pack(side="left", padx=(0,8))
    Btn(br, "Guardar contraseña",cmd=save, sty="pri", icon="💾").pack(side="left")
    (e_act if pide_actual else e_new).focus_set()

# ─── VENTANA LOGIN ────────────────────────────────────────────────────────────
class LoginWin(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NóminaApp — Iniciar Sesión")
        self.geometry("900x580"); self.resizable(False, False)
        self.configure(bg=C["bg"])
        self.update_idletasks()
        x=(self.winfo_screenwidth()-900)//2; y=(self.winfo_screenheight()-580)//2
        self.geometry(f"900x580+{x}+{y}")
        self._ui()

    def _ui(self):
        # Panel izquierdo
        lf = tk.Frame(self, bg=C["accent"], width=420)
        lf.pack(side="left", fill="y"); lf.pack_propagate(False)
        cv = tk.Canvas(lf, bg=C["accent"], highlightthickness=0)
        cv.pack(fill="both", expand=True)
        cv.create_oval(-60,-60,200,200, fill="#6366F1", outline="")
        cv.create_oval(200,350,520,650, fill="#3730A3", outline="")
        cv.create_oval(280,-40,480,160, fill="#4338CA", outline="")
        cv.create_text(210,195, text="💼", font=("Segoe UI Emoji",52), fill=C["white"])
        cv.create_text(210,268, text="NóminaApp", font=("Segoe UI",28,"bold"), fill=C["white"])
        cv.create_text(210,306, text="Gestión de Nómina Empresarial",
            font=("Segoe UI",12), fill="#C7D2FE")
        for i,t in enumerate(["✓  Cálculo automático de deducciones",
                               "✓  Colillas de pago en PDF",
                               "✓  Reportes generales por período",
                               "✓  Base de datos local segura"]):
            cv.create_text(210, 368+i*30, text=t, font=("Segoe UI",11), fill="#EEF2FF")

        # Panel derecho
        rf = tk.Frame(self, bg=C["bg"]); rf.pack(side="right", fill="both", expand=True)
        box = tk.Frame(rf, bg=C["bg"]); box.place(relx=.5, rely=.5, anchor="center")

        tk.Label(box, text="Bienvenido",         font=F["h1"], bg=C["bg"], fg=C["txt"]).pack(anchor="w")
        tk.Label(box, text="Ingresa tus credenciales para continuar",
            font=F["bd"], bg=C["bg"], fg=C["sub"]).pack(anchor="w", pady=(4,28))

        tk.Label(box, text="Usuario",    font=F["h4"], bg=C["bg"], fg=C["sub"]).pack(anchor="w")
        self.eu = Inp(box, ph="Nombre de usuario", width=32)
        self.eu.pack(ipady=8, pady=(4,16), fill="x")

        tk.Label(box, text="Contraseña", font=F["h4"], bg=C["bg"], fg=C["sub"]).pack(anchor="w")
        self.ep = Inp(box, show="●", width=32)
        self.ep.pack(ipady=8, pady=(4,24), fill="x")

        Btn(box, "Iniciar Sesión", cmd=self._login, sty="pri", w=32, icon="🔐").pack(fill="x", ipady=4)
        self.le = tk.Label(box, text="", font=F["sm"], bg=C["bg"], fg=C["err"], wraplength=300)
        self.le.pack(pady=(12,0))
        self.eu.bind("<Return>", lambda _: self.ep.focus_set())
        self.ep.bind("<Return>", lambda _: self._login())
        self.eu.focus_set()

    def _login(self):
        u = self.eu.val(); p = self.ep.get().strip()
        if not u: self.le.config(text="⚠  Ingresa tu usuario."); return
        if not p: self.le.config(text="⚠  Ingresa tu contraseña."); return
        try:
            bloqueo = db.usuario_bloqueado(u)
            if bloqueo:
                self.le.config(
                    text=f"🔒  Cuenta bloqueada por intentos fallidos.\n"
                         f"Intenta de nuevo en {bloqueo} min.")
                return
            user = db.autenticar(u, p)
        except Exception as ex: self.le.config(text=f"Error BD: {ex}"); return
        if not user:
            self.le.config(text="✗  Credenciales incorrectas.")
            self.ep.delete(0, tk.END); return
        if user.get("debe_cambiar_password"):
            if not self._forzar_cambio_password(user, p):
                return
        db.establecer_sesion(user)
        self.destroy()
        (SuperAdminWin if user["rol"] == "superadmin" else AdminWin)(user).mainloop()

    def _forzar_cambio_password(self, user, actual):
        """Modal obligatorio cuando el flag debe_cambiar_password está activo.
        Retorna True si cambió la contraseña (puede entrar), False si canceló."""
        win = tk.Toplevel(self); win.title("Cambio de contraseña obligatorio")
        win.geometry("440x420"); win.resizable(False, False)
        win.configure(bg=C["bg"]); win.transient(self); win.grab_set()
        win.protocol("WM_DELETE_WINDOW", win.destroy)

        tk.Label(win, text="🔐  Debes cambiar tu contraseña",
            font=F["h3"], bg=C["bg"], fg=C["txt"]).pack(pady=(20,4), padx=24, anchor="w")
        tk.Label(win,
            text="Estás usando una contraseña temporal o de fábrica.\n"
                 "Por seguridad, define una nueva antes de continuar.",
            font=F["bd"], bg=C["bg"], fg=C["sub"], justify="left"
            ).pack(padx=24, anchor="w", pady=(0,8))
        tk.Label(win, text="Requisitos: mínimo 8 caracteres, una letra y un número.",
            font=F["sm"], bg=C["bg"], fg=C["muted"], justify="left"
            ).pack(padx=24, anchor="w", pady=(0,12))

        tk.Label(win, text="Nueva contraseña *", font=F["sm"],
            bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w")
        e_new = Inp(win, show="●", width=44); e_new.pack(padx=24, fill="x", ipady=7, pady=(2,12))
        tk.Label(win, text="Confirmar contraseña *", font=F["sm"],
            bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w")
        e_cf = Inp(win, show="●", width=44); e_cf.pack(padx=24, fill="x", ipady=7, pady=(2,8))

        lbl = tk.Label(win, text="", font=F["sm"], bg=C["bg"],
            fg=C["err"], wraplength=390, justify="left")
        lbl.pack(padx=24, anchor="w")
        res = {"ok": False}

        def save():
            nueva = e_new.get().strip(); conf = e_cf.get().strip()
            ok_v, msg_v = val_pass(nueva, conf)
            if not ok_v: lbl.config(text=f"⚠  {msg_v}"); return
            if nueva == actual:
                lbl.config(text="⚠  La nueva contraseña no puede ser igual a la actual.")
                return
            try:    ok, msg = db.cambiar_password_propio(user["id"], actual, nueva)
            except Exception as ex: lbl.config(text=f"Error: {ex}"); return
            if ok:
                res["ok"] = True
                messagebox.showinfo("✓ Contraseña actualizada",
                    "Tu contraseña fue actualizada. Ya puedes continuar.", parent=win)
                win.destroy()
            else:
                lbl.config(text=f"✗  {msg}")

        br = tk.Frame(win, bg=C["bg"]); br.pack(pady=12, padx=24, anchor="e")
        Btn(br, "Cancelar",          cmd=win.destroy, sty="gh").pack(side="left", padx=(0,8))
        Btn(br, "Guardar y entrar",  cmd=save, sty="pri", icon="💾").pack(side="left")
        e_new.focus_set()
        self.wait_window(win)
        return res["ok"]

# ─── BASE WINDOW ──────────────────────────────────────────────────────────────
class BaseWin(tk.Tk):
    def _init_layout(self, title, badge, badge_color, nav,
                     geo="1200x700", minsize=(1000,600)):
        self.title(title); self.geometry(geo); self.minsize(*minsize)
        self.configure(bg=C["bg"]); setup_style()
        self.update_idletasks()
        w,h=[int(x) for x in geo.split("x")]
        x=(self.winfo_screenwidth()-w)//2; y=(self.winfo_screenheight()-h)//2
        self.geometry(f"{geo}+{x}+{y}")

        # TopBar
        tb = tk.Frame(self, bg=C["topbar"], height=56)
        tb.pack(fill="x"); tb.pack_propagate(False)
        tk.Label(tb, text="💼 NóminaApp", font=F["lg"],
            bg=C["topbar"], fg=C["alight"]).pack(side="left", padx=20, pady=12)
        self._topbar_right(tb, badge, badge_color)

        # Main
        main = tk.Frame(self, bg=C["bg"]); main.pack(fill="both", expand=True)

        # Sidebar
        sb = tk.Frame(main, bg=C["sidebar"], width=220)
        sb.pack(side="left", fill="y"); sb.pack_propagate(False)
        tk.Label(sb, text="MENÚ", font=("Segoe UI",8,"bold"),
            bg=C["sidebar"], fg=C["muted"]).pack(pady=(24,8), padx=20, anchor="w")

        self._nb = {}; self._ak = None
        for key, lbl, cmd in nav:
            b = tk.Button(sb, text=lbl,
                command=lambda c=cmd, k=key: self._nav(k,c),
                bg=C["sidebar"], fg=C["sub"],
                activebackground=C["accent"], activeforeground=C["white"],
                font=F["bd"], relief="flat", cursor="hand2", anchor="w", padx=20, pady=10)
            b.pack(fill="x")
            b.bind("<Enter>",  lambda e,b=b,k=key: b.config(bg=C["hover"]) if self._ak!=k else None)
            b.bind("<Leave>",  lambda e,b=b,k=key: b.config(bg=C["accent"] if self._ak==k else C["sidebar"]))
            self._nb[key] = b

        self.ct = tk.Frame(main, bg=C["bg"])
        self.ct.pack(side="right", fill="both", expand=True)

        # Cierre de sesión por inactividad
        self._ultima_actividad = time.monotonic()
        self.bind_all("<Key>",    self._registrar_actividad, add="+")
        self.bind_all("<Button>", self._registrar_actividad, add="+")
        self._vigilar_inactividad()

    SESION_MINUTOS = 15  # auto-logout por inactividad

    def _registrar_actividad(self, *_):
        self._ultima_actividad = time.monotonic()

    def _vigilar_inactividad(self):
        try:
            if not self.winfo_exists(): return
            if time.monotonic() - self._ultima_actividad > self.SESION_MINUTOS*60:
                messagebox.showinfo("Sesión expirada",
                    "La sesión se cerró por inactividad.\n"
                    "Ingresa nuevamente.", parent=self)
                db.cerrar_sesion()
                self.destroy(); LoginWin().mainloop(); return
            self.after(10_000, self._vigilar_inactividad)
        except tk.TclError:
            pass  # ventana ya destruida

    def _reautenticar(self, motivo):
        """Pide la contraseña del usuario en sesión antes de una acción
        destructiva. Retorna True si se verificó."""
        win = tk.Toplevel(self); win.title("Confirmar identidad")
        win.geometry("420x300"); win.resizable(False, False)
        win.configure(bg=C["bg"]); win.transient(self); win.grab_set()
        win.protocol("WM_DELETE_WINDOW", win.destroy)

        tk.Label(win, text="🔐  Confirmar identidad", font=F["h3"],
            bg=C["bg"], fg=C["txt"]).pack(pady=(20,4), padx=24, anchor="w")
        tk.Label(win, text=motivo, font=F["bd"], bg=C["bg"], fg=C["sub"],
            wraplength=370, justify="left").pack(padx=24, anchor="w", pady=(0,12))

        tk.Label(win, text=f"Contraseña de {self.user['nombre']} *", font=F["sm"],
            bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w")
        e_pw = Inp(win, show="●", width=44); e_pw.pack(padx=24, fill="x", ipady=7, pady=(2,8))

        lbl = tk.Label(win, text="", font=F["sm"], bg=C["bg"],
            fg=C["err"], wraplength=370, justify="left")
        lbl.pack(padx=24, anchor="w")
        res = {"ok": False}

        def verificar(*_):
            pw = e_pw.get().strip()
            if not pw: lbl.config(text="⚠  Ingresa tu contraseña."); return
            try:
                if db.verificar_password_usuario(self.user["id"], pw):
                    res["ok"] = True; win.destroy()
                else:
                    lbl.config(text="✗  Contraseña incorrecta.")
                    e_pw.delete(0, tk.END)
            except Exception as ex: lbl.config(text=f"Error: {ex}")

        e_pw.bind("<Return>", verificar)
        br = tk.Frame(win, bg=C["bg"]); br.pack(pady=12, padx=24, anchor="e")
        Btn(br, "Cancelar",   cmd=win.destroy, sty="gh").pack(side="left", padx=(0,8))
        Btn(br, "Confirmar",  cmd=verificar, sty="pri", icon="🔐").pack(side="left")
        e_pw.focus_set()
        self.wait_window(win)
        return res["ok"]

    def _topbar_right(self, tb, badge, badge_color): pass

    def _nav(self, key, cmd):
        self._ak = key
        for k,b in self._nb.items():
            b.config(bg=C["accent"] if k==key else C["sidebar"],
                     fg=C["white"]   if k==key else C["sub"])
        cmd()

    def _sec(self, key): self._nav(key, getattr(self, f"_s_{key}"))
    def _clr(self):
        for w in self.ct.winfo_children(): w.destroy()
    def _logout(self):
        db.cerrar_sesion(); self.destroy(); LoginWin().mainloop()

    # ── UI helpers ──────────────────────────────────────────────────────────
    def _hdr(self, title, btn=None, cmd=None, icon="＋"):
        f = tk.Frame(self.ct, bg=C["bg"]); f.pack(fill="x", padx=24, pady=(20,0))
        tk.Label(f, text=title, font=F["h2"], bg=C["bg"], fg=C["txt"]).pack(side="left")
        if btn: Btn(f, btn, cmd=cmd, sty="pri", icon=icon).pack(side="right")

    def _sub(self, t):
        tk.Label(self.ct, text=t, font=F["bd"], bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(4,14))

    def _tree(self, cols, widths, parent=None):
        fr = tk.Frame(parent or self.ct, bg=C["bg"]); fr.pack(fill="both", expand=True, padx=24, pady=(0,6))
        tv = ttk.Treeview(fr, columns=cols, show="headings", selectmode="browse")
        for c,(t,w) in zip(cols,widths): tv.heading(c,text=t); tv.column(c,width=w,minwidth=36)
        vs = ttk.Scrollbar(fr, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.pack(side="left", fill="both", expand=True); vs.pack(side="right", fill="y")
        tv.tag_configure("odd",  background=C["card"])
        tv.tag_configure("even", background="#253045")
        tv.tag_configure("off",  foreground=C["muted"])
        return tv

    def _tag(self, i, on=True):
        base = "odd" if i%2 else "even"
        return (base,) if on else (base,"off")

    def _sel(self, tv, what="elemento"):
        s = tv.selection()
        if not s: messagebox.showwarning("Sin selección", f"Selecciona un {what}.", parent=self); return None
        return int(s[0])

    # ── Modal helpers ────────────────────────────────────────────────────────
    def _win(self, title, geo="500x480"):
        w = tk.Toplevel(self); w.title(title); w.geometry(geo)
        w.configure(bg=C["bg"]); w.grab_set(); w.resizable(False,False); w.transient(self)
        return w

    def _field(self, win, label, val="", show="", w=46):
        tk.Label(win, text=label, font=F["sm"], bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w")
        e = Inp(win, show=show, width=w)
        e.pack(padx=24, fill="x", ipady=7, pady=(2,10))
        if val: e.insert(0, str(val))
        return e

    def _err_lbl(self, win):
        l = tk.Label(win, text="", font=F["sm"], fg=C["err"],
            bg=C["bg"], wraplength=450, justify="left")
        l.pack(padx=24, anchor="w"); return l

    def _btns(self, win, save_cmd, save_txt="Guardar", icon="💾"):
        f = tk.Frame(win, bg=C["bg"]); f.pack(pady=20, padx=24, anchor="e", side="bottom")
        Btn(f, "Cancelar", cmd=win.destroy, sty="gh").pack(side="left", padx=(0,8))
        Btn(f, save_txt,   cmd=save_cmd, sty="pri", icon=icon).pack(side="left")


# ─── SUPERADMIN ───────────────────────────────────────────────────────────────
class SuperAdminWin(BaseWin):
    def __init__(self, user):
        super().__init__(); self.user = user
        self._init_layout("NóminaApp — Superadministrador", "SUPERADMIN", C["accent"],
            [("empresas","🏢  Empresas",self._s_empresas),
             ("admins",  "👥  Administradores",self._s_admins),
             ("bitacora","📜  Bitácora Global",self._s_bitacora),
             ("parametros","⚖️  Parámetros Legales",self._s_parametros),
             ("backup",  "💾  Backup General",self._s_backup)],
            geo="1200x700", minsize=(1000,600))
        self._sec("empresas")

    def _topbar_right(self, tb, badge, bc):
        rt = tk.Frame(tb, bg=C["topbar"]); rt.pack(side="right", padx=20)
        bf = tk.Frame(rt, bg=bc, padx=8, pady=3); bf.pack(side="left", padx=(0,12))
        tk.Label(bf, text=badge, font=("Segoe UI",8,"bold"), bg=bc, fg=C["white"]).pack()
        tk.Label(rt, text=f"👤  {self.user['nombre']}", font=F["bd"],
            bg=C["topbar"], fg=C["sub"]).pack(side="left")
        Btn(rt, "Mi contraseña", icon="🔐", sty="gh",
            cmd=lambda: modal_password(self, self.user["id"], self.user["nombre"],
                pide_actual=True, titulo="Cambiar mi contraseña")
            ).pack(side="left", padx=(12,0))
        Btn(rt, "Cerrar sesión", icon="🚪", sty="gh",
            cmd=self._logout).pack(side="left", padx=(8,0))

    # ═══ EMPRESAS ════════════════════════════════════════════════════════════
    def _s_empresas(self):
        self._clr()
        self._hdr("🏢  Gestión de Empresas", "Nueva Empresa", self._form_empresa)
        self._sub("Empresas registradas en el sistema. Doble clic para editar.")
        self.tv_emp = self._tree(
            ("id","nombre","nit","dir","tel","fecha"),
            [("ID",45),("Nombre",205),("NIT",130),("Dirección",200),("Teléfono",115),("Creado",110)])
        self.tv_emp.bind("<Double-1>", lambda _: self._edit_empresa())
        fr = tk.Frame(self.ct, bg=C["bg"]); fr.pack(padx=24, anchor="w", pady=(4,18))
        Btn(fr,"Editar",   cmd=self._edit_empresa,  sty="out",icon="✏").pack(side="left",padx=(0,8))
        Btn(fr,"Eliminar", cmd=self._del_empresa,   sty="err",icon="🗑").pack(side="left")
        self._load_emp()

    def _load_emp(self):
        self.tv_emp.delete(*self.tv_emp.get_children())
        try:
            for i,e in enumerate(db.listar_empresas()):
                self.tv_emp.insert("","end",iid=str(e["id"]),
                    values=(e["id"],e["nombre"],e["nit"],
                        e.get("direccion","") or "",e.get("telefono","") or "",
                        (e.get("creado_en","") or "")[:10]),
                    tags=self._tag(i))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _form_empresa(self, emp=None):
        nuevo = emp is None
        win = self._win("Nueva Empresa" if nuevo else "Editar Empresa","500x600")
        win.resizable(False, True)

        # Scrollable container
        outer = tk.Frame(win, bg=C["bg"]); outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=C["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        p = tk.Frame(canvas, bg=C["bg"])
        cwin = canvas.create_window((0,0), window=p, anchor="nw")
        p.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cwin, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)),"units"))
        win.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

        tk.Label(p, text=f"🏢  {'Nueva' if nuevo else 'Editar'} Empresa",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")
        tk.Label(p, text="Los campos * son obligatorios.",
            font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,14))

        def fld(lbl, val="", show=""):
            tk.Label(p, text=lbl, font=F["sm"], bg=C["bg"], fg=C["sub"]).pack(padx=24,anchor="w")
            ent = Inp(p, show=show, width=44)
            ent.pack(padx=24, fill="x", ipady=7, pady=(2,10))
            if val: ent.insert(0, str(val))
            return ent

        e_nom = fld("Nombre *",   emp.get("nombre","")   if emp else "")
        e_nit = fld("NIT *",      emp.get("nit","")      if emp else "")
        e_dir = fld("Dirección",  emp.get("direccion","") if emp else "")
        e_tel = fld("Teléfono",   emp.get("telefono","")  if emp else "")
        e_dep = fld("Departamento (código DIAN, ej. 05=Antioquia)",
                    emp.get("departamento","") if emp else "")
        e_mun = fld("Municipio (código Divipola, ej. 05001=Medellín)",
                    emp.get("municipio","") if emp else "")

        tk.Label(p, text="Período de pago *", font=F["sm"], bg=C["bg"], fg=C["sub"]).pack(padx=24,anchor="w",pady=(4,2))
        e_periodo = Cbo(p, values=["Mensual","Quincenal"], state="readonly", width=30)
        e_periodo.pack(padx=24, fill="x", ipady=4, pady=(0,10))
        pp_val = emp.get("periodo_pago","mensual") if emp else "mensual"
        e_periodo.set("Quincenal" if pp_val=="quincenal" else "Mensual")

        # Logo
        tk.Label(p, text="─── Logo de la empresa ───", font=F["h4"],
            bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(10,6))
        logo_selector = ImgSelector(p, img_type="logo")
        logo_selector.pack(padx=24, fill="x", pady=(0,10))
        if emp and emp.get("logo_path"):
            logo_selector.set_path(emp["logo_path"])

        le = tk.Label(p, text="", font=F["sm"], fg=C["err"], bg=C["bg"], wraplength=440)
        le.pack(padx=24, anchor="w")

        def save():
            nom=e_nom.val(); nit=e_nit.val()
            if not nom: le.config(text="⚠  El nombre es obligatorio."); return
            if not nit: le.config(text="⚠  El NIT es obligatorio."); return
            periodo_pago = "quincenal" if e_periodo.get()=="Quincenal" else "mensual"
            logo_path = logo_selector.get_path()
            if logo_path:
                logo_path = db.guardar_imagen(logo_path,"logo") if nuevo else \
                            db.guardar_imagen(logo_path,"logo",empresa_id=emp["id"])
            try:
                if nuevo:
                    ok,msg = db.crear_empresa(nom,nit,e_dir.val(),e_tel.val(),
                                              logo_path,periodo_pago,
                                              departamento=e_dep.val() or None,
                                              municipio=e_mun.val() or None)
                else:
                    ok,msg = db.actualizar_empresa(emp["id"],nom,nit,e_dir.val(),e_tel.val(),
                                                   logo_path,periodo_pago,
                                                   departamento=e_dep.val() or None,
                                                   municipio=e_mun.val() or None)
            except Exception as ex: le.config(text=f"Error: {ex}"); return
            if ok: messagebox.showinfo("✓ Éxito",msg,parent=win); win.destroy(); self._load_emp()
            else:  le.config(text=f"✗  {msg}")

        bf = tk.Frame(p, bg=C["bg"]); bf.pack(pady=(14,24), padx=24, anchor="e")
        Btn(bf,"Cancelar",cmd=win.destroy,sty="gh").pack(side="left",padx=(0,8))
        Btn(bf,"Guardar empresa",cmd=save,sty="pri",icon="💾").pack(side="left")
        e_nom.focus_set()

    def _edit_empresa(self):
        eid = self._sel(self.tv_emp,"empresa")
        if eid is None: return
        try:   emp = db.obtener_empresa(eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not emp: messagebox.showerror("Error","Empresa no encontrada.",parent=self); return
        self._form_empresa(emp)

    def _del_empresa(self):
        eid = self._sel(self.tv_emp,"empresa")
        if eid is None: return
        try:   emp = db.obtener_empresa(eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not emp: messagebox.showerror("Error","No encontrada.",parent=self); return
        if not messagebox.askyesno("Confirmar eliminación",
            f"¿Eliminar '{emp['nombre']}'?\n\n"
            "⚠ Esto eliminará TODOS los empleados, nóminas y admins asociados.\n"
            "Esta acción NO se puede deshacer.",parent=self): return
        try:   db.eliminar_empresa(eid); self._load_emp()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    # ═══ ADMINISTRADORES ═════════════════════════════════════════════════════
    def _s_admins(self):
        self._clr()
        self._hdr("👥  Gestión de Administradores","Nuevo Admin",self._form_admin)
        self._sub("Administradores del sistema por empresa. Doble clic para editar.")
        self.tv_adm = self._tree(
            ("id","nombre","user","empresa","estado","fecha"),
            [("ID",45),("Nombre",190),("Usuario",130),
             ("Empresa",195),("Estado",90),("Creado",110)])
        self.tv_adm.bind("<Double-1>", lambda _: self._edit_admin())
        fr = tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,18))
        Btn(fr,"Editar",            cmd=self._edit_admin,   sty="out",icon="✏").pack(side="left",padx=(0,8))
        Btn(fr,"Cambiar contraseña",cmd=self._pass_admin,   sty="out",icon="🔑").pack(side="left",padx=(0,8))
        Btn(fr,"Activar/Desactivar",cmd=self._toggle_admin, sty="wa", icon="⏸").pack(side="left",padx=(0,8))
        Btn(fr,"Eliminar",          cmd=self._del_admin,    sty="err",icon="🗑").pack(side="left")
        self._load_adm()

    def _load_adm(self):
        self.tv_adm.delete(*self.tv_adm.get_children())
        try:
            for i,a in enumerate(db.listar_admins()):
                on = bool(a.get("activo",1))
                self.tv_adm.insert("","end",iid=str(a["id"]),
                    values=(a["id"],a["nombre"],a["username"],
                        a.get("empresa_nombre","—") or "—",
                        "✅ Activo" if on else "⛔ Inactivo",
                        (a.get("creado_en","") or "")[:10]),
                    tags=self._tag(i,on))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _form_admin(self, adm=None):
        nuevo = adm is None
        try:    emps = db.listar_empresas()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not emps:
            messagebox.showwarning("Sin empresas",
                "Crea al menos una empresa primero.",parent=self); return

        h = "640" if nuevo else "580"
        win = self._win("Nuevo Administrador" if nuevo else "Editar Administrador",f"500x{h}")
        tk.Label(win,text=f"👥  {'Nuevo' if nuevo else 'Editar'} Administrador",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")
        tk.Label(win,text="Los campos * son obligatorios.",
            font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,14))

        e_nom  = self._field(win,"Nombre completo *", val=adm["nombre"]   if adm else "")
        e_usr  = self._field(win,"Usuario (login) *", val=adm["username"] if adm else "")
        e_pw = e_cf = None
        if nuevo:
            e_pw = self._field(win,"Contraseña *",  show="●")
            e_cf = self._field(win,"Confirmar contraseña *", show="●")
        
        # Selector de foto
        tk.Label(win, text="Foto de perfil", font=F["h4"], 
                bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(8,2))
        foto_selector = ImgSelector(win, img_type="foto")
        foto_selector.pack(padx=24, fill="x", pady=(0,10))
        
        # Si es edición, cargar foto existente
        if adm and adm.get("foto_path"):
            foto_selector.set_path(adm["foto_path"])

        tk.Label(win,text="Empresa *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w")
        opts = [f"{e['nombre']}  (NIT: {e['nit']})" for e in emps]
        cb = Cbo(win,values=opts,state="readonly",width=44)
        cb.pack(padx=24,fill="x",ipady=4,pady=(2,10))
        idx=0
        if adm and adm.get("empresa_id"):
            ids=[e["id"] for e in emps]
            if adm["empresa_id"] in ids: idx=ids.index(adm["empresa_id"])
        cb.current(idx)
        le = self._err_lbl(win)

        def save():
            nom=e_nom.get().strip(); usr=e_usr.get().strip(); i=cb.current()
            if not nom: le.config(text="⚠  El nombre es obligatorio."); return
            if not usr: le.config(text="⚠  El usuario es obligatorio."); return
            if i<0:     le.config(text="⚠  Selecciona una empresa."); return
            eid = emps[i]["id"]
            
            # Guardar imagen si se seleccionó una nueva
            foto_path = foto_selector.get_path()
            if foto_path:
                if nuevo:
                    saved_foto = db.guardar_imagen(foto_path, "foto")
                else:
                    saved_foto = db.guardar_imagen(foto_path, "foto", usuario_id=adm["id"])
                foto_path = saved_foto
            
            try:
                if nuevo:
                    pw=e_pw.get().strip(); cf=e_cf.get().strip()
                    ok_v,msg_v=val_pass(pw,cf)
                    if not ok_v: le.config(text=f"⚠  {msg_v}"); return
                    ok,msg=db.crear_admin(usr,pw,nom,eid,foto_path)
                else:
                    ok,msg=db.actualizar_admin(adm["id"],usr,nom,eid,foto_path)
            except Exception as ex: le.config(text=f"Error: {ex}"); return
            if ok: messagebox.showinfo("✓ Éxito",msg,parent=win); win.destroy(); self._load_adm()
            else:  le.config(text=f"✗  {msg}")

        self._btns(win,save,"Crear Admin" if nuevo else "Guardar cambios"); e_nom.focus_set()

    def _edit_admin(self):
        aid=self._sel(self.tv_adm,"administrador")
        if aid is None: return
        try:    adm=db.obtener_usuario(aid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not adm: messagebox.showerror("Error","No encontrado.",parent=self); return
        self._form_admin(adm)

    def _pass_admin(self):
        aid=self._sel(self.tv_adm,"administrador")
        if aid is None: return
        try:    adm=db.obtener_usuario(aid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not adm: messagebox.showerror("Error","No encontrado.",parent=self); return
        modal_password(self,aid,adm["nombre"],pide_actual=False,
            titulo=f"Restablecer contraseña — {adm['nombre']}")

    def _toggle_admin(self):
        aid=self._sel(self.tv_adm,"administrador")
        if aid is None: return
        try:    adm=db.obtener_usuario(aid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not adm: messagebox.showerror("Error","No encontrado.",parent=self); return
        est="activo" if adm["activo"] else "inactivo"
        acc="desactivar" if adm["activo"] else "activar"
        if not messagebox.askyesno("Cambiar estado",
            f"'{adm['nombre']}' está {est}.\n¿Deseas {acc} este administrador?",
            parent=self): return
        try:
            ok,msg=db.toggle_activo(aid)
            if ok: self._load_adm()
            else:  messagebox.showerror("Error",msg,parent=self)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _del_admin(self):
        aid=self._sel(self.tv_adm,"administrador")
        if aid is None: return
        try:    adm=db.obtener_usuario(aid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not adm: messagebox.showerror("Error","No encontrado.",parent=self); return
        if not messagebox.askyesno("Confirmar eliminación",
            f"¿Eliminar permanentemente a '{adm['nombre']}' ({adm['username']})?\n"
            "Esta acción NO se puede deshacer.",parent=self): return
        try:   db.eliminar_usuario(aid); self._load_adm()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    # ═══ BITÁCORA GLOBAL (todas las empresas) ═══════════════════════════════════
    def _s_bitacora(self):
        self._clr()
        self._hdr("📜  Bitácora Global de Auditoría")
        self._sub("Registro de acciones de todas las empresas del sistema.")
        self.tv_bit=self._tree(
            ("id","fecha","usuario","accion","detalle","empresa"),
            [("ID",40),("Fecha",150),("Usuario",140),("Acción",180),("Detalle",280),("Empresa",120)])
        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,10))
        Btn(fr,"Actualizar",cmd=self._load_bitacora_sa,sty="out",icon="🔄").pack(side="left",padx=(0,8))
        Btn(fr,"Exportar PDF",cmd=self._gen_pdf_bitacora_sa,sty="pri",icon="📄").pack(side="left")
        self._load_bitacora_sa()

    def _load_bitacora_sa(self):
        self.tv_bit.delete(*self.tv_bit.get_children())
        try:
            registros=db.listar_bitacora(500,empresa_id=None)
            empresas_cache = {}
            for i,r in enumerate(registros):
                eid = r.get("empresa_id")
                if eid and eid not in empresas_cache:
                    try:
                        emp = db.obtener_empresa(eid)
                        empresas_cache[eid] = emp.get("nombre","?") if emp else "?"
                    except:
                        empresas_cache[eid] = "?"
                emp_nom = empresas_cache.get(eid,"—")
                self.tv_bit.insert("","end",iid=str(r["id"]),
                    values=(r["id"],(r.get("fecha","") or "")[:19],
                        r.get("usuario_nom","—"),r.get("accion",""),
                        r.get("detalle","") or "",emp_nom),
                    tags=self._tag(i))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _gen_pdf_bitacora_sa(self):
        try:
            registros=db.listar_bitacora(500,empresa_id=None)
            if not registros:
                messagebox.showwarning("Sin datos","No hay registros en la bitácora.",parent=self); return
            path=pdf.generar_reporte_bitacora(registros,empresa_id=None)
            messagebox.showinfo("✓ PDF generado",f"Reporte de bitácora global guardado en:\n{path}",parent=self)
            abrir(path,self)
        except Exception as ex: messagebox.showerror("Error al generar PDF",str(ex),parent=self)

    # ═══ BACKUP GENERAL (todas las empresas) ═══════════════════════════════════
    def _s_backup(self):
        self._clr()
        self._hdr("💾  Backup General del Sistema")
        self._sub("Exporta o restaura TODOS los datos del sistema (todas las empresas).")

        card=Card(self.ct,padx=24,pady=20); card.pack(fill="x",padx=24,pady=(0,18))

        # Exportar backup completo
        tk.Label(card,text="📦  Exportar Backup Completo",font=F["h3"],bg=C["card"],fg=C["txt"]).pack(anchor="w",pady=(0,6))
        tk.Label(card,text="Crea una copia de seguridad de TODA la base de datos.\nIncluye todas las empresas, empleados, nóminas y configuraciones.",
            font=F["bd"],bg=C["card"],fg=C["sub"],wraplength=600,justify="left").pack(anchor="w",pady=(0,10))
        Btn(card,"Exportar Backup Completo",cmd=self._exportar_backup_sa,sty="ok",icon="📦").pack(anchor="w",pady=(0,16))

        # Separador
        tk.Frame(card,bg=C["border"],height=1).pack(fill="x",pady=8)

        # Importar backup completo
        tk.Label(card,text="📥  Restaurar Backup Completo",font=F["h3"],bg=C["card"],fg=C["err"]).pack(anchor="w",pady=(8,6))
        tk.Label(card,text="Reemplaza TODA la base de datos con un backup previo.\n⚠ Esta acción sobrescribe TODOS los datos de TODAS las empresas.",
            font=F["bd"],bg=C["card"],fg=C["sub"],wraplength=600,justify="left").pack(anchor="w",pady=(0,10))
        Btn(card,"Restaurar Backup Completo",cmd=self._importar_backup_sa,sty="err",icon="📥").pack(anchor="w")

    def _exportar_backup_sa(self):
        from tkinter import filedialog
        ts=datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta=filedialog.asksaveasfilename(
            parent=self,
            title="Guardar backup completo",
            defaultextension=".db",
            initialfile=f"NominaApp_backup_COMPLETO_{ts}.db",
            filetypes=[("Base de datos","*.db"),("Todos","*.*")])
        if not ruta: return
        from tkinter import simpledialog
        pw=simpledialog.askstring("Contraseña del backup",
            "El backup queda cifrado.\n\n"
            "Escribe una contraseña si vas a restaurarlo en OTRO equipo\n"
            "(déjalo vacío para usarlo solo en esta instalación):",
            show="*",parent=self)
        ok,msg=db.exportar_backup(ruta,password=pw or None)
        if ok:
            messagebox.showinfo("✓ Backup exportado",msg,parent=self)
        else:
            messagebox.showerror("Error",msg,parent=self)

    def _importar_backup_sa(self):
        from tkinter import filedialog
        if not self._reautenticar(
            "Vas a reemplazar TODA la base de datos con un backup.\n"
            "Confirma tu identidad para continuar."):
            return
        if not messagebox.askyesno("⚠ Confirmar restauración",
            "¿Estás seguro de restaurar un backup COMPLETO?\n\n"
            "⚠ TODOS los datos de TODAS las empresas serán reemplazados.\n"
            "Se recomienda exportar un backup antes de restaurar.",
            parent=self): return
        ruta=filedialog.askopenfilename(
            parent=self,
            title="Seleccionar archivo de backup completo",
            filetypes=[("Base de datos","*.db"),("Todos","*.*")])
        if not ruta: return
        pw=None
        if db.archivo_cifrado(ruta) and not db.probar_clave_db(ruta):
            from tkinter import simpledialog
            pw=simpledialog.askstring("Backup cifrado",
                "Este backup está protegido con contraseña.\n"
                "Escríbela para continuar:",show="*",parent=self)
            if pw is None: return
        ok,msg=db.importar_backup(ruta,password=pw)
        if ok:
            messagebox.showinfo("✓ Backup restaurado",msg,parent=self)
        else:
            messagebox.showerror("Error",msg,parent=self)

    # ═══ PARÁMETROS LEGALES ═══════════════════════════════════════════════════
    def _s_parametros(self):
        self._clr()
        self._hdr("⚖️  Parámetros Legales","Nuevo Año",self._form_parametros)
        self._sub("SMMLV, auxilio de transporte, UVT y porcentajes de deducción "
                  "por año. Los cálculos de nómina usan el año del período.")
        self.tv_par=self._tree(
            ("id","anio","smmlv","aux","uvt","salud","pension","fsp","umbral"),
            [("ID",35),("Año",60),("SMMLV",110),("Aux. Transporte",120),
             ("UVT",80),("% Salud",70),("% Pensión",75),("% FSP",60),("Umbral FSP",90)])
        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,18))
        Btn(fr,"Editar",cmd=self._edit_parametros,sty="out",icon="✏").pack(side="left",padx=(0,8))
        Btn(fr,"Eliminar",cmd=self._del_parametros,sty="err",icon="🗑").pack(side="left",padx=(0,8))
        self._load_parametros()

    def _load_parametros(self):
        self.tv_par.delete(*self.tv_par.get_children())
        try:
            for i,p in enumerate(db.listar_parametros_legales()):
                self.tv_par.insert("","end",iid=str(p["id"]),
                    values=(p["id"],p["anio"],fmt(p["smmlv"]),fmt(p["aux_transporte"]),
                            fmt(p["uvt"]) if p.get("uvt") else "—",
                            f"{p['pct_salud']:g}%",f"{p['pct_pension']:g}%",
                            f"{p['pct_fsp']:g}%",f"{p['umbral_fsp_smmlv']:g} SMMLV"),
                    tags=self._tag(i))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _form_parametros(self, par=None):
        nuevo = par is None
        win = self._win("Parámetros Legales","500x640")
        tk.Label(win, text=f"⚖️  {'Nuevo' if nuevo else 'Editar'} año de parámetros",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")
        tk.Label(win, text="Valores oficiales vigentes para ese año.",
            font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,14))
        e_anio=self._field(win,"Año *",val=str(par["anio"]) if par else "")
        e_sml=self._field(win,"SMMLV (COP) *",val=str(int(par["smmlv"])) if par else "")
        e_aux=self._field(win,"Auxilio de transporte (COP) *",
            val=str(int(par["aux_transporte"])) if par else "")
        e_uvt=self._field(win,"UVT (COP, opcional)",
            val=str(int(par["uvt"])) if par and par.get("uvt") else "")
        e_sal=self._field(win,"% Salud empleado *",
            val=f"{par['pct_salud']:g}" if par else "4")
        e_pen=self._field(win,"% Pensión empleado *",
            val=f"{par['pct_pension']:g}" if par else "4")
        e_fsp=self._field(win,"% Fondo Solidaridad Pensional *",
            val=f"{par['pct_fsp']:g}" if par else "1")
        e_umb=self._field(win,"Umbral FSP en SMMLV *",
            val=f"{par['umbral_fsp_smmlv']:g}" if par else "4")
        le=self._err_lbl(win)
        def save():
            def num(e,dec=False):
                t=e.get().strip().replace(".","" if not dec else "")
                return float(t.replace(",",".")) if t else 0.0
            try:
                anio=int(e_anio.get().strip()); smmlv=num(e_sml); aux=num(e_aux)
                uvt=num(e_uvt) or None
                sal=num(e_sal); pen=num(e_pen); fsp=num(e_fsp); umb=num(e_umb)
            except: le.config(text="⚠  Valores inválidos."); return
            try:
                ok,msg=db.guardar_parametros_legales(anio,smmlv,aux,uvt,
                    sal,pen,fsp,umb)
            except Exception as ex: le.config(text=f"Error: {ex}"); return
            if ok: messagebox.showinfo("✓ Éxito",msg,parent=win); win.destroy(); self._load_parametros()
            else:  le.config(text=f"✗  {msg}")
        self._btns(win,save,"Guardar parámetros")

    def _edit_parametros(self):
        pid=self._sel(self.tv_par,"registro")
        if pid is None: return
        try:
            par=next((p for p in db.listar_parametros_legales() if p["id"]==pid),None)
            if not par: messagebox.showerror("Error","Registro no encontrado.",parent=self); return
            self._form_parametros(par)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _del_parametros(self):
        pid=self._sel(self.tv_par,"registro")
        if pid is None: return
        if not messagebox.askyesno("Confirmar eliminación",
            "¿Eliminar estos parámetros?\nLas nóminas de ese año usarán los "
            "valores del año más reciente disponible.",parent=self): return
        try: db.eliminar_parametros_legales(pid); self._load_parametros()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)


# ─── ADMIN ────────────────────────────────────────────────────────────────────
class AdminWin(BaseWin):
    def __init__(self, user):
        super().__init__(); self.user=user; self.eid=user["empresa_id"]
        try:    self.emp=db.obtener_empresa(self.eid) or {}
        except: self.emp={}
        self._init_layout(
            f"NóminaApp — {self.emp.get('nombre','Mi Empresa')}",
            "ADMIN", C["ok"],
            [("dash",    "📊  Dashboard",        self._s_dash),
             ("emps",    "👤  Empleados",         self._s_emps),
             ("prestamos","💳  Préstamos",        self._s_prestamos),
             ("novedades","📅  Novedades",        self._s_novedades),
             ("prestaciones","🏖️  Prestaciones",   self._s_prestaciones),
             ("contratos","📋  Contratos",        self._s_contratos),
             ("nomina",  "💰  Nómina",           self._s_nomina),
             ("colilla", "🧾  Colillas de Pago",  self._s_colilla),
             ("hist",    "📋  Historial Nómina",  self._s_hist),
             ("dian",    "🏛️  DIAN",              self._s_dian),
             ("rep",     "📄  Reportes PDF",      self._s_rep),
             ("bitacora","📜  Bitácora",          self._s_bitacora),
             ("backup",  "💾  Backup",            self._s_backup)],
            geo="1300x760", minsize=(1100,640))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Limpiar duplicados al iniciar (bases de datos antiguas)
        try:
            eliminados = db.limpiar_nominas_duplicadas(self.eid)
            if eliminados > 0:
                import tkinter.messagebox as mb
                mb.showinfo("Limpieza automática",
                    f"Se encontraron y eliminaron {eliminados} nómina(s) duplicada(s) "
                    f"de períodos anteriores.\n\nSolo se conservó la más reciente por empleado.",
                    parent=self)
        except: pass
        self._sec("dash")

    def _on_close(self):
        if messagebox.askyesno("Cerrar NóminaApp",
            "¿Estás seguro de que deseas cerrar la aplicación?\n\n"
            "Asegúrate de haber guardado todos los cambios.", parent=self):
            self.destroy()

    def _topbar_right(self, tb, badge, bc):
        rt=tk.Frame(tb,bg=C["topbar"]); rt.pack(side="right",padx=20)
        bf=tk.Frame(rt,bg=bc,padx=8,pady=3); bf.pack(side="left",padx=(0,12))
        tk.Label(bf,text=badge,font=("Segoe UI",8,"bold"),bg=bc,fg=C["white"]).pack()

        # Contenedor para nombre y foto
        user_frame=tk.Frame(rt,bg=C["topbar"]); user_frame.pack(side="left",padx=(0,12))

        # Foto de perfil
        foto_path = self.user.get('foto_path')
        if foto_path and os.path.exists(foto_path):
            try:
                from PIL import Image, ImageTk
                img = Image.open(foto_path)
                img = img.resize((32, 32), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                foto_label = tk.Label(user_frame, image=photo, bg=C["topbar"])
                foto_label.image = photo
                foto_label.pack(side="left", padx=(0,8))
            except:
                tk.Label(user_frame, text="👤", font=("Segoe UI", 16),
                        bg=C["topbar"], fg=C["sub"]).pack(side="left", padx=(0,8))
        else:
            tk.Label(user_frame, text="👤", font=("Segoe UI", 16),
                    bg=C["topbar"], fg=C["sub"]).pack(side="left", padx=(0,8))

        # Nombre del usuario
        tk.Label(user_frame,text=f"{self.user['nombre']}",font=F["bd"],
            bg=C["topbar"],fg=C["sub"]).pack(side="left")

        Btn(rt,"Mi contraseña",icon="🔐",sty="gh",
            cmd=lambda: modal_password(self,self.user["id"],self.user["nombre"],
                pide_actual=True,titulo="Cambiar mi contraseña")
            ).pack(side="left",padx=(12,0))
        Btn(rt,"Cerrar sesión",icon="🚪",sty="gh",
            cmd=self._logout).pack(side="left",padx=(8,0))

    # ═══ DASHBOARD ═══════════════════════════════════════════════════════════
    def _s_dash(self):
        self._clr()
        try:    s=db.estadisticas_empresa(self.eid)
        except: s={"total_empleados":0,"total_nominas":0,"total_pagado":0,"ultimo_periodo":"—"}

        # Header con logo
        hdr_frame=tk.Frame(self.ct,bg=C["bg"]); hdr_frame.pack(fill="x",padx=24,pady=(20,4))
        logo_path = self.emp.get("logo_path")
        if logo_path and os.path.exists(logo_path):
            try:
                from PIL import Image, ImageTk
                img = Image.open(logo_path).resize((56,56), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                lbl_logo = tk.Label(hdr_frame, image=photo, bg=C["bg"])
                lbl_logo.image = photo
                lbl_logo.pack(side="left", padx=(0,14))
            except: pass
        tk.Label(hdr_frame,text="📊  Dashboard",font=F["h2"],bg=C["bg"],fg=C["txt"]).pack(side="left",anchor="w")
        tk.Label(self.ct,text=f"Resumen de {self.emp.get('nombre','')}",
            font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,18))
        cf=tk.Frame(self.ct,bg=C["bg"]); cf.pack(fill="x",padx=24,pady=(0,20))
        for i,(t,v,ic,col) in enumerate([
            ("Empleados Activos", str(s["total_empleados"]),      "👥", C["alight"]),
            ("Nóminas Generadas", str(s["total_nominas"]),        "📋", C["ok"]),
            ("Último Período",    s["ultimo_periodo"],             "📅", C["warn"]),
            ("Total Pagado",      fmt(s["total_pagado"]),         "💵", C["ok"]),
        ]):
            sc=StatCard(cf,t,v,ic,col)
            sc.grid(row=0,column=i,padx=(0 if i==0 else 12,0),sticky="nsew")
            cf.columnconfigure(i,weight=1)

        tk.Label(self.ct,text="Datos de la Empresa",font=F["h3"],
            bg=C["bg"],fg=C["txt"]).pack(padx=24,anchor="w",pady=(0,10))
        card=Card(self.ct,padx=20,pady=16); card.pack(fill="x",padx=24,pady=(0,18))
        for lbl,val in [("🏢 Nombre:",self.emp.get("nombre","")),
                        ("🆔 NIT:",   self.emp.get("nit","")),
                        ("📍 Dirección:",self.emp.get("direccion","—") or "—"),
                        ("📞 Teléfono:", self.emp.get("telefono","—") or "—")]:
            r=tk.Frame(card,bg=C["card"]); r.pack(fill="x",pady=3)
            tk.Label(r,text=lbl,font=F["h4"],width=14,bg=C["card"],fg=C["sub"],anchor="w").pack(side="left")
            tk.Label(r,text=val,font=F["bd"],bg=C["card"],fg=C["txt"]).pack(side="left")

        tk.Label(self.ct,text="Acciones Rápidas",font=F["h3"],
            bg=C["bg"],fg=C["txt"]).pack(padx=24,anchor="w",pady=(0,10))
        qf=tk.Frame(self.ct,bg=C["bg"]); qf.pack(fill="x",padx=24)
        Btn(qf,"Nuevo Empleado",sty="pri",cmd=lambda:self._sec("emps")).pack(side="left",padx=(0,10))
        Btn(qf,"Nómina",sty="ok", cmd=lambda:self._sec("nomina")).pack(side="left",padx=(0,10))
        Btn(qf,"Colillas",sty="out",cmd=lambda:self._sec("colilla")).pack(side="left",padx=(0,10))
        Btn(qf,"Ver Reportes",  sty="out",cmd=lambda:self._sec("rep")).pack(side="left")

    # ═══ EMPLEADOS ════════════════════════════════════════════════════════════
    def _s_emps(self):
        self._clr()
        self._hdr("👤  Gestión de Empleados","Nuevo Empleado",self._form_emp)
        self._sub("Empleados de la empresa. Doble clic para editar.")
        self.tv_emp=self._tree(
            ("id","nombre","doc","cargo","vinc","salario","edad","tipo_cuenta","num_cuenta","ingreso","estado"),
            [("ID",35),("Nombre",150),("Documento",90),("Cargo",115),
             ("Tipo",75),("Salario/Contrato",110),("Edad",45),("T.Cuenta",80),("N°Cuenta",105),("Ingreso",80),("Estado",65)])
        self.tv_emp.bind("<Double-1>",lambda _: self._edit_emp())
        
        # Opciones de filtrado de empleados
        filter_frame=tk.Frame(self.ct,bg=C["bg"]); filter_frame.pack(padx=24,anchor="w",pady=(4,8))
        tk.Label(filter_frame,text="Filtrar empleados:",font=F["h4"],bg=C["bg"],fg=C["sub"]).pack(side="left")
        
        self._emp_filter = tk.StringVar(value="todos")
        tk.Radiobutton(filter_frame, text="Todos", variable=self._emp_filter, value="todos",
            font=F["bd"], bg=C["bg"], fg=C["txt"], selectcolor=C["card"],
            activebackground=C["bg"], activeforeground=C["txt"],
            command=self._load_emps).pack(side="left", padx=(8,4))
        tk.Radiobutton(filter_frame, text="Solo activos", variable=self._emp_filter, value="activos",
            font=F["bd"], bg=C["bg"], fg=C["txt"], selectcolor=C["card"],
            activebackground=C["bg"], activeforeground=C["txt"],
            command=self._load_emps).pack(side="left", padx=4)
        tk.Radiobutton(filter_frame, text="Solo inactivos", variable=self._emp_filter, value="inactivos",
            font=F["bd"], bg=C["bg"], fg=C["txt"], selectcolor=C["card"],
            activebackground=C["bg"], activeforeground=C["txt"],
            command=self._load_emps).pack(side="left", padx=4)
        
        # Barra de búsqueda rápida
        search_frame=tk.Frame(self.ct,bg=C["bg"]); search_frame.pack(padx=24,anchor="w",pady=(0,4))
        tk.Label(search_frame,text="🔍 Buscar:",font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(side="left")
        self._emp_search=Inp(search_frame,ph="Nombre, documento o cargo...",width=40)
        self._emp_search.pack(side="left",padx=(6,0),ipady=5)
        self._emp_search.bind("<KeyRelease>",lambda _: self._load_emps())
        
        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,18))
        Btn(fr,"Editar",            cmd=self._edit_emp,   sty="out",icon="✏").pack(side="left",padx=(0,8))
        Btn(fr,"Activar/Desactivar",cmd=self._toggle_emp, sty="wa", icon="⏸").pack(side="left",padx=(0,8))
        Btn(fr,"Generar PDF",       cmd=self._gen_pdf_empleados, sty="pri",icon="📄").pack(side="left",padx=(0,8))
        Btn(fr,"Certificado Laboral",cmd=self._gen_certificado,sty="ok",icon="📋").pack(side="left",padx=(0,8))
        
        # Menú desplegable para opciones de eliminación
        del_menu = tk.Menu(fr, tearoff=0)
        del_menu.add_command(label="Inactivar empleado", command=self._del_emp)
        del_menu.add_command(label="Eliminar definitivamente", command=self._del_emp_definitivo)
        
        del_btn = Btn(fr, "Eliminar ▾", sty="err", icon="🗑")
        del_btn.pack(side="left")
        del_btn.bind("<Button-1>", lambda e: del_menu.post(e.x_root, e.y_root))
        self._load_emps()

    def _load_emps(self):
        self.tv_emp.delete(*self.tv_emp.get_children())
        try:
            # Obtener el filtro seleccionado
            filtro = getattr(self, '_emp_filter', None)
            if filtro is None:
                filtro = tk.StringVar(value="todos")
                self._emp_filter = filtro
            
            tipo_filtro = filtro.get()
            
            # Obtener texto de búsqueda
            search_text = ""
            if hasattr(self, '_emp_search') and self._emp_search:
                search_text = self._emp_search.val().lower()
            
            # Obtener todos los empleados
            todos_empleados = db.listar_empleados(self.eid, True)
            
            # Filtrar según la selección
            if tipo_filtro == "activos":
                empleados_filtrados = [e for e in todos_empleados if e.get("activo", 1)]
            elif tipo_filtro == "inactivos":
                empleados_filtrados = [e for e in todos_empleados if not e.get("activo", 1)]
            else:  # "todos"
                empleados_filtrados = todos_empleados
            
            # Filtrar por búsqueda
            if search_text:
                empleados_filtrados = [e for e in empleados_filtrados if
                    search_text in e.get("nombre","").lower() or
                    search_text in e.get("documento","").lower() or
                    search_text in e.get("cargo","").lower()]
            
            # Mostrar empleados filtrados
            for i,e in enumerate(empleados_filtrados):
                on=bool(e.get("activo",1))
                vinc = e.get("tipo_vinculacion","salario")
                if vinc == "prestacion":
                    vinc_lbl = "🔧 Prestación"
                    valor_lbl = fmt(e.get("tarifa_diaria") or e["salario"]) + "/día"
                else:
                    vinc_lbl = "💼 Salario"
                    valor_lbl = fmt(e["salario"])
                self.tv_emp.insert("","end",iid=str(e["id"]),
                    values=(e["id"],e["nombre"],e["documento"],e["cargo"],
                        vinc_lbl, valor_lbl,
                        str(e.get("edad","—")) if e.get("edad") else "—",
                        e.get("tipo_cuenta","") or "—",
                        e.get("numero_cuenta","") or "—",
                        e.get("fecha_ingreso","—") or "—",
                        "Activo" if on else "Inactivo"),
                    tags=self._tag(i,on))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _form_emp(self, e=None):
        nuevo = e is None
        win = self._win("Nuevo Empleado" if nuevo else "Editar Empleado", "520x660")
        win.resizable(False, True)

        # ── Canvas scrollable ───────────────────────────────────────────────
        outer = tk.Frame(win, bg=C["bg"]); outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=C["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=C["bg"])
        canvas_win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda ev: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda ev: canvas.itemconfig(canvas_win, width=ev.width))
        canvas.bind_all("<MouseWheel>", lambda ev: canvas.yview_scroll(int(-1*(ev.delta/120)), "units"))
        win.bind("<Destroy>", lambda ev: canvas.unbind_all("<MouseWheel>"))
        p = inner

        # ── Header ─────────────────────────────────────────────────────────
        tk.Label(p, text=f"👤  {'Nuevo' if nuevo else 'Editar'} Empleado",
            font=F["h3"], bg=C["bg"], fg=C["txt"]).pack(pady=(20,2), padx=24, anchor="w")
        tk.Label(p, text="Los campos * son obligatorios.",
            font=F["sm"], bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(0,14))

        # ── Helper para campos ──────────────────────────────────────────────
        def fld(label, val="", show="", solo_num=False, alfanumerico=False):
            tk.Label(p, text=label, font=F["sm"], bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w")
            ent = Inp(p, show=show, width=46)
            ent.pack(padx=24, fill="x", ipady=7, pady=(2,10))
            if val: ent.insert(0, str(val))
            if solo_num:
                def _only_num(sv):
                    v = sv.get()
                    clean = re.sub(r"[^0-9]", "", v)
                    if v != clean: sv.set(clean)
                sv = tk.StringVar(value=ent.get())
                ent.config(textvariable=sv)
                sv.trace_add("write", lambda *_: _only_num(sv))
            elif alfanumerico:
                def _alfanum(sv):
                    v = sv.get()
                    clean = re.sub(r"[^0-9a-zA-ZáéíóúÁÉÍÓÚñÑ\-]", "", v)
                    if v != clean: sv.set(clean)
                sv = tk.StringVar(value=ent.get())
                ent.config(textvariable=sv)
                sv.trace_add("write", lambda *_: _alfanum(sv))
            return ent

        e_nom = fld("Nombre completo *",        e.get("nombre","")    if e else "")
        e_doc = fld("Documento de identidad *", e.get("documento","") if e else "", alfanumerico=True)
        e_car = fld("Cargo *",                  e.get("cargo","")     if e else "")

        # ── Tipo de vinculación ─────────────────────────────────────────────
        tk.Label(p, text="Tipo de vinculación *", font=F["h4"], bg=C["bg"], fg=C["sub"]
            ).pack(padx=24, anchor="w", pady=(4,6))
        vinc_var = tk.StringVar(value=e.get("tipo_vinculacion","salario") if e else "salario")
        vf = tk.Frame(p, bg=C["bg"]); vf.pack(padx=24, fill="x", pady=(0,4))
        for txt, val in [
            ("💼  Salario fijo (mensual/quincenal según empresa)", "salario"),
            ("🔧  Prestación de servicios (pago por días trabajados)", "prestacion"),
        ]:
            tk.Radiobutton(vf, text=txt, variable=vinc_var, value=val,
                font=F["bd"], bg=C["bg"], fg=C["txt"],
                selectcolor=C["card"], activebackground=C["bg"], activeforeground=C["txt"]
                ).pack(anchor="w", pady=(0,4))

        # Campos dinámicos según tipo (contenedor fijo para que nunca
        # queden por debajo de los botones al re-empaquetar)
        dyn = tk.Frame(p, bg=C["bg"]); dyn.pack(fill="x")
        lbl_sal  = tk.Label(dyn, text="Salario base mensual (COP) *", font=F["sm"], bg=C["bg"], fg=C["sub"])
        e_sal    = Inp(dyn, width=46)
        if e and e.get("salario") and e.get("tipo_vinculacion","salario")=="salario":
            e_sal.insert(0, str(int(float(e["salario"]))))

        lbl_tar  = tk.Label(dyn, text="Tarifa por día (COP) *", font=F["sm"], bg=C["bg"], fg=C["sub"])
        e_tar    = Inp(dyn, width=46)
        if e and e.get("tarifa_diaria"): e_tar.insert(0, str(int(float(e["tarifa_diaria"]))))

        def _toggle_vinc(*_):
            v = vinc_var.get()
            lbl_sal.pack_forget(); e_sal.pack_forget()
            lbl_tar.pack_forget(); e_tar.pack_forget()
            if v == "salario":
                lbl_sal.pack(padx=24, anchor="w")
                e_sal.pack(padx=24, fill="x", ipady=7, pady=(2,10))
            elif v == "prestacion":
                lbl_tar.pack(padx=24, anchor="w")
                e_tar.pack(padx=24, fill="x", ipady=7, pady=(2,10))
            win.after(50, lambda: canvas.configure(scrollregion=canvas.bbox("all")))
        vinc_var.trace_add("write", _toggle_vinc)
        _toggle_vinc()

        e_ing = fld("Fecha de ingreso (YYYY-MM-DD)", e.get("fecha_ingreso","") if e else "")

        # ── Información de cobro ────────────────────────────────────────────
        tk.Label(p, text="─── Información de Cobro ───", font=F["h4"],
            bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(10,6))
        tk.Label(p, text="Tipo de cuenta", font=F["sm"], bg=C["bg"], fg=C["sub"]
            ).pack(padx=24, anchor="w", pady=(0,2))
        tipo_cuenta_vals = ["Ahorros","Corriente","Otro"]
        e_tipo_cuenta = Cbo(p, values=tipo_cuenta_vals, state="readonly", width=30)
        e_tipo_cuenta.pack(padx=24, fill="x", ipady=4, pady=(0,10))
        if e and e.get("tipo_cuenta"):
            e_tipo_cuenta.set(e["tipo_cuenta"] if e["tipo_cuenta"] in tipo_cuenta_vals else "Otro")

        e_num_cuenta = fld("Número de cuenta", e.get("numero_cuenta","") if e else "", solo_num=True)
        e_edad       = fld("Edad",             str(e.get("edad","")) if e and e.get("edad") else "", solo_num=True)
        e_correo     = fld("Correo electrónico (opcional)", e.get("correo","") if e else "")

        # ── Datos DIAN (nómina electrónica) ─────────────────────────────────
        tk.Label(p, text="─── Datos DIAN (nómina electrónica) ───", font=F["h4"],
            bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(10,6))
        tk.Label(p, text="Requeridos para generar el documento mensual. "
            "Si no los diligencias, el nombre se reparte automáticamente.",
            font=F["sm"], bg=C["bg"], fg=C["muted"], wraplength=460,
            justify="left").pack(padx=24, anchor="w", pady=(0,8))

        def cbo_dian(label, mapping, actual):
            tk.Label(p, text=label, font=F["sm"], bg=C["bg"], fg=C["sub"]
                ).pack(padx=24, anchor="w", pady=(0,2))
            keys = list(mapping.keys())
            cb = Cbo(p, values=[f"{k} — {v}" for k, v in mapping.items()],
                     state="readonly", width=44)
            cb.pack(padx=24, fill="x", ipady=4, pady=(0,10))
            cb.current(keys.index(actual) if actual in keys else 0)
            return cb, keys

        e_pnom  = fld("Primer nombre (DIAN)",   e.get("primer_nombre","")   if e else "")
        e_onom  = fld("Otros nombres",          e.get("otros_nombres","")   if e else "")
        e_pap   = fld("Primer apellido (DIAN)", e.get("primer_apellido","") if e else "")
        e_sap   = fld("Segundo apellido",       e.get("segundo_apellido","") if e else "")
        cb_tdoc, tdoc_keys = cbo_dian("Tipo de documento",
            db.TIPOS_DOCUMENTO_DIAN, (e.get("tipo_documento") if e else "13") or "13")
        cb_ttra, ttra_keys = cbo_dian("Tipo de trabajador",
            db.TIPOS_TRABAJADOR_DIAN, (e.get("tipo_trabajador") if e else "01") or "01")
        cb_stra, stra_keys = cbo_dian("Subtipo de trabajador",
            db.SUBTIPOS_TRABAJADOR_DIAN, (e.get("subtipo_trabajador") if e else "00") or "00")
        cb_tcon, tcon_keys = cbo_dian("Tipo de contrato",
            db.TIPOS_CONTRATO_DIAN, (e.get("tipo_contrato") if e else "1") or "1")
        e_mun   = fld("Municipio de trabajo (código Divipola, ej. 05001)",
                      e.get("municipio_laboral","") if e else "")
        e_eps   = fld("EPS",                e.get("eps","")             if e else "")
        e_arl   = fld("ARL",                e.get("arl","")             if e else "")
        e_fpen  = fld("Fondo de pensiones", e.get("fondo_pension","")   if e else "")
        e_fces  = fld("Fondo de cesantías", e.get("fondo_cesantias","") if e else "")
        e_banco = fld("Banco (código o nombre)", e.get("banco","")      if e else "")
        e_fret  = fld("Fecha de retiro (YYYY-MM-DD, si aplica)",
                      e.get("fecha_retiro","") if e else "")
        arp_var = tk.IntVar(value=int(e.get("alto_riesgo_pension") or 0) if e else 0)
        tk.Checkbutton(p, text="Alto riesgo de pensión (oficio de alto riesgo)",
            variable=arp_var, font=F["bd"], bg=C["bg"], fg=C["txt"],
            selectcolor=C["card"], activebackground=C["bg"],
            activeforeground=C["txt"]).pack(padx=24, anchor="w", pady=(0,10))

        # ── Error ───────────────────────────────────────────────────────────
        le = tk.Label(p, text="", font=F["sm"], fg=C["err"], bg=C["bg"],
            wraplength=460, justify="left")
        le.pack(padx=24, anchor="w")

        # ── Botones SIEMPRE al final del scroll ─────────────────────────────
        bf = tk.Frame(p, bg=C["bg"]); bf.pack(pady=(16,30), padx=24, anchor="e")
        Btn(bf, "Cancelar", cmd=win.destroy, sty="gh").pack(side="left", padx=(0,8))
        Btn(bf, "Registrar" if nuevo else "Guardar cambios",
            cmd=lambda: _save(), sty="pri", icon="💾").pack(side="left")

        # ── Lógica guardar ──────────────────────────────────────────────────
        def _save():
            nom = e_nom.val(); doc = e_doc.val(); car = e_car.val()
            ing = e_ing.val(); tipo_cuenta = e_tipo_cuenta.get()
            num_cuenta = e_num_cuenta.val(); edad_str = e_edad.val()
            vinc = vinc_var.get()

            if not nom: le.config(text="⚠  El nombre es obligatorio."); return
            if not doc: le.config(text="⚠  El documento es obligatorio."); return
            if not car: le.config(text="⚠  El cargo es obligatorio."); return

            sal = 0.0; tarifa_diaria = None

            if vinc == "salario":
                sr = e_sal.val()
                if not sr: le.config(text="⚠  El salario es obligatorio."); return
                try:
                    sal = float(sr.replace(".","").replace(",","."))
                    if sal <= 0: raise ValueError
                except: le.config(text="⚠  El salario debe ser un número positivo."); return
                ok_min, msg_min = db.validar_salario_minimo(sal)
                if not ok_min:
                    if not messagebox.askyesno("Salario por debajo del mínimo",
                        f"{msg_min}\n\n¿Deseas continuar?", parent=win): return

            elif vinc == "prestacion":
                tr = e_tar.val()
                if not tr: le.config(text="⚠  La tarifa diaria es obligatoria."); return
                try:
                    tarifa_diaria = float(tr.replace(".","").replace(",","."))
                    if tarifa_diaria <= 0: raise ValueError
                except: le.config(text="⚠  La tarifa debe ser un número positivo."); return
                sal = tarifa_diaria  # guardamos tarifa como referencia en salario

            if ing:
                try: datetime.strptime(ing, "%Y-%m-%d")
                except: le.config(text="⚠  Fecha inválida. Usa YYYY-MM-DD."); return

            edad = None
            if edad_str:
                try:
                    edad = int(edad_str)
                    if edad < 0 or edad > 120: le.config(text="⚠  Edad inválida."); return
                except: le.config(text="⚠  La edad debe ser un número."); return

            if tipo_cuenta not in ["Ahorros","Corriente","Otro"]: tipo_cuenta = None
            correo_emp = e_correo.val().strip() or None

            # Datos DIAN: si no se separan los nombres, se reparten del nombre
            pnom = e_pnom.val(); onom = e_onom.val()
            pap  = e_pap.val();  sap  = e_sap.val()
            if not pnom and not pap:
                toks = nom.split()
                pnom = toks[0] if toks else ""
                pap  = toks[-1] if len(toks) > 1 else ""
                onom = " ".join(toks[1:-1]) if len(toks) > 2 else ""
            fret = e_fret.val()
            if fret:
                try: datetime.strptime(fret, "%Y-%m-%d")
                except: le.config(text="⚠  Fecha de retiro inválida."); return
            dian = {
                "primer_nombre": pnom or None, "otros_nombres": onom or None,
                "primer_apellido": pap or None, "segundo_apellido": sap or None,
                "tipo_documento": tdoc_keys[cb_tdoc.current()],
                "tipo_trabajador": ttra_keys[cb_ttra.current()],
                "subtipo_trabajador": stra_keys[cb_stra.current()],
                "tipo_contrato": tcon_keys[cb_tcon.current()],
                "municipio_laboral": e_mun.val() or None,
                "eps": e_eps.val() or None, "arl": e_arl.val() or None,
                "fondo_pension": e_fpen.val() or None,
                "fondo_cesantias": e_fces.val() or None,
                "banco": e_banco.val() or None,
                "fecha_retiro": fret or None,
                "alto_riesgo_pension": int(arp_var.get()),
            }

            try:
                if nuevo:
                    ok, msg = db.crear_empleado(self.eid, nom, doc, car, sal, ing,
                        tipo_cuenta, num_cuenta, edad, vinc, tarifa_diaria, correo_emp,
                        dian=dian)
                else:
                    ok, msg = db.actualizar_empleado(e["id"], self.eid, nom, doc, car, sal, ing,
                        tipo_cuenta, num_cuenta, edad, vinc, tarifa_diaria, correo_emp,
                        dian=dian)
            except Exception as ex: le.config(text=f"Error: {ex}"); return

            if ok:
                db.registrar_bitacora(self.user["id"], self.user["nombre"],
                    "Creó empleado" if nuevo else "Editó empleado",
                    f"{nom} — {doc} [{vinc}]", empresa_id=self.eid)
                messagebox.showinfo("✓ Éxito", msg, parent=win)
                win.destroy(); self._load_emps()
            else:
                le.config(text=f"✗  {msg}")

        e_nom.focus_set()

    def _edit_emp(self):
        eid=self._sel(self.tv_emp,"empleado")
        if eid is None: return
        try:   emp=db.obtener_empleado(eid,self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not emp: messagebox.showerror("Error","Empleado no encontrado.",parent=self); return
        self._form_emp(emp)

    def _toggle_emp(self):
        eid=self._sel(self.tv_emp,"empleado")
        if eid is None: return
        try:   emp=db.obtener_empleado(eid,self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not emp: messagebox.showerror("Error","No encontrado.",parent=self); return
        est="activo" if emp["activo"] else "inactivo"
        acc="desactivar" if emp["activo"] else "activar"
        if not messagebox.askyesno("Cambiar estado",
            f"'{emp['nombre']}' está {est}.\n¿Deseas {acc} este empleado?",
            parent=self): return
        try:   
            ok,msg = db.toggle_activo_empleado(eid,self.eid)
            if ok: 
                db.registrar_bitacora(self.user["id"],self.user["nombre"],"Cambió estado empleado",emp["nombre"],empresa_id=self.eid)
                messagebox.showinfo("✓ Éxito",msg,parent=self)
                self._load_emps()
            else:
                messagebox.showerror("Error",msg,parent=self)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _del_emp(self):
        eid=self._sel(self.tv_emp,"empleado")
        if eid is None: return
        try:   emp=db.obtener_empleado(eid,self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not emp: messagebox.showerror("Error","No encontrado.",parent=self); return
        if not messagebox.askyesno("Confirmar",
            f"¿Inactivar a '{emp['nombre']}'?\nNo aparecerá en listas activas.",
            parent=self): return
        try:   db.eliminar_empleado(eid,self.eid); self._load_emps()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _del_emp_definitivo(self):
        eid=self._sel(self.tv_emp,"empleado")
        if eid is None: return
        try:   emp=db.obtener_empleado(eid,self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not emp: messagebox.showerror("Error","No encontrado.",parent=self); return
        
        # Verificar si tiene nóminas asociadas
        conn = db.get_connection()
        nominas_count = conn.execute(
            "SELECT COUNT(*) FROM nominas WHERE empleado_id=? AND empresa_id=?", 
            (eid, self.eid)
        ).fetchone()[0]
        conn.close()
        
        # Crear diálogo para decidir qué hacer con las nóminas
        win = tk.Toplevel(self)
        win.title("Eliminar Empleado Definitivamente")
        win.geometry("500x400")
        win.configure(bg=C["bg"])
        win.grab_set()
        win.resizable(False, False)
        win.transient(self)
        
        tk.Label(win, text=f"🗑  Eliminar a '{emp['nombre']}'",
            font=F["h3"], bg=C["bg"], fg=C["txt"]).pack(pady=(20,2), padx=24, anchor="w")
        
        if nominas_count > 0:
            tk.Label(win, text=f"⚠  Este empleado tiene {nominas_count} registros de nómina asociados.",
                font=F["bd"], bg=C["bg"], fg=C["warn"]).pack(padx=24, anchor="w", pady=(0,10))
            
            tk.Label(win, text="¿Qué desea hacer con los registros de nómina?",
                font=F["h4"], bg=C["bg"], fg=C["txt"]).pack(padx=24, anchor="w", pady=(10,8))
            
            choice = tk.StringVar(value="mantener")

            tk.Radiobutton(win, text="Conservar los registros de nómina\n(el empleado quedará inactivo, no se elimina)",
                variable=choice, value="mantener", font=F["bd"], bg=C["bg"], fg=C["txt"],
                selectcolor=C["card"], activebackground=C["bg"], activeforeground=C["txt"],
                justify="left").pack(padx=24, anchor="w", pady=4)

            tk.Radiobutton(win, text=f"Eliminar al empleado y sus {nominas_count} registros de nómina",
                variable=choice, value="eliminar", font=F["bd"], bg=C["bg"], fg=C["txt"],
                selectcolor=C["card"], activebackground=C["bg"], activeforeground=C["txt"],
                justify="left").pack(padx=24, anchor="w", pady=4)
        else:
            tk.Label(win, text="✅  Este empleado no tiene registros de nómina asociados.",
                font=F["bd"], bg=C["bg"], fg=C["ok"]).pack(padx=24, anchor="w", pady=(0,10))
            choice = tk.StringVar(value="eliminar")  # No hay nóminas, se puede eliminar directamente
        
        tk.Label(win, text="\n⚠  La eliminación definitiva NO se puede deshacer.",
            font=F["h4"], bg=C["bg"], fg=C["err"]).pack(padx=24, anchor="w", pady=(10,0))
        
        def confirmar():
            if not self._reautenticar(
                f"Vas a eliminar definitivamente a '{emp['nombre']}'.\n"
                "Confirma tu identidad para continuar."):
                return
            mantener = choice.get() == "mantener" and nominas_count > 0
            if mantener:
                pregunta = (f"¿Inactivar a '{emp['nombre']}'?\n\n"
                            "Sus registros de nómina se conservarán y el empleado\n"
                            "quedará como inactivo (podrá reactivarse después).")
            else:
                pregunta = (f"¿Está seguro de eliminar permanentemente a '{emp['nombre']}'?\n\n"
                            f"Se eliminarán también sus {nominas_count} registros de nómina.\n"
                            "Esta acción NO se puede deshacer.")
            if not messagebox.askyesno("Confirmar", pregunta, parent=win):
                return

            try:
                if mantener:
                    ok, msg = db.eliminar_empleado(eid, self.eid)
                else:
                    ok, msg = db.eliminar_empleado_definitivo(eid, self.eid, True)

                if ok:
                    messagebox.showinfo("✓ Éxito", msg, parent=win)
                    win.destroy()
                    self._load_emps()
                else:
                    messagebox.showerror("Error", msg, parent=win)
            except Exception as ex:
                messagebox.showerror("Error", str(ex), parent=win)
        
        btn_frame = tk.Frame(win, bg=C["bg"])
        btn_frame.pack(pady=20, padx=24, anchor="e", side="bottom")
        Btn(btn_frame, "Cancelar", cmd=win.destroy, sty="gh").pack(side="left", padx=(0,8))
        Btn(btn_frame, "Eliminar Definitivamente", cmd=confirmar, sty="err", icon="🗑").pack(side="left")

    def _gen_pdf_empleados(self):
        """Genera PDF con los empleados filtrados desde la vista de gestión de empleados."""
        try:
            # Obtener información de la empresa
            empresa = db.obtener_empresa(self.eid)
            if not empresa:
                messagebox.showerror("Error", "No se pudo obtener información de la empresa.", parent=self)
                return
            
            # Obtener el filtro seleccionado
            filtro = getattr(self, '_emp_filter', None)
            if filtro is None:
                filtro = tk.StringVar(value="todos")
                self._emp_filter = filtro
            
            tipo_filtro = filtro.get()
            
            # Obtener empleados según el filtro
            if tipo_filtro == "activos":
                empleados = db.obtener_reporte_empleados(self.eid, incluir_inactivos=False)
                filtro_desc = "solo activos"
            elif tipo_filtro == "inactivos":
                # Para inactivos, obtenemos todos y filtramos
                todos = db.obtener_reporte_empleados(self.eid, incluir_inactivos=True)
                empleados = [e for e in todos if not e.get("activo", 1)]
                filtro_desc = "solo inactivos"
            else:  # "todos"
                empleados = db.obtener_reporte_empleados(self.eid, incluir_inactivos=True)
                filtro_desc = "todos"
            
            if not empleados:
                messagebox.showwarning("Sin datos", f"No hay empleados {filtro_desc}.", parent=self)
                return
            
            # Generar PDF
            path = pdf.generar_reporte_empleados(empleados, empresa, empresa_id=self.eid)
            
            # Contar activos e inactivos para el mensaje
            activos = sum(1 for e in empleados if e.get("activo", 1))
            inactivos = len(empleados) - activos
            
            messagebox.showinfo("✓ PDF generado", 
                f"Reporte de empleados guardado en:\n{path}\n\n"
                f"Tipo: {filtro_desc}\n"
                f"Total empleados: {len(empleados)}\n"
                f"Activos: {activos}" + (f"\nInactivos: {inactivos}" if inactivos > 0 else ""), 
                parent=self)
            
            # Abrir el PDF
            abrir(path, self)
            
        except Exception as ex:
            messagebox.showerror("Error al generar PDF", str(ex), parent=self)

    def _gen_certificado(self):
        """Genera certificado laboral PDF para el empleado seleccionado."""
        eid=self._sel(self.tv_emp,"empleado")
        if eid is None:
            messagebox.showwarning("Aviso","Por favor selecciona un empleado de la lista.",parent=self)
            return
        try:
            emp=db.obtener_empleado(eid,self.eid)
            if not emp:
                messagebox.showerror("Error","Empleado no encontrado.",parent=self)
                return
            if not messagebox.askyesno("Certificado Laboral",
                f"¿Generar certificado laboral para '{emp['nombre']}'?",
                parent=self):
                return
            path=pdf.generar_certificado_laboral(emp,self.emp,empresa_id=self.eid)
            db.registrar_bitacora(self.user["id"],self.user["nombre"],"Generó certificado laboral",emp["nombre"],empresa_id=self.eid)
            messagebox.showinfo("✓ Certificado generado",
                f"Certificado laboral guardado en:\n{path}",parent=self)
            abrir(path,self)
        except Exception as ex:
            import traceback; traceback.print_exc()
            messagebox.showerror("Error al generar certificado",str(ex),parent=self)

    # ═══ PRÉSTAMOS ═══════════════════════════════════════════════════════════
    def _s_prestamos(self):
        self._clr()
        self._hdr("💳  Préstamos de Empleados","Nuevo Préstamo",self._form_prestamo)
        self._sub("Gestiona los préstamos de los empleados. Al pagar nómina podrás abonar o pagar el saldo pendiente.")
        self.tv_pres=self._tree(
            ("id","empleado","documento","cargo","monto","abonado","saldo","estado","fecha"),
            [("ID",35),("Empleado",165),("Documento",95),("Cargo",120),
             ("Monto",95),("Abonado",95),("Saldo",95),("Estado",75),("Fecha",90)])
        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,18))
        Btn(fr,"Abonar / Pagar",cmd=self._abonar_prestamo,sty="ok",icon="💰").pack(side="left",padx=(0,8))
        Btn(fr,"Editar",cmd=self._edit_prestamo,sty="out",icon="✏").pack(side="left",padx=(0,8))
        Btn(fr,"Eliminar",cmd=self._del_prestamo,sty="err",icon="🗑").pack(side="left",padx=(0,8))
        
        # Filtro por estado
        filter_frame=tk.Frame(self.ct,bg=C["bg"]); filter_frame.pack(padx=24,anchor="w",pady=(0,8))
        tk.Label(filter_frame,text="Filtrar:",font=F["h4"],bg=C["bg"],fg=C["sub"]).pack(side="left")
        self._pres_filter = tk.StringVar(value="todos")
        for txt, val in [("Todos","todos"),("Activos","activo"),("Pagados","pagado")]:
            tk.Radiobutton(filter_frame, text=txt, variable=self._pres_filter, value=val,
                font=F["bd"], bg=C["bg"], fg=C["txt"], selectcolor=C["card"],
                activebackground=C["bg"], activeforeground=C["txt"],
                command=self._load_prestamos).pack(side="left", padx=(8,4))
        
        self._load_prestamos()

    def _load_prestamos(self):
        self.tv_pres.delete(*self.tv_pres.get_children())
        try:
            filtro = getattr(self, '_pres_filter', None)
            estado = filtro.get() if filtro and filtro.get() != "todos" else None
            prestamos = db.listar_prestamos(self.eid, estado=estado)
            for i,p in enumerate(prestamos):
                est = p.get("estado","activo")
                est_txt = "✅ Pagado" if est == "pagado" else "Activo"
                self.tv_pres.insert("","end",iid=str(p["id"]),
                    values=(p["id"],p.get("empleado_nombre",""),p.get("documento",""),
                        p.get("cargo",""),fmt(p["monto"]),fmt(p.get("abonado",0)),
                        fmt(p.get("saldo",0)),est_txt,
                        (p.get("fecha","") or "")[:10]),
                    tags=self._tag(i, est=="activo"))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _form_prestamo(self, pres=None):
        nuevo = pres is None
        win = self._win("Nuevo Préstamo" if nuevo else "Editar Préstamo","500x520")
        tk.Label(win, text=f"💳  {'Nuevo' if nuevo else 'Editar'} Préstamo",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")
        tk.Label(win, text="Los campos * son obligatorios.",
            font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,14))
        
        # Selector de empleado
        try:    emps=db.listar_empleados(self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=win); return
        if not emps:
            messagebox.showwarning("Sin empleados","Registra empleados primero.",parent=win); return
        
        tk.Label(win,text="Empleado *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w")
        emp_opts=[f"{e['nombre']}  —  {e['cargo']}" for e in emps]
        cb_emp=Cbo(win,values=emp_opts,state="readonly",width=40)
        cb_emp.pack(padx=24,fill="x",ipady=4,pady=(2,10))
        # Si es edición, seleccionar empleado
        if pres:
            ids=[e["id"] for e in emps]
            if pres["empleado_id"] in ids: cb_emp.current(ids.index(pres["empleado_id"]))
            cb_emp.config(state="disabled")
        
        e_monto=self._field(win,"Monto del préstamo (COP) *",
            val=str(int(float(pres["monto"]))) if pres else "")
        e_fecha=self._field(win,"Fecha (YYYY-MM-DD) *",
            val=pres.get("fecha","") if pres else datetime.now().strftime("%Y-%m-%d"))
        e_obs=self._field(win,"Observaciones",
            val=pres.get("observaciones","") if pres else "")
        
        le=self._err_lbl(win)
        def save():
            i=cb_emp.current()
            if i<0: le.config(text="⚠  Selecciona un empleado."); return
            eid_emp=emps[i]["id"]
            monto_str=e_monto.get().strip(); fecha=e_fecha.get().strip()
            if not monto_str or not fecha:
                le.config(text="⚠  Los campos marcados son obligatorios."); return
            try:
                monto=float(monto_str.replace(".","").replace(",","."))
                if monto<=0: raise ValueError
            except: le.config(text="⚠  Monto inválido."); return
            try: datetime.strptime(fecha,"%Y-%m-%d")
            except: le.config(text="⚠  Fecha inválida. Usa YYYY-MM-DD."); return
            
            try:
                if nuevo:
                    ok,msg=db.crear_prestamo(self.eid,eid_emp,monto,fecha,e_obs.get().strip())
                else:
                    ok,msg=db.actualizar_prestamo(pres["id"],monto,e_obs.get().strip())
            except Exception as ex: le.config(text=f"Error: {ex}"); return
            if ok: messagebox.showinfo("✓ Éxito",msg,parent=win); win.destroy(); self._load_prestamos()
            else:  le.config(text=f"✗  {msg}")
        self._btns(win,save,"Guardar préstamo")

    def _abonar_prestamo(self):
        pid=self._sel(self.tv_pres,"préstamo")
        if pid is None: return
        try:
            prestamos=db.listar_prestamos(self.eid)
            pres=next((p for p in prestamos if p["id"]==pid),None)
            if not pres: messagebox.showerror("Error","Préstamo no encontrado.",parent=self); return
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        
        if pres.get("estado")=="pagado":
            messagebox.showinfo("Préstamo pagado","Este préstamo ya está completamente pagado.",parent=self); return
        
        saldo=float(pres.get("saldo",0))
        win=self._win("Abonar al Préstamo","500x480")
        tk.Label(win,text=f"💰  Abonar al Préstamo #{pres['id']}",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")
        tk.Label(win,text=f"Empleado: {pres.get('empleado_nombre','')}",
            font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w")
        tk.Label(win,text=f"Monto original: {fmt(float(pres['monto']))}  |  Abonado: {fmt(float(pres.get('abonado',0)))}  |  Saldo: {fmt(saldo)}",
            font=F["bd"],bg=C["bg"],fg=C["ok"] if saldo>0 else C["err"]).pack(padx=24,anchor="w",pady=(0,14))
        
        # Opciones: pagar todo o abonar cantidad
        tk.Label(win,text="Tipo de pago *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w")
        tipo_var=tk.StringVar(value="abono")
        tipo_frame=tk.Frame(win,bg=C["bg"]); tipo_frame.pack(padx=24,fill="x",pady=(2,10))
        for txt,val in [("Pagar saldo completo","total"),("Abonar cantidad","abono")]:
            tk.Radiobutton(tipo_frame,text=txt,variable=tipo_var,value=val,
                font=F["bd"],bg=C["bg"],fg=C["txt"],selectcolor=C["card"],
                activebackground=C["bg"],activeforeground=C["txt"]).pack(side="left",padx=(0,12))
        
        e_abono=self._field(win,"Monto a abonar (COP) *",val=str(int(saldo)))
        
        def _toggle_abono(*_):
            if tipo_var.get()=="total":
                e_abono.delete(0,tk.END); e_abono.insert(0,str(int(saldo)))
                e_abono.config(state="disabled")
            else:
                e_abono.config(state="normal")
        tipo_var.trace_add("write",_toggle_abono)
        _toggle_abono()
        
        le=self._err_lbl(win)
        def save():
            abono_str=e_abono.get().strip()
            if not abono_str: le.config(text="⚠  Ingresa el monto a abonar."); return
            try:
                monto_abono=float(abono_str.replace(".","").replace(",","."))
                if monto_abono<=0: raise ValueError
            except: le.config(text="⚠  Monto inválido."); return
            if monto_abono>saldo:
                le.config(text=f"⚠  El abono supera el saldo pendiente ({fmt(saldo)})."); return
            try:
                ok,msg=db.abonar_prestamo(pid,monto_abono)
            except Exception as ex: le.config(text=f"Error: {ex}"); return
            if ok: messagebox.showinfo("✓ Éxito",msg,parent=win); win.destroy(); self._load_prestamos()
            else:  le.config(text=f"✗  {msg}")
        self._btns(win,save,"Registrar abono")

    def _edit_prestamo(self):
        pid=self._sel(self.tv_pres,"préstamo")
        if pid is None: return
        try:
            prestamos=db.listar_prestamos(self.eid)
            pres=next((p for p in prestamos if p["id"]==pid),None)
            if not pres: messagebox.showerror("Error","Préstamo no encontrado.",parent=self); return
            self._form_prestamo(pres)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _del_prestamo(self):
        pid=self._sel(self.tv_pres,"préstamo")
        if pid is None: return
        if not messagebox.askyesno("Confirmar eliminación",
            "¿Eliminar este préstamo?\n⚠ Esta acción no se puede deshacer.",parent=self): return
        try: db.eliminar_prestamo(pid); self._load_prestamos()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    # ═══ NOVEDADES ═══════════════════════════════════════════════════════════
    def _s_novedades(self):
        self._clr()
        self._hdr("📅  Novedades de Nómina","Nueva Novedad",self._form_novedad)
        self._sub("Incapacidades, licencias, vacaciones, suspensiones y otros eventos "
                  "que afectan la nómina (base para el reporte DIAN).")
        self.tv_nov=self._tree(
            ("id","empleado","tipo","inicio","fin","valor","estado","obs"),
            [("ID",35),("Empleado",180),("Tipo",170),("Inicio",95),
             ("Fin",95),("Valor",95),("Estado",75),("Observaciones",180)])
        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,18))
        Btn(fr,"Editar",cmd=self._edit_novedad,sty="out",icon="✏").pack(side="left",padx=(0,8))
        Btn(fr,"Cerrar novedad",cmd=self._cerrar_novedad,sty="gh",icon="✔").pack(side="left",padx=(0,8))
        Btn(fr,"Eliminar",cmd=self._del_novedad,sty="err",icon="🗑").pack(side="left",padx=(0,8))
        self._load_novedades()

    def _load_novedades(self):
        self.tv_nov.delete(*self.tv_nov.get_children())
        try:
            for i,n in enumerate(db.listar_novedades(self.eid)):
                self.tv_nov.insert("","end",iid=str(n["id"]),
                    values=(n["id"],n.get("empleado_nombre",""),
                            db.TIPOS_NOVEDAD.get(n["tipo"],n["tipo"]),
                            n.get("fecha_inicio",""),n.get("fecha_fin") or "—",
                            fmt(n["valor"]) if n.get("valor") else "—",
                            "✅ Cerrada" if n.get("estado")=="cerrada" else "Activa",
                            (n.get("observaciones") or "")[:40]),
                    tags=self._tag(i))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _form_novedad(self, nov=None):
        nuevo = nov is None
        win = self._win("Nueva Novedad" if nuevo else "Editar Novedad","500x620")
        tk.Label(win, text=f"📅  {'Nueva' if nuevo else 'Editar'} Novedad",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")

        try:    emps=db.listar_empleados(self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=win); return
        if not emps:
            messagebox.showwarning("Sin empleados","Registra empleados primero.",parent=win); return

        tk.Label(win,text="Empleado *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w")
        emp_opts=[f"{e['nombre']}  —  {e['cargo']}" for e in emps]
        cb_emp=Cbo(win,values=emp_opts,state="readonly",width=40)
        cb_emp.pack(padx=24,fill="x",ipady=4,pady=(2,10))
        if nov:
            ids=[e["id"] for e in emps]
            if nov["empleado_id"] in ids: cb_emp.current(ids.index(nov["empleado_id"]))
            cb_emp.config(state="disabled")

        tk.Label(win,text="Tipo de novedad *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w")
        tipos=list(db.TIPOS_NOVEDAD.keys())
        cb_tipo=Cbo(win,values=[db.TIPOS_NOVEDAD[t] for t in tipos],state="readonly",width=40)
        cb_tipo.pack(padx=24,fill="x",ipady=4,pady=(2,10))
        cb_tipo.current(tipos.index(nov["tipo"]) if nov and nov["tipo"] in tipos else 0)

        hoy=datetime.now().strftime("%Y-%m-%d")
        e_ini=self._field(win,"Fecha inicio (YYYY-MM-DD) *",
            val=nov.get("fecha_inicio","") if nov else hoy)
        e_fin=self._field(win,"Fecha fin (YYYY-MM-DD, opcional)",
            val=(nov.get("fecha_fin") or "") if nov else "")
        e_val=self._field(win,"Valor asociado (COP, opcional)",
            val=str(int(nov["valor"])) if nov and nov.get("valor") else "")
        e_obs=self._field(win,"Observaciones",
            val=(nov.get("observaciones") or "") if nov else "")

        le=self._err_lbl(win)
        def save():
            i=cb_emp.current(); ti=cb_tipo.current()
            if i<0: le.config(text="⚠  Selecciona un empleado."); return
            fi=e_ini.get().strip(); ff=e_fin.get().strip() or None
            try: datetime.strptime(fi,"%Y-%m-%d")
            except: le.config(text="⚠  Fecha de inicio inválida (YYYY-MM-DD)."); return
            if ff:
                try: datetime.strptime(ff,"%Y-%m-%d")
                except: le.config(text="⚠  Fecha fin inválida (YYYY-MM-DD)."); return
            valor=None
            vtxt=e_val.get().strip()
            if vtxt:
                try:
                    valor=float(vtxt.replace(".","").replace(",","."))
                    if valor<0: raise ValueError
                except: le.config(text="⚠  Valor inválido."); return
            try:
                if nuevo:
                    ok,msg=db.crear_novedad(self.eid,emps[i]["id"],tipos[ti],fi,
                        ff,valor,e_obs.get().strip() or None)
                else:
                    ok,msg=db.actualizar_novedad(nov["id"],tipos[ti],fi,ff,
                        valor,e_obs.get().strip() or None)
            except Exception as ex: le.config(text=f"Error: {ex}"); return
            if ok: messagebox.showinfo("✓ Éxito",msg,parent=win); win.destroy(); self._load_novedades()
            else:  le.config(text=f"✗  {msg}")
        self._btns(win,save,"Guardar novedad")

    def _edit_novedad(self):
        nid=self._sel(self.tv_nov,"novedad")
        if nid is None: return
        try:
            nov=db.obtener_novedad(nid)
            if not nov: messagebox.showerror("Error","Novedad no encontrada.",parent=self); return
            self._form_novedad(nov)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _cerrar_novedad(self):
        nid=self._sel(self.tv_nov,"novedad")
        if nid is None: return
        try:
            nov=db.obtener_novedad(nid)
            if not nov or nov.get("estado")=="cerrada": return
            ok,msg=db.actualizar_novedad(nid,nov["tipo"],nov["fecha_inicio"],
                nov.get("fecha_fin"),nov.get("valor"),nov.get("observaciones"),
                estado="cerrada")
            if ok: self._load_novedades()
            else: messagebox.showerror("Error",msg,parent=self)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _del_novedad(self):
        nid=self._sel(self.tv_nov,"novedad")
        if nid is None: return
        if not messagebox.askyesno("Confirmar eliminación",
            "¿Eliminar esta novedad?\n⚠ Esta acción no se puede deshacer.",parent=self): return
        try: db.eliminar_novedad(nid); self._load_novedades()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    # ═══ DIAN — NÓMINA ELECTRÓNICA ═══════════════════════════════════════════
    def _s_dian(self):
        self._clr()
        self._hdr("🏛️  Nómina Electrónica — DIAN")
        self._sub("Documento Soporte de Pago mensual por trabajador (Res. 000013/2021). "
                  "Se acumulan todas las nóminas del mes — quincenas incluidas — en un "
                  "solo XML. Transmisión obligatoria dentro de los primeros 10 días "
                  "del mes siguiente.")

        # Config + plazo
        info = tk.Frame(self.ct, bg=C["card"]); info.pack(padx=24, fill="x", pady=(4,10))
        try:
            cfg = db.obtener_config_dian(self.eid)
            ult_per = (db.listar_periodos(self.eid) or [db.obtener_periodo_actual()])[0]
            d_rest = db.dias_plazo_dian(ult_per)
            plazo_txt = (f"⏰ Plazo {ult_per}: {d_rest} día(s) para el 10 del mes siguiente"
                         if d_rest >= 0 else f"⚠ Plazo {ult_per} VENCIDO hace {-d_rest} día(s)")
            plazo_col = C["ok"] if d_rest > 5 else (C["accent"] if d_rest >= 0 else C["err"])
        except Exception as ex:
            cfg = {}; ult_per = "—"; plazo_txt = str(ex); plazo_col = C["err"]
        tk.Label(info, text=f"Prefijo: {cfg.get('prefijo','NE')}   |   "
            f"Próximo consecutivo: {int(cfg.get('consecutivo') or 0)+1}   |   "
            f"Ambiente: {cfg.get('ambiente','habilitacion').upper()}   |   "
            f"Último período: {ult_per}", font=F["bd"], bg=C["card"], fg=C["txt"]
            ).pack(padx=14, pady=(10,2), anchor="w")
        tk.Label(info, text=plazo_txt, font=F["bd"], bg=C["card"], fg=plazo_col
            ).pack(padx=14, pady=(0,10), anchor="w")

        # Acciones de configuración
        fr = tk.Frame(self.ct, bg=C["bg"]); fr.pack(padx=24, anchor="w", pady=(0,8))
        Btn(fr,"Configuración",cmd=self._form_config_dian,sty="out",icon="⚙").pack(side="left",padx=(0,8))
        Btn(fr,"Verificar conectividad",cmd=self._dian_conectividad,sty="out",icon="🌐").pack(side="left",padx=(0,8))
        Btn(fr,"Validar datos empleados",cmd=self._dian_validar,sty="out",icon="🔍").pack(side="left",padx=(0,8))

        # Generación por período
        genf = tk.Frame(self.ct, bg=C["bg"]); genf.pack(padx=24, anchor="w", pady=(0,10))
        tk.Label(genf,text="Período (YYYY-MM):",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(side="left",padx=(0,4))
        self.dian_per = Inp(genf,width=10); self.dian_per.pack(side="left",ipady=5,padx=(0,8))
        self.dian_per.insert(0, ult_per if ult_per != "—" else db.obtener_periodo_actual())
        Btn(genf,"Generar documentos del período",cmd=self._dian_generar,sty="ok",icon="📄").pack(side="left",padx=(0,8))

        self.tv_dian = self._tree(
            ("id","numero","empleado","periodo","tipo","cune","ambiente","estado","envio"),
            [("ID",35),("Número",90),("Empleado",170),("Período",80),("Tipo",70),
             ("CUNE",130),("Ambiente",90),("Estado",90),("Envío",140)])
        fr2 = tk.Frame(self.ct,bg=C["bg"]); fr2.pack(padx=24,anchor="w",pady=(4,18))
        Btn(fr2,"Transmitir",cmd=self._dian_transmitir,sty="pri",icon="📤").pack(side="left",padx=(0,8))
        Btn(fr2,"Marcar aceptado",cmd=lambda:self._dian_marcar("aceptado"),sty="ok",icon="✅").pack(side="left",padx=(0,8))
        Btn(fr2,"Marcar rechazado",cmd=lambda:self._dian_marcar("rechazado"),sty="err",icon="⛔").pack(side="left",padx=(0,8))
        Btn(fr2,"Nota de ajuste",cmd=self._dian_nota_ajuste,sty="out",icon="📝").pack(side="left",padx=(0,8))
        Btn(fr2,"Abrir carpeta XML",cmd=self._dian_abrir_xml,sty="gh",icon="📂").pack(side="left",padx=(0,8))
        self._load_dian()

    def _load_dian(self):
        self.tv_dian.delete(*self.tv_dian.get_children())
        try:
            for i,d in enumerate(db.listar_documentos_dian(self.eid)):
                self.tv_dian.insert("","end",iid=str(d["id"]),
                    values=(d["id"],d.get("numero",""),d.get("empleado_nombre",""),
                            d["periodo"],d["tipo"],
                            (d.get("cune") or "")[:18]+"…" if d.get("cune") else "—",
                            d.get("ambiente",""),d.get("estado",""),
                            d.get("fecha_envio") or "—"),
                    tags=self._tag(i))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _form_config_dian(self):
        try: cfg = db.obtener_config_dian(self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        win = self._win("Configuración DIAN","500x560")
        tk.Label(win, text="⚙  Configuración DIAN", font=F["h3"],
            bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")
        tk.Label(win, text="El PIN y el Software ID se registran ante la DIAN "
            "(o los entrega tu proveedor tecnológico).",
            font=F["sm"],bg=C["bg"],fg=C["sub"],wraplength=450,justify="left"
            ).pack(padx=24,anchor="w",pady=(0,14))
        e_pre=self._field(win,"Prefijo de numeración *",val=cfg.get("prefijo","NE"))
        e_con=self._field(win,"Consecutivo actual *",val=str(int(cfg.get("consecutivo") or 0)))
        e_sid=self._field(win,"Software ID",val=cfg.get("software_id") or "")
        e_pin=self._field(win,"Software PIN",val=cfg.get("software_pin") or "")
        tk.Label(win,text="Ambiente *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w")
        cb_amb=Cbo(win,values=["habilitacion","produccion"],state="readonly",width=30)
        cb_amb.pack(padx=24,fill="x",ipady=4,pady=(2,10))
        cb_amb.set(cfg.get("ambiente","habilitacion"))
        tk.Label(win,text="⚠ Usa 'habilitacion' hasta pasar el set de pruebas DIAN.",
            font=F["sm"],bg=C["bg"],fg=C["accent"],wraplength=450).pack(padx=24,anchor="w")
        le=self._err_lbl(win)
        def save():
            try: cons=int(e_con.get().strip() or "0")
            except: le.config(text="⚠  Consecutivo inválido."); return
            try:
                ok,msg=db.guardar_config_dian(self.eid,e_pre.get().strip() or "NE",
                    cons,cb_amb.get(),e_sid.get().strip() or None,
                    e_pin.get().strip() or None)
            except Exception as ex: le.config(text=f"Error: {ex}"); return
            if ok: messagebox.showinfo("✓ Éxito",msg,parent=win); win.destroy(); self._s_dian()
            else:  le.config(text=f"✗  {msg}")
        self._btns(win,save,"Guardar configuración")

    def _dian_conectividad(self):
        ok,msg=db.verificar_conectividad()
        (messagebox.showinfo if ok else messagebox.showwarning)(
            "Conectividad DIAN",msg,parent=self)

    def _dian_validar(self):
        try: falt = db.empleados_sin_datos_dian(self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not falt:
            messagebox.showinfo("✓ Datos completos",
                "Todos los empleados activos tienen los campos DIAN requeridos.",
                parent=self); return
        detalle="\n\n".join(f"• {n}:\n    {', '.join(f)}" for n,f in falt)
        messagebox.showwarning("Datos DIAN faltantes",
            f"{len(falt)} empleado(s) con campos faltantes:\n\n{detalle}",parent=self)

    def _dian_generar(self):
        per=self.dian_per.get().strip()
        if not re.match(r"^\d{4}-\d{2}$",per):
            messagebox.showerror("Error","Período inválido (YYYY-MM).",parent=self); return
        try:
            n,skips,errs=db.generar_documentos_periodo(self.eid,per)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        msg=f"Se generaron {n} documento(s) del período {per}."
        if skips: msg+=f"\n{len(skips)} omitido(s) (ya existían)."
        if errs:  msg+="\n\nErrores:\n"+"\n".join(errs[:6])
        messagebox.showinfo("Generación DIAN",msg,parent=self)
        self._load_dian()

    def _dian_transmitir(self):
        did=self._sel(self.tv_dian,"documento")
        if did is None: return
        try:
            ok,msg=db.transmitir_documento_dian(did)
            (messagebox.showinfo if ok else messagebox.showerror)(
                "Transmisión DIAN",msg,parent=self)
            self._load_dian()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _dian_marcar(self, estado):
        did=self._sel(self.tv_dian,"documento")
        if did is None: return
        resp=None
        if estado in ("aceptado","rechazado"):
            from tkinter import simpledialog
            resp=simpledialog.askstring("Respuesta DIAN",
                "Respuesta/mensaje de la DIAN (opcional):",parent=self)
            if resp is None: return
        try:
            ok,msg=db.marcar_documento_dian(did,estado,resp)
            if ok: self._load_dian()
            else: messagebox.showerror("Error",msg,parent=self)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _dian_nota_ajuste(self):
        did=self._sel(self.tv_dian,"documento")
        if did is None: return
        from tkinter import simpledialog
        motivo=simpledialog.askstring("Nota de Ajuste",
            "Motivo del ajuste (corrección de valores, fechas, etc.):",parent=self)
        if not motivo: return
        try:
            ok,msg,_id=db.generar_nota_ajuste(did,motivo)
            if ok: messagebox.showinfo("✓ Éxito",msg,parent=self); self._load_dian()
            else: messagebox.showerror("Error",msg,parent=self)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _dian_abrir_xml(self):
        did=self._sel(self.tv_dian,"documento")
        try:
            d=db._dian_dir(self.eid)
            if did is not None:
                doc=db.obtener_documento_dian(did)
                if doc and doc.get("xml_path") and os.path.exists(doc["xml_path"]):
                    os.startfile(doc["xml_path"]); return
            os.startfile(d)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    # ═══ PRESTACIONES SOCIALES ═══════════════════════════════════════════════
    def _s_prestaciones(self):
        self._clr()
        self._hdr("🏖️  Prestaciones Sociales")
        self._sub("Prima (jun/dic), cesantías (dic/feb), intereses de cesantías "
                  "(ene) y vacaciones. Se calculan con los parámetros del período "
                  "y se incluyen en el documento DIAN del mes de pago.")

        fr = tk.Frame(self.ct, bg=C["bg"]); fr.pack(padx=24, anchor="w", pady=(4,10))
        tk.Label(fr, text="Generar:", font=F["h4"], bg=C["bg"], fg=C["sub"]
            ).pack(side="left", padx=(0,10))
        Btn(fr,"Prima",cmd=lambda:self._gen_prest("prima"),sty="out",icon="💵").pack(side="left",padx=(0,8))
        Btn(fr,"Cesantías",cmd=lambda:self._gen_prest("cesantias"),sty="out",icon="🏦").pack(side="left",padx=(0,8))
        Btn(fr,"Intereses cesantías",cmd=lambda:self._gen_prest("intereses_cesantias"),sty="out",icon="📈").pack(side="left",padx=(0,8))
        Btn(fr,"Vacaciones",cmd=lambda:self._gen_prest("vacaciones"),sty="out",icon="🌴").pack(side="left",padx=(0,8))

        self.tv_prest = self._tree(
            ("id","empleado","tipo","periodo","base","dias","valor","estado"),
            [("ID",35),("Empleado",190),("Tipo",160),("Período",90),
             ("Base",100),("Días",50),("Valor",110),("Estado",90)])
        fr2 = tk.Frame(self.ct, bg=C["bg"]); fr2.pack(padx=24, anchor="w", pady=(4,18))
        Btn(fr2,"Marcar pagada",cmd=self._pagar_prest,sty="ok",icon="✅").pack(side="left",padx=(0,8))
        Btn(fr2,"Eliminar",cmd=self._del_prest,sty="err",icon="🗑").pack(side="left",padx=(0,8))
        self._load_prestaciones()

    def _load_prestaciones(self):
        self.tv_prest.delete(*self.tv_prest.get_children())
        try:
            for i,p in enumerate(db.listar_prestaciones(self.eid)):
                self.tv_prest.insert("","end",iid=str(p["id"]),
                    values=(p["id"],p.get("empleado_nombre",""),
                            db.TIPOS_PRESTACION.get(p["tipo"],p["tipo"]),
                            p["periodo"],fmt(p["base"]),p["dias"],
                            fmt(p["valor"]),
                            "✅ Pagada" if p.get("estado")=="pagada" else "Calculada"),
                    tags=self._tag(i))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _gen_prest(self, tipo):
        from tkinter import simpledialog
        hints = {"prima": "Semestre: 2026-1S o 2026-2S",
                 "cesantias": "Año: 2026",
                 "intereses_cesantias": "Año: 2026",
                 "vacaciones": "Mes de pago: 2026-12"}
        per = simpledialog.askstring("Generar prestación",
            f"{db.TIPOS_PRESTACION[tipo]}\n\nPeríodo ({hints[tipo]}):",
            parent=self)
        if not per: return
        try:
            n, errs = db.generar_prestacion(self.eid, tipo, per.strip())
        except Exception as ex:
            messagebox.showerror("Error",str(ex),parent=self); return
        msg = f"Se calcularon {n} registro(s)."
        if errs: msg += "\n\nErrores:\n" + "\n".join(errs[:6])
        messagebox.showinfo("Prestaciones", msg, parent=self)
        self._load_prestaciones()

    def _pagar_prest(self):
        pid = self._sel(self.tv_prest, "prestación")
        if pid is None: return
        try:
            ok,msg = db.marcar_prestacion_pagada(pid)
            if ok: self._load_prestaciones()
            else: messagebox.showerror("Error",msg,parent=self)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _del_prest(self):
        pid = self._sel(self.tv_prest, "prestación")
        if pid is None: return
        if not messagebox.askyesno("Confirmar eliminación",
            "¿Eliminar este registro de prestación?",parent=self): return
        try: db.eliminar_prestacion(pid); self._load_prestaciones()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    # ═══ CONTRATOS ════════════════════════════════════════════════════════════
    def _s_contratos(self):
        self._clr()
        self._hdr("📋  Contratos","Nuevo Contrato",self._form_contrato)
        self._sub("Gestiona contratos de empleados. Al liquidar se genera la colilla y queda en el historial.")

        self.tv_cont=self._tree(
            ("id","empleado","cargo","descripcion","valor","ded","neto","f_inicio","f_fin","estado"),
            [("ID",35),("Empleado",155),("Cargo",110),("Descripción",165),
             ("Valor",100),("Deduc.",90),("Neto",100),("Inicio",80),("Fin",80),("Estado",85)])

        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,10))
        Btn(fr,"⚡ Liquidar contrato",cmd=self._liquidar_contrato,sty="ok").pack(side="left",padx=(0,8))
        Btn(fr,"✏ Editar",          cmd=self._edit_contrato,   sty="out").pack(side="left",padx=(0,8))
        Btn(fr,"🗑 Eliminar",        cmd=self._del_contrato,    sty="err").pack(side="left",padx=(0,8))

        ff=tk.Frame(self.ct,bg=C["bg"]); ff.pack(padx=24,anchor="w",pady=(0,8))
        tk.Label(ff,text="Filtrar:",font=F["h4"],bg=C["bg"],fg=C["sub"]).pack(side="left")
        self._cont_filter=tk.StringVar(value="todos")
        for txt,val in [("Todos","todos"),("Activos","activo"),("Liquidados","liquidado")]:
            tk.Radiobutton(ff,text=txt,variable=self._cont_filter,value=val,
                font=F["bd"],bg=C["bg"],fg=C["txt"],selectcolor=C["card"],
                activebackground=C["bg"],activeforeground=C["txt"],
                command=self._load_contratos).pack(side="left",padx=(8,4))

        self._load_contratos()

    def _load_contratos(self):
        self.tv_cont.delete(*self.tv_cont.get_children())
        try:
            filtro=getattr(self,"_cont_filter",None)
            estado=filtro.get() if filtro and filtro.get()!="todos" else None
            conts=db.listar_contratos(self.eid, estado=estado)
            for i,c in enumerate(conts):
                est=c.get("estado","activo")
                est_txt="✅ Liquidado" if est=="liquidado" else "🟢 Activo"
                neto=float(c.get("valor_total",0))-float(c.get("deducciones",0))
                self.tv_cont.insert("","end",iid=str(c["id"]),
                    values=(c["id"],c.get("empleado_nombre",""),c.get("cargo",""),
                        c.get("descripcion",""),fmt(c.get("valor_total",0)),
                        fmt(c.get("deducciones",0)),fmt(neto),
                        (c.get("fecha_inicio","") or "")[:10],
                        (c.get("fecha_fin","") or "")[:10] or "—",
                        est_txt),
                    tags=self._tag(i, est=="activo"))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _form_contrato(self, cont=None):
        nuevo=cont is None
        win=self._win("Nuevo Contrato" if nuevo else "Editar Contrato","520x600")
        win.resizable(False,True)

        outer=tk.Frame(win,bg=C["bg"]); outer.pack(fill="both",expand=True)
        canvas=tk.Canvas(outer,bg=C["bg"],highlightthickness=0)
        vsb=ttk.Scrollbar(outer,orient="vertical",command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right",fill="y"); canvas.pack(side="left",fill="both",expand=True)
        p=tk.Frame(canvas,bg=C["bg"])
        cwin=canvas.create_window((0,0),window=p,anchor="nw")
        p.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfig(cwin,width=e.width))
        canvas.bind_all("<MouseWheel>",lambda e:canvas.yview_scroll(int(-1*(e.delta/120)),"units"))
        win.bind("<Destroy>",lambda e:canvas.unbind_all("<MouseWheel>"))

        tk.Label(p,text=f"📋  {'Nuevo' if nuevo else 'Editar'} Contrato",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")
        tk.Label(p,text="Los campos * son obligatorios.",
            font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,14))

        try: emps=db.listar_empleados(self.eid)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=win); return
        if not emps:
            messagebox.showwarning("Sin empleados","Registra empleados primero.",parent=win); return

        def fld(lbl,val=""):
            tk.Label(p,text=lbl,font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(4,2))
            e=Inp(p,width=46); e.pack(padx=24,fill="x",ipady=7,pady=(0,6))
            if val: e.insert(0,str(val))
            return e

        tk.Label(p,text="Empleado *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(4,2))
        emp_opts=[f"{e['nombre']}  —  {e['cargo']}" for e in emps]
        cb_emp=Cbo(p,values=emp_opts,state="readonly",width=46)
        cb_emp.pack(padx=24,fill="x",ipady=4,pady=(0,10))
        if cont:
            ids=[e["id"] for e in emps]
            if cont["empleado_id"] in ids: cb_emp.current(ids.index(cont["empleado_id"]))
            cb_emp.config(state="disabled")
        else:
            cb_emp.current(0)

        e_desc  =fld("Descripción del contrato *", cont.get("descripcion","")  if cont else "")
        e_val   =fld("Valor total (COP) *",         str(int(float(cont["valor_total"])))  if cont else "")
        e_ded   =fld("Deducciones (COP)",           str(int(float(cont.get("deducciones",0)))) if cont else "0")
        e_fi    =fld("Fecha inicio (YYYY-MM-DD) *", cont.get("fecha_inicio","") if cont else datetime.now().strftime("%Y-%m-%d"))
        e_ff    =fld("Fecha fin estimada (YYYY-MM-DD)", cont.get("fecha_fin","") if cont else "")
        e_obs   =fld("Observaciones",               cont.get("observaciones","") if cont else "")

        le=tk.Label(p,text="",font=F["sm"],fg=C["err"],bg=C["bg"],wraplength=460)
        le.pack(padx=24,anchor="w")

        def _save():
            idx=cb_emp.current()
            if idx<0: le.config(text="⚠  Selecciona un empleado."); return
            eid_emp=emps[idx]["id"]
            desc=e_desc.val(); val_s=e_val.val(); fi=e_fi.val()
            if not desc: le.config(text="⚠  La descripción es obligatoria."); return
            if not fi:   le.config(text="⚠  La fecha de inicio es obligatoria."); return
            try:
                val=float(val_s.replace(".","").replace(",",".")); assert val>0
            except: le.config(text="⚠  El valor debe ser un número positivo."); return
            try: ded=float(e_ded.val().replace(".","").replace(",",".") or 0)
            except: le.config(text="⚠  Las deducciones deben ser un número."); return
            try: datetime.strptime(fi,"%Y-%m-%d")
            except: le.config(text="⚠  Fecha inicio inválida. Usa YYYY-MM-DD."); return
            ff_val=e_ff.val().strip() or None
            if ff_val:
                try: datetime.strptime(ff_val,"%Y-%m-%d")
                except: le.config(text="⚠  Fecha fin inválida. Usa YYYY-MM-DD."); return
            obs=e_obs.val() or None
            # ── Verificar contratos activos existentes del empleado ────────
            if nuevo:
                conts_emp = [c for c in db.listar_contratos(self.eid)
                             if c["empleado_id"]==eid_emp and c["estado"]=="activo"]
                if conts_emp:
                    resp = messagebox.askyesno("Contrato activo existente",
                        f"Este empleado ya tiene {len(conts_emp)} contrato(s) activo(s).\n\n"
                        "¿Deseas agregar un nuevo contrato de todas formas?\n"
                        "(Elige 'No' para mantener los contratos actuales y cancelar)",
                        parent=win)
                    if not resp:
                        win.destroy(); return

            try:
                if nuevo:
                    ok,msg=db.crear_contrato(self.eid,eid_emp,desc,val,fi,ff_val,ded,obs)
                else:
                    ok,msg=db.actualizar_contrato(cont["id"],self.eid,desc,val,fi,ff_val,ded,obs)
            except Exception as ex: le.config(text=f"Error: {ex}"); return

            if ok:
                db.registrar_bitacora(self.user["id"],self.user["nombre"],
                    "Creó contrato" if nuevo else "Editó contrato",
                    f"{emps[idx]['nombre']} — {desc}",empresa_id=self.eid)
                # ── Preguntar si cambiar modalidad del empleado ─────────────
                if nuevo:
                    emp_sel  = emps[idx]
                    vinc_act = emp_sel.get("tipo_vinculacion","salario")
                    lbl_act  = "Salario fijo" if vinc_act=="salario" else "Prestación de servicios"
                    nombre_emp = emp_sel["nombre"]
                    resp2 = messagebox.askyesno("Modalidad del empleado",
                        f"Contrato creado para {nombre_emp}.\n\n"
                        f"Modalidad actual: {lbl_act}\n\n"
                        "Deseas cambiar su modalidad de vinculacion?\n"
                        "  Si: se abre ventana para elegir nueva modalidad\n"
                        "  No: mantener como esta y asociar contrato aparte",
                        parent=win)
                    if resp2:
                        win.destroy()
                        self._cambiar_modalidad_post_contrato(emp_sel)
                        self._load_contratos()
                        return
                messagebox.showinfo("✓ Contrato creado", msg, parent=win)
                win.destroy(); self._load_contratos()
            else: le.config(text=f"✗  {msg}")

        bf=tk.Frame(p,bg=C["bg"]); bf.pack(pady=(14,24),padx=24,anchor="e")
        Btn(bf,"Cancelar",cmd=win.destroy,sty="gh").pack(side="left",padx=(0,8))
        Btn(bf,"Guardar contrato",cmd=_save,sty="pri",icon="💾").pack(side="left")

    def _liquidar_contrato(self):
        cid=self._sel(self.tv_cont,"contrato")
        if cid is None: return
        try:
            conts=db.listar_contratos(self.eid)
            cont=next((c for c in conts if c["id"]==cid),None)
            if not cont: messagebox.showerror("Error","Contrato no encontrado.",parent=self); return
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return

        if cont.get("estado")=="liquidado":
            messagebox.showinfo("Ya liquidado","Este contrato ya fue liquidado anteriormente.",parent=self); return

        # Verificar préstamos activos del empleado
        try:
            prestamos_activos = db.obtener_prestamos_activos(self.eid, cont["empleado_id"])
            saldo_prestamos = sum(float(p.get("saldo",0)) for p in prestamos_activos)
        except:
            prestamos_activos = []; saldo_prestamos = 0.0

        win=self._win("⚡  Liquidar Contrato","520x520")
        tk.Label(win,text="⚡  Liquidar Contrato",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,4),padx=24,anchor="w")
        tk.Label(win,text=f"Empleado: {cont.get('empleado_nombre','')}",
            font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w")
        tk.Label(win,text=f"Descripción: {cont.get('descripcion','')}",
            font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,12))

        e_val=self._field(win,"Valor final a pagar (COP) *",
            val=str(int(float(cont["valor_total"]))))
        e_ded=self._field(win,"Otras deducciones (COP)",
            val=str(int(float(cont.get("deducciones",0)))))

        # Sección préstamos
        desc_prest_var = tk.BooleanVar(value=saldo_prestamos > 0)
        if saldo_prestamos > 0:
            pf=tk.Frame(win,bg=C["card"],padx=14,pady=10); pf.pack(fill="x",padx=24,pady=(4,8))
            tk.Label(pf,
                text=f"💳  {len(prestamos_activos)} préstamo(s) activo(s) — Saldo total: {fmt(saldo_prestamos)}",
                font=F["bd"],bg=C["card"],fg=C["warn"]).pack(anchor="w")
            tk.Checkbutton(pf,
                text=f"Descontar saldo de préstamos ({fmt(saldo_prestamos)}) de esta liquidación",
                variable=desc_prest_var,font=F["sm"],bg=C["card"],fg=C["txt"],
                selectcolor=C["bg"],activebackground=C["card"],
                command=lambda:_recalc()).pack(anchor="w",pady=(6,0))
        else:
            tk.Label(win,text="✓ Sin préstamos activos",
                font=F["sm"],bg=C["bg"],fg=C["muted"]).pack(padx=24,anchor="w",pady=(0,8))

        # Preview neto
        pv=tk.Label(win,text="",font=F["h3"],bg=C["bg"],fg=C["ok"])
        pv.pack(padx=24,anchor="w",pady=(4,4))

        pv_prest=tk.Label(win,text="",font=F["sm"],bg=C["bg"],fg=C["warn"])
        pv_prest.pack(padx=24,anchor="w")

        def _recalc(*_):
            try:
                v=float(e_val.get().strip().replace(".","").replace(",",".") or 0)
                d=float(e_ded.get().strip().replace(".","").replace(",",".") or 0)
                desc=saldo_prestamos if desc_prest_var.get() else 0
                neto=v-d-desc
                pv.config(text=f"Neto a pagar: {fmt(neto)}")
                if desc>0:
                    pv_prest.config(text=f"  (incluye descuento préstamos: −{fmt(desc)})")
                else:
                    pv_prest.config(text="")
            except: pass

        e_val.bind("<KeyRelease>",_recalc)
        e_ded.bind("<KeyRelease>",_recalc)
        _recalc()

        le=self._err_lbl(win)

        def _confirmar():
            try:
                val=float(e_val.get().strip().replace(".","").replace(",","."))
                ded=float(e_ded.get().strip().replace(".","").replace(",",".") or 0)
            except: le.config(text="⚠  Valores inválidos."); return
            desc=saldo_prestamos if desc_prest_var.get() else 0
            neto=val-ded-desc

            msg_conf=(
                f"¿Liquidar contrato de '{cont.get('empleado_nombre','')}'?\n\n"
                f"Valor contrato:   {fmt(val)}\n"
                f"Deducciones:      {fmt(ded)}\n"
            )
            if desc>0:
                msg_conf+=f"Préstamos:        −{fmt(desc)}\n"
            msg_conf+=(
                f"{'─'*38}\n"
                f"Neto a pagar:     {fmt(neto)}\n\n"
                f"⚠ Esta acción no se puede deshacer."
            )
            if not messagebox.askyesno("Confirmar liquidación",msg_conf,parent=win): return

            try:
                ok,msg,nid=db.liquidar_contrato(
                    cid, self.eid, val, ded,
                    descontar_prestamos=bool(desc_prest_var.get()))
                if not ok: le.config(text=f"✗  {msg}"); return
                nom=db.obtener_nomina(nid,self.eid)
                path=pdf.generar_colilla(nom,empresa_id=self.eid)
                db.registrar_colilla_generada(nid,path)
                # Guardar colilla_path en el contrato
                import sqlite3 as _sq
                _c=_sq.connect(db.DB_PATH); _c.execute(
                    "UPDATE contratos SET colilla_path=? WHERE id=?",(path,cid))
                _c.commit(); _c.close()
                db.registrar_bitacora(self.user["id"],self.user["nombre"],
                    "Liquidó contrato",
                    f"{cont.get('empleado_nombre','')} — {cont.get('descripcion','')}",
                    empresa_id=self.eid)
                messagebox.showinfo("✅ Contrato liquidado",
                    f"Liquidado exitosamente.\n\n"
                    f"Empleado: {cont.get('empleado_nombre','')}\n"
                    f"Neto pagado: {fmt(neto)}\n\n"
                    f"Colilla guardada en:\n{path}",parent=win)
                abrir(path,self)
                win.destroy()
                self._load_contratos()
                # Post-liquidación: qué hacer con el empleado
                self._post_liquidacion(cont)
            except Exception as ex:
                le.config(text=f"Error: {ex}")

        bf=tk.Frame(win,bg=C["bg"]); bf.pack(pady=14,padx=24,fill="x",side="bottom")
        Btn(bf,"Cancelar",cmd=win.destroy,sty="gh").pack(side="left",padx=(0,10))
        Btn(bf,"⚡ Liquidar y generar colilla",cmd=_confirmar,sty="ok").pack(side="left")

    def _cambiar_modalidad_post_contrato(self, emp):
        """Ventana para elegir nueva modalidad del empleado después de crear un contrato."""
        win = self._win("Cambiar modalidad del empleado", "440x240")
        nombre_emp = emp["nombre"]
        tk.Label(win, text=f"Empleado: {nombre_emp}",
            font=F["h3"], bg=C["bg"], fg=C["txt"]).pack(pady=(20,4), padx=24, anchor="w")
        tk.Label(win, text="Elige la nueva modalidad de vinculacion:",
            font=F["sm"], bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(0,14))

        def _aplicar(nuevo_tipo):
            try:
                nueva_tar = emp.get("tarifa_diaria") if nuevo_tipo=="prestacion" else None
                db.actualizar_empleado(emp["id"], self.eid,
                    emp["nombre"], emp["documento"], emp["cargo"],
                    emp["salario"], emp.get("fecha_ingreso"),
                    emp.get("tipo_cuenta"), emp.get("numero_cuenta"),
                    emp.get("edad"), nuevo_tipo, nueva_tar, emp.get("correo"))
                db.registrar_bitacora(self.user["id"], self.user["nombre"],
                    "Cambio modalidad post-contrato",
                    f"{nombre_emp}: salario/prestacion -> {nuevo_tipo}", empresa_id=self.eid)
                messagebox.showinfo("Modalidad actualizada",
                    f"Modalidad de {nombre_emp} cambiada a: "
                    f"{'Salario fijo' if nuevo_tipo=='salario' else 'Prestacion de servicios'}",
                    parent=win)
            except Exception as ex:
                messagebox.showerror("Error", str(ex), parent=win)
            win.destroy()
            self._load_emps()

        bf = tk.Frame(win, bg=C["bg"]); bf.pack(padx=24, fill="x", pady=4)
        Btn(bf, "💼  Cambiar a Salario fijo",
            cmd=lambda: _aplicar("salario"), sty="out").pack(fill="x", pady=4)
        Btn(bf, "🔧  Cambiar a Prestacion de servicios",
            cmd=lambda: _aplicar("prestacion"), sty="out").pack(fill="x", pady=4)
        Btn(bf, "✓  Cancelar (mantener como esta)",
            cmd=win.destroy, sty="gh").pack(fill="x", pady=4)

    def _post_liquidacion(self, cont):
        """Ofrece opciones para el empleado después de liquidar su contrato."""
        emp_id  = cont.get("empleado_id")
        emp_nom = cont.get("empleado_nombre", "el empleado")

        win = self._win("¿Qué hacer con el empleado?", "440x280")
        tk.Label(win,
            text="Contrato liquidado ✅",
            font=F["h3"], bg=C["bg"], fg=C["ok"]).pack(pady=(20,2), padx=24, anchor="w")
        tk.Label(win,
            text=f"Empleado: {emp_nom}",
            font=F["bd"], bg=C["bg"], fg=C["txt"]).pack(padx=24, anchor="w")
        tk.Label(win,
            text="¿Qué deseas hacer con este empleado ahora?",
            font=F["sm"], bg=C["bg"], fg=C["sub"]).pack(padx=24, anchor="w", pady=(8,14))

        def _aplicar(nuevo_tipo):
            try:
                emp = db.obtener_empleado(emp_id, self.eid)
                if not emp:
                    win.destroy(); return
                if nuevo_tipo == "inactivo":
                    # Solo inactivar — mantener tipo_vinculacion actual
                    db.actualizar_empleado(emp["id"], self.eid,
                        emp["nombre"], emp["documento"], emp["cargo"],
                        emp["salario"], emp.get("fecha_ingreso"),
                        emp.get("tipo_cuenta"), emp.get("numero_cuenta"),
                        emp.get("edad"), emp.get("tipo_vinculacion","salario"),
                        emp.get("tarifa_diaria"), emp.get("correo"))
                    try: db.cambiar_estado_empleado(emp_id, self.eid, False)
                    except Exception: pass
                else:
                    # Cambiar modalidad y mantener activo
                    nuevo_sal = emp.get("tarifa_diaria") or emp["salario"] if nuevo_tipo=="prestacion" else emp["salario"]
                    nueva_tar = emp.get("tarifa_diaria") if nuevo_tipo=="prestacion" else None
                    db.actualizar_empleado(emp["id"], self.eid,
                        emp["nombre"], emp["documento"], emp["cargo"],
                        nuevo_sal, emp.get("fecha_ingreso"),
                        emp.get("tipo_cuenta"), emp.get("numero_cuenta"),
                        emp.get("edad"), nuevo_tipo,
                        nueva_tar, emp.get("correo"))
                db.registrar_bitacora(self.user["id"], self.user["nombre"],
                    "Cambió estado post-contrato",
                    f"{emp_nom} → {nuevo_tipo}", empresa_id=self.eid)
            except Exception as ex:
                messagebox.showerror("Error", str(ex), parent=win)
            win.destroy()
            self._load_emps()
            self._load_contratos()

        bf = tk.Frame(win, bg=C["bg"]); bf.pack(padx=24, fill="x", pady=4)
        Btn(bf, "🚫  Inactivar empleado",
            cmd=lambda: _aplicar("inactivo"), sty="err").pack(fill="x", pady=3)
        Btn(bf, "🔧  Cambiar a Prestación de servicios",
            cmd=lambda: _aplicar("prestacion"), sty="out").pack(fill="x", pady=3)
        Btn(bf, "💼  Cambiar a Salario fijo",
            cmd=lambda: _aplicar("salario"), sty="out").pack(fill="x", pady=3)
        Btn(bf, "✓  Mantener como está",
            cmd=win.destroy, sty="gh").pack(fill="x", pady=3)

    def _edit_contrato(self):
        cid=self._sel(self.tv_cont,"contrato")
        if cid is None: return
        try:
            conts=db.listar_contratos(self.eid)
            cont=next((c for c in conts if c["id"]==cid),None)
            if not cont: messagebox.showerror("Error","Contrato no encontrado.",parent=self); return
            if cont.get("estado")=="liquidado":
                messagebox.showinfo("Sin cambios","Un contrato liquidado no puede editarse.",parent=self); return
            self._form_contrato(cont)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _del_contrato(self):
        cid=self._sel(self.tv_cont,"contrato")
        if cid is None: return
        if not messagebox.askyesno("Confirmar eliminación",
            "¿Eliminar este contrato?\n⚠ Solo se pueden eliminar contratos activos.",parent=self): return
        try:
            ok,msg=db.eliminar_contrato(cid,self.eid)
            if ok: self._load_contratos()
            else: messagebox.showwarning("No se puede eliminar",msg,parent=self)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)
    def _s_nomina(self):
        self._clr()
        periodo_pago = self.emp.get("periodo_pago","mensual")
        periodo_txt  = "Quincenal" if periodo_pago=="quincenal" else "Mensual"

        self._hdr("💰  Generar Nómina")
        self._sub(f"Empresa configurada con pago {periodo_txt.lower()}. Solo se muestran empleados de salario fijo.")

        try:
            empleados = db.listar_empleados_por_tipo(self.eid, tipo_vinculacion="salario")
            prestadores = db.listar_empleados_por_tipo(self.eid, tipo_vinculacion="prestacion")
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return

        # ── Banner período de pago con pestañas ─────────────────────────────
        banner = tk.Frame(self.ct,bg=C["accent"],padx=16,pady=8)
        banner.pack(fill="x",padx=24,pady=(0,0))
        tk.Label(banner,
            text=f"🗓  Período de pago: {periodo_txt.upper()}",
            font=F["h4"],bg=C["accent"],fg=C["white"]).pack(side="left")

        # Pestañas: Salario fijo | Prestación de servicios
        tab_fr = tk.Frame(self.ct, bg=C["bg"]); tab_fr.pack(fill="x",padx=24,pady=(8,4))
        self._nom_tab = tk.StringVar(value="salario")

        btn_sal = tk.Button(tab_fr, text="💼 Salario fijo",
            font=F["bd"], relief="sunken", cursor="hand2",
            bg=C["accent"], fg=C["white"], padx=12, pady=6,
            command=lambda:_switch_tab("salario"))
        btn_sal.pack(side="left", padx=(0,4))
        btn_pre = tk.Button(tab_fr, text="🔧 Prestación de servicios",
            font=F["bd"], relief="raised", cursor="hand2",
            bg=C["card"], fg=C["white"], padx=12, pady=6,
            command=lambda:_switch_tab("prestacion"))
        btn_pre.pack(side="left")
        if prestadores:
            tk.Label(tab_fr,
                text=f"  ({len(prestadores)} prestador{'es' if len(prestadores)>1 else ''})",
                font=F["sm"],bg=C["bg"],fg=C["ok"]).pack(side="left",padx=8)

        # ── FRAME SALARIO FIJO ───────────────────────────────────────────────
        frame_sal = tk.Frame(self.ct,bg=C["bg"])

        if not empleados:
            tk.Label(frame_sal,
                text="No hay empleados con salario fijo activos.\nRegistra empleados primero.",
                font=F["h3"],bg=C["bg"],fg=C["sub"]).pack(expand=True,pady=30)
        else:
            # Panel superior: período y quincena
            top=tk.Frame(frame_sal,bg=C["bg"]); top.pack(fill="x",padx=24,pady=(0,10))

            tk.Label(top,text="Período (YYYY-MM) *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(side="left",padx=(0,4))
            self.np=Inp(top,width=10); self.np.pack(side="left",ipady=5,padx=(0,8))
            self.np.insert(0,db.obtener_periodo_actual())
            Btn(top,"📅 Usar mes actual",cmd=self._actualizar_periodo_nom,sty="gh").pack(side="left",padx=(0,12))

            self.nq = None
            if periodo_pago == "quincenal":
                tk.Label(top,text="Quincena:",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(side="left",padx=(0,4))
                self.nq=Cbo(top,values=["1ra quincena","2da quincena"],state="readonly",width=22)
                self.nq.pack(side="left",ipady=4,padx=(0,8))
                self.nq.current(0)

            Btn(top,"🔄 Cargar empleados",cmd=lambda:self._load_nom_emps(empleados,periodo_pago),sty="out").pack(side="left",padx=(0,8))
            Btn(top,"💾 Guardar nómina de TODOS",cmd=lambda:self._save_nom_all(empleados,periodo_pago),sty="ok").pack(side="left")

            self._nom_info=tk.Label(frame_sal,
                text="Presiona 'Cargar empleados' para ver el resumen de nómina del período.",
                font=F["bd"],bg=C["bg"],fg=C["muted"]); self._nom_info.pack(padx=24,anchor="w",pady=(0,6))
            self._nom_err=tk.Label(frame_sal,text="",font=F["sm"],bg=C["bg"],fg=C["err"],wraplength=700)
            self._nom_err.pack(padx=24,anchor="w",pady=(0,4))

            # Tabla de empleados con su nómina
            self.tv_nom=self._tree(
                ("id","empleado","cargo","salario","dias","hextras","bonif","deducciones","prestamo","bruto","neto"),
                [("ID",35),("Empleado",165),("Cargo",100),("Salario",90),
                 ("Días",50),("H.Extras",70),("Bonif.",80),("Otras Deds",90),
                 ("Préstamo",90),("Bruto",100),("Neto a Pagar",110)], parent=frame_sal)

        # Mostrar frame_sal inicialmente
        frame_sal.pack(fill="both",expand=True,pady=(4,0))

        # ── FRAME PRESTACIÓN DE SERVICIOS ────────────────────────────────────
        frame_pre = tk.Frame(self.ct,bg=C["bg"])
        if not prestadores:
            tk.Label(frame_pre,
                text="No hay empleados de prestación de servicios activos.",
                font=F["h3"],bg=C["bg"],fg=C["sub"]).pack(expand=True,pady=30)
        else:
            top_pre=tk.Frame(frame_pre,bg=C["bg"]); top_pre.pack(fill="x",padx=24,pady=(0,10))
            tk.Label(top_pre,text="Período *",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(side="left",padx=(0,4))
            self.np_pre=Inp(top_pre,width=10); self.np_pre.pack(side="left",ipady=5,padx=(0,8))
            self.np_pre.insert(0,db.obtener_periodo_actual())
            Btn(top_pre,"📅 Mes actual",
                cmd=lambda:(self.np_pre.delete(0,tk.END),self.np_pre.insert(0,db.obtener_periodo_actual())),
                sty="gh").pack(side="left",padx=(0,12))
            Btn(top_pre,"🔄 Cargar prestadores",cmd=lambda:self._load_nom_prestadores(prestadores),sty="out").pack(side="left")
            self._pre_info=tk.Label(frame_pre,
                text="Presiona 'Cargar prestadores' para calcular pagos.",
                font=F["bd"],bg=C["bg"],fg=C["muted"]); self._pre_info.pack(padx=24,anchor="w",pady=(0,6))
            self.tv_pre=self._tree(
                ("id","empleado","cargo","tarifa","dias","bonif","ded","prest","neto"),
                [("ID",35),("Empleado",175),("Cargo",130),("Tarifa/día",95),
                 ("Días",50),("Bonif.",90),("Deds.",90),("Préstamo",90),("Neto",110)],
                parent=frame_pre)
            self.tv_pre.bind("<Double-1>",lambda _:self._edit_nom_prestador(prestadores))
            fr_pre=tk.Frame(frame_pre,bg=C["bg"]); fr_pre.pack(padx=24,anchor="w",pady=(4,10))
            Btn(fr_pre,"💾 Registrar pagos de TODOS",
                cmd=lambda:self._save_nom_prestadores(prestadores),sty="ok").pack(side="left")

        # Activar lógica de pestañas ahora que ambos frames existen
        def _switch_tab(t):
            self._nom_tab.set(t)
            for btn,val in [(btn_sal,"salario"),(btn_pre,"prestacion")]:
                btn.config(relief="sunken" if t==val else "raised",
                           bg=C["accent"] if t==val else C["card"], fg=C["white"])
            if t=="salario":
                frame_sal.pack(fill="both",expand=True,pady=(4,0)); frame_pre.pack_forget()
            else:
                frame_pre.pack(fill="both",expand=True,pady=(4,0)); frame_sal.pack_forget()
        btn_sal.config(command=lambda:_switch_tab("salario"))
        btn_pre.config(command=lambda:_switch_tab("prestacion"))

        # Datos temporales para edición por empleado
        self._nom_data = {}

    def _load_nom_emps(self, empleados, periodo_pago):
        """Carga empleados en tabla de nómina con días y salario correctos según período."""
        self.tv_nom.delete(*self.tv_nom.get_children())
        self._nom_data = {}
        quincena = 0
        dias_default = 30
        if periodo_pago == "quincenal" and self.nq:
            quincena = 1 if self.nq.current() == 0 else 2
            dias_default = 15  # quincenal = 15 días base

        for e in empleados:
            prestamos_activos = db.obtener_prestamos_activos(self.eid, e["id"])
            prestamo = sum(float(p["saldo"]) for p in prestamos_activos)

            c = db.calcular_nomina(float(e["salario"]), dias_default, 0, 0, 0, 0,
                periodo_pago=periodo_pago, quincena=quincena, prestamo=prestamo,
                periodo=self.np.get().strip())

            data = {
                "empleado_id": e["id"],
                "salario": float(e["salario"]),
                "dias": dias_default, "hext": 0, "vhr": 0,
                "bon": 0, "ded": 0, "prestamo": prestamo,
                "calc": c
            }
            self._nom_data[e["id"]] = data

            self.tv_nom.insert("","end",iid=str(e["id"]),
                values=(e["id"], e["nombre"], e.get("cargo",""),
                    fmt(float(e["salario"])), str(dias_default), "0", "0", "0",
                    fmt(prestamo), fmt(c["salario_bruto"]), fmt(c["salario_neto"])),
                tags=("activo",))

        total = len(empleados)
        total_neto = sum(d["calc"]["salario_neto"] for d in self._nom_data.values())
        periodo_lbl = f"{'Quincenal' if periodo_pago=='quincenal' else 'Mensual'} ({dias_default} días base)"
        self._nom_info.config(
            text=f"{total} empleados  |  {periodo_lbl}  |  Total neto: {fmt(total_neto)}  |  Doble clic para editar",
            fg=C["ok"])
        self.tv_nom.bind("<Double-1>", lambda _: self._edit_nom_emp(empleados, periodo_pago))

    def _edit_nom_emp(self, empleados, periodo_pago):
        """Permite editar días, horas extras, bonificaciones, deducciones y préstamo de un empleado."""
        eid = self._sel(self.tv_nom, "empleado")
        if eid is None: return
        data = self._nom_data.get(eid)
        if not data: return
        
        win = self._win("Editar Nómina — Empleado","500x790")
        emp = next((e for e in empleados if e["id"]==eid), None)
        if not emp: return
        
        tk.Label(win,text=f"📝  Nómina de {emp['nombre']}",
            font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,2),padx=24,anchor="w")
        tk.Label(win,text=f"Cargo: {emp.get('cargo','')}  |  Salario: {fmt(float(emp['salario']))}",
            font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,14))
        
        e_dias=self._field(win,"Días trabajados *",val=str(data["dias"]))
        e_hext=self._field(win,"Horas extras",val=str(data["hext"]))
        e_vhr=self._field(win,"Valor hora extra (COP)",val=str(data["vhr"]))
        e_bon=self._field(win,"Bonificaciones (COP)",val=str(data["bon"]))
        e_ded=self._field(win,"Otras deducciones (COP)",val=str(data["ded"]))
        
        # Préstamos activos (puede haber varios)
        prestamos_activos = db.obtener_prestamos_activos(self.eid, eid)
        if prestamos_activos:
            saldo_total = sum(float(p.get("saldo",0)) for p in prestamos_activos)
            txt_pres = f"{len(prestamos_activos)} préstamo(s) activo(s) — Saldo total: {fmt(saldo_total)}"
            tk.Label(win,text=txt_pres,font=F["bd"],bg=C["bg"],fg=C["ok"]).pack(padx=24,anchor="w",pady=(8,2))
            e_prest=self._field(win,"Descuento préstamo total (COP)",val=str(int(saldo_total)))
        else:
            tk.Label(win,text="Sin préstamos activos",
                font=F["bd"],bg=C["bg"],fg=C["muted"]).pack(padx=24,anchor="w",pady=(8,2))
            e_prest=self._field(win,"Descuento préstamo (COP)",val="0")
            saldo_total = 0
        
        # Preview
        pv_frame=tk.Frame(win,bg=C["card"]); pv_frame.pack(padx=24,fill="x",pady=(10,4))
        pv_labels={}
        for key,lab in [("bruto","Bruto"),("aux","Aux. transporte"),("salud","Salud"),
                        ("pension","Pensión"),("fsp","FSP"),("ret","Retención"),
                        ("prest","Préstamo"),("deds","Otras Deds"),("neto","NETO")]:
            r=tk.Frame(pv_frame,bg=C["card"]); r.pack(fill="x",pady=1)
            tk.Label(r,text=lab,font=F["bd"],bg=C["card"],fg=C["sub"],width=14,anchor="w").pack(side="left")
            v=tk.Label(r,text="—",font=F["bd"],bg=C["card"],fg=C["txt"],anchor="e"); v.pack(side="right")
            pv_labels[key]=v
        
        quincena = 0
        if periodo_pago == "quincenal" and self.nq:
            quincena = 1 if self.nq.current() == 0 else 2
        
        def recalc(*_):
            try:
                c=db.calcular_nomina(float(emp["salario"]),
                    int(e_dias.get() or 30), float(e_hext.get() or 0),
                    float(e_vhr.get() or 0), float(e_bon.get() or 0),
                    float(e_ded.get() or 0),
                    periodo_pago=periodo_pago, quincena=quincena,
                    prestamo=float(e_prest.get() or 0),
                    periodo=self.np.get().strip())
                pv_labels["bruto"].config(text=fmt(c["salario_bruto"]))
                pv_labels["aux"].config(text=fmt(c["aux_transporte"]) if c['aux_transporte']>0 else "$ 0")
                pv_labels["salud"].config(text=f"−{fmt(c['salud'])}" if c['salud']>0 else "$ 0")
                pv_labels["pension"].config(text=f"−{fmt(c['pension'])}" if c['pension']>0 else "$ 0")
                pv_labels["fsp"].config(text=f"−{fmt(c['fsp'])}" if c['fsp']>0 else "$ 0")
                pv_labels["ret"].config(text=f"−{fmt(c['retencion'])}" if c.get('retencion',0)>0 else "$ 0")
                pv_labels["prest"].config(text=f"−{fmt(c['prestamo'])}" if c['prestamo']>0 else "$ 0")
                pv_labels["deds"].config(text=f"−{fmt(float(e_ded.get() or 0))}" if float(e_ded.get() or 0)>0 else "$ 0")
                pv_labels["neto"].config(text=fmt(c["salario_neto"]))
            except: pass
        
        for w in [e_dias,e_hext,e_vhr,e_bon,e_ded,e_prest]:
            w.bind("<KeyRelease>",recalc)
        recalc()
        
        le=self._err_lbl(win)
        def save():
            try:
                dias=int(e_dias.get() or 30)
                hext=float(e_hext.get() or 0)
                vhr=float(e_vhr.get() or 0)
                bon=float(e_bon.get() or 0)
                ded=float(e_ded.get() or 0)
                prest=float(e_prest.get() or 0)
            except: le.config(text="⚠  Valores inválidos."); return
            if dias<=0 or dias>31: le.config(text="⚠  Días deben ser 1-31."); return
            if prest > 0 and prestamos_activos:
                if prest > saldo_total:
                    le.config(text=f"⚠  Préstamo ({fmt(prest)}) supera saldo total ({fmt(saldo_total)})."); return
            
            # Actualizar datos temporales
            c=db.calcular_nomina(float(emp["salario"]),dias,hext,vhr,bon,ded,
                periodo_pago=periodo_pago,quincena=quincena,prestamo=prest,
                periodo=self.np.get().strip())
            self._nom_data[eid] = {
                "empleado_id": eid, "salario": float(emp["salario"]),
                "dias": dias, "hext": hext, "vhr": vhr,
                "bon": bon, "ded": ded, "prestamo": prest, "calc": c
            }
            # Actualizar fila en tabla
            self.tv_nom.item(str(eid), values=(
                eid, emp["nombre"], emp.get("cargo",""),
                fmt(float(emp["salario"])), str(dias), str(hext),
                fmt(bon), fmt(ded), fmt(prest),
                fmt(c["salario_bruto"]), fmt(c["salario_neto"])))
            # Recalcular total
            total_neto = sum(d["calc"]["salario_neto"] for d in self._nom_data.values())
            self._nom_info.config(
                text=f"{len(self._nom_data)} empleados  |  Total nómina: {fmt(total_neto)}",
                fg=C["ok"])
            messagebox.showinfo("✓ Actualizado",f"Nómina de {emp['nombre']} actualizada.",parent=win)
            win.destroy()
        
        self._btns(win,save,"Actualizar nómina")

    def _save_nom_all(self, empleados, periodo_pago):
        """Guarda la nómina de todos los empleados del período."""
        if not self._nom_data:
            self._nom_err.config(text="⚠  Primero carga los empleados con 'Cargar empleados'."); return
        per = self.np.get().strip()
        if not per: self._nom_err.config(text="⚠  Ingresa el período (YYYY-MM)."); return
        try: datetime.strptime(per,"%Y-%m")
        except: self._nom_err.config(text="⚠  Período con formato incorrecto. Usa YYYY-MM."); return
        
        quincena = 0
        if periodo_pago == "quincenal" and self.nq:
            quincena = 1 if self.nq.current() == 0 else 2
        
        if not messagebox.askyesno("Confirmar nómina",
            f"¿Generar nómina para {len(self._nom_data)} empleados del período {per}?",
            parent=self): return
        
        guardados = 0
        ya_existian = 0
        errores = []
        for eid, d in self._nom_data.items():
            try:
                ok,msg,nid=db.guardar_nomina(self.eid,d["empleado_id"],per,
                    d["dias"],d["hext"],d["vhr"],d["bon"],d["ded"],"",
                    d["salario"],quincena=quincena,prestamo=d["prestamo"],periodo_pago=periodo_pago)
                if ok:
                    guardados += 1
                elif "Ya existe" in msg:
                    ya_existian += 1
                else:
                    errores.append(f"{d['empleado_id']}: {msg}")
            except Exception as ex:
                errores.append(f"{d['empleado_id']}: {ex}")
        
        db.registrar_bitacora(self.user["id"],self.user["nombre"],
            f"Generó nómina período {per}",f"{guardados} empleados",empresa_id=self.eid)

        msg = f"✓ Se guardaron {guardados} nóminas para el período {per}."
        if ya_existian: msg += f"\nℹ {ya_existian} ya existían y no se modificaron."
        if errores: msg += f"\n⚠ Errores ({len(errores)}):\n" + "\n".join(errores[:5])
        messagebox.showinfo("Nómina generada",msg,parent=self)
        
        # Preguntar si generar colillas
        if messagebox.askyesno("Colillas","¿Generar colillas de pago para todos?",parent=self):
            try:
                noms=db.listar_nominas(self.eid,per)
                generados=[]; omitidos=[]
                for n in noms:
                    try:
                        if db.obtener_colilla_path(n["id"]):
                            omitidos.append(n.get("empleado_nombre",""))
                            continue
                        nom=db.obtener_nomina(n["id"],self.eid)
                        path=pdf.generar_colilla(nom,empresa_id=self.eid)
                        db.registrar_colilla_generada(n["id"], path)
                        generados.append(path)
                    except: pass
                msg_col = f"Se generaron {len(generados)} colillas."
                if omitidos: msg_col += f"\n{len(omitidos)} ya existían y no se modificaron."
                messagebox.showinfo("✓ Colillas",msg_col,parent=self)
                abrir(get_reports_dir(self.eid), self)
            except Exception as ex:
                messagebox.showwarning("Colillas",f"Error al generar colillas:\n{ex}",parent=self)
        
        self._nom_data = {}
        self._load_nom_emps(empleados, periodo_pago)

    # ═══ COLILLAS DE PAGO (comprobante individual de cada trabajador) ══════
    def _s_colilla(self):
        self._clr()
        self._hdr("🧾  Colillas de Pago")
        self._sub("Genera colillas de pago para cualquier nómina registrada. Selecciona empleado y período.")

        # Filtros
        ff=tk.Frame(self.ct,bg=C["bg"]); ff.pack(fill="x",padx=24,pady=(0,10))

        tk.Label(ff,text="Empleado:",font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(side="left")
        try:    emps=db.listar_empleados(self.eid,True)
        except: emps=[]
        emp_opts=["Todos los empleados"]+[f"{e['nombre']} — {e['cargo']}" for e in emps]
        self._col_emp_ids=[None]+[e["id"] for e in emps]
        self._col_emp_cb=Cbo(ff,values=emp_opts,state="readonly",width=32)
        self._col_emp_cb.pack(side="left",padx=(6,16),ipady=4)
        self._col_emp_cb.current(0)

        tk.Label(ff,text="Período:",font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(side="left")
        try:
            _, periodos_con_info = db.generar_lista_periodos_con_actual(self.eid)
        except:
            periodos_con_info = [f"{db.obtener_periodo_actual()} 📅 (Actual)"]
        self._col_per_cb=Cbo(ff,values=periodos_con_info,state="readonly",width=14)
        self._col_per_cb.pack(side="left",padx=(6,12),ipady=4)
        # Seleccionar el período actual por defecto
        if "📅 (Actual)" in periodos_con_info[0]:
            self._col_per_cb.current(0)

        Btn(ff,"Filtrar",cmd=self._load_colillas,sty="out",icon="🔍").pack(side="left")

        # Tabla de nóminas disponibles
        self.tv_col=self._tree(
            ("id","periodo","empleado","cargo","dias","bruto","deds","neto","colilla","fecha"),
            [("ID",40),("Período",85),("Empleado",175),("Cargo",145),
             ("Días",50),("Bruto",110),("Deduc.",105),("Neto",110),("Colilla",65),("Generado",100)])
        self.tv_col.bind("<Double-1>",lambda _: self._gen_colilla_sel())

        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,10))
        Btn(fr,"🖨  Generar colilla seleccionada",cmd=self._gen_colilla_sel,sty="pri").pack(side="left",padx=(0,10))
        Btn(fr,"📦  Generar colillas de TODO el período",cmd=self._gen_colillas_periodo,sty="ok").pack(side="left")

        # Indicador
        self._col_info=tk.Label(self.ct,text="",font=F["sm"],bg=C["bg"],fg=C["sub"])
        self._col_info.pack(padx=24,anchor="w",pady=(0,6))

        self._load_colillas()

    def _load_colillas(self):
        self.tv_col.delete(*self.tv_col.get_children())
        emp_idx=self._col_emp_cb.current()
        emp_id=self._col_emp_ids[emp_idx] if emp_idx<len(self._col_emp_ids) else None
        per_raw=self._col_per_cb.get()
        # Limpiar el período para obtener solo YYYY-MM
        if "📅" in per_raw:
            per = per_raw.split(" ")[0]
        else:
            per = None if per_raw == "Todos" else per_raw
        try:    noms=db.listar_nominas(self.eid,per,emp_id)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        for i,n in enumerate(noms):
            ded=float(n.get("salud",0))+float(n.get("pension",0))+float(n.get("prestamo",0))+float(n.get("deducciones",0))
            tiene_colilla = "✅ Sí" if db.obtener_colilla_path(n["id"]) else "⬜ No"
            self.tv_col.insert("","end",iid=str(n["id"]),
                values=(n["id"],n.get("periodo",""),n.get("empleado_nombre",""),
                    n.get("cargo",""),n.get("dias_trabajados",30),
                    fmt(n.get("salario_bruto",0)),fmt(ded),
                    fmt(n.get("salario_neto",0)),tiene_colilla,(n.get("generado_en","") or "")[:10]),
                tags=self._tag(i))
        total=len(noms)
        self._col_info.config(text=f"{total} registro{'s' if total!=1 else ''} encontrado{'s' if total!=1 else ''}.")

    def _gen_colilla_sel(self):
        nid=self._sel(self.tv_col,"nómina")
        if nid is None: return
        try:
            # Verificar si ya existe la colilla por nomina_id
            path_existente = db.obtener_colilla_path(nid)
            if path_existente:
                messagebox.showinfo("Colilla ya generada",
                    f"Esta colilla ya fue generada anteriormente.\n\n"
                    f"Se abrirá el archivo existente:\n{path_existente}", parent=self)
                abrir(path_existente, self)
                return

            nom=db.obtener_nomina(nid,self.eid)
            if not nom: messagebox.showerror("Error","Nómina no encontrada.",parent=self); return
            path=pdf.generar_colilla(nom,empresa_id=self.eid)
            db.registrar_colilla_generada(nid, path)
            messagebox.showinfo("✓ Colilla generada",
                f"Colilla de pago guardada en:\n{path}\n\n"
                f"Empleado: {nom.get('empleado_nombre','')}\n"
                f"Período: {nom.get('periodo','')}\n"
                f"Neto: {fmt(nom.get('salario_neto',0))}",parent=self)
            abrir(path,self)
            self._load_colillas()
        except Exception as ex: messagebox.showerror("Error al generar PDF",str(ex),parent=self)

    def _gen_colillas_periodo(self):
        per=self._col_per_cb.get()
        if "📅" in per:
            per = per.split(" ")[0]
        if per=="Todos":
            messagebox.showwarning("Selecciona un período",
                "Selecciona un período específico para generar colillas masivas.",parent=self); return
        try:    noms=db.listar_nominas(self.eid,per)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not noms:
            messagebox.showwarning("Sin datos",f"No hay nóminas para el período {per}.",parent=self); return

        # Verificar cuántas ya tienen colilla generada
        ya_tienen = [n for n in noms if db.obtener_colilla_path(n["id"])]
        pendientes = [n for n in noms if not db.obtener_colilla_path(n["id"])]

        if not pendientes:
            messagebox.showinfo("Colillas ya generadas",
                f"Todas las colillas del período {per} ya fueron generadas.\n\n"
                f"Total: {len(noms)} colilla(s)\n\n"
                f"Para verlas, busca en la carpeta de reportes.",parent=self)
            abrir(get_reports_dir(self.eid), self)
            return

        msg_conf = f"¿Generar {len(pendientes)} colilla(s) para el período {per}?"
        if ya_tienen:
            msg_conf += f"\n\nℹ {len(ya_tienen)} empleado(s) ya tienen colilla generada y se omitirán."
        if not messagebox.askyesno("Confirmar", msg_conf, parent=self): return

        generados=[]; errores=[]
        for n in pendientes:
            try:
                nom_full=db.obtener_nomina(n["id"],self.eid)
                path=pdf.generar_colilla(nom_full,empresa_id=self.eid)
                db.registrar_colilla_generada(n["id"], path)
                generados.append(nom_full.get("empleado_nombre",""))
            except Exception as ex:
                errores.append(f"{n.get('empleado_nombre','?')}: {ex}")

        msg=f"✓ Se generaron {len(generados)} colilla(s) para el período {per}."
        if ya_tienen: msg+=f"\nℹ {len(ya_tienen)} ya existían y no se modificaron."
        if errores: msg+=f"\n⚠ Errores ({len(errores)}):\n"+"\n".join(errores)
        messagebox.showinfo("Colillas generadas",msg,parent=self)
        self._load_colillas()
        abrir(get_reports_dir(self.eid), self)

    # ═══ HISTORIAL NÓMINA ═════════════════════════════════════════════════════
    def _s_hist(self):
        self._clr()
        self._hdr("📋  Historial de Nómina")
        self._sub("Consulta y gestiona nóminas generadas. Doble clic para colilla PDF.")
        ff=tk.Frame(self.ct,bg=C["bg"]); ff.pack(fill="x",padx=24,pady=(0,6))
        tk.Label(ff,text="Período:",font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(side="left")
        try:
            _, periodos_con_info = db.generar_lista_periodos_con_actual(self.eid)
        except:
            periodos_con_info = [f"{db.obtener_periodo_actual()} 📅 (Actual)"]
        self._hc=Cbo(ff,values=["Todos"]+periodos_con_info,state="readonly",width=18)
        self._hc.pack(side="left",padx=8,ipady=4)
        if periodos_con_info and "📅 (Actual)" in periodos_con_info[0]:
            self._hc.current(1)
        else:
            self._hc.current(0)

        tk.Label(ff,text="🔍 Empleado:",font=F["bd"],bg=C["bg"],fg=C["sub"]).pack(side="left",padx=(16,4))
        self._hist_search=Inp(ff,ph="Buscar por nombre...",width=28)
        self._hist_search.pack(side="left",ipady=5)
        self._hist_search.bind("<KeyRelease>",lambda _: self._load_hist())

        Btn(ff,"Filtrar",cmd=self._load_hist,sty="out",icon="🔍").pack(side="left",padx=(8,0))

        self.tv_hist=self._tree(
            ("id","per","emp","car","dias","hext","bruto","deds","neto","fecha"),
            [("ID",40),("Período",85),("Empleado",185),("Cargo",155),
             ("Días",50),("H.Extra",60),("Bruto",115),("Deduc.",110),("Neto",115),("Generado",105)])
        self.tv_hist.bind("<Double-1>",lambda _: self._colilla_hist())

        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,18))
        Btn(fr,"Generar colilla PDF",cmd=self._colilla_hist,sty="pri",icon="📄").pack(side="left",padx=(0,8))
        Btn(fr,"Eliminar registro",  cmd=self._del_nom,    sty="err",icon="🗑").pack(side="left",padx=(0,8))
        Btn(fr,"Limpiar duplicados", cmd=self._limpiar_duplicados_hist, sty="wa",icon="🧹").pack(side="left")
        # Leyenda contratos liquidados
        tk.Label(self.ct,text="  🟣 Morado = Liquidación de contrato",
            font=F["sm"],bg=C["bg"],fg="#A78BFA").pack(padx=24,anchor="w",pady=(0,6))
        self.tv_hist.tag_configure("contrato",background="#1E293B",foreground="#A78BFA")
        self._load_hist()

    def _load_hist(self):
        self.tv_hist.delete(*self.tv_hist.get_children())
        per_raw=self._hc.get()
        if "📅" in per_raw:
            per = per_raw.split(" ")[0]
        elif per_raw == "Todos":
            per = None
        else:
            per = per_raw
        search = self._hist_search.val().lower() if hasattr(self,"_hist_search") else ""
        try:    noms=db.listar_nominas(self.eid,per)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if search:
            noms = [n for n in noms if search in n.get("empleado_nombre","").lower()]
        for i,n in enumerate(noms):
            ded=float(n.get("salud",0))+float(n.get("pension",0))+float(n.get("prestamo",0))+float(n.get("deducciones",0))
            # quincena=99 indica liquidación de contrato
            es_contrato = n.get("quincena",0)==99
            self.tv_hist.insert("","end",iid=str(n["id"]),
                values=(n["id"],n.get("periodo",""),n.get("empleado_nombre",""),
                    n.get("cargo",""),n.get("dias_trabajados",30),n.get("horas_extras",0),
                    fmt(n.get("salario_bruto",0)),fmt(ded),
                    fmt(n.get("salario_neto",0)),(n.get("generado_en","") or "")[:10]),
                tags=("contrato",) if es_contrato else self._tag(i))

    def _limpiar_duplicados_hist(self):
        if not messagebox.askyesno("Limpiar duplicados",
            "Esto eliminará nóminas duplicadas, conservando solo la más reciente "
            "por cada empleado y período.\n\n¿Continuar?", parent=self): return
        try:
            eliminados = db.limpiar_nominas_duplicadas(self.eid)
            if eliminados > 0:
                messagebox.showinfo("✓ Limpieza completada",
                    f"Se eliminaron {eliminados} nómina(s) duplicada(s).\n"
                    f"Solo se conservó la más reciente por empleado y período.", parent=self)
            else:
                messagebox.showinfo("Sin duplicados",
                    "No se encontraron nóminas duplicadas.", parent=self)
            self._load_hist()
        except Exception as ex:
            messagebox.showerror("Error", str(ex), parent=self)

    def _colilla_hist(self):
        nid=self._sel(self.tv_hist,"nómina")
        if nid is None: return
        try:
            # Verificar si ya tiene colilla generada
            path_existente = db.obtener_colilla_path(nid)
            if path_existente:
                messagebox.showinfo("Colilla ya generada",
                    f"Esta colilla ya fue generada anteriormente.\n\n"
                    f"Se abrirá el archivo existente:\n{path_existente}", parent=self)
                abrir(path_existente, self)
                return
            nom=db.obtener_nomina(nid,self.eid)
            if not nom: messagebox.showerror("Error","Nómina no encontrada.",parent=self); return
            path=pdf.generar_colilla(nom,empresa_id=self.eid)
            db.registrar_colilla_generada(nid, path)
            messagebox.showinfo("✓ PDF generado",f"Colilla guardada en:\n{path}",parent=self)
            abrir(path,self)
        except Exception as ex: messagebox.showerror("Error al generar PDF",str(ex),parent=self)

    def _del_nom(self):
        nid=self._sel(self.tv_hist,"nómina")
        if nid is None: return
        if not messagebox.askyesno("Confirmar eliminación",
            "¿Eliminar este registro de nómina?\n⚠ Esta acción no se puede deshacer.",
            parent=self): return
        try:   db.eliminar_nomina(nid,self.eid); self._load_hist()
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    # ═══ REPORTES PDF ═════════════════════════════════════════════════════════
    def _s_rep(self):
        self._clr()
        self._hdr("📄  Reportes PDF")
        self._sub("Genera reportes consolidados de nómina por período.")
        card=Card(self.ct,padx=24,pady=20); card.pack(fill="x",padx=24,pady=(0,18))
        tk.Label(card,text="Período de nómina *",font=F["h4"],bg=C["card"],fg=C["sub"]).pack(anchor="w")
        try:    
            periodos_ordenados, periodos_con_info = db.generar_lista_periodos_con_actual(self.eid)
        except: 
            periodos_ordenados = [db.obtener_periodo_actual()]
            periodos_con_info = [f"{db.obtener_periodo_actual()} 📅 (Actual)"]
        if periodos_ordenados:
            self._rc=Cbo(card,values=periodos_con_info,state="readonly",width=24)
            self._rc.pack(anchor="w",ipady=4,pady=(4,8))
            # Seleccionar el período actual por defecto
            if "📅 (Actual)" in periodos_con_info[0]:
                self._rc.current(0)
            bf=tk.Frame(card,bg=C["card"]); bf.pack(anchor="w",pady=(4,8))
            Btn(bf,"Generar Reporte PDF",cmd=self._gen_rep,sty="pri",icon="📄").pack(side="left",padx=(0,10))
            Btn(bf,"Exportar Excel",cmd=self._gen_excel,sty="ok",icon="📊").pack(side="left")
        else:
            tk.Label(card,text="No hay nóminas registradas aún. Genera nóminas primero.",
                font=F["bd"],bg=C["card"],fg=C["muted"]).pack(anchor="w",pady=12)

        tk.Label(self.ct,text="Archivos generados",font=F["h3"],
            bg=C["bg"],fg=C["txt"]).pack(padx=24,anchor="w",pady=(8,8))
        ff=tk.Frame(self.ct,bg=C["bg"]); ff.pack(fill="both",expand=True,padx=24,pady=(0,8))
        self.tv_rep=ttk.Treeview(ff,columns=("nom","ruta"),show="headings",selectmode="browse")
        self.tv_rep.heading("nom",text="Archivo"); self.tv_rep.heading("ruta",text="Ruta completa")
        self.tv_rep.column("nom",width=320); self.tv_rep.column("ruta",width=480)
        self.tv_rep.pack(fill="both",expand=True)
        self.tv_rep.tag_configure("odd",background=C["card"]); self.tv_rep.tag_configure("even",background="#253045")
        self.tv_rep.bind("<Double-1>",lambda _: self._abrir_rep())
        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,20))
        Btn(fr,"Abrir PDF",       cmd=self._abrir_rep,  sty="out",icon="📂").pack(side="left",padx=(0,8))
        Btn(fr,"Eliminar PDF",    cmd=self._del_rep,    sty="err",icon="🗑").pack(side="left",padx=(0,8))
        Btn(fr,"Actualizar lista",cmd=self._load_reps,  sty="gh", icon="🔄").pack(side="left")
        self._load_reps()

    def _load_reps(self):
        self.tv_rep.delete(*self.tv_rep.get_children())
        try:
            emp_dir = get_reports_dir(self.eid)
            files=sorted([f for f in os.listdir(emp_dir) if f.endswith(".pdf")],reverse=True)
            for i,f in enumerate(files):
                tag="odd" if i%2 else "even"
                self.tv_rep.insert("","end",values=(f,os.path.join(emp_dir,f)),tags=(tag,))
        except Exception as ex:
            messagebox.showwarning("Aviso",f"No se pudo listar reportes:\n{ex}",parent=self)

    def _gen_rep(self):
        per=self._rc.get()
        # Limpiar el período para obtener solo YYYY-MM
        if "📅" in per:
            per = per.split(" ")[0]
        try:    noms=db.listar_nominas(self.eid,per)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not noms:
            messagebox.showwarning("Sin datos",f"No hay nóminas para {per}.",parent=self); return
        try:
            path=pdf.generar_reporte_nomina(self.emp,noms,per,empresa_id=self.eid)
            messagebox.showinfo("✓ PDF generado",f"Reporte guardado en:\n{path}",parent=self)
            self._load_reps(); abrir(path,self)
        except Exception as ex: messagebox.showerror("Error al generar PDF",str(ex),parent=self)

    def _gen_excel(self):
        from tkinter import filedialog
        per=self._rc.get()
        if "📅" in per:
            per = per.split(" ")[0]
        try:    noms=db.listar_nominas(self.eid,per)
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return
        if not noms:
            messagebox.showwarning("Sin datos",f"No hay nóminas para {per}.",parent=self); return
        ts=datetime.now().strftime("%Y%m%d_%H%M%S")
        emp_name=self.emp.get("nombre","Empresa").replace(" ","_")
        ruta=filedialog.asksaveasfilename(
            parent=self, title="Guardar Excel de nómina",
            defaultextension=".xlsx",
            initialfile=f"Nomina_{emp_name}_{per}_{ts}.xlsx",
            filetypes=[("Excel","*.xlsx"),("Todos","*.*")])
        if not ruta: return
        try:
            db.exportar_nomina_excel(self.eid, per, ruta)
            messagebox.showinfo("✓ Excel generado",
                f"Nómina del período {per} exportada a:\n{ruta}\n\n"
                f"Total empleados: {len(noms)}",parent=self)
            abrir(ruta,self)
        except Exception as ex:
            messagebox.showerror("Error al generar Excel",str(ex),parent=self)


    def _load_nom_prestadores(self, prestadores):
        """Carga prestadores en la tabla con dias=0 para editar manualmente."""
        if not hasattr(self,'tv_pre'): return
        self.tv_pre.delete(*self.tv_pre.get_children())
        self._pre_data = {}
        for e in prestadores:
            tarifa = float(e.get("tarifa_diaria") or e.get("salario") or 0)
            prestamos = db.obtener_prestamos_activos(self.eid, e["id"])
            saldo_prest = sum(float(p["saldo"]) for p in prestamos)
            self._pre_data[e["id"]] = {"empleado_id":e["id"],"tarifa":tarifa,
                "dias":0,"bonif":0,"ded":0,"prestamo":saldo_prest}
            self.tv_pre.insert("","end",iid=str(e["id"]),
                values=(e["id"],e["nombre"],e.get("cargo",""),
                    fmt(tarifa),"0","0","0",fmt(saldo_prest),"$ 0"),tags=("activo",))
        self._pre_info.config(
            text=f"{len(prestadores)} prestador(es) — Doble clic para ingresar dias trabajados",
            fg=C["ok"])

    def _edit_nom_prestador(self, prestadores):
        if not hasattr(self,'tv_pre'): return
        sel = self.tv_pre.selection()
        if not sel: messagebox.showwarning("Aviso","Selecciona un prestador.",parent=self); return
        eid_emp = int(sel[0])
        emp = next((e for e in prestadores if e["id"]==eid_emp), None)
        if not emp: return
        data   = self._pre_data.get(eid_emp, {})
        tarifa = data.get("tarifa", float(emp.get("tarifa_diaria") or 0))
        pres   = db.obtener_prestamos_activos(self.eid, eid_emp)
        saldo_prest = sum(float(p["saldo"]) for p in pres)

        win = self._win(f"Dias — {emp['nombre']}","420x340")
        tk.Label(win,text=f"Prestador: {emp['nombre']}",font=F["h3"],bg=C["bg"],fg=C["txt"]).pack(pady=(20,4),padx=24,anchor="w")
        tk.Label(win,text=f"Tarifa: {fmt(tarifa)}/dia",font=F["sm"],bg=C["bg"],fg=C["sub"]).pack(padx=24,anchor="w",pady=(0,12))
        e_dias = self._field(win,"Dias trabajados *",val=str(data.get("dias",0)))
        e_bon  = self._field(win,"Bonificaciones (COP)",val=str(int(data.get("bonif",0))))
        e_ded  = self._field(win,"Deducciones adicionales (COP)",val=str(int(data.get("ded",0))))
        desc_prest = tk.BooleanVar(value=bool(saldo_prest))
        if pres:
            tk.Checkbutton(win,text=f"Descontar prestamo ({fmt(saldo_prest)})",
                variable=desc_prest,font=F["bd"],bg=C["bg"],fg=C["txt"],
                selectcolor=C["card"],activebackground=C["bg"]).pack(padx=24,anchor="w")
        pv=tk.Label(win,text="Neto: $0",font=F["h3"],bg=C["bg"],fg=C["ok"])
        pv.pack(padx=24,anchor="w",pady=(8,0))
        def _recalc(*_):
            try:
                d=int(e_dias.get().strip() or 0); b=float(e_bon.get().strip().replace(".","").replace(",",".") or 0)
                dd=float(e_ded.get().strip().replace(".","").replace(",",".") or 0)
                p=saldo_prest if desc_prest.get() else 0
                neto=tarifa*d+b-dd-p
                pv.config(text=f"Neto: {fmt(neto)}",fg=C["ok"] if neto>=0 else C["err"])
            except: pass
        e_dias.bind("<KeyRelease>",_recalc); e_bon.bind("<KeyRelease>",_recalc); e_ded.bind("<KeyRelease>",_recalc)
        if pres: desc_prest.trace_add("write",_recalc)
        _recalc()
        def _ok():
            try: dias=int(e_dias.get().strip()); assert dias>=0
            except: messagebox.showwarning("Aviso","Ingresa los dias trabajados.",parent=win); return
            bon=float(e_bon.get().strip().replace(".","").replace(",",".") or 0)
            ded=float(e_ded.get().strip().replace(".","").replace(",",".") or 0)
            p=saldo_prest if desc_prest.get() else 0
            self._pre_data[eid_emp].update({"dias":dias,"bonif":bon,"ded":ded,"prestamo":p})
            neto=tarifa*dias+bon-ded-p
            self.tv_pre.item(str(eid_emp),values=(eid_emp,emp["nombre"],emp.get("cargo",""),
                fmt(tarifa),str(dias),fmt(bon),fmt(ded),fmt(p),fmt(neto)))
            win.destroy()
        bf=tk.Frame(win,bg=C["bg"]); bf.pack(pady=12,padx=24,anchor="e")
        Btn(bf,"Cancelar",cmd=win.destroy,sty="gh").pack(side="left",padx=(0,8))
        Btn(bf,"Confirmar",cmd=_ok,sty="pri").pack(side="left")

    def _save_nom_prestadores(self, prestadores):
        if not hasattr(self,'_pre_data') or not self._pre_data:
            messagebox.showwarning("Aviso","Primero carga los prestadores.",parent=self); return
        per=self.np_pre.get().strip() if hasattr(self,'np_pre') else db.obtener_periodo_actual()
        try: datetime.strptime(per,"%Y-%m")
        except: messagebox.showerror("Error","Periodo invalido. Usa YYYY-MM.",parent=self); return
        pendientes=[(eid_e,d) for eid_e,d in self._pre_data.items() if d.get("dias",0)>0]
        if not pendientes:
            messagebox.showwarning("Sin dias","Ingresa los dias de al menos un prestador.",parent=self); return
        if not messagebox.askyesno("Confirmar",
            f"Registrar pago de {len(pendientes)} prestador(es) para {per}?",parent=self): return
        guardados=0; ya_existen=0; errores=[]
        for eid_e,d in pendientes:
            ok,msg,nid=db.guardar_nomina_prestacion(
                self.eid,eid_e,per,d["tarifa"],d["dias"],
                d.get("bonif",0),d.get("ded",0),d.get("prestamo",0))
            if ok: guardados+=1
            elif "Ya existe" in msg: ya_existen+=1
            else: errores.append(msg)
        db.registrar_bitacora(self.user["id"],self.user["nombre"],
            f"Registro pagos prestacion {per}",f"{guardados} prestadores",empresa_id=self.eid)
        msg_f=f"Se registraron {guardados} pagos para {per}."
        if ya_existen: msg_f+=f"\n{ya_existen} ya existian."
        if errores: msg_f+="\nErrores:\n"+"; ".join(errores[:3])
        messagebox.showinfo("Pagos registrados",msg_f,parent=self)

    def _actualizar_periodo_nom(self):
        """Actualiza el campo de período al mes actual."""
        periodo_actual = db.obtener_periodo_actual()
        self.np.delete(0, tk.END)
        self.np.insert(0, periodo_actual)
        messagebox.showinfo("✓ Período Actualizado", f"Período establecido a: {periodo_actual}", parent=self)

    def _s_liquidar_contrato(self):
        """Pantalla para liquidar el pago final de un empleado por contrato."""
        self._clr()
        self._hdr("📋  Liquidar Contrato")
        self._sub("Genera la nómina y colilla de pago al finalizar un contrato.")

        try:
            contratos = db.listar_empleados_por_tipo(self.eid, tipo_vinculacion="contrato")
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self); return

        if not contratos:
            tk.Label(self.ct,text="No hay empleados por contrato activos.",
                font=F["h3"],bg=C["bg"],fg=C["sub"]).pack(expand=True)
            Btn(self.ct,"← Volver a Nómina",cmd=lambda:self._sec("nomina"),sty="gh").pack(pady=10)
            return

        card=tk.Frame(self.ct,bg=C["card"],padx=24,pady=20)
        card.pack(fill="x",padx=24,pady=(0,10))

        def lbl(t): tk.Label(card,text=t,font=F["sm"],bg=C["card"],fg=C["sub"]).pack(anchor="w",pady=(8,2))
        def inp(w=30, val=""):
            e=Inp(card,width=w); e.pack(fill="x",ipady=6,pady=(0,4))
            if val: e.insert(0,val)
            return e

        lbl("Empleado *")
        emp_opts=[f"{e['nombre']}  —  {e['cargo']}  |  Contrato: {fmt(e.get('valor_contrato') or 0)}" for e in contratos]
        cb_emp=Cbo(card,values=emp_opts,state="readonly",width=70)
        cb_emp.pack(fill="x",ipady=4,pady=(0,8))
        cb_emp.current(0)

        lbl("Período de liquidación (YYYY-MM) *")
        e_per=inp(14, db.obtener_periodo_actual())

        lbl("Valor total del contrato (COP) *")
        e_val=inp(22)

        lbl("Deducciones (COP)")
        e_ded=inp(22,"0")

        lbl("Observaciones")
        e_obs=inp(60,"Liquidación final de contrato")

        pv=tk.Label(card,text="Neto a pagar: $ 0",font=F["h3"],bg=C["card"],fg=C["ok"])
        pv.pack(anchor="w",pady=(10,4))

        le=tk.Label(card,text="",font=F["sm"],fg=C["err"],bg=C["card"],wraplength=600)
        le.pack(anchor="w",pady=(0,8))

        bf=tk.Frame(card,bg=C["card"]); bf.pack(anchor="w",pady=(8,4))
        Btn(bf,"← Volver",cmd=lambda:self._sec("nomina"),sty="gh").pack(side="left",padx=(0,12))
        Btn(bf,"💾  Generar liquidación y colilla",cmd=lambda:_liquidar(),sty="ok").pack(side="left")

        # ── Funciones definidas DESPUÉS de los widgets ──────────────────────
        def _recalc(*_):
            try:
                val=float(e_val.get().strip().replace(".","").replace(",",".") or 0)
                ded=float(e_ded.get().strip().replace(".","").replace(",",".") or 0)
                pv.config(text=f"Neto a pagar: {fmt(val-ded)}")
            except: pass

        def _sel_emp(*_):
            idx=cb_emp.current()
            if idx>=0:
                vc=contratos[idx].get("valor_contrato") or 0
                e_val.delete(0,tk.END)
                e_val.insert(0,str(int(vc)) if vc else "")
                _recalc()

        def _liquidar():
            idx=cb_emp.current()
            if idx<0: le.config(text="⚠  Selecciona un empleado."); return
            emp_c=contratos[idx]
            per=e_per.get().strip()
            val_str=e_val.get().strip()
            ded_str=e_ded.get().strip()
            if not per: le.config(text="⚠  El período es obligatorio."); return
            try: datetime.strptime(per,"%Y-%m")
            except: le.config(text="⚠  Período inválido. Usa YYYY-MM."); return
            try:
                val=float(val_str.replace(".","").replace(",","."))
                if val<=0: raise ValueError
            except: le.config(text="⚠  El valor del contrato es obligatorio y positivo."); return
            try: ded=float(ded_str.replace(".","").replace(",",".") or 0)
            except: le.config(text="⚠  Deducción inválida."); return

            if not messagebox.askyesno("Confirmar liquidación",
                f"¿Liquidar contrato de '{emp_c['nombre']}'?\n\n"
                f"Valor contrato:  {fmt(val)}\n"
                f"Deducciones:     {fmt(ded)}\n"
                f"Neto a pagar:    {fmt(val-ded)}\n\n"
                f"Período: {per}",parent=self): return
            try:
                ok,msg,nid=db.guardar_nomina_contrato(
                    self.eid,emp_c["id"],per,val,ded,e_obs.get().strip())
                if not ok:
                    le.config(text=f"✗  {msg}"); return
                db.registrar_bitacora(self.user["id"],self.user["nombre"],
                    "Liquidó contrato",f"{emp_c['nombre']} — {per}",empresa_id=self.eid)
                nom=db.obtener_nomina(nid,self.eid)
                path=pdf.generar_colilla(nom,empresa_id=self.eid)
                db.registrar_colilla_generada(nid,path)
                messagebox.showinfo("✓ Contrato liquidado",
                    f"Liquidación generada exitosamente.\n\n"
                    f"Empleado: {emp_c['nombre']}\n"
                    f"Neto pagado: {fmt(val-ded)}\n\n"
                    f"Colilla guardada en:\n{path}",parent=self)
                abrir(path,self)
                self._sec("nomina")
            except Exception as ex:
                le.config(text=f"Error: {ex}")

        # Conectar eventos después de definir funciones
        cb_emp.bind("<<ComboboxSelected>>",_sel_emp)
        e_val.bind("<KeyRelease>",_recalc)
        e_ded.bind("<KeyRelease>",_recalc)
        _sel_emp()  # cargar valores iniciales

    def _gen_rep_empleados(self):
        try:
            # Obtener información de la empresa
            empresa = db.obtener_empresa(self.eid)
            if not empresa:
                messagebox.showerror("Error", "No se pudo obtener información de la empresa.", parent=self)
                return
            
            # Obtener lista de empleados según la opción seleccionada
            incluir_inactivos = self._chk_inactivos.get() if hasattr(self, '_chk_inactivos') else True
            empleados = db.obtener_reporte_empleados(self.eid, incluir_inactivos=incluir_inactivos)
            
            if not empleados:
                tipo_msg = "activos" if not incluir_inactivos else "registrados"
                messagebox.showwarning("Sin datos", f"No hay empleados {tipo_msg}.", parent=self)
                return
            
            # Generar PDF
            path = pdf.generar_reporte_empleados(empleados, empresa, empresa_id=self.eid)
            
            # Contar activos e inactivos para el mensaje
            activos = sum(1 for e in empleados if e.get("activo", 1))
            inactivos = len(empleados) - activos
            
            msg_tipo = " (solo activos)" if not incluir_inactivos else " (incluyendo inactivos)"
            messagebox.showinfo("✓ PDF generado", 
                f"Reporte de empleados guardado en:\n{path}\n\n"
                f"Total empleados{msg_tipo}: {len(empleados)}\n"
                f"Activos: {activos}" + (f"\nInactivos: {inactivos}" if incluir_inactivos else ""), 
                parent=self)
            self._load_reps()
            abrir(path, self)
            
        except Exception as ex:
            messagebox.showerror("Error al generar PDF", str(ex), parent=self)

    def _abrir_rep(self):
        sel=self.tv_rep.selection()
        if not sel: messagebox.showwarning("Sin selección","Selecciona un archivo PDF.",parent=self); return
        abrir(self.tv_rep.item(sel[0])["values"][1],self)

    def _del_rep(self):
        sel=self.tv_rep.selection()
        if not sel: 
            messagebox.showwarning("Sin selección","Selecciona un archivo PDF para eliminar.",parent=self); return
        
        item = self.tv_rep.item(sel[0])
        filename = item["values"][0]
        filepath = item["values"][1]
        
        if not messagebox.askyesno("Confirmar eliminación",
            f"¿Eliminar el archivo '{filename}'?\n\n"
            "⚠ Esta acción eliminará permanentemente el archivo PDF.\n"
            "Esta acción NO se puede deshacer.",
            parent=self): return
        
        try:
            os.remove(filepath)
            messagebox.showinfo("✓ Eliminado", f"Archivo '{filename}' eliminado correctamente.", parent=self)
            self._load_reps()
        except FileNotFoundError:
            messagebox.showerror("Error", f"El archivo '{filename}' no existe.", parent=self)
            self._load_reps()
        except PermissionError:
            messagebox.showerror("Error", f"No se tiene permiso para eliminar '{filename}'.\nEl archivo puede estar abierto.", parent=self)
        except Exception as ex:
            messagebox.showerror("Error", f"Error al eliminar '{filename}':\n{str(ex)}", parent=self)

    # ═══ BITÁCORA DE AUDITORÍA ═════════════════════════════════════════════════
    def _s_bitacora(self):
        self._clr()
        self._hdr("📜  Bitácora de Auditoría")
        self._sub("Registro de acciones realizadas en el sistema.")
        self.tv_bit=self._tree(
            ("id","fecha","usuario","accion","detalle"),
            [("ID",40),("Fecha",150),("Usuario",140),("Acción",180),("Detalle",340)])
        fr=tk.Frame(self.ct,bg=C["bg"]); fr.pack(padx=24,anchor="w",pady=(4,10))
        Btn(fr,"Actualizar",cmd=self._load_bitacora,sty="out",icon="🔄").pack(side="left",padx=(0,8))
        Btn(fr,"Exportar PDF",cmd=self._gen_pdf_bitacora,sty="pri",icon="📄").pack(side="left")
        self._load_bitacora()

    def _load_bitacora(self):
        self.tv_bit.delete(*self.tv_bit.get_children())
        try:
            registros=db.listar_bitacora(200,empresa_id=self.eid)
            for i,r in enumerate(registros):
                self.tv_bit.insert("","end",iid=str(r["id"]),
                    values=(r["id"],(r.get("fecha","") or "")[:19],
                        r.get("usuario_nom","—"),r.get("accion",""),
                        r.get("detalle","") or ""),
                    tags=self._tag(i))
        except Exception as ex: messagebox.showerror("Error",str(ex),parent=self)

    def _gen_pdf_bitacora(self):
        try:
            registros=db.listar_bitacora(200,empresa_id=self.eid)
            if not registros:
                messagebox.showwarning("Sin datos","No hay registros en la bitácora.",parent=self); return
            path=pdf.generar_reporte_bitacora(registros,self.emp,empresa_id=self.eid)
            messagebox.showinfo("✓ PDF generado",f"Reporte de bitácora guardado en:\n{path}",parent=self)
            abrir(path,self)
        except Exception as ex: messagebox.showerror("Error al generar PDF",str(ex),parent=self)

    # ═══ BACKUP / RESTAURACIÓN ═════════════════════════════════════════════════
    def _s_backup(self):
        self._clr()
        self._hdr("💾  Backup y Restauración")
        self._sub("Exporta o restaura los datos de tu empresa.")

        card=Card(self.ct,padx=24,pady=20); card.pack(fill="x",padx=24,pady=(0,18))

        # Exportar backup
        tk.Label(card,text="📦  Exportar Backup de Empresa",font=F["h3"],bg=C["card"],fg=C["txt"]).pack(anchor="w",pady=(0,6))
        tk.Label(card,text="Crea una copia de seguridad de los datos de tu empresa.\nIncluye empleados, nóminas, préstamos y bitácora de esta empresa solamente.",
            font=F["bd"],bg=C["card"],fg=C["sub"],wraplength=600,justify="left").pack(anchor="w",pady=(0,10))
        Btn(card,"Exportar Backup de Empresa",cmd=self._exportar_backup,sty="ok",icon="📦").pack(anchor="w",pady=(0,16))

        # Separador
        tk.Frame(card,bg=C["border"],height=1).pack(fill="x",pady=8)

        # Importar backup
        tk.Label(card,text="📥  Restaurar Backup de Empresa",font=F["h3"],bg=C["card"],fg=C["err"]).pack(anchor="w",pady=(8,6))
        tk.Label(card,text="Reemplaza los datos de tu empresa con un backup previo.\n⚠ Los datos de otras empresas NO se verán afectados.",
            font=F["bd"],bg=C["card"],fg=C["sub"],wraplength=600,justify="left").pack(anchor="w",pady=(0,10))
        Btn(card,"Restaurar Backup de Empresa",cmd=self._importar_backup,sty="err",icon="📥").pack(anchor="w")

    def _exportar_backup(self):
        from tkinter import filedialog
        ts=datetime.now().strftime("%Y%m%d_%H%M%S")
        emp_name = self.emp.get('nombre','Empresa').replace(' ','_')
        ruta=filedialog.asksaveasfilename(
            parent=self,
            title="Guardar backup de empresa",
            defaultextension=".db",
            initialfile=f"NominaApp_backup_{emp_name}_{ts}.db",
            filetypes=[("Base de datos","*.db"),("Todos","*.*")])
        if not ruta: return
        from tkinter import simpledialog
        pw=simpledialog.askstring("Contraseña del backup",
            "El backup queda cifrado.\n\n"
            "Escribe una contraseña si vas a restaurarlo en OTRO equipo\n"
            "(déjalo vacío para usarlo solo en esta instalación):",
            show="*",parent=self)
        ok,msg=db.exportar_backup_empresa(self.eid,ruta,password=pw or None)
        if ok:
            db.registrar_bitacora(self.user["id"],self.user["nombre"],"Exportó backup de empresa",ruta,empresa_id=self.eid)
            messagebox.showinfo("✓ Backup exportado",msg,parent=self)
        else:
            messagebox.showerror("Error",msg,parent=self)

    def _importar_backup(self):
        from tkinter import filedialog
        if not self._reautenticar(
            "Vas a reemplazar los datos de TU EMPRESA con un backup.\n"
            "Confirma tu identidad para continuar."):
            return
        if not messagebox.askyesno("⚠ Confirmar restauración",
            "¿Estás seguro de restaurar un backup de empresa?\n\n"
            "⚠ Los datos de TU EMPRESA serán reemplazados.\n"
            "Los datos de otras empresas NO se verán afectados.\n"
            "Se recomienda exportar un backup antes de restaurar.",
            parent=self): return
        ruta=filedialog.askopenfilename(
            parent=self,
            title="Seleccionar archivo de backup de empresa",
            filetypes=[("Base de datos","*.db"),("Todos","*.*")])
        if not ruta: return
        pw=None
        if db.archivo_cifrado(ruta) and not db.probar_clave_db(ruta):
            from tkinter import simpledialog
            pw=simpledialog.askstring("Backup cifrado",
                "Este backup está protegido con contraseña.\n"
                "Escríbela para continuar:",show="*",parent=self)
            if pw is None: return
        ok,msg=db.importar_backup_empresa(self.eid,ruta,password=pw)
        if ok:
            db.registrar_bitacora(self.user["id"],self.user["nombre"],"Restauró backup de empresa",ruta,empresa_id=self.eid)
            messagebox.showinfo("✓ Backup restaurado",msg,parent=self)
        else:
            messagebox.showerror("Error",msg,parent=self)


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _setup_logging()
    _install_error_hooks()
    _check_single_instance()
    try:
        ok, detalle = db.verificar_integridad()
        if not ok and db.archivo_cifrado(db.DB_PATH):
            _r = tk.Tk(); _r.withdraw()
            messagebox.showerror("No se pudo descifrar la base de datos",
                "La base de datos está cifrada y no se pudo abrir.\n\n"
                "Posibles causas:\n"
                "• Falta el archivo de llave (nomina.db.key)\n"
                "• La BD proviene de otra instalación/equipo\n"
                "• El archivo está dañado\n\n"
                "Restaura un backup exportado con contraseña o recupera\n"
                "el archivo .key de esta instalación.", parent=_r)
            _r.destroy()
            log.error("No se pudo abrir la BD cifrada: %s", detalle)
            raise SystemExit(1)
        if not ok:
            _r = tk.Tk(); _r.withdraw()
            messagebox.showwarning("Base de datos dañada",
                f"La base de datos está corrupta ({detalle}).\n"
                "Se moverá a un archivo de respaldo y se creará una nueva.\n"
                "Restaura un backup desde la sección Backup.", parent=_r)
            _r.destroy()
            log.error("integrity_check falló: %s", detalle)
            movida = db.apartar_db_corrupta()
            if movida: log.info("BD corrupta movida a %s", movida)
        ruta_bak = db.backup_automatico()
        if ruta_bak: log.info("Backup automático: %s", ruta_bak)
        db.init_db()
        db.migrar_base_datos()  # Migrar columnas nuevas
        db.crear_carpetas_imagenes()  # Crear carpetas para imágenes
    except SystemExit:
        raise
    except Exception as e:
        log.exception("Error crítico al inicializar")
        root=tk.Tk(); root.withdraw()
        messagebox.showerror("Error crítico",
            f"No se pudo inicializar la base de datos:\n{e}\n\n"
            "Verifica que la aplicación tenga permisos de escritura.")
        raise SystemExit(1)
    # Cubrir TODAS las ventanas Tk (login, admin, superadmin se crean como
    # instancias tk.Tk separadas en cada transición de sesión)
    tk.Tk.report_callback_exception = _tk_error_callback
    LoginWin().mainloop()