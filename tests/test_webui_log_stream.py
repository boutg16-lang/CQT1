# -*- coding: utf-8 -*-
"""Child progress bars use CR; the log reader must not hide them."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui.utils import iter_process_lines


class ChunkyStream:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, size=-1):
        return self.chunks.pop(0) if self.chunks else ""


def test_cr_and_lf_are_both_line_endings():
    data = "Downloading 10%\rDownloading 55%\rDownloading 100%\nDone\n"
    assert list(iter_process_lines(io.StringIO(data))) == [
        "Downloading 10%", "Downloading 55%",
        "Downloading 100%", "Done",
    ]


def test_crlf_split_across_chunks():
    stream = ChunkyStream(["a\r", "\nb\rc", "\nd"])
    assert list(iter_process_lines(stream)) == ["a", "b", "c", "d"]


def test_trailing_text_without_newline_is_kept():
    assert list(iter_process_lines(io.StringIO("last line"))) == ["last line"]


def test_blank_lines_are_not_lost_by_the_reader():
    assert list(iter_process_lines(io.StringIO("a\r\r\nb\n"))) == ["a", "", "b"]


def test_lines_are_streamed_before_the_child_exits():
    import subprocess
    import sys
    import time

    process = subprocess.Popen(
        [sys.executable, "-u", "-c",
         "import time; print('first', flush=True); time.sleep(1.2); print('second', flush=True)"],
        stdout=subprocess.PIPE, text=True, bufsize=1)
    started = time.time()
    first_delay = None
    for line in iter_process_lines(process.stdout):
        if line == "first":
            first_delay = time.time() - started
    process.wait(timeout=5)
    assert first_delay is not None and first_delay < 0.8
