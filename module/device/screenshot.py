import os
import time,sys
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import get_color, image_size, limit_in, save_image
from module.device.method.adb import Adb
if sys.platform == 'win32':
    from module.device.method.window import Window
else:
    class Window:
        pass    
from module.device.method.droidcast import DroidCast
from module.device.method.scrcpy import Scrcpy
from module.exception import RequestHumanTakeover, ScriptError
from module.logger import logger


class Screenshot(Adb, DroidCast, Scrcpy, Window):
    _screen_size_checked = False
    _screen_black_checked = False
    _minicap_uninstalled = False
    _screenshot_interval = Timer(0.1)
    _last_save_time = {}
    image: np.ndarray

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if sys.platform == 'win32':
            super(Window, self).__init__(*args, **kwargs)

    @cached_property
    def screenshot_methods(self):
        return {
            'ADB': self.screenshot_adb,
            'ADB_nc': self.screenshot_adb_nc,
            'uiautomator2': self.screenshot_uiautomator2,
            # 'aScreenCap': self.screenshot_ascreencap,
            # 'aScreenCap_nc': self.screenshot_ascreencap_nc,
            'DroidCast': self.screenshot_droidcast,
            'DroidCast_raw': self.screenshot_droidcast_raw,
            'scrcpy': self.screenshot_scrcpy,
            'window_background': self.screenshot_window_background if sys.platform == 'win32' else None
        }

    def screenshot(self):
        """
        Returns:
            np.ndarray:
        """
        self._screenshot_interval.wait()
        self._screenshot_interval.reset()

        for _ in range(2):
            method = self.screenshot_methods.get(
                self.config.script.device.screenshot_method,
                self.screenshot_adb  # 第二个参数默认的是screenshot_adb
            )
            self.image = method()

            # if self.config.Emulator_ScreenshotDedithering:
            #     # This will take 40-60ms
            #     cv2.fastNlMeansDenoising(self.image, self.image, h=17, templateWindowSize=1, searchWindowSize=2)
            # self.image = self._handle_orientated_image(self.image)

            # if self.config.Error_SaveError:
            if self.config.script.error.save_error:
                self.screenshot_deque.append({'time': datetime.now(), 'image': self.image})

            if self.check_screen_size() and self.check_screen_black():
                break
            else:
                continue

        return self.image

    def _handle_orientated_image(self, image):
        """
        Args:
            image (np.ndarray):

        Returns:
            np.ndarray:
        """
        width, height = image_size(self.image)
        if width == 1280 and height == 720:
            return image

        # Rotate screenshots only when they're not 1280x720
        if self.orientation == 0:
            pass
        elif self.orientation == 1:
            image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif self.orientation == 2:
            image = cv2.rotate(image, cv2.ROTATE_180)
        elif self.orientation == 3:
            image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        else:
            raise ScriptError(f'Invalid device orientation: {self.orientation}')

        return image

    @cached_property
    def screenshot_deque(self):
        # return deque(maxlen=int(self.config.Error_ScreenshotLength))
        return deque(maxlen=int(self.config.script.error.screenshot_length))

    def save_screenshot(self, genre='items', interval=None, to_base_folder=False):
        """Save a screenshot. Use millisecond timestamp as file name.

        Args:
            genre (str, optional): Screenshot type.
            interval (int, float): Seconds between two save. Saves in the interval will be dropped.
            to_base_folder (bool): If save to base folder.

        Returns:
            bool: True if save succeed.
        """
        now = time.time()
        if interval is None:
            interval = 1  # 可改

        if now - self._last_save_time.get(genre, 0) > interval:
            fmt = 'png'
            file: str = '%s.%s' % (int(now * 1000), fmt)

            folder = Path('./log/screenshots')
            folder.mkdir(parents=True, exist_ok=True)
            file: Path = folder / file
            file = file.resolve()
            # folder = os.path.join(folder, genre)
            # if not os.path.exists(folder):
            #     os.mkdir(folder)

            # file = os.path.join(folder, file)
            self.image_save(file)
            self._last_save_time[genre] = now
            return True
        else:
            self._last_save_time[genre] = now
            return False

    def screenshot_last_save_time_reset(self, genre):
        self._last_save_time[genre] = 0

    def screenshot_interval_set(self, interval=None):
        """
        Args:
            interval (int, float, str):
                Minimum interval between 2 screenshots in seconds.
                Or None for Optimization_ScreenshotInterval, 'combat' for Optimization_CombatScreenshotInterval
        """
        if interval is None:
            origin = self.config.script.optimization.screenshot_interval
            interval = limit_in(origin, 0.1, 0.3)
            if interval != origin:
                logger.warning(f'Optimization.ScreenshotInterval {origin} is revised to {interval}')
                self.config.script.optimization.screenshot_interval = interval
        elif interval == 'combat':
            origin = self.config.script.optimization.combat_screenshot_interval
            interval = limit_in(origin, 0.3, 1.0)
            if interval != origin:
                logger.warning(f'Optimization.CombatScreenshotInterval {origin} is revised to {interval}')
                self.config.script.optimization.combat_screenshot_interval = interval
        elif isinstance(interval, (int, float)):
            # No limitation for manual set in code
            pass
        else:
            logger.warning(f'Unknown screenshot interval: {interval}')
            raise ScriptError(f'Unknown screenshot interval: {interval}')
        # Screenshot interval in scrcpy is meaningless,
        # video stream is received continuously no matter you use it or not.
        # if self.config.script.device.screenshot_method == 'scrcpy':
        if self.config.script.device.screenshot_method == 'scrcpy':
            interval = 0.1

        if interval != self._screenshot_interval.limit:
            logger.info(f'Screenshot interval set to {interval}s')
            self._screenshot_interval.limit = interval

    def image_show(self, image=None):
        if image is None:
            image = self.image
        Image.fromarray(image).show()

    def image_save(self, file):
        save_image(self.image, file)

    def check_screen_size(self):
        """
        Screen size must be 1280x720.
        Take a screenshot before call.
        """
        if self._screen_size_checked:
            return True

        orientated = False
        for _ in range(2):
            # Check screen size
            width, height = image_size(self.image)
            logger.attr('Screen_size', f'{width}x{height}')
            if width == 1280 and height == 720:
                self._screen_size_checked = True
                return True
            elif not orientated and (width == 720 and height == 1280):
                logger.info('Received orientated screenshot, handling')
                self.get_orientation()
                self.image = self._handle_orientated_image(self.image)
                orientated = True
                width, height = image_size(self.image)
                if width == 720 and height == 1280:
                    logger.info('Unable to handle orientated screenshot, continue for now')
                    return True
                else:
                    continue
            # elif self.config.Emulator_Serial == 'wsa-0':
            #     self.display_resize_wsa(0)
            #     return False
            elif hasattr(self, 'app_is_running') and not self.app_is_running():
                logger.warning('Received orientated screenshot, game not running')
                return True
            else:
                logger.critical(f'Resolution not supported: {width}x{height}')
                logger.critical('Please set emulator resolution to 1280x720')
                raise RequestHumanTakeover

    def check_screen_black(self):
        if self._screen_black_checked:
            return True
        # Check screen color
        # May get a pure black screenshot on some emulators.
        color = get_color(self.image, area=(0, 0, 1280, 720))
        if sum(color) < 1:
            # if self.config.Emulator_Serial == 'wsa-0':
            #     for _ in range(2):
            #         display = self.get_display_id()
            #         if display == 0:
            #             return True
            #     logger.info(f'Game running on display {display}')
            #     logger.warning('Game not running on display 0, will be restarted')
            #     self.app_stop_uiautomator2()
            #     return False
            if self.config.script.device.screenshot_method == 'uiautomator2':
                logger.warning(f'Received pure black screenshots from emulator, color: {color}')
                logger.warning('Uninstall minicap and retry')
                self.uninstall_minicap()
                self._screen_black_checked = False
                return False
            else:
                logger.warning(f'Received pure black screenshots from emulator, color: {color}')
                logger.warning(f'Screenshot method {self.config.script.device.screenshot_method}'
                               f'may not work on emulator `{self.serial}`, or the emulator is not fully started')
                if self.is_mumu_family:
                    if self.config.script.device.screenshot_method == 'DroidCast':
                        self.droidcast_stop()
                    else:
                        logger.warning('If you are using MuMu X, please upgrade to version >= 12.1.5.0')
                self._screen_black_checked = False
                return False
        else:
            self._screen_black_checked = True
            return True


if __name__ == "__main__":
    s = Screenshot(config="oas1")
    s.screenshot()
    s.image_show()
