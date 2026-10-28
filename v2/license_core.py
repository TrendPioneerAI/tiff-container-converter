from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


PRODUCT_ID = "bigtiff_to_classic_tiff"
LICENSE_FORMAT_VERSION = 1
PUBLIC_KEY_B64 = "3CxSMlpKBCERYFVLPU9bBS+wLykDigtrIm0RqXdvi4U="


class LicenseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LicenseInfo:
    license_id: str
    customer: str
    issued_at: str
    expires_at: str
    machine_id: str
    max_files_per_batch: int
    features: tuple[str, ...]
    note: str
    source_path: str = ""

    @property
    def unlimited_batch(self) -> bool:
        return self.max_files_per_batch == 0

    @property
    def batch_limit_text(self) -> str:
        return "不限" if self.unlimited_batch else str(self.max_files_per_batch)


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical_payload(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def machine_code() -> str:
    components: list[str] = []
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                components.append(str(value))
        except OSError:
            pass
    components.extend([platform.node(), hex(uuid.getnode()), platform.machine()])
    normalized = "|".join(part.strip().lower() for part in components if part and part.strip())
    if not normalized:
        raise LicenseError("无法取得本机设备标识")
    digest = hashlib.sha256((PRODUCT_ID + "|" + normalized).encode("utf-8")).hexdigest().upper()
    return "BTFC-" + "-".join(digest[index : index + 5] for index in range(0, 25, 5))


def public_key() -> Ed25519PublicKey:
    if PUBLIC_KEY_B64 == "__PUBLIC_KEY_B64__":
        raise LicenseError("客户端尚未配置许可证公钥")
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
    except Exception as exc:
        raise LicenseError("客户端许可证公钥无效") from exc


def generate_keypair(private_path: Path, public_path: Path, overwrite: bool = False) -> None:
    if (private_path.exists() or public_path.exists()) and not overwrite:
        raise LicenseError("密钥文件已经存在；拒绝覆盖现有授权体系")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def public_key_b64_from_pem(public_path: Path) -> str:
    key = serialization.load_pem_public_key(public_path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("公钥不是Ed25519格式")
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:
        raise LicenseError(f"无法读取授权私钥：{path}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError("授权私钥不是Ed25519格式")
    return key


def create_license(
    private_key_path: Path,
    customer: str,
    expires_at: str,
    bound_machine: str = "*",
    max_files_per_batch: int = 0,
    note: str = "",
    license_id: str | None = None,
) -> dict:
    customer = customer.strip()
    if not customer:
        raise LicenseError("客户名称不能为空")
    try:
        expiry = dt.date.fromisoformat(expires_at)
    except ValueError as exc:
        raise LicenseError("到期日期必须使用YYYY-MM-DD格式") from exc
    if max_files_per_batch < 0:
        raise LicenseError("批量上限不能为负数")
    if bound_machine != "*" and not bound_machine.startswith("BTFC-"):
        raise LicenseError("设备码格式不正确；留空或*表示不绑定设备")
    payload = {
        "format_version": LICENSE_FORMAT_VERSION,
        "product": PRODUCT_ID,
        "license_id": license_id or str(uuid.uuid4()),
        "customer": customer,
        "issued_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "expires_at": expiry.isoformat(),
        "machine_id": bound_machine or "*",
        "max_files_per_batch": int(max_files_per_batch),
        "features": ["single_conversion", "batch_conversion", "folder_scan"],
        "note": note.strip(),
    }
    signature = load_private_key(private_key_path).sign(canonical_payload(payload))
    return {"payload": payload, "signature": _b64encode(signature)}


def save_license(envelope: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_license(path: Path, expected_machine: str | None = None) -> LicenseInfo:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        payload = envelope["payload"]
        signature = _b64decode(envelope["signature"])
    except Exception as exc:
        raise LicenseError("许可证文件格式无效") from exc
    try:
        public_key().verify(signature, canonical_payload(payload))
    except InvalidSignature as exc:
        raise LicenseError("许可证数字签名无效或内容已被修改") from exc
    except Exception as exc:
        raise LicenseError("许可证验证失败") from exc
    if payload.get("format_version") != LICENSE_FORMAT_VERSION:
        raise LicenseError("许可证格式版本不受支持")
    if payload.get("product") != PRODUCT_ID:
        raise LicenseError("许可证不适用于本软件")
    try:
        expires = dt.date.fromisoformat(payload["expires_at"])
    except (KeyError, ValueError) as exc:
        raise LicenseError("许可证到期日期无效") from exc
    if dt.date.today() > expires:
        raise LicenseError(f"许可证已于 {expires.isoformat()} 到期")
    actual_machine = expected_machine or machine_code()
    licensed_machine = str(payload.get("machine_id", ""))
    if licensed_machine not in ("*", actual_machine):
        raise LicenseError(f"许可证绑定了其他设备；本机设备码为 {actual_machine}")
    features = tuple(str(item) for item in payload.get("features", []))
    if "single_conversion" not in features:
        raise LicenseError("许可证未授权图像转换功能")
    batch_limit = int(payload.get("max_files_per_batch", 0))
    if batch_limit < 0:
        raise LicenseError("许可证批量上限无效")
    return LicenseInfo(
        license_id=str(payload.get("license_id", "")),
        customer=str(payload.get("customer", "")),
        issued_at=str(payload.get("issued_at", "")),
        expires_at=expires.isoformat(),
        machine_id=licensed_machine,
        max_files_per_batch=batch_limit,
        features=features,
        note=str(payload.get("note", "")),
        source_path=str(path.resolve()),
    )


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "BigTIFFToClassicTIFF"
    return Path.home() / ".bigtiff_to_classic_tiff"


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def candidate_license_paths(explicit: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    if explicit:
        paths.append(explicit)
    paths.extend([app_data_dir() / "license.lic", executable_dir() / "license.lic"])
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def find_valid_license(explicit: Path | None = None) -> tuple[LicenseInfo | None, str]:
    errors: list[str] = []
    for path in candidate_license_paths(explicit):
        if not path.is_file():
            continue
        try:
            return verify_license(path), ""
        except LicenseError as exc:
            errors.append(f"{path}: {exc}")
    return None, "；".join(errors) if errors else "未安装许可证"


def install_license(source: Path) -> tuple[LicenseInfo, Path]:
    info = verify_license(source)
    destination = app_data_dir() / "license.lic"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    installed = verify_license(destination)
    return installed, destination
