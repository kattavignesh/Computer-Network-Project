# server.py
import socket
import threading
import os
from typing import Dict

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5001
BUFFER_SIZE = 4096
SEPARATOR = "<SEPARATOR>"

FILES_DIR = "files"
os.makedirs(FILES_DIR, exist_ok=True)

# --- Concurrency primitives ---
_file_locks: Dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()

def _get_file_lock(filename: str) -> threading.Lock:
    # Double-checked creation of a per-file lock
    lock = _file_locks.get(filename)
    if lock is None:
        with _file_locks_guard:
            if filename not in _file_locks:
                _file_locks[filename] = threading.Lock()
            lock = _file_locks[filename]
    return lock

def handle_client(client_socket, addr):
    print(f"[+] New connection from {addr}")
    try:
        client_socket.settimeout(15)
    except Exception:
        pass
    try:
        while True:
            header = client_socket.recv(BUFFER_SIZE).decode()
            if not header:
                break

            # LIST
            if header == "LIST":
                files = os.listdir(FILES_DIR)
                entries = []
                for f in files:
                    path = os.path.join(FILES_DIR, f)
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        size = 0
                    entries.append(f"{f}|{size}")
                payload = ",".join(entries) if entries else "EMPTY"
                client_socket.send(payload.encode())

            # UPLOAD<SEPARATOR>filename<SEPARATOR>filesize
            elif header.startswith("UPLOAD"):
                try:
                    _, filename, filesize = header.split(SEPARATOR)
                    filesize = int(filesize)
                except Exception:
                    client_socket.send("ERROR".encode())
                    continue

                client_socket.send("READY".encode())  # ack before receiving bytes
                path = os.path.join(FILES_DIR, filename)
                tmp_path = path + ".part"

                lock = _get_file_lock(filename)
                with lock:
                    received = 0
                    with open(tmp_path, "wb") as f:
                        while received < filesize:
                            chunk = client_socket.recv(min(BUFFER_SIZE, filesize - received))
                            if not chunk:
                                break
                            f.write(chunk)
                            received += len(chunk)
                    # Atomic replace to avoid readers seeing partial file
                    os.replace(tmp_path, path)

            # DOWNLOAD<SEPARATOR>filename
            elif header.startswith("DOWNLOAD"):
                try:
                    _, filename = header.split(SEPARATOR)
                except Exception:
                    client_socket.send("ERROR".encode())
                    continue
                path = os.path.join(FILES_DIR, filename)
                if not os.path.exists(path):
                    client_socket.send("ERROR".encode())
                    continue

                lock = _get_file_lock(filename)
                with lock:
                    filesize = os.path.getsize(path)
                    client_socket.send(f"{filename}{SEPARATOR}{filesize}".encode())
                    # wait for client OK
                    ack = client_socket.recv(BUFFER_SIZE).decode()
                    if ack != "OK":
                        continue

                    with open(path, "rb") as f:
                        while True:
                            bytes_read = f.read(BUFFER_SIZE)
                            if not bytes_read:
                                break
                            client_socket.sendall(bytes_read)

            # QUIT
            elif header == "QUIT":
                break

            else:
                client_socket.send("INVALID".encode())
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        client_socket.close()
        print(f"[-] Connection closed {addr}")

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((SERVER_HOST, SERVER_PORT))
    server.listen(20)
    print(f"🚀 Server listening on {SERVER_HOST}:{SERVER_PORT}")
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
