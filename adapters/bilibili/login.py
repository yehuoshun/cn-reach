# -*- coding: utf-8 -*-
"""B站扫码登录（QR login）——cookie 失效时自己换新，不用手动导出。

移植自 yehuoshun/interest-radar scripts/bili_qr_login.py（MIT），改为包内模块并复用
请求层的 UA / Referer 与 cookie 路径解析。

运行：
    python -m adapters.bilibili.login [--timeout 180] [--qr-dir 目录]
    python -m adapters.bilibili.cli login            # 同一个入口，走 CLI

流程：取二维码 → 轮询扫码状态 → 成功后把新的登录态（SESSDATA / bili_jct /
DedeUserID / DedeUserID__ckMd5）写回 `client.cookie_path()` 指向的 cookie 文件，
旧文件先备份成 `<同名>.bak-qr`，文件其余字段（buvid3 等）原样保留。

唯一可选依赖 `qrcode`：没装时只打印登录 URL 与安装提示、以退出码 4 结束（不是
崩溃）；轮询与写回 cookie 不需要它。其余只用标准库 urllib / http.cookiejar /
json / re / time / pathlib。

二维码 PNG 默认落在 <工作区>/tmp（被 .gitignore 挡住），可用 --qr-dir 改。

状态行（stdout，供 agent 解析）：
    QR_KEY <key>            QR_SAVED <path>        QR_URL <url>
    WAIT_SCAN               SCANNED_CONFIRM_PENDING
    LOGIN_OK                COOKIE_FILE <path>     COOKIE_MASK ...
    QR_EXPIRED              POLL_TIMEOUT          POLL_ERR <原因>
    PARSE_FAIL              GENERATE_FAIL          MISSING_DEP qrcode

退出码：0 成功 · 1 生成或解析失败 · 2 二维码过期 · 3 轮询超时 · 4 缺 qrcode
"""
import http.cookiejar
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import client

PASSPORT = "https://passport.bilibili.com"
QR_GENERATE = f"{PASSPORT}/x/passport-login/web/qrcode/generate"
QR_POLL = f"{PASSPORT}/x/passport-login/web/qrcode/poll"
HEADERS = {  # 复用请求层的 UA / Referer，别让两处漂移
    "User-Agent": client.UA,
    "Referer": client.REFERER,
    "Accept": "application/json, text/plain, */*",
}
AUTH_KEYS = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")
REQUIRED_KEYS = ("SESSDATA", "bili_jct", "DedeUserID")
POLL_SECONDS, POLL_INTERVAL, HTTP_TIMEOUT = 180.0, 2.0, 15.0  # 总时长 / 间隔 / 单次超时
EXIT_OK, EXIT_FAIL, EXIT_EXPIRED, EXIT_TIMEOUT, EXIT_MISSING_DEP = 0, 1, 2, 3, 4


def _log(message: str) -> None:
    print(message, flush=True)


def _mask(value: str) -> str:
    """cookie 值一律不进日志：只留前 4 位 + 省略号 + 长度。"""
    return f"{value[:4]}…(共 {len(value)} 字符)" if value else "空"


def _http_json(url: str, timeout: float = HTTP_TIMEOUT) -> dict:
    """GET → dict；网络 / JSON 异常统一抛 client.BiliError（kind 区分网络与接口）。"""
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 —— 网络层什么都可能抛
        raise client.BiliError(f"网络请求失败：{type(exc).__name__}: {exc}", kind="network") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise client.BiliError(f"响应不是 JSON（前 60 字）：{raw[:60]!r}", kind="api") from exc
    if not isinstance(payload, dict):
        raise client.BiliError("响应结构异常：顶层不是对象", kind="api")
    return payload


def _follow(url: str) -> tuple:
    """跟随 passport 的 crossDomain 跳转；Set-Cookie 由 jar 接住。"""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=HTTP_TIMEOUT) as response:
        return response.status, response.geturl(), jar


def _collect_auth(target: str, jar) -> dict:
    """从 jar 取四个登录字段；jar 没捞到就退回解析跳转链接里 gourl 的 query。"""
    got = {cookie.name: cookie.value for cookie in jar} if jar is not None else {}
    auth = {key: got.get(key, "") for key in AUTH_KEYS}
    if not all(auth[key] for key in REQUIRED_KEYS):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(target or "").query)
        inner = urllib.parse.parse_qs(query.get("gourl", [""])[0])
        for key in AUTH_KEYS:
            auth[key] = auth[key] or inner.get(key, [""])[0]
    return auth


def _write_cookie(auth: dict) -> Path:
    """写回 cookie 文件：保留其它字段，旧内容先备份成 <同名>.bak-qr。"""
    path = client.cookie_path()
    old = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    if old:
        path.with_name(path.name + ".bak-qr").write_text(old + "\n", encoding="utf-8")
    text = old
    for key in AUTH_KEYS:
        value = auth.get(key, "")
        if not value:
            continue
        field = f"{key}={value}"
        pattern = re.compile(rf"{key}=[^;]*")
        if pattern.search(text):
            text = pattern.sub(lambda _m, replacement=field: replacement, text)
        else:
            text = f"{text}; {field}" if text else field
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip().strip(";").strip() + "\n", encoding="utf-8")
    return path


def _generate() -> tuple:
    """取二维码：返回 (登录 URL, qrcode_key)，失败抛 client.BiliError。"""
    payload = _http_json(QR_GENERATE)
    if payload.get("code") != 0:
        raise client.BiliError(f"二维码接口 code={payload.get('code')} msg={payload.get('message')}",
                               code=payload.get("code"))
    data = payload.get("data") or {}
    url, key = str(data.get("url") or ""), str(data.get("qrcode_key") or "")
    if not url or not key:
        raise client.BiliError("二维码接口没返回 url / qrcode_key")
    return url, key


def _save_qr(text: str, out_dir: Path) -> Path:
    """渲染二维码 PNG；qrcode 是可选依赖，ImportError 交回 main 处理。"""
    import qrcode  # 只在真要出图时导入，缺包装不上也不影响后面轮询

    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"bili-qr-{int(time.time())}.png"
    qrcode.make(text).save(str(path))
    return path


def _parse_args(argv: list) -> tuple:
    """只认 --timeout 秒 / --qr-dir 目录；二维码是临时产物，默认落 <工作区>/tmp。"""
    timeout, qr_dir = POLL_SECONDS, client.workspace_root() / "tmp"
    for index, item in enumerate(argv):
        nxt = argv[index + 1] if index + 1 < len(argv) else ""
        if item in ("--timeout", "-t") and nxt.replace(".", "", 1).isdigit():
            timeout = float(nxt)
        elif item in ("--qr-dir", "-o") and nxt:
            qr_dir = Path(nxt)
    return timeout, qr_dir


def _finish(target: str) -> int:
    """确认成功：跟随跳转收 cookie → 写回文件。"""
    jar = None
    if target:
        try:
            status, final, jar = _follow(target)
            _log(f"CROSS_STATUS {status} final={final[:100]}")
        except Exception as exc:  # noqa: BLE001 —— 跳转失败还能靠 gourl 兜底
            _log(f"CROSS_ERR {type(exc).__name__}: {exc}")
            jar = None
    auth = _collect_auth(target, jar)
    missing = [key for key in REQUIRED_KEYS if not auth.get(key)]
    if missing:
        _log(f"PARSE_FAIL 没解析出登录态，缺 {'/'.join(missing)}（cookie 文件未改动）")
        return EXIT_FAIL
    try:
        path = _write_cookie(auth)
    except OSError as exc:
        _log(f"PARSE_FAIL cookie 文件写回失败：{exc}")
        return EXIT_FAIL
    _log(f"COOKIE_FILE {path}")
    _log(f"COOKIE_MASK SESSDATA={_mask(auth['SESSDATA'])} DedeUserID={_mask(auth['DedeUserID'])}")
    _log("LOGIN_OK")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """入口：返回退出码 0/1/2/3/4，不向外抛异常。"""
    timeout_s, qr_dir = _parse_args(list(sys.argv[1:] if argv is None else argv))
    try:  # 1) 取二维码
        qr_url, qr_key = _generate()
    except client.BiliError as exc:
        _log(f"GENERATE_FAIL {exc}")
        return EXIT_FAIL
    _log(f"QR_KEY {qr_key}")
    try:  # 2) 渲染二维码（可选依赖，缺了不算失败）
        qr_path = _save_qr(qr_url, qr_dir)
    except ImportError:
        _log("MISSING_DEP qrcode")
        _log(f"QR_URL {qr_url}")
        _log('装依赖后重跑：python -m pip install "qrcode[pil]"')
        return EXIT_MISSING_DEP
    except Exception as exc:  # noqa: BLE001 —— 出图失败按生成失败算
        _log(f"GENERATE_FAIL 二维码出图失败：{type(exc).__name__}: {exc}")
        return EXIT_FAIL
    _log(f"QR_SAVED {qr_path}")
    deadline, last = time.monotonic() + timeout_s, None  # 3) 轮询
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            payload = _http_json(f"{QR_POLL}?qrcode_key={urllib.parse.quote(qr_key)}")
        except client.BiliError as exc:  # 单次轮询失败不致命，继续等
            _log(f"POLL_ERR {exc}")
            continue
        if payload.get("code") != 0:
            _log(f"POLL_HTTP_FAIL code={payload.get('code')} msg={payload.get('message')}")
            break
        data = payload.get("data") or {}
        dcode = data.get("code")  # -2 未扫码 / -4 已扫待确认 / 0 成功 / -5 过期
        if dcode == 0:
            return _finish(str(data.get("url") or ""))
        if dcode == -5:
            _log("QR_EXPIRED")
            return EXIT_EXPIRED
        if dcode != last:  # 状态变化才打印，避免刷屏
            _log({-2: "WAIT_SCAN", -4: "SCANNED_CONFIRM_PENDING"}.get(dcode, f"UNKNOWN_DCODE {dcode}"))
        last = dcode
    _log("POLL_TIMEOUT")
    return EXIT_TIMEOUT


if __name__ == "__main__":
    raise SystemExit(main())
