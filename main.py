#!/usr/bin/env python3

import logging
from jukebox import JukeBox

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s')

jb = JukeBox()
jb.run()
