import base64, hashlib, mimetypes, os, re, shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).parent
ALLOWED_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".csv",
    ".txt",
    ".md",
    ".zip",
    ".mp3",
    ".wav",
    ".mp4",
    ".webm",
}


def settings():
    import json

    return json.loads((ROOT / "adapter-settings.json").read_text())


def collect_media(steps, answer):
    cfg = settings()
    dest = Path(cfg["mediaRoot"])
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    roots = [
        Path(cfg["workspace"]).resolve(),
        Path.home() / ".gemini/antigravity/brain",
        Path.home() / ".gemini/antigravity/artifacts",
        Path.home() / ".gemini/antigravity-cli/brain",
        Path.home() / ".gemini/antigravity/conversations",
    ]
    roots = [p.resolve() for p in roots]
    paths = []
    inline = []
    for st in steps:
        g = st.get("generateImage", {})
        for k in ["generatedMedia", "generatedImage"]:
            m = g.get(k, {})
            if m.get("uri"):
                paths.append(m["uri"])
            data = m.get("inlineData") or m.get("base64Data")
            if data:
                inline.append((data, m.get("mimeType", "image/png")))
        n = st.get("notifyUser", {})
        paths.extend(n.get("reviewAbsoluteUris", []))
        c = st.get("fileChange", {})
        if c.get("absolutePathUri"):
            paths.append(c["absolutePathUri"])
    paths += re.findall(r"(?:!?\\?\[[^\]]*\\?\])\(([^)]+)\)", answer)
    paths += re.findall(r"MEDIA:\s*([^\n]+)", answer)
    result = []
    for value in paths:
        value = unquote(value.strip().strip('<>"'))
        if value.startswith("file://"):
            value = urlparse(value).path
        try:
            p = Path(value).expanduser().resolve()
        except (ValueError, OSError):
            continue
        if (
            not p.is_file()
            or p.suffix.lower() not in ALLOWED_EXT
            or p.stat().st_size > 20 * 1024 * 1024
        ):
            continue
        if p.name in ["task.md", "implementation_plan.md", "walkthrough.md"]:
            continue
        if not any(p == r or r in p.parents for r in roots):
            continue
        data = p.read_bytes()
        name = hashlib.sha256(data).hexdigest()[:12] + "-" + p.name
        target = dest / name
        if not target.exists():
            target.write_bytes(data)
            os.chmod(target, 0o600)
        if str(target) not in result:
            result.append(str(target))
    for value, mime in inline:
        try:
            data = base64.b64decode(value, validate=True)
        except Exception:
            continue
        if len(data) > 20 * 1024 * 1024:
            continue
        ext = mimetypes.guess_extension(mime) or ".bin"
        if ext not in ALLOWED_EXT:
            continue
        p = dest / (hashlib.sha256(data).hexdigest()[:16] + ext)
        if not p.exists():
            p.write_bytes(data)
            os.chmod(p, 0o600)
        if str(p) not in result:
            result.append(str(p))
    return result
