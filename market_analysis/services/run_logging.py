"""File-logging helper for long-running jobs.

Both the ``scripts.daily_update`` CLI and the GUI ``DailyWorker``
funnel progress through this so a single ``logs/daily_update.log``
plus ``daily_update.err`` accumulates regardless of which entry
point launched the run.  Each invocation opens with a dated banner
so consecutive runs in the rolling file are visually separable.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, TextIO


REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / "logs"


Progress = Callable[[str], None]


def _write_header(stream: TextIO, label: str, source: str) -> None:
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    bar = "=" * 72
    stream.write(f"\n{bar}\n")
    stream.write(f"  {label} — {ts}\n")
    stream.write(f"  source: {source}\n")
    stream.write(f"{bar}\n")
    stream.flush()


@contextmanager
def run_log(
    job: str,
    *,
    source: str,
    extra_progress: Progress | None = None,
    log_level: str = "INFO",
    enabled: bool = True,
) -> Iterator[Progress]:
    """Tee progress lines to ``logs/<job>.log`` and warnings to ``<job>.err``.

    Yields a ``progress(line)`` callable.  When ``enabled`` is False the
    callable just delegates to ``extra_progress`` (or no-ops); useful for
    ad-hoc CLI runs that pass ``--no-log-file``.

    Adds a stream handler to the root logger so ``logging.warning`` lands
    in ``<job>.err``; the handler is removed on exit so the next run
    doesn't double-log to a closed file.
    """
    log_fp: TextIO | None = None
    err_fp: TextIO | None = None
    err_handler: logging.Handler | None = None

    if enabled:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_fp = (LOG_DIR / f"{job}.log").open("a", encoding="utf-8")
        err_fp = (LOG_DIR / f"{job}.err").open("a", encoding="utf-8")
        _write_header(log_fp, job, source)
        _write_header(err_fp, job, source)
        err_handler = logging.StreamHandler(err_fp)
        err_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        err_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        logging.getLogger().addHandler(err_handler)
        # Bring the root logger up to at least the requested level so
        # warnings actually fire through to the handler.
        root = logging.getLogger()
        if root.level == logging.NOTSET or root.level > err_handler.level:
            root.setLevel(err_handler.level)

    def progress(line: str) -> None:
        if log_fp is not None:
            log_fp.write(line + "\n")
            log_fp.flush()
        if extra_progress is not None:
            extra_progress(line)

    try:
        yield progress
    finally:
        if err_handler is not None:
            logging.getLogger().removeHandler(err_handler)
        if log_fp is not None:
            log_fp.close()
        if err_fp is not None:
            err_fp.close()
