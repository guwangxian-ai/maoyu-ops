#!/usr/bin/env python3
"""Install the pinned official CLI privately, without shell/profile changes."""
import hashlib
import platform
import tarfile
import urllib.request
from pathlib import Path

VERSION = "3.8.7"
PACKAGES = {
    ("Darwin", "arm64"): ("mac-arm", "darwin-arm64", "ff7595ee31cf2b693f4ff406ec76da857edf8f96b42390d8385bc654b6d05353"),
}


def main():
    system = (platform.system(), platform.machine())
    if system not in PACKAGES:
        raise SystemExit("此脚本当前固定核验 macOS arm64。其他平台请从百度官方发行安装 CLI，再配置 BAIDU_BDPAN_BIN；不要使用全局账号配置。")
    channel, suffix, checksum = PACKAGES[system]
    root = Path(__file__).resolve().parents[1] / "data/local-dev/runtime/bdpan"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    url = f"https://issuecdn.baidupcs.com/issue/netdisk/ai-bdpan/{channel}/{VERSION}/bdpan-{VERSION}-{suffix}.tar.gz"
    archive = root / f"bdpan-{VERSION}-{suffix}.tar.gz"
    with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
        while chunk := response.read(1024**2):
            output.write(chunk)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        archive.unlink(missing_ok=True)
        raise SystemExit("官方下载包与项目固定 SHA256 不符，已停止安装")
    with tarfile.open(archive) as package:
        matches = [m for m in package.getmembers() if m.isfile() and Path(m.name).name == "bdpan"]
        if len(matches) != 1:
            raise SystemExit("官方归档内容异常，已停止安装")
        with package.extractfile(matches[0]) as source, (root / "bdpan").open("wb") as output:
            output.write(source.read())
    (root / "bdpan").chmod(0o700)
    print(f"已安装官方 bdpan {VERSION}: {root / 'bdpan'}")


if __name__ == "__main__":
    main()
