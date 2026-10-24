import os, socket, secrets
from pathlib import Path

s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
token = secrets.token_urlsafe(32)
exe = os.environ.get("AGY_BINARY", str(Path.home() / ".local/bin/agy"))
args = [
    exe,
    "--hub",
    "--hub-port=" + str(port),
    "--app_data_dir=antigravity",
    "--csrf_token=" + token,
]
qemu = os.environ.get("AGY_QEMU_BINARY")
if qemu:
    os.environ["GODEBUG"] = ",".join(
        filter(None, [os.environ.get("GODEBUG"), "asyncpreemptoff=1"])
    )
    os.execv(qemu, [qemu, "-cpu", "max"] + args)
os.execv(exe, args)
