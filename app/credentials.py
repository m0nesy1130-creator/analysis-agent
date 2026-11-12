"""从当前 Windows 用户的凭据管理器读取 Agent 运行凭据。"""

import sys
from getpass import getpass

LOCAL_MYSQL = ("127.0.0.1", 3306, "analysis_agent", "analysis_agent_checkpoint")
MYSQL_CREDENTIAL = "analysis-agent/MySQL/local-checkpoint"
DEEPSEEK_CREDENTIAL = "analysis-agent/DeepSeek/api.deepseek.com"


def _windows_api():
    if sys.platform != "win32":
        raise RuntimeError("Windows 凭据管理器仅在 Windows 上可用。")
    try:
        from win32ctypes.pywin32 import pywintypes, win32cred
    except ImportError:
        raise RuntimeError("请安装 requirements.txt 中的 Windows 凭据依赖。") from None
    return win32cred, pywintypes


def read_credential(target: str) -> str | None:
    if sys.platform != "win32":
        return None
    win32cred, pywintypes = _windows_api()
    try:
        entry = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
        return entry["CredentialBlob"].decode("utf-16-le")
    except pywintypes.error as error:
        if error.winerror == 1168:  # ERROR_NOT_FOUND
            return None
        raise RuntimeError("无法读取 Windows 凭据管理器。") from None
    except UnicodeError:
        raise RuntimeError("保存的 Agent 凭据格式无效，请重新配置。") from None


def _write_credential(target: str, username: str, secret: str) -> None:
    win32cred, pywintypes = _windows_api()
    try:
        win32cred.CredWrite({
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": target,
            "UserName": username,
            "CredentialBlob": secret,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        })
    except pywintypes.error:
        raise RuntimeError("无法保存 Windows 凭据。") from None


def save_runtime_credentials(mysql_password: str, deepseek_api_key: str) -> None:
    api_key = deepseek_api_key.strip()
    if not (mysql_password and api_key):
        raise ValueError("请输入 Agent 数据库密码和 DeepSeek API Key。")
    _write_credential(MYSQL_CREDENTIAL, LOCAL_MYSQL[2], mysql_password)
    _write_credential(DEEPSEEK_CREDENTIAL, "api-key", api_key)


def main() -> int:
    if not sys.stdin.isatty():
        print("请在本机 PowerShell 交互运行 python -m app.credentials。", file=sys.stderr)
        return 1
    try:
        _windows_api()
        mysql_password = getpass("Agent 数据库密码：")
        api_key = getpass("DeepSeek API Key：")
        save_runtime_credentials(mysql_password, api_key)
    except (EOFError, KeyboardInterrupt):
        return 1
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    print("Agent 运行凭据已保存到 Windows 凭据管理器。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
