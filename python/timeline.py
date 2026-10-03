"""One chart timeline, using positive milli-BPM events and 480 ticks per beat."""

from bisect import bisect_right
import math

RESOLUTION = 480


class Timeline:
    def __init__(self, tempo_map: list[dict]) -> None:
        if not tempo_map or tempo_map[0].get("tick") != 0:
            raise ValueError("Tempo map must start at tick zero")
        self.entries = tempo_map
        self.ticks, self.seconds, self.bpms = [], [], []
        elapsed = 0.0
        for entry in tempo_map:
            tick, bpm = entry.get("tick"), entry.get("bpm")
            if (isinstance(tick, bool) or not isinstance(tick, int) or tick < 0
                    or isinstance(bpm, bool) or not isinstance(bpm, int) or bpm <= 0):
                raise ValueError("Tempo entries require nonnegative integer ticks and positive integer milli-BPM")
            if self.ticks:
                if tick <= self.ticks[-1]:
                    raise ValueError("Tempo events must be strictly sorted with unique ticks")
                elapsed += (tick - self.ticks[-1]) * 60000 / (self.bpms[-1] * RESOLUTION)
            self.ticks.append(tick)
            self.seconds.append(elapsed)
            self.bpms.append(bpm)

    def seconds_to_ticks(self, seconds: float) -> int:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("Event time must be nonnegative and finite")
        index = bisect_right(self.seconds, seconds) - 1
        return self.ticks[index] + round((seconds - self.seconds[index]) * RESOLUTION * self.bpms[index] / 60000)

    def ticks_to_seconds(self, tick: int) -> float:
        if not math.isfinite(tick) or tick < 0:
            raise ValueError("Event tick must be nonnegative and finite")
        index = bisect_right(self.ticks, tick) - 1
        return self.seconds[index] + (tick - self.ticks[index]) * 60000 / (RESOLUTION * self.bpms[index])


def constant_tempo_map(tempo: float) -> list[dict]:
    if not math.isfinite(tempo) or tempo <= 0:
        raise ValueError("Tempo must be positive and finite")
    return [{"tick": 0, "bpm": max(1, round(tempo * 1000))}]


def time_to_tick(time: float, tempo: float, offset: float = 0) -> int:
    return Timeline(constant_tempo_map(tempo)).seconds_to_ticks(max(0, time - offset))


def ticks_to_time(tick: int, tempo: float) -> float:
    return Timeline(constant_tempo_map(tempo)).ticks_to_seconds(tick)
