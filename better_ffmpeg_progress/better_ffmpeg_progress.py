from pathlib import Path
from typing import Callable, Optional
import subprocess
import sys

from tqdm import tqdm


class FfmpegProcess:
    """
    Args:
        command (list): A list of arguments to pass to FFmpeg.

        ffmpeg_loglevel (str, optional): Desired FFmpeg log level. Default is "verbose".

    Raises:
        ValueError: If the list of arguments does not include "-i".
    """

    def __init__(self, commands: list[str], ffmpeg_loglevel="verbose"):
        if "-i" not in commands:
            raise ValueError("FFmpeg command must include '-i'")

        self._ffmpeg_args = commands + ["-hide_banner", "-loglevel", ffmpeg_loglevel]
        self._output_filepath = commands[-1]

        self._set_file_info()

        self._estimated_size: Optional[float] = None
        self._eta: Optional[float] = None
        self._percentage_progress = 0
        self._progress_bar = None
        self._seconds_processed = 0
        self._speed = 0
        self._current_size = 0

    def _set_file_info(self):
        index_of_filepath = self._ffmpeg_args.index("-i") + 1
        self._filepath = self._ffmpeg_args[index_of_filepath]
        self._can_get_duration = True

        try:
            self._duration_secs = float(
                subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        "-i",
                        self._filepath,
                    ],
                    capture_output=True,
                ).stdout
            )
            print(
                f"The duration of {self._filepath} has been detected as {self._duration_secs} seconds."
            )
        except Exception:
            self._can_get_duration = False

        if self._can_get_duration:
            self._ffmpeg_args += ["-progress", "pipe:1", "-nostats"]

    def _should_overwrite(self):
        if "-y" not in self._ffmpeg_args and Path(self._output_filepath).exists():
            choice = input(f"{self._output_filepath} already exists. Overwrite? [Y/N]: ")

            if choice.lower() != "y":
                print(
                    "FFmpeg will not run as the output filename already exists, and you do not want it to be overwritten."
                )
                return False

            self._ffmpeg_args.insert(1, "-y")
        return True

    def _update_progress(self, ffmpeg_output: str, progress_handler: Optional[Callable]):
        if not ffmpeg_output:
            return
        value = ffmpeg_output.split("=")[1].strip()

        if progress_handler is None:
            if "out_time_ms" in ffmpeg_output:
                seconds_processed = round(int(value) / 1_000_000, 1)
                if self._progress_bar:
                    self._progress_bar.update(seconds_processed - self._progress_bar.n)
            return

        if "total_size" in ffmpeg_output and "N/A" not in value:
            self._current_size = int(value)

        elif "out_time_ms" in ffmpeg_output:
            self._seconds_processed = int(value) / 1_000_000

            if self._can_get_duration:
                self._percentage_progress = (self._seconds_processed / self._duration_secs) * 100

                if self._current_size is not None and self._percentage_progress != 0.0:
                    self._estimated_size = self._current_size * (100 / self._percentage_progress)

        elif "speed" in ffmpeg_output:
            speed_str = value[:-1]

            if speed_str != "0" and "N/A" not in speed_str:
                self._speed = float(speed_str)

                if self._can_get_duration:
                    self._eta = (self._duration_secs - self._seconds_processed) / self._speed

        if ffmpeg_output == "progress=end":
            self._percentage_progress = 100
            self._eta = 0

        progress_handler(self._percentage_progress, self._speed, self._eta, self._estimated_size)

    def run(
        self,
        progress_handler: Optional[Callable] = None,
        ffmpeg_output_file: Optional[str | Path] = None,
        success_handler: Optional[Callable] = None,
        error_handler: Optional[Callable] = None,
    ):
        if not self._should_overwrite():
            return

        if ffmpeg_output_file is None:
            ffmpeg_path = Path("./ffmpeg_output")
            ffmpeg_path.mkdir(exist_ok=True)
            ffmpeg_output_file = ffmpeg_path / f"{Path(self._filepath).name}.txt"

        with open(ffmpeg_output_file, "a") as f:
            process = subprocess.Popen(self._ffmpeg_args, stdout=subprocess.PIPE, stderr=f)
            print(f"\nRunning: {' '.join(self._ffmpeg_args)}\n")

        if progress_handler is None and self._can_get_duration:
            self._progress_bar = tqdm(
                total=round(self._duration_secs, 1),
                unit="s",
                dynamic_ncols=True,
                leave=False,
            )

        try:
            while process.poll() is None:
                ffmpeg_out_io = process.stdout
                if ffmpeg_out_io is None:
                    continue
                self._update_progress(ffmpeg_out_io.readline().decode().strip(), progress_handler)

            if process.returncode != 0:
                if error_handler:
                    error_handler()
                    return

                print(
                    f"The FFmpeg process encountered an error. The output of FFmpeg can be found in {ffmpeg_output_file}"
                )

            if self._progress_bar:
                self._progress_bar.update(self._duration_secs - self._progress_bar.n)

            if success_handler:
                success_handler()

            print(f"\n\nDone! To see FFmpeg's output, check out {ffmpeg_output_file}")

        except KeyboardInterrupt:
            if self._progress_bar:
                self._progress_bar.close()
            print("[KeyboardInterrupt] FFmpeg process killed.")
            sys.exit()

        except Exception as e:
            print(f"[Better FFmpeg Process] {e}")
