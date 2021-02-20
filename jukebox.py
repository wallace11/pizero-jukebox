import time
import logging
import random
import os
import argparse

import RPi.GPIO as gpio
import alsaaudio as alsa
import vlc
import pydbus


class Consts:
    PIN_HANDLERS = ['play', 'volume_up', 'volume_down']
    DEFAULT_PINS = [7, 11, 13]

    BLUETOOTH_PROFILE = 'bluealsa'
    BASE_VOLUME = 40
    VOLUME_STEP = 5
    SONGS_PATH = '/home/pi/songs'
    DEFAULT_VERBOSITY = 'info'
    VERBOSITY_OPTIONS = ('info', 'debug')
    PLAYABLE_FILE_TYPES = ('.mp3', '.m4a', '.webm', '.wav', '.aac', '.ogg')
    DEFAULT_PLAYER_ARGS = ['--aout=alsa']


class JukeBox:
    def __init__(self):
        self.macaddr = None
        self.bt_profile = Consts.BLUETOOTH_PROFILE
        self.start_volume = Consts.BASE_VOLUME
        self.songs_dir = Consts.SONGS_PATH
        self.pins = Consts.DEFAULT_PINS
        self.verbosity = Consts.DEFAULT_VERBOSITY
        self.logger = logging.getLogger(__name__)

        self._parse_args()

        self.logger.setLevel(self.verbosity.upper())

        self.bluetooth = self.Bluetooth(mac_address=self.macaddr)
        self._setup_bluetooth()

        self.audio = self.Audio(control=f'{self.bluetooth.name} - A2DP',
                                device=self.bt_profile)
        self._setup_sound()

        self.player = self.Player(audio_device=self.bt_profile)
        self._setup_player()

        self._setup_gpio()

    def _parse_args(self):
        parser = argparse.ArgumentParser(description='PiZero JukeBox')
        parser.add_argument('-m', '--macaddr', required=True,
                            help="An already paired Bluetooth device's MAC address")
        parser.add_argument('-b', '--bt-profile', default=self.bt_profile,
                            help='Bluetooth profile name as configured in ALSA (~/.asoundrc)')
        parser.add_argument('-o', '--start-volume', type=int, default=self.start_volume,
                            help='Default volume level')
        parser.add_argument('-s', '--songs-dir', default=self.songs_dir,
                            help='Root directory for songs')
        parser.add_argument('-p', '--pins', nargs=3, default=self.pins,
                            help='Pins layout in order of play volume-up volume-down')
        parser.add_argument('-v', '--verbosity', default=self.verbosity,
                            choices=Consts.VERBOSITY_OPTIONS, help='Verbosity level')
        args = parser.parse_args()
        self.__dict__.update(vars(args))

    def _setup_bluetooth(self):
        self.bluetooth.initialize()
        self.bluetooth.connect()

    def _setup_sound(self):
        self.audio.initialize()
        self.audio.unmute()
        self.audio.set_volume(self.start_volume)

    def _setup_player(self):
        def song_finished_callback(event):
            self.player.finished = True

        self.player.initialize()
        self.play_next()
        self.player.events.event_attach(eventtype=vlc.EventType.MediaPlayerEndReached,
                                        callback=song_finished_callback)

    def _setup_gpio(self):
        gpio.setwarnings(False)
        gpio.setmode(gpio.BOARD)

        for pin in self.pins:
           gpio.setup(pin, gpio.IN, pull_up_down=gpio.PUD_DOWN)

    def get_song(self):
        files = []
        for root, _, f in os.walk(self.songs_dir):
            f = filter(lambda f: f.endswith(Consts.PLAYABLE_FILE_TYPES), f)
            files.extend(map(lambda x: os.path.join(root, x), f))
        return random.choice(files)

    def play_next(self, file=None):
        if not file:
            file = self.get_song()
        self.player.load_file(file)
        self.player.play()
        self.player.finished = False

    def play_handler(self):
        self.player.pause()

    def volume_up_handler(self):
        self.audio.set_volume(f'+{Consts.VOLUME_STEP}')

    def volume_down_handler(self):
        self.audio.set_volume(f'-{Consts.VOLUME_STEP}')

    def run(self):
        try:
            counter = 0
            while True:
                time.sleep(0.1)

                if counter % 20 == 0:
                    counter = 1

                    if not self.bluetooth.connected:
                        self.player.stop()
                        self._setup_bluetooth()
                        self._setup_sound()
                        self._setup_player()

                counter += 1

                if self.player.finished:
                    self.play_next()

                for pin, handler in zip(self.pins, Consts.PIN_HANDLERS):
                    if gpio.input(pin):
                        getattr(self, f'{handler}_handler')()

                        # Neglect further actions while button is pressed
                        while gpio.input(pin):
                            time.sleep(0.3)
        except KeyboardInterrupt:
            self.logger.info('buh-bye')
        finally:
            gpio.cleanup()

    class Bluetooth:
        def __init__(self, mac_address):
            self.mac_address = mac_address
            self.bus = pydbus.SystemBus()
            self.device = None
            self.logger = logging.getLogger(__name__)

        def _get_device(self):
            try:
                mac_address_conv = self.mac_address.replace(':', '_')
                device = self.bus.get('org.bluez',
                                      f'/org/bluez/hci0/dev_{mac_address_conv}')
            except ValueError:
                raise

            return device

        def initialize(self):
            self.device = self._get_device()

        def connect(self):
            while not self.connected:
                self.logger.info(
                    f'Device {self.name} ({self.mac_address}) is disconnected. Trying to reconnect...')
                try:
                    self.device.connect()
                except AttributeError as e:
                    self.logger.error(e)
                    pass
                time.sleep(3)

            self.logger.info(
                f'Device {self.name} ({self.mac_address}) is connected!')

        def __getattr__(self, attr):
            try:
                return object.__getattribute__(self, attr)
            except AttributeError:
                self.logger.debug(f'Bluetooth device action received: {attr}')
                return getattr(self.device, attr.title())

    class Audio:
        def __init__(self, **kwargs):
            self.mixer = None
            self.kwargs = kwargs
            self.logger = logging.getLogger(__name__)

        def _get_mixer(self):
            while True:
                try:
                    mixer = alsa.Mixer(**self.kwargs)
                    break
                except alsa.ALSAAudioError as e:
                    self.logger.error(e)
                    time.sleep(3)

            return mixer

        def initialize(self):
            self.mixer = self._get_mixer()

        def unmute(self):
            self.mixer.setmute(0)
            self.logger.debug(f'Unmuted {self.mixer.mixer()}')

        def toggle_mute(self):
            self.mixer.setmute(0 if all(self.mixer.getmute()) else 1)
            self.logger.debug(f'Toggled mute for {self.mixer.mixer()}')

        def set_volume(self, volume):
            if isinstance(volume, str):
                if volume.startswith(('+', '-')):
                    current_volume = max(self.mixer.getvolume())
                    operator, new_volume = volume[0], volume[1:]
                    volume = eval(f'{current_volume}{operator}{new_volume}')
                else:
                    volume = int(volume)

            self.mixer.setvolume(volume)
            self.logger.debug(
                f'Set volume for {self.mixer.mixer()} to {volume}')

    class Player:
        def __init__(self, player_args=[], audio_device=None):
            self.args = player_args or Consts.DEFAULT_PLAYER_ARGS

            if audio_device:
                self.args.append(f'--alsa-audio-device={audio_device}')

            self.instance = self.player = self.events = self.finished = None

            self.logger = logging.getLogger(__name__)

        def initialize(self):
            self.instance = vlc.Instance(' '.join(self.args))
            self.player = self.instance.media_player_new()
            self.events = self.player.event_manager()
            self.finished = False

        def load_file(self, file):
            media = self.instance.media_new_path(file)
            self.player.set_media(media)
            self.logger.info(f'Loaded file {file}')

        def __getattr__(self, attr):
            try:
                return object.__getattribute__(self, attr)
            except AttributeError:
                self.logger.debug(f'Player action received: {attr}')
                return getattr(self.player, attr)
