import os
import sys
if sys.platform.startswith('win'):
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
import json
import socket
import threading
import time
import pyperclip
try:
    import tkinter as tk
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False
    tk = None
from screeninfo import get_monitors
from pynput.mouse import Controller as MouseController, Button, Listener as MouseListener
from pynput.keyboard import Controller as KeyboardController, Key, KeyCode, Listener as KeyboardListener

CONFIG_FILE = "config.json"
UDP_PORT = 9998
TCP_PORT = 9999

# Premium Dark Colors (Catppuccin Palette)
BG_DARK = "#1e1e2e"
BG_CARD = "#252538"
TXT_LIGHT = "#cdd6f4"
TXT_MUTED = "#a6adc8"
ACCENT_PURPLE = "#cba6f7"
ACCENT_GREEN = "#a6e3a1"
ACCENT_RED = "#f38ba8"

# Global State
state = "LOCAL"
running = False
role = "Master"
other_position = "Right"
tcp_sock = None
last_synced_clipboard_text = ""
ignore_next_move = False

# Screen Resolution (Primary)
W_M, H_M = 1920, 1080
try:
    for m in get_monitors():
        if m.is_primary:
            W_M = m.width
            H_M = m.height
            break
except Exception:
    pass

cx, cy = W_M // 2, H_M // 2

# Controllers
mouse_controller = MouseController()
keyboard_controller = KeyboardController()

# Active Listeners
local_mouse_listener = None
suppressed_mouse_listener = None
suppressed_keyboard_listener = None

pressed_keys = set()
server_socket_ref = None  # Reference to close server socket on stop

# ================= Configuration Management =================

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"role": "Master", "other_position": "Right"}

def save_config(r, pos):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"role": r, "other_position": pos}, f, indent=4)
    except Exception:
        pass

# ================= Networking Helpers =================

def send_msg(sock, msg):
    if sock:
        try:
            payload = (json.dumps(msg) + "\n").encode('utf-8')
            sock.sendall(payload)
        except Exception:
            pass

def serialize_key(key):
    if hasattr(key, 'vk') and key.vk is not None:
        return {"vk": key.vk}
    elif hasattr(key, 'char') and key.char is not None:
        return {"char": key.char}
    else:
        return {"name": key.name}

def reconstruct_key(event):
    if "vk" in event:
        return KeyCode.from_vk(event["vk"])
    elif "char" in event:
        return KeyCode.from_char(event["char"])
    elif "name" in event:
        try:
            return Key[event["name"]]
        except KeyError:
            return None
    return None

# ================= Master Input Callbacks =================

def on_master_move(x, y):
    global state, other_position, W_M, H_M, tcp_sock
    if state != "LOCAL" or not running:
        return
        
    trigger = False
    if other_position == "Right" and x >= W_M - 5:
        trigger = True
    elif other_position == "Left" and x <= 5:
        trigger = True
        
    if trigger and tcp_sock:
        state = "REMOTE"
        ratio_y = y / H_M
        send_msg(tcp_sock, {"type": "transition_to_slave", "ratio_y": ratio_y})
        threading.Thread(target=start_suppressed_listeners, daemon=True).start()
        return False

def on_suppressed_move(x, y):
    global ignore_next_move, state, tcp_sock, cx, cy
    if state != "REMOTE" or not running:
        return
    if ignore_next_move:
        ignore_next_move = False
        return
        
    dx = x - cx
    dy = y - cy
    if dx != 0 or dy != 0:
        send_msg(tcp_sock, {"type": "mouse_move", "dx": dx, "dy": dy})
        ignore_next_move = True
        mouse_controller.position = (cx, cy)

def on_suppressed_click(x, y, button, pressed):
    global state, tcp_sock
    if state != "REMOTE" or not running:
        return
    send_msg(tcp_sock, {
        "type": "mouse_click",
        "button": button.name,
        "pressed": pressed
    })

def on_suppressed_scroll(x, y, dx, dy):
    global state, tcp_sock
    if state != "REMOTE" or not running:
        return
    send_msg(tcp_sock, {
        "type": "mouse_scroll",
        "dx": dx,
        "dy": dy
    })

def on_suppressed_press(key):
    global state, tcp_sock, pressed_keys
    if state != "REMOTE" or not running:
        return
    pressed_keys.add(key)
    
    # Emergency Failsafe Abort: Ctrl + Alt + Shift + Escape
    ctrl_pressed = any(k in pressed_keys for k in [Key.ctrl, Key.ctrl_l, Key.ctrl_r])
    alt_pressed = any(k in pressed_keys for k in [Key.alt, Key.alt_l, Key.alt_r])
    shift_pressed = any(k in pressed_keys for k in [Key.shift, Key.shift_l, Key.shift_r])
    if ctrl_pressed and alt_pressed and shift_pressed and key == Key.esc:
        emergency_abort()
        return False
        
    msg = serialize_key(key)
    msg["type"] = "key_press"
    send_msg(tcp_sock, msg)

def on_suppressed_release(key):
    global state, tcp_sock, pressed_keys
    if state != "REMOTE" or not running:
        return
    if key in pressed_keys:
        pressed_keys.remove(key)
        
    msg = serialize_key(key)
    msg["type"] = "key_release"
    send_msg(tcp_sock, msg)

def start_suppressed_listeners():
    global suppressed_mouse_listener, suppressed_keyboard_listener, ignore_next_move, cx, cy
    ignore_next_move = True
    mouse_controller.position = (cx, cy)
    
    suppressed_mouse_listener = MouseListener(
        on_move=on_suppressed_move,
        on_click=on_suppressed_click,
        on_scroll=on_suppressed_scroll,
        suppress=True
    )
    suppressed_keyboard_listener = KeyboardListener(
        on_press=on_suppressed_press,
        on_release=on_suppressed_release,
        suppress=True
    )
    suppressed_mouse_listener.start()
    suppressed_keyboard_listener.start()

def stop_suppressed_listeners():
    global suppressed_mouse_listener, suppressed_keyboard_listener
    try:
        if suppressed_mouse_listener:
            suppressed_mouse_listener.stop()
    except Exception:
        pass
    try:
        if suppressed_keyboard_listener:
            suppressed_keyboard_listener.stop()
    except Exception:
        pass

def emergency_abort():
    stop_suppressed_listeners()
    os._exit(0)

# ================= Event Processing =================

def handle_event(event):
    global state, other_position, W_M, H_M, tcp_sock, local_mouse_listener, last_synced_clipboard_text
    
    evt_type = event.get("type")
    
    if role == "Slave":
        if evt_type == "transition_to_slave":
            state = "REMOTE_CONTROLLED"
            ratio_y = event["ratio_y"]
            y = int(ratio_y * H_M)
            x = 0 if other_position == "Right" else W_M - 1
            mouse_controller.position = (x, y)
            
        elif evt_type == "mouse_move" and state == "REMOTE_CONTROLLED":
            mouse_controller.move(event["dx"], event["dy"])
            
            # Boundary check to return to Master
            cx_s, cy_s = mouse_controller.position
            transition_back = False
            if other_position == "Right" and cx_s <= 0:
                transition_back = True
            elif other_position == "Left" and cx_s >= W_M - 1:
                transition_back = True
                
            if transition_back:
                state = "LOCAL"
                ratio_y = cy_s / H_M
                send_msg(tcp_sock, {"type": "transition_to_master", "ratio_y": ratio_y})
                
        elif evt_type == "mouse_click" and state == "REMOTE_CONTROLLED":
            btn = Button[event["button"]]
            if event["pressed"]:
                mouse_controller.press(btn)
            else:
                mouse_controller.release(btn)
                
        elif evt_type == "mouse_scroll" and state == "REMOTE_CONTROLLED":
            mouse_controller.scroll(event["dx"], event["dy"])
            
        elif evt_type == "key_press" and state == "REMOTE_CONTROLLED":
            k = reconstruct_key(event)
            if k:
                keyboard_controller.press(k)
                
        elif evt_type == "key_release" and state == "REMOTE_CONTROLLED":
            k = reconstruct_key(event)
            if k:
                keyboard_controller.release(k)
                
        elif evt_type == "clipboard_sync":
            last_synced_clipboard_text = event["text"]
            pyperclip.copy(event["text"])
            
    elif role == "Master":
        if evt_type == "transition_to_master":
            state = "LOCAL"
            ratio_y = event["ratio_y"]
            y = int(ratio_y * H_M)
            x = W_M - 10 if other_position == "Right" else 10
            
            stop_suppressed_listeners()
            mouse_controller.position = (x, y)
            
            local_mouse_listener = MouseListener(on_move=on_master_move)
            local_mouse_listener.start()
            
        elif evt_type == "clipboard_sync":
            last_synced_clipboard_text = event["text"]
            pyperclip.copy(event["text"])

# ================= Threads & Sockets Loops =================

def clipboard_sync_loop():
    global last_synced_clipboard_text, running, tcp_sock
    while running:
        try:
            text = pyperclip.paste()
            if text and text != last_synced_clipboard_text:
                last_synced_clipboard_text = text
                if tcp_sock:
                    send_msg(tcp_sock, {"type": "clipboard_sync", "text": text})
        except Exception:
            pass
        time.sleep(0.5)

def run_tcp_reader(sock, on_connect_cb, on_disconnect_cb):
    global tcp_sock, state, local_mouse_listener
    tcp_sock = sock
    on_connect_cb()
    try:
        f = sock.makefile('r', encoding='utf-8')
        while running:
            line = f.readline()
            if not line:
                break
            event = json.loads(line.strip())
            handle_event(event)
    except Exception:
        pass
    finally:
        tcp_sock = None
        on_disconnect_cb()
        if role == "Master" and state == "REMOTE":
            state = "LOCAL"
            stop_suppressed_listeners()
            try:
                local_mouse_listener = MouseListener(on_move=on_master_move)
                local_mouse_listener.start()
            except Exception:
                pass

def start_udp_broadcast():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while running and not tcp_sock:
        try:
            s.sendto(b"LINKY_SLAVE", ('<broadcast>', UDP_PORT))
        except Exception:
            pass
        time.sleep(2)
    s.close()

def listen_udp_broadcast():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', UDP_PORT))
    while running:
        s.settimeout(1.0)
        try:
            data, addr = s.recvfrom(1024)
            if data == b"LINKY_SLAVE":
                s.close()
                return addr[0]
        except socket.timeout:
            continue
        except Exception:
            break
    s.close()
    return None

def service_loop(update_status_fn):
    global running, server_socket_ref, local_mouse_listener
    
    def on_connect():
        update_status_fn("Conectado", ACCENT_GREEN)
        
    def on_disconnect():
        if running:
            update_status_fn("Buscando...", ACCENT_PURPLE)
            
    # Start Clipboard Sync
    threading.Thread(target=clipboard_sync_loop, daemon=True).start()
    
    if role == "Slave":
        server_socket_ref = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket_ref.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server_socket_ref.bind(('0.0.0.0', TCP_PORT))
            server_socket_ref.listen(1)
        except Exception as e:
            update_status_fn(f"Error puerto: {e}", ACCENT_RED)
            running = False
            return
            
        while running:
            threading.Thread(target=start_udp_broadcast, daemon=True).start()
            update_status_fn("Esperando...", ACCENT_PURPLE)
            try:
                server_socket_ref.settimeout(1.0)
                while running:
                    try:
                        sock, addr = server_socket_ref.accept()
                        break
                    except socket.timeout:
                        continue
                if not running:
                    break
                run_tcp_reader(sock, on_connect, on_disconnect)
            except Exception:
                time.sleep(2)
        try:
            server_socket_ref.close()
        except Exception:
            pass
            
    elif role == "Master":
        while running:
            update_status_fn("Buscando...", ACCENT_PURPLE)
            slave_ip = listen_udp_broadcast()
            if not slave_ip or not running:
                continue
                
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((slave_ip, TCP_PORT))
                
                local_mouse_listener = MouseListener(on_move=on_master_move)
                local_mouse_listener.start()
                
                run_tcp_reader(sock, on_connect, on_disconnect)
            except Exception:
                time.sleep(2)

# ================= GUI Application =================

class LinkyGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Linky")
        self.root.geometry("400x380")
        self.root.configure(bg=BG_DARK)
        self.root.resizable(False, False)
        
        # Load Config
        config = load_config()
        self.role_var = tk.StringVar(value=config["role"])
        self.pos_var = tk.StringVar(value=config["other_position"])
        
        self.setup_ui()
        
    def setup_ui(self):
        # Header
        header = tk.Label(
            self.root, text="LINKY", font=("Segoe UI", 20, "bold"),
            bg=BG_DARK, fg=ACCENT_PURPLE
        )
        header.pack(pady=15)
        
        # Symmetrical Card for Controls
        card = tk.Frame(self.root, bg=BG_CARD, bd=0)
        card.pack(fill="both", expand=True, padx=25, pady=5)
        
        # Role config
        lbl_role = tk.Label(
            card, text="Rol de esta PC:", font=("Segoe UI", 11, "bold"),
            bg=BG_CARD, fg=TXT_LIGHT
        )
        lbl_role.pack(anchor="w", padx=20, pady=(15, 5))
        
        r_frame = tk.Frame(card, bg=BG_CARD)
        r_frame.pack(fill="x", padx=20)
        
        self.rb_master = tk.Radiobutton(
            r_frame, text="Master (Físico)", variable=self.role_var, value="Master",
            font=("Segoe UI", 10), bg=BG_CARD, fg=TXT_LIGHT,
            activebackground=BG_CARD, activeforeground=ACCENT_PURPLE,
            selectcolor=BG_DARK
        )
        self.rb_master.pack(side="left", padx=(0, 15))
        
        self.rb_slave = tk.Radiobutton(
            r_frame, text="Slave (Remota)", variable=self.role_var, value="Slave",
            font=("Segoe UI", 10), bg=BG_CARD, fg=TXT_LIGHT,
            activebackground=BG_CARD, activeforeground=ACCENT_PURPLE,
            selectcolor=BG_DARK
        )
        self.rb_slave.pack(side="left")
        
        # Position config
        lbl_pos = tk.Label(
            card, text="Ubicación de la OTRA PC:", font=("Segoe UI", 11, "bold"),
            bg=BG_CARD, fg=TXT_LIGHT
        )
        lbl_pos.pack(anchor="w", padx=20, pady=(15, 5))
        
        p_frame = tk.Frame(card, bg=BG_CARD)
        p_frame.pack(fill="x", padx=20)
        
        self.rb_left = tk.Radiobutton(
            p_frame, text="A la Izquierda", variable=self.pos_var, value="Left",
            font=("Segoe UI", 10), bg=BG_CARD, fg=TXT_LIGHT,
            activebackground=BG_CARD, activeforeground=ACCENT_PURPLE,
            selectcolor=BG_DARK
        )
        self.rb_left.pack(side="left", padx=(0, 15))
        
        self.rb_right = tk.Radiobutton(
            p_frame, text="A la Derecha", variable=self.pos_var, value="Right",
            font=("Segoe UI", 10), bg=BG_CARD, fg=TXT_LIGHT,
            activebackground=BG_CARD, activeforeground=ACCENT_PURPLE,
            selectcolor=BG_DARK
        )
        self.rb_right.pack(side="left")
        
        # Status text
        self.lbl_status = tk.Label(
            self.root, text="Estado: Desconectado", font=("Segoe UI", 10, "italic"),
            bg=BG_DARK, fg=TXT_MUTED
        )
        self.lbl_status.pack(pady=(15, 5))
        
        # Action button
        self.btn_action = tk.Button(
            self.root, text="Iniciar Servicio", font=("Segoe UI", 11, "bold"),
            bg=ACCENT_PURPLE, fg=BG_DARK, activebackground=TXT_LIGHT,
            activeforeground=BG_DARK, bd=0, relief="flat", padx=30, pady=8,
            command=self.toggle_service
        )
        self.btn_action.pack(pady=(5, 20))
        
    def toggle_service(self):
        global running, role, other_position
        if not running:
            # Start
            running = True
            role = self.role_var.get()
            other_position = self.pos_var.get()
            save_config(role, other_position)
            
            # Disable widgets
            self.rb_master.config(state="disabled")
            self.rb_slave.config(state="disabled")
            self.rb_left.config(state="disabled")
            self.rb_right.config(state="disabled")
            
            self.btn_action.config(text="Detener Servicio", bg=ACCENT_RED, fg=BG_DARK)
            
            # Run socket loop in background thread
            threading.Thread(target=service_loop, args=(self.update_status,), daemon=True).start()
        else:
            # Stop
            self.stop_all()
            
    def stop_all(self):
        global running, tcp_sock, server_socket_ref, local_mouse_listener
        running = False
        
        # Close sockets
        if tcp_sock:
            try:
                tcp_sock.close()
            except Exception:
                pass
        if server_socket_ref:
            try:
                server_socket_ref.close()
            except Exception:
                pass
                
        # Stop mouse/keyboard listeners
        stop_suppressed_listeners()
        try:
            if local_mouse_listener:
                local_mouse_listener.stop()
        except Exception:
            pass
            
        # Re-enable widgets
        self.rb_master.config(state="normal")
        self.rb_slave.config(state="normal")
        self.rb_left.config(state="normal")
        self.rb_right.config(state="normal")
        
        self.btn_action.config(text="Iniciar Servicio", bg=ACCENT_PURPLE, fg=BG_DARK)
        self.update_status("Desconectado", TXT_MUTED)
        
    def update_status(self, text, color):
        # Thread safe update
        self.root.after(0, lambda: self.lbl_status.config(text=f"Estado: {text}", fg=color))

def main():
    if HAS_TKINTER:
        root = tk.Tk()
        app = LinkyGUI(root)
        
        # Clean exit hook
        def on_closing():
            app.stop_all()
            root.destroy()
            sys.exit(0)
            
        root.protocol("WM_DELETE_WINDOW", on_closing)
        root.mainloop()
    else:
        global role, other_position, running
        print("=== LINKY (MODO CONSOLA) ===")
        print("Advertencia: python3-tk no está instalado. Ejecutando en consola.\n")
        
        config = load_config()
        role = config.get("role", "Master")
        other_position = config.get("other_position", "Right")
        
        print("Configuración actual:")
        print(f"  Rol: {role}")
        print(f"  Ubicación de la otra PC: {other_position}\n")
        
        change = input("¿Deseas cambiar la configuración? (s/N): ").strip().lower()
        if change == "s":
            print("\n1. Master (PC con mouse/teclado físico)")
            print("2. Slave (PC remota controlada)")
            role_choice = input("Selecciona el rol (1 o 2): ").strip()
            role = "Master" if role_choice == "1" else "Slave"
            
            print("\n¿Dónde se encuentra la OTRA PC respecto a esta?")
            print("1. Izquierda")
            print("2. Derecha")
            pos_choice = input("Selecciona la posición (1 o 2): ").strip()
            other_position = "Left" if pos_choice == "1" else "Right"
            
            save_config(role, other_position)
            print("Configuración guardada.\n")
            
        running = True
        
        def console_status_fn(text, color):
            print(f"[Estado] {text}")
            
        print("Iniciando servicio... Presiona Ctrl+C para detener.")
        try:
            service_loop(console_status_fn)
        except KeyboardInterrupt:
            print("\nDeteniendo servicio...")
            running = False
            sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
