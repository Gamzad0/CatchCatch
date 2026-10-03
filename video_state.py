"""Read the real HTML video state without changing playback or LMS data."""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from browser_reader import BrowserReader


@dataclass(frozen=True)
class VideoState:
    duration: float
    current_time: float
    paused: bool
    ended: bool
    ready_state: int

    @property
    def playing(self) -> bool:
        return not self.paused and not self.ended and self.ready_state >= 2

    @property
    def wait_seconds(self) -> float:
        """A full viewing duration with the requested three-minute margin."""
        return self.duration + 180.0


def read_video_state(reader: 'BrowserReader') -> Optional[VideoState]:
    """Read #my-video itself or its unique inner video; None means not ready."""
    from browser_reader import BrowserReaderError, _VIDEO_ELEMENT_HELPER

    value = reader._evaluate('(() => {' + _VIDEO_ELEMENT_HELPER + '''
        const video = lectureVideo();
        if (!video) return null;
        if (!Number.isFinite(video.duration) || video.duration <= 0) return null;
        return {duration: video.duration, current_time: video.currentTime,
                paused: video.paused, ended: video.ended, ready_state: video.readyState};
    })()''')
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BrowserReaderError('Invalid video state')
    try:
        state = VideoState(**value)
    except (TypeError, ValueError) as error:
        raise BrowserReaderError('Invalid video state') from error
    if (any(isinstance(number, bool) or not isinstance(number, (int, float))
            or not math.isfinite(number) for number in (state.duration, state.current_time))
            or state.duration <= 0 or state.current_time < 0
            or not isinstance(state.paused, bool) or not isinstance(state.ended, bool)
            or isinstance(state.ready_state, bool) or not isinstance(state.ready_state, int)
            or not 0 <= state.ready_state <= 4):
        raise BrowserReaderError('Invalid video state')
    return state
