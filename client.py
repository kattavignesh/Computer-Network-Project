# client.py
import socket
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import os
import time

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5001
BUFFER_SIZE = 4096
SEPARATOR = "<SEPARATOR>"

# ---------------- Helper utils ----------------
def human_size(n):
    for unit in ['B','KB','MB','GB','TB']:
        if n < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"

def ext_icon(name):
    ext = name.split('.')[-1].lower() if '.' in name else ''
    mapping = {
        'pdf': '📕', 'txt': '📄', 'py': '🐍', 'c': '🔧', 'cpp': '🔧',
        'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'mp4': '🎞️', 'mp3': '🔊',
        'zip': '🗜️', 'rar': '🗜️', 'doc': '📝', 'docx': '📝', 'xls': '📊',
    }
    return mapping.get(ext, '📁')

# ---------------- Networking (auto connect with retry) ----------------
class Connection:
    def __init__(self, host, port, retry_interval=3):
        self.host = host
        self.port = port
        self.sock = None
        self.lock = threading.Lock()
        self.connected = False
        self.retry_interval = retry_interval
        self._start_auto_connect()

    def _start_auto_connect(self):
        t = threading.Thread(target=self._auto_connect_loop, daemon=True)
        t.start()

    def _auto_connect_loop(self):
        while True:
            if not self.connected:
                try:
                    self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.sock.connect((self.host, self.port))
                    try:
                        # avoid blocking the GUI if server stalls
                        self.sock.settimeout(5)
                    except Exception:
                        pass
                    self.connected = True
                    print("[CLIENT] Connected to server")
                    # safe callback if app exists
                    try:
                        app.on_connected()
                    except Exception:
                        pass
                except Exception:
                    self.connected = False
            time.sleep(self.retry_interval)

    def send(self, data: bytes):
        with self.lock:
            if not self.connected:
                raise ConnectionError("Not connected")
            self.sock.sendall(data)

    def recv(self, size=BUFFER_SIZE):
        with self.lock:
            if not self.connected:
                raise ConnectionError("Not connected")
            return self.sock.recv(size)

    def close(self):
        with self.lock:
            try:
                if self.sock:
                    self.sock.close()
            except:
                pass
            self.connected = False

# ---------------- GUI Application ----------------
class App:
    def __init__(self, root):
        self.root = root
        self.conn = Connection(SERVER_HOST, SERVER_PORT)
        self.root.title("CN File Transfer (Socket) — Vignesh")
        self.root.geometry("620x680")
        self.root.configure(bg="#0f1720")

        # mapping index -> real filename
        self.file_map = {}
        self.refreshing = False
        self._last_progress = -1

        # small helper to safely update UI from worker threads
        self.ui = lambda fn, *a, **k: self.root.after(0, lambda: fn(*a, **k))

        self.setup_ui()
        self.poll_connection()

    def setup_ui(self):
        # Title (glass-like frame)
        top = tk.Frame(self.root, bg="#0b1220", bd=1, relief=tk.FLAT)
        top.place(relx=0.5, rely=0.03, anchor='n', width=580, height=70)
        tk.Label(top, text="File Transfer (Computer Networks Project)",
                 fg="white", bg="#0b1220", font=("Segoe UI", 14, "bold")).pack(pady=12)

        # Server file list frame
        frame = tk.Frame(self.root, bg="#071018")
        frame.place(relx=0.5, rely=0.16, anchor='n', width=580, height=420)

        self.listbox = tk.Listbox(frame, bg="#071018", fg="white", font=("Segoe UI", 10),
                                  selectbackground="#0ea5ff", activestyle='none')
        self.listbox.place(x=10, y=10, width=420, height=360)

        # Right panel for details + controls
        right = tk.Frame(frame, bg="#071018")
        right.place(x=440, y=10, width=130, height=360)

        ttk.Button(right, text="Refresh", command=self.refresh_list).pack(pady=8, ipadx=6)
        ttk.Button(right, text="Download", command=self.download_selected).pack(pady=8, ipadx=6)
        ttk.Button(right, text="Upload Files", command=self.upload_files).pack(pady=8, ipadx=6)
        ttk.Button(right, text="Quit", command=self.on_quit).pack(pady=8, ipadx=6)

        # Progress area
        prog_frame = tk.Frame(self.root, bg="#071018")
        prog_frame.place(relx=0.5, rely=0.78, anchor='n', width=580, height=120)

        tk.Label(prog_frame, text="Progress:", fg="white", bg="#071018", font=("Segoe UI", 10)).place(x=10, y=6)
        self.progress = ttk.Progressbar(prog_frame, orient='horizontal', length=520, mode='determinate')
        self.progress.place(x=10, y=32)
        self.progress_label = tk.Label(prog_frame, text="Idle", fg="white", bg="#071018", font=("Segoe UI", 9))
        self.progress_label.place(x=10, y=66)

        # status bar
        self.status = tk.Label(self.root, text="Status: Connecting...", fg="#94a3b8", bg="#0f1720", anchor='w')
        self.status.place(relx=0.01, rely=0.95, relwidth=0.98)

    def poll_connection(self):
        if self.conn.connected:
            self.status.config(text="Status: Connected to server")
            # auto refresh shortly after connected
            self.refresh_list()
        else:
            self.status.config(text="Status: Server offline — retrying...")
        self.root.after(2000, self.poll_connection)

    def on_connected(self):
        # callback from Connection when established
        # (Connection prints and app polls connection periodically)
        pass

    # ---------------- Actions ----------------
    def refresh_list(self):
        if self.refreshing:
            return
        if not self.conn.connected:
            messagebox.showwarning("Not connected", "Server not available yet.")
            return

        def worker_refresh():
            try:
                self.conn.send(b"LIST")
                data = self.conn.recv(BUFFER_SIZE).decode()
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to refresh: {e}"))
                self.refreshing = False
                return

            def apply_list(data_str):
                self.listbox.delete(0, tk.END)
                self.file_map = {}
                if data_str == "EMPTY":
                    self.listbox.insert(tk.END, "(No files on server)")
                else:
                    idx_local = 0
                    for entry in data_str.split(","):
                        if not entry.strip():
                            continue
                        name, size = entry.split("|")
                        icon = ext_icon(name)
                        display = f"{icon}  {name}    ({human_size(int(size))})"
                        self.listbox.insert(tk.END, display)
                        self.file_map[idx_local] = name
                        idx_local += 1
                self.refreshing = False

            self.root.after(0, lambda: apply_list(data))

        self.refreshing = True
        threading.Thread(target=worker_refresh, daemon=True).start()

    def upload_files(self):
        if not self.conn.connected:
            messagebox.showwarning("Not connected", "Server not available yet.")
            return
        filepaths = filedialog.askopenfilenames(title="Select files to upload")
        if not filepaths:
            return

        def worker(paths):
            total_files = len(paths)
            for idx, path in enumerate(paths, start=1):
                filename = os.path.basename(path)
                filesize = os.path.getsize(path)
                header = f"UPLOAD{SEPARATOR}{filename}{SEPARATOR}{filesize}"
                try:
                    self.conn.send(header.encode())
                    ready = self.conn.recv(BUFFER_SIZE).decode()
                    if ready != "READY":
                        raise RuntimeError("Server not ready for upload")
                    sent = 0
                    self.ui(self.progress.configure, value=0)
                    self.ui(self.progress_label.config, text=f"Uploading {filename} ({idx}/{total_files})")
                    with open(path, "rb") as f:
                        while sent < filesize:
                            chunk = f.read(BUFFER_SIZE)
                            if not chunk:
                                break
                            self.conn.send(chunk)
                            sent += len(chunk)
                            percent = int((sent / filesize) * 100)
                            if percent != self._last_progress:
                                self._last_progress = percent
                                self.ui(self.progress.configure, value=percent)
                    # small pause to show 100%
                    self.ui(self.progress.configure, value=100)
                    time.sleep(0.2)
                except Exception as ex:
                    self.ui(messagebox.showerror, "Upload error", f"{filename}\n{ex}")
                    return
            self.ui(self.progress_label.config, text="All uploads completed ✅")
            self.ui(self.refresh_list)

        threading.Thread(target=worker, args=(filepaths,), daemon=True).start()

    def download_selected(self):
        if not self.conn.connected:
            messagebox.showwarning("Not connected", "Server not available yet.")
            return
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("Select file", "Choose a file to download from the list.")
            return

        idx = sel[0]
        # use mapping to get real filename
        filename = self.file_map.get(idx)
        if not filename:
            messagebox.showerror("Error", "Filename mapping not found!")
            return

        save_path = filedialog.asksaveasfilename(initialfile=filename)
        if not save_path:
            return

        def worker_download(filename, save_path):
            try:
                self.conn.send(f"DOWNLOAD{SEPARATOR}{filename}".encode())
                header = self.conn.recv(BUFFER_SIZE).decode()
                if header == "ERROR":
                    self.ui(messagebox.showerror, "Error", "File not found on server.")
                    return
                fname, fsize = header.split(SEPARATOR)
                fsize = int(fsize)
                # acknowledge
                self.conn.send(b"OK")
                received = 0
                self.ui(self.progress.configure, value=0)
                self.ui(self.progress_label.config, text=f"Downloading {fname}")
                with open(save_path, "wb") as f:
                    while received < fsize:
                        chunk = self.conn.recv(min(BUFFER_SIZE, fsize - received))
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                        percent = int((received / fsize) * 100)
                        if percent != self._last_progress:
                            self._last_progress = percent
                            self.ui(self.progress.configure, value=percent)
                self.ui(self.progress.configure, value=100)
                self.ui(self.progress_label.config, text=f"Downloaded: {os.path.basename(save_path)} ✅")
                self.ui(messagebox.showinfo, "Download", f"Saved to: {save_path}")
                self.ui(self.refresh_list)
            except Exception as ex:
                self.ui(messagebox.showerror, "Download error", str(ex))

        threading.Thread(target=worker_download, args=(filename, save_path), daemon=True).start()

    def on_quit(self):
        try:
            if self.conn.connected:
                self.conn.send(b"QUIT")
        except:
            pass
        self.conn.close()
        self.root.destroy()

# ---------------- Run app ----------------
root = tk.Tk()

# Ask for server IP and port at startup (defaults pre-filled)
try:
    ip_input = simpledialog.askstring(
        "Server Address",
        "Enter server IPv4 address",
        initialvalue=SERVER_HOST,
        parent=root,
    )
    if ip_input:
        SERVER_HOST = ip_input.strip()
    port_input = simpledialog.askstring(
        "Server Port",
        "Enter server port",
        initialvalue=str(SERVER_PORT),
        parent=root,
    )
    if port_input and port_input.isdigit():
        SERVER_PORT = int(port_input)
except Exception:
    # Fallback silently to defaults if dialogs fail
    pass

app = App(root)
root.protocol("WM_DELETE_WINDOW", app.on_quit)
root.mainloop()
