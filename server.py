# server.py
import socket
import threading
import os
from typing import Dict
from urllib.parse import quote, unquote

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5001
BUFFER_SIZE = 4096
SEPARATOR = "<SEPARATOR>"

FILES_DIR = os.path.join(os.path.dirname(__file__), "files")
os.makedirs(FILES_DIR, exist_ok=True)

# --- Concurrency primitives ---
class ReadWriteLock:
    def __init__(self):
        self._readers = 0
        self._readers_lock = threading.Lock()
        self._writers_lock = threading.Lock()
        self._no_readers = threading.Condition(self._readers_lock)

    def acquire_read(self):
        with self._readers_lock:
            self._readers += 1

    def release_read(self):
        with self._readers_lock:
            self._readers -= 1
            if self._readers == 0:
                self._no_readers.notify_all()

    def acquire_write(self):
        self._writers_lock.acquire()
        # wait for active readers to drain
        with self._readers_lock:
            while self._readers > 0:
                self._no_readers.wait()

    def release_write(self):
        self._writers_lock.release()

_file_rwlocks: Dict[str, ReadWriteLock] = {}
_file_locks_guard = threading.Lock()

def _get_file_rwlock(filename: str) -> ReadWriteLock:
    lock = _file_rwlocks.get(filename)
    if lock is None:
        with _file_locks_guard:
            if filename not in _file_rwlocks:
                _file_rwlocks[filename] = ReadWriteLock()
            lock = _file_rwlocks[filename]
    return lock

def handle_client(client_socket, addr):
    print(f"[+] New connection from {addr}")
    try:
        # allow long operations; avoid premature timeouts
        client_socket.settimeout(300)
    except Exception:
        pass
    try:
        while True:
            header = client_socket.recv(BUFFER_SIZE).decode(errors="ignore").strip()
            if not header:
                break

            # LIST
            if header == "LIST":
                print(f"[LIST] from {addr}")
                files = os.listdir(FILES_DIR)
                entries = []
                for f in files:
                    path = os.path.join(FILES_DIR, f)
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        size = 0
                    # Encode filename to avoid delimiter conflicts
                    safe_name = quote(f, safe="")
                    entries.append(f"{safe_name}|{size}")
                payload = ",".join(entries) if entries else "EMPTY"
                client_socket.send(payload.encode())

            # UPLOAD<SEPARATOR>filename<SEPARATOR>filesize
            elif header.startswith("UPLOAD"):
                try:
                    _, filename_enc, filesize = header.split(SEPARATOR, 2)
                    filename = unquote(filename_enc)
                    filesize = int(filesize)
                except Exception:
                    print(f"[UPLOAD] parse error from {addr}: {header}")
                    client_socket.send("ERROR".encode())
                    continue

                # sanitize filename
                filename = os.path.basename(filename)
                if not filename or filename in (".", ".."):
                    client_socket.send("ERROR".encode())
                    continue

                client_socket.send("READY".encode())  # ack before receiving bytes
                path = os.path.join(FILES_DIR, filename)
                tmp_path = path + ".part"

                rw = _get_file_rwlock(filename)
                rw.acquire_write()
                try:
                    received = 0
                    with open(tmp_path, "wb") as f:
                        while received < filesize:
                            chunk = client_socket.recv(min(BUFFER_SIZE, filesize - received))
                            if not chunk:
                                break
                            f.write(chunk)
                            received += len(chunk)
                    if received == filesize:
                        # Atomic replace to avoid readers seeing partial file
                        os.replace(tmp_path, path)
                        print(f"[UPLOAD] saved {filename} ({filesize} bytes) from {addr}")
                    else:
                        # incomplete upload; cleanup
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                        print(f"[UPLOAD] incomplete for {filename} from {addr}: {received}/{filesize}")
                finally:
                    rw.release_write()

            # DOWNLOAD<SEPARATOR>filename
            elif header.startswith("DOWNLOAD"):
                try:
                    _, filename_enc = header.split(SEPARATOR, 1)
                    filename = unquote(filename_enc)
                except Exception:
                    print(f"[DOWNLOAD] parse error from {addr}: {header}")
                    client_socket.send("ERROR".encode())
                    continue
                filename = os.path.basename(filename)
                path = os.path.join(FILES_DIR, filename)
                if not os.path.exists(path):
                    print(f"[DOWNLOAD] missing {filename} for {addr}")
                    client_socket.send("ERROR".encode())
                    continue

                rw = _get_file_rwlock(filename)
                rw.acquire_read()
                try:
                    filesize = os.path.getsize(path)
                    client_socket.send(f"{filename}{SEPARATOR}{filesize}".encode())
                    # wait for client OK
                    ack = client_socket.recv(BUFFER_SIZE).decode()
                    if ack != "OK":
                        print(f"[DOWNLOAD] aborted by client before data {addr}")
                        continue

                    with open(path, "rb") as f:
                        while True:
                            bytes_read = f.read(BUFFER_SIZE)
                            if not bytes_read:
                                break
                            client_socket.sendall(bytes_read)
                    print(f"[DOWNLOAD] sent {filename} ({filesize} bytes) to {addr}")
                finally:
                    rw.release_read()

            # DELETE<SEPARATOR>filename
            elif header.startswith("DELETE"):
                try:
                    _, filename_enc = header.split(SEPARATOR, 1)
                    filename = unquote(filename_enc)
                except Exception:
                    print(f"[DELETE] parse error from {addr}: {header}")
                    client_socket.send("ERROR".encode())
                    continue
                filename = os.path.basename(filename)
                path = os.path.join(FILES_DIR, filename)
                if not os.path.exists(path):
                    print(f"[DELETE] missing {filename} for {addr}")
                    client_socket.send("ERROR:NOT_FOUND".encode())
                    continue

                rw = _get_file_rwlock(filename)
                rw.acquire_write()
                try:
                    # only allow file deletion (no directories)
                    if os.path.isdir(path):
                        raise IsADirectoryError("Path is a directory")
                    try:
                        # ensure not read-only on Windows
                        os.chmod(path, 0o666)
                    except Exception:
                        pass
                    os.remove(path)
                    client_socket.send("OK".encode())
                    print(f"[DELETE] removed {filename} for {addr}")
                except PermissionError as e:
                    # Common on Windows when file is in use (WinError 32)
                    msg = getattr(e, 'winerror', None)
                    reason = f"IN_USE:{msg}" if msg is not None else "IN_USE"
                    print(f"[DELETE] permission error for {filename} at {addr}: {e}")
                    try:
                        client_socket.send(f"ERROR:{reason}".encode())
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[DELETE] failed {filename} for {addr}: {e}")
                    try:
                        client_socket.send(f"ERROR:{e}".encode())
                    except Exception:
                        pass
                finally:
                    rw.release_write()

            # QUIT
            elif header == "QUIT":
                print(f"[QUIT] from {addr}")
                break

            else:
                print(f"[INVALID] from {addr}: {header}")
                client_socket.send("INVALID".encode())
    except (ConnectionResetError, BrokenPipeError) as e:
        print(f"[ERROR] connection error with {addr}: {e}")
    except Exception as e:
        print(f"[ERROR] unexpected for {addr}: {e}")
    finally:
        client_socket.close()
        print(f"[-] Connection closed {addr}")

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((SERVER_HOST, SERVER_PORT))
    server.listen(50)
    # Derive LAN IP for users to connect from other machines
    try:
        _tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _tmp.connect(("8.8.8.8", 80))
        lan_ip = _tmp.getsockname()[0]
        _tmp.close()
    except Exception:
        lan_ip = "<unknown>"
    print(f"🚀 Server listening on {SERVER_HOST}:{SERVER_PORT} (LAN IP: {lan_ip}:{SERVER_PORT})")
    try:
        while True:
            client_sock, client_addr = server.accept()
            t = threading.Thread(target=handle_client, args=(client_sock, client_addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        server.close()

if __name__ == "__main__":
    start_server()
