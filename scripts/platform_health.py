from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PLATFORMS = {
    "Instagram": "https://www.instagram.com/p/aye83DjauH/",
    "TikTok": "https://www.tiktok.com/@leenabhushan/video/6748451240264420610",
    "YouTube": "https://www.youtube.com/watch?v=BaW_jenozKc",
    "Facebook": "https://www.facebook.com/video.php?v=274175099429670",
    "X/Twitter": "https://twitter.com/MunTheShinobi/status/1600009574919962625",
    "Reddit": "https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/",
    "Vimeo": "https://player.vimeo.com/video/54469442",
    "Dailymotion": "https://www.dailymotion.com/video/x5kesuj",
    "Pinterest": "https://www.pinterest.com/pin/664281013778109217/",
    "Bluesky": "https://bsky.app/profile/bsky.app/post/3l3vgf77uco2g",
    "Twitch": "https://www.twitch.tv/videos/1536751224",
}
REPORT_PATH = Path("platform-health-report.md")


def check_platform(name: str, url: str) -> tuple[bool, str]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-cache-dir",
        "--no-warnings",
        "--skip-download",
        "--dump-single-json",
        "--no-playlist",
        "--socket-timeout",
        "20",
        "--retries",
        "1",
        "--fragment-retries",
        "1",
        url,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=75,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "Tiempo de espera agotado"

    if result.returncode == 0 and result.stdout.strip():
        return True, "OK"

    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    detail = lines[-1] if lines else f"yt-dlp terminó con código {result.returncode}"
    if len(detail) > 400:
        detail = f"{detail[:400]}…"
    return False, detail


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    for name, url in PLATFORMS.items():
        ok, detail = check_platform(name, url)
        results.append((name, ok, detail))
        print(f"{'OK' if ok else 'FAIL'} {name}: {detail}")

    failed = [item for item in results if not item[1]]
    report = [
        "# RitaDown · monitor de plataformas",
        "",
        "Comprobación automática con `yt-dlp --skip-download` sobre enlaces públicos de referencia.",
        "",
        "| Plataforma | Estado | Detalle |",
        "| --- | --- | --- |",
    ]
    for name, ok, detail in results:
        safe_detail = detail.replace("|", "\\|").replace("\n", " ")
        report.append(f"| {name} | {'✅ OK' if ok else '❌ Error'} | {safe_detail} |")
    report += [
        "",
        f"Resultado: **{len(results) - len(failed)}/{len(results)} plataformas operativas**.",
        "",
        "Un fallo puede deberse a cambios de la plataforma, bloqueo temporal del runner o una regresión de `yt-dlp`.",
    ]
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
